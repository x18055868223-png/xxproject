import importlib.util
import json
from pathlib import Path
import subprocess
import types

from test_signal_evidence_v2 import AS_OF_MS, base_card, transition_for


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "signal_evidence_v2.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("signal_evidence_v211", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_head_tool():
    source = subprocess.check_output(
        ["git", "-C", str(ROOT), "show", "HEAD:tools/signal_evidence_v2.py"],
        encoding="utf-8",
    )
    module = types.ModuleType("signal_evidence_v2_head_for_v211")
    exec(compile(source, "HEAD:tools/signal_evidence_v2.py", "exec"),
         module.__dict__)
    return module


def facts_by_id(packet):
    return {item["id"]: item for item in packet["facts"]}


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def ms(minutes):
    return AS_OF_MS + minutes * 60000


def semantic_entry(source_ref, *, observed=None, generated=None,
                   fetched=None, available=None, basis="field_time"):
    return {
        "source_ref": source_ref,
        "observed_at_ms": observed,
        "generated_at_ms": generated,
        "fetched_at_ms": fetched,
        "available_at_ms": available,
        "time_basis": basis,
        "time_errors": [],
    }


def install_gex_time_semantics(card):
    gex = card["factor_cross_section"]["gex_info"]
    gex.update({
        "market_state": "positive_gamma",
        "net_gamma_notional_usd": 210000000.0,
        "dvol": 41.2,
        "n1": 97.0,
        "n2": 95.0,
        "p1": 103.0,
        "p2": 101.0,
        "call_wall": 101.0,
        "put_wall": 97.0,
        "flip_point": 101.0,
        "max_gamma_strike": 99.0,
        "gex_time_semantics": {
            "schema_version": "gex_time_semantics@1.0.0",
            "fields": {
                "gex_board.market_state": semantic_entry(
                    "gex-latest.profiles.total.meta.regime",
                    generated=ms(-6), fetched=ms(-5),
                    basis="profile_group_update_time"),
                "gex_board.total_net_gex": semantic_entry(
                    "gex-latest.total_gex",
                    generated=ms(-4), fetched=ms(-3),
                    basis="upstream_result_time"),
                "gex_board.dvol": semantic_entry(
                    "gex-latest.dvol",
                    observed=ms(-9), generated=ms(-8), fetched=ms(-3),
                    basis="dvol_native_time"),
                "gamma_exposure.n1": semantic_entry(
                    "gex-latest.profiles.total.walls.n1",
                    generated=ms(-20), fetched=ms(-18),
                    basis="profile_group_update_time"),
                "gamma_exposure.n2": semantic_entry(
                    "gex-latest.profiles.total.walls.n2",
                    generated=ms(-12), fetched=ms(-11),
                    basis="profile_group_update_time"),
                "gamma_exposure.p1": semantic_entry(
                    "gex-latest.profiles.total.walls.p1",
                    generated=ms(-10), fetched=ms(-9),
                    basis="profile_group_update_time"),
                "gamma_exposure.p2": semantic_entry(
                    "gex-latest.profiles.total.walls.p2",
                    generated=ms(-7), fetched=ms(-6),
                    basis="profile_group_update_time"),
                "gamma_exposure.flip_point": semantic_entry(
                    "gex-latest.flip_point",
                    generated=ms(-15), fetched=ms(-14),
                    basis="upstream_result_time"),
                "gamma_exposure.magnet_price": semantic_entry(
                    "gex-latest.profiles.total.meta.magnet_a1",
                    generated=ms(-13), fetched=ms(-12),
                    basis="profile_group_update_time"),
            },
        },
    })


def retime_gex_semantics(card, base_ms):
    fields = card["factor_cross_section"]["gex_info"]["gex_time_semantics"]["fields"]
    for entry in fields.values():
        if entry.get("observed_at_ms") is not None:
            entry["observed_at_ms"] = base_ms - 90000
        if entry.get("generated_at_ms") is not None:
            entry["generated_at_ms"] = base_ms - 80000
        if entry.get("fetched_at_ms") is not None:
            entry["fetched_at_ms"] = base_ms - 70000
        if entry.get("available_at_ms") is not None:
            entry["available_at_ms"] = base_ms - 60000


def test_default_v211_fact_and_provenance_contract(tool):
    packet = tool.build_evidence_packet(base_card())
    assert_true(packet["schema"] == "signal_evidence_packet@2.1.1",
                "default packet schema")
    for fact in packet["facts"]:
        assert_true(tuple(fact.keys()) == tool.FACT_KEYS,
                    "v2.1.1 fact key contract")
        assert_true(fact["available_at_ms"] > 0,
                    "available time must be positive")
        assert_true(tuple(fact["provenance"].keys()) == tool.PROVENANCE_KEYS,
                    "v2.1.1 provenance key contract")


