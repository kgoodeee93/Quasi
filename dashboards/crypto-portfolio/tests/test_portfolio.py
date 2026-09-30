import asyncio
import json

import httpx
import pytest

from app.config import ConfigError, Settings, parse_config
from app.portfolio import build_dashboard, pnl_from_pct
from app.providers import Position
from app.service import collect


def market(coin, price, ch24, ch7=0.0, mcap=1e9, rank=50, spark=None):
    return {
        "id": coin, "symbol": coin[:3], "name": coin.title(), "current_price": price,
        "market_cap": mcap, "market_cap_rank": rank, "fully_diluted_valuation": mcap * 2,
        "total_volume": mcap / 10, "high_24h": price * 1.1, "low_24h": price * 0.9,
        "price_change_percentage_1h_in_currency": 0.0,
        "price_change_percentage_24h_in_currency": ch24,
        "price_change_percentage_7d_in_currency": ch7,
        "price_change_percentage_30d_in_currency": 0.0,
        "circulating_supply": 50.0, "max_supply": 100.0,
        "sparkline_in_7d": {"price": spark or [price] * 168},
        "last_updated": "2026-09-30T12:00:00.000Z",
    }


MARKETS = {
    "bitcoin": market("bitcoin", 100.0, 25.0, rank=1),     # was 80 -> +20 per coin
    "solana": market("solana", 10.0, -50.0, rank=7),       # was 20 -> -10 per coin
    "usd-coin": market("usd-coin", 1.0, 0.0, rank=6),
}
POSITIONS = [
    Position("Ledger", "bitcoin", "bitcoin", 1.0),
    Position("Hot", "exchange", "bitcoin", 1.0),
    Position("Hot", "solana", "solana", 10.0),
    Position("Hot", "ethereum", "usd-coin", 100.0),
]


def test_pnl_from_pct():
    assert pnl_from_pct(125, 25) == pytest.approx(25)   # 100 -> 125
    assert pnl_from_pct(50, -50) == pytest.approx(-50)  # 100 -> 50
    assert pnl_from_pct(100, None) == 0
    assert pnl_from_pct(100, -100) == 0


def test_combined_view_totals_and_pnl():
    d = build_dashboard(POSITIONS, MARKETS, {"bitcoin": 150.0}, ["Ledger", "Hot"])
    s = d["views"]["all"]["summary"]
    assert s["total_value"] == pytest.approx(400)       # 200 BTC + 100 SOL + 100 USDC
    assert s["pnl_24h"] == pytest.approx(40 - 100)      # BTC +40, SOL -100
    assert s["pnl_24h_pct"] == pytest.approx(-60 / 460 * 100)
    assert s["stable_share"] == pytest.approx(25)
    assert s["unrealized_pnl"] == pytest.approx(50)     # BTC 200 vs 150 cost
    assert s["top_gainer"]["symbol"] == "BIT" and s["top_loser"]["symbol"] == "SOL"
    assert s["wallets"] == 2 and s["chains"] == 4
    assert s["hhi"] == pytest.approx((0.5**2 + 0.25**2 + 0.25**2) * 10_000)

    btc = next(r for r in d["views"]["all"]["holdings"] if r["id"] == "bitcoin")
    assert btc["amount"] == 2 and btc["allocation"] == pytest.approx(50)
    assert {w["wallet"] for w in btc["wallets"]} == {"Ledger", "Hot"}
    assert btc["circulating_pct"] == pytest.approx(50)
    assert btc["range_24h_position"] == pytest.approx(0.5)


def test_wallet_view_prorates_cost_basis():
    d = build_dashboard(POSITIONS, MARKETS, {"bitcoin": 150.0}, ["Ledger", "Hot"])
    ledger = d["views"]["Ledger"]
    assert ledger["summary"]["total_value"] == pytest.approx(100)
    assert ledger["holdings"][0]["cost_basis"] == pytest.approx(75)
    by_wallet = {w["name"]: w for w in d["views"]["all"]["by_wallet"]}
    assert by_wallet["Hot"]["value"] == pytest.approx(300)


def test_history_uses_current_amounts():
    markets = {**MARKETS, "bitcoin": market("bitcoin", 100.0, 0.0, spark=[50.0] * 167 + [100.0])}
    d = build_dashboard([Position("L", "bitcoin", "bitcoin", 2.0)], markets, {}, ["L"])
    hist = d["views"]["all"]["history"]
    assert hist[0]["v"] == pytest.approx(100) and hist[-1]["v"] == pytest.approx(200)
    assert d["views"]["all"]["summary"]["drawdown_7d"] == pytest.approx(0)


