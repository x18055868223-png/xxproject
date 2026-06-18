# -*- coding: utf-8 -*-
"""VRP 建仓前权利金风险补偿定价门（执行层 canonical 收口版，R3）。

把 VRP 封版 v1.1 的纯门逻辑落为执行层 src 模块；**4 个重复原语收口到执行层 canonical
单一真值源**（删 VRP 本地副本）：
  normalise_iv      -> hedge_risk._normalise_iv          （直接用同名，不别名，保证 bundle 内联后名字存在）
  _norm_cdf         -> hedge_risk._norm_cdf
  _option_fee       -> accounting.acct_option_fee_ccy    （费率 0.0003/0.125 即 canonical 常量）
  _spread_half_cost -> accounting.acct_spread_cost       （**保留 VRP 的 None/倒挂→0 安全语义**）
black_scholes_price_usd 是唯一保留的新能力。门判定与 v1.1 等价（tests/test_vrp_gate.py 等价性测试背书）。

边界：只过滤、不判方向、不选期、不进 PLAN_WEIGHTS、不解 ALLOW_TRADING；只跑 EDB 背书侧。
"""
from dataclasses import asdict, dataclass
from math import log, sqrt
from typing import Optional, Tuple

from accounting import acct_option_fee_ccy, acct_spread_cost
from hedge_risk import _normalise_iv, _norm_cdf

PASS = "PASS"
BLOCK = "BLOCK"
DISTORTED_REVIEW = "DISTORTED_REVIEW"
VRP_FACTOR_VERSION = "1.1.0"


@dataclass(frozen=True)
class ScenarioConfig:
    rv_weights: Tuple[float, float, float] = (0.45, 0.35, 0.20)
    low_percentile_threshold: float = 0.25
    high_percentile_threshold: float = 0.75
    low_percentile_multiplier: float = 1.25
    high_percentile_multiplier: float = 0.92
    cold_start_multiplier: float = 1.10
    min_history_days: int = 30
    term_backwardation_ratio: float = 1.18
    event_backwardation_ratio: float = 1.35
    min_window_vol_edge: float = 0.02
    min_candidate_edge_ccy: float = 0.0
    spread_round_trip_multiplier: float = 2.0
    annualization_days: int = 365


def selected_policy_config():
    """执行层采用的 VRP 策略（strict_cost_cold_guard_v1_1，268k 遍历选出，危险/冷启动通过=0）。
    费率不再由本 config 提供（收口到 accounting canonical 常量 0.0003/0.125）。"""
    return ScenarioConfig(
        low_percentile_multiplier=1.25,
        high_percentile_multiplier=0.92,
        cold_start_multiplier=1.35,
        term_backwardation_ratio=1.18,
        min_candidate_edge_ccy=0.00005,
        min_window_vol_edge=0.02,
        spread_round_trip_multiplier=3.0,
    )


@dataclass(frozen=True)
class WindowInput:
    window_id: str
    expiry: str
    dte_hours: float
    side: str
    front_anchor_iv: float
    atm_front_iv: Optional[float]
    term_reference_iv_5_10d: Optional[float]
    rv_24h: float
    rv_72h: float
    rv_7d: float
    rv_percentile: Optional[float]
    history_days: int


@dataclass(frozen=True)
class CandidateQuote:
    window_id: str
    side: str
    spot: float
    short_strike: float
    protection_strike: float
    dte_hours: float
    amount: float
    short_bid: float
    short_ask: float
    protection_bid: float
    protection_ask: float
    executable_short_iv: float
    executable_protection_iv: Optional[float]
    forward_vol_hurdle: float
    short_instrument: str = ""
    protection_instrument: str = ""
    short_delta: Optional[float] = None


def _weighted_average(values, weights):
    total_weight = 0.0
    total = 0.0
    for value, weight in zip(values, weights):
        if value is None:
            continue
        total += value * weight
        total_weight += weight
    if total_weight <= 0:
        return None
    return total / total_weight


