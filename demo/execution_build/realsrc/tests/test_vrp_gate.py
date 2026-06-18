# -*- coding: utf-8 -*-
"""R3：VRP canonical 收口版等价性 + PRICE_GATE 集成。

核心 robustness 证据：新 `vrp_gate`（4 原语收口执行层 canonical）与 VRP 封版快照原版
在同一输入上**门决策/hurdle/edge/friction 完全一致** → 收口零行为漂移。
含倒挂价差场景（验证 _spread_half_cost 收口保留 None/倒挂→0 安全语义、不崩溃）。
"""
import os, sys, time, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))          # vrp_gate/accounting/hedge_risk/strategy
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))           # vrp_adapter(execution_build)

import vrp_gate as VG
import vrp_adapter   # 复用其 snapshot 载入器，拿 VRP 封版原版做对照

_SNAP_MODEL, _SNAP_POLICY = vrp_adapter._load_vrp_modules()


def _approx(a, b, eps=1e-9):
    if a is None or b is None:
        return a == b
    return abs(a - b) <= eps


# 场景：window kwargs + candidate kwargs（两版用同一份输入）
_SCENARIOS = {
    "PASS_fat_iv": (
        dict(window_id="W", expiry="24h", dte_hours=24.0, side="SHORT_CALL",
             front_anchor_iv=0.86, atm_front_iv=0.84, term_reference_iv_5_10d=0.80,
             rv_24h=0.42, rv_72h=0.40, rv_7d=0.38, rv_percentile=0.50, history_days=60),
        dict(window_id="W", side="SHORT_CALL", spot=68000.0, short_strike=70000.0,
             protection_strike=72000.0, dte_hours=24.0, amount=1.0,
             short_bid=0.060, short_ask=0.062, protection_bid=0.012, protection_ask=0.014,
             executable_short_iv=0.92, executable_protection_iv=0.84,
             short_instrument="S", protection_instrument="P", short_delta=0.30)),
    "BLOCK_thin_window_edge": (
        dict(window_id="W", expiry="24h", dte_hours=24.0, side="SHORT_CALL",
             front_anchor_iv=0.41, atm_front_iv=0.40, term_reference_iv_5_10d=0.40,
             rv_24h=0.42, rv_72h=0.40, rv_7d=0.38, rv_percentile=0.50, history_days=60),
        dict(window_id="W", side="SHORT_CALL", spot=68000.0, short_strike=70000.0,
             protection_strike=72000.0, dte_hours=24.0, amount=1.0,
             short_bid=0.020, short_ask=0.022, protection_bid=0.010, protection_ask=0.012,
             executable_short_iv=0.41, executable_protection_iv=0.40,
             short_instrument="S", protection_instrument="P", short_delta=0.30)),
    "BLOCK_thin_candidate_edge": (
        dict(window_id="W", expiry="24h", dte_hours=24.0, side="SHORT_CALL",
             front_anchor_iv=0.86, atm_front_iv=0.84, term_reference_iv_5_10d=0.80,
             rv_24h=0.42, rv_72h=0.40, rv_7d=0.38, rv_percentile=0.50, history_days=60),
        dict(window_id="W", side="SHORT_CALL", spot=68000.0, short_strike=70000.0,
             protection_strike=72000.0, dte_hours=24.0, amount=1.0,
             short_bid=0.013, short_ask=0.015, protection_bid=0.012, protection_ask=0.014,
             executable_short_iv=0.86, executable_protection_iv=0.84,
             short_instrument="S", protection_instrument="P", short_delta=0.30)),
    "DISTORTED_backwardation": (
        dict(window_id="W", expiry="24h", dte_hours=24.0, side="SHORT_CALL",
             front_anchor_iv=0.95, atm_front_iv=0.92, term_reference_iv_5_10d=0.70,
             rv_24h=0.42, rv_72h=0.40, rv_7d=0.38, rv_percentile=0.50, history_days=60),
        dict(window_id="W", side="SHORT_CALL", spot=68000.0, short_strike=70000.0,
             protection_strike=72000.0, dte_hours=24.0, amount=1.0,
             short_bid=0.060, short_ask=0.062, protection_bid=0.012, protection_ask=0.014,
             executable_short_iv=0.95, executable_protection_iv=0.90,
             short_instrument="S", protection_instrument="P", short_delta=0.30)),
    "CROSSED_protection_spread": (   # 倒挂：protection_bid > protection_ask（验证收口安全语义）
        dict(window_id="W", expiry="24h", dte_hours=24.0, side="SHORT_CALL",
             front_anchor_iv=0.86, atm_front_iv=0.84, term_reference_iv_5_10d=0.72,
             rv_24h=0.42, rv_72h=0.40, rv_7d=0.38, rv_percentile=0.50, history_days=60),
        dict(window_id="W", side="SHORT_CALL", spot=68000.0, short_strike=70000.0,
             protection_strike=72000.0, dte_hours=24.0, amount=1.0,
             short_bid=0.060, short_ask=0.062, protection_bid=0.016, protection_ask=0.014,
             executable_short_iv=0.92, executable_protection_iv=0.84,
             short_instrument="S", protection_instrument="P", short_delta=0.30)),
}


