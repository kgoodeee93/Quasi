import asyncio
import json
from datetime import timedelta

import httpx
import pytest

from sniper_feed import sources
from sniper_feed.notify import format_alert
from sniper_feed.rules import ACTIVITY, Rules, evaluate, passed
from sniper_feed.scan import run_scan
from sniper_feed.store import Store, now, replay_exit

GOOD = "38M2z4UCfZEkUHFzD48vhKQhFhNyRGto8pZbLjNipump"
LINKED = "Ai66LHZG9MCzg1WKdawwqduVAXpNDUuV8M3uyq5ppump"
QUIET = "4CxjZyMNoF1p6eXHXHv1TWpS2X47CuUmLwtGe18spump"
WSOL = "So11111111111111111111111111111111111111112"


def pool(mint, name, buys, sells, buyers, vol, liq=200_000.0, base=None):
    return {"id": f"solana_pool_{mint[:6]}", "type": "pool",
            "attributes": {"address": f"pool{mint[:6]}", "name": name, "base_token_price_usd": "0.002",
                           "market_cap_usd": "1950000", "fdv_usd": "1950000", "reserve_in_usd": str(liq),
                           "pool_created_at": "2026-11-23T21:11:11Z", "volume_usd": {"m5": str(vol)},
                           "transactions": {"m5": {"buys": buys, "sells": sells, "buyers": buyers, "sellers": 9}}},
            "relationships": {"base_token": {"data": {"id": f"solana_{base or mint}"}}}}


GECKO_TRENDING = {"data": [
    pool(GOOD, "<b>JIMMY</b> / SOL", 202, 107, 40, 20036.8),
    pool(GOOD, "JIMMY / SOL", 5, 1, 4, 100.0, liq=1_000.0),                # shallower pool of the same token
    pool(LINKED, "CATE / SOL", 120, 60, 50, 30000.0),
    pool(QUIET, "mihao / SOL", 18, 17, 15, 2610.0),
    pool(WSOL, "SOL / USDC", 900, 400, 300, 900000.0),                     # quote asset, not a launch
]}


def rug(networks, supply=1_000_000_000_000_000, creator=0, mint_auth=None):
    return {"token": {"supply": supply, "decimals": 6}, "creatorBalance": creator, "mintAuthority": mint_auth,
            "freezeAuthority": None, "insiderNetworks": networks, "rugged": False}


RUG = {GOOD: rug([{"currentHolding": 30_000_000_000_000, "activeAccounts": 40}]),       # 3%
       LINKED: rug([{"currentHolding": 41_000_000_000_000, "activeAccounts": 43},
                    {"currentHolding": 24_000_000_000_000, "activeAccounts": 4}])}     # 6.5%

RISK = {"status": "ok", "mint_authority_renounced": True, "freeze_authority_renounced": True,
        "snipers_pct": 0.0, "dev_hold_pct": 0.0}


class Fake:
    def __init__(self):
        self.moby_tools: list[str] = []
        self.rug_calls: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "geckoterminal" in url:
            if "token_price" in url:
                mints = url.rsplit("/", 1)[1].split(",")
                return httpx.Response(200, json={"data": {"attributes": {"token_prices": {m: "0.003" for m in mints}}}})
            return httpx.Response(200, json=GECKO_TRENDING if "trending" in url else {"data": []})
        if "rugcheck" in url:
            mint = url.split("/tokens/")[1].split("/")[0]
            self.rug_calls.append(mint)
            return httpx.Response(200, json=RUG[mint])
        msg = json.loads(req.content)
        if "id" not in msg:
            return httpx.Response(202)
        if msg["method"] == "initialize":
            result = {"capabilities": {}}
        else:
            name, args = msg["params"]["name"], msg["params"]["arguments"]
            self.moby_tools.append(name)
            if name == "get_token_overviews":
                body = {"results": [{"token_address": m, "found": True, "overview": {"overview": {
                    "top10_supply_pct": 19.0, "safety_tier": "green"}}} for m in args["token_addresses"]]}
            else:
                body = RISK
            result = {"content": [{"type": "text", "text": json.dumps(body)}], "structuredContent": body}
        return httpx.Response(200, text=f"data: {json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': result})}\n\n",
                              headers={"content-type": "text/event-stream"})


