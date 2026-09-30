"""Fetch every wallet in parallel, price everything, build the dashboard payload."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import httpx

from .config import PortfolioConfig, Settings, Wallet
from .portfolio import build_dashboard
from .providers import Position
from .providers.bitcoin import fetch_bitcoin
from .providers.evm import fetch_evm
from .providers.prices import CoinGecko
from .providers.solana import fetch_solana


def _wallet_jobs(client, settings: Settings, wallet: Wallet, cg: CoinGecko):
    """One coroutine per (address, chain) the wallet covers, labelled for error messages."""
    if wallet.type == "manual":
        return []
    jobs = []
    for addr in wallet.addresses:
        short = f"{addr[:6]}…{addr[-4:]}"
        if wallet.type == "evm":
            for chain in wallet.chains:
                jobs.append((f"{wallet.label} {chain} {short}",
                             fetch_evm(client, settings, wallet.label, addr, chain, cg.resolve)))
        elif wallet.type == "solana":
            jobs.append((f"{wallet.label} solana {short}",
                         fetch_solana(client, settings, wallet.label, addr, cg.resolve)))
        elif wallet.type == "bitcoin":
            jobs.append((f"{wallet.label} bitcoin {short}",
                         fetch_bitcoin(client, settings, wallet.label, addr)))
    return jobs


async def collect(config: PortfolioConfig, settings: Settings,
                  client: httpx.AsyncClient | None = None) -> dict:
    own_client = client is None
    client = client or httpx.AsyncClient(headers={"user-agent": "quasi-crypto-dashboard/1.0"})
    try:
        cg = CoinGecko(settings, client)
        warnings: list[str] = []
        positions: list[Position] = []

        if any(w.type in ("evm", "solana") for w in config.wallets):
            await cg.load_contract_map()

        for w in config.wallets:
            if w.type == "manual":
                positions += [Position(w.label, h["chain"] or w.display_chain or "manual",
                                       h["coin"], h["amount"])
                              for h in w.holdings]

        jobs = [job for w in config.wallets for job in _wallet_jobs(client, settings, w, cg)]
        results = await asyncio.gather(*(coro for _, coro in jobs), return_exceptions=True)
        for (name, _), res in zip(jobs, results):
            if isinstance(res, Exception):
                warnings.append(f"{name}: fetch failed ({type(res).__name__}: {res})")
            else:
                positions += res[0]
                warnings += res[1]

        markets = await cg.markets([p.coin_id for p in positions]) if positions else {}
        unpriced = sorted({p.coin_id for p in positions} - markets.keys())
        if unpriced:
            warnings.append(f"No CoinGecko price for: {', '.join(unpriced)} (check the coin id)")

        data = build_dashboard(positions, markets, config.cost_basis, [w.label for w in config.wallets])
        data.update({
            "currency": settings.vs_currency,
            "demo": config.demo,
            "warnings": warnings,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        })
        return data
    finally:
        if own_client:
            await client.aclose()


class PortfolioCache:
    """Keeps one computed payload for `ttl` seconds so page refreshes don't burn API quota."""

    def __init__(self, ttl: int):
        self.ttl = ttl
        self._data: dict | None = None
        self._at = 0.0
        self._lock = asyncio.Lock()

    async def get(self, loader, force: bool = False) -> dict:
        async with self._lock:
            if force or self._data is None or time.time() - self._at > self.ttl:
                self._data = await loader()
                self._at = time.time()
            return self._data
