# -*- coding: utf-8 -*-
"""
主编排 main()（FMZ 入口）。两轮分离，运行后不经界面命令调整计划或仓位。

计划轮 ROUND_MODE="PLAN"：
  枚举所有符合范围(剩余到期/delta/腿宽)的同期垂直备选 → 初筛 top-K → S:PM 模拟 →
  按 胜率/盈亏比/信号 综合排序 → 输出方案库(含方案号+推荐标签)并持久化(_G)。绝不下单。
下单轮 ROUND_MODE="ORDER"：
  读取持久化方案库，按 SELECTED_PLAN 取方案号 → 重新取价+模拟复核 → 仅 ALLOW_TRADING=True 才真实开仓。

约定：本项目内一律用「裸名 + 模块前缀」，合成单文件后位于同一命名空间，bundle 仅剥离项目内 import。
"""

import time

from config import *
from fmz_shim import _G, Log, LogStatus, Sleep, GetCommand
from gates import (gate_summary, gate_decision, ACTION_ENTRY, ACTION_HEDGE_OPEN,
                  ACTION_HEDGE_REDUCE, ACTION_EXIT)
from cmd_router import route_command, cmd_ledger_record
from signal_receiver import receive_signal
from recommend import (build_recommendation_library, resolve_confirm_code,
                       precommit_recheck, evaluate_precommit_checks)
from position import (build_vertical_entry_snapshot, reference_profit_capture_ratio,
                      take_profit_qualified, short_buyback_budget, short_buyback_price_cap,
                      exit_campaign_decision, EXIT_PAUSED_BUDGET, EXIT_PAUSED_DATA,
                      EXIT_WORKING_SHORT, EXIT_WORKING_LONG,
                      entry_campaign_decision, ENTRY_ABANDONED,
                      position_reconcile, protection_recovery_decision)
from authorization import (authorize_from_code, is_authorized, revoke, auth_code,
                          POLICY_TAKE_PROFIT, POLICY_RISK_EXIT)
from hedge import (hedge_target_contracts, hedge_order_action, hedge_orphan,
                  hedge_side, hedge_venue_config, HEDGE_INSTRUMENT)
from binance_io import bnc_get_position_btc
from deribit_io import *
from leg_selection import *
from spm_sim import *
from accounting import *
from plans import *
from ledger import *
from execution import *
from display import *
from hedge_risk import build_entry_risk_anchor
from vrp_gate import apply_vrp_gate
from risk_controls import (evaluate_portfolio_budget, evaluate_projected_budget,
                          unified_action_arbiter)

_MENU_KEY = "spm_plan_menu_v1"
_LAST = {"plan_ms": 0}
# 选用方案明细锁定：启动时锁定一个方案的编号，之后不随方案库刷新而改变（重启复位）
_LOCKED = {"detail_id": None}


def _now_ms():
    return int(time.time() * 1000)


def _spot_price():
    if UNDERLYING_REF_PRICE:
        return UNDERLYING_REF_PRICE
    return dbt_index_price(SETTLEMENT_CURRENCY)


def _delta_lookup():
    cache = {}

    def fn(inst):
        if inst not in cache:
            t = dbt_ticker(inst) or {}
            cache[inst] = (t.get("greeks") or {}).get("delta")
        return cache[inst]
    return fn


def _quote_cache():
    cache = {}

    def fn(inst):
        if inst not in cache:
            cache[inst] = exec_quote(inst)
        return cache[inst]
    return fn


def _first_in_width(prots):
    lo, hi = PROTECTION_WIDTH_RANGE
    for p in prots:
        if lo <= p.get("_width", 1e18) <= hi:
            return p
    return None


# ---------- 计划轮：方案库构建 ----------

def _build_menu(now_ms, spot):
    """枚举同期垂直→初筛→top-K 跑 S:PM→排序。返回 (menu, pm_ok, model, reason, diag)。
    diag = 枚举漏斗计数，用于看清是哪个门控在生效（无候选时尤其有用）。"""
    want_call = legsel_is_call_bias(DIRECTION_BIAS)
    delta_fn, quote_fn = _delta_lookup(), _quote_cache()
    diag = {"短腿扫描": 0, "delta区间外": 0, "无报价/无买盘": 0, "权利金过薄": 0,
            "价差过宽": 0, "无合格保护腿(腿宽内)": 0, "生成候选": 0, "进入菜单": 0, "合格": 0}
    instruments = dbt_get_instruments(SETTLEMENT_CURRENCY, "option")
    if not instruments:
        return [], False, None, "NO_INSTRUMENTS", diag
    short_exps = legsel_expiries_in_band(instruments, SHORT_DTE_HOURS[0], SHORT_DTE_HOURS[1],
                                         now_ms, want_call)
    if not short_exps:
        return [], False, None, "NO_SHORT_EXPIRY_IN_BAND", diag
    pm_ok, model = spm_account_is_portfolio_margin(dbt_account_summary(SETTLEMENT_CURRENCY))
    pref = plan_preferred_delta(SIGNAL_STATE, SIGNAL_CONFIDENCE, SHORT_DELTA_RANGE)
    dmin, dmax = SHORT_DELTA_RANGE

    prelim = []
    for s_exp, s_insts in short_exps.items():
        s_dte_h = legsel_dte_hours(s_exp, now_ms)
        for short in legsel_short_enriched(s_insts, spot, want_call, delta_fn):
            diag["短腿扫描"] += 1
            if not (dmin <= abs(short["_delta"]) <= dmax):
                diag["delta区间外"] += 1
                continue
            sq = quote_fn(short["instrument_name"])
            if not sq or sq.get("best_bid") in (None, 0) or sq.get("mark") is None:
                diag["无报价/无买盘"] += 1
                continue
            if sq["mark"] < MIN_SHORT_PREMIUM:
                diag["权利金过薄"] += 1
                continue
            ssr = exec_spread_ratio(sq)
            if ssr is not None and ssr > MAX_SPREAD_RATIO:
                diag["价差过宽"] += 1
                continue
            # 同期垂直：保护腿取同到期、更价外、腿宽达标者；长腿是定额风险封顶，
            # 便宜的 OTM 长腿正是所需 → **不套用过度虚值过滤**
            vprot = _first_in_width(legsel_protection_candidates(
                s_insts, short["strike"], want_call, PROTECTION_WIDTH_RANGE,
                None, 0.0))
            if not vprot:
                diag["无合格保护腿(腿宽内)"] += 1
                continue
            pq = quote_fn(vprot["instrument_name"])
            if not pq or pq.get("mark") is None:
                continue
            c = plan_assemble(ORDER_AMOUNT, spot, MIN_MARGIN_RELIEF_RATIO, pref,
                              want_call, short, sq, vprot, pq,
                              None, pm_ok, model, s_dte_h, s_dte_h)
            c["_re"] = {"short": short, "sq": sq, "prot": vprot, "pq": pq,
                        "s_dte": s_dte_h, "p_dte": s_dte_h}
            prelim.append(c)
            diag["生成候选"] += 1

    if not prelim:
        return [], pm_ok, model, "NO_CANDIDATE", diag
    prelim.sort(key=lambda c: plan_prelim_score(c, PLAN_WEIGHTS), reverse=True)
    topk = prelim[:max(MENU_SIZE * 2, MENU_SIZE)]

    final = []
    for c in topk:                                    # 仅对 top-K 跑 S:PM（控制 API 调用）
        re = c["_re"]
        spm = spm_simulate_structure(SETTLEMENT_CURRENCY, re["short"]["instrument_name"],
                                     re["prot"]["instrument_name"], ORDER_AMOUNT)
        final.append(plan_assemble(
            ORDER_AMOUNT, spot, MIN_MARGIN_RELIEF_RATIO, pref,
            want_call, re["short"], re["sq"], re["prot"], re["pq"], spm, pm_ok, model,
            re["s_dte"], re["p_dte"]))
    menu = plan_rank(final, PLAN_WEIGHTS, MENU_SIZE)
    diag["进入菜单"] = len(menu)
    diag["合格"] = sum(1 for c in menu if c.get("qualified"))
    return menu, pm_ok, model, "OK", diag


