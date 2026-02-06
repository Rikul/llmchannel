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
        self._message_count = 0  # Track message count to avoid frequent DB queries
        # Will be initialized when database context is available

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
            self._message_count += 1
            
            # Only cleanup every 10 messages to reduce overhead
            if self._message_count > MAX_HISTORY:
                if self._message_count % 10 == 0:
                    # Use efficient bulk delete for old messages
                    recent_messages = Message.query.order_by(Message.timestamp.desc()).limit(
                        MAX_HISTORY
                    ).all()
                    
                    if len(recent_messages) == MAX_HISTORY:
                        cutoff_time = recent_messages[-1].timestamp
                        Message.query.filter(Message.timestamp < cutoff_time).delete()
                        db.session.commit()
            
            # Trim cache to max history
            if len(self._message_cache) > MAX_HISTORY:
                self._message_cache = self._message_cache[-MAX_HISTORY:]
        
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
            # Update cache only - will persist when streaming finishes
            for msg in reversed(self._message_cache):
                if msg["id"] == msg_id:
                    msg["content"] += token
                    break
        
        self._announce("stream_token", {"id": msg_id, "token": token})

    def finish_streaming(self, msg_id):
        with self._lock:
            # Update database with final content
            message = Message.query.get(msg_id)
            if message:
                # Get final content from cache
                for msg in reversed(self._message_cache):
                    if msg["id"] == msg_id:
                        message.content = msg["content"]
                        message.streaming = False
                        break
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
            
            # Load from database if cache is empty (e.g., after app restart)
            messages = Message.query.order_by(Message.timestamp).all()
            # Keep only the most recent MAX_HISTORY messages
            if len(messages) > MAX_HISTORY:
                messages = messages[-MAX_HISTORY:]
            
            self._message_cache = [msg.to_dict() for msg in messages]
            # Synchronize message count with database
            self._message_count = Message.query.count()
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
