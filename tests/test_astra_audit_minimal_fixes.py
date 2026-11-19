"""Regression tests for Astra 6.2 audit minimal fixes (R01/O01, D01, D02, M01, V01)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tools import astra_level_behaviour_v1 as levels
from tools import astra_player_dossier_v1 as dossier
from tools import astra_player_input_v1 as player
from tools import astra_player_review_v1 as review
from tools import astra_player_tracking_v13 as tracking
from tools import astra_research_archive_v1 as research_archive


# ---------------------------------------------------------------------------
# R01 + O01: Per-hand isolation in sync-open and degraded batch observability
# ---------------------------------------------------------------------------

def test_r01_o01_corrupt_old_block_does_not_block_valid_open_hands(tmp_path, capsys):
    old_hand = tmp_path / "dossiers/Deribit_BTC_20261117T080000Z/hands/hand_aaaaaaaaaaaaaaaaaaaaaaaa"
    old_hand2 = tmp_path / "dossiers/Deribit_BTC_20261117T080000Z/hands/hand_cccccccccccccccccccccccc"
    good_hand1 = tmp_path / "dossiers/Deribit_BTC_20261118T080000Z/hands/hand_bbbbbbbbbbbbbbbbbbbbbbbb"
    good_hand2 = tmp_path / "dossiers/Deribit_BTC_20261119T080000Z/hands/hand_dddddddddddddddddddddddd"
    for h in (old_hand, old_hand2, good_hand1, good_hand2):
        (h / "tracking").mkdir(parents=True)
    # Two corrupt legacy requests (final_block.json & blocked.json)
    (old_hand / "tracking/request.json").write_text("{}", encoding="utf-8")
    (old_hand / "tracking/final_block.json").write_text("{corrupt", encoding="utf-8")
    (old_hand2 / "tracking/request.json").write_text("{}", encoding="utf-8")
    (old_hand2 / "tracking/blocked.json").write_text("{corrupt", encoding="utf-8")
    # Two valid open locked hands
    for idx, gh in enumerate((good_hand1, good_hand2), 1):
        (gh / "expected.json").write_text(json.dumps({"expiry_ms": 1795000000000 + idx * 1000}), encoding="utf-8")
        (gh / "tracking/link.json").write_text("{}", encoding="utf-8")

    synced = []

    def fake_sync(_d, hand, _service, **_kw):
        synced.append(hand.name)
        return {"state": "OPEN", "hand_id": hand.name}

    with patch.object(tracking, "recover_initial", return_value=[]), \
         patch.object(tracking, "sync", side_effect=fake_sync), \
         patch.object(dossier.Dossiers, "summary", return_value=tmp_path / "summary.md"), \
         patch.object(sys, "argv", ["astra_player_tracking_v13.py", "sync-open", "--root", str(tmp_path)]):
        with pytest.raises(SystemExit) as excinfo:
            tracking.main()

    assert excinfo.value.code == 2  # DEGRADED is observable to systemd/operators
    assert synced == [good_hand1.name, good_hand2.name]
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert len(out) == 4
    failed_rows = [r for r in out if r.get("state") == "LINK_UNVERIFIED"]
    assert {r["hand_id"] for r in failed_rows} == {old_hand.name, old_hand2.name}
    assert all(r.get("phase") == "request" and r.get("dossier_id") == "Deribit_BTC_20261117T080000Z" for r in failed_rows)
    err_meta = json.loads(captured.err.strip().splitlines()[-1])
    assert err_meta["batch_status"] == "DEGRADED"
    assert err_meta["failed_count"] == 2
    assert err_meta["ok_count"] == 2


def test_o01_classify_sync_open_batch_states():
    assert tracking.classify_sync_open_batch([])["batch_status"] == "ALL_OK"
    assert tracking.classify_sync_open_batch([{"state": "OPEN", "hand_id": "h1"}])["batch_status"] == "ALL_OK"
    assert tracking.classify_sync_open_batch([
        {"state": "LINK_UNVERIFIED", "hand_id": "h1", "reason": "JSONDecodeError"},
        {"state": "OPEN", "hand_id": "h2"},
    ])["batch_status"] == "DEGRADED"
    assert tracking.classify_sync_open_batch([
        {"state": "TRACKER_EXPORT_UNAVAILABLE", "hand_id": "h1", "reason": "ValueError"},
    ])["batch_status"] == "FAILED"


def test_o01_settle_due_reports_skipped_corrupt_dossier(tmp_path):
    bad = tmp_path / "dossiers/Deribit_BTC_20261117T080000Z/hands/hand_aaaaaaaaaaaaaaaaaaaaaaaa"
    bad.mkdir(parents=True)
    (bad / "expected.json").write_text("{corrupt", encoding="utf-8")
    d = dossier.Dossiers(tmp_path, clock=lambda: 1795000000000)
    errors = []
    settled = list(d.settle_due(errors=errors))
    assert settled == []
    assert len(errors) == 1
    assert errors[0]["dossier_id"] == "Deribit_BTC_20261117T080000Z"


# ---------------------------------------------------------------------------
# D01: Spot 4h/12h completion clock & comparability qualification
# ---------------------------------------------------------------------------

def _spot_row(fast, slow, *, asof_ms=100_000_000):
    return {
        "quality": "ok",
        "asof_ms": asof_ms,
        "values": {"micro_flow": {"fast_4h": fast, "slow_12h": slow}},
    }


def test_d01_spot_windows_clock_and_coverage_matrix():
    cutoff = 100_000_100
    base_fast = {"data_ready": True, "cvd_unit": "BTC", "cvd_sum": -5.0, "price_return_pct": -1.0,
                 "source": "binance", "symbol": "BTCUSDT", "coverage_frac": 1.0, "coverage_hours": 4.0, "bar_count": 48}
    base_slow = {"data_ready": True, "cvd_unit": "BTC", "cvd_sum": -20.0, "price_return_pct": -1.0,
                 "source": "binance", "symbol": "BTCUSDT", "coverage_frac": 1.0, "coverage_hours": 12.0, "bar_count": 144}

    # 1. Both clocks missing -> not comparable
    r_both_missing = player.derive_non_overlapping_spot_windows(_spot_row(base_fast, base_slow), cutoff=cutoff)
    assert r_both_missing["comparable"] is False
    assert "previous_8h" not in r_both_missing

    # 2. Single clock missing -> not comparable
    r_single = player.derive_non_overlapping_spot_windows(
        _spot_row({**base_fast, "end_ts_ms": 100_000_000}, base_slow), cutoff=cutoff
    )
    assert r_single["comparable"] is False

    # 3. Mismatched clocks -> not comparable
    r_mismatch = player.derive_non_overlapping_spot_windows(
        _spot_row({**base_fast, "end_ts_ms": 100_000_000}, {**base_slow, "end_ts_ms": 99_940_000}), cutoff=cutoff
    )
    assert r_mismatch["comparable"] is False

    # 4. Future completion clock > cutoff -> not comparable
    r_future = player.derive_non_overlapping_spot_windows(
        _spot_row({**base_fast, "end_ts_ms": cutoff + 1}, {**base_slow, "end_ts_ms": cutoff + 1}), cutoff=cutoff
    )
    assert r_future["comparable"] is False

    # 5. Low coverage or bar_complete=False -> not comparable
    r_low_cov = player.derive_non_overlapping_spot_windows(
        _spot_row({**base_fast, "end_ts_ms": 100_000_000, "coverage_frac": 0.5},
                  {**base_slow, "end_ts_ms": 100_000_000}), cutoff=cutoff
    )
    assert r_low_cov["comparable"] is False
    r_incomplete = player.derive_non_overlapping_spot_windows(
        _spot_row({**base_fast, "end_ts_ms": 100_000_000, "bar_complete": False},
                  {**base_slow, "end_ts_ms": 100_000_000}), cutoff=cutoff
    )
    assert r_incomplete["comparable"] is False

    # 6. Valid matching completion clocks + coverage -> comparable & retains previous_8h
    r_ok = player.derive_non_overlapping_spot_windows(
        _spot_row({**base_fast, "end_ts_ms": 100_000_000, "bar_complete": True},
                  {**base_slow, "end_ts_ms": 100_000_000, "bar_complete": True}), cutoff=cutoff
    )
    assert r_ok["comparable"] is True
    assert r_ok["window_end_ms"] == 100_000_000
    assert r_ok["previous_8h"]["cvd_sum"] == pytest.approx(-15.0)
    assert r_ok["previous_8h"]["cvd_rate_btc_per_h"] == pytest.approx(-1.875)


# ---------------------------------------------------------------------------
# D02: Chart unverified minute bucket vs independent Deribit L2 orderbook as-of
# ---------------------------------------------------------------------------

def test_d02_level_behaviour_at_respects_timestamp_semantics():
    pts = [[1_000_000, 63880.3], [1_060_000, 64043.976333]]
    assert levels.at(pts, 1_071_052, timestamp_semantics="minute_close") == 64043.976333
    assert levels.at(pts, 1_071_052, timestamp_semantics="unverified_minute_label") == 63880.3


def test_d01_d02_real_2026_11_17_frozen_hand_readonly_comparison():
    repo_root = Path(__file__).resolve().parents[2]
    # Handle running from worktree (.worktrees/<name>) or main root
    candidates = [
        repo_root / ".artifacts/player-ae-replay/inputs",
        Path(__file__).resolve().parents[3] / ".artifacts/player-ae-replay/inputs",
    ]
    input_dir = next((p for p in candidates if (p / "source.json").is_file()), None)
    if input_dir is None:
        pytest.skip("local 2026-11-17 frozen hand artifact not present")

    src_bytes = (input_dir / "source.json").read_bytes()
    orig_input_bytes = (input_dir / "input.md").read_bytes()
    src_sha_before = hashlib.sha256(src_bytes).hexdigest()
    orig_sha_before = hashlib.sha256(orig_input_bytes).hexdigest()

    source = json.loads(src_bytes.decode("utf-8"))
    rendered = player.build_input(
        *source["cases"],
        *source["cards"],
        index_path=source["index_path"],
        ard_history=source["history"],
        ard_band_snapshot=source.get("band_snapshot"),
    )

    # D01: real 11-17 hand lacks window completion clocks -> no fabricated previous_8h
    assert "前8小时不重叠窗口" not in rendered
    assert "未作12h减4h不重叠相减" in rendered
    assert "近4小时现货成交柱" in rendered and "近12小时现货成交柱" in rendered

    # D02: 16:00 unverified chart bucket (64,044) is NOT promoted as current index
    assert "64,044" not in rendered
    assert "合格图表末点63,880.3" in rendered
    assert "2026-11-17 15:59:00 UTC" in rendered
    assert "双as-of核验" in rendered
    # Independent Deribit L2 orderbook observations (64,101.86 / 64,093.32) are preserved
    assert "Deribit原生盘口独立观测" in rendered
    assert "64,101.86 USD" in rendered
    assert "64,093.32 USD" in rendered
    assert "在责任区内（短腿与保护腿之间）" in rendered

    # Read-only guarantee: original EXACT files are untouched
    assert hashlib.sha256((input_dir / "source.json").read_bytes()).hexdigest() == src_sha_before
    assert hashlib.sha256((input_dir / "input.md").read_bytes()).hexdigest() == orig_sha_before


# ---------------------------------------------------------------------------
# M01: Discretionary REVOKE attribution requires real frozen C#/S#/R# mapping
# ---------------------------------------------------------------------------

def _hold_raw(verdict, affected):
    payload = {
        "verdict": verdict,
        "affected_conditions": affected,
        "public_reply": f"model says {verdict}",
        "key_facts": ["fact-1"],
    }
    return f"{verdict}\n\n<HOLD_REVIEW_JSON>{json.dumps(payload)}</HOLD_REVIEW_JSON>"


def test_m01_discretionary_revoke_attribution_cases():
    valid_ids = ["M1", "C1", "H1", "S1"]

    # 1. Empty affected_conditions -> UNVERIFIED, preserves raw verdict
    r_empty = review.parse_hold_review_response(
        _hold_raw("REVOKE", []), frozen_condition={}, mechanical_state="INTACT", valid_condition_ids=valid_ids
    )
    assert r_empty["verdict"] == "REVOKE"
    assert r_empty["protocol_status"] == "DISCRETIONARY_REVOKE_UNVERIFIED"
    assert r_empty["discretionary_revoke_attribution"] == "UNVERIFIED"
    assert r_empty["condition_id_mapping_status"] == "EMPTY_CONDITION_IDS"

    # 2. Unknown ID only -> UNVERIFIED
    r_fake = review.parse_hold_review_response(
        _hold_raw("REVOKE", ["FAKE"]), frozen_condition={}, mechanical_state="INTACT", valid_condition_ids=valid_ids
    )
    assert r_fake["verdict"] == "REVOKE"
    assert r_fake["protocol_status"] == "DISCRETIONARY_REVOKE_UNVERIFIED"
    assert r_fake["discretionary_revoke_attribution"] == "UNVERIFIED"
    assert r_fake["condition_id_mapping_status"] == "UNMAPPED_CONDITION_ID"

    # 3. Non-discretionary ID only (M1 intact / H1 historical baseline) -> UNVERIFIED
    r_m1_only = review.parse_hold_review_response(
        _hold_raw("REVOKE", ["M1"]), frozen_condition={}, mechanical_state="INTACT", valid_condition_ids=valid_ids
    )
    assert r_m1_only["protocol_status"] == "DISCRETIONARY_REVOKE_UNVERIFIED"
    assert r_m1_only["condition_id_mapping_status"] == "NON_DISCRETIONARY_BASIS_ONLY"

    # 4. Valid frozen C1 / S1 -> VERIFIED discretionary revoke
    for cid in ("C1", "S1"):
        r_ok = review.parse_hold_review_response(
            _hold_raw("REVOKE", [cid]), frozen_condition={}, mechanical_state="INTACT", valid_condition_ids=valid_ids
        )
        assert r_ok["protocol_status"] == "DISCRETIONARY_REVOKE"
        assert r_ok["discretionary_revoke_attribution"] == "VERIFIED"
        assert r_ok["discretionary_mapped_condition_ids"] == [cid]

    # 5. Mechanical M1 triggered -> preserves ALIGNED mechanical revoke semantics
    r_trig = review.parse_hold_review_response(
        _hold_raw("REVOKE", ["M1"]),
        frozen_condition={},
        program_triggered=True,
        mechanical_state="TRIGGERED",
        valid_condition_ids=valid_ids,
    )
    assert r_trig["protocol_status"] == "ALIGNED"
    assert r_trig["discretionary_revoke_attribution"] == "NOT_APPLICABLE"


# ---------------------------------------------------------------------------
# V01: Research inquiry LLM callback default disabled unless explicitly gated
# ---------------------------------------------------------------------------

def test_v01_research_inquiry_gate_default_disabled_and_opt_in(tmp_path):
    archive = research_archive.Archive(tmp_path / "archive")
    args = argparse.Namespace(
        workbench="http://127.0.0.1:8895",
        inbox=str(tmp_path / "inbox"),
        receipts=str(tmp_path / "receipts"),
        workbench_dir=str(tmp_path / "wb"),
    )
    seen = []

    def fake_tick(_archive, **kwargs):
        seen.append(kwargs["run_inquiry"])
        return {"ok": True}

    env_disabled = {"LLM_API_KEY": "dummy-key"}
    with patch.dict(os.environ, env_disabled, clear=True), \
         patch.object(research_archive, "_http_json", return_value={}), \
         patch.object(research_archive, "Deribit", return_value=object()), \
         patch.object(research_archive, "tick", side_effect=fake_tick):
        research_archive._tick_main(args, archive)
    assert seen[-1] is None

    env_enabled = {"LLM_API_KEY": "dummy-key", "ASTRA_RESEARCH_INQUIRY_ENABLED": "1"}
    with patch.dict(os.environ, env_enabled, clear=True), \
         patch.object(research_archive, "_http_json", return_value={}), \
         patch.object(research_archive, "Deribit", return_value=object()), \
         patch.object(research_archive, "tick", side_effect=fake_tick):
        research_archive._tick_main(args, archive)
    assert callable(seen[-1])


def test_v01_production_manifest_present_and_enforces_policies():
    manifest_path = Path(__file__).resolve().parents[1] / "deploy/player_dossier/production_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["policy"]["forbid_full_git_tree_overwrite"] is True
    assert manifest["policy"]["research_inquiry_default_disabled"] is True
    assert manifest["policy"]["research_inquiry_gate_env"] == "ASTRA_RESEARCH_INQUIRY_ENABLED=1"
    assert manifest["tracked_non_blocking_items"]["X01"]["status"] == "SEPARATE_SAFETY_TICKET_BEFORE_NEXT_MANUAL_FMZ_USE"
