# -*- coding: utf-8 -*-
"""端到端空跑冒烟：计划轮(枚举+排序+持久化方案库) + 下单轮(按号复核+空跑预览)。"""
import os, sys, time, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

try:
    from urllib.parse import parse_qs
except ImportError:
    from urlparse import parse_qs

import fmz_shim

_BASE = {"t": None}
H = 3600000
SPOT = 73400.0
# 48h 短腿到期：strike -> (delta, mark)
S48 = {74000: (0.45, 0.016), 75000: (0.38, 0.012), 76000: (0.30, 0.008),
       77000: (0.22, 0.005), 78000: (0.15, 0.0035), 79000: (0.10, 0.0025), 80000: (0.06, 0.0018)}
# 168h 远期(日历保护)到期：strike -> (delta, mark)
P168 = {76000: (0.34, 0.018), 77000: (0.28, 0.014), 78000: (0.22, 0.011),
        79000: (0.18, 0.009), 80000: (0.14, 0.007), 81000: (0.11, 0.0055), 82000: (0.09, 0.0045)}


def _instruments(now_ms):
    s_exp, p_exp = now_ms + 48 * H, now_ms + 168 * H
    out = []
    for k in S48:
        out.append({"instrument_name": "BTC-S-%d-C" % k, "strike": k, "option_type": "call",
                    "expiration_timestamp": s_exp, "kind": "option", "tick_size": 0.0001})
    for k in P168:
        out.append({"instrument_name": "BTC-P-%d-C" % k, "strike": k, "option_type": "call",
                    "expiration_timestamp": p_exp, "kind": "option", "tick_size": 0.0001})
    return out


def _quote(inst):
    p = inst.split("-")
    side, strike = p[1], int(p[2])
    delta, mark = (S48[strike] if side == "S" else P168[strike])
    return {"mark_price": mark, "best_bid_price": round(mark * 0.97, 6),
            "best_ask_price": round(mark * 1.03, 6), "underlying_price": SPOT,
            "greeks": {"delta": delta}}


def _handler(*args):
    _, _m, path, query = args
    qs = parse_qs(query or "")
    if _BASE["t"] is None:
        _BASE["t"] = int(time.time() * 1000)
    now = _BASE["t"]
    if path.endswith("/public/get_instruments"):
        return {"result": _instruments(now)}
    if path.endswith("/public/get_index_price"):
        return {"result": {"index_price": SPOT}}
    if path.endswith("/public/ticker"):
        return {"result": _quote(qs.get("instrument_name", ["BTC-S-76000-C"])[0])}
    if path.endswith("/public/get_instrument"):
        return {"result": {"tick_size": 0.0001, "contract_size": 1, "min_trade_amount": 0.1}}
    if path.endswith("/private/get_account_summary"):
        return {"result": {"margin_model": "segregated_pm", "portfolio_margining_enabled": True,
                           "initial_margin": 0.02, "maintenance_margin": 0.015}}
    if path.endswith("/private/get_positions"):
        return {"result": []}
    if path.endswith("/private/simulate_portfolio"):
        simpos = json.loads(qs.get("simulated_positions", ["{}"])[0])
        im = 0.025 if len(simpos) == 1 else 0.013     # relief ~0.48
        return {"result": {"initial_margin": im, "maintenance_margin": im * 0.8,
                           "available_funds": 1.0}}
    return {"result": None}


def _setup(ST, EX, round_mode, selected=1):
    fmz_shim.exchange.io_handler = _handler
    ST.SETTLEMENT_CURRENCY = "BTC"
    ST.DIRECTION_BIAS = "SHORT_CALL"
    ST.ROUND_MODE = round_mode
    ST.SELECTED_PLAN = selected
    ST.MENU_SIZE = 6
    ST.SHORT_DELTA_RANGE = (0.15, 0.45)
    ST.PROTECTION_WIDTH_RANGE = (2000, 2500)
    ST.SIGNAL_CONFIDENCE = 62
    ST.PLAN_WEIGHTS = {"win_rate": 0.375, "rr": 0.375, "signal": 0.25}
    ST.SHORT_DTE_HOURS = (24, 72)
    ST.ORDER_AMOUNT = 0.1
    ST.MIN_MARGIN_RELIEF_RATIO = 0.10
    ST.MIN_SHORT_PREMIUM = 0.0005
    ST.MAX_SPREAD_RATIO = 0.60
    ST.PLAN_REFRESH_SECONDS = 45
    ST.UNDERLYING_REF_PRICE = None
    ST.ALLOW_TRADING = False
    EX.ALLOW_TRADING = False
    ST._LOCKED["detail_id"] = None         # 复位明细锁定，保证测试确定性


