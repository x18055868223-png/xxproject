# -*- coding: utf-8 -*-
"""建仓可行性评分（execution_feasibility，纯逻辑）。

回答：当前盘口快照下，该垂直结构是否具备**完整建立所需的经济空间与报价条件**。
**不是 fill_probability**（无历史成交标签）；不读 `_G`、不下单、不改计划/人工审计。
口径与 entry campaign 一致：复用 `execution.exec_buy_price/exec_sell_price` 价格阶梯 +
`position.entry_credit_capped_index/entry_net_credit` 信用底线档；费率收口 accounting。

设计：硬门(Q1-Q6) + 软分(0~100，4 组件加权) + 排序折损(penalty)。阈值由 cfg 注入（集中在 config）。
不变量见规范 §16（EF-01..10）。
"""
from execution import exec_buy_price, exec_sell_price
from position import entry_credit_capped_index, entry_net_credit
from config import PROTECTION_LOW_PREMIUM_MAX, PROTECTION_ABS_SPREAD_MAX

SCHEMA_NAME = "ExecutionFeasibilityPackage"
SCHEMA_VERSION = "nrd.execution.feasibility.v1"

GRADE_HIGHLY = "HIGHLY_BUILDABLE"
GRADE_BUILDABLE = "BUILDABLE"
GRADE_PATIENT = "PATIENT_ONLY"
GRADE_FRAGILE = "FRAGILE"
GRADE_REJECT = "REJECT"

# 默认阈值（仅作 cfg 缺省；生产由 config.FEAS_* 注入）
DEFAULT_CFG = {
    "max_short_spread": 0.60, "max_protection_spread": 0.60,
    "protection_low_premium_max": PROTECTION_LOW_PREMIUM_MAX,
    "protection_abs_spread_max": PROTECTION_ABS_SPREAD_MAX,
    "min_net_credit": 0.0, "min_retention": 0.45, "min_survival_ticks": 0,
    "retention_bad": 0.45, "retention_good": 0.90,
    "spread_bad": 0.60, "spread_good": 0.10,
    "friction_bad": 0.60, "friction_good": 0.10,
    "weights": {"credit_retention": 0.30, "spread": 0.25, "friction": 0.25, "credit_survival": 0.20},
}


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _clamp01(v):
    return max(0.0, min(1.0, v))


def safe_spread_ratio(bid, ask):
    """相对价差 (ask-bid)/mid；缺数据/非法(<=0 或 ask<bid) → None。"""
    if not (_is_num(bid) and _is_num(ask)) or bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2.0
    return (ask - bid) / mid if mid > 0 else None


def _quote_abs_spread(q):
    bid = (q or {}).get("best_bid")
    ask = (q or {}).get("best_ask")
    if not (_is_num(bid) and _is_num(ask)) or ask < bid:
        return None
    return ask - bid


def _protection_spread_soft_ok(q, ratio, cfg):
    if ratio is not None and ratio <= cfg["max_protection_spread"]:
        return True
    abs_spread = _quote_abs_spread(q)
    ask = (q or {}).get("best_ask")
    return (_is_num(abs_spread) and _is_num(ask)
            and ask <= cfg["protection_low_premium_max"]
            and abs_spread <= cfg["protection_abs_spread_max"] + 1e-12)


def mark_credit_after_fees(short_mark, prot_mark, amount, fees):
    """mark 口径净 credit = (短腿 mark − 保护腿 mark)×数量 − 入场费。"""
    return (short_mark - prot_mark) * amount - (fees or 0.0)


def executable_credit_after_fees(short_bid, prot_ask, amount, fees):
    """可成交保守口径净 credit = (短腿 bid − 保护腿 ask)×数量 − 入场费（建仓更真实）。"""
    return (short_bid - prot_ask) * amount - (fees or 0.0)


def credit_retention_ratio(exec_credit, mark_credit):
    """可成交 credit / mark credit；mark<=0 → None（由硬门拒绝）。"""
    if not _is_num(mark_credit) or mark_credit <= 0:
        return None
    return exec_credit / mark_credit


def entry_friction_estimate(short_mark, short_bid, prot_ask, prot_mark, amount, fees):
    """保守入场摩擦 = [max(0,短腿 mark−bid) + max(0,保护腿 ask−mark)]×数量 + 入场费。
    任一价格偏离为负（异常 mark）按 0 处理，避免负摩擦奖励（EF-06）。"""
    short_slip = max(0.0, (short_mark - short_bid))
    prot_slip = max(0.0, (prot_ask - prot_mark))
    return (short_slip + prot_slip) * amount + (fees or 0.0)


