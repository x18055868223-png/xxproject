# -*- coding: utf-8 -*-
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import display as D


def _tables_from_panel(panel):
    # 面板格式： "header...#color\n`<json array>`"
    assert "`" in panel
    raw = panel.split("`", 1)[1].rsplit("`", 1)[0]
    return json.loads(raw)


def _ctx():
    return {
        "currency": "BTC", "signal_state": "TRADE_SUPPORT_WEAK",
        "direction_bias": "SHORT_CALL", "allow_trading": False,
        "state": "SPM_SIMULATION", "spot": 73400.0, "reason": "DRY_RUN_PLAN_ONLY",
        "short_instrument": "BTC-31MAY26-78000-C", "short_strike": 78000,
        "short_dte_hours": 47.56, "short_mark": 0.0001, "short_bid": 0,
        "short_ask": 0.0002, "short_tick": 0.0001, "short_delta": 0.04,
        "protection_instrument": "BTC-5JUN26-79000-C", "protection_strike": 79000,
        "protection_dte_days": 6.98, "protection_mark": 0.0011, "protection_bid": 0.001,
        "protection_ask": 0.0011, "protection_tick": 0.0001, "protection_delta": 0.06,
        "im_short_only": 0.0073, "im_with_protection": 0.0012,
        "margin_relief_abs": 0.0061, "margin_relief_ratio": 0.837,
        "min_required_ratio": 0.1, "pm_accepted": True,
        "account_margin_model": "segregated_pm",
        "short_premium_income": 0.00001, "estimated_entry_fee": 0.0000125,
        "protection_entry_cost": 0.00011, "full_burn_cost": 0.000124,
        "estimated_spread_cost": 0.00001,
    }


def test_reason_and_state_maps():
    assert D.disp_reason_cn("DRY_RUN_PLAN_ONLY").startswith("空跑")
    assert D.disp_reason_cn("EXIT_REVIEW_SIGNAL:NO_TRADE_BLOCKED").find("阻断") >= 0
    assert D.disp_state_cn("SHORT_ACTIVE_PROTECTED") == "已保护·卖方持仓"
    assert D.disp_signal_cn("TRADE_SUPPORT_WEAK").find("弱支持") >= 0


def test_panel_is_valid_fmz_tables():
    panel = D.disp_status_panel(_ctx(), "进场流水线 [空跑核对]")
    tables = _tables_from_panel(panel)
    # 交互控制台(置顶) + 运行概览 + 保证金与成本(合并) + 合理性检查 = 4 表
    assert isinstance(tables, list) and len(tables) == 4
    assert tables[0]["title"] == "交互控制台"
    for t in tables:
        assert t["type"] == "table"
        assert "title" in t and "cols" in t and "rows" in t
        for r in t["rows"]:
            assert len(r) == len(t["cols"])   # 行列对齐


def test_health_flags_no_bid_and_cost_multiple():
    notes = D.disp_health_notes(_ctx())
    levels = [lv for lv, _ in notes]
    texts = " ".join(t for _, t in notes)
    assert "警示" in levels                 # best_bid=0
    assert "无买盘" in texts
    assert "倍" in texts                     # 保护成本/权利金倍数提示


def test_health_clean_passes():
    ctx = _ctx()
    ctx.update(short_bid=0.0002, short_premium_income=0.01,
               estimated_entry_fee=0.0001, protection_entry_cost=0.02,
               protection_delta=0.2)
    notes = D.disp_health_notes(ctx)
    assert any(lv == "通过" for lv, _ in notes)


def test_btc_usd_formatting():
    s = D._btc_usd(0.0001, 73400.0)
    assert "BTC" in s and "$" in s
    assert D._usd(None, 73400.0) == "—"


def test_console_table_present_and_confirm_code_and_hint():
    ctx = _ctx()
    ctx.update(
        console_phase="HARD_APPROVAL_WAIT",
        gate_summary={"ENTRY": {"allowed": False}, "EXIT": {"allowed": True},
                      "HEDGE_OPEN": {"allowed": False}, "HEDGE_REDUCE": {"allowed": True}},
        signal_verdict={"availability": "OK", "block_new_opens": False,
                        "side_hint": "call_credit_spread"},
        pending_candidates=[{"id": 1234, "summary": "SHORT_CALL Δ0.30", "confirm_code": "A4F2"}])
    tables = _tables_from_panel(D.disp_status_panel(ctx, "测试"))
    assert tables[0]["title"] == "交互控制台"
    rows = {r[0]: r[1] for r in tables[0]["rows"]}
    assert "待计划硬授权" in rows["阶段"]
    assert "A4F2" in rows["待批 #1234"]
    assert "执行" in rows["操作提示"] and "确认码" in rows["操作提示"]
    assert "进场✗" in rows["执行门控"] and "退出✓" in rows["执行门控"]


def test_console_kill_hint_overrides():
    ctx = _ctx(); ctx.update(kill_new_risk=True, console_phase="POSITION_MANAGE")
    assert "急停" in D.disp_operation_hint(ctx)


def test_console_offline_manual_hint():
    ctx = _ctx(); ctx.update(signal_verdict={"availability": "OFFLINE_MANUAL",
                                             "block_new_opens": False})
    assert "离线手动" in D.disp_operation_hint(ctx)