def test_plan_round_builds_vertical_menu():
    import ledger as LG  # noqa
    import strategy as ST
    import execution as EX
    _setup(ST, EX, "PLAN")
    ctx = ST._plan_round(ST._spot_price())
    menu = ctx["menu"]
    assert 1 <= len(menu) <= 6
    # 只含同期垂直
    assert all(m["mode"] == 2 for m in menu)
    # 短腿与保护腿同到期（垂直硬约束）
    assert all(m["short_expiry"] == m["protection_expiry"] for m in menu)
    # 编号连续 + 至少含 均衡 标签
    assert [m["plan_no"] for m in menu] == list(range(1, len(menu) + 1))
    assert any("均衡" in m["tags"] for m in menu)
    # 垂直：有效净credit == 单笔净credit（无复用/残值）
    assert all(m["net_credit_effective"] == m["net_credit_single"] for m in menu)
    # 已持久化方案库
    assert ST._G(ST._MENU_KEY) and len(ST._G(ST._MENU_KEY)) == len(menu)


def test_order_round_resolves_selected_and_previews():
    import ledger as LG
    import strategy as ST
    import execution as EX
    # 先跑计划轮持久化菜单
    _setup(ST, EX, "PLAN")
    plan_ctx = ST._plan_round(ST._spot_price())
    menu = plan_ctx["menu"]
    target_id = menu[0]["id"]                       # 按稳定唯一编号选择
    # 切下单轮，按编号选方案，空跑预览
    _setup(ST, EX, "ORDER", selected=target_id)
    new_state, ctx = ST._run_order(LG.ledger_load(), ST._spot_price())
    assert ctx["selected_plan"] == target_id
    assert ctx["short_instrument"] == menu[0]["short_instrument"]
    assert ctx["protection_instrument"] == menu[0]["protection_instrument"]
    assert new_state == LG.S_NO_POSITION and ctx["reason"] == "ORDER_PREVIEW_DRY"
    # 面板含策略选择明细表
    tables = json.loads(ST.disp_status_panel(ctx, "下单轮").split("`", 1)[1].rsplit("`", 1)[0])
    assert any("策略选择明细" in t["title"] for t in tables)


def test_live_order_records_entry_risk_anchor_on_short_ledger():
    import ledger as LG
    import strategy as ST
    import execution as EX
    _setup(ST, EX, "PLAN")
    plan_ctx = ST._plan_round(ST._spot_price())
    selected = plan_ctx["menu"][0]
    target_id = selected["id"]
    _setup(ST, EX, "ORDER", selected=target_id)
    orig_open = ST.exec_open_structure
    try:
        ST.exec_open_structure = lambda short_i, prot_i, amt: {
            "dry": False,
            "protection_fill": {"filled": amt, "avg_price": 0.010},
            "short_fill": {"filled": amt, "avg_price": 0.008},
        }
        led = LG.ledger_load()
        new_state, _ctx = ST._run_order(led, ST._spot_price())
        anchor = led["short"].get("entry_risk_anchor")
        assert new_state == LG.S_SHORT_ACTIVE_PROTECTED
        assert anchor and anchor["entry_loss_boundary"] == selected["breakeven"]
        assert 0.0 <= anchor["entry_touch_probability"] <= 1.0
        assert anchor["entry_dte_hours"] > 0
    finally:
        ST.exec_open_structure = orig_open


