import gzip
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools import astra_data_retention_v23 as retention


NOW = int(datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)


def snapshot(ts_ms: int, *, mode: str = "live_public_read_only", sid: str | None = None):
    decision = {
        "ts_ms": ts_ms,
        "demo_version": "2.0.0",
        "symbol": "BTC",
        "runtime_facts": {
            "runtime_mode": mode,
            "current_price": 100000,
            "current_price_source": "test",
            "current_price_observed_ms": ts_ms,
        },
    }
    if sid:
        decision["id"] = sid
    return {
        "schema_name": "EvaluationSnapshot",
        "schema_version": "test",
        "decision": decision,
        "factor_snapshot": {"space_gate": {"state": "STRUCTURE_ELIGIBLE"}},
    }


def decision_snapshot(ts_ms: int, *, sid: str = "decision-native"):
    return {
        "schema_name": "DecisionSnapshot",
        "schema_version": "decision_snapshot@2.3.0",
        "id": sid,
        "source_clock": {
            "decision_ts_ms": ts_ms,
            "source_observed_ms": ts_ms - 500,
        },
        "decision": {
            "ts_ms": ts_ms,
            "symbol": "BTC",
            "side": "AUDIT_ONLY",
        },
        "source": {
            "producer": "fmz",
            "log": "decisions.jsonl",
        },
    }


