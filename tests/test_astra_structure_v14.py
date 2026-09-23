from __future__ import annotations

import csv
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import astra_structure_v14 as v14


def ms(text: str) -> int:
    return int(pd.Timestamp(text, tz="UTC").timestamp() * 1000)


def iso(ms_value: int) -> str:
    return datetime.fromtimestamp(ms_value / 1000, tz=UTC).isoformat()


def payout(side: str, short: float, long: float, settlement: float) -> float:
    if side == "put":
        return (max(short - settlement, 0.0) - max(long - settlement, 0.0)) / settlement
    return (max(settlement - short, 0.0) - max(settlement - long, 0.0)) / settlement


def pair_row(row_id: str, side: str, date: str, settlement: float, label_hint: float | None = None, vol_240: float | None = 0.02) -> dict[str, object]:
    asof = ms(date + "T00:00:00")
    expiry = asof + 12 * 60 * 60 * 1000
    if side == "put":
        entry, short, long, outward_short, outward_long = 101.0, 100.0, 90.0, 95.0, 85.0
    else:
        entry, short, long, outward_short, outward_long = 99.0, 100.0, 110.0, 105.0, 115.0
    width = abs(short - long)
    original = payout(side, short, long, settlement)
    outward = payout(side, outward_short, outward_long, settlement)
    denom = width / entry
    return {
        "row_id": row_id,
        "observation_id": "obs-" + row_id,
        "side": side,
        "side_credit": side + "_credit",
        "as_of_ms": asof,
        "as_of_utc": iso(asof),
        "entry_ms": asof,
        "entry_utc": iso(asof),
        "expiry_ms": expiry,
        "expiry_utc": iso(expiry),
        "delivery_date": datetime.fromtimestamp(expiry / 1000, tz=UTC).date().isoformat(),
        "delivery_year": datetime.fromtimestamp(expiry / 1000, tz=UTC).year,
        "actual_width": width,
        "entry_price": entry,
        "dte_hours": 12.0,
        "short_strike": short,
        "long_strike": long,
        "outward_short_strike": outward_short,
        "outward_long_strike": outward_long,
        "shift": abs(outward_short - short),
        "distance_to_width": abs(entry - short) / width,
        "shift_to_width": abs(outward_short - short) / width,
        "short_distance_fraction": abs(short / entry - 1.0),
        "outward_short_distance_fraction": abs(outward_short / entry - 1.0),
        "width_fraction": width / entry,
        "vol_240": vol_240,
        "short_name": row_id + "-short",
        "long_name": row_id + "-long",
        "outward_short_name": row_id + "-out-short",
        "outward_long_name": row_id + "-out-long",
        "short_creation_ms": asof - 1000,
        "long_creation_ms": asof - 1000,
        "outward_short_creation_ms": asof - 1000,
        "outward_long_creation_ms": asof - 1000,
        "settlement_price": settlement,
        "official_delivery_price": settlement,
        "payout_btc": original,
        "outward_payout_btc": outward,
        "loss_normalized": original / denom,
        "outward_loss_normalized": outward / denom,
        "normalization_denominator_btc": denom,
        "delta_btc": original - outward,
        "delta_normalized": (original - outward) / denom if label_hint is None else label_hint,
        "short_leg_breached": original > 0,
        "protection_leg_breached": False,
        "outward_short_leg_breached": outward > 0,
        "outward_protection_leg_breached": False,
        "both_safe": original == 0 and outward == 0,
        "both_protection_leg_breached": False,
        "payout_category": "partial",
        "outward_payout_category": "zero" if outward == 0 else "partial",
    }


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def make_protocol(root: Path, files: list[Path], evaluation_years: list[int] | None = None, *, corrupt_delivery_hash: bool = False) -> Path:
    r14 = root / "r14"
    source_manifest = {"files": {str(path): {"sha256": v14.sha256_file(path), "bytes": path.stat().st_size} for path in files}}
    if corrupt_delivery_hash:
        for path in source_manifest["files"]:
            if Path(path).name == "delivery_prices.json":
                source_manifest["files"][path]["sha256"] = "0" * 64
    write_json(r14 / "source_manifest.json", source_manifest)
    protocol = {
        "schema": "astra_entry_exit_protocol@1.4.0",
        "new_results_observed": False,
        "structure": {
            "fit_source_years": [2020, 2021, 2022],
            "evaluation_years": evaluation_years or [2022],
            "geometry_edges": {
                "dte_hours": [16.0],
                "distance_to_width": [0.5],
                "shift_to_width": [0.5],
                "width_fraction": [0.05],
            },
            "minimum_cell_days": 30,
            "prior_days": 60,
            "candidates": list(v14.CANDIDATES),
            "calibration_minimum_days": 30,
            "calibration_prior_days": 30,
        },
        "uncertainty": {"replicates": 50, "seed": 20260922},
        "source_manifest_sha256": v14.sha256_file(r14 / "source_manifest.json"),
    }
    write_json(r14 / "protocol_v14.json", protocol)
    write_json(
        r14 / "protocol_seal.json",
        {
            "protocol_sha256": v14.sha256_file(r14 / "protocol_v14.json"),
            "new_results_observed": False,
        },
    )
    write_json(r14 / "preresult_amendment_01.json", {"clarification": "fixture"})
    return r14 / "protocol_v14.json"


