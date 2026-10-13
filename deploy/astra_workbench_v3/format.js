// Small, dependency-free formatting and DOM helpers.

export const isNum = (v) => typeof v === "number" && Number.isFinite(v);

export function usd(v, digits = 0) {
  if (!isNum(v)) return "—";
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function kusd(v) {
  if (!isNum(v)) return "—";
  return (v / 1000).toLocaleString("en-US", { maximumFractionDigits: v % 1000 === 0 ? 0 : 2 }) + "k";
}

export function btc(v, digits = 4) {
  if (!isNum(v)) return "—";
  return v.toFixed(digits);
}

export function pct(v, digits = 1) {
  if (!isNum(v)) return "—";
  return v.toFixed(digits) + "%";
}

export function ratioPct(v, digits = 1) {
  return isNum(v) ? pct(v * 100, digits) : "—";
}

export function compactUsd(v) {
  if (!isNum(v)) return "—";
  const a = Math.abs(v);
  const s = v < 0 ? "−" : "";
  if (a >= 1e9) return s + (a / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return s + (a / 1e6).toFixed(1) + "M";
  if (a >= 1e3) return s + (a / 1e3).toFixed(0) + "k";
  return s + a.toFixed(0);
}

export function ago(ms, now = Date.now()) {
  if (!isNum(ms)) return "—";
  const s = Math.max(0, Math.round((now - ms) / 1000));
  if (s < 60) return s + " 秒前";
  const m = Math.round(s / 60);
  if (m < 60) return m + " 分钟前";
  const h = Math.round(m / 6) / 10;
  if (h < 48) return h + " 小时前";
  return Math.round(h / 24) + " 天前";
}

export function dur(ms) {
  if (!isNum(ms)) return "—";
  const m = Math.round(ms / 60000);
  if (Math.abs(m) < 60) return m + " 分钟";
  const h = ms / 3600000;
  if (Math.abs(h) < 48) return h.toFixed(1) + " 小时";
  return (h / 24).toFixed(1) + " 天";
}

const BJT = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
});
export function clock(ms) {
  return isNum(ms) ? BJT.format(new Date(ms)) : "—";
}

export function get(obj, path, fallback = undefined) {
  let cur = obj;
  for (const part of path.split(".")) {
    if (cur == null) return fallback;
    cur = cur[part];
  }
  return cur === undefined ? fallback : cur;
}

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const SVGNS = "http://www.w3.org/2000/svg";
export function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "text") el.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c) el.append(c);
  return el;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}
