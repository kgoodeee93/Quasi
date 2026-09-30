"""Solana: native SOL plus SPL / Token-2022 balances over plain JSON-RPC."""
from __future__ import annotations

import httpx

from ..config import Settings
from . import Position, Resolver

TOKEN_PROGRAMS = (
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # SPL Token
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",  # Token-2022
)


async def _rpc(client: httpx.AsyncClient, url: str, method: str, params: list):
    r = await client.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                          timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise RuntimeError(data["error"].get("message", str(data["error"])))
    return data["result"]


async def fetch_solana(client: httpx.AsyncClient, settings: Settings, wallet_label: str,
                       address: str, resolve: Resolver) -> tuple[list[Position], list[str]]:
    url = settings.solana_rpc_url
    positions: list[Position] = []
    warnings: list[str] = []

    lamports = (await _rpc(client, url, "getBalance", [address]))["value"]
    if lamports:
        positions.append(Position(wallet_label, "solana", "solana", lamports / 1e9))

    amounts: dict[str, float] = {}
    for program in TOKEN_PROGRAMS:
        res = await _rpc(client, url, "getTokenAccountsByOwner",
                         [address, {"programId": program}, {"encoding": "jsonParsed"}])
        for acct in res["value"]:
            info = acct["account"]["data"]["parsed"]["info"]
            ui = float(info["tokenAmount"].get("uiAmountString") or 0)
            if ui > 0:
                amounts[info["mint"]] = amounts.get(info["mint"], 0.0) + ui

    skipped = 0
    for mint, amount in amounts.items():
        coin = resolve("solana", mint)
        if coin:
            positions.append(Position(wallet_label, "solana", coin, amount))
        else:
            skipped += 1
    if skipped:
        warnings.append(f"{wallet_label} (solana): ignored {skipped} unlisted/spam token(s)")
    return positions, warnings
