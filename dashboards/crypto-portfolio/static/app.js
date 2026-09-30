"use strict";

const state = {
  data: null,
  view: "all",
  sort: { key: "value", dir: -1 },
  alloc: "asset",
  search: "",
  hideDust: true,
  open: new Set(),
};
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ---------- formatting ---------- */
let CUR = "USD";
function money(v, opts = {}) {
  if (v == null || !isFinite(v)) return "–";
  const a = Math.abs(v);
  if (opts.compact && a >= 1e5) {
    return new Intl.NumberFormat("en-US", { style: "currency", currency: CUR, notation: "compact", maximumFractionDigits: 2 }).format(v);
  }
  const digits = a === 0 ? 2 : a >= 1 ? 2 : a >= 0.01 ? 4 : Math.min(10, 2 - Math.floor(Math.log10(a)) + 1);
  return new Intl.NumberFormat("en-US", { style: "currency", currency: CUR, minimumFractionDigits: a >= 1 ? 2 : 2, maximumFractionDigits: digits }).format(v);
}
const signedMoney = (v, opts) => (v == null ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + money(Math.abs(v), opts));
const pct = (v, d = 2) => (v == null || !isFinite(v) ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(d) + "%");
const plainPct = (v, d = 1) => (v == null || !isFinite(v) ? "–" : v.toFixed(d) + "%");
const cls = (v) => (v == null || Math.abs(v) < 1e-9 ? "flat" : v > 0 ? "up" : "down");
const arrow = (v) => (v == null || Math.abs(v) < 1e-9 ? "" : v > 0 ? "▲ " : "▼ ");
const delta = (v, fmt = pct) => `<span class="${cls(v)}">${arrow(v)}${fmt(v)}</span>`;
function amount(v) {
  if (v == null) return "–";
  const a = Math.abs(v);
  if (a >= 1e9) return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(v);
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: a >= 1000 ? 2 : a >= 1 ? 4 : 8 }).format(v);
}
const compactNum = (v) => (v == null ? "–" : new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(v));

/* ---------- data ---------- */
async function load(refresh = false) {
  const btn = $("#refresh");
  btn.disabled = true;
  $("#content").classList.add("loading");
  try {
    const r = await fetch(`/api/portfolio${refresh ? "?refresh=true" : ""}`);
    const body = await r.json();
    if (!r.ok) throw new Error(body.detail || r.statusText);
    state.data = body;
    CUR = body.currency.toUpperCase();
    if (!body.views[state.view]) state.view = "all";
    renderShell();
    render();
  } catch (e) {
    if (!state.data) $("#content").innerHTML = `<div class="error"><b>Couldn't load portfolio.</b><br>${esc(e.message)}</div>`;
    else $("#updated").textContent = `Refresh failed: ${e.message}`;
  } finally {
    btn.disabled = false;
    $("#content").classList.remove("loading");
  }
}

function renderShell() {
  const d = state.data;
  $("#demo-badge").hidden = !d.demo;
  $("#updated").textContent = "Updated " + new Date(d.generated_at).toLocaleTimeString();
  const sel = $("#view");
  sel.innerHTML = `<option value="all">All wallets (combined)</option>` +
    d.wallets.map((w) => `<option value="${esc(w)}">${esc(w)}</option>`).join("");
  sel.value = state.view;
  $("#warnings").innerHTML = d.warnings.length
    ? `<details class="warnings"><summary>${d.warnings.length} data notice${d.warnings.length > 1 ? "s" : ""}</summary><ul>${d.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></details>`
    : "";
}

function visibleRows(view) {
  const q = state.search.trim().toLowerCase();
  return view.holdings.filter((r) =>
    (!state.hideDust || r.value >= 1) &&
    (!q || r.symbol.toLowerCase().includes(q) || r.name.toLowerCase().includes(q)));
}

