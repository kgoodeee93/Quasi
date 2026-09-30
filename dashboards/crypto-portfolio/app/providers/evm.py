"""EVM chains (Ethereum, Base, Arbitrum, Optimism, Polygon, Robinhood Chain, Arc, HyperEVM).

Native coin: always read over RPC (Alchemy if keyed and supported, else a public RPC).
ERC-20s, in order of preference:
  1. Alchemy (ALCHEMY_API_KEY set and the chain is on Alchemy)
  2. Blockscout explorer API (keyless) where the chain has one
  3. Multicall3 balanceOf over every token CoinGecko lists on the chain (keyless, any RPC)
"""
from __future__ import annotations

import httpx

from ..config import EVM_CHAINS, Settings
from typing import Callable

from . import Position, Resolver

METADATA_BATCH = 50
MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"  # same address on nearly every EVM chain
MULTICALL_BATCH = 250
BALANCE_OF = "70a08231"
DECIMALS = "313ce567"


async def _rpc(client: httpx.AsyncClient, url: str, payload):
    r = await client.post(url, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(data["error"].get("message", str(data["error"])))
    return data


def _call(method: str, params: list, id_: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params}


async def _alchemy_tokens(client, url: str, address: str) -> dict[str, tuple[int, int | None]]:
    """contract -> (raw balance, decimals-or-None). Decimals are fetched later, only for known tokens."""
    balances: dict[str, tuple[int, int | None]] = {}
    page_key = None
    while True:
        opts = {"pageKey": page_key} if page_key else {}
        res = (await _rpc(client, url, _call("alchemy_getTokenBalances", [address, "erc20", opts])))["result"]
        for tb in res.get("tokenBalances", []):
            raw = tb.get("tokenBalance") or "0x0"
            value = int(raw, 16) if raw not in ("0x", "") else 0
            if value > 0:
                balances[tb["contractAddress"].lower()] = (value, None)
        page_key = res.get("pageKey")
        if not page_key:
            return balances


async def _alchemy_decimals(client, url: str, contracts: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in range(0, len(contracts), METADATA_BATCH):
        chunk = contracts[i:i + METADATA_BATCH]
        batch = [_call("alchemy_getTokenMetadata", [c], id_=j) for j, c in enumerate(chunk)]
        for item in await _rpc(client, url, batch):
            decimals = (item.get("result") or {}).get("decimals")
            if decimals is not None:
                out[chunk[item["id"]]] = int(decimals)
    return out


async def _blockscout_tokens(client, host: str, address: str) -> dict[str, tuple[int, int | None]]:
    r = await client.get(f"https://{host}/api/v2/addresses/{address}/token-balances", timeout=30)
    r.raise_for_status()
    balances: dict[str, tuple[int, int | None]] = {}
    for item in r.json():
        token = item.get("token") or {}
        if token.get("type") != "ERC-20" or not item.get("value"):
            continue
        decimals = token.get("decimals")
        value = int(item["value"])
        if value > 0:
            balances[token["address_hash"].lower()] = (value, int(decimals) if decimals else None)
    return balances


def _word(hexstr: str) -> str:
    return hexstr.rjust(64, "0")


def _encode_aggregate3(targets: list[str], calldata: str) -> str:
    """ABI-encode Multicall3.aggregate3((address,bool,bytes)[]) with the same calldata for every target."""
    n = len(targets)
    data_len = len(calldata) // 2
    data_padded = calldata.ljust(((data_len + 31) // 32) * 64, "0")
    tuple_size = 32 * 4 + len(data_padded) // 2  # target, allowFailure, bytes offset, bytes length, bytes
    head = [_word("20"), _word(format(n, "x"))]
    head += [_word(format(n * 32 + i * tuple_size, "x")) for i in range(n)]
    body = [_word(t[2:].lower()) + _word("1") + _word("60") + _word(format(data_len, "x")) + data_padded
            for t in targets]
    return "0x82ad56cb" + "".join(head) + "".join(body)


def _decode_aggregate3(result: str) -> list[int | None]:
    """Decode (bool success, bytes returnData)[] into the first 32-byte word of each return, or None."""
    data = bytes.fromhex(result[2:])
    word = lambda off: int.from_bytes(data[off:off + 32], "big")  # noqa: E731
    start = word(0)
    n = word(start)
    out: list[int | None] = []
    for i in range(n):
        t = start + 32 + word(start + 32 + i * 32)
        ok, blen = word(t), word(t + word(t + 32))
        out.append(word(t + word(t + 32) + 32) if ok and blen >= 32 else None)
    return out


async def _multicall(client, url: str, targets: list[str], calldata: str) -> list[int | None]:
    out: list[int | None] = []
    for i in range(0, len(targets), MULTICALL_BATCH):
        chunk = targets[i:i + MULTICALL_BATCH]
        res = await _rpc(client, url, _call("eth_call", [{"to": MULTICALL3, "data": _encode_aggregate3(chunk, calldata)}, "latest"]))
        out += _decode_aggregate3(res["result"])
    return out


async def _multicall_tokens(client, url: str, address: str, contracts: list[str]) -> dict[str, tuple[int, int | None]]:
    evm = [c for c in contracts if c.startswith("0x") and len(c) == 42]
    balances = await _multicall(client, url, evm, BALANCE_OF + _word(address[2:].lower()))
    held = [(c, b) for c, b in zip(evm, balances) if b]
    decimals = await _multicall(client, url, [c for c, _ in held], DECIMALS) if held else []
    return {c: (b, d) for (c, b), d in zip(held, decimals) if d is not None and d <= 36}


async def fetch_evm(client: httpx.AsyncClient, settings: Settings, wallet_label: str,
                    address: str, chain: str, resolve: Resolver,
                    listed: Callable[[str], list[str]] | None = None) -> tuple[list[Position], list[str]]:
    meta = EVM_CHAINS[chain]
    warnings: list[str] = []
    use_alchemy = bool(settings.alchemy_api_key and meta["alchemy"])
    url = (f"https://{meta['alchemy']}.g.alchemy.com/v2/{settings.alchemy_api_key}"
           if use_alchemy else meta["public_rpc"])

    positions: list[Position] = []
    native = await _rpc(client, url, _call("eth_getBalance", [address, "latest"]))
    native_amount = int(native["result"], 16) / 1e18
    if native_amount > 0:
        positions.append(Position(wallet_label, chain, meta["native"], native_amount))

    if use_alchemy:
        balances = await _alchemy_tokens(client, url, address)
    elif meta["blockscout"]:
        try:
            balances = await _blockscout_tokens(client, meta["blockscout"], address)
        except (httpx.HTTPError, ValueError) as e:
            if not listed:
                raise
            warnings.append(f"{wallet_label} ({chain}): Blockscout unavailable ({type(e).__name__}), used RPC scan")
            balances = await _multicall_tokens(client, url, address, listed(meta["platform"]))
    elif listed:
        # Only tokens CoinGecko lists are checked, so there is no spam to filter here.
        balances = await _multicall_tokens(client, url, address, listed(meta["platform"]))
    else:
        warnings.append(f"{wallet_label} ({chain}): no token source, only the native coin was read")
        return positions, warnings

    # Drop anything CoinGecko doesn't know (airdropped spam) before any metadata calls.
    # A token that prices as the gas coin (e.g. Arc's USDC ERC-20 interface) mirrors the
    # native balance already counted above.
    known = {c: coin if (coin := resolve(meta["platform"], c)) != meta["native"] else None for c in balances}
    contracts = [c for c, coin in known.items() if coin]
    skipped = len(balances) - len(contracts)
    if skipped:
        warnings.append(f"{wallet_label} ({chain}): ignored {skipped} unlisted/spam token(s)")

    missing = [c for c in contracts if balances[c][1] is None]
    decimals = await _alchemy_decimals(client, url, missing) if (missing and use_alchemy) else {}
    for c in contracts:
        raw, dec = balances[c]
        dec = dec if dec is not None else decimals.get(c)
        if dec is not None:
            positions.append(Position(wallet_label, chain, known[c], raw / 10 ** dec))
    return positions, warnings
