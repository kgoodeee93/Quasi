"""Browser dashboard: page + static assets, demo overview, live calendar overview (fake Google service)."""
from datetime import datetime, time
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from quasi.api import app, calendar_overview
from quasi.integrations.gcal import GoogleCalendar

from .test_quasi import TZ, FakeCalendar, settings

client = TestClient(app)


def test_page_and_assets_are_served():
    page = client.get("/")
    assert page.status_code == 200 and 'id="kpi-grid"' in page.text
    for asset, marker in [("app.js", "function esc("), ("styles.css", "--bg:")]:
        r = client.get(f"/static/{asset}")
        assert r.status_code == 200 and marker in r.text


def test_overview_is_demo_until_google_is_connected(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", str(tmp_path / "missing.json"))
    data = client.get("/api/overview").json()
    assert data["connected"] is False
    assert {k["label"] for k in data["kpis"]} == {"Today", "Focus", "Approvals"}
    assert data["events"] and data["approvals"]


def at(hour: int) -> datetime:
    tz = ZoneInfo(TZ)
    return datetime.combine(datetime.now(tz).date(), time(hour), tz)


def test_calendar_overview_uses_live_numbers(tmp_path):
    svc = FakeCalendar(
        events=[
            {"summary": "Standup", "start": {"dateTime": "2026-10-01T09:00:00-04:00"},
             "end": {"dateTime": "2026-10-01T09:30:00-04:00"}, "hangoutLink": "https://meet/x"},
            {"summary": "<img src=x onerror=alert(1)>", "start": {"date": "2026-10-01"},
             "end": {"date": "2026-10-02"}, "location": "Office"},
        ],
        # The overview asks freebusy about *today*, so the busy hour has to be today's 09:00-10:00.
        busy=[{"start": at(9).isoformat(), "end": at(10).isoformat()}],
    )
    data = calendar_overview(GoogleCalendar(settings(tmp_path), service=svc))
    assert data["connected"] is True
    assert [(e["time"], e["location"], e["type"]) for e in data["events"]] == [
        ("9:00 AM", "https://meet/x", "calendar"), ("All day", "Office", "all-day")]
    kpis = {k["label"]: k["value"] for k in data["kpis"]}
    assert kpis == {"Today": "2 events", "Free 9–6": "8h 00m", "Approvals": "0 pending"}
    # Titles go out as data; app.js escapes them before they touch the DOM.
    assert data["events"][1]["title"] == "<img src=x onerror=alert(1)>"


def test_brief_endpoint_still_exists():
    assert any(getattr(r, "path", None) == "/brief" for r in app.routes)
