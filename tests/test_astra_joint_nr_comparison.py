from __future__ import annotations

import csv
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from astra_joint_nr_comparison import build_nr_comparison


def ms(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def write_spot_zip(root: Path, entry: int, price: float) -> None:
    dt = datetime.fromtimestamp(entry / 1000, timezone.utc)
    folder = root / "raw" / "binance" / "spot" / "monthly"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"BTCUSDT-1m-{dt:%Y-%m}.zip"
    row = [
        str(entry),
        str(price),
        str(price + 100),
        str(price - 100),
        str(price + 50),
        "10",
        str(entry + 59_999),
        "1000",
        "10",
        "5",
        "500",
        "0",
    ]
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"BTCUSDT-1m-{dt:%Y-%m}.csv", ",".join(row) + "\n")


def write_portable_models(root: Path) -> None:
    portable = root / "portable"
    portable.mkdir(parents=True, exist_ok=True)
    for filename, group in [("model.json", "statistical"), ("joint.json", "joint"), ("mechanism.json", "mechanism")]:
        artifact = {
            "schema": "astra_two_part_payout_model@1.0.0",
            "status": "available",
            "model_kind": "two_part_gam",
            "model_version": f"test-{group}",
            "feature_group": group,
            "training_cutoff": "2021-12-31",
            "scope": {
                "training_feature_support": {
                    "side_sign": {"min": -1.0, "max": 1.0},
                }
            },
            "preprocess": {
                "version": "test",
                "numeric_features": [
                    {
                        "name": "side_sign",
                        "median": 0.0,
                        "mean": 0.0,
                        "scale": 1.0,
                        "spline": {"type": "constant"},
                    }
                ],
            },
            "design_columns": ["side_sign__constant", "side_sign__missing"],
            "design_size": 2,
            "logistic_model": {"coef": [0.0, 0.0], "intercept": 0.0},
            "gamma_model": {"coef": [0.0, 0.0], "intercept": 0.0},
            "artifact_hash": f"test-{group}-hash",
        }
        (portable / filename).write_text(json.dumps(artifact), encoding="utf-8")


def native_obs(card_id: str, confirmed: int, side_hint: str, dte_expected: bool = True) -> dict:
    entry = confirmed + 60_000
    expiry = ms(datetime(2026, 9, 11, 8, 0, tzinfo=timezone.utc))
    return {
        "schema": "astra_nr_observation_interface@1.0.0",
        "identity": {
            "card_id": card_id,
            "confirmed_time_ms": confirmed,
            "confirmed_time_utc": datetime.fromtimestamp(confirmed / 1000, timezone.utc).isoformat(),
            "confirmed_date_bjt": "2026-09-11",
            "strategy_version": "1.6.2",
            "record_kind": "native_nr_event",
            "event_type": "NR_REPAIR_CONFIRMED",
            "episode_id": f"ep-{card_id}",
            "is_synthetic": False,
            "symbol": "BTC",
        },
        "original_signal_markers": {
            "decision_lean": "BULLISH_WEAK" if side_hint == "put_credit_spread" else "NEUTRAL",
            "side_hint": side_hint,
            "support_label": "TRADE_SUPPORT_WEAK",
            "trade_allowed": True,
        },
        "availability_markers": {
            "anchor": {"available": True},
            "near_term_market_context": False,
            "options": {
                "structure_fields_present": True,
                "field_level_options_factor": True,
                "strict_contract_usable": None,
                "gamma_regime": "TRANSITION",
                "net_gamma_notional_usd": 123456.0,
                "put_wall": 78000.0,
                "call_wall": 82000.0,
                "pin_strike": 80000.0,
            },
        },
        "reference_timing": {
            "entry_open_ms": entry,
            "expiry_ms": expiry,
            "dte_hours": (expiry - entry) / 3_600_000,
            "ordinary_round_8_24h": dte_expected,
        },
        "research_role": {"eligible_native_nr_interface": True},
        "source": {"source_record_hash": f"hash-{card_id}", "fact_line": 1},
        "um_rebuilt_features": {
            "status": "available",
            "entry_open_ms": entry,
            "ret_15": 0.001,
            "ret_30": -0.002,
            "net_flow_15": 0.1,
            "net_flow_30": -0.1,
            "pressure_response_15": 0.05,
            "pressure_response_30": -0.02,
        },
    }