def test_unpriced_positions_are_dropped():
    d = build_dashboard([Position("L", "x", "not-a-coin", 5.0)], MARKETS, {}, ["L"])
    assert d["views"]["all"]["holdings"] == []


def test_config_validation():
    with pytest.raises(ConfigError):
        parse_config({"wallets": [{"label": "a", "type": "evm", "address": "0x1", "chains": ["fantom"]}]})
    with pytest.raises(ConfigError):
        parse_config({"wallets": [{"label": "a", "type": "solana"}]})
    with pytest.raises(ConfigError):
        parse_config({"wallets": [{"label": "a", "type": "manual"}, {"label": "a", "type": "manual"}]})
    cfg = parse_config({"wallets": [{"label": "x", "type": "evm", "address": "0xA"}],
                        "cost_basis": {"_comment": "ignored", "Bitcoin": 10}})
    assert cfg.wallets[0].chains == ["ethereum"] and cfg.cost_basis == {"bitcoin": 10.0}


def test_collect_end_to_end_with_mocked_apis(tmp_path):
    """Every provider path, with HTTP mocked: EVM (Alchemy), Solana, Bitcoin, manual."""
    usdc_eth = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    usdc_sol = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "coins/list" in url:
            return httpx.Response(200, json=[
                {"id": "usd-coin", "platforms": {"ethereum": usdc_eth, "solana": usdc_sol}}])
        if "coins/markets" in url:
            ids = req.url.params["ids"].split(",")
            return httpx.Response(200, json=[{**market(i, 1.0 if i == "usd-coin" else 100.0, 10.0), "id": i}
                                             for i in ids])
        if "alchemy.com" in url:
            body = json.loads(req.content)
            if isinstance(body, list):  # batched metadata
                return httpx.Response(200, json=[{"id": c["id"], "result": {"decimals": 6}} for c in body])
            if body["method"] == "eth_getBalance":
                return httpx.Response(200, json={"result": hex(2 * 10**18)})
            return httpx.Response(200, json={"result": {"tokenBalances": [
                {"contractAddress": usdc_eth.upper().replace("X", "x"), "tokenBalance": hex(500 * 10**6)},
                {"contractAddress": "0xspam", "tokenBalance": "0x1"}]}})
        if "solana" in url:
            body = json.loads(req.content)
            if body["method"] == "getBalance":
                return httpx.Response(200, json={"result": {"value": 3 * 10**9}})
            accounts = [] if "zQdB" in body["params"][1]["programId"] else [
                {"account": {"data": {"parsed": {"info": {"mint": usdc_sol, "tokenAmount": {"uiAmountString": "25"}}}}}}]
            return httpx.Response(200, json={"result": {"value": accounts}})
        if "mempool" in url:
            return httpx.Response(200, json={"chain_stats": {"funded_txo_sum": 150_000_000, "spent_txo_sum": 50_000_000},
                                             "mempool_stats": {}})
        return httpx.Response(404)

    settings = Settings(alchemy_api_key="k", wallets_file=tmp_path / "none.json", cache_dir=tmp_path)
    cfg = parse_config({"wallets": [
        {"label": "MM", "type": "evm", "address": "0xabc", "chains": ["ethereum"]},
        {"label": "Phantom", "type": "solana", "address": "So1"},
        {"label": "Cold", "type": "bitcoin", "address": "bc1q"},
        {"label": "CEX", "type": "manual", "holdings": [{"coin": "bitcoin", "amount": 0.5}]},
    ]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await collect(cfg, settings, client)

    d = asyncio.run(run())
    holdings = {r["id"]: r for r in d["views"]["all"]["holdings"]}
    assert holdings["ethereum"]["amount"] == pytest.approx(2)
    assert holdings["solana"]["amount"] == pytest.approx(3)
    assert holdings["usd-coin"]["amount"] == pytest.approx(525)
    assert holdings["bitcoin"]["amount"] == pytest.approx(1.5)
    assert any("1 unlisted/spam" in w for w in d["warnings"])
    assert set(d["views"]) == {"all", "MM", "Phantom", "Cold", "CEX"}


def test_password_gate(monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as main
    monkeypatch.setattr(main.settings, "dashboard_password", "s3cret")
    client = TestClient(main.app)
    assert client.get("/api/health").status_code == 200
    r = client.get("/")
    assert r.status_code == 401 and "Basic" in r.headers["www-authenticate"]
    assert client.get("/", auth=("anyone", "wrong")).status_code == 401
    assert client.get("/", auth=("anyone", "s3cret")).status_code == 200
