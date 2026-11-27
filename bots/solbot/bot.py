"""
Solana memecoin trading bot.

Flow:
  1. SCAN  -> new entries: the sniper feed's alerts (ENTRY_SOURCE=feed), or Solana Tracker's
              trending list filtered by ENTRY_FILTERS (ENTRY_SOURCE=solanatracker).
  2. BUY   -> spend POSITION_SIZE_SOL on each new pick; the entry price comes from the fill.
  3. MANAGE-> on every tick, check each open position for take-profits (live
              price) and stops (confirmed on a 1-minute close), plus a hard stop on the live
              price. The stop climbs the ladder as take-profits hit.

Dry run unless SOLBOT_LIVE=yes: every buy and sell is simulated at the live price and written
to the trade journal, so the dry run itself shows whether the strategy makes money.
`python bot.py report` prints the result so far.

Not financial advice. Memecoins are extremely high risk; you can lose the
entire position on any trade. You are responsible for every parameter here.
"""

import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()  # before config and notify: both read the environment

import config  # noqa: E402
import notify  # noqa: E402
from clients import Chain, FeedSource, Jupiter, SolanaTracker, load_keypair  # noqa: E402
from notify import esc  # noqa: E402

FAIL_NOTIFY_EVERY = 300  # seconds between repeat "sell failed" pings for the same position