def test_explicit_old_packet_schemas_are_head_exact(tool):
    card = base_card()
    install_gex_time_semantics(card)
    head = load_head_tool()

    for schema in ("signal_evidence_packet@2.0.0",
                   "signal_evidence_packet@2.1.0"):
        expected = head.build_evidence_packet(
            card,
            packet_schema=None if schema == "signal_evidence_packet@2.1.0" else schema,
        )
        actual = tool.build_evidence_packet(card, packet_schema=schema)
        assert_true(actual == expected, f"{schema} exact packet")
        assert_true(tool.packet_hash(actual) == head.packet_hash(expected),
                    f"{schema} exact hash")


def test_gex_field_times_use_selected_wall_aliases(tool):
    card = base_card(price=100.0)
    install_gex_time_semantics(card)

    facts = facts_by_id(tool.build_evidence_packet(card))
    call_wall = facts["structure.gamma.call_wall"]
    put_wall = facts["structure.gamma.put_wall"]
    net = facts["structure.gex.net_gamma_notional_usd"]
    dvol = facts["pressure.volatility.dvol"]

    assert_true(call_wall["value"] == 101.0, "selected call wall value")
    assert_true(call_wall["provenance"]["selected_source"].endswith("walls.p2"),
                "call wall should use the selected p2 time entry")
    assert_true(call_wall["provenance"]["generated_at_ms"] == ms(-7),
                "call wall generated time")
    assert_true(put_wall["value"] == 97.0, "selected put wall value")
    assert_true(put_wall["provenance"]["selected_source"].endswith("walls.n1"),
                "put wall should use the selected n1 time entry")
    assert_true(put_wall["provenance"]["generated_at_ms"] == ms(-20),
                "put wall generated time")
    assert_true(net["provenance"]["observed_at_ms"] is None,
                "net GEX has generated time but no true observation time")
    assert_true(net["provenance"]["generated_at_ms"] == ms(-4),
                "net GEX generated time")
    assert_true(dvol["provenance"]["observed_at_ms"] == ms(-9),
                "DVOL should keep its own observation time")


def test_gex_wall_aliases_support_mirror_selection(tool):
    card = base_card(price=100.0)
    install_gex_time_semantics(card)
    gex = card["factor_cross_section"]["gex_info"]
    gex.update({
        "n1": 96.0,
        "n2": 99.0,
        "p1": 104.0,
        "p2": 105.0,
        "call_wall": 104.0,
        "put_wall": 99.0,
    })

    facts = facts_by_id(tool.build_evidence_packet(card))
    call_wall = facts["structure.gamma.call_wall"]
    put_wall = facts["structure.gamma.put_wall"]

    assert_true(call_wall["value"] == 104.0, "mirror call wall value")
    assert_true(call_wall["provenance"]["selected_source"].endswith("walls.p1"),
                "mirror call wall should use p1 time entry")
    assert_true(call_wall["provenance"]["generated_at_ms"] == ms(-10),
                "mirror call wall generated time")
    assert_true(put_wall["value"] == 99.0, "mirror put wall value")
    assert_true(put_wall["provenance"]["selected_source"].endswith("walls.n2"),
                "mirror put wall should use n2 time entry")
    assert_true(put_wall["provenance"]["generated_at_ms"] == ms(-12),
                "mirror put wall generated time")


def test_gex_explicit_alias_time_metadata_wins_over_same_value_fields(tool):
    card = base_card(price=100.0)
    install_gex_time_semantics(card)
    gex = card["factor_cross_section"]["gex_info"]
    fields = gex["gex_time_semantics"]["fields"]
    gex.update({
        "n2": 101.0,
        "p2": 101.0,
        "call_wall": 101.0,
    })
    fields["gamma_exposure.call_wall"] = semantic_entry(
        "gex-latest.selected.upper_wall",
        generated=ms(-2), fetched=ms(-1),
        basis="selected_wall_alias_time")
    fields["gamma_exposure.n2"]["generated_at_ms"] = ms(4)
    fields["gamma_exposure.n2"]["fetched_at_ms"] = ms(5)

    facts = facts_by_id(tool.build_evidence_packet(card))
    call_wall = facts["structure.gamma.call_wall"]

    assert_true(call_wall["usable"] is True,
                "explicit alias should avoid same-value future field")
    assert_true(call_wall["provenance"]["selected_source"].endswith("selected.upper_wall"),
                "explicit selected wall source should be used first")
    assert_true(call_wall["provenance"]["generated_at_ms"] == ms(-2),
                "explicit alias generated time")


