---
name: run-crypto-portfolio
description: Build, run, test and drive the crypto portfolio dashboard (FastAPI + Aurora web UI). Use when asked to start or run the dashboard, screenshot it, check the UI/layout after a change, click through wallets/palettes/holdings, hit /api/portfolio, or run its tests.
---

The dashboard is a FastAPI server (`app/`) that serves a vanilla-JS page (`static/`). An agent
drives it by starting the server in the background and piping commands to
`.claude/skills/run-crypto-portfolio/driver.py`, a headless-Chromium REPL that prints `ready`,
then `ok` / `ERR ...` per command and writes screenshots to `/tmp/crypto-shots/`.

All paths below are relative to `dashboards/crypto-portfolio/`.

## Prerequisites

Python 3.11+, outbound HTTPS to `api.coingecko.com` (every page load prices live), and Chromium.
This container ships Chromium build 1194 under `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers`, which
matches Playwright **1.56.0**, so pin it:

```bash
pip install -q -r requirements-dev.txt
pip install -q "playwright==1.56.0"
```

## Run (agent path)

Start the server in **demo mode** (`WALLETS_FILE=/nonexistent` ignores any local `wallets.json`)
and poll until healthy:

```bash
WALLETS_FILE=/nonexistent nohup python3 -m uvicorn app.main:app --port 8000 > /tmp/crypto-dashboard.log 2>&1 &
timeout 30 bash -c 'until curl -sf localhost:8000/api/health >/dev/null; do sleep 0.5; done' && curl -s localhost:8000/api/health
```

Drive it. This exact script ran clean (≈4 s) and covers the main surfaces:

```bash
python3 .claude/skills/run-crypto-portfolio/driver.py <<'EOF'
ss home
text .hero-num
view Phantom
text .hero-label
view all
expand BTC
ss-el .holdings holdings-btc
click button[data-sort="change_24h"]
eval document.querySelector('th[aria-sort]').textContent.trim()
palette ember
eval document.documentElement.dataset.palette
viewport 390 844
overflow
ss mobile
errors
quit
EOF
```

Then **open the PNGs** in `/tmp/crypto-shots/` and look at them. `overflow` must show
`page == viewport` and `table[0] == table[1]` (no horizontal scroll); `errors` must print
`(no errors)`.

| command | does |
|---|---|
| `nav [path]` | load page, wait for the hero chart (`#chart svg`) |
| `view <label>` | wallet tab: `all` or a wallet label (`Ledger (cold)`, `MetaMask`, `Coinbase`, `Phantom` in demo) |
| `expand <SYM>` | open a holdings drill-down row |
| `palette aurora\|ember\|lagoon` | pick from the palette menu (persists in localStorage) |
| `click` / `fill` / `press` / `wait` | Playwright selectors (`css`, `text=…`, `:has-text()`) |
| `text <sel>` / `eval <js>` | print innerText / JSON result |
| `ss [name]` / `ss-el <sel> [name]` | full-page / element screenshot → `/tmp/crypto-shots/<name>.png` |
| `viewport <w> <h>` · `overflow` · `errors` · `sleep <ms>` · `quit` | |

Flags/env: `--base`/`BASE_URL` (default `http://localhost:8000`), `--password`/`DASHBOARD_PASSWORD`,
`--width`/`--height` (1440×900), `SHOTS` (screenshot dir). Under tmux, wait for the `ready` line
before sending commands and for `ok`/`ERR` after each.

**Password-protected / real wallets.** With your real (git-ignored) `wallets.json` and a password:

```bash
DASHBOARD_PASSWORD=s3cret nohup python3 -m uvicorn app.main:app --port 8001 > /tmp/crypto-dashboard-8001.log 2>&1 &
timeout 30 bash -c 'until curl -sf localhost:8001/api/health >/dev/null; do sleep 0.5; done'
curl -s -o /dev/null -w "no-pw %{http_code}\n" localhost:8001/          # -> 401
BASE_URL=http://localhost:8001 DASHBOARD_PASSWORD=s3cret python3 .claude/skills/run-crypto-portfolio/driver.py <<'EOF'
text .hero-label
ss real-wallets
quit
EOF
```

