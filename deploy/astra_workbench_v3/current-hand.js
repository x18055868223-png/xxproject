import { api } from "./api.js";
import { ago, isNum } from "./format.js";

export const LABELS = {
  AWAITING_DISPATCH: "牌面已冻结，尚无模型请求记录", DISPATCH_EXPIRED: "回复窗口已结束，未创建模型请求",
  REQUEST_IN_PROGRESS: "模型请求处理中，等待回执", DELIVERY_UNKNOWN: "模型回执待人工核对，自动重发已阻断", PROVIDER_REJECTED: "供应商已拒绝请求",
  REPLY_SAVED: "已保存回复，待解析归档", DECISION_PENDING: "已解析，待记录决策",
  HANDOFF_PENDING: "已接受，待核验新鲜盘口", HANDOFF_EXPIRED: "交接超时，未锁定",
  DEAL_BLOCKED: "发牌处理受阻", LINK_CONFIRMED: "已建立影子追踪",
  AWAITING_RESPONSE: "等待正式回复", NO_RESPONSE: "回复窗口已结束", EXPECTED: "待发牌",
  DATA_BLOCKED: "资料阻塞", MISSED: "错过采样窗口", PARSE_FAILED: "回复未通过解析",
  PROVENANCE_MISSING: "回复缺模型或作答时钟",
  REQUEST_FAILED: "请求失败", TIMEOUT: "模型超时", LATE: "回复超时到达",
  LLM_NOT_CONFIGURED: "模型密钥未配置", DAILY_BUDGET_EXHAUSTED: "今日调用上限已用尽",
  POLICY_BLOCKED: "策略受阻", TRACKER_EXPORT_UNAVAILABLE: "轨迹导出暂不可用",
  VALID: "解析有效", DUPLICATE: "重复回复",
  EVIDENCE_UNAVAILABLE: "证据暂不可读", TRACKING: "已关联锁定追踪",
  ARCHIVED: "归档证据可读（实时追踪已过保留期）", TRACKER_UNAVAILABLE: "追踪暂不可读",
  PENDING_LOCK: "影子锁待核实", LOCK_BLOCKED: "锁定受阻", LINK_UNVERIFIED: "锁定回执待核对", SHADOW_LOCK_REQUESTED: "已提交锁定申请",
  OBSERVATION: "锁定追踪采样同步", CONDITION_UNKNOWN: "网格检查：资料待补齐",
  CONDITION_TRIGGERED: "网格检查：已触发破坏条件", CONDITION_NOT_TRIGGERED: "网格检查：未触发破坏条件",
  REVIEW_REQUIRED: "ARD 变化需复核",
  AI_HOLD: "AI 续持 · HOLD",
  AI_REVOKE: "AI 建议撤销 · REVOKE",
  AI_HOLD_UNKNOWN: "AI 复核无法判断 · UNKNOWN",
  Accept: "已接受 · Accept",
  Reject: "已拒绝 · Reject",
  Unknown: "暂不判定 · Unknown",
  AI_REVIEW_REQUESTED: "已发起 AI 持仓复核",
  HOLD_REVIEW_FAILED: "持仓复核调用失败",
  HOLD_REVIEW_QUEUED: "已入复核去抖队列",
  HOLD_REVIEW_SKIPPED: "持仓复核已跳过",
  SIMULATED_ORDER: "影子模拟买回短腿",
  SHADOW_ACTION: "影子撤销记录",
  SHADOW_EXITED: "影子已退出（终态 · 已停止主动追踪）",
  active: "观察中", stopped: "已停止观察", expired: "已到期", error: "追踪异常", paused: "追踪暂停",
};
const REASON_STAGES = new Set([
  "DEAL_BLOCKED", "LOCK_BLOCKED", "LINK_UNVERIFIED", "PENDING_LOCK",
  "DATA_BLOCKED", "DELIVERY_UNKNOWN", "PROVIDER_REJECTED", "REQUEST_FAILED",
  "TIMEOUT", "PARSE_FAILED", "EVIDENCE_UNAVAILABLE",
]);
export function stageText(hand) {
  const state = hand?.stage || hand?.state;
  if (!state) return "状态未知";
  const label = LABELS[state] || state;
  return hand.stage_reason && REASON_STAGES.has(state)
    ? `${label} · ${hand.stage_reason}` : label;
}
export function lockProgressText(hand) {
  if (!hand || !hand.tracking_state) return null;
  const ts = hand.tracking_state;
  const base = LABELS[ts] || ts;
  if (ts === "TRACKING") {
    return hand.tracker_status ? `${base}（${LABELS[hand.tracker_status] || hand.tracker_status}）` : base;
  }
  if (ts === "ARCHIVED" || ts === "LINK_CONFIRMED") return base;
  const attempts = isNum(hand.pending_lock?.attempts_count) ? `${hand.pending_lock.attempts_count} 次` : null;
  const reason = hand.pending_lock?.last_failure_reason || hand.lock_reason || hand.stage_reason;
  const extra = [attempts ? `已尝试 ${attempts}` : null, reason].filter(Boolean).join(" · ");
  return extra ? `${base}（${extra}）` : base;
}
let epoch = 0, authorized = false, firstView = null, onUnauthorized = null;
export function setUnauthorizedHandler(fn) {
  onUnauthorized = typeof fn === "function" ? fn : null;
}
function clearBox(box) {
  if (!box) return;
  box.hidden = true;
  box.textContent = "";
  delete box.dataset.ready;
  delete box.dataset.state;
}
export function setSession(ok) {
  if (authorized === Boolean(ok)) return;
  authorized = Boolean(ok);
  epoch++;
  if (authorized) loadCurrent();
  else {
    firstView = null;
    clearBox(document.getElementById("current-hand"));
  }
}
export function initialView() {
  const first = firstView;
  firstView = null;
  return first;
}

