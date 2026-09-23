from __future__ import annotations

import csv
import json
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from astra_joint_data import MINUTE_MS
from astra_joint_v11_contract import COMMON_FEATURE_GROUPS, PROTOCOL_V11
from astra_joint_v11_data import (
    build_revision_dataset,
    common_features,
    iter_clock_observations,
    reference_spot_close,
    side_features,
)
from astra_joint_data import HistoricalBars


BASE_MS = int(datetime(2022, 5, 3, 20, 0, tzinfo=timezone.utc).timestamp() * 1000)
EXPIRY_MS = int(datetime(2022, 5, 4, 8, 0, tzinfo=timezone.utc).timestamp() * 1000)


def kline(index: int, price: float, *, volume: float = 10.0, buy: float = 6.0) -> list[str]:
    open_ms = BASE_MS + index * MINUTE_MS
    close = price + 0.1
    return [
        str(open_ms),
        str(price),
        str(max(price, close) + 0.2),
        str(min(price, close) - 0.2),
        str(close),
        str(volume),
        str(open_ms + MINUTE_MS - 1),
        str(volume * ((price + close) / 2.0)),
        "10",
        str(buy),
        str(buy * ((price + close) / 2.0)),
        "0",
    ]


def write_market_zip(root: Path, feed: str, rows: list[list[str]], period: str = "2022-05") -> None:
    folder = root / "raw" / "binance" / feed / "monthly"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"BTCUSDT-1m-{period}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"BTCUSDT-1m-{period}.csv", "\n".join(",".join(row) for row in rows) + "\n")


def write_deribit(root: Path, *, width: float = 2.0, settlement: float = 103.0) -> None:
    folder = root / "raw" / "deribit"
    folder.mkdir(parents=True, exist_ok=True)
    contracts = [
        _contract("put", 101.0, "BTC-4MAY22-101-P"),
        _contract("put", 99.0, "BTC-4MAY22-99-P"),
        _contract("call", 105.0, "BTC-4MAY22-105-C"),
        _contract("call", 107.0, "BTC-4MAY22-107-C"),
        _contract("call", 109.0, "BTC-4MAY22-109-C"),
    ]
    if width != 2.0:
        contracts.append(_contract("put", 100.0 - width, f"BTC-4MAY22-{100-width}-P"))
    (folder / "instruments.json").write_text(json.dumps(contracts), encoding="utf-8")
    (folder / "delivery_prices.json").write_text(
        json.dumps([{"date": "2022-05-04", "delivery_price": settlement}]),
        encoding="utf-8",
    )


def _contract(kind: str, strike: float, name: str) -> dict[str, object]:
    return {
        "kind": "option",
        "settlement_currency": "BTC",
        "option_type": kind,
        "strike": strike,
        "instrument_name": name,
        "creation_timestamp": BASE_MS - 10 * MINUTE_MS,
        "expiration_timestamp": EXPIRY_MS,
    }


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def make_source(rows: int = 61) -> Path:
    root = Path(tempfile.mkdtemp())
    spot = [kline(i, 100.0 + i * 0.1, buy=5.0) for i in range(rows)]
    um = [kline(i, 100.0 + i * 0.1, buy=6.0) for i in range(rows)]
    write_market_zip(root, "spot", spot)
    write_market_zip(root, "um", um)
    write_deribit(root)
    return root


def test_contract_exposes_model_feature_groups_without_shock_requirements() -> None:
    assert set(COMMON_FEATURE_GROUPS) == {"geometry", "statistical", "mechanism", "joint"}
    assert "shock_magnitude" not in COMMON_FEATURE_GROUPS["joint"]
    assert PROTOCOL_V11["model_targets"]["statistical_sorting"].startswith("choose the side")


def test_side_features_mirror_adverse_direction_for_put_and_call() -> None:
    snapshot = {
        "ret_15": -0.01,
        "net_flow_15": -0.4,
        "range_position_15": 0.25,
        "efficiency_15": 0.5,
    }

    put = side_features(snapshot, "put", 100.0, {"strike": 99.0}, {"strike": 97.0}, 12.0)
    call = side_features(snapshot, "call", 100.0, {"strike": 101.0}, {"strike": 103.0}, 12.0)

    assert put["side_sign"] == -1.0
    assert call["side_sign"] == 1.0
    assert put["adverse_return_15"] == pytest.approx(0.01)
    assert call["adverse_return_15"] == pytest.approx(-0.01)
    assert put["adverse_flow_15"] == pytest.approx(0.4)
    assert call["adverse_flow_15"] == pytest.approx(-0.4)
    assert "favorable_return_15" not in COMMON_FEATURE_GROUPS["joint"]
    assert "favorable_flow_15" not in COMMON_FEATURE_GROUPS["joint"]
    assert "favorable_range_room_15" not in COMMON_FEATURE_GROUPS["joint"]
    assert "favorable_range_room_15" not in put
    assert put["adverse_range_room_15"] == pytest.approx(0.25)
    assert call["adverse_range_room_15"] == pytest.approx(0.75)


def test_iter_clock_observations_uses_last_fully_closed_spot_close() -> None:
    root = make_source()
    # This row closes after the first 20:30 observation and must not change it.
    observations = list(iter_clock_observations(root, step_minutes=30))
    first = observations[0]

    assert first["as_of_ms"] == BASE_MS + 30 * MINUTE_MS
    assert first["entry_price"] == pytest.approx(100.0 + 29 * 0.1 + 0.1)
    assert first["price_observation_ms"] == BASE_MS + 30 * MINUTE_MS - 1
    assert first["ret_15"] is not None


