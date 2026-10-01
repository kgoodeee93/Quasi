"""Agent runtime: a Claude tool-use loop over the integration tool registry.

Why a hand-written loop instead of the SDK's tool runner: tools come from the
plugin registry as JSON schemas, and every call has to pass through the
approval gate and the audit log. The loop is ~40 lines and we own all of it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from ..integrations.base import ToolRegistry
from .approvals import Approver, AuditLog, require_approval

SYSTEM_PROMPT = """You are Quasi, Kevin's operations assistant. You have tools for his real accounts \
(today: Google Calendar). Use them to answer; never guess at schedule details.

- Answer briefly and concretely: times, titles, who's involved. Lead with what matters most.
- Times are in the user's timezone; say "today"/"tomorrow" where it reads naturally.
- Read freely. Anything that changes the outside world (creating events, sending, paying) needs \
the user's approval, which the system asks for automatically when you call the tool. If a write \
is declined, say it wasn't done and don't retry it.
- If a tool fails, say what failed in one line and what the user can do about it."""

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16000


@dataclass
class AgentResult:
    text: str
    stop_reason: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)  # full history, for follow-up turns


@dataclass
class Agent:
    client: Any                      # anthropic.Anthropic (or a fake in tests)
    registry: ToolRegistry
    approver: Approver
    audit: AuditLog
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    max_turns: int = 12
    now: Callable[[], datetime] = datetime.now
    system: str = SYSTEM_PROMPT

    def ask(self, question: str, history: list[dict[str, Any]] | None = None) -> AgentResult:
        messages = list(history or [])
        # The clock goes in the user turn, not the system prompt, so the system
        # prompt and tool list stay byte-identical across requests (cacheable).
        stamp = self.now().strftime("%A %Y-%m-%d %H:%M %Z").strip()
        messages.append({"role": "user", "content": f"[Current local time: {stamp}]\n\n{question}"})
        calls: list[dict[str, Any]] = []
        tools = self.registry.api_definitions()

        for _ in range(self.max_turns):
            resp = self.client.beta.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=self.system,
                tools=tools,
                messages=messages,
                output_config={"effort": self.effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",  # a safety-classifier decline re-runs on a fallback model
            )
            # Append the full content (thinking, fallback and tool_use blocks included), unmodified.
            messages.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason == "tool_use":
                results = [self._run_tool(b, calls) for b in resp.content if b.type == "tool_use"]
                messages.append({"role": "user", "content": results})  # all results in one message
                continue
            if resp.stop_reason == "pause_turn":
                continue
            return AgentResult(self._text(resp), resp.stop_reason, calls, messages)

        return AgentResult("I hit my step limit before finishing. Try a narrower question.",
                           "max_turns", calls, messages)

    @staticmethod
    def _text(resp) -> str:
        if resp.stop_reason == "refusal":
            return "I can't help with that request."
        text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
        if resp.stop_reason == "max_tokens":
            text += "\n\n(Answer was cut off; ask me to continue.)"
        return text

    def _run_tool(self, block, calls: list[dict[str, Any]]) -> dict[str, Any]:
        args = dict(block.input) if isinstance(block.input, dict) else json.loads(block.input)
        tool = self.registry.tools.get(block.name)
        record: dict[str, Any] = {"tool": block.name, "input": args}

        def result(content: Any, error: bool = False) -> dict[str, Any]:
            record.update(ok=not error)
            calls.append(record)
            self.audit.record(**record)
            return {"type": "tool_result", "tool_use_id": block.id, "is_error": error,
                    "content": content if isinstance(content, str) else json.dumps(content, default=str)}

        if tool is None:
            return result(f"Unknown tool {block.name}.", error=True)
        if tool.writes:
            approved = require_approval(self.approver, tool.name, args)
            record["approved"] = approved
            if not approved:
                return result("The user declined this action. It was not performed. Do not retry it.", error=True)
        try:
            return result(tool.handler(**args))
        except Exception as e:  # report to the model; it explains to the user
            record["error"] = f"{type(e).__name__}: {e}"
            return result(f"Tool failed: {type(e).__name__}: {e}", error=True)
