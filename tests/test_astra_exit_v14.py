from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import astra_exit_v14 as ex


def _obs(as_of_ms: int, spot: float) -> dict[str, object]:
    return {
        "as_of_ms": as_of_ms,
        "observation_id": f"obs-{as_of_ms}",
        "last_closed_price": spot,
        "last_closed_time_ms": as_of_ms - 1,
        "price_observation_ms": as_of_ms - 1,
        "spot_open_time_ms": as_of_ms - 60000,
    }


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "row_id": "r1",
        "delivery_date": "2023-01-01",
        "year": 2023,
        "side": "put_credit",
        "entry_ms": 0,
        "expiry_ms": 7_200_000,
        "short_name": "BTC-1JAN23-47000-P",
        "long_name": "BTC-1JAN23-45000-P",
        "short_strike": 47_000.0,
        "long_strike": 45_000.0,
        "actual_width": 2_000.0,
        "entry_price": 50_000.0,
        "settlement_price": 44_000.0,
        "row_weight": 1.0,
        "payout_btc": 0.04,
        "loss_normalized": 1.0,
        "short_leg_breached": True,
        "protection_leg_breached": True,
    }
    row.update(overrides)
    return row


def test_valid_observation_requires_fully_closed_prior_minute():
    assert ex.validate_spot_observation(_obs(1_800_000, 46_900.0)) == (True, None)

    invalid = _obs(1_800_000, 46_900.0)
    invalid["price_observation_ms"] = 1_800_000
    assert ex.validate_spot_observation(invalid) == (
        False,
        "price_observation_not_before_as_of",
    )

    invalid = _obs(1_800_000, 46_900.0)
    invalid["spot_open_time_ms"] = 1_740_001
    assert ex.validate_spot_observation(invalid) == (
        False,
        "spot_open_time_not_minute_aligned",
    )

    stale = _obs(1_800_000, 46_900.0)
    stale["price_observation_ms"] = 1_739_999
    stale["last_closed_time_ms"] = 1_739_999
    stale["spot_open_time_ms"] = 1_680_000
    assert ex.validate_spot_observation(stale) == (
        False,
        "stale_closed_minute_not_latest",
    )


def test_short_touch_first_triggers_on_first_valid_crossing():
    row = _row()
    result = ex.evaluate_exit_path(
        row,
        {
            1_800_000: _obs(1_800_000, 46_900.0),
            3_600_000: _obs(3_600_000, 46_000.0),
        },
        "SHORT_TOUCH_FIRST",
    )

    assert result["path_status"] == "triggered"
    assert result["trigger_as_of_ms"] == 1_800_000
    assert result["trigger_spot"] == pytest.approx(46_900.0)
    assert result["threshold"] == pytest.approx(47_000.0)


def test_missing_or_invalid_scheduled_point_before_trigger_makes_path_unknown():
    row = _row()
    result = ex.evaluate_exit_path(
        row,
        {
            1_800_000: _obs(1_800_000, 48_000.0),
            5_400_000: _obs(5_400_000, 46_000.0),
        },
        "SHORT_TOUCH_FIRST",
    )

    assert result["path_status"] == "path_unknown"
    assert result["unknown_as_of_ms"] == 3_600_000
    assert result["path_unknown_reason"] == "missing_observation"


def test_call_mid_width_intrusion_crosses_above_threshold():
    row = _row(
        side="call_credit",
        short_name="BTC-1JAN23-53000-C",
        long_name="BTC-1JAN23-55000-C",
        short_strike=53_000.0,
        long_strike=55_000.0,
    )

    result = ex.evaluate_exit_path(
        row,
        {
            1_800_000: _obs(1_800_000, 53_500.0),
            3_600_000: _obs(3_600_000, 54_100.0),
        },
        "MID_WIDTH_INTRUSION_FIRST",
    )

    assert result["path_status"] == "triggered"
    assert result["trigger_as_of_ms"] == 3_600_000
    assert result["threshold"] == pytest.approx(54_000.0)


