from __future__ import annotations

import csv
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import astra_entry_structure as structure


BASE = datetime(2022, 1, 1, 0, 0, tzinfo=UTC)
ENTRY_MS = int(BASE.timestamp() * 1000)
EXPIRY_MS = int((BASE + timedelta(hours=12)).timestamp() * 1000)
DATE = datetime.fromtimestamp(EXPIRY_MS / 1000, tz=UTC).date().isoformat()


def ms_iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()


def contract(side: str, strike: float, created: int | None = None) -> dict[str, object]:
    code = "P" if side == "put" else "C"
    return {
        "kind": "option",
        "base_currency": "BTC",
        "quote_currency": "BTC",
        "counter_currency": "USD",
        "settlement_currency": "BTC",
        "price_index": "btc_usd",
        "contract_size": 1.0,
        "option_type": side,
        "strike": strike,
        "instrument_name": f"BTC-UNIT-{int(strike)}-{code}",
        "creation_timestamp": ENTRY_MS - 1_000 if created is None else created,
        "expiration_timestamp": EXPIRY_MS,
    }


def put_row(row_id: str, settlement: float, *, status: str = "settled", entry_price: float = 100.0) -> dict[str, object]:
    payout = structure._payout_btc("put", 100.0, 90.0, settlement)
    denom = 10.0 / entry_price
    return {
        "schema": "fixture",
        "row_id": row_id,
        "observation_id": f"obs-{row_id}",
        "event_family": "fixture",
        "observation_kind": "clock_30m",
        "as_of_ms": ENTRY_MS,
        "as_of_utc": ms_iso(ENTRY_MS),
        "entry_ms": ENTRY_MS,
        "entry_utc": ms_iso(ENTRY_MS),
        "expiry_ms": EXPIRY_MS,
        "expiry_utc": ms_iso(EXPIRY_MS),
        "delivery_date": DATE,
        "delivery_year": 2022,
        "split": "fixture",
        "historical_usage_role": "fixture",
        "side": "put",
        "target_width": 2000.0,
        "actual_width": 10.0,
        "short_strike": 100.0,
        "long_strike": 90.0,
        "short_name": "BTC-UNIT-100-P",
        "long_name": "BTC-UNIT-90-P",
        "short_creation_ms": ENTRY_MS - 1_000,
        "long_creation_ms": ENTRY_MS - 1_000,
        "entry_price": entry_price,
        "price_source": "fixture",
        "price_observation_ms": ENTRY_MS - 1,
        "price_observation_utc": ms_iso(ENTRY_MS - 1),
        "spot_source_file": "fixture-spot",
        "um_source_file": "fixture-um",
        "option_source_sha256": "optionhash",
        "delivery_source_sha256": "deliveryhash",
        "source_observation_hash": f"source-{row_id}",
        "preoutcome_row_hash": f"pre-{row_id}",
        "dte_hours": 12.0,
        "short_distance_fraction": abs(100.0 / entry_price - 1.0),
        "width_fraction": 10.0 / entry_price,
        "side_sign": -1.0,
        "ret_15": 0.0,
        "ret_30": 0.0,
        "ret_240": 0.0,
        "ret_720": 0.0,
        "ret_1440": 0.0,
        "vol_15": 0.01,
        "vol_30": 0.01,
        "vol_240": 0.02,
        "vol_720": 0.02,
        "vol_1440": 0.02,
        "net_flow_15": 0.0,
        "net_flow_30": 0.0,
        "net_flow_240": 0.0,
        "hour_sin": 0.0,
        "hour_cos": 1.0,
        "adverse_return_15": 0.0,
        "adverse_return_30": 0.0,
        "adverse_return_240": 0.0,
        "adverse_return_720": 0.0,
        "adverse_return_1440": 0.0,
        "adverse_flow_15": 0.0,
        "adverse_flow_30": 0.0,
        "adverse_flow_240": 0.0,
        "vwap_deviation_15": 0.0,
        "vwap_deviation_30": 0.0,
        "vwap_migration_15": 0.0,
        "range_position_15": 0.5,
        "range_position_30": 0.5,
        "range_expansion_15": 0.0,
        "efficiency_15": 0.0,
        "efficiency_30": 0.0,
        "pressure_response_15": 0.0,
        "pressure_response_30": 0.0,
        "adverse_range_room_15": 0.5,
        "adverse_range_room_30": 0.5,
        "favorable_range_room_15": 0.5,
        "favorable_range_room_30": 0.5,
        "adverse_flow_response_15": 0.0,
        "adverse_flow_response_30": 0.0,
        "favorable_flow_response_15": 0.0,
        "favorable_flow_response_30": 0.0,
        "settlement_price": settlement,
        "payout_btc": payout,
        "loss_normalized": payout / denom,
        "short_leg_breached": settlement <= 100.0,
        "protection_leg_breached": settlement <= 90.0,
        "outcome_status": status,
    }


