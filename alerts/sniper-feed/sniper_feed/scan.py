"""One scan: candidates -> 5-minute activity rules -> safety rules -> alerts.

Cheap checks first: GeckoTerminal's pool lists already carry the 5-minute numbers, so the
activity rules cost nothing per token. Only tokens that pass them get Moby and RugCheck lookups.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx

from . import sources
from .rules import ACTIVITY, SAFETY, Rules, evaluate, passed

MAX_DEEP_CHECKS = 8   # tokens per scan that get Moby + RugCheck lookups


@dataclass
class ScanResult:
    candidates: int = 0
    active: int = 0
    alerts: list[dict] = field(default_factory=list)      # {"token", "metrics", "checks"}
    near_misses: list[dict] = field(default_factory=list)  # passed activity, failed safety
    warnings: list[str] = field(default_factory=list)


def merge_safety(moby: dict, rug: dict | None) -> dict:
    """Moby first; RugCheck fills dev holding and the authorities when Moby has no number.
    Bundled supply comes only from RugCheck's linked-wallet networks."""
    rug = rug or {}
    out = dict(moby)
    for key in ("dev_pct", "mint_disabled", "freeze_disabled"):
        if out.get(key) is None and rug.get(key) is not None:
            out[key] = rug[key]
    out["bundled_pct"] = rug.get("bundled_pct")
    out["linked_wallets"] = rug.get("linked_wallets")
    out["rugged"] = rug.get("rugged", False)
    return out


def hard_blocks(m: dict) -> list[dict]:
    """Never alerted whatever the rules say; recorded as checks so misses show why."""
    return [
        {"rule": "not_red_tier", "label": "Moby safety tier not red", "limit": True,
         "value": m.get("safety_tier"), "ok": m.get("safety_tier") != "red"},
        {"rule": "not_rugged", "label": "RugCheck: not rugged", "limit": True,
         "value": bool(m.get("rugged")), "ok": not m.get("rugged")},
        {"rule": "not_tokenized_stock", "label": "Not a tokenized stock", "limit": True,
         "value": m.get("asset_class"), "ok": m.get("asset_class") != "tokenized_stock"},
        # Dozens of buys and not one sell in 5 minutes can mean nobody is able to sell: a honeypot.
        {"rule": "has_sells_5m", "label": "Sells in last 5m (honeypot guard)", "limit": True,
         "value": m.get("sells_5m"), "ok": (m.get("sells_5m") or 0) > 0},
    ]


async def run_scan(client: httpx.AsyncClient, rules: Rules, moby: sources.Moby | None,
                   skip=lambda mint: False) -> ScanResult:
    res = ScanResult()
    pools = await sources.gecko_candidates(client)
    res.candidates = len(pools)

    active = [p for p in pools if passed(evaluate(p, rules, ACTIVITY)) and not skip(p["mint"])]
    active.sort(key=lambda p: p["vol_5m"] or 0, reverse=True)
    res.active = len(active)
    deep = active[:MAX_DEEP_CHECKS]
    if len(active) > MAX_DEEP_CHECKS:
        res.warnings.append(f"{len(active) - MAX_DEEP_CHECKS} active tokens not deep-checked this scan")
    if not deep:
        return res

    overviews: dict = {}
    if moby:
        try:
            overviews = await moby.overviews([p["mint"] for p in deep])
        except (sources.SourceError, httpx.HTTPError) as e:
            res.warnings.append(f"Moby overviews unavailable: {e}")
    else:
        res.warnings.append("No MOBY_API_KEY: top-10 and sniper rules can't pass")

    sem = asyncio.Semaphore(4)

    async def safe(coro):
        async with sem:
            try:
                return await coro
            except (sources.SourceError, httpx.HTTPError) as e:
                return e

    risks = await asyncio.gather(*(safe(moby.risk(p["mint"])) if moby else asyncio.sleep(0) for p in deep))
    rugs = await asyncio.gather(*(safe(sources.rugcheck(client, p["mint"])) for p in deep))

    for p, risk, rug in zip(deep, risks, rugs):
        for name, v in (("Moby risk", risk), ("RugCheck", rug)):
            if isinstance(v, Exception):
                res.warnings.append(f"{p['symbol']}: {name} unavailable ({v})")
        m = merge_safety(sources.moby_metrics(overviews.get(p["mint"]),
                                              None if isinstance(risk, Exception) else risk),
                         None if isinstance(rug, Exception) else rug)
        metrics = {**p, **m}
        checks = evaluate(metrics, rules) + hard_blocks(metrics)
        entry = {"token": p, "metrics": metrics, "checks": checks}
        if passed(checks):
            res.alerts.append(entry)
        else:
            res.near_misses.append(entry)
    return res