def test_reference_cost_and_trigger_are_outcome_blind_to_terminal_payout():
    observations = {1_800_000: _obs(1_800_000, 46_000.0)}
    low_terminal = ex.build_exit_ledger_row(
        _row(payout_btc=0.0, loss_normalized=0.0, protection_leg_breached=False),
        observations,
        "SHORT_TOUCH_FIRST",
    )
    high_terminal = ex.build_exit_ledger_row(
        _row(payout_btc=0.40, loss_normalized=10.0, protection_leg_breached=True),
        observations,
        "SHORT_TOUCH_FIRST",
    )

    assert low_terminal["trigger_as_of_ms"] == high_terminal["trigger_as_of_ms"]
    assert low_terminal["trigger_spot"] == high_terminal["trigger_spot"]
    assert low_terminal["reference_cost_btc_k0"] == pytest.approx(
        high_terminal["reference_cost_btc_k0"]
    )
    assert low_terminal["reference_cost_btc_k0p025"] == pytest.approx(
        high_terminal["reference_cost_btc_k0p025"]
    )
    assert low_terminal["affordable_all_in_exit_debit_btc"] != high_terminal[
        "affordable_all_in_exit_debit_btc"
    ]
    assert low_terminal["reference_gain_exit_minus_hold_btc_k0"] != high_terminal[
        "reference_gain_exit_minus_hold_btc_k0"
    ]


def test_intrinsic_reference_keeps_uncapped_terminal_tail_for_gain():
    row = _row(payout_btc=0.12, loss_normalized=3.0)
    trigger = ex.build_exit_ledger_row(row, {1_800_000: _obs(1_800_000, 46_000.0)}, "SHORT_TOUCH_FIRST")

    intrinsic = (47_000.0 - 46_000.0) / 46_000.0
    assert trigger["immediate_intrinsic_btc"] == pytest.approx(intrinsic)
    assert trigger["reference_cost_btc_k0"] == pytest.approx(intrinsic)
    assert trigger["terminal_loss_normalized"] == pytest.approx(3.0)
    assert trigger["reference_gain_exit_minus_hold_btc_k0"] == pytest.approx(0.12 - intrinsic)


def test_terminal_outcome_class_uses_protection_or_settlement_not_normalized_cutoff():
    partial_tail = _row(
        payout_btc=0.08,
        loss_normalized=2.0,
        protection_leg_breached=False,
        settlement_price=46_000.0,
    )
    full_below_one = _row(
        side="call_credit",
        short_name="BTC-1JAN23-53000-C",
        long_name="BTC-1JAN23-55000-C",
        short_strike=53_000.0,
        long_strike=55_000.0,
        payout_btc=0.02,
        loss_normalized=0.5,
        protection_leg_breached=False,
        settlement_price=55_100.0,
    )

    assert ex.terminal_outcome_class(partial_tail) == "terminal_partial_payout"
    assert ex.terminal_outcome_class(full_below_one) == "terminal_full_payout"


def test_quote_gate_accepts_only_same_instrument_fast_two_leg_quote():
    row = _row(expiry_ms=7_200_000, payout_btc=0.04, trigger_as_of_ms=1_800_000)
    valid = ex.validate_executable_exit_quote(
        row,
        {
            "short_name": "BTC-1JAN23-47000-P",
            "long_name": "BTC-1JAN23-45000-P",
            "size": 1,
            "short_ask_depth": 1,
            "long_bid_depth": 1,
            "short_ask_btc": 0.03,
            "long_bid_btc": 0.01,
            "short_quote_ms": 1_801_000,
            "long_quote_ms": 1_802_500,
            "trigger_ms": 0,
            "received_ms": 1_803_000,
            "exit_fees_btc": 0.001,
            "hold_delivery_fees_btc": 0.0002,
        },
    )

    assert valid["valid"] is True
    assert valid["exit_debit_btc"] == pytest.approx(0.0208)
    assert valid["actual_delta_btc"] == pytest.approx(0.0192)

    invalid = ex.validate_executable_exit_quote(
        row,
        {
            "short_name": "WRONG",
            "long_name": "BTC-1JAN23-45000-P",
            "size": 1,
            "short_ask_btc": 0.03,
            "long_bid_btc": 0.01,
            "short_quote_ms": 1_801_000,
            "long_quote_ms": 1_804_000,
            "trigger_ms": 1_800_000,
            "received_ms": 1_870_001,
        },
    )

    assert invalid["valid"] is False
    assert "short_instrument_mismatch" in invalid["reasons"]
    assert "leg_quote_skew_gt_2s" in invalid["reasons"]
    assert "receipt_delay_gt_60s" in invalid["reasons"]
    assert "short_ask_depth_lt_one" in invalid["reasons"]
    assert "long_bid_depth_lt_one" in invalid["reasons"]
    assert "invalid_exit_fees_btc" in invalid["reasons"]
    assert "invalid_hold_delivery_fees_btc" in invalid["reasons"]


