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
        self._on_message_finalized = None
        # Will be initialized when database context is available

    def set_on_message_finalized(self, callback):
        self._on_message_finalized = callback

    def add_message(self, sender, content, msg_type, color, msg_id=None):
        msg_id = msg_id or str(uuid.uuid4())
        timestamp = time.time()
        
        with self._lock:
            try:
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
                
                db.session.add(message)
                db.session.commit()
                
                # Update cache
                msg_dict = message.to_dict()
                self._message_cache.append(msg_dict)
                self._message_count += 1
                
                # Only cleanup every 10 messages to reduce overhead
                if self._message_count > MAX_HISTORY:
                    if self._message_count % 10 == 0:
                        # Use efficient bulk delete for old messages without loading full objects
                        cutoff_time = (
                            db.session.query(Message.timestamp)
                            .order_by(Message.timestamp.desc())
                            .offset(MAX_HISTORY - 1)
                            .limit(1)
                            .scalar()
                        )

                        if cutoff_time is not None:
                            Message.query.filter(Message.timestamp < cutoff_time).delete(
                                synchronize_session='fetch'
                            )
                            db.session.commit()
                
                # Trim cache to max history
                if len(self._message_cache) > MAX_HISTORY:
                    self._message_cache = self._message_cache[-MAX_HISTORY:]
                
                self._announce("new_message", msg_dict)
                if self._on_message_finalized:
                    self._on_message_finalized(msg_dict)
                return msg_dict
            except Exception as e:
                db.session.rollback()
                raise Exception(f"Failed to add message: {e}")

    def start_streaming_message(self, sender, color, msg_id):
        timestamp = time.time()
        
        with self._lock:
            try:
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
                
                db.session.add(message)
                db.session.commit()
                
                # Update cache
                msg_dict = message.to_dict()
                self._message_cache.append(msg_dict)
                
                self._announce("stream_start", msg_dict)
                return msg_dict
            except Exception as e:
                db.session.rollback()
                raise Exception(f"Failed to start streaming message: {e}")

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
            try:
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
                else:
                    # Log warning if message not found in database
                    import logging
                    logging.warning(f"Message {msg_id} not found in database during finish_streaming")
                
                # Update cache
                msg_dict = None
                for msg in reversed(self._message_cache):
                    if msg["id"] == msg_id:
                        msg["streaming"] = False
                        msg_dict = dict(msg)
                        break
                
                self._announce("stream_end", {"id": msg_id})
                if msg_dict and self._on_message_finalized:
                    self._on_message_finalized(msg_dict)
            except Exception as e:
                db.session.rollback()
                raise Exception(f"Failed to finish streaming message: {e}")

    def cancel_streaming_message(self, msg_id):
        with self._lock:
            try:
                message = Message.query.get(msg_id)
                if message:
                    db.session.delete(message)
                    db.session.commit()

                self._message_cache = [
                    msg for msg in self._message_cache if msg.get("id") != msg_id
                ]
                self._announce("stream_end", {"id": msg_id})
            except Exception as e:
                db.session.rollback()
                raise Exception(f"Failed to cancel streaming message: {e}")

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
            try:
                # Clear database
                Message.query.delete(synchronize_session='fetch')
                db.session.commit()
                
                # Clear cache and reset count
                self._message_cache.clear()
                self._message_count = 0
                
                self._announce("clear", {})
            except Exception as e:
                db.session.rollback()
                raise Exception(f"Failed to clear messages: {e}")

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