def friction_to_credit_ratio(friction, exec_credit):
    """摩擦 / 可成交 credit；exec_credit<=0 → None（由硬门拒绝）。"""
    if not _is_num(exec_credit) or exec_credit <= 0:
        return None
    return friction / exec_credit


def credit_survival_profile(short_quote, prot_quote, amount, credit_floor, max_tick_steps, fees):
    """追价阶梯上仍满足 credit floor 的最大档（复用 entry campaign 同一阶梯与信用底线档）。
    返回 {credit_survival_ticks, max_tick_steps, credit_survival_ratio, credit_at_last_surviving_step}。
    n_survive=-1 表示第 0 档即低于底线。"""
    steps = max(0, int(max_tick_steps or 0))
    prot_buy = [exec_buy_price(prot_quote["mark"], prot_quote["best_ask"], prot_quote["tick"], n)
                for n in range(steps + 1)]
    short_sell = [exec_sell_price(short_quote["mark"], short_quote["best_bid"], short_quote["tick"], n)
                  for n in range(steps + 1)]
    i_cap = entry_credit_capped_index(prot_buy, short_sell, amount, fees, credit_floor)
    ratio = (i_cap + 1) / float(steps + 1) if i_cap >= 0 else 0.0
    last_credit = (entry_net_credit(short_sell[i_cap], prot_buy[i_cap], amount, fees)
                   if i_cap >= 0 else None)
    return {"credit_survival_ticks": i_cap, "max_tick_steps": steps,
            "credit_survival_ratio": ratio, "credit_at_last_surviving_step": last_credit}


def component_score_linear(value, bad, good):
    """线性归一到 0~100。`(value-bad)/(good-bad)` 自动适配方向：
    good>bad → 越大越好（如 retention）；good<bad → 越小越好（如 spread/friction）。value None → 0。"""
    if not _is_num(value) or bad == good:
        return 0.0
    return _clamp01((value - bad) / (good - bad)) * 100.0


def _grade(score):
    if score >= 85:
        return GRADE_HIGHLY
    if score >= 70:
        return GRADE_BUILDABLE
    if score >= 55:
        return GRADE_PATIENT
    return GRADE_FRAGILE


def _leg_quote_ok(q):
    return bool(q) and all(_is_num(q.get(k)) for k in ("mark", "best_bid", "best_ask", "tick")) \
        and q["best_bid"] > 0 and q["best_ask"] > 0 and q["best_ask"] >= q["best_bid"] and q["tick"] > 0


def _reject(failures, warnings=None):
    return {"schema_name": SCHEMA_NAME, "schema_version": SCHEMA_VERSION, "status": "REJECT",
            "grade": GRADE_REJECT, "score": 0.0, "score_norm": 0.0,
            "hard_gate_passed": False, "hard_failures": list(failures), "warnings": list(warnings or []),
            "economics": {}, "liquidity": {}, "campaign": {}, "components": {},
            "reason_codes": ["EXEC_FEASIBILITY_REJECT"]}