def test_quote_gate_rejects_missing_row_trigger_and_insufficient_depth_or_negative_fees():
    invalid = ex.validate_executable_exit_quote(
        _row(expiry_ms=7_200_000, payout_btc=0.04),
        {
            "short_name": "BTC-1JAN23-47000-P",
            "long_name": "BTC-1JAN23-45000-P",
            "size": 1,
            "short_ask_depth": 0.5,
            "long_bid_depth": 0.0,
            "short_ask_btc": 0.03,
            "long_bid_btc": 0.01,
            "short_quote_ms": 1_801_000,
            "long_quote_ms": 1_802_000,
            "received_ms": 1_803_000,
            "exit_fees_btc": -0.001,
            "hold_delivery_fees_btc": 0.0,
        },
    )

    assert invalid["valid"] is False
    assert "missing_quote_timestamps" in invalid["reasons"]
    assert "short_ask_depth_lt_one" in invalid["reasons"]
    assert "long_bid_depth_lt_one" in invalid["reasons"]
    assert "invalid_exit_fees_btc" in invalid["reasons"]


def test_summary_separates_path_unknown_from_not_triggered_and_keeps_actual_null():
    triggered = ex.build_exit_ledger_row(_row(row_id="t"), {1_800_000: _obs(1_800_000, 46_000.0)}, "SHORT_TOUCH_FIRST")
    not_triggered = ex.build_exit_ledger_row(
        _row(row_id="n", row_weight=2.0, expiry_ms=3_600_000),
        {1_800_000: _obs(1_800_000, 48_000.0)},
        "SHORT_TOUCH_FIRST",
    )
    unknown = ex.build_exit_ledger_row(
        _row(row_id="u", row_weight=3.0, expiry_ms=5_400_000),
        {1_800_000: _obs(1_800_000, 48_000.0)},
        "SHORT_TOUCH_FIRST",
    )

    summary = ex.summarize_exit_ledger(pd.DataFrame([triggered, not_triggered, unknown]))
    group = summary["groups"][0]

    assert summary["actual_effect_support"] is False
    assert summary["actual_delta_btc"] is None
    assert group["triggered_weight"] == pytest.approx(1.0)
    assert group["not_triggered_weight"] == pytest.approx(2.0)
    assert group["path_unknown_weight"] == pytest.approx(3.0)
    assert group["known_path_weight"] == pytest.approx(3.0)
    assert group["quote_unavailable_weight"] == pytest.approx(1.0)
    assert "known_path_per_original_reference_gain_contribution_k0" in group
    assert summary["pooled_rule_side_groups"][0]["year"] == "pooled"
    assert summary["actual_effect_gates"]["status"] == "unassessable_noquotes"


def test_protocol_manifest_and_amendment_hashes_are_verified(tmp_path: Path):
    source = tmp_path / "source.txt"
    source.write_text("frozen", encoding="utf-8")
    manifest = {
        "files": {
            str(source): {
                "sha256": ex.digest_file(source),
                "bytes": source.stat().st_size,
            }
        }
    }
    manifest_path = tmp_path / "source_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    protocol_path = tmp_path / "protocol_v14.json"
    protocol_path.write_text(
        json.dumps({"source_manifest_sha256": ex.digest_file(manifest_path)}),
        encoding="utf-8",
    )
    protocol_sha = ex.digest_file(protocol_path)
    (tmp_path / "protocol_seal.json").write_text(
        json.dumps({"protocol_sha256": protocol_sha}),
        encoding="utf-8",
    )
    (tmp_path / "preresult_amendment_01.json").write_text(
        json.dumps({"original_protocol_sha256": protocol_sha}),
        encoding="utf-8",
    )

    result = ex.verify_protocol_sources(protocol_path)

    assert result["protocol_sha256"] == protocol_sha
    assert result["source_manifest_sha256"] == ex.digest_file(manifest_path)
    assert result["preresult_amendment_01_sha256"] == ex.digest_file(
        tmp_path / "preresult_amendment_01.json"
    )
    assert result["checked_file_count"] == 1
    assert ex.require_manifest_coverage(result, [source])[str(source)]["sha256"] == ex.digest_file(source)

    unsealed = tmp_path / "source-copy.txt"
    unsealed.write_text("frozen", encoding="utf-8")
    with pytest.raises(ValueError, match="input_not_in_source_manifest"):
        ex.require_manifest_coverage(result, [unsealed])