# ---------- ctx 组装 ----------

def _ctx_base(state, spot, reason=None):
    return {
        "version": STRATEGY_VERSION,
        "currency": SETTLEMENT_CURRENCY, "signal_state": SIGNAL_STATE,
        "direction_bias": DIRECTION_BIAS, "allow_trading": ALLOW_TRADING,
        "round_mode": ROUND_MODE, "signal_confidence": SIGNAL_CONFIDENCE,
        "state": state,
        "max_chase_steps": MAX_CHASE_STEPS, "min_required_ratio": MIN_MARGIN_RELIEF_RATIO,
        "reason": reason, "spot": spot, "amount": ORDER_AMOUNT,
        "selected_plan": SELECTED_PLAN, "protection_mode": None,
    }


def _flat_plan_fields(p):
    return dict(
        short_instrument=p["short_instrument"], short_strike=p["short_strike"],
        short_dte_hours=p["short_dte_hours"], short_mark=p["short_mark"],
        short_bid=p["short_bid"], short_ask=p["short_ask"], short_tick=p["short_tick"],
        short_delta=p["short_delta"],
        protection_instrument=p["protection_instrument"], protection_strike=p["protection_strike"],
        protection_dte_days=p["protection_dte_days"], protection_mark=p["protection_mark"],
        protection_bid=p["protection_bid"], protection_ask=p["protection_ask"],
        protection_tick=p["protection_tick"], protection_delta=p["protection_delta"],
        im_short_only=p["im_short_only"], im_with_protection=p["im_with_protection"],
        margin_relief_abs=p["margin_relief_abs"], margin_relief_ratio=p["margin_relief_ratio"],
        pm_accepted=p["pm_ok"], account_margin_model=p["account_model"],
        short_premium_income=p["premium_income"], estimated_entry_fee=p["entry_fee"],
        estimated_spread_cost=p["spread_cost"], protection_entry_cost=p["protection_premium"],
        full_burn_cost=p["full_burn"],
        win_rate=p["win_rate"], net_credit=p["net_credit_effective"],
        net_credit_single=p["net_credit_single"], max_loss=p["max_loss"], rr=p["rr"],
        ev=p.get("ev"),
        covered_cycles=p["covered_cycles"], residual_value=p["residual_value"],
        amortized_cost_per_cycle=p["amortized_cost_per_cycle"],
        protection_mode=p["mode"], protection_mode_cn=p["mode_cn"], plan_tags=p.get("tags"),
        selected_id=p.get("id"),
        short_expiry_label=p.get("short_expiry_label"),
        protection_expiry_label=p.get("protection_expiry_label"),
        protection_dte_hours=p.get("protection_dte_hours"),
        breakeven=p.get("breakeven"), credit_on_margin=p.get("credit_on_margin"),
    )


def _ctx_with_menu(state, spot, reason, menu, selected_no, detail_plan):
    ctx = _ctx_base(state, spot, reason)
    ctx["menu"] = menu
    ctx["selected_plan"] = selected_no
    if detail_plan:
        ctx.update(_flat_plan_fields(detail_plan))
    return ctx


def _emit(ctx, note=""):
    LogStatus(disp_status_panel(ctx, note))
    Log(disp_log_summary(ctx, note))


# ---------- 计划轮 ----------

def _plan_round(spot):
    now_ms = _now_ms()
    if not spot:
        return _ctx_base(S_NO_POSITION, spot, "NO_SPOT")
    menu, pm_ok, model, reason, diag = _build_menu(now_ms, spot)
    if reason != "OK" or not menu:
        ctx = _ctx_base(S_NO_POSITION, spot, reason)
        ctx["enum_diag"] = diag                       # 无候选时也展示漏斗，便于定位门控
        return ctx
    _G(_MENU_KEY, menu)                               # 持久化方案库供下单轮按【编号】取用
    # 锁定「选用方案明细」：启动时取最推荐(SELECTED_PLAN 有效则用，否则综合分#1)，
    # 之后不随方案库刷新而改变；仅当锁定项掉出方案库时才重新锁定。
    ids = [c["id"] for c in menu]
    if _LOCKED["detail_id"] not in ids:
        _LOCKED["detail_id"] = SELECTED_PLAN if SELECTED_PLAN in ids else menu[0]["id"]
    detail = next((c for c in menu if c["id"] == _LOCKED["detail_id"]), menu[0])
    r = "PLAN_MENU_READY"
    if SIGNAL_STATE not in ENTER_SIGNALS:
        r = "PLAN_MENU_READY(注意:当前信号 %s 不放行进场)" % SIGNAL_STATE
    ctx = _ctx_with_menu(S_PROTECTION_SELECTION, spot, r, menu, _LOCKED["detail_id"], detail)
    ctx["enum_diag"] = diag
    return ctx


