import copy
import threading
import uuid

import llm_clients
from config import DEFAULT_BOTS


class BotManager:
    def __init__(self, message_store):
        self.bots = {}
        self.message_store = message_store
        self._lock = threading.Lock()

    def load_defaults(self):
        for bot_cfg in DEFAULT_BOTS:
            bot = copy.deepcopy(bot_cfg)
            self.bots[bot["id"]] = bot

    def add_bot(self, nickname, provider, model, api_key, system_prompt, color):
        bot = {
            "id": str(uuid.uuid4()),
            "nickname": nickname,
            "provider": provider,
            "model": model,
            "api_key": api_key,
            "system_prompt": system_prompt,
            "color": color,
            "enabled": True,
        }
        with self._lock:
            self.bots[bot["id"]] = bot
        return bot

    def remove_bot(self, bot_id):
        with self._lock:
            return self.bots.pop(bot_id, None)

    def update_bot(self, bot_id, **kwargs):
        with self._lock:
            bot = self.bots.get(bot_id)
            if not bot:
                return None
            for key, value in kwargs.items():
                if key in bot and key != "id":
                    bot[key] = value
            return bot

    def get_bot(self, bot_id):
        return self.bots.get(bot_id)

    def get_all_bots(self):
        with self._lock:
            return list(self.bots.values())

    def set_bot_enabled(self, bot_id, enabled):
        with self._lock:
            bot = self.bots.get(bot_id)
            if bot:
                bot["enabled"] = enabled
                return bot
        return None

    def dispatch_to_all(self, operator_message):
        with self._lock:
            bots_snapshot = [
                copy.deepcopy(b) for b in self.bots.values()
                if b["enabled"] and b["api_key"]
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