def test_gex_same_value_wall_fields_merge_time_limits_conservatively(tool):
    card = base_card(price=100.0)
    install_gex_time_semantics(card)
    gex = card["factor_cross_section"]["gex_info"]
    fields = gex["gex_time_semantics"]["fields"]
    gex.update({
        "n2": 101.0,
        "p2": 101.0,
        "call_wall": 101.0,
    })
    fields["gamma_exposure.n2"]["generated_at_ms"] = ms(3)
    fields["gamma_exposure.n2"]["fetched_at_ms"] = ms(4)

    facts = facts_by_id(tool.build_evidence_packet(card))
    call_wall = facts["structure.gamma.call_wall"]

    assert_true(call_wall["usable"] is False,
                "conflicting same-value fields should keep the latest time limit")
    assert_true("walls.n2" in call_wall["provenance"]["selected_source"]
                and "walls.p2" in call_wall["provenance"]["selected_source"],
                "merged provenance should name both matching fields")
    assert_true(call_wall["provenance"]["generated_at_ms"] == ms(3),
                "merged generated time should be the latest matching time")
    assert_true(any("晚于卡片记录时间" in item
                    for item in call_wall["provenance"]["time_errors"]),
                "merged future time should be visible")


def test_gex_without_semantics_does_not_forge_observed_time(tool):
    card = base_card()
    gex = card["factor_cross_section"]["gex_info"]
    gex.update({
        "observed_at_ms": ms(-4),
        "fetched_at_ms": ms(-3),
        "age_ms": 180000,
        "net_gamma_notional_usd": 210000000.0,
        "market_state": "positive_gamma",
        "dvol": 40.0,
    })

    facts = facts_by_id(tool.build_evidence_packet(card))
    for fact_id in (
            "structure.gex.market_state",
            "structure.gex.net_gamma_notional_usd",
            "pressure.volatility.dvol"):
        fact = facts[fact_id]
        assert_true(fact["observed_at_ms"] is None,
                    f"{fact_id} should not borrow old observed/fetch/card time")
        assert_true(fact["provenance"]["observed_at_ms"] is None,
                    f"{fact_id} provenance observed time")
        assert_true(fact["provenance"]["fetched_at_ms"] == ms(-3),
                    f"{fact_id} keeps fetched time as fetched only")
        assert_true(fact["available_at_ms"] == AS_OF_MS,
                    f"{fact_id} uses card archive as availability upper bound")
        assert_true("without_gex_field_time_semantics" in fact["provenance"]["time_basis"],
                    f"{fact_id} states missing field semantics")
        assert_true(fact["usable"] is True, f"{fact_id} stays usable")


def test_future_gex_time_closes_only_dependent_facts(tool):
    card = base_card(price=100.0)
    install_gex_time_semantics(card)
    fields = card["factor_cross_section"]["gex_info"]["gex_time_semantics"]["fields"]
    fields["gamma_exposure.p2"]["generated_at_ms"] = ms(3)
    fields["gamma_exposure.p2"]["fetched_at_ms"] = ms(4)

    facts = facts_by_id(tool.build_evidence_packet(card))
    call_wall = facts["structure.gamma.call_wall"]
    call_distance = facts["structure.distance.call_wall_pct"]
    put_wall = facts["structure.gamma.put_wall"]
    put_distance = facts["structure.distance.put_wall_pct"]

    assert_true(call_wall["usable"] is False,
                "future call wall time should close the source fact")
    assert_true(call_distance["usable"] is False,
                "future call wall time should propagate to distance")
    assert_true(any("晚于卡片记录时间" in item
                    for item in call_wall["provenance"]["time_errors"]),
                "future call wall should carry a readable time error")
    assert_true(put_wall["usable"] is True and put_distance["usable"] is True,
                "valid put wall should not be affected by call wall time")