def scan(fake, rules=Rules(), with_moby=True):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as client:
            moby = sources.Moby(client, "k") if with_moby else None
            return await run_scan(client, rules, moby)
    return asyncio.run(go())


def test_rules_file_matches_defaults_and_rejects_typos(tmp_path):
    from pathlib import Path
    assert Rules.load(Path(__file__).parent.parent / "rules.json") == Rules()
    assert Rules().bundled_max_pct == 5
    bad = tmp_path / "r.json"
    bad.write_text('{"bundle_max_pct": 5}')
    with pytest.raises(ValueError, match="bundle_max_pct"):
        Rules.load(bad)


def test_missing_data_fails_the_rule():
    checks = {c["rule"]: c["ok"] for c in evaluate({"mint_disabled": True}, Rules())}
    assert checks["mint_disabled"] is True
    assert checks["freeze_disabled"] is False and checks["bundled_max_pct"] is False
    assert checks["vol_5m_min"] is False


def test_parse_pool_and_candidates_keep_the_deepest_pool_per_token():
    p = sources.parse_pool(GECKO_TRENDING["data"][0])
    assert (p["txns_5m"], p["buy_sell_ratio"], p["buyers_5m"]) == (309, 1.89, 40)
    assert sources.parse_pool(GECKO_TRENDING["data"][4]) is None
    assert sources.parse_pool(pool(GOOD, "X", 5, 0, 5, 1.0))["buy_sell_ratio"] == 999.0

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(Fake())) as c:
            return await sources.gecko_candidates(c)
    rows = {r["mint"]: r for r in asyncio.run(go())}
    assert set(rows) == {GOOD, LINKED, QUIET} and rows[GOOD]["liquidity"] == 200_000.0


def test_rugcheck_linked_wallet_share():
    r = sources.parse_rugcheck(RUG[LINKED])
    assert (r["bundled_pct"], r["linked_wallets"], r["mint_disabled"]) == (6.5, 47, True)
    assert sources.parse_rugcheck(rug(None))["bundled_pct"] is None      # no graph: unknown, not 0
    assert sources.parse_rugcheck(rug([]))["bundled_pct"] == 0
    assert sources.parse_rugcheck(rug([], mint_auth="X"))["mint_disabled"] is False


def test_scan_alerts_only_on_a_full_pass():
    fake = Fake()
    res = scan(fake)
    assert [a["token"]["mint"] for a in res.alerts] == [GOOD]
    assert [m["token"]["mint"] for m in res.near_misses] == [LINKED]  # 6.5% linked > 5% cap
    assert QUIET not in fake.rug_calls                                # failed activity: no deep lookups
    assert set(fake.moby_tools) <= sources.Moby.TOOLS
    loose = scan(Fake(), Rules(bundled_max_pct=10))
    assert {a["token"]["mint"] for a in loose.alerts} == {GOOD, LINKED}


def test_scan_without_moby_never_alerts():
    res = scan(Fake(), with_moby=False)
    assert res.alerts == [] and any("MOBY_API_KEY" in w for w in res.warnings)


def test_moby_client_refuses_trade_tools_before_sending():
    sent = []

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(500))) as c:
            m = sources.Moby(c, "k")
            for tool in ("quick_buy", "execute_swap", "create_order", "scan_tokens"):
                with pytest.raises(sources.SourceError, match="not allowed"):
                    await m.call(tool, {})
    asyncio.run(go())
    assert sent == []