# ---------- 整合 PLAN 通顺缝（R6）：真实菜单 → VRP 双门 → 组合硬预算 ----------

def integrated_plan_preview(spot, market_context=None, portfolio_state=None):
    """整合执行流的 PLAN 段（执行会话式）：真实 _build_menu → VRP 双门过滤(给 market_context 时)
    → 组合硬预算(给 portfolio_state 时) → 返回可锁定方案 + 各域裁决。

    main() 在拿到实时 IV/RV(market_context) 与组合状态后调用本函数；选中方案的会话锁定/授权
    (ExecutionSession+ApprovalIntent，plan_hash+TTL) 与 FMZ 命令栏交互在上线 spike 接入。
    边界：VRP/预算**只过滤**，不进 PLAN_WEIGHTS、不判方向、不解 ALLOW_TRADING。"""
    now_ms = _now_ms()
    menu, pm_ok, model, reason, diag = _build_menu(now_ms, spot)
    out = {"reason": reason, "menu": menu, "enum_diag": diag, "pm_ok": pm_ok,
           "vrp_passed": None, "vrp_blocked": None, "portfolio_budget": None,
           "lockable": list(menu or [])}
    if reason != "OK" or not menu:
        out["lockable"] = []
        return out
    # PRICE_GATE：VRP 双门（独立 AND 硬门；BLOCK 不进可锁定方案）
    if market_context:
        passed, blocked = apply_vrp_gate(menu, market_context)
        out["vrp_passed"] = [p for p, _g in passed]
        out["vrp_blocked"] = [{"id": p.get("id"), "reason_codes": g["reason_codes"]}
                              for p, g in blocked]
        out["lockable"] = list(out["vrp_passed"])
    # 组合硬预算（缺口2，入场前额外 AND 门；占位安全：超即 size=0 → 无可锁定）
    if portfolio_state:
        budget = evaluate_portfolio_budget(
            portfolio_state.get("current", {}), portfolio_state.get("limits", {}),
            portfolio_state.get("proposed_size", ORDER_AMOUNT))
        out["portfolio_budget"] = budget
        if budget["decision"] == "BLOCK":
            out["lockable"] = []
    return out


# ---------- 下单轮 ----------

def _run_order(led, spot):
    menu = _G(_MENU_KEY) or []
    if not menu:
        return S_NO_POSITION, _ctx_base(S_NO_POSITION, spot, "NO_PLAN_MENU(请先运行计划轮)")
    sel = next((c for c in menu if c.get("id") == SELECTED_PLAN), None)
    if not sel:
        return S_NO_POSITION, _ctx_with_menu(S_NO_POSITION, spot,
                                             "PLAN_ID_NOT_IN_MENU:%s" % SELECTED_PLAN, menu, SELECTED_PLAN, None)

    # 复核：重新取价 + 重新模拟 S:PM
    want_call = legsel_is_call_bias(DIRECTION_BIAS)
    pref = plan_preferred_delta(SIGNAL_STATE, SIGNAL_CONFIDENCE, SHORT_DELTA_RANGE)
    short = {"instrument_name": sel["short_instrument"], "strike": sel["short_strike"],
             "expiration_timestamp": sel.get("short_expiry"), "_delta": sel.get("short_delta")}
    prot = {"instrument_name": sel["protection_instrument"], "strike": sel["protection_strike"],
            "expiration_timestamp": sel.get("protection_expiry")}
    sq, pq = exec_quote(short["instrument_name"]), exec_quote(prot["instrument_name"])
    spm = spm_simulate_structure(SETTLEMENT_CURRENCY, short["instrument_name"],
                                 prot["instrument_name"], ORDER_AMOUNT)
    pm_ok, model = spm_account_is_portfolio_margin(dbt_account_summary(SETTLEMENT_CURRENCY))
    rv = plan_assemble(ORDER_AMOUNT, spot, MIN_MARGIN_RELIEF_RATIO, pref,
                       want_call, short, sq, prot, pq, spm, pm_ok, model,
                       sel.get("short_dte_hours"), sel.get("protection_dte_hours"))
    ctx = _ctx_with_menu(S_SPM_SIMULATION, spot, None, menu, SELECTED_PLAN, rv)
    # 「将下达订单」意图（保护腿优先在前）
    ctx["order_intent"] = [
        dict(leg="保护腿", **exec_plan_prices("buy", prot["instrument_name"], ORDER_AMOUNT)),
        dict(leg="卖方腿", **exec_plan_prices("sell", short["instrument_name"], ORDER_AMOUNT)),
    ]

    if not rv["qualified"]:
        ctx["reason"] = "PLAN_NOT_QUALIFIED:" + (rv.get("reject_reason") or "")
        return S_NO_POSITION, ctx

    result = exec_open_structure(short["instrument_name"], prot["instrument_name"], ORDER_AMOUNT)
    if result.get("dry"):
        ctx["reason"] = "ORDER_PREVIEW_DRY"
        return S_NO_POSITION, ctx

    prot_fill = result.get("protection_fill") or {}
    filled_prot = prot_fill.get("filled", 0.0)
    if filled_prot <= 0:
        ctx["reason"] = "PROTECTION_NOT_FILLED"
        return S_NO_POSITION, ctx

    inv = ledger_make_inventory(
        prot["instrument_name"], "CALL" if want_call else "PUT", prot["strike"],
        prot.get("expiration_timestamp"), filled_prot, prot_fill.get("avg_price", 0.0),
        acct_option_fee_ccy(prot_fill.get("avg_price", 0.0), filled_prot),
        rv.get("margin_relief_ratio"))
    inv["mode"] = sel["mode"]
    led["protection"] = inv

    short_fill = result.get("short_fill") or {}
    filled_short = short_fill.get("filled", 0.0)
    if filled_short > 0:
        ledger_allocate_short(led, filled_short)
        entry_anchor = build_entry_risk_anchor(
            DIRECTION_BIAS, spot, sel.get("short_dte_hours"),
            rv.get("short_delta"), sq.get("gamma"), sq.get("mark_iv"),
            rv.get("breakeven"), SIGNAL_STATE, "UNKNOWN")
        led["short"] = {"instrument": short["instrument_name"], "strike": short["strike"],
                        "amount": filled_short, "expiry": short.get("expiration_timestamp"),
                        "avg_price": short_fill.get("avg_price"), "plan_id": SELECTED_PLAN,
                        "entry_risk_anchor": entry_anchor}
        ledger_save(led)
        ctx["reason"] = "STRUCTURE_OPEN"
        return S_SHORT_ACTIVE_PROTECTED, ctx

    ledger_save(led)
    ctx["reason"] = "PROTECTION_ACTIVE_NO_SHORT"
    return S_PROTECTION_ACTIVE_NO_SHORT, ctx


