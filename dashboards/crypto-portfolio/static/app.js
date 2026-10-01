"use strict";

/* Quasi portfolio front end. Renders /api/portfolio; all math happens server-side. */

const state = {
  data: null,
  view: "all",
  alloc: "asset",
  sort: { key: "value", dir: -1 },
  search: "",
  hideDust: true,
  auto: true,
  open: new Set(),
};
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

/* ---------- formatting ---------- */
let CUR = "USD";
function money(v, { compact = false, cents = true } = {}) {
  if (v == null || !isFinite(v)) return "–";
  const a = Math.abs(v);
  if (compact && a >= 1e5) {
    return new Intl.NumberFormat("en-US", { style: "currency", currency: CUR, notation: "compact", maximumFractionDigits: 2 }).format(v);
  }
  let max = cents ? 2 : 0;
  if (a > 0 && a < 1) max = Math.min(10, Math.max(2, 2 - Math.floor(Math.log10(a)) + 1));
  return new Intl.NumberFormat("en-US", { style: "currency", currency: CUR, minimumFractionDigits: Math.min(max, cents ? 2 : 0), maximumFractionDigits: max }).format(v);
}
const sign = (v) => (v > 0 ? "+" : v < 0 ? "−" : "");
const smoney = (v, o) => (v == null ? "–" : sign(v) + money(Math.abs(v), o));
const pct = (v, d = 2) => (v == null || !isFinite(v) ? "–" : sign(v) + Math.abs(v).toFixed(d) + "%");
const ppct = (v, d = 1) => (v == null || !isFinite(v) ? "–" : v.toFixed(d) + "%");
const tone = (v) => (v == null || Math.abs(v) < 1e-9 ? "flat" : v > 0 ? "up" : "down");
const tri = (v) => (v == null || Math.abs(v) < 1e-9 ? "" :
  `<svg class="tri" viewBox="0 0 10 10" aria-hidden="true"><path d="${v > 0 ? "M5 1 L9 8 L1 8 Z" : "M5 9 L9 2 L1 2 Z"}"/></svg>`);
const pill = (v, text) => `<span class="pill ${tone(v)}">${tri(v)}${text ?? pct(v)}</span>`;
function amount(v) {
  if (v == null) return "–";
  const a = Math.abs(v);
  if (a >= 1e6) return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(v);
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: a >= 1000 ? 2 : a >= 1 ? 4 : 8 }).format(v);
}

/* ---------- identity colors (follow the entity, never its rank) ---------- */
const TOKEN = {
  BTC: ["#F7931A", "#C2620A", "₿"], ETH: ["#8C9EFF", "#4F5BD5", "Ξ"], SOL: ["#9945FF", "#14F195", "S"],
  LINK: ["#4C7CF5", "#2A5ADA", "L"], USDC: ["#4A90E2", "#2775CA", "$"], USDT: ["#3CC39B", "#1E8A6A", "₮"],
  UNI: ["#FF4FA3", "#C8006A", "U"], JUP: ["#C7F284", "#00BEF0", "J"], BONK: ["#FFC24A", "#E07B00", "B"],
  HYPE: ["#97FCE4", "#0F766E", "H"], POL: ["#A57BFF", "#6C2BD9", "P"],
};
const SERIES = ["#F7931A", "#8C9EFF", "#E879F9", "#38BDF8", "#FBBF24", "#34D399", "#F472B6", "#A78BFA"];
function hash(s) { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) >>> 0; return h; }
function tokenColor(sym) { return TOKEN[sym]?.[0] ?? SERIES[hash(sym) % SERIES.length]; }
function coin(r, size = 36) {
  const [a, b, ch] = TOKEN[r.symbol] ?? [tokenColor(r.symbol), "#334155", r.symbol[0]];
  const img = r.image ? `<img src="${esc(r.image)}" alt="" loading="lazy" onerror="this.remove()">` : "";
  return `<div class="coin" aria-hidden="true" style="width:${size}px;height:${size}px;background:linear-gradient(135deg,${a},${b})">${esc(ch)}${img}</div>`;
}
const WALLET_GRADS = [["#64748B", "#1E293B"], ["#F6851B", "#C05A0C"], ["#3B82F6", "#1D4ED8"], ["#AB9FF2", "#6E56CF"], ["#34D399", "#0F766E"], ["#F472B6", "#BE185D"]];
function avatar(name) {
  const [a, b] = WALLET_GRADS[hash(name) % WALLET_GRADS.length];
  return `<div class="avatar" aria-hidden="true" style="background:linear-gradient(135deg,${a},${b})">${esc(name[0]?.toUpperCase() ?? "?")}</div>`;
}

