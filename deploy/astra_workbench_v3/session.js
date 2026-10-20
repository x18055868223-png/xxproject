// Session strip and entry warnings (docs/opus/13). Facts about the intraday cycle and
// veto-only warnings; nothing here recommends a trade. Times show Beijing time with UTC.
import { h, isNum, get } from "./format.js";

const BJ = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hour12: false });
const UTC = new Intl.DateTimeFormat("zh-CN", { timeZone: "UTC", hour: "2-digit", minute: "2-digit", hour12: false });
const bj = (ms) => (isNum(ms) ? BJ.format(new Date(ms)) : "—");
const utc = (ms) => (isNum(ms) ? UTC.format(new Date(ms)) : "—");
const share = (v) => (isNum(v) ? `${Math.round(v * 100)}%` : "—");

export function untilText(ms, now) {
  if (!isNum(ms)) return "—";
  const left = ms - now;
  if (left <= 0) return "进行中";
  const hours = left / 3.6e6;
  return hours < 1 ? `${Math.max(1, Math.round(left / 60000))} 分钟后` : `${hours.toFixed(1)} 小时后`;
}

// What the strip shows, from view.session_clock; the countdowns move with the page clock.
export function clockModel(view, now = Date.now()) {
  const c = get(view, "session_clock");
  if (!c || c.error_type || !c.phase) return { available: false, reason: c && c.error_type ? "时段读数暂不可用" : "时段读数尚未提供" };
  // a window that started within the hour is still in progress (as the server keeps it)
  const windows = (c.windows || []).filter((w) => isNum(w.at_ms) && w.at_ms > now - 3.6e6).slice(0, 3)
    .map((w) => ({ label: w.label_cn + (w.tentative ? "（日期待确认）" : ""), at: w.at_ms, until: untilText(w.at_ms, now),
      bj: bj(w.at_ms), utc: utc(w.at_ms), kind: w.kind }));
  return {
    available: true,
    phaseCode: c.phase.code, phase: c.phase.label_cn, level: c.hour_level,
    expiryBj: bj(c.expiry_ms), expiryUtc: utc(c.expiry_ms),
    hoursLeft: c.hours_to_expiry, beyond: c.beyond_cycle === true, fromContract: c.expiry_source === "contract",
    gaps: (c.calendar_gaps || []).length > 0,
    timeLeft: c.remaining_time_share, riskLeft: c.remaining_variance_share,
    factor: c.remaining_clock_factor, windows,
  };
}

// Risk density of each hour: its historical price variance relative to the all-days hourly mean.
// A fact about the hour, not a verdict: the US session is also where the day is priced, and a
// quiet late hour leaves little premium against an unchanged maximum loss.
export const densityTier = (r) => (!isNum(r) ? "na" : r >= 1.25 ? "busy" : r < 0.8 ? "quiet" : "mid");
const TIER_CN = { busy: "波动集中", mid: "接近平均", quiet: "安静", na: "—" };

// The 24 hours of one holding cycle, starting at the 08:00 UTC expiry (16:00 Beijing), with
// the current hour and the contract's remaining hours marked.
export function cycleCells(c, now) {
  const profile = c.hour_profile || {};
  const start = new Date(c.expiry_ms).getUTCHours();
  const nowHour = c.now_hour_utc;
  const hoursLeft = isNum(c.hours_to_expiry) ? Math.min(24, Math.ceil(c.hours_to_expiry)) : 0;
  const cells = [];
  for (let i = 0; i < 24; i += 1) {
    const hourUtc = (start + i) % 24;
    const pick = (type) => (profile[type] || []).find((r) => r.hour_utc === hourUtc) || {};
    const wd = pick("weekday"), we = pick("weekend");
    // hours left until expiry: the last `hoursLeft` columns of the cycle
    const held = i >= 24 - hoursLeft;
    cells.push({ hourUtc, hourBj: (hourUtc + 8) % 24, current: hourUtc === nowHour && held,
      held, weekday: { risk: wd.risk_ratio, tier: densityTier(wd.risk_ratio) },
      weekend: { risk: we.risk_ratio, tier: densityTier(we.risk_ratio) } });
  }
  return cells;
}

