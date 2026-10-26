// Price map: one horizontal price axis shared by both sides of the book.
// Lower prices on the left (Put liability), higher on the right (Call liability).
// Tracks by basis: the policy itself, this expiry's own option structure, then background
// levels of other bases (all-expiry GEX, on-chain cost, trade zones). Only the spot crosses all.
import { s, h, clear, usd, kusd, pct, compactUsd, isNum } from "./format.js";

// The SVG is drawn at its on-screen width so labels keep their real pixel size;
// below MIN_W the band scrolls sideways instead of shrinking text.
const MIN_W = 760;
let W = 1400;
const PAD_L = 92;
const PAD_R = 24;
const TRACKS = [
  { id: "liability", label: "责任区", h: 92 },
  { id: "expiry", label: "本期结构", h: 124 },
  { id: "back", label: "背景价位", h: 80 },
];
const AXIS_H = 26;
// Lane under the tracks: expected-move percentages, then a footnote row for levels outside the view.
const SIGMA_LANE = 34;

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

const signed = (v, d = 2) => `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(d)}`;

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
  // a glyph with its label beside it (right, then left, then above)
  const besideSpots = (px, yy) => [{ x: px + 9, y: yy + 4, anchor: "start" }, { x: px - 9, y: yy + 4, anchor: "end" }, { x: px, y: yy - 10 }];

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
  // A side without a candidate keeps its half of the track, with the reason, instead of vanishing.
  for (const side of ["put", "call"]) {
    if (model.sides[side] || !model.missing || !model.missing[side]) continue;
    const name = side === "put" ? "Put" : "Call";
    const a = side === "put" ? PAD_L + 6 : Math.min(W - PAD_R - 60, sx + 46);
    const z = side === "put" ? Math.max(PAD_L + 60, sx - 46) : W - PAD_R - 6;
    root.append(s("rect", { class: "zone-missing", x: a, y: L.y0 + 10, width: Math.max(0, z - a), height: L.h - 20, rx: 3 }));
    place(`${name} 本轮无候选`, [{ x: (a + z) / 2, y: L.mid - 2 }], { fill: "var(--ink-2)", "font-weight": 600 });
    place(model.missing[side], [{ x: (a + z) / 2, y: L.mid + 14 }], { fill: "var(--ink-3)" });
  }
  for (const [side, bx] of beLabels) {
    place("盈亏平衡", [{ x: bx, y: L.y1 - 12 }, { x: bx + 6, y: L.y1 - 12, anchor: "start" }, { x: bx - 6, y: L.y1 - 12, anchor: "end" },
      { x: bx, y: L.y0 + 40 }], { class: `be-${side}`, fill: `var(--${side})` });
  }

  // ── this expiry's structure: the top dollar-Gamma strikes of each side as bars (the deepest is
  // the wall), the pin on top, open-interest peaks and this expiry's max pain on the bottom lane
  const E = T.expiry;
  const base = E.y1 - 30;
  const barMax = base - (E.y0 + 40);
  const pinY = E.y0 + 13;
  const lowY = E.y1 - 15;
  const pf = model.profile;
  root.append(s("line", { x1: PAD_L, x2: W - PAD_R, y1: base, y2: base, stroke: "var(--rule)" }));
  const later = [];   // labels placed after every glyph and bar has reserved its box
  if (!pf) {
    place("本期期权结构暂缺", [{ x: (PAD_L + W - PAD_R) / 2, y: E.mid }], { fill: "var(--ink-3)" });
  } else {
    const bars = [...pf.put.map((b) => ({ ...b, side: "put" })), ...pf.call.map((b) => ({ ...b, side: "call" }))];
    const gmax = Math.max(1, ...bars.map((b) => b.gex));
    const strikesOf = (side) => new Set(bars.filter((b) => b.side === side).map((b) => b.strike));
    const both = new Set([...strikesOf("put")].filter((k) => strikesOf("call").has(k)));
    for (const b of bars) {
      if (!inView(b.strike)) continue;
      const name = b.side === "put" ? "Put" : "Call";
      const isWall = b.strike === pf.wall[b.side];
      const px = x(b.strike) + (both.has(b.strike) ? (b.side === "put" ? -6.5 : 6.5) : 0);
      const hgt = Math.max(4, Math.sqrt(b.gex / gmax) * barMax);
      root.append(s("rect", { class: `pf-bar ${b.side}${isWall ? " wall" : ""}`, x: px - 5.5, y: base - hgt, width: 11, height: hgt, rx: 2 }));
      reserve(px - 6, base - hgt, px + 6, base);
      const oiSame = isWall && pf.oi[b.side] === b.strike;
      addHit(px - 6, px + 6, base - hgt - 4, base + 2, `本期 ${name} Gamma ${usd(b.strike)}`,
        [`每 1% 变动约 ${compactUsd(b.gex)} 美元 Gamma`, isWall ? `本侧最大，即本期 ${name} 墙${oiSame ? "；持仓量也在这里最大" : ""}` : null,
          pf.note, liabilityText(b.strike, model)].filter(Boolean).join("；"));
      if (isWall) later.push({ text: `${name} 墙 ${usd(b.strike)}${oiSame ? " · 持仓峰同位" : ""}`, px, ty: base - hgt - 5,
        attrs: { fill: `var(--${b.side})`, "font-weight": 600 }, value: compactUsd(b.gex) });
      else later.push({ text: compactUsd(b.gex), px, ty: base - hgt - 5, attrs: { fill: "var(--ink-3)", "font-size": 10 } });
    }
    // pin: a marker on top with a faint guide down to the bars
    if (isNum(pf.pin) && inView(pf.pin)) {
      const px = x(pf.pin);
      root.append(s("line", { class: "pf-guide", x1: px, x2: px, y1: pinY + 6, y2: base }));
      root.append(s("path", { d: `M${px - 6} ${pinY - 5} L${px + 6} ${pinY - 5} L${px} ${pinY + 6} Z`, fill: "var(--ink)" }));
      reserve(px - 7, pinY - 7, px + 7, pinY + 7);
      later.unshift({ text: `Pin ${usd(pf.pin)}`, px, spots: besideSpots(px, pinY), attrs: { fill: "var(--ink)", "font-weight": 600 } });
      addHit(px - 7, px + 7, pinY - 8, pinY + 8, `本期 Pin ${usd(pf.pin)}`,
        `到期前预期波动范围内两侧合计 Gamma 最大的行权价；若做市商净多 Gamma，价格易在附近钉住。${pf.note}；${liabilityText(pf.pin, model)}`);
    }
    // open-interest peaks that are not at the wall, and this expiry's max pain, on the bottom lane
    const low = [];
    for (const side of ["put", "call"]) {
      const k = pf.oi[side];
      if (isNum(k) && k !== pf.wall[side]) low.push({ price: k, kind: "oi", side, text: `${side === "put" ? "Put" : "Call"} 持仓峰 ${kusd(k)}`,
        title: `本期 ${side === "put" ? "Put" : "Call"} 持仓峰 ${usd(k)}`, body: `持仓量最大的行权价：仓位堆积处。${pf.note}` });
    }
    if (isNum(pf.maxPain)) low.push({ price: pf.maxPain, kind: "pain", text: `最大痛点 ${kusd(pf.maxPain)}`,
      title: `本期最大痛点 ${usd(pf.maxPain)}`, body: "该到期所有期权买方合计价值最小的价格；gexmonitor 口径" });
    for (const m of low) {
      if (!inView(m.price)) continue;
      const px = x(m.price);
      if (m.kind === "oi") root.append(s("circle", { cx: px, cy: lowY, r: 4, fill: `var(--${m.side})` }));
      else root.append(s("path", { d: `M${px - 5.5} ${lowY + 5} L${px + 5.5} ${lowY + 5} L${px} ${lowY - 5} Z`, fill: "var(--warn)" }));
      reserve(px - 6, lowY - 6, px + 6, lowY + 6);
      later.push({ text: m.text, px, spots: besideSpots(px, lowY), attrs: { fill: m.kind === "oi" ? `var(--${m.side})` : "var(--ink-2)" } });
      addHit(px - 6, px + 6, lowY - 7, lowY + 7, m.title, `${m.body}；${liabilityText(m.price, model)}`);
    }
  }

  // ── background levels: on-chain cost (top row), all-expiry GEX levels (middle row), KPF zones
  const R = T.back;
  const costY = R.y0 + 15, gexY = R.y0 + 35;
  const backMarkers = model.markers.slice();
  const flip = (model.levels || []).find((l) => l.role === "flip");
  if (flip) backMarkers.push({ price: flip.price, label: flip.label, short: "翻转位", glyph: "ring", color: "var(--depth)", note: flip.note });
  for (const z of model.zones) {
    if (!isNum(z.lo) || !isNum(z.hi) || z.hi < lo || z.lo > hi) continue;
    const a = x(Math.max(lo, z.lo)), b = x(Math.min(hi, z.hi));
    root.append(s("rect", { class: "kpf", x: a, y: R.y0 + 48, width: Math.max(3, b - a), height: R.h - 56, rx: 4 }));
    reserve(a, R.y0 + 48, Math.max(a + 3, b), R.y1 - 8);
    addHit(a, Math.max(a + 6, b), R.y0 + 46, R.y1, `${z.label} ${usd(z.lo)}–${usd(z.hi)}`,
      [z.note, z.containsShort ? "区间包含短腿" : null].filter(Boolean).join("；"));
  }
  for (const m of backMarkers) {
    if (!isNum(m.price) || !inView(m.price)) continue;
    const px = x(m.price);
    const yy = m.track === "trade" ? costY : gexY;
    if (m.glyph === "diamond") root.append(s("path", { d: `M${px} ${yy - 6} L${px + 6} ${yy} L${px} ${yy + 6} L${px - 6} ${yy} Z`, fill: m.color || "var(--ink)" }));
    else if (m.glyph === "ring") root.append(s("circle", { cx: px, cy: yy, r: 5, fill: "var(--sheet)", stroke: m.color || "var(--ink)", "stroke-width": 2 }));
    else root.append(s("path", { d: `M${px - 6} ${yy + 5} L${px + 6} ${yy + 5} L${px} ${yy - 6} Z`, fill: m.color || "var(--ink)" }));
    reserve(px - 7, yy - 7, px + 7, yy + 7);
    if (m.short) later.push({ text: m.short, px, spots: besideSpots(px, yy), attrs: { fill: "var(--ink-2)" } });
    addHit(px - 7, px + 7, yy - 9, yy + 9, `${m.label} ${usd(m.price)}`, [m.note, liabilityText(m.price, model)].filter(Boolean).join("；"));
  }

  // labels after every glyph and bar: the wall labels first, each with its value above when it fits
  for (const l of later) {
    if (l.spots) { place(l.text, l.spots, l.attrs); continue; }
    const ok = place(l.text, [{ x: l.px, y: l.ty }, { x: l.px + 8, y: l.ty, anchor: "start" }, { x: l.px - 8, y: l.ty, anchor: "end" }], l.attrs);
    if (ok && l.value) place(l.value, [{ x: l.px, y: l.ty - 13 }], { fill: "var(--ink-3)", "font-size": 10 });
  }

  // σ percentages in their own lane below the tracks
  for (const [ex, text] of sigmaLabels) {
    place(text, [{ x: ex + 3, y: plotBottom + 13, anchor: "start" }, { x: ex - 3, y: plotBottom + 13, anchor: "end" }], { fill: "var(--ink-3)" });
  }
  // levels outside the view: the nearest one at each edge named with its distance, in the footnote row
  const outside = { left: [], right: [] };
  const pool = [...model.markers, ...(model.levels || []).map((l) => ({ ...l, short: l.label })),
    ...model.zones.filter((z) => isNum(z.lo) && isNum(z.hi)).map((z) => ({ ...z, price: (z.lo + z.hi) / 2 }))];
  for (const m of pool) if (isNum(m.price) && !inView(m.price)) (m.price < lo ? outside.left : outside.right).push(m);
  for (const [items, left] of [[outside.left, true], [outside.right, false]]) {
    if (!items.length) continue;
    const near = items.slice().sort((a, b) => left ? b.price - a.price : a.price - b.price)[0];
    const name = near.label && near.label.length <= 12 ? near.label : near.short || near.label;
    const text = `${name} ${usd(near.price)}（${signed((near.price / model.spot - 1) * 100, 1)}%）${items.length > 1 ? `，另 ${items.length - 1} 项` : ""}`;
    place(left ? `◂ 图外 ${text}` : `图外 ${text} ▸`, [{ x: left ? PAD_L : W - PAD_R, y: plotBottom + 29, anchor: left ? "start" : "end" }],
      { fill: "var(--ink-3)" });
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
  const exp = model?.profile ? `${model.profile.expiry}${model.profile.stale ? "，数据偏旧" : ""}` : null;
  const items = [
    ["var(--put-soft)", "Put 责任区（短腿至保护腿）"],
    ["var(--call-soft)", "Call 责任区"],
    ["var(--sigma)", "到期前隐含预期波动（虚线为 2 倍）"],
    ...(exp ? [["var(--put)", `本期 Put / Call 美元 Gamma 前 3 档，最深为墙（${exp}）`], ["var(--ink)", "▼ 本期 Pin"],
      ["var(--put)", "● 持仓峰（持仓量最大）"], ["var(--warn)", "▲ 最大痛点"]] : [["var(--ink-3)", "本期期权结构暂缺"]]),
    ["var(--depth)", "○ Gamma 翻转位（全期限）"],
    ["var(--ink-3)", "◆ 链上持有者成本"],
    ["color-mix(in srgb, var(--ink-3) 30%, transparent)",
      model?.kpfAsOf ? `KPF 成交接受区（成交数据截至 ${new Date(model.kpfAsOf).toISOString().slice(5, 10)}）` : "KPF 成交接受区"],
  ];
  for (const [c, t] of items) {
    const span = document.createElement("span");
    const em = document.createElement("em"); em.style.background = c; span.append(em, t); el.append(span);
  }
  const more = document.createElement("span");
  more.className = "legend-note";
  more.textContent = "周度、0DTE 与全期限墙见下方价位表";
  el.append(more);
}

