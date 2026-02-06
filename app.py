from flask import Flask, render_template, request, jsonify, Response, redirect, url_for
from flask_cors import CORS
import os
import threading
import uuid
from sqlalchemy import inspect, text

from config import OPERATOR_NAME, OPERATOR_COLOR
from message_store import MessageStore
from bot_manager import BotManager
from models import db

app = Flask(__name__)
CORS(app)

OAUTH_STATE = {}
OAUTH_STATE_LOCK = threading.Lock()

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


def _ensure_bot_columns():
    inspector = inspect(db.engine)
    columns = [col["name"] for col in inspector.get_columns("bots")]
    if "gemini_access_token" not in columns:
        db.session.execute(text(
            "ALTER TABLE bots ADD COLUMN gemini_access_token VARCHAR(2048) DEFAULT ''"
        ))
        db.session.commit()


def _get_google_oauth_config():
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    redirect_uri = os.environ.get("GOOGLE_OAUTH_REDIRECT_URL", "")
    scopes_raw = os.environ.get(
        "GOOGLE_OAUTH_SCOPES",
        "https://www.googleapis.com/auth/generative-language"
    )

    if not client_id or not client_secret:
        return None

    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "scopes": [s.strip() for s in scopes_raw.split(",") if s.strip()],
    }

# Create tables and load defaults
with app.app_context():
    # Ensure instance directory exists
    os.makedirs(os.path.join(basedir, "instance"), exist_ok=True)
    try:
        db.create_all()
        _ensure_bot_columns()
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
        token = sb.get("gemini_access_token", "")
        sb["gemini_access_token"] = ("****" + token[-4:]) if token else ""
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

    # Only update OAuth token if a real (non-masked) value was sent
    if "gemini_access_token" in data and not data["gemini_access_token"].startswith("****"):
        updates["gemini_access_token"] = data["gemini_access_token"]

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


@app.route("/api/bots/<bot_id>/google/login")
def google_oauth_login(bot_id):
    config = _get_google_oauth_config()
    if not config:
        return jsonify({"error": "Google OAuth not configured"}), 500

    if not config["redirect_uri"]:
        config["redirect_uri"] = url_for("google_oauth_callback", _external=True)

    bot = bot_mgr.get_bot(bot_id)
    if not bot:
        return jsonify({"error": "Bot not found"}), 404

    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
                "redirect_uris": [config["redirect_uri"]],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=config["scopes"],
        redirect_uri=config["redirect_uri"],
    )

    state = str(uuid.uuid4())
    with OAUTH_STATE_LOCK:
        OAUTH_STATE[state] = bot_id

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return redirect(auth_url)


@app.route("/api/oauth/google/callback")
def google_oauth_callback():
    config = _get_google_oauth_config()
    if not config:
        return jsonify({"error": "Google OAuth not configured"}), 500

    if not config["redirect_uri"]:
        config["redirect_uri"] = url_for("google_oauth_callback", _external=True)

    state = request.args.get("state", "")
    if not state:
        return jsonify({"error": "Missing OAuth state"}), 400

    with OAUTH_STATE_LOCK:
        bot_id = OAUTH_STATE.pop(state, "")

    if not bot_id:
        return jsonify({"error": "Invalid OAuth state"}), 400

    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
                "redirect_uris": [config["redirect_uri"]],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=config["scopes"],
        redirect_uri=config["redirect_uri"],
        state=state,
    )

    flow.fetch_token(authorization_response=request.url)
    token = flow.credentials.token

    if not token:
        return jsonify({"error": "Missing access token"}), 400

    bot_mgr.update_bot(bot_id, gemini_access_token=token)
    return redirect(url_for("index"))


# ── Run ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True, threaded=True, port=5000)
