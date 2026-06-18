# -*- coding: utf-8 -*-
"""R2：执行会话骨架接真实数据。

用真实 strategy._build_menu 产出的**真实方案**（真实合约/行权/报价/S:PM）驱动
session_core.ExecutionSession，验证 plan_hash 稳定+防重排、显式授权门、TTL 过期阻断。
与 codex 用 toy plan 测会话不同：这里 lock 的是真实选腿方案。
"""
import os, sys, time, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

try:
    from urllib.parse import parse_qs
except ImportError:
    from urlparse import parse_qs

import fmz_shim
import session_core as SC

H = 3600000
SPOT = 73400.0
S48 = {74000: (0.45, 0.016), 75000: (0.38, 0.012), 76000: (0.30, 0.008),
       77000: (0.22, 0.005), 78000: (0.15, 0.0035), 79000: (0.10, 0.0025)}
_BASE = {"t": None}


def _handler(*args):
    _, _m, path, query = args
    qs = parse_qs(query or "")
    if _BASE["t"] is None:
        _BASE["t"] = int(time.time() * 1000)
    now = _BASE["t"]
    if path.endswith("/public/get_instruments"):
        out = []
        for k in S48:
            out.append({"instrument_name": "BTC-S-%d-C" % k, "strike": k, "option_type": "call",
                        "expiration_timestamp": now + 48 * H, "kind": "option", "tick_size": 0.0001})
        return {"result": out}
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
    fmz_shim.exchange.io_handler = _handler
    import strategy as ST
    ST.SETTLEMENT_CURRENCY = "BTC"; ST.DIRECTION_BIAS = "SHORT_CALL"; ST.ROUND_MODE = "PLAN"
    ST.MENU_SIZE = 6; ST.SHORT_DELTA_RANGE = (0.15, 0.45); ST.PROTECTION_WIDTH_RANGE = (2000, 2500)
    ST.ENABLE_CALENDAR = False; ST.SIGNAL_CONFIDENCE = 62; ST.SIGNAL_STATE = "TRADE_SUPPORT_WEAK"
    ST.SHORT_DTE_HOURS = (24, 72); ST.ORDER_AMOUNT = 0.1; ST.MIN_MARGIN_RELIEF_RATIO = 0.10
    ST.UNDERLYING_REF_PRICE = None; ST.MIN_SHORT_PREMIUM = 0.0005; ST.MAX_SPREAD_RATIO = 0.60
    ST._LOCKED["detail_id"] = None
    menu, pm_ok, model, reason, diag = ST._build_menu(ST._now_ms(), SPOT)
    return menu


def _plan_dict(p):
    """会话锁定用的方案标识子集（真实选腿结果）。"""
    return {"id": p["id"], "mode": p["mode"], "side": "SHORT_CALL",
            "short_instrument": p["short_instrument"], "protection_instrument": p["protection_instrument"],
            "short_strike": p["short_strike"], "protection_strike": p["protection_strike"],
            "win_rate": p["win_rate"], "rr": p["rr"], "net_credit": p["net_credit_effective"]}


def test_session_locks_real_plan_hash_stable_and_tamper_detected():
    menu = _real_menu()
    assert menu, "真实 _build_menu 应产出方案"
    plan = _plan_dict(menu[0])
    s = SC.ExecutionSession.open("exec-r2", "sig-r2", now_ts=1000)
    locked = s.lock_plan(plan, now_ts=1001, ttl_sec=30)
    # 同方案重锁 → hash 稳定
    s2 = SC.ExecutionSession.open("exec-r2b", "sig-r2", now_ts=1000)
    locked2 = s2.lock_plan(_plan_dict(menu[0]), now_ts=1001, ttl_sec=30)
    assert locked["plan_hash"] == locked2["plan_hash"]
    assert s.state == "PLAN_LOCKED"
    # 篡改方案（模拟重排/换腿）→ hash 变化（防误选）
    tampered = _plan_dict(menu[0]); tampered["short_strike"] += 500
    s3 = SC.ExecutionSession.open("exec-r2c", "sig-r2", now_ts=1000)
    locked3 = s3.lock_plan(tampered, now_ts=1001, ttl_sec=30)
    assert locked3["plan_hash"] != locked["plan_hash"]


def _checks(all_ok=True):
    return SC.PrecommitChecks(signal_fresh=all_ok, vrp_rechecked=all_ok, spm_rechecked=all_ok,
                              quotes_rechecked=all_ok, ledger_rechecked=all_ok,
                              spread_ok=all_ok, maker_only=True)


def test_session_explicit_authorization_gate():
    menu = _real_menu()
    plan = _plan_dict(menu[0])
    # 未显式授权(allow_real_order=False) → 不可下单（空跑）
    s = SC.ExecutionSession.open("exec-r2", "sig-r2", now_ts=1000)
    s.lock_plan(plan, now_ts=1001, ttl_sec=30)
    s.approve_locked_plan(now_ts=1002, checks=_checks(True), allow_real_order=False)
    assert s.state == "ARMED_PREVIEW"
    assert s.can_commit_order(now_ts=1003) is False
    # 显式授权 + 检查全过 + TTL 内 → 可下单
    s.approve_locked_plan(now_ts=1004, checks=_checks(True), allow_real_order=True)
    assert s.can_commit_order(now_ts=1005) is True
    # 某项 precommit 未过 → 不可下单
    s.approve_locked_plan(now_ts=1006, checks=_checks(False), allow_real_order=True)
    assert s.can_commit_order(now_ts=1007) is False


def test_session_ttl_expiry_blocks_commit():
    menu = _real_menu()
    plan = _plan_dict(menu[0])
    s = SC.ExecutionSession.open("exec-r2", "sig-r2", now_ts=1000)
    s.lock_plan(plan, now_ts=1001, ttl_sec=30)            # expires_ts=1031
    s.approve_locked_plan(now_ts=1002, checks=_checks(True), allow_real_order=True)
    assert s.can_commit_order(now_ts=1020) is True        # TTL 内
    assert s.can_commit_order(now_ts=1040) is False       # TTL 过期 → 拒绝旧报价进入执行
    assert s.approval_intent["approval_state"] == "EXPIRED"