def call_row(row_id: str, settlement: float) -> dict[str, object]:
    row = put_row(row_id, settlement)
    row.update(
        {
            "side": "call",
            "side_sign": 1.0,
            "short_strike": 100.0,
            "long_strike": 110.0,
            "short_name": "BTC-UNIT-100-C",
            "long_name": "BTC-UNIT-110-C",
            "payout_btc": structure._payout_btc("call", 100.0, 110.0, settlement),
            "short_leg_breached": settlement >= 100.0,
            "protection_leg_breached": settlement >= 110.0,
        }
    )
    row["loss_normalized"] = row["payout_btc"] / 0.1
    return row


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def write_fixture(
    root: Path,
    rows: list[dict[str, object]],
    contracts: list[dict[str, object]],
    delivery_price: float,
) -> tuple[Path, Path, Path]:
    research_v11 = root / "v11"
    research_v1 = root / "v1"
    write_csv(research_v11 / "step30" / "model_input" / "model_rows-2022.csv", rows)
    raw = research_v1 / "raw" / "deribit"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "instruments.json").write_text(json.dumps(contracts), encoding="utf-8")
    (raw / "delivery_prices.json").write_text(
        json.dumps([{"date": DATE, "delivery_price": delivery_price}]),
        encoding="utf-8",
    )
    write_source_manifest(root, research_v11, research_v1)
    return research_v11, research_v1, root / "out"


def write_source_manifest(root: Path, research_v11: Path, research_v1: Path, *, omit: set[Path] | None = None) -> None:
    omit = omit or set()
    source_files = {}
    for path in [
        research_v11 / "step30" / "model_input" / "model_rows-2022.csv",
        research_v1 / "raw" / "deribit" / "instruments.json",
        research_v1 / "raw" / "deribit" / "delivery_prices.json",
    ]:
        if path in omit:
            continue
        source_files[str(path)] = {"sha256": structure.sha256_file(path), "bytes": path.stat().st_size}
    (root / "source_manifest.json").write_text(json.dumps({"files": source_files}), encoding="utf-8")


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def put_contracts_with_future_nearer() -> list[dict[str, object]]:
    return [
        contract("put", 100.0),
        contract("put", 90.0),
        contract("put", 97.0, ENTRY_MS + 1),
        contract("put", 87.0, ENTRY_MS + 1),
        contract("put", 95.0),
        contract("put", 85.0),
        contract("put", 80.0),
        contract("put", 70.0),
    ]


def test_output_schemas_have_unique_columns() -> None:
    assert len(structure.PAIR_COLUMNS) == len(set(structure.PAIR_COLUMNS))
    assert len(structure.GAP_COLUMNS) == len(set(structure.GAP_COLUMNS))
    assert structure.PAIR_COLUMNS.count("vol_240") == 1


def test_selects_nearest_exact_width_pair_and_excludes_future_listing(tmp_path: Path) -> None:
    research_v11, research_v1, output = write_fixture(
        tmp_path,
        [put_row("finite", 97.0)],
        put_contracts_with_future_nearer(),
        97.0,
    )

    manifest = structure.build_structure_ledger(research_v11, research_v1, output, [2022])
    rows = read_rows(output / "paired_structure_rows.csv")

    assert manifest["coverage"]["paired_rows"] == 1
    assert manifest["data_integrity_ok"] is True
    assert manifest["outputs"]["paired_structure_rows.csv"]["rows"] == 1
    assert len(manifest["outputs"]["paired_structure_rows.csv"]["sha256"]) == 64
    assert rows[0]["outward_short_strike"] == "95.0"
    assert rows[0]["outward_long_strike"] == "85.0"
    assert float(rows[0]["delta_btc"]) == pytest.approx(3.0 / 97.0)
    assert float(rows[0]["delta_normalized"]) == pytest.approx((3.0 / 97.0) / 0.1)


