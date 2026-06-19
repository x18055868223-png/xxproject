# -*- coding: utf-8 -*-
"""E2.2 单一主链 run_cycle 集成测试（OFFLINE_MANUAL 信号 + 短确认码硬授权 + 急停/恢复/拒绝/幂等）。

注：mock 合约到期基于真实 time.time()，故 run_cycle() 用真实 _now_ms()（不传 now_ms），
使 DTE 落在 24–72h 带内；命令幂等键依赖 refresh_seq+code 而非时间戳，故无需固定时间。"""
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
# 单一 48h 到期（垂直：保护腿同到期），strike -> (delta, mark)
S48 = {74000: (0.45, 0.016), 75000: (0.38, 0.012), 76000: (0.30, 0.008),
       77000: (0.22, 0.005), 78000: (0.15, 0.0035), 79000: (0.10, 0.0025), 80000: (0.06, 0.0018)}


def _instruments(now_ms):
    s_exp = now_ms + 48 * H
    return [{"instrument_name": "BTC-S-%d-C" % k, "strike": k, "option_type": "call",
             "expiration_timestamp": s_exp, "kind": "option", "tick_size": 0.0001}
            for k in S48]


def _quote(inst):
    strike = int(inst.split("-")[2])
    delta, mark = S48[strike]
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
        im = 0.025 if len(simpos) == 1 else 0.013
        return {"result": {"initial_margin": im, "maintenance_margin": im * 0.8, "available_funds": 1.0}}
    return {"result": None}


def _setup():
    fmz_shim._STORE.clear()           # 清空 _G（会话/库/锁定/账本/命令账本）
    fmz_shim._commands.clear()        # 清空命令队列
    fmz_shim.exchange.io_handler = _handler
    import strategy as ST
    import execution as EX
    ST.SETTLEMENT_CURRENCY = "BTC"; EX.SETTLEMENT_CURRENCY = "BTC"
    ST.DIRECTION_BIAS = "SHORT_CALL"
    ST.SIGNAL_SOURCE = "OFFLINE_MANUAL"
    ST.SIGNAL_STATE = "TRADE_SUPPORT_WEAK"
    ST.SIGNAL_CONFIDENCE = 62
    ST.SHORT_DELTA_RANGE = (0.15, 0.45)
    ST.PROTECTION_WIDTH_RANGE = (2000, 2500)
    ST.SHORT_DTE_HOURS = (24, 72)
    ST.ORDER_AMOUNT = 0.1
    ST.MENU_SIZE = 6
    ST.MIN_MARGIN_RELIEF_RATIO = 0.10
    ST.MIN_SHORT_PREMIUM = 0.0005
    ST.MAX_SPREAD_RATIO = 0.60
    ST.PLAN_WEIGHTS = {"win_rate": 0.375, "rr": 0.375, "signal": 0.25}
    ST.UNDERLYING_REF_PRICE = None
    ST.ALLOW_ENTRY_TRADING = False
    ST.KILL_NEW_RISK = False
    ST.EMERGENCY_REDUCE_ONLY = False
    ST.ROBOT_ID = "r-test"
    ST.HEDGE_VENUE = "DERIBIT"
    return ST


def test_run_cycle_builds_library_then_hard_auth_lock():
    ST = _setup()
    a = ST.run_cycle()
    assert a["console_phase"] == "HARD_APPROVAL_WAIT"
    assert a["pending_candidates"]
    code = a["pending_candidates"][0]["confirm_code"]
    assert code and len(code) >= 4
    fmz_shim._commands.append("执行:" + code)
    b = ST.run_cycle()
    assert b["last_command"] == "EXECUTE" and b["last_command_outcome"] == "locked"
    assert b["console_phase"] == "PLAN_LOCKED"


def test_run_cycle_duplicate_execute_ignored():
    ST = _setup()
    a = ST.run_cycle()
    code = a["pending_candidates"][0]["confirm_code"]
    fmz_shim._commands.append("执行:" + code)
    ST.run_cycle()                                       # 锁定
    fmz_shim._commands.append("执行:" + code)            # 同码重复
    c = ST.run_cycle()
    assert c["last_command_outcome"] == "duplicate_ignored"
    assert c["console_phase"] == "PLAN_LOCKED"


