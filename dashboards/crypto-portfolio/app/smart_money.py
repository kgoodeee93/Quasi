"""Who's buying what you hold: Moby whale / KOL flow for your Solana tokens, plus the market pulse.

Moby covers Solana only. SOL and stablecoins are left out of the per-token view: Solana swaps
settle in them, so Moby records no flow "into" them and would always report them as quiet.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

import httpx

from .config import Settings
from .providers.moby import Moby

WINDOWS = ("h1", "h4", "h24")
MAX_TOKENS = 25          # largest Solana holdings by value; Moby allows 600 reads/minute
NO_FLOW = {"solana"}     # plus every stablecoin (is_stable)
MINT = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")  # base58 Solana address

FLOW_KEYS = ("net_flow_usd", "buy_volume_usd", "sell_volume_usd", "buy_count", "sell_count",
             "wallets_in", "last_buy_at", "last_sell_at")
SLICE_KEYS = ("net_flow_usd", "buy_volume_usd", "sell_volume_usd", "buyers", "sellers", "trades",
              "dump_sells")
TOP_KEYS = ("net_flow_usd", "buy_volume_usd", "sell_volume_usd", "buyers", "sellers")


def tracked_tokens(portfolio: dict) -> tuple[list[dict], list[str]]:
    """(Solana tokens to look up, symbols of Solana holdings deliberately skipped)."""
    mints = portfolio.get("solana_tokens") or {}
    tokens, skipped = [], []
    for r in portfolio["views"]["all"]["holdings"]:  # already sorted by value, largest first
        mint = mints.get(r["id"])
        if not mint or not MINT.match(mint):
            continue
        if r["id"] in NO_FLOW or r.get("is_stable"):
            skipped.append(r["symbol"])
            continue
        tokens.append({"coin_id": r["id"], "mint": mint, "symbol": r["symbol"], "name": r["name"],
                       "value": r["value"]})
    return tokens[:MAX_TOKENS], skipped


def _pick(d: dict | None, keys: tuple[str, ...]) -> dict:
    """Copy the fields the panel shows, typed: numbers stay numbers, timestamps become UTC, anything
    else becomes None, so nothing from Moby reaches the page as markup by way of a numeric field."""
    d = d or {}
    out = {}
    for k in keys:
        v = d.get(k)
        if k in ("last_buy_at", "last_sell_at"):
            out[k] = _utc(v) if isinstance(v, str) else None
        elif k in ("tier", "label", "text"):
            out[k] = str(v) if v is not None else None
        else:
            out[k] = _num(v)
    return out


def _num(v) -> float | int | None:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _utc(ts: str | None) -> str | None:
    """Moby timestamps carry no zone; they are UTC."""
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).isoformat()


def _pulse_window(w: dict, held: set[str]) -> dict:
    by_type = w.get("by_wallet_type") or {}

    def top(rows):
        return [{"mint": r["token_address"], "symbol": str(r.get("symbol") or "?")[:24], **_pick(r, TOP_KEYS),
                 "held": r["token_address"] in held}
                for r in rows or [] if MINT.match(str(r.get("token_address", "")))]

    return {"all": _pick(w.get("totals"), SLICE_KEYS), "whale": _pick(by_type.get("whale"), SLICE_KEYS),
            "kol": _pick(by_type.get("kol"), SLICE_KEYS),
            "top_net_buy": top(w.get("top_net_buy")), "top_net_sell": top(w.get("top_net_sell"))}


async def collect_smart_money(portfolio: dict, settings: Settings,
                              client: httpx.AsyncClient | None = None) -> dict:
    own_client = client is None
    client = client or httpx.AsyncClient(headers={"user-agent": "quasi-crypto-dashboard/1.0"})
    try:
        moby = Moby(client, settings.moby_url, settings.moby_api_key)
        tokens, skipped = tracked_tokens(portfolio)
        held = set((portfolio.get("solana_tokens") or {}).values())

        pulse = await moby.call("get_smart_money_pulse")  # first call also opens the MCP session

        sem = asyncio.Semaphore(5)

        async def flow(mint: str) -> dict:
            async with sem:
                return await moby.call("get_token_smart_money", {"token_address": mint})

        async def overviews() -> dict:
            if not tokens:
                return {}
            res = await moby.call("get_token_overviews", {"token_addresses": [t["mint"] for t in tokens]})
            out = {}
            for r in res.get("results", []):
                if r.get("found"):
                    body = r.get("overview") or {}  # {"overview": {...}, "data_as_of", "data_notes"}
                    out[r.get("token_address")] = body.get("overview", body)
            return out

        ov, *flows = await asyncio.gather(overviews(), *(flow(t["mint"]) for t in tokens),
                                          return_exceptions=True)
        warnings: list[str] = []
        if isinstance(ov, Exception):
            warnings.append(f"Moby token overviews unavailable ({ov})")
            ov = {}

        rows = []
        for t, f in zip(tokens, flows):
            o = ov.get(t["mint"]) or {}
            read = o.get("smart_money_read")
            if isinstance(f, Exception):
                warnings.append(f"{t['symbol']}: smart-money flow unavailable ({f})")
            rows.append({**t,
                         "safety_tier": o.get("safety_tier") if isinstance(o.get("safety_tier"), str) else None,
                         "safety_note": str(o.get("safety_note") or "")[:200] or None,
                         "read": _pick(read, ("tier", "label", "text")) if read else None,
                         "flow": None if isinstance(f, Exception)
                         else {w: _pick((f.get("flow") or {}).get(w), FLOW_KEYS) for w in WINDOWS}})

        return {
            "enabled": True,
            "source": "Moby",
            "as_of": _utc(pulse.get("data_as_of")),
            "min_trade_usd": _num(pulse.get("min_trade_usd")),
            "holdings": rows,
            "skipped": skipped,
            "pulse": {w: _pulse_window(pulse.get(w) or {}, held) for w in WINDOWS},
            "warnings": warnings,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        if own_client:
            await client.aclose()
