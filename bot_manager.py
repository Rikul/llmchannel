import re
import sys
import threading
import time
import uuid
from flask import current_app

import llm_clients
from config import DEFAULT_BOTS, NICKNAME_CHARS, OPERATOR_NAME, PASS_TOKEN
from models import db, Bot
from utils import is_oauth_or_rate_limit_error

BOT_FIELDS = (
    "nickname", "base_url", "api_key", "model", "persona",
    "temperature", "max_tokens", "color", "enabled",
)


def _is_mentioned(nickname, content):
    pattern = f"(?<!{NICKNAME_CHARS})@{re.escape(nickname)}(?!{NICKNAME_CHARS})"
    return re.search(pattern, content, re.IGNORECASE) is not None


# ── Reply Filter ─────────────────────────────────────────────────────────

class _ReplyFilter:
    """Buffers the start of a streamed reply so noise never reaches the channel.

    Handles the PASS token (bot chose to stay silent), a leading "[Nick]:" echo of the
    history format, and a leading <think>...</think> block from local reasoning models.
    """

    def __init__(self, nickname):
        self._buf = ""
        self._decided = False
        self._thinking = False
        self._name_prefix = f"[{nickname}]:"
        self._prefixes = [PASS_TOKEN, self._name_prefix, "<think>"]
        self.passed = False

    def feed(self, token):
        """Return the text that is safe to show for this token (may be empty)."""
        if self.passed:
            return ""
        if self._decided:
            return token
        self._buf += token
        return self._resolve(final=False)

    def finish(self):
        """Flush anything still buffered. Sets `passed` if the reply was empty or [PASS]."""
        if self.passed or self._decided:
            return ""
        out = self._resolve(final=True)
        if not self._decided:
            self.passed = True
        return out

    def _resolve(self, final):
        while True:
            if self._thinking:
                end = self._buf.find("</think>")
                if end == -1:
                    if final:
                        self._buf = ""
                    return ""
                self._buf = self._buf[end + len("</think>"):]
                self._thinking = False
                continue

            text = self._buf.lstrip()
            if not text:
                return ""
            if text.upper().startswith(PASS_TOKEN):
                self.passed = True
                return ""
            if text.startswith(self._name_prefix):
                self._buf = text[len(self._name_prefix):]
                continue
            if text.startswith("<think>"):
                self._buf = text[len("<think>"):]
                self._thinking = True
                continue
            if not final and any(p.upper().startswith(text.upper()) for p in self._prefixes):
                return ""  # Could still turn into one of the prefixes; keep buffering

            self._decided = True
            self._buf = ""
            return text


# ── Bot Manager ──────────────────────────────────────────────────────────

