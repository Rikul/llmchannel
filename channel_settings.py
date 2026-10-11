import threading

from config import DEFAULT_BOT_CHAIN_LIMIT, DEFAULT_SYSTEM_PROMPT
from models import db, Setting

DEFAULTS = {
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
    "bot_chain_limit": DEFAULT_BOT_CHAIN_LIMIT,
}


class ChannelSettings:
    """Operator-controlled channel settings, persisted in the settings table and cached."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cache = dict(DEFAULTS)

    def load(self):
        """Load stored values over the defaults. Requires an app context."""
        with self._lock:
            for row in Setting.query.all():
                if row.key == "system_prompt":
                    self._cache["system_prompt"] = row.value
                elif row.key == "bot_chain_limit":
                    try:
                        self._cache["bot_chain_limit"] = int(row.value)
                    except ValueError:
                        pass

    def get(self, key):
        with self._lock:
            return self._cache[key]

    def get_all(self):
        with self._lock:
            return dict(self._cache)

    def update(self, **values):
        """Persist and cache the given values. Requires an app context."""
        with self._lock:
            try:
                values = {k: v for k, v in values.items() if k in DEFAULTS}
                for key, value in values.items():
                    row = db.session.get(Setting, key)
                    if row is None:
                        db.session.add(Setting(key=key, value=str(value)))
                    else:
                        row.value = str(value)
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                raise Exception(f"Failed to update settings: {e}")
            self._cache.update(values)
            return dict(self._cache)
