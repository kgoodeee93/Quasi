"""Phase 0 tests: Calendar integration (fake Google service) and the agent loop (scripted fake Claude)."""
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import pytest

from quasi.agents.approvals import AuditLog
from quasi.agents.runtime import FALLBACK_BETA, Agent
from quasi.config import Settings
from quasi.integrations.base import ToolRegistry, schema
from quasi.integrations.gcal import GoogleCalendar

TZ = "America/New_York"


# ---------- fake Google Calendar service ----------
class _Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class FakeCalendar:
    def __init__(self, events=None, busy=None):
        self.events_data = events or []
        self.busy = busy or []
        self.inserted = []
        self.list_calls = []

    def settings(self):
        return NS(get=lambda setting: _Exec({"value": TZ}))

    def events(self):
        def list_(**kw):
            self.list_calls.append(kw)
            return _Exec({"items": self.events_data})

        def insert(calendarId, body, sendUpdates):
            self.inserted.append((body, sendUpdates))
            return _Exec({**body, "id": "evt1", "htmlLink": "https://cal/evt1"})
        return NS(list=list_, insert=insert)

    def freebusy(self):
        return NS(query=lambda body: _Exec({"calendars": {"primary": {"busy": self.busy}}}))


def settings(tmp_path: Path, tz=None) -> Settings:
    return Settings(model="claude-opus-5-5", effort="medium", max_turns=6,
                    google_client_secret=tmp_path / "cs.json", google_token=tmp_path / "tok.json",
                    audit_log=tmp_path / "audit.jsonl", timezone=tz)


def test_list_events_formats_and_uses_calendar_timezone(tmp_path):
    svc = FakeCalendar(events=[
        {"summary": "Standup", "start": {"dateTime": "2026-10-01T09:00:00-04:00"},
         "end": {"dateTime": "2026-10-01T09:15:00-04:00"}, "hangoutLink": "https://meet/x",
         "attendees": [{"email": "me@x.com", "self": True, "responseStatus": "accepted"}, {"email": "amy@x.com"}]},
        {"summary": "Holiday", "start": {"date": "2026-10-01"}, "end": {"date": "2026-10-02"}},
        {"summary": "Gone", "status": "cancelled", "start": {"date": "2026-10-01"}, "end": {"date": "2026-10-02"}},
    ])
    cal = GoogleCalendar(settings(tmp_path), service=svc)
    out = cal.list_events("2026-10-01")
    assert out["timezone"] == TZ
    assert out["from"] == "2026-10-01T00:00:00-04:00" and out["to"] == "2026-10-02T00:00:00-04:00"
    assert [e["title"] for e in out["events"]] == ["Standup", "Holiday"]
    standup = out["events"][0]
    assert standup["attendees"] == ["amy@x.com"] and standup["my_response"] == "accepted"
    assert out["events"][1]["all_day"] is True
    assert svc.list_calls[0]["singleEvents"] is True


def test_find_free_time_between_meetings(tmp_path):
    svc = FakeCalendar(busy=[
        {"start": "2026-10-01T13:00:00Z", "end": "2026-10-01T14:00:00Z"},   # 09:00-10:00 local
        {"start": "2026-10-01T16:30:00Z", "end": "2026-10-01T17:00:00Z"},   # 12:30-13:00 local
    ])
    cal = GoogleCalendar(settings(tmp_path), service=svc)
    out = cal.find_free_time("2026-10-01", 60)
    assert out["free_slots"] == [
        {"start": "10:00", "end": "12:30", "minutes": 150},
        {"start": "13:00", "end": "18:00", "minutes": 300},
    ]


def test_create_event_localizes_times(tmp_path):
    svc = FakeCalendar()
    cal = GoogleCalendar(settings(tmp_path, tz="Europe/London"), service=svc)
    out = cal.create_event("Call", "2026-10-02T14:00", "2026-10-02T14:30", attendees=["a@b.com"])
    body, send = svc.inserted[0]
    assert body["start"] == {"dateTime": "2026-10-02T14:00:00+01:00", "timeZone": "Europe/London"}
    assert body["attendees"] == [{"email": "a@b.com"}] and send == "all"
    assert out["created"] and out["link"] == "https://cal/evt1"


def test_strict_schema_shape():
    s = schema({"a": {"type": "string"}, "b": {"type": "integer"}}, required=["a"])
    assert s["additionalProperties"] is False and s["required"] == ["a", "b"]
    assert s["properties"]["b"]["type"] == ["integer", "null"]


# ---------- scripted fake Claude ----------
def tool_use(id_, name, input_):
    return NS(type="tool_use", id=id_, name=name, input=input_)


