"""python -m sniper_feed {scan,run,track,stats}

  scan   one scan; prints what passed and the near misses. --send posts alerts to Telegram.
  run    scan + track forever (--every seconds). Posts only with --send.
  track  record prices for alerts whose 15m / 1h / 4h / 24h mark has come.
  stats  the track record so far.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

from . import sources
from .notify import format_alert, send_telegram
from .rules import Rules
from .scan import run_scan
from .store import Store

HERE = Path(__file__).resolve().parent.parent
UA = {"user-agent": "sniper-feed/1.0"}


def log(*a) -> None:
    print(*a, file=sys.stderr, flush=True)


def summary(entry: dict) -> str:
    fails = [f"{c['label']} {c['value'] if c['value'] is not None else 'no data'} (limit {c['limit']})"
             for c in entry["checks"] if not c["ok"]]
    m = entry["metrics"]
    if m.get("safety_tier") == "red":
        fails.append("Moby safety tier red")
    if m.get("rugged"):
        fails.append("RugCheck: rugged")
    if m.get("asset_class") == "tokenized_stock":
        fails.append("tokenized stock (excluded)")
    return f"{entry['token']['symbol']:<12} " + ("; ".join(fails) or "PASS")


async def scan_once(args, rules: Rules, store: Store, client: httpx.AsyncClient) -> None:
    key = os.environ.get("MOBY_API_KEY", "")
    moby = sources.Moby(client, key, os.environ.get("MOBY_MCP_URL", sources.MOBY_URL)) if key else None
    res = await run_scan(client, rules, moby, skip=lambda mint: store.recently_alerted(mint, args.cooldown_hours))
    log(f"scan: {res.candidates} candidates, {res.active} passed 5m activity, {len(res.alerts)} alert(s)")
    for w in res.warnings:
        log("  warning:", w)
    for e in res.near_misses:
        log("  miss ", summary(e))
    tg_token, tg_chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    for e in res.alerts:
        alert_id = store.add_alert(e["token"], e["checks"])
        text = format_alert(e["token"], e["metrics"])
        log("  ALERT", summary(e))
        if args.send:
            if not (tg_token and tg_chat):
                raise SystemExit("--send needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
            await send_telegram(client, tg_token, tg_chat, text)
            store.mark_sent(alert_id)
        else:
            print(text + "\n", flush=True)


async def track(store: Store, client: httpx.AsyncClient) -> int:
    due = store.due()
    if not due:
        return 0
    prices = await sources.gecko_prices(client, sorted({mint for _, mint, _, _ in due}))
    n = 0
    for alert_id, mint, horizon, entry in due:
        if mint in prices:
            store.add_outcome(alert_id, horizon, entry, prices[mint])
            n += 1
    return n


async def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="sniper_feed", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["scan", "run", "track", "stats"])
    p.add_argument("--rules", default=str(HERE / "rules.json"))
    p.add_argument("--db", default=os.environ.get("FEED_DB", "feed.db"))
    p.add_argument("--send", action="store_true", help="post alerts to Telegram")
    p.add_argument("--every", type=int, default=60, help="seconds between scans for `run` (min 30)")
    p.add_argument("--cooldown-hours", type=float, default=24, help="don't re-alert a token within this")
    args = p.parse_args(argv)

    rules, store = Rules.load(args.rules), Store(args.db)
    if args.command == "stats":
        print(json.dumps(store.stats(), indent=2))
        return
    async with httpx.AsyncClient(headers=UA, timeout=20) as client:
        if args.command == "scan":
            await scan_once(args, rules, store, client)
        elif args.command == "track":
            log(f"track: recorded {await track(store, client)} outcome(s)")
        else:
            every = max(args.every, 30)  # GeckoTerminal allows 30 calls/min; a scan uses 3
            while True:
                try:
                    await scan_once(args, rules, store, client)
                    n = await track(store, client)
                    if n:
                        log(f"track: recorded {n} outcome(s)")
                except (sources.SourceError, httpx.HTTPError, RuntimeError) as e:
                    log("run error:", e)
                await asyncio.sleep(every)


if __name__ == "__main__":
    asyncio.run(main())
