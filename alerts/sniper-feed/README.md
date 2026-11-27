# Sniper feed

An 11-check momentum screen for Solana tokens that posts passing tokens to a Telegram channel and
records what each one's price did 15 minutes, 1 hour, 4 hours and 24 hours later. That record is
the product: nobody pays for alerts without one.

The same screen also ships as a **Moby AI Trading strategy** (`strategy/moby-strategy.md`) that
people paste into their own agent.

## The screen (`rules.json`)

| Check | Limit | Source |
|---|---|---|
| Volume, 5 min | ≥ $15,000 | GeckoTerminal |
| Transactions, 5 min | ≥ 50 | GeckoTerminal |
| Buys, 5 min | ≥ 30 | GeckoTerminal |
| Unique buyers, 5 min | ≥ 25 | GeckoTerminal |
| Buys ÷ sells, 5 min | ≥ 1.5 | GeckoTerminal |
| Top 10 holders | ≤ 25% | Moby |
| Dev holding | ≤ 5% | Moby, RugCheck if Moby has none |
| Sniper holding | ≤ 15% | Moby |
| Bundled supply | ≤ 5% | RugCheck: supply held by wallets linked by transfers |
| Mint authority | disabled | Moby, RugCheck if Moby has none |
| Freeze authority | disabled | Moby, RugCheck if Moby has none |

A check with no data fails. Moby safety tier red, tokenized stocks, anything RugCheck calls
rugged, and tokens with buys but not one sell in the last 5 minutes (a possible honeypot) are
never alerted. "Bundled supply" is RugCheck's linked-wallet networks, the closest free
measure of same-block bundles; it can read a little high for tokens with active market makers.

Each scan costs 3 GeckoTerminal calls for the candidates (trending pages 1–2 and new pools). Only tokens
that pass the five 5-minute checks get Moby and RugCheck lookups, at most 8 per scan.

## Run it

```bash
cd alerts/sniper-feed
pip install -r requirements-dev.txt
export MOBY_API_KEY=...                       # in a Claude cloud session the proxy injects it
python -m sniper_feed scan                    # one scan: prints alerts and near misses, sends nothing
python -m sniper_feed run --every 60          # scan + record outcomes forever, print only
python -m sniper_feed run --every 60 --send   # ...and post to Telegram (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
python -m sniper_feed stats                   # the track record (below)
python -m sniper_feed backup --to copy.db     # safe copy while `run` is writing
python -m pytest -q
```

Set `COINGECKO_API_KEY` to use a paid CoinGecko plan (same data, commercial licence, higher limits)
instead of the free GeckoTerminal API. Rate-limited calls are retried after 10s and 20s.

## The track record

Everything lives in `FEED_DB` (default `feed.db`, never committed). `stats` reports:

- **alerts**: tokens that passed every check and were posted.
- **misses**: tokens that passed the 5-minute activity checks but failed a safety check. Never
  posted, tracked the same way, at most once per token every 6 hours.
- **only_failed**: misses that failed exactly one rule, grouped by that rule. If the misses that only
  failed `bundled_max_pct` beat the alerts, the bundle cap is costing you winners. Change rules on
  this evidence, not on one token.

For each, two kinds of outcome:

- **Spot returns** at 15m, 1h, 4h and 24h. Only recorded near the mark (up to 50% late plus 5
  minutes); a missed mark stays missing instead of being filled with a later price.
- **Exit replays** (`--tp 50 --sl 25` by default): once 24 hours have passed, the 5-minute candles
  are replayed to see whether the price reached the take-profit or the stop-loss first. A candle
  that touches both counts as the stop. `avg_trade_pct` is the average result per trade at those
  exits, which is what a subscriber trading every alert would have made before fees and slippage.

## Deploy (always-on VM)

`run` has to stay up and keep its database, so it runs on a small Compute Engine VM rather than
Cloud Run, whose disk is wiped on restart. From Google Cloud Shell, after this code is on `main`:

```bash
cd alerts/sniper-feed && ./deploy/deploy-vm.sh
```

The first run asks for your Moby API key, Telegram bot token and channel id (typed, never echoed),
stores them in Secret Manager, and sends a test message to the channel. It creates:

- an e2-micro VM (the free-tier size in us-central1, us-east1 and us-west1; its external IP may cost
  a few dollars a month), running the feed as a locked-down `sniper` user under systemd;
- a service account that can read only those secrets and write only its backup bucket;
- a private bucket with an hourly copy of the database (`feed.db`) and one per day (`daily/`). A
  rebuilt VM restores from the latest copy, so the track record survives.

Re-run it to deploy new code. `SEND=0` records without posting; `REF=branch` deploys a branch.

**Telegram setup:** create a bot with @BotFather, create a private channel, add the bot as an
admin, post one message in the channel, then open
`https://api.telegram.org/bot<token>/getUpdates` in your browser: the channel id is the
`chat.id` starting with `-100`.

## Selling it

1. **Prove it first (2–4 weeks).** Run it into a private channel. Publish `stats` as-is, including
   the losers. If `avg_trade_pct` is negative, change the rules (using `only_failed`), not the record.
2. **Free channel, delayed.** Post each alert 15–30 minutes late with its outcome so far. This is the
   marketing.
3. **Paid channel, real time.** Telegram channel subscriptions (Stars) or Whop / LaunchPass for card
   payments. Typical range for crypto alert channels: $20–80/month.
4. **Moby strategy pack.** Sell `strategy/moby-strategy.md` plus a setup guide as a one-time download,
   with one of your `AGENT-` invite codes per buyer: Moby pays you 10% of each referred user's
   trading fees.

## Before charging anyone

- **Data licences.** GeckoTerminal's keyless API is free with a link back, but CoinGecko advises against
  keyless endpoints in production, and its paid plans carry the commercial licence. RugCheck's and
  Moby's terms on paid redistribution weren't found; ask both in writing before launch.
- **Not advice.** Every alert carries a not-financial-advice line. Don't promise returns; show the
  full record.
- **No securities.** Tokenized stocks are excluded on purpose: paid signals on securities can need
  regulatory registration.