/* ---------- render ---------- */
function render() {
  const v = state.data.views[state.view];
  const s = v.summary;
  const rows = visibleRows(v);
  if (!v.holdings.length) {
    $("#content").innerHTML = `<div class="empty">No priced holdings in this view.</div>`;
    return;
  }
  $("#content").innerHTML = `
    ${kpis(s)}
    <div class="grid two">
      <div class="card"><h2>Portfolio value · 7 days <span class="meta">at current holdings</span></h2>
        <div class="chart" id="history"></div>
        <div class="chart-stats">
          <span>7d high <b>${money(s.high_7d)}</b></span>
          <span>7d low <b>${money(s.low_7d)}</b></span>
          <span>From 7d high <b class="${cls(s.drawdown_7d)}">${pct(s.drawdown_7d)}</b></span>
          <span>1h <b class="${cls(s.pnl_1h)}">${signedMoney(s.pnl_1h)}</b></span>
        </div>
      </div>
      <div class="card"><h2>Allocation ${allocTabs()}</h2><div id="alloc"></div></div>
    </div>
    <div class="grid half">
      <div class="card"><h2>24h PnL by asset</h2>${pnlBars(rows)}</div>
      <div class="card"><h2>Risk &amp; insights</h2>${insights(s)}</div>
    </div>
    ${state.view === "all" ? walletCards(v.by_wallet) : ""}
    <div class="card"><h2>Holdings <span class="meta">${rows.length} of ${v.holdings.length} assets · click a row for details</span></h2>
      <div class="table-wrap">${table(rows)}</div>
    </div>`;
  drawHistory($("#history"), v.history);
  renderAlloc(v, rows);
  bindContent();
}

function kpis(s) {
  const tile = (label, value, sub = "", extra = "") =>
    `<div class="card tile ${extra}"><div class="label">${label}</div><div class="value">${value}</div><div class="sub">${sub}</div></div>`;
  const hero = tile(state.view === "all" ? "Total balance · all wallets" : `Balance · ${esc(state.view)}`,
    money(s.total_value),
    `${delta(s.pnl_24h, signedMoney)} ${delta(s.pnl_24h_pct)} today · ${s.assets} assets${s.wallets ? ` · ${s.wallets} wallets · ${s.chains} chains` : ""}`, "hero");
  const unreal = s.unrealized_pnl == null
    ? tile("Unrealized PnL", "–", "Add cost_basis in wallets.json")
    : tile("Unrealized PnL", `<span class="${cls(s.unrealized_pnl)}">${signedMoney(s.unrealized_pnl)}</span>`,
      `${pct(s.unrealized_pnl_pct)} on ${money(s.cost_basis)} · covers ${plainPct(s.cost_basis_coverage, 0)} of value`);
  return `<div class="grid kpis">${hero}
    ${tile("24h PnL", `<span class="${cls(s.pnl_24h)}">${signedMoney(s.pnl_24h)}</span>`, delta(s.pnl_24h_pct))}
    ${tile("7d change", `<span class="${cls(s.pnl_7d)}">${signedMoney(s.pnl_7d)}</span>`, delta(s.pnl_7d_pct))}
    ${tile("30d change", `<span class="${cls(s.pnl_30d)}">${signedMoney(s.pnl_30d)}</span>`, delta(s.pnl_30d_pct))}
    ${unreal}
    ${tile("Stablecoins", plainPct(s.stable_share), `${money(s.stable_value)} dry powder`)}
  </div>`;
}

function allocTabs() {
  const opts = state.view === "all" ? ["asset", "wallet", "chain"] : ["asset", "chain"];
  return `<span class="seg" role="group">${opts.map((o) =>
    `<button data-alloc="${o}" aria-pressed="${state.alloc === o}">${o[0].toUpperCase() + o.slice(1)}</button>`).join("")}</span>`;
}

function renderAlloc(v, rows) {
  let items;
  if (state.alloc === "wallet" && v.by_wallet) items = v.by_wallet.map((g) => ({ name: g.name, value: g.value, pct: g.allocation }));
  else if (state.alloc === "chain") items = v.by_chain.map((g) => ({ name: g.name, value: g.value, pct: g.allocation }));
  else {
    const all = [...v.holdings].sort((a, b) => b.value - a.value);
    items = all.slice(0, 8).map((r) => ({ name: r.symbol, value: r.value, pct: r.allocation }));
    if (all.length > 8) {
      const rest = all.slice(8);
      items.push({ name: `Other (${rest.length})`, value: rest.reduce((t, r) => t + r.value, 0), pct: rest.reduce((t, r) => t + (r.allocation || 0), 0) });
    }
  }
  const max = Math.max(...items.map((i) => i.pct || 0), 1);
  $("#alloc").innerHTML = `<div class="bars">${items.map((i) => `
    <div class="bar-row" title="${esc(i.name)}: ${money(i.value)} (${plainPct(i.pct)})">
      <span class="name">${esc(i.name)}</span>
      <div class="bar-track"><div class="bar-fill" style="left:0;width:${(i.pct / max) * 100}%"></div></div>
      <span class="v">${plainPct(i.pct)} · ${money(i.value, { compact: true })}</span>
    </div>`).join("")}</div>`;
}

