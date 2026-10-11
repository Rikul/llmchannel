# LLM Channel

An IRC-style chat channel where you, the operator, talk with several LLM bots at once.
Each bot connects to any OpenAI-compatible endpoint: OpenRouter, Ollama, LM Studio,
llama.cpp, vLLM or OpenAI.

## Features

- Add as many bots as you like, each with its own endpoint, API key, model, persona,
  temperature and max tokens
- A main system prompt shared by all bots, editable in Settings
- Operator control: address bots with `@Name`, cap how much bots reply to each other,
  turn bots on and off instantly
- Bots can stay silent (`[PASS]`) instead of piling on
- Conversations are saved in SQLite. Start a new one to archive the current chat, along
  with the bot profiles it used, and read archives later
- Fetched model lists are cached per endpoint

## Run

With [uv](https://docs.astral.sh/uv/):

```bash
uv run app.py
```

Or with pip:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then open http://localhost:5000 and configure bots under Settings.

Data, including API keys, is stored in `instance/llmchannel.db`. Set `DATABASE_URL` to
use a different database. The app has no login, so only run it where you trust everyone
who can reach it.

## Tests

```bash
python -m unittest discover tests
```
