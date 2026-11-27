"""SQLite record of every alert, every near miss, and what the price did afterwards.

Two kinds of row in `signals`:
- `alert`: passed every check and was posted. This is the track record you sell on.
- `miss`: passed the 5-minute activity checks but failed a safety check. Never posted; tracked
  the same way so a rule change can be judged on outcomes ("what if the bundle cap were 10%?").

Two kinds of outcome:
- `outcomes`: the spot price 15m / 1h / 4h / 24h after the signal, recorded only near each mark.
- `exits`: replayed from 5-minute candles once 24h have passed: did the price reach the
  take-profit before the stop-loss? A candle that touches both counts as the stop (conservative).

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
  signal_id INTEGER PRIMARY KEY REFERENCES signals (id),
  tp_pct REAL NOT NULL,
  sl_pct REAL NOT NULL,
  result TEXT NOT NULL CHECK (result IN ('tp', 'sl', 'open')),
  minutes_to_exit REAL,
  close_return_pct REAL,       -- return at the end of the window, for 'open'
  max_up_pct REAL NOT NULL,
  max_down_pct REAL NOT NULL,
  candles INTEGER NOT NULL,
  checked_at TEXT NOT NULL
);
"""


def now() -> datetime:
    return datetime.now(timezone.utc)


def replay_exit(candles: list[list[float]], start_ts: float, entry: float, tp_pct: float, sl_pct: float,
                window: float = EXIT_WINDOW) -> dict | None:
    """First touch of take-profit vs stop-loss over [start_ts, start_ts + window).

    candles: [ts, open, high, low, close, volume] in any order. A candle starting before the signal
    is skipped: its high may have printed before the entry existed.
    """
    rows = sorted(c for c in candles if start_ts <= c[0] < start_ts + window)
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
    return {
        "result": result,
        "minutes_to_exit": round((when - start_ts) / 60, 1) if when is not None else None,
        "close_return_pct": round((rows[-1][4] / entry - 1) * 100, 2) if result == "open" else None,
        "max_up_pct": round((max(r[2] for r in rows) / entry - 1) * 100, 2),
        "max_down_pct": round((min(r[3] for r in rows) / entry - 1) * 100, 2),
        "candles": len(rows),
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
            "SELECT s.id, s.pool, s.at, s.price FROM signals s LEFT JOIN exits e ON e.signal_id = s.id "
            "WHERE e.signal_id IS NULL AND s.pool IS NOT NULL AND s.price > 0 AND s.at BETWEEN ? AND ? "
            "ORDER BY s.kind = 'alert' DESC, s.at LIMIT ?", (lo, hi, limit)).fetchall()

    def add_exit(self, signal_id: int, tp_pct: float, sl_pct: float, r: dict, at: datetime | None = None) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO exits (signal_id, tp_pct, sl_pct, result, minutes_to_exit, close_return_pct, "
            "max_up_pct, max_down_pct, candles, checked_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (signal_id, tp_pct, sl_pct, r["result"], r["minutes_to_exit"], r["close_return_pct"],
             r["max_up_pct"], r["max_down_pct"], r["candles"], (at or now()).isoformat()))
        self.db.commit()

    # --- reading ---------------------------------------------------------------------------------

    def _exit_summary(self, where: str, args: tuple) -> dict | None:
        rows = self.db.execute(f"SELECT e.* FROM exits e JOIN signals s ON s.id = e.signal_id WHERE {where}",
                               args).fetchall()
        if not rows:
            return None
        n = len(rows)
        tp = [r for r in rows if r["result"] == "tp"]
        sl = [r for r in rows if r["result"] == "sl"]
        opened = [r for r in rows if r["result"] == "open"]
        # Average result per trade at these exits: TP and SL fills at their levels, open at the 24h close.
        pnl = [r["tp_pct"] if r["result"] == "tp" else -r["sl_pct"] if r["result"] == "sl"
               else (r["close_return_pct"] or 0) for r in rows]
        return {"n": n, "tp_pct": rows[0]["tp_pct"], "sl_pct": rows[0]["sl_pct"],
                "hit_tp_pct": round(100 * len(tp) / n, 1), "hit_sl_pct": round(100 * len(sl) / n, 1),
                "open_pct": round(100 * len(opened) / n, 1),
                "avg_trade_pct": round(statistics.fmean(pnl), 1),
                "median_minutes_to_tp": round(statistics.median(r["minutes_to_exit"] for r in tp), 1) if tp else None}

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

    def backup(self, dest: str) -> None:
        """Consistent copy while the feed keeps writing (SQLite online backup)."""
        target = sqlite3.connect(dest)
        with target:
            self.db.backup(target)
        target.close()
