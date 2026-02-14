import os
import sys

from flask import current_app

from config import OPERATOR_NAME
from models import Bot, db


def _is_oauth_or_rate_limit_error(error_msg):
    """Check if error message is related to OAuth or rate limiting."""
    error_lower = str(error_msg).lower()
    oauth_keywords = ['oauth', 'unauthorized', '401', '403', 'authentication']
    rate_limit_keywords = ['rate limit', 'rate_limit', 'ratelimit', '429', 'quota', 'too many requests']
    
    for keyword in oauth_keywords + rate_limit_keywords:
        if keyword in error_lower:
            return True
    return False


def _format_channel_history(messages, bot_nickname):
    """Convert channel message history into a provider-agnostic format.

    Returns list of dicts: {"role": "user"|"assistant", "content": str}
    - Bot's own prior messages -> assistant role
    - Everything else -> user role with [Name]: prefix
    """
    formatted = []
    for msg in messages:
        if msg.get("streaming"):
            continue
        sender = msg["sender"]
        content = msg["content"]
        if not content.strip():
            continue

        if sender == bot_nickname:
            formatted.append({"role": "assistant", "content": content})
        else:
            formatted.append({
                "role": "user",
                "content": f"[{sender}]: {content}",
            })

    # Merge consecutive same-role messages (required by Anthropic)
    merged = []
    for entry in formatted:
        if merged and merged[-1]["role"] == entry["role"]:
            merged[-1]["content"] += "\n" + entry["content"]
        else:
            merged.append(dict(entry))

    return merged


def stream_response(
    provider,
    model,
    api_key,
    messages,
    system_prompt,
    gemini_access_token=None,
    gemini_refresh_token=None,
    bot_id=None,
):
    """Generator that yields tokens one at a time from the LLM API."""
    if provider == "gemini":
        if not api_key and not gemini_access_token and not gemini_refresh_token:
            yield "[Error: No API key or Google OAuth configured for this bot]"
            return
    elif provider == "ollama":
        # Ollama runs locally, no API key required
        pass
    elif not api_key:
        yield "[Error: No API key configured for this bot]"
        return

    try:
        if provider == "openai":
            yield from _stream_openai(model, api_key, messages, system_prompt)
        elif provider == "anthropic":
            yield from _stream_anthropic(model, api_key, messages, system_prompt)
        elif provider == "gemini":
            yield from _stream_gemini(
                model,
                api_key,
                messages,
                system_prompt,
                gemini_access_token,
                gemini_refresh_token,
                bot_id,
            )
        elif provider == "deepseek":
            yield from _stream_deepseek(model, api_key, messages, system_prompt)
        elif provider == "qwen":
            yield from _stream_qwen(model, api_key, messages, system_prompt)
        elif provider == "ollama":
            yield from _stream_ollama(model, api_key, messages, system_prompt)
        elif provider == "openai-compatible":
            yield from _stream_openai_compatible(model, api_key, messages, system_prompt)
        else:
            yield f"[Error: Unknown provider '{provider}']"
    except Exception as e:
        error_msg = str(e)
        if _is_oauth_or_rate_limit_error(error_msg):
            # Log OAuth and rate limit errors to stderr, not to channel
            print(f"LLM API error ({provider}): {error_msg}", file=sys.stderr)
            yield "[Error: Authentication or rate limit issue - check server logs]"
        else:
            yield f"[Error: {error_msg}]"


def _stream_openai(model, api_key, messages, system_prompt):
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    formatted = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        formatted.append({"role": msg["role"], "content": msg["content"]})

    stream = client.chat.completions.create(
        model=model,
        messages=formatted,
        stream=True,
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


def _stream_anthropic(model, api_key, messages, system_prompt):
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)

    # Ensure messages start with user role (Anthropic requirement)
    formatted = []
    for msg in messages:
        formatted.append({"role": msg["role"], "content": msg["content"]})

    if not formatted or formatted[0]["role"] != "user":
        formatted.insert(0, {"role": "user", "content": "[System]: Channel opened."})

    with client.messages.stream(
        model=model,
        max_tokens=4096,
        system=system_prompt,
        messages=formatted,
    ) as stream:
        for text in stream.text_stream:
            yield text


def _stream_gemini(
    model,
    api_key,
    messages,
    system_prompt,
    gemini_access_token=None,
    gemini_refresh_token=None,
    bot_id=None,
):
    if (gemini_access_token or gemini_refresh_token) and not api_key:
        access_token = gemini_access_token
        if gemini_refresh_token:
            refreshed_token = _refresh_google_access_token(gemini_refresh_token)
            if refreshed_token:
                access_token = refreshed_token
                _store_refreshed_access_token(bot_id, refreshed_token)

        if not access_token:
            raise Exception("Missing Google OAuth access token")

        yield from _stream_gemini_oauth(model, access_token, messages, system_prompt)
        return

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)

    contents = []
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(types.Content(
            role=role,
            parts=[types.Part(text=msg["content"])],
        ))

    # Ensure contents is not empty and starts with user
    if not contents or contents[0].role != "user":
        contents.insert(0, types.Content(
            role="user",
            parts=[types.Part(text="[System]: Channel opened.")],
        ))

    response = client.models.generate_content_stream(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
        ),
    )
    for chunk in response:
        if chunk.text:
            yield chunk.text




