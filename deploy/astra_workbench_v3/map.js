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
  { id: "gamma", label: "Gamma 结构", h: 116 },
  { id: "trade", label: "成交与成本", h: 60 },
];
const AXIS_H = 26;
// Lane under the tracks for the expected-move percentages, so they never sit on track content.
const SIGMA_LANE = 18;

/** Rough rendered width: CJK glyphs about one em, digits and Latin about 0.6 em. */
function textWidth(text, size = 11) {
  let w = 0;
  for (const ch of String(text)) w += /[⺀-￿]/.test(ch) ? size : size * 0.6;
  return w;
}

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
  const totalH = AXIS_H + TRACKS.reduce((a, t) => a + t.h, 0) + SIGMA_LANE + 4;
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

  // Labels never sit on each other: each tries its candidate spots in order and is left
  // to the hover tip when every spot is taken. Glyphs and bars reserve their own boxes.
  const taken = [];
  const reserve = (x0, y0, x1, y1) => taken.push({ x0, y0, x1, y1 });
  const boxOf = (tx, ty, text, anchor, size) => {
    const w = textWidth(text, size);
    const x0 = anchor === "middle" ? tx - w / 2 : anchor === "end" ? tx - w : tx;
    return { x0: x0 - 2, x1: x0 + w + 2, y0: ty - size, y1: ty + 3 };
  };
  const free = (b) => b.x0 >= PAD_L - 4 && b.x1 <= W - PAD_R + 4
    && !taken.some((o) => b.x0 < o.x1 && b.x1 > o.x0 && b.y0 < o.y1 && b.y1 > o.y0);
  const place = (text, spots, attrs = {}) => {
    const size = attrs["font-size"] || 11;
    for (const sp of spots) {
      const anchor = sp.anchor || "middle";
      const b = boxOf(sp.x, sp.y, text, anchor, size);
      if (!free(b)) continue;
      taken.push(b);
      root.append(s("text", { ...attrs, class: `halo ${attrs.class || ""}`.trim(), x: sp.x, y: sp.y, "text-anchor": anchor, text }));
      return true;
    }
    return false;
  };

  const sx = x(model.spot);
  reserve(sx - 36, 0, sx + 36, 20);

  // σ bands behind everything; their percentages live in their own lane under the tracks.
  const sigmaLabels = [];
  if (isNum(model.sigmaPct)) {
    const sig = model.spot * model.sigmaPct / 100;
    for (const k of [2, 1]) {
      const a = Math.max(lo, model.spot - k * sig), b = Math.min(hi, model.spot + k * sig);
      if (k === 1) root.append(s("rect", { class: "sigma", x: x(a), y: plotTop, width: Math.max(0, x(b) - x(a)), height: plotBottom - plotTop }));
      for (const edge of [model.spot - k * sig, model.spot + k * sig]) {
        if (!inView(edge)) continue;
        root.append(s("line", { class: "sigma-edge", x1: x(edge), x2: x(edge), y1: plotTop, y2: plotBottom + 4 }));
        sigmaLabels.push([x(edge), `${edge > model.spot ? "+" : "−"}${(k * model.sigmaPct).toFixed(2)}%`]);
      }
    }
  }

  // Track separators and labels
  for (const t of TRACKS) {
    const g = T[t.id];
    root.append(s("line", { x1: PAD_L, x2: W - PAD_R, y1: g.y1, y2: g.y1, stroke: "var(--rule-2)" }));
    root.append(s("text", { class: "track-label", x: 4, y: g.mid + 4, text: t.label }));
  }

  // Axis: a tick label under the spot tag is skipped rather than covered.
  const axis = s("g", { class: "axis" });
  const step = niceStep(hi - lo);
  for (let p = Math.ceil(lo / step) * step; p <= hi; p += step) {
    axis.append(s("line", { x1: x(p), x2: x(p), y1: AXIS_H - 6, y2: plotBottom, "stroke-opacity": 0.35 }));
    if (Math.abs(x(p) - sx) > 46) axis.append(s("text", { x: x(p), y: AXIS_H - 10, "text-anchor": "middle", text: kusd(p) }));
  }
  root.append(axis);

  const hits = [];
  const addHit = (x0, x1, y0, y1, title, body) => hits.push({ x0, x1, y0, y1, title, body });

  // ── liability track
  const L = T.liability;
  const beLabels = [];
  for (const side of ["put", "call"]) {
    const b = model.sides[side];
    if (!b || !isNum(b.short) || !isNum(b.long)) continue;
    const a = Math.max(lo, Math.min(b.short, b.long)), z = Math.min(hi, Math.max(b.short, b.long));
    root.append(s("rect", { class: `zone-${side}`, x: x(a), y: L.y0 + 10, width: Math.max(0, x(z) - x(a)), height: L.h - 20, rx: 3,
      fill: `url(#liab-${side})` }));
    const name = side === "put" ? "Put" : "Call";
    for (const [v, lab] of [[b.short, "短腿"], [b.long, "保护腿"]]) {
      if (!inView(v)) continue;
      root.append(s("line", { class: `strike-${side}`, x1: x(v), x2: x(v), y1: L.y0 + 6, y2: L.y1 - 6 }));
      const inward = (side === "put") === (lab === "短腿") ? "end" : "start";
      const dx = inward === "end" ? -5 : 5;
      const text = `${name}${lab} ${usd(v)}`;
      place(text, [{ x: x(v) + dx, y: L.y0 + 24, anchor: inward }, { x: x(v) + dx, y: L.y0 + 40, anchor: inward },
        { x: x(v) - dx, y: L.y0 + 24, anchor: inward === "end" ? "start" : "end" }],
        { class: `be-${side}`, fill: `var(--${side})`, "font-weight": 600 });
      addHit(x(v) - 5, x(v) + 5, L.y0, L.y1, `${name} ${lab} ${usd(v)}`, b.title || "");
    }
    if (isNum(b.breakeven) && inView(b.breakeven)) {
      root.append(s("line", { class: `be be-${side}`, x1: x(b.breakeven), x2: x(b.breakeven), y1: L.y0 + 32, y2: L.y1 - 6, stroke: `var(--${side})` }));
      beLabels.push([side, x(b.breakeven)]);
      addHit(x(b.breakeven) - 5, x(b.breakeven) + 5, L.y0, L.y1, `${name} 盈亏平衡 ${usd(b.breakeven)}`,
        isNum(b.beOutsidePct) ? `距现价 ${pct(b.beOutsidePct, 2)}${isNum(b.beMovePct) ? `，约为到期前预期波动的 ${Math.round(b.beMovePct * 100)}%` : ""}` : "");
    }
  }
  for (const [side, bx] of beLabels) {
    place("盈亏平衡", [{ x: bx, y: L.y1 - 12 }, { x: bx + 6, y: L.y1 - 12, anchor: "start" }, { x: bx - 6, y: L.y1 - 12, anchor: "end" },
      { x: bx, y: L.y0 + 40 }], { class: `be-${side}`, fill: `var(--${side})` });
  }

  // ── gamma track: bars above and below the zero line, position markers in the bottom lane
  const G = T.gamma;
  root.append(s("line", { x1: PAD_L, x2: W - PAD_R, y1: G.mid, y2: G.mid, stroke: "var(--rule)" }));
  const gexMax = Math.max(1, ...model.walls.filter((w) => isNum(w.gex)).map((w) => Math.abs(w.gex)));
  const barUp = G.h / 2 - 16, barDown = G.h / 2 - 30;
  const markerY = G.y1 - 9;
  const outside = { left: [], right: [] };
  const ordered = model.walls.slice().sort((a, b) => Math.abs(b.gex || 0) - Math.abs(a.gex || 0));
  for (const w of model.walls) if (isNum(w.price) && !inView(w.price)) (w.price < lo ? outside.left : outside.right).push(w);
  for (const m of model.markers) if (isNum(m.price) && !inView(m.price)) (m.price < lo ? outside.left : outside.right).push(m);
  for (const z of model.zones) if (isNum(z.lo) && isNum(z.hi) && (z.hi < lo || z.lo > hi)) (z.hi < lo ? outside.left : outside.right).push({ ...z, price: (z.lo + z.hi) / 2 });

  // Out-of-view levels first: compact arrows at the edges, nearest two named.
  const edge = (items, left) => {
    if (!items.length) return;
    const sorted = items.slice().sort((a, b) => left ? b.price - a.price : a.price - b.price);
    const shown = sorted.slice(0, 2).map((i) => `${i.short || i.label} ${kusd(i.price)}`);
    const text = shown.join("  ") + (sorted.length > 2 ? `  等 ${sorted.length} 项` : "");
    place(left ? `◂ ${text}` : `${text} ▸`, [{ x: left ? PAD_L + 4 : W - PAD_R - 4, y: G.y0 + 13, anchor: left ? "start" : "end" }],
      { fill: "var(--ink-3)" });
  };
  edge(outside.left, true);
  edge(outside.right, false);

  const bars = [];
  for (const w of ordered) {
    if (!isNum(w.price) || !inView(w.price)) continue;
    const px = x(w.price);
    if (isNum(w.gex)) {
      const up = w.gex >= 0;
      const hgt = Math.max(4, Math.sqrt(Math.abs(w.gex) / gexMax) * (up ? barUp : barDown));
      const width = 11;
      const offset = w.kind === "zero_dte" ? 6.5 : -6.5;
      const bx = px - width / 2 + offset, by = up ? G.mid - hgt : G.mid;
      root.append(s("rect", { class: `${up ? "wall-pos" : "wall-neg"} ${w.kind}`, x: bx, y: by, width, height: hgt, rx: 2 }));
      reserve(bx, by, bx + width, by + hgt);
      bars.push({ w, up, hgt, lx: px + offset });
    } else {
      root.append(s("line", { x1: px, x2: px, y1: G.y0 + 18, y2: G.mid + barDown, stroke: "var(--depth)", "stroke-width": 1.5, "stroke-dasharray": "1 3" }));
    }
    addHit(px - 6, px + 6, G.y0, G.y1, `${w.label} ${usd(w.price)}`,
      [isNum(w.gex) ? `GEX ${compactUsd(w.gex)}` : null, liabilityText(w.price, model)].filter(Boolean).join("；"));
  }

  // Position markers: glyphs reserve their boxes before any value label is placed.
  const markerLabels = [];
  for (const m of model.markers) {
    if (!isNum(m.price) || !inView(m.price)) continue;
    const px = x(m.price);
    const yy = m.track === "trade" ? T.trade.y0 + 17 : m.lane === "top" ? G.y0 + 9 : markerY;
    if (m.glyph === "diamond") root.append(s("path", { d: `M${px} ${yy - 6} L${px + 6} ${yy} L${px} ${yy + 6} L${px - 6} ${yy} Z`, fill: m.color || "var(--ink)" }));
    else if (m.glyph === "ring") root.append(s("circle", { cx: px, cy: yy, r: 5, fill: "var(--sheet)", stroke: m.color || "var(--ink)", "stroke-width": 2 }));
    else if (m.glyph === "square") root.append(s("rect", { x: px - 5, y: yy - 5, width: 10, height: 10, rx: 1.5, fill: m.color || "var(--ink)" }));
    else if (m.glyph === "pin") root.append(s("path", { d: `M${px - 6} ${yy - 5} L${px + 6} ${yy - 5} L${px} ${yy + 6} Z`, fill: m.color || "var(--ink)" }));
    else root.append(s("path", { d: `M${px - 6} ${yy + 5} L${px + 6} ${yy + 5} L${px} ${yy - 6} Z`, fill: m.color || "var(--ink)" }));
    reserve(px - 7, yy - 7, px + 7, yy + 7);
    if (m.short) markerLabels.push([m.short, px, yy]);
    addHit(px - 7, px + 7, yy - 9, yy + 9, `${m.label} ${usd(m.price)}`, [m.note, liabilityText(m.price, model)].filter(Boolean).join("；"));
  }
  for (const [text, px, yy] of markerLabels) {
    place(text, [{ x: px + 9, y: yy + 4, anchor: "start" }, { x: px - 9, y: yy + 4, anchor: "end" }, { x: px, y: yy - 10 }],
      { fill: "var(--ink-2)" });
  }
  // Value labels on the largest walls only, above a positive bar or below a negative one.
  for (const { w, up, hgt, lx } of bars) {
    if (Math.abs(w.gex) < gexMax * 0.35) continue;
    const ty = up ? G.mid - hgt - 4 : G.mid + hgt + 11;
    place(compactUsd(w.gex), [{ x: lx, y: ty }, { x: lx + 8, y: ty, anchor: "start" }, { x: lx - 8, y: ty, anchor: "end" }],
      { fill: "var(--ink-3)", "font-size": 10 });
  }

  // ── trade track: cost diamonds in the upper lane (above), KPF zones in the lower lane
  const R = T.trade;
  for (const z of model.zones) {
    if (!isNum(z.lo) || !isNum(z.hi) || z.hi < lo || z.lo > hi) continue;
    const a = x(Math.max(lo, z.lo)), b = x(Math.min(hi, z.hi));
    root.append(s("rect", { class: "kpf", x: a, y: R.y0 + 32, width: Math.max(3, b - a), height: R.h - 40, rx: 4 }));
    addHit(a, Math.max(a + 6, b), R.y0 + 28, R.y1, `${z.label} ${usd(z.lo)}–${usd(z.hi)}`,
      [z.note, z.containsShort ? "区间包含短腿" : null].filter(Boolean).join("；"));
  }

  // σ percentages in their own lane below the tracks
  for (const [ex, text] of sigmaLabels) {
    place(text, [{ x: ex + 3, y: plotBottom + 13, anchor: "start" }, { x: ex - 3, y: plotBottom + 13, anchor: "end" }], { fill: "var(--ink-3)" });
  }

  // Spot line and tag
  root.append(s("line", { class: "spot-line", x1: sx, x2: sx, y1: AXIS_H - 2, y2: plotBottom }));
  const tag = s("g", { class: "spot-tag" });
  tag.append(s("rect", { x: sx - 34, y: 0, width: 68, height: 18, rx: 9 }));
  tag.append(s("text", { x: sx, y: 13, "text-anchor": "middle", text: usd(model.spot) }));
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

