# Crypto Portfolio Dashboard

One dashboard for every wallet you own: EVM chains, Solana, Bitcoin, and exchange/manual balances.
It combines them into a single view and also lets you drill into each wallet. Everything is priced
live from CoinGecko.

![stack](https://img.shields.io/badge/stack-FastAPI%20%2B%20vanilla%20JS-blue)

## Quick start

```bash
cd dashboards/crypto-portfolio
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
# open http://localhost:8000
```

With no `wallets.json` it runs a **demo portfolio**, so you can see it working right away.

### Add your wallets

```bash
cp wallets.example.json wallets.json   # git-ignored
cp .env.example .env                   # git-ignored, add API keys
```

| Wallet `type` | What it reads | Needs |
|---|---|---|
| `evm` | Native coin + ERC-20s on `ethereum`, `base`, `arbitrum`, `optimism`, `polygon`, `robinhood` (Robinhood Chain), `arc` (Circle's Arc, where USDC is the gas coin), `hyperevm` | Nothing. Tokens come from Alchemy if `ALCHEMY_API_KEY` is set, else Blockscout, else a Multicall3 `balanceOf` scan over every token CoinGecko lists on that chain. An Alchemy key makes it faster and more reliable. |
| `solana` | SOL + SPL + Token-2022 tokens | Nothing (public RPC). Set `SOLANA_RPC_URL` (Helius, QuickNode) if you get rate-limited. |
| `hyperliquid` | HyperCore spot balances, perp account equity, staked HYPE, vault deposits | Nothing (public info API) |
| `bitcoin` | Balance of one or more addresses (`addresses: [...]`) | Nothing (mempool.space) |
| `manual` | Coins held on exchanges (Coinbase, Kraken…) or anywhere else | `coin` = CoinGecko id (the slug in `coingecko.com/en/coins/<id>`) |

Optional `cost_basis` (total paid per coin across all wallets) turns on unrealized PnL. In a
single-wallet view it's pro-rated by how much of the coin that wallet holds.

Only **public addresses** go in `wallets.json`. The app never needs, and must never be given, a seed
phrase or private key.

Tokens CoinGecko doesn't list (airdropped spam, scam tokens) are dropped automatically. The count
shows up under "data notices".

## Deploy to Google Cloud Run (one command)

The live site is **password-protected** (your browser shows a login prompt, which works on a phone).
`wallets.json` and your API keys go into **Secret Manager**. They are never baked into the image or committed.

Before you start: a Google Cloud project with **billing enabled** (Cloud Run needs it even inside
the free tier). Create one at [console.cloud.google.com/projectcreate](https://console.cloud.google.com/projectcreate).

1. **Open the repo in Cloud Shell** (it's already signed in to your Google account; when asked,
   choose *Trust* so your files survive between sessions):

   [![Open in Cloud Shell](https://gstatic.com/cloudssh/images/open-btn.svg)](https://shell.cloud.google.com/cloudshell/editor?cloudshell_git_repo=https%3A%2F%2Fgithub.com%2Fkgoodeee93%2FQuasi&cloudshell_workspace=dashboards%2Fcrypto-portfolio)

   Or by hand: open [shell.cloud.google.com](https://shell.cloud.google.com) and run
   `git clone https://github.com/kgoodeee93/Quasi.git && cd Quasi/dashboards/crypto-portfolio`.
2. **Point it at your project and add your wallets** (Cloud Shell has an editor):
   ```bash
   gcloud config set project YOUR_PROJECT_ID
   cp wallets.example.json wallets.json && cloudshell edit wallets.json   # your public addresses
   cp .env.example .env                   # optional: ALCHEMY_API_KEY, COINGECKO_API_KEY, MOBY_API_KEY
   ```
3. **Deploy:**
   ```bash
   ./deploy.sh                            # or: DASHBOARD_PASSWORD='pick-one' ./deploy.sh
   ```
   The first run takes about 3–5 minutes (it builds the container). If it asks to create an
   Artifact Registry repository, answer **Y**. It prints the URL and, on the first run, a
   generated password. Log in with any username.

**Updating wallets or keys:** edit the files and run `./deploy.sh` again.
**Changing the password:** `DASHBOARD_PASSWORD='new' ./deploy.sh`.
**Reading the password:** `gcloud secrets versions access latest --secret crypto-dashboard-password`.

**Cost:** it scales to zero when nobody is looking (`min-instances 0`, `max-instances 1`), so personal use
fits inside Cloud Run's free tier. Secret Manager costs about $0.06 per secret per month beyond its
free allowance. The first page load after it has been idle takes a few seconds (cold start).

## What's on the dashboard

**Filters (top row):** all wallets combined or one wallet at a time, asset search, hide dust (< $1),
auto-refresh every 60s.

**KPI tiles:** total balance, 24h / 7d / 30d PnL ($ and %), unrealized PnL vs cost basis,
stablecoin share (dry powder).

**Charts:**
- **Portfolio value, 7 days:** your *current* holdings priced hourly over the week, with hover crosshair, 7d high/low, drawdown from the 7d high, and 1h change.
- **Allocation** by asset, wallet or chain.
- **24h PnL by asset:** which positions made or lost you money today, in dollars.

**Risk & insights:** top gainer/loser, biggest $ contributor and drag, largest position, top-5 share,
concentration (HHI plus "effective number of assets"), value-weighted market cap, and market-cap
exposure (large / mid / small caps and stables).

**Wallet cards:** value, 24h PnL and portfolio share per wallet. Click one to drill in.

**Smart money** (shown when `MOBY_API_KEY` is set; Solana only): who's buying what you hold, from
[Moby](https://moby.win)'s tracked whale, KOL and smart-money wallets, over 1h / 4h / 24h.
- **Your tokens:** net flow, wallets in, buy/sell counts and time of the last buy, Moby's safety tier
  (Verified / Unscored / Caution) and its one-line read. SOL and stablecoins are left out: Solana
  swaps settle in them, so Moby records no flow into them.
- **Whole market:** whale and KOL net flow, and the five most net-bought and net-sold tokens, marked
  "You hold" when they're yours.

The server calls only Moby's read-only tools (`get_smart_money_pulse`, `get_token_overviews`,
`get_token_smart_money`); its client refuses every other tool, so the dashboard can't trade. Results
are cached for `MOBY_CACHE_TTL` seconds (default 120, about how often Moby refreshes).

**Holdings table** (sortable; click a row to expand):

| Column | Meaning |
|---|---|
| Price, 1h / 24h / 7d / 30d | Price change over each window |
| Holdings, Value, Alloc. | Amount held (all wallets), value, % of portfolio |
| 24h PnL | Change in value of what you hold now over 24h |
| Market cap (+ rank), 24h volume | From CoinGecko |
| Unreal. PnL | Value − cost basis (if configured) |
| 7d | Sparkline |
| **Expanded row** | Per-wallet/chain split, 24h range and where the price sits in it, market cap 24h change, FDV, Mcap/FDV (below 0.5 means large token unlocks are still ahead), volume/mcap (liquidity), ATH and distance from it, 1y change, circulating vs max supply, 7d/30d PnL, cost basis and average cost |

### How PnL is computed

`24h PnL = value_now × pct / (100 + pct)`, which is today's value minus what the same holdings were
worth 24h ago. It reflects price movement only. Deposits, withdrawals and trades during the window
aren't counted (that needs transaction history; see the roadmap).

## How it's built

```
app/
  config.py          settings from .env + wallets.json parsing/validation
  providers/
    prices.py        CoinGecko: contract→coin id map (cached 24h) + market data
    evm.py           Alchemy JSON-RPC: native balance, ERC-20 balances, decimals
    solana.py        Solana JSON-RPC: SOL + token accounts
    bitcoin.py       mempool.space address stats
    moby.py          Moby MCP client (streamable HTTP), read-only tools only
  portfolio.py       PURE math: positions + prices → every metric (no I/O, fully unit-tested)
  service.py         fetches all wallets in parallel (asyncio.gather), one failure doesn't sink the rest
  smart_money.py     Moby flow for your Solana tokens + market pulse, shaped for the panel
  main.py            FastAPI: GET /api/portfolio, GET /api/smart-money (?refresh=true), serves static/,
                     optional password gate
Dockerfile           production image (non-root, uvicorn on $PORT)
deploy.sh            Cloud Run + Secret Manager deploy
static/              index.html + app.js (vanilla JS, hand-rolled SVG charts) + styles.css (light/dark)
tests/               pytest, all HTTP mocked
```

The data flow is **wallets → Positions (wallet, chain, coin id, amount) → CoinGecko prices →
portfolio.py → one JSON payload** containing a view for "all" plus one per wallet. The frontend only
renders; it does no math. Results are cached for `CACHE_TTL` seconds to keep API usage low. The
CoinGecko client retries on 429 rate limits.

Run tests: `pip install -r requirements-dev.txt && python -m pytest -q`

## Roadmap ideas

- Transaction-aware PnL (realized PnL, deposits/withdrawals) via Alchemy transfers / Helius parsed txs
- DeFi positions (LP, lending, staking) via DeBank / Zerion APIs
- Exchange APIs (Coinbase, Kraken) instead of manual entries
- Daily snapshots to SQLite/Supabase for a real long-term value chart
- Price/PnL alerts (Telegram / email)