/** All levels in one list, split at the spot: price, distance (% and expected moves), name, basis. */
export function ladderTable(el, model) {
  const wasOpen = el.querySelector("details") ? el.querySelector("details").open : false;   // keep the reader's choice across refreshes
  el.innerHTML = "";
  const lad = model && model.ladder;
  if (!lad || !isNum(model.spot)) return;
  const col = (title, rows) => h("div", { class: "ladder-col" },
    h("div", { class: "ladder-head small muted", text: title }),
    rows.length ? h("table", { class: "ladder" }, h("tbody", {}, ...rows.map((r) => h("tr", { class: r.key ? "key" : "" },
      h("td", { class: "num", text: usd(r.price) }),
      h("td", { class: "num muted", text: `${signed(r.distPct)}%${isNum(r.moves) ? ` · ${signed(r.moves)} 倍` : ""}` }),
      h("td", { text: r.name }),
      h("td", {}, h("span", { class: "basis", text: r.basisCn }))))))
      : h("p", { class: "small muted", text: "无" }));
  el.append(h("details", { class: "ladder-wrap", open: wasOpen },
    h("summary", {}, h("b", { text: "全部关键价位" }),
      h("span", { class: "muted", text: `　${lad.below.length + lad.above.length} 项，含图外；距现价 ${usd(model.spot)} 的百分比与预期波动倍数，加粗为本期期权` })),
    h("div", { class: "ladder-cols" }, col("现价下方（由近到远）", lad.below), col("现价上方（由近到远）", lad.above))));
}