def _order_loop(spot):
    led = ledger_load()
    state = ledger_get_state()
    if SIGNAL_STATE in EXIT_REVIEW_SIGNALS:                          # §9.2
        ctx = _ctx_base(S_EXIT_OR_WAIT_REVIEW, spot, "EXIT_REVIEW_SIGNAL:" + SIGNAL_STATE)
        ledger_set_state(S_EXIT_OR_WAIT_REVIEW)
        _emit(ctx, "下单轮·退出/复核信号")
    elif state == S_SHORT_ACTIVE_PROTECTED:                          # §4.2/§9.1
        ctx = _ctx_base(state, spot, "SAME_DIRECTION_CONFIRMATION")
        _emit(ctx, "下单轮·持仓中(同向不加仓)")
    elif KILL_SWITCH or not ledger_can_enter(SIGNAL_STATE, ENTER_SIGNALS) \
            or state not in (S_NO_POSITION, S_SIGNAL_READY):
        ctx = _ctx_base(state, spot, "IDLE(kill=%s,signal=%s)" % (KILL_SWITCH, SIGNAL_STATE))
        _emit(ctx, "下单轮·空闲")
    else:
        new_state, ctx = _run_order(led, spot)
        if new_state != S_NO_POSITION:
            ledger_set_state(new_state)
        _emit(ctx, "下单轮" + (" [空跑预览]" if not ALLOW_TRADING else ""))


# ========== E2：单一持续主链 run_cycle（取代 PLAN/ORDER 双脚本；main() 于 E2.3 切换）==========

_SESSION_KEY = "spm_session_id_v1"
_REFRESH_KEY = "spm_refresh_seq_v1"
_LIB_KEY = "spm_reco_lib_v1"
_LOCKED_KEY = "spm_locked_plan_v1"
_RUNTIME_KILL_KEY = "spm_runtime_kill_v1"
_LIB_BUILD_TS_KEY = "spm_lib_build_ts_v1"


def _session_id():
    sid = _G(_SESSION_KEY)
    if not sid:
        sid = "sess-%d" % _now_ms()
        _G(_SESSION_KEY, sid)
    return sid


def _refresh_seq():
    return int(_G(_REFRESH_KEY) or 0)


def _bump_refresh_seq():
    n = _refresh_seq() + 1
    _G(_REFRESH_KEY, n)
    return n


def _effective_kill():
    """配置急停 KILL_NEW_RISK 或运行时【急停】命令（_G）任一为真即急停。"""
    return bool(KILL_NEW_RISK) or bool(_G(_RUNTIME_KILL_KEY))


def _gate_summary_now():
    return gate_summary(ALLOW_ENTRY_TRADING, ALLOW_EXIT_TRADING, ALLOW_HEDGE_TRADING,
                        _effective_kill(), EMERGENCY_REDUCE_ONLY)


def _signal_allows_entry(verdict):
    if (verdict or {}).get("availability") == "OFFLINE_MANUAL":
        return SIGNAL_STATE in ENTER_SIGNALS          # 离线手动：静态信号决定
    return bool((verdict or {}).get("tradeable")) and not (verdict or {}).get("block_new_opens")


def _has_position(state):
    return state in (S_SHORT_ACTIVE_PROTECTED, S_PROTECTION_ACTIVE_NO_SHORT,
                     S_SHORT_FLAT_LONG_RESIDUAL)


def _handle_execute(code, now_ms):
    """硬授权：在当前推荐库按确认码定位冻结快照 → 锁定不可变副本。
    预提交 13 项硬门与受控真实开仓由后续每轮 _attempt_commit 评估（见 E3.4）。"""
    lib = _G(_LIB_KEY)
    snap = resolve_confirm_code(lib, code)
    if not snap:
        return "confirm_code_invalid_or_stale"
    locked = dict(snap)
    locked["locked_ts"] = now_ms
    _G(_LOCKED_KEY, locked)
    return "locked"


def _handle_command(ctype, cmd, now_ms):
    if ctype == "KILL":
        _G(_RUNTIME_KILL_KEY, True)
        return "killed_new_risk"
    if ctype == "RESUME":
        _G(_RUNTIME_KILL_KEY, None)
        _G(_LOCKED_KEY, None)                          # 恢复要求重新对账 + 新的计划硬批准
        return "resumed_requires_new_plan_approval"
    if ctype == "REJECT":
        _G(_LOCKED_KEY, None)
        return "rejected_back_to_wait"
    if ctype == "EXECUTE":
        return _handle_execute(cmd.get("arg"), now_ms)
    if ctype == "EXIT_AUTHORIZE":
        return _handle_exit_authorize(cmd.get("arg"), now_ms, POLICY_TAKE_PROFIT)
    if ctype == "RISK_EXIT_AUTHORIZE":
        return _handle_exit_authorize(cmd.get("arg"), now_ms, POLICY_RISK_EXIT)
    if ctype == "EXIT_REVOKE":
        return _handle_exit_revoke(now_ms)
    return "noop"


def _handle_exit_authorize(code, now_ms, policy):
    """软授权：校验授权码与当前 position+policy 匹配 → 落 _G（与 position_id 绑定，非阻塞）。"""
    snap = _G(_POSITION_KEY)
    pos_id = (snap or {}).get("position_id")
    if not pos_id:
        return "no_position_to_authorize"
    kw = {}
    if policy == POLICY_RISK_EXIT:
        kw = {"max_exit_spend": RISK_EXIT_MAX_SPEND, "allowed_order_types": ["post_only"]}
    auth = authorize_from_code(code, pos_id, policy, now_ms, **kw)
    if not auth:
        return "auth_code_invalid"
    _G(_EXIT_AUTH_KEY, auth)
    return "authorized:" + policy


