"""Moby (https://moby.win): Solana whale / KOL / smart-money flow over its MCP HTTP endpoint.

Moby's MCP server also has tools that trade real money from the user's Moby agent wallet. This
client can only call the read-only tools in READ_TOOLS: anything else raises before a request is
sent, so the dashboard can never place, cancel or price a trade.
"""
from __future__ import annotations

import json

import httpx

READ_TOOLS = frozenset({"get_smart_money_pulse", "get_token_overviews", "get_token_smart_money"})
PROTOCOL_VERSION = "2025-06-18"


class MobyError(RuntimeError):
    pass


class Moby:
    def __init__(self, client: httpx.AsyncClient, url: str, api_key: str, timeout: float = 20):
        self.client = client
        self.url = url
        self.timeout = timeout
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        self._next_id = 0
        self._ready = False

    async def _rpc(self, method: str, params: dict | None = None, notify: bool = False) -> dict:
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self._next_id += 1
            msg["id"] = self._next_id
        r = await self.client.post(self.url, json=msg, headers=self.headers, timeout=self.timeout)
        if r.status_code in (401, 403):
            raise MobyError(f"Moby rejected the API key (HTTP {r.status_code}); check MOBY_API_KEY")
        r.raise_for_status()
        if session := r.headers.get("mcp-session-id"):
            self.headers["Mcp-Session-Id"] = session
        if notify:
            return {}
        return _response(r, msg["id"])

    async def _init(self) -> None:
        if self._ready:
            return
        await self._rpc("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                       "clientInfo": {"name": "quasi-crypto-dashboard", "version": "1.0"}})
        await self._rpc("notifications/initialized", notify=True)
        self._ready = True

    async def call(self, tool: str, arguments: dict | None = None) -> dict:
        if tool not in READ_TOOLS:
            raise MobyError(f"{tool} is not a read-only Moby tool; the dashboard never calls it")
        await self._init()
        res = await self._rpc("tools/call", {"name": tool, "arguments": arguments or {}})
        if "error" in res:
            raise MobyError(f"{tool}: {res['error'].get('message', res['error'])}")
        result = res.get("result") or {}
        text = " ".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        if result.get("isError"):
            raise MobyError(f"{tool}: {text[:200] or 'error'}")
        if isinstance(result.get("structuredContent"), dict):
            return result["structuredContent"]
        try:
            return json.loads(text)
        except ValueError as e:
            raise MobyError(f"{tool}: unexpected response") from e


def _response(r: httpx.Response, msg_id: int) -> dict:
    """A JSON-RPC response arrives either as plain JSON or as server-sent events."""
    if "text/event-stream" not in r.headers.get("content-type", ""):
        return r.json()
    for line in r.text.splitlines():
        if line.startswith("data:"):
            try:
                msg = json.loads(line[5:])
            except ValueError:
                continue
            if msg.get("id") == msg_id:
                return msg
    raise MobyError("Moby sent no response to the request")
