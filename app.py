from flask import Flask, render_template, request, jsonify, Response
from flask_cors import CORS
import os

from config import OPERATOR_NAME, OPERATOR_COLOR
from message_store import MessageStore
from bot_manager import BotManager
from models import db

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
bot_mgr = BotManager(store)

# Create tables and load defaults
with app.app_context():
    # Ensure instance directory exists
    os.makedirs(os.path.join(basedir, "instance"), exist_ok=True)
    try:
        db.create_all()
        bot_mgr.load_defaults()
    except Exception as e:
        app.logger.error(f"Failed to initialize database: {e}")
        raise


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
    bot_mgr.dispatch_to_all(msg)
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


# ── Bots API ─────────────────────────────────────────────────────────────

@app.route("/api/bots")
def get_bots():
    bots = bot_mgr.get_all_bots()
    safe = []
    for b in bots:
        sb = dict(b)
        key = sb.get("api_key", "")
        sb["api_key"] = ("****" + key[-4:]) if key else ""
        safe.append(sb)
    return jsonify(safe)


@app.route("/api/bots", methods=["POST"])
def add_bot():
    data = request.get_json(force=True)
    bot = bot_mgr.add_bot(
        nickname=data.get("nickname", "Bot"),
        provider=data.get("provider", "openai"),
        model=data.get("model", "gpt-4o-mini"),
        api_key=data.get("api_key", ""),
        system_prompt=data.get("system_prompt", "You are a helpful assistant in a group chat."),
        color=data.get("color", "#6B7280"),
    )
    return jsonify(bot), 201


@app.route("/api/bots/<bot_id>", methods=["PUT"])
def update_bot(bot_id):
    data = request.get_json(force=True)
    existing = bot_mgr.get_bot(bot_id)
    if not existing:
        return jsonify({"error": "Bot not found"}), 404

    updates = {}
    for field in ("nickname", "provider", "model", "system_prompt", "color", "enabled"):
        if field in data:
            updates[field] = data[field]

    # Only update API key if a real (non-masked) value was sent
    if "api_key" in data and not data["api_key"].startswith("****"):
        updates["api_key"] = data["api_key"]

    bot = bot_mgr.update_bot(bot_id, **updates)
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
    enabled = data.get("enabled", True)
    bot = bot_mgr.set_bot_enabled(bot_id, enabled)
    if not bot:
        return jsonify({"error": "Bot not found"}), 404
    return jsonify(bot)


# ── Run ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True, threaded=True, port=5000)
