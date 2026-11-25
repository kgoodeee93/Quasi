"""Alert text and Telegram delivery."""
from __future__ import annotations

from html import escape

import httpx

DISCLAIMER = "Not financial advice. Automated screen; most new tokens go to zero. Size small, use a stop."


def money(v: float | None) -> str:
    if v is None:
        return "?"
    for div, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"${v / div:.2f}{unit}"
    return f"${v:,.0f}"


def price(v: float | None) -> str:
    if v is None:
        return "?"
    return f"${v:.4g}" if v < 1 else f"${v:,.4f}"


def pct(v) -> str:
    return "?" if v is None else f"{v:.1f}%"


def format_alert(token: dict, m: dict) -> str:
    """Telegram HTML. Every value from a data source is escaped: token names are attacker-chosen."""
    mint = escape(token["mint"])
    sym = escape(token.get("symbol") or "?")
    ratio = m.get("buy_sell_ratio")
    lines = [
        f"🚨 <b>{sym}</b> passed all 11 checks",
        f"MC {money(token.get('mcap'))} · Liq {money(token.get('liquidity'))} · {price(token.get('price'))}",
        f"5m: {money(m.get('vol_5m'))} vol · {m.get('txns_5m')} tx · {m.get('buys_5m')} buys / "
        f"{m.get('sells_5m')} sells ({'no sells' if ratio == 999.0 else ratio}) · {m.get('buyers_5m')} buyers",
        f"Top 10 {pct(m.get('top10_pct'))} · Dev {pct(m.get('dev_pct'))} · Snipers {pct(m.get('sniper_pct'))} · "
        f"Bundled {pct(m.get('bundled_pct'))}",
        "Mint ✅ · Freeze ✅" + (f" · Moby {escape(str(m['safety_tier']))}" if m.get("safety_tier") else ""),
        f"<code>{mint}</code>",
        f'<a href="https://dexscreener.com/solana/{mint}">Chart</a> · '
        f'<a href="https://rugcheck.xyz/tokens/{mint}">RugCheck</a> · '
        f'<a href="https://www.geckoterminal.com/solana/tokens/{mint}">GeckoTerminal</a>',
        f"<i>{escape(DISCLAIMER)}</i>",
    ]
    return "\n".join(lines)


async def send_telegram(client: httpx.AsyncClient, token: str, chat_id: str, text: str) -> None:
    r = await client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                                "disable_web_page_preview": True}, timeout=20)
    if r.status_code != 200:
        # The bot token is in the URL: never echo the request, only Telegram's description.
        desc = r.json().get("description", "") if "json" in r.headers.get("content-type", "") else ""
        raise RuntimeError(f"Telegram refused the message (HTTP {r.status_code}) {desc[:200]}")
