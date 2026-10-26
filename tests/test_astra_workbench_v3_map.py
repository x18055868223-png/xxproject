"""v3 price map model: this expiry's key levels as lines, one naming convention, full price ladder."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODEL = (ROOT / "deploy" / "astra_workbench_v3" / "model.js").as_uri()


def run(expr, payload):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    script = (f'import * as M from "{MODEL}";\n'
              'import fs from "node:fs";\n'
              'const p = JSON.parse(fs.readFileSync(0, "utf8"));\n'
              f'console.log(JSON.stringify({expr}));')
    result = subprocess.run([node, "--input-type=module", "-e", script], input=json.dumps(payload),
                            text=True, encoding="utf-8", capture_output=True, check=False, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def view(next_expiry="27OCT26", oi_call=81_500.0):
    gex = {"near_term_walls": {"weekly": {"walls": {"n1": {"strike": 79_000, "gex_usd": -1.5e7}, "p1": {"strike": 82_000, "gex_usd": 1.1e7}}}},
           "resistance_walls": [85_000], "magnet_price": 80_000, "max_pain_price": 78_000, "max_pain_target_expiry": "30OCT26",
           "next_expiry": {"expiry": next_expiry, "max_pain_price": 80_000, "hours_to_expiry": 22}}
    return {"market_context": {"current_price_usd": 80_500.0, "market_facts": {"space_context": {"gex_info": gex}}},
            "btc_map": {"reference_pool": [{"role": "EFFECTIVE_FLIP", "raw_price": 80_035.0},
                                           {"role": "KPF_BASIN", "raw_low": 82_650, "raw_high": 83_150, "observation_end_ms": 1}],
                        "background": {"inventory": {"metrics": {"cost_basis": {"cp_sth_usd": 76_704.0, "cp_lth_usd": 84_377.0}}}}},
            "expiry_gamma": {"by_expiry": {"27OCT26": {"expiry": "27OCT26",
                "top": {"gamma_put": [{"strike": 80_000, "value": 3.9e6}, {"strike": 79_500, "value": 1.0e6}],
                        "gamma_call": [{"strike": 81_500, "value": 2.6e6}, {"strike": 80_000, "value": 1.5e6}]}, "walls": {
                "pin": 81_500.0, "gamma_put": 80_000.0, "oi_put": 79_000.0, "gamma_call": 81_500.0, "oi_call": oi_call}}}}}


SIDES = 'const sides = { put: { snap: {}, short: 80000, long: 78000, shortName: "BTC-27OCT26-80000-P", sigmaPct: 1.5 } };'


def model(payload):
    return run(f"(() => {{ {SIDES} return M.mapModel(p, sides); }})()", payload)


def test_this_expiry_levels_are_lines_with_one_naming_convention():
    m = model(view())
    levels = {(l["role"], l["price"]): l for l in m["levels"]}
    assert levels[("pin", 81_500)]["name"] == "Pin"
    assert levels[("wall_put", 80_000)]["name"] == "Put 墙"
    assert levels[("oi_put", 79_000)]["name"] == "Put 持仓峰"
    assert levels[("wall_call", 81_500)]["name"] == "Call 墙（持仓峰同位）"     # same strike: one level, not two
    assert ("oi_call", 81_500) not in levels
    assert levels[("max_pain", 80_000)]["label"].startswith("本期最大痛点")      # next expiry == this expiry
    assert levels[("flip", 80_035)]["basis"] == "全期限" and "全期限" in levels[("flip", 80_035)]["label"]
    # the old glyphs for the same roles are gone from the marker lanes
    shorts = {mk.get("short") for mk in m["markers"]}
    assert not shorts & {"Flip", "Call 墙", "Put Gamma 墙", "Put 持仓墙", "近痛点"}
    assert any(s and s.endswith("痛点") and s.startswith("30OCT") for s in shorts)


def test_other_expiry_max_pain_stays_a_marker_and_split_oi_peak_is_its_own_level():
    m = model(view(next_expiry="28OCT26", oi_call=82_000.0))
    assert not any(l["role"] == "max_pain" for l in m["levels"])
    assert any(mk.get("short") == "近痛点" and mk.get("basis") == "下一到期" for mk in m["markers"])
    names = {l["name"] for l in m["levels"]}
    assert {"Call 墙", "Call 持仓峰"} <= names


def test_ladder_lists_every_level_with_distance_including_off_map_costs():
    lad = model(view())["ladder"]
    below = [(r["price"], r["name"]) for r in lad["below"]]
    above = [(r["price"], r["name"]) for r in lad["above"]]
    assert [p for p, _ in below] == sorted((p for p, _ in below), reverse=True)    # nearest first
    assert [p for p, _ in above] == sorted(p for p, _ in above)
    sth = next(r for r in lad["below"] if r["name"] == "短期持有者成本")
    assert sth["basisCn"] == "链上（背景）" and round(sth["distPct"], 2) == round((76_704 / 80_500 - 1) * 100, 2)
    assert round(sth["moves"], 3) == round(sth["distPct"] / 1.5, 3)
    keys = {r["name"] for r in lad["below"] + lad["above"] if r.get("key")}
    assert keys and all("本期" in n for n in keys)                                  # bold = this expiry only
    assert any(r["name"].startswith("周度 负 Gamma 墙") for r in lad["below"])


def test_main_expiry_max_pain_of_this_contract_is_one_level_and_day_tokens_read_cleanly():
    v = view(next_expiry="28OCT26")
    v["market_context"]["market_facts"]["space_context"]["gex_info"]["max_pain_target_expiry"] = "27OCT26"
    m = model(v)
    pains = [l for l in m["levels"] if l["role"] == "max_pain"]
    assert len(pains) == 1 and pains[0]["price"] == 78_000 and pains[0]["label"].startswith("本期最大痛点")
    assert not any(mk.get("basis") == "主到期" for mk in m["markers"])
    v["market_context"]["market_facts"]["space_context"]["gex_info"]["max_pain_target_expiry"] = "5NOV26"
    assert any(mk.get("short") == "5NOV 痛点" for mk in model(v)["markers"])


def test_profile_carries_this_expiry_top_strikes_walls_and_max_pain():
    pf = model(view())["profile"]
    assert pf["expiry"] == "27OCT26"
    assert [b["strike"] for b in pf["put"]] == [80_000, 79_500] and pf["call"][0] == {"strike": 81_500, "gex": 2.6e6}
    assert pf["wall"] == {"put": 80_000, "call": 81_500} and pf["oi"] == {"put": 79_000, "call": 81_500}
    assert pf["pin"] == 81_500 and pf["maxPain"] == 80_000
    v = view()
    del v["expiry_gamma"]
    assert model(v)["profile"] is None
