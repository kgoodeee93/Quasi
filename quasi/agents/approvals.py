"""Human-in-the-loop gate and audit log.

Every tool call is logged. Every write tool asks the approver first; a "no"
goes back to the model as a declined tool result, so it can tell the user
instead of retrying.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# Approver: (tool name, tool input) -> approved?
Approver = Callable[[str, dict[str, Any]], bool]


def deny_all(name: str, args: dict[str, Any]) -> bool:
    """Default for non-interactive callers (API, scheduler) until the approvals queue exists."""
    return False


def cli_approver(name: str, args: dict[str, Any]) -> bool:
    print(f"\n⚠️  Quasi wants to run a write action: {name}")
    for k, v in args.items():
        if v is not None:
            print(f"   {k}: {v}")
    try:
        return input("   Approve? [y/N] ").strip().lower() in {"y", "yes"}
    except EOFError:
        return False


@dataclass
class AuditLog:
    """Append-only JSONL file. One line per tool call, approved or not."""
    path: Path

    def __post_init__(self):
        self._lock = threading.Lock()

    def record(self, **event: Any) -> None:
        event = {"ts": datetime.now(timezone.utc).isoformat(), **event}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, open(self.path, "a") as f:
            f.write(json.dumps(event, default=str) + "\n")
        os.chmod(self.path, 0o600)


def require_approval(approver: Approver, name: str, args: dict[str, Any]) -> bool:
    return bool(approver(name, args))
