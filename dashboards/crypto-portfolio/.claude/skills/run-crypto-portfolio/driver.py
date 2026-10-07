#!/usr/bin/env python3
"""Headless-Chromium REPL for the crypto portfolio dashboard.

Reads one command per line from stdin (pipe a heredoc, or run under tmux and
send-keys). Prints `ready` once the dashboard has rendered, and `ok`/`ERR ...`
after every command, so callers can poll for completion.

  python3 driver.py [--base URL] [--password PW] [--width W --height H] < commands

Commands
  nav [path]                 load base URL + path (default /) and wait for the hero chart
  wait <selector>            wait until selector is visible (10s)
  click <selector>           click (Playwright selector syntax: css, text=..., has-text)
  fill <selector> <text>     type into an input
  press <key>                keyboard key, e.g. Escape, Enter
  view <name>                switch wallet tab: "all" or a wallet label from wallets.json
  palette <aurora|ember|lagoon>
  expand <SYMBOL>            open the holdings drill-down row for a coin (e.g. BTC)
  viewport <w> <h>           resize; the chart re-renders on resize
  text <selector>            print innerText
  eval <js expression>       print the JSON result
  ss [name]                  full-page screenshot -> $SHOTS (default /tmp/crypto-shots)
  ss-el <selector> [name]    screenshot one element
  overflow                   print document/table scrollWidth vs viewport (layout regression check)
  errors                     print page errors + console errors (cert errors on coin logos filtered)
  sleep <ms>
  quit
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from pathlib import Path

from playwright.sync_api import Error as PWError
from playwright.sync_api import sync_playwright

SHOTS = Path(os.environ.get("SHOTS", "/tmp/crypto-shots"))
READY = "#chart svg"  # the hero chart only renders after /api/portfolio succeeded


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("BASE_URL", "http://localhost:8000"))
    ap.add_argument("--password", default=os.environ.get("DASHBOARD_PASSWORD") or None)
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    args = ap.parse_args()
    SHOTS.mkdir(parents=True, exist_ok=True)

    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            viewport={"width": args.width, "height": args.height},
            http_credentials={"username": "agent", "password": args.password} if args.password else None,
        )
        page = ctx.new_page()
        page.set_default_timeout(10_000)  # a bad selector fails in 10s, not Playwright's 30s
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: m.type == "error" and errors.append(f"console: {m.text}"))

        def nav(path: str = "/") -> None:
            page.goto(args.base.rstrip("/") + path)
            page.wait_for_selector(READY, timeout=60_000)  # first load fetches live prices: a few seconds
            page.wait_for_timeout(500)

        def shot(name: str | None, el: str | None = None) -> None:
            name = name or f"shot-{int(time.time() * 1000)}"
            path = SHOTS / f"{name}.png"
            (page.locator(el).first.screenshot(path=str(path)) if el
             else page.screenshot(path=str(path), full_page=True))
            print(path)

        nav()
        print("ready", flush=True)

        for raw in sys.stdin:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            cmd, _, rest = line.partition(" ")
            a = shlex.split(rest) if rest else []
            try:
                if cmd == "quit":
                    break
                elif cmd == "nav":
                    nav(a[0] if a else "/")
                elif cmd == "wait":
                    page.wait_for_selector(rest, timeout=10_000)
                elif cmd == "click":
                    page.click(rest)
                elif cmd == "fill":
                    page.fill(a[0], " ".join(a[1:]))
                elif cmd == "press":
                    page.keyboard.press(a[0])
                elif cmd == "view":
                    page.click(f'#wallet-nav button[data-view="{rest}"]')
                    page.wait_for_selector(READY)
                elif cmd == "palette":
                    page.click("#palette")
                    page.click(f'[data-pal="{a[0]}"]')
                elif cmd == "expand":
                    page.click(f'tr.row:has(.sym:text-is("{a[0]}")) .expand')
                    page.wait_for_selector("tr.detail")
                elif cmd == "viewport":
                    page.set_viewport_size({"width": int(a[0]), "height": int(a[1])})
                    page.wait_for_timeout(300)
                elif cmd == "text":
                    print(page.locator(rest).first.inner_text())
                elif cmd == "eval":
                    print(json.dumps(page.evaluate(rest), default=str))
                elif cmd == "ss":
                    shot(a[0] if a else None)
                elif cmd == "ss-el":
                    shot(a[1] if len(a) > 1 else None, a[0])
                elif cmd == "overflow":
                    print(json.dumps(page.evaluate("""() => ({
                        viewport: innerWidth,
                        page: document.documentElement.scrollWidth,
                        table: [document.querySelector('.tbl-wrap')?.scrollWidth,
                                document.querySelector('.tbl-wrap')?.clientWidth]})""")))
                elif cmd == "errors":
                    real = [e for e in errors if "ERR_CERT" not in e]
                    print("\n".join(real) if real else "(no errors)")
                elif cmd == "sleep":
                    page.wait_for_timeout(int(a[0]))
                else:
                    print(f"ERR unknown command: {cmd}")
                    continue
                print("ok", flush=True)
            except (PWError, IndexError, ValueError) as e:
                print(f"ERR {cmd}: {str(e).splitlines()[0]}", flush=True)
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