def evaluate_execution_feasibility(inp, cfg=None):
    """输入 {short_quote, protection_quote, amount, credit_floor, max_tick_steps, fee_estimate, now_ms}
    → ExecutionFeasibilityPackage（硬门 + 软分 + 等级）。缺关键报价/越价 → fail-closed(REJECT)。"""
    c = dict(DEFAULT_CFG)
    c.update(cfg or {})
    sq, pq = inp.get("short_quote"), inp.get("protection_quote")
    amount = inp.get("amount") or 0.0
    fees = inp.get("fee_estimate") or 0.0
    credit_floor = inp.get("credit_floor", 0.0)
    max_steps = inp.get("max_tick_steps", 0)

    # Q1/Q2：双腿报价完整 + 盘口合法（缺保护腿 ask 等 → 硬拒，EF-01/EF-07）
    fail = []
    warnings = []
    if not _leg_quote_ok(sq):
        fail.append("SHORT_QUOTE_INCOMPLETE")
    if not _leg_quote_ok(pq):
        fail.append("PROTECTION_QUOTE_INCOMPLETE")
    if fail:
        return _reject(fail)

    # Q3：双腿 spread 不超绝对上限（保护腿同等评估）
    ss = safe_spread_ratio(sq["best_bid"], sq["best_ask"])
    ps = safe_spread_ratio(pq["best_bid"], pq["best_ask"])
    worst = max(ss, ps)
    if ss > c["max_short_spread"]:
        fail.append("SHORT_SPREAD_TOO_WIDE")
    protection_soft = _protection_spread_soft_ok(pq, ps, c)
    if ps > c["max_protection_spread"] and not protection_soft:
        fail.append("PROTECTION_SPREAD_TOO_WIDE")
    elif ps > c["max_protection_spread"]:
        warnings.append("PROTECTION_SPREAD_SOFT_LOW_PREMIUM")

    # Q4/Q5：可成交 credit 为正且达底线；mark credit>0；保留率达标（EF-02）
    mark_credit = mark_credit_after_fees(sq["mark"], pq["mark"], amount, fees)
    exec_credit = executable_credit_after_fees(sq["best_bid"], pq["best_ask"], amount, fees)
    if mark_credit <= 0:
        fail.append("MARK_CREDIT_NON_POSITIVE")
    if exec_credit <= 0:
        fail.append("EXECUTABLE_CREDIT_NON_POSITIVE")
    elif exec_credit < c["min_net_credit"]:
        fail.append("EXECUTABLE_CREDIT_BELOW_FLOOR")
    retention = credit_retention_ratio(exec_credit, mark_credit)
    if retention is not None and retention < c["min_retention"]:
        fail.append("CREDIT_RETENTION_TOO_LOW")

    # Q6：追价后至少有最低可用空间
    surv = credit_survival_profile(sq, pq, amount, credit_floor, max_steps, fees)
    if surv["credit_survival_ticks"] < c["min_survival_ticks"]:
        fail.append("CREDIT_SURVIVAL_INSUFFICIENT")

    if fail:
        return _reject(fail, warnings)

    # 软分（depth 缺省 → None，权重在余下组件间重归一化，EF-08）
    cr_score = component_score_linear(retention, c["retention_bad"], c["retention_good"])
    sp_score = component_score_linear(worst, c["spread_bad"], c["spread_good"])
    friction = entry_friction_estimate(sq["mark"], sq["best_bid"], pq["best_ask"], pq["mark"], amount, fees)
    fr_ratio = friction_to_credit_ratio(friction, exec_credit)
    fr_score = component_score_linear(fr_ratio, c["friction_bad"], c["friction_good"])
    surv_score = surv["credit_survival_ratio"] * 100.0
    comps = {"credit_retention": cr_score, "spread": sp_score,
             "friction": fr_score, "credit_survival": surv_score, "depth": None}
    w = c["weights"]
    num = sum(w[k] * comps[k] for k in comps if comps[k] is not None and k in w)
    den = sum(w[k] for k in comps if comps[k] is not None and k in w)
    score = (num / den) if den > 0 else 0.0

    reason_codes = ["DUAL_LEG_QUOTES_OK", "EXECUTABLE_CREDIT_POSITIVE",
                    "CREDIT_SURVIVES_%d_TICKS" % (surv["credit_survival_ticks"] + 1)]
    return {
        "schema_name": SCHEMA_NAME, "schema_version": SCHEMA_VERSION, "status": "PASS",
        "grade": _grade(score), "score": round(score, 2), "score_norm": round(score / 100.0, 4),
        "hard_gate_passed": True, "hard_failures": [], "warnings": warnings,
        "economics": {"mark_credit_after_fees": mark_credit,
                      "executable_credit_after_fees": exec_credit,
                      "credit_retention_ratio": retention,
                      "entry_friction_estimate": friction,
                      "friction_to_credit_ratio": fr_ratio},
        "liquidity": {"short_spread_ratio": ss, "protection_spread_ratio": ps,
                      "protection_abs_spread": _quote_abs_spread(pq),
                      "protection_low_premium_soft": bool(ps > c["max_protection_spread"]
                                                          and protection_soft),
                      "worst_leg_spread_ratio": worst,
                      "depth_coverage_ratio": None, "depth_state": "NOT_EVALUATED"},
        "campaign": surv,
        "components": {"credit_retention_score": cr_score, "spread_score": sp_score,
                       "friction_score": fr_score, "credit_survival_score": surv_score,
                       "depth_score": None},
        "reason_codes": reason_codes,
    }


def feasibility_penalty(score_norm, floor=0.50):
    """排序折损：penalty = floor + (1-floor)×score_norm（满分不折损；可行性 0 最多保留 floor）。"""
    sn = _clamp01(score_norm if _is_num(score_norm) else 0.0)
    return floor + (1.0 - floor) * sn
