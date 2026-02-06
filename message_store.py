import json
import queue
import time
import uuid
from threading import Lock

from config import MAX_HISTORY
from models import db, Message


class MessageStore:
    def __init__(self):
        self._lock = Lock()
        self._listeners = []
        self._message_cache = []  # In-memory cache for quick access

    def add_message(self, sender, content, msg_type, color, msg_id=None):
        msg_id = msg_id or str(uuid.uuid4())
        timestamp = time.time()
        
        # Create message in database
        message = Message(
            id=msg_id,
            sender=sender,
            content=content,
            timestamp=timestamp,
            msg_type=msg_type,
            color=color,
            streaming=False
        )
        
        with self._lock:
            db.session.add(message)
            db.session.commit()
            
            # Update cache
            msg_dict = message.to_dict()
            self._message_cache.append(msg_dict)
            if len(self._message_cache) > MAX_HISTORY:
                self._message_cache = self._message_cache[-MAX_HISTORY:]
            
            # Clean up old messages in database
            total_count = Message.query.count()
            if total_count > MAX_HISTORY:
                oldest_messages = Message.query.order_by(Message.timestamp).limit(
                    total_count - MAX_HISTORY
                ).all()
                for old_msg in oldest_messages:
                    db.session.delete(old_msg)
                db.session.commit()
        
        self._announce("new_message", msg_dict)
        return msg_dict

    def start_streaming_message(self, sender, color, msg_id):
        timestamp = time.time()
        
        # Create streaming message in database
        message = Message(
            id=msg_id,
            sender=sender,
            content='',
            timestamp=timestamp,
            msg_type='bot',
            color=color,
            streaming=True
        )
        
        with self._lock:
            db.session.add(message)
            db.session.commit()
            
            # Update cache
            msg_dict = message.to_dict()
            self._message_cache.append(msg_dict)
        
        self._announce("stream_start", msg_dict)
        return msg_dict

    def append_to_streaming(self, msg_id, token):
        with self._lock:
            # Update database
            message = Message.query.get(msg_id)
            if message:
                message.content += token
                db.session.commit()
            
            # Update cache
            for msg in reversed(self._message_cache):
                if msg["id"] == msg_id:
                    msg["content"] += token
                    break
        
        self._announce("stream_token", {"id": msg_id, "token": token})

    def finish_streaming(self, msg_id):
        with self._lock:
            # Update database
            message = Message.query.get(msg_id)
            if message:
                message.streaming = False
                db.session.commit()
            
            # Update cache
            for msg in reversed(self._message_cache):
                if msg["id"] == msg_id:
                    msg["streaming"] = False
                    break
        
        self._announce("stream_end", {"id": msg_id})

    def get_history(self):
        with self._lock:
            # Try to use cache first
            if self._message_cache:
                return list(self._message_cache)
            
            # Load from database if cache is empty
            messages = Message.query.order_by(Message.timestamp).limit(MAX_HISTORY).all()
            self._message_cache = [msg.to_dict() for msg in messages]
            return list(self._message_cache)

    def clear(self):
        with self._lock:
            # Clear database
            Message.query.delete()
            db.session.commit()
            
            # Clear cache
            self._message_cache.clear()
        
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