/* ---------- palette (Aurora default; Ember, Lagoon) ---------- */
const PALETTES = [["aurora", "Aurora"], ["ember", "Ember"], ["lagoon", "Lagoon"]];
function setPalette(p) {
  if (!PALETTES.some(([k]) => k === p)) p = "aurora";
  document.documentElement.dataset.palette = p;
  store.set("palette", p);
  document.querySelectorAll("[data-pal]").forEach((b) => b.setAttribute("aria-checked", b.dataset.pal === p));
}
setPalette(store.get("palette") || "aurora");

/* ---------- data ---------- */
async function load(refresh = false) {
  const btn = $("#refresh");
  btn.disabled = true;
  document.body.classList.add("loading");
  try {
    const r = await fetch(`/api/portfolio${refresh ? "?refresh=true" : ""}`);
    const body = await r.json();
    if (!r.ok) throw new Error(body.detail || r.statusText);
    state.data = body;
    CUR = body.currency.toUpperCase();
    if (!body.views[state.view]) state.view = "all";
    renderChrome();
    render();
  } catch (e) {
    if (!state.data) $("#content").innerHTML = `<div class="card empty"><b>Couldn't load your portfolio.</b><br>${esc(e.message)}</div>`;
    else $("#updated").textContent = `Refresh failed · ${e.message}`;
  } finally {
    btn.disabled = false;
    document.body.classList.remove("loading");
  }
}

