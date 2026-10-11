from flask import Flask, render_template, request, jsonify, Response
import hashlib
import json
import os
import re
import time
from urllib.parse import urlsplit
from sqlalchemy import inspect, text
from werkzeug.exceptions import UnsupportedMediaType

from dotenv import load_dotenv

import llm_clients
from channel_settings import ChannelSettings
from config import (
    DEFAULT_SYSTEM_PROMPT, ENDPOINT_PRESETS, NICKNAME_CHARS, OPERATOR_NAME, OPERATOR_COLOR,
)
from message_store import MessageStore
from bot_manager import BotManager, NicknameTaken
from models import db, Bot, EndpointModels

load_dotenv()

# No CORS: the UI is same-origin, and other websites must not drive this API from a
# user's browser (it can reach local services and holds API keys)
app = Flask(__name__)

# Database configuration
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL',
    f'sqlite:///{os.path.join(basedir, "instance", "llmchannel.db")}'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize database
db.init_app(app)

store = MessageStore()
settings = ChannelSettings()
bot_mgr = BotManager(store, settings)
store.set_on_message_finalized(bot_mgr.handle_message_finalized)
store.set_snapshot_provider(lambda: {"bots": bot_mgr.get_all_bots(), **settings.get_all()})

MAX_SYSTEM_PROMPT_CHARS = 20000
MAX_PERSONA_CHARS = 8000


# ── Legacy Migration ─────────────────────────────────────────────────────

_LEGACY_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "deepseek": "https://api.deepseek.com",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
}


# Per-bot prompts the app used to ship as defaults. They aren't personas, so they're dropped;
# anything else (including edited copies of these) is kept as the bot's persona.
_LEGACY_DEFAULT_PROMPTS = {
    "You are a helpful assistant in a group chat.",
    "You are a helpful assistant participating in a group chat channel.",
} | {
    f"You are {name}, a helpful AI assistant participating in a group chat channel. "
    "Keep your responses concise and conversational. You can see all messages from "
    "the operator and other bots. Engage naturally in the discussion."
    for name in ("Claude", "GPT", "Gemini")
}
# SHA-256 of whitespace-normalized SYSTEM_PROMPT.md as shipped in commits 0aaf515 and 5ff3ae6
_LEGACY_DEFAULT_PROMPT_HASHES = {
    "0a00a9cfc48afd00ca38a8fbd017a53b4f937a336e1f4041b64485aa881e0e9a",
    "24d4850d6b7dc362e8b703b077a26ca5c3d8d5aae48cb246ab38ee02678b6f55",
}


def _is_legacy_default_prompt(prompt):
    normalized = " ".join(prompt.split())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return normalized in _LEGACY_DEFAULT_PROMPTS or digest in _LEGACY_DEFAULT_PROMPT_HASHES


def _legacy_bot_to_fields(row):
    """Map a bot row from the old multi-provider schema to the endpoint-based schema."""
    provider = row.get("provider") or ""
    api_key = row.get("api_key") or ""

    if provider == "ollama":
        # Old schema overloaded api_key as the Ollama base URL
        base_url = (api_key.strip() or "http://localhost:11434").rstrip("/") + "/v1"
        api_key = ""
    elif provider == "openai-compatible":
        base_url, _, api_key = api_key.partition("|")
    else:
        base_url = _LEGACY_BASE_URLS.get(provider, "")

    persona = (row.get("system_prompt") or "").strip()
    if _is_legacy_default_prompt(persona):
        persona = ""

    return {
        "id": row["id"],
        "nickname": row["nickname"],
        "base_url": base_url.strip(),
        "api_key": api_key.strip(),
        "model": row.get("model") or "",
        "persona": persona,
        "color": row.get("color") or "#6B7280",
        # Hosted providers need a key; OAuth-only Gemini bots and unconfigured stock bots
        # lose theirs here, so turn them off instead of letting them fail on every message
        "enabled": bool(row.get("enabled")) and not (
            provider in _LEGACY_BASE_URLS and not api_key.strip()
        ),
    }


def _migrate_legacy_bots():
    """Rebuild the bots table if it still uses the old provider-based schema."""
    inspector = inspect(db.engine)
    if "bots" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("bots")}
    if "provider" not in columns:
        return

    rows = [dict(r) for r in db.session.execute(text("SELECT * FROM bots")).mappings()]
    # Keep the old table until the new rows are committed, so a failure loses nothing
    db.session.execute(text("ALTER TABLE bots RENAME TO bots_legacy"))
    db.session.commit()
    try:
        Bot.__table__.create(db.engine)
        for row in rows:
            db.session.add(Bot(**_legacy_bot_to_fields(row)))
        db.session.commit()
    except Exception:
        db.session.rollback()
        Bot.__table__.drop(db.engine, checkfirst=True)
        db.session.execute(text("ALTER TABLE bots_legacy RENAME TO bots"))
        db.session.commit()
        raise
    db.session.execute(text("DROP TABLE bots_legacy"))
    db.session.commit()
    app.logger.info(f"Migrated {len(rows)} bot(s) to OpenAI-compatible endpoints")


