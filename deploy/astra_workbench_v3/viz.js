// Small inline SVG components for metric rows. No dependencies.
import { s, isNum } from "./format.js";

const W = 120, H = 14;

/** Bar growing left/right from zero; |v| beyond `span` is clipped with a notch. */
const STRETCH = { preserveAspectRatio: "none" };

export function diverging(v, span, { good = 0 } = {}) {
  const svg = s("svg", { class: "viz", viewBox: `0 0 ${W} ${H}`, "aria-hidden": "true", ...STRETCH });
  svg.append(s("rect", { x: 0, y: 5, width: W, height: 4, rx: 2, fill: "var(--rule)", "fill-opacity": 0.55 }));
  svg.append(s("line", { x1: W / 2, x2: W / 2, y1: 1, y2: 13, stroke: "var(--ink-3)", "stroke-width": 1, "vector-effect": "non-scaling-stroke" }));
  if (!isNum(v) || !isNum(span) || span <= 0) return svg;
  const k = Math.max(-1, Math.min(1, v / span));
  const w = Math.abs(k) * (W / 2);
  const tone = good === 0 ? "var(--depth)" : (Math.sign(v) === Math.sign(good) ? "var(--ok)" : "var(--liability)");
  svg.append(s("rect", { x: k >= 0 ? W / 2 : W / 2 - w, y: 3, width: Math.max(1.5, w), height: 8, rx: 2, fill: tone, "fill-opacity": 0.8 }));
  if (Math.abs(v / span) > 1) svg.append(s("path", { d: k > 0 ? `M${W - 1} 2 L${W - 1} 12 L${W - 5} 7 Z` : `M1 2 L1 12 L5 7 Z`, fill: tone }));
  return svg;
}

/** 0..max fill with an optional pass threshold marker. */
export function gauge(v, max, { threshold = null, invert = false } = {}) {
  const svg = s("svg", { class: "viz", viewBox: `0 0 ${W} ${H}`, "aria-hidden": "true", ...STRETCH });
  svg.append(s("rect", { x: 0, y: 4, width: W, height: 6, rx: 3, fill: "var(--rule)", "fill-opacity": 0.55 }));
  if (isNum(v) && isNum(max) && max > 0) {
    const w = Math.max(0, Math.min(1, v / max)) * W;
    const pass = threshold == null ? null : invert ? v <= threshold : v >= threshold;
    const tone = pass == null ? "var(--depth)" : pass ? "var(--ok)" : "var(--warn)";
    svg.append(s("rect", { x: 0, y: 4, width: Math.max(1.5, w), height: 6, rx: 3, fill: tone, "fill-opacity": 0.85 }));
  }
  if (isNum(threshold) && isNum(max)) {
    const tx = Math.max(0, Math.min(1, threshold / max)) * W;
    svg.append(s("line", { x1: tx, x2: tx, y1: 0, y2: 14, stroke: "var(--ink)", "stroke-width": 1.5, "vector-effect": "non-scaling-stroke" }));
  }
  return svg;
}

/** Two-part split, e.g. realized profit vs loss. */
export function split(a, b, { aColor = "var(--ok)", bColor = "var(--liability)" } = {}) {
  const svg = s("svg", { class: "viz", viewBox: `0 0 ${W} ${H}`, "aria-hidden": "true", ...STRETCH });
  if (!isNum(a) || !isNum(b) || a + b <= 0) {
    svg.append(s("rect", { x: 0, y: 4, width: W, height: 6, rx: 3, fill: "var(--rule)", "fill-opacity": 0.55 }));
    return svg;
  }
  const wa = (a / (a + b)) * W;
  svg.append(s("rect", { x: 0, y: 4, width: wa, height: 6, fill: aColor, "fill-opacity": 0.75 }));
  svg.append(s("rect", { x: wa, y: 4, width: W - wa, height: 6, fill: bColor, "fill-opacity": 0.75 }));
  return svg;
}

/** Points on a short price axis with the current price marked. */
export function positions(points, spot, { width = 260 } = {}) {
  const valid = points.filter((p) => isNum(p.price));
  const all = [...valid.map((p) => p.price), spot].filter(isNum);
  const svg = s("svg", { class: "viz wide", viewBox: `0 0 ${width} 34`, "aria-hidden": "true" });
  if (all.length < 2) return svg;
  let lo = Math.min(...all), hi = Math.max(...all);
  const pad = (hi - lo) * 0.08 || 1;
  lo -= pad; hi += pad;
  const X = (p) => 6 + ((p - lo) / (hi - lo)) * (width - 12);
  svg.append(s("line", { x1: 6, x2: width - 6, y1: 18, y2: 18, stroke: "var(--rule)", "stroke-width": 2 }));
  for (const p of valid) {
    svg.append(s("path", { d: `M${X(p.price)} 13 L${X(p.price) + 5} 18 L${X(p.price)} 23 L${X(p.price) - 5} 18 Z`, fill: p.color || "var(--ink-2)" }));
    svg.append(s("text", { x: X(p.price), y: 9, "text-anchor": "middle", "font-size": 9, fill: "var(--ink-2)", text: p.label }));
  }
  if (isNum(spot)) {
    svg.append(s("line", { x1: X(spot), x2: X(spot), y1: 10, y2: 28, stroke: "var(--depth)", "stroke-width": 2 }));
    svg.append(s("text", { x: X(spot), y: 33, "text-anchor": "middle", "font-size": 9, fill: "var(--depth)", text: "现价" }));
  }
  return svg;
}
