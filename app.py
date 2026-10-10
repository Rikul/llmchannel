from flask import Flask, render_template, request, jsonify, Response
from flask_cors import CORS
import os
from sqlalchemy import inspect, text

from dotenv import load_dotenv

import llm_clients
from channel_settings import ChannelSettings
from config import (
    DEFAULT_SYSTEM_PROMPT, ENDPOINT_PRESETS, OPERATOR_NAME, OPERATOR_COLOR,
)
from message_store import MessageStore
from bot_manager import BotManager
from models import db, Bot

load_dotenv()

app = Flask(__name__)
CORS(app)

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

    # Old bots stored the full channel prompt per bot; only keep genuinely custom personas
    persona = (row.get("system_prompt") or "").strip()
    if "{{CHAT_HISTORY}}" in persona or persona.startswith("You are a helpful assistant"):
        persona = ""

    return {
        "id": row["id"],
        "nickname": row["nickname"],
        "base_url": base_url.strip(),
        "api_key": api_key.strip(),
        "model": row.get("model") or "",
        "persona": persona,
        "color": row.get("color") or "#6B7280",
        "enabled": bool(row.get("enabled")),
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
    db.session.execute(text("DROP TABLE bots"))
    db.session.commit()
    db.create_all()
    for row in rows:
        db.session.add(Bot(**_legacy_bot_to_fields(row)))
    db.session.commit()
    app.logger.info(f"Migrated {len(rows)} bot(s) to OpenAI-compatible endpoints")


# Create tables and load defaults
with app.app_context():
    # Ensure instance directory exists
    os.makedirs(os.path.join(basedir, "instance"), exist_ok=True)
    try:
        _migrate_legacy_bots()
        db.create_all()
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


def _validate_bot_fields(data, bot_id=None):
    """Validate and normalize bot fields from a request body.

    Returns (fields, error). Only keys present in `data` are included in `fields`.
    """
    fields = {}

    if "nickname" in data:
        nickname = str(data["nickname"] or "").strip()
        if not nickname or len(nickname) > 32:
            return None, "Nickname must be 1-32 characters"
        if any(c in nickname for c in "[]@:") or any(c.isspace() for c in nickname):
            return None, "Nickname cannot contain spaces or [ ] @ :"
        if nickname.lower() == OPERATOR_NAME.lower():
            return None, f"Nickname '{OPERATOR_NAME}' is reserved"
        for other in bot_mgr.get_all_bots():
            if other["id"] != bot_id and other["nickname"].lower() == nickname.lower():
                return None, f"Another bot is already named '{other['nickname']}'"
        fields["nickname"] = nickname

    if "base_url" in data:
        base_url = str(data["base_url"] or "").strip().rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            return None, "Endpoint URL must start with http:// or https://"
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
            try:
                max_tokens = int(max_tokens)
            except (TypeError, ValueError):
                return None, "Max tokens must be a whole number"
            if max_tokens < 1:
                return None, "Max tokens must be positive"
            fields["max_tokens"] = max_tokens

    if "color" in data:
        fields["color"] = str(data["color"] or "#6B7280")[:20]

    if "enabled" in data:
        fields["enabled"] = bool(data["enabled"])

    # Only update API key if a real (non-masked) value was sent; "" clears it
    if "api_key" in data and not _is_masked(data["api_key"]):
        fields["api_key"] = str(data["api_key"] or "").strip()

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
    data = request.get_json(force=True)
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


@app.route("/api/clear", methods=["POST"])
def clear_messages():
    store.clear()
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
    data = request.get_json(force=True)
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
        try:
            limit = int(data["bot_chain_limit"])
        except (TypeError, ValueError):
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
    data = request.get_json(force=True)
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
    bot = bot_mgr.add_bot(**fields)
    bot["api_key"] = _mask(bot["api_key"])
    return jsonify(bot), 201


@app.route("/api/bots/<bot_id>", methods=["PUT"])
def update_bot(bot_id):
    data = request.get_json(force=True)
    if not bot_mgr.get_bot(bot_id):
        return jsonify({"error": "Bot not found"}), 404

    fields, error = _validate_bot_fields(data, bot_id=bot_id)
    if error:
        return jsonify({"error": error}), 400

    bot = bot_mgr.update_bot(bot_id, **fields)
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
    data = request.get_json(force=True)
    bot = bot_mgr.set_bot_enabled(bot_id, data.get("enabled", True))
    if not bot:
        return jsonify({"error": "Bot not found"}), 404
    bot["api_key"] = _mask(bot["api_key"])
    return jsonify(bot)


@app.route("/api/models", methods=["POST"])
def list_models():
    """List models from an endpoint. Uses the stored key when the UI sends a masked one."""
    data = request.get_json(force=True)
    base_url = str(data.get("base_url") or "").strip()
    api_key = data.get("api_key") or ""
    if not base_url.startswith(("http://", "https://")):
        return jsonify({"error": "Endpoint URL must start with http:// or https://"}), 400

    if _is_masked(api_key):
        # Never send a stored key anywhere but the endpoint it was saved for
        bot = bot_mgr.get_bot(data.get("bot_id", ""))
        same_endpoint = bot and bot["base_url"].rstrip("/") == base_url.rstrip("/")
        api_key = bot["api_key"] if same_endpoint else ""

    try:
        models = llm_clients.list_models(base_url, api_key)
    except Exception as e:
        return jsonify({"error": f"Could not list models: {e}"}), 502
    return jsonify({"models": models})


# ── Run ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True, threaded=True, port=5000)
