"""Smart-money panel: Moby MCP client (read-only), payload shaping, and the API endpoint."""
import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.providers.moby import READ_TOOLS, Moby, MobyError
from app.smart_money import collect_smart_money, tracked_tokens

WSOL = "So11111111111111111111111111111111111111112"
JUP = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
HOT = "9hjBLBrc7WZW2H8UQvBAvG5WiTZotXUv2Bdcn4f4pump"

PORTFOLIO = {
    "solana_tokens": {"solana": WSOL, "jupiter-exchange-solana": JUP, "bonk": BONK, "usd-coin": USDC},
    "views": {"all": {"holdings": [  # sorted by value, as build_view returns them
        {"id": "solana", "symbol": "SOL", "name": "Solana", "value": 900.0, "is_stable": False},
        {"id": "usd-coin", "symbol": "USDC", "name": "USDC", "value": 500.0, "is_stable": True},
        {"id": "jupiter-exchange-solana", "symbol": "JUP", "name": "Jupiter", "value": 80.0, "is_stable": False},
        {"id": "bonk", "symbol": "BONK", "name": "Bonk", "value": 20.0, "is_stable": False},
        {"id": "bitcoin", "symbol": "BTC", "name": "Bitcoin", "value": 5000.0, "is_stable": False},
    ]}},
}


def window(net, buys=2, sells=1, last="2026-11-18T19:03:22"):
    return {"net_flow_usd": net, "buy_volume_usd": max(net, 0) + 10, "sell_volume_usd": max(-net, 0) + 10,
            "buy_count": buys, "sell_count": sells, "wallets_in": buys, "supply_pct": None,
            "tokens_in": 1.0, "tokens_out": 1.0, "last_buy_at": last, "last_sell_at": None}


def slice_(net):
    return {"trades": 9, "buys": 5, "sells": 4, "buy_volume_usd": 10.0, "sell_volume_usd": 5.0,
            "net_flow_usd": net, "buyers": 3, "sellers": 2, "dump_sells": 1, "dump_wallets": 1, "first_buys": 0}


PULSE_WINDOW = {
    "totals": slice_(-100.0),
    "by_wallet_type": {"whale": slice_(-250.0), "kol": slice_(150.0), "smart_money": slice_(0.0)},
    "fresh_launch": {},
    "top_net_buy": [{"token_address": HOT, "symbol": "DCTY", "net_flow_usd": 70872.39, "buy_volume_usd": 1.0,
                     "sell_volume_usd": 0.0, "buyers": 23, "sellers": 8},
                    {"token_address": "not a mint<script>", "symbol": "BAD", "net_flow_usd": 1.0},
                    {"token_address": BONK, "symbol": "BONK", "net_flow_usd": 5.0, "buyers": "<img onerror=x>",
                     "sellers": True}],
    "top_net_sell": [{"token_address": JUP, "symbol": "JUP", "net_flow_usd": -19900.0, "buy_volume_usd": 0.0,
                      "sell_volume_usd": 19900.0, "buyers": 0, "sellers": 2}],
}
TOOL_RESULTS = {
    "get_smart_money_pulse": {"network": "solana", "data_as_of": "2026-11-18T19:03:44.738750",
                              "min_trade_usd": 100.0, "h1": PULSE_WINDOW, "h4": PULSE_WINDOW, "h24": PULSE_WINDOW},
    "get_token_overviews": {"count": 2, "found_count": 2, "results": [
        {"token_address": JUP, "found": True, "overview": {"overview": {
            "symbol": "JUP", "safety_tier": "green", "safety_note": "Green: verified.",
            "smart_money_read": {"tier": "holding", "label": "Holding", "text": "Smart money is holding.",
                                 "computed_at": "x"}}, "data_as_of": "x", "data_notes": []}},
        {"token_address": BONK, "found": False, "error": "not found"},
    ]},
}


class FakeMoby:
    """MCP over streamable HTTP, answering in server-sent events like mcp.moby.win does."""

    def __init__(self, fail_tokens=()):
        self.calls: list[tuple[str, dict]] = []
        self.auth: set[str] = set()
        self.fail_tokens = set(fail_tokens)

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.auth.add(req.headers.get("authorization", ""))
        msg = json.loads(req.content)
        if "id" not in msg:  # notification
            return httpx.Response(202)
        if msg["method"] == "initialize":
            result = {"capabilities": {"tools": {}}, "serverInfo": {"name": "moby"}}
        else:
            name, args = msg["params"]["name"], msg["params"]["arguments"]
            self.calls.append((name, args))
            if name == "get_token_smart_money":
                if args["token_address"] in self.fail_tokens:
                    result = {"content": [{"type": "text", "text": "rate limited"}], "isError": True}
                else:
                    net = {JUP: -12036.9, BONK: 6768.89}[args["token_address"]]
                    body = {"flow": {"h1": window(net / 10), "h4": window(net / 2), "h24": window(net)},
                            "data_as_of": None}
                    result = {"content": [{"type": "text", "text": json.dumps(body)}], "structuredContent": body}
            else:
                body = TOOL_RESULTS[name]
                result = {"content": [{"type": "text", "text": json.dumps(body)}], "structuredContent": body}
        sse = f"event: message\ndata: {json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': result})}\n\n"
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})


