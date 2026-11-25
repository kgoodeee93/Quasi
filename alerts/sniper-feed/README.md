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

A check with no data fails. Moby safety tier red, tokenized stocks and anything RugCheck calls
rugged are never alerted. "Bundled supply" is RugCheck's linked-wallet networks, the closest free
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
python -m sniper_feed stats                   # the track record: median / mean return, win rate per horizon
python -m pytest -q
```

Alerts and outcomes live in `FEED_DB` (default `feed.db`, never committed). An outcome is only
recorded near its mark (up to 50% late plus 5 minutes); a missed mark stays missing instead of being
filled with a later price.

`run` needs to stay up and keep its database: a small always-on VM (for example a GCE e2-micro
with a systemd service) is the simplest home. Cloud Run's disk is wiped on restart.

## Selling it

1. **Prove it first (2–4 weeks).** Run `run --send` into a private channel. Publish `stats` as-is,
   including the losers. If the 1-hour median is negative, change the rules, not the record.
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
