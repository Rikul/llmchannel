# Agent Guidelines for LLM Channel

LLM Channel is a Flask and jQuery application for IRC-style conversations between one human
operator and multiple bots. Bots connect through OpenAI-compatible chat-completions APIs.

## Build, Run, and Test

Python 3.12 or newer is required.

```bash
# Set up and run with uv
uv sync
uv run app.py

# Or set up with pip
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py

# Run the existing unit tests
uv run python -m unittest discover tests -v
# Or, from an activated pip environment
python -m unittest discover tests -v
```

The development server listens on <http://localhost:5000>. Importing `app.py` initializes the
database, runs compatibility migrations, restores or creates the live conversation, loads
settings, and creates the default disabled bots if the `bots` table is empty.

## Source Map

- `app.py`: Flask setup, startup migrations, validation, pages, and JSON/SSE routes.
- `bot_manager.py`: bot CRUD, mention targeting, reply scheduling, background workers, and
  streamed-reply filtering.
- `message_store.py`: the live-message cache, SQLite persistence, conversation archives, and
  SSE listener fan-out.
- `llm_clients.py`: system-prompt construction, channel-history formatting, OpenAI client
  creation, streamed completions, and model listing.
- `channel_settings.py`: persisted and in-memory channel settings.
- `models.py`: SQLAlchemy models.
- `config.py`: constants, endpoint presets, default bots, and default prompt loading.
- `SYSTEM_PROMPT.md`: the default shared prompt sent to bots.
- `templates/index.html`: the single Jinja page.
- `static/js/app.js`: chat, conversation navigation, SSE, and participant sidebar behavior.
- `static/js/settings.js`: channel and bot settings UI.
- `static/js/theme.js`: persisted light/dark theme selection.
- `static/css/styles.css`: application styling and theme variables.
- `tests/test_bots.py`: unit tests for prompt, history, mention, URL, and reply-filter behavior.

## Code Style

### Python

- Use 4-space indentation, double quotes for new strings, and a 100-character line limit.
- Use `snake_case` for functions, variables, and modules; `PascalCase` for classes; and
  `UPPER_CASE` for module constants.
- Prefix private helpers with an underscore.
- Group imports as standard library, third-party packages, then absolute local imports.
- Keep two blank lines before top-level functions and classes and one between methods.
- Use `# ── Section Name ──` headings to organize larger modules.
- Add Google-style docstrings to public functions where their contract is not obvious.

```python
def stream_response(base_url, api_key, model, messages, system_prompt,
                    temperature=None, max_tokens=None):
    """Generator that yields tokens from an OpenAI-compatible endpoint."""
```

### JavaScript

- Use 4-space indentation, single-quoted strings, semicolons, and a 100-character line limit.
- Use `camelCase` for functions and variables and `PascalCase` for module namespaces.
- Prefix jQuery objects with `$`, such as `$card` and `$list`.
- Keep code in IIFE modules and expose only functions used by another module.
- Bind events with jQuery `.on()`; namespace reusable handlers when they may need removal.
- Check for missing values and DOM nodes before using them.

### HTML and CSS

- Keep Jinja logic minimal and keep the frontend as a same-origin application.
- Use CSS custom properties for theme values and BEM-like component names such as
  `.bot-card` and `.toggle-switch`.
- Reserve inline styles for one-off layout or values generated dynamically.

## Runtime Architecture

### HTTP API

All application APIs are under `/api/`:

- `GET/POST /api/messages`: read the live cache or add an operator message.
- `GET/POST /api/conversations`: list conversations or archive the live one and start another.
- `GET/PUT/DELETE /api/conversations/<id>`: inspect, rename, or delete a conversation. The live
  conversation cannot be deleted.
- `GET /api/stream`: receive message and conversation events over SSE.
- `GET/PUT /api/settings`: read or update the shared prompt and bot-to-bot reply limit.
- `GET/POST /api/bots` and `PUT/DELETE /api/bots/<id>`: manage bots.
- `POST /api/bots/<id>/toggle`: enable or disable a bot immediately.
- `GET/POST /api/models`: read cached model lists or fetch and cache a list from an endpoint.