def test_run_cycle_invalid_code_rejected():
    ST = _setup()
    ST.run_cycle()                                        # 先建库
    fmz_shim._commands.append("执行:ZZZZ")
    ctx = ST.run_cycle()
    assert ctx["last_command_outcome"] == "confirm_code_invalid_or_stale"
    assert ctx["console_phase"] == "HARD_APPROVAL_WAIT"


def test_run_cycle_kill_overrides_entry_gate_and_resume():
    ST = _setup()
    ST.ALLOW_ENTRY_TRADING = True                         # 即使开了进场门
    ST.run_cycle()
    fmz_shim._commands.append("急停")
    ctx = ST.run_cycle()
    assert ctx["kill_new_risk"] and ctx["console_phase"] == "KILLED"
    assert not ctx["gate_summary"]["ENTRY"]["allowed"]    # 急停覆盖进场门
    fmz_shim._commands.append("恢复")
    ctx2 = ST.run_cycle()
    assert not ctx2["kill_new_risk"]


def test_run_cycle_reject_clears_lock():
    ST = _setup()
    a = ST.run_cycle()
    code = a["pending_candidates"][0]["confirm_code"]
    fmz_shim._commands.append("执行:" + code)
    ST.run_cycle()                                        # 锁定
    fmz_shim._commands.append("拒绝")
    ctx = ST.run_cycle()
    assert ctx["console_phase"] != "PLAN_LOCKED"          # 锁定已清，回待批


def test_run_cycle_offline_manual_blocked_signal_no_library():
    ST = _setup()
    ST.SIGNAL_STATE = "NO_TRADE_BLOCKED"                  # 不放行进场
    ctx = ST.run_cycle()
    assert ctx["console_phase"] == "OFFLINE_MANUAL"
    assert not ctx["pending_candidates"]


def test_run_cycle_locked_dryrun_precommit_preview():
    ST = _setup()
    a = ST.run_cycle()
    code = a["pending_candidates"][0]["confirm_code"]
    fmz_shim._commands.append("执行:" + code)
    b = ST.run_cycle()                                    # 锁定同轮即尝试预提交（默认空跑）
    assert b["console_phase"] == "PLAN_LOCKED"
    assert b.get("precommit") is not None
    assert "vrp_rechecked" in b["precommit"]["failed"]    # OFFLINE 无 VRP → fail-closed，不真实成交
    assert b.get("order_intent")                          # 空跑预览仍展示将下单意图
    assert not b.get("entry_snapshot")


def test_run_cycle_commit_path_with_forced_precommit_and_gate():
    import ledger as LG
    ST = _setup()
    a = ST.run_cycle()
    code = a["pending_candidates"][0]["confirm_code"]
    fmz_shim._commands.append("执行:" + code)
    ST.run_cycle()                                        # 锁定（首轮预提交空跑失败）
    orig_live, orig_step = ST._build_precommit_live, ST.exec_entry_campaign_step
    ST._build_precommit_live = lambda locked, spot, verdict: {
        "signal_fresh": True, "sig_package_id": locked.get("signal_package_id"),
        "same_expiry": True, "vrp_pass": True, "spm_relief": 0.5, "min_relief": 0.1,
        "quotes_fresh": True, "net_credit_after_costs": 0.0003,
        "projected_budget_decision": "ALLOW", "ledger_reconciled": True,
        "no_unknown_orders": True, "spread_ok": True, "_budget": {"decision": "ALLOW"}}
    ST.exec_entry_campaign_step = lambda prot_i, short_i, amount, floor, steps, attempt, pdone, sdone, allow_live, label="entry": {
        "quotes_ok": True, "credit_ok": True, "dry": False, "prot_price": 0.006, "short_price": 0.010,
        "net_credit": 0.0003, "n_used": 0, "prot_fill": amount, "short_fill": amount, "reason": "ENTRY_STEP"}
    ST.ALLOW_ENTRY_TRADING = True
    try:
        c = ST.run_cycle()                                # 预提交全过 + 进场门开 → 开仓活动一轮成交达标
    finally:
        ST._build_precommit_live, ST.exec_entry_campaign_step = orig_live, orig_step
        ST.ALLOW_ENTRY_TRADING = False
    assert c["console_phase"] == "POSITION_MANAGE"
    snap = c.get("entry_snapshot")
    assert snap and snap["entry_profit_ceiling_net"] > 0   # 冻结入场利润上限
    assert LG.ledger_get_state() == LG.S_SHORT_ACTIVE_PROTECTED
    assert ST._G(ST._POSITION_KEY)                         # 入场快照已持久化


