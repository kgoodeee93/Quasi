# Quasi

Quasi is a browser-first personal operations dashboard and agent system meant to feel useful in a real browser, not only through a terminal.

## What this app does

- Shows a daily operations dashboard
- Summarizes your current calendar, focus blocks, and approvals
- Lets you ask questions in natural language
- Connects to Google Calendar when configured
- Continues to work with demo data even before OAuth is set up

## Run it

```bash
cd Quasi
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn quasi.api:app --reload
```

Then open http://localhost:8000 in your browser.

## Browser-first workflow

- Home dashboard: today at a glance
- Quick actions: ask about calendar, free time, or next actions
- Approvals panel: pending tasks and follow-ups
- Chat interface: ask Quasi questions in plain English

## Optional: connect your Google Calendar

```bash
python -m quasi connect google
```

Then refresh the dashboard and Quasi will load live schedule data from your Google account.

## Docs

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [quasi/README.md](quasi/README.md)
- [dashboards/crypto-portfolio/README.md](dashboards/crypto-portfolio/README.md)
