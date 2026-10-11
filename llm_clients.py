from urllib.parse import urlsplit

from config import BOT_RESPONSE_TIMEOUT, OPERATOR_NAME
from utils import is_oauth_or_rate_limit_error

# Local servers (Ollama, LM Studio, llama.cpp) ignore the key, but the SDK requires one
_PLACEHOLDER_KEY = "not-needed"

_NO_PERSONA = "(No persona set. Be yourself: helpful, direct and concise.)"


# ── Prompt Building ──────────────────────────────────────────────────────

def build_system_prompt(template, bot_name, persona, participants):
    """Fill the channel system prompt template for one bot.

    Supported placeholders: {{BOT_NAME}}, {{OPERATOR_NAME}}, {{PARTICIPANTS}}, {{PERSONA}}.
    If the template has no {{PERSONA}} placeholder, a non-empty persona is appended.
    """
    persona = (persona or "").strip()
    prompt = template
    prompt = prompt.replace("{{BOT_NAME}}", bot_name)
    prompt = prompt.replace("{{OPERATOR_NAME}}", OPERATOR_NAME)
    prompt = prompt.replace("{{PARTICIPANTS}}", ", ".join(participants))
    if "{{PERSONA}}" in prompt:
        prompt = prompt.replace("{{PERSONA}}", persona or _NO_PERSONA)
    elif persona:
        prompt += "\n\n## Your persona\n" + persona
    return prompt


def _format_channel_history(messages, bot_nickname):
    """Convert channel message history into chat-completion messages.

    Returns list of dicts: {"role": "user"|"assistant", "content": str}
    - Bot's own prior messages -> assistant role
    - Everything else -> user role with [Name]: prefix
    - Only the last message per sender is included
    """
    last_by_sender = {}
    for msg in messages:
        if msg.get("streaming"):
            continue
        content = msg.get("content", "")
        if not content.strip():
            continue
        sender = msg.get("sender")
        if not sender:
            continue
        last_by_sender[sender] = msg

    ordered = sorted(last_by_sender.values(), key=lambda item: item.get("timestamp", 0))

    formatted = []
    for msg in ordered:
        sender = msg["sender"]
        content = msg["content"]
        if sender == bot_nickname:
            formatted.append({"role": "assistant", "content": content})
        else:
            formatted.append({
                "role": "user",
                "content": f"[{sender}]: {content}",
            })

    # Merge consecutive same-role messages (strict chat templates require alternation)
    merged = []
    for entry in formatted:
        if merged and merged[-1]["role"] == entry["role"]:
            merged[-1]["content"] += "\n" + entry["content"]
        else:
            merged.append(dict(entry))

    # Many local chat templates require the first turn after system to be a user turn
    if not merged or merged[0]["role"] != "user":
        merged.insert(0, {"role": "user", "content": "[System]: Channel opened."})

    return merged


def normalize_base_url(url):
    """Canonical form of an endpoint URL, used as the model-list cache key.

    Lowercases scheme and host and drops trailing slashes; the path keeps its case.
    """
    parts = urlsplit((url or "").strip())
    path = parts.path.rstrip("/")
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}"


# ── OpenAI-Compatible Client ─────────────────────────────────────────────

def _make_client(base_url, api_key, timeout=BOT_RESPONSE_TIMEOUT):
    from openai import OpenAI

    return OpenAI(
        base_url=base_url.strip().rstrip("/"),
        api_key=api_key or _PLACEHOLDER_KEY,
        timeout=timeout,
        max_retries=1,
    )


def stream_response(base_url, api_key, model, messages, system_prompt,
                    temperature=None, max_tokens=None):
    """Generator that yields tokens one at a time from an OpenAI-compatible endpoint."""
    if not base_url:
        yield "[Error: No endpoint URL configured for this bot]"
        return
    if not model:
        yield "[Error: No model configured for this bot]"
        return

    try:
        client = _make_client(base_url, api_key)
        formatted = [{"role": "system", "content": system_prompt}]
        formatted.extend({"role": m["role"], "content": m["content"]} for m in messages)

        kwargs = {"model": model, "messages": formatted, "stream": True}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens:
            kwargs["max_tokens"] = max_tokens

        stream = client.chat.completions.create(**kwargs)
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
    except Exception as e:
        error_msg = str(e)
        if is_oauth_or_rate_limit_error(error_msg):
            raise
        yield f"[Error: {error_msg}]"


def list_models(base_url, api_key):
    """Return the sorted model IDs an endpoint advertises via GET /models."""
    client = _make_client(base_url, api_key, timeout=15)
    return sorted({m.id for m in client.models.list()})
