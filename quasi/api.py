"""HTTP API and browser dashboard. Run: uvicorn quasi.api:app --reload, then open http://localhost:8000

Write actions are declined over HTTP until the approvals queue lands (Phase 2):
the agent tells you it couldn't do them, and you can approve from the CLI.
"""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agents import Agent, build_agent, run_brief
from .agents.approvals import deny_all
from .demo_data import build_demo_overview
from .integrations.google_auth import GoogleAuthError

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Quasi", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")

FOCUS = [
    "Review email triage",
    "Finish project follow-up",
    "Protect your next focus block",
]


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    answer: str
    stop_reason: str
    tool_calls: list[dict[str, Any]]


@lru_cache(maxsize=1)
def agent() -> Agent:
    return build_agent(approver=deny_all, interactive_auth=False)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((ROOT / "static" / "index.html").read_text(encoding="utf-8"))


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


def _clock(start: str | None, all_day: bool, tz) -> str:
    if all_day or not start:
        return "All day"
    return datetime.fromisoformat(start).astimezone(tz).strftime("%I:%M %p").lstrip("0")


def calendar_overview(cal) -> dict[str, Any]:
    """Today's dashboard from a connected GoogleCalendar. Every number shown is read live."""
    today = cal.now().date().isoformat()
    events = cal.list_events(today)["events"]
    free = sum(s["minutes"] for s in cal.find_free_time(today, 15)["free_slots"])
    shown = [{
        "title": e["title"],
        "time": _clock(e["start"], e["all_day"], cal.tz),
        "location": e.get("location") or e.get("video_link") or "No location",
        "type": "all-day" if e["all_day"] else "calendar",
    } for e in events[:6]]
    n = len(events)
    return {
        "connected": True,
        "summary": f"{n} event{'s' if n != 1 else ''} on your calendar today" + (
            f", {len(shown)} shown." if n > len(shown) else "."),
        "events": shown,
        "focus": FOCUS,
        "kpis": [
            {"label": "Today", "value": f"{n} event{'s' if n != 1 else ''}", "tone": "blue"},
            {"label": "Free 9–6", "value": f"{free // 60}h {free % 60:02d}m", "tone": "green"},
            # No approvals queue over HTTP until Phase 2: write actions are declined, not queued.
            {"label": "Approvals", "value": "0 pending", "tone": "amber"},
        ],
        "approvals": [],
        "generated_at": cal.now().isoformat(),
    }


@app.get("/api/overview")
def overview() -> dict[str, Any]:
    """Live calendar overview once Google is connected; demo data before that."""
    from .config import Settings
    from .integrations.gcal import GoogleCalendar

    settings = Settings.load()
    if not settings.google_token.exists():
        return build_demo_overview()
    try:
        return calendar_overview(GoogleCalendar(settings, interactive=False))
    except GoogleAuthError as e:
        problem = str(e)
    except Exception as e:  # network, API quota, revoked access: keep the page usable
        problem = f"Google Calendar couldn't be read ({type(e).__name__})."
    data = build_demo_overview()
    data["summary"] = f"{problem} Showing demo data."
    return data


@app.post("/api/ask", response_model=AskResponse)
def ask_api(req: AskRequest):
    try:
        res = agent().ask(req.question)
    except GoogleAuthError as e:
        raise HTTPException(503, str(e))
    return AskResponse(answer=res.text, stop_reason=res.stop_reason, tool_calls=res.tool_calls)


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest):
    return ask_api(req)


class BriefResponse(BaseModel):
    brief: str
    saved_to: str
    stop_reason: str


@app.post("/brief", response_model=BriefResponse)
def brief():
    """Generate today's brief (what the 7am scheduler will call). Never emails from HTTP."""
    try:
        b, path, _ = run_brief(interactive_auth=False, email=False)
    except GoogleAuthError as e:
        raise HTTPException(503, str(e))
    return BriefResponse(brief=b.text, saved_to=str(path), stop_reason=b.stop_reason)
