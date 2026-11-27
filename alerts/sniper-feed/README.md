# Sniper feed

An 11-check momentum screen for Solana tokens that posts passing tokens to a Telegram channel and
records what each one's price did 15 minutes, 1 hour, 4 hours and 24 hours later. That record is
the product: nobody pays for alerts without one.

The same screen also ships as a **Moby AI Trading strategy** (`strategy/moby-strategy.md`) that
people paste into their own agent, and drives the **trading bot** in `bots/solbot`, which buys
the alerts (in dry run until you switch it live). The public **track-record page** for selling it
lives in `web/track-record` (Vercel).

## The screen (`rules.json`)

| Check | Limit | Source |
|---|---|---|
| Volume, 5 min | ≥ $15,000 | GeckoTerminal |
| Transactions, 5 min | ≥ 50 | GeckoTerminal |
| Buys, 5 min | ≥ 30 | GeckoTerminal |
| Unique buyers, 5 min | ≥ 25 | GeckoTerminal |
| Buys ÷ sells, 5 min | ≥ 1.5 | GeckoTerminal |
| Top 10 holders | ≤ 25% | Moby, Solana Tracker |
| Dev holding | ≤ 5% | Moby, RugCheck, Solana Tracker |
| Sniper holding | ≤ 15% | Moby, Solana Tracker |
| Bundled supply | ≤ 5% | Solana Tracker's bundlers; without a key, RugCheck's linked wallets |
| Mint authority | disabled | Moby, RugCheck, Solana Tracker |
| Freeze authority | disabled | Moby, RugCheck, Solana Tracker |

Solana Tracker is optional (`SOLANA_TRACKER_API_KEY`). Where sources disagree, the worse number
counts. A check with no data fails. Moby safety tier red, tokenized stocks, anything RugCheck calls
rugged, and tokens with buys but not one sell in the last 5 minutes (a possible honeypot) are
never alerted. Without Solana Tracker, "bundled supply" is RugCheck's linked-wallet networks, the
closest free measure of same-block bundles; it can read a little high for tokens with active
market makers.

Moby often has no sniper figure for a token, and missing data fails, so **without a Solana
Tracker key expect few alerts**. With one, about 1–2 extra requests per deep-checked token
(roughly 30k a month: the Advanced plan's 200k covers it).

Each scan costs 3 GeckoTerminal calls for the candidates (trending pages 1–2 and new pools). Only tokens
that pass the five 5-minute checks get the safety lookups, at most 8 per scan.

## Run it

```bash
cd alerts/sniper-feed
pip install -r requirements-dev.txt
export MOBY_API_KEY=...                       # in a Claude cloud session the proxy injects it
python -m sniper_feed scan                    # one scan: prints alerts and near misses, sends nothing
python -m sniper_feed run --every 60          # scan + record outcomes forever, print only
python -m sniper_feed run --every 60 --send   # ...and post to Telegram (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
python -m sniper_feed stats                   # the track record (below)
python -m sniper_feed publish --to stats.json # the public version, for the website (alerts only, 24h delay)
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
- **Exit replays**: once 24 hours have passed, the 5-minute candles are replayed twice:
  - `tp_sl` (`--tp 50 --sl 25`): did the price reach the take-profit or the stop-loss first? A
    candle that touches both counts as the stop.
  - `ladder` (`ladder.json`, the bot's exits): partial take-profits on the highs, stops on the
    closes, the ratchet and the trailing runner, in the bot's order. A candle holding both a spike
    and a −50% rug counts as the rug.

  `avg_trade_pct` is the average result per trade at those exits: what a subscriber, or the bot,
  would have made on every alert before fees and slippage.

## Deploy (always-on VM)

`run` has to stay up and keep its database, so it runs on a small Compute Engine VM rather than
Cloud Run, whose disk is wiped on restart. From Google Cloud Shell, after this code is on `main`:

```bash
cd alerts/sniper-feed && ./deploy/deploy-vm.sh
```

The first run asks for your Moby API key, Telegram bot token and channel id (typed, never echoed),
stores them in Secret Manager, and sends a test message to the channel. Optional prompts (Enter to
skip): a paid CoinGecko key, a Solana Tracker key, a private Telegram chat for the bot's trade pings
(never the subscribers' channel), and a Jupiter key. It creates:

- an e2-micro VM (the free-tier size in us-central1, us-east1 and us-west1; its external IP may cost
  a few dollars a month), running the feed and the trading bot (**dry run**) as a locked-down
  `sniper` user under systemd;
- a service account that can read only those secrets and write only its buckets;
- a private bucket with an hourly copy of the database (`feed.db`), one per day (`daily/`), and the
  bot's positions and trade journal (`solbot/`). A rebuilt VM restores from the latest copies;
  older copies are kept for about a week;
- a public bucket holding only `stats.json`, the delayed public track record for the website,
  refreshed hourly (`PUBLISH=0` to skip).

Re-run it to deploy new code. `SEND=0` records without posting, `BOT=0` leaves the bot off,
`REF=branch` deploys a branch. **The bot is in dry run on every deploy** unless that deploy says
`BOT_LIVE=yes`, which asks for the burner wallet's key and an RPC URL and makes you type LIVE.

**Telegram setup:** create a bot with @BotFather, create a private channel, add the bot as an
admin, post one message in the channel, then open
`https://api.telegram.org/bot<token>/getUpdates` in your browser: the channel id is the
`chat.id` starting with `-100`.

## Selling it

1. **Prove it first (2–4 weeks).** Run it into a private channel. The public page
   (`web/track-record`) shows every alert as-is, losers included. If `avg_trade_pct` is negative,
   change the rules (using `only_failed`), not the record.
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