def test_run_cycle_position_manage_runs_arbiter():
    import ledger as LG
    ST = _setup()
    LG.ledger_set_state(LG.S_SHORT_ACTIVE_PROTECTED)       # 造持仓态
    ST._G(ST._POSITION_KEY, {"position_id": "pos-1", "remaining_short_qty": 0.1,
                             "entry_profit_ceiling_net": 0.0003})
    ctx = ST.run_cycle()
    assert ctx["console_phase"] == "POSITION_MANAGE"
    arb = ctx.get("action_arb")
    assert arb and arb["preferred_action"] in (
        "HOLD", "EXIT_PREFERRED", "HEDGE_READY", "TAKE_PROFIT_READY")


def test_run_cycle_recovery_blocked_halts_new_open():
    ST = _setup()
    ST._G(ST._RECOVERY_KEY, {"state": "RECOVERY_BLOCKED", "reasons": ["x"], "allow_new_open": False})
    ctx = ST.run_cycle()
    assert ctx["console_phase"] == "RECOVERY_BLOCKED"
    assert not ctx.get("pending_candidates")


def test_run_cycle_soft_authorize_and_revoke():
    import ledger as LG
    import authorization as A
    ST = _setup()
    LG.ledger_set_state(LG.S_SHORT_ACTIVE_PROTECTED)
    ST._G(ST._POSITION_KEY, {"position_id": "pos-7", "remaining_short_qty": 0.1})
    c0 = ST.run_cycle()
    assert "未授权" in (c0.get("exit_auth_state") or "")     # 控制台显示授权码
    code = A.auth_code("pos-7", A.POLICY_TAKE_PROFIT)
    fmz_shim._commands.append("授权止盈:" + code)
    c1 = ST.run_cycle()
    assert c1["last_command_outcome"].startswith("authorized")
    assert "已授权" in (c1.get("exit_auth_state") or "")
    fmz_shim._commands.append("撤销授权")
    c2 = ST.run_cycle()
    assert c2["last_command_outcome"] == "revoked"
    assert "未授权" in (c2.get("exit_auth_state") or "")


def test_run_cycle_authorize_invalid_code():
    import ledger as LG
    ST = _setup()
    LG.ledger_set_state(LG.S_SHORT_ACTIVE_PROTECTED)
    ST._G(ST._POSITION_KEY, {"position_id": "pos-7"})
    fmz_shim._commands.append("授权止盈:WRONG")
    c = ST.run_cycle()
    assert c["last_command_outcome"] == "auth_code_invalid"


def _open_tp_position(ST, LG, A):
    LG.ledger_set_state(LG.S_SHORT_ACTIVE_PROTECTED)
    ST._G(ST._POSITION_KEY, {"position_id": "pos-tp", "short_instrument": "BTC-S-80000-C",
                             "remaining_short_qty": 0.1, "long_remaining_qty": 0.1,
                             "entry_profit_ceiling_net": 0.01,
                             "max_total_exit_spend": 0.002, "realized_exit_spend": 0.0,
                             "take_profit_target_ratio": 0.80})
    ST._G(ST._EXIT_AUTH_KEY, A.build_authorization("pos-tp", A.POLICY_TAKE_PROFIT, 1))


def test_run_cycle_take_profit_qualified_dryrun_no_real_buyback():
    import ledger as LG
    import authorization as A
    ST = _setup()
    _open_tp_position(ST, LG, A)
    ctx = ST.run_cycle()                                  # 默认 ALLOW_EXIT_TRADING=False
    assert ctx["console_phase"] == "POSITION_MANAGE"
    assert ctx.get("exit_campaign_state") == "WORKING_SHORT"   # 资格达标 + 预算允许 → 就绪
    assert ctx["action_arb"]["preferred_action"] == "TAKE_PROFIT_READY"
    assert ST._G(ST._POSITION_KEY)["remaining_short_qty"] == 0.1   # 门关 → 空跑，不动真实仓