# ---------- canonical 收口原语 ----------
def _option_fee(price_ccy, amount):
    """收口 accounting.acct_option_fee_ccy（单一费率真值源）；保留 VRP max(price,0) 守护。"""
    return acct_option_fee_ccy(max(price_ccy, 0.0), amount)


def _spread_half_cost(bid, ask, amount):
    """收口 accounting.acct_spread_cost 核心算式，但保留 VRP 的 None/倒挂(ask<bid)→0 安全语义
    （canonical 对缺失返回 None、不挡倒挂，裸用会使 full_round_trip_friction 求和遇 None 崩溃）。"""
    if bid is None or ask is None or ask < bid:
        return 0.0
    return acct_spread_cost(bid, ask, amount)


# ---------- hurdle / 窗口门 ----------
def forward_vol_hurdle(rv_24h, rv_72h, rv_7d, rv_percentile, history_days, config):
    rvs = [_normalise_iv(rv_24h), _normalise_iv(rv_72h), _normalise_iv(rv_7d)]
    rv_regime_anchor = _weighted_average(rvs, config.rv_weights)
    if rv_regime_anchor is None:
        return None, {"rv_regime_anchor": None, "percentile_adjustment": None,
                      "cold_start_multiplier": None, "reason_codes": ["RV_REGIME_ANCHOR_MISSING"]}
    reason_codes = []
    percentile_adjustment = 1.0
    if rv_percentile is None:
        reason_codes.append("RV_PERCENTILE_MISSING")
    elif rv_percentile <= config.low_percentile_threshold:
        percentile_adjustment = config.low_percentile_multiplier
        reason_codes.append("RV_LOW_PERCENTILE_HURDLE_UP")
    elif rv_percentile >= config.high_percentile_threshold:
        percentile_adjustment = config.high_percentile_multiplier
        reason_codes.append("RV_HIGH_PERCENTILE_HURDLE_RELAXED")
    cold_start_multiplier = 1.0
    if history_days < config.min_history_days:
        cold_start_multiplier = config.cold_start_multiplier
        reason_codes.append("COLD_START_HURDLE_UP")
    hurdle = rv_regime_anchor * percentile_adjustment * cold_start_multiplier
    return hurdle, {"rv_regime_anchor": rv_regime_anchor,
                    "percentile_adjustment": percentile_adjustment,
                    "cold_start_multiplier": cold_start_multiplier,
                    "forward_vol_hurdle": hurdle, "reason_codes": reason_codes}


def assess_window(window, config):
    """廉价 vol-space 预筛：front 锚 IV vs forward_vol_hurdle + 期限结构/数据质量路由。"""
    reason_codes = []
    front_iv = _normalise_iv(window.front_anchor_iv)
    atm_front_iv = _normalise_iv(window.atm_front_iv)
    term_iv = _normalise_iv(window.term_reference_iv_5_10d)
    hurdle, hurdle_meta = forward_vol_hurdle(
        window.rv_24h, window.rv_72h, window.rv_7d,
        window.rv_percentile, window.history_days, config)
    reason_codes.extend(hurdle_meta.get("reason_codes") or [])
    gate = PASS
    front_to_term_state = "NORMAL"
    if front_iv is None or hurdle is None:
        gate = BLOCK
        reason_codes.append("WINDOW_DATA_MISSING")
    if term_iv and front_iv:
        ratio = front_iv / term_iv
        if ratio >= config.event_backwardation_ratio:
            front_to_term_state = "EVENT_DISTORTED"
            gate = DISTORTED_REVIEW
            reason_codes.append("FRONT_TERM_EVENT_DISTORTED")
        elif ratio >= config.term_backwardation_ratio:
            front_to_term_state = "STRESSED_BACKWARDATION"
            gate = DISTORTED_REVIEW
            reason_codes.append("FRONT_TERM_BACKWARDATION")
        elif ratio >= 0.96:
            front_to_term_state = "FLAT"
    representative_vol_edge = None
    if front_iv is not None and hurdle is not None:
        representative_vol_edge = front_iv - hurdle
        if gate == PASS and representative_vol_edge < config.min_window_vol_edge:
            gate = BLOCK
            reason_codes.append("WINDOW_VRP_EDGE_TOO_THIN")
    return {"window_id": window.window_id, "expiry": window.expiry, "dte_hours": window.dte_hours,
            "side": window.side, "main_anchor_delta": 0.30, "front_anchor_iv": front_iv,
            "atm_front_iv": atm_front_iv, "term_reference_iv_5_10d": term_iv,
            "front_to_term_state": front_to_term_state,
            "rv_regime_anchor": hurdle_meta.get("rv_regime_anchor"),
            "rv_lookback_n_days": window.history_days, "rv_percentile": window.rv_percentile,
            "percentile_adjustment": hurdle_meta.get("percentile_adjustment"),
            "cold_start_multiplier": hurdle_meta.get("cold_start_multiplier"),
            "forward_vol_hurdle": hurdle, "representative_vol_edge": representative_vol_edge,
            "window_vrp_gate": gate, "reason_codes": sorted(set(reason_codes))}


