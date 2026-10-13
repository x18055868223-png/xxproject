// Price map: one horizontal price axis shared by both sides of the book.
// Lower prices on the left (Put liability), higher on the right (Call liability).
import { s, clear, usd, kusd, pct, compactUsd, isNum } from "./format.js";

// The SVG is drawn at its on-screen width so labels keep their real pixel size;
// below MIN_W the band scrolls sideways instead of shrinking text.
const MIN_W = 760;
let W = 1400;
const PAD_L = 92;
const PAD_R = 24;
const TRACKS = [
  { id: "liability", label: "责任区", h: 92 },
  { id: "gamma", label: "Gamma 结构", h: 108 },
  { id: "trade", label: "成交与成本", h: 46 },
];
const AXIS_H = 26;

function niceStep(span) {
  const raw = span / 9;
  const p = 10 ** Math.floor(Math.log10(raw));
  for (const m of [1, 2, 2.5, 5, 10]) if (raw <= m * p) return m * p;
  return 10 * p;
}

/** Choose a price window that keeps both books and ~2σ in view without far outliers. */
export function priceWindow(model) {
  const spot = model.spot;
  const pts = [];
  for (const side of ["put", "call"]) {
    const b = model.sides[side];
    if (!b) continue;
    for (const v of [b.short, b.long, b.breakeven]) if (isNum(v)) pts.push(v);
  }
  const sig = isNum(model.sigmaPct) ? spot * model.sigmaPct / 100 : spot * 0.015;
  pts.push(spot - 2.2 * sig, spot + 2.2 * sig);
  let lo = Math.min(...pts), hi = Math.max(...pts);
  const span = hi - lo;
  lo -= span * 0.08; hi += span * 0.08;
  return [lo, hi];
}

function liabilityText(price, model) {
  const parts = [];
  for (const [side, name] of [["put", "Put"], ["call", "Call"]]) {
    const b = model.sides[side];
    if (!b || !isNum(b.short) || !isNum(b.long)) continue;
    const inZone = side === "put" ? price <= b.short && price >= b.long : price >= b.short && price <= b.long;
    const beyond = side === "put" ? price < b.long : price > b.long;
    if (inZone) parts.push(`位于 ${name} 短腿至保护腿之间`);
    else if (beyond) parts.push(`越过 ${name} 保护腿`);
  }
  return parts.length ? parts.join("；") : "在两侧责任区之外";
}

