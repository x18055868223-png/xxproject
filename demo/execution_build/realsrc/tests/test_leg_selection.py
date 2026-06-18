# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import leg_selection as LS

H = 3600000
NOW = 1_700_000_000_000


def _inst(name, strike, otype, dte_h):
    return {"instrument_name": name, "strike": strike, "option_type": otype,
            "expiration_timestamp": NOW + int(dte_h * H)}


def test_dte_hours():
    assert abs(LS.legsel_dte_hours(NOW + 48 * H, NOW) - 48) < 1e-9


def test_pick_expiry_nearest_center_in_band():
    insts = [_inst("a", 100, "call", 12),   # 带外(太近)
             _inst("b", 100, "call", 30),   # 带内
             _inst("c", 110, "call", 50),   # 带内（更近中心48）
             _inst("d", 100, "call", 100)]  # 带外(太远)
    exp, sub = LS.legsel_pick_expiry_instruments(insts, 24, 72, 48, NOW, True)
    assert exp == NOW + 50 * H
    assert len(sub) == 1 and sub[0]["instrument_name"] == "c"


def test_pick_expiry_none_in_band():
    insts = [_inst("a", 100, "call", 12), _inst("d", 100, "call", 100)]
    exp, sub = LS.legsel_pick_expiry_instruments(insts, 24, 72, 48, NOW, True)
    assert exp is None and sub == []


def _short_set(spot):
    insts = [_inst("c74", 74000, "call", 48), _inst("c75", 75000, "call", 48),
             _inst("c76", 76000, "call", 48), _inst("c77", 77000, "call", 48),
             _inst("c78", 78000, "call", 48)]
    deltas = {"c74": 0.45, "c75": 0.40, "c76": 0.30, "c77": 0.20, "c78": 0.10}
    return insts, (lambda n: deltas[n])


def test_short_enriched_and_pick_nearest_delta_tiers():
    spot = 73400
    insts, dof = _short_set(spot)
    enriched = LS.legsel_short_enriched(insts, spot, True, dof)
    assert len(enriched) == 5 and all("_delta" in e for e in enriched)
    # 3 档目标 delta 各选不同行权
    assert LS.legsel_pick_nearest_delta(enriched, 0.20)["strike"] == 77000
    assert LS.legsel_pick_nearest_delta(enriched, 0.30)["strike"] == 76000
    assert LS.legsel_pick_nearest_delta(enriched, 0.40)["strike"] == 75000


def test_pick_nearest_delta_put():
    spot = 73400
    insts = [_inst("p72", 72000, "put", 48), _inst("p71", 71000, "put", 48),
             _inst("p70", 70000, "put", 48)]
    dof = (lambda n: {"p72": -0.30, "p71": -0.20, "p70": -0.10}[n])
    enriched = LS.legsel_short_enriched(insts, spot, False, dof)
    assert LS.legsel_pick_nearest_delta(enriched, 0.30)["strike"] == 72000


def test_protection_by_width_in_band_and_deep_otm_filter():
    short_strike = 76000
    insts = [_inst("c77", 77000, "call", 48), _inst("c78", 78000, "call", 48),
             _inst("c785", 78500, "call", 48), _inst("c80", 80000, "call", 48),
             _inst("c90", 90000, "call", 48)]

    def dof(name):
        return {"c90": 0.02}.get(name, 0.3)   # c90 过度虚值

    ordered = LS.legsel_protection_candidates(
        insts, short_strike, True, (2000, 2500), delta_of=dof,
        deep_otm_max_delta=0.05)
    names = [o["instrument_name"] for o in ordered]
    widths = {o["instrument_name"]: o["_width"] for o in ordered}
    assert "c90" not in names                 # 过度虚值剔除
    # 带内(2000~2500)：78000(2000)/78500(2500)；中心 2250 等距，稳定排序保留输入序 → 78000 在前
    assert names[0] in ("c78", "c785")
    assert widths["c78"] == 2000 and widths["c785"] == 2500
    # 带外(77000 宽1000、80000 宽4000)排在带内之后
    assert names.index("c80") >= 2 and names.index("c77") >= 2


def test_protection_by_width_put_side():
    short_strike = 73000
    insts = [_inst("p71", 71000, "put", 48), _inst("p705", 70500, "put", 48),
             _inst("p72", 72000, "put", 48)]
    ordered = LS.legsel_protection_candidates(
        insts, short_strike, False, (2000, 2500), delta_of=lambda n: 0.3)
    names = [o["instrument_name"] for o in ordered]
    # put 外侧=更低行权；带内 71000(宽2000)/70500(宽2500)；72000(宽1000)带外排后
    assert names[0] in ("p71", "p705")
    assert names[-1] == "p72"
