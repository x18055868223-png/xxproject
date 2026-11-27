// Live execution, read-only: what the AI player decided, what the executor is doing,
// the real position it holds and how this hand ends. Facts come only from
// GET /execution/live (or the local simulated fixture); this module has no write path.
import { h, clear, isNum, dur, clock, ago } from "./format.js";

/* ── vocabulary ─────────────────────────────────────────── */

const MODE = {
  SHADOW_ONLY: { text: "影子 · 不下单", tone: "shadow" },
  DRY_RUN: { text: "演练 · 不发交易所", tone: "shadow" },
  TESTNET: { text: "测试网 · 真实下单（非真实资金）", tone: "testnet" },
  LIVE: { text: "实盘 · 真实资金", tone: "real" },
  MAINNET: { text: "实盘 · 真实资金", tone: "real" },
};

const OUTCOME_CN = {
  ABORTED_ZERO_FILL: "零成交中止",
  EARLY_FLAT_SETTLED: "提前平仓结清",
  EXPIRY_SETTLED: "到期交割结清",
};

const EXIT_CN = {
  AI_REVOKE: "AI 牌手 REVOKE",
  OPERATOR_EXIT_ALL: "人工强制退出",
  NAKED_SHORT_BREACH: "保护不足纠错退出",
};

const PURPOSE_CN = { EP: "买保护腿", ES: "卖短腿", TP: "止盈买回", RX: "退出买回短腿", LX: "回收保护腿" };

const CMD_CN = {
  OPEN: "挂单中", FILLED: "已成交", CANCELLED: "已撤", EXPIRED_UNFILLED: "到期未成", REJECTED: "被拒",
  PREPARED_UNKNOWN: "结果未知", SUBMIT_UNKNOWN: "结果未知", CANCEL_UNKNOWN: "撤单结果未知",
};
const UNKNOWN_CMD = new Set(["PREPARED_UNKNOWN", "SUBMIT_UNKNOWN", "CANCEL_UNKNOWN"]);

const VALUATION_TIER_CN = {
  EXECUTABLE_TWO_LEG_QUOTE: "可执行双腿盘口",
  INDICATIVE_MARK_ONLY: "仅标记价参考",
  UNPRICED: "无有效盘口",
};

const HALT_CN = {
  CONDITION_TRIGGERED: "条件触发",
  P5_CONDITION_TRIGGERED: "条件触发",
  P5_PRE_ENTRY_INVALIDATED: "条件触发",
  ENTRY_WINDOW_EXPIRED: "建仓窗口结束",
  ES_RESCUE_WINDOW_EXPIRED: "短腿补齐窗口结束",
  ES_PRE_EXPIRY_CUTOFF: "临近到期停止卖短腿",
  EP_PRE_EXPIRY_CUTOFF: "临近到期停止建仓",
  EP_ABORTED_OR_REJECTED: "保护腿建仓中止",
  PROTECTION_CAP_EXCEEDED: "保护腿预算超限",
  OPERATOR_HALT_ENTRY: "人工暂停新增",
  OPERATOR_PAUSE_ENTRY: "人工暂停新增",
  WRITES_DISABLED: "已停写",
  MARGIN_OR_STRESS_INSUFFICIENT: "资金或压力门不足",
  MARGIN_PRECHECK_FAILED: "资金或压力门不足",
  CAPITAL_NOT_ARMED: "入场未启用",
  RUNTIME_ANOMALY_HALT: "运行异常",
};

// Alerts the reader must act on or know about. Anything not listed reads as a generic notice.
const ALERTS = [
  [/^NAKED_SHORT_BREACH/, "bad", "保护腿少于短腿", "存在裸空敞口，执行器已锁存退出并优先买回短腿。"],
  [/^RX_MARGIN_OR_CASH_EXHAUSTED/, "bad", "现金不足，短腿买回失败",
    "保护腿保持锁定，价差风险仍在；执行器 10 秒退避重试，补保或持有至到期交割需人工决定。"],
  [/^UNKNOWN_UNVERIFIABLE_PAST_HOT_CACHE:/, "bad", "未知订单超出缓存", "未知订单超过热缓存核验期，需人工核对并处理后才能恢复。"],
  [/^ORDER_RECONCILE_CONFLICT_/, "bad", "订单对账冲突", "交易所历史与本地成交数量不一致，对应锁定量冻结等待人工核对。"],
  [/^ORDER_TRADES_EXCEED_ORDER_FILLED:/, "bad", "成交汇总异常", "明细成交量超过订单成交量，新操作已保护性阻断。"],
  [/^(HOLD_REVIEW_LLM_UNAVAILABLE|M1_TRIGGERED_LLM_UNAVAILABLE|HOLD_REVIEW_FAILED)/, "bad", "持仓复审不可用",
    "AI 牌手暂时无法复核这一局，不会机械平仓；REVOKE 判断缺位期间需留意。"],
  [/^EXTERNAL_UNATTRIBUTED_POSITION/, "bad", "账户出现未归因仓位", "新开仓已阻断，需核对子账户。"],
  [/^UNEXPECTED_(LONG_IN_SHORT|SHORT_IN_LONG)_LEG/, "bad", "腿上出现反向持仓", "持仓方向与这一局不符，需核对子账户。"],
  [/^P5_UNVERIFIED_REVOKE_IGNORED:/, "warn", "忽略未核验撤销", "P5 撤销未通过影子退出核验，暂不机械平仓。"],
  [/^ENTRY_GATE_WAIT:/, "warn", "建仓门控等待", "资金或保证金检查暂未放行，等待条件满足后再建仓。"],
  [/^QUOTE_UNAVAILABLE_AT_REVOKE/, "warn", "退出时缺有效报价", "短腿卖价或标记价不可用，执行器逐秒重读后再买回。"],
  [/^(MARK_MISSING_AT_REVOKE|ASK_MISSING_AT_REVOKE)/, "warn", "退出时报价不全", "已按交易所价格带或标记价护栏买回短腿。"],
  [/^(TP_UNATTAINABLE|TP_UNATTAINABLE_ALERT)/, "warn", "未挂止盈单", "匹配净信用过低，最小合法价位也无盈利空间；仓位仍由 REVOKE 与到期覆盖。"],
  [/^CANCEL_SLOW_ALERT/, "warn", "撤单确认偏慢", "撤单超过 8 秒未确认，继续核验交易所订单终态。"],
  [/^STAGE2_DELIVERY_FUTURE_PENDING/, "warn", "交割过渡期货未结", "实值期权已转为同到期期货，等待交割现金流。"],
  [/^EXIT_PRICE_BELOW_EXCHANGE_MIN_BAND/, "warn", "退出限价低于价格带", "短腿买回价格低于交易所下限，等待盘口恢复后重试。"],
  [/^CLOCK_SKEW_HIGH/, "warn", "交易所时钟偏差大", "时间判定可能偏移，读数时效需留意。"],
  [/^VENUE_DEGRADED/, "warn", "交易所链路降级", "读取或限流异常，执行器按退避节奏继续。"],
  [/^(WRITES_DISABLED_OPEN_ORDERS|EXIT_CAP_EXCEEDED)/, null, null, null],  // covered by other lines
];