# ---------- BS pricer（唯一保留的新能力）----------
def black_scholes_price_usd(option_type, spot, strike, dte_hours, sigma, annualization_days=365):
    if spot <= 0 or strike <= 0 or dte_hours <= 0 or sigma <= 0:
        return 0.0
    t = max(dte_hours / (24.0 * annualization_days), 1e-9)
    vol_sqrt_t = sigma * sqrt(t)
    if vol_sqrt_t <= 0:
        return 0.0
    d1 = (log(spot / strike) + 0.5 * sigma * sigma * t) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    if option_type == "call":
        return max(0.0, spot * _norm_cdf(d1) - strike * _norm_cdf(d2))
    return max(0.0, strike * _norm_cdf(-d2) - spot * _norm_cdf(-d1))


def _option_type_for_side(side):
    if side == "SHORT_CALL":
        return "call"
    if side == "SHORT_PUT":
        return "put"
    raise ValueError("Unsupported side: %s" % side)


# ---------- 候选门（权威 ccy full-burn）----------
def assess_candidate(candidate, config):
    reason_codes = []
    option_type = _option_type_for_side(candidate.side)
    hurdle = _normalise_iv(candidate.forward_vol_hurdle)
    short_iv = _normalise_iv(candidate.executable_short_iv)
    protection_iv = _normalise_iv(candidate.executable_protection_iv)
    if protection_iv is None:
        protection_iv = hurdle
    executable_net_credit = (candidate.short_bid - candidate.protection_ask) * candidate.amount
    short_hurdle = black_scholes_price_usd(
        option_type, candidate.spot, candidate.short_strike, candidate.dte_hours,
        hurdle or 0.0, config.annualization_days) / candidate.spot
    protection_hurdle = black_scholes_price_usd(
        option_type, candidate.spot, candidate.protection_strike, candidate.dte_hours,
        hurdle or 0.0, config.annualization_days) / candidate.spot
    hurdle_net_credit = (short_hurdle - protection_hurdle) * candidate.amount
    entry_exit_fees = (2.0 * _option_fee(candidate.short_bid, candidate.amount)
                       + 2.0 * _option_fee(candidate.protection_ask, candidate.amount))
    spread_reserve = config.spread_round_trip_multiplier * (
        _spread_half_cost(candidate.short_bid, candidate.short_ask, candidate.amount)
        + _spread_half_cost(candidate.protection_bid, candidate.protection_ask, candidate.amount))
    full_round_trip_friction = entry_exit_fees + spread_reserve
    candidate_edge = executable_net_credit - hurdle_net_credit - full_round_trip_friction
    gate = PASS
    if short_iv is None or hurdle is None:
        gate = BLOCK
        reason_codes.append("CANDIDATE_IV_OR_HURDLE_MISSING")
    if candidate_edge <= config.min_candidate_edge_ccy:
        gate = BLOCK
        reason_codes.append("CANDIDATE_FULL_BURN_EDGE_TOO_THIN")
    return {"window_id": candidate.window_id,
            "instrument_pair": {"short": candidate.short_instrument,
                                "protection": candidate.protection_instrument},
            "side": candidate.side, "short_delta": candidate.short_delta,
            "short_dte_hours": candidate.dte_hours,
            "width": abs(candidate.protection_strike - candidate.short_strike),
            "executable_short_iv": short_iv, "executable_protection_iv": protection_iv,
            "forward_vol_hurdle": hurdle,
            "vertical_net_credit_at_executable_quotes": executable_net_credit,
            "vertical_net_credit_at_forward_vol_hurdle": hurdle_net_credit,
            "full_round_trip_friction": full_round_trip_friction,
            "candidate_vrp_edge_ccy": candidate_edge, "candidate_vrp_gate": gate,
            "vrp_residual_score": max(0.0, candidate_edge),
            "reason_codes": sorted(set(reason_codes)), "raw_candidate": asdict(candidate)}