function renderChrome() {
  const d = state.data;
  $("#demo-badge").hidden = !d.demo;
  $("#updated").textContent = "Updated " + new Date(d.generated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const names = ["all", ...d.wallets];
  $("#wallet-nav").innerHTML = names.map((w) =>
    `<button data-view="${esc(w)}" aria-pressed="${state.view === w}">${w === "all" ? "All wallets" : esc(w)}</button>`).join("");
  $("#warnings").innerHTML = d.warnings.length
    ? `<details class="notice"><summary>${d.warnings.length} data notice${d.warnings.length > 1 ? "s" : ""}</summary><ul>${d.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></details>`
    : "";
}

function visibleRows(v) {
  const q = state.search.trim().toLowerCase();
  return v.holdings.filter((r) => (!state.hideDust || r.value >= 1) &&
    (!q || r.symbol.toLowerCase().includes(q) || r.name.toLowerCase().includes(q)));
}

/* ---------- render ---------- */
function render() {
  const v = state.data.views[state.view];
  const s = v.summary;
  if (!v.holdings.length) {
    $("#content").innerHTML = `<div class="card empty">No priced holdings in this view yet.</div>`;
    return;
  }
  const rows = visibleRows(v);
  $("#content").innerHTML = `<div class="grid">
    <div class="row-a">${hero(s)}<section class="card alloc" aria-labelledby="alloc-h">${allocHead()}<div id="alloc"></div></section></div>
    <div class="tiles">${tiles(s)}</div>
    <div class="row-c">
      ${state.view === "all" ? walletsCard(v.by_wallet) : chainsCard(v.by_chain)}
      ${pnlCard(rows, s)}
      ${riskCard(s)}
    </div>
    ${holdingsCard(v, rows)}
  </div>`;
  drawChart(v.history);
  renderAlloc(v);
}

function hero(s) {
  const [d, c] = money(s.total_value).split(".");
  const scope = state.view === "all"
    ? `Total balance · ${s.wallets} wallet${s.wallets === 1 ? "" : "s"} · ${s.chains} chain${s.chains === 1 ? "" : "s"} · ${s.assets} assets`
    : `${esc(state.view)} · ${s.assets} asset${s.assets === 1 ? "" : "s"}`;
  return `<section class="hero" aria-label="Total balance">
    <div class="hero-top">
      <div>
        <div class="hero-label">${scope}</div>
        <h1 class="hero-num">${d}${c ? `<span class="cents">.${c}</span>` : ""}</h1>
        <div class="hero-delta"><span class="glass-pill">${tri(s.pnl_24h)}${smoney(s.pnl_24h)} · ${pct(s.pnl_24h_pct)}</span><span>today</span></div>
      </div>
      <span class="range-chip">Last 7 days</span>
    </div>
    <div class="chart" id="chart"></div>
    <div class="days" id="days"></div>
    <div class="hero-stats">
      <span>7d high <b class="num">${money(s.high_7d)}</b></span>
      <span>7d low <b class="num">${money(s.low_7d)}</b></span>
      <span>From 7d high <b class="num">${pct(s.drawdown_7d)}</b></span>
      <span>Last hour <b class="num">${smoney(s.pnl_1h)}</b></span>
    </div>
  </section>`;
}

function allocHead() {
  const opts = state.view === "all" ? ["asset", "wallet", "chain"] : ["asset", "chain"];
  if (!opts.includes(state.alloc)) state.alloc = "asset";
  return `<div class="card-head"><h2 id="alloc-h">Allocation</h2>
    <div class="seg" role="group" aria-label="Group allocation by">${opts.map((o) =>
      `<button data-alloc="${o}" aria-pressed="${state.alloc === o}">${o[0].toUpperCase() + o.slice(1)}</button>`).join("")}</div></div>`;
}

function allocItems(v) {
  if (state.alloc === "wallet" && v.by_wallet) return v.by_wallet.map((g, i) => ({ name: g.name, pct: g.allocation, value: g.value, color: SERIES[i % SERIES.length] }));
  if (state.alloc === "chain") return v.by_chain.map((g, i) => ({ name: g.name, pct: g.allocation, value: g.value, color: SERIES[(i + 1) % SERIES.length] }));
  const stable = v.holdings.filter((r) => r.is_stable);
  const risky = v.holdings.filter((r) => !r.is_stable);
  const top = risky.slice(0, 4).map((r) => ({ name: r.symbol, pct: r.allocation, value: r.value, color: tokenColor(r.symbol) }));
  const items = [...top];
  if (stable.length) items.push({ name: "Stablecoins", pct: stable.reduce((t, r) => t + r.allocation, 0), value: stable.reduce((t, r) => t + r.value, 0), color: "#CBD5E1" });
  const rest = risky.slice(4);
  if (rest.length) items.push({ name: `Other (${rest.length})`, pct: rest.reduce((t, r) => t + r.allocation, 0), value: rest.reduce((t, r) => t + r.value, 0), color: "#475569" });
  return items;
}

function renderAlloc(v) {
  const items = allocItems(v).filter((i) => i.pct > 0);
  const gap = items.length > 1 ? 0.7 : 0;
  let acc = 0;
  const stops = items.map((i) => {
    const seg = `${i.color} ${acc.toFixed(2)}% ${(acc + i.pct - gap).toFixed(2)}%, #11121A ${(acc + i.pct - gap).toFixed(2)}% ${(acc + i.pct).toFixed(2)}%`;
    acc += i.pct;
    return seg;
  });
  const lead = items.reduce((m, i) => (i.pct > m.pct ? i : m), items[0]);
  $("#alloc").innerHTML = `
    <div class="donut-wrap" style="margin-bottom:22px">
      <div class="donut" role="img" aria-label="Allocation: ${esc(items.map((i) => `${i.name} ${ppct(i.pct)}`).join(", "))}" style="background:conic-gradient(${stops.join(",")})">
        <div class="donut-hole"><span class="k">Largest</span><span class="v">${esc(lead.name)}</span><span class="p">${ppct(lead.pct)}</span></div>
      </div>
    </div>
    <ul class="legend">${items.map((i) => `<li><span class="sw" style="background:${i.color}"></span><span class="n">${esc(i.name)}</span>
      <span class="pc num">${ppct(i.pct)}</span><span class="val num">${money(i.value, { compact: true, cents: i.value < 1000 })}</span></li>`).join("")}</ul>`;
}

const ICON = {
  week: '<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
  month: '<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M3 10h18M8 3v4M16 3v4"/>',
  gain: '<path d="M12 3v18"/><path d="M17 7.5c0-1.9-2.2-3.5-5-3.5s-5 1.6-5 3.5 2.2 3.1 5 3.5 5 1.6 5 3.5-2.2 3.5-5 3.5-5-1.6-5-3.5"/>',
  shield: '<path d="M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6l8-3z"/><path d="M9 12l2 2 4-4"/>',
};
function tile(icon, label, value, cls, sub) {
  return `<article class="card tile"><div class="tile-top"><span>${label}</span>
    <div class="chip-icon" aria-hidden="true"><svg width="18" height="18" viewBox="0 0 24 24">${ICON[icon]}</svg></div></div>
    <div class="tile-value ${cls}">${value}</div><div class="tile-sub">${sub}</div></article>`;
}
function tiles(s) {
  const unreal = s.unrealized_pnl == null
    ? tile("gain", "Unrealized PnL", "–", "flat", "Add <code>cost_basis</code> in wallets.json")
    : tile("gain", "Unrealized PnL", smoney(s.unrealized_pnl), tone(s.unrealized_pnl), `${pill(s.unrealized_pnl_pct)}<span>on ${money(s.cost_basis, { cents: false })} cost</span>`);
  return [
    tile("week", "7-day change", smoney(s.pnl_7d), tone(s.pnl_7d), `${pill(s.pnl_7d_pct)}<span>vs. last week</span>`),
    tile("month", "30-day change", smoney(s.pnl_30d), tone(s.pnl_30d), `${pill(s.pnl_30d_pct)}<span>vs. last month</span>`),
    unreal,
    tile("shield", "Dry powder", ppct(s.stable_share), "", `<span class="flat">${money(s.stable_value)}</span><span>in stablecoins</span>`),
  ].join("");
}

function groupRow(g, sub, clickable) {
  const inner = `${avatar(g.name)}<div class="wl-main"><div class="wl-name">${esc(g.name)}</div><div class="wl-sub">${sub}</div></div>
    <div class="wl-right"><div class="wl-val num">${money(g.value)}</div><div class="${tone(g.pnl_24h_pct)}" style="font-size:12px">${pct(g.pnl_24h_pct)}</div></div>`;
  return `<li class="wl">${clickable ? `<button class="wl-btn" data-view="${esc(g.name)}" aria-label="Open ${esc(g.name)}">${inner}</button>` : `<div class="wl-btn" style="cursor:default">${inner}</div>`}
    <div class="share"><div class="share-track"><div class="share-fill" style="width:${Math.max(g.allocation, 1).toFixed(1)}%"></div></div><span class="num">${ppct(g.allocation)}</span></div></li>`;
}
function walletsCard(ws) {
  return `<section class="card list-card" aria-labelledby="wal-h"><div class="card-head"><h2 id="wal-h">Wallets</h2><span class="meta">24h · share</span></div>
    <ul>${ws.map((w) => groupRow(w, `${w.assets} asset${w.assets === 1 ? "" : "s"}`, true)).join("")}</ul></section>`;
}
function chainsCard(cs) {
  return `<section class="card list-card" aria-labelledby="ch-h"><div class="card-head"><h2 id="ch-h">Chains</h2><span class="meta">24h · share</span></div>
    <ul>${cs.map((c) => groupRow(c, `${c.assets} asset${c.assets === 1 ? "" : "s"}`, false)).join("")}</ul></section>`;
}

function pnlCard(rows, s) {
  const movers = rows.filter((r) => Math.abs(r.pnl_24h) >= 0.005).sort((a, b) => Math.abs(b.pnl_24h) - Math.abs(a.pnl_24h)).slice(0, 6);
  const max = Math.max(...movers.map((r) => Math.abs(r.pnl_24h)), 1e-9);
  const bars = movers.length ? movers.map((r) => `<li class="bar" title="${esc(r.symbol)} ${smoney(r.pnl_24h)} (${pct(r.change_24h)})">
      <span class="sym">${esc(r.symbol)}</span>
      <div class="bar-track"><div class="bar-fill ${tone(r.pnl_24h)}" style="width:${Math.max((Math.abs(r.pnl_24h) / max) * 100, 1.5).toFixed(1)}%"></div></div>
      <span class="v num ${tone(r.pnl_24h)}">${smoney(r.pnl_24h)}</span></li>`).join("")
    : `<li class="empty">No movement in the last 24h.</li>`;
  return `<section class="card pnl-card" aria-labelledby="pnl-h">
    <div class="card-head"><h2 id="pnl-h">24h PnL by asset</h2><span class="meta">Top movers in ${CUR}</span></div>
    <ul class="bars">${bars}</ul><div style="flex:1"></div>
    <div class="net ${tone(s.pnl_24h) === "down" ? "down" : "up"}"><span>Net today</span><b class="num ${tone(s.pnl_24h)}">${smoney(s.pnl_24h)}</b></div>
  </section>`;
}

function riskCard(s) {
  const hhi = s.hhi ?? 0;
  const label = hhi > 2500 ? ["Highly concentrated", "#FDA4AF"] : hhi > 1500 ? ["Moderately concentrated", "#FDE68A"] : ["Diversified", "#86EFAC"];
  const tiers = [["Large cap", s.cap_tiers.large, "#A78BFA"], ["Mid cap", s.cap_tiers.mid, "#38BDF8"], ["Small cap", s.cap_tiers.small, "#F472B6"], ["Stables", s.stable_share, "#CBD5E1"]];
  const fact = (k, v, sub = "", cls = "") => `<div class="fact"><div class="k">${k}</div><div class="v ${cls}">${v}</div>${sub ? `<div class="s">${sub}</div>` : ""}</div>`;
  const g = s.top_gainer;
  return `<section class="card risk" aria-labelledby="risk-h">
    <div class="card-head"><h2 id="risk-h">Risk &amp; insights</h2><span class="meta">Concentration</span></div>
    <div>
      <div class="hhi"><span class="big">${Math.round(hhi).toLocaleString()}</span><span style="font-size:13px;color:${label[1]}">${label[0]}</span></div>
      <div class="meta" style="white-space:normal">HHI · as diversified as ≈${s.effective_assets ? s.effective_assets.toFixed(1) : "–"} equal-weight assets</div>
      <div class="gauge"><i style="left:${Math.min(hhi / 100, 100).toFixed(1)}%"></i></div>
      <div class="gauge-labels"><span>Diversified</span><span>Concentrated</span></div>
    </div>
    <div>
      <div class="label-sm">Market-cap exposure</div>
      <div class="stack" role="img" aria-label="${tiers.map((t) => `${t[0]} ${ppct(t[1])}`).join(", ")}">${tiers.filter((t) => t[1] > 0).map((t) => `<div style="flex:${t[1].toFixed(2)} 0 0;background:${t[2]}"></div>`).join("")}</div>
      <div class="mini-legend">${tiers.map((t) => `<span><i style="background:${t[2]}"></i>${t[0]} <b class="num">${ppct(t[1])}</b></span>`).join("")}</div>
    </div>
    <div class="facts">
      ${fact("Top gainer 24h", g ? `${esc(g.symbol)} ${pct(g.change_24h)}` : "–", g ? smoney(g.pnl_24h) : "", g ? tone(g.change_24h) : "")}
      ${fact("Top 5 share", ppct(s.top5_share), "of portfolio value")}
      ${fact("Weighted market cap", money(s.weighted_market_cap, { compact: true }), "excl. stablecoins")}
      ${fact("From 7d high", pct(s.drawdown_7d), `High ${money(s.high_7d, { cents: false })}`, tone(s.drawdown_7d))}
    </div>
  </section>`;
}

/* ---------- holdings table ---------- */
const COLS = [
  { key: "symbol", label: "Asset", l: true },
  { key: "price", label: "Price", sm: true },
  { key: "change_24h", label: "24h" },
  { key: "change_7d", label: "7d", md: true },
  { key: "change_30d", label: "30d", md: true },
  { key: "amount", label: "Holdings", sm: true },
  { key: "value", label: "Value" },
  { key: "pnl_24h", label: "24h PnL", sm: true },
  { key: "allocation", label: "Allocation", sm: true },
  { key: "market_cap", label: "Market cap", md: true },
  { key: "volume_24h", label: "24h volume", md: true, lg: true },
  { key: null, label: "7d", l: true, sm: true },
];

function holdingsCard(v, rows) {
  const { key, dir } = state.sort;
  const sorted = [...rows].sort((a, b) => {
    const x = a[key], y = b[key];
    if (x == null) return 1;
    if (y == null) return -1;
    return (typeof x === "string" ? x.localeCompare(y) : x - y) * dir;
  });
  const head = COLS.map((c) => {
    const sortAttr = c.key && key === c.key ? ` aria-sort="${dir > 0 ? "ascending" : "descending"}"` : "";
    const arrow = c.key && key === c.key ? (dir > 0 ? " ↑" : " ↓") : "";
    const label = c.key ? `<button data-sort="${c.key}">${c.label}${arrow}</button>` : c.label;
    const cls = [c.l && "l", c.md && "hide-md", c.lg && "hide-lg", c.sm && "hide-sm"].filter(Boolean).join(" ");
    return `<th scope="col" class="${cls}"${sortAttr}>${label}</th>`;
  }).join("");
  const body = sorted.map((r) => row(r) + (state.open.has(r.id) ? detail(r) : "")).join("")
    || `<tr><td colspan="${COLS.length}" class="l empty">No assets match.</td></tr>`;
  return `<section class="card holdings" aria-labelledby="hold-h">
    <div class="holdings-head">
      <div><h2 id="hold-h">Holdings</h2><div class="meta" style="margin-top:2px">${rows.length} of ${v.holdings.length} assets · select a row for details</div></div>
      <div class="toolbar">
        <button class="tool-btn" id="dust" aria-pressed="${state.hideDust}">Hide dust &lt; $1</button>
        <button class="tool-btn" id="auto" aria-pressed="${state.auto}">Auto-refresh 60s</button>
        <button class="tool-btn" id="csv">Export CSV</button>
      </div>
    </div>
    <div class="tbl-wrap"><table class="tbl"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>
  </section>`;
}

function row(r) {
  const open = state.open.has(r.id);
  const chains = r.chains.map((c) => `<span class="chain">${esc(c)}</span>`).join("");
  return `<tr class="row${open ? " open" : ""}" data-id="${esc(r.id)}">
    <td><div class="asset">
      <button class="expand" data-toggle="${esc(r.id)}" aria-expanded="${open}" aria-label="${open ? "Hide" : "Show"} ${esc(r.symbol)} details">
        <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M4.5 3l3 3-3 3"/></svg></button>
      ${coin(r)}
      <div><div class="sym-line"><span class="sym">${esc(r.symbol)}</span>${chains}</div><div class="name">${esc(r.name)}</div></div>
    </div></td>
    <td class="num hide-sm">${money(r.price)}</td>
    <td>${pill(r.change_24h)}</td>
    <td class="num hide-md ${tone(r.change_7d)}">${pct(r.change_7d)}</td>
    <td class="num hide-md ${tone(r.change_30d)}">${pct(r.change_30d)}</td>
    <td class="num flat hide-sm">${amount(r.amount)}</td>
    <td class="num" style="font-weight:650">${money(r.value)}</td>
    <td class="num hide-sm ${tone(r.pnl_24h)}">${smoney(r.pnl_24h)}</td>
    <td class="hide-sm"><div class="alloc-cell"><span class="num">${ppct(r.allocation)}</span><div class="mini-track"><div style="width:${Math.max(r.allocation ?? 0, 2).toFixed(1)}%"></div></div></div></td>
    <td class="num hide-md">${money(r.market_cap, { compact: true })}${r.market_cap_rank ? `<div class="rank">#${r.market_cap_rank}</div>` : ""}</td>
    <td class="num hide-md hide-lg flat">${money(r.volume_24h, { compact: true })}</td>
    <td class="l hide-sm">${spark(r.sparkline, r.change_7d)}</td>
  </tr>`;
}

function detail(r) {
  const stat = (k, v, extra = "") => `<div class="stat"><div class="k">${k}</div><div class="v num">${v}</div>${extra}</div>`;
  const pos = r.range_24h_position == null ? null : Math.max(0, Math.min(1, r.range_24h_position));
  const holders = r.wallets.map((w) => `<tr><td>${esc(w.wallet)}</td><td><span class="chain">${esc(w.chain)}</span></td>
    <td class="num flat" style="text-align:right">${amount(w.amount)} ${esc(r.symbol)}</td><td class="num" style="text-align:right;font-weight:600">${money(w.value)}</td></tr>`).join("");
  const ath = r.ath_date ? new Date(r.ath_date).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "";
  return `<tr class="detail"><td colspan="${COLS.length}"><div class="detail-grid">
    <div class="holders"><div class="label-sm">Where you hold ${esc(r.symbol)}</div><table><tbody>${holders}</tbody></table></div>
    <div class="stats">
      ${stat("24h range", `${money(r.low_24h)} – ${money(r.high_24h)}`, pos == null ? "" : `<div class="range" title="Price is at ${Math.round(pos * 100)}% of today's range"><i style="left:${(pos * 100).toFixed(1)}%"></i></div>`)}
      ${stat("All-time high", money(r.ath), `<div class="s ${tone(r.ath_change)}">${pct(r.ath_change)}${ath ? ` · ${ath}` : ""}</div>`)}
      ${stat("Fully diluted value", money(r.fdv, { compact: true }), r.mcap_fdv_ratio == null ? "" : `<div class="s">Mcap / FDV ${r.mcap_fdv_ratio.toFixed(2)}${r.mcap_fdv_ratio < 0.5 ? " · large unlocks ahead" : ""}</div>`)}
      ${stat("Liquidity", r.volume_mcap_ratio == null ? "–" : (r.volume_mcap_ratio * 100).toFixed(2) + "%", `<div class="s">24h volume / market cap</div>`)}
      ${stat("Circulating supply", r.circulating_pct == null ? "–" : `${ppct(r.circulating_pct)} of max`)}
      ${stat("1-year change", `<span class="${tone(r.change_1y)}">${pct(r.change_1y)}</span>`)}
      ${stat("Cost basis", r.cost_basis == null ? "–" : money(r.cost_basis, { cents: false }), r.avg_cost == null ? "" : `<div class="s">avg ${money(r.avg_cost)} / ${esc(r.symbol)}</div>`)}
      ${stat("Unrealized PnL", r.unrealized_pnl == null ? "–" : `<span class="${tone(r.unrealized_pnl)}">${smoney(r.unrealized_pnl)}</span>`, r.unrealized_pnl_pct == null ? "" : `<div class="s ${tone(r.unrealized_pnl_pct)}">${pct(r.unrealized_pnl_pct)}</div>`)}
    </div>
  </div></td></tr>`;
}

function spark(points, change, w = 96, h = 32) {
  if (!points || points.length < 2) return "";
  const lo = Math.min(...points), hi = Math.max(...points), span = hi - lo || 1;
  const d = points.map((p, i) => `${i ? "L" : "M"}${((i / (points.length - 1)) * w).toFixed(1)} ${(h - 3 - ((p - lo) / span) * (h - 6)).toFixed(1)}`).join(" ");
  const c = change == null ? "var(--muted)" : change >= 0 ? "var(--up)" : "var(--down)";
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img" aria-label="7 day trend ${pct(change)}"><path d="${d}" style="stroke:${c}"/></svg>`;
}

/* ---------- hero chart: 7d value, crosshair tooltip ---------- */
function drawChart(points) {
  const el = $("#chart"), days = $("#days");
  if (!el) return;
  if (!points.length) { el.innerHTML = `<div class="empty" style="color:rgba(255,255,255,.8)">No price history yet.</div>`; days.innerHTML = ""; return; }
  const W = Math.max(el.clientWidth, 200), H = el.clientHeight || 200;
  const vals = points.map((p) => p.v);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo) * 0.12 || hi * 0.01 || 1;
  lo -= pad; hi += pad;
  const x = (i) => (points.length === 1 ? W : (i / (points.length - 1)) * W);
  const y = (v) => (1 - (v - lo) / (hi - lo)) * H;
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(p.v).toFixed(1)}`).join(" ");
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Portfolio value over the last 7 days, from ${money(vals[0])} to ${money(vals[vals.length - 1])}">
      <defs><linearGradient id="heroFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity="0.38"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient></defs>
      <path d="${line} L${W} ${H} L0 ${H} Z" fill="url(#heroFill)"/>
      <path class="line" d="${line}"/>
      <line class="cross" y1="0" y2="${H}" visibility="hidden"/>
    </svg><div class="end-dot" style="left:${x(points.length - 1)}px;top:${y(vals[vals.length - 1])}px"></div>`;
  days.innerHTML = points.map((p, i) => {
    const t = new Date(p.t);
    return t.getHours() === 0 && i > 0 && i < points.length - 6
      ? `<span style="left:${((x(i) / W) * 100).toFixed(2)}%">${t.toLocaleDateString(undefined, { weekday: "short" })}</span>` : "";
  }).join("");

  const svg = $("svg", el), cross = $(".cross", el), dot = $(".end-dot", el), tip = $("#tooltip");
  const last = vals[vals.length - 1];
  const move = (ev) => {
    const r = svg.getBoundingClientRect();
    const i = Math.max(0, Math.min(points.length - 1, Math.round(((ev.clientX - r.left) / r.width) * (points.length - 1))));
    const p = points[i];
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
    dot.style.left = `${x(i)}px`; dot.style.top = `${y(p.v)}px`;
    const chg = last - p.v;
    tip.innerHTML = `<b class="num">${money(p.v)}</b>${new Date(p.t).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" })}<br><span class="${tone(chg)}">${smoney(chg)} to now</span>`;
    tip.hidden = false;
    const tx = Math.min(ev.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
    tip.style.left = `${tx}px`; tip.style.top = `${Math.max(8, ev.clientY - tip.offsetHeight - 14)}px`;
  };
  const leave = () => {
    cross.setAttribute("visibility", "hidden"); tip.hidden = true;
    dot.style.left = `${x(points.length - 1)}px`; dot.style.top = `${y(last)}px`;
  };
  svg.addEventListener("pointermove", move);
  svg.addEventListener("pointerdown", move);
  svg.addEventListener("pointerleave", leave);
}

