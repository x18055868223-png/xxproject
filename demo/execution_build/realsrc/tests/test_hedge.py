# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import hedge as H


def test_hedge_side():
    assert H.hedge_side("CALL") == "buy" and H.hedge_side("SHORT_CALL") == "buy"
    assert H.hedge_side("PUT") == "sell" and H.hedge_side("SHORT_PUT") == "sell"
    assert H.hedge_side("X") is None


def test_target_zero_when_short_flat_or_closed():
    # 短腿归零 → 目标立即 0（不等保护腿）
    assert H.hedge_target_contracts(0.0, 0.3, 0.5, 73000, 10, 1) == 0.0
    # 结构 CLOSED/SETTLED → 0
    assert H.hedge_target_contracts(0.1, 0.3, 0.5, 73000, 10, 1, "SETTLED") == 0.0
    assert H.hedge_target_contracts(0.1, 0.3, 0.5, 73000, 10, 1, "CLOSED") == 0.0


def test_target_contracts_rounding():
    # delta_btc = 0.1*0.3*0.5 = 0.015 ; raw = 0.015*73000/10 = 109.5 → round to min_trade 1 → 110 (109.5→110)
    t = H.hedge_target_contracts(0.1, 0.3, 0.5, 73000, 10, 1)
    assert abs(t - 110.0) < 1e-6


def test_order_action_open_increase_reduce_unwind():
    # 从 0 → 目标 100：HEDGE_OPEN，非 reduce_only（P0-5 关键：建仓不能 reduce_only）
    a = H.hedge_order_action(0.0, 100.0, 1)
    assert a["action"] == "HEDGE_OPEN" and a["reduce_only"] is False
    # 50 → 100：HEDGE_INCREASE，非 reduce_only
    assert H.hedge_order_action(50.0, 100.0, 1)["action"] == "HEDGE_INCREASE"
    assert H.hedge_order_action(50.0, 100.0, 1)["reduce_only"] is False
    # 100 → 50：HEDGE_REDUCE，强制 reduce_only
    r = H.hedge_order_action(100.0, 50.0, 1)
    assert r["action"] == "HEDGE_REDUCE" and r["reduce_only"] is True
    # 100 → 0：HEDGE_UNWIND，强制 reduce_only
    u = H.hedge_order_action(100.0, 0.0, 1)
    assert u["action"] == "HEDGE_UNWIND" and u["reduce_only"] is True
    # 相等 → HOLD
    assert H.hedge_order_action(100.0, 100.0, 1)["action"] == "HEDGE_HOLD"


def test_orphan_detection():
    assert H.hedge_orphan(0.0, 50.0) is True       # 期权卖方风险没了但 perp 还在
    assert H.hedge_orphan(0.1, 50.0) is False
    assert H.hedge_orphan(0.0, 0.0) is False


def test_hedge_venue_config():
    d = H.hedge_venue_config("DERIBIT")
    assert d["venue"] == "DERIBIT" and d["instrument"] == "BTC-PERPETUAL"
    assert d["linear"] is False and d["maker_only"] is False
    b = H.hedge_venue_config("BINANCE")
    assert b["venue"] == "BINANCE" and b["instrument"] == "BTCUSDC"
    assert b["linear"] is True and b["maker_only"] is True       # USDC maker 0 费 → 默认 maker
    assert H.hedge_venue_config(None)["venue"] == "DERIBIT"      # 默认 Deribit


def test_hedge_target_linear_vs_inverse():
    # 线性(Binance)：size = delta_btc = 0.1*0.3*0.5 = 0.015 BTC，取整 0.001 → 0.015
    lin = H.hedge_target_contracts(0.1, 0.3, 0.5, 73000, 1, 0.001, linear=True)
    assert abs(lin - 0.015) < 1e-9
    # 反向(Deribit)同参数 → delta_btc*spot/contract_size，数量级完全不同（证明换算区分场所）
    inv = H.hedge_target_contracts(0.1, 0.3, 0.5, 73000, 1, 0.001, linear=False)
    assert inv > 1000
    # 线性下短腿归零仍 → 0
    assert H.hedge_target_contracts(0.0, 0.3, 0.5, 73000, 1, 0.001, linear=True) == 0.0


def test_structure_net_delta():
    # 同期 call 垂直：短腿 0.30 − 保护腿 0.15 = 0.15（保护腿抵消一半敞口）
    assert abs(H.structure_net_delta(0.30, 0.15) - 0.15) < 1e-12
    # put 垂直：−0.30 − (−0.15) = −0.15
    assert abs(H.structure_net_delta(-0.30, -0.15) - (-0.15)) < 1e-12
    # 保护腿 delta 缺失 → 退化为短腿 delta（保守过对冲）
    assert H.structure_net_delta(0.30, None) == 0.30
    # 短腿缺失 → None
    assert H.structure_net_delta(None, 0.15) is None


def test_hedge_direction_consistent():
    # SHORT_CALL：净 delta>0 → position delta<0 → 应 buy（一致）
    assert H.hedge_direction_consistent("CALL", 0.15) is True
    # SHORT_PUT：净 delta<0 → position delta>0 → 应 sell（一致）
    assert H.hedge_direction_consistent("PUT", -0.15) is True
    # 反向结构（保护腿过抵消使净 delta 与方向相悖）→ 不一致
    assert H.hedge_direction_consistent("CALL", -0.04) is False
    assert H.hedge_direction_consistent("PUT", 0.04) is False
    # 数据缺失 / 净≈0 / 无方向 → True（不阻断）
    assert H.hedge_direction_consistent("CALL", None) is True
    assert H.hedge_direction_consistent("CALL", 0.0) is True
    assert H.hedge_direction_consistent("X", 0.15) is True


def test_settlement_guard():
    s = H.settlement_guard(0.1, near_expiry=False, settled=True, perp_qty=50.0)
    assert s["target"] == 0.0 and s["orphan"] is True
    ne = H.settlement_guard(0.0, near_expiry=True, settled=False, perp_qty=0.0)
    assert ne["target"] == 0.0 and ne["reason"] == "NEAR_EXPIRY_NO_NEW_HEDGE"
    assert H.settlement_guard(0.1, False, False, 0.0)["reason"] == "NORMAL"
