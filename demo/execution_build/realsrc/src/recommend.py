# -*- coding: utf-8 -*-
"""垂直推荐库编码 + 冻结审批快照 + 短确认码（rec_*）。纯函数，便于单测。

补充意见 P0-2 的核心修复：
  - `quality_code` 由**冻结的质量口径**（signal 包 / VRP / S:PM 释放分桶 / 预算决定）哈希得到，
    **不含实时行情逐 tick 值**，且 S:PM 释放按 0.1 分桶——子桶波动不改码，用户输入期间确认码稳定；
  - `confirm_code` 标识冻结审批快照(session+strategy+quality+plan_hash) 的短码（Base32 前 4，库内冲突自动延长）；
  - 质量发生**材料级**变化（跨桶 / 换信号包 / 结构变）→ quality_code 变 → plan_hash 变 → confirm_code 变 →
    旧码在新库 `resolve_confirm_code` 失败（自动过期），而非依赖实时哈希全等。

`refresh_seq` **不**进入 quality_code（避免每轮刷新改码）；它单独存于审批快照，供 cmd_router 幂等键使用。
"""
import base64
import hashlib

QUALIFIED = "QUALIFIED"
RELIEF_BUCKET = 10          # S:PM 释放分桶粒度：int(ratio*10)，0.1 一桶（漂移容差）


def _h(*parts):
    s = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _b32(hexstr, length):
    raw = base64.b32encode(bytes.fromhex(hexstr)).decode("ascii").rstrip("=")
    return raw[:length]


def _relief_bucket(ratio):
    if not isinstance(ratio, (int, float)):
        return "NA"
    return int(ratio * RELIEF_BUCKET)


def side_of(short_instrument):
    s = str(short_instrument or "")
    if s.endswith("-C"):
        return "CALL"
    if s.endswith("-P"):
        return "PUT"
    return "UNK"


def strategy_code(side, expiry_label, short_strike, long_strike):
    """稳定结构身份：VCS|<side>|<expiry>|<short_strike>|<long_strike>。"""
    return "VCS|%s|%s|%s|%s" % (side, expiry_label, short_strike, long_strike)


def quality_code(signal_package_id, relief_ratio, vrp_state, budget_decision):
    """冻结质量短码（前 8）。仅由材料级质量口径决定，S:PM 释放分桶 → 漂移容差。"""
    return _h(signal_package_id, _relief_bucket(relief_ratio),
              vrp_state, budget_decision)[:8]


def plan_hash(strategy_code_str, quality_code_str, side,
              short_instrument, long_instrument, amount):
    return _h(strategy_code_str, quality_code_str, side,
              short_instrument, long_instrument, amount)[:16]


def confirm_code(session_id, strategy_code_str, quality_code_str, plan_hash_str, length=4):
    """短确认码：标识冻结审批快照，稳定不随行情逐轮翻动。Base32 前 length 位。"""
    return _b32(_h(session_id, strategy_code_str, quality_code_str, plan_hash_str), length)


def build_approval_snapshot(candidate, session_id, signal_package_id, refresh_seq, now_ts):
    """把一个 plans.plan_assemble 菜单项冻结为可批准的审批快照。"""
    short_inst = candidate.get("short_instrument") or ""
    long_inst = candidate.get("protection_instrument") or ""
    side = side_of(short_inst)
    vrp_state = candidate.get("vrp_state") or candidate.get("vrp_gate")
    budget_decision = candidate.get("budget_decision")
    sc = strategy_code(side, candidate.get("short_expiry_label"),
                       candidate.get("short_strike"), candidate.get("protection_strike"))
    qc = quality_code(signal_package_id, candidate.get("margin_relief_ratio"),
                      vrp_state, budget_decision)
    ph = plan_hash(sc, qc, side, short_inst, long_inst, candidate.get("amount"))
    cc = confirm_code(session_id, sc, qc, ph)
    return {
        "schema_name": "VerticalApprovalSnapshot",
        "session_id": session_id, "signal_package_id": signal_package_id,
        "refresh_seq": refresh_seq, "plan_id": candidate.get("id"),
        "side": side, "strategy_code": sc, "quality_code": qc,
        "plan_hash": ph, "confirm_code": cc,
        "recommendation_state": QUALIFIED if candidate.get("qualified", True) else "REJECTED",
        "short_instrument": short_inst, "long_instrument": long_inst,
        "short_strike": candidate.get("short_strike"),
        "long_strike": candidate.get("protection_strike"),
        "short_expiry": candidate.get("short_expiry"),       # 锁定→入场快照→持仓后 DTE
        "breakeven": candidate.get("breakeven"),             # 入场风险锚 loss_boundary
        "amount": candidate.get("amount"),
        "entry_net_credit_after_costs": candidate.get("net_credit_effective"),
        "max_loss": candidate.get("max_loss"),
        "margin_relief_ratio": candidate.get("margin_relief_ratio"),
        "frozen_ts": now_ts,
        "summary": "%s Δ%s 宽%s" % (side, candidate.get("short_delta"), candidate.get("width")),
    }


