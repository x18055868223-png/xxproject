# -*- coding: utf-8 -*-
"""R6：整合 PLAN 通顺缝端到端——真实 _build_menu → VRP 双门 → 组合硬预算 → 可锁定方案。

证明执行 bundle 的主流程真正用上整合层（VRP/预算只过滤、独立 AND 门），而非模块挂着不用。
"""
import os, sys, time, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import fmz_shim

H = 3600000
SPOT = 73400.0
S48 = {74000: (0.45, 0.016), 75000: (0.38, 0.012), 76000: (0.30, 0.008),
       77000: (0.22, 0.005), 78000: (0.15, 0.0035)}
_BASE = {"t": None}


def _handler(*args):
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


def _setup():
    fmz_shim.exchange.io_handler = _handler
    import strategy as ST
    ST.SETTLEMENT_CURRENCY = "BTC"; ST.DIRECTION_BIAS = "SHORT_CALL"; ST.ROUND_MODE = "PLAN"
    ST.MENU_SIZE = 6; ST.SHORT_DELTA_RANGE = (0.15, 0.45); ST.PROTECTION_WIDTH_RANGE = (2000, 2500)
    ST.ENABLE_CALENDAR = False; ST.SIGNAL_CONFIDENCE = 62; ST.SIGNAL_STATE = "TRADE_SUPPORT_WEAK"
    ST.SHORT_DTE_HOURS = (24, 72); ST.ORDER_AMOUNT = 0.1; ST.MIN_MARGIN_RELIEF_RATIO = 0.10
    ST.UNDERLYING_REF_PRICE = None; ST.MIN_SHORT_PREMIUM = 0.0005; ST.MAX_SPREAD_RATIO = 0.60
    ST._LOCKED["detail_id"] = None
    return ST


_FAT = dict(side="SHORT_CALL", front_anchor_iv=0.92, atm_front_iv=0.90,
            term_reference_iv_5_10d=0.86, rv_24h=0.42, rv_72h=0.40, rv_7d=0.38,
            rv_percentile=0.50, history_days=60, executable_short_iv=0.95,
            executable_protection_iv=0.85)
_THIN = dict(_FAT, front_anchor_iv=0.41, atm_front_iv=0.40, term_reference_iv_5_10d=0.41)


def test_no_context_lockable_is_full_menu():
    ST = _setup()
    out = ST.integrated_plan_preview(SPOT)
    assert out["reason"] == "OK" and out["menu"]
    assert out["lockable"] == out["menu"]          # 无 VRP/预算上下文 → 全可锁定（向后兼容）


def test_thin_vrp_blocks_all_lockable_empty():
    ST = _setup()
    out = ST.integrated_plan_preview(SPOT, market_context=_THIN)
    assert out["vrp_blocked"] and len(out["vrp_blocked"]) == len(out["menu"])
    assert out["lockable"] == []                    # 薄 IV → VRP 全 BLOCK → 无可锁定（不开新仓）
    assert all(b["reason_codes"] for b in out["vrp_blocked"])


def test_vrp_partition_complete_and_filter_applied():
    ST = _setup()
    out = ST.integrated_plan_preview(SPOT, market_context=_FAT)
    assert out["vrp_passed"] is not None and out["vrp_blocked"] is not None
    assert len(out["vrp_passed"]) + len(out["vrp_blocked"]) == len(out["menu"])  # 纯过滤、partition 完整
    assert out["lockable"] == out["vrp_passed"]     # 可锁定 = VRP 双门 PASS 子集


def test_portfolio_budget_breach_blocks_lockable():
    ST = _setup()
    pstate = {"current": {"open_positions": 2, "short_gamma": 0.9, "short_vega": 0.0, "margin_used": 0.0},
              "limits": {"max_open_positions": 1, "max_short_gamma": 0.5, "max_short_vega": 1.0, "max_margin": 5000.0},
              "proposed_size": 0.1}
    out = ST.integrated_plan_preview(SPOT, market_context=_FAT, portfolio_state=pstate)
    assert out["portfolio_budget"]["decision"] == "BLOCK"
    assert out["lockable"] == []                    # 组合预算超限 → 无可锁定（入场前 AND 门）
