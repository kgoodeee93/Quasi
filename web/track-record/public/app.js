// Renders /api/stats. Every value from the data goes in with textContent: token names are
// chosen by whoever launched the token.
(function () {
  const $ = (id) => document.getElementById(id);
  const pct = (v) => (v === null || v === undefined ? "–" : (v > 0 ? "+" : "") + v.toFixed(1) + "%");
  const tone = (el, v) => { if (typeof v === "number" && v !== 0) el.classList.add(v > 0 ? "up" : "down"); };
  const money = (v) => {
    if (typeof v !== "number") return "–";
    for (const [d, u] of [[1e9, "B"], [1e6, "M"], [1e3, "K"]])
      if (Math.abs(v) >= d) return "$" + (v / d).toFixed(2).replace(/\.?0+$/, "") + u;
    return "$" + v.toFixed(0);
  };
  const setVal = (id, v, fmt) => { const el = $(id); el.textContent = fmt(v); tone(el, v); };
  const RULES = {
    vol_5m_min: (v) => `5-minute volume ≥ ${money(v)}`, txns_5m_min: (v) => `≥ ${v} transactions in 5 minutes`,
    buys_5m_min: (v) => `≥ ${v} buys in 5 minutes`, buyers_5m_min: (v) => `≥ ${v} unique buyers in 5 minutes`,
    buy_sell_ratio_min: (v) => `buys ÷ sells ≥ ${v}`, top10_max_pct: (v) => `top 10 holders ≤ ${v}%`,
    dev_max_pct: (v) => `dev holding ≤ ${v}%`, sniper_max_pct: (v) => `snipers ≤ ${v}%`,
    bundled_max_pct: (v) => `bundled supply ≤ ${v}%`, mint_disabled: (v) => (v ? "mint authority disabled" : null),
    freeze_disabled: (v) => (v ? "freeze authority disabled" : null),
  };

  function render(d) {
    const a = d.alerts || {}, ex = a.exits || {}, lad = ex.ladder, tp = ex.tp_sl, h1 = (a.horizons || {})["1h"];
    $("delay").textContent = d.delay_hours;
    $("c-count").textContent = a.count ?? 0;
    setVal("c-ladder", lad ? lad.avg_trade_pct : null, pct);
    $("c-ladder-n").textContent = lad ? `${lad.n} trades replayed` : "after 24 hours";
    $("c-win").textContent = lad ? lad.win_rate_pct.toFixed(0) + "%" : "–";
    if (d.exit_tp_sl) $("c-tp-label").textContent = `+${d.exit_tp_sl.tp_pct}% before −${d.exit_tp_sl.sl_pct}%`;
    $("c-tp").textContent = tp ? tp.hit_tp_pct.toFixed(0) + "%" : "–";
    setVal("c-1h", h1 ? h1.median_pct : null, pct);
    if (d.join_url) { $("join").href = d.join_url; $("join").hidden = false; }

    const rows = d.recent || [];
    $("empty").hidden = rows.length > 0;
    $("recent").hidden = rows.length === 0;
    const body = $("recent").querySelector("tbody");
    for (const r of rows) {
      const tr = document.createElement("tr");
      const cell = (text, cls) => { const td = document.createElement("td"); if (cls) td.className = cls; td.textContent = text; tr.appendChild(td); return td; };
      const when = new Date(r.at);
      const posted = cell(when.toLocaleDateString(undefined, { month: "short", day: "numeric" }));
      const time = document.createElement("span");
      time.className = "opt";
      time.textContent = ", " + when.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
      posted.appendChild(time);
      const tok = cell("");
      const link = document.createElement("a");
      link.href = "https://dexscreener.com/solana/" + encodeURIComponent(r.mint);
      link.rel = "noopener";
      link.textContent = r.symbol || "?";
      const mint = document.createElement("span");
      mint.className = "mint opt";
      mint.textContent = r.mint.slice(0, 4) + "…" + r.mint.slice(-4);
      tok.append(link, mint);
      cell(money(r.mcap), "num opt");
      for (const h of ["1h", "24h"]) tone(cell(pct((r.returns || {})[h]), "num opt"), (r.returns || {})[h]);
      const l = (r.exits || {}).ladder;
      tone(cell(l ? pct(l.return_pct) + (l.result === "open" ? " (open)" : "") : "pending", "num"), l && l.return_pct);
      body.appendChild(tr);
    }

    const list = $("rules");
    for (const [k, v] of Object.entries(d.rules || {})) {
      const text = RULES[k] && RULES[k](v);
      if (text) { const li = document.createElement("li"); li.textContent = text; list.appendChild(li); }
    }
    for (const t of ["buys but no sells in 5 minutes (possible honeypot)", "red safety tier, rugged, or a tokenized stock"]) {
      const li = document.createElement("li"); li.textContent = "never: " + t; list.appendChild(li);
    }
    if (d.ladder) {
      const tps = d.ladder.take_profits.map((t) => `${Math.round(t.sell_pct * 100)}% at ${t.mult}x`).join(", ");
      $("exits-explained").textContent = `"Bot exits" replays the trading bot's ladder on 5-minute candles: ${tps}` +
        `${d.ladder.trail_runner ? " (the last part trails " + Math.round(d.ladder.trail_pct * 100) + "% below its peak)" : ""}; ` +
        `sells half at −${Math.round(d.ladder.early_stops[0].drop_pct * 100)}% and the rest at ` +
        `−${Math.round(d.ladder.early_stops[1].drop_pct * 100)}% before the first take-profit, then a stop that climbs.`;
    }
    $("updated").textContent = d.generated_at ? "Updated " + new Date(d.generated_at).toLocaleString() + "." : "";
  }

  fetch("/api/stats").then((r) => r.json()).then((d) => {
    if (d.error) throw new Error(d.error);
    render(d);
  }).catch(() => {
    $("empty").hidden = false;
    $("empty").textContent = "The track record couldn't be loaded right now. Try again in a minute.";
  });
})();
