// Session strip and entry warnings (docs/opus/13). Facts about the intraday cycle,
// veto-only warnings and the busy-window holding note (entry_warnings@0.2, docs/opus/22);
// nothing here recommends a trade. Times show Beijing time with UTC.
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

// The top strip's time lamp: how much of the cycle's risk is still ahead and the next
// high-variance window. A fact about the clock, never a verdict (docs/opus/23 §3 P1).
export function timeLamp(view, now = Date.now()) {
  const m = clockModel(view, now);
  if (!m.available) return { level: "off", text: "时段读数缺失", title: m.reason };
  const next = m.windows[0];
  const risk = `剩余风险 ${share(m.riskLeft)}`;
  const win = next ? `${next.label} ${next.until}` : "交割前无高波动窗口";   // short: the strip stays one line
  const title = ["剩余风险 = 到交割前还剩下的日内方差占一个完整持仓周期的比例（近两年 BTC 日内节律）",
    ...m.windows.map((w) => `${w.label}：北京 ${w.bj}（UTC ${w.utc}），${w.until}`)].join("\n");
  return { level: "time", text: `${risk} · ${win}`, title };
}

// Risk density of each hour: its historical price variance relative to the all-days hourly mean.
// A fact about the hour, not a verdict: the US session is also where the day is priced, and a
// quiet late hour leaves little premium against an unchanged maximum loss.
export const densityTier = (r) => (!isNum(r) ? "na" : r >= 1.25 ? "busy" : r < 0.8 ? "quiet" : "mid");
const TIER_CN = { busy: "波动集中", mid: "接近平均", quiet: "安静", na: "—" };