def _migrate_message_conversations():
    """Add messages.conversation_id to databases created before conversations existed."""
    inspector = inspect(db.engine)
    if "messages" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("messages")}
    if "conversation_id" in columns:
        return
    db.session.execute(text("ALTER TABLE messages ADD COLUMN conversation_id VARCHAR(36)"))
    db.session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_messages_conversation_id ON messages (conversation_id)"
    ))
    db.session.commit()


def _migrate_conversation_settings():
    """Add the archived-settings columns to conversations tables created before them."""
    inspector = inspect(db.engine)
    if "conversations" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("conversations")}
    if "system_prompt" not in columns:
        db.session.execute(text("ALTER TABLE conversations ADD COLUMN system_prompt TEXT"))
    if "bot_chain_limit" not in columns:
        db.session.execute(text("ALTER TABLE conversations ADD COLUMN bot_chain_limit INTEGER"))
    db.session.commit()


# Create tables and load defaults
with app.app_context():
    # Ensure instance directory exists
    os.makedirs(os.path.join(basedir, "instance"), exist_ok=True)
    try:
        _migrate_legacy_bots()
        _migrate_message_conversations()
        _migrate_conversation_settings()
        db.create_all()
        store.load_active_conversation()
        settings.load()
        bot_mgr.load_defaults()
    except Exception as e:
        app.logger.error(f"Failed to initialize database: {e}")
        raise


# ── Helpers ──────────────────────────────────────────────────────────────

def _mask(key):
    return ("****" + key[-4:]) if key else ""


def _is_masked(value):
    return isinstance(value, str) and value.startswith("****")


def _origin(url):
    """(scheme, host, effective port) of a URL, for deciding where a stored key may go."""
    parts = urlsplit((url or "").strip())
    scheme = parts.scheme.lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    return scheme, (parts.hostname or "").lower(), port or {"http": 80, "https": 443}.get(scheme)


def _same_origin(url_a, url_b):
    return _origin(url_a) == _origin(url_b)


def _endpoint_url_error(url):
    """Why an endpoint URL is unacceptable, or None if it's fine."""
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        return "Endpoint URL must start with http:// or https://"
    if parts.username is not None or parts.password is not None:
        # They'd be stored and shown unmasked; keys belong in the API key field
        return "Endpoint URL can't contain a username or password; use the API key field"
    try:
        port = parts.port
    except ValueError:
        return "Endpoint URL has an invalid port"
    if not parts.hostname or port == 0:
        return "Endpoint URL needs a host"
    return None


def _parse_int(value):
    """Parse a whole number from JSON (int, integral float, or digit string), else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str) and re.fullmatch(r"\s*-?\d+\s*", value):
        return int(value)
    return None


def _json_body():
    """The request's JSON object. Requiring application/json means another website can't
    send it without a CORS preflight, which this app never grants."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise UnsupportedMediaType("Send a JSON object with Content-Type: application/json")
    return data


@app.errorhandler(UnsupportedMediaType)
def _unsupported_media_type(e):
    return jsonify({"error": e.description}), 415