def num(v):
    """A JSON number as float, else None. Booleans and strings are not numbers here."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


# ---------------------------------------------------------------------------
# Position state
# ---------------------------------------------------------------------------
@dataclass
class Position:
    mint: str
    symbol: str
    entry_price: float          # USD per token; 0 = bought but not priced yet (set next tick)
    tokens_total: int           # base units bought
    tokens_remaining: int
    high_water: float           # highest price seen (for the trailing runner)
    tp_hits: list = field(default_factory=list)   # indices of TPs already taken
    sl1_done: bool = False      # early partial stop already fired
    decimals: int = 6
    cost_sol: float = 0.0       # SOL spent on the entry
    proceeds_sol: float = 0.0   # SOL received from sells so far
    opened_at: float = 0.0

    def highest_tp(self):
        return max(self.tp_hits) + 1 if self.tp_hits else 0  # 1-based, 0 = none

    @classmethod
    def from_dict(cls, d):
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


class MinuteCloses:
    """1-minute closes built from the bot's own price checks: the last price seen in each
    wall-clock minute. Same idea as a closed 1m candle, with no chart API call per position
    per tick (that alone would be ~500k Solana Tracker requests a month at 3 positions)."""

    def __init__(self):
        self._current = {}   # mint -> (minute, last price seen in it)
        self._closed = {}    # mint -> close of the most recent finished minute

    def record(self, mint, price, now):
        minute = int(now // 60)
        cur = self._current.get(mint)
        if cur and cur[0] != minute:
            self._closed[mint] = cur[1]
        self._current[mint] = (minute, price)

    def last_close(self, mint):
        return self._closed.get(mint)

    def forget(self, mint):
        self._current.pop(mint, None)
        self._closed.pop(mint, None)


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------
class Bot:
    def __init__(self, st=None, jup=None, chain=None, feed=None, clock=time.time, sleep=time.sleep):
        self.now, self.sleep = clock, sleep
        if not config.DRY_RUN and (jup is None or chain is None):
            missing = [n for n in ("WALLET_PRIVATE_KEY", "RPC_URL") if not os.environ.get(n, "").strip()]
            if missing:
                raise SystemExit(f"Live trading needs {' and '.join(missing)}. "
                                 "Unset SOLBOT_LIVE to run in dry run.")
        kp = None
        if not config.DRY_RUN and jup is None:
            kp = load_keypair(os.environ["WALLET_PRIVATE_KEY"].strip())
        self.jup = jup or Jupiter(os.environ.get("JUPITER_API_KEY", "").strip(), kp)
        self.chain = chain
        if self.chain is None and not config.DRY_RUN:
            self.chain = Chain(os.environ["RPC_URL"].strip(), self.jup.wallet)

        self.st, self.feed = st, feed
        if config.ENTRY_SOURCE == "solanatracker":
            if self.st is None:
                key = os.environ.get("SOLANA_TRACKER_API_KEY", "").strip()
                if not key:
                    raise SystemExit("ENTRY_SOURCE=solanatracker needs SOLANA_TRACKER_API_KEY")
                self.st = SolanaTracker(key)
        elif config.ENTRY_SOURCE == "feed":
            self.feed = self.feed or FeedSource(config.FEED_DB)
        else:
            raise SystemExit(f"ENTRY_SOURCE must be 'feed' or 'solanatracker', not {config.ENTRY_SOURCE!r}")

        self.closes = MinuteCloses()
        self._sol_usd_cache = None
        self._fail_notified = {}
        self._load_state()

    # ---- persistence -----------------------------------------------------
    def _today(self):
        return datetime.fromtimestamp(self.now(), timezone.utc).date().isoformat()

    def _load_state(self):
        self.positions, self.seen, self.traded = {}, {}, set()
        self.day, self.day_buys, self.day_pnl_sol = self._today(), 0, 0.0
        self.feed_last_id, self.limit_notified = None, ""
        legacy = os.path.join(config.STATE_DIR, "positions.json")  # the original bot's file
        candidates = [config.STATE_FILE, config.STATE_FILE + ".bak", legacy]
        for path in candidates:
            if not os.path.exists(path):
                continue
            try:
                with open(path) as f:
                    data = json.load(f)
                if "positions" not in data:
                    data = {"positions": data}
                positions = {m: Position.from_dict(p) for m, p in data["positions"].items()}
            except (OSError, ValueError, TypeError, AttributeError) as e:
                print(f"[state] {path} unreadable: {e}")
                continue
            self.positions = positions
            self.seen = {m: float(t) for m, t in (data.get("seen") or {}).items()}
            self.traded = set(data.get("traded") or []) | set(positions)
            if data.get("day") == self.day:
                self.day_buys = int(data.get("day_buys") or 0)
                self.day_pnl_sol = float(data.get("day_pnl_sol") or 0)
            self.feed_last_id = data.get("feed_last_id")
            if path != config.STATE_FILE:
                print(f"[state] loaded {path}")
            return
        if any(os.path.exists(p) for p in candidates):
            msg = (f"{config.STATE_FILE} and its backup are unreadable. Refusing to start so open "
                   "positions aren't forgotten: check the wallet, then fix or remove the file.")
            notify.send("⚠️ <b>Bot not started</b>\n" + esc(msg))
            raise SystemExit(msg)

    def _save_state(self):
        cutoff = self.now() - config.RECHECK_MINUTES * 60
        self.seen = {m: t for m, t in self.seen.items() if t >= cutoff}
        data = {"positions": {m: asdict(p) for m, p in self.positions.items()},
                "seen": self.seen, "traded": sorted(self.traded), "day": self.day,
                "day_buys": self.day_buys, "day_pnl_sol": self.day_pnl_sol,
                "feed_last_id": self.feed_last_id}
        tmp = config.STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        # Keep the previous good copy: a crash between these two steps still leaves one to load.
        if os.path.exists(config.STATE_FILE):
            os.replace(config.STATE_FILE, config.STATE_FILE + ".bak")
        os.replace(tmp, config.STATE_FILE)

    def _journal(self, **row):
        row = {"ts": datetime.fromtimestamp(self.now(), timezone.utc).isoformat(),
               "dry_run": config.DRY_RUN, **row}
        with open(config.JOURNAL_FILE, "a") as f:
            f.write(json.dumps(row) + "\n")

    # ---- limits ----------------------------------------------------------
    def _roll_day(self):
        today = self._today()
        if today != self.day:
            self.day, self.day_buys, self.day_pnl_sol, self.limit_notified = today, 0, 0.0, ""

    def entry_block(self):
        """Why no new position may be opened right now, or None."""
        if os.path.exists(config.PAUSE_FILE):
            return "paused (PAUSE file present)"
        if len(self.positions) >= config.MAX_OPEN_POSITIONS:
            return "max open positions"
        if self.day_buys >= config.MAX_BUYS_PER_DAY:
            return f"daily buy limit ({config.MAX_BUYS_PER_DAY}) reached"
        if self.day_pnl_sol <= -config.MAX_DAILY_LOSS_SOL:
            return f"daily loss limit ({config.MAX_DAILY_LOSS_SOL} SOL) reached"
        return None

    def _sol_usd(self):
        if self._sol_usd_cache is None:
            self._sol_usd_cache = num(self.jup.price(config.SOL_MINT))
        return self._sol_usd_cache

    # ---- entry filter (solanatracker source) -----------------------------
    def passes_filters(self, item):
        """(symbol, decimals, None) on a pass, or (None, None, reason) on a reject.
        A number Solana Tracker doesn't return is a reject, never a pass."""
        tok = item.get("token", item) if isinstance(item, dict) else {}
        mint = tok.get("mint") or tok.get("address") or (item or {}).get("mint")
        # Trending rows are full token records already; only fetch when one isn't.
        info = item if isinstance(item.get("risk"), dict) else self.st.token_info(mint)
        if not info:
            return None, None, "no token info"
        risk = info.get("risk") or {}
        token = info.get("token") or {}
        f = config.ENTRY_FILTERS

        def reject(reason):
            return None, None, reason

        # --- holder / authority gate (from the token record: no extra request) ---
        top10 = num(risk.get("top10"))
        if top10 is None:
            return reject("no top-10 data")
        if top10 > f["max_top10_pct"]:
            return reject(f"top10 {top10:.1f}% > {f['max_top10_pct']}%")
        dev = num((risk.get("dev") or {}).get("percentage"))
        if dev is None:
            return reject("no dev-holding data")
        if dev > f["max_dev_pct"]:
            return reject(f"dev {dev:.1f}% > {f['max_dev_pct']}%")
        snipers = num((risk.get("snipers") or {}).get("totalPercentage"))
        if snipers is None:
            return reject("no sniper data")
        if snipers > f["max_sniper_pct"]:
            return reject(f"snipers {snipers:.1f}% > {f['max_sniper_pct']}%")

        # The docs show risks both as strings and as {name, ...} objects: accept either.
        risk_names = {r.get("name") if isinstance(r, dict) else str(r) for r in risk.get("risks") or []}
        if risk.get("rugged") or "Rugged" in risk_names:
            return reject("flagged rugged")
        pools = [p for p in info.get("pools") or [] if isinstance(p, dict)]
        pool = max(pools, key=lambda p: num((p.get("liquidity") or {}).get("usd")) or 0, default=None)
        security = (pool or {}).get("security")
        if f["require_mint_disabled"] or f["require_freeze_disabled"]:
            if not isinstance(security, dict):
                return reject("no mint/freeze authority data")
            if f["require_mint_disabled"] and (security.get("mintAuthority") or "Mint Authority Enabled" in risk_names):
                return reject("mint authority enabled")
            if f["require_freeze_disabled"] and (security.get("freezeAuthority") or "Freeze Authority Enabled" in risk_names):
                return reject("freeze authority enabled")

        # --- 5-minute activity gate (1 request) ---
        s = self.st.stats_5m(mint)
        if not isinstance(s, dict):
            return reject("no 5m stats")
        buys, sells, buyers, txns = (num(s.get(k)) for k in ("buys", "sells", "buyers", "transactions"))
        vol = num((s.get("volume") or {}).get("total"))
        if None in (buys, sells, buyers, txns, vol):
            return reject("incomplete 5m stats")
        if vol < f["min_volume_5m_usd"]:
            return reject(f"vol ${vol:,.0f} < ${f['min_volume_5m_usd']:,}")
        if txns < f["min_transactions_5m"]:
            return reject(f"txns {txns:.0f} < {f['min_transactions_5m']}")
        if buys < f["min_buys_5m"]:
            return reject(f"buys {buys:.0f} < {f['min_buys_5m']}")
        if buyers < f["min_buyers_5m"]:
            return reject(f"buyers {buyers:.0f} < {f['min_buyers_5m']}")
        if sells == 0:
            return reject("no sells in 5m (thin, or a honeypot)")
        if (buys / sells) < f["min_buy_sell_ratio"]:
            return reject(f"buy/sell {buys / sells:.2f} < {f['min_buy_sell_ratio']}")

        # --- bundlers last: the dedicated endpoint is the reliable number (1 request, finalists only)
        bundled = num((risk.get("bundlers") or {}).get("totalPercentage"))
        if bundled is None or bundled <= f["max_bundled_pct"]:
            confirmed = self.st.bundlers(mint)
            if confirmed is not None:
                bundled = confirmed
        if bundled is None:
            return reject("no bundler data")
        if bundled > f["max_bundled_pct"]:
            return reject(f"bundled {bundled:.1f}% > {f['max_bundled_pct']}%")

        decimals = token.get("decimals") if isinstance(token.get("decimals"), int) else None
        return token.get("symbol") or mint[:6], decimals, None

    # ---- buy -------------------------------------------------------------
    def open_position(self, mint, symbol, decimals=None):
        size = config.POSITION_SIZE_SOL
        lamports = int(size * 1e9)
        print(f"  BUY  {symbol} ({mint}) with {size} SOL" + (" [dry run]" if config.DRY_RUN else ""))
        sol_usd = self._sol_usd()
        info = self.jup.price_info([mint]).get(mint) or {}
        price = num(info.get("usdPrice"))
        dec = info.get("decimals") if isinstance(info.get("decimals"), int) else decimals

        if config.DRY_RUN:
            if not price or not sol_usd or dec is None:
                print("    no Jupiter price yet; skipped")
                return
            fill = price * (1 + config.DRY_RUN_SLIPPAGE_PCT / 100)
            tokens = int(size * sol_usd / fill * 10 ** dec)
            if tokens > 0:
                self._add_position(mint, symbol, fill, tokens, dec, size, None)
            return

        res = self.jup.swap(config.SOL_MINT, mint, lamports, config.BUY_SLIPPAGE_BPS)
        if not res["ok"]:
            print(f"    buy failed: {res['error']}")
            if res.get("sent"):
                # The signed transaction reached Jupiter, so it may still land. Check before
                # walking away, or the tokens would sit in the wallet with no stop at all.
                self.sleep(5)
                held = self.chain.token_balance(mint)
                if held:
                    print("    ...but the tokens arrived; managing the position")
                    self._add_position(mint, symbol, price or 0.0, held, dec or 6, size, None,
                                       note="buy reported failed but the tokens arrived")
                    return
                if held is None:
                    notify.send(f"⚠️ <b>BUY {esc(symbol)}: CHECK THE WALLET</b>\nThe buy reported "
                                f"{esc(res['error'])} and the balance check failed too.\n<code>{esc(mint)}</code>")
                    self.traded.add(mint)  # don't buy it again on top of a possible fill
                    self._save_state()
                    return
            notify.send(f"⚠️ <b>BUY {esc(symbol)} FAILED</b>\n{esc(res['error'])}")
            return

        tokens = res["out_amount"]
        entry = 0.0  # unknown: set from the first live price (see manage)
        if dec is not None and tokens > 0 and sol_usd:
            entry = (res["in_amount"] / 1e9 * sol_usd) / (tokens / 10 ** dec)  # what we paid per token
        elif price:
            entry = price
        self._add_position(mint, symbol, entry, tokens, dec if dec is not None else 6,
                           res["in_amount"] / 1e9 or size, res.get("signature"))

    def _add_position(self, mint, symbol, entry, tokens, decimals, cost_sol, signature, note=""):
        pos = Position(mint=mint, symbol=symbol, entry_price=entry, tokens_total=tokens,
                       tokens_remaining=tokens, high_water=entry, decimals=decimals,
                       cost_sol=cost_sol, opened_at=self.now())
        self.positions[mint] = pos
        self.traded.add(mint)
        self.day_buys += 1
        self._save_state()
        self._journal(side="buy", mint=mint, symbol=symbol, tokens=tokens, price_usd=entry,
                      sol=-cost_sol, signature=signature, note=note)
        dry = " <i>(dry run)</i>" if config.DRY_RUN else ""
        tx = f"<a href='https://solscan.io/tx/{esc(signature)}'>tx</a> · " if signature else ""
        print(f"    filled: {tokens} base units @ ${entry:.8g}" + (f"  tx={signature}" if signature else ""))
        notify.send(f"🟢 <b>BUY {esc(symbol)}</b>{dry}\n{cost_sol:g} SOL @ ${entry:.8g}"
                    + (f"\n{esc(note)}" if note else "") + f"\n{tx}<code>{esc(mint)}</code>")

    # ---- sell ------------------------------------------------------------
    def sell(self, pos: Position, fraction, reason, price):
        """Sell `fraction` of the original position (1.0 = everything left).
        True when done (sold, or nothing left to sell); False when it failed: retry next tick."""
        if fraction >= 1.0:
            amount = pos.tokens_remaining
            if not config.DRY_RUN:
                onchain = self.chain.token_balance(pos.mint)
                if onchain == 0:
                    print(f"  {pos.symbol}: the wallet holds none of it; closing the position")
                    notify.send(f"⚪️ <b>{esc(pos.symbol)}</b>: no tokens left in the wallet; position closed")
                    pos.tokens_remaining = 0
                    return True
                if onchain:
                    amount = onchain   # sweep real balance on full exits
        else:
            amount = min(int(pos.tokens_total * fraction), pos.tokens_remaining)
        if amount <= 0:
            return True

        print(f"  SELL {pos.symbol}  {int(fraction * 100)}%  ({reason})")
        emoji = "🟢" if reason.startswith("TP") else "🔴"
        if config.DRY_RUN:
            fill = price * (1 - config.DRY_RUN_SLIPPAGE_PCT / 100)
            sol_usd = self._sol_usd()
            got = amount / 10 ** pos.decimals * fill / sol_usd if sol_usd else 0.0
            pos.tokens_remaining -= amount
            pos.proceeds_sol += got
            self._journal(side="sell", mint=pos.mint, symbol=pos.symbol, tokens=amount,
                          price_usd=fill, sol=got, reason=reason)
            notify.send(f"{emoji} <b>SELL {esc(pos.symbol)}</b> {int(fraction * 100)}% "
                        f"· {esc(reason)} · +{got:.4f} SOL <i>(dry run)</i>")
            return True

        res = self.jup.swap(pos.mint, config.SOL_MINT, amount, config.SLIPPAGE_BPS)
        if not res["ok"]:
            print(f"    sell failed: {res['error']}")
            key = (pos.mint, reason)
            if self.now() - self._fail_notified.get(key, 0) >= FAIL_NOTIFY_EVERY:
                self._fail_notified[key] = self.now()
                notify.send(f"⚠️ <b>SELL {esc(pos.symbol)} FAILED</b> · {esc(reason)}\n"
                            f"{esc(res['error'])}\nRetrying every tick.")
            return False
        pos.tokens_remaining = max(0, pos.tokens_remaining - amount)
        got = res["out_amount"] / 1e9
        pos.proceeds_sol += got
        self._journal(side="sell", mint=pos.mint, symbol=pos.symbol, tokens=amount,
                      price_usd=price, sol=got, reason=reason, signature=res.get("signature"))
        print(f"    sold for {got:.4f} SOL  tx={res['signature']}")
        notify.send(
            f"{emoji} <b>SELL {esc(pos.symbol)}</b> {int(fraction * 100)}% · {esc(reason)}\n"
            f"+{got:.4f} SOL · "
            f"<a href='https://solscan.io/tx/{esc(res['signature'])}'>tx</a>")
        return True

    # ---- manage one position --------------------------------------------
    def manage(self, pos: Position, live_price):
        if not live_price or live_price <= 0:
            return
        self.closes.record(pos.mint, live_price, self.now())
        if pos.entry_price <= 0:
            # Bought, but no price was known at the fill: start managing from the first one.
            pos.entry_price = pos.high_water = live_price
            print(f"  {pos.symbol}: entry price set late at ${live_price:.8g}")
            notify.send(f"ℹ️ <b>{esc(pos.symbol)}</b>: entry price set from the first live price "
                        f"(${live_price:.8g}); stops are measured from it")
            self._save_state()
            return
        pos.high_water = max(pos.high_water, live_price)
        mult = live_price / pos.entry_price

        if config.HARD_STOP_PCT and live_price <= pos.entry_price * (1 - config.HARD_STOP_PCT):
            # Far below entry: sell now, on the live price, without waiting for a minute close.
            self.sell(pos, 1.0, f"hard stop -{int(config.HARD_STOP_PCT * 100)}%", live_price)
        else:
            # --- take-profits (fire on live price, sell into strength) ---
            for i, tp in enumerate(config.TAKE_PROFITS):
                if i in pos.tp_hits:
                    continue
                # final tranche becomes a trailing runner when enabled
                if config.TRAIL_RUNNER and i == len(config.TAKE_PROFITS) - 1:
                    continue
                if mult >= tp["mult"]:
                    if self.sell(pos, tp["sell_pct"], f"TP{i + 1} @ {tp['mult']}x", live_price):
                        pos.tp_hits.append(i)
                    else:
                        break  # failed: retry this TP next tick before any higher one

            # --- stops (confirm on a closed 1m close, not a wick) ---
            close = self.closes.last_close(pos.mint) if config.STOP_CONFIRM_1M_CLOSE else None
            confirm = close if close is not None else live_price
            if pos.tokens_remaining > 0 and pos.highest_tp() == 0:
                # BEFORE TP1: two-step early-weakness stop, flat by SL2
                sl1, sl2 = config.EARLY_STOPS
                if confirm <= pos.entry_price * (1 - sl2["drop_pct"]):
                    self.sell(pos, 1.0, f"SL2 -{int(sl2['drop_pct'] * 100)}% full exit", live_price)
                elif not pos.sl1_done and confirm <= pos.entry_price * (1 - sl1["drop_pct"]):
                    if self.sell(pos, sl1["sell_pct"], f"SL1 -{int(sl1['drop_pct'] * 100)}%", live_price):
                        pos.sl1_done = True
            elif pos.tokens_remaining > 0:
                # AFTER TP1: ratcheting stop that climbs with each take-profit.
                tp = pos.highest_tp()
                stop_mult = config.RATCHET_STOP_MULT.get(min(tp, 3), 1.0)
                stop_price = pos.entry_price * stop_mult
                if config.TRAIL_RUNNER and tp >= 3:
                    stop_price = max(stop_price, pos.high_water * (1 - config.TRAIL_PCT))
                if confirm <= stop_price:
                    tag = "trail" if (config.TRAIL_RUNNER and tp >= 3) else f"lock {stop_mult}x"
                    self.sell(pos, 1.0, f"ratchet stop ({tag})", live_price)

        if pos.tokens_remaining <= 0:
            self._close(pos)
        self._save_state()

    def _close(self, pos):
        pnl = pos.proceeds_sol - pos.cost_sol
        self.day_pnl_sol += pnl
        del self.positions[pos.mint]
        self.closes.forget(pos.mint)
        self._journal(side="close", mint=pos.mint, symbol=pos.symbol, sol=pnl,
                      cost_sol=pos.cost_sol, proceeds_sol=pos.proceeds_sol,
                      held_minutes=round((self.now() - pos.opened_at) / 60, 1))
        print(f"  CLOSED {pos.symbol}  P&L {pnl:+.4f} SOL")
        dry = " <i>(dry run)</i>" if config.DRY_RUN else ""
        notify.send(f"⚪️ <b>CLOSED {esc(pos.symbol)}</b>{dry} · P&amp;L {pnl:+.4f} SOL")

    # ---- scan ------------------------------------------------------------
    def scan(self):
        block = self.entry_block()
        if block:
            if "limit" in block and self.limit_notified != block:
                self.limit_notified = block
                notify.send(f"⏸ <b>No new entries today</b> · {esc(block)}. Open positions are still managed.")
            if config.ENTRY_SOURCE == "feed":
                self._skip_feed()
            return
        if config.ENTRY_SOURCE == "feed":
            self._scan_feed()
        else:
            self._scan_trending()

    def _skip_feed(self):
        """While entries are blocked, consume alerts without buying, so they aren't bought late."""
        if self.feed_last_id is not None:
            for a in self.feed.alerts_after(self.feed_last_id):
                self.feed_last_id = max(self.feed_last_id, a["id"])
            self._save_state()

    def _scan_feed(self):
        if self.feed_last_id is None:
            last = self.feed.last_alert_id()
            if last is None:
                return  # the feed hasn't created its database yet
            self.feed_last_id = last  # start from now: never buy alerts from before the bot started
            self._save_state()
            return
        for a in self.feed.alerts_after(self.feed_last_id):
            self.feed_last_id = max(self.feed_last_id, a["id"])
            mint, symbol = a["mint"], a.get("symbol") or a["mint"][:6]
            if mint in self.positions or mint in self.traded:
                continue
            try:
                age = self.now() - datetime.fromisoformat(a["at"]).timestamp()
            except (TypeError, ValueError):
                continue
            if age > config.FEED_MAX_AGE_SECONDS:
                print(f"[scan] skip {symbol}: alert is {age:.0f}s old")
                continue
            if self.entry_block():
                continue
            print(f"[scan] FEED ALERT {symbol} {mint}")
            self.open_position(mint, symbol)
        self._save_state()

    def _scan_trending(self):
        print("[scan] pulling trending...")
        now = self.now()
        for item in self.st.trending("5m")[:config.CANDIDATES_PER_SCAN]:
            if self.entry_block():
                break
            tok = item.get("token", item) if isinstance(item, dict) else {}
            mint = tok.get("mint") or tok.get("address") or (item.get("mint") if isinstance(item, dict) else None)
            if not mint or mint in self.positions or mint in self.traded:
                continue
            if now - self.seen.get(mint, 0) < config.RECHECK_MINUTES * 60:
                continue  # rejected recently; it gets another look after RECHECK_MINUTES
            self.seen[mint] = now
            symbol, decimals, reason = self.passes_filters(item)
            if symbol:
                print(f"[scan] PASS {symbol} {mint}")
                self.open_position(mint, symbol, decimals)
            else:
                print(f"[scan] skip {mint[:8]}… — {reason}")
        self._save_state()

    # ---- main loop -------------------------------------------------------
    def tick(self, n):
        self._sol_usd_cache = None
        self._roll_day()
        if self.positions:
            prices = self.jup.prices(list(self.positions.keys()))
            for mint in list(self.positions.keys()):
                try:
                    self.manage(self.positions[mint], prices.get(mint))
                except Exception as e:  # one bad position must not stop the others' stops
                    print(f"[manage] {mint[:8]}… error: {type(e).__name__}: {e}")
        if n % config.SCAN_EVERY_TICKS == 0:
            self.scan()

    def run(self):
        mode = "DRY RUN (no real trades)" if config.DRY_RUN else "LIVE TRADING"
        wallet = self.jup.wallet or "none (dry run)"
        print(f"=== bot started | {mode} | entries: {config.ENTRY_SOURCE} | wallet {wallet} ===", flush=True)
        notify.send(f"🤖 <b>Bot started</b> · {mode}\nEntries: {esc(config.ENTRY_SOURCE)} · "
                    f"{len(self.positions)} open position(s)")
        n = 0
        while True:
            try:
                self.tick(n)
            except Exception as e:
                print(f"[loop] error: {type(e).__name__}: {e}")
            sys.stdout.flush()
            n += 1
            time.sleep(config.TICK_SECONDS)