def test_missing_exact_width_pair_is_preserved_as_gap(tmp_path: Path) -> None:
    research_v11, research_v1, output = write_fixture(
        tmp_path,
        [put_row("gap", 97.0)],
        [contract("put", 100.0), contract("put", 90.0), contract("put", 95.0), contract("put", 84.0)],
        97.0,
    )

    manifest = structure.build_structure_ledger(research_v11, research_v1, output, [2022])
    gaps = read_rows(output / "pairing_gaps.csv")

    assert manifest["coverage"]["paired_rows"] == 0
    assert manifest["data_integrity_ok"] is True
    assert manifest["geometry_distribution_label_free"]["original_all_rows"]["distance_to_width"]["count"] == 1
    assert manifest["geometry_distribution_label_free"]["paired_rows"]["distance_to_width"]["count"] == 0
    assert manifest["geometry_distribution_label_free"]["gap_rows"]["distance_to_width"]["count"] == 1
    assert gaps[0]["row_id"] == "gap"
    assert gaps[0]["reason"] == "no_outward_short_with_exact_same_width_protection"


def test_tail_states_keep_zero_positive_and_uncapped_coin_normalized_cases(tmp_path: Path) -> None:
    research_v11, research_v1, output = write_fixture(
        tmp_path,
        [put_row("safe", 110.0), put_row("finite", 97.0), put_row("tail", 80.0)],
        put_contracts_with_future_nearer(),
        110.0,
    )
    # Give each row the same official price by rerunning separately would hide
    # the state matrix, so call build_pair_row directly after validating pairing.
    contracts, by_expiry = structure.load_instruments(research_v1 / "raw" / "deribit" / "instruments.json")
    rows = []
    for settlement in (110.0, 97.0, 80.0):
        row = put_row(f"r{settlement}", settlement)
        short, long, reason = structure.find_outward_pair(row, contracts, by_expiry)
        assert reason is None
        rows.append(structure.build_pair_row(row, short, long, settlement))

    assert rows[0]["both_safe"] is True
    assert rows[0]["delta_btc"] == pytest.approx(0.0)
    assert rows[1]["delta_btc"] > 0
    assert rows[2]["both_protection_leg_breached"] is True
    assert rows[2]["delta_btc"] == pytest.approx(0.0)
    assert rows[2]["loss_normalized"] == pytest.approx(1.25)
    assert rows[2]["outward_loss_normalized"] == pytest.approx(1.25)
    assert rows[2]["normalization_denominator_btc"] == pytest.approx(0.1)


def test_tail_flags_use_strict_breach_boundaries() -> None:
    put_short, put_protection, put_category = structure._tail_flags("put", 100.0, 90.0, 90.0)
    call_short, call_protection, call_category = structure._tail_flags("call", 100.0, 110.0, 110.0)

    assert put_short is True
    assert put_protection is False
    assert put_category == "full_width"
    assert call_short is True
    assert call_protection is False
    assert call_category == "full_width"


def test_call_side_outward_pair_uses_same_width_and_side(tmp_path: Path) -> None:
    contracts = [
        contract("call", 100.0),
        contract("call", 110.0),
        contract("call", 105.0),
        contract("call", 115.0),
        contract("put", 105.0),
        contract("put", 95.0),
    ]
    research_v11, research_v1, output = write_fixture(tmp_path, [call_row("call", 103.0)], contracts, 103.0)

    structure.build_structure_ledger(research_v11, research_v1, output, [2022])
    rows = read_rows(output / "paired_structure_rows.csv")

    assert rows[0]["side"] == "call"
    assert rows[0]["outward_short_strike"] == "105.0"
    assert rows[0]["outward_long_strike"] == "115.0"
    assert float(rows[0]["delta_btc"]) == pytest.approx(3.0 / 103.0)