def _handle_exit_revoke(now_ms):
    auth = _G(_EXIT_AUTH_KEY)
    if not auth:
        return "no_authorization"
    _G(_EXIT_AUTH_KEY, revoke(auth, now_ms))
    return "revoked"


def _dispatch_command(raw, meta, now_ms):
    """轮询并分发一条 FMZ 命令；全部入命令账本审计，消费型严格幂等。"""
    res = route_command(raw, meta, now_ms)
    status, cmd = res["status"], res["command"]
    if status == "EMPTY":
        return {"action": None, "status": status}
    if status == "UNKNOWN":
        cmd_ledger_record(cmd, None, "UNKNOWN", "ignored", now_ms)
        return {"action": "UNKNOWN", "status": status}
    if status == "DUPLICATE":
        cmd_ledger_record(cmd, res["key"], "DUPLICATE", "ignored", now_ms)
        return {"action": cmd["type"], "status": status, "outcome": "duplicate_ignored"}
    outcome = _handle_command(cmd["type"], cmd, now_ms)
    cmd_ledger_record(cmd, res["key"], "ACCEPTED", outcome, now_ms)
    return {"action": cmd["type"], "status": status, "outcome": outcome}


_POSITION_KEY = "spm_entry_snapshot_v1"      # 冻结的 VerticalEntrySnapshot


def _current_portfolio():
    """当前组合风险载荷（E3：无并发持仓时为空；E4 接入真实多仓汇总）。"""
    return {"open_positions": 0, "short_gamma": 0.0, "short_vega": 0.0, "margin_used": 0.0}


def _build_precommit_live(locked, spot, verdict):
    """预取实时复核数据供 evaluate_precommit_checks。
    VRP 需 market_context（总线模式）；OFFLINE 无 → vrp_pass=None（fail-closed：不真实成交，仅空跑预览）。"""
    short_i = locked.get("short_instrument")
    long_i = locked.get("long_instrument")
    amount = locked.get("amount") or ORDER_AMOUNT
    sq, lq = exec_quote(short_i), exec_quote(long_i)
    quotes_fresh = bool(sq and lq and sq.get("mark") is not None and lq.get("mark") is not None
                        and sq.get("best_bid") not in (None, 0) and lq.get("best_ask") not in (None, 0))
    ssr, lsr = exec_spread_ratio(sq), exec_spread_ratio(lq)
    spread_ok = (ssr is not None and lsr is not None
                 and ssr <= MAX_SPREAD_RATIO and lsr <= MAX_SPREAD_RATIO)
    net_credit = fee_reserve = None
    if quotes_fresh:
        fee_reserve = (acct_option_fee_ccy(sq["mark"], amount)
                       + acct_option_fee_ccy(lq["mark"], amount))
        net_credit = (sq["mark"] - lq["mark"]) * amount - fee_reserve
    spm = spm_simulate_structure(SETTLEMENT_CURRENCY, short_i, long_i, amount)
    relief = (spm or {}).get("relief_ratio")
    proposed = {
        "short_gamma": (sq or {}).get("gamma") or 0.0,
        "short_vega": 0.0,                       # vega 待 Greeks 接入（E6/E7）
        "structure_margin": (spm or {}).get("im_with_protection"),
        "max_spread_loss": locked.get("max_loss"),
        "hedge_margin_reserve": 0.0,             # E7 接对冲保证金估算
        "fee_reserve": fee_reserve,
    }
    budget = evaluate_projected_budget(proposed, _current_portfolio(), PORTFOLIO_LIMITS)
    rec = ledger_reconcile(SETTLEMENT_CURRENCY)
    reconciled = (rec.get("actual") == rec.get("expected"))
    sig_pkg = verdict.get("package_id") or ("manual:" + str(SIGNAL_STATE))
    return {
        "signal_fresh": verdict.get("availability") in ("OK", "OFFLINE_MANUAL"),
        "sig_package_id": sig_pkg,
        "same_expiry": plan_expiry_label(short_i) == plan_expiry_label(long_i),
        "vrp_pass": None,                        # OFFLINE 无 market_context（总线模式接入 VRP 双门）
        "spm_relief": relief, "min_relief": MIN_MARGIN_RELIEF_RATIO,
        "quotes_fresh": quotes_fresh,
        "net_credit_after_costs": net_credit,
        "projected_budget_decision": budget.get("decision"),
        "ledger_reconciled": reconciled,
        "no_unknown_orders": True,               # 真实活动订单查询 E4 接入
        "spread_ok": spread_ok,
        "_budget": budget,
    }