let pendingSeq = 0, inFlightEpoch = 0;
export async function loadCurrent() {
  const box = document.getElementById("current-hand");
  if (!box || !authorized || inFlightEpoch === epoch) return;
  const seq = epoch;
  const myPending = ++pendingSeq;
  inFlightEpoch = seq;
  try {
    const data = await api.currentHand();
    if (!authorized || epoch !== seq) return;
    if (!data || typeof data !== "object" || data.code === "bad_json" || !("hand" in data)) {
      const badErr = new Error(data?.message || "bad_response");
      badErr.code = data?.code || "bad_response";
      throw badErr;
    }
    const { hand } = data;
    if (!hand) {
      clearBox(box);
      return;
    }
    const expiry = isNum(hand.expiry_ms) ? ` · ${new Date(hand.expiry_ms).toISOString().slice(0, 10)} 到期` : "";
    const updated = hand.last_update_ms;
    const isDecision = ["Accept", "Reject", "Unknown"].includes(hand.state);
    const sideText = hand.side ? ` / ${hand.side === "put" ? "Put" : hand.side === "call" ? "Call" : hand.side}` : "";
    const verdictPart = isDecision
      ? `${LABELS[hand.state]}${sideText}`
      : stageText(hand);
    const lockPart = isDecision && hand.tracking_state ? ` · 影子锁：${lockProgressText(hand)}` : "";
    delete box.dataset.state;
    box.hidden = false;
    box.textContent = `正式牌${expiry} · ${verdictPart}${lockPart}`
      + (isNum(updated) ? ` · 更新 ${new Date(updated).toISOString().slice(11, 19)} UTC（${ago(updated)}）` : "");
    box.dataset.ready = "true";
  } catch (err) {
    if (err?.status === 401 && epoch === seq) {
      setSession(false);
      onUnauthorized?.(err);
      return;
    }
    if (err?.status === 404 && err?.body?.code === "shadow_dossiers_disabled" && epoch === seq) {
      clearBox(box);
      return;
    }
    if (authorized && epoch === seq) {
      const detail = err?.code === "bad_json" || err?.code === "bad_response"
        ? "响应格式异常"
        : (err?.status ? `HTTP ${err.status}` : "读取超时或连接中断");
      box.textContent = `正式牌状态暂不可读 · ${detail}`;
      box.dataset.state = "error";
      box.hidden = false;
      delete box.dataset.ready;
    }
  } finally {
    if (pendingSeq === myPending) inFlightEpoch = 0;
  }
}

// The public entry can render the formal hand while the larger UI modules are downloading.
const gated = typeof location !== "undefined" && (location.protocol === "https:" || new URLSearchParams(location.search).has("gate"));
export const startupSession = gated && !new URLSearchParams(location.search).has("fixture")
  ? api.session().then(session => {
      setSession(session.authenticated);
      if (authorized) {
        firstView = api.view();
        firstView.catch(() => {}); // load() handles the original rejection when it starts
      }
      return session;
    }) : null;
startupSession?.catch(() => {}); // loadSession() owns session-error rendering