# ---------- PRICE_GATE 适配：真实菜单方案 + 市场上下文 -> 双门裁决 ----------
def gate_plan(plan, market_context, config=None):
    """对一个真实选腿方案过 VRP 双门（窗口门→候选门）。market_context 提供 IV/RV：
    side / front_anchor_iv / atm_front_iv / term_reference_iv_5_10d / rv_24h/72h/7d /
    rv_percentile / history_days / executable_short_iv / executable_protection_iv。
    返回 VrpGatePackage（只过滤；不判方向/不选期/不进权重）。"""
    config = config or selected_policy_config()
    side = market_context["side"]
    expiry_hours = float(plan.get("short_dte_hours") or market_context.get("dte_hours") or 24.0)
    window_id = "%s-%dh" % (side, int(expiry_hours))
    window = WindowInput(
        window_id=window_id, expiry="%dh" % int(expiry_hours), dte_hours=expiry_hours, side=side,
        front_anchor_iv=market_context["front_anchor_iv"],
        atm_front_iv=market_context.get("atm_front_iv"),
        term_reference_iv_5_10d=market_context.get("term_reference_iv_5_10d"),
        rv_24h=market_context["rv_24h"], rv_72h=market_context["rv_72h"],
        rv_7d=market_context["rv_7d"], rv_percentile=market_context.get("rv_percentile"),
        history_days=int(market_context.get("history_days", 0)))
    wa = assess_window(window, config)
    cand = CandidateQuote(
        window_id=window_id, side=side, spot=plan["spot"],
        short_strike=plan["short_strike"], protection_strike=plan["protection_strike"],
        dte_hours=expiry_hours, amount=plan["amount"],
        short_bid=plan["short_bid"], short_ask=plan["short_ask"],
        protection_bid=plan["protection_bid"], protection_ask=plan["protection_ask"],
        executable_short_iv=market_context["executable_short_iv"],
        executable_protection_iv=market_context.get("executable_protection_iv"),
        forward_vol_hurdle=wa["forward_vol_hurdle"] or 0.0,
        short_instrument=plan.get("short_instrument", ""),
        protection_instrument=plan.get("protection_instrument", ""),
        short_delta=plan.get("short_delta"))
    ca = assess_candidate(cand, config)
    passed = (wa["window_vrp_gate"] == PASS and ca["candidate_vrp_gate"] == PASS)
    return {"schema_name": "VrpGatePackage", "schema_version": "nrd.integration.vrp_gate.v0.5",
            "factor_version": VRP_FACTOR_VERSION, "window": wa, "candidate": ca,
            "pass": bool(passed),
            "reason_codes": sorted(set((wa.get("reason_codes") or [])
                                       + (ca.get("reason_codes") or [])))}


def apply_vrp_gate(menu, market_context, config=None):
    """对真实菜单逐方案过 VRP 双门，返回 (passed[(plan,gate)], blocked[(plan,gate)])。
    PRICE_GATE：BLOCK 的候选不进可锁定方案；VRP 不进 PLAN_WEIGHTS。"""
    passed, blocked = [], []
    for plan in menu:
        gate = gate_plan(plan, market_context, config)
        (passed if gate["pass"] else blocked).append((plan, gate))
    return passed, blocked