def test_run_cycle_exit_buyback_flattens_short_when_gated_on():
    import ledger as LG
    import authorization as A
    ST = _setup()
    _open_tp_position(ST, LG, A)
    ST.ALLOW_EXIT_TRADING = True
    orig = ST.exec_exit_buyback_step
    ST.exec_exit_buyback_step = lambda inst, amt, cap, allow_live, allow_taker=False, label="exit_short": {
        "filled": amt, "avg_price": 0.0018, "dry": False, "taker": allow_taker, "reason": "EXIT_STEP"}
    try:
        ST.run_cycle()
    finally:
        ST.exec_exit_buyback_step = orig
        ST.ALLOW_EXIT_TRADING = False
    assert ST._G(ST._POSITION_KEY)["remaining_short_qty"] == 0.0         # 买回完成
    assert LG.ledger_get_state() == LG.S_SHORT_FLAT_LONG_RESIDUAL        # 转保护腿回收态


def test_run_cycle_hedge_decision_surfaced():
    import ledger as LG
    ST = _setup()
    LG.ledger_set_state(LG.S_SHORT_ACTIVE_PROTECTED)
    ST._G(ST._POSITION_KEY, {"position_id": "pos-h", "short_instrument": "BTC-S-76000-C",
                             "side": "CALL", "remaining_short_qty": 0.1,
                             "entry_profit_ceiling_net": 0.001, "max_total_exit_spend": 0.0002})
    ctx = ST.run_cycle()                                   # 默认 ALLOW_HEDGE_TRADING=False → dry
    assert ctx["console_phase"] == "POSITION_MANAGE"
    hs = ctx.get("hedge_state")
    assert hs and "buy" in hs and "HEDGE_OPEN" in hs and "DERIBIT" in hs   # SHORT_CALL→buy 对冲，从0→开仓


def test_run_cycle_hedge_binance_venue():
    import ledger as LG
    ST = _setup()
    ST.HEDGE_VENUE = "BINANCE"
    LG.ledger_set_state(LG.S_SHORT_ACTIVE_PROTECTED)
    ST._G(ST._POSITION_KEY, {"position_id": "pos-hb", "short_instrument": "BTC-S-76000-C",
                             "side": "CALL", "remaining_short_qty": 0.1})
    ctx = ST.run_cycle()
    hs = ctx.get("hedge_state")
    assert hs and "BINANCE" in hs      # 场所=Binance、线性目标(BTC)；默认 ALLOW_HEDGE_TRADING=False→dry


def _all_pass_live(ST):
    ST._build_precommit_live = lambda locked, spot, verdict: {
        "signal_fresh": True, "sig_package_id": locked.get("signal_package_id"),
        "same_expiry": True, "vrp_pass": True, "spm_relief": 0.5, "min_relief": 0.1,
        "quotes_fresh": True, "net_credit_after_costs": 0.0003,
        "projected_budget_decision": "ALLOW", "ledger_reconciled": True,
        "no_unknown_orders": True, "spread_ok": True, "_budget": {"decision": "ALLOW"}}


def test_entry_campaign_dry_persists_not_oneshot():
    """开仓活动核心：预提交过但门关(dry)时，跨多轮持久 PLAN_LOCKED、不一次性放弃、锁不清空。"""
    ST = _setup()
    a = ST.run_cycle()
    code = a["pending_candidates"][0]["confirm_code"]
    fmz_shim._commands.append("执行:" + code)
    ST.run_cycle()                                         # 锁定
    orig = ST._build_precommit_live
    _all_pass_live(ST)
    try:
        last = None
        for _ in range(6):                                 # 跨 6 轮（旧一次性逻辑早该 abandon）
            last = ST.run_cycle()                          # ALLOW_ENTRY_TRADING 默认 False → dry
        assert last["console_phase"] == "PLAN_LOCKED"      # 持久待成交、未放弃
        assert ST._G(ST._LOCKED_KEY) is not None           # 锁仍在（非一次性消费）
        assert last.get("entry_state") == "ENTRY_WORKING"  # 信用底线内、可挂(dry)
    finally:
        ST._build_precommit_live = orig


# ===== H1：持仓后链路补强（P0①②③）=====

