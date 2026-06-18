# -*- coding: utf-8 -*-
"""核验已移植的整合契约模块在本路径可跑通（重点：vrp_adapter 连真实 VRP 快照）。

这是唯一一份冒烟脚本（保持收束，不复制 codex 的 16 测试 sprawl）。
真实因子焊接进两份 FMZ 时再配干净回归。
    python verify_integration_core.py
"""
from __future__ import annotations

import json

import session_core
import risk_controls
import kpf_cut_policy
import vrp_adapter


def _signal_package():
    return {
        "schema_name": "SignalEvidencePackage",
        "package_id": "sig-verify-01",
        "created_ts": 1_780_000_000,
        "expires_ts": 1_780_000_090,
        "direction_evidence": {"edb": {"lean": "SHORT_CALL", "confidence": 72, "coverage": 0.8}},
        "strategy_recommendation": {"side_hint": "CALL", "expiry_hours": 24},
    }


def _market_context():
    return {"spot": 68000.0, "front_anchor_iv": 0.82, "atm_front_iv": 0.80,
            "term_reference_iv_5_10d": 0.72, "rv_24h": 0.42, "rv_72h": 0.40,
            "rv_7d": 0.38, "rv_percentile": 0.50, "history_days": 60}


def _candidate_quote():
    return {"short_strike": 70000.0, "protection_strike": 72000.0, "amount": 1.0,
            "short_bid": 0.040, "short_ask": 0.042, "protection_bid": 0.010,
            "protection_ask": 0.012, "executable_short_iv": 0.86,
            "executable_protection_iv": 0.82, "short_instrument": "BTC-24H-70000-C",
            "protection_instrument": "BTC-24H-72000-C", "short_delta": 0.30}


def main():
    ok = []

    # 1) KPF 减法 helper（Phase1）
    w = kpf_cut_policy.normalize_plan_weights_without_kpf(
        {"win_rate": 0.30, "rr": 0.30, "signal": 0.20, "kpf": 0.20})
    assert abs(sum(w.values()) - 1.0) < 1e-9 and "kpf" not in w, w
    assert abs(w["win_rate"] - 0.375) < 1e-9, w
    p = kpf_cut_policy.persistence_level_from_adverse_flags(True, True)
    assert p["level"] == "HIGH", p
    ok.append("kpf_cut_policy: 权重归一 %s; persistence 两项制 OK" % w)

    # 2) vrp_adapter —— 连真实 VRP 快照（关键真实代码验证）
    gate = vrp_adapter.evaluate_demo_vrp_gate(
        signal_package=_signal_package(),
        market_context=_market_context(),
        candidate_quote=_candidate_quote())
    assert gate["schema_name"] == "VrpGatePackage", gate
    assert gate["factor_version"] == "1.1.0", gate
    assert "window" in gate and "candidate" in gate
    ok.append("vrp_adapter: 真实 VRP v%s 双门跑通 window_gate=%s candidate_gate=%s pass=%s"
              % (gate["factor_version"], gate["window"]["window_vrp_gate"],
                 gate["candidate"]["candidate_vrp_gate"], gate["pass"]))

    # 3) session_core —— ExecutionSession + ApprovalIntent（plan_hash/TTL/precommit）
    s = session_core.ExecutionSession.open("exec-verify", "sig-verify-01", now_ts=100)
    s.lock_plan({"side": "SHORT_CALL", "expiry_hours": 24, "net_edge": 12.5}, now_ts=101, ttl_sec=30)
    checks = session_core.PrecommitChecks(True, gate["pass"], True, True, True, True, True)
    s.approve_locked_plan(now_ts=102, checks=checks, allow_real_order=False, operator_note="verify dry-run")
    commit_dry = s.can_commit_order(now_ts=103)         # allow_real_order=False → 不可下单
    s2 = session_core.ExecutionSession.open("exec-verify2", "sig-verify-01", now_ts=100)
    s2.lock_plan({"side": "SHORT_CALL"}, now_ts=101, ttl_sec=30)
    s2.approve_locked_plan(now_ts=102, checks=checks, allow_real_order=True)
    expired = s2.can_commit_order(now_ts=200)            # TTL 过期 → 不可下单
    assert commit_dry is False and expired is False, (commit_dry, expired)
    ok.append("session_core: dry-run 不可下单=%s; TTL 过期阻断=%s（安全门生效）" % (commit_dry, expired))

    # 4) risk_controls —— 四缺口因子 + 安全默认
    budget = risk_controls.evaluate_portfolio_budget(
        current={"open_positions": 2, "short_gamma": 0.9, "short_vega": 0.0, "margin_used": 0.0},
        limits={"max_open_positions": 1, "max_short_gamma": 0.5, "max_short_vega": 1.0, "max_margin": 5000.0},
        proposed_size=1.0)
    assert budget["decision"] == "BLOCK" and budget["allowed_size"] == 0.0, budget
    manage = risk_controls.decide_position_manage(0.75, 0.70, 24, "LOW")
    assert manage["decision"] == "TAKE_PROFIT_READY", manage
    attr = risk_controls.build_attribution("exec-verify", 5.0, 0.0, 0.0, 1.0, 0.5, 0.0, 0.0)
    rep = risk_controls.replay_expectation([attr])
    ok.append("risk_controls: 预算超限→BLOCK size=0; 止盈→%s; 归因 net=%.2f; 回放 bucket=%s"
              % (manage["decision"], attr["net_pnl_after_costs"], rep["expectation_bucket"]))

    print("=== 整合契约模块核验 (本路径, 连真实 VRP 快照) ===")
    for line in ok:
        print("  PASS " + line)
    print("\n全部 PASS：5 模块中 4 个冒烟 + vrp_adapter 真实代码连通。")


if __name__ == "__main__":
    main()