def test_window_gate_equivalence_vs_snapshot():
    cfg_new, cfg_snap = VG.selected_policy_config(), _SNAP_POLICY.selected_policy_config()
    for name, (w, _c) in _SCENARIOS.items():
        new = VG.assess_window(VG.WindowInput(**w), cfg_new)
        snap = _SNAP_MODEL.assess_window(_SNAP_MODEL.WindowInput(**w), cfg_snap)
        assert new["window_vrp_gate"] == snap["window_vrp_gate"], (name, new["window_vrp_gate"], snap["window_vrp_gate"])
        assert _approx(new["forward_vol_hurdle"], snap["forward_vol_hurdle"]), name
        assert _approx(new["representative_vol_edge"], snap["representative_vol_edge"]), name
        assert new["front_to_term_state"] == snap["front_to_term_state"], name
        assert new["reason_codes"] == snap["reason_codes"], (name, new["reason_codes"], snap["reason_codes"])


def test_candidate_gate_equivalence_vs_snapshot():
    cfg_new, cfg_snap = VG.selected_policy_config(), _SNAP_POLICY.selected_policy_config()
    for name, (w, c) in _SCENARIOS.items():
        hurdle = VG.assess_window(VG.WindowInput(**w), cfg_new)["forward_vol_hurdle"] or 0.0
        cc = dict(c, forward_vol_hurdle=hurdle)   # 候选门的 hurdle 来自窗口门（必填字段）
        new = VG.assess_candidate(VG.CandidateQuote(**cc), cfg_new)
        snap = _SNAP_MODEL.assess_candidate(_SNAP_MODEL.CandidateQuote(**cc), cfg_snap)
        assert new["candidate_vrp_gate"] == snap["candidate_vrp_gate"], (name, new["candidate_vrp_gate"], snap["candidate_vrp_gate"])
        assert _approx(new["candidate_vrp_edge_ccy"], snap["candidate_vrp_edge_ccy"]), (name, new["candidate_vrp_edge_ccy"], snap["candidate_vrp_edge_ccy"])
        assert _approx(new["full_round_trip_friction"], snap["full_round_trip_friction"]), name
        assert _approx(new["vertical_net_credit_at_forward_vol_hurdle"], snap["vertical_net_credit_at_forward_vol_hurdle"]), name


def _gate_pair(w, c, cfg):
    hurdle = VG.assess_window(VG.WindowInput(**w), cfg)["forward_vol_hurdle"] or 0.0
    win = VG.assess_window(VG.WindowInput(**w), cfg)["window_vrp_gate"]
    cand = VG.assess_candidate(VG.CandidateQuote(**dict(c, forward_vol_hurdle=hurdle)), cfg)["candidate_vrp_gate"]
    return win, cand


def test_expected_gate_outcomes():
    """除等价外，锁定预期门态，防两版同时错。"""
    cfg = VG.selected_policy_config()
    out = {n: _gate_pair(w, c, cfg) for n, (w, c) in _SCENARIOS.items()}
    assert out["PASS_fat_iv"] == ("PASS", "PASS"), out["PASS_fat_iv"]
    assert out["BLOCK_thin_window_edge"][0] == "BLOCK"
    assert out["BLOCK_thin_candidate_edge"] == ("PASS", "BLOCK"), out["BLOCK_thin_candidate_edge"]
    assert out["DISTORTED_backwardation"][0] == "DISTORTED_REVIEW"
    # 倒挂价差不崩溃，且 friction 有限（倒挂腿半价差按 0 计）
    w5, c5 = _SCENARIOS["CROSSED_protection_spread"]
    h5 = VG.assess_window(VG.WindowInput(**w5), cfg)["forward_vol_hurdle"] or 0.0
    ca = VG.assess_candidate(VG.CandidateQuote(**dict(c5, forward_vol_hurdle=h5)), cfg)
    assert isinstance(ca["full_round_trip_friction"], float)


def test_canonical_primitive_wiring():
    """证明 4 原语确实走执行层 canonical（而非本地副本）。"""
    import accounting, hedge_risk
    assert VG._option_fee(0.05, 1.0) == accounting.acct_option_fee_ccy(0.05, 1.0)
    assert VG._spread_half_cost(0.010, 0.012, 1.0) == accounting.acct_spread_cost(0.010, 0.012, 1.0)
    assert VG._spread_half_cost(None, 0.012, 1.0) == 0.0          # 缺失→0（canonical 返回 None）
    assert VG._spread_half_cost(0.016, 0.014, 1.0) == 0.0         # 倒挂→0（canonical 不挡）
    assert VG._normalise_iv(65.0) == hedge_risk._normalise_iv(65.0) == 0.65