def test_closed_archival_when_all_flat():
    """P0②：两腿 + 对冲 perp 均归零 → 归档 CLOSED、清快照。"""
    import ledger as LG
    ST = _setup()
    LG.ledger_set_state(LG.S_SHORT_FLAT_LONG_RESIDUAL)
    ST._G(ST._POSITION_KEY, {"position_id": "pos-c", "short_instrument": "BTC-S-80000-C",
                             "remaining_short_qty": 0.0, "long_remaining_qty": 0.0})
    ST.run_cycle()
    assert LG.ledger_get_state() == LG.S_CLOSED
    assert ST._G(ST._POSITION_KEY) is None
    assert ST._G(ST._CLOSED_HISTORY_KEY) and len(ST._G(ST._CLOSED_HISTORY_KEY)) == 1


def test_protection_recovery_then_closed_when_gated_on():
    """P0②：短腿归零后回收保护腿（门开+成交）→ 两腿归零 → CLOSED。"""
    import ledger as LG
    ST = _setup()
    ST.ALLOW_EXIT_TRADING = True
    LG.ledger_set_state(LG.S_SHORT_FLAT_LONG_RESIDUAL)
    ST._G(ST._POSITION_KEY, {"position_id": "pos-r", "short_instrument": "BTC-S-80000-C",
                             "long_instrument": "BTC-S-79000-C",
                             "remaining_short_qty": 0.0, "long_remaining_qty": 0.1})
    orig = ST.exec_protection_recovery_step
    ST.exec_protection_recovery_step = lambda li, qty, allow_live, label="recover_long": {
        "sold": qty, "price": 0.001, "dry": False, "state": "COMPLETE", "reason": "RECOVER_STEP"}
    try:
        ST.run_cycle()
    finally:
        ST.exec_protection_recovery_step = orig
        ST.ALLOW_EXIT_TRADING = False
    assert LG.ledger_get_state() == LG.S_CLOSED
    assert ST._G(ST._POSITION_KEY) is None


def test_exit_active_forbids_new_hedge_open():
    """P0③：授权+止盈资格达标(退出活动 WORKING)时，对冲本应 OPEN 被禁→HEDGE_HOLD。"""
    import ledger as LG
    import authorization as A
    ST = _setup()
    LG.ledger_set_state(LG.S_SHORT_ACTIVE_PROTECTED)
    ST._G(ST._POSITION_KEY, {"position_id": "pos-e", "short_instrument": "BTC-S-80000-C",
                             "side": "CALL", "remaining_short_qty": 0.1, "long_remaining_qty": 0.1,
                             "entry_profit_ceiling_net": 0.01, "max_total_exit_spend": 0.002,
                             "take_profit_target_ratio": 0.80})
    ST._G(ST._EXIT_AUTH_KEY, A.build_authorization("pos-e", A.POLICY_TAKE_PROFIT, 1))
    ctx = ST.run_cycle()
    assert ctx.get("exit_campaign_state") == "WORKING_SHORT"
    assert "HEDGE_HOLD" in (ctx.get("hedge_state") or "")   # 退出期禁新增对冲敞口


# ===== 风险严重度 → 仲裁（接 hedge_risk.evaluate_position_risk）=====

def _risk_anchor(loss_boundary, entry_prob=0.25, dte=48.0):
    """入场风险锚（最小字段）：触界基线 + 损失边界 + DTE + 入场 gamma。"""
    return {"entry_touch_probability": entry_prob, "entry_probability_confidence": "LOW",
            "entry_loss_boundary": loss_boundary, "entry_dte_hours": dte,
            "entry_short_gamma": 0.00005}


def test_run_cycle_entry_freezes_risk_anchor():
    """入场 _attempt_commit 冻结快照时一并冻结 entry_risk_anchor + short_expiry_ts。"""
    ST = _setup()
    a = ST.run_cycle()
    code = a["pending_candidates"][0]["confirm_code"]
    fmz_shim._commands.append("执行:" + code)
    ST.run_cycle()                                        # 锁定
    orig_live, orig_step = ST._build_precommit_live, ST.exec_entry_campaign_step
    _all_pass_live(ST)
    ST.exec_entry_campaign_step = lambda prot_i, short_i, amount, floor, steps, attempt, pdone, sdone, allow_live, label="entry": {
        "quotes_ok": True, "credit_ok": True, "dry": False, "prot_price": 0.006, "short_price": 0.010,
        "net_credit": 0.0003, "n_used": 0, "prot_fill": amount, "short_fill": amount, "reason": "ENTRY_STEP"}
    ST.ALLOW_ENTRY_TRADING = True
    try:
        c = ST.run_cycle()
    finally:
        ST._build_precommit_live, ST.exec_entry_campaign_step = orig_live, orig_step
        ST.ALLOW_ENTRY_TRADING = False
    snap = c.get("entry_snapshot")
    assert snap and snap.get("entry_risk_anchor")               # 风险锚已冻结
    assert "entry_touch_probability" in snap["entry_risk_anchor"]
    assert snap.get("short_expiry_ts")                          # 短腿到期已冻结（供持仓后 DTE）