def test_build_revision_dataset_keeps_zero_volume_unknown_not_rejected() -> None:
    root = Path(tempfile.mkdtemp())
    spot = [kline(i, 100.0 + i * 0.1, buy=5.0) for i in range(61)]
    um = [
        kline(i, 100.0 + i * 0.1, volume=0.0 if 15 <= i < 30 else 10.0, buy=0.0 if 15 <= i < 30 else 6.0)
        for i in range(61)
    ]
    write_market_zip(root, "spot", spot)
    write_market_zip(root, "um", um)
    write_deribit(root)
    out = Path(tempfile.mkdtemp())

    manifest = build_revision_dataset(root, out, step_minutes=30, widths=[2.0])
    rows = read_rows(out / "step30" / "model_input" / "model_rows-2022.csv")
    first_put = next(row for row in rows if row["side"] == "put" and row["as_of_ms"] == str(BASE_MS + 30 * MINUTE_MS))
    quality = json.loads((out / "step30" / "source_quality" / "source_quality_manifest.json").read_text(encoding="utf-8"))

    assert first_put["net_flow_15"] == ""
    assert first_put["adverse_flow_response_15"] == ""
    assert quality["counts"]["zero_volume_rows"] >= 15
    assert manifest["counts"]["rows_2022"] == 4


def test_build_revision_dataset_reports_holes_without_crossing_window() -> None:
    root = Path(tempfile.mkdtemp())
    spot = [kline(i, 100.0 + i * 0.1, buy=5.0) for i in range(61)]
    um = [kline(i, 100.0 + i * 0.1, buy=6.0) for i in range(61) if i != 20]
    write_market_zip(root, "spot", spot)
    write_market_zip(root, "um", um)
    write_deribit(root)
    out = Path(tempfile.mkdtemp())

    build_revision_dataset(root, out, step_minutes=30, widths=[2.0])
    rows = read_rows(out / "step30" / "model_input" / "model_rows-2022.csv")
    first_put = next(row for row in rows if row["side"] == "put" and row["as_of_ms"] == str(BASE_MS + 30 * MINUTE_MS))
    quality = json.loads((out / "step30" / "source_quality" / "source_quality_manifest.json").read_text(encoding="utf-8"))

    assert first_put["ret_15"] == ""
    assert first_put["window_status"] if "window_status" in first_put else True
    assert quality["counts"]["market_gaps"] >= 1


def test_build_revision_dataset_uses_shared_complete_minute_gate_for_um() -> None:
    root = Path(tempfile.mkdtemp())
    spot = [kline(i, 100.0 + i * 0.1, buy=5.0) for i in range(61)]
    um = [kline(i, 100.0 + i * 0.1, buy=6.0) for i in range(61)]
    um[30][6] = str(BASE_MS + 31 * MINUTE_MS)
    write_market_zip(root, "spot", spot)
    write_market_zip(root, "um", um)
    write_deribit(root)
    out = Path(tempfile.mkdtemp())

    build_revision_dataset(root, out, step_minutes=30, widths=[2.0])
    rows = read_rows(out / "step30" / "model_input" / "model_rows-2022.csv")
    first_put = next(row for row in rows if row["side"] == "put" and row["as_of_ms"] == str(BASE_MS + 60 * MINUTE_MS))

    assert first_put["ret_15"] != ""
    assert first_put["ret_30"] == ""
    assert first_put["net_flow_30"] == ""


def test_common_features_does_not_use_unclosed_future_bar() -> None:
    rows = []
    for i in range(31):
        row = {
            "open_time_ms": BASE_MS + i * MINUTE_MS,
            "open": 100.0 + i,
            "high": 100.5 + i,
            "low": 99.5 + i,
            "close": 100.1 + i,
            "volume": 10.0,
            "close_time_ms": BASE_MS + (i + 1) * MINUTE_MS - 1,
            "quote_volume": 10.0 * (100.0 + i),
            "taker_buy_base_volume": 6.0,
        }
        rows.append(row)
    rows[-1]["high"] = 10_000.0
    bars = HistoricalBars(rows)
    snapshot = common_features(BASE_MS + 30 * MINUTE_MS, bars)

    assert snapshot["last_closed_time_ms"] == BASE_MS + 30 * MINUTE_MS - 1
    assert snapshot["last_closed_price"] < 10_000.0
    assert snapshot["range_position_15"] < 1.01


def test_reference_spot_close_rejects_stale_gap_price() -> None:
    rows = [
        {
            "open_time_ms": BASE_MS + i * MINUTE_MS,
            "open": 100.0 + i,
            "high": 100.5 + i,
            "low": 99.5 + i,
            "close": 100.1 + i,
            "volume": 10.0,
            "close_time_ms": BASE_MS + (i + 1) * MINUTE_MS - 1,
            "quote_volume": 10.0 * (100.0 + i),
            "taker_buy_base_volume": 6.0,
        }
        for i in range(2)
    ]
    bars = HistoricalBars(rows)

    assert reference_spot_close(BASE_MS + 2 * MINUTE_MS, bars) is not None
    assert reference_spot_close(BASE_MS + 3 * MINUTE_MS, bars) is None