def prepare_root(tmp_path: Path) -> Path:
    root = tmp_path
    nr_dir = root / "archives-analysis"
    nr_dir.mkdir(parents=True)
    raw = root / "raw" / "deribit"
    raw.mkdir(parents=True)

    confirmed = ms(datetime(2026, 9, 10, 21, 0, tzinfo=timezone.utc))
    entry = confirmed + 60_000
    write_spot_zip(root, entry, 80_000.0)

    expiry = ms(datetime(2026, 9, 11, 8, 0, tzinfo=timezone.utc))
    contracts = []
    for strike, opt in [
        (76_500, "put"),
        (77_000, "put"),
        (77_500, "put"),
        (79_000, "put"),
        (81_000, "call"),
        (82_500, "call"),
        (83_000, "call"),
        (83_500, "call"),
    ]:
        contracts.append(
            {
                "instrument_name": f"BTC-11SEP26-{strike}-{opt[0].upper()}",
                "kind": "option",
                "settlement_currency": "BTC",
                "option_type": opt,
                "strike": float(strike),
                "expiration_timestamp": expiry,
                "creation_timestamp": entry - 3_600_000,
            }
        )
    (raw / "instruments.json").write_text(json.dumps(contracts), encoding="utf-8")
    (raw / "delivery_prices.json").write_text(
        json.dumps([{"date": "2026-09-11", "delivery_price": 83_200.0}]), encoding="utf-8"
    )

    good = native_obs("good", confirmed, "put_credit_spread")
    short_dte_confirmed = ms(datetime(2026, 9, 11, 1, 0, tzinfo=timezone.utc))
    short = native_obs("short", short_dte_confirmed, "none", dte_expected=False)
    synthetic = native_obs("synthetic", confirmed, "none")
    synthetic["identity"]["is_synthetic"] = True
    synthetic["identity"]["record_kind"] = "synthetic_sample"
    lines = [synthetic, good, short]
    (nr_dir / "nr_observation_interface.jsonl").write_text(
        "".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in lines), encoding="utf-8"
    )

    event_dir = root / "events"
    event_dir.mkdir()
    (event_dir / "observations.jsonl").write_text(
        json.dumps(
            {
                "observation_id": "price-event-same-minute",
                "episode_id": "price-episode",
                "observation_kind": "cooldown",
                "entry_ms": entry,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    write_portable_models(root)
    return root


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def test_build_nr_comparison_preserves_cards_and_side_width_rows(tmp_path: Path) -> None:
    root = prepare_root(tmp_path)
    summary = build_nr_comparison(root=root)

    assert summary["counts"]["input_rows"] == 3
    assert summary["counts"]["native_nr_cards"] == 2
    assert summary["counts"]["ordinary_8_24h_cards"] == 1
    assert summary["counts"]["comparison_rows"] == 12
    assert summary["counts"]["cards_with_primary_width_settled_row"] == 1
    assert summary["status_counts"]["settled"] == 6
    assert summary["status_counts"]["dte_excluded"] == 6
    assert summary["counts"]["model_prediction_rows"] == 2

    out = root / "nr-comparison"
    card_rows = read_csv(out / "nr_card_ledger.csv")
    assert [row["card_id"] for row in card_rows] == ["good", "short"]
    assert card_rows[0]["price_event_overlap_status"] == "matched"
    assert "price-event-same-minute" in card_rows[0]["price_event_overlap_ids"]
    assert card_rows[1]["primary_failure_reason"] == "dte_excluded"

    comparison_rows = read_csv(out / "nr_side_width_comparison.csv")
    good_rows = [row for row in comparison_rows if row["card_id"] == "good"]
    assert {row["comparison_bucket"] for row in good_rows if row["side"] == "put"} == {"original_direction_side"}
    assert {row["comparison_bucket"] for row in good_rows if row["side"] == "call"} == {"opposite_side"}
    primary_call = [row for row in good_rows if row["side"] == "call" and row["target_width"] == "2000"][0]
    assert primary_call["result_status"] == "settled"
    assert primary_call["short_strike"] == "81000.0"
    assert primary_call["long_strike"] == "83000.0"
    assert float(primary_call["payout_btc"]) > 0
    assert float(primary_call["loss_normalized"]) > 0
    assert primary_call["payout_intrusion_at_breakeven_unknown_credit"] == "not_evaluated_without_net_credit"

    prediction_lines = (out / "prediction_requests.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(prediction_lines) == 2
    prediction = json.loads(prediction_lines[0])
    assert prediction["prediction_status"] == "awaiting_model_artifact"
    assert prediction["model_required"] == "astra_joint_two_part_payout_model@pending"

    model_prediction_lines = (out / "nr_model_predictions.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(model_prediction_lines) == 2
    model_prediction = json.loads(model_prediction_lines[0])
    assert model_prediction["domain_status"] == "native_nr_domain_shift_from_price_rebalance_training_family"
    assert model_prediction["qualification_status"] == "development_projection_only_not_nr_model_qualification"
    assert model_prediction["sample_weight"] == 1
    assert model_prediction["predictions"]["champion_statistical"]["status"] == "available"
    assert model_prediction["predictions"]["joint"]["expected_loss_normalized"] == 0.5
    assert summary["model_prediction_summary"]["by_model"]["champion_statistical"]["rows_scored_unique_identity"] == 2


def test_missing_delivery_keeps_rows_but_does_not_count_results(tmp_path: Path) -> None:
    root = prepare_root(tmp_path)
    (root / "raw" / "deribit" / "delivery_prices.json").write_text("[]", encoding="utf-8")
    summary = build_nr_comparison(root=root, link_price_events=False)

    assert summary["counts"]["native_nr_cards"] == 2
    assert summary["counts"]["ordinary_8_24h_cards"] == 1
    assert summary["counts"]["cards_with_any_settled_row"] == 0
    assert summary["status_counts"]["missing_official_delivery"] == 6
    rows = read_csv(root / "nr-comparison" / "nr_side_width_comparison.csv")
    missing = [row for row in rows if row["card_id"] == "good"]
    assert {row["result_status"] for row in missing} == {"not_counted"}
    assert {row["failure_reason"] for row in missing} == {"missing_official_delivery"}


def test_duplicate_identity_is_preserved_but_not_counted_as_independent_sample(tmp_path: Path) -> None:
    root = prepare_root(tmp_path)
    interface = root / "archives-analysis" / "nr_observation_interface.jsonl"
    rows = [json.loads(line) for line in interface.read_text(encoding="utf-8").splitlines() if line.strip()]
    duplicate = json.loads(json.dumps(rows[1]))
    duplicate["source"]["fact_line"] = 2
    interface.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in [rows[0], rows[1], duplicate, rows[2]]),
        encoding="utf-8",
    )

    summary = build_nr_comparison(root=root, link_price_events=False)

    assert summary["counts"]["native_nr_cards"] == 3
    assert summary["counts"]["native_nr_unique_identities"] == 2
    assert summary["counts"]["duplicate_identity_groups"] == 1
    assert summary["counts"]["ordinary_8_24h_rows"] == 2
    assert summary["counts"]["ordinary_8_24h_cards"] == 1
    assert summary["row_status_counts_all_rows"]["settled"] == 12
    assert summary["status_counts"]["settled"] == 6
    assert summary["counts"]["prediction_request_rows"] == 4
    assert summary["counts"]["model_prediction_rows"] == 4
    assert summary["model_prediction_summary"]["by_model"]["champion_statistical"]["rows_scored_unique_identity"] == 2
    assert summary["duplicate_identity_groups"][0]["relation"] == "same_card_and_record_hash_payload_differs_only_source_fact_line"

    card_rows = read_csv(root / "nr-comparison" / "nr_card_ledger.csv")
    duplicate_rows = [row for row in card_rows if row["card_id"] == "good"]
    assert [row["identity_duplicate_role"] for row in duplicate_rows] == ["primary_identity", "duplicate_identity"]
    assert [row["sample_weight"] for row in duplicate_rows] == ["1", "0"]
