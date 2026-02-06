import copy
import threading
import uuid

import llm_clients
from config import DEFAULT_BOTS
from models import db, Bot


class BotManager:
    def __init__(self, message_store):
        self.message_store = message_store
        self._lock = threading.Lock()

    def load_defaults(self):
        """Load default bots into database if none exist."""
        with self._lock:
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

    def add_bot(self, nickname, provider, model, api_key, system_prompt, color):
        bot = Bot(
            id=str(uuid.uuid4()),
            nickname=nickname,
            provider=provider,
            model=model,
            api_key=api_key,
            system_prompt=system_prompt,
            color=color,
            enabled=True
        )
        with self._lock:
            db.session.add(bot)
            db.session.commit()
        return bot.to_dict()

    def remove_bot(self, bot_id):
        with self._lock:
            bot = Bot.query.get(bot_id)
            if bot:
                db.session.delete(bot)
                db.session.commit()
                return bot.to_dict()
            return None

    def update_bot(self, bot_id, **kwargs):
        with self._lock:
            bot = Bot.query.get(bot_id)
            if not bot:
                return None
            for key, value in kwargs.items():
                if hasattr(bot, key) and key != 'id':
                    setattr(bot, key, value)
            db.session.commit()
            return bot.to_dict()

    def get_bot(self, bot_id):
        bot = Bot.query.get(bot_id)
        return bot.to_dict() if bot else None

    def get_all_bots(self):
        with self._lock:
            bots = Bot.query.all()
            return [bot.to_dict() for bot in bots]

    def set_bot_enabled(self, bot_id, enabled):
        with self._lock:
            bot = Bot.query.get(bot_id)
            if bot:
                bot.enabled = enabled
                db.session.commit()
                return bot.to_dict()
        return None

    def dispatch_to_all(self, operator_message):
        with self._lock:
            bots = Bot.query.filter_by(enabled=True).all()
            bots_snapshot = [
                bot.to_dict() for bot in bots
                if bot.api_key
            ]

        for bot in bots_snapshot:
            t = threading.Thread(
                target=self._run_bot_response,
                args=(bot,),
                daemon=True,
            )
            t.start()

    def _run_bot_response(self, bot_config):
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
                messages=formatted,
                system_prompt=bot_config["system_prompt"],
            ):
                self.message_store.append_to_streaming(msg_id, token)
        except Exception as e:
            self.message_store.append_to_streaming(msg_id, f"[Error: {str(e)}]")
        finally:
            self.message_store.finish_streaming(msg_id)
