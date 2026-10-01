"""The plugin contract every integration implements.

An integration exposes typed Tools. A Tool is a JSON-schema'd function the agent
can call; `writes=True` marks anything that changes the outside world (sends,
creates, pays, deletes), and the runtime routes those through the approval gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Any]
    writes: bool = False  # True -> require_approval() before it runs

    def to_api(self) -> dict[str, Any]:
        """The tool definition sent to the Claude API."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "strict": True,
        }


def schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    """JSON schema for a tool's input. Strict tool use needs additionalProperties: false
    and every property listed in `required`; optional ones are made nullable instead."""
    req = required if required is not None else list(properties)
    props = {}
    for name, spec in properties.items():
        if name not in req:
            spec = {**spec, "type": [spec["type"], "null"]}
        props[name] = spec
    return {"type": "object", "properties": props, "required": list(properties), "additionalProperties": False}


class Integration(Protocol):
    name: str

    def connect(self) -> None:
        """Authenticate (OAuth etc.). Safe to call repeatedly."""

    def sync(self) -> dict[str, Any]:
        """Pull fresh data for caching/briefs. Returns a small status dict."""

    def tools(self) -> list[Tool]:
        """Tools this integration offers to agents."""


@dataclass
class ToolRegistry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def add(self, *integrations: Integration) -> "ToolRegistry":
        for integ in integrations:
            for t in integ.tools():
                if t.name in self.tools:
                    raise ValueError(f"duplicate tool name: {t.name}")
                self.tools[t.name] = t
        return self

    def api_definitions(self) -> list[dict[str, Any]]:
        # Sorted so the tool list is byte-stable between requests (prompt caching).
        return [self.tools[n].to_api() for n in sorted(self.tools)]
