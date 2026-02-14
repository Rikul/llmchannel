import os
import uuid

OPERATOR_NAME = "Operator"
OPERATOR_COLOR = "#3B82F6"

MAX_HISTORY = 500
SSE_RETRY_MS = 3000
BOT_RESPONSE_TIMEOUT = 120

SYSTEM_PROMPT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "SYSTEM_PROMPT.md",
)


def _load_system_prompt():
    try:
        with open(SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except Exception:
        return "You are a helpful assistant in a group chat."


DEFAULT_SYSTEM_PROMPT = _load_system_prompt()

DEFAULT_BOTS = [
    {
        "id": str(uuid.uuid4()),
        "nickname": "Claude",
        "provider": "anthropic",
        "model": "claude-sonnet-4-5-20250929",
        "api_key": "",
        "system_prompt": DEFAULT_SYSTEM_PROMPT,
        "color": "#8B5CF6",
        "enabled": True,
    },
    {
        "id": str(uuid.uuid4()),
        "nickname": "GPT",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "api_key": "",
        "system_prompt": DEFAULT_SYSTEM_PROMPT,
        "color": "#10B981",
        "enabled": True,
    },
    {
        "id": str(uuid.uuid4()),
        "nickname": "Gemini",
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "api_key": "",
        "system_prompt": DEFAULT_SYSTEM_PROMPT,
        "color": "#F59E0B",
        "enabled": True,
    },
]
