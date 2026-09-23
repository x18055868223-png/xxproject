"""Economic invariants: cost shocks keep frozen actions, paired ratios, source integrity."""
import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"tools"))
import astra_commercial_review_v17 as review
import astra_underwriting_v16 as core


def test_fixed_action_cost_shock_moves_break_even_by_same_amount():
    dates=pd.date_range("2022-01-01",periods=80).strftime("%Y-%m-%d")
    actions=np.array([1,0,0,1,1,0,1,0]*10)
    pnl=np.linspace(-.2,.3,80)*actions
    original=review.ratio_interval(dates,pnl,actions,repetitions=300)
    shock=.013
    changed=review.ratio_interval(dates,pnl-shock*actions,actions,repetitions=300)
    for key in ("mean","lower","upper"):
        assert changed[key] == pytest.approx(original[key]-shock,abs=1e-14)
    assert changed["active_weight"] == original["active_weight"]


def test_joint_ratio_with_unequal_active_counts_has_exact_constant_margin():
    dates=pd.date_range("2022-01-01",periods=60).strftime("%Y-%m-%d")
    actions=np.r_[np.ones(8),np.zeros(22),np.ones(30)]
    result=review.ratio_interval(dates,actions*.07,actions,repetitions=300)
    assert result["active_weight"]==38
    for key in ("mean","lower","upper"):
        assert result[key]==pytest.approx(.07)


def test_no_trades_has_no_executed_unit_headroom():
    result=review.ratio_interval(["2022-01-01","2022-01-10"],[0,0],[0,0])
    assert result["mean"] is None and result["lower"] is None
    assert result["active_weight"]==0


def test_unknown_cannot_be_silently_removed_from_margin_ratio():
    with pytest.raises(ValueError,match="unknown"):
        review.ratio_interval(["2022-01-01","2022-01-10"],[.2,np.nan],[1,1])
    with pytest.raises(ValueError,match="lengths"):
        review.ratio_interval(["2022-01-01"],[.2,.3],[1])


def test_source_seal_fails_when_economic_input_changes(tmp_path):
    source=tmp_path/"credit.json"
    source.write_text('{"credit":0.01}',encoding="utf-8")
    seal=tmp_path/"seal.json"
    seal.write_text(json.dumps({"source_hashes":{str(source):core.digest(source)}}),encoding="utf-8")
    review.verify_sources(seal)
    source.write_text('{"credit":0.02}',encoding="utf-8")
    with pytest.raises(ValueError,match="sealed source changed"):
        review.verify_sources(seal)
