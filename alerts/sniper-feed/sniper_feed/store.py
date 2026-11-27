"""SQLite record of every alert, every near miss, and what the price did afterwards.

Two kinds of row in `signals`:
- `alert`: passed every check and was posted. This is the track record you sell on.
- `miss`: passed the 5-minute activity checks but failed a safety check. Never posted; tracked
  the same way so a rule change can be judged on outcomes ("what if the bundle cap were 10%?").

Two kinds of outcome:
- `outcomes`: the spot price 15m / 1h / 4h / 24h after the signal, recorded only near each mark.
- `exits`: replayed from 5-minute candles once 24h have passed, two strategies per signal:
  - `tp_sl`: did the price reach the take-profit before the stop-loss? A candle that touches
    both counts as the stop (conservative).
  - `ladder`: the trading bot's exit ladder (bots/solbot): partial take-profits on the high,
    stops confirmed on the close, a ratcheting stop and a trailing runner. This is what the bot
    would have made on every alert, before fees and slippage.

Every signal is stored before it is sent, and outcomes are only appended, never edited.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone

HORIZONS = {"15m": 900, "1h": 3600, "4h": 14400, "24h": 86400}
EXIT_WINDOW = 86400          # replay the first 24h after the signal
EXIT_DEADLINE = 2 * 86400    # ...as long as we get to it within 48h (older candles may be gone)

# Mirrors bots/solbot/config.py (override with ladder.json): keep them in sync.
DEFAULT_LADDER = {
    "take_profits": [{"mult": 2.0, "sell_pct": 0.15}, {"mult": 4.0, "sell_pct": 0.25},
                     {"mult": 8.0, "sell_pct": 0.25}, {"mult": 10.0, "sell_pct": 0.35}],
    "early_stops": [{"drop_pct": 0.25, "sell_pct": 0.50}, {"drop_pct": 0.40, "sell_pct": 1.00}],
    "ratchet_stop_mult": {"1": 1.0, "2": 2.0, "3": 4.0},
    "trail_runner": True,
    "trail_pct": 0.30,
    "hard_stop_pct": 0.50,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('alert', 'miss')),
  mint TEXT NOT NULL,
  symbol TEXT,
  pool TEXT,
  at TEXT NOT NULL,
  price REAL,
  mcap REAL,
  liquidity REAL,
  failed TEXT NOT NULL DEFAULT '',   -- comma-separated rule names; empty for alerts
  checks TEXT NOT NULL,
  sent INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS signals_mint ON signals (mint, kind, at);
CREATE TABLE IF NOT EXISTS outcomes (
  signal_id INTEGER NOT NULL REFERENCES signals (id),
  horizon TEXT NOT NULL,
  price REAL NOT NULL,
  return_pct REAL NOT NULL,
  checked_at TEXT NOT NULL,
  PRIMARY KEY (signal_id, horizon)
);
CREATE TABLE IF NOT EXISTS exits (
  signal_id INTEGER NOT NULL REFERENCES signals (id),
  strategy TEXT NOT NULL CHECK (strategy IN ('tp_sl', 'ladder')),
  result TEXT NOT NULL,          -- tp_sl: tp | sl | open; ladder: closed | open
  return_pct REAL NOT NULL,      -- the trade at these exits; open = marked at the window's last close
  minutes_to_exit REAL,
  max_up_pct REAL NOT NULL,
  max_down_pct REAL NOT NULL,
  candles INTEGER NOT NULL,
  detail TEXT NOT NULL,          -- JSON: the settings used, and the ladder's fills
  checked_at TEXT NOT NULL,
  PRIMARY KEY (signal_id, strategy)
);
"""


def now() -> datetime:
    return datetime.now(timezone.utc)


def _window(candles: list[list[float]], start_ts: float, window: float) -> list[list[float]]:
    """Candles inside [start_ts, start_ts + window), oldest first. A candle starting before the
    signal is skipped: its high may have printed before the entry existed."""
    return sorted(c for c in candles if start_ts <= c[0] < start_ts + window)


def _range(rows, entry) -> dict:
    return {"max_up_pct": round((max(r[2] for r in rows) / entry - 1) * 100, 2),
            "max_down_pct": round((min(r[3] for r in rows) / entry - 1) * 100, 2),
            "candles": len(rows)}