def _validate_bot_fields(data, bot_id=None):
    """Validate and normalize bot fields from a request body.

    Returns (fields, error). Only keys present in `data` are included in `fields`.
    """
    fields = {}

    if "nickname" in data:
        nickname = str(data["nickname"] or "").strip()
        if not nickname or len(nickname) > 32:
            return None, "Nickname must be 1-32 characters"
        if not re.fullmatch(NICKNAME_CHARS + "+", nickname):
            return None, "Nickname can only use letters, numbers, _ and -"
        if nickname.lower() == OPERATOR_NAME.lower():
            return None, f"Nickname '{OPERATOR_NAME}' is reserved"
        # Uniqueness is checked by BotManager under its lock, atomically with the write
        fields["nickname"] = nickname

    if "base_url" in data:
        base_url = str(data["base_url"] or "").strip().rstrip("/")
        url_error = _endpoint_url_error(base_url)
        if url_error:
            return None, url_error
        fields["base_url"] = base_url

    if "model" in data:
        model = str(data["model"] or "").strip()
        if not model:
            return None, "Model is required"
        fields["model"] = model

    if "persona" in data:
        persona = str(data["persona"] or "")
        if len(persona) > MAX_PERSONA_CHARS:
            return None, f"Persona must be under {MAX_PERSONA_CHARS} characters"
        fields["persona"] = persona

    if "temperature" in data:
        temperature = data["temperature"]
        if temperature in (None, ""):
            fields["temperature"] = None
        else:
            try:
                temperature = float(temperature)
            except (TypeError, ValueError):
                return None, "Temperature must be a number"
            if not 0 <= temperature <= 2:
                return None, "Temperature must be between 0 and 2"
            fields["temperature"] = temperature

    if "max_tokens" in data:
        max_tokens = data["max_tokens"]
        if max_tokens in (None, ""):
            fields["max_tokens"] = None
        else:
            max_tokens = _parse_int(max_tokens)
            if max_tokens is None:
                return None, "Max tokens must be a whole number"
            if max_tokens < 1:
                return None, "Max tokens must be positive"
            fields["max_tokens"] = max_tokens

    if "color" in data:
        fields["color"] = str(data["color"] or "#6B7280")[:20]

    if "enabled" in data:
        if not isinstance(data["enabled"], bool):
            return None, "Enabled must be true or false"
        fields["enabled"] = data["enabled"]

    # Only update API key if a real (non-masked) value was sent; "" clears it
    if "api_key" in data and not _is_masked(data["api_key"]):
        fields["api_key"] = str(data["api_key"] or "").strip()

    # A stored key must never follow the bot to a different origin (scheme, host or port)
    if bot_id and "base_url" in fields and "api_key" not in fields:
        existing = bot_mgr.get_bot(bot_id)
        if existing and existing["api_key"] and not _same_origin(
            existing["base_url"], fields["base_url"]
        ):
            return None, "Endpoint changed: re-enter the API key (or clear it) to save"

    return fields, None


# ── Pages ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── Messages API ─────────────────────────────────────────────────────────

@app.route("/api/messages")
def get_messages():
    return jsonify(store.get_history())


@app.route("/api/messages", methods=["POST"])
def send_message():
    data = _json_body()
    content = data.get("content", "").strip()
    if not content:
        return jsonify({"error": "Empty message"}), 400

    msg = store.add_message(
        sender=OPERATOR_NAME,
        content=content,
        msg_type="operator",
        color=OPERATOR_COLOR,
    )
    return jsonify(msg), 201


# ── Conversations API ────────────────────────────────────────────────────

@app.route("/api/conversations")
def list_conversations():
    return jsonify(store.list_conversations())


@app.route("/api/conversations", methods=["POST"])
def new_conversation():
    """Archive the live conversation and start a new one."""
    _json_body()  # Not a simple request, so other websites can't trigger it
    return jsonify(store.new_conversation()), 201


@app.route("/api/conversations/<conversation_id>")
def get_conversation(conversation_id):
    conv = store.get_conversation(conversation_id)
    if not conv:
        return jsonify({"error": "Conversation not found"}), 404
    conv["messages"] = store.get_conversation_messages(conversation_id)
    return jsonify(conv)


@app.route("/api/conversations/<conversation_id>", methods=["PUT"])
def rename_conversation(conversation_id):
    data = _json_body()
    title = " ".join(str(data.get("title") or "").split())[:200]
    conv = store.rename_conversation(conversation_id, title)
    if not conv:
        return jsonify({"error": "Conversation not found"}), 404
    return jsonify(conv)


