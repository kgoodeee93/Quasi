---
name: run-quasi
description: Build, run, test and drive Quasi core (Claude agent runtime, Google Calendar + Gmail integrations, morning brief, CLI and FastAPI). Use when asked to run quasi, start its API, exercise the agent loop, calendar or Gmail tools, generate the morning brief, check the approval gate or audit log, or run its tests.
---

Quasi core is a Python package (`quasi/`) with a CLI (`python -m quasi`) and an API
(`uvicorn quasi.api:app`). Real runs need a Claude API key **and** a Google OAuth sign-in, which a
fresh container won't have, so the agent path is the offline harness
`.claude/skills/run-quasi/driver.py`: it runs the real tool registry, calendar + Gmail tool code,
`Agent.ask()` loop, approval gate, audit log and morning-brief workflow against a fixture calendar,
a fixture inbox and a scripted model.

All paths below are relative to the repo root. (The crypto dashboard has its own skill:
`dashboards/crypto-portfolio/.claude/skills/run-crypto-portfolio/`.)

## Setup

Use `python3 -m pip`, not `pip` (see Gotchas), and give Python 3.11 its own `cryptography`:

```bash
python3 -m pip install -q -e ".[dev]"
python3 -m pip install -q --ignore-installed cryptography cffi
export QUASI_HOME=/tmp/quasi-home     # keep OAuth/token/audit files out of ~/.config/quasi
```

## Run (agent path): offline driver

```bash
python3 .claude/skills/run-quasi/driver.py tools
python3 .claude/skills/run-quasi/driver.py call calendar_find_free_time '{"day":"'$(date +%F)'","duration_minutes":60,"day_start":null,"day_end":null}'
python3 .claude/skills/run-quasi/driver.py call gmail_search '{"query":"is:unread","max_results":5}'
python3 .claude/skills/run-quasi/driver.py agent             # write declined
python3 .claude/skills/run-quasi/driver.py agent --approve   # write approved
python3 .claude/skills/run-quasi/driver.py brief
```

- `tools`: registry contents and which tools are `writes=True` (approval-gated):
  `calendar_create_event` and `gmail_create_draft`. There is no email-send tool.
- `call <tool> '<json>'`: one tool handler against the fixtures. Calendar: Standup 09:00–09:15 and
  Client call 13:00–14:00, America/New_York. Inbox: an overdue-invoice email, a family email and
  a prompt-injection attempt (`deals@spam.example`). Pass every schema property; optional ones as `null`.
- `agent`: one `Agent.ask()` through a canned exchange (list events → create "Gym" → final text).
  Expected: declined → `tool_result #2 (t2, is_error=True): The user declined…`,
  `events inserted into calendar: 0`; `--approve` → `is_error=False`, `events inserted into calendar: 1`.
- `brief`: the real morning-brief workflow (gather → one model call → save → email to self).
  Expected: `model call: tools given = False | fallbacks = default`, the three email subjects in
  the data sent to Claude, `emailed: To = kevin@example.com | Subject = Quasi brief · <date>`.

## Run: CLI and API (verified failure modes without credentials)

```bash
python3 -m quasi --help
python3 -m quasi ask "what's on my calendar today?" < /dev/null; echo "exit=$?"
# -> error: OAuth client file not found at /tmp/quasi-home/google_client_secret.json ... ; exit=2

nohup python3 -m uvicorn quasi.api:app --port 8100 > /tmp/quasi-api.log 2>&1 &
timeout 30 bash -c 'until curl -sf localhost:8100/health >/dev/null; do sleep 0.5; done'; curl -s localhost:8100/health
curl -s -X POST localhost:8100/ask -H 'content-type: application/json' -d '{"question":"what is on my calendar today?"}' -w "\nHTTP %{http_code}\n"
curl -s -X POST localhost:8100/brief -w "\nHTTP %{http_code}\n"
# -> both: {"detail":"Google isn't connected (or needs the newly added permissions). Run `python -m quasi connect google` once."}  HTTP 503
lsof -ti:8100 -sTCP:LISTEN | xargs -r kill
```

With real credentials (`ANTHROPIC_API_KEY`, a Desktop OAuth client JSON at
`$QUASI_HOME/google_client_secret.json`, then `python -m quasi connect google` in a browser), the
same `ask` / `chat` / `brief` / `POST /ask` / `POST /brief` work on your real calendar and inbox.
That path needs a browser for the Google consent screen and a Claude key, and was **not**
exercised in a container; see `quasi/README.md`.

## Test

```bash
python3 -m pytest -q tests     # -> 18 passed (fake Google services + scripted Claude)
```

## Gotchas

- **`pip` and `python3` can be different Pythons.** In this container image `pip` is Python 3.13's
  while `python3` is 3.11, so `pip install` "succeeds" and then `import anthropic` fails with
  `ModuleNotFoundError`. Always `python3 -m pip`.
- **Debian's system `cryptography` breaks google-auth under Python 3.11** (built for 3.13):
  `ModuleNotFoundError: No module named '_cffi_backend'` followed by `pyo3_runtime.PanicException`
  on `import google.auth.transport.requests`. Fix: `python3 -m pip install --ignore-installed cryptography cffi`.
- **No live Claude calls from this container.** A dummy `ANTHROPIC_API_KEY` is not swapped for a
  real one (`401 invalid x-api-key`), and the session's own Anthropic auth is not available to
  your code. Live model runs happen on a machine with your own key.
- **One Google token, all scopes.** Calendar and Gmail share `GOOGLE_SCOPES` in
  `quasi/integrations/google_auth.py`. Adding a scope invalidates every existing token: the CLI
  re-opens the consent screen, the API answers 503 until `python -m quasi connect google` is re-run.
- **No API key doesn't fail fast.** `anthropic.Anthropic()` constructs fine without
  `ANTHROPIC_API_KEY`; Google auth fails first (the agent asks the calendar for its timezone before
  calling Claude), so a missing key only shows up after Google is connected.
- **`/ask` never performs writes; `/brief` never emails.** The API uses `deny_all`, and the brief
  is only emailed from the CLI (`brief --email`). Use the driver or the CLI to exercise those paths.
- **Strict tool schemas need every key.** Optional arguments are nullable but still required:
  `{"start_date": "…", "end_date": null}`. The driver's `call` uses Python defaults; the model
  must send all keys.
- **Scripted-model harnesses must snapshot `messages`.** `Agent.ask()` mutates one list across
  turns; record `list(kw["messages"])` per request or you'll read the next turn's assistant message
  instead of the tool results (`TypeError: 'types.SimpleNamespace' object is not subscriptable`).

## Troubleshooting

- **`error: OAuth client file not found at …/google_client_secret.json`** (exit 2): expected
  without Google setup. Use the driver, or follow `quasi/README.md` to create the OAuth client.
- **`HTTP 503 Google isn't connected (or needs the newly added permissions)`** from `/ask` or
  `/brief`: the API never opens a browser. Run `python -m quasi connect google` once on a machine
  with a browser (again after scopes change).
- **`KeyError: '<name>'` from `driver.py call`**: unknown tool name → run `driver.py tools`.
