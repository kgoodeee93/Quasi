#!/usr/bin/env python3
"""Offline harness for Quasi core: real tools, runtime, approval gate and audit log,
with a fixture Google Calendar and (for `agent`) a scripted model. No API keys needed.

  python3 .claude/skills/run-quasi/driver.py tools
  python3 .claude/skills/run-quasi/driver.py call <tool_name> '<json args>'
  python3 .claude/skills/run-quasi/driver.py agent [--approve]

`call` runs one tool handler exactly as the agent would (same registry, same schema).
`agent` runs Agent.ask() through a canned two-step exchange (list events, then create one)
so you can watch the loop, the approval prompt result, tool_result linking and the audit log.
Run from the repo root (the `quasi` package must be importable: `pip install -e .`).
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

from quasi.agents.approvals import AuditLog
from quasi.agents.runtime import Agent
from quasi.config import Settings
from quasi.integrations.base import ToolRegistry
from quasi.integrations.gcal import GoogleCalendar

TZ = "America/New_York"
TODAY = date.today().isoformat()


class _Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class FixtureCalendar:
    """Stands in for googleapiclient's calendar service: two meetings today, records inserts."""

    def __init__(self):
        self.inserted = []
        self.items = [
            {"summary": "Standup", "start": {"dateTime": f"{TODAY}T09:00:00-04:00"},
             "end": {"dateTime": f"{TODAY}T09:15:00-04:00"}, "hangoutLink": "https://meet.example/abc",
             "attendees": [{"email": "kevin@example.com", "self": True, "responseStatus": "accepted"},
                           {"email": "amy@example.com"}]},
            {"summary": "Client call: Acme", "start": {"dateTime": f"{TODAY}T13:00:00-04:00"},
             "end": {"dateTime": f"{TODAY}T14:00:00-04:00"}},
        ]

    def settings(self):
        return NS(get=lambda setting: _Exec({"value": TZ}))

    def events(self):
        def insert(calendarId, body, sendUpdates):
            self.inserted.append(body)
            return _Exec({**body, "id": "fixture-1", "htmlLink": "https://calendar.example/fixture-1"})
        return NS(list=lambda **kw: _Exec({"items": self.items}), insert=insert)

    def freebusy(self):
        busy = [{"start": i["start"]["dateTime"], "end": i["end"]["dateTime"]} for i in self.items]
        return NS(query=lambda body: _Exec({"calendars": {"primary": {"busy": busy}}}))


class ScriptedClaude:
    """Returns canned responses in order; records every request it receives."""

    def __init__(self, script):
        self.script, self.requests = list(script), []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.requests.append({**kw, "messages": list(kw["messages"])})  # snapshot: the loop mutates the list
        return self.script.pop(0)


def setup(home: Path):
    settings = Settings(model="claude-opus-5-5", effort="medium", max_turns=6,
                        google_client_secret=home / "cs.json", google_token=home / "tok.json",
                        audit_log=home / "audit.jsonl", timezone=None)
    cal = GoogleCalendar(settings, service=FixtureCalendar())
    return settings, cal, ToolRegistry().add(cal)


def main(argv: list[str]) -> int:
    home = Path(tempfile.mkdtemp(prefix="quasi-driver-"))
    settings, cal, registry = setup(home)
    cmd = argv[0] if argv else "help"

    if cmd == "tools":
        for name, t in sorted(registry.tools.items()):
            print(f"{name:28} writes={t.writes}")
        return 0

    if cmd == "call":
        tool = registry.tools[argv[1]]
        args = json.loads(argv[2]) if len(argv) > 2 else {}
        print(json.dumps(tool.handler(**args), indent=2, default=str))
        return 0

    if cmd == "agent":
        approve = "--approve" in argv
        tool = lambda i, n, a: NS(type="tool_use", id=i, name=n, input=a)  # noqa: E731
        text = lambda t: NS(type="text", text=t)  # noqa: E731
        client = ScriptedClaude([
            NS(stop_reason="tool_use", content=[tool("t1", "calendar_list_events",
                                                     {"start_date": TODAY, "end_date": None})]),
            NS(stop_reason="tool_use", content=[tool("t2", "calendar_create_event", {
                "summary": "Gym", "start": f"{TODAY}T18:00", "end": f"{TODAY}T19:00",
                "description": None, "location": None, "attendees": None})]),
            NS(stop_reason="end_turn", content=[text("(scripted final answer)")]),
        ])
        agent = Agent(client=client, registry=registry, approver=lambda n, a: approve,
                      audit=AuditLog(settings.audit_log), now=lambda: datetime.now(ZoneInfo(TZ)))
        res = agent.ask("what's on my calendar today? also book gym at 6pm")
        print("first user turn:", client.requests[0]["messages"][0]["content"].splitlines()[0])
        print("request params:", {k: client.requests[0][k] for k in ("model", "betas", "fallbacks", "output_config")})
        for i, req in enumerate(client.requests[1:], 1):
            for r in req["messages"][-1]["content"]:
                print(f"tool_result #{i} ({r['tool_use_id']}, is_error={r['is_error']}): {r['content'][:160]}")
        print("events inserted into calendar:", len(cal.service.inserted))
        print("audit log:", settings.audit_log)
        print(settings.audit_log.read_text().strip())
        print("final:", res.text, f"[{res.stop_reason}]")
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