// The 24 hours of one holding cycle, starting at the 08:00 UTC expiry (16:00 Beijing), with
// the current hour, the contract's remaining hours and the high-variance windows (US open,
// CME reopen, macro releases) marked in the hour they fall in.
export function cycleCells(c, now) {
  const profile = c.hour_profile || {};
  const start = new Date(c.expiry_ms).getUTCHours();
  const nowHour = c.now_hour_utc;
  const hoursLeft = isNum(c.hours_to_expiry) ? Math.min(24, Math.ceil(c.hours_to_expiry)) : 0;
  const cycleStart = isNum(c.expiry_ms) ? c.expiry_ms - 24 * 3.6e6 : null;
  const wins = Array.from({ length: 24 }, () => []);
  for (const w of c.windows || []) {
    if (!w || !isNum(w.at_ms) || !isNum(cycleStart)) continue;
    const i = Math.floor((w.at_ms - cycleStart) / 3.6e6);
    if (i >= 0 && i < 24) wins[i].push({ label: w.label_cn + (w.tentative ? "（日期待确认）" : ""), bj: bj(w.at_ms), utc: utc(w.at_ms), kind: w.kind });
  }
  const cells = [];
  for (let i = 0; i < 24; i += 1) {
    const hourUtc = (start + i) % 24;
    const pick = (type) => (profile[type] || []).find((r) => r.hour_utc === hourUtc) || {};
    const wd = pick("weekday"), we = pick("weekend");
    // hours left until expiry: the last `hoursLeft` columns of the cycle
    const held = i >= 24 - hoursLeft;
    cells.push({ hourUtc, hourBj: (hourUtc + 8) % 24, current: hourUtc === nowHour && held,
      held, windows: wins[i], weekday: { risk: wd.risk_ratio, tier: densityTier(wd.risk_ratio) },
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
  const cell = (x, type) => {
    const win = type === today && x.windows.length ? x.windows : null;
    return h("td", {
      class: `ss-cell ${x[type].tier}${x.current && type === today ? " now" : ""}${x.held && type === today ? " held" : ""}${win ? " win" : ""}`,
      title: `北京 ${String(x.hourBj).padStart(2, "0")}:00（UTC ${String(x.hourUtc).padStart(2, "0")}:00）`
        + `：波动为全天平均的 ${NUM(x[type].risk)} 倍（${TIER_CN[x[type].tier]}）`
        + (win ? `；高波动窗口：${win.map((w) => `${w.label} 北京 ${w.bj}`).join("、")}` : "") });
  };
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
        ["quiet", "安静（< 平均 0.8 倍，工作日亚洲上午）"], ["now-key", "现在"], ["held-key", "本合约剩余持仓时段"],
        ["win-key", "高波动窗口所在小时（美股开盘、CME 周日开盘、宏观发布；悬停看名称）"]]
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
  return "";
}

// entry_warnings@0.1 vetoed a busy window with a thin cushion; @0.2 made it a holding note
// (docs/opus/22). Old blocks (cached views, sessions) read that item as the note, never a veto.
const LEGACY_BUSY = "busy_window_thin_cushion";
const BUSY_TAIL = "在这类时点按标准短腿建仓，2 小时内触及短腿约 35%（其他时点约 26%）；这是持有中的盯盘提示，不是否决。";

// The busy-window note: { active: true / false / null, reason }, or null when the block has none.
export function busyNote(block) {
  const note = ((block && block.notes) || []).find((n) => n && n.code === "busy_window");
  if (note) return { active: note.active == null ? null : note.active, reason: note.reason_cn || "" };
  const old = ((block && block.warnings) || []).find((w) => w && w.code === LEGACY_BUSY);
  if (!old) return null;
  const e = old.evidence || {};
  const win = e.next_window && typeof e.next_window === "object" ? e.next_window : null;
  const active = win || e.busy_now || old.triggered === true ? true : old.triggered === false ? false : null;
  if (!active) return { active, reason: "" };
  const at = win && isNum(win.in_hours) && win.in_hours > 0 ? `约 ${win.in_hours.toFixed(1)} 小时后` : "正在进行";
  return { active, reason: `${win ? `${win.label_cn || "窗口"}${at}` : "当前处于高波动时段"}。${BUSY_TAIL}` };
}

export function warningModel(candidate) {
  const block = candidate && candidate.entry_warnings;
  if (!block) return { available: false, text: "不建仓警示：读数尚未提供" };
  if (block.error_type) return { available: false, text: "不建仓警示：读数暂不可用" };
  const items = (block.warnings || []).filter((w) => w && w.code !== LEGACY_BUSY)
    .map((w) => ({ code: w.code, label: w.label_cn, triggered: w.triggered, reason: w.reason_cn,
      evidence: warningEvidence({ ...w, threshold_raw: w.threshold }), threshold: w.threshold_cn || w.threshold }));
  const fired = items.filter((w) => w.triggered === true);
  const unknown = items.filter((w) => w.triggered == null);
  return { available: true, fired, unknown, items, total: items.length, historyAvailable: block.history_available !== false,
    note: busyNote(block) };
}

// The note as its own line, only while it is on: amber, and it says it is not a veto.
function noteLine(note) {
  if (!note || note.active !== true) return null;
  return h("p", { class: "ew-note small warn-text", style: "margin: 4px 0 8px", text: `时段提示（不是否决）：${note.reason}` });
}

// unsuitable@0.1 (tools/astra_unsuitable_v1.card): vetoes and cautions with their numbers, why and
// what clears them. "No item" only means none of these checks fired; it is never a reason to trade.
export function unsuitableModel(candidate) {
  const u = candidate && candidate.unsuitable;
  if (!u || typeof u !== "object" || u.error_type || !Array.isArray(u.items)) return null;
  const items = u.items.filter((i) => i && typeof i === "object" && i.title_cn);
  const vetoes = items.filter((i) => i.kind === "veto");
  const cautions = items.filter((i) => i.kind !== "veto");
  const unknown = (u.unknown_cn || []).filter(Boolean).map(String);
  // the checks the card runs: data, each entry warning, the buffer, and the two price checks when priced
  const w = warningModel(candidate);
  const pricing = candidate.readings?.pricing;
  const priced = !!pricing && typeof pricing === "object" && "reservation_btc" in pricing;
  const total = 1 + (w.available && w.total ? w.total : 1) + 1 + (priced ? 2 : 0);
  return { vetoes, cautions, items: [...vetoes, ...cautions], unknown, total, ran: Math.max(0, total - unknown.length) };
}

function unsuitableItem(i) {
  return h("li", { class: `uv-item ${i.kind === "veto" ? "veto" : "caution"}`, title: i.basis ? `依据：${i.basis}` : null },
    h("b", { class: "uv-title", text: i.title_cn }),
    i.evidence_cn ? h("span", { class: "uv-evidence num", text: i.evidence_cn }) : null,
    i.why_cn ? h("span", { class: "uv-why", text: i.why_cn }) : null,
    i.clears_cn ? h("span", { class: "uv-clears muted", text: `解除条件：${i.clears_cn}` }) : null);
}

export function warningBlock(candidate, opts = {}) {
  const m = warningModel(candidate);
  const u = unsuitableModel(candidate);
  if (u) return unsuitableBlock(candidate, m, u, opts);
  if (!m.available) return h("p", { class: "ew-none muted small", text: m.text });
  const note = noteLine(m.note);
  const quiet = (p) => (note ? h("div", { class: "ew-quiet" }, p, note) : p);
  if (!m.total) return quiet(h("p", { class: "ew-none muted small", text: "不建仓警示：读数为空" }));
  const gap = m.unknown.length ? `，${m.unknown.length} 项数据不足` : "";
  if (!m.fired.length) {
    // never say "none" when nothing could be checked
    const text = m.unknown.length === m.total ? `不建仓警示：无法判断（${m.total} 项数据都不足）`
      : `不建仓警示：未触发 ${m.total - m.unknown.length} 项${gap}`;
    return quiet(h("p", { class: "ew-none muted small", text }));
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
    note, more);
}

/** The card's unsuitable block: vetoes first and most prominent, then cautions, the checks that
 * could not run, the busy-window note, and the entry warnings' thresholds folded below. */
function unsuitableBlock(candidate, m, u, opts) {
  const note = m.available ? noteLine(m.note) : null;
  const unknownLine = u.unknown.length ? `${u.unknown.length} 项无法检查：${u.unknown.join("、")}` : "";
  if (!u.items.length) {
    const p = h("p", { class: "ew-none muted small",
      text: `未见否决项（这不是做的理由）：已检查 ${u.ran} 项${unknownLine ? `，${unknownLine}` : ""}` });
    return note ? h("div", { class: "ew-quiet" }, p, note) : p;
  }
  const veto = u.vetoes.length > 0;
  const head = veto ? `不适合做 · 否决 ${u.vetoes.length} 项${u.cautions.length ? ` · 提醒 ${u.cautions.length} 项` : ""}`
    : `提醒 ${u.cautions.length} 项`;
  let more = null;
  if (m.available && m.total) {
    const key = `ew-${candidate.side}`;
    more = h("details", { class: "ew-more", open: opts.isOpen ? opts.isOpen(key) : false }, h("summary", { text: "阈值与全部检查项" }),
      h("ul", {}, ...m.items.map((w) => h("li", {},
        h("b", { text: `${w.label}：${w.triggered === true ? "触发" : w.triggered === false ? "未触发" : "数据不足"}` }),
        h("span", { class: "small", text: ` ${w.reason || ""}` }),
        h("span", { class: "small muted", text: ` 阈值：${Object.entries(w.threshold || {}).map(([k, v]) => `${k} ${v}`).join("；")}` })))));
    if (opts.keepOpen) opts.keepOpen(more, key);
  }
  return h("div", { class: `entry-warnings uv ${veto ? "veto" : "caution"}`, onclick: (e) => e.stopPropagation() },
    h("div", { class: "ew-head" }, h("b", { text: head }),
      veto ? h("span", { class: "ew-tag", text: "阈值未经验证，只作否决" }) : null),
    h("ul", { class: "ew-list uv-list" }, ...u.items.map(unsuitableItem)),
    unknownLine ? h("p", { class: "small muted uv-unknown", text: `${unknownLine}。` }) : null,
    note, more);
}
