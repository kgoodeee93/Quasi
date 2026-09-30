"""EVM chains (Ethereum, Base, Arbitrum, Optimism, Polygon).

With an Alchemy key we read the native coin plus every ERC-20. Without one we
fall back to a public RPC and can only read the native coin.
"""
from __future__ import annotations

import httpx

from ..config import EVM_CHAINS, Settings
from . import Position, Resolver

METADATA_BATCH = 50


async def _rpc(client: httpx.AsyncClient, url: str, payload):
    r = await client.post(url, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(data["error"].get("message", str(data["error"])))
    return data


def _call(method: str, params: list, id_: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params}


async def fetch_evm(client: httpx.AsyncClient, settings: Settings, wallet_label: str,
                    address: str, chain: str, resolve: Resolver) -> tuple[list[Position], list[str]]:
    meta = EVM_CHAINS[chain]
    warnings: list[str] = []
    key = settings.alchemy_api_key
    url = f"https://{meta['alchemy']}.g.alchemy.com/v2/{key}" if key else meta["public_rpc"]

    positions: list[Position] = []
    native = await _rpc(client, url, _call("eth_getBalance", [address, "latest"]))
    native_amount = int(native["result"], 16) / 1e18
    if native_amount > 0:
        positions.append(Position(wallet_label, chain, meta["native"], native_amount))

    if not key:
        warnings.append(f"{wallet_label} ({chain}): no ALCHEMY_API_KEY, only the native coin was read")
        return positions, warnings

    # 1) every ERC-20 with a non-zero balance (paginated)
    balances: dict[str, int] = {}
    page_key = None
    while True:
        opts = {"pageKey": page_key} if page_key else {}
        res = (await _rpc(client, url, _call("alchemy_getTokenBalances", [address, "erc20", opts])))["result"]
        for tb in res.get("tokenBalances", []):
            raw = tb.get("tokenBalance") or "0x0"
            value = int(raw, 16) if raw not in ("0x", "") else 0
            if value > 0:
                balances[tb["contractAddress"].lower()] = value
        page_key = res.get("pageKey")
        if not page_key:
            break

    # 2) drop anything CoinGecko doesn't know (airdropped spam) before paying for metadata calls
    known = {c: resolve(meta["platform"], c) for c in balances}
    contracts = [c for c, coin in known.items() if coin]
    skipped = len(balances) - len(contracts)
    if skipped:
        warnings.append(f"{wallet_label} ({chain}): ignored {skipped} unlisted/spam token(s)")

    # 3) decimals, batched
    for i in range(0, len(contracts), METADATA_BATCH):
        chunk = contracts[i:i + METADATA_BATCH]
        batch = [_call("alchemy_getTokenMetadata", [c], id_=j) for j, c in enumerate(chunk)]
        results = await _rpc(client, url, batch)
        for item in results:
            contract = chunk[item["id"]]
            decimals = (item.get("result") or {}).get("decimals")
            if decimals is None:
                continue
            amount = balances[contract] / 10 ** int(decimals)
            positions.append(Position(wallet_label, chain, known[contract], amount))
    return positions, warnings