def replay_exit(candles: list[list[float]], start_ts: float, entry: float, tp_pct: float, sl_pct: float,
                window: float = EXIT_WINDOW) -> dict | None:
    """First touch of take-profit vs stop-loss. candles: [ts, open, high, low, close, volume]."""
    rows = _window(candles, start_ts, window)
    if not rows or entry <= 0:
        return None
    tp, sl = entry * (1 + tp_pct / 100), entry * (1 - sl_pct / 100)
    result, when = "open", None
    for ts, _o, high, low, _c, *_ in rows:
        if low <= sl:              # checked first: a candle touching both counts as the stop
            result, when = "sl", ts
            break
        if high >= tp:
            result, when = "tp", ts
            break
    close_ret = round((rows[-1][4] / entry - 1) * 100, 2) if result == "open" else None
    return {
        "result": result,
        "return_pct": tp_pct if result == "tp" else -sl_pct if result == "sl" else close_ret,
        "minutes_to_exit": round((when - start_ts) / 60, 1) if when is not None else None,
        "close_return_pct": close_ret,
        **_range(rows, entry),
    }


def replay_ladder(candles: list[list[float]], start_ts: float, entry: float, ladder: dict = DEFAULT_LADDER,
                  window: float = EXIT_WINDOW) -> dict | None:
    """The bot's exit ladder over 5-minute candles, in the bot's order on every candle:
    1. hard stop on the low (the bot's live-price stop; first, so a candle that holds a rug
       and a spike counts as the rug),
    2. take-profits on the high, filled at their level,
    3. stops on the close (the bot confirms stops on a closed minute; here, a closed 5 minutes).
    Fractions are of the original position, as in the bot. Returns the trade's % result."""
    rows = _window(candles, start_ts, window)
    if not rows or entry <= 0:
        return None
    tps = ladder["take_profits"]
    sl1, sl2 = ladder["early_stops"]
    ratchet = {int(k): float(v) for k, v in ladder["ratchet_stop_mult"].items()}
    trail, trail_pct, hard = ladder["trail_runner"], ladder["trail_pct"], ladder.get("hard_stop_pct") or 0

    remaining, proceeds, high_water = 1.0, 0.0, entry
    hits: list[int] = []
    sl1_done = False
    fills: list[list] = []
    exit_ts = None

    def sell(fraction: float, price: float, reason: str, ts: float) -> None:
        nonlocal remaining, proceeds
        amount = remaining if fraction >= 1 else min(fraction, remaining)
        if amount <= 0:
            return
        remaining -= amount
        proceeds += amount * price / entry
        fills.append([round((ts - start_ts) / 60, 1), reason, round(amount, 4), round(price / entry, 4)])

    for ts, _o, high, low, close, *_ in rows:
        high_water = max(high_water, high)
        if hard and low <= entry * (1 - hard):
            sell(1.0, entry * (1 - hard), "hard stop", ts)
        else:
            for i, tp in enumerate(tps):
                if i in hits or (trail and i == len(tps) - 1):
                    continue
                if high >= entry * tp["mult"]:
                    sell(tp["sell_pct"], entry * tp["mult"], f"TP{i + 1}", ts)
                    hits.append(i)
            top = max(hits) + 1 if hits else 0
            if remaining > 0 and top == 0:
                if close <= entry * (1 - sl2["drop_pct"]):
                    sell(1.0, close, "SL2", ts)
                elif not sl1_done and close <= entry * (1 - sl1["drop_pct"]):
                    sell(sl1["sell_pct"], close, "SL1", ts)
                    sl1_done = True
            elif remaining > 0:
                stop = entry * ratchet.get(min(top, 3), 1.0)
                if trail and top >= 3:
                    stop = max(stop, high_water * (1 - trail_pct))
                if close <= stop:
                    sell(1.0, close, "trail" if trail and top >= 3 else "ratchet", ts)
        if remaining <= 1e-9:
            exit_ts = ts
            break

    value = proceeds + remaining * rows[-1][4] / entry if remaining > 1e-9 else proceeds
    return {
        "result": "open" if remaining > 1e-9 else "closed",
        "return_pct": round((value - 1) * 100, 2),
        "minutes_to_exit": round((exit_ts - start_ts) / 60, 1) if exit_ts is not None else None,
        "tps_hit": len(hits),
        "fills": fills,
        **_range(rows, entry),
    }


def _summary(rets: list[float]) -> dict:
    return {"n": len(rets), "median_pct": round(statistics.median(rets), 1),
            "mean_pct": round(statistics.fmean(rets), 1),
            "win_rate_pct": round(100 * sum(r > 0 for r in rets) / len(rets), 1),
            "best_pct": round(max(rets), 1), "worst_pct": round(min(rets), 1)}