def test_run_cycle_risk_severe_prefers_exit_but_unauthorized_holds():
    """高 delta 短腿(触界概率高) → tail_risk_state=EXIT_PREFERRED → 仲裁 preferred=EXIT_PREFERRED；
    未授权 + 退出门关 → executable 回落 HOLD（不空动真实仓）。"""
    import ledger as LG
    ST = _setup()
    LG.ledger_set_state(LG.S_SHORT_ACTIVE_PROTECTED)
    ST._G(ST._POSITION_KEY, {"position_id": "pos-rs", "short_instrument": "BTC-S-74000-C",
                             "side": "CALL", "remaining_short_qty": 0.1, "long_remaining_qty": 0.1,
                             "entry_profit_ceiling_net": 0.002, "max_total_exit_spend": 0.0004,
                             "entry_risk_anchor": _risk_anchor(74000)})
    ctx = ST.run_cycle()
    assert ctx["console_phase"] == "POSITION_MANAGE"
    assert ctx.get("risk_state") == "EXIT_PREFERRED"
    assert ctx["action_arb"]["preferred_action"] == "EXIT_PREFERRED"
    assert ctx["action_arb"]["executable_action"] == "HOLD"      # 未授权 → 不可执行，回落
    assert ST._G(ST._POSITION_KEY)["remaining_short_qty"] == 0.1  # 默认空跑不动仓


def test_run_cycle_risk_mild_holds():
    """低 delta 短腿(触界概率低) → tail_risk_state=NORMAL → exit_preferred/hedge_ready 均假 → HOLD。"""
    import ledger as LG
    ST = _setup()
    LG.ledger_set_state(LG.S_SHORT_ACTIVE_PROTECTED)
    ST._G(ST._POSITION_KEY, {"position_id": "pos-rm", "short_instrument": "BTC-S-80000-C",
                             "side": "CALL", "remaining_short_qty": 0.1, "long_remaining_qty": 0.1,
                             "entry_risk_anchor": _risk_anchor(80000)})
    ctx = ST.run_cycle()
    assert ctx.get("risk_state") == "NORMAL"
    assert ctx["action_arb"]["preferred_action"] == "HOLD"


def test_run_cycle_risk_exit_executes_when_authorized_and_gated():
    """风险主动退出全链路：EXIT_PREFERRED + 风险退出授权 + 退出门开 + 预算/盘口足 →
    executable=EXIT_PREFERRED → 单动作收口买回短腿至归零（非止盈资格驱动）。"""
    import ledger as LG
    import authorization as A
    ST = _setup()
    LG.ledger_set_state(LG.S_SHORT_ACTIVE_PROTECTED)
    ST._G(ST._POSITION_KEY, {"position_id": "pos-rx", "short_instrument": "BTC-S-74000-C",
                             "side": "CALL", "remaining_short_qty": 0.1, "long_remaining_qty": 0.1,
                             "entry_profit_ceiling_net": 0.002, "max_total_exit_spend": 0.0004,
                             "realized_exit_spend": 0.0, "take_profit_target_ratio": 0.80,
                             "entry_risk_anchor": _risk_anchor(74000)})
    # 风险退出授权携带独立预算 max_exit_spend（=RISK_EXIT_MAX_SPEND，需 >0 才可越价退出）
    ST._G(ST._EXIT_AUTH_KEY, A.build_authorization("pos-rx", A.POLICY_RISK_EXIT, 1, max_exit_spend=0.05))
    ST.ALLOW_EXIT_TRADING = True
    seen = {"taker": None}
    orig = ST.exec_exit_buyback_step
    def _fake_exit(inst, amt, cap, allow_live, allow_taker=False, label="exit_short"):
        seen["taker"] = allow_taker
        return {"filled": amt, "avg_price": 0.015, "dry": False, "taker": allow_taker, "reason": "RISK_EXIT_STEP"}
    ST.exec_exit_buyback_step = _fake_exit
    try:
        ctx = ST.run_cycle()
    finally:
        ST.exec_exit_buyback_step = orig
        ST.ALLOW_EXIT_TRADING = False
    assert ctx["action_arb"]["preferred_action"] == "EXIT_PREFERRED"
    assert ctx["action_arb"]["executable_action"] == "EXIT_PREFERRED"
    assert seen["taker"] is True                                     # F1：风险退出越价吃单（非被动）
    assert ST._G(ST._POSITION_KEY)["remaining_short_qty"] == 0.0     # 风险退出已买回归零
    assert LG.ledger_get_state() == LG.S_SHORT_FLAT_LONG_RESIDUAL


