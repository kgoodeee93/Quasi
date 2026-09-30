"""Hyperliquid (HyperCore) account: spot balances, perp equity, staked HYPE, vault deposits.

All from the public info API, no key. CoinGecko lists HyperCore tokens under the
"hyperliquid" platform keyed by their tokenId, so spot tokens are priced (and spam
filtered) the same way as on-chain tokens.
"""
from __future__ import annotations

import httpx

from . import Position, Resolver

API = "https://api.hyperliquid.xyz/info"


async def _info(client: httpx.AsyncClient, body: dict):
    r = await client.post(API, json=body, timeout=30)
    r.raise_for_status()
    return r.json()


async def fetch_hyperliquid(client: httpx.AsyncClient, wallet_label: str, address: str,
                            resolve: Resolver) -> tuple[list[Position], list[str]]:
    positions: list[Position] = []
    warnings: list[str] = []

    spot_meta = await _info(client, {"type": "spotMeta"})
    token_ids = {t["index"]: t["tokenId"] for t in spot_meta["tokens"]}

    spot = await _info(client, {"type": "spotClearinghouseState", "user": address})
    skipped = 0
    for b in spot.get("balances", []):
        amount = float(b.get("total") or 0)
        if amount <= 0:
            continue
        coin = resolve("hyperliquid", token_ids.get(b["token"], ""))
        if coin:
            positions.append(Position(wallet_label, "hyperliquid spot", coin, amount))
        else:
            skipped += 1
    if skipped:
        warnings.append(f"{wallet_label} (hyperliquid): ignored {skipped} spot token(s) CoinGecko doesn't list")

    # Perp account equity is USDC collateral plus unrealized PnL, so it is valued as USDC.
    perps = await _info(client, {"type": "clearinghouseState", "user": address})
    equity = float((perps.get("marginSummary") or {}).get("accountValue") or 0)
    if equity > 0:
        positions.append(Position(wallet_label, "hyperliquid perps", "usd-coin", equity))

    staking = await _info(client, {"type": "delegatorSummary", "user": address})
    staked = sum(float(staking.get(k) or 0) for k in ("delegated", "undelegated", "totalPendingWithdrawal"))
    if staked > 0:
        positions.append(Position(wallet_label, "hyperliquid staking", "hyperliquid", staked))

    vaults = await _info(client, {"type": "userVaultEquities", "user": address})
    vault_equity = sum(float(v.get("equity") or 0) for v in vaults or [])
    if vault_equity > 0:
        positions.append(Position(wallet_label, "hyperliquid vaults", "usd-coin", vault_equity))

    return positions, warnings
