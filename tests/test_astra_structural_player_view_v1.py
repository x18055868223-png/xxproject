"""Paired compression, active decision protocol and separate illustrative judgements."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

from test_astra_structural_board_v1 import inputs, E, CUT, M, H

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import astra_structural_board_v1 as raw
import astra_level_behaviour_v1 as levels
import astra_structural_player_view_v1 as player

DOCS = ROOT / "docs/rulebook"
EXAMPLES = DOCS / "examples/player_view"


def boards(kind="accept"):
    result = {}
    for side in player.SIDES:
        case, card = inputs(side=side)
        if kind == "reject":
            c = raw.entry(case, "L1.contract")["values"]
            short, long = (81000.0, 79000.0) if side == "put" else (80000.0, 82000.0)
            suffix = "P" if side == "put" else "C"
            c.update(short_strike_usd=short, long_strike_usd=long,
                     short_instrument=f"BTC-12NOV26-{short:g}-{suffix}",
                     long_instrument=f"BTC-12NOV26-{long:g}-{suffix}")
            case["case_id"] = f"synthetic_both_inside_{side}"
            raw.entry(case, "L1.liability")["values"].update(
                starts_at_usd=short, protection_at_usd=long,
                btc_contract_cap=None if side == "put" else 2000 / long)
            walls = raw.entry(case, "L3.expiry_walls")["values"]
            ks = sorted(levels.levels_for(walls, c, None))
            pts = [[t, 78900.0 if t < CUT - 12 * H else 81300.0 if t < CUT - 2 * H else 80600.0]
                   for t in range(CUT - 25 * H, CUT + 1, M)]
            snaps = [{"at_ms": t, "strikes": [{"k": k, "c_oi": 200.0, "p_oi": 500.0} for k in ks]}
                     for t in (CUT - 24 * H - 5 * M, CUT - 6 * H - 5 * M, CUT - 5 * M)]
            card = levels.build_card(expiry_ms=E, rnd="r2", side=side, read_at_ms=CUT, walls=walls,
                                     points=pts, case=case, contract=c, strikes=ks, snapshots=snaps)
        result[side] = raw.build(case, card)
    return result


def assessment(view, put="acceptable", call="acceptable"):
    states = {"put": put, "call": call}
    accepted = [s for s, state in states.items() if state == "acceptable"]
    unresolved = "unassessable" in states.values()
    return {"schema": player.ASSESSMENT, "view_sha256": raw.digest(view),
            "decision": "Accept" if accepted else "Unknown" if unresolved else "Reject",
            "at_least_one_acceptable": True if accepted else None if unresolved else False,
            "selected_side": accepted[0] if accepted else None,
            "side_assessments": {s: {"state": state,
                "phenomenon_refs": [p["id"] for p in view["phenomena"]
                                    if s in p["sides"] and p["kind"] in ("position", "structures")],
                "reason": "本示范按这一侧责任边界与已引用的空间事实作出判断。"} for s, state in states.items()},
            "scope": "structure_only", "execution_authorized": False}


def schema(name):
    return json.loads((DOCS / f"{name}_0.1.schema.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("kind,count", [("accept", 7), ("reject", 5)])
def test_native_boards_compress_to_five_to_seven_statements(kind, count):
    source = boards(kind)
    frozen = copy.deepcopy(source)
    view = player.build(**source)
    jsonschema.validate(view, schema("structural_player_view"))
    assert source == frozen and view == player.build(**source)
    assert len(view["phenomena"]) == count
    assert view["critical_gaps"] == []
    assert view["coverage"]["assessable_sides"] == ["put", "call"]
    for phenomenon in view["phenomena"]:
        for side in phenomenon["sides"]:
            assert phenomenon["boundary_ids"][side] == source[side]["boundary_id"]
        for side, refs in phenomenon["source_observation_refs"].items():
            assert set(refs) <= {o["id"] for o in source[side]["observations"]}
    assert not {"decision", "selected_side", "at_least_one_acceptable", "unknowns"}.intersection(view)
    text = json.dumps(view, ensure_ascii=False)
    assert all(k not in text for k in ["dealer_gamma", "causal_flow_confirmation", "stability", "movement"])


def test_oi_and_aggressors_aggregate_without_direction_prediction_or_double_count():
    source = boards()
    view = player.build(**source)
    structure = next(p for p in view["phenomena"] if p["kind"] == "structures" and p["sides"] == ["put"])
    group = next(g for g in structure["measurements"]["groups"] if g["region"] == "outside_liability")
    assert group["level_count"] == 6 and group["between_index_and_short_count"] == 4
    assert group["oi_put_btc_known"] == 6 * 802.0
    flow = next(p for p in view["phenomena"] if p["kind"] == "flow" and p["sides"] == ["put"])
    group = next(g for g in flow["measurements"]["groups"] if g["region"] == "outside_liability")
    assert group["p_buy_btc"] == 36 and group["p_sell_btc"] == 72 and group["p_net_buy_btc"] == -36
    assert group["c_net_buy_btc"] == 0
    assert "责任外侧的79000 USD结构位" in flow["description"]
    call_flow = next(p for p in view["phenomena"] if p["kind"] == "flow" and p["sides"] == ["call"])
    assert "79000 USD结构位" in call_flow["description"]  # not the intervening 81000–82000 levels
    # Each side is its own responsibility reading of shared market facts; never sum across sides.
    duplicate = copy.deepcopy(source)
    duplicate["put"]["observations"].append(copy.deepcopy(duplicate["put"]["observations"][2]))
    with pytest.raises(ValueError, match="duplicate"):
        player.build(**duplicate)


def test_regions_are_side_relative_and_include_both_boundary_neighborhoods():
    for side, cases in [("put", [(76000, "near_boundary"), (74050, "near_boundary"),
                                (78000, "outside_liability"), (75000, "inside_liability")]),
                        ("call", [(84000, "near_boundary"), (86050, "near_boundary"),
                                  (82000, "outside_liability"), (85000, "inside_liability")])]:
        b = boards()[side]["boundary"]
        for k, expected in cases:
            assert player.region(k, b) == expected


def test_missing_obligation_details_or_flow_does_not_block_accept():
    source = boards()
    for b in source.values():
        b["observations"] = [o for o in b["observations"] if o["kind"] in ("position", "space")]
    view = player.build(**source)
    assert len(view["phenomena"]) == 5 and not view["critical_gaps"]
    a = assessment(view)
    player.validate_assessment(a, view)
    assert a["decision"] == "Accept"
    for p in view["phenomena"]:
        if p["kind"] == "structures":
            assert all(g["oi_put_btc_known"] is None and g["oi_put_levels"] == 0 for g in p["measurements"]["groups"])


@pytest.mark.parametrize("missing", ["put", "call"])
def test_one_clear_side_can_accept_despite_other_side_blocked(missing):
    source = boards()
    source[missing] = None
    view = player.build(**source)
    jsonschema.validate(view, schema("structural_player_view"))
    assert view["critical_gaps"] == [{"side": missing, "code": "candidate_missing",
                                     "blocks_this_side": True, "blocks_all_sides": False}]
    states = {s: "unassessable" if s == missing else "acceptable" for s in player.SIDES}
    a = assessment(view, **states)
    player.validate_assessment(a, view)
    jsonschema.validate(a, schema("structural_player_assessment"))
    assert a["decision"] == "Accept" and a["selected_side"] != missing


@pytest.mark.parametrize("mutation", ["cutoff", "index", "boundary", "expiry", "future", "future_window"])
def test_cross_context_and_future_observation_rejected(mutation):
    source = boards()
    c = source["call"]
    if mutation == "cutoff":
        c["cutoff_ms"] += 1
    elif mutation == "index":
        o = next(o for o in c["observations"] if o["kind"] == "position")
        o["measurements"]["index_usd"] += 1
        o["level_usd"] += 1
        o["short_offset_usd"] += 1
        o["protection_offset_usd"] += 1
    elif mutation == "expiry":
        c["boundary"]["expiry_ms"] += H
        c["boundary_id"] = raw.digest(c["boundary"])
        for o in c["observations"]:
            o["boundary_id"] = c["boundary_id"]
    elif mutation == "future":
        c["observations"][0]["observed_at_ms"] = CUT + 1
    elif mutation == "future_window":
        next(o for o in c["observations"] if o["kind"] == "path")["window_end_ms"] = CUT + 1
    else:
        c["observations"][0]["boundary_id"] = "0" * 64
    with pytest.raises(ValueError):
        player.build(**source)


def test_flow_window_mismatch_omitted_without_forcing_abstention():
    source = boards()
    for o in source["put"]["observations"]:
        if o["kind"] == "flow_at_structure":
            o["window_start_ms"] += H
    view = player.build(**source)
    assert not any(p["kind"] == "flow" and p["sides"] == ["put"] for p in view["phenomena"])
    player.validate_assessment(assessment(view), view)


def test_other_path_window_not_reported_as_current_24h():
    source = boards()
    for o in source["put"]["observations"]:
        if o["kind"] == "path":
            o["window_start_ms"] += H
    view = player.build(**source)
    p = next(p for p in view["phenomena"] if p["kind"] == "structures" and p["sides"] == ["put"])
    assert all(g["path_levels"] == 0 for g in p["measurements"]["groups"])
    assert not any(p["kind"] == "flow" and p["sides"] == ["put"] for p in view["phenomena"])
    player.validate_assessment(assessment(view), view)


@pytest.mark.parametrize("cross_at,between_crossed", [(80000, True), (82000, False)])
def test_feedback_prose_distinguishes_intervening_from_other_structures(cross_at, between_crossed):
    source = boards()
    for o in source["put"]["observations"]:
        if o["kind"] in ("path", "flow_at_structure"):
            o["measurements"]["crossings"] = int(o["level_usd"] == cross_at)
    view = player.build(**source)
    text = next(p["description"] for p in view["phenomena"] if p["kind"] == "structures" and p["sides"] == ["put"])
    if between_crossed:
        assert "指数至短腿间结构部分有穿越记录" in text
        assert "其余位置结构未见穿越" in text
    else:
        assert "指数至短腿间结构未见穿越" in text
        assert "其余位置结构部分有穿越记录" in text
    assert len(view["phenomena"]) == 7


def test_partial_feedback_does_not_claim_all_intervening_structures_were_observed():
    source = boards()
    source["put"]["observations"] = [o for o in source["put"]["observations"]
                                     if o["kind"] != "path" or o["level_usd"] == 78000]
    view = player.build(**source)
    text = next(p["description"] for p in view["phenomena"] if p["kind"] == "structures" and p["sides"] == ["put"])
    assert "指数至短腿间已读部分结构未见穿越" in text
    assert "其余位置结构未见穿越" not in text
    player.validate_assessment(assessment(view), view)


def test_obligation_qualification_is_shared_once_without_an_abstention_gate():
    view = player.build(**boards())
    text = "\n".join(p["description"] for p in view["phenomena"])
    assert text.count("净对冲义务的方向与强度未识别") == 1
    assert "距到期18h" in text
    assert not view["critical_gaps"]
    a = assessment(view)
    player.validate_assessment(a, view)
    assert a["decision"] == "Accept"


def test_reject_requires_both_sides_ruled_out_and_unknown_cannot_hide_an_acceptable_side():
    view = player.build(**boards("reject"))
    a = assessment(view, put="unfavorable", call="unfavorable")
    player.validate_assessment(a, view)
    jsonschema.validate(a, schema("structural_player_assessment"))
    assert a["decision"] == "Reject" and a["at_least_one_acceptable"] is False
    for wrong in ("Reject", "Unknown"):
        a = assessment(view, put="acceptable", call="unfavorable")
        a.update(decision=wrong, selected_side=None, at_least_one_acceptable=False if wrong == "Reject" else None)
        with pytest.raises(ValueError):
            player.validate_assessment(a, view)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(a, schema("structural_player_assessment"))


def test_missing_both_sides_is_unknown_not_no_qualified_vertical():
    view = player.build(None, None, cutoff_ms=CUT)
    a = assessment(view, put="unassessable", call="unassessable")
    player.validate_assessment(a, view)
    jsonschema.validate(a, schema("structural_player_assessment"))
    assert a["decision"] == "Unknown" and all(g["blocks_all_sides"] for g in view["critical_gaps"])
    a = assessment(view, put="no_qualified_vertical", call="no_qualified_vertical")
    with pytest.raises(ValueError, match="missing evidence"):
        player.validate_assessment(a, view)


def test_explicit_qualification_failures_can_reject_without_inventing_market_facts():
    proof = {s: {"reason": "no_matching_instruments", "asof_ms": CUT, "evidence_ref": f"synthetic_search/{s}",
                 "source_sha256": "a" * 64, "side": s, "venue": "Deribit", "underlying": "BTC", "expiry_ms": E}
             for s in player.SIDES}
    view = player.build(None, None, cutoff_ms=CUT, unavailable=proof)
    jsonschema.validate(view, schema("structural_player_view"))
    player.validate_assessment(assessment(view, put="no_qualified_vertical", call="no_qualified_vertical"), view)
    proof["put"]["asof_ms"] = CUT + 1
    with pytest.raises(ValueError):
        player.build(None, None, cutoff_ms=CUT, unavailable=proof)
    proof["put"]["asof_ms"] = CUT
    proof["put"]["expiry_ms"] += H
    with pytest.raises(ValueError, match="expiry scopes"):
        player.build(None, None, cutoff_ms=CUT, unavailable=proof)


@pytest.mark.parametrize("kind", ["position", "space"])
def test_only_missing_primary_geometry_blocks_both_sides(kind):
    source = boards()
    for b in source.values():
        b["observations"] = [o for o in b["observations"] if o["kind"] != kind]
    view = player.build(**source)
    jsonschema.validate(view, schema("structural_player_view"))
    assert not view["coverage"]["at_least_one_side_assessable"]
    assert all(g["blocks_all_sides"] for g in view["critical_gaps"])
    player.validate_assessment(assessment(view, put="unassessable", call="unassessable"), view)


@pytest.mark.parametrize("mutation", ["generic_unknown", "wrong_ref", "flow_only", "hash", "flag", "action"])
def test_assessment_cannot_bypass_active_spatial_contract(mutation):
    view = player.build(**boards())
    a = assessment(view)
    if mutation == "generic_unknown":
        a["side_assessments"]["put"]["state"] = "unassessable"
    elif mutation == "wrong_ref":
        a["side_assessments"]["put"]["phenomenon_refs"] = [next(p["id"] for p in view["phenomena"] if p["sides"] == ["call"])]
    elif mutation == "flow_only":
        a["side_assessments"]["put"]["phenomenon_refs"] = [next(p["id"] for p in view["phenomena"] if p["kind"] == "flow" and p["sides"] == ["put"])]
    elif mutation == "hash":
        a["view_sha256"] = "0" * 64
    elif mutation == "flag":
        a["at_least_one_acceptable"] = False
    else:
        a["execution_authorized"] = True
    with pytest.raises(ValueError):
        player.validate_assessment(a, view)


@pytest.mark.parametrize("kind", ["accept", "reject"])
def test_saved_examples_replay_with_separate_assessment(kind):
    source = json.loads((EXAMPLES / f"{kind}_boards.json").read_text(encoding="utf-8"))
    assert source == boards(kind)
    view = player.build(**source)
    assert view == json.loads((EXAMPLES / f"{kind}_view.json").read_text(encoding="utf-8"))
    a = json.loads((EXAMPLES / f"{kind}_assessment.json").read_text(encoding="utf-8"))
    player.validate_assessment(a, view)
    jsonschema.validate(view, schema("structural_player_view"))
    jsonschema.validate(a, schema("structural_player_assessment"))


def test_cli_and_no_free_text_or_result_leak(tmp_path):
    source = boards()
    for b in source.values():
        b.update(score=99, decision="Accept", advice="LEAK_ME")
        for o in b["observations"]:
            o["description"] = "LEAK_ME"
    paths = []
    for side in player.SIDES:
        path = tmp_path / f"{side}.json"
        path.write_text(json.dumps(source[side]), encoding="utf-8")
        paths.append(path)
    out = tmp_path / "view.json"
    cmd = [sys.executable, str(ROOT / "tools/astra_structural_player_view_v1.py"),
           "--put", str(paths[0]), "--call", str(paths[1]), "--out", str(out)]
    subprocess.run(cmd, check=True, capture_output=True)
    assert "LEAK_ME" not in out.read_text(encoding="utf-8")
    frozen = out.read_bytes()
    assert subprocess.run(cmd, capture_output=True).returncode != 0 and out.read_bytes() == frozen