def _attempt_commit(locked, spot, verdict, now_ms):
    """锁定方案 → 预提交 13 项 → **开仓活动(entry campaign)**：信用底线内 maker、保护腿先成交、
    **跨轮持久重挂**（替代一次性追价）。预提交不过/门控关 → 仅空跑预览；两腿成交达标 → 冻结入场快照；
    超 ENTRY_MAX_ATTEMPTS 仍未成交 → 放弃(撤/回退保护腿残量、清锁回等待)。低成本 ∧ 提高成功率。"""
    lib = _G(_LIB_KEY)
    live = _build_precommit_live(locked, spot, verdict)
    pre = evaluate_precommit_checks(locked, lib, live)
    amount = locked.get("amount") or ORDER_AMOUNT
    short_i, long_i = locked.get("short_instrument"), locked.get("long_instrument")
    prog = dict(locked.get("entry") or {"prot_done": 0.0, "short_done": 0.0, "attempts": 0,
                                        "prot_cost": 0.0, "short_credit": 0.0})
    result = {"precommit": pre, "budget": live.get("_budget"), "committed": False,
              "entry_snapshot": None, "entry_state": None, "net_credit": None, "reason": None,
              "order_intent": [
                  dict(leg="保护腿", **exec_plan_prices("buy", long_i, amount)),
                  dict(leg="卖方腿", **exec_plan_prices("sell", short_i, amount))]}
    if not pre["passed"]:
        result["reason"] = "PRECOMMIT_FAILED:" + ",".join(pre["failed"])
        return result
    gate = gate_decision(ACTION_ENTRY, ALLOW_ENTRY_TRADING, ALLOW_EXIT_TRADING,
                         ALLOW_HEDGE_TRADING, _effective_kill(), EMERGENCY_REDUCE_ONLY)
    step = exec_entry_campaign_step(long_i, short_i, amount, ENTRY_MIN_NET_CREDIT,
                                    ENTRY_MAX_TICK_STEPS, prog["attempts"],
                                    prog["prot_done"], prog["short_done"],
                                    allow_live=gate["allowed"], label="entry")
    result["net_credit"] = step.get("net_credit")
    decision = entry_campaign_decision(
        True, step.get("quotes_ok"), step.get("credit_ok"), prog["attempts"], ENTRY_MAX_ATTEMPTS,
        prog["prot_done"] >= amount - 1e-12, prog["short_done"] >= amount - 1e-12)
    result["entry_state"] = decision["state"]
    if gate["allowed"] and not step.get("dry"):                  # 仅门开且真实下单时累计/计尝试
        pf, sf = (step.get("prot_fill") or 0.0), (step.get("short_fill") or 0.0)
        prog["prot_done"] = min(amount, prog["prot_done"] + pf)
        prog["short_done"] = min(prog["prot_done"], prog["short_done"] + sf)
        prog["prot_cost"] += pf * (step.get("prot_price") or 0.0)
        prog["short_credit"] += sf * (step.get("short_price") or 0.0)
        prog["attempts"] += 1
        locked["entry"] = prog
        _G(_LOCKED_KEY, locked)
    if prog["prot_done"] >= amount - 1e-12 and prog["short_done"] >= amount - 1e-12:
        avg_prot = (prog["prot_cost"] / prog["prot_done"]) if prog["prot_done"] > 0 else step.get("prot_price")
        avg_short = (prog["short_credit"] / prog["short_done"]) if prog["short_done"] > 0 else step.get("short_price")
        entry_fees = (acct_option_fee_ccy(avg_short or 0.0, prog["short_done"])
                      + acct_option_fee_ccy(avg_prot or 0.0, prog["prot_done"]))
        snap = build_vertical_entry_snapshot(
            locked, {"filled": prog["short_done"], "avg_price": avg_short},
            {"filled": prog["prot_done"], "avg_price": avg_prot}, entry_fees, now_ms)
        _G(_POSITION_KEY, snap)
        _G(_LOCKED_KEY, None)
        ledger_set_state(S_SHORT_ACTIVE_PROTECTED)
        result.update({"committed": True, "entry_snapshot": snap, "reason": "STRUCTURE_OPEN"})
        return result
    if decision["state"] == ENTRY_ABANDONED:                     # 超额度未成交 → 撤/回退保护腿残量
        if gate["allowed"] and prog["prot_done"] > 0 and UNWIND_PROTECTION_ON_NO_SHORT:
            exec_maker_only_fill("sell", long_i, prog["prot_done"], label="entry_unwind")
        _G(_LOCKED_KEY, None)
        result["reason"] = "ENTRY_ABANDONED:" + decision["reason"]
        return result
    result["reason"] = decision["state"] + (":dry" if step.get("dry") else "")
    return result


_RECOVERY_KEY = "spm_recovery_verdict_v1"
_EXIT_AUTH_KEY = "spm_exit_auth_v1"          # E5 软授权（此处先占位读取）


def _recovery_verdict():
    return _G(_RECOVERY_KEY) or {"state": "OK", "allow_new_open": True}


def startup_recovery_check(currency):
    """启动恢复：读交易所真实期权/永续持仓 + 账本短腿 → 裁决并落 _G（恢复完成前禁开新仓）。"""
    opt = dbt_get_positions(currency, "option") or []
    try:
        perp = dbt_get_positions(currency, "future") or []
    except Exception:
        perp = []
    perp_qty = sum(abs(p.get("size") or 0.0) for p in perp)
    led = ledger_load()
    short_qty = (led.get("short") or {}).get("amount") or 0.0
    verdict = evaluate_startup_recovery(opt, perp_qty, short_qty, active_orders=[])
    _G(_RECOVERY_KEY, verdict)
    return verdict


def _evaluate_take_profit(snap):
    """据入场快照 + 实时短腿盘口算止盈资格(参考捕获率) 与退出预算/价格上限。保护腿价值不入分母。"""
    if not snap:
        return {"ratio": None, "qualified": False, "remaining_short_qty": 0.0,
                "remaining_budget": None, "price_cap": 0.0, "quote_ok": False}
    rem_qty = snap.get("remaining_short_qty") or 0.0
    q = exec_quote(snap.get("short_instrument"))
    quote_ok = bool(q and q.get("mark") is not None and q.get("best_bid") not in (None, 0)
                    and q.get("best_ask") is not None)
    ceiling = snap.get("entry_profit_ceiling_net")
    max_spend = snap.get("max_total_exit_spend")
    realized = snap.get("realized_exit_spend") or 0.0
    cons_ref = (q["mark"] * rem_qty) if (quote_ok and rem_qty) else None
    est_fee = acct_option_fee_ccy(q["mark"], rem_qty) if quote_ok else None
    reserve = (max_spend * EXIT_RESERVE_RATIO) if isinstance(max_spend, (int, float)) else None
    ratio = reference_profit_capture_ratio(ceiling, cons_ref, est_fee, reserve)
    qualified = take_profit_qualified(ratio, snap.get("take_profit_target_ratio") or 0.80)
    fee_reserve = reserve or 0.0
    rem_budget = short_buyback_budget(max_spend, realized, fee_reserve)
    tick = (q or {}).get("tick") or 0.0
    cap = short_buyback_price_cap(rem_budget, fee_reserve, rem_qty, tick) if rem_budget else 0.0
    return {"ratio": ratio, "qualified": qualified, "remaining_short_qty": rem_qty,
            "remaining_budget": rem_budget, "price_cap": cap, "quote_ok": quote_ok}


def _apply_exit_fill(snap, step, now_ms):
    """把一次短腿买回成交计入入场快照：减剩余短腿、加已用退出支出；归零则转 SHORT_FLAT_LONG_RESIDUAL。"""
    filled = step.get("filled") or 0.0
    price = step.get("avg_price") or step.get("price") or 0.0
    fee = acct_option_fee_ccy(price, filled)
    snap["remaining_short_qty"] = max(0.0, (snap.get("remaining_short_qty") or 0.0) - filled)
    snap["realized_exit_spend"] = (snap.get("realized_exit_spend") or 0.0) + price * filled + fee
    snap["last_exit_ts"] = now_ms
    if snap["remaining_short_qty"] <= 1e-12:
        ledger_set_state(S_SHORT_FLAT_LONG_RESIDUAL)   # 短腿归零，转保护腿回收（不可直跳 CLOSED）
    _G(_POSITION_KEY, snap)


