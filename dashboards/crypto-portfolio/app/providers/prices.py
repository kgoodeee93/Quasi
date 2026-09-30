"""CoinGecko: contract -> coin id mapping, and market data for every coin we hold."""
from __future__ import annotations

import asyncio
import json
import time
import httpx

from ..config import Settings

COINS_LIST_TTL = 24 * 3600
MARKETS_PAGE = 50  # ids per /coins/markets request; more overflows the URL length limit


class CoinGecko:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client
        self.cache_dir = settings.cache_dir
        pro = settings.coingecko_plan == "pro" and settings.coingecko_api_key
        self.base = "https://pro-api.coingecko.com/api/v3" if pro else "https://api.coingecko.com/api/v3"
        self.headers = {}
        if settings.coingecko_api_key:
            header = "x-cg-pro-api-key" if pro else "x-cg-demo-api-key"
            self.headers[header] = settings.coingecko_api_key
        self._contracts: dict[tuple[str, str], str] | None = None

    async def _get(self, path: str, params: dict | None = None, retries: int = 3):
        for attempt in range(retries + 1):
            r = await self.client.get(f"{self.base}{path}", params=params, headers=self.headers, timeout=30)
            if r.status_code != 429 or attempt == retries:
                r.raise_for_status()
                return r.json()
            # Rate limited (common without an API key): honor Retry-After, else back off 5s, 10s, 20s.
            wait = float(r.headers.get("retry-after") or 5 * 2 ** attempt)
            await asyncio.sleep(min(wait, 60))

    async def load_contract_map(self) -> None:
        """Build (platform, contract) -> coin id from /coins/list, cached on disk for a day.

        This one call is what lets us ignore airdropped spam tokens: anything
        CoinGecko doesn't list has no price and gets dropped.
        """
        if self._contracts is not None:
            return
        cache = self.cache_dir / "coins_list.json"
        coins = None
        if cache.exists() and time.time() - cache.stat().st_mtime < COINS_LIST_TTL:
            coins = json.loads(cache.read_text())
        if coins is None:
            coins = await self._get("/coins/list", {"include_platform": "true"})
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(coins))
        mapping: dict[tuple[str, str], str] = {}
        for coin in coins:
            for platform, address in (coin.get("platforms") or {}).items():
                if platform and address:
                    mapping.setdefault((platform, normalize_address(platform, address)), coin["id"])
        self._contracts = mapping

    def resolve(self, platform: str, contract: str) -> str | None:
        assert self._contracts is not None, "call load_contract_map() first"
        return self._contracts.get((platform, normalize_address(platform, contract)))

    async def markets(self, coin_ids: list[str]) -> dict[str, dict]:
        """Price, market cap, volume, % changes, ATH, supply and 7d sparkline per coin."""
        out: dict[str, dict] = {}
        ids = sorted(set(coin_ids))
        for i in range(0, len(ids), MARKETS_PAGE):
            chunk = ids[i:i + MARKETS_PAGE]
            rows = await self._get("/coins/markets", {
                "vs_currency": self.settings.vs_currency,
                "ids": ",".join(chunk),
                "per_page": MARKETS_PAGE,
                "sparkline": "true",
                "price_change_percentage": "1h,24h,7d,30d,1y",
            })
            for row in rows:
                out[row["id"]] = row
        return out


def normalize_address(platform: str, address: str) -> str:
    # EVM addresses are case-insensitive; Solana (base58) is case-sensitive.
    return address.strip() if platform == "solana" else address.strip().lower()
