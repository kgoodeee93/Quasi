"""Bitcoin: confirmed + mempool balance per address from a mempool.space-compatible API."""
from __future__ import annotations

import httpx

from ..config import Settings
from . import Position


async def fetch_bitcoin(client: httpx.AsyncClient, settings: Settings, wallet_label: str,
                        address: str) -> tuple[list[Position], list[str]]:
    r = await client.get(f"{settings.bitcoin_api_url}/address/{address}", timeout=30)
    r.raise_for_status()
    data = r.json()
    sats = 0
    for stats in (data.get("chain_stats", {}), data.get("mempool_stats", {})):
        sats += stats.get("funded_txo_sum", 0) - stats.get("spent_txo_sum", 0)
    positions = [Position(wallet_label, "bitcoin", "bitcoin", sats / 1e8)] if sats > 0 else []
    return positions, []