/* ── small readers ──────────────────────────────────────── */

const DEC_RE = /^-?\d+(?:\.\d+)?$/;
const SCI_RE = /^-?\d+(?:\.\d+)?[eE][+-]?\d+$/;
function normDecStr(v) {
  if (v == null) return null;
  if (typeof v === "number") {
    if (!Number.isFinite(v)) return null;
    return v.toFixed(8).replace(/\.?0+$/, "") || "0";
  }
  const s = String(v).trim();
  if (!s) return null;
  if (DEC_RE.test(s)) return s;
  if (SCI_RE.test(s)) {
    const n = Number(s);
    if (!Number.isFinite(n)) return null;
    return n.toFixed(8).replace(/\.?0+$/, "") || "0";
  }
  return null;
}
const isDec = (v) => normDecStr(v) !== null;
const decNum = (v) => { const s = normDecStr(v); return s !== null ? Number(s) : null; };
const nonZero = (v) => { const n = decNum(v); return n !== null && n !== 0; };

/** A decimal string as written by the coordinator; missing stays unknown, never zero. */
export function fmtDec(v, unit = "", missing = "未知") {
  const raw = normDecStr(v);
  if (raw === null) return missing;
  const neg = raw.startsWith("-") && Number(raw) !== 0;
  const [i, f = ""] = raw.replace(/^-/, "").split(".");
  const frac = f.replace(/0+$/, "");
  const out = (neg ? "−" : "") + i + (frac ? `.${frac}` : "");
  return unit ? `${out} ${unit}` : out;
}

function signed(v, unit = "BTC") {
  const n = decNum(v);
  if (n === null) return "未知";
  return (n > 0 ? "+" : "") + fmtDec(v, unit);
}

function sideCn(side) {
  const s = String(side || "").toLowerCase();
  return s === "put" ? "Put 价差" : s === "call" ? "Call 价差" : "价差";
}

const HAND_REF = /^Deribit_BTC_\d{8}T\d{6}Z\/hand_[0-9a-f]{24}$/;
export function dossierHref(ep) {
  const ref = ep?.dossier_id && ep?.hand_id ? `${ep.dossier_id}/${ep.hand_id}` : "";
  return HAND_REF.test(ref) ? `#player-hand/${ref}` : null;
}

/* ── fixture (local preview only) ───────────────────────── */

/** Pick one scenario from the simulated fixture file, or accept a single status object. */
export function pickExecFixture(raw, requestedKey = null) {
  if (!raw || typeof raw !== "object") return null;
  if (raw.states && typeof raw.states === "object") {
    const keys = Object.keys(raw.states);
    if (!keys.length) return null;
    const own = (k) => k && Object.prototype.hasOwnProperty.call(raw.states, k);
    const key = own(requestedKey) ? requestedKey : own(raw.default) ? raw.default : keys[0];
    const labels = raw.labels && typeof raw.labels === "object" ? raw.labels : {};
    return { status: raw.states[key], key, keys, labels };
  }
  if (typeof raw.schema === "string" && raw.schema.startsWith("astra_exec_status@")) {
    return { status: raw, key: "default", keys: [], labels: {} };
  }
  return null;
}

/* ── model ──────────────────────────────────────────────── */

/**
 * Server-side age is the authority (Workbench recomputes status_age_ms / facts_age_ms / stale per read;
 * facts only count while an episode is open). Locally we only add the time since this page fetched it,
 * and call it stale on our own only when we have not re-read for well past one poll.
 */
function freshness(status, nowMs, fixture, fetchedAtMs) {
  const active = Boolean(status.active_episode);
  const elapsed = !fixture && isNum(fetchedAtMs) ? Math.max(0, nowMs - fetchedAtMs) : 0;
  const ages = [];
  if (isNum(status.status_age_ms)) ages.push(status.status_age_ms);
  if (active && isNum(status.facts_age_ms)) ages.push(status.facts_age_ms);
  // a raw coordinator file without Workbench recomputation: fall back to its own generation time
  if (!ages.length && !fixture && isNum(status.generated_at_ms)) ages.push(Math.max(0, (isNum(fetchedAtMs) ? fetchedAtMs : nowMs) - status.generated_at_ms));
  const ageMs = ages.length ? Math.max(...ages) + elapsed : null;
  const limit = isNum(status.stale_after_ms) ? status.stale_after_ms : 15_000;
  const health = String(status.health || "").toUpperCase();
  const stale = status.stale === true || health === "STALE" || health === "DISCONNECTED"
    || !isNum(ageMs) || elapsed > limit + 30_000 || (!ages.length ? false : Math.max(...ages) > limit);
  const what = active && isNum(status.facts_age_ms) ? "事实" : "状态";
  const text = !isNum(ageMs) ? "时效未知"
    : ageMs < 60_000 ? `${what} ${Math.round(ageMs / 1000)} 秒前` : `${what} ${dur(ageMs)}前`;
  return { ageMs, limit, stale, text, disconnected: health === "DISCONNECTED" };
}