export function sessionStrip(view, now = Date.now()) {
  const m = clockModel(view, now);
  if (!m.available) return h("div", { class: "session-strip muted small", text: m.reason });
  const c = get(view, "session_clock");
  const cells = cycleCells(c, now);
  const today = c.now_day_type === "weekend" ? "weekend" : "weekday";
  const cell = (x, type) => h("td", {
    class: `ss-cell ${x[type].tier}${x.current && type === today ? " now" : ""}${x.held && type === today ? " held" : ""}`,
    title: `北京 ${String(x.hourBj).padStart(2, "0")}:00（UTC ${String(x.hourUtc).padStart(2, "0")}:00）`
      + `：波动为全天平均的 ${NUM(x[type].risk)} 倍（${TIER_CN[x[type].tier]}）` });
  const row = (type, label) => h("tr", { class: type === today ? "today" : "" },
    h("th", { scope: "row", text: label, title: type === today ? "今天" : "" }), ...cells.map((x) => cell(x, type)));
  const head = h("tr", {}, h("th", {}), ...cells.map((x, i) => h("th", {
    class: `ss-hour${x.current ? " now" : ""}${i % 3 === 0 ? "" : " minor"}`, text: String(x.hourBj).padStart(2, "0") })));
  const riskWord = isNum(m.factor) ? (m.factor >= 1.15 ? "剩下的时段比平均更难熬" : m.factor < 0.85 ? "剩下的时段比平均更安静" : "剩下的时段接近平均") : "";
  const next = m.windows[0];
  return h("div", { class: "session-strip" },
    h("div", { class: "ss-top" },
      h("b", { text: "时段风险密度" }),
      h("span", { class: "small muted", text: "近两年 BTC 日内节律 · 北京时间 · 从 16:00 交割起的一个持仓周期" })),
    h("p", { class: "ss-now-line" },
      h("span", { class: `ss-phase ${m.level || ""}`, text: `现在：${m.phase}` }),
      h("span", { text: `　到交割（北京 ${m.expiryBj}）还有 ${isNum(m.hoursLeft) ? m.hoursLeft.toFixed(1) : "—"} 小时：剩余时间 ${share(m.timeLeft)}，剩余风险 ${share(m.riskLeft)}，${riskWord}` }),
      next ? h("span", { class: "ss-next", text: `　接下来：${next.label} ${next.until}（北京 ${next.bj}）` }) : null),
    h("div", { class: "ss-table-wrap" }, h("table", { class: "ss-table" }, h("thead", {}, head),
      h("tbody", {}, row("weekday", "工作日"), row("weekend", "周末")))),
    h("ul", { class: "ss-legend small" },
      ...[["busy", "波动集中（≥ 平均 1.25 倍，工作日美股开盘前后）"], ["mid", "接近平均"],
        ["quiet", "安静（< 平均 0.8 倍，工作日亚洲上午）"], ["now-key", "现在"], ["held-key", "本合约剩余持仓时段"]]
        .map(([cls, text]) => h("li", {}, h("span", { class: `ss-key ${cls}` }), h("span", { text })))),
    h("p", { class: "ss-rows small muted", text: `上一行是工作日，下一行是周末；${today === "weekend" ? "今天是周末" : "今天是工作日"}，对应的一行更深。` }),
    h("p", { class: "ss-basis small", text: "说明：颜色只表示该小时过去两年的波动强度。美股开盘前后是全天关键定价窗口，选腿的主要证据来自这里；"
      + "越接近交割，剩余保费越少而最大亏损不变，风险密度低不等于值得入场或继续持有。入场与持有的实际结果另由存档对账统计（约 20 个到期日后再看）。" }));
}

const NUM = (v, d = 2) => (isNum(v) ? v.toFixed(d) : "—");
const signedPts = (v) => (isNum(v) ? `${v >= 0 ? "+" : ""}${v.toFixed(1)} 点` : "无数据");