def ensure_unique_confirm_codes(snaps, session_id, max_len=8):
    """库内若有确认码冲突，对全部项统一延长位数直至唯一（封顶 max_len）。"""
    length = 4
    while length <= max_len:
        seen = {}
        for s in snaps:
            cc = confirm_code(session_id, s["strategy_code"], s["quality_code"],
                              s["plan_hash"], length)
            seen.setdefault(cc, []).append(s)
        if all(len(v) == 1 for v in seen.values()):
            break
        length += 1
    length = min(length, max_len)
    for s in snaps:
        s["confirm_code"] = confirm_code(session_id, s["strategy_code"],
                                         s["quality_code"], s["plan_hash"], length)
    return snaps


def build_recommendation_library(menu, session_id, signal_package_id, refresh_seq, now_ts):
    """从垂直菜单构建推荐库（每项冻结为审批快照，确认码库内唯一）。"""
    snaps = [build_approval_snapshot(c, session_id, signal_package_id, refresh_seq, now_ts)
             for c in (menu or [])]
    ensure_unique_confirm_codes(snaps, session_id)
    return {
        "schema_name": "VerticalRecommendationLibrary",
        "session_id": session_id, "signal_package_id": signal_package_id,
        "refresh_seq": refresh_seq, "generated_ts": now_ts,
        "recommendations": snaps,
    }


def resolve_confirm_code(library, code):
    """在**当前**库中按确认码定位审批快照；找不到（含质量漂移致码变）→ None（旧码自动过期）。"""
    code = str(code or "").strip().upper()
    if not code:
        return None
    for s in (library or {}).get("recommendations", []):
        if str(s.get("confirm_code", "")).upper() == code:
            if s.get("recommendation_state") == QUALIFIED:
                return s
    return None


def precommit_recheck(locked_snapshot, current_library, live_checks):
    """预提交复核（补充意见 P0-2：不要求实时质量哈希全等，按漂移容差）。
    通过条件：①锁定快照的 strategy_code 仍在当前库且仍 QUALIFIED；②plan_hash 一致
    （quality_code 已分桶 → 子桶漂移不改 plan_hash，跨桶/换包/结构变才改）；③live_checks 全部为真。"""
    reasons = []
    match = next((s for s in (current_library or {}).get("recommendations", [])
                  if s.get("strategy_code") == locked_snapshot.get("strategy_code")
                  and s.get("recommendation_state") == QUALIFIED), None)
    if not match:
        reasons.append("STRATEGY_NO_LONGER_QUALIFIED_IN_LIBRARY")
    elif match.get("plan_hash") != locked_snapshot.get("plan_hash"):
        reasons.append("PLAN_HASH_DRIFTED_BEYOND_TOLERANCE")
    for k, ok in (live_checks or {}).items():
        if not ok:
            reasons.append("LIVE_CHECK_FAILED:" + str(k))
    return {"passed": not reasons, "reasons": reasons}


# ---------- 预提交 13 项硬门评估器（设计稿 §8.1；fail-closed：缺数据即视为不通过）----------

PRECOMMIT_CHECKS = (
    "signal_fresh", "same_signal_package", "locked_plan_hash_match",
    "locked_quality_code_match", "vertical_only", "vrp_rechecked",
    "spm_rechecked", "quotes_rechecked", "entry_net_credit_after_costs_positive",
    "projected_budget_passed", "ledger_reconciled", "no_unknown_orders", "spread_ok",
)


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def evaluate_precommit_checks(locked, current_library, live):
    """对锁定方案做 13 项预提交复核。`live` 为调用方预取的实时复核数据：
      signal_fresh(bool), sig_package_id, same_expiry(bool), vrp_pass(bool|None),
      spm_relief(float), min_relief(float), quotes_fresh(bool), net_credit_after_costs(float),
      projected_budget_decision('ALLOW'|...), ledger_reconciled(bool),
      no_unknown_orders(bool), spread_ok(bool)
    返回 {checks:{name:bool}, passed:bool, failed:[names]}；任一缺数据 → 该项 False（fail-closed）。"""
    locked = locked or {}
    live = live or {}
    match = next((s for s in (current_library or {}).get("recommendations", [])
                  if s.get("strategy_code") == locked.get("strategy_code")
                  and s.get("recommendation_state") == QUALIFIED), None)
    c = {
        "signal_fresh": bool(live.get("signal_fresh")),
        "same_signal_package": (live.get("sig_package_id") is not None
                                and locked.get("signal_package_id") == live.get("sig_package_id")),
        "locked_plan_hash_match": bool(match) and match.get("plan_hash") == locked.get("plan_hash"),
        "locked_quality_code_match": bool(match) and match.get("quality_code") == locked.get("quality_code"),
        "vertical_only": locked.get("side") in ("CALL", "PUT") and bool(live.get("same_expiry")),
        "vrp_rechecked": live.get("vrp_pass") is True,
        "spm_rechecked": (_is_num(live.get("spm_relief")) and _is_num(live.get("min_relief"))
                          and live["spm_relief"] >= live["min_relief"]),
        "quotes_rechecked": bool(live.get("quotes_fresh")),
        "entry_net_credit_after_costs_positive": (_is_num(live.get("net_credit_after_costs"))
                                                  and live["net_credit_after_costs"] > 0),
        "projected_budget_passed": live.get("projected_budget_decision") == "ALLOW",
        "ledger_reconciled": bool(live.get("ledger_reconciled")),
        "no_unknown_orders": bool(live.get("no_unknown_orders")),
        "spread_ok": bool(live.get("spread_ok")),
    }
    failed = [k for k in PRECOMMIT_CHECKS if not c.get(k)]
    return {"checks": c, "passed": not failed, "failed": failed}