def text(t):
    return NS(type="text", text=t)


class FakeClaude:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.requests.append({**kw, "messages": list(kw["messages"])})
        return self.script.pop(0)


def make_agent(tmp_path, script, approver=lambda n, a: False, svc=None):
    cal = GoogleCalendar(settings(tmp_path), service=svc or FakeCalendar())
    client = FakeClaude(script)
    agent = Agent(client=client, registry=ToolRegistry().add(cal), approver=approver,
                  audit=AuditLog(tmp_path / "audit.jsonl"),
                  now=lambda: datetime(2026, 10, 1, 8, 0, tzinfo=ZoneInfo(TZ)))
    return agent, client


def test_agent_answers_calendar_question(tmp_path):
    svc = FakeCalendar(events=[{"summary": "Dentist", "start": {"dateTime": "2026-10-01T15:00:00-04:00"},
                                "end": {"dateTime": "2026-10-01T16:00:00-04:00"}}])
    agent, client = make_agent(tmp_path, [
        NS(stop_reason="tool_use", content=[text("Checking."), tool_use("t1", "calendar_list_events",
                                                                          {"start_date": "2026-10-01", "end_date": None})]),
        NS(stop_reason="end_turn", content=[text("You have the dentist at 3pm.")]),
    ], svc=svc)
    res = agent.ask("what's on my calendar today?")

    assert res.text == "You have the dentist at 3pm." and res.stop_reason == "end_turn"
    first = client.requests[0]
    assert first["betas"] == [FALLBACK_BETA] and first["fallbacks"] == "default"
    assert first["model"] == "claude-opus-5-5" and first["output_config"] == {"effort": "medium"}
    assert "Thursday 2026-10-01 08:00 EDT" in first["messages"][0]["content"]
    assert [t["name"] for t in first["tools"]] == sorted(t["name"] for t in first["tools"])
    # second request carries the tool result in one user message, linked by id
    result_msg = client.requests[1]["messages"][-1]
    assert result_msg["role"] == "user" and result_msg["content"][0]["tool_use_id"] == "t1"
    assert "Dentist" in result_msg["content"][0]["content"] and result_msg["content"][0]["is_error"] is False
    audit = [json.loads(l) for l in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert audit[0]["tool"] == "calendar_list_events" and audit[0]["ok"] is True


@pytest.mark.parametrize("approve", [False, True])
def test_write_tools_need_approval(tmp_path, approve):
    svc = FakeCalendar()
    seen = []
    agent, client = make_agent(tmp_path, [
        NS(stop_reason="tool_use", content=[tool_use("t1", "calendar_create_event", {
            "summary": "Gym", "start": "2026-10-01T18:00", "end": "2026-10-01T19:00",
            "description": None, "location": None, "attendees": None})]),
        NS(stop_reason="end_turn", content=[text("done")]),
    ], approver=lambda n, a: seen.append(n) or approve, svc=svc)
    res = agent.ask("book gym at 6")
    assert seen == ["calendar_create_event"]
    assert bool(svc.inserted) is approve
    tr = client.requests[1]["messages"][-1]["content"][0]
    assert tr["is_error"] is (not approve)
    assert res.tool_calls[0]["approved"] is approve


def test_tool_errors_and_unknown_tools_go_back_to_model(tmp_path):
    agent, client = make_agent(tmp_path, [
        NS(stop_reason="tool_use", content=[
            tool_use("t1", "calendar_list_events", {"start_date": "not-a-date", "end_date": None}),
            tool_use("t2", "nope", {})]),
        NS(stop_reason="end_turn", content=[text("Sorry, bad date.")]),
    ])
    agent.ask("x")
    results = client.requests[1]["messages"][-1]["content"]
    assert len(results) == 2 and all(r["is_error"] for r in results)
    assert "ValueError" in results[0]["content"] and "Unknown tool" in results[1]["content"]


def test_refusal_and_turn_limit(tmp_path):
    agent, _ = make_agent(tmp_path, [NS(stop_reason="refusal", content=[])])
    assert agent.ask("x").text == "I can't help with that request."
    loop = [NS(stop_reason="tool_use", content=[tool_use(f"t{i}", "calendar_list_events",
                                                         {"start_date": "2026-10-01", "end_date": None})])
            for i in range(12)]
    agent, _ = make_agent(tmp_path, loop)
    assert agent.ask("x").stop_reason == "max_turns"


def test_api_health():
    from fastapi.testclient import TestClient
    from quasi.api import app
    assert TestClient(app).get("/health").json() == {"ok": True}