function authOf(status) {
  return status.capital_policy || status.capital_auth || status.capital || null;
}

function armedOf(status) {
  const auth = authOf(status);
  const v = auth && "entry_armed" in auth ? auth.entry_armed : ("entry_armed" in status ? status.entry_armed : null);
  if (auth && auth.entry_paused === true) return false;
  return v === true ? true : v === false ? false : null;
}

function alertLines(codes) {
  const out = [];
  const seen = new Set();
  for (const raw of codes) {
    const code = String(raw || "");
    const rule = ALERTS.find(([re]) => re.test(code));
    if (rule && rule[1] === null) continue;
    const key = rule ? rule[2] : code.split(":")[0];
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(rule ? { tone: rule[1], title: rule[2], text: rule[3], code }
      : { tone: "warn", title: "执行提醒", text: "协调器发出未归类提示，详见下方原始编号。", code });
  }
  return out;
}

function stageOf(ep, nowMs, takeover, cmds = []) {
  const expired = isNum(ep.expiry_ms) && isNum(nowMs) && nowMs >= ep.expiry_ms;
  const postEntry = Boolean(ep.entry_closed || ep.exit_latched);
  const shortOpen = nonZero(ep.short_pos_btc) || ep.short_liability_cleared === false;
  const hasStage2Future = (Array.isArray(ep.alerts) && ep.alerts.some((a) => /^STAGE2_DELIVERY_FUTURE_PENDING/.test(String(a))))
    || Boolean(ep.ledger?.two_stage_settlement?.has_itm && ep.inventory_cleared !== true);
  const hasUnknown = cmds.some((c) => UNKNOWN_CMD.has(String(c?.status || "").toUpperCase()));
  const hasOpenProt = nonZero(ep.prot_pos_btc);

  if (ep.cashflow_settled === true) {
    return { key: "settled", text: OUTCOME_CN[ep.terminal_outcome] || "已结清", tone: "fit" };
  }
  if (takeover) return { key: "takeover", text: "已停写 · 风险未解除", tone: "unfit" };
  if (ep.exit_latched && shortOpen) {
    const who = ep.exit_latch_reason === "AI_REVOKE" ? "REVOKE 退出中"
      : ep.exit_latch_reason === "OPERATOR_EXIT_ALL" ? "强制退出中" : "纠错退出中";
    return { key: "exit", text: who, tone: "unfit" };
  }
  if (hasUnknown && !shortOpen && !hasOpenProt) {
    return { key: "unknown_pending", text: ep.exit_latched ? "退出中 · 订单结果待核验" : "订单结果待核验", short: "结果未知", tone: "unfit" };
  }
  if (expired) {
    // Settlement normally lands within minutes of 08:00 UTC; hours without it deserves a look.
    const late = nowMs - ep.expiry_ms > 2 * 3_600_000;
    if (hasStage2Future) {
      return { key: "expired_stage2", text: late ? "已到期 · 交割期货未结且超时" : "已到期 · 交割期货待结算", short: "期货待交割", tone: "contested", late, stage2: true };
    }
    return { key: "expired", text: late ? "已到期 · 交割久未落账" : "已到期 · 待交割落账", short: "到期待落账", tone: late ? "contested" : "live", late };
  }
  if (postEntry && ep.inventory_cleared === true) return { key: "inventory", text: "库存已清 · 待流水落账", short: "待流水落账", tone: "live" };
  if (postEntry && ep.short_liability_cleared === true) {
    if (!hasOpenProt && ep.residual?.status !== "LX_ACTIVE") {
      return { key: "residual", text: "两腿已清 · 待库存确认", short: "待库存确认", tone: "live", flat: true };
    }
    const hold = ep.residual?.status === "RESIDUAL_HOLD";
    return { key: "residual", text: hold ? "短腿已清 · 保护腿持有" : "短腿已清 · 回收保护腿", short: "残值处置", tone: "live" };
  }
  if (ep.phase === "ENTRY" && !ep.entry_closed) return { key: "entry", text: "建仓中", tone: "live" };
  if (ep.entry_closed || ep.phase === "TP_MANAGED" || ep.phase === "ENTRY_CLOSED") {
    return { key: "hold", text: "持仓 · 止盈托管", short: "止盈托管", tone: "live" };
  }
  return { key: "unknown", text: "状态未知", tone: "none" };
}

function tpLine(tp) {
  const st = String(tp?.substate || "");
  const ordSt = String(tp?.order_status || "").toUpperCase();
  const px = isDec(tp?.active_limit_price_btc) ? `买回限价 ${fmtDec(tp.active_limit_price_btc)}` : null;
  const rem = isDec(tp?.active_remaining_btc) ? `剩余 ${fmtDec(tp.active_remaining_btc, "BTC")}` : null;
  const cap = decNum(tp?.target_capture_ratio ?? tp?.capture_ratio);
  if (ordSt && UNKNOWN_CMD.has(ordSt)) return { text: "止盈单结果未知，核验中", tone: "bad" };
  if (st === "TP_ACTIVE" || st === "TP_CLAMPED_TO_MIN_TICK") {
    if (ordSt && ordSt !== "OPEN") return { text: "止盈单未在挂（等待重挂或核验）", tone: "warn" };
    if (st === "TP_ACTIVE") return { text: ["止盈单挂单中", px, rem].filter(Boolean).join(" · "), tone: "" };
    return { text: ["止盈单挂在最小价位", px, rem, isNum(cap) ? `实际捕获 ${Math.round(cap * 100)}%` : "非 85%"].filter(Boolean).join(" · "), tone: "" };
  }
  if (st === "TP_DEFERRED_EXCHANGE_BAND") return { text: "止盈暂缓：价格带暂不允许合法挂单，条件恢复后自动补挂", tone: "warn" };
  if (st.startsWith("TP_UNATTAINABLE")) return { text: "未挂止盈：净信用过低，没有盈利价位", tone: "warn" };
  if (st === "TP_FROZEN_NEAR_EXPIRY") return { text: "临近到期，止盈单不再改动", tone: "" };
  if (st === "TP_READY") return { text: "建仓已封口，正在挂止盈单", tone: "" };
  return { text: "止盈单状态未读到", tone: "" };
}

