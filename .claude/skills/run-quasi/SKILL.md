---
name: run-quasi
description: Build, run, test and drive Quasi core (the Claude agent runtime, Google Calendar integration, CLI and FastAPI). Use when asked to run quasi, start its API, exercise the agent loop or calendar tools, check the approval gate or audit log, or run its tests.
---

Quasi core is a Python package (`quasi/`) with a CLI (`python -m quasi`) and an API
(`uvicorn quasi.api:app`). Real runs need a Claude API key **and** a Google OAuth sign-in, which a
fresh container won't have, so the agent path is the offline harness
`.claude/skills/run-quasi/driver.py`: it runs the real tool registry, calendar tool code,
`Agent.ask()` loop, approval gate and audit log against a fixture calendar and a scripted model.

All paths below are relative to the repo root. (The crypto dashboard has its own skill:
`dashboards/crypto-portfolio/.claude/skills/run-crypto-portfolio/`.)

## Setup

```bash
pip install -q -e ".[dev]"
export QUASI_HOME=/tmp/quasi-home     # keep OAuth/token/audit files out of ~/.config/quasi
```

## Run (agent path): offline driver

```bash
python3 .claude/skills/run-quasi/driver.py tools
python3 .claude/skills/run-quasi/driver.py call calendar_find_free_time '{"day":"'$(date +%F)'","duration_minutes":60,"day_start":null,"day_end":null}'
python3 .claude/skills/run-quasi/driver.py agent             # write declined
python3 .claude/skills/run-quasi/driver.py agent --approve   # write approved
```

- `tools`: registry contents and which tools are `writes=True` (approval-gated).
- `call <tool> '<json>'`: one tool handler against the fixture calendar (Standup 09:00–09:15,
  Client call 13:00–14:00, America/New_York). Pass every schema property; optional ones as `null`.
- `agent`: one `Agent.ask()` through a canned exchange (list events → create "Gym" → final text).
  It prints the request params (model, `fallbacks`, effort), each `tool_result` with `is_error`,
  how many events were inserted, and the audit-log JSONL. Expected:
  declined → `tool_result #2 (t2, is_error=True): The user declined…`, `events inserted into calendar: 0`;
  `--approve` → `is_error=False`, `events inserted into calendar: 1`.

## Run: CLI and API (verified failure modes without credentials)

```bash
python3 -m quasi --help
python3 -m quasi ask "what's on my calendar today?" < /dev/null; echo "exit=$?"
# -> error: OAuth client file not found at /tmp/quasi-home/google_client_secret.json ... ; exit=2

nohup python3 -m uvicorn quasi.api:app --port 8100 > /tmp/quasi-api.log 2>&1 &
timeout 30 bash -c 'until curl -sf localhost:8100/health >/dev/null; do sleep 0.5; done'; curl -s localhost:8100/health
curl -s -X POST localhost:8100/ask -H 'content-type: application/json' -d '{"question":"what is on my calendar today?"}' -w "\nHTTP %{http_code}\n"
# -> {"detail":"Google isn't connected yet. Run `python -m quasi connect google` once."}  HTTP 503
lsof -ti:8100 -sTCP:LISTEN | xargs -r kill
```

With real credentials (`ANTHROPIC_API_KEY`, a Desktop OAuth client JSON at
`$QUASI_HOME/google_client_secret.json`, then `python -m quasi connect google` in a browser), the
same `ask` / `chat` / `POST /ask` answer from your calendar. That path needs a browser for the
Google consent screen and was **not** exercised in a container; see `quasi/README.md`.

## Test

```bash
python3 -m pytest -q tests     # -> 10 passed (fake Google service + scripted Claude)
```

## Gotchas

- **No API key doesn't fail fast.** `anthropic.Anthropic()` constructs fine without
  `ANTHROPIC_API_KEY`; the first thing that fails is Google auth (the agent asks the calendar for
  its timezone before calling Claude), so a missing key only shows up after Google is connected.
- **`/ask` never performs writes.** The API uses `deny_all`, so create-event requests come back
  as "declined" until the approvals queue exists. Use the driver's `agent --approve` or the CLI
  (interactive `Approve? [y/N]`) to exercise an approved write.
- **Strict tool schemas need every key.** Optional arguments are nullable but still required:
  `{"start_date": "…", "end_date": null}`, not `{"start_date": "…"}`. The driver's `call` uses
  the handler's Python defaults, but the model must send all keys.
- **Scripted-model harnesses must snapshot `messages`.** `Agent.ask()` mutates one list across
  turns; record `list(kw["messages"])` per request or you'll read the next turn's assistant message
  instead of the tool results (`TypeError: 'types.SimpleNamespace' object is not subscriptable`).

## Troubleshooting

- **`error: OAuth client file not found at …/google_client_secret.json`** (exit 2): expected
  without Google setup. Use the driver, or follow `quasi/README.md` to create the OAuth client.
- **`HTTP 503 Google isn't connected yet`** from `/ask`: the API never opens a browser
  (`interactive_auth=False`). Run `python -m quasi connect google` once on a machine with a browser.
- **`KeyError: '<name>'` from `driver.py call`**: unknown tool name → run `driver.py tools`.