function pnlBars(rows) {
  const items = [...rows].filter((r) => Math.abs(r.pnl_24h) > 0.005)
    .sort((a, b) => Math.abs(b.pnl_24h) - Math.abs(a.pnl_24h)).slice(0, 10)
    .sort((a, b) => b.pnl_24h - a.pnl_24h);
  if (!items.length) return `<div class="empty">No 24h movement.</div>`;
  const max = Math.max(...items.map((r) => Math.abs(r.pnl_24h)));
  return `<div class="bars diverge">${items.map((r) => {
    const w = (Math.abs(r.pnl_24h) / max) * 50;
    return `<div class="bar-row" title="${esc(r.symbol)}: ${signedMoney(r.pnl_24h)} (${pct(r.change_24h)})">
      <span class="name">${esc(r.symbol)}</span>
      <div class="bar-track"><div class="bar-fill ${r.pnl_24h >= 0 ? "pos" : "neg"}" style="width:${w}%"></div></div>
      <span class="v ${cls(r.pnl_24h)}">${arrow(r.pnl_24h)}${signedMoney(r.pnl_24h)}</span>
    </div>`;
  }).join("")}</div>`;
}

function insights(s) {
  const fact = (label, value, sub = "") => `<div class="fact"><div class="label">${label}</div><div class="value">${value}</div><div class="sub">${sub}</div></div>`;
  const mover = (m) => (m ? `${esc(m.symbol)} ${delta(m.change_24h)}` : "–");
  const hhi = s.hhi == null ? "" : s.hhi > 2500 ? "highly concentrated" : s.hhi > 1500 ? "moderately concentrated" : "diversified";
  const tiers = [
    ["Large cap (top 10)", s.cap_tiers.large, "var(--series-1)"],
    ["Mid cap (11–100)", s.cap_tiers.mid, "var(--series-2)"],
    ["Small cap (100+)", s.cap_tiers.small, "var(--series-3)"],
    ["Stablecoins", s.stable_share, "var(--series-4)"],
  ];
  return `<div class="facts">
      ${fact("Top gainer 24h", mover(s.top_gainer), s.top_gainer ? signedMoney(s.top_gainer.pnl_24h) : "")}
      ${fact("Top loser 24h", mover(s.top_loser), s.top_loser ? signedMoney(s.top_loser.pnl_24h) : "")}
      ${fact("Biggest $ contributor", s.best_contributor ? `${esc(s.best_contributor.symbol)} <span class="${cls(s.best_contributor.pnl_24h)}">${signedMoney(s.best_contributor.pnl_24h)}</span>` : "–")}
      ${fact("Biggest $ drag", s.worst_contributor ? `${esc(s.worst_contributor.symbol)} <span class="${cls(s.worst_contributor.pnl_24h)}">${signedMoney(s.worst_contributor.pnl_24h)}</span>` : "–")}
      ${fact("Largest position", s.top_holding ? `${esc(s.top_holding.symbol)} · ${plainPct(s.top_holding.allocation)}` : "–", `Top 5 = ${plainPct(s.top5_share)} of portfolio`)}
      ${fact("Concentration (HHI)", s.hhi == null ? "–" : Math.round(s.hhi).toLocaleString(), `${hhi} · ≈ ${s.effective_assets ? s.effective_assets.toFixed(1) : "–"} equal-weight assets`)}
      ${fact("Value-weighted market cap", money(s.weighted_market_cap, { compact: true }), "excl. stablecoins")}
      ${fact("Drawdown from 7d high", `<span class="${cls(s.drawdown_7d)}">${pct(s.drawdown_7d)}</span>`)}
    </div>
    <div class="section-title">Market-cap exposure</div>
    <div class="stack">${tiers.filter((t) => t[1] > 0).map((t) => `<div style="flex:${t[1]};background:${t[2]}" title="${t[0]}: ${plainPct(t[1])}"></div>`).join("")}</div>
    <div class="legend">${tiers.map((t) => `<span><i style="background:${t[2]}"></i>${t[0]} ${plainPct(t[1])}</span>`).join("")}</div>`;
}