function actionOf(ep, stage, cmds) {
  const entry = ep.ledger?.entry || {};
  const open = (p) => cmds.find((c) => c.purpose === p);
  const unknownOf = (p) => cmds.find((c) => c.purpose === p && UNKNOWN_CMD.has(String(c.status || "").toUpperCase()));
  const hasUnknown = cmds.some((c) => UNKNOWN_CMD.has(String(c?.status || "").toUpperCase()));
  const anyLegUnknown = unknownOf("EP") || unknownOf("ES") || unknownOf("RX") || unknownOf("LX");
  const cashOut = ep.revoke && "cash_wall_10009" in ep.revoke
    ? ep.revoke.cash_wall_10009 === true
    : (ep.alerts || []).some((a) => /^RX_MARGIN_OR_CASH_EXHAUSTED/.test(String(a)));
  if (stage.key === "settled") return { text: "本局已结束，无在途订单", tone: "" };
  if (stage.key === "takeover") {
    return { text: `执行器已停写，不再下单、撤单或改单；交易所上 ${cmds.length} 笔在途订单保持原样`, tone: "bad" };
  }
  if (stage.key === "unknown_pending") {
    return { text: "在途订单回执不明，核验终态前冻结操作，不重复下单", tone: "bad" };
  }
  if (stage.key === "expired_stage2") {
    return { text: "实值期权已转为同到期交割期货，等待交易所结算落账，勿手动重复平仓", tone: "warn" };
  }
  if (stage.key === "inventory") {
    if (anyLegUnknown) return { text: "在途订单回执不明，核验终态前冻结操作，不重复下单", tone: "bad" };
    return { text: "仓位已归零，等待成交与交割流水全部落账", tone: "" };
  }
  if (stage.key === "exit") {
    const rx = open("RX");
    const since = isNum(ep.exit_started_at_ms) ? `，${clock(ep.exit_started_at_ms)} 起` : "";
    if (cashOut) return { text: `买回短腿被拒（现金不足），每 10 秒重试；保护腿保持锁定${since}`, tone: "bad" };
    if (unknownOf("RX")) return { text: `买回短腿订单结果未知，核验中${since}`, tone: "bad" };
    return {
      text: rx ? `限价 IOC 买回短腿 ${fmtDec(rx.amount, "BTC")}（已成 ${fmtDec(rx.filled_amount)}）${since}`
        : `已撤原止盈，正在买回短腿${since}`,
      tone: "warn",
    };
  }
  if (stage.key === "residual") {
    if (unknownOf("LX")) return { text: "回收保护腿订单结果未知，核验中", tone: "bad" };
    if (hasUnknown) return { text: "在途订单回执不明，核验终态前冻结操作，不重复下单", tone: "bad" };
    if (stage.flat) return { text: "仓位已归零，等待在途订单与库存确认", tone: "" };
    const lx = open("LX") || ep.residual?.status === "LX_ACTIVE";
    return lx ? { text: "限价 IOC 回收保护腿残值", tone: "" }
      : { text: "保护腿买盘不足，持有待复评或到期归零", tone: "" };
  }
  if (stage.key === "entry") {
    const unk = unknownOf("ES") || unknownOf("EP");
    if (unk) return { text: `${PURPOSE_CN[unk.purpose]}订单结果未知，核验前不再下单`, tone: "bad" };
    if (ep.entry_halted) return { text: `已停止新增（${HALT_CN[ep.entry_halt_reason] || "原因见卷宗"}），在途单收口`, tone: "warn" };
    if (open("ES")) return { text: "保护已到位，限价 IOC 卖短腿", tone: "" };
    if (open("EP")) return { text: "限价 IOC 买保护腿", tone: "" };
    return { text: `保护腿已成 ${fmtDec(entry.ep_filled_btc)} · 短腿已成 ${fmtDec(entry.es_filled_btc)}，建仓窗口内`, tone: "" };
  }
  if (stage.key === "expired") return { text: "已过到期时间，止盈单由交易所终止，等待交割落账", tone: "" };
  return tpLine(ep.tp);
}

function trackOf(ep, stage) {
  const exit = Boolean(ep.exit_latched);
  const postEntry = Boolean(ep.entry_closed || ep.exit_latched);
  const notUnknown = stage.key !== "unknown_pending";
  const done = {
    entry: Boolean(ep.entry_closed) || ["hold", "exit", "residual", "inventory", "settled", "expired", "expired_stage2"].includes(stage.key),
    short: postEntry && ep.short_liability_cleared === true && notUnknown,
    inv: postEntry && ep.inventory_cleared === true && notUnknown,
    cash: ep.cashflow_settled === true,
  };
  const steps = [
    { k: "entry", label: "建仓", done: done.entry },
    { k: "mid", label: exit ? "退出" : "托管", done: done.short, alert: exit },
    { k: "short", label: "短腿清", done: done.short },
    { k: "inv", label: "库存清", done: done.inv },
    { k: "cash", label: "结清", done: done.cash },
  ];
  const cur = steps.findIndex((s) => !s.done);
  steps.forEach((s, i) => { s.state = s.done ? "done" : i === cur ? "now" : "todo"; });
  return steps;
}

