# Crypto Portfolio Dashboard

One dashboard for every wallet you own: EVM chains, Solana, Bitcoin, and exchange/manual balances.
It combines them into a single view and also lets you drill into each wallet. Everything is priced
live from CoinGecko.

![stack](https://img.shields.io/badge/stack-FastAPI%20%2B%20vanilla%20JS-blue)

## Quick start

```bash
cd dashboards/crypto-portfolio
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
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
| `evm` | Native coin + every ERC-20 on `ethereum`, `base`, `arbitrum`, `optimism`, `polygon` | `ALCHEMY_API_KEY` for tokens (free tier works). Without it, only ETH/POL. |
| `solana` | SOL + SPL + Token-2022 tokens | Nothing (public RPC). Set `SOLANA_RPC_URL` (Helius, QuickNode) if you get rate-limited. |
| `bitcoin` | Balance of one or more addresses (`addresses: [...]`) | Nothing (mempool.space) |
| `manual` | Coins held on exchanges (Coinbase, Kraken…) or anywhere else | `coin` = CoinGecko id (the slug in `coingecko.com/en/coins/<id>`) |

Optional `cost_basis` (total paid per coin across all wallets) turns on unrealized PnL. In a
single-wallet view it's pro-rated by how much of the coin that wallet holds.

Only **public addresses** go in `wallets.json`. The app never needs, and must never be given, a seed
phrase or private key.

Tokens CoinGecko doesn't list (airdropped spam, scam tokens) are dropped automatically. The count
shows up under "data notices".

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
  portfolio.py       PURE math: positions + prices → every metric (no I/O, fully unit-tested)
  service.py         fetches all wallets in parallel (asyncio.gather), one failure doesn't sink the rest
  main.py            FastAPI: GET /api/portfolio (?refresh=true), serves static/
static/              index.html + app.js (vanilla JS, hand-rolled SVG charts) + styles.css (light/dark)
tests/               pytest, all HTTP mocked
```

The data flow is **wallets → Positions (wallet, chain, coin id, amount) → CoinGecko prices →
portfolio.py → one JSON payload** containing a view for "all" plus one per wallet. The frontend only
renders; it does no math. Results are cached for `CACHE_TTL` seconds to keep API usage low. The
CoinGecko client retries on 429 rate limits.

Run tests: `python -m pytest -q`

## Roadmap ideas

- Transaction-aware PnL (realized PnL, deposits/withdrawals) via Alchemy transfers / Helius parsed txs
- DeFi positions (LP, lending, staking) via DeBank / Zerion APIs
- Exchange APIs (Coinbase, Kraken) instead of manual entries
- Daily snapshots to SQLite/Supabase for a real long-term value chart
- Price/PnL alerts (Telegram / email)