def test_alert_text_escapes_token_names():
    res = scan(Fake())
    text = format_alert(res.alerts[0]["token"], res.alerts[0]["metrics"])
    assert "<b>JIMMY</b> /" not in text and "&lt;b&gt;JIMMY&lt;/b&gt;" in text
    assert "Not financial advice" in text and GOOD in text and "Bundled 3.0%" in text


def test_store_cooldown_outcome_window_and_stats(tmp_path):
    s = Store(str(tmp_path / "f.db"))
    t0 = now() - timedelta(minutes=20)
    a = s.add_signal("alert", {"mint": GOOD, "symbol": "JIMMY", "price": 0.002}, [], at=t0)
    assert s.recent(GOOD, 24, kind="alert") and not s.recent(GOOD, 24, kind="miss") and not s.recent(LINKED, 24)
    assert s.due() == [(a, GOOD, "15m", 0.002)]
    s.add_outcome(a, "15m", 0.002, 0.001)
    assert s.due() == []
    # 3 hours later the 1h mark is too stale to record; the 4h mark isn't due yet
    assert s.due(at=t0 + timedelta(hours=3)) == []
    st = s.stats()
    assert st["alerts"]["count"] == 1 and st["misses"]["count"] == 0
    assert st["alerts"]["horizons"]["15m"] == {
        "n": 1, "median_pct": -50.0, "mean_pct": -50.0, "win_rate_pct": 0.0, "best_pct": -50.0, "worst_pct": -50.0}


def c(ts, o, h, l, cl):
    return [ts, o, h, l, cl, 1.0]


def test_replay_exit_first_touch_and_conservative_ties():
    t = 1_000_000
    # a candle before the signal reaching +100% is ignored; then TP at +50% on the 2nd candle
    tp = replay_exit([c(t - 300, 1, 2, 1, 1), c(t, 1, 1.2, 0.9, 1.1), c(t + 300, 1.1, 1.6, 1.0, 1.5)], t, 1.0, 50, 25)
    assert (tp["result"], tp["minutes_to_exit"], tp["max_up_pct"], tp["candles"]) == ("tp", 5.0, 60.0, 2)
    # one candle touching both levels counts as the stop
    both = replay_exit([c(t, 1, 1.7, 0.7, 1.0)], t, 1.0, 50, 25)
    assert both["result"] == "sl" and both["minutes_to_exit"] == 0
    # neither touched: open, with the window's close return; candles past the window ignored
    op = replay_exit([c(t, 1, 1.1, 0.9, 1.05), c(t + 86400, 1, 9, 0.1, 9)], t, 1.0, 50, 25)
    assert (op["result"], op["close_return_pct"], op["max_down_pct"]) == ("open", 5.0, -10.0)
    assert replay_exit([], t, 1.0, 50, 25) is None


def test_near_misses_tracked_and_grouped_by_the_rule_they_failed(tmp_path):
    s = Store(str(tmp_path / "f.db"))
    res = scan(Fake())
    miss = res.near_misses[0]
    assert [ch["rule"] for ch in miss["checks"] if not ch["ok"]] == ["bundled_max_pct"]
    t0 = now() - timedelta(hours=30)
    a = s.add_signal("alert", res.alerts[0]["token"], res.alerts[0]["checks"], at=t0)
    m = s.add_signal("miss", miss["token"], miss["checks"], at=t0)
    s.add_outcome(a, "1h", 0.002, 0.001)
    s.add_outcome(m, "1h", 0.002, 0.004)
    assert [r["id"] for r in s.exits_due()] == [a, m]  # alerts replayed first
    s.add_exit(a, 50, 25, {"result": "sl", "minutes_to_exit": 10, "close_return_pct": None,
                           "max_up_pct": 5, "max_down_pct": -40, "candles": 288})
    s.add_exit(m, 50, 25, {"result": "tp", "minutes_to_exit": 30, "close_return_pct": None,
                           "max_up_pct": 80, "max_down_pct": -5, "candles": 288})
    assert s.exits_due() == []
    st = s.stats()
    assert st["alerts"]["exits"]["avg_trade_pct"] == -25 and st["misses"]["exits"]["hit_tp_pct"] == 100
    only = st["only_failed"]["bundled_max_pct"]
    assert only["count"] == 1 and only["1h"]["median_pct"] == 100.0 and only["exits"]["avg_trade_pct"] == 50
    s.backup(str(tmp_path / "copy.db"))
    assert Store(str(tmp_path / "copy.db")).stats() == st


