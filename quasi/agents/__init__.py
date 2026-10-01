"""Agent construction. Phase 0 has one agent with the Calendar tools; the router comes in Phase 1."""
from __future__ import annotations

from datetime import datetime

from ..config import Settings
from ..integrations.base import ToolRegistry
from ..integrations.gcal import GoogleCalendar
from .approvals import Approver, AuditLog, deny_all
from .runtime import Agent, AgentResult

__all__ = ["Agent", "AgentResult", "build_agent"]


def build_agent(settings: Settings | None = None, approver: Approver = deny_all,
                interactive_auth: bool = True, client=None) -> Agent:
    settings = settings or Settings.load()
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    gcal = GoogleCalendar(settings, interactive=interactive_auth)
    return Agent(
        client=client,
        registry=ToolRegistry().add(gcal),
        approver=approver,
        audit=AuditLog(settings.audit_log),
        model=settings.model,
        effort=settings.effort,
        max_turns=settings.max_turns,
        now=lambda: datetime.now(gcal.tz),
    )