/* ---------- CSV export ---------- */
function exportCsv() {
  const v = state.data.views[state.view];
  const cols = ["symbol", "name", "amount", "price", "value", "allocation", "change_24h", "change_7d", "change_30d", "pnl_24h", "market_cap", "volume_24h", "cost_basis", "unrealized_pnl"];
  const q = (x) => (x == null ? "" : /[",\n]/.test(String(x)) ? `"${String(x).replace(/"/g, '""')}"` : String(x));
  const csv = [cols.join(","), ...v.holdings.map((r) => cols.map((c) => q(r[c])).join(","))].join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = `portfolio-${state.view === "all" ? "all" : state.view.replace(/\W+/g, "-")}-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/* ---------- palette menu ---------- */
function paletteMenu() {
  let menu = $("#palette-menu");
  if (menu) { menu.remove(); return; }
  menu = document.createElement("div");
  menu.id = "palette-menu";
  menu.setAttribute("role", "menu");
  menu.className = "card";
  const cur = document.documentElement.dataset.palette;
  menu.innerHTML = `<div class="label-sm" style="padding:4px 8px 6px">Color palette</div>` + PALETTES.map(([k, n]) =>
    `<button role="menuitemradio" data-pal="${k}" aria-checked="${k === cur}"><span class="pal-sw pal-${k}"></span>${n}${k === "aurora" ? '<span class="meta">default</span>' : ""}</button>`).join("");
  const r = $("#palette").getBoundingClientRect();
  Object.assign(menu.style, { position: "fixed", top: `${r.bottom + 8}px`, right: `${Math.max(8, window.innerWidth - r.right)}px` });
  document.body.appendChild(menu);
  menu.querySelector(`[data-pal="${cur}"]`)?.focus();
}

/* ---------- events (delegated, so re-renders need no re-binding) ---------- */
function setView(v) {
  state.view = v;
  state.open.clear();
  renderChrome();
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
}
document.addEventListener("click", (e) => {
  const t = e.target.closest("button, tr.row");
  const menu = $("#palette-menu");
  if (menu && !e.target.closest("#palette-menu, #palette")) menu.remove();
  if (!t) return;
  if (t.dataset.pal) { setPalette(t.dataset.pal); menu?.remove(); return; }
  if (t.id === "palette") return paletteMenu();
  if (t.id === "refresh") return load(true);
  if (!state.data) return;
  if (t.dataset.view) return setView(t.dataset.view);
  if (t.dataset.alloc) {
    state.alloc = t.dataset.alloc;
    t.parentElement.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", b === t));
    return renderAlloc(state.data.views[state.view]);
  }
  if (t.dataset.sort) {
    const k = t.dataset.sort;
    state.sort = state.sort.key === k ? { key: k, dir: -state.sort.dir } : { key: k, dir: k === "symbol" ? 1 : -1 };
    return render();
  }
  if (t.id === "dust") { state.hideDust = !state.hideDust; return render(); }
  if (t.id === "auto") { state.auto = !state.auto; store.set("auto", state.auto ? "1" : "0"); t.setAttribute("aria-pressed", state.auto); return; }
  if (t.id === "csv") return exportCsv();
  const id = t.dataset.toggle || (t.matches("tr.row") ? t.dataset.id : null);
  if (id) {
    state.open.has(id) ? state.open.delete(id) : state.open.add(id);
    render();
  }
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#palette-menu")?.remove(); });
$("#search").addEventListener("input", (e) => { state.search = e.target.value; if (state.data) render(); });
state.auto = store.get("auto") !== "0";
setInterval(() => state.auto && !document.hidden && load(), 60_000);
let resizeT;
window.addEventListener("resize", () => {
  clearTimeout(resizeT);
  resizeT = setTimeout(() => state.data && drawChart(state.data.views[state.view].history), 120);
});
load();
