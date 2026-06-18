# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import plans as P

W = {"win_rate": 0.375, "rr": 0.375, "signal": 0.25}


def _approx(a, b, eps=1e-9):
    return abs(a - b) <= eps


def test_win_rate():
    assert _approx(P.plan_win_rate(0.30), 0.70)


def test_effective_credit_vertical():
    eff, single, amort, res = P.plan_effective_credit(0.001, 0.0006)
    assert _approx(single, 0.0004) and _approx(eff, 0.0004) and res == 0.0
    assert _approx(amort, 0.0006)                          # 垂直：amortized = 保护腿权利金


def test_max_loss_and_rr():
    wb = (2000 / 73400.0) * 0.1
    ml = P.plan_max_loss(2000, 73400, 0.00088, 0.1)
    assert _approx(ml, wb - 0.00088)
    assert _approx(P.plan_rr(0.00088, ml), 0.00088 / ml)
    assert P.plan_rr(-0.001, ml) is None


def test_signal_fit_and_preferred_delta():
    pd_weak = P.plan_preferred_delta("TRADE_SUPPORT_WEAK", 30, (0.15, 0.45))
    pd_strong = P.plan_preferred_delta("TRADE_SUPPORT_STRONG", 90, (0.15, 0.45))
    assert pd_strong > pd_weak                             # 信号强 → 偏高 delta
    assert _approx(P.plan_signal_fit(0.30, 0.30), 1.0)
    assert P.plan_signal_fit(0.30, 0.20) < 1.0


def _mk(sd, sm, pm, pstrike, relief):
    short = {"instrument_name": "S", "strike": 75000, "_delta": sd, "expiration_timestamp": 1}
    sq = {"mark": sm, "best_bid": sm - 0.0003, "best_ask": sm + 0.0003, "tick": 0.0001, "delta": sd}
    prot = {"instrument_name": "P%d" % pstrike, "strike": pstrike, "expiration_timestamp": 1}
    pq = {"mark": pm, "best_bid": pm - 0.0003, "best_ask": pm + 0.0003, "tick": 0.0001, "delta": 0.12}
    spm = {"im_short_only": 0.025, "im_with_protection": 0.025 * (1 - relief),
           "relief_abs": 0.025 * relief, "relief_ratio": relief}
    return P.plan_assemble(0.1, 73400, 0.10, 0.30, True,
                           short, sq, prot, pq, spm, True, "segregated_pm", 48, 48)


def test_assemble_and_rank_vertical_and_tags():
    a = _mk(0.30, 0.010, 0.006, 77000, 0.40)
    b = _mk(0.22, 0.008, 0.005, 78000, 0.50)
    assert a["qualified"] and b["qualified"]
    assert a["net_credit_effective"] == a["net_credit_single"]  # 垂直无复用
    assert a["mode"] == P.MODE_VERTICAL and b["mode"] == P.MODE_VERTICAL
    menu = P.plan_rank([a, b], W, 6)
    assert len(menu) == 2 and all(m["plan_no"] for m in menu)
    assert {m["mode"] for m in menu} == {P.MODE_VERTICAL}
    alltags = sum([m["tags"] for m in menu], [])
    assert "均衡" in alltags and "高胜率" in alltags


def test_breakeven_and_credit_on_margin():
    # 卖 call：盈亏平衡价 = 短行权 + 每张净credit折USD
    be = P.plan_breakeven(True, 74000, 0.010, 0.006, 73000)
    assert _approx(be, 74000 + (0.010 - 0.006) * 73000)     # = 74000 + 292 = 74292
    # 卖 put：方向相反
    bep = P.plan_breakeven(False, 72000, 0.010, 0.006, 73000)
    assert _approx(bep, 72000 - (0.010 - 0.006) * 73000)
    # 信用/保证金回报率
    assert _approx(P.plan_credit_on_margin(0.0004, 0.02), 0.02)
    assert P.plan_credit_on_margin(0.0004, 0) is None


def test_plan_id_stable_unique_and_labels():
    a = P.plan_id(P.MODE_VERTICAL, "BTC-1JUN26-74000-C", "BTC-1JUN26-76000-C")
    b = P.plan_id(P.MODE_VERTICAL, "BTC-1JUN26-74000-C", "BTC-1JUN26-76000-C")
    c = P.plan_id(P.MODE_VERTICAL, "BTC-1JUN26-74000-C", "BTC-1JUN26-77000-C")
    assert a == b                       # 同结构 → 编号稳定(确定性)
    assert a != c                       # 不同结构 → 编号不同
    assert 1000 <= a <= 9999            # 4 位编号
    assert P.plan_expiry_label("BTC-1JUN26-74000-C") == "1JUN26"


def test_assemble_reject_no_bid():
    short = {"instrument_name": "S", "strike": 75000, "_delta": 0.30}
    sq = {"mark": 0.01, "best_bid": 0, "best_ask": 0.0103, "delta": 0.30}
    prot = {"instrument_name": "P", "strike": 77000}
    pq = {"mark": 0.006, "delta": 0.12}
    p = P.plan_assemble(0.1, 73400, 0.10, 0.30, True,
                        short, sq, prot, pq, {"relief_ratio": 0.4}, True, "segregated_pm")
    assert not p["qualified"] and "买盘" in p["reject_reason"]