def write_gz_jsonl(path: Path, rows, *, raw_tail: bytes | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n")
        if raw_tail:
            handle.write(raw_tail)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def archive(root: Path, name: str, rows, *, raw_tail: bytes | None = None) -> Path:
    path = root / name
    write_gz_jsonl(path, rows, raw_tail=raw_tail)
    return path


def test_dry_run_matches_only_exact_archives_and_protects_recent_and_latest(tmp_path):
    fmz = tmp_path / "fmz"
    retained = tmp_path / "retained"
    fmz.mkdir()
    retained.mkdir()
    old = archive(fmz, "snapshots.jsonl-20260920-000000.gz", [snapshot(NOW - 6 * 86400_000, sid="old")])
    latest_old = archive(fmz, "snapshots.jsonl-20260921-000000.gz", [snapshot(NOW - 5 * 86400_000, sid="latest-old")])
    recent = archive(fmz, "snapshots.jsonl-20260926-110000.gz", [snapshot(NOW - 3600_000, sid="recent")])
    ignored = archive(fmz, "signal_review.jsonl-20260920-000000.gz", [snapshot(NOW - 1)])
    (fmz / "signal_llm_reviews.jsonl").write_text("budget sidecar must stay\n", encoding="utf-8")

    result = retention.maintain(
        fmz_log_dir=fmz.resolve(),
        retained_dir=retained.resolve(),
        now_ms=NOW,
    )

    assert result["mode"] == "dry-run"
    assert result["recognized_archives"] == 3
    assert result["freeze_candidate_count"] == 1
    assert result["protected_raw_count"] == 2
    assert result["receipts"][0]["source_file_name"] == old.name
    protected_names = {row["source_file_name"] for row in result["protected"]}
    assert protected_names == {latest_old.name, recent.name}
    assert old.exists() and latest_old.exists() and recent.exists() and ignored.exists()
    assert (fmz / "signal_llm_reviews.jsonl").read_text(encoding="utf-8") == "budget sidecar must stay\n"
    assert not list(retained.rglob("*"))


def test_apply_freezes_snapshot_unlinks_once_and_keeps_receipt(tmp_path):
    fmz = tmp_path / "fmz"
    retained = tmp_path / "retained"
    fmz.mkdir()
    retained.mkdir()
    source = archive(fmz, "decisions.jsonl-20260920-010203.gz", [
        {"kind": "decision"},
        {"evaluation_snapshot": snapshot(NOW - 6 * 86400_000, sid="wrapped")},
    ])

    first = retention.maintain(
        fmz_log_dir=fmz.resolve(),
        retained_dir=retained.resolve(),
        now_ms=NOW,
        latest_raw_protect=0,
        apply=True,
    )
    second = retention.maintain(
        fmz_log_dir=fmz.resolve(),
        retained_dir=retained.resolve(),
        now_ms=NOW,
        latest_raw_protect=0,
        apply=True,
    )

    assert first["deleted_count"] == 1
    assert not source.exists()
    assert second["recognized_archives"] == 0
    freezes = list((retained / "snapshots").glob("*.snapshot.json"))
    receipts = list((retained / "receipts").glob("*.receipt.json"))
    assert len(freezes) == 1
    assert len(receipts) == 1
    frozen = read_json(freezes[0])
    receipt = read_json(receipts[0])
    assert frozen["normalized_evaluation_snapshot"]["decision"]["id"] == "wrapped"
    assert frozen["zero_llm"] is True
    assert receipt["unlinked"] is True
    assert receipt["llm_calls"] == 0
    assert read_json(retained / "retained_index.json")["entries"][0]["snapshot_id"] == "wrapped"


def test_decisions_archive_retains_native_decision_snapshot_without_masquerading(tmp_path):
    fmz = tmp_path / "fmz"
    retained = tmp_path / "retained"
    fmz.mkdir()
    retained.mkdir()
    native = decision_snapshot(NOW - 6 * 86400_000, sid="native-decision-1")
    source = archive(fmz, "decisions.jsonl-20260920-030405.gz", [
        {"kind": "noise"},
        native,
    ])

    result = retention.maintain(
        fmz_log_dir=fmz.resolve(),
        retained_dir=retained.resolve(),
        now_ms=NOW,
        latest_raw_protect=0,
        apply=True,
    )

    assert result["deleted_count"] == 1
    assert not source.exists()
    frozen = read_json(next((retained / "snapshots").glob(f"{source.name}*.snapshot.json")))
    receipt = read_json(next((retained / "receipts").glob(f"{source.name}*.receipt.json")))
    assert frozen["retained_record_kind"] == "DecisionSnapshot"
    assert frozen["source_schema_name"] == "DecisionSnapshot"
    assert frozen["source_schema_version"] == "decision_snapshot@2.3.0"
    assert frozen["source_clock"]["source_clock"]["decision_ts_ms"] == native["source_clock"]["decision_ts_ms"]
    assert frozen["normalized_decision_snapshot"] == native
    assert frozen["normalized_full_row"] == native
    assert "normalized_evaluation_snapshot" not in frozen
    assert receipt["retained_record_kind"] == "DecisionSnapshot"
    assert receipt["snapshot_id"] == "native-decision-1"


def test_snapshots_archive_rejects_native_decision_snapshot_family_mismatch(tmp_path):
    fmz = tmp_path / "fmz"
    retained = tmp_path / "retained"
    fmz.mkdir()
    retained.mkdir()
    source = archive(fmz, "snapshots.jsonl-20260920-030405.gz", [
        decision_snapshot(NOW - 6 * 86400_000, sid="wrong-family"),
    ])

    result = retention.maintain(
        fmz_log_dir=fmz.resolve(),
        retained_dir=retained.resolve(),
        now_ms=NOW,
        latest_raw_protect=0,
        apply=True,
    )

    assert source.exists()
    receipt = result["receipts"][0]
    assert receipt["action"] == "skip_unreadable_or_no_snapshot"
    assert receipt["scan"]["error"] == "no_complete_family_allowed_snapshot"
    assert receipt["scan"]["allowed_record_kinds"] == ["EvaluationSnapshot"]


def test_rejects_overlapping_directories_and_symlink_archive(tmp_path):
    fmz = tmp_path / "fmz"
    fmz.mkdir()
    overlap = fmz / "retained"
    overlap.mkdir()
    with pytest.raises(retention.RetentionError, match="must not overlap"):
        retention.maintain(
            fmz_log_dir=fmz.resolve(),
            retained_dir=overlap.resolve(),
            now_ms=NOW,
            apply=True,
        )

    retained = tmp_path / "retained"
    retained.mkdir()
    target = archive(fmz, "snapshots.jsonl-20260920-000000.real.gz", [snapshot(NOW - 1)])
    link = fmz / "snapshots.jsonl-20260920-000000.gz"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not available in this test environment")
    with pytest.raises(retention.RetentionError, match="symlink"):
        retention.maintain(
            fmz_log_dir=fmz.resolve(),
            retained_dir=retained.resolve(),
            now_ms=NOW,
            latest_raw_protect=0,
            apply=True,
        )


def test_damaged_gzip_is_reported_and_half_line_uses_last_complete_snapshot(tmp_path):
    fmz = tmp_path / "fmz"
    retained = tmp_path / "retained"
    fmz.mkdir()
    retained.mkdir()
    damaged = fmz / "decisions.jsonl-20260919-000000.gz"
    damaged.write_bytes(b"not a gzip")
    half = archive(
        fmz,
        "snapshots.jsonl-20260920-000000.gz",
        [snapshot(NOW - 6 * 86400_000, sid="complete")],
        raw_tail=b'{"schema_name":"EvaluationSnapshot","decision":',
    )

    result = retention.maintain(
        fmz_log_dir=fmz.resolve(),
        retained_dir=retained.resolve(),
        now_ms=NOW,
        latest_raw_protect=0,
        apply=True,
    )

    by_name = {row["source_file_name"]: row for row in result["receipts"]}
    assert by_name[damaged.name]["action"] == "skip_unreadable_or_no_snapshot"
    assert damaged.exists()
    assert by_name[half.name]["unlinked"] is True
    frozen = read_json(next((retained / "snapshots").glob(f"{half.name}*.snapshot.json")))
    assert frozen["snapshot_id"] == "complete"
    assert frozen["source_tail_sha256"]


def test_freeze_write_failure_keeps_source_gzip(tmp_path, monkeypatch):
    fmz = tmp_path / "fmz"
    retained = tmp_path / "retained"
    fmz.mkdir()
    retained.mkdir()
    source = archive(fmz, "decisions.jsonl-20260920-000000.gz", [snapshot(NOW - 6 * 86400_000, sid="keep")])
    original = retention._atomic_write_json

    def failing_write(path, value):
        if str(path).endswith(".snapshot.json"):
            raise retention.RetentionError("forced freeze failure")
        return original(path, value)

    monkeypatch.setattr(retention, "_atomic_write_json", failing_write)
    with pytest.raises(retention.RetentionError, match="forced freeze failure"):
        retention.maintain(
            fmz_log_dir=fmz.resolve(),
            retained_dir=retained.resolve(),
            now_ms=NOW,
            latest_raw_protect=0,
            apply=True,
        )
    assert source.exists()


def test_empty_current_snapshots_protects_snapshot_archives(tmp_path):
    fmz = tmp_path / "fmz"
    retained = tmp_path / "retained"
    fmz.mkdir()
    retained.mkdir()
    (fmz / "snapshots.jsonl").write_bytes(b"")
    old_snapshot = archive(fmz, "snapshots.jsonl-20260920-000000.gz", [snapshot(NOW - 6 * 86400_000, sid="empty-current")])
    old_decision = archive(fmz, "decisions.jsonl-20260920-000000.gz", [snapshot(NOW - 6 * 86400_000, sid="decision")])

    result = retention.maintain(
        fmz_log_dir=fmz.resolve(),
        retained_dir=retained.resolve(),
        now_ms=NOW,
        latest_raw_protect=0,
        apply=True,
    )

    protected = {row["source_file_name"]: row for row in result["protected"]}
    assert old_snapshot.name in protected
    assert "current_snapshots_jsonl_empty" in protected[old_snapshot.name]["protected_reasons"]
    assert old_snapshot.exists()
    assert not old_decision.exists()


def test_refresh_current_only_uses_live_nonfuture_current_file_and_preserves_empty(tmp_path):
    fmz = tmp_path / "fmz"
    retained = tmp_path / "retained"
    fmz.mkdir()
    retained.mkdir()
    latest = retained / "latest.json"
    latest.write_text(json.dumps({"existing": True}), encoding="utf-8")
    (fmz / "snapshots.jsonl").write_bytes(b"")
    empty = retention.refresh_latest_current(
        fmz_log_dir=fmz.resolve(),
        retained_dir=retained.resolve(),
        now_ms=NOW,
        apply=True,
    )
    assert empty["status"] == "source_empty"
    assert read_json(latest) == {"existing": True}

    rows = [
        snapshot(NOW - 2_000, mode="local_synthetic_preview", sid="synthetic"),
        snapshot(NOW + 1_000, sid="future"),
        snapshot(NOW - 1_000, sid="live"),
    ]
    (fmz / "snapshots.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    result = retention.refresh_latest_current(
        fmz_log_dir=fmz.resolve(),
        retained_dir=retained.resolve(),
        now_ms=NOW,
        apply=True,
    )
    assert result["bridge_written"] is True
    assert result["snapshot_id"] == "live"
    written = read_json(latest)
    assert written["source"] == "current_snapshots_jsonl"
    assert written["normalized_evaluation_snapshot"]["decision"]["id"] == "live"


def test_systemd_maintenance_contract_is_data_server_only():
    root = Path(__file__).resolve().parents[1]
    service = (root / "deploy/signal_audit/data-engine-maintenance.service").read_text(encoding="utf-8")
    timer = (root / "deploy/signal_audit/data-engine-maintenance.timer").read_text(encoding="utf-8")
    env = (root / "deploy/signal_audit/data-engine-maintenance.env.example").read_text(encoding="utf-8")
    assert "astra_data_server_v23.py" in service
    assert "--fmz-log-dir ${ASTRA_FMZ_LOG_DIR}" in service
    assert "--state-dir ${ASTRA_DATA_STATE_DIR}" in service
    assert "LLM" not in service
    assert "OnUnitActiveSec=300" in timer
    assert "ASTRA_FMZ_LOG_DIR=" in env
    assert "ASTRA_DATA_STATE_DIR=" in env