function walletCards(wallets) {
  if (!wallets || wallets.length < 2) return "";
  return `<div class="card" style="margin-bottom:16px"><h2>Wallets <span class="meta">click to drill in</span></h2><div class="wallets">${wallets.map((w) => `
    <button class="wallet" data-wallet="${esc(w.name)}">
      <div class="w-name">${esc(w.name)}</div>
      <div class="w-value">${money(w.value)}</div>
      <div class="w-meta">${delta(w.pnl_24h, signedMoney)} ${delta(w.pnl_24h_pct)} · ${w.assets} asset${w.assets > 1 ? "s" : ""}</div>
      <div class="w-share" title="${plainPct(w.allocation)} of portfolio"><div style="width:${w.allocation}%"></div></div>
      <div class="w-meta">${plainPct(w.allocation)} of portfolio</div>
    </button>`).join("")}</div></div>`;
}

/* ---------- holdings table ---------- */
const COLS = [
  { key: "symbol", label: "Asset" },
  { key: "price", label: "Price", num: true },
  { key: "change_1h", label: "1h", num: true },
  { key: "change_24h", label: "24h", num: true },
  { key: "change_7d", label: "7d", num: true },
  { key: "change_30d", label: "30d", num: true },
  { key: "amount", label: "Holdings", num: true },
  { key: "value", label: "Value", num: true },
  { key: "pnl_24h", label: "24h PnL", num: true },
  { key: "allocation", label: "Alloc.", num: true },
  { key: "market_cap", label: "Market cap", num: true },
  { key: "volume_24h", label: "24h volume", num: true },
  { key: "unrealized_pnl", label: "Unreal. PnL", num: true },
  { key: "sparkline", label: "7d", sortable: false },
];

