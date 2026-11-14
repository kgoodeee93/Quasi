# Quasi core (Phases 0–1)

A Claude agent over your Google Calendar and Gmail, plus a morning brief:

- `python -m quasi ask "..."` reads your calendar, finds free time, searches and reads mail, creates
  events and drafts replies. Creating an event or a draft asks for your approval first. It cannot send email.
- `python -m quasi brief --email` writes the 7am brief (today's schedule, the 5 emails that most need
  you, tomorrow's first event) and emails it to your own inbox.

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
2. **APIs & Services → Library** → enable **Google Calendar API** and **Gmail API**.
3. **APIs & Services → OAuth consent screen** → External, app name "Quasi", add your Gmail as a **test user**.
   (Test mode is fine for personal use; publishing needs Google's review, see Security in the architecture doc.)
4. **Credentials → Create credentials → OAuth client ID → Desktop app**. Download the JSON.
5. Save it as `~/.config/quasi/google_client_secret.json`.

Then sign in once (opens your browser). It asks for Calendar (read + create events) and Gmail
(read, plus compose for drafts and the self-addressed brief):

```bash
python -m quasi connect google
```

**Already connected in Phase 0?** Run it again: Gmail permissions were added, and an old
Calendar-only token stops working until you re-consent.

**Consent screen tip:** while the OAuth app is in *Testing*, Google expires your sign-in every 7 days.
For personal use, set the consent screen to *In production* (you'll see an "unverified app" warning
once) so the scheduled brief keeps working.

## Use it

```bash
python -m quasi ask "what's on my calendar today?"
python -m quasi ask "when am I free for an hour tomorrow afternoon?"
python -m quasi ask "book gym tomorrow 6-7pm"      # asks: Approve? [y/N]
python -m quasi ask "anything from Acme in the last two days?"
python -m quasi ask "draft a reply to Amy saying the invoice is on its way"   # asks: Approve? [y/N]
python -m quasi chat                              # back-and-forth conversation
python -m quasi brief                             # print + save today's brief
python -m quasi brief --email                     # ...and email it to yourself

uvicorn quasi.api:app --reload                    # HTTP: POST /ask {"question": "..."}, POST /brief
```

Over HTTP, write actions are declined until the approvals queue ships (Phase 2), and `/brief` never
emails. Briefs are saved to `~/.config/quasi/briefs/YYYY-MM-DD.md`.

**Every morning at 7** (on a machine that stays on; Cloud Scheduler replaces this in Phase 5), e.g. `crontab -e`:

```
0 7 * * * cd /path/to/Quasi && python3 -m quasi brief --email >> ~/.config/quasi/brief.log 2>&1
```

## How it fits together

```
quasi/
  config.py               settings from env/.env (model, effort, file locations)
  integrations/
    base.py               Tool + Integration contract, ToolRegistry  <- every plugin implements this
    google_auth.py        OAuth installed-app flow, token cached 0600
    gcal.py               Calendar: list_events, find_free_time, create_event (writes=True)
    gmail.py              Gmail: search, read, create_draft (writes=True); send_to_self for the brief only
  agents/
    runtime.py            the Claude tool-use loop: call model -> run tools -> feed results back
    approvals.py          require_approval() gate + JSONL audit log of every tool call
    briefing.py           morning brief: gather calendar + inbox in code, ONE Claude call, no tools
    __init__.py           build_agent(): wires integrations into an Agent
  api.py                  FastAPI: /health, /ask
  __main__.py             CLI
tests/                    fake Google services + scripted fake Claude, no keys needed
```

**Email is untrusted input.** Subjects and bodies are written by strangers, so both prompts tell the
model to treat them as data, the brief call gets no tools at all, and anything an email could talk the
agent into (an event, a draft) still needs your approval. There is no send tool: drafts wait in Gmail.

**Adding an integration** (PayPal/Plaid are next): write a class with `connect()`, `sync()` and
`tools()` returning `Tool`s, mark anything that sends/changes things `writes=True`, and add it in
`build_agent()`. The runtime, approval gate and audit log pick it up automatically.

**Model**: `claude-opus-5-5` at `medium` effort, with server-side refusal fallback enabled
(`fallbacks="default"`), so a request a safety classifier declines is retried on a fallback model.
Change model or effort with `QUASI_MODEL` / `QUASI_EFFORT`.

**Where your data lives**: `~/.config/quasi/` holds the OAuth client, your token (`0600`) and
`audit.jsonl` (one line per tool call, approved or not). Tokens move to encrypted Supabase storage
when multi-device/dashboard work starts.

Run tests: `python -m pytest -q tests`