def _refresh_google_access_token(refresh_token):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")

    if not refresh_token or not client_id or not client_secret:
        return None

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
    )
    try:
        creds.refresh(Request())
        return creds.token
    except Exception as e:
        current_app.logger.warning(f"Google OAuth token refresh failed: {e}")
        return None
    except Exception:
        return None

    return creds.token


def _store_refreshed_access_token(bot_id, access_token):
    if not bot_id or not access_token:
        return

    try:
        app = current_app._get_current_object()
    except RuntimeError:
        return

    with app.app_context():
        try:
            bot = db.session.get(Bot, bot_id)
            if not bot:
                return
            bot.gemini_access_token = access_token
            db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception(
                "Failed to store refreshed Gemini access token for bot_id=%s",
                bot_id,
            )


def _stream_gemini_oauth(model, access_token, messages, system_prompt):
    import json
    import requests

    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/" +
        model +
        ":streamGenerateContent?alt=sse"
    )

    contents = []
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        contents.append({
            "role": role,
            "parts": [{"text": msg["content"]}],
        })

    if not contents or contents[0]["role"] != "user":
        contents.insert(0, {
            "role": "user",
            "parts": [{"text": "[System]: Channel opened."}],
        })

    payload = {
        "contents": contents,
    }

    if system_prompt:
        payload["systemInstruction"] = {
            "parts": [{"text": system_prompt}],
        }

    response = requests.post(
        endpoint,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        data=json.dumps(payload),
        stream=True,
        timeout=60,
    )

    if response.status_code != 200:
        raise Exception(f"Gemini OAuth error: {response.status_code} {response.text}")

    for line in response.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line or line == "[DONE]":
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        candidates = data.get("candidates", [])
        for candidate in candidates:
            content = candidate.get("content", {})
            parts = content.get("parts", [])
            for part in parts:
                text = part.get("text")
                if text:
                    yield text


def _stream_deepseek(model, api_key, messages, system_prompt):
    """Stream responses from DeepSeek API (OpenAI-compatible)."""
    from openai import OpenAI

    client = OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com",
    )
    formatted = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        formatted.append({"role": msg["role"], "content": msg["content"]})

    stream = client.chat.completions.create(
        model=model,
        messages=formatted,
        stream=True,
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


def _stream_qwen(model, api_key, messages, system_prompt):
    """Stream responses from Qwen/DashScope API (OpenAI-compatible)."""
    from openai import OpenAI

    client = OpenAI(
        api_key=api_key,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    formatted = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        formatted.append({"role": msg["role"], "content": msg["content"]})

    stream = client.chat.completions.create(
        model=model,
        messages=formatted,
        stream=True,
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


def _stream_ollama(model, api_key, messages, system_prompt):
    """Stream responses from Ollama local API."""
    import json
    import requests

    # Default Ollama endpoint; `api_key` is overloaded here as a base URL
    base_url = api_key if api_key else "http://localhost:11434"
    # Normalize base URL to avoid issues like "//api/chat" and stray whitespace
    base_url = base_url.strip()
    if base_url.endswith("/"):
        base_url = base_url.rstrip("/")
    endpoint = f"{base_url}/api/chat"

    formatted = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        formatted.append({"role": msg["role"], "content": msg["content"]})

    payload = {
        "model": model,
        "messages": formatted,
        "stream": True,
    }

    response = requests.post(
        endpoint,
        json=payload,
        stream=True,
        timeout=120,
    )

    if response.status_code != 200:
        raise Exception(f"Ollama error: {response.status_code} {response.text}")

    for line in response.iter_lines(decode_unicode=True):
        if not line:
            continue
        try:
            data = json.loads(line)
            if "message" in data and "content" in data["message"]:
                content = data["message"]["content"]
                if content:
                    yield content
        except json.JSONDecodeError:
            continue


def _stream_openai_compatible(model, api_key, messages, system_prompt):
    """Stream responses from any OpenAI-compatible API.
    
    API key format: "BASE_URL|ACTUAL_API_KEY"
    Example: "https://api.together.xyz/v1|your-api-key-here"
    """
    from openai import OpenAI

    # Parse base_url from api_key if it contains |
    if "|" in api_key:
        base_url, actual_key = api_key.split("|", 1)
    else:
        raise Exception("OpenAI-compatible provider requires API key format: BASE_URL|API_KEY")

    client = OpenAI(
        api_key=actual_key,
        base_url=base_url,
    )
    formatted = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        formatted.append({"role": msg["role"], "content": msg["content"]})

    stream = client.chat.completions.create(
        model=model,
        messages=formatted,
        stream=True,
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