def make_research_v13(
    tmp_path: Path,
    *,
    corrupt_old_label: bool = False,
    tail: bool = False,
    nested_integrity_ok: bool = True,
    corrupt_delivery_hash: bool = False,
    include_vol: bool = True,
    nan_vol: bool = False,
) -> tuple[Path, Path]:
    r13 = tmp_path / "r13"
    delivery = []
    rows = []
    specs = [
        ("fit-put", "put", "2020-01-01", 97.0),
        ("fit-call", "call", "2020-01-01", 97.0),
        ("cal-put", "put", "2021-10-02", 97.0),
        ("cal-call", "call", "2021-10-02", 97.0),
        ("eval-put", "put", "2022-01-02", 80.0 if tail else 97.0),
        ("eval-call", "call", "2022-01-02", 80.0 if tail else 97.0),
    ]
    for row_id, side, date, settlement in specs:
        row = pair_row(row_id, side, date, settlement, vol_240=(float("nan") if nan_vol else 0.02))
        if not include_vol:
            row.pop("vol_240", None)
        rows.append(row)
        delivery.append({"date": row["delivery_date"], "delivery_price": settlement})
    write_csv(r13 / "structure_ledger_01" / "paired_structure_rows.csv", rows)
    gap_asof = ms("2022-01-02T00:00:00")
    gap_expiry = gap_asof + 12 * 60 * 60 * 1000
    gap = {
        "row_id": "gap-put",
        "side": "put",
        "as_of_ms": gap_asof,
        "expiry_ms": gap_expiry,
        "delivery_year": 2022,
        "delivery_date": "2022-01-02",
    }
    write_csv(r13 / "structure_ledger_01" / "pairing_gaps.csv", [gap])
    manifest = {
        "data_integrity": {"ok": nested_integrity_ok},
        "outputs": {
            "paired_structure_rows.csv": {
                "sha256": v14.sha256_file(r13 / "structure_ledger_01" / "paired_structure_rows.csv")
            },
            "pairing_gaps.csv": {
                "sha256": v14.sha256_file(r13 / "structure_ledger_01" / "pairing_gaps.csv")
            },
        },
    }
    write_json(r13 / "structure_ledger_01" / "manifest.json", manifest)
    eval_rows = [row for row in rows if str(row["row_id"]).startswith("eval")]
    old_rows = []
    for row in eval_rows:
        label = float(row["delta_normalized"]) + (0.1 if corrupt_old_label and row["row_id"] == "eval-put" else 0.0)
        old_rows.append(
            {
                "row_id": row["row_id"],
                "delta_normalized": label,
                "HIST_SIDE_MEAN": 0.10,
                "GEOMETRY_TABLE": 0.20,
                "GEOMETRY_VOL_TABLE": 0.25,
            }
        )
    write_csv(r13 / "structure_evaluation_01" / "structure_predictions.csv", old_rows)
    delivery_path = tmp_path / "r1" / "raw" / "deribit" / "delivery_prices.json"
    dedup = {row["date"]: row for row in delivery}
    write_json(delivery_path, list(dedup.values()))
    protocol = make_protocol(
        tmp_path,
        [
            r13 / "structure_ledger_01" / "paired_structure_rows.csv",
            r13 / "structure_ledger_01" / "pairing_gaps.csv",
            r13 / "structure_ledger_01" / "manifest.json",
            r13 / "structure_evaluation_01" / "structure_predictions.csv",
            delivery_path,
        ],
        corrupt_delivery_hash=corrupt_delivery_hash,
    )
    return r13, protocol