Mutation routes that accept bodies require a JSON object. Return JSON with an appropriate HTTP
status and a descriptive `error` field on failure.

### Messages and Conversations

Messages are persisted in SQLite and linked through `messages.conversation_id`. Exactly one
conversation has `archived_at IS NULL`; only that conversation accepts messages. `MessageStore`
caches its most recent `MAX_HISTORY` messages in memory without deleting older database rows.

Starting a new conversation freezes non-empty partial replies, removes empty placeholders,
archives the live row, snapshots every current bot into `conversation_bots` without API keys,
and saves the shared prompt and reply limit on the archived conversation. Background reply
threads stop after detecting that their stream is no longer live. Archived conversations are
read-only; resuming them is not implemented.

`MessageStore` emits `new_message`, `stream_start`, `stream_token`, `stream_end`,
`conversation_changed`, and `conversations_updated` SSE events. Keep its lock-held sections
short, and copy mutable data when returning it across thread boundaries.

### Bot Dispatch and LLM Requests

Each bot stores `base_url`, optional `api_key`, `model`, `persona`, optional `temperature` and
`max_tokens`, `color`, and `enabled`. All endpoints use the OpenAI Python SDK; there are no
provider-specific clients. Local endpoints receive a placeholder key when no key is configured
because the SDK requires one.

An operator message begins a new reply round. `@Name` mentions target matching bots; otherwise
all enabled, configured bots may answer. Replies are staggered and run in daemon threads. The
configured chain limit applies only to subsequent bot-to-bot replies. A silent response does not
spend the chain budget.

`llm_clients._format_channel_history` sends only the most recent completed message from each
participant, maps the current bot's own message to the assistant role, merges adjacent roles,
and ensures the first non-system turn is a user turn. `_ReplyFilter` removes leading bot-name
echoes and `<think>...</think>` blocks and drops empty or `[PASS]` replies.

`SYSTEM_PROMPT.md` supplies the default shared prompt. Supported placeholders are
`{{BOT_NAME}}`, `{{OPERATOR_NAME}}`, `{{PARTICIPANTS}}`, and `{{PERSONA}}`. If a custom template
omits `{{PERSONA}}`, a non-empty persona is appended. Add endpoint presets to `ENDPOINT_PRESETS`
in `config.py`. Model-list cache keys use `llm_clients.normalize_base_url`; keep the matching
`normalizeUrl` implementation in `static/js/settings.js` synchronized.

## Database Models

- `Bot`: current bot configuration, including the API key.
- `Conversation`: live/archive metadata and archived channel settings.
- `ConversationBot`: an API-key-free bot snapshot captured during archiving.
- `Message`: persisted operator and bot messages, including streaming placeholders.
- `EndpointModels`: JSON-encoded cached model IDs keyed by normalized endpoint URL.
- `Setting`: persisted channel setting values.

Schema changes currently use startup compatibility migrations in `app.py`, followed by
`db.create_all()`. Account for existing SQLite databases when adding or changing columns.

## Security and Validation

- Never commit API keys, database files, or other secrets.
- API keys are stored unencrypted in the configured database. Mask them in responses as
  `"****" + key[-4:]` and never send a stored key to a changed endpoint origin.
- Validate all user input. Bot nicknames are case-insensitively unique, restricted to letters,
  numbers, `_`, and `-`, and cannot equal the operator name.
- Endpoint URLs must use HTTP or HTTPS, include a host, and contain no embedded credentials.
- Keep the UI same-origin and do not enable CORS casually: the server holds API keys and can
  reach operator-configured local endpoints.
- Use SQLAlchemy or parameterized SQL. Raw SQL is limited to fixed startup migration statements.

## Change Checklist

- Preserve thread safety around shared message, dispatch, and settings state.
- Do not leak API keys through JSON responses, logs, archives, or model-fetch requests.
- Keep backend endpoint normalization and frontend cache lookup behavior aligned.
- Add or update tests under `tests/` for changed prompt, mention, filtering, or URL behavior.
- Run `python -m unittest discover tests -v` before handing off a change.
