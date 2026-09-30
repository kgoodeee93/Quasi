"""Pure portfolio math: positions + market data -> everything the dashboard shows.

No I/O in here, so it's all unit-testable with fixture data.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .providers import Position

STABLECOINS = {
    "tether", "usd-coin", "dai", "ethena-usde", "first-digital-usd", "paypal-usd", "usds",
    "frax", "true-usd", "binance-usd", "gemini-dollar", "liquity-usd", "crvusd", "gho",
    "usual-usd", "ripple-usd", "sky-dollar", "susds", "bridged-usdc-polygon-pos-bridge",
}
SPARK_POINTS = 56  # per-row sparkline resolution (7d at ~3h)


def pnl_from_pct(value: float, pct: float | None) -> float:
    """Value change over a period, given today's value and the period's % change.

    past = value / (1 + pct/100)  ->  pnl = value - past = value * pct / (100 + pct)
    """
    if pct is None or pct <= -100:
        return 0.0
    return value * pct / (100 + pct)


def _pct(part: float, whole: float) -> float | None:
    return part / whole * 100 if whole else None


def _downsample(series: list[float], n: int) -> list[float]:
    if len(series) <= n:
        return list(series)
    step = (len(series) - 1) / (n - 1)
    return [series[round(i * step)] for i in range(n)]


def holding_row(coin_id: str, amount: float, market: dict, cost_basis: float | None,
                breakdown: list[dict]) -> dict:
    price = market.get("current_price") or 0.0
    value = amount * price
    ch = {p: market.get(f"price_change_percentage_{p}_in_currency") for p in ("1h", "24h", "7d", "30d", "1y")}
    if ch["24h"] is None:
        ch["24h"] = market.get("price_change_percentage_24h")
    mcap = market.get("market_cap") or None
    vol = market.get("total_volume") or None
    hi, lo = market.get("high_24h"), market.get("low_24h")
    supply_cap = market.get("max_supply") or market.get("total_supply")
    circ = market.get("circulating_supply")
    spark = (market.get("sparkline_in_7d") or {}).get("price") or []

    row = {
        "id": coin_id,
        "symbol": (market.get("symbol") or coin_id).upper(),
        "name": market.get("name") or coin_id,
        "image": market.get("image"),
        "amount": amount,
        "price": price,
        "value": value,
        "change_1h": ch["1h"], "change_24h": ch["24h"], "change_7d": ch["7d"],
        "change_30d": ch["30d"], "change_1y": ch["1y"],
        "pnl_1h": pnl_from_pct(value, ch["1h"]),
        "pnl_24h": pnl_from_pct(value, ch["24h"]),
        "pnl_7d": pnl_from_pct(value, ch["7d"]),
        "pnl_30d": pnl_from_pct(value, ch["30d"]),
        "market_cap": mcap,
        "market_cap_rank": market.get("market_cap_rank"),
        "market_cap_change_24h": market.get("market_cap_change_percentage_24h"),
        "fdv": market.get("fully_diluted_valuation"),
        "mcap_fdv_ratio": (mcap / market["fully_diluted_valuation"])
        if mcap and market.get("fully_diluted_valuation") else None,
        "volume_24h": vol,
        "volume_mcap_ratio": (vol / mcap) if vol and mcap else None,
        "high_24h": hi, "low_24h": lo,
        "range_24h_position": ((price - lo) / (hi - lo)) if hi and lo and hi > lo else None,
        "ath": market.get("ath"),
        "ath_change": market.get("ath_change_percentage"),
        "ath_date": market.get("ath_date"),
        "circulating_supply": circ,
        "max_supply": market.get("max_supply"),
        "circulating_pct": _pct(circ, supply_cap) if circ and supply_cap else None,
        "is_stable": coin_id in STABLECOINS,
        "chains": sorted({b["chain"] for b in breakdown}),
        "wallets": breakdown,
        "sparkline": _downsample(spark, SPARK_POINTS),
        "cost_basis": cost_basis,
        "avg_cost": (cost_basis / amount) if cost_basis is not None and amount else None,
        "unrealized_pnl": (value - cost_basis) if cost_basis is not None else None,
        "unrealized_pnl_pct": _pct(value - cost_basis, cost_basis) if cost_basis else None,
        "allocation": None,  # filled once the total is known
    }
    return row


def value_history(amounts: dict[str, float], markets: dict[str, dict], current_total: float) -> dict:
    """Portfolio value over the last 7 days, at today's holdings (hourly)."""
    sparks = {c: (markets[c].get("sparkline_in_7d") or {}).get("price") or [] for c in amounts}
    sparks = {c: s for c, s in sparks.items() if s}
    if not sparks:
        return {"points": [], "high": None, "low": None, "drawdown": None}
    n = min(len(s) for s in sparks.values())
    values = [0.0] * n
    for coin, s in sparks.items():
        tail = s[-n:]
        for i in range(n):
            values[i] += amounts[coin] * tail[i]
    # Coins without a sparkline count as flat at their current value.
    flat = sum(amounts[c] * (markets[c].get("current_price") or 0) for c in amounts if c not in sparks)
    values = [v + flat for v in values]
    values[-1] = current_total  # pin the last point to the live total

    updated = [markets[c].get("last_updated") for c in sparks if markets[c].get("last_updated")]
    end = max((datetime.fromisoformat(u.replace("Z", "+00:00")) for u in updated),
              default=datetime.now(timezone.utc))
    points = [{"t": (end - timedelta(hours=n - 1 - i)).isoformat(), "v": v} for i, v in enumerate(values)]
    high, low = max(values), min(values)
    return {"points": points, "high": high, "low": low,
            "drawdown": _pct(current_total - high, high)}


