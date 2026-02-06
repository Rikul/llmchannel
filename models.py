from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone

db = SQLAlchemy()


class Bot(db.Model):
    """Model for bot configurations."""
    __tablename__ = 'bots'

    id = db.Column(db.String(36), primary_key=True)
    nickname = db.Column(db.String(100), nullable=False)
    provider = db.Column(db.String(50), nullable=False)
    model = db.Column(db.String(100), nullable=False)
    api_key = db.Column(db.String(500), nullable=True, default='')
    system_prompt = db.Column(db.Text, nullable=False)
    color = db.Column(db.String(20), nullable=False)
    enabled = db.Column(db.Boolean, nullable=False, default=True)

    def to_dict(self):
        """Convert bot to dictionary."""
        return {
            'id': self.id,
            'nickname': self.nickname,
            'provider': self.provider,
            'model': self.model,
            'api_key': self.api_key,
            'system_prompt': self.system_prompt,
            'color': self.color,
            'enabled': self.enabled,
        }


class Message(db.Model):
    """Model for chat messages."""
    __tablename__ = 'messages'

    id = db.Column(db.String(36), primary_key=True)
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
            'sender': self.sender,
            'content': self.content,
            'timestamp': self.timestamp,
            'type': self.msg_type,
            'color': self.color,
            'streaming': self.streaming,
        }


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
