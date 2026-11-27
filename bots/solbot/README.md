# Solana memecoin trading bot

Buys new Solana tokens and manages the exits with a take-profit ladder and a
ratcheting/trailing stop. It runs in **dry run** unless you switch it live: every buy and sell
is simulated at the live Jupiter price, written to `trades.jsonl`, and `python bot.py report`
tells you whether the strategy would have made money.

> Not financial advice. Memecoins are extremely high risk and you can lose a
> whole position on any trade. Every number in `config.py` is your decision.

## How it works

- **bot.py**: the scan → buy → manage loop.
- **config.py**: every strategy knob. The only file you edit.
- **clients.py**: Jupiter (prices + swaps), your RPC (balances), Solana Tracker or the sniper feed (entries).
- **notify.py**: Telegram pings for buys, sells, failures and closes.

**Entries** (`ENTRY_SOURCE`):
- `feed` (default): buys the alerts of the sniper feed (`alerts/sniper-feed`), read from its
  database. One screen, one track record, and no Solana Tracker plan needed.
- `solanatracker`: scans Solana Tracker's trending list with `ENTRY_FILTERS`. Needs a paid key
  (Advanced, about €50/month for 200k requests; the free plan's 2,500/month won't last a day).

**Exits** (unchanged from the original): 15% at 2x, 25% at 4x, 25% at 8x, and the last 35% trails
30% below its peak. Before the first take-profit: half out at −25%, the rest at −40%. After it,
the stop climbs to breakeven, then 2x, then 4x. Stops wait for a closed 1-minute close.

## What changed from the original (all of these could lose money)

1. **It wouldn't start.** `requirements.txt` allowed any `solana` version from 0.34 up, and 0.42
   removed the `solana.rpc.api` module `clients.py` imported. A fresh install crashed. The balance
   check now uses plain JSON-RPC and the `solana` package is gone.
2. **Telegram never sent.** `notify.py` read its settings at import, before `.env` was loaded, so
   every ping (including "SELL FAILED") silently did nothing. It now reads them at send time.
3. **Positions could have no stop at all.** If the price lookup failed right after a buy, the entry
   was 0 and the position was skipped forever. It now takes the first live price. A buy that
   reported failure but landed anyway is now detected from the wallet balance and managed.
4. **Failed sells were recorded as done.** A failed take-profit or SL1 was marked as taken, so the
   bot believed it had sold. Now only a sell that went through counts; failures retry every tick
   (with a ping at most every 5 minutes).
5. **Missing data passed the filters.** `or 0` turned a missing top-10, dev, sniper or bundle figure
   into 0%. Missing now rejects. Bundles come from Solana Tracker's dedicated endpoint, because the
   token record "may omit" them; mint/freeze come from the pool's on-chain authorities.
6. **A token rejected once was never looked at again** until restart. Now after 15 minutes.
7. **The entry price was a quote, not the fill.** With 5% buy slippage every level was off. Entry
   now comes from what the swap actually paid.
8. **Dry run didn't test the exits.** It "bought" 1 base unit, so every partial sell rounded to 0.
   It now simulates real token amounts and records P&L.
9. **The state file could corrupt** mid-write and crash-loop the bot with positions open. Writes
   are now atomic with a backup copy; if both are unreadable it refuses to start and pings you.
10. **No limits.** New: max 10 buys a day, stop buying after 0.6 SOL realized loss in a day, a hard
    stop on the live price at −50% (rugs don't wait for a candle close), and a `PAUSE` file.
11. **~500k chart requests a month** for stop confirmation (3 positions × every 15s). The bot now
    builds its 1-minute closes from its own price checks.
12. Smaller: Telegram HTML is escaped (a `<` in a token name or error used to drop the message),
    the RPC URL and bot token are kept out of error logs, and one bad position no longer stops the
    others' stops.

## Run it

```bash
cd bots/solbot
python3 -m venv venv && . venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # dry run needs nothing in it; add Telegram for pings
python bot.py                 # dry run, buying the feed's alerts (FEED_DB points at its database)
python bot.py report          # closed trades, wins, P&L in SOL
python -m pytest -q
```

On the VM, `alerts/sniper-feed/deploy/deploy-vm.sh` runs it next to the feed (in dry run) as the
`solbot` service, with its state backed up hourly. See the feed's README.

## Before going live

- Let the dry run (and the feed's "bot exits" track record) run for a couple of weeks. If
  `report` and the track record aren't positive after fees, don't go live.
- Use a **burner wallet** funded with what you'd accept losing entirely. Never your main wallet.
- Use your own RPC (Helius, Triton or QuickNode free tier); the public one drops transactions.
- Live needs `SOLBOT_LIVE=yes`, `WALLET_PRIVATE_KEY` and `RPC_URL`. On the VM:
  `BOT_LIVE=yes ./deploy/deploy-vm.sh` asks for the key and RPC URL (stored in Secret Manager)
  and makes you type LIVE. A plain re-run goes back to dry run.
- A −25% stop on a token that just passed a 5-minute volume filter sits inside normal noise:
  expect stop-outs before TP1.