// Plain-language evidence line per warning, from its evidence values.
export function warningEvidence(w) {
  const e = w.evidence || {};
  if (w.code === "term_inversion") {
    return `时钟校正后：本期 ${e.contract_expiry || "—"} ${NUM(e.contract_atm_iv_clock_adjusted, 1)}，一周 ${e.week_expiry || "—"} ${NUM(e.week_atm_iv_clock_adjusted, 1)}，差 ${NUM(e.gap_vol_pts, 1)} 点`;
  }
  if (w.code === "premium_thickening") {
    return `本期 IV（时钟校正）较近 6 小时 ${signedPts(e.atm_jump_vol_pts)}，本侧偏斜 ${signedPts(e.skew_jump_vol_pts)}`
      + (isNum(e.other_side_skew_jump_vol_pts) ? `，另一侧偏斜 ${signedPts(e.other_side_skew_jump_vol_pts)}` : "，另一侧无数据");
  }
  if (w.code === "busy_window_thin_cushion") {
    const t = w.threshold_raw || {};
    const within = isNum(t.window_within_hours) ? t.window_within_hours : 2;
    const below = isNum(t.distance_in_moves_below) ? t.distance_in_moves_below : 0.5;
    return `σ 距离 ${NUM(e.distance_in_moves)}（阈值：${within} 小时内有窗口或当前处于高波动时段，且 σ 距离 < ${below}）`;
  }
  return "";
}

export function warningModel(candidate) {
  const block = candidate && candidate.entry_warnings;
  if (!block) return { available: false, text: "不建仓警示：读数尚未提供" };
  if (block.error_type) return { available: false, text: "不建仓警示：读数暂不可用" };
  const items = (block.warnings || []).map((w) => ({ code: w.code, label: w.label_cn, triggered: w.triggered, reason: w.reason_cn,
    evidence: warningEvidence({ ...w, threshold_raw: w.threshold }), threshold: w.threshold_cn || w.threshold }));
  const fired = items.filter((w) => w.triggered === true);
  const unknown = items.filter((w) => w.triggered == null);
  return { available: true, fired, unknown, items, total: items.length, historyAvailable: block.history_available !== false };
}

export function warningBlock(candidate, opts = {}) {
  const m = warningModel(candidate);
  if (!m.available) return h("p", { class: "ew-none muted small", text: m.text });
  if (!m.total) return h("p", { class: "ew-none muted small", text: "不建仓警示：读数为空" });
  const gap = m.unknown.length ? `，${m.unknown.length} 项数据不足` : "";
  if (!m.fired.length) {
    // never say "none" when nothing could be checked
    const text = m.unknown.length === m.total ? `不建仓警示：无法判断（${m.total} 项数据都不足）`
      : `不建仓警示：未触发 ${m.total - m.unknown.length} 项${gap}`;
    return h("p", { class: "ew-none muted small", text });
  }
  const key = `ew-${candidate.side}`;
  const more = h("details", { class: "ew-more", open: opts.isOpen ? opts.isOpen(key) : false }, h("summary", { text: "阈值与全部检查项" }),
    h("ul", {}, ...m.items.map((w) => h("li", {},
      h("b", { text: `${w.label}：${w.triggered === true ? "触发" : w.triggered === false ? "未触发" : "数据不足"}` }),
      h("span", { class: "small", text: ` ${w.reason || ""}` }),
      h("span", { class: "small muted", text: ` 阈值：${Object.entries(w.threshold || {}).map(([k, v]) => `${k} ${v}`).join("；")}` })))));
  if (opts.keepOpen) opts.keepOpen(more, key);
  return h("div", { class: "entry-warnings", onclick: (e) => e.stopPropagation() },
    h("div", { class: "ew-head" }, h("b", { text: `不建仓警示 ${m.fired.length} 项` }),
      h("span", { class: "ew-tag", text: "阈值未经验证，只作否决提醒" })),
    h("ul", { class: "ew-list" }, ...m.fired.map((w) => h("li", {},
      h("b", { text: w.label }), h("span", { class: "ew-reason", text: w.reason || "" }),
      h("span", { class: "ew-evidence num", text: w.evidence })))),
    gap ? h("p", { class: "small muted", text: `另有 ${m.unknown.length} 项数据不足：${m.unknown.map((w) => w.label).join("、")}。` }) : null,
    more);
}