def _evaluate_hedge(snap):
    """对冲决策（场所感知）：按 HEDGE_VENUE 选 Deribit(反向) 或 Binance(线性) → perp 真实持仓 +
    目标(随剩余短腿敞口) + open/reduce 动作 + 孤儿。默认不真实下单。"""
    rem_qty = (snap or {}).get("remaining_short_qty") or 0.0
    vcfg = hedge_venue_config(HEDGE_VENUE, HEDGE_BINANCE_INSTRUMENT, HEDGE_BINANCE_MAKER_ONLY)
    state = "SETTLED" if rem_qty <= 0 else "OPEN"
    delta = (exec_quote((snap or {}).get("short_instrument")) or {}).get("delta") if snap else None
    if vcfg["venue"] == "BINANCE":
        perp_qty = bnc_get_position_btc(vcfg["instrument"])
        contract_size, min_trade = 1.0, HEDGE_BINANCE_MIN_TRADE
    else:
        try:
            perp = dbt_get_positions(SETTLEMENT_CURRENCY, "future") or []
        except Exception:
            perp = []
        perp_qty = sum((p.get("size") or 0.0) for p in perp)
        meta = dbt_get_instrument(vcfg["instrument"]) or {}
        contract_size = meta.get("contract_size") or HEDGE_CONTRACT_SIZE_FALLBACK
        min_trade = meta.get("min_trade_amount") or HEDGE_MIN_TRADE_FALLBACK
    target = hedge_target_contracts(rem_qty, delta, HEDGE_REDUCTION_RATIO, _spot_price(),
                                    contract_size, min_trade, state, linear=vcfg["linear"])
    return {"perp_qty": perp_qty, "target": target,
            "action": hedge_order_action(perp_qty, target, min_trade),
            "orphan": hedge_orphan(rem_qty, perp_qty),
            "side": hedge_side((snap or {}).get("side")),
            "venue": vcfg["venue"], "instrument": vcfg["instrument"], "venue_cfg": vcfg}


def manage_cycle(now_ms):
    """持仓管理一轮（设计稿 §9.1 的 10 域）：对账 + 止盈资格 + 退出活动(受授权+预算+门控) + 四输出仲裁。
    退出真实买回受 ALLOW_EXIT_TRADING 门控，默认空跑预览；对冲真实执行 E7 接入。"""
    snap = _G(_POSITION_KEY)
    pos_id = (snap or {}).get("position_id")
    rec = ledger_reconcile(SETTLEMENT_CURRENCY)
    recovery = _recovery_verdict()
    auth = _G(_EXIT_AUTH_KEY)
    authorized = is_authorized(auth, pos_id, now_ms)
    tp_code = auth_code(pos_id, POLICY_TAKE_PROFIT) if pos_id else None

    tp = _evaluate_take_profit(snap)
    exit_gate = gate_decision(ACTION_EXIT, ALLOW_ENTRY_TRADING, ALLOW_EXIT_TRADING,
                              ALLOW_HEDGE_TRADING, _effective_kill(), EMERGENCY_REDUCE_ONLY)["allowed"]
    decision = exit_campaign_decision(authorized, tp["qualified"], tp["remaining_short_qty"],
                                      tp["remaining_budget"], tp["quote_ok"], tp["price_cap"])
    exit_state = decision["state"]
    if decision["can_order"] and exit_gate and snap:        # 受控真实买回（默认门关→不进入）
        step = exec_exit_buyback_step(snap.get("short_instrument"), tp["remaining_short_qty"],
                                      tp["price_cap"], allow_live=True, label="exit_short")
        if not step.get("dry") and (step.get("filled") or 0) > 0:
            _apply_exit_fill(snap, step, now_ms)
            snap = _G(_POSITION_KEY)

    hedge = _evaluate_hedge(snap)
    hedge_exec = gate_decision(ACTION_HEDGE_OPEN, ALLOW_ENTRY_TRADING, ALLOW_EXIT_TRADING,
                               ALLOW_HEDGE_TRADING, _effective_kill(),
                               EMERGENCY_REDUCE_ONLY)["allowed"]
    # 受控对冲下单（默认门关→dry）：reduce/unwind 强制 reduce_only 且 kill 下仍允许；open/increase 受 emergency 阻断
    h_act = hedge["action"]["action"]
    if h_act != "HEDGE_HOLD":
        h_gate_act = ACTION_HEDGE_REDUCE if hedge["action"]["reduce_only"] else ACTION_HEDGE_OPEN
        if gate_decision(h_gate_act, ALLOW_ENTRY_TRADING, ALLOW_EXIT_TRADING, ALLOW_HEDGE_TRADING,
                         _effective_kill(), EMERGENCY_REDUCE_ONLY)["allowed"]:
            exec_hedge_step(hedge["venue_cfg"], hedge["side"], hedge["action"]["delta_contracts"],
                            hedge["action"]["reduce_only"], allow_live=True, label="hedge")
    pause = ("PAUSED_BY_BUDGET" if exit_state == EXIT_PAUSED_BUDGET else
             ("PAUSED_BY_DATA" if exit_state == EXIT_PAUSED_DATA else None))
    arb = unified_action_arbiter({
        "recovery_blocked": recovery.get("state") == "RECOVERY_BLOCKED",
        "orphan_hedge": (recovery.get("state") == "ORPHAN_HEDGE_EMERGENCY") or hedge["orphan"],
        "in_flight_order": False, "exit_preferred": False, "hedge_ready": False,
        "take_profit_ready": tp["qualified"],
        "exit_authorized": authorized,
        "exit_executable": bool(decision["can_order"] and exit_gate),
        "exit_pause_reason": pause,
        "hedge_executable": hedge_exec,
    })
    return {"arb": arb, "entry_snapshot": snap, "reconcile": rec,
            "auth": auth, "authorized": authorized, "tp_auth_code": tp_code,
            "exit_campaign_state": exit_state, "tp_ratio": tp["ratio"], "hedge": hedge}