class BotManager:
    def __init__(self, message_store, settings):
        self.message_store = message_store
        self.settings = settings
        self._lock = threading.Lock()
        self._app = None  # Will be set when app context is available
        self._last_seen_msg_id = {}
        self._in_flight = {}  # bot id -> conversation id of its running reply
        self._next_dispatch_time = 0.0
        self._dispatch_delay = 4.0
        # Each operator message starts a new round with a fresh bot-to-bot reply budget
        self._round = 0
        self._chain_replies = 0

    def load_defaults(self):
        """Load default bots into database if none exist."""
        # Store app reference for background threads
        if self._app is None:
            self._app = current_app._get_current_object()

        with self._lock:
            try:
                if Bot.query.count() == 0:
                    for bot_cfg in DEFAULT_BOTS:
                        db.session.add(Bot(**bot_cfg))
                    db.session.commit()
            except Exception as e:
                db.session.rollback()
                raise Exception(f"Failed to load default bots: {e}")

    def add_bot(self, **fields):
        with self._lock:
            try:
                values = {k: v for k, v in fields.items() if k in BOT_FIELDS}
                values.setdefault("enabled", True)
                bot = Bot(id=str(uuid.uuid4()), **values)
                db.session.add(bot)
                db.session.commit()
                return bot.to_dict()
            except Exception as e:
                db.session.rollback()
                raise Exception(f"Failed to add bot: {e}")

    def remove_bot(self, bot_id):
        with self._lock:
            try:
                bot = db.session.get(Bot, bot_id)
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
                bot = db.session.get(Bot, bot_id)
                if not bot:
                    return None
                for key, value in kwargs.items():
                    if key in BOT_FIELDS:
                        setattr(bot, key, value)
                db.session.commit()
                return bot.to_dict()
            except Exception as e:
                db.session.rollback()
                raise Exception(f"Failed to update bot: {e}")

    def get_bot(self, bot_id):
        with self._lock:
            bot = db.session.get(Bot, bot_id)
            return bot.to_dict() if bot else None

    def get_all_bots(self):
        with self._lock:
            return [bot.to_dict() for bot in Bot.query.all()]

    def set_bot_enabled(self, bot_id, enabled):
        return self.update_bot(bot_id, enabled=bool(enabled))

    def _run_bot_response(self, bot_config, trigger_msg_id, delay, participants,
                          round_id, counted, conversation_id):
        # Ensure Flask app context for database operations in thread
        with self._app.app_context():
            if delay > 0:
                time.sleep(delay)
            msg_id = str(uuid.uuid4())
            nickname = bot_config["nickname"]
            reply = _ReplyFilter(nickname)
            cancelled = False
            abandoned = False

            # The operator may have started a new conversation while this reply was queued
            started = self.message_store.start_streaming_message(
                nickname, bot_config["color"], msg_id, conversation_id=conversation_id
            )
            if started is None:
                with self._lock:
                    self._release(bot_config.get("id"), conversation_id)
                return

            history = self.message_store.get_history()
            # Exclude the streaming placeholder we just created
            history = [m for m in history if m["id"] != msg_id]
            formatted = llm_clients._format_channel_history(history, nickname)

            system_prompt = llm_clients.build_system_prompt(
                self.settings.get("system_prompt"),
                nickname,
                bot_config.get("persona", ""),
                participants,
            )

            try:
                for token in llm_clients.stream_response(
                    base_url=bot_config["base_url"],
                    api_key=bot_config.get("api_key", ""),
                    model=bot_config["model"],
                    messages=formatted,
                    system_prompt=system_prompt,
                    temperature=bot_config.get("temperature"),
                    max_tokens=bot_config.get("max_tokens"),
                ):
                    # Stop paying for tokens once the conversation has been archived
                    if not self.message_store.is_streaming(msg_id):
                        abandoned = True
                        break
                    text = reply.feed(token)
                    if text:
                        self.message_store.append_to_streaming(msg_id, text)
                if not abandoned:
                    tail = reply.finish()
                    if tail:
                        self.message_store.append_to_streaming(msg_id, tail)
                    cancelled = reply.passed
            except Exception as e:
                error_msg = str(e)
                if is_oauth_or_rate_limit_error(error_msg):
                    print(
                        f"Bot '{nickname}' auth/rate limit error: {error_msg}",
                        file=sys.stderr,
                    )
                    cancelled = True
                else:
                    self.message_store.append_to_streaming(msg_id, f"[Error: {error_msg}]")
            finally:
                if cancelled:
                    self.message_store.cancel_streaming_message(msg_id)
                else:
                    self.message_store.finish_streaming(msg_id)
                with self._lock:
                    self._release(bot_config.get("id"), conversation_id)
                    if bot_config.get("id"):
                        self._last_seen_msg_id[bot_config["id"]] = trigger_msg_id
                    # A silent reply doesn't spend the round's bot-to-bot budget
                    if cancelled and counted and round_id == self._round:
                        self._chain_replies = max(0, self._chain_replies - 1)

    def _release(self, bot_id, conversation_id):
        """Clear a bot's in-flight mark, unless a newer conversation's reply now holds it.

        Caller must hold self._lock.
        """
        if bot_id and self._in_flight.get(bot_id) == conversation_id:
            del self._in_flight[bot_id]

    def handle_message_finalized(self, message):
        if not message:
            return

        msg_id = message.get("id")
        sender = message.get("sender")
        content = message.get("content", "")
        is_operator = message.get("type") == "operator"
        if not msg_id:
            return
        # Don't let bots react to another bot's error output
        if not is_operator and content.startswith("[Error:"):
            return

        with self._lock:
            if is_operator:
                self._round += 1
                self._chain_replies = 0

            bots = Bot.query.filter(
                Bot.enabled.is_(True),
                Bot.base_url.isnot(None),
                Bot.base_url != "",
            ).all()
            bots_snapshot = [bot.to_dict() for bot in bots]
            participants = [OPERATOR_NAME] + [b["nickname"] for b in bots_snapshot]

            # The operator can address specific bots with @Name; only those reply
            targets = None
            if is_operator:
                mentioned = {
                    b["id"] for b in bots_snapshot if _is_mentioned(b["nickname"], content)
                }
                targets = mentioned or None

            chain_limit = self.settings.get("bot_chain_limit")

            for bot in bots_snapshot:
                bot_id = bot.get("id")
                if not bot_id:
                    continue
                if bot.get("nickname") == sender:
                    self._last_seen_msg_id[bot_id] = msg_id
                    continue
                if targets is not None and bot_id not in targets:
                    continue
                if self._last_seen_msg_id.get(bot_id) == msg_id:
                    continue
                # A reply still winding down in an archived conversation doesn't block this one
                if self._in_flight.get(bot_id) == message.get("conversation_id"):
                    continue
                if not is_operator:
                    if self._chain_replies >= chain_limit:
                        continue
                    self._chain_replies += 1

                self._last_seen_msg_id[bot_id] = msg_id
                self._in_flight[bot_id] = message.get("conversation_id")
                now = time.time()
                scheduled = max(now, self._next_dispatch_time)
                self._next_dispatch_time = scheduled + self._dispatch_delay
                delay = max(0.0, scheduled - now)

                t = threading.Thread(
                    target=self._run_bot_response,
                    args=(
                        bot, msg_id, delay, participants, self._round, not is_operator,
                        message.get("conversation_id"),
                    ),
                    daemon=True,
                )
                t.start()
