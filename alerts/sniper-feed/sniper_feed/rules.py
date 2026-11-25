"""The screen: 11 rules, each checked against one metric. A missing metric fails the rule.

A paid feed must never alert on a token it could not fully check, so "no data" is a fail, and the
alert shows which source was missing instead of guessing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass(frozen=True)
class Rules:
    vol_5m_min: float = 15_000
    txns_5m_min: int = 50
    buys_5m_min: int = 30
    buyers_5m_min: int = 25
    buy_sell_ratio_min: float = 1.5
    top10_max_pct: float = 25
    dev_max_pct: float = 5
    sniper_max_pct: float = 15
    bundled_max_pct: float = 5
    mint_disabled: bool = True
    freeze_disabled: bool = True

    @classmethod
    def load(cls, path: str | Path) -> "Rules":
        data = json.loads(Path(path).read_text())
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown rule(s) in {path}: {', '.join(sorted(unknown))}")
        return cls(**data)


# (rule field, label, metric key, kind). kind: "min" value >= limit, "max" value <= limit,
# "flag" value must be True when the rule is switched on.
ACTIVITY = (
    ("vol_5m_min", "Volume 5m", "vol_5m", "min"),
    ("txns_5m_min", "Transactions 5m", "txns_5m", "min"),
    ("buys_5m_min", "Buys 5m", "buys_5m", "min"),
    ("buyers_5m_min", "Buyers 5m", "buyers_5m", "min"),
    ("buy_sell_ratio_min", "Buys / sells", "buy_sell_ratio", "min"),
)
SAFETY = (
    ("top10_max_pct", "Top 10 holders", "top10_pct", "max"),
    ("dev_max_pct", "Dev holding", "dev_pct", "max"),
    ("sniper_max_pct", "Sniper holding", "sniper_pct", "max"),
    ("bundled_max_pct", "Bundled supply", "bundled_pct", "max"),
    ("mint_disabled", "Mint authority disabled", "mint_disabled", "flag"),
    ("freeze_disabled", "Freeze authority disabled", "freeze_disabled", "flag"),
)
ALL = ACTIVITY + SAFETY


def evaluate(metrics: dict, rules: Rules, which=ALL) -> list[dict]:
    """One row per rule: {rule, label, limit, value, ok}. ok is False when the metric is missing."""
    out = []
    for field, label, key, kind in which:
        limit = getattr(rules, field)
        value = metrics.get(key)
        if kind == "flag":
            ok = True if not limit else value is True
        elif value is None:
            ok = False
        elif kind == "min":
            ok = value >= limit
        else:
            ok = value <= limit
        out.append({"rule": field, "label": label, "limit": limit, "value": value, "ok": ok})
    return out


def passed(checks: list[dict]) -> bool:
    return all(c["ok"] for c in checks)
