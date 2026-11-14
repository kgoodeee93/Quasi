from __future__ import annotations

from datetime import datetime
from typing import Any


def build_demo_overview() -> dict[str, Any]:
    now = datetime.now()
    return {
        "connected": False,
        "summary": "Everything is set up for a clean, useful daily view, but Google Calendar is not connected yet.",
        "events": [
            {"title": "Daily standup", "time": "9:00 AM", "location": "Zoom", "type": "meeting"},
            {"title": "Client check-in", "time": "11:30 AM", "location": "Google Meet", "type": "meeting"},
            {"title": "Deep work block", "time": "2:00 PM", "location": "Focus time", "type": "focus"},
            {"title": "Follow-up calls", "time": "4:30 PM", "location": "Phone", "type": "call"},
        ],
        "focus": [
            "Review inbox and triage priority messages",
            "Ship the most important task before lunch",
            "Keep one uninterrupted work block for execution",
        ],
        "kpis": [
            {"label": "Today", "value": "4 blocks", "tone": "blue"},
            {"label": "Focus", "value": "2 hrs", "tone": "green"},
            {"label": "Approvals", "value": "3 pending", "tone": "amber"},
        ],
        "approvals": [
            {"title": "Accept calendar invite", "context": "Client sync", "status": "pending"},
            {"title": "Approve invoice follow-up", "context": "Finance", "status": "pending"},
            {"title": "Reply to lead", "context": "Inbox", "status": "pending"},
        ],
        "generated_at": now.isoformat(),
    }


__all__ = ["build_demo_overview"]
