"""Google Calendar integration: read your schedule, find free time, create events (approval-gated)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .base import Tool, schema
from .google_auth import get_credentials

# events = read + create on your calendars; calendar.readonly covers the timezone setting and free/busy.
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
]


class GoogleCalendar:
    name = "gcal"

    def __init__(self, settings, service=None, interactive: bool = True):
        self.settings = settings
        self._service = service          # injectable for tests
        self.interactive = interactive
        self._tz: ZoneInfo | None = None

    # ---- plugin interface ----
    def connect(self) -> None:
        if self._service is None:
            from googleapiclient.discovery import build
            creds = get_credentials(SCOPES, self.settings.google_client_secret,
                                    self.settings.google_token, self.interactive)
            self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    def sync(self) -> dict[str, Any]:
        today = self.list_events(date.today().isoformat(), None)
        return {"events_today": len(today["events"])}

    def tools(self) -> list[Tool]:
        return [
            Tool(
                name="calendar_list_events",
                description=(
                    "List events on the user's Google Calendar between two dates (inclusive). "
                    "Use for questions like 'what's on my calendar today/tomorrow/this week'. "
                    "Dates are YYYY-MM-DD in the user's timezone; end_date defaults to start_date."),
                input_schema=schema({
                    "start_date": {"type": "string", "description": "First day, YYYY-MM-DD"},
                    "end_date": {"type": "string", "description": "Last day, YYYY-MM-DD"},
                }, required=["start_date"]),
                handler=self.list_events,
            ),
            Tool(
                name="calendar_find_free_time",
                description=(
                    "Find open slots of at least `duration_minutes` on a given day, within working hours "
                    "(default 09:00-18:00 in the user's timezone)."),
                input_schema=schema({
                    "day": {"type": "string", "description": "YYYY-MM-DD"},
                    "duration_minutes": {"type": "integer", "description": "Minimum slot length"},
                    "day_start": {"type": "string", "description": "HH:MM, default 09:00"},
                    "day_end": {"type": "string", "description": "HH:MM, default 18:00"},
                }, required=["day", "duration_minutes"]),
                handler=self.find_free_time,
            ),
            Tool(
                name="calendar_create_event",
                description=(
                    "Create an event on the user's primary calendar. This is a write action and the "
                    "user must approve it. Times are local ISO datetimes like 2026-10-02T14:00."),
                input_schema=schema({
                    "summary": {"type": "string", "description": "Event title"},
                    "start": {"type": "string", "description": "Local start, YYYY-MM-DDTHH:MM"},
                    "end": {"type": "string", "description": "Local end, YYYY-MM-DDTHH:MM"},
                    "description": {"type": "string", "description": "Notes"},
                    "location": {"type": "string", "description": "Place or video link"},
                    "attendees": {"type": "array", "items": {"type": "string"}, "description": "Guest emails"},
                }, required=["summary", "start", "end"]),
                handler=self.create_event,
                writes=True,
            ),
        ]

    # ---- helpers ----
    @property
    def service(self):
        self.connect()
        return self._service

    @property
    def tz(self) -> ZoneInfo:
        if self._tz is None:
            name = self.settings.timezone
            if not name:
                name = self.service.settings().get(setting="timezone").execute().get("value", "UTC")
            self._tz = ZoneInfo(name)
        return self._tz

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def _day_bounds(self, start: str, end: str | None) -> tuple[datetime, datetime]:
        d0 = date.fromisoformat(start)
        d1 = date.fromisoformat(end) if end else d0
        if d1 < d0:
            d0, d1 = d1, d0
        return (datetime.combine(d0, time.min, self.tz), datetime.combine(d1 + timedelta(days=1), time.min, self.tz))

    @staticmethod
    def _event(e: dict[str, Any]) -> dict[str, Any]:
        s, en = e.get("start", {}), e.get("end", {})
        return {
            "title": e.get("summary", "(no title)"),
            "start": s.get("dateTime") or s.get("date"),
            "end": en.get("dateTime") or en.get("date"),
            "all_day": "date" in s,
            "location": e.get("location"),
            "video_link": e.get("hangoutLink"),
            "attendees": [a.get("email") for a in e.get("attendees", []) if not a.get("self")],
            "my_response": next((a.get("responseStatus") for a in e.get("attendees", []) if a.get("self")), None),
        }

    # ---- tool handlers ----
    def list_events(self, start_date: str, end_date: str | None = None) -> dict[str, Any]:
        t0, t1 = self._day_bounds(start_date, end_date)
        items, page = [], None
        while True:
            res = self.service.events().list(
                calendarId="primary", timeMin=t0.isoformat(), timeMax=t1.isoformat(),
                singleEvents=True, orderBy="startTime", maxResults=250, pageToken=page).execute()
            items += res.get("items", [])
            page = res.get("nextPageToken")
            if not page:
                break
        events = [self._event(e) for e in items if e.get("status") != "cancelled"]
        return {"timezone": str(self.tz), "from": t0.isoformat(), "to": t1.isoformat(), "events": events}

    def find_free_time(self, day: str, duration_minutes: int, day_start: str | None = None,
                       day_end: str | None = None) -> dict[str, Any]:
        d = date.fromisoformat(day)
        ws = datetime.combine(d, time.fromisoformat(day_start or "09:00"), self.tz)
        we = datetime.combine(d, time.fromisoformat(day_end or "18:00"), self.tz)
        res = self.service.freebusy().query(body={
            "timeMin": ws.isoformat(), "timeMax": we.isoformat(), "timeZone": str(self.tz),
            "items": [{"id": "primary"}]}).execute()
        busy = sorted((datetime.fromisoformat(b["start"].replace("Z", "+00:00")).astimezone(self.tz),
                       datetime.fromisoformat(b["end"].replace("Z", "+00:00")).astimezone(self.tz))
                      for b in res["calendars"]["primary"].get("busy", []))
        slots, cursor = [], ws
        for b0, b1 in busy:
            if b0 > cursor and (b0 - cursor) >= timedelta(minutes=duration_minutes):
                slots.append((cursor, b0))
            cursor = max(cursor, b1)
        if we > cursor and (we - cursor) >= timedelta(minutes=duration_minutes):
            slots.append((cursor, we))
        return {"timezone": str(self.tz), "day": day,
                "free_slots": [{"start": a.strftime("%H:%M"), "end": b.strftime("%H:%M"),
                                "minutes": int((b - a).total_seconds() // 60)} for a, b in slots]}

    def create_event(self, summary: str, start: str, end: str, description: str | None = None,
                     location: str | None = None, attendees: list[str] | None = None) -> dict[str, Any]:
        def local(s: str) -> dict[str, str]:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=self.tz)
            return {"dateTime": dt.isoformat(), "timeZone": str(self.tz)}
        body: dict[str, Any] = {"summary": summary, "start": local(start), "end": local(end)}
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        if attendees:
            body["attendees"] = [{"email": a} for a in attendees]
        created = self.service.events().insert(calendarId="primary", body=body,
                                               sendUpdates="all" if attendees else "none").execute()
        return {"created": True, "id": created.get("id"), "link": created.get("htmlLink"), **self._event(created)}
