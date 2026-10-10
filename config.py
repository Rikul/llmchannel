import os
import uuid

OPERATOR_NAME = "Operator"
OPERATOR_COLOR = "#3B82F6"

MAX_HISTORY = 500
SSE_RETRY_MS = 3000
BOT_RESPONSE_TIMEOUT = 120

# Characters allowed in bot nicknames. @mentions use the same set as their boundaries, so
# @Ada never matches inside @Ada-Bob
NICKNAME_CHARS = r"[\w-]"

# A bot replies with exactly this token to stay silent for a turn
PASS_TOKEN = "[PASS]"

# Max bot-to-bot replies after each operator message (0 = bots only answer the operator)
DEFAULT_BOT_CHAIN_LIMIT = 4

ENDPOINT_PRESETS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
    "lmstudio": "http://localhost:1234/v1",
    "llamacpp": "http://localhost:8080/v1",
    "openai": "https://api.openai.com/v1",
}

SYSTEM_PROMPT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "SYSTEM_PROMPT.md",
)


def _load_system_prompt():
    try:
        with open(SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except Exception:
        return "You are {{BOT_NAME}}, a helpful assistant in a group chat run by {{OPERATOR_NAME}}."


DEFAULT_SYSTEM_PROMPT = _load_system_prompt()

DEFAULT_BOTS = [
    {
        "id": str(uuid.uuid4()),
        "nickname": "Router",
        "base_url": ENDPOINT_PRESETS["openrouter"],
        "api_key": "",
        "model": "openai/gpt-4o-mini",
        "persona": "",
        "color": "#10B981",
        "enabled": False,
    },
    {
        "id": str(uuid.uuid4()),
        "nickname": "Local",
        "base_url": ENDPOINT_PRESETS["ollama"],
        "api_key": "",
        "model": "llama3.2",
        "persona": "",
        "color": "#F59E0B",
        "enabled": False,
    },
]