def report(journal=None):
    """Summary of the trade journal: closed positions, wins, P&L in SOL."""
    path = journal or config.JOURNAL_FILE
    rows = []
    if os.path.exists(path):
        with open(path) as f:
            rows = [json.loads(line) for line in f if line.strip()]
    closes = [r for r in rows if r.get("side") == "close"]
    pnl = [r.get("sol", 0.0) for r in closes]
    reasons = {}
    for r in rows:
        if r.get("side") == "sell":
            key = r.get("reason", "?").split(" @")[0].split(" -")[0]
            reasons[key] = reasons.get(key, 0) + 1
    out = {"dry_run_rows": sum(1 for r in rows if r.get("dry_run")), "live_rows": sum(1 for r in rows if not r.get("dry_run")),
           "buys": sum(1 for r in rows if r.get("side") == "buy"), "closed": len(closes),
           "wins": sum(1 for p in pnl if p > 0), "pnl_sol": round(sum(pnl), 4),
           "avg_pnl_sol": round(sum(pnl) / len(pnl), 4) if pnl else None,
           "best_sol": round(max(pnl), 4) if pnl else None, "worst_sol": round(min(pnl), 4) if pnl else None,
           "sells_by_reason": reasons}
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    if sys.argv[1:] == ["report"]:
        report()
    else:
        Bot().run()