def test_run_cycle_risk_hedge_ready_executes_when_gated():
    """HEDGE_READY（需总线 edb/ggr，OFFLINE 不可达 → monkeypatch 风险包）+ 对冲门开 + 无在场对冲 →
    executable=HEDGE_READY → 单动作收口落对冲单。"""
    import ledger as LG
    ST = _setup()
    LG.ledger_set_state(LG.S_SHORT_ACTIVE_PROTECTED)
    ST._G(ST._POSITION_KEY, {"position_id": "pos-rh", "short_instrument": "BTC-S-74000-C",
                             "side": "CALL", "remaining_short_qty": 0.1,
                             "entry_risk_anchor": _risk_anchor(74000)})
    ST.ALLOW_HEDGE_TRADING = True
    called = {"hedge": 0}
    orig_risk, orig_hedge = ST.evaluate_position_risk, ST.exec_hedge_step
    ST.evaluate_position_risk = lambda **kw: {"tail_risk_state": "HEDGE_READY",
                                              "current_risk": {}, "reason_codes": []}
    def _fake_hedge(venue_cfg, side, contracts, reduce_only, allow_live, label="hedge"):
        called["hedge"] += 1
        return {"placed": True, "dry": False}
    ST.exec_hedge_step = _fake_hedge
    try:
        ctx = ST.run_cycle()
    finally:
        ST.evaluate_position_risk, ST.exec_hedge_step = orig_risk, orig_hedge
        ST.ALLOW_HEDGE_TRADING = False
    assert ctx.get("risk_state") == "HEDGE_READY"
    assert ctx["action_arb"]["preferred_action"] == "HEDGE_READY"
    assert ctx["action_arb"]["executable_action"] == "HEDGE_READY"
    assert called["hedge"] == 1                                      # 对冲单已落（单动作收口）


def test_run_cycle_risk_exit_unaffordable_falls_back_to_hedge():
    """F1：风险退出预算不足以越价(ask>cap)→exit 不可执行→仲裁回退对冲(不再卡在挂不上的退出)。"""
    import ledger as LG
    import authorization as A
    ST = _setup()
    LG.ledger_set_state(LG.S_SHORT_ACTIVE_PROTECTED)
    ST._G(ST._POSITION_KEY, {"position_id": "pos-fb", "short_instrument": "BTC-S-74000-C",
                             "side": "CALL", "remaining_short_qty": 0.1, "long_remaining_qty": 0.1,
                             "entry_risk_anchor": _risk_anchor(74000)})
    ST._G(ST._EXIT_AUTH_KEY, A.build_authorization("pos-fb", A.POLICY_RISK_EXIT, 1, max_exit_spend=0.0001))
    ST.ALLOW_EXIT_TRADING = True
    ST.ALLOW_HEDGE_TRADING = True                                    # 对冲门开 → 回退可执行
    called = {"hedge": 0}
    orig = ST.exec_hedge_step
    def _fake_hedge(vcfg, side, contracts, reduce_only, allow_live, label="hedge"):
        called["hedge"] += 1
        return {"filled": contracts, "dry": False, "reason": "HEDGE_STEP"}
    ST.exec_hedge_step = _fake_hedge
    try:
        ctx = ST.run_cycle()
    finally:
        ST.exec_hedge_step = orig
        ST.ALLOW_EXIT_TRADING = False
        ST.ALLOW_HEDGE_TRADING = False
    arb = ctx["action_arb"]
    assert arb["preferred_action"] == "EXIT_PREFERRED"               # 偏退出
    assert arb["executable_action"] == "HEDGE_READY"                 # 但退出越价不可成交 → 回退对冲
    assert arb["blocked_reason"] == "EXIT_NOT_EXECUTABLE"
    assert called["hedge"] == 1
    assert ST._G(ST._POSITION_KEY)["remaining_short_qty"] == 0.1     # 未买回（退出受阻）


