# Quasi core (Phase 0)

`python -m quasi ask "what's on my calendar today?"` is a Claude agent that reads your Google Calendar,
finds free time, and creates events. Creating an event asks for your approval first.

Architecture and roadmap: [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).

## Setup (one time, ~10 minutes)

```bash
cd Quasi
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # put your ANTHROPIC_API_KEY in it
```

**Google Cloud OAuth client** (lets Quasi ask for access to *your* account):

1. In [Google Cloud Console](https://console.cloud.google.com/) create (or pick) a project.
2. **APIs & Services → Library** → enable **Google Calendar API**.
3. **APIs & Services → OAuth consent screen** → External, app name "Quasi", add your Gmail as a **test user**.
   (Test mode is fine for personal use; publishing needs Google's review, see Security in the architecture doc.)
4. **Credentials → Create credentials → OAuth client ID → Desktop app**. Download the JSON.
5. Save it as `~/.config/quasi/google_client_secret.json`.

Then sign in once (opens your browser):

```bash
python -m quasi connect google
```

## Use it

```bash
python -m quasi ask "what's on my calendar today?"
python -m quasi ask "when am I free for an hour tomorrow afternoon?"
python -m quasi ask "book gym tomorrow 6-7pm"      # asks: Approve? [y/N]
python -m quasi chat                              # back-and-forth conversation

uvicorn quasi.api:app --reload                    # HTTP: POST /ask {"question": "..."}
```

Over HTTP, write actions are declined until the approvals queue ships (Phase 2). The agent tells you
it couldn't do them.

## How it fits together

```
quasi/
  config.py               settings from env/.env (model, effort, file locations)
  integrations/
    base.py               Tool + Integration contract, ToolRegistry  <- every plugin implements this
    google_auth.py        OAuth installed-app flow, token cached 0600
    gcal.py               Calendar: list_events, find_free_time, create_event (writes=True)
  agents/
    runtime.py            the Claude tool-use loop: call model -> run tools -> feed results back
    approvals.py          require_approval() gate + JSONL audit log of every tool call
    __init__.py           build_agent(): wires integrations into an Agent
  api.py                  FastAPI: /health, /ask
  __main__.py             CLI
tests/test_quasi.py       fake Google service + scripted fake Claude, no keys needed
```

**Adding an integration** (Gmail is next): write a class with `connect()`, `sync()` and
`tools()` returning `Tool`s, mark anything that sends/changes things `writes=True`, and add it in
`build_agent()`. The runtime, approval gate and audit log pick it up automatically.

**Model**: `claude-opus-5-5` at `medium` effort, with server-side refusal fallback enabled
(`fallbacks="default"`), so a request a safety classifier declines is retried on a fallback model.
Change model or effort with `QUASI_MODEL` / `QUASI_EFFORT`.

**Where your data lives**: `~/.config/quasi/` holds the OAuth client, your token (`0600`) and
`audit.jsonl` (one line per tool call, approved or not). Tokens move to encrypted Supabase storage
when multi-device/dashboard work starts.

Run tests: `python -m pytest -q tests`
