import importlib.util
import json
import pathlib
import sys
import tempfile

try:
    import pytest
except Exception:
    pytest = None


ROOT = pathlib.Path(__file__).resolve().parents[1]
SIGNAL_FILE = ROOT / "demo" / "最新交付物" / "neutral_regulation_demo_fmz.py"


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def load_signal_module():
    spec = importlib.util.spec_from_file_location(
        "neutral_regulation_demo_fmz_data_export", SIGNAL_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.fmz_log = lambda *parts: None
    module.fmz_status = lambda text: None
    module.fmz_push = lambda text: None
    return module


if pytest is not None:
    @pytest.fixture(scope="module")
    def module():
        return load_signal_module()


def base_config(module, tmpdir, **overrides):
    config = dict(module.CONFIG)
    config.update({
        "startup_log_enabled": False,
        "tick_summary_log_enabled": False,
        "log_status_enabled": False,
        "chart_enabled": False,
        "signal_review_push_test": False,
        "signal_review_push_enabled": False,
        "fixed_analysis_round_enabled": False,
        "live_fetch_enabled": False,
        "offline_fixture_enabled": True,
        "logs_dir": str(tmpdir),
        "space_gate_state_file": str(tmpdir / "space_gate_state.json"),
        "data_export_enabled": True,
        "data_export_snapshot_file": str(tmpdir / "snapshots.jsonl"),
        "data_export_interval_sec": 300,
        "data_export_min_interval_sec": 180,
        "data_export_max_interval_sec": 600,
        "data_export_retry_interval_sec": 180,
        "data_export_max_snapshots": 12,
        "decision_snapshot_diagnostic_log_enabled": False,
    })
    config.update(overrides)
    return config


def read_jsonl(path):
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def sample_space_gate(module, state=None, observed_ms=1_798_200_000_000):
    return {
        "state": state or module.SPACE_GATE_ELIGIBLE,
        "observed_at_ms": observed_ms,
        "since_ts_ms": observed_ms,
        "previous_state": None,
        "last_transition_ts_ms": None,
        "reason_codes": [],
        "anchor": {
            "ok": True,
            "score": 72.0,
            "source_ts_ms": observed_ms - 60_000,
            "freshness": "FRESH",
        },
        "net_gamma": {
            "ok": True,
            "sign": "POSITIVE",
            "value": 4_200_000.0,
            "source": "gexmonitorapi.gex_board.total_net_gex",
            "source_ts_ms": observed_ms - 60_000,
            "transition": False,
            "conflict": False,
        },
    }


def sample_factor_snapshot(module, state=None):
    return {"space_gate": sample_space_gate(module, state=state)}


def sample_payload(module, label, factor_snapshot=None):
    factor_snapshot = factor_snapshot or sample_factor_snapshot(module)
    payload_ms = module.now_ms()
    return {
        "label": label,
        "source_available_at_ms": payload_ms,
        "decision": {
            "ts_ms": payload_ms,
            "runtime_facts": {"current_price_observed_ms": 111},
            "trade_allowed": False,
            "factor_snapshot": factor_snapshot,
        },
        "modules": [],
        "factor_snapshot": factor_snapshot,
        "contract_audit": {"ok": True},
    }


def force_due(exporter):
    exporter.last_success_ms = (
        exporter.last_success_ms
        - (exporter.interval_sec * 1000 + 1))


def test_manifest_config_and_permissions(module):
    manifest = module.build_data_source_manifest(module.CONFIG)
    sources = {item["source"]: item for item in manifest["sources"]}
    assert_true("gexmonitorapi_info" in sources,
                "gexmonitorapi /v1/info should be in native manifest")
    assert_true(sources["gexmonitorapi_info"]["auth"] == "none",
                "gexmonitorapi info is token-free (server PUBLIC_READ)")
    validation = module.validate_data_source_manifest(module.CONFIG)
    assert_true(validation["ok"], "data source manifest should validate")
    assert_true(module.CONFIG["demo_version"] == "2.0.4",
                "FMZ artifact identity should be 2.0.4")
    assert_true(module.CONFIG["read_only_demo"] is True,
                "read-only demo safety default should stay true")
    assert_true("llm_review_enabled" not in module.CONFIG,
                "dead LLM compatibility marker should be removed")
    assert_true(module.CONFIG["signal_review_push_enabled"] is False,
                "FMZ push should stay disabled by default")
    with tempfile.TemporaryDirectory() as raw:
        tmpdir = pathlib.Path(raw)
        config = base_config(
            module, tmpdir,
            data_export_interval_sec=1,
            data_export_min_interval_sec=1,
            data_export_max_interval_sec=9999,
            data_export_retry_interval_sec=2)
        exporter = module.WorkbenchSnapshotExporter(config)
        assert_true(exporter.interval_sec == 180,
                    "export interval should clamp to 180 seconds minimum")
        assert_true(exporter.max_interval_sec == 600,
                    "export max interval should clamp to 600 seconds")
        assert_true(exporter.retry_interval_sec == 180,
                    "retry interval should also avoid every-tick retries")


def test_runtime_first_tick_cadence_transition_and_restart(module):
    with tempfile.TemporaryDirectory() as raw:
        tmpdir = pathlib.Path(raw)
        path = tmpdir / "snapshots.jsonl"
        config = base_config(module, tmpdir)
        runtime = module.DemoRuntime(config)
        decision, _modules = runtime.tick(live_fetch=False)
        rows = read_jsonl(path)
        assert_true(len(rows) == 1, "startup tick should force one snapshot")
        first = rows[0]
        assert_true(first["decision"]["ts_ms"] <= first["source_available_at_ms"]
                    <= first["ingested_at_ms"] <= first["snapshot_exported_ms"],
                    "producer-local handoff/export clocks must be ordered")
        provenance = first["clock_provenance"]
        assert_true(provenance["not_remote_receiver_ingestion"] is True
                    and provenance["not_filesystem_visibility_proof"] is True
                    and provenance["not_market_source_ingestion"] is True,
                    "producer clocks must not impersonate remote or market clocks")
        current = first["current_snapshot_context"]
        assert_true(current["observed_at_ms"] == first["decision"]["ts_ms"],
                    "current context must bind this exact decision timestamp")
        assert_true(current["session_context"] == dict(
                    module.classify_signal_session_context(first["decision"]["ts_ms"], config),
                    observed_at_ms=first["decision"]["ts_ms"]),
                    "current session must use the existing deterministic classifier")
        assert_true(current["temporal_durability"] == current["session_context"]["premise_durability"],
                    "temporal context must use the current session")
        assert_true(first["schema_name"] == module.SCHEMA_EVALUATION_SNAPSHOT,
                    "snapshot ring should use native EvaluationSnapshot schema")
        assert_true(first["snapshot_export_trigger"] == "startup",
                    "first snapshot trigger should be startup")
        bridge = first["data_export_bridge"]
        assert_true(bridge["status"] == "ok",
                    "exported bridge health should be ok after write")
        assert_true((first["decision"]["runtime_facts"]
                     ["data_export_bridge"]["status"]) == "ok",
                    "decision runtime facts should carry export health")
        observed = first["decision"]["runtime_facts"][
            "current_price_observed_ms"]
        assert_true(observed == runtime.current_price_observed_ms,
                    "export must not rewrite source observation time")
        gate = first["factor_snapshot"]["space_gate"]
        assert_true(gate["net_gamma"]["source"]
                    == "gexmonitorapi.gex_board.total_net_gex",
                    "export should retain producer-native SpaceGate source")
        assert_true(decision["runtime_facts"]["read_only_demo"] is True,
                    "runtime decision should remain read-only")
        assert_true(not (tmpdir / "decisions.jsonl").exists(),
                    "redundant per-tick DecisionSnapshot log is off by default")

        runtime.tick(live_fetch=False)
        assert_true(len(read_jsonl(path)) == 1,
                    "second tick inside cadence should not append snapshot")

        runtime.snapshot_exporter.last_exported_spacegate_signature = "old"
        runtime.tick(live_fetch=False)
        rows = read_jsonl(path)
        assert_true(len(rows) == 2,
                    "spacegate signature change should force a snapshot")
        assert_true(rows[-1]["snapshot_export_trigger"]
                    == "spacegate_transition",
                    "transition trigger should be explicit")

        restarted = module.DemoRuntime(config)
        restarted.tick(live_fetch=False)
        rows = read_jsonl(path)
        assert_true(len(rows) == 3,
                    "restart should preserve existing ring and write startup")
        assert_true(rows[-1]["snapshot_export_trigger"] == "startup",
                    "restart first tick should be identifiable as startup")


def test_spacegate_metric_and_source_clock_do_not_bypass_cadence(module):
    with tempfile.TemporaryDirectory() as raw:
        tmpdir = pathlib.Path(raw)
        config = base_config(module, tmpdir)
        exporter = module.WorkbenchSnapshotExporter(config)
        factor_snapshot = sample_factor_snapshot(module)
        assert_true(exporter.maybe_write(
            sample_payload(module, "first", factor_snapshot), factor_snapshot),
            "initial snapshot write should succeed")

        same_gate = sample_factor_snapshot(module)
        same_gate["space_gate"]["anchor"]["score"] = 88.0
        same_gate["space_gate"]["net_gamma"]["source_ts_ms"] += 120_000
        assert_true(not exporter.maybe_write(
            sample_payload(module, "metric-clock-change", same_gate),
            same_gate),
            "score and source clock changes should stay on cadence")
        rows = read_jsonl(tmpdir / "snapshots.jsonl")
        assert_true(len(rows) == 1,
                    "same gate semantics must not force an extra snapshot")

        blocked_gate = sample_factor_snapshot(module, state=module.SPACE_GATE_BLOCKED)
        blocked_gate["space_gate"]["reason_codes"] = ["AnchorBelow60"]
        assert_true(exporter.maybe_write(
            sample_payload(module, "real-gate-change", blocked_gate),
            blocked_gate),
            "real gate-state change should still force snapshot")
        rows = read_jsonl(tmpdir / "snapshots.jsonl")
        assert_true(rows[-1]["snapshot_export_trigger"]
                    == "spacegate_transition",
                    "gate semantic transition trigger should be explicit")


def test_short_write_fsync_and_atomic_failure(module):
    with tempfile.TemporaryDirectory() as raw:
        tmpdir = pathlib.Path(raw)
        path = tmpdir / "snapshots.jsonl"
        config = base_config(module, tmpdir)
        exporter = module.WorkbenchSnapshotExporter(config)
        factor_snapshot = sample_factor_snapshot(module)

        assert_true(exporter.maybe_write(
            sample_payload(module, "first", factor_snapshot), factor_snapshot),
            "initial snapshot write should succeed")
        original_text = path.read_text(encoding="utf-8")

        original_write = module.os.write
        original_fsync = module.os.fsync
        fsync_calls = {"count": 0}

        def short_write(fd, data):
            if len(data) > 7:
                return original_write(fd, data[:7])
            return original_write(fd, data)

        def counting_fsync(fd):
            fsync_calls["count"] += 1
            return original_fsync(fd)

        module.os.write = short_write
        module.os.fsync = counting_fsync
        try:
            force_due(exporter)
            assert_true(exporter.maybe_write(
                sample_payload(module, "short-write", factor_snapshot),
                factor_snapshot),
                "short os.write calls should still complete")
        finally:
            module.os.write = original_write
            module.os.fsync = original_fsync
        assert_true(fsync_calls["count"] >= 1,
                    "snapshot export should fsync successful writes")
        rows = read_jsonl(path)
        assert_true(rows[-1]["label"] == "short-write",
                    "short-write payload should be durable")

        original_replace = module.os.replace

        def failing_replace(src, dst):
            raise OSError("simulated replace failure")

        module.os.replace = failing_replace
        try:
            force_due(exporter)
            assert_true(not exporter.maybe_write(
                sample_payload(module, "replace-fail", factor_snapshot),
                factor_snapshot),
                "replace failure should make export fail closed")
        finally:
            module.os.replace = original_replace
        assert_true(path.read_text(encoding="utf-8") != original_text,
                    "control file should already contain short-write update")
        assert_true(read_jsonl(path)[-1]["label"] == "short-write",
                    "atomic failure must keep previous complete file")
        assert_true(exporter.health_snapshot()["status"] == "error",
                    "replace failure should be visible in bridge health")

        assert_true(not exporter.maybe_write(
            sample_payload(module, "retry-too-soon", factor_snapshot),
            factor_snapshot),
            "failure should not retry every tick")
        assert_true(exporter.health_snapshot()["last_skip_reason"]
                    == "retry_wait",
                    "bounded retry wait should be explicit")


def test_non_finite_snapshot_payload_fails_closed(module):
    with tempfile.TemporaryDirectory() as raw:
        tmpdir = pathlib.Path(raw)
        path = tmpdir / "snapshots.jsonl"
        config = base_config(module, tmpdir)
        exporter = module.WorkbenchSnapshotExporter(config)
        factor_snapshot = sample_factor_snapshot(module)
        assert_true(exporter.maybe_write(
            sample_payload(module, "first", factor_snapshot), factor_snapshot),
            "initial snapshot write should succeed")
        original_text = path.read_text(encoding="utf-8")

        force_due(exporter)
        bad_payload = sample_payload(module, "nan", factor_snapshot)
        bad_payload["factor_snapshot"]["space_gate"]["anchor"]["score"] = float("nan")
        assert_true(not exporter.maybe_write(bad_payload, factor_snapshot),
                    "non-finite values should fail closed")
        assert_true(path.read_text(encoding="utf-8") == original_text,
                    "non-finite failure must preserve previous ring file")
        health = exporter.health_snapshot()
        assert_true(health["status"] == "error",
                    "non-finite failure should surface in bridge health")
        assert_true(health["last_error_type"] == "ValueError",
                    "non-finite rejection should be explicit")


def test_ring_limit_and_disabled_mode(module):
    with tempfile.TemporaryDirectory() as raw:
        tmpdir = pathlib.Path(raw)
        config = base_config(module, tmpdir, data_export_max_snapshots=2)
        exporter = module.WorkbenchSnapshotExporter(config)
        factor_snapshot = sample_factor_snapshot(module)
        for label in ("a", "b", "c"):
            if exporter.last_success_ms is not None:
                force_due(exporter)
            assert_true(exporter.maybe_write(
                sample_payload(module, label, factor_snapshot),
                factor_snapshot),
                "ring write should succeed for " + label)
        labels = [row["label"] for row in read_jsonl(tmpdir / "snapshots.jsonl")]
        assert_true(labels == ["b", "c"],
                    "snapshot ring should retain only the newest records")

        disabled = module.WorkbenchSnapshotExporter(
            base_config(module, tmpdir, data_export_enabled=False,
                        data_export_snapshot_file=str(tmpdir / "off.jsonl")))
        assert_true(not disabled.maybe_write(
            sample_payload(module, "off", factor_snapshot), factor_snapshot),
            "disabled exporter should not write")
        assert_true(disabled.health_snapshot()["status"] == "disabled",
                    "disabled health should be explicit")


def test_diagnostic_decision_log_requires_explicit_switch(module):
    with tempfile.TemporaryDirectory() as raw:
        tmpdir = pathlib.Path(raw)
        config = base_config(
            module, tmpdir,
            decision_snapshot_diagnostic_log_enabled=True)
        runtime = module.DemoRuntime(config)
        runtime.tick(live_fetch=False)
        rows = read_jsonl(tmpdir / "decisions.jsonl")
        assert_true(len(rows) == 1,
                    "explicit diagnostic switch should write DecisionSnapshot")
        assert_true(rows[0]["schema_name"] == module.SCHEMA_DECISION_SNAPSHOT,
                    "diagnostic file keeps native DecisionSnapshot schema")


def test_missing_future_or_boolean_handoff_clock_preserves_old_ring(module):
    with tempfile.TemporaryDirectory() as raw:
        tmpdir = pathlib.Path(raw)
        exporter = module.WorkbenchSnapshotExporter(base_config(module, tmpdir))
        assert_true(exporter.maybe_write(sample_payload(module, "valid")),
                    "valid clock should export")
        before = (tmpdir / "snapshots.jsonl").read_bytes()
        for invalid in (None, True, module.now_ms() + 60_000):
            force_due(exporter)
            # A failed attempt has retry backoff; exercise each independent
            # invalid input without sleeping or relaxing production cadence.
            exporter.last_attempt_ms = None
            payload = sample_payload(module, "invalid")
            payload["source_available_at_ms"] = invalid
            assert_true(not exporter.maybe_write(payload),
                        "invalid clock must fail closed")
            assert_true((tmpdir / "snapshots.jsonl").read_bytes() == before,
                        "invalid clock must preserve existing evidence bytes")


def main():
    module = load_signal_module()
    test_manifest_config_and_permissions(module)
    test_runtime_first_tick_cadence_transition_and_restart(module)
    test_spacegate_metric_and_source_clock_do_not_bypass_cadence(module)
    test_short_write_fsync_and_atomic_failure(module)
    test_non_finite_snapshot_payload_fails_closed(module)
    test_ring_limit_and_disabled_mode(module)
    test_diagnostic_decision_log_requires_explicit_switch(module)
    test_missing_future_or_boolean_handoff_clock_preserves_old_ring(module)
    print("fmz_23_data_export: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("fmz_23_data_export: FAIL - " + str(exc))
        sys.exit(1)
