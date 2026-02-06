import uuid

OPERATOR_NAME = "Operator"
OPERATOR_COLOR = "#3B82F6"

MAX_HISTORY = 500
SSE_RETRY_MS = 3000
BOT_RESPONSE_TIMEOUT = 120

DEFAULT_BOTS = [
    {
        "id": str(uuid.uuid4()),
        "nickname": "Claude",
        "provider": "anthropic",
        "model": "claude-sonnet-4-5-20250929",
        "api_key": "",
        "system_prompt": (
            "You are Claude, a helpful AI assistant participating in a group chat channel. "
            "Keep your responses concise and conversational. You can see all messages from "
            "the operator and other bots. Engage naturally in the discussion."
        ),
        "color": "#8B5CF6",
        "enabled": True,
    },
    {
        "id": str(uuid.uuid4()),
        "nickname": "GPT",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "api_key": "",
        "system_prompt": (
            "You are GPT, a helpful AI assistant participating in a group chat channel. "
            "Keep your responses concise and conversational. You can see all messages from "
            "the operator and other bots. Engage naturally in the discussion."
        ),
        "color": "#10B981",
        "enabled": True,
    },
    {
        "id": str(uuid.uuid4()),
        "nickname": "Gemini",
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "api_key": "",
        "system_prompt": (
            "You are Gemini, a helpful AI assistant participating in a group chat channel. "
            "Keep your responses concise and conversational. You can see all messages from "
            "the operator and other bots. Engage naturally in the discussion."
        ),
        "color": "#F59E0B",
        "enabled": True,
    },
]