/** One reading of the execution status: everything the panel and the status strip show. */
export function execModel(status, { fixture = false, nowMs = Date.now(), fetchedAtMs = null, readError = false } = {}) {
  const mode = String(status?.mode || "").toUpperCase();
  const modeInfo = fixture ? { text: "模拟数据 · 非真实状态", tone: "sim" } : (MODE[mode] || { text: "运行模式未知", tone: "unknown" });
  const fresh = freshness(status || {}, nowMs, fixture, fetchedAtMs);
  const ep = status?.active_episode && typeof status.active_episode === "object" ? status.active_episode : null;
  const refNow = fixture && isNum(status?.generated_at_ms) ? status.generated_at_ms : nowMs;
  const cmds = Array.isArray(ep?.open_or_unknown_commands) ? ep.open_or_unknown_commands : [];
  const unknown = cmds.filter((c) => UNKNOWN_CMD.has(String(c?.status || "").toUpperCase()));
  const shortOpen = Boolean(ep && nonZero(ep.short_pos_btc));
  const protOpen = Boolean(ep && nonZero(ep.prot_pos_btc));
  const openRisk = Boolean(ep && ep.cashflow_settled !== true && (shortOpen || protOpen || cmds.length));
  const writesOff = status?.writes_enabled === false;
  const shadowMode = mode === "SHADOW_ONLY" || mode === "DRY_RUN";
  // The producer's own flag first; infer only where writes are a real mode (v1: TESTNET only).
  const takeover = Boolean(status?.manual_takeover_required) || (writesOff && mode === "TESTNET" && openRisk);
  const stage = ep ? stageOf(ep, refNow, takeover, cmds) : { key: "idle", text: "空闲", tone: "none" };

  const banners = [];
  if (takeover) {
    banners.push({ tone: "bad", title: "已停写，风险未解除",
      text: `执行器不再向交易所写入，也不会自动恢复；交易所上的仓位与挂单保留原样，需人工在子账户接管。`
        + (ep ? ` 当前短腿 ${fmtDec(ep.short_pos_btc, "BTC")}、保护腿 ${fmtDec(ep.prot_pos_btc, "BTC")}、在途订单 ${cmds.length} 笔。` : "") });
  }
  if (unknown.length) {
    banners.push({ tone: "bad", title: `${unknown.length} 笔订单结果未知`,
      text: "交易所回执不明，按完整证据核验前对应数量保持锁定，不新增风险、不重复下单；退出与止盈的既有保护照常。" });
  }
  const activeAlerts = (Array.isArray(ep?.alerts) ? ep.alerts : []).filter((a) => {
    const code = String(a || "");
    if (takeover && /^WRITES_DISABLED/.test(code)) return false;
    if (ep?.revoke?.cash_wall_10009 === false && /^RX_MARGIN_OR_CASH_EXHAUSTED/.test(code)) return false;
    return true;
  });
  banners.push(...alertLines(activeAlerts));
  if (fresh.stale) {
    banners.push({ tone: openRisk || fresh.disconnected ? "bad" : "warn",
      title: fresh.disconnected ? "执行状态中断" : "执行状态过期",
      text: `${isNum(fresh.ageMs) ? `最近事实已是 ${fresh.ageMs < 60_000 ? `${Math.round(fresh.ageMs / 1000)} 秒` : dur(fresh.ageMs)}前（阈值 ${Math.round(fresh.limit / 1000)} 秒）` : "缺少事实时间"}，以下读数不代表交易所当前状态。` });
  } else if (readError) {
    banners.push({ tone: "warn", title: "最近一次读取失败", text: "仍显示上一次读到的状态，过期后会明确标出。" });
  } else if (String(status?.health || "").toUpperCase() === "DEGRADED" || status?.last_error) {
    banners.push({ tone: "warn", title: "执行器降级运行", text: "最近一次读取或运行出错，执行器按退避节奏继续；详见下方原始编号。" });
  }

  const bad = banners.some((b) => b.tone === "bad");
  const warn = banners.some((b) => b.tone === "warn") || stage.key === "exit" || Boolean(stage.late) || Boolean(stage.stage2);
  const level = bad ? "bad" : warn ? "warn" : ep ? "ok" : "off";
  const stripCore = takeover ? "需人工接管"
    : unknown.length ? `${unknown.length} 笔结果未知`
    : bad ? banners.find((b) => b.tone === "bad").title.split("，")[0]
    : ep ? (stage.short || stage.text)
    : fresh.stale ? "状态过期"
    : shadowMode ? "影子待命" : "空闲";

  const acct = status?.account && typeof status.account === "object" ? status.account : null;
  const buffer = decNum(acct?.margin_buffer_ratio);
  const auth = status ? authOf(status) : null;
  const effBudget = auth?.effective_budget_btc ?? auth?.b_eff_btc;
  const hist = Array.isArray(status?.history_episodes) ? status.history_episodes : [];

  return {
    mode, modeInfo, fixture, fresh, armed: armedOf(status || {}), shadowMode, writesOff, takeover,
    level, stripText: `${fixture ? "模拟" : "执行"} · ${stripCore}`,
    stripTitle: `${modeInfo.text} · ${fresh.text}`,
    banners, stage, ep, cmds, unknown, openRisk, refNow,
    action: ep ? actionOf(ep, stage, cmds) : null,
    track: ep ? trackOf(ep, stage) : null,
    account: acct ? {
      available: acct.available_funds_btc, equity: acct.equity_btc, balance: acct.balance_btc,
      buffer, bufferTone: isNum(buffer) && buffer < 2.5 ? "bad" : isNum(buffer) && buffer < 4 ? "warn" : "",
    } : null,
    budget: auth && (isDec(effBudget) || isDec(auth.budget_cap_btc))
      ? { eff: effBudget, cap: auth.budget_cap_btc } : null,
    last: hist[0] || null,
  };
}

