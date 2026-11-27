"""The bot against fake Jupiter / RPC / Solana Tracker / feed clients: nothing here touches the
network or signs anything."""
import json
import sqlite3
from datetime import datetime, timezone

import pytest

import bot as botmod
import config
import notify

MINT, MINT2 = "So1aNaMemeCoin1111111111111111111111111111", "AnotherMeme11111111111111111111111111111111"
SOL_USD = 100.0


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeJup:
    def __init__(self):
        self.px = {config.SOL_MINT: SOL_USD}
        self.decimals = {MINT: 6, MINT2: 6}
        self.swap_results = []   # popped in order; default success
        self.swaps = []
        self.wallet = "FakeWallet"

    def price_info(self, mints):
        return {m: {"usdPrice": self.px[m], "decimals": self.decimals.get(m, 9)} for m in mints if m in self.px}

    def prices(self, mints):
        return {m: v["usdPrice"] for m, v in self.price_info(mints).items()}

    def price(self, mint):
        return self.prices([mint]).get(mint)

    def swap(self, i, o, amount, slip):
        self.swaps.append((i, o, amount))
        if self.swap_results:
            return self.swap_results.pop(0)
        if i == config.SOL_MINT:   # buy: 0.2 SOL -> 1,000,000 tokens (6 decimals) = $0.00002/token
            return {"ok": True, "in_amount": amount, "out_amount": 1_000_000 * 10 ** 6, "signature": "sigbuy"}
        return {"ok": True, "in_amount": amount, "out_amount": int(0.05 * 1e9), "signature": "sigsell"}


class FakeChain:
    def __init__(self):
        self.balances = {}

    def token_balance(self, mint):
        return self.balances.get(mint)


class FakeFeed:
    def __init__(self, alerts=()):
        self.alerts = list(alerts)

    def last_alert_id(self):
        return max((a["id"] for a in self.alerts), default=0)

    def alerts_after(self, last_id):
        return [a for a in self.alerts if a["id"] > last_id]


def alert(i, mint, at):
    return {"id": i, "mint": mint, "symbol": "MEME", "at": datetime.fromtimestamp(at, timezone.utc).isoformat()}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(config, "JOURNAL_FILE", str(tmp_path / "trades.jsonl"))
    monkeypatch.setattr(config, "PAUSE_FILE", str(tmp_path / "PAUSE"))
    monkeypatch.setattr(config, "DRY_RUN", True)
    monkeypatch.setattr(config, "ENTRY_SOURCE", "feed")
    sent = []
    monkeypatch.setattr(notify, "send", lambda text: sent.append(text) or True)
    return tmp_path, sent


def make(feed=None, live=False, monkeypatch=None, clock=None):
    if live:
        monkeypatch.setattr(config, "DRY_RUN", False)
    return botmod.Bot(jup=FakeJup(), chain=FakeChain(), feed=feed or FakeFeed(), clock=clock or Clock(),
                      sleep=lambda s: None)


def open_via_feed(b, clock, price=0.00002):
    """First scan starts the feed cursor; an alert after that gets bought."""
    b.jup.px[MINT] = price
    b.scan()                                   # cursor starts at the current last alert
    b.feed.alerts.append(alert(b.feed.last_alert_id() + 1, MINT, clock()))
    b.scan()
    return b.positions[MINT]


def journal(tmp):
    p = tmp / "trades.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


# --- notifications --------------------------------------------------------------------------

