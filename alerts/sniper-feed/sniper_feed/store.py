"""SQLite record of every alert and what the price did afterwards: the track record you sell on.

Every alert is stored before it is sent, so a run that dies mid-send can't publish an alert that
the record doesn't hold, and outcomes are only ever appended, never edited.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone

HORIZONS = {"15m": 900, "1h": 3600, "4h": 14400, "24h": 86400}

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY,
  mint TEXT NOT NULL,
  symbol TEXT,
  alerted_at TEXT NOT NULL,
  price REAL,
  mcap REAL,
  liquidity REAL,
  checks TEXT NOT NULL,
  sent INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS alerts_mint ON alerts (mint, alerted_at);
CREATE TABLE IF NOT EXISTS outcomes (
  alert_id INTEGER NOT NULL REFERENCES alerts (id),
  horizon TEXT NOT NULL,
  price REAL NOT NULL,
  return_pct REAL NOT NULL,
  checked_at TEXT NOT NULL,
  PRIMARY KEY (alert_id, horizon)
);
"""


def now() -> datetime:
    return datetime.now(timezone.utc)


class Store:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def recently_alerted(self, mint: str, hours: float, at: datetime | None = None) -> bool:
        since = ((at or now()) - timedelta(hours=hours)).isoformat()
        row = self.db.execute("SELECT 1 FROM alerts WHERE mint = ? AND alerted_at >= ?", (mint, since)).fetchone()
        return row is not None

    def add_alert(self, token: dict, checks: list[dict], at: datetime | None = None) -> int:
        cur = self.db.execute(
            "INSERT INTO alerts (mint, symbol, alerted_at, price, mcap, liquidity, checks) VALUES (?,?,?,?,?,?,?)",
            (token["mint"], token.get("symbol"), (at or now()).isoformat(), token.get("price"),
             token.get("mcap"), token.get("liquidity"), json.dumps(checks)))
        self.db.commit()
        return cur.lastrowid

    def mark_sent(self, alert_id: int) -> None:
        self.db.execute("UPDATE alerts SET sent = 1 WHERE id = ?", (alert_id,))
        self.db.commit()

    def due(self, at: datetime | None = None) -> list[tuple[int, str, str, float]]:
        """(alert_id, mint, horizon, entry price) for every unrecorded horizon whose time is now.

        A horizon is only recorded close to its mark (up to 50% late, plus 5 minutes): a price read
        hours late would be a different trade, so a missed mark stays missing rather than filled.
        """
        at = at or now()
        oldest = (at - timedelta(seconds=max(HORIZONS.values()) * 1.5 + 300)).isoformat()
        rows = self.db.execute("SELECT id, mint, alerted_at, price FROM alerts WHERE price > 0 AND alerted_at >= ?",
                               (oldest,)).fetchall()
        done = {(r["alert_id"], r["horizon"]) for r in self.db.execute("SELECT alert_id, horizon FROM outcomes")}
        out = []
        for r in rows:
            age = (at - datetime.fromisoformat(r["alerted_at"])).total_seconds()
            for h, secs in HORIZONS.items():
                if secs <= age <= secs * 1.5 + 300 and (r["id"], h) not in done:
                    out.append((r["id"], r["mint"], h, r["price"]))
        return out

    def add_outcome(self, alert_id: int, horizon: str, entry: float, price: float, at: datetime | None = None) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO outcomes (alert_id, horizon, price, return_pct, checked_at) VALUES (?,?,?,?,?)",
            (alert_id, horizon, price, (price / entry - 1) * 100, (at or now()).isoformat()))
        self.db.commit()

    def stats(self) -> dict:
        total = self.db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        out = {"alerts": total, "horizons": {}}
        for h in HORIZONS:
            rets = [r[0] for r in self.db.execute("SELECT return_pct FROM outcomes WHERE horizon = ?", (h,))]
            if rets:
                out["horizons"][h] = {
                    "n": len(rets),
                    "median_pct": round(statistics.median(rets), 1),
                    "mean_pct": round(statistics.fmean(rets), 1),
                    "win_rate_pct": round(100 * sum(r > 0 for r in rets) / len(rets), 1),
                    "best_pct": round(max(rets), 1),
                    "worst_pct": round(min(rets), 1),
                }
        return out