/** The status-strip lamp; never green without a fresh, alert-free real reading. */
export function execStripModel(status, error = null, opts = {}) {
  if (!status) {
    if (error?.status === 404) return { hidden: true, level: "off", text: "执行未接入", title: "" };
    if (error) return { hidden: false, level: "bad", text: "执行读取失败", title: execErrorCn(error) };
    return { hidden: true, level: "off", text: "执行", title: "" };
  }
  const m = execModel(status, { ...opts, readError: Boolean(error) });
  return { hidden: false, level: m.level, text: m.stripText, title: m.stripTitle };
}

/** Whether the panel should re-read faster than the page poll. */
export function execNeedsFastPoll(status) {
  const ep = status?.active_episode;
  return Boolean(ep && ep.cashflow_settled !== true);
}

export function execErrorCn(err) {
  if (err?.status === 401) return "工作台会话已过期，请重新登录";
  if (err?.status === 404) return "执行通道未接入";
  if (err?.status) return `服务器返回 HTTP ${err.status}`;
  return /fetch|network/i.test(err?.message || "") ? "无法连接工作台服务端" : (err?.message || "读取失败");
}

/* ── render ─────────────────────────────────────────────── */

function row(k, main, sub, tone = "") {
  return h("div", { class: `drow exec-row ${tone}`.trim() },
    h("div", { class: "drow-k", text: k }),
    h("div", { class: "drow-body" },
      h("div", { class: "drow-main" }, ...[].concat(main)),
      ...[].concat(sub || []).filter(Boolean).map((s) => (s instanceof Node ? s : h("div", { class: "drow-sub", text: s })))));
}

function renderHead(m, opts) {
  const tags = h("div", { class: "exec-tags" },
    h("span", { class: `seal ${m.stage.tone}`, text: m.stage.text }),
    h("span", { class: `chip exec-mode ${m.modeInfo.tone}`, text: m.modeInfo.text }),
    m.fixture || m.shadowMode ? null
      : m.armed === true ? h("span", { class: "chip", text: "入场已启用" })
      : m.armed === false ? h("span", { class: "chip warn", text: "入场未启用" }) : null);
  const meta = [];
  if (m.account) {
    meta.push(`可用 ${fmtDec(m.account.available, "BTC")}`);
    if (isNum(m.account.buffer)) meta.push(h("span", { class: m.account.bufferTone ? `${m.account.bufferTone}-text` : "", text: `维持缓冲 ${m.account.buffer.toFixed(1)} 倍` }));
  }
  if (m.budget && isDec(m.budget.eff) && (!m.shadowMode || (decNum(m.budget.cap) || 0) > 0 || nonZero(m.budget.eff))) {
    meta.push(`风险预算 ${fmtDec(m.budget.eff)}${isDec(m.budget.cap) ? ` / ${fmtDec(m.budget.cap, "BTC")}` : " BTC"}`);
  }
  meta.push(h("span", { class: m.fresh.stale ? "warn-text" : "", text: m.fresh.text }));
  const right = h("div", { class: "exec-meta num" });
  meta.forEach((x, i) => { if (i) right.append(h("i", { class: "sep", text: "·" })); right.append(x); });
  return h("div", { class: "exec-head" }, tags, right);
}

function renderBanners(m) {
  if (!m.banners.length) return null;
  return h("div", { class: "exec-banners" }, ...m.banners.map((b) =>
    h("div", { class: `exec-banner ${b.tone === "bad" ? "danger" : "warn"}`, role: b.tone === "bad" ? "alert" : null, title: b.code || null },
      h("b", { text: b.title }), h("p", { text: b.text }))));
}

function renderIdle(m) {
  const why = m.shadowMode ? "影子模式只观察 AI 牌手决策，不会下单。"
    : m.writesOff ? "执行器写入已停用，不会新开仓。"
    : m.armed === false ? "入场未启用：出现 Accept 也不会新开仓。"
    : m.armed === true ? "出现新鲜有效的 AI 牌手 Accept 且资金与保证金门通过后，执行器自动建仓。"
    : "出现新鲜有效的 AI 牌手 Accept 后由执行器自动建仓（入场启用状态未接入）。";
  const box = h("div", { class: "exec-idle" }, h("p", { class: "exec-idle-main" }, h("b", { text: "无未结清对局。" }), ` ${why}`));
  const last = m.last;
  if (last) {
    const href = dossierHref(last);
    const lastPnl = last.episode_total_realized_pnl_btc ?? last.realized_pnl_btc;
    const pnl = isDec(lastPnl) ? `已实现 ${signed(lastPnl)}` : null;
    box.append(h("p", { class: "exec-last num" },
      h("span", { class: "muted", text: "上一局" }),
      ` ${sideCn(last.side)} · ${OUTCOME_CN[last.terminal_outcome] || "已结清"}`,
      pnl ? ` · ${pnl}` : "",
      isNum(last.updated_at_ms) ? ` · ${clock(last.updated_at_ms)}` : "",
      href ? h("a", { class: "exec-link", href, text: "卷宗" }) : null));
  }
  return box;
}

function renderTrack(steps) {
  return h("ol", { class: "exec-track", "aria-label": "这一局的结束路径" }, ...steps.map((s) =>
    h("li", { class: `${s.state}${s.alert ? " alert" : ""}` }, h("i"), h("span", { text: s.label }))));
}

function renderCmds(cmds) {
  if (!cmds.length) return null;
  return h("div", { class: "scroll-x exec-cmds" }, h("table", { class: "t compact" },
    h("thead", {}, h("tr", {}, ...["订单", "合约", "数量 / 已成", "限价", "状态"].map((x) => h("th", { text: x })))),
    h("tbody", {}, ...cmds.map((c) => {
      const st = String(c.status || "").toUpperCase();
      return h("tr", {},
        h("td", { text: PURPOSE_CN[c.purpose] || c.purpose || "订单" }),
        h("td", { class: "num", text: c.instrument_name || "—" }),
        h("td", { class: "num", text: `${fmtDec(c.amount)} / ${fmtDec(c.filled_amount)}` }),
        h("td", { class: "num", text: fmtDec(c.limit_price, "", "—") }),
        h("td", {}, h("span", { class: `chip ${UNKNOWN_CMD.has(st) ? "bad" : st === "OPEN" ? "" : "ok"}`, text: CMD_CN[st] || st || "未知" })));
    }))));
}

