import importlib.util
import json
import pathlib
import sys
import tempfile

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SIGNAL_FILE = (
    ROOT / "demo" / "\u6700\u65b0\u4ea4\u4ed8\u7269" /
    "neutral_regulation_demo_fmz.py"
)
BASE_MS = 1_798_200_000_000


def load_signal_module():
    spec = importlib.util.spec_from_file_location(
        "nrd_signal_fmz_20_space_gate", SIGNAL_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod():
    return load_signal_module()


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(
            "{}: expected {!r}, got {!r}".format(message, expected, actual))


def base_config(mod):
    cfg = dict(mod.CONFIG)
    cfg["space_gate_net_gamma_fresh_ms"] = 600_000
    return cfg


def anchor(mod, score=72.0, state=None, source_ts=BASE_MS - 60_000,
           freshness="FRESH"):
    return {
        "state": state or mod.STATE_VALID,
        "facts": {
            "anchor_gravity_ref_score": score,
            "gex_source_ts_ms": source_ts,
            "freshness": freshness,
        },
    }


def gex_info(mod, total=250_000_000.0, source_ts=BASE_MS - 60_000,
             quality=None, stale=False, data_state="live",
             availability="ready", asset="BTC", time_basis="source_observation_time",
             include_observed=True, include_generated=False,
             include_fetched=False):
    entry = {
        "source_ref": "gex-latest.total_gex",
        "time_basis": time_basis,
        "time_errors": [],
    }
    if include_observed:
        entry["observed_at_ms"] = source_ts
    if include_generated:
        entry["generated_at_ms"] = source_ts
    if include_fetched:
        entry["fetched_at_ms"] = source_ts
    return {
        "asset": asset,
        "quality": quality or mod.QUALITY_OK,
        "stale": stale,
        "data_state": data_state,
        "availability": availability,
        "total_net_gex": total,
        "market_state": "positive_gamma",
        "gex_time_semantics": {
            "schema_version": "gex_time_semantics@1.0.0",
            "fields": {
                "gex_board.total_net_gex": entry,
            },
        },
    }


def gamma(regime="POSITIVE_GAMMA_PINNING", data_state="OK",
          proxy=0.42, reason_codes=None, agrees=True):
    return {
        "regime": regime,
        "data_state": data_state,
        "net_gamma_notional": proxy,
        "gex_info_agrees": agrees,
        "reason_codes": list(reason_codes or []),
    }


def gate(mod, cfg=None, a=None, g=None, gg=None, observed=BASE_MS):
    cfg = cfg or base_config(mod)
    return mod.build_space_gate_snapshot(
        a or anchor(mod),
        g if g is not None else gex_info(mod),
        gg if gg is not None else gamma(),
        cfg,
        observed_at_ms=observed)


def assert_shape(mod, snapshot):
    assert_true(isinstance(snapshot.get("anchor"), dict),
                "space_gate anchor is native dict")
    assert_true(isinstance(snapshot.get("net_gamma"), dict),
                "space_gate net_gamma is native dict")
    for key in (
            "state", "observed_at_ms", "since_ts_ms", "previous_state",
            "last_transition_ts_ms", "reason_codes"):
        assert_true(key in snapshot, "space_gate has " + key)
    net = snapshot["net_gamma"]
    for key in (
            "value", "unit", "sign", "source", "source_ts_ms", "age_ms",
            "valid", "transition", "conflict", "ok"):
        assert_true(key in net, "net_gamma has " + key)
    assert_true(snapshot["state"] in mod.SPACE_GATE_STATES,
                "space_gate state vocabulary")


def test_g01_eligible_uses_positive_fresh_observed_net_gamma(mod):
    snapshot = gate(mod)
    assert_shape(mod, snapshot)
    assert_equal(snapshot["state"], mod.SPACE_GATE_ELIGIBLE,
                 "G01 eligible state")
    assert_equal(snapshot["reason_codes"], [], "G01 no block reason")
    assert_equal(snapshot["anchor"]["ok"], True, "G01 anchor ok")
    net = snapshot["net_gamma"]
    assert_equal(net["ok"], True, "G01 net gamma ok")
    assert_equal(net["unit"], "USD", "G01 net unit")
    assert_equal(net["sign"], "POSITIVE", "G01 sign contract")
    assert_equal(net["source"], "gexmonitorapi.gex_board.total_net_gex",
                 "G01 source contract")
    assert_equal(net["source_ts_ms"], BASE_MS - 60_000,
                 "G01 source timestamp")
    assert_equal(net["age_ms"], 60_000, "G01 freshness recomputed")


def test_g02_anchor_must_be_valid_and_score_60(mod):
    low = gate(mod, a=anchor(mod, score=59.99))
    assert_equal(low["state"], mod.SPACE_GATE_BLOCKED,
                 "G02 low score blocks")
    assert_true("SPACE_GATE_ANCHOR_SCORE_LOW" in low["reason_codes"],
                "G02 low score reason")
    invalid = gate(mod, a=anchor(mod, state=mod.STATE_WEAK, score=80.0))
    assert_equal(invalid["state"], mod.SPACE_GATE_BLOCKED,
                 "G02 weak anchor blocks")
    assert_true("SPACE_GATE_ANCHOR_NOT_VALID" in invalid["reason_codes"],
                "G02 weak anchor reason")


def test_g03_gex_info_identity_and_field_freshness(mod):
    # 2.0.4: payload-level quality/stale/data_state/availability no longer gate;
    # the gate reads only total_net_gex and that field's own source clock.
    for gex in (gex_info(mod, quality=mod.QUALITY_STALE), gex_info(mod, stale=True),
                gex_info(mod, data_state="lkgv_cache"),
                gex_info(mod, availability="missing")):
        snapshot = gate(mod, g=gex)
        assert_equal(snapshot["state"], mod.SPACE_GATE_ELIGIBLE,
                     "G03 payload-level status does not gate a fresh field")
    cases = (
        (gex_info(mod, asset="ETH"), "SPACE_GATE_GEX_INFO_ASSET_MISMATCH"),
        (gex_info(mod, source_ts=BASE_MS - 3_600_000), "SPACE_GATE_NET_GAMMA_NOT_FRESH"),
    )
    for gex, reason in cases:
        snapshot = gate(mod, g=gex)
        assert_equal(snapshot["state"], mod.SPACE_GATE_BLOCKED,
                     "G03 invalid gex_info blocks")
        assert_true(reason in snapshot["reason_codes"],
                    "G03 reason " + reason)


def test_g04_positive_regime_cannot_replace_positive_number(mod):
    missing = gate(mod, g=gex_info(mod, total=None))
    assert_equal(missing["state"], mod.SPACE_GATE_BLOCKED,
                 "G04 missing total_net_gex blocks")
    assert_true("SPACE_GATE_NET_GAMMA_MISSING" in missing["reason_codes"],
                "G04 missing numeric reason")
    assert_equal(missing["net_gamma"]["sign"], "UNKNOWN",
                 "G04 unknown sign")
    zero = gate(mod, g=gex_info(mod, total=0.0))
    assert_equal(zero["state"], mod.SPACE_GATE_BLOCKED,
                 "G04 zero total_net_gex blocks")
    assert_equal(zero["net_gamma"]["sign"], "ZERO", "G04 zero sign")
    negative = gate(mod, g=gex_info(mod, total=-1.0))
    assert_equal(negative["state"], mod.SPACE_GATE_BLOCKED,
                 "G04 negative total_net_gex blocks")
    assert_equal(negative["net_gamma"]["sign"], "NEGATIVE",
                 "G04 negative sign")


def test_g05_freshness_uses_source_time_not_fetch_time(mod):
    stale_source = gate(mod, g=gex_info(mod, source_ts=BASE_MS - 900_000))
    assert_equal(stale_source["state"], mod.SPACE_GATE_BLOCKED,
                 "G05 stale observed source blocks")
    assert_true("SPACE_GATE_NET_GAMMA_NOT_FRESH" in stale_source["reason_codes"],
                "G05 stale reason")
    fetch_only = gex_info(
        mod,
        source_ts=BASE_MS - 1_000,
        time_basis="fetched_time_only_observation_unknown",
        include_observed=False,
        include_fetched=True)
    fetched_snapshot = gate(mod, g=fetch_only)
    assert_equal(fetched_snapshot["state"], mod.SPACE_GATE_BLOCKED,
                 "G05 fetched-only time blocks")
    assert_true("SPACE_GATE_NET_GAMMA_SOURCE_TS_MISSING"
                in fetched_snapshot["reason_codes"],
                "G05 fetched-only is not source time")
    generic_time_only = gex_info(mod, include_observed=False)
    generic_time_only["observed_at_ms"] = BASE_MS - 1_000
    generic_snapshot = gate(mod, g=generic_time_only)
    assert_equal(generic_snapshot["state"], mod.SPACE_GATE_BLOCKED,
                 "G05 generic payload observation cannot time the net-gex field")


def test_g06_valid_negative_deribit_proxy_conflicts_with_positive_gex(mod):
    conflict = gate(mod, gg=gamma(proxy=-0.11))
    assert_equal(conflict["state"], mod.SPACE_GATE_BLOCKED,
                 "G06 negative proxy conflict blocks")
    assert_equal(conflict["net_gamma"]["conflict"], True,
                 "G06 conflict flag")
    assert_true("SPACE_GATE_GGR_CONFLICT" in conflict["reason_codes"],
                "G06 conflict reason")
    stale_proxy = gate(
        mod,
        gg=gamma(proxy=-0.11, reason_codes=["GGR_GREEKS_STALE"]))
    assert_equal(stale_proxy["state"], mod.SPACE_GATE_ELIGIBLE,
                 "G06 stale negative proxy is not a valid conflict")


def test_g07_ggr_transition_data_state_and_reported_conflict_block(mod):
    transition = gate(mod, gg=gamma(regime="TRANSITION"))
    assert_equal(transition["state"], mod.SPACE_GATE_BLOCKED,
                 "G07 transition blocks")
    assert_equal(transition["net_gamma"]["transition"], True,
                 "G07 transition flag")
    bad_data = gate(mod, gg=gamma(data_state="MISSING"))
    assert_equal(bad_data["state"], mod.SPACE_GATE_BLOCKED,
                 "G07 bad GGR data_state blocks")
    disagree = gate(mod, gg=gamma(reason_codes=["GGR_AGG_NET_GEX_DISAGREES"]))
    assert_equal(disagree["state"], mod.SPACE_GATE_BLOCKED,
                 "G07 reported GGR conflict blocks")


def test_g08_persistence_restart_and_tick_snapshot_native_fields(mod):
    with tempfile.TemporaryDirectory() as tmp:
        cfg = base_config(mod)
        state_path = pathlib.Path(tmp) / "space_gate_state.json"
        cfg["space_gate_state_file"] = str(state_path)
        tracker = mod.SpaceGateStateTracker(cfg)
        first = tracker.update(gate(mod, cfg=cfg, observed=BASE_MS))
        assert_equal(first["state"], mod.SPACE_GATE_ELIGIBLE,
                     "G08 initial eligible")
        assert_equal(first["since_ts_ms"], BASE_MS, "G08 first since")
        assert_equal(first["previous_state"], None, "G08 no fake previous")
        assert_equal(first["last_transition_ts_ms"], None,
                     "G08 no fake transition")

        restarted = mod.SpaceGateStateTracker(cfg)
        second = restarted.update(gate(mod, cfg=cfg, observed=BASE_MS + 1000))
        assert_equal(second["since_ts_ms"], BASE_MS,
                     "G08 restart preserves since")
        assert_equal(second["last_transition_ts_ms"], None,
                     "G08 restart does not invent transition")

        blocked = restarted.update(gate(
            mod, cfg=cfg, a=anchor(mod, score=50.0),
            observed=BASE_MS + 2000))
        assert_equal(blocked["state"], mod.SPACE_GATE_BLOCKED,
                     "G08 state changes to blocked")
        assert_equal(blocked["previous_state"], mod.SPACE_GATE_ELIGIBLE,
                     "G08 previous state")
        assert_equal(blocked["last_transition_ts_ms"], BASE_MS + 2000,
                     "G08 real transition time")

        tick_cfg = dict(mod.CONFIG)
        tick_cfg.update({
            "logs_dir": tmp,
            "space_gate_state_file": str(pathlib.Path(tmp) /
                                         "tick_space_gate_state.json"),
            "startup_log_enabled": False,
            "tick_summary_log_enabled": False,
            "state_change_log_enabled": False,
            "log_status_enabled": False,
            "chart_enabled": False,
            "signal_review_enabled": False,
            "fixed_analysis_round_enabled": False,
            "live_fetch_enabled": False,
            "offline_fixture_enabled": True,
        })
        runtime = mod.DemoRuntime(tick_cfg)
        decision, _modules = runtime.tick(live_fetch=False)
        decision_gate = decision["factor_snapshot"]["space_gate"]
        assert_shape(mod, decision_gate)
        assert_equal(
            decision["contract_audit"]["checks"][
                "factor_snapshot_space_gate_native"],
            True,
            "G08 producer contract sees native space_gate")
        snapshot_path = pathlib.Path(tmp) / "snapshots.jsonl"
        rows = [
            json.loads(line)
            for line in snapshot_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert_true(rows, "G08 tick writes EvaluationSnapshot jsonl")
        written_gate = rows[-1]["factor_snapshot"]["space_gate"]
        assert_shape(mod, written_gate)
        assert_equal(written_gate["net_gamma"]["source"],
                     "gexmonitorapi.gex_board.total_net_gex",
                     "G08 written source contract")


def main():
    mod = load_signal_module()
    assert_equal(mod.CONFIG["demo_version"], "2.0.5",
                 "FMZ producer version")
    test_g01_eligible_uses_positive_fresh_observed_net_gamma(mod)
    test_g02_anchor_must_be_valid_and_score_60(mod)
    test_g03_gex_info_identity_and_field_freshness(mod)
    test_g04_positive_regime_cannot_replace_positive_number(mod)
    test_g05_freshness_uses_source_time_not_fetch_time(mod)
    test_g06_valid_negative_deribit_proxy_conflicts_with_positive_gex(mod)
    test_g07_ggr_transition_data_state_and_reported_conflict_block(mod)
    test_g08_persistence_restart_and_tick_snapshot_native_fields(mod)
    print("fmz_20_space_gate: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("fmz_20_space_gate: FAIL - " + str(exc))
        sys.exit(1)