def training(days: int = 30, duplicate_first: bool = False) -> pd.DataFrame:
    rows = []
    for i in range(days):
        rows.append(
            {
                "side": "put",
                "delivery_date": (pd.Timestamp("2020-01-01") + pd.Timedelta(days=i)).strftime("%Y-%m-%d"),
                "dte_hours": 12.0,
                "distance_to_width": 0.2,
                "shift_to_width": 0.4,
                "width_fraction": 0.04,
                "delta_normalized": 0.2,
            }
        )
    if duplicate_first:
        rows.append(dict(rows[0]))
    return pd.DataFrame(rows)


def protocol_dict() -> dict[str, object]:
    return {
        "structure": {
            "geometry_edges": {
                "dte_hours": [16.0],
                "distance_to_width": [0.5],
                "shift_to_width": [0.5],
                "width_fraction": [0.05],
            },
            "minimum_cell_days": 30,
            "prior_days": 60,
            "calibration_minimum_days": 30,
            "calibration_prior_days": 30,
        }
    }


def test_date_weighted_fit_ignores_duplicate_same_day() -> None:
    first = v14.fit_coarse_model(training(30), protocol_dict())
    second = v14.fit_coarse_model(training(30, duplicate_first=True), protocol_dict())
    assert first["side_mean"] == pytest.approx(second["side_mean"])
    assert first["cells"] == second["cells"]


def test_sparse_grid_falls_back_to_side_mean() -> None:
    model = v14.fit_coarse_model(training(29), protocol_dict())
    assert model["cells"] == {}
    pred = v14.predict_coarse(training(1), model, protocol_dict())
    assert pred.COARSE_GEOM_SHRINK.iloc[0] == pytest.approx(0.2)
    assert pred.COARSE_GEOM_SHRINK_supported.iloc[0] == False


def test_missing_or_nan_vol_does_not_change_geometry_predictions(tmp_path: Path) -> None:
    r13_a, protocol_a = make_research_v13(tmp_path / "a", include_vol=True)
    r13_b, protocol_b = make_research_v13(tmp_path / "b", include_vol=False)
    r13_c, protocol_c = make_research_v13(tmp_path / "c", nan_vol=True)

    a = v14.run(r13_a, protocol_a, tmp_path / "out-a")
    b = v14.run(r13_b, protocol_b, tmp_path / "out-b")
    c = v14.run(r13_c, protocol_c, tmp_path / "out-c")
    pa = pd.read_csv(tmp_path / "out-a" / "predictions.csv")
    pb = pd.read_csv(tmp_path / "out-b" / "predictions.csv")
    pc = pd.read_csv(tmp_path / "out-c" / "predictions.csv")

    assert "vol_240" in pa.columns
    assert "vol_240" not in pb.columns
    assert pb.COARSE_GEOM_SHRINK.tolist() == pytest.approx(pa.COARSE_GEOM_SHRINK.tolist())
    assert pc.COARSE_GEOM_SHRINK.tolist() == pytest.approx(pa.COARSE_GEOM_SHRINK.tolist())
    assert b["rows"] == a["rows"] == c["rows"]


def test_calibration_scale_nonnegative_and_zero_prediction_cases() -> None:
    dates = [(pd.Timestamp("2021-10-01") + pd.Timedelta(days=i)).strftime("%Y-%m-%d") for i in range(30)]
    cal = pd.DataFrame({"delivery_date": dates, "delta_normalized": [0.2] * 30, "COARSE_GEOM_SHRINK": [0.1] * 30})
    scale = v14.fit_calibration(cal, protocol_dict())
    assert scale["qualified"] is True
    assert scale["scale"] == pytest.approx(1.5)
    zero_ok = v14.fit_calibration(cal.assign(delta_normalized=0.0, COARSE_GEOM_SHRINK=0.0), protocol_dict())
    assert zero_ok["qualified"] is True
    assert zero_ok["scale"] == 1.0
    zero_bad = v14.fit_calibration(cal.assign(COARSE_GEOM_SHRINK=0.0), protocol_dict())
    assert zero_bad["qualified"] is False
    assert zero_bad["scale"] == 1.0


def test_split_year_strict_expiry_purge() -> None:
    frame = pd.DataFrame(
        [
            {"as_of_ms": ms("2021-09-30T00:00:00"), "expiry_ms": ms("2021-10-01T00:00:00"), "delivery_date": "2021-10-01"},
            {"as_of_ms": ms("2021-09-29T00:00:00"), "expiry_ms": ms("2021-09-30T00:00:00"), "delivery_date": "2021-09-30"},
            {"as_of_ms": ms("2021-10-01T00:00:00"), "expiry_ms": ms("2021-10-02T00:00:00"), "delivery_date": "2021-10-02"},
            {"as_of_ms": ms("2022-01-01T00:00:00"), "expiry_ms": ms("2022-01-02T00:00:00"), "delivery_date": "2022-01-02"},
        ]
    )
    (fit, cal, ev), meta = v14.split_year(frame, 2022)
    assert [len(fit), len(cal), len(ev)] == [1, 1, 1]
    assert meta["purged_cross_boundary_rows"] == [1, 0, 0]