function renderEpisode(m) {
  const ep = m.ep;
  const entry = ep.ledger?.entry || {};
  const acct = ep.ledger?.accounting || {};
  const href = dossierHref(ep);
  const qty = isDec(ep.target_qty_btc) ? ep.target_qty_btc : entry.matched_qty_btc;
  const left = isNum(ep.expiry_ms) ? (m.refNow < ep.expiry_ms ? `剩 ${dur(ep.expiry_ms - m.refNow)}` : "已过到期") : null;

  const title = h("div", { class: "exec-ep-title" },
    h("h3", { text: `${sideCn(ep.side)} · 卖 ${ep.short_instrument || "未知"} / 买 ${ep.long_instrument || "未知"}` }),
    h("span", { class: "muted small num", text: [isDec(qty) ? `${fmtDec(qty)} BTC` : null,
      isNum(ep.expiry_ms) ? `到期 ${clock(ep.expiry_ms)}` : null, left].filter(Boolean).join(" · ") }),
    href ? h("a", { class: "exec-link", href, text: "查看卷宗" }) : null);

  // AI player: the hand exists only because of a valid Accept; REVOKE is the one AI signal that ends it early.
  const dec = ep.decision || {};
  const decAt = dec.decided_at_ms ?? (isNum(ep.decided_at_ms) && ep.decided_at_ms > 0 ? ep.decided_at_ms : null);
  const rev = ep.latest_review || null;
  const aiMain = [h("span", { class: "drow-v", text: "Accept" }),
    isNum(decAt) ? h("span", { class: "muted small num", text: clock(decAt) }) : null];
  if (ep.exit_latched && ep.exit_latch_reason === "AI_REVOKE") {
    aiMain.push(h("span", { class: "drow-v bad-text", text: "→ REVOKE" }),
      isNum(ep.exit_started_at_ms) ? h("span", { class: "muted small num", text: clock(ep.exit_started_at_ms) }) : null);
  } else if (rev?.action) {
    aiMain.push(h("span", { class: "chip", text: `最近复核 ${rev.action}` }),
      isNum(rev.at_ms) ? h("span", { class: "muted small num", text: ago(rev.at_ms, m.refNow) }) : null);
  }
  const aiSub = ep.exit_latched && ep.exit_latch_reason && ep.exit_latch_reason !== "AI_REVOKE"
    ? `退出原因：${EXIT_CN[ep.exit_latch_reason] || "其他"}（非 AI 信号）`
    : ep.entry_halted && /CONDITION|INVALIDATED/.test(String(ep.entry_halt_reason || ""))
      ? "条件触发：只停止新增，已成仓位继续托管，等待 AI 正式复审" : null;

  // Real position from venue facts.
  const shortQ = decNum(ep.short_pos_btc), protQ = decNum(ep.prot_pos_btc);
  const covered = isNum(shortQ) && isNum(protQ) ? protQ >= Math.abs(shortQ) : null;
  const posMain = [
    h("span", { class: "drow-v num", text: `短腿 ${fmtDec(ep.short_pos_btc)}` }),
    h("span", { class: "drow-v num", text: `保护腿 ${fmtDec(ep.prot_pos_btc)}` }),
    covered === false ? h("span", { class: "chip bad", text: "保护不足" })
      : covered === true && nonZero(ep.short_pos_btc) ? h("span", { class: "chip ok", text: "保护覆盖" }) : null,
    m.stage.stage2 ? h("span", { class: "chip warn", text: "交割期货未结" }) : null,
  ];
  const matchedPos = nonZero(entry.matched_qty_btc);
  const posSub = [
    matchedPos && isDec(entry.matched_net_credit_btc) ? `配对净信用 ${fmtDec(entry.matched_net_credit_btc, "BTC")}` : null,
    isDec(acct.unrealized_executable_pnl_btc) && m.stage.key !== "settled" ? `按盘口可平 ${signed(acct.unrealized_executable_pnl_btc)}` : null,
  ].filter(Boolean).join(" · ") || null;

  const rows = [
    row("AI 牌手", aiMain, aiSub),
    row("执行器", h("span", { class: `drow-v ${m.action.tone ? `${m.action.tone}-text` : ""}`.trim(), text: m.action.text }), null),
    row("真实仓位", posMain, posSub),
  ];
  const postEntry = Boolean(ep.entry_closed || ep.exit_latched);
  const settledish = ep.cashflow_settled === true || (postEntry && ep.inventory_cleared === true && m.stage.key !== "unknown_pending" && !m.stage.stage2);
  const endSub = settledish
    ? [isDec(acct.episode_total_realized_pnl_btc) ? `已实现 ${signed(acct.episode_total_realized_pnl_btc)}` : "已实现 未知",
       isDec(acct.actual_fees_btc) ? `手续费 ${fmtDec(acct.actual_fees_btc, "BTC")}` : null,
       ep.cashflow_settled === true ? "以交易所流水为准" : "流水落齐前不计为结清"].filter(Boolean).join(" · ")
    : m.stage.key === "exit" ? "短腿买回后回收保护腿，再核对流水结清"
    : "正常结束：止盈成交或到期交割；AI REVOKE 会提前退出";
  rows.push(row("一局结束", renderTrack(m.track), endSub));

  // The executor row already names the one live order; the table only earns its place when there is more to see.
  const showCmds = m.unknown.length > 0 || m.cmds.length > 1 || m.takeover;
  return h("div", { class: "exec-episode" }, title, h("div", { class: "deck-rows exec-rows" }, ...rows), showCmds ? renderCmds(m.cmds) : null);
}