function table(rows) {
  const { key, dir } = state.sort;
  const sorted = [...rows].sort((a, b) => {
    const x = a[key], y = b[key];
    if (x == null) return 1;
    if (y == null) return -1;
    return (typeof x === "string" ? x.localeCompare(y) : x - y) * dir;
  });
  const head = COLS.map((c) => `<th class="${c.num ? "num" : ""}" ${c.sortable === false ? "" : `data-sort="${c.key}"`}
      ${key === c.key ? `aria-sort="${dir > 0 ? "ascending" : "descending"}"` : ""}>${c.label}</th>`).join("");
  const body = sorted.map((r) => {
    const open = state.open.has(r.id);
    return `<tr class="row" data-id="${esc(r.id)}" aria-expanded="${open}">
      <td><div class="asset">${r.image ? `<img src="${esc(r.image)}" alt="" loading="lazy">` : ""}
        <div><div class="sym">${esc(r.symbol)}</div>
        <div class="nm" title="${esc(r.name)} · ${esc(r.chains.join(", "))}">${esc(r.name)} · ${r.chains.map(esc).join(", ")}</div></div></div></td>
      <td class="num">${money(r.price)}</td>
      <td class="num">${delta(r.change_1h)}</td>
      <td class="num">${delta(r.change_24h)}</td>
      <td class="num">${delta(r.change_7d)}</td>
      <td class="num">${delta(r.change_30d)}</td>
      <td class="num">${amount(r.amount)}</td>
      <td class="num"><b>${money(r.value)}</b></td>
      <td class="num ${cls(r.pnl_24h)}">${signedMoney(r.pnl_24h)}</td>
      <td class="num">${plainPct(r.allocation)}</td>
      <td class="num">${money(r.market_cap, { compact: true })} ${r.market_cap_rank ? `<div class="rank">#${r.market_cap_rank}</div>` : ""}</td>
      <td class="num">${money(r.volume_24h, { compact: true })}</td>
      <td class="num">${r.unrealized_pnl == null ? "–" : `<span class="${cls(r.unrealized_pnl)}">${signedMoney(r.unrealized_pnl)}</span><div class="rank">${pct(r.unrealized_pnl_pct)}</div>`}</td>
      <td>${sparkline(r.sparkline, r.change_7d)}</td>
    </tr>${open ? detail(r) : ""}`;
  }).join("");
  return `<table><thead><tr>${head}</tr></thead><tbody>${body || `<tr><td colspan="${COLS.length}" class="empty">No assets match.</td></tr>`}</tbody></table>`;
}

function detail(r) {
  const stat = (label, value) => `<div><div class="label">${label}</div><div class="value">${value}</div></div>`;
  const rangePos = r.range_24h_position == null ? null : Math.max(0, Math.min(1, r.range_24h_position));
  const wallets = r.wallets.map((b) => `<tr><td>${esc(b.wallet)}</td><td><span class="chip">${esc(b.chain)}</span></td>
      <td class="num">${amount(b.amount)}</td><td class="num">${money(b.value)}</td>
      <td class="num ${cls(b.pnl_24h)}">${signedMoney(b.pnl_24h)}</td><td class="num">${plainPct(b.allocation)}</td></tr>`).join("");
  return `<tr class="detail"><td colspan="${COLS.length}"><div class="detail-grid">
    <div><div class="section-title" style="margin-top:0">Where you hold ${esc(r.symbol)}</div>
      <table><thead><tr><th>Wallet</th><th>Chain</th><th class="num">Amount</th><th class="num">Value</th><th class="num">24h PnL</th><th class="num">Alloc.</th></tr></thead><tbody>${wallets}</tbody></table>
    </div>
    <div class="stats">
      ${stat("24h range", `${money(r.low_24h)} – ${money(r.high_24h)}${rangePos == null ? "" : `<div class="range" title="Price sits at ${Math.round(rangePos * 100)}% of today's range"><i style="left:${rangePos * 100}%"></i></div>`}`)}
      ${stat("Market cap 24h", delta(r.market_cap_change_24h))}
      ${stat("Fully diluted valuation", money(r.fdv, { compact: true }))}
      ${stat("Mcap / FDV", r.mcap_fdv_ratio == null ? "–" : r.mcap_fdv_ratio.toFixed(2) + (r.mcap_fdv_ratio < 0.5 ? " · heavy unlocks ahead" : ""))}
      ${stat("Volume / Mcap (liquidity)", r.volume_mcap_ratio == null ? "–" : (r.volume_mcap_ratio * 100).toFixed(2) + "%")}
      ${stat("All-time high", `${money(r.ath)} <span class="rank">${r.ath_date ? new Date(r.ath_date).toLocaleDateString() : ""}</span>`)}
      ${stat("From ATH", delta(r.ath_change))}
      ${stat("1y change", delta(r.change_1y))}
      ${stat("Circulating supply", `${compactNum(r.circulating_supply)}${r.circulating_pct == null ? "" : ` · ${plainPct(r.circulating_pct)} of max`}`)}
      ${stat("7d PnL", `<span class="${cls(r.pnl_7d)}">${signedMoney(r.pnl_7d)}</span>`)}
      ${stat("30d PnL", `<span class="${cls(r.pnl_30d)}">${signedMoney(r.pnl_30d)}</span>`)}
      ${stat("Cost basis / avg cost", r.cost_basis == null ? "–" : `${money(r.cost_basis)} · ${money(r.avg_cost)}`)}
    </div>
  </div></td></tr>`;
}

function sparkline(points, change) {
  if (!points || points.length < 2) return "";
  const w = 72, h = 24, min = Math.min(...points), max = Math.max(...points), span = max - min || 1;
  const d = points.map((p, i) => `${i ? "L" : "M"}${((i / (points.length - 1)) * w).toFixed(1)},${(h - 2 - ((p - min) / span) * (h - 4)).toFixed(1)}`).join("");
  const color = change == null ? "var(--muted)" : change >= 0 ? "var(--up-mark)" : "var(--down-mark)";
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-label="7 day trend ${pct(change)}"><path d="${d}" style="stroke:${color}"/></svg>`;
}

/* ---------- 7d value line chart with crosshair tooltip ---------- */
function drawHistory(el, points) {
  if (!points.length) { el.innerHTML = `<div class="empty">No price history.</div>`; return; }
  const W = Math.max(el.clientWidth, 280), H = 240, pad = { l: 64, r: 12, t: 10, b: 26 };
  const vals = points.map((p) => p.v);
  let min = Math.min(...vals), max = Math.max(...vals);
  const padV = (max - min) * 0.1 || max * 0.01 || 1;
  min -= padV; max += padV;
  const x = (i) => pad.l + (i / (points.length - 1)) * (W - pad.l - pad.r);
  const y = (v) => pad.t + (1 - (v - min) / (max - min)) * (H - pad.t - pad.b);
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
  const area = `${line}L${x(points.length - 1)},${H - pad.b}L${x(0)},${H - pad.b}Z`;
  const ticks = 4, grid = [];
  for (let k = 0; k <= ticks; k++) {
    const v = min + ((max - min) * k) / ticks;
    grid.push(`<line class="gridline" x1="${pad.l}" x2="${W - pad.r}" y1="${y(v)}" y2="${y(v)}"/>
      <text class="axis-label" x="${pad.l - 8}" y="${y(v) + 4}" text-anchor="end">${money(v, { compact: true })}</text>`);
  }
  const days = [];
  points.forEach((p, i) => {
    const t = new Date(p.t);
    if (t.getHours() === 0 && i > 0 && i < points.length - 3) {
      days.push(`<text class="axis-label" x="${x(i)}" y="${H - 6}" text-anchor="middle">${t.toLocaleDateString(undefined, { weekday: "short" })}</text>`);
    }
  });
  el.innerHTML = `<svg height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Portfolio value over 7 days">
      ${grid.join("")}${days.join("")}
      <path class="area" d="${area}"/><path class="line" d="${line}"/>
      <line class="crosshair" y1="${pad.t}" y2="${H - pad.b}" visibility="hidden"/>
      <circle class="dot" r="4" visibility="hidden"/>
      <rect x="${pad.l}" y="0" width="${W - pad.l - pad.r}" height="${H}" fill="transparent"/>
    </svg><div class="tooltip" hidden></div>`;
  const svg = el.querySelector("svg"), cross = svg.querySelector(".crosshair"), dot = svg.querySelector(".dot"), tip = el.querySelector(".tooltip");
  const last = vals[vals.length - 1];
  svg.addEventListener("pointermove", (ev) => {
    const r = svg.getBoundingClientRect();
    const px = ((ev.clientX - r.left) / r.width) * W;
    const i = Math.max(0, Math.min(points.length - 1, Math.round(((px - pad.l) / (W - pad.l - pad.r)) * (points.length - 1))));
    const p = points[i];
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
    dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(p.v)); dot.setAttribute("visibility", "visible");
    const chg = last - p.v;
    tip.innerHTML = `<b>${money(p.v)}</b>${new Date(p.t).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" })}<br><span class="${cls(chg)}">${signedMoney(chg)} to now</span>`;
    tip.hidden = false;
    const left = (x(i) / W) * r.width;
    tip.style.left = `${Math.min(left + 12, r.width - tip.offsetWidth - 4)}px`;
    tip.style.top = `${Math.max(0, (y(p.v) / H) * r.height - 60)}px`;
  });
  svg.addEventListener("pointerleave", () => {
    cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); tip.hidden = true;
  });
}