class Store:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # --- writing ---------------------------------------------------------------------------------

    def recent(self, mint: str, hours: float, kind: str | None = None, at: datetime | None = None) -> bool:
        since = ((at or now()) - timedelta(hours=hours)).isoformat()
        q, args = "SELECT 1 FROM signals WHERE mint = ? AND at >= ?", [mint, since]
        if kind:
            q, args = q + " AND kind = ?", args + [kind]
        return self.db.execute(q, args).fetchone() is not None

    def add_signal(self, kind: str, token: dict, checks: list[dict], at: datetime | None = None) -> int:
        failed = ",".join(c["rule"] for c in checks if not c["ok"])
        cur = self.db.execute(
            "INSERT INTO signals (kind, mint, symbol, pool, at, price, mcap, liquidity, failed, checks) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (kind, token["mint"], token.get("symbol"), token.get("pool"), (at or now()).isoformat(),
             token.get("price"), token.get("mcap"), token.get("liquidity"), failed, json.dumps(checks)))
        self.db.commit()
        return cur.lastrowid

    def mark_sent(self, signal_id: int) -> None:
        self.db.execute("UPDATE signals SET sent = 1 WHERE id = ?", (signal_id,))
        self.db.commit()

    def due(self, at: datetime | None = None) -> list[tuple[int, str, str, float]]:
        """(signal_id, mint, horizon, entry price) for every unrecorded horizon whose time is now.

        A horizon is only recorded close to its mark (up to 50% late, plus 5 minutes): a price read
        hours late would be a different trade, so a missed mark stays missing rather than filled.
        """
        at = at or now()
        oldest = (at - timedelta(seconds=max(HORIZONS.values()) * 1.5 + 300)).isoformat()
        rows = self.db.execute("SELECT id, mint, at, price FROM signals WHERE price > 0 AND at >= ?",
                               (oldest,)).fetchall()
        done = {(r["signal_id"], r["horizon"]) for r in self.db.execute("SELECT signal_id, horizon FROM outcomes")}
        out = []
        for r in rows:
            age = (at - datetime.fromisoformat(r["at"])).total_seconds()
            for h, secs in HORIZONS.items():
                if secs <= age <= secs * 1.5 + 300 and (r["id"], h) not in done:
                    out.append((r["id"], r["mint"], h, r["price"]))
        return out

    def add_outcome(self, signal_id: int, horizon: str, entry: float, price: float, at: datetime | None = None) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO outcomes (signal_id, horizon, price, return_pct, checked_at) VALUES (?,?,?,?,?)",
            (signal_id, horizon, price, (price / entry - 1) * 100, (at or now()).isoformat()))
        self.db.commit()

    def exits_due(self, at: datetime | None = None, limit: int = 5) -> list[sqlite3.Row]:
        """Signals 24-48h old with a pool and no exit replay yet, alerts first."""
        at = at or now()
        lo = (at - timedelta(seconds=EXIT_DEADLINE)).isoformat()
        hi = (at - timedelta(seconds=EXIT_WINDOW)).isoformat()
        return self.db.execute(
            "SELECT s.id, s.pool, s.at, s.price FROM signals s "
            "LEFT JOIN exits e ON e.signal_id = s.id AND e.strategy = 'tp_sl' "
            "WHERE e.signal_id IS NULL AND s.pool IS NOT NULL AND s.price > 0 AND s.at BETWEEN ? AND ? "
            "ORDER BY s.kind = 'alert' DESC, s.at LIMIT ?", (lo, hi, limit)).fetchall()

    def add_exit(self, signal_id: int, strategy: str, r: dict, detail: dict, at: datetime | None = None) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO exits (signal_id, strategy, result, return_pct, minutes_to_exit, "
            "max_up_pct, max_down_pct, candles, detail, checked_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (signal_id, strategy, r["result"], r["return_pct"], r["minutes_to_exit"], r["max_up_pct"],
             r["max_down_pct"], r["candles"], json.dumps(detail), (at or now()).isoformat()))
        self.db.commit()

    # --- reading ---------------------------------------------------------------------------------

    def _exit_summary(self, where: str, args: tuple) -> dict | None:
        rows = self.db.execute(f"SELECT e.* FROM exits e JOIN signals s ON s.id = e.signal_id WHERE {where}",
                               args).fetchall()
        if not rows:
            return None
        out: dict = {}
        tp_sl = [r for r in rows if r["strategy"] == "tp_sl"]
        if tp_sl:
            n, settings = len(tp_sl), json.loads(tp_sl[0]["detail"])
            hit = [r for r in tp_sl if r["result"] == "tp"]
            out["tp_sl"] = {
                "n": n, "tp_pct": settings.get("tp_pct"), "sl_pct": settings.get("sl_pct"),
                "hit_tp_pct": round(100 * len(hit) / n, 1),
                "hit_sl_pct": round(100 * sum(r["result"] == "sl" for r in tp_sl) / n, 1),
                "open_pct": round(100 * sum(r["result"] == "open" for r in tp_sl) / n, 1),
                "avg_trade_pct": round(statistics.fmean(r["return_pct"] for r in tp_sl), 1),
                "median_minutes_to_tp": round(statistics.median(r["minutes_to_exit"] for r in hit), 1) if hit else None}
        ladder = [r for r in rows if r["strategy"] == "ladder"]
        if ladder:
            rets = [r["return_pct"] for r in ladder]
            out["ladder"] = {**_summary(rets), "avg_trade_pct": round(statistics.fmean(rets), 1),
                             "still_open_pct": round(100 * sum(r["result"] == "open" for r in ladder) / len(ladder), 1)}
        return out

    def stats(self) -> dict:
        out: dict = {}
        for kind, label in (("alert", "alerts"), ("miss", "misses")):
            n = self.db.execute("SELECT COUNT(*) FROM signals WHERE kind = ?", (kind,)).fetchone()[0]
            horizons = {}
            for h in HORIZONS:
                rets = [r[0] for r in self.db.execute(
                    "SELECT o.return_pct FROM outcomes o JOIN signals s ON s.id = o.signal_id "
                    "WHERE s.kind = ? AND o.horizon = ?", (kind, h))]
                if rets:
                    horizons[h] = _summary(rets)
            out[label] = {"count": n, "horizons": horizons,
                          "exits": self._exit_summary("s.kind = ?", (kind,))}
        # What-if: misses that failed exactly one rule, grouped by that rule. If the 1h median of
        # "only failed bundled_max_pct" beats the alerts', the bundle cap is costing you winners.
        what_if = {}
        for (rule,) in self.db.execute(
                "SELECT DISTINCT failed FROM signals WHERE kind = 'miss' AND failed != '' AND failed NOT LIKE '%,%'"):
            rets = [r[0] for r in self.db.execute(
                "SELECT o.return_pct FROM outcomes o JOIN signals s ON s.id = o.signal_id "
                "WHERE s.kind = 'miss' AND s.failed = ? AND o.horizon = '1h'", (rule,))]
            what_if[rule] = {"count": self.db.execute(
                "SELECT COUNT(*) FROM signals WHERE kind = 'miss' AND failed = ?", (rule,)).fetchone()[0],
                "1h": _summary(rets) if rets else None,
                "exits": self._exit_summary("s.kind = 'miss' AND s.failed = ?", (rule,))}
        out["only_failed"] = what_if
        return out

    def public_stats(self, delay_hours: float = 24, limit: int = 50, at: datetime | None = None) -> dict:
        """The public track record: aggregates over every alert, plus the latest alerts that are at
        least `delay_hours` old with their outcomes. Recent alerts stay off the public page: they
        are what subscribers pay for. Never includes near misses."""
        at = at or now()
        cutoff = (at - timedelta(hours=delay_hours)).isoformat()
        recent = []
        for s in self.db.execute(
                "SELECT id, symbol, mint, at, price, mcap, liquidity FROM signals "
                "WHERE kind = 'alert' AND at <= ? ORDER BY at DESC LIMIT ?", (cutoff, limit)).fetchall():
            outcomes = {r["horizon"]: round(r["return_pct"], 1) for r in self.db.execute(
                "SELECT horizon, return_pct FROM outcomes WHERE signal_id = ?", (s["id"],))}
            exits = {r["strategy"]: {"result": r["result"], "return_pct": round(r["return_pct"], 1)}
                     for r in self.db.execute("SELECT strategy, result, return_pct FROM exits WHERE signal_id = ?",
                                              (s["id"],))}
            recent.append({"symbol": s["symbol"], "mint": s["mint"], "at": s["at"], "mcap": s["mcap"],
                           "liquidity": s["liquidity"], "returns": outcomes, "exits": exits})
        alerts = self.stats()["alerts"]
        return {"generated_at": at.isoformat(), "delay_hours": delay_hours, "alerts": alerts, "recent": recent}

    def backup(self, dest: str) -> None:
        """Consistent copy while the feed keeps writing (SQLite online backup)."""
        target = sqlite3.connect(dest)
        with target:
            self.db.backup(target)
        target.close()