def test_hard_blocks_turn_a_full_pass_into_a_miss():
    from sniper_feed.scan import hard_blocks
    fails = [b["rule"] for b in hard_blocks({"safety_tier": "red", "rugged": True, "asset_class": "tokenized_stock",
                                             "sells_5m": 0}) if not b["ok"]]
    assert fails == ["not_red_tier", "not_rugged", "not_tokenized_stock", "has_sells_5m"]
    assert all(b["ok"] for b in hard_blocks({"safety_tier": "grey", "sells_5m": 3}))


def test_all_buys_no_sells_is_held_back_as_a_possible_honeypot():
    fake = Fake()
    saved = GECKO_TRENDING["data"][0]
    GECKO_TRENDING["data"][0] = pool(GOOD, "JIMMY / SOL", 202, 0, 40, 20036.8)
    try:
        res = scan(fake)
    finally:
        GECKO_TRENDING["data"][0] = saved
    assert res.alerts == []
    jimmy = next(m for m in res.near_misses if m["token"]["mint"] == GOOD)
    assert [c["rule"] for c in jimmy["checks"] if not c["ok"]] == ["has_sells_5m"]


def test_gecko_candles_parses_and_rejects_bad_pools():
    body = {"data": {"attributes": {"ohlcv_list": [[1700000300, "1", "2", "0.5", "1.5", "10"], ["junk"]]}}}
    seen = []

    def handler(req):
        seen.append(req.url)
        return httpx.Response(200, json=body)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as cl:
            rows = await sources.gecko_candles(cl, "FnHWfV49ywCtGCDTyf34AVN8BiXxcgXUKqLctkrkP1gQ", 1700000000, 86400)
            with pytest.raises(sources.SourceError):
                await sources.gecko_candles(cl, "../../etc", 0, 300)
            return rows
    assert asyncio.run(go()) == [[1700000300.0, 1.0, 2.0, 0.5, 1.5, 10.0]]
    assert seen[0].params["aggregate"] == "5" and seen[0].params["limit"] == "290" and len(seen) == 1


def test_activity_rules_alone():
    p = sources.parse_pool(GECKO_TRENDING["data"][3])
    assert not passed(evaluate(p, Rules(), ACTIVITY))


def test_gecko_retries_429_and_uses_the_paid_api_when_keyed(monkeypatch):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(429) if len(calls) == 1 else httpx.Response(200, json={"ok": 1})

    async def go(**kw):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as cl:
            return await sources.gecko_get(cl, "/networks/solana/new_pools?page=1", backoff=0, **kw)
    monkeypatch.delenv("COINGECKO_API_KEY", raising=False)
    assert asyncio.run(go()) == {"ok": 1} and len(calls) == 2
    assert str(calls[0].url).startswith("https://api.geckoterminal.com/api/v2/")
    monkeypatch.setenv("COINGECKO_API_KEY", "cg-key")
    calls.clear()
    calls.append(None)  # skip the 429 this time
    asyncio.run(go())
    assert str(calls[1].url).startswith("https://pro-api.coingecko.com/api/v3/onchain/networks/solana/new_pools")
    assert calls[1].headers["x-cg-pro-api-key"] == "cg-key"
    calls.clear()
    handler_429 = lambda req: httpx.Response(429)

    async def always_429():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler_429)) as cl:
            await sources.gecko_get(cl, "/x", retries=1, backoff=0)
    with pytest.raises(sources.SourceError, match="rate limit"):
        asyncio.run(always_429())