def run(fake: FakeMoby, portfolio=PORTFOLIO) -> dict:
    settings = Settings(moby_api_key="test-key")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as client:
            return await collect_smart_money(portfolio, settings, client)
    return asyncio.run(go())


def test_tracked_tokens_skip_sol_stables_and_non_solana():
    tokens, skipped = tracked_tokens(PORTFOLIO)
    assert [t["symbol"] for t in tokens] == ["JUP", "BONK"]
    assert skipped == ["SOL", "USDC"]


def test_smart_money_payload_from_moby():
    fake = FakeMoby()
    d = run(fake)
    assert fake.auth == {"Bearer test-key"}
    assert {name for name, _ in fake.calls} <= READ_TOOLS
    jup, bonk = d["holdings"]
    assert (jup["symbol"], jup["safety_tier"], jup["read"]["label"]) == ("JUP", "green", "Holding")
    assert jup["flow"]["h24"]["net_flow_usd"] == pytest.approx(-12036.9)
    assert jup["flow"]["h1"]["last_buy_at"] == "2026-11-18T19:03:22+00:00"  # Moby's naive UTC, made explicit
    assert bonk["read"] is None and bonk["flow"]["h4"]["net_flow_usd"] == pytest.approx(3384.445)
    assert d["skipped"] == ["SOL", "USDC"] and d["warnings"] == []
    assert d["as_of"] == "2026-11-18T19:03:44.738750+00:00"
    p = d["pulse"]["h24"]
    assert (p["whale"]["net_flow_usd"], p["kol"]["net_flow_usd"]) == (-250.0, 150.0)
    assert [t["symbol"] for t in p["top_net_buy"]] == ["DCTY", "BONK"]  # malformed address dropped
    assert (p["top_net_buy"][1]["buyers"], p["top_net_buy"][1]["sellers"]) == (None, None)  # non-numbers
    assert p["top_net_buy"][0]["held"] is False and p["top_net_sell"][0]["held"] is True


def test_one_failing_token_degrades_to_a_warning():
    d = run(FakeMoby(fail_tokens={BONK}))
    jup, bonk = d["holdings"]
    assert jup["flow"] is not None and bonk["flow"] is None
    assert any("BONK" in w and "rate limited" in w for w in d["warnings"])


def test_no_solana_tokens_still_returns_the_market():
    fake = FakeMoby()
    d = run(fake, {"solana_tokens": {}, "views": {"all": {"holdings": []}}})
    assert d["holdings"] == [] and d["pulse"]["h1"]["top_net_buy"]
    assert [name for name, _ in fake.calls] == ["get_smart_money_pulse"]


def test_client_refuses_trade_tools_before_sending_anything():
    sent = []

    async def go():
        transport = httpx.MockTransport(lambda req: sent.append(req) or httpx.Response(500))
        async with httpx.AsyncClient(transport=transport) as client:
            moby = Moby(client, "https://mcp.moby.win/mcp", "k")
            for tool in ("quick_buy", "quick_sell", "execute_swap", "create_order", "cancel_order",
                         "get_swap_quote", "save_trading_profile"):
                with pytest.raises(MobyError, match="not a read-only"):
                    await moby.call(tool, {})
    asyncio.run(go())
    assert sent == []


def test_endpoint_off_without_key_and_survives_moby_errors(monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as main
    client = TestClient(main.app)
    monkeypatch.setattr(main.settings, "moby_api_key", "")
    assert client.get("/api/smart-money").json() == {"enabled": False}

    async def fake_portfolio(refresh: bool = False):
        return PORTFOLIO

    async def down(*a, **kw):
        raise MobyError("Moby rejected the API key (HTTP 401); check MOBY_API_KEY")

    monkeypatch.setattr(main.settings, "moby_api_key", "k")
    monkeypatch.setattr(main, "portfolio", fake_portfolio)
    monkeypatch.setattr(main, "collect_smart_money", down)
    body = client.get("/api/smart-money?refresh=true").json()
    assert body["enabled"] is True and "401" in body["error"]
