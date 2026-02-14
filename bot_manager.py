import copy
import threading
import time
import uuid
from flask import current_app
from sqlalchemy import and_, or_

import llm_clients
from config import DEFAULT_BOTS
from models import db, Bot
from utils import is_oauth_or_rate_limit_error


class BotManager:
    def __init__(self, message_store):
        self.message_store = message_store
        self._lock = threading.Lock()
        self._app = None  # Will be set when app context is available
        self._last_seen_msg_id = {}
        self._in_flight = set()
        self._next_dispatch_time = 0.0
        self._dispatch_delay = 4.0

    def load_defaults(self):
        """Load default bots into database if none exist."""
        # Store app reference for background threads
        if self._app is None:
            self._app = current_app._get_current_object()
        
        with self._lock:
            try:
                existing_count = Bot.query.count()
                if existing_count == 0:
                    for bot_cfg in DEFAULT_BOTS:
                        bot = Bot(
                            id=bot_cfg['id'],
                            nickname=bot_cfg['nickname'],
                            provider=bot_cfg['provider'],
                            model=bot_cfg['model'],
                            api_key=bot_cfg['api_key'],
                            system_prompt=bot_cfg['system_prompt'],
                            color=bot_cfg['color'],
                            enabled=bot_cfg['enabled']
                        )
                        db.session.add(bot)
                    db.session.commit()
            except Exception as e:
                db.session.rollback()
                raise Exception(f"Failed to load default bots: {e}")

    def add_bot(self, nickname, provider, model, api_key, system_prompt, color):
        with self._lock:
            try:
                bot = Bot(
                    id=str(uuid.uuid4()),
                    nickname=nickname,
                    provider=provider,
                    model=model,
                    api_key=api_key,
                    gemini_access_token="",
                    gemini_refresh_token="",
                    system_prompt=system_prompt,
                    color=color,
                    enabled=True
                )
                db.session.add(bot)
                db.session.commit()
                return bot.to_dict()
            except Exception as e:
                db.session.rollback()
                raise Exception(f"Failed to add bot: {e}")

    def remove_bot(self, bot_id):
        with self._lock:
            try:
                bot = Bot.query.get(bot_id)
                if bot:
                    bot_dict = bot.to_dict()
                    db.session.delete(bot)
                    db.session.commit()
                    return bot_dict
                return None
            except Exception as e:
                db.session.rollback()
                raise Exception(f"Failed to remove bot: {e}")

    def update_bot(self, bot_id, **kwargs):
        with self._lock:
            try:
                bot = Bot.query.get(bot_id)
                if not bot:
                    return None
                for key, value in kwargs.items():
                    if hasattr(bot, key) and key != 'id':
                        setattr(bot, key, value)
                db.session.commit()
                return bot.to_dict()
            except Exception as e:
                db.session.rollback()
                raise Exception(f"Failed to update bot: {e}")

    def get_bot(self, bot_id):
        with self._lock:
            bot = Bot.query.get(bot_id)
            return bot.to_dict() if bot else None

    def get_all_bots(self):
        with self._lock:
            bots = Bot.query.all()
            return [bot.to_dict() for bot in bots]

    def set_bot_enabled(self, bot_id, enabled):
        with self._lock:
            try:
                bot = Bot.query.get(bot_id)
                if bot:
                    bot.enabled = enabled
                    db.session.commit()
                    return bot.to_dict()
                return None
            except Exception as e:
                db.session.rollback()
                raise Exception(f"Failed to set bot enabled status: {e}")

    def dispatch_to_all(self, message):
        if message:
            self.handle_message_finalized(message)

    def _run_bot_response(self, bot_config, trigger_msg_id, delay=0.0):
        # Ensure Flask app context for database operations in thread
        with self._app.app_context():
            if delay > 0:
                time.sleep(delay)
            msg_id = str(uuid.uuid4())
            nickname = bot_config["nickname"]
            color = bot_config["color"]

            self.message_store.start_streaming_message(nickname, color, msg_id)

            history = self.message_store.get_history()
            # Exclude the streaming placeholder we just created
            history = [m for m in history if m["id"] != msg_id]

            formatted = llm_clients._format_channel_history(history, nickname)

            try:
                for token in llm_clients.stream_response(
                    provider=bot_config["provider"],
                    model=bot_config["model"],
                    api_key=bot_config["api_key"],
                    gemini_access_token=bot_config.get("gemini_access_token", ""),
                    gemini_refresh_token=bot_config.get("gemini_refresh_token", ""),
                    bot_id=bot_config.get("id"),
                    messages=formatted,
                    system_prompt=bot_config["system_prompt"],
                ):
                    self.message_store.append_to_streaming(msg_id, token)
            except Exception as e:
                error_msg = str(e)
                if is_oauth_or_rate_limit_error(error_msg):
                    # Log OAuth and rate limit errors instead of sending to channel
                    current_app.logger.error(f"Bot '{nickname}' OAuth/rate limit error: {error_msg}")
                    self.message_store.append_to_streaming(msg_id, "[Error: Authentication or rate limit issue - check server logs]")
                else:
                    self.message_store.append_to_streaming(msg_id, f"[Error: {error_msg}]")
            finally:
                self.message_store.finish_streaming(msg_id)
                with self._lock:
                    self._in_flight.discard(bot_config.get("id"))
                    if bot_config.get("id"):
                        self._last_seen_msg_id[bot_config["id"]] = trigger_msg_id

    def handle_message_finalized(self, message):
        if not message:
            return

        msg_id = message.get("id")
        sender = message.get("sender")
        if not msg_id:
            return

        with self._lock:
            bots = Bot.query.filter(
                Bot.enabled.is_(True),
                or_(
                    and_(
                        Bot.provider == "gemini",
                        Bot.gemini_access_token.isnot(None),
                        Bot.gemini_access_token != ""
                    ),
                    and_(
                        Bot.provider == "gemini",
                        Bot.gemini_refresh_token.isnot(None),
                        Bot.gemini_refresh_token != ""
                    ),
                    Bot.provider == "ollama",
                    and_(
                        Bot.api_key.isnot(None),
                        Bot.api_key != ""
                    )
                )
            ).all()
            bots_snapshot = [bot.to_dict() for bot in bots]

            for bot in bots_snapshot:
                bot_id = bot.get("id")
                if not bot_id:
                    continue
                if bot.get("nickname") == sender:
                    self._last_seen_msg_id[bot_id] = msg_id
                    continue
                if self._last_seen_msg_id.get(bot_id) == msg_id:
                    continue
                if bot_id in self._in_flight:
                    continue

                self._last_seen_msg_id[bot_id] = msg_id
                self._in_flight.add(bot_id)
                now = time.time()
                scheduled = max(now, self._next_dispatch_time)
                self._next_dispatch_time = scheduled + self._dispatch_delay
                delay = max(0.0, scheduled - now)

                t = threading.Thread(
                    target=self._run_bot_response,
                    args=(bot, msg_id, delay),
                    daemon=True,
                )
                t.start()
