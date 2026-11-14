"""Agent construction: the conversational agent (Calendar + Gmail tools) and the morning brief."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..config import Settings
from ..integrations.base import ToolRegistry
from ..integrations.gcal import GoogleCalendar
from ..integrations.gmail import Gmail
from . import briefing
from .approvals import Approver, AuditLog, deny_all
from .runtime import Agent, AgentResult

__all__ = ["Agent", "AgentResult", "build_agent", "run_brief"]


def _client(client):
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    return client


def build_agent(settings: Settings | None = None, approver: Approver = deny_all,
                interactive_auth: bool = True, client=None,
                calendar: GoogleCalendar | None = None, gmail: Gmail | None = None) -> Agent:
    settings = settings or Settings.load()
    gcal = calendar or GoogleCalendar(settings, interactive=interactive_auth)
    mail = gmail or Gmail(settings, interactive=interactive_auth)
    return Agent(
        client=_client(client),
        registry=ToolRegistry().add(gcal, mail),
        approver=approver,
        audit=AuditLog(settings.audit_log),
        model=settings.model,
        effort=settings.effort,
        max_turns=settings.max_turns,
        now=lambda: datetime.now(gcal.tz),
    )


def run_brief(settings: Settings | None = None, interactive_auth: bool = True, email: bool = False,
              client=None, calendar: GoogleCalendar | None = None, gmail: Gmail | None = None,
              home: Path | None = None) -> tuple[briefing.Brief, Path, dict | None]:
    """Gather, write, save (and optionally email to yourself) today's brief."""
    settings = settings or Settings.load()
    gcal = calendar or GoogleCalendar(settings, interactive=interactive_auth)
    mail = gmail or Gmail(settings, interactive=interactive_auth)
    now = datetime.now(gcal.tz)
    brief = briefing.write_brief(_client(client), briefing.gather(gcal, mail, now), settings.model, settings.effort)
    path = briefing.save(brief, home or settings.audit_log.parent, now)
    sent = mail.send_to_self(briefing.subject(now), brief.text) if email else None
    AuditLog(settings.audit_log).record(tool="morning_brief", saved=str(path), emailed=bool(sent),
                                        stop_reason=brief.stop_reason, ok=True)
    return brief, path, sent
