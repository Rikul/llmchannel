import json
import queue
import time
import uuid
from threading import Lock

from sqlalchemy import func

from config import MAX_HISTORY
from models import db, Conversation, Message

TITLE_MAX_CHARS = 60


def _make_title(text):
    title = " ".join(text.split())
    if len(title) > TITLE_MAX_CHARS:
        title = title[:TITLE_MAX_CHARS - 1].rstrip() + "…"
    return title


class MessageStore:
    """Messages of the live conversation, plus archiving and reading past conversations.

    Every message is kept in SQLite. Only the live conversation is cached in memory
    (its last MAX_HISTORY messages), and only it accepts new messages.
    """

    def __init__(self):
        self._lock = Lock()
        self._listeners = []
        self._message_cache = []  # Live conversation, oldest first
        self._conversation_id = None
        self._on_message_finalized = None

    def set_on_message_finalized(self, callback):
        self._on_message_finalized = callback

    # ── Conversations ────────────────────────────────────────────────

    def load_active_conversation(self):
        """Pick up the live conversation from the database, creating one if needed.

        Requires an app context. Also adopts messages saved before conversations existed.
        """
        with self._lock:
            try:
                conv = (
                    Conversation.query.filter(Conversation.archived_at.is_(None))
                    .order_by(Conversation.created_at.desc())
                    .first()
                )
                if conv is None:
                    conv = self._create_conversation()
                Message.query.filter(Message.conversation_id.is_(None)).update(
                    {"conversation_id": conv.id}, synchronize_session=False
                )
                if not conv.title:
                    first = (
                        Message.query.filter_by(conversation_id=conv.id, msg_type="operator")
                        .order_by(Message.timestamp)
                        .first()
                    )
                    if first:
                        conv.title = _make_title(first.content)
                # Bot replies cut off by a restart can never finish. Tokens only reach the
                # database when a stream ends, so these are usually empty placeholders.
                for row in Message.query.filter(Message.streaming.is_(True)).all():
                    if row.content.strip():
                        row.streaming = False
                    else:
                        db.session.delete(row)
                db.session.commit()
                self._conversation_id = conv.id
                self._message_cache = self._load_messages(conv.id, limit=MAX_HISTORY)
            except Exception as e:
                db.session.rollback()
                raise Exception(f"Failed to load conversation: {e}")

    @property
    def active_conversation_id(self):
        with self._lock:
            return self._conversation_id

    def new_conversation(self):
        """Archive the live conversation and start an empty one.

        Does nothing if the live conversation has no messages. Returns the live conversation.
        """
        with self._lock:
            try:
                current = db.session.get(Conversation, self._conversation_id)
                if current is not None and not self._message_cache:
                    return current.to_dict(message_count=0)

                self._freeze_streaming_messages()
                if current is not None:
                    current.archived_at = time.time()
                conv = self._create_conversation()
                db.session.commit()

                self._conversation_id = conv.id
                self._message_cache = []
                data = conv.to_dict(message_count=0)
                self._announce("conversation_changed", data)
                return data
            except Exception as e:
                db.session.rollback()
                raise Exception(f"Failed to start new conversation: {e}")

    def list_conversations(self):
        """All conversations, live first, then archived newest first, with message counts."""
        with self._lock:
            counts = dict(
                db.session.query(Message.conversation_id, func.count(Message.id))
                .group_by(Message.conversation_id)
                .all()
            )
            convs = Conversation.query.order_by(Conversation.created_at.desc()).all()
            result = [c.to_dict(message_count=counts.get(c.id, 0)) for c in convs]
            result.sort(key=lambda c: c["archived"])
            return result

    def get_conversation(self, conversation_id):
        with self._lock:
            conv = db.session.get(Conversation, conversation_id)
            if conv is None:
                return None
            count = Message.query.filter_by(conversation_id=conversation_id).count()
            return conv.to_dict(message_count=count)

    def get_conversation_messages(self, conversation_id):
        """All messages of any conversation (live or archived), oldest first."""
        with self._lock:
            if conversation_id == self._conversation_id:
                return [dict(m) for m in self._message_cache]
            return self._load_messages(conversation_id)

    def rename_conversation(self, conversation_id, title):
        with self._lock:
            try:
                conv = db.session.get(Conversation, conversation_id)
                if conv is None:
                    return None
                conv.title = title
                db.session.commit()
                data = conv.to_dict()
            except Exception as e:
                db.session.rollback()
                raise Exception(f"Failed to rename conversation: {e}")
        self._announce("conversations_updated", {})
        return data

    def delete_conversation(self, conversation_id):
        """Delete an archived conversation and its messages. The live one can't be deleted."""
        with self._lock:
            if conversation_id == self._conversation_id:
                raise ValueError("The live conversation can't be deleted")
            try:
                conv = db.session.get(Conversation, conversation_id)
                if conv is None:
                    return False
                Message.query.filter_by(conversation_id=conversation_id).delete(
                    synchronize_session=False
                )
                db.session.delete(conv)
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                raise Exception(f"Failed to delete conversation: {e}")
        self._announce("conversations_updated", {})
        return True

    def _create_conversation(self):
        now = time.time()
        conv = Conversation(id=str(uuid.uuid4()), title="", created_at=now, updated_at=now)
        db.session.add(conv)
        db.session.flush()
        return conv

    def _load_messages(self, conversation_id, limit=None):
        query = Message.query.filter_by(conversation_id=conversation_id)
        if limit:
            rows = query.order_by(Message.timestamp.desc()).limit(limit).all()
            rows.reverse()
        else:
            rows = query.order_by(Message.timestamp).all()
        return [m.to_dict() for m in rows]

    def _freeze_streaming_messages(self):
        """Save half-streamed bot replies as they are so the archive is consistent."""
        for msg in self._message_cache:
            if not msg.get("streaming"):
                continue
            row = db.session.get(Message, msg["id"])
            if row is None:
                continue
            if msg["content"].strip():
                row.content = msg["content"]
                row.streaming = False
            else:
                db.session.delete(row)

    def _touch_conversation(self, timestamp, title_source=None):
        conv = db.session.get(Conversation, self._conversation_id)
        if conv is None:
            return
        conv.updated_at = timestamp
        if title_source and not conv.title:
            conv.title = _make_title(title_source)

    # ── Messages (live conversation) ─────────────────────────────────

    def add_message(self, sender, content, msg_type, color, msg_id=None):
        msg_id = msg_id or str(uuid.uuid4())
        timestamp = time.time()

        with self._lock:
            try:
                message = Message(
                    id=msg_id,
                    conversation_id=self._conversation_id,
                    sender=sender,
                    content=content,
                    timestamp=timestamp,
                    msg_type=msg_type,
                    color=color,
                    streaming=False
                )
                db.session.add(message)
                # The operator's first message names the conversation
                self._touch_conversation(
                    timestamp, title_source=content if msg_type == "operator" else None
                )
                db.session.commit()

                msg_dict = message.to_dict()
                self._append_to_cache(msg_dict)

                self._announce("new_message", msg_dict)
                if self._on_message_finalized:
                    self._on_message_finalized(msg_dict)
                return msg_dict
            except Exception as e:
                db.session.rollback()
                raise Exception(f"Failed to add message: {e}")

    def start_streaming_message(self, sender, color, msg_id, conversation_id=None):
        """Create a placeholder bot message in the live conversation.

        Returns None (and creates nothing) if `conversation_id` is given and no longer live.
        """
        timestamp = time.time()

        with self._lock:
            if conversation_id is not None and conversation_id != self._conversation_id:
                return None
            try:
                message = Message(
                    id=msg_id,
                    conversation_id=self._conversation_id,
                    sender=sender,
                    content='',
                    timestamp=timestamp,
                    msg_type='bot',
                    color=color,
                    streaming=True
                )
                db.session.add(message)
                db.session.commit()

                msg_dict = message.to_dict()
                self._append_to_cache(msg_dict)

                self._announce("stream_start", msg_dict)
                return msg_dict
            except Exception as e:
                db.session.rollback()
                raise Exception(f"Failed to start streaming message: {e}")

    def is_streaming(self, msg_id):
        """True while the message is still an open stream in the live conversation."""
        with self._lock:
            msg = self._find_cached(msg_id)
            return bool(msg and msg.get("streaming"))

    def append_to_streaming(self, msg_id, token):
        with self._lock:
            # Update cache only - will persist when streaming finishes
            msg = self._find_cached(msg_id)
            if msg is None or not msg.get("streaming"):
                return False
            msg["content"] += token
            self._announce("stream_token", {"id": msg_id, "token": token})
            return True

    def finish_streaming(self, msg_id):
        with self._lock:
            msg = self._find_cached(msg_id)
            if msg is None or not msg.get("streaming"):
                return  # Archived mid-stream; already saved by _freeze_streaming_messages
            try:
                message = db.session.get(Message, msg_id)
                if message:
                    message.content = msg["content"]
                    message.streaming = False
                    self._touch_conversation(time.time())
                    db.session.commit()
                else:
                    import logging
                    logging.warning(f"Message {msg_id} not found in database during finish_streaming")

                msg["streaming"] = False
                msg_dict = dict(msg)

                self._announce("stream_end", {"id": msg_id})
                if self._on_message_finalized:
                    self._on_message_finalized(msg_dict)
            except Exception as e:
                db.session.rollback()
                raise Exception(f"Failed to finish streaming message: {e}")

    def cancel_streaming_message(self, msg_id):
        with self._lock:
            msg = self._find_cached(msg_id)
            if msg is None or not msg.get("streaming"):
                return
            try:
                message = db.session.get(Message, msg_id)
                if message:
                    db.session.delete(message)
                    db.session.commit()

                self._message_cache = [
                    m for m in self._message_cache if m.get("id") != msg_id
                ]
                self._announce("stream_end", {"id": msg_id})
            except Exception as e:
                db.session.rollback()
                raise Exception(f"Failed to cancel streaming message: {e}")

    def get_history(self):
        """Recent messages of the live conversation (copies), oldest first."""
        with self._lock:
            return [dict(m) for m in self._message_cache]

    def _find_cached(self, msg_id):
        for msg in reversed(self._message_cache):
            if msg["id"] == msg_id:
                return msg
        return None

    def _append_to_cache(self, msg_dict):
        self._message_cache.append(msg_dict)
        if len(self._message_cache) > MAX_HISTORY:
            self._message_cache = self._message_cache[-MAX_HISTORY:]

    # ── Server-Sent Events ───────────────────────────────────────────

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
        for q in list(self._listeners):
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(q)
        for q in dead:
            try:
                self._listeners.remove(q)
            except ValueError:
                pass