def run_cycle(now_ms=None):
    """单一持续主链一轮：命令轮询 → 信号接收 → 门控 → 维护推荐库/锁定 → 渲染控制台。
    无持仓且信号放行时维护垂直推荐库 + 短确认码；`执行:码` 经 resolve 锁定 + 预提交。
    返回 ctx（含控制台字段），便于单测。真实开仓/管理/退出/对冲在 E3+ 接入。"""
    now_ms = now_ms or _now_ms()
    sid = _session_id()
    meta = {"robot_id": ROBOT_ID, "session_id": sid, "refresh_seq": _refresh_seq()}
    disp = _dispatch_command(GetCommand(), meta, now_ms)

    verdict = receive_signal(now_ms, SIGNAL_SOURCE, SIGNAL_FILE_PATH, SIGNAL_G_KEY,
                             SIGNAL_SCHEMA_VERSION_PREFIX)
    gsum = _gate_summary_now()
    kill = _effective_kill()
    state = ledger_get_state()
    has_pos = _has_position(state)
    locked = _G(_LOCKED_KEY)
    spot = _spot_price()

    pending = []
    commit_result = None
    manage_result = None
    recovery = _recovery_verdict()
    rec_ok = recovery.get("allow_new_open", True)
    if recovery.get("state") == "RECOVERY_BLOCKED":
        phase = "RECOVERY_BLOCKED"
    elif has_pos:
        manage_result = manage_cycle(now_ms)        # 持仓管理：急停下仍运行（停新风险不停管理）
        phase = "POSITION_MANAGE"
    elif kill:
        phase = "KILLED"
    elif locked:
        commit_result = _attempt_commit(locked, spot, verdict, now_ms)
        phase = "POSITION_MANAGE" if commit_result["committed"] else "PLAN_LOCKED"
    elif (not _signal_allows_entry(verdict)) or (not rec_ok):
        phase = ("OFFLINE_MANUAL" if verdict.get("availability") == "OFFLINE_MANUAL"
                 else "WAIT_SIGNAL")
    else:
        phase = "RECOMMEND_READY"
        lib = _G(_LIB_KEY)
        last_build = int(_G(_LIB_BUILD_TS_KEY) or 0)
        # 节流：每 PLAN_REFRESH_SECONDS 才重建一次推荐库（省 API + 稳定 refresh_seq/确认码）
        if spot and (not lib or now_ms - last_build >= PLAN_REFRESH_SECONDS * 1000):
            menu, pm_ok, model, reason, diag = _build_menu(now_ms, spot)
            if reason == "OK" and menu:
                rseq = _bump_refresh_seq()
                sig_pkg = verdict.get("package_id") or ("manual:" + str(SIGNAL_STATE))
                lib = build_recommendation_library(menu, sid, sig_pkg, rseq, now_ms)
                _G(_LIB_KEY, lib)
                _G(_LIB_BUILD_TS_KEY, now_ms)
        if lib and lib.get("recommendations"):
            pending = [{"id": s["plan_id"], "summary": s["summary"],
                        "confirm_code": s["confirm_code"]}
                       for s in lib["recommendations"][:MENU_SIZE]]
            phase = "HARD_APPROVAL_WAIT"

    ctx = _ctx_base(state, spot, "RUN_CYCLE:" + phase)
    ctx["console_phase"] = phase
    ctx["gate_summary"] = gsum
    ctx["signal_verdict"] = verdict
    ctx["pending_candidates"] = pending
    ctx["kill_new_risk"] = kill
    ctx["last_command"] = disp.get("action")
    ctx["last_command_outcome"] = disp.get("outcome")
    if commit_result:
        ctx["precommit"] = commit_result.get("precommit")
        ctx["order_intent"] = commit_result.get("order_intent")
        ctx["commit_reason"] = commit_result.get("reason")
        ctx["projected_budget"] = commit_result.get("budget")
        ctx["entry_state"] = commit_result.get("entry_state")
        ctx["entry_net_credit"] = commit_result.get("net_credit")
        if commit_result.get("entry_snapshot"):
            ctx["entry_snapshot"] = commit_result["entry_snapshot"]
    if manage_result:
        ctx["action_arb"] = manage_result.get("arb")
        ctx["entry_snapshot"] = manage_result.get("entry_snapshot")
        if manage_result.get("authorized"):
            ctx["exit_auth_state"] = "已授权(AUTHORIZED)"
        else:
            ctx["exit_auth_state"] = "未授权 ｜ 授权止盈码 %s" % (manage_result.get("tp_auth_code") or "—")
        ctx["exit_campaign_state"] = manage_result.get("exit_campaign_state")
        _r = manage_result.get("tp_ratio")
        ctx["take_profit_ratio"] = ("%.1f%%" % (_r * 100)) if isinstance(_r, (int, float)) else "数据缺口"
        _h = manage_result.get("hedge")
        if _h:
            ctx["hedge_state"] = "[%s] %s 目标%.4g 当前%.4g %s%s" % (
                _h.get("venue") or "—", _h.get("side") or "—", _h.get("target") or 0.0,
                _h.get("perp_qty") or 0.0, _h["action"]["action"], "·孤儿" if _h.get("orphan") else "")
    if recovery.get("state") != "OK":
        ctx["recovery_state"] = recovery.get("state")
    if locked and not (commit_result and commit_result.get("committed")):
        ctx["locked_plan_summary"] = "%s %s" % (locked.get("confirm_code"), locked.get("summary"))
    _emit(ctx, "主链")
    return ctx


def main():
    errs = validate_config()
    if errs:
        Log("[config] 配置错误，拒绝运行:", "; ".join(errs))
        LogStatus("配置错误：" + "; ".join(errs))
        return

    Log("[boot] S:PM 垂直价差执行链 v%s 启动（单一主链 run_cycle）" % STRATEGY_VERSION,
        "ALLOW_ENTRY=%s" % ALLOW_ENTRY_TRADING, "信号源=%s" % SIGNAL_SOURCE,
        "currency=%s" % SETTLEMENT_CURRENCY)
    startup_recovery_check(SETTLEMENT_CURRENCY)        # 启动恢复：可解释映射 → OK/RECOVERY_BLOCKED/ORPHAN

    while True:
        try:
            run_cycle()
        except Exception as e:
            Log("[loop] 异常:", str(e))
        Sleep(LOOP_INTERVAL_MS)