export function legend(el, model) {
  el.innerHTML = "";
  const items = [
    ["var(--put-soft)", "Put 责任区（短腿至保护腿）"],
    ["var(--call-soft)", "Call 责任区"],
    ["var(--sigma)", "到期前隐含预期波动（虚线为 2 倍）"],
    ["var(--depth)", "正 Gamma 墙"],
    ["var(--neg-gamma)", "负 Gamma 墙"],
    ["color-mix(in srgb, var(--ink-3) 30%, transparent)",
      model?.kpfAsOf ? `KPF 成交接受区（成交数据截至 ${new Date(model.kpfAsOf).toISOString().slice(5, 10)}）` : "KPF 成交接受区"],
    ["var(--ink-3)", "◆ 链上持有者成本"],
    ...(model?.expiry ? [["var(--put)", "■ 本期 Put 墙"], ["var(--call)", "■ 本期 Call 墙"],
      ["var(--ink)", `▼ 本期 Pin（${model.expiry.expiry} 到期${model.expiry.stale ? "，数据偏旧" : ""}）`]]
      : [["var(--ink-3)", "本期墙暂缺"]]),
  ];
  for (const [c, t] of items) {
    const span = document.createElement("span");
    const em = document.createElement("em"); em.style.background = c; span.append(em, t); el.append(span);
  }
}