def test_only_vertical_in_menu():
    import strategy as ST
    import execution as EX
    _setup(ST, EX, "PLAN")
    ctx = ST._plan_round(ST._spot_price())
    menu = ctx["menu"]
    assert menu and all(m["mode"] == 2 for m in menu)


def test_order_round_no_menu_guard():
    import ledger as LG
    import strategy as ST
    import execution as EX
    _setup(ST, EX, "ORDER", selected=1)
    ST._G(ST._MENU_KEY, None)                      # 清空持久化菜单
    _, ctx = ST._run_order(LG.ledger_load(), ST._spot_price())
    assert "NO_PLAN_MENU" in ctx["reason"]


# ===== 看涨·卖 PUT 方向（验证策略不止支持卖 Call）=====
S48P = {73000: (-0.45, 0.016), 72000: (-0.38, 0.012), 71000: (-0.30, 0.008),
        70000: (-0.22, 0.005), 69000: (-0.15, 0.0035), 68000: (-0.10, 0.0025)}
P168P = {71000: (-0.34, 0.018), 70000: (-0.28, 0.014), 69000: (-0.22, 0.011),
         68000: (-0.18, 0.009), 67000: (-0.14, 0.007), 66000: (-0.11, 0.0055)}


def _handler_put(*args):
    _, _m, path, query = args
    qs = parse_qs(query or "")
    if _BASE["t"] is None:
        _BASE["t"] = int(time.time() * 1000)
    now = _BASE["t"]
    if path.endswith("/public/get_instruments"):
        out = []
        for k in S48P:
            out.append({"instrument_name": "BTC-S-%d-P" % k, "strike": k, "option_type": "put",
                        "expiration_timestamp": now + 48 * H, "kind": "option", "tick_size": 0.0001})
        for k in P168P:
            out.append({"instrument_name": "BTC-P-%d-P" % k, "strike": k, "option_type": "put",
                        "expiration_timestamp": now + 168 * H, "kind": "option", "tick_size": 0.0001})
        return {"result": out}
    if path.endswith("/public/get_index_price"):
        return {"result": {"index_price": SPOT}}
    if path.endswith("/public/ticker"):
        p = qs.get("instrument_name", ["BTC-S-72000-P"])[0].split("-")
        d, m = (S48P if p[1] == "S" else P168P)[int(p[2])]
        return {"result": {"mark_price": m, "best_bid_price": round(m * 0.97, 6),
                           "best_ask_price": round(m * 1.03, 6), "underlying_price": SPOT,
                           "greeks": {"delta": d}}}
    if path.endswith("/public/get_instrument"):
        return {"result": {"tick_size": 0.0001}}
    if path.endswith("/private/get_account_summary"):
        return {"result": {"margin_model": "segregated_pm", "portfolio_margining_enabled": True}}
    if path.endswith("/private/get_positions"):
        return {"result": []}
    if path.endswith("/private/simulate_portfolio"):
        sp = json.loads(qs.get("simulated_positions", ["{}"])[0])
        im = 0.025 if len(sp) == 1 else 0.013
        return {"result": {"initial_margin": im, "maintenance_margin": im * 0.8, "available_funds": 1.0}}
    return {"result": None}


def test_plan_round_short_put_bullish():
    import strategy as ST
    import execution as EX
    _setup(ST, EX, "PLAN")
    fmz_shim.exchange.io_handler = _handler_put
    ST.DIRECTION_BIAS = "SHORT_PUT"                 # 看涨：卖出下方 put
    ctx = ST._plan_round(ST._spot_price())
    menu = ctx["menu"]
    assert 1 <= len(menu) <= 6
    assert all(m["mode"] == 2 for m in menu)        # 只含同期垂直
    # 卖 put：短腿/保护腿均为 PUT，且保护腿行权 < 短腿行权（更外侧/更低）
    for m in menu:
        assert m["short_instrument"].endswith("-P")
        assert m["protection_instrument"].endswith("-P")
        assert m["protection_strike"] < m["short_strike"]
    assert any(m["qualified"] for m in menu)