def _group(rows: list[dict], key: str, total: float) -> list[dict]:
    groups: dict[str, dict] = defaultdict(lambda: {"value": 0.0, "pnl_24h": 0.0, "assets": set()})
    for r in rows:
        for b in r["wallets"]:
            g = groups[b[key]]
            g["value"] += b["value"]
            g["pnl_24h"] += b["pnl_24h"]
            g["assets"].add(r["id"])
    out = []
    for name, g in groups.items():
        out.append({"name": name, "value": g["value"], "pnl_24h": g["pnl_24h"],
                    "pnl_24h_pct": _pct(g["pnl_24h"], g["value"] - g["pnl_24h"]),
                    "allocation": _pct(g["value"], total), "assets": len(g["assets"])})
    return sorted(out, key=lambda g: -g["value"])


def summarize(rows: list[dict], history: dict) -> dict:
    total = sum(r["value"] for r in rows)
    s = {"total_value": total, "assets": len(rows)}
    for p in ("1h", "24h", "7d", "30d"):
        pnl = sum(r[f"pnl_{p}"] for r in rows)
        s[f"pnl_{p}"] = pnl
        s[f"pnl_{p}_pct"] = _pct(pnl, total - pnl)

    with_basis = [r for r in rows if r["cost_basis"] is not None]
    basis = sum(r["cost_basis"] for r in with_basis)
    s["cost_basis"] = basis if with_basis else None
    s["unrealized_pnl"] = sum(r["value"] for r in with_basis) - basis if with_basis else None
    s["unrealized_pnl_pct"] = _pct(s["unrealized_pnl"], basis) if with_basis and basis else None
    s["cost_basis_coverage"] = _pct(sum(r["value"] for r in with_basis), total) if with_basis else 0

    weights = [r["value"] / total for r in rows] if total else []
    ranked = sorted(rows, key=lambda r: -r["value"])
    s["top_holding"] = {"symbol": ranked[0]["symbol"], "allocation": ranked[0]["allocation"]} if ranked else None
    s["top5_share"] = sum(r["allocation"] or 0 for r in ranked[:5]) if ranked else None
    hhi = sum(w * w for w in weights)
    s["hhi"] = hhi * 10_000 if weights else None  # 0-10,000; >2,500 = highly concentrated
    s["effective_assets"] = 1 / hhi if hhi else None  # "you're as diversified as N equal holdings"

    stable = sum(r["value"] for r in rows if r["is_stable"])
    s["stable_value"] = stable
    s["stable_share"] = _pct(stable, total)

    tiers = {"large": 0.0, "mid": 0.0, "small": 0.0}  # rank 1-10, 11-100, 101+/unranked
    for r in rows:
        if r["is_stable"]:
            continue
        rank = r["market_cap_rank"]
        tiers["large" if rank and rank <= 10 else "mid" if rank and rank <= 100 else "small"] += r["value"]
    s["cap_tiers"] = {k: _pct(v, total) for k, v in tiers.items()}

    volatile = [r for r in rows if not r["is_stable"] and r["market_cap"]]
    vol_value = sum(r["value"] for r in volatile)
    s["weighted_market_cap"] = (sum(r["market_cap"] * r["value"] for r in volatile) / vol_value) if vol_value else None

    movers = [r for r in rows if r["change_24h"] is not None and not r["is_stable"]]
    s["top_gainer"] = _mover(max(movers, key=lambda r: r["change_24h"])) if movers else None
    s["top_loser"] = _mover(min(movers, key=lambda r: r["change_24h"])) if movers else None
    s["best_contributor"] = _mover(max(rows, key=lambda r: r["pnl_24h"])) if rows else None
    s["worst_contributor"] = _mover(min(rows, key=lambda r: r["pnl_24h"])) if rows else None
    s["high_7d"], s["low_7d"], s["drawdown_7d"] = history["high"], history["low"], history["drawdown"]
    return s


