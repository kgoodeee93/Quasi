"""python -m sniper_feed {scan,run,track,stats,publish,backup}

  scan    one scan; prints alerts and near misses. --send posts alerts to Telegram.
  run     scan + track forever (--every seconds). Posts only with --send.
  track   record 15m / 1h / 4h / 24h prices and the exit replays (tp_sl and ladder) that are due.
  stats   the track record: alerts, near misses, and misses grouped by the one rule they failed.
  publish write the public track record (alerts only, details delayed) to the path given with --to.
  backup  copy the database to the path given with --to, safely while `run` is writing.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import httpx

from . import sources
from .notify import format_alert, send_telegram
from .rules import Rules
from .scan import run_scan
from .store import DEFAULT_LADDER, EXIT_WINDOW, Store, replay_exit, replay_ladder

HERE = Path(__file__).resolve().parent.parent
UA = {"user-agent": "sniper-feed/1.0"}
MISS_COOLDOWN_HOURS = 6   # record a token's near miss at most this often


def log(*a) -> None:
    print(*a, file=sys.stderr, flush=True)


def summary(entry: dict) -> str:
    fails = []
    for c in entry["checks"]:
        if c["ok"]:
            continue
        value = c["value"] if c["value"] is not None else "no data"
        fails.append(f"{c['label']}: {value}" if isinstance(c["limit"], bool)
                     else f"{c['label']} {value} (limit {c['limit']})")
    return f"{entry['token']['symbol']:<12} " + ("; ".join(fails) or "PASS")


async def scan_once(args, rules: Rules, store: Store, client: httpx.AsyncClient) -> None:
    key = os.environ.get("MOBY_API_KEY", "")
    moby = sources.Moby(client, key, os.environ.get("MOBY_MCP_URL", sources.MOBY_URL)) if key else None
    res = await run_scan(client, rules, moby,
                         skip=lambda mint: store.recent(mint, args.cooldown_hours, kind="alert"),
                         st_key=os.environ.get("SOLANA_TRACKER_API_KEY", "").strip())
    log(f"scan: {res.candidates} candidates, {res.active} passed 5m activity, {len(res.alerts)} alert(s)")
    for w in res.warnings:
        log("  warning:", w)
    for e in res.near_misses:
        log("  miss ", summary(e))
        if not store.recent(e["token"]["mint"], MISS_COOLDOWN_HOURS):
            store.add_signal("miss", e["token"], e["checks"])
    tg_token, tg_chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if res.alerts and args.send and not (tg_token and tg_chat):
        raise SystemExit("--send needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
    for e in res.alerts:
        signal_id = store.add_signal("alert", e["token"], e["checks"])
        text = format_alert(e["token"], e["metrics"])
        log("  ALERT", summary(e))
        if args.send:
            await send_telegram(client, tg_token, tg_chat, text)
            store.mark_sent(signal_id)
        else:
            print(text + "\n", flush=True)


def load_ladder(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return DEFAULT_LADDER
    data = json.loads(p.read_text())
    missing = set(DEFAULT_LADDER) - set(data)
    if missing:
        raise SystemExit(f"{path} is missing {', '.join(sorted(missing))}")
    return data


async def track(args, store: Store, client: httpx.AsyncClient) -> tuple[int, int]:
    """(spot outcomes recorded, exit replays recorded)."""
    n_spot = 0
    due = store.due()
    if due:
        prices = await sources.gecko_prices(client, sorted({mint for _, mint, _, _ in due}))
        for signal_id, mint, horizon, entry in due:
            if mint in prices:
                store.add_outcome(signal_id, horizon, entry, prices[mint])
                n_spot += 1
    n_exit = 0
    for row in store.exits_due(limit=args.exit_batch):
        start = datetime.fromisoformat(row["at"]).timestamp()
        try:
            candles = await sources.gecko_candles(client, row["pool"], start, EXIT_WINDOW)
        except (sources.SourceError, httpx.HTTPError) as e:
            log(f"  exit replay for signal {row['id']} failed: {e}")
            continue
        r = replay_exit(candles, start, row["price"], args.tp, args.sl)
        if r:
            store.add_exit(row["id"], "tp_sl", r, {"tp_pct": args.tp, "sl_pct": args.sl})
            lad = replay_ladder(candles, start, row["price"], args.ladder_cfg)
            store.add_exit(row["id"], "ladder", lad, {"fills": lad["fills"], "tps_hit": lad["tps_hit"]})
            n_exit += 1
    return n_spot, n_exit


async def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="sniper_feed", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["scan", "run", "track", "stats", "publish", "backup"])
    p.add_argument("--rules", default=str(HERE / "rules.json"))
    p.add_argument("--db", default=os.environ.get("FEED_DB", "feed.db"))
    p.add_argument("--send", action="store_true", help="post alerts to Telegram")
    p.add_argument("--every", type=int, default=60, help="seconds between scans for `run` (min 30)")
    p.add_argument("--cooldown-hours", type=float, default=24, help="don't re-alert a token within this")
    p.add_argument("--tp", type=float, default=50, help="take-profit %% for exit replays")
    p.add_argument("--sl", type=float, default=25, help="stop-loss %% for exit replays")
    p.add_argument("--exit-batch", type=int, default=5, help="exit replays per track pass (1 API call each)")
    p.add_argument("--ladder", default=str(HERE / "ladder.json"), help="the bot's exit ladder, for replays")
    p.add_argument("--delay-hours", type=float, default=24, help="`publish`: hide alerts newer than this")
    p.add_argument("--to", help="destination file for `backup` and `publish`")
    args = p.parse_args(argv)

    rules, store = Rules.load(args.rules), Store(args.db)
    args.ladder_cfg = load_ladder(args.ladder)
    if args.command == "stats":
        print(json.dumps(store.stats(), indent=2))
        return
    if args.command == "publish":
        if not args.to:
            raise SystemExit("publish needs --to PATH")
        public = {**store.public_stats(args.delay_hours), "rules": asdict(rules),
                  "ladder": args.ladder_cfg, "exit_tp_sl": {"tp_pct": args.tp, "sl_pct": args.sl},
                  "data": "GeckoTerminal (geckoterminal.com), Moby, RugCheck"
                          + (", Solana Tracker" if os.environ.get("SOLANA_TRACKER_API_KEY") else "")}
        tmp = Path(args.to + ".tmp")
        tmp.write_text(json.dumps(public, indent=1))
        tmp.replace(args.to)
        log(f"publish: wrote {args.to} ({len(public['recent'])} alerts listed)")
        return
    if args.command == "backup":
        if not args.to:
            raise SystemExit("backup needs --to PATH")
        store.backup(args.to)
        log(f"backup: wrote {args.to}")
        return
    async with httpx.AsyncClient(headers=UA, timeout=20) as client:
        if args.command == "scan":
            await scan_once(args, rules, store, client)
        elif args.command == "track":
            log("track: recorded %d price(s), %d exit replay(s)" % await track(args, store, client))
        else:
            every = max(args.every, 30)  # GeckoTerminal allows 30 calls/min; a scan uses 3
            while True:
                try:
                    await scan_once(args, rules, store, client)
                    spot, ex = await track(args, store, client)
                    if spot or ex:
                        log(f"track: recorded {spot} price(s), {ex} exit replay(s)")
                except (sources.SourceError, httpx.HTTPError, RuntimeError) as e:
                    log("run error:", e)
                await asyncio.sleep(every)


if __name__ == "__main__":
    asyncio.run(main())