def test_original_leg_attribute_mismatch_is_a_gap(tmp_path: Path) -> None:
    long_leg = contract("put", 90.0)
    long_leg["settlement_currency"] = "USDC"
    research_v11, research_v1, output = write_fixture(
        tmp_path,
        [put_row("attrs", 97.0)],
        [contract("put", 100.0), long_leg, contract("put", 95.0), contract("put", 85.0)],
        97.0,
    )

    manifest = structure.build_structure_ledger(research_v11, research_v1, output, [2022])
    gaps = read_rows(output / "pairing_gaps.csv")

    assert manifest["coverage"]["paired_rows"] == 0
    assert manifest["data_integrity_ok"] is False
    assert manifest["data_integrity"]["integrity_gap_reasons"] == {"original_leg_attributes_mismatch": 1}
    assert gaps[0]["reason"] == "original_leg_attributes_mismatch"


def test_original_created_after_entry_is_gap_and_integrity_failure(tmp_path: Path) -> None:
    row = put_row("late", 97.0)
    row["short_creation_ms"] = ENTRY_MS + 1
    late_short = contract("put", 100.0, ENTRY_MS + 1)
    research_v11, research_v1, output = write_fixture(
        tmp_path,
        [row],
        [late_short, contract("put", 90.0), contract("put", 95.0), contract("put", 85.0)],
        97.0,
    )

    manifest = structure.build_structure_ledger(research_v11, research_v1, output, [2022])
    gaps = read_rows(output / "pairing_gaps.csv")

    assert manifest["data_integrity_ok"] is False
    assert gaps[0]["reason"] == "original_short_created_after_entry"


def test_cli_fails_after_writing_integrity_gap_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    row = put_row("late-cli", 97.0)
    row["short_creation_ms"] = ENTRY_MS + 1
    research_v11, research_v1, output = write_fixture(
        tmp_path,
        [row],
        [contract("put", 100.0, ENTRY_MS + 1), contract("put", 90.0), contract("put", 95.0), contract("put", 85.0)],
        97.0,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "astra_entry_structure.py",
            "--research-v11",
            str(research_v11),
            "--research-v1",
            str(research_v1),
            "--output",
            str(output),
            "--years",
            "2022",
        ],
    )

    with pytest.raises(SystemExit, match="data integrity gaps"):
        structure.main()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["data_integrity_ok"] is False


def test_non_settled_rows_are_not_selected(tmp_path: Path) -> None:
    research_v11, research_v1, output = write_fixture(
        tmp_path,
        [put_row("pending", 97.0, status="pending")],
        put_contracts_with_future_nearer(),
        97.0,
    )

    manifest = structure.build_structure_ledger(research_v11, research_v1, output, [2022])

    assert manifest["coverage"]["input_rows"] == 0
    assert read_rows(output / "paired_structure_rows.csv") == []


def test_official_settlement_mismatch_rejects_source(tmp_path: Path) -> None:
    research_v11, research_v1, output = write_fixture(
        tmp_path,
        [put_row("bad", 97.0)],
        put_contracts_with_future_nearer(),
        96.0,
    )

    with pytest.raises(ValueError, match="official settlement mismatch"):
        structure.build_structure_ledger(research_v11, research_v1, output, [2022])


def test_source_manifest_is_required_and_checked(tmp_path: Path) -> None:
    research_v11, research_v1, output = write_fixture(
        tmp_path,
        [put_row("finite", 97.0)],
        put_contracts_with_future_nearer(),
        97.0,
    )

    manifest = structure.build_structure_ledger(research_v11, research_v1, output, [2022])

    assert manifest["source_manifest_check"]["checked"] is True


def test_missing_source_manifest_entry_fails_closed(tmp_path: Path) -> None:
    research_v11, research_v1, output = write_fixture(
        tmp_path,
        [put_row("finite", 97.0)],
        put_contracts_with_future_nearer(),
        97.0,
    )
    write_source_manifest(
        tmp_path,
        research_v11,
        research_v1,
        omit={research_v1 / "raw" / "deribit" / "instruments.json"},
    )

    with pytest.raises(ValueError, match="source manifest mismatch"):
        structure.build_structure_ledger(research_v11, research_v1, output, [2022])


def test_missing_source_manifest_file_fails_closed(tmp_path: Path) -> None:
    research_v11, research_v1, output = write_fixture(
        tmp_path,
        [put_row("finite", 97.0)],
        put_contracts_with_future_nearer(),
        97.0,
    )
    (tmp_path / "source_manifest.json").unlink()

    with pytest.raises(ValueError, match="missing required source manifest"):
        structure.build_structure_ledger(research_v11, research_v1, output, [2022])
