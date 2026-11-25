"""Data sources, all read-only.

- GeckoTerminal (free, keyless, 30 calls/min): trending and new Solana pools with 5-minute buys,
  sells, unique buyers and volume; spot prices for the track record.
- Moby MCP: top-10 share, dev and sniper holdings, mint and freeze authority, safety tier.
- RugCheck: wallets linked to each other by transfers ("insider networks"), the bundle proxy.
"""
from __future__ import annotations

import json
import re

import httpx

GECKO = "https://api.geckoterminal.com/api/v2"
RUGCHECK = "https://api.rugcheck.xyz/v1"
MOBY_URL = "https://mcp.moby.win/mcp"

MINT = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
# Quote assets: a pool whose base token is one of these is not a token launch.
NOT_TOKENS = {
    "So11111111111111111111111111111111111111112",   # wSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}


class SourceError(RuntimeError):
    pass


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --- GeckoTerminal -------------------------------------------------------------------------------

def parse_pool(row: dict) -> dict | None:
    a = row.get("attributes") or {}
    base = (((row.get("relationships") or {}).get("base_token") or {}).get("data") or {}).get("id", "")
    mint = base.removeprefix("solana_")
    if not MINT.match(mint) or mint in NOT_TOKENS:
        return None
    m5 = (a.get("transactions") or {}).get("m5") or {}
    buys, sells = m5.get("buys"), m5.get("sells")
    ratio = None
    if isinstance(buys, int) and isinstance(sells, int):
        ratio = round(buys / sells, 2) if sells else (999.0 if buys else None)  # no sells: cap, JSON-safe
    return {
        "mint": mint,
        "symbol": str(a.get("name", "?")).split(" / ")[0][:24],
        "pool": a.get("address"),
        "price": _f(a.get("base_token_price_usd")),
        "mcap": _f(a.get("market_cap_usd")) or _f(a.get("fdv_usd")),
        "liquidity": _f(a.get("reserve_in_usd")),
        "pool_created_at": a.get("pool_created_at"),
        "vol_5m": _f((a.get("volume_usd") or {}).get("m5")),
        "buys_5m": buys,
        "sells_5m": sells,
        "txns_5m": buys + sells if isinstance(buys, int) and isinstance(sells, int) else None,
        "buyers_5m": m5.get("buyers"),
        "buy_sell_ratio": ratio,
    }


async def gecko_candidates(client: httpx.AsyncClient, trending_pages: int = 2) -> list[dict]:
    """Trending and newest Solana pools, one row per token (its deepest pool)."""
    paths = [f"/networks/solana/trending_pools?page={p}" for p in range(1, trending_pages + 1)]
    paths.append("/networks/solana/new_pools?page=1")
    best: dict[str, dict] = {}
    for path in paths:
        r = await client.get(GECKO + path, headers={"accept": "application/json"})
        if r.status_code == 429:
            raise SourceError("GeckoTerminal rate limit (30 calls/min) hit; scan less often")
        r.raise_for_status()
        for row in r.json().get("data", []):
            p = parse_pool(row)
            if p and (p["mint"] not in best or (p["liquidity"] or 0) > (best[p["mint"]]["liquidity"] or 0)):
                best[p["mint"]] = p
    return list(best.values())


async def gecko_prices(client: httpx.AsyncClient, mints: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for i in range(0, len(mints), 30):
        chunk = ",".join(mints[i:i + 30])
        r = await client.get(f"{GECKO}/simple/networks/solana/token_price/{chunk}")
        r.raise_for_status()
        prices = ((r.json().get("data") or {}).get("attributes") or {}).get("token_prices") or {}
        out.update({m: p for m, v in prices.items() if (p := _f(v)) is not None})
    return out


# --- RugCheck ------------------------------------------------------------------------------------

def parse_rugcheck(report: dict) -> dict:
    """Linked-wallet supply %, plus fallbacks for dev holding and the two authorities."""
    supply = _f((report.get("token") or {}).get("supply"))
    nets = report.get("insiderNetworks")
    bundled = None
    if supply and isinstance(nets, list):
        bundled = sum(_f(n.get("currentHolding")) or 0 for n in nets) / supply * 100
    creator = _f(report.get("creatorBalance"))
    return {
        "bundled_pct": round(bundled, 2) if bundled is not None else None,
        "linked_wallets": sum(int(n.get("activeAccounts") or 0) for n in nets) if isinstance(nets, list) else None,
        "dev_pct": round(creator / supply * 100, 4) if supply and creator is not None else None,
        "mint_disabled": "mintAuthority" in report and report["mintAuthority"] is None,
        "freeze_disabled": "freezeAuthority" in report and report["freezeAuthority"] is None,
        "rugged": bool(report.get("rugged")),
    }


async def rugcheck(client: httpx.AsyncClient, mint: str) -> dict:
    r = await client.get(f"{RUGCHECK}/tokens/{mint}/report", timeout=25)
    if r.status_code == 429:
        raise SourceError("RugCheck rate limit")
    r.raise_for_status()
    return parse_rugcheck(r.json())


# --- Moby (read-only MCP over streamable HTTP) ---------------------------------------------------

class Moby:
    """Only the read tools below can be called; anything else raises before a request is sent."""

    TOOLS = frozenset({"get_token_overviews", "get_token_risk_signals"})

    def __init__(self, client: httpx.AsyncClient, api_key: str, url: str = MOBY_URL):
        self.client, self.url = client, url
        self.headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream"}
        self._id = 0
        self._ready = False

    async def _rpc(self, method: str, params: dict | None = None, notify: bool = False) -> dict:
        msg: dict = {"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})}
        if not notify:
            self._id += 1
            msg["id"] = self._id
        r = await self.client.post(self.url, json=msg, headers=self.headers, timeout=25)
        if r.status_code in (401, 403):
            raise SourceError(f"Moby rejected the API key (HTTP {r.status_code})")
        r.raise_for_status()
        if sid := r.headers.get("mcp-session-id"):
            self.headers["Mcp-Session-Id"] = sid
        if notify:
            return {}
        if "text/event-stream" not in r.headers.get("content-type", ""):
            return r.json()
        for line in r.text.splitlines():
            if line.startswith("data:"):
                try:
                    m = json.loads(line[5:])
                except ValueError:
                    continue
                if m.get("id") == msg["id"]:
                    return m
        raise SourceError("Moby sent no response")

    async def call(self, tool: str, args: dict) -> dict:
        if tool not in self.TOOLS:
            raise SourceError(f"{tool} is not allowed; the feed only reads from Moby")
        if not self._ready:
            await self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                           "clientInfo": {"name": "sniper-feed", "version": "1.0"}})
            await self._rpc("notifications/initialized", notify=True)
            self._ready = True
        res = await self._rpc("tools/call", {"name": tool, "arguments": args})
        if "error" in res:
            raise SourceError(f"{tool}: {res['error'].get('message', res['error'])}")
        result = res.get("result") or {}
        text = " ".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        if result.get("isError"):
            raise SourceError(f"{tool}: {text[:200] or 'error'}")
        if isinstance(result.get("structuredContent"), dict):
            return result["structuredContent"]
        try:
            return json.loads(text)
        except ValueError as e:
            raise SourceError(f"{tool}: unexpected response") from e

    async def overviews(self, mints: list[str]) -> dict[str, dict]:
        res = await self.call("get_token_overviews", {"token_addresses": mints[:50]})
        out = {}
        for r in res.get("results", []):
            if r.get("found"):
                body = r.get("overview") or {}
                out[r.get("token_address")] = body.get("overview", body)
        return out

    async def risk(self, mint: str) -> dict:
        return await self.call("get_token_risk_signals", {"token_address": mint})


def moby_metrics(overview: dict | None, risk: dict | None) -> dict:
    """Moby's numbers in the feed's metric names; None wherever Moby has no data."""
    o, r = overview or {}, risk or {}
    ok = r.get("status") == "ok"
    return {
        "top10_pct": _f(o.get("top10_supply_pct")),
        "safety_tier": o.get("safety_tier") if isinstance(o.get("safety_tier"), str) else None,
        "asset_class": o.get("asset_class"),
        "dev_pct": _f(r.get("dev_hold_pct")) if ok else None,
        "sniper_pct": _f(r.get("snipers_pct")) if ok else None,
        "mint_disabled": r.get("mint_authority_renounced") if ok and isinstance(r.get("mint_authority_renounced"), bool) else None,
        "freeze_disabled": r.get("freeze_authority_renounced") if ok and isinstance(r.get("freeze_authority_renounced"), bool) else None,
    }