@app.route("/api/conversations/<conversation_id>", methods=["DELETE"])
def delete_conversation(conversation_id):
    try:
        deleted = store.delete_conversation(conversation_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not deleted:
        return jsonify({"error": "Conversation not found"}), 404
    return jsonify({"ok": True})


# ── SSE Stream ───────────────────────────────────────────────────────────

@app.route("/api/stream")
def stream():
    def event_stream():
        for data in store.listen():
            yield data

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── Channel Settings API ─────────────────────────────────────────────────

@app.route("/api/settings")
def get_settings():
    data = settings.get_all()
    data["default_system_prompt"] = DEFAULT_SYSTEM_PROMPT
    data["endpoint_presets"] = ENDPOINT_PRESETS
    return jsonify(data)


@app.route("/api/settings", methods=["PUT"])
def update_settings():
    data = _json_body()
    updates = {}

    if "system_prompt" in data:
        prompt = str(data["system_prompt"] or "").strip()
        if not prompt:
            prompt = DEFAULT_SYSTEM_PROMPT
        if len(prompt) > MAX_SYSTEM_PROMPT_CHARS:
            return jsonify({
                "error": f"System prompt must be under {MAX_SYSTEM_PROMPT_CHARS} characters"
            }), 400
        updates["system_prompt"] = prompt

    if "bot_chain_limit" in data:
        limit = _parse_int(data["bot_chain_limit"])
        if limit is None:
            return jsonify({"error": "Bot reply limit must be a whole number"}), 400
        if not 0 <= limit <= 100:
            return jsonify({"error": "Bot reply limit must be between 0 and 100"}), 400
        updates["bot_chain_limit"] = limit

    return jsonify(settings.update(**updates))


# ── Bots API ─────────────────────────────────────────────────────────────

@app.route("/api/bots")
def get_bots():
    safe = []
    for b in bot_mgr.get_all_bots():
        sb = dict(b)
        sb["api_key"] = _mask(sb.get("api_key", ""))
        safe.append(sb)
    return jsonify(safe)


@app.route("/api/bots", methods=["POST"])
def add_bot():
    data = _json_body()
    payload = {
        "nickname": "Bot",
        "base_url": ENDPOINT_PRESETS["openrouter"],
        "api_key": "",
        "model": "openai/gpt-4o-mini",
        "persona": "",
        "color": "#6366f1",
        "enabled": False,
    }
    payload.update(data)
    fields, error = _validate_bot_fields(payload)
    if error:
        return jsonify({"error": error}), 400
    try:
        bot = bot_mgr.add_bot(**fields)
    except NicknameTaken as e:
        return jsonify({"error": str(e)}), 400
    bot["api_key"] = _mask(bot["api_key"])
    return jsonify(bot), 201


@app.route("/api/bots/<bot_id>", methods=["PUT"])
def update_bot(bot_id):
    data = _json_body()
    if not bot_mgr.get_bot(bot_id):
        return jsonify({"error": "Bot not found"}), 404

    fields, error = _validate_bot_fields(data, bot_id=bot_id)
    if error:
        return jsonify({"error": error}), 400

    try:
        bot = bot_mgr.update_bot(bot_id, **fields)
    except NicknameTaken as e:
        return jsonify({"error": str(e)}), 400
    if not bot:
        return jsonify({"error": "Bot not found"}), 404
    bot["api_key"] = _mask(bot["api_key"])
    return jsonify(bot)


@app.route("/api/bots/<bot_id>", methods=["DELETE"])
def delete_bot(bot_id):
    removed = bot_mgr.remove_bot(bot_id)
    if not removed:
        return jsonify({"error": "Bot not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/bots/<bot_id>/toggle", methods=["POST"])
def toggle_bot(bot_id):
    data = _json_body()
    enabled = data.get("enabled", True)
    if not isinstance(enabled, bool):
        return jsonify({"error": "Enabled must be true or false"}), 400
    bot = bot_mgr.set_bot_enabled(bot_id, enabled)
    if not bot:
        return jsonify({"error": "Bot not found"}), 404
    bot["api_key"] = _mask(bot["api_key"])
    return jsonify(bot)


def _cache_models(base_url, models):
    """Store an endpoint's model list, replacing any earlier fetch. Returns the cache entry."""
    key = llm_clients.normalize_base_url(base_url)
    fetched_at = time.time()
    try:
        row = db.session.get(EndpointModels, key)
        if row is None:
            row = EndpointModels(base_url=key)
            db.session.add(row)
        row.models = json.dumps(models)
        row.fetched_at = fetched_at
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        app.logger.warning(f"Failed to cache model list for {key}: {e}")
    return {"models": models, "fetched_at": fetched_at}


@app.route("/api/models")
def cached_models():
    """All cached model lists, keyed by normalized endpoint URL. Makes no network calls."""
    cache = {}
    for row in EndpointModels.query.all():
        try:
            models = json.loads(row.models)
        except ValueError:
            continue
        cache[row.base_url] = {"models": models, "fetched_at": row.fetched_at}
    return jsonify(cache)


@app.route("/api/models", methods=["POST"])
def list_models():
    """Fetch models from an endpoint and cache them.

    Uses the stored key when the UI sends a masked one.
    """
    data = _json_body()
    base_url = str(data.get("base_url") or "").strip()
    api_key = data.get("api_key") or ""
    url_error = _endpoint_url_error(base_url)
    if url_error:
        return jsonify({"error": url_error}), 400

    if _is_masked(api_key):
        # Never send a stored key anywhere but the endpoint it was saved for
        bot = bot_mgr.get_bot(data.get("bot_id", ""))
        same_endpoint = bot and _same_origin(bot["base_url"], base_url)
        api_key = bot["api_key"] if same_endpoint else ""

    try:
        models = llm_clients.list_models(base_url, api_key)
    except Exception as e:
        return jsonify({"error": f"Could not list models: {e}"}), 502

    entry = _cache_models(base_url, models)
    entry["base_url"] = llm_clients.normalize_base_url(base_url)
    return jsonify(entry)


# ── Run ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True, threaded=True, port=5000)