def test_notify_reads_settings_at_send_time_and_escapes(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert notify.send("x") is False            # no settings: silent no-op
    posted = []

    class R:
        ok, status_code = True, 200
    monkeypatch.setattr(notify.requests, "post", lambda url, json, timeout: posted.append(json) or R())
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")  # set AFTER import: the original read it at import
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    assert notify.send("hi") is True and posted[0]["chat_id"] == "c"
    assert notify.esc("<urllib3.connection object at 0x1> & co") == "&lt;urllib3.connection object at 0x1&gt; &amp; co"


# --- dry run: a full trade, partial take-profits, P&L in the journal --------------------------

def test_dry_run_trades_the_ladder_and_records_pnl(env):
    tmp, sent = env
    clock = Clock()
    b = make(clock=clock)
    pos = open_via_feed(b, clock)
    entry = pos.entry_price
    assert entry == pytest.approx(0.00002 * 1.01)                  # simulated slippage on the buy
    assert pos.tokens_total == int(0.2 * SOL_USD / entry * 10 ** 6) and pos.cost_sol == 0.2
    assert b.jup.swaps == []                                        # dry run never swaps

    b.manage(pos, entry * 2.1)                                      # TP1 @ 2x: 15% sold
    assert pos.tp_hits == [0] and pos.tokens_remaining == pos.tokens_total - int(pos.tokens_total * 0.15)
    clock.t += 60
    b.manage(pos, entry * 4.2)                                      # TP2 @ 4x: 25% more
    assert pos.tp_hits == [0, 1]
    # falls through the 2x ratchet: needs a finished minute's close below it
    clock.t += 60
    b.manage(pos, entry * 1.5)                                      # live below 2x, but the last close was 4.2x
    assert MINT in b.positions
    clock.t += 60
    b.manage(pos, entry * 1.5)                                      # the 1.5x minute closed: stop confirmed
    assert MINT not in b.positions
    rows = journal(tmp)
    assert [r["side"] for r in rows] == ["buy", "sell", "sell", "sell", "close"]
    assert all(r["dry_run"] for r in rows)
    close = rows[-1]
    assert close["sol"] > 0 and b.day_pnl_sol == pytest.approx(close["sol"])
    assert "P&amp;L" in sent[-1]
    rep = botmod.report(str(tmp / "trades.jsonl"))
    assert rep["closed"] == 1 and rep["wins"] == 1


def test_stop_waits_for_a_minute_close_not_a_wick(env):
    clock = Clock(1_800_000_000.0)               # start of a minute
    b = make(clock=clock)
    pos = open_via_feed(b, clock)
    e = pos.entry_price
    b.manage(pos, e * 0.70)                      # -30% wick, first minute: no close yet -> live price used
    assert pos.sl1_done                          # (documented fallback: no close exists yet)
    clock.t += 60
    b.manage(pos, e * 0.95)
    clock.t += 15
    b.manage(pos, e * 0.58)                      # -42% intraminute wick; last close was -30% (above SL2)
    assert MINT in b.positions
    clock.t += 60
    b.manage(pos, e * 0.58)                      # that minute closed at -42%: SL2
    assert MINT not in b.positions


def test_hard_stop_sells_on_the_live_price(env):
    clock = Clock()
    b = make(clock=clock)
    pos = open_via_feed(b, clock)
    clock.t += 60
    b.manage(pos, pos.entry_price * 1.1)         # a close well above every stop
    clock.t += 5
    b.manage(pos, pos.entry_price * 0.45)        # rug: -55% live, no close yet
    assert MINT not in b.positions


# --- live-mode bookkeeping (fake swaps) ------------------------------------------------------

def test_failed_take_profit_is_retried_not_marked_done(env, monkeypatch):
    clock = Clock()
    b = make(live=True, monkeypatch=monkeypatch, clock=clock)
    pos = open_via_feed(b, clock)
    assert b.jup.swaps[0][0] == config.SOL_MINT
    assert pos.entry_price == pytest.approx((0.2 * SOL_USD) / 1_000_000)   # from the fill, not a quote
    b.jup.swap_results = [{"ok": False, "error": "status=Failed code=-1", "sent": True}]
    b.manage(pos, pos.entry_price * 2.5)
    assert pos.tp_hits == [] and pos.tokens_remaining == pos.tokens_total   # not marked, nothing lost
    b.manage(pos, pos.entry_price * 2.5)                                    # retried: succeeds
    assert pos.tp_hits == [0]


def test_failed_sl1_is_retried(env, monkeypatch):
    clock = Clock()
    b = make(live=True, monkeypatch=monkeypatch, clock=clock)
    pos = open_via_feed(b, clock)
    b.jup.swap_results = [{"ok": False, "error": "execute: timeout", "sent": True}]
    b.manage(pos, pos.entry_price * 0.7)
    assert not pos.sl1_done
    b.manage(pos, pos.entry_price * 0.7)
    assert pos.sl1_done


def test_unpriced_fill_is_priced_on_the_next_tick(env, monkeypatch):
    clock = Clock()
    b = make(live=True, monkeypatch=monkeypatch, clock=clock)
    b.jup.decimals = {}                     # Jupiter knows no decimals and no price yet
    b.scan()
    b.feed.alerts.append(alert(1, MINT, clock()))
    b.scan()
    pos = b.positions[MINT]
    assert pos.entry_price == 0             # the original left this unmanaged forever
    b.manage(pos, 0.00003)
    assert pos.entry_price == 0.00003 and pos.high_water == 0.00003
    b.manage(pos, 0.00003 * 0.4)            # and now it has stops
    assert MINT not in b.positions


def test_buy_that_reported_failure_but_landed_is_adopted(env, monkeypatch):
    clock = Clock()
    b = make(live=True, monkeypatch=monkeypatch, clock=clock)
    b.jup.px[MINT] = 0.00002
    b.jup.swap_results = [{"ok": False, "error": "execute: read timeout", "sent": True}]
    b.chain.balances[MINT] = 123_000_000
    b.scan()
    b.feed.alerts.append(alert(1, MINT, clock()))
    b.scan()
    assert b.positions[MINT].tokens_total == 123_000_000


def test_full_exit_sweeps_the_real_balance_and_closes_when_empty(env, monkeypatch):
    clock = Clock()
    b = make(live=True, monkeypatch=monkeypatch, clock=clock)
    pos = open_via_feed(b, clock)
    b.chain.balances[MINT] = 0               # sold elsewhere (or never really filled)
    b.manage(pos, pos.entry_price * 0.4)
    assert MINT not in b.positions and len(b.jup.swaps) == 1   # no pointless sell attempt


def test_live_mode_needs_a_key_and_an_rpc(env, monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.delenv("WALLET_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("RPC_URL", raising=False)
    with pytest.raises(SystemExit, match="WALLET_PRIVATE_KEY and RPC_URL"):
        botmod.Bot(feed=FakeFeed())


# --- entries ---------------------------------------------------------------------------------

def test_feed_entries_start_from_now_skip_stale_and_respect_limits(env):
    tmp, sent = env
    clock = Clock()
    b = make(feed=FakeFeed([alert(1, MINT, clock() - 30)]), clock=clock)
    b.jup.px[MINT] = b.jup.px[MINT2] = 0.00002
    b.scan()
    assert b.positions == {}                 # alert 1 predates the bot: never bought
    b.feed.alerts.append(alert(2, MINT2, clock() - 600))
    b.scan()
    assert b.positions == {}                 # 10 minutes old: stale
    (tmp / "PAUSE").write_text("")
    b.feed.alerts.append(alert(3, MINT, clock()))
    b.scan()
    assert b.positions == {} and b.feed_last_id == 3   # paused: consumed, not bought later
    (tmp / "PAUSE").unlink()
    b.day_pnl_sol = -config.MAX_DAILY_LOSS_SOL
    b.feed.alerts.append(alert(4, MINT, clock()))
    b.scan()
    assert b.positions == {} and any("daily loss limit" in s for s in sent)
    clock.t += 86400                          # next UTC day: limits reset
    b._roll_day()
    b.feed.alerts.append(alert(5, MINT, clock()))
    b.scan()
    assert MINT in b.positions


def test_feed_source_reads_the_feed_database(tmp_path):
    from clients import FeedSource
    db = tmp_path / "feed.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE signals (id INTEGER PRIMARY KEY, kind TEXT, mint TEXT, symbol TEXT, at TEXT, price REAL)")
    con.executemany("INSERT INTO signals (kind, mint, symbol, at, price) VALUES (?,?,?,?,?)",
                    [("alert", MINT, "A", "2026-11-27T00:00:00+00:00", 1.0),
                     ("miss", MINT2, "B", "2026-11-27T00:01:00+00:00", 1.0),
                     ("alert", MINT2, "C", "2026-11-27T00:02:00+00:00", 1.0)])
    con.commit()
    con.close()
    f = FeedSource(str(db))
    assert f.last_alert_id() == 3
    assert [a["symbol"] for a in f.alerts_after(0)] == ["A", "C"] and f.alerts_after(3) == []
    assert FeedSource(str(tmp_path / "missing.db")).last_alert_id() is None


def st_record(**over):
    rec = {"token": {"mint": MINT, "symbol": "MEME", "decimals": 6},
           "pools": [{"liquidity": {"usd": 90000}, "security": {"mintAuthority": None, "freezeAuthority": None}}],
           "risk": {"top10": 18.0, "dev": {"percentage": 1.0}, "snipers": {"totalPercentage": 3.0},
                    "bundlers": {"totalPercentage": 0.5}, "risks": [], "rugged": False}}
    for k, v in over.items():
        rec["risk"][k] = v
    return rec


class FakeST:
    def __init__(self, items, stats=None, bundlers=0.5):
        self.items, self.calls = items, []
        self.stats = stats or {"buys": 60, "sells": 30, "buyers": 40, "transactions": 90, "volume": {"total": 20000}}
        self.bundler_pct = bundlers

    def trending(self, tf):
        return self.items

    def token_info(self, mint):
        self.calls.append("token_info")
        return None

    def stats_5m(self, mint):
        self.calls.append("stats")
        return self.stats

    def bundlers(self, mint):
        self.calls.append("bundlers")
        return self.bundler_pct


def st_bot(monkeypatch, st):
    monkeypatch.setattr(config, "ENTRY_SOURCE", "solanatracker")
    b = botmod.Bot(st=st, jup=FakeJup(), clock=Clock(), sleep=lambda s: None)
    b.jup.px[MINT] = 0.00002
    return b


def test_solanatracker_filters_fail_closed(env, monkeypatch):
    b = st_bot(monkeypatch, FakeST([]))
    assert b.passes_filters(st_record())[2] is None
    assert b.st.calls == ["stats", "bundlers"]          # trending rows are used as-is: no token_info call
    assert b.passes_filters(st_record(top10=None))[2] == "no top-10 data"
    assert b.passes_filters(st_record(snipers={}))[2] == "no sniper data"
    assert b.passes_filters(st_record(risks=["Mint Authority Enabled"]))[2] == "mint authority enabled"
    assert b.passes_filters(st_record(risks=[{"name": "Freeze Authority Enabled"}]))[2] == "freeze authority enabled"
    no_sec = st_record()
    no_sec["pools"] = []
    assert b.passes_filters(no_sec)[2] == "no mint/freeze authority data"
    # bundlers omitted from the record: the dedicated endpoint decides
    b.st.bundler_pct = 3.1
    assert b.passes_filters(st_record(bundlers=None))[2] == "bundled 3.1% > 2.0%"
    b.st.bundler_pct = None
    assert b.passes_filters(st_record(bundlers=None))[2] == "no bundler data"
    b.st.stats = {"buys": 60, "sells": 0, "buyers": 40, "transactions": 60, "volume": {"total": 20000}}
    assert "honeypot" in b.passes_filters(st_record())[2]


def test_rejected_tokens_get_another_look_later(env, monkeypatch):
    st = FakeST([st_record(top10=40.0)])
    b = st_bot(monkeypatch, st)
    b.scan()
    assert MINT in b.seen and b.positions == {}
    st.items = [st_record()]
    b.scan()
    assert b.positions == {}                             # same token, too soon: not re-checked
    b.now.t += config.RECHECK_MINUTES * 60 + 1
    b.scan()
    assert MINT in b.positions                           # re-checked and bought (the original never looked again)


# --- state -----------------------------------------------------------------------------------

def test_state_survives_restarts_and_a_corrupt_file_never_forgets_positions(env):
    tmp, sent = env
    clock = Clock()
    b = make(clock=clock)
    open_via_feed(b, clock)
    b2 = make(clock=clock)
    assert MINT in b2.positions and b2.feed_last_id == b.feed_last_id
    (tmp / "state.json").write_text("{not json")
    b3 = make(clock=clock)
    assert MINT in b3.positions                           # fell back to state.json.bak
    (tmp / "state.json.bak").write_text("also broken")
    with pytest.raises(SystemExit, match="Refusing to start"):
        make(clock=clock)
    assert any("Bot not started" in s for s in sent)


def test_loads_the_original_positions_json(env):
    tmp, _ = env
    (tmp / "positions.json").write_text(json.dumps({MINT: {
        "mint": MINT, "symbol": "OLD", "entry_price": 1.0, "tokens_total": 10, "tokens_remaining": 10,
        "high_water": 1.0, "tp_hits": [], "sl1_done": False}}))
    b = make()
    assert b.positions[MINT].symbol == "OLD" and MINT in b.traded