function renderMore(status, m) {
  const ep = m.ep;
  const det = h("details", { class: "exec-more" }, h("summary", { text: ep ? "实盘账本、影子参照与原始编号" : "原始编号" }));
  const inner = h("div", { class: "exec-more-body" });
  if (ep) {
    const e = ep.ledger?.entry || {}, a = ep.ledger?.accounting || {};
    const kv = (rows) => h("dl", { class: "kv" }, ...rows.flatMap(([k, v]) => [h("dt", { text: k }), h("dd", { text: v })]));
    inner.append(h("section", { class: "exec-book real" }, h("h4", { text: "实盘账本 · 交易所成交与流水" }), kv([
      ["保护 / 短腿已成", `${fmtDec(e.ep_filled_btc)} / ${fmtDec(e.es_filled_btc, "BTC")}`],
      ["配对量 · 净信用", `${fmtDec(e.matched_qty_btc, "BTC")} · ${fmtDec(e.matched_net_credit_btc, "BTC")}`],
      ["净现金流", fmtDec(a.cashflow_btc, "BTC")],
      ["未平开仓信用", `${fmtDec(a.open_entry_credit_btc, "BTC")}（不计收益）`],
      ["已实现 · 本局合计", `${fmtDec(a.realized_closed_pnl_btc)} · ${fmtDec(a.episode_total_realized_pnl_btc, "BTC")}`],
      ["未配对保护成本", fmtDec(a.unmatched_prot_cost_btc, "BTC")],
      ["未实现 标记 / 盘口", `${fmtDec(a.unrealized_mark_pnl_btc)} / ${fmtDec(a.unrealized_executable_pnl_btc, "BTC")}`],
      ["交割现金流", fmtDec(a.settlement_pnl_btc, "BTC")],
      ["手续费 实付 / 预估", `${fmtDec(a.actual_fees_btc)} / ${fmtDec(a.estimated_fees_btc, "BTC")}`],
    ])));
    const sh = ep.shadow_counterfactual;
    const tier = sh?.valuation_tier ? (VALUATION_TIER_CN[sh.valuation_tier] || String(sh.valuation_tier)) : "未知";
    inner.append(h("section", { class: "exec-book shadow" }, h("h4", { text: "影子参照 · 研究对比，不是实盘流水" }),
      sh && typeof sh === "object" && Object.keys(sh).length
        ? kv([["影子退出盈亏", fmtDec(sh.shadow_exit_pnl_btc ?? sh.exit_pnl_btc ?? sh.indicative_exit_pnl_btc ?? sh.pnl_btc, "BTC")], ["估值层级", tier]])
        : h("p", { class: "muted small", text: "这一局暂无影子对照。" })));
  }
  inner.append(h("pre", { class: "exec-trace-pre", text: JSON.stringify({
    schema: status.schema, mode: status.mode, health: status.health, subaccount_id: status.subaccount_id,
    writes_enabled: status.writes_enabled, writes_disabled_reason: status.writes_disabled_reason, last_error: status.last_error,
    last_reject_reason: status.capital_policy?.last_reject_reason,
    generated_at_ms: status.generated_at_ms, facts_asof_ms: status.facts_asof_ms,
    episode_id: ep?.episode_id, hand_id: ep?.hand_id, dossier_id: ep?.dossier_id, phase: ep?.phase,
    entry_closure_reason: ep?.entry_closure_reason, entry_halt_reason: ep?.entry_halt_reason,
    exit_latch_reason: ep?.exit_latch_reason, tp: ep?.tp, residual: ep?.residual, alerts: ep?.alerts,
    commands: ep?.commands, open_or_unknown_commands: ep?.open_or_unknown_commands, two_stage_settlement: ep?.ledger?.two_stage_settlement,
    history_episodes: status.history_episodes,
  }, null, 2) }));
  det.append(inner);
  return det;
}

/** Render the read-only execution band into #exec-live. */
export function renderExecution(box, execState, opts = {}) {
  clear(box);
  if (opts.needsLogin) {
    box.append(h("p", { class: "empty pad", text: "登录工作台后可查看实盘执行状态。" }));
    return;
  }
  const status = execState?.status || null;
  const err = execState?.error || null;
  if (!status) {
    box.append(err?.status === 404
      ? h("p", { class: "empty pad", text: "执行通道未接入：工作台处于研究与影子追踪模式。" })
      : err ? h("div", { class: "exec-banner danger", role: "alert" }, h("b", { text: "执行状态读取失败" }),
        h("p", { text: `${execErrorCn(err)}。未显示任何推定状态。` }))
      : h("p", { class: "empty pad", text: "正在读取执行状态…" }));
    return;
  }
  const m = execModel(status, { fixture: Boolean(opts.fixture), nowMs: opts.nowMs ?? Date.now(), fetchedAtMs: opts.fetchedAtMs ?? null, readError: Boolean(err) });
  box.classList.toggle("exec-sim", m.fixture);
  if (m.fixture) {
    box.append(h("div", { class: "exec-sim-bar", "aria-label": "模拟情景" },
      h("b", { text: "模拟数据" }), h("span", { class: "muted small", text: "本地预览情景，不是交易所事实：" }),
      ...(execState.fixtureKeys || []).map((k) => h("button", {
        type: "button", class: `chip${execState.fixtureKey === k ? " on" : ""}`,
        text: execState.fixtureLabels?.[k] || k, onclick: () => opts.onPickFixture?.(k),
      }))));
  }
  box.append(renderHead(m, opts));
  const banners = renderBanners(m);
  if (banners) box.append(banners);
  box.append(m.ep ? renderEpisode(m) : renderIdle(m));
  const more = renderMore(status, m);
  more.open = Boolean(opts.moreOpen);
  more.addEventListener("toggle", () => opts.onToggleMore?.(more.open));
  box.append(more);
}
