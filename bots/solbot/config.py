"""
All the knobs live here. This is the only file you edit to change strategy.
Nothing in this file talks to the network: it's configuration, plus a few environment
switches so the same code runs in dry run and live without editing anything in git.
"""
import os

# ---------------------------------------------------------------------------
# LIVE OR DRY RUN
# ---------------------------------------------------------------------------
# Dry run unless the environment says SOLBOT_LIVE=yes. Dry run simulates every buy and sell at
# the live Jupiter price (minus DRY_RUN_SLIPPAGE_PCT), records the result, and signs nothing; it
# needs no wallet key. Going live also needs WALLET_PRIVATE_KEY and RPC_URL.
DRY_RUN = os.environ.get("SOLBOT_LIVE", "").strip().lower() != "yes"
DRY_RUN_SLIPPAGE_PCT = 1.0     # each simulated fill is this much worse than the quoted price

# ---------------------------------------------------------------------------
# SIZING & RUNTIME  (set these before you go live)
# ---------------------------------------------------------------------------

POSITION_SIZE_SOL = 0.20      # <-- SOL spent per entry. SET THIS to your size.
MAX_OPEN_POSITIONS = 3        # <-- most positions open at once. SET THIS.

TICK_SECONDS = 15             # how often the manage loop checks open positions
SCAN_EVERY_TICKS = 4          # scan for new entries every Nth tick (~60s here)
CANDIDATES_PER_SCAN = 25      # how many trending tokens to vet per scan (solanatracker source)
RECHECK_MINUTES = 15          # a rejected token is looked at again after this long
SLIPPAGE_BPS = 300            # 3% slippage cap on exit sells (memecoins are thin)
BUY_SLIPPAGE_BPS = 500        # 5% on entries

STATE_DIR = os.environ.get("SOLBOT_STATE_DIR", ".")
STATE_FILE = os.path.join(STATE_DIR, "state.json")      # positions + counters, survives restarts
JOURNAL_FILE = os.path.join(STATE_DIR, "trades.jsonl")  # every fill, dry or live: the P&L record
PAUSE_FILE = os.path.join(STATE_DIR, "PAUSE")           # create this file to stop new entries

# Solana wrapped-SOL mint (the input token for every buy).
SOL_MINT = "So11111111111111111111111111111111111111112"

# ---------------------------------------------------------------------------
# SAFETY LIMITS  (new: the bot stops opening positions when one is hit; exits keep running)
# ---------------------------------------------------------------------------

MAX_BUYS_PER_DAY = 10          # entries per UTC day
MAX_DAILY_LOSS_SOL = 0.60      # realized loss per UTC day (3 full stop-outs at 0.20 SOL)
HARD_STOP_PCT = 0.50           # live price this far below entry sells everything at once, without
                               # waiting for a candle close: rugs don't wait either

# ---------------------------------------------------------------------------
# ENTRIES
# ---------------------------------------------------------------------------
# "feed": buy the sniper feed's alerts (alerts/sniper-feed), read from its database. One screen,
#         one track record, no Solana Tracker plan needed.
# "solanatracker": scan Solana Tracker's trending list with ENTRY_FILTERS below (Advanced plan).
ENTRY_SOURCE = os.environ.get("ENTRY_SOURCE", "feed").strip().lower()
FEED_DB = os.environ.get("FEED_DB", "../../alerts/sniper-feed/feed.db")
FEED_MAX_AGE_SECONDS = 180     # never buy an alert older than this (e.g. after the bot was down)

# A token must pass ALL of these (solanatracker source). A missing number fails the filter.
ENTRY_FILTERS = {
    "min_volume_5m_usd":   15_000,   # 5m USD volume
    "min_transactions_5m": 50,       # 5m total transactions
    "min_buys_5m":         30,       # 5m buys
    "min_buyers_5m":       25,       # 5m unique buyers
    "min_buy_sell_ratio":  1.5,      # 5m buys / sells
    "max_top10_pct":       25.0,     # top-10 holders % of supply
    "max_dev_pct":         5.0,      # dev holding %
    "max_sniper_pct":      15.0,     # sniper holding %
    "max_bundled_pct":     2.0,      # bundled supply % — hard cap: bundlers dump into buys
    "require_mint_disabled":   True,
    "require_freeze_disabled": True,
}


# ---------------------------------------------------------------------------
# EXIT LADDER
# ---------------------------------------------------------------------------

# Take-profits. sell_pct is a fraction of the ORIGINAL position (they sum to 1.0).
# Weighted to the top so winners keep most of the bag running into 8x-10x.
TAKE_PROFITS = [
    {"mult": 2.0,  "sell_pct": 0.15},
    {"mult": 4.0,  "sell_pct": 0.25},
    {"mult": 8.0,  "sell_pct": 0.25},
    {"mult": 10.0, "sell_pct": 0.35},   # skipped if TRAIL_RUNNER is on (see below)
]

# Early-weakness stops, used ONLY before TP1 has hit. Two steps: a partial bail,
# then a full exit. By the second stop you are flat ("out within 2 SLs").
# drop_pct is measured from your entry price.
EARLY_STOPS = [
    {"drop_pct": 0.25, "sell_pct": 0.50},   # SL1: price -25% -> sell half
    {"drop_pct": 0.40, "sell_pct": 1.00},   # SL2: price -40% -> sell the rest
]

# Profit-protect ratchet, used AFTER TP1. Once you're in profit the stop climbs
# with each take-profit, and a breach sells 100% of whatever is left.
# Keyed by the highest TP index hit (1 = TP1 done, 2 = TP2 done, ...).
# Value is the price multiple (vs entry) the stop sits at.
RATCHET_STOP_MULT = {
    1: 1.0,   # after TP1 -> breakeven
    2: 2.0,   # after TP2 -> lock 2x
    3: 4.0,   # after TP3 -> lock 4x  (or trail, see below)
}

# Let the final tranche run instead of hard-selling at 10x. Once TP3 (8x) hits,
# the remaining bag exits on a trailing stop TRAIL_PCT below its peak, so a
# 15x-20x move isn't capped. This is the biggest "more on winners" lever.
TRAIL_RUNNER = True
TRAIL_PCT = 0.30              # trail 30% off the high-water mark

# Stops confirm on a closed 1-minute close, not an intrabar wick, so a quick
# dip-and-recover doesn't stop you out. Take-profits ignore this (they fire on
# the live price so you sell into the spike). The bot builds the 1-minute closes
# itself from its own 15-second price checks (no chart API calls).
STOP_CONFIRM_1M_CLOSE = True
