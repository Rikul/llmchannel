import json
import queue
import time
import uuid
from threading import Lock

from config import MAX_HISTORY


class MessageStore:
    def __init__(self):
        self.messages = []
        self._lock = Lock()
        self._listeners = []

    def add_message(self, sender, content, msg_type, color, msg_id=None):
        msg = {
            "id": msg_id or str(uuid.uuid4()),
            "sender": sender,
            "content": content,
            "timestamp": time.time(),
            "type": msg_type,
            "color": color,
            "streaming": False,
        }
        with self._lock:
            self.messages.append(msg)
            if len(self.messages) > MAX_HISTORY:
                self.messages = self.messages[-MAX_HISTORY:]
        self._announce("new_message", msg)
        return msg

    def start_streaming_message(self, sender, color, msg_id):
        msg = {
            "id": msg_id,
            "sender": sender,
            "content": "",
            "timestamp": time.time(),
            "type": "bot",
            "color": color,
            "streaming": True,
        }
        with self._lock:
            self.messages.append(msg)
        self._announce("stream_start", msg)
        return msg

    def append_to_streaming(self, msg_id, token):
        with self._lock:
            for msg in reversed(self.messages):
                if msg["id"] == msg_id:
                    msg["content"] += token
                    break
        self._announce("stream_token", {"id": msg_id, "token": token})

    def finish_streaming(self, msg_id):
        with self._lock:
            for msg in reversed(self.messages):
                if msg["id"] == msg_id:
                    msg["streaming"] = False
                    break
        self._announce("stream_end", {"id": msg_id})

    def get_history(self):
        with self._lock:
            return list(self.messages)

    def clear(self):
        with self._lock:
            self.messages.clear()
        self._announce("clear", {})

    def listen(self):
        q = queue.Queue(maxsize=100)
        self._listeners.append(q)
        try:
            while True:
                try:
                    data = q.get(timeout=30)
                    yield data
                except queue.Empty:
                    yield ":\n\n"  # SSE keepalive comment
        except GeneratorExit:
            pass
        finally:
            try:
                self._listeners.remove(q)
            except ValueError:
                pass

    def _announce(self, event_type, data):
        msg = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
        dead = []
        for q in self._listeners:
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(q)
        for q in dead:
            try:
                self._listeners.remove(q)
            except ValueError:
                pass