def test_run_cycle_risk_data_gap_surfaced_no_action():
    """F3：短腿盘口缺 delta/IV → 风险评估显式数据缺口（不静默判 NORMAL），不驱动主动动作。"""
    import ledger as LG
    ST = _setup()
    LG.ledger_set_state(LG.S_SHORT_ACTIVE_PROTECTED)
    ST._G(ST._POSITION_KEY, {"position_id": "pos-dg", "short_instrument": "BTC-S-76000-C",
                             "side": "CALL", "remaining_short_qty": 0.1, "long_remaining_qty": 0.1,
                             "entry_risk_anchor": _risk_anchor(76000)})
    orig = ST.exec_quote
    ST.exec_quote = lambda inst: {"mark": 0.01, "best_bid": 0.009, "best_ask": 0.011,
                                  "tick": 0.0001, "delta": None, "gamma": None, "mark_iv": None}
    try:
        ctx = ST.run_cycle()
    finally:
        ST.exec_quote = orig
    assert ctx.get("risk_state") is None
    assert (ctx.get("risk_pkg") or {}).get("market_data_gap") is True
    assert ctx["action_arb"]["preferred_action"] == "HOLD"           # 数据缺口不触发主动退出/对冲


def test_run_cycle_orphan_cleanup_bypasses_hedge_gate():
    """C2：裸 perp(short=0,perp≠0)清理为纯降险 reduce_only，perp 已存在=场所已配置 →
    即使 ALLOW_HEDGE_TRADING=False 也自动清理。"""
    import ledger as LG
    ST = _setup()                                                    # ALLOW_HEDGE_TRADING 默认 False
    LG.ledger_set_state(LG.S_SHORT_FLAT_LONG_RESIDUAL)
    ST._G(ST._POSITION_KEY, {"position_id": "pos-orph", "short_instrument": "BTC-S-76000-C",
                             "side": "CALL", "remaining_short_qty": 0.0, "long_remaining_qty": 0.0})
    orig_pos, orig_hedge = ST.dbt_get_positions, ST.exec_hedge_step
    ST.dbt_get_positions = lambda cur, kind="option": ([{"size": 100.0}] if kind == "future" else [])
    seen = {"reduce_only": None, "n": 0}
    def _fake_hedge(vcfg, side, contracts, reduce_only, allow_live, label="hedge"):
        seen["reduce_only"], seen["n"] = reduce_only, seen["n"] + 1
        return {"filled": contracts, "dry": False, "reason": "HEDGE_STEP"}
    ST.exec_hedge_step = _fake_hedge
    try:
        ctx = ST.run_cycle()
    finally:
        ST.dbt_get_positions, ST.exec_hedge_step = orig_pos, orig_hedge
    assert ctx["action_arb"]["executable_action"] == "ORPHAN_HEDGE_EMERGENCY"
    assert seen["n"] == 1 and seen["reduce_only"] is True            # 不受 allow_hedge 阻断、强制 reduce_only


def test_reconcile_mismatch_surfaced_not_blocking():
    """P0①：快照有仓但交易所持仓不符 → reconciled=False 已 surfaced，但仍继续管理(不阻断风险收口)。"""
    import ledger as LG
    ST = _setup()
    LG.ledger_set_state(LG.S_SHORT_ACTIVE_PROTECTED)
    ST._G(ST._POSITION_KEY, {"position_id": "pos-x", "short_instrument": "BTC-S-80000-C",
                             "remaining_short_qty": 0.1, "long_remaining_qty": 0.1})
    ctx = ST.run_cycle()
    assert ctx["console_phase"] == "POSITION_MANAGE"
    assert ctx.get("reconciled") is False
