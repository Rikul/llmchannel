from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone

db = SQLAlchemy()


class Bot(db.Model):
    """Model for bot configurations (any OpenAI-compatible endpoint)."""
    __tablename__ = 'bots'

    id = db.Column(db.String(36), primary_key=True)
    nickname = db.Column(db.String(100), nullable=False)
    base_url = db.Column(db.String(500), nullable=False, default='')
    api_key = db.Column(db.String(500), nullable=True, default='')
    model = db.Column(db.String(200), nullable=False)
    persona = db.Column(db.Text, nullable=False, default='')
    temperature = db.Column(db.Float, nullable=True)
    max_tokens = db.Column(db.Integer, nullable=True)
    color = db.Column(db.String(20), nullable=False)
    enabled = db.Column(db.Boolean, nullable=False, default=True)

    def to_dict(self):
        """Convert bot to dictionary."""
        return {
            'id': self.id,
            'nickname': self.nickname,
            'base_url': self.base_url,
            'api_key': self.api_key,
            'model': self.model,
            'persona': self.persona,
            'temperature': self.temperature,
            'max_tokens': self.max_tokens,
            'color': self.color,
            'enabled': self.enabled,
        }


class Conversation(db.Model):
    """A chat session. Exactly one is live (archived_at is None); the rest are archived."""
    __tablename__ = 'conversations'

    id = db.Column(db.String(36), primary_key=True)
    title = db.Column(db.String(200), nullable=False, default='')
    created_at = db.Column(db.Float, nullable=False)
    updated_at = db.Column(db.Float, nullable=False)
    archived_at = db.Column(db.Float, nullable=True, index=True)

    def to_dict(self, message_count=None):
        """Convert conversation to dictionary."""
        data = {
            'id': self.id,
            'title': self.title,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'archived_at': self.archived_at,
            'archived': self.archived_at is not None,
        }
        if message_count is not None:
            data['message_count'] = message_count
        return data


class Message(db.Model):
    """Model for chat messages."""
    __tablename__ = 'messages'

    id = db.Column(db.String(36), primary_key=True)
    conversation_id = db.Column(db.String(36), nullable=True, index=True)
    sender = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.Float, nullable=False, index=True)
    msg_type = db.Column(db.String(20), nullable=False)
    color = db.Column(db.String(20), nullable=False)
    streaming = db.Column(db.Boolean, nullable=False, default=False)

    def to_dict(self):
        """Convert message to dictionary."""
        return {
            'id': self.id,
            'conversation_id': self.conversation_id,
            'sender': self.sender,
            'content': self.content,
            'timestamp': self.timestamp,
            'type': self.msg_type,
            'color': self.color,
            'streaming': self.streaming,
        }


class EndpointModels(db.Model):
    """Model IDs last fetched from an endpoint's /models, keyed by normalized base URL."""
    __tablename__ = 'endpoint_models'

    base_url = db.Column(db.String(500), primary_key=True)
    models = db.Column(db.Text, nullable=False, default='[]')  # JSON list of model IDs
    fetched_at = db.Column(db.Float, nullable=False)


class Setting(db.Model):
    """Model for app settings."""
    __tablename__ = 'settings'

    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        """Convert setting to dictionary."""
        return {
            'key': self.key,
            'value': self.value,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
