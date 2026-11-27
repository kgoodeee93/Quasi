"""
Thin wrappers around the services the bot talks to:

  SolanaTracker  -> discovery + per-token stats/risk  (entry filter data, solanatracker source)
  FeedSource     -> new alerts from the sniper feed's database (feed source)
  Jupiter        -> live prices + swap execution       (buy/sell)
  Chain          -> on-chain token balance over JSON-RPC (for clean full exits)

Every method returns plain dicts/numbers and swallows network hiccups by
returning None (or empty), so the bot loop can skip a token instead of crashing.
"""

import base64
import sqlite3
import time
from pathlib import Path

import requests

ST_BASE = "https://data.solanatracker.io"
JUP_BASE = "https://api.jup.ag"


# ===========================================================================
# Solana Tracker  (entry data)
# ===========================================================================
class SolanaTracker:
    def __init__(self, api_key):
        self.s = requests.Session()
        self.s.headers.update({"x-api-key": api_key})
        self.requests = 0  # count against the plan's monthly quota (Advanced: 200k)

    def _get(self, path, params=None):
        try:
            self.requests += 1
            r = self.s.get(f"{ST_BASE}{path}", params=params, timeout=15)
            if r.status_code == 429:
                time.sleep(2)
                self.requests += 1
                r = self.s.get(f"{ST_BASE}{path}", params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"[ST] {path} failed: {e}")
            return None

    def trending(self, timeframe="5m"):
        """Up to 100 trending tokens for the timeframe, each a full token record (token, pools,
        risk). Our candidate pool."""
        data = self._get(f"/tokens/trending/{timeframe}")
        return data if isinstance(data, list) else []

    def token_info(self, mint):
        """Full token record: metadata, pools, and the risk object."""
        return self._get(f"/tokens/{mint}")

    def stats_5m(self, mint):
        """5-minute window: buyers, sellers, buys, sells, volume, transactions."""
        data = self._get(f"/stats/{mint}")
        if not isinstance(data, dict):
            return None
        return data.get("5m")

    def bundlers(self, mint):
        """Bundler wallets' current share of supply, in percent, or None. The token record's
        risk.bundlers "may be omitted" even when bundlers exist, so this is the source of truth."""
        data = self._get(f"/tokens/{mint}/bundlers")
        if not isinstance(data, dict):
            return None
        pct = data.get("percentage")
        return float(pct) if isinstance(pct, (int, float)) and not isinstance(pct, bool) else None


# ===========================================================================
# Sniper feed  (entries from alerts/sniper-feed)
# ===========================================================================
class FeedSource:
    """Reads the feed's SQLite database read-only. The feed writes; the bot never does."""

    def __init__(self, path):
        self.uri = Path(path).resolve().as_uri() + "?mode=ro"

    def _query(self, sql, args=()):
        try:
            con = sqlite3.connect(self.uri, uri=True, timeout=5)
            try:
                con.row_factory = sqlite3.Row
                return con.execute(sql, args).fetchall()
            finally:
                con.close()
        except sqlite3.Error as e:
            print(f"[feed] read failed: {e}")
            return None

    def last_alert_id(self):
        rows = self._query("SELECT MAX(id) AS m FROM signals WHERE kind = 'alert'")
        return (rows[0]["m"] or 0) if rows else None

    def alerts_after(self, last_id):
        rows = self._query("SELECT id, mint, symbol, at, price FROM signals "
                           "WHERE kind = 'alert' AND id > ? ORDER BY id", (last_id,))
        return [dict(r) for r in rows] if rows else []