export function renderMap(svg, tip, model, { focus = null, onPick = null } = {}) {
  clear(svg);
  const host = svg.parentElement;
  W = Math.max(MIN_W, Math.round((host && host.clientWidth) || 1400));
  svg.style.minWidth = `${MIN_W}px`;
  if (!model || !isNum(model.spot)) {
    svg.setAttribute("viewBox", `0 0 ${W} 120`);
    svg.append(s("text", { x: W / 2, y: 64, "text-anchor": "middle", class: "track-label", text: "等待现价与候选数据" }));
    return;
  }
  const [lo, hi] = priceWindow(model);
  const plotW = W - PAD_L - PAD_R;
  const x = (p) => PAD_L + ((p - lo) / (hi - lo)) * plotW;
  const inView = (p) => p >= lo && p <= hi;
  const totalH = AXIS_H + TRACKS.reduce((a, t) => a + t.h, 0) + 8;
  svg.setAttribute("viewBox", `0 0 ${W} ${totalH}`);

  // Liability deepens from the short leg toward the protective leg.
  const defs = s("defs", {},
    s("linearGradient", { id: "liab-put", x1: "1", x2: "0", y1: "0", y2: "0" },
      s("stop", { offset: "0", "stop-color": "var(--put)", "stop-opacity": 0.06 }),
      s("stop", { offset: "1", "stop-color": "var(--put)", "stop-opacity": 0.34 })),
    s("linearGradient", { id: "liab-call", x1: "0", x2: "1", y1: "0", y2: "0" },
      s("stop", { offset: "0", "stop-color": "var(--call)", "stop-opacity": 0.06 }),
      s("stop", { offset: "1", "stop-color": "var(--call)", "stop-opacity": 0.34 })),
  );
  svg.append(defs);
  const root = s("g", { class: focus ? `focus-${focus}` : "" });
  svg.append(root);

  // Track geometry
  let y = AXIS_H;
  const T = {};
  for (const t of TRACKS) { T[t.id] = { y0: y, y1: y + t.h, mid: y + t.h / 2, h: t.h }; y += t.h; }
  const plotTop = AXIS_H, plotBottom = y;

  // σ bands behind everything
  if (isNum(model.sigmaPct)) {
    const sig = model.spot * model.sigmaPct / 100;
    for (const k of [2, 1]) {
      const a = Math.max(lo, model.spot - k * sig), b = Math.min(hi, model.spot + k * sig);
      if (k === 1) root.append(s("rect", { class: "sigma", x: x(a), y: plotTop, width: Math.max(0, x(b) - x(a)), height: plotBottom - plotTop }));
      for (const edge of [model.spot - k * sig, model.spot + k * sig]) {
        if (!inView(edge)) continue;
        root.append(s("line", { class: "sigma-edge", x1: x(edge), x2: x(edge), y1: plotTop, y2: plotBottom }));
        root.append(s("text", { x: x(edge) + 3, y: plotBottom - 4, fill: "var(--ink-3)",
          text: `${edge > model.spot ? "+" : "−"}${(k * model.sigmaPct).toFixed(2)}%` }));
      }
    }
  }

  // Track separators and labels
  for (const t of TRACKS) {
    const g = T[t.id];
    root.append(s("line", { x1: PAD_L, x2: W - PAD_R, y1: g.y1, y2: g.y1, stroke: "var(--rule-2)" }));
    root.append(s("text", { class: "track-label", x: 4, y: g.mid + 4, text: t.label }));
  }

  // Axis
  const axis = s("g", { class: "axis" });
  const step = niceStep(hi - lo);
  for (let p = Math.ceil(lo / step) * step; p <= hi; p += step) {
    axis.append(s("line", { x1: x(p), x2: x(p), y1: AXIS_H - 6, y2: plotBottom, "stroke-opacity": 0.35 }));
    axis.append(s("text", { x: x(p), y: AXIS_H - 10, "text-anchor": "middle", text: kusd(p) }));
  }
  root.append(axis);

  const hits = [];
  const addHit = (x0, x1, y0, y1, title, body) => hits.push({ x0, x1, y0, y1, title, body });

  // ── liability track
  const L = T.liability;
  for (const side of ["put", "call"]) {
    const b = model.sides[side];
    if (!b || !isNum(b.short) || !isNum(b.long)) continue;
    const a = Math.max(lo, Math.min(b.short, b.long)), z = Math.min(hi, Math.max(b.short, b.long));
    root.append(s("rect", { class: `zone-${side}`, x: x(a), y: L.y0 + 10, width: Math.max(0, x(z) - x(a)), height: L.h - 20, rx: 3,
      fill: `url(#liab-${side})` }));
    const name = side === "put" ? "Put" : "Call";
    for (const [v, lab, cls] of [[b.short, "短腿", `strike-${side}`], [b.long, "保护腿", `strike-${side}`]]) {
      if (!inView(v)) continue;
      root.append(s("line", { class: cls, x1: x(v), x2: x(v), y1: L.y0 + 6, y2: L.y1 - 6 }));
      const anchor = (side === "put") === (lab === "短腿") ? "end" : "start";
      const dx = anchor === "end" ? -5 : 5;
      root.append(s("text", { x: x(v) + dx, y: L.y0 + 22, "text-anchor": anchor, text: `${name}${lab} ${usd(v)}`,
        fill: `var(--${side})`, "font-weight": 600 }));
      addHit(x(v) - 5, x(v) + 5, L.y0, L.y1, `${name} ${lab} ${usd(v)}`, b.title || "");
    }
    if (isNum(b.breakeven) && inView(b.breakeven)) {
      root.append(s("line", { class: `be be-${side}`, x1: x(b.breakeven), x2: x(b.breakeven), y1: L.y0 + 30, y2: L.y1 - 6, stroke: `var(--${side})` }));
      root.append(s("text", { class: `be-${side}`, x: x(b.breakeven), y: L.y1 - 10, "text-anchor": "middle", text: "盈亏平衡", fill: `var(--${side})` }));
      addHit(x(b.breakeven) - 5, x(b.breakeven) + 5, L.y0, L.y1, `${name} 盈亏平衡 ${usd(b.breakeven)}`,
        isNum(b.beOutsidePct) ? `距现价 ${pct(b.beOutsidePct, 2)}${isNum(b.beMovePct) ? `，约为到期前预期波动的 ${Math.round(b.beMovePct * 100)}%` : ""}` : "");
    }
  }

  // ── gamma track
  const G = T.gamma;
  root.append(s("line", { x1: PAD_L, x2: W - PAD_R, y1: G.mid, y2: G.mid, stroke: "var(--rule)" }));
  const gexMax = Math.max(1, ...model.walls.filter((w) => isNum(w.gex)).map((w) => Math.abs(w.gex)));
  const barH = (G.h / 2) - 12;
  const outside = { left: [], right: [] };
  const placed = [];
  const ordered = model.walls.slice().sort((a, b) => Math.abs(b.gex || 0) - Math.abs(a.gex || 0));
  for (const w of ordered) {
    if (!isNum(w.price)) continue;
    if (!inView(w.price)) { (w.price < lo ? outside.left : outside.right).push(w); continue; }
    const px = x(w.price);
    if (isNum(w.gex)) {
      const hgt = Math.max(4, Math.sqrt(Math.abs(w.gex) / gexMax) * barH);
      const up = w.gex >= 0;
      const width = 11;
      const offset = w.kind === "zero_dte" ? 6.5 : -6.5;
      root.append(s("rect", { class: `${up ? "wall-pos" : "wall-neg"} ${w.kind}`, x: px - width / 2 + offset, y: up ? G.mid - hgt : G.mid,
        width, height: hgt, rx: 2 }));
      const lx = px + offset;
      if (Math.abs(w.gex) >= gexMax * 0.35 && !placed.some((p) => p.up === up && Math.abs(p.x - lx) < 34)) {
        placed.push({ x: lx, up });
        root.append(s("text", { x: lx, y: up ? G.mid - hgt - 4 : G.mid + hgt + 11,
          "text-anchor": "middle", fill: "var(--ink-3)", "font-size": 10, text: compactUsd(w.gex) }));
      }
    } else {
      root.append(s("line", { x1: px, x2: px, y1: G.y0 + 8, y2: G.y1 - 8, stroke: "var(--depth)", "stroke-width": 1.5, "stroke-dasharray": "1 3" }));
    }
    addHit(px - 6, px + 6, G.y0, G.y1, `${w.label} ${usd(w.price)}`,
      [isNum(w.gex) ? `GEX ${compactUsd(w.gex)}` : null, liabilityText(w.price, model)].filter(Boolean).join("；"));
  }
  for (const m of model.markers) {
    if (!isNum(m.price)) continue;
    if (!inView(m.price)) { (m.price < lo ? outside.left : outside.right).push(m); continue; }
    const px = x(m.price);
    const yy = m.track === "trade" ? T.trade.mid : G.y1 - 10;
    if (m.glyph === "diamond") root.append(s("path", { d: `M${px} ${yy - 6} L${px + 6} ${yy} L${px} ${yy + 6} L${px - 6} ${yy} Z`, fill: m.color || "var(--ink)" }));
    else if (m.glyph === "ring") root.append(s("circle", { cx: px, cy: yy, r: 5, fill: "var(--sheet)", stroke: m.color || "var(--ink)", "stroke-width": 2 }));
    else root.append(s("path", { d: `M${px - 6} ${yy + 5} L${px + 6} ${yy + 5} L${px} ${yy - 6} Z`, fill: m.color || "var(--ink)" }));
    if (m.short) root.append(s("text", { x: px, y: yy - 9, "text-anchor": "middle", text: m.short, fill: "var(--ink-2)" }));
    addHit(px - 7, px + 7, yy - 9, yy + 9, `${m.label} ${usd(m.price)}`, [m.note, liabilityText(m.price, model)].filter(Boolean).join("；"));
  }

  // ── trade track: KPF zones
  const R = T.trade;
  for (const z of model.zones) {
    if (!isNum(z.lo) || !isNum(z.hi)) continue;
    if (z.hi < lo || z.lo > hi) { (z.hi < lo ? outside.left : outside.right).push({ ...z, price: (z.lo + z.hi) / 2 }); continue; }
    const a = x(Math.max(lo, z.lo)), b = x(Math.min(hi, z.hi));
    root.append(s("rect", { class: "kpf", x: a, y: R.y0 + 12, width: Math.max(3, b - a), height: R.h - 24, rx: 5 }));
    addHit(a, Math.max(a + 6, b), R.y0, R.y1, `${z.label} ${usd(z.lo)}–${usd(z.hi)}`,
      [z.note, z.containsShort ? "区间包含短腿" : null].filter(Boolean).join("；"));
  }

  // Out-of-view levels: compact arrows at the edges
  const edge = (items, left) => {
    if (!items.length) return;
    // Nearest two only; the rest are counted so the edge never turns into a list.
    const sorted = items.slice().sort((a, b) => left ? b.price - a.price : a.price - b.price);
    const shown = sorted.slice(0, 2).map((i) => `${i.short || i.label} ${kusd(i.price)}`);
    const text = shown.join("  ") + (sorted.length > 2 ? `  等 ${sorted.length} 项` : "");
    const xx = left ? PAD_L + 4 : W - PAD_R - 4;
    root.append(s("text", { x: xx, y: G.y0 + 14, "text-anchor": left ? "start" : "end", fill: "var(--ink-3)",
      text: left ? `◂ ${text}` : `${text} ▸` }));
  };
  edge(outside.left, true);
  edge(outside.right, false);

  // Spot line and tag
  const sx = x(model.spot);
  root.append(s("line", { class: "spot-line", x1: sx, x2: sx, y1: AXIS_H - 2, y2: plotBottom }));
  const tag = s("g", { class: "spot-tag" });
  const label = usd(model.spot);
  tag.append(s("rect", { x: sx - 34, y: 0, width: 68, height: 18, rx: 9 }));
  tag.append(s("text", { x: sx, y: 13, "text-anchor": "middle", text: label }));
  root.append(tag);

  // Hover layer
  const layer = s("g");
  for (const hbox of hits) {
    const r = s("rect", { class: "hit", x: hbox.x0, y: hbox.y0, width: Math.max(8, hbox.x1 - hbox.x0), height: hbox.y1 - hbox.y0 });
    r.addEventListener("mouseenter", (ev) => showTip(ev, hbox));
    r.addEventListener("mousemove", (ev) => moveTip(ev));
    r.addEventListener("mouseleave", hideTip);
    layer.append(r);
  }
  root.append(layer);

  function showTip(ev, hbox) {
    tip.innerHTML = "";
    const b = document.createElement("b"); b.textContent = hbox.title; tip.append(b);
    if (hbox.body) tip.append(document.createTextNode(hbox.body));
    tip.hidden = false; moveTip(ev);
  }
  function moveTip(ev) {
    const host = (tip.offsetParent || svg.parentElement).getBoundingClientRect();
    const left = Math.min(ev.clientX - host.left + 14, host.width - 290);
    tip.style.left = `${Math.max(4, left)}px`;
    tip.style.top = `${ev.clientY - host.top + 14}px`;
  }
  function hideTip() { tip.hidden = true; }
  if (onPick) svg.onclick = (ev) => {
    const pt = ev.clientX - svg.getBoundingClientRect().left;
    const price = lo + ((pt / svg.getBoundingClientRect().width) * W - PAD_L) / plotW * (hi - lo);
    onPick(price < model.spot ? "put" : "call");
  };
}

export function legend(el) {
  el.innerHTML = "";
  const items = [
    ["var(--put-soft)", "Put 责任区（短腿至保护腿）"],
    ["var(--call-soft)", "Call 责任区"],
    ["var(--sigma)", "到期前隐含预期波动（虚线为 2 倍）"],
    ["var(--depth)", "正 Gamma 墙"],
    ["var(--neg-gamma)", "负 Gamma 墙"],
    ["color-mix(in srgb, var(--ink-3) 30%, transparent)", "KPF 成交接受区"],
    ["var(--ink-3)", "◆ 链上持有者成本"],
  ];
  for (const [c, t] of items) {
    const span = document.createElement("span");
    const em = document.createElement("em"); em.style.background = c; span.append(em, t); el.append(span);
  }
}