def _mover(r: dict) -> dict:
    return {"symbol": r["symbol"], "change_24h": r["change_24h"], "pnl_24h": r["pnl_24h"]}


def build_view(positions: list[Position], markets: dict[str, dict], cost_basis: dict[str, float],
               basis_share: dict[str, float] | None = None, include_wallets: bool = False) -> dict:
    """One dashboard view (all wallets combined, or a single wallet).

    basis_share: coin -> fraction of the coin's total amount held in this view,
    so a portfolio-wide cost basis is pro-rated onto a single wallet.
    """
    per_coin: dict[str, list[Position]] = defaultdict(list)
    for p in positions:
        if p.coin_id in markets:
            per_coin[p.coin_id].append(p)

    rows = []
    for coin, ps in per_coin.items():
        m = markets[coin]
        price = m.get("current_price") or 0.0
        pct24 = m.get("price_change_percentage_24h_in_currency", m.get("price_change_percentage_24h"))
        merged: dict[tuple[str, str], float] = defaultdict(float)
        for p in ps:
            merged[(p.wallet, p.chain)] += p.amount
        breakdown = [{"wallet": w, "chain": c, "amount": a, "value": a * price,
                      "pnl_24h": pnl_from_pct(a * price, pct24)}
                     for (w, c), a in sorted(merged.items(), key=lambda kv: -kv[1])]
        basis = cost_basis.get(coin)
        if basis is not None and basis_share is not None:
            basis *= basis_share.get(coin, 0.0)
        rows.append(holding_row(coin, sum(merged.values()), m, basis, breakdown))

    total = sum(r["value"] for r in rows)
    for r in rows:
        r["allocation"] = _pct(r["value"], total)
        for b in r["wallets"]:
            b["allocation"] = _pct(b["value"], total)
    rows.sort(key=lambda r: -r["value"])

    history = value_history({r["id"]: r["amount"] for r in rows}, markets, total)
    view = {
        "summary": summarize(rows, history),
        "holdings": rows,
        "history": history["points"],
        "by_chain": _group(rows, "chain", total),
    }
    if include_wallets:
        view["by_wallet"] = _group(rows, "wallet", total)
        view["summary"]["wallets"] = len(view["by_wallet"])
        view["summary"]["chains"] = len(view["by_chain"])
    return view


def build_dashboard(positions: list[Position], markets: dict[str, dict],
                    cost_basis: dict[str, float], wallet_labels: list[str]) -> dict:
    views = {"all": build_view(positions, markets, cost_basis, include_wallets=True)}
    totals: dict[str, float] = defaultdict(float)
    for p in positions:
        totals[p.coin_id] += p.amount
    for label in wallet_labels:
        mine = [p for p in positions if p.wallet == label]
        held: dict[str, float] = defaultdict(float)
        for p in mine:
            held[p.coin_id] += p.amount
        share = {c: held[c] / totals[c] for c in held if totals[c]}
        views[label] = build_view(mine, markets, cost_basis, basis_share=share)
    return {"views": views, "wallets": wallet_labels}