def test_year_support_filters_gaps_by_same_evaluation_window_not_delivery_year() -> None:
    result = pd.DataFrame(
        [
            {
                "side": "call",
                "evaluation_year": 2022,
                "delivery_date": "2022-01-02",
                "COARSE_GEOM_SHRINK_supported": True,
                "COARSE_GEOM_CAL3M_calibration_qualified": True,
            }
        ]
    )
    gaps = pd.DataFrame(
        [
            {
                "row_id": "eval-gap",
                "side": "call",
                "as_of_ms": ms("2022-01-02T00:00:00"),
                "expiry_ms": ms("2022-01-02T12:00:00"),
                "delivery_date": "2022-01-02",
                "delivery_year": 2022,
            },
            {
                "row_id": "cal-gap-with-2022-delivery",
                "side": "call",
                "as_of_ms": ms("2021-12-31T23:30:00"),
                "expiry_ms": ms("2022-01-01T08:00:00"),
                "delivery_date": "2022-01-01",
                "delivery_year": 2022,
            },
        ]
    )

    support = v14.year_support(result, gaps, [2022], "call", "COARSE_GEOM_SHRINK_supported", False)

    assert support["paired_rows"] == 1
    assert support["original_rows"] == 2
    assert support["paired_fraction"] == pytest.approx(0.5)


def test_year_support_fails_when_nonempty_gap_lacks_window_columns() -> None:
    result = pd.DataFrame(
        [{"side": "put", "evaluation_year": 2022, "delivery_date": "2022-01-02", "COARSE_GEOM_SHRINK_supported": True}]
    )
    gaps = pd.DataFrame([{"row_id": "gap", "side": "put", "delivery_date": "2022-01-02", "delivery_year": 2022}])

    with pytest.raises(ValueError, match="gap ledger missing"):
        v14.year_support(result, gaps, [2022], "put", "COARSE_GEOM_SHRINK_supported", False)


def test_run_writes_outputs_and_keeps_uncapped_tail(tmp_path: Path) -> None:
    r13, protocol = make_research_v13(tmp_path, tail=True)
    summary = v14.run(r13, protocol, tmp_path / "out")

    assert summary["rows"] == 2
    assert summary["preresult_amendment_01_sha256"]
    assert summary["outputs"]["predictions.csv"]["sha256"]
    assert "summary.json" not in summary["outputs"]
    assert summary["sides"]["put"]["full_tail"]["original_over_one_rows"] == 1
    assert (tmp_path / "out" / "predictions.csv").exists()
    assert json.loads((tmp_path / "out" / "failure.json").read_text(encoding="utf-8"))["failed"] is False
    receipt = json.loads((tmp_path / "out" / "receipt_manifest.json").read_text(encoding="utf-8"))
    assert receipt["outputs"]["summary.json"]["sha256"] == v14.sha256_file(tmp_path / "out" / "summary.json")
    assert receipt["outputs"]["predictions.csv"]["sha256"] == v14.sha256_file(tmp_path / "out" / "predictions.csv")


def test_output_directory_must_not_exist(tmp_path: Path) -> None:
    r13, protocol = make_research_v13(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(FileExistsError):
        v14.run(r13, protocol, out)


def test_old_comparator_label_mismatch_fails_before_modeling(tmp_path: Path) -> None:
    r13, protocol = make_research_v13(tmp_path, corrupt_old_label=True)
    with pytest.raises(ValueError, match="label"):
        v14.run(r13, protocol, tmp_path / "out")
    assert json.loads((tmp_path / "out" / "failure.json").read_text(encoding="utf-8"))["failed"] is True


def test_nested_data_integrity_must_be_true(tmp_path: Path) -> None:
    r13, protocol = make_research_v13(tmp_path, nested_integrity_ok=False)
    with pytest.raises(ValueError, match="data_integrity.ok"):
        v14.run(r13, protocol, tmp_path / "out")


def test_delivery_prices_hash_is_checked_before_read(tmp_path: Path) -> None:
    r13, protocol = make_research_v13(tmp_path, corrupt_delivery_hash=True)
    with pytest.raises(ValueError, match="delivery_prices|hash mismatch|frozen input"):
        v14.run(r13, protocol, tmp_path / "out")