# ---------- PRICE_GATE 集成：真实菜单过滤 ----------
H = 3600000
SPOT = 73400.0
S48 = {74000: (0.45, 0.016), 75000: (0.38, 0.012), 76000: (0.30, 0.008),
       77000: (0.22, 0.005), 78000: (0.15, 0.0035)}
_BASE = {"t": None}


def _handler(*args):
    import fmz_shim  # noqa
    _, _m, path, query = args
    from urllib.parse import parse_qs
    qs = parse_qs(query or "")
    if _BASE["t"] is None:
        _BASE["t"] = int(time.time() * 1000)
    now = _BASE["t"]
    if path.endswith("/public/get_instruments"):
        return {"result": [{"instrument_name": "BTC-S-%d-C" % k, "strike": k, "option_type": "call",
                            "expiration_timestamp": now + 48 * H, "kind": "option", "tick_size": 0.0001}
                           for k in S48]}
    if path.endswith("/public/get_index_price"):
        return {"result": {"index_price": SPOT}}
    if path.endswith("/public/ticker"):
        k = int(qs.get("instrument_name", ["BTC-S-76000-C"])[0].split("-")[2])
        d, m = S48[k]
        return {"result": {"mark_price": m, "best_bid_price": round(m * 0.97, 6),
                           "best_ask_price": round(m * 1.03, 6), "underlying_price": SPOT,
                           "greeks": {"delta": d, "gamma": 0.00005}, "mark_iv": 0.7}}
    if path.endswith("/public/get_instrument"):
        return {"result": {"tick_size": 0.0001, "contract_size": 1, "min_trade_amount": 0.1}}
    if path.endswith("/private/get_account_summary"):
        return {"result": {"margin_model": "segregated_pm", "portfolio_margining_enabled": True}}
    if path.endswith("/private/get_positions"):
        return {"result": []}
    if path.endswith("/private/simulate_portfolio"):
        sp = json.loads(qs.get("simulated_positions", ["{}"])[0])
        im = 0.025 if len(sp) == 1 else 0.013
        return {"result": {"initial_margin": im, "maintenance_margin": im * 0.8, "available_funds": 1.0}}
    return {"result": None}


def _real_menu():
    import fmz_shim
    fmz_shim.exchange.io_handler = _handler
    import strategy as ST
    ST.SETTLEMENT_CURRENCY = "BTC"; ST.DIRECTION_BIAS = "SHORT_CALL"; ST.ROUND_MODE = "PLAN"
    ST.MENU_SIZE = 6; ST.SHORT_DELTA_RANGE = (0.15, 0.45); ST.PROTECTION_WIDTH_RANGE = (2000, 2500)
    ST.ENABLE_CALENDAR = False; ST.SIGNAL_CONFIDENCE = 62; ST.SIGNAL_STATE = "TRADE_SUPPORT_WEAK"
    ST.SHORT_DTE_HOURS = (24, 72); ST.ORDER_AMOUNT = 0.1; ST.MIN_MARGIN_RELIEF_RATIO = 0.10
    ST.UNDERLYING_REF_PRICE = None; ST.MIN_SHORT_PREMIUM = 0.0005; ST.MAX_SPREAD_RATIO = 0.60
    ST._LOCKED["detail_id"] = None
    menu, *_ = ST._build_menu(ST._now_ms(), SPOT)
    return menu


def test_apply_vrp_gate_filters_real_menu():
    menu = _real_menu()
    assert menu
    fat = dict(side="SHORT_CALL", front_anchor_iv=0.92, atm_front_iv=0.90,
               term_reference_iv_5_10d=0.78, rv_24h=0.42, rv_72h=0.40, rv_7d=0.38,
               rv_percentile=0.50, history_days=60, executable_short_iv=0.95,
               executable_protection_iv=0.85)
    thin = dict(fat, front_anchor_iv=0.41, atm_front_iv=0.40, term_reference_iv_5_10d=0.41)
    p_fat, b_fat = VG.apply_vrp_gate(menu, fat)
    p_thin, b_thin = VG.apply_vrp_gate(menu, thin)
    # 纯过滤：对每个真实候选都跑出裁决、不崩、partition 完整
    assert len(p_fat) + len(b_fat) == len(menu)
    assert len(p_thin) + len(b_thin) == len(menu)
    # 薄边际上下文：窗口门全 BLOCK（VRP 正确拦下 underpaid 窗口，不进可锁定方案）
    assert len(p_thin) == 0
    assert all(g["window"]["window_vrp_gate"] == "BLOCK" for _p, g in b_thin)
    assert all(g["reason_codes"] for _p, g in b_thin)
    # 肥 IV 上下文：窗口门可放行（front_iv 远高于 hurdle）——证明 VRP 非一刀切
    assert any(g["window"]["window_vrp_gate"] == "PASS" for _p, g in (p_fat + b_fat))
