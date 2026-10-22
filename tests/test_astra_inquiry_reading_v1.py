"""The inquirer's reading view (docs/opus/18 §4)."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import astra_inquiry_reading_v1 as reading


def _case():
    return {"case_id": "cf_x", "asof_ms": 1792648777645,
            "layers": {"L0": [], "L1": [{"ref": "L1.economics", "asof_ms": 1792648777645, "label_cn": "收支",
                                         "quality": "ok", "lineage": ["x"],
                                         "values": {"premium_spread_btc": 0.004399999999999999,
                                                    "valuation_spot_usd": 82748.25,
                                                    "net_credit_after_fee_btc": None}}],
                       "L5": [{"ref": "L5.m_die", "asof_ms": None, "values": {"state": "NO_MOVE", "score": -0.28064945677881925,
                                                                             "last_closed_bar_time": 1792628340000,
                                                                             "age_ms": 20369188, "lag_ms": -1500,
                                                                             "venue": "Deribit", "side": "put",
                                                                             "kind": "BTC-23OCT26-83000-C"},
                               "relations": {"flip": 81764.16733787143, "rate": 3.05e-06}}]},
            "refs": {"L1.economics": {"layer": "L1", "index": 0}, "L5.m_die": {"layer": "L5", "index": 0}},
            "build": {"builder": "astra_case_file_v1@0.1.5", "built_at_ms": 1792648818563}}


def test_clocks_durations_and_display_precision():
    v = reading.view(_case())
    m = v["layers"]["L5"][0]
    assert m["values"]["last_closed_bar_time"] == "2026-10-22 00:19 UTC"
    assert m["values"]["age"] == "5.7 小时" and "age_ms" not in m["values"]
    assert m["values"]["lag"] == "-2 秒"
    assert m["values"]["score"] == -0.2806
    assert m["relations"] == {"flip": 81764.17, "rate": 3.05e-06}
    e = v["layers"]["L1"][0]
    assert e["values"]["premium_spread_btc"] == 0.0044 and e["values"]["valuation_spot_usd"] == 82748.25
    assert e["asof"] == "2026-10-22 05:59 UTC" and "asof_ms" not in e
    assert v["asof"] == "2026-10-22 05:59 UTC" and v["case_id"] == "cf_x" and v["refs"] == _case()["refs"]
    assert v["build"]["reading_view"] == reading.VERSION and "built_at_ms" not in v["build"]


def test_enum_codes_read_in_chinese_but_names_and_venues_stay():
    m = reading.view(_case())["layers"]["L5"][0]["values"]
    assert m["state"] != "NO_MOVE" and not m["state"].isascii()
    assert m["venue"] == "Deribit" and m["side"] == "put" and m["kind"] == "BTC-23OCT26-83000-C"


def test_chinese_keys_only_where_a_word_exists_and_never_overwrite():
    e = reading.view(_case(), chinese_keys=True)["layers"]["L1"][0]["values"]
    assert "net_credit_after_fee_btc" not in e and any("净信用" in k for k in e)
    assert len(e) == 3
    clash = {"layers": {"L1": [{"ref": "L1.x", "values": {"premium_spread_btc": 1.0, reading.lookup("premium_spread_btc"): 2.0}}]},
             "refs": {"L1.x": {"layer": "L1", "index": 0}}}
    values = reading.view(clash, chinese_keys=True)["layers"]["L1"][0]["values"]
    assert sorted(values.values()) == [1.0, 2.0]


def test_view_does_not_touch_the_archived_case():
    case = _case()
    before = json.dumps(case, sort_keys=True)
    reading.view(case, chinese_keys=True)
    assert json.dumps(case, sort_keys=True) == before


def test_terms_json_matches_the_page_dictionaries():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    result = subprocess.run([node, str(ROOT / "tools" / "astra_inquiry_terms_sync.mjs"), "--check"],
                            capture_output=True, text=True, encoding="utf-8", cwd=ROOT, check=False)
    assert result.returncode == 0, result.stderr


def test_a_clock_never_overwrites_a_sibling_named_like_its_stripped_key():
    for values in ({"expiry_ms": 1792742400000, "expiry": "23OCT26"}, {"expiry": "23OCT26", "expiry_ms": 1792742400000}):
        case = {"layers": {"L3": [{"ref": "L3.x", "values": values}]}, "refs": {"L3.x": {"layer": "L3", "index": 0}}}
        out = reading.view(case)["layers"]["L3"][0]["values"]
        assert out["expiry"] == "23OCT26" and out["expiry_ms"] == "2026-10-23 08:00 UTC"