/* ---------- events ---------- */
function bindContent() {
  document.querySelectorAll("[data-alloc]").forEach((b) => b.addEventListener("click", () => {
    state.alloc = b.dataset.alloc;
    document.querySelectorAll("[data-alloc]").forEach((o) => o.setAttribute("aria-pressed", o === b));
    const v = state.data.views[state.view];
    renderAlloc(v, visibleRows(v));
  }));
  document.querySelectorAll("[data-wallet]").forEach((b) => b.addEventListener("click", () => setView(b.dataset.wallet)));
  document.querySelectorAll("th[data-sort]").forEach((th) => th.addEventListener("click", () => {
    const key = th.dataset.sort;
    state.sort = state.sort.key === key ? { key, dir: -state.sort.dir } : { key, dir: key === "symbol" ? 1 : -1 };
    render();
  }));
  document.querySelectorAll("tr.row").forEach((tr) => tr.addEventListener("click", () => {
    const id = tr.dataset.id;
    state.open.has(id) ? state.open.delete(id) : state.open.add(id);
    render();
  }));
}

function setView(v) {
  state.view = v;
  if (v !== "all" && state.alloc === "wallet") state.alloc = "asset";
  $("#view").value = v;
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

$("#view").addEventListener("change", (e) => setView(e.target.value));
$("#search").addEventListener("input", (e) => { state.search = e.target.value; if (state.data) render(); });
$("#dust").addEventListener("change", (e) => { state.hideDust = e.target.checked; if (state.data) render(); });
$("#refresh").addEventListener("click", () => load(true));
$("#theme").addEventListener("click", () => {
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("theme", root.dataset.theme); } catch {}
});
try { const t = localStorage.getItem("theme"); if (t) document.documentElement.dataset.theme = t; } catch {}

let timer = setInterval(() => $("#auto").checked && !document.hidden && load(), 60_000);
let resizeT;
window.addEventListener("resize", () => {
  clearTimeout(resizeT);
  resizeT = setTimeout(() => state.data && drawHistory($("#history"), state.data.views[state.view].history), 150);
});
load();