def test_change_fact_inherits_previous_and_current_time_errors(tool):
    previous = base_card("PREV-TIME", AS_OF_MS - 600000, price=100.0)
    current = base_card("CURR-TIME", AS_OF_MS, price=101.0)
    install_gex_time_semantics(previous)
    install_gex_time_semantics(current)
    retime_gex_semantics(previous, previous["identity"]["confirmed_time_ms"])
    retime_gex_semantics(current, current["identity"]["confirmed_time_ms"])
    prev_fields = previous["factor_cross_section"]["gex_info"]["gex_time_semantics"]["fields"]
    prev_fields["gamma_exposure.p2"]["generated_at_ms"] = (
        previous["identity"]["confirmed_time_ms"] + 60000
    )
    transition = transition_for(tool, previous, current)

    facts = facts_by_id(tool.build_evidence_packet(current, previous, transition))
    change = facts["change.structure.call_wall_delta_pct"]
    assert_true(change["usable"] is False,
                "previous future wall time should close the change fact")
    assert_true(any("晚于卡片记录时间" in item
                    for item in change["provenance"]["time_errors"]),
                "change fact should keep inherited previous time error")
    assert_true(facts["change.structure.put_wall_delta_pct"]["usable"] is True,
                "unrelated put wall change should remain usable")


def test_malformed_gex_time_closes_that_fact(tool):
    card = base_card()
    install_gex_time_semantics(card)
    fields = card["factor_cross_section"]["gex_info"]["gex_time_semantics"]["fields"]
    fields["gex_board.total_net_gex"]["generated_at_ms"] = "not-a-time"

    facts = facts_by_id(tool.build_evidence_packet(card))
    net = facts["structure.gex.net_gamma_notional_usd"]
    dvol = facts["pressure.volatility.dvol"]
    assert_true(net["usable"] is False,
                "malformed generated time should close net GEX")
    assert_true(any("无法解析" in item
                    for item in net["provenance"]["time_errors"]),
                "malformed time should be recorded")
    assert_true(dvol["usable"] is True,
                "malformed net GEX time should not close DVOL")


def test_cache_attempt_time_does_not_override_field_times(tool):
    card = base_card()
    install_gex_time_semantics(card)
    gex = card["factor_cross_section"]["gex_info"]
    gex["latest_attempt_at"] = "2099-01-01T00:00:00+00:00"
    gex["last_error"] = "temporary upstream failure after cached value"

    facts = facts_by_id(tool.build_evidence_packet(card))
    net = facts["structure.gex.net_gamma_notional_usd"]
    assert_true(net["provenance"]["generated_at_ms"] == ms(-4),
                "cached field should keep its own generated time")
    assert_true(net["available_at_ms"] == AS_OF_MS,
                "cache latest attempt should not become fact availability")
    assert_true(net["usable"] is True,
                "future latest attempt metadata must not close cached field")


def run_all():
    tool = load_tool()
    card = base_card()
    install_gex_time_semantics(card)
    card["factor_cross_section"]["gex_info"]["gex_time_semantics"]["schema_version"] = "gex_time_semantics@9.0.0"
    unknown_facts = facts_by_id(tool.build_evidence_packet(card))
    assert_true(not unknown_facts["structure.gex.net_gamma_notional_usd"]["usable"],
                "unknown time protocol cannot silently become fetch-only legacy input")
    card = base_card(price=100.0)
    install_gex_time_semantics(card)
    gex = card["factor_cross_section"]["gex_info"]
    gex.update(n2=101.0, p2=101.0, call_wall=101.0)
    fields = gex["gex_time_semantics"]["fields"]
    fields["gamma_exposure.n2"]["observed_at_ms"] = ms(-3)
    merged = facts_by_id(tool.build_evidence_packet(card))["structure.gamma.call_wall"]
    assert_true(merged["usable"] and merged["observed_at_ms"] is None,
                "ambiguous observation can remain usable with honest unknown time")
    fields["gamma_exposure.n2"]["observed_at_ms"] = ms(2)
    merged = facts_by_id(tool.build_evidence_packet(card))["structure.gamma.call_wall"]
    assert_true(not merged["usable"], "ambiguous future observation must still constrain availability")
    test_default_v211_fact_and_provenance_contract(tool)
    test_explicit_old_packet_schemas_are_head_exact(tool)
    test_gex_field_times_use_selected_wall_aliases(tool)
    test_gex_wall_aliases_support_mirror_selection(tool)
    test_gex_explicit_alias_time_metadata_wins_over_same_value_fields(tool)
    test_gex_same_value_wall_fields_merge_time_limits_conservatively(tool)
    test_gex_without_semantics_does_not_forge_observed_time(tool)
    test_future_gex_time_closes_only_dependent_facts(tool)
    test_change_fact_inherits_previous_and_current_time_errors(tool)
    test_malformed_gex_time_closes_that_fact(tool)
    test_cache_attempt_time_does_not_override_field_times(tool)
    print("signal_evidence_v211_time: PASS")


if __name__ == "__main__":
    run_all()
