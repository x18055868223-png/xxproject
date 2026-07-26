import datetime
import importlib.util
import json
import pathlib
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
SIGNAL_FILE = (
    ROOT / "demo" / "\u6700\u65b0\u4ea4\u4ed8\u7269" /
    "neutral_regulation_demo_fmz.py"
)


def load_signal_module():
    spec = importlib.util.spec_from_file_location(
        "nrd_signal_fmz_157_fixed_round", SIGNAL_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(
            "{}: expected {!r}, got {!r}".format(message, expected, actual))


def utc8_ms(year, month, day, hour, minute, second=0):
    tz = datetime.timezone(datetime.timedelta(hours=8))
    dt = datetime.datetime(
        year, month, day, hour, minute, second, tzinfo=tz)
    return int(dt.timestamp() * 1000)


def base_edb(mod, direction="NEUTRAL", confidence=39):
    return {
        "factor_name": "EDB",
        "precondition": {"nr_active": False, "nr_state": "NR_IDLE"},
        "edb_score": 0.0,
        "edb_score_raw": 0.0,
        "agreement": 0.70,
        "coverage": 0.90,
        "confidence": confidence,
        "calibration_state": "UNCALIBRATED",
        "confidence_decomposition": {
            "strength": 0.49,
            "agr_factor": 0.70,
            "cov_factor": 0.90,
            "ggr_mult": 1.0,
            "confidence_final": confidence,
            "score_full": 0.75,
            "agreement_floor": 0.6,
            "coverage_floor": 0.5,
        },
        "lean": direction,
        "side_hint": "none",
        "support_label": "WAIT_CONFIRMATION",
        "next_action": "WAIT_FOR_EVIDENCE",
        "conflict_level": "MILD",
        "veto_reason": None,
        "evidence": [{
            "key": "TMV",
            "vote": -0.5,
            "weight": 0.34,
            "eff_weight": 0.34,
            "info": 1.0,
            "participation_status": "ACTIVE",
            "detail": {"tmv_blend": -0.5},
        }],
        "summary_cn": "测试截面：等待确认。",
    }


def base_factor_snapshot(mod):
    edb = base_edb(mod)
    return {
        "edb": edb,
        "flow": {
            "direction": mod.DIRECTION_BEARISH,
            "tmv_blend": -0.5,
            "last_funding_rate": -0.000004,
            "tmvf_funding_effect": "neutral",
            "micro_flow": {
                "fast_4h": {
                    "data_ready": True,
                    "cvd_norm": 0.02,
                    "cvd_sum": 13.4,
                    "price_return_pct": 0.1,
                },
            },
        },
        "macro_pressure": {
            "macro_score": 0.0,
            "macro_regime": "NEUTRAL",
            "data_status": "full_live",
        },
        "gamma_regime": {
            "regime": "TRANSITION",
            "confidence_multiplier": 1.0,
            "veto": False,
        },
        "skew": {"vote": 0.0, "data_state": "OK"},
    }


def runtime_facts(snapshot_ms):
    return {
        "current_price": 65609.02,
        "current_price_source": "test",
        "snapshot_collected_time_ms": snapshot_ms,
    }


def test_fixed_round_slot_context(mod, config):
    assert_true(
        mod.build_fixed_analysis_round_context(
            utc8_ms(2026, 7, 24, 22, 59, 59), config) is None,
        "22:59:59 BJT must not trigger")
    ctx = mod.build_fixed_analysis_round_context(
        utc8_ms(2026, 7, 24, 23, 0, 0), config)
    assert_equal(ctx["slot"], "20260724_2300_bjt", "fixed slot id")
    assert_equal(ctx["episode_id"], "fixed_us_round_20260724_2300_bjt",
                 "fixed deterministic episode")
    assert_equal(ctx["scheduled_time_utc8"], "2026-07-24T23:00:00+08:00",
                 "fixed scheduled BJT time")
    assert_equal(ctx["ny_reference_label"], "11:00 EDT",
                 "summer BJT 23:00 maps to 11:00 EDT")
    assert_true(ctx["audit_only"], "fixed round is audit-only")
    assert_true(ctx["does_not_override_producer_decision"],
                "fixed round cannot override producer")
    assert_true(
        mod.build_fixed_analysis_round_context(
            utc8_ms(2026, 7, 24, 23, 4, 59), config) is not None,
        "23:04:59 BJT remains inside trigger window")
    assert_true(
        mod.build_fixed_analysis_round_context(
            utc8_ms(2026, 7, 24, 23, 5, 0), config) is None,
        "23:05:00 BJT must not trigger")

    winter = mod.build_fixed_analysis_round_context(
        utc8_ms(2026, 1, 15, 23, 0, 0), config)
    assert_equal(winter["ny_reference_label"], "10:00 EST",
                 "winter anchor remains BJT 23:00, not forced 11:00 EST")


def test_pure_fixed_round_card_contract(mod, config):
    snapshot_ms = utc8_ms(2026, 7, 24, 23, 2, 30)
    ctx = mod.build_fixed_analysis_round_context(snapshot_ms, config)
    tracker = mod.SignalEventTracker(config)
    nr = {
        "state": "NR_IDLE",
        "is_active": False,
        "event_context": {},
        "anchor_context": {"anchor_score": 51.0},
    }
    recorded = tracker.maybe_record_fixed_round(
        nr, base_factor_snapshot(mod), runtime_facts(snapshot_ms), ctx)
    assert_true(recorded, "pure fixed round should bypass only the signal trigger")
    assert_equal(len(tracker.events), 1, "pure fixed round creates one card")

    card = tracker.events[0]
    assert_equal(card["episode_id"], ctx["episode_id"],
                 "pure fixed episode is deterministic")
    assert_equal(card["confirmed_time"], ctx["scheduled_time_ms"],
                 "pure fixed full card id uses deterministic 23:00 time")
    assert_equal(card["window"]["is_active"], False,
                 "fixed round must not fake active Anchor+DIE")
    assert_equal(card["window"]["nr_state"], "NR_IDLE",
                 "fixed round preserves producer timing state")
    assert_true("FIXED_ROUND_ANALYSIS" in card["tags"],
                "producer card carries fixed tag")

    record = mod.build_audit_record(card, config)
    assert_equal(record["schema"]["record_type"], "fixed_analysis_round_audit",
                 "pure fixed record type")
    assert_equal(record["identity"]["event_type"], "FIXED_ANALYSIS_ROUND",
                 "pure fixed event type")
    assert_true("FIXED_ROUND_ANALYSIS" in record["identity"]["tags"],
                "audit identity carries fixed tag")
    assert_equal(record["analysis_round"]["slot"], ctx["slot"],
                 "producer-native analysis_round is retained")
    assert_equal(record["analysis_round"]["bypassed_gate"],
                 "DIE_ANCHOR_TRIGGER_ONLY",
                 "fixed round bypass scope is narrow")
    assert_equal(record["decision"]["confidence"], 39,
                 "producer confidence is not rewritten")
    assert_equal(record["decision"]["trade_allowed"], False,
                 "fixed round remains read-only")
    assert_equal(record["decision_matrix"]["execution_allowed"], False,
                 "fixed round cannot authorize execution")
    assert_equal(record["decision_matrix"]["window"],
                 "NOT_REQUIRED_FOR_FIXED_ANALYSIS",
                 "fixed round must not claim Anchor+DIE confirmation")
    assert_true(record["identity"]["card_id"].startswith(
        "20260724T230000+0800-BTC-fixed_us_round_20260724_2300_bjt-"),
        "full id is deterministic by fixed slot")


def test_fixed_round_dedup_and_regular_signal_merge(mod, config):
    snapshot_ms = utc8_ms(2026, 7, 24, 23, 1, 0)
    ctx = mod.build_fixed_analysis_round_context(snapshot_ms, config)
    old_now = mod.now_ms
    mod.now_ms = lambda: snapshot_ms
    try:
        tracker = mod.SignalEventTracker(config)
        nr = {
            "state": "NR_REPAIR_CONFIRMED",
            "is_active": True,
            "event_context": {
                "episode_id": "nr_regular_same_tick",
                "episode_direction": "DOWN",
            },
            "anchor_context": {"anchor_score": 72.0},
        }
        assert_true(
            tracker.maybe_record(nr, base_factor_snapshot(mod),
                                 runtime_facts(snapshot_ms)),
            "regular signal should record first")
        assert_true(
            tracker.maybe_record_fixed_round(
                nr, base_factor_snapshot(mod), runtime_facts(snapshot_ms), ctx),
            "fixed round should merge into same-tick regular card")
        assert_equal(len(tracker.events), 1,
                     "same tick regular+fixed must stay one card")
        card = tracker.events[0]
        assert_equal(card["episode_id"], "nr_regular_same_tick",
                     "regular episode remains authoritative")
        assert_equal(card["analysis_round"]["merged_with_regular_signal"], True,
                     "analysis_round records merge")
        record = mod.build_audit_record(card, config)
        assert_equal(record["identity"]["event_type"], "NR_REPAIR_CONFIRMED",
                     "merged regular event_type remains unchanged")
        assert_equal(record["schema"]["record_type"],
                     "confirmed_signal_event_audit",
                     "merged regular record_type remains unchanged")
        assert_true("FIXED_ROUND_ANALYSIS" in record["identity"]["tags"],
                    "merged card still carries fixed tag")

        assert_equal(
            tracker.maybe_record_fixed_round(
                nr, base_factor_snapshot(mod), runtime_facts(snapshot_ms), ctx),
            False,
            "same fixed slot must not generate duplicate cards")
    finally:
        mod.now_ms = old_now


def test_runtime_fixed_round_state_records_json_and_push_done(mod, config):
    with tempfile.TemporaryDirectory() as tempdir:
        cfg = dict(config)
        cfg.update({
            "logs_dir": tempdir,
            "fixed_analysis_round_state_file": str(
                pathlib.Path(tempdir) / "fixed_analysis_round_state.json"),
            "startup_log_enabled": False,
            "chart_enabled": False,
            "offline_fixture_enabled": False,
            "signal_review_push_enabled": False,
        })
        snapshot_ms = utc8_ms(2026, 7, 24, 23, 3, 0)
        ctx = mod.build_fixed_analysis_round_context(snapshot_ms, cfg)
        old_now = mod.now_ms
        mod.now_ms = lambda: snapshot_ms
        try:
            runtime = mod.DemoRuntime(cfg)
            tracker = mod.SignalEventTracker(cfg)
            nr = {
                "state": "NR_IDLE",
                "is_active": False,
                "event_context": {},
                "anchor_context": {"anchor_score": 52.0},
            }
            assert_true(
                tracker.maybe_record_fixed_round(
                    nr, base_factor_snapshot(mod), runtime_facts(snapshot_ms),
                    ctx),
                "fixed card should be produced for runtime state test")
            runtime.signal_events = tracker
            runtime.last_signal_recorded = True
            runtime._emit_signal_review_card()

            review_path = pathlib.Path(tempdir) / "signal_review.jsonl"
            assert_true(review_path.exists(),
                        "fixed audit JSONL must be written")
            lines = [
                json.loads(line) for line in review_path.read_text(
                    encoding="utf-8").splitlines() if line.strip()
            ]
            assert_equal(len(lines), 1, "one fixed audit record is written")
            assert_equal(lines[0]["identity"]["event_type"],
                         "FIXED_ANALYSIS_ROUND",
                         "runtime writes fixed event record")

            state = json.loads(pathlib.Path(
                cfg["fixed_analysis_round_state_file"]).read_text(
                    encoding="utf-8"))
            slot_state = (state.get("slots") or {}).get(ctx["slot"])
            assert_true(slot_state is not None,
                        "state file records fixed slot")
            assert_equal(slot_state["slot"], ctx["slot"],
                         "state stores slot")
            assert_equal(slot_state["json_written"], True,
                         "state stores JSON completion")
            assert_equal(slot_state["push_done"], True,
                         "state stores push completion when push disabled")

            restarted = mod.DemoRuntime(cfg)
            assert_true(
                restarted._fixed_analysis_round_already_done(ctx["slot"]),
                "state file prevents same-day restart duplicate")
        finally:
            mod.now_ms = old_now


def test_restart_resumes_push_without_duplicate_json(mod, config):
    with tempfile.TemporaryDirectory() as tempdir:
        cfg = dict(config)
        cfg.update({
            "logs_dir": tempdir,
            "fixed_analysis_round_state_file": str(
                pathlib.Path(tempdir) / "fixed_analysis_round_state.json"),
            "startup_log_enabled": False,
            "chart_enabled": False,
            "offline_fixture_enabled": False,
            "signal_review_push_enabled": True,
        })
        snapshot_ms = utc8_ms(2026, 7, 24, 23, 3, 0)
        ctx = mod.build_fixed_analysis_round_context(snapshot_ms, cfg)
        nr = {
            "state": "NR_IDLE",
            "is_active": False,
            "event_context": {},
            "anchor_context": {"anchor_score": 52.0},
        }
        old_now = mod.now_ms
        old_push = mod.fmz_push
        mod.now_ms = lambda: snapshot_ms
        try:
            runtime = mod.DemoRuntime(cfg)
            tracker = mod.SignalEventTracker(cfg)
            assert_true(tracker.maybe_record_fixed_round(
                nr, base_factor_snapshot(mod), runtime_facts(snapshot_ms), ctx),
                "first runtime should produce fixed card")
            runtime.signal_events = tracker
            runtime.last_signal_recorded = True
            mod.fmz_push = lambda _text: (_ for _ in ()).throw(
                RuntimeError("push unavailable"))
            runtime._emit_signal_review_card()

            review_path = pathlib.Path(tempdir) / "signal_review.jsonl"
            assert_equal(len(review_path.read_text(
                encoding="utf-8").splitlines()), 1,
                "JSON should be durable before failed push")
            state = json.loads(pathlib.Path(
                cfg["fixed_analysis_round_state_file"]).read_text(
                    encoding="utf-8"))
            slot_state = state["slots"][ctx["slot"]]
            assert_true(slot_state["json_written"],
                        "partial state remembers durable JSON")
            assert_true(not slot_state["push_done"],
                        "partial state keeps push pending")

            pathlib.Path(cfg["fixed_analysis_round_state_file"]).unlink()
            pushed = []
            mod.fmz_push = pushed.append
            restarted = mod.DemoRuntime(cfg)
            retry_tracker = mod.SignalEventTracker(cfg)
            assert_true(retry_tracker.maybe_record_fixed_round(
                nr, base_factor_snapshot(mod), runtime_facts(snapshot_ms), ctx),
                "restart should reconstruct pending fixed delivery")
            restarted.signal_events = retry_tracker
            restarted.last_signal_recorded = True
            restarted._emit_signal_review_card()
            assert_equal(len(review_path.read_text(
                encoding="utf-8").splitlines()), 1,
                "restart must not duplicate already durable JSON")
            assert_equal(len(pushed), 1, "restart should retry the pending push")
            assert_true(pushed[0].startswith("【固定轮次分析】"),
                        "retry push keeps fixed-round identity")
        finally:
            mod.now_ms = old_now
            mod.fmz_push = old_push


def main():
    mod = load_signal_module()
    config = dict(mod.CONFIG)
    test_fixed_round_slot_context(mod, config)
    test_pure_fixed_round_card_contract(mod, config)
    test_fixed_round_dedup_and_regular_signal_merge(mod, config)
    test_runtime_fixed_round_state_records_json_and_push_done(mod, config)
    test_restart_resumes_push_without_duplicate_json(mod, config)
    print("fmz_157_fixed_analysis_round: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("fmz_157_fixed_analysis_round: FAIL - " + str(exc))
        sys.exit(1)