# ===========================================================================
# Jupiter  (price + swaps)
# ===========================================================================
class Jupiter:
    def __init__(self, api_key, keypair=None):
        self.s = requests.Session()
        if api_key:
            self.s.headers.update({"x-api-key": api_key})
        self.kp = keypair
        self.wallet = str(keypair.pubkey()) if keypair else None

    # ---- prices ----------------------------------------------------------
    def price_info(self, mints):
        """{mint: {"usdPrice", "decimals", ...}} for up to 50 mints per call."""
        out = {}
        mints = list(dict.fromkeys(mints))
        for i in range(0, len(mints), 50):
            try:
                r = self.s.get(f"{JUP_BASE}/price/v3",
                               params={"ids": ",".join(mints[i:i + 50])}, timeout=15)
                r.raise_for_status()
                out.update({m: v for m, v in r.json().items() if v and "usdPrice" in v})
            except Exception as e:
                print(f"[JUP] price failed: {e}")
        return out

    def prices(self, mints):
        """USD price per mint. Returns {mint: usd_price}."""
        return {m: v["usdPrice"] for m, v in self.price_info(mints).items()}

    def price(self, mint):
        return self.prices([mint]).get(mint)

    # ---- swap flow: order -> sign -> execute -----------------------------
    def _order(self, input_mint, output_mint, amount, slippage_bps):
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(int(amount)),
            "taker": self.wallet,
            "slippageBps": str(slippage_bps),
        }
        r = self.s.get(f"{JUP_BASE}/swap/v2/order", params=params, timeout=20)
        r.raise_for_status()
        return r.json()

    def _execute(self, signed_tx_b64, request_id):
        r = self.s.post(f"{JUP_BASE}/swap/v2/execute",
                        json={"signedTransaction": signed_tx_b64,
                              "requestId": request_id}, timeout=30)
        r.raise_for_status()
        return r.json()

    def _sign(self, tx_b64):
        from solders.transaction import VersionedTransaction
        raw = VersionedTransaction.from_bytes(base64.b64decode(tx_b64))
        signed = VersionedTransaction(raw.message, [self.kp])
        return base64.b64encode(bytes(signed)).decode()

    def swap(self, input_mint, output_mint, amount, slippage_bps):
        """
        Execute a swap. Returns a dict:
          {ok, in_amount, out_amount, signature}  on success
          {ok: False, error, sent}                on failure; sent=True means the signed
                                                  transaction reached Jupiter and may still land
        Fresh memecoins route through Metis (on-chain), which needs only our
        single signature. If a JupiterZ RFQ route is ever returned it needs a
        second market-maker signature and will fail here — rare for new tokens.
        """
        if self.kp is None:
            return {"ok": False, "error": "no wallet key (dry run)", "sent": False}
        try:
            order = self._order(input_mint, output_mint, amount, slippage_bps)
        except Exception as e:
            return {"ok": False, "error": f"order: {e}", "sent": False}

        tx = order.get("transaction")
        if not tx:
            return {"ok": False, "sent": False,
                    "error": f"no tx (router={order.get('router')}, "
                             f"code={order.get('errorCode')}, {order.get('errorMessage') or ''})"}

        try:
            signed = self._sign(tx)
        except Exception as e:
            return {"ok": False, "error": f"sign: {e}", "sent": False}
        try:
            result = self._execute(signed, order["requestId"])
        except Exception as e:
            return {"ok": False, "error": f"execute: {e}", "sent": True}

        if result.get("status") == "Success":
            return {"ok": True,
                    "in_amount": int(result.get("totalInputAmount", 0)),
                    "out_amount": int(result.get("totalOutputAmount", 0)),
                    "signature": result.get("signature")}
        return {"ok": False, "sent": True,
                "error": f"status={result.get('status')} code={result.get('code')}"}


# ===========================================================================
# On-chain balance  (so a "sell everything" really is everything)
# ===========================================================================
class Chain:
    """Plain JSON-RPC: no solana-py dependency (its sync client was removed in 0.42)."""

    def __init__(self, rpc_url, wallet_pubkey_str):
        self.url = rpc_url
        self.owner = wallet_pubkey_str

    def token_balance(self, mint):
        """Raw token balance (base units) the wallet holds of `mint`; 0 if none; None on error."""
        body = {"jsonrpc": "2.0", "id": 1, "method": "getTokenAccountsByOwner",
                "params": [self.owner, {"mint": mint}, {"encoding": "jsonParsed"}]}
        try:
            r = requests.post(self.url, json=body, timeout=15)
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                print(f"[RPC] balance failed: {data['error']}")
                return None
            return sum(int(a["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"])
                       for a in data["result"]["value"])
        except Exception as e:
            print(f"[RPC] balance failed: {type(e).__name__}")  # the RPC URL often embeds a key
            return None


def load_keypair(private_key_b58):
    from solders.keypair import Keypair
    return Keypair.from_base58_string(private_key_b58)
