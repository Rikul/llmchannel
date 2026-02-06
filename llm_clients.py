from config import OPERATOR_NAME


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


def stream_response(provider, model, api_key, messages, system_prompt):
    """Generator that yields tokens one at a time from the LLM API."""
    if not api_key:
        yield "[Error: No API key configured for this bot]"
        return

    try:
        if provider == "openai":
            yield from _stream_openai(model, api_key, messages, system_prompt)
        elif provider == "anthropic":
            yield from _stream_anthropic(model, api_key, messages, system_prompt)
        elif provider == "gemini":
            yield from _stream_gemini(model, api_key, messages, system_prompt)
        else:
            yield f"[Error: Unknown provider '{provider}']"
    except Exception as e:
        yield f"[Error: {str(e)}]"


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


def _stream_gemini(model, api_key, messages, system_prompt):
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