**Stop** by port (never `pkill -f` — see Gotchas):

```bash
lsof -ti:8000 -sTCP:LISTEN | xargs -r kill; lsof -ti:8001 -sTCP:LISTEN | xargs -r kill
```

## Direct invocation (no server, no browser)

Most backend PRs touch `app/providers/*` or `app/portfolio.py`. Call the pipeline directly:

```bash
python3 -c "
import asyncio
from app.config import Settings, parse_config
from app.service import collect
cfg = parse_config({'wallets': [
  {'label': 'Cold', 'type': 'bitcoin', 'address': '1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa'},
  {'label': 'CEX', 'type': 'manual', 'holdings': [{'coin': 'ethereum', 'amount': 1.5}]}]})
d = asyncio.run(collect(cfg, Settings.from_env()))
s = d['views']['all']['summary']
print(round(s['total_value']), [r['symbol'] for r in d['views']['all']['holdings']], d['warnings'])
"
```

`d['views']` has `all` plus one view per wallet label; `d['warnings']` lists provider failures
and filtered spam tokens.

## Test

```bash
python3 -m pytest -q        # -> 10 passed, all HTTP mocked
```

## Run (human path)

`uvicorn app.main:app --reload` → open http://localhost:8000 → Ctrl-C. Deploying: `./deploy.sh`
from Google Cloud Shell (see `README.md`); it needs gcloud auth, which this container lacks.

## Gotchas

- **Latest Playwright can't find the browser.** `pip install playwright` (1.63) wants
  `/opt/pw-browsers/chromium_headless_shell-1243/...`; the container has 1194 → pin `1.56.0`.
  Don't run `playwright install` here.
- **`pkill -f "uvicorn app.main:app"` kills your own shell** (exit 144) because the pattern matches
  the command line that runs it. Stop servers with `lsof -ti:<port> -sTCP:LISTEN | xargs -r kill`.
- **A local `wallets.json` silently switches off demo mode.** It's git-ignored, so it may exist on
  a dev box with someone's real addresses. Use `WALLETS_FILE=/nonexistent` for reproducible demo data.
- **Numbers move between runs.** Prices are live; assert on structure (`.hero-label`, row counts,
  `overflow`), not on dollar values.
- **Coin logos fail in this sandbox** (`ERR_CERT_AUTHORITY_INVALID` from `coin-images.coingecko.com`
  through the agent proxy). The UI falls back to gradient monograms; the driver's `errors` filters
  these. Logos load fine outside the sandbox.
- **CoinGecko 429s without a key.** The client retries with backoff (5 s, 10 s, 20 s); a cold
  start with many tokens can take tens of seconds. Set `COINGECKO_API_KEY` in `.env` if it matters.
- **Real EVM/Solana wallets download a ~4 MB coin list** on first run (cached in `.cache/` for 24 h).
  Demo mode (manual holdings only) skips it.
- **Docker image build in this sandbox** needs two workarounds (the real `Dockerfile` is fine for
  Cloud Build): Docker Hub returns `429 Too Many Requests` → `docker pull mirror.gcr.io/library/python:3.12-slim`
  and tag it `python:3.12-slim`; pip inside the build fails TLS (`self-signed certificate in
  certificate chain`) → build a scratch copy with `/root/.ccr/ca-bundle.crt` copied in and
  `ENV PIP_CERT=/ca.crt SSL_CERT_FILE=/ca.crt`, using `docker build --network host`. Start the
  daemon first with `dockerd &` (no socket at `/var/run/docker.sock` otherwise).

## Troubleshooting

- **`BrowserType.launch: Executable doesn't exist at /opt/pw-browsers/chromium_headless_shell-1243/…`**:
  wrong Playwright version → `pip install "playwright==1.56.0"`.
- **`ERR click: Page.click: Timeout 10000ms exceeded.`**: selector didn't match. Wallet tabs are
  `#wallet-nav button[data-view="<label>"]`, sort headers `button[data-sort="<field>"]`, rows
  `tr.row` with the symbol in `.sym`.
- **`401 Password required`**: the server has `DASHBOARD_PASSWORD` set → pass the same value to the driver.
