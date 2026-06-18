# -*- coding: utf-8 -*-
"""HEDGE_WATCH 域（R5）：持仓后对冲监控集成缝。

驻执行内：读账本 short 记录的 EntryRiskAnchor + 实时行情 + **SignalEvidencePackage 的
edb/ggr**（整合契约形状），调真实 hedge_risk.evaluate_position_risk，产出 PositionRiskPackage
（仅 HEDGE_READY 时带 DRY_INTENT_ONLY 的 HedgeIntentPackage）。

边界：只在持仓后运行；不入场、不判方向、不自动改期权账本、第一版不真实下单。
对冲只读 EntryRiskAnchor 的 VRP 血缘，不反向重做 VRP。
"""
from hedge_risk import evaluate_position_risk


def watch_position(position_id, direction_bias, short_record, current_market,
                   signal_evidence=None, exit_friction=None, recent_history=None,
                   now_ms=None, existing_hedge=False):
    """short_record: 账本 short 记录（含 entry_risk_anchor）。
    current_market: {price, dte_hours, short_delta, short_gamma, iv}。
    signal_evidence: SignalEvidencePackage（取 direction_evidence.edb + pre_trade_context.ggr）。
    返回 PositionRiskPackage（position_risk.v0.4，持续性两项制）。"""
    anchor = (short_record or {}).get("entry_risk_anchor") or {}
    se = signal_evidence or {}
    edb = (se.get("direction_evidence") or {}).get("edb")
    ggr = (se.get("pre_trade_context") or {}).get("ggr")
    return evaluate_position_risk(
        position_id=position_id,
        direction_bias=direction_bias,
        entry_risk_anchor=anchor,
        current_price=current_market.get("price"),
        dte_hours=current_market.get("dte_hours"),
        short_delta=current_market.get("short_delta"),
        short_gamma=current_market.get("short_gamma"),
        iv=current_market.get("iv"),
        loss_boundary=anchor.get("entry_loss_boundary"),
        edb=edb,
        gamma_regime=ggr,
        exit_friction=exit_friction,
        recent_history=recent_history,
        now_ms=now_ms,
        existing_hedge=existing_hedge,
    )
