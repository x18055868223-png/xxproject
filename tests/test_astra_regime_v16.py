import math
from pathlib import Path
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from astra_regime_v16 import (
    FIVE_MINUTE_MS,
    MINUTE_MS,
    build_from_facts,
    build_regime_4h_v0,
    build_regimes_4h_v0,
    regime_boundaries,
    required_minute_open_times,
    validate_um_minute_schema,
)


BASE_ASOF = (1_800_000_000_000 // FIVE_MINUTE_MS) * FIVE_MINUTE_MS
RAW_UM_SOURCE = r"raw\binance\um\monthly\BTCUSDT-1m-2027-01.zip"
RAW_SPOT_SOURCE = r"raw\binance\spot\monthly\BTCUSDT-1m-2027-01.zip"


def closes_from_returns(returns, start=100.0):
    closes = [start]
    for value in returns:
        closes.append(closes[-1] * math.exp(value))
    return closes


def rows_for_closes(asof_ms, boundary_closes, *, source="um", market=None, source_sha256=None):
    boundaries = regime_boundaries(asof_ms)
    assert len(boundary_closes) == len(boundaries)
    close_by_open = {boundary - MINUTE_MS: close for boundary, close in zip(boundaries, boundary_closes)}
    rows = []
    for open_ms in required_minute_open_times(asof_ms):
        rows.append(
            {
                "open_time_ms": open_ms,
                "close_time_ms": open_ms + MINUTE_MS - 1,
                "close": close_by_open.get(open_ms, 100.0),
                "source": source,
            }
        )
        if market is not None:
            rows[-1]["market"] = market
        if source_sha256 is not None:
            rows[-1]["source_sha256"] = source_sha256
    return rows


def result_for_returns(returns):
    return build_regime_4h_v0(
        rows_for_closes(BASE_ASOF, closes_from_returns(returns)),
        BASE_ASOF,
        source_identity="binance_um_1m",
    )


def test_boundaries_are_fixed_utc_five_minutes_and_require_closed_minutes():
    asof = BASE_ASOF + 123_456
    boundaries = regime_boundaries(asof)
    needed = required_minute_open_times(asof)

    assert len(boundaries) == 49
    assert len(needed) == 241
    assert boundaries[-1] == (asof // FIVE_MINUTE_MS) * FIVE_MINUTE_MS
    assert all(boundary % FIVE_MINUTE_MS == 0 for boundary in boundaries)
    assert needed[0] == boundaries[0] - MINUTE_MS
    assert needed[-1] == boundaries[-1] - MINUTE_MS
    assert needed[-1] + MINUTE_MS - 1 < asof


def test_monotone_up_down_round_trip_and_mixed_paths_classify_by_fixed_rules():
    up = result_for_returns([0.001] * 48)
    down = result_for_returns([-0.001] * 48)
    round_trip = result_for_returns([0.01, -0.01] * 24)
    mixed = result_for_returns([0.01] * 29 + [-0.01] * 19)

    assert up["regime"] == "UP"
    assert up["E4"] == pytest.approx(1.0)
    assert down["regime"] == "DOWN"
    assert down["E4"] == pytest.approx(-1.0)
    assert round_trip["regime"] == "RANGE"
    assert round_trip["R4"] == pytest.approx(0.0)
    assert mixed["regime"] == "MIXED"
    assert 0.15 < mixed["E4"] < 0.30
    assert mixed["Z4"] > 1.0


def test_future_append_does_not_change_existing_state_or_metrics():
    rows = rows_for_closes(BASE_ASOF, closes_from_returns([0.001] * 48))
    future_open = BASE_ASOF + 60 * MINUTE_MS
    future = {
        "open_time_ms": future_open,
        "close_time_ms": future_open + MINUTE_MS - 1,
        "close": 1_000_000_000.0,
        "source": "spot",
    }

    base = build_regime_4h_v0(rows, BASE_ASOF, source_identity="binance_um_1m")
    extended = build_regime_4h_v0(rows + [future], BASE_ASOF, source_identity="binance_um_1m")

    assert extended == base


def test_missing_minute_and_invalid_minute_shape_are_unknown():
    rows = rows_for_closes(BASE_ASOF, closes_from_returns([0.001] * 48))
    missing = rows[:17] + rows[18:]
    bad_shape = [dict(row) for row in rows]
    bad_shape[17]["close_time_ms"] += 1
    duplicate = rows + [dict(rows[17])]

    assert build_regime_4h_v0(missing, BASE_ASOF, source_identity="binance_um_1m")["status_reason"] == "missing_minute"
    assert (
        build_regime_4h_v0(bad_shape, BASE_ASOF, source_identity="binance_um_1m")["status_reason"]
        == "invalid_minute_shape"
    )
    assert build_regime_4h_v0(duplicate, BASE_ASOF, source_identity="binance_um_1m")["status_reason"] == "duplicate_minute"


def test_flat_or_non_positive_prices_are_unknown_not_range():
    flat = build_regime_4h_v0(
        rows_for_closes(BASE_ASOF, [100.0] * 49),
        BASE_ASOF,
        source_identity="binance_um_1m",
    )
    bad = rows_for_closes(BASE_ASOF, closes_from_returns([0.001] * 48))
    bad[0]["close"] = 0.0

    assert flat["regime"] == "UNKNOWN"
    assert flat["status_reason"] == "zero_return_denominator"
    assert build_regime_4h_v0(bad, BASE_ASOF, source_identity="binance_um_1m")["status_reason"] == "non_positive_close"


def test_source_identity_keeps_um_separate_from_spot():
    rows = rows_for_closes(BASE_ASOF, closes_from_returns([0.001] * 48), source="um")
    spot_rows = rows_for_closes(BASE_ASOF, closes_from_returns([0.001] * 48), source="spot")
    raw_um_rows = rows_for_closes(
        BASE_ASOF,
        closes_from_returns([0.001] * 48),
        source=RAW_UM_SOURCE,
        market="um",
    )
    raw_spot_rows = rows_for_closes(
        BASE_ASOF,
        closes_from_returns([0.001] * 48),
        source=RAW_SPOT_SOURCE,
        market="um",
    )

    assert build_regime_4h_v0(rows, BASE_ASOF, source_identity="binance_um_1m")["regime"] == "UP"
    assert build_regime_4h_v0(raw_um_rows, BASE_ASOF, source_identity="binance_um_1m")["regime"] == "UP"
    assert build_regime_4h_v0(rows, BASE_ASOF, source_identity="spot")["status_reason"] == "source_not_um"
    assert build_regime_4h_v0(spot_rows, BASE_ASOF, source_identity="binance_um_1m")["status_reason"] == "source_not_um"
    assert (
        build_regime_4h_v0(raw_spot_rows, BASE_ASOF, source_identity="binance_um_1m")["status_reason"]
        == "source_not_um"
    )


def test_available_at_ms_must_not_arrive_after_asof():
    rows = rows_for_closes(BASE_ASOF, closes_from_returns([0.001] * 48), source=RAW_UM_SOURCE, market="um")
    rows[23]["available_at_ms"] = BASE_ASOF
    ok = build_regime_4h_v0(rows, BASE_ASOF, source_identity="binance_um_1m")
    rows[23]["available_at_ms"] = BASE_ASOF + 1

    assert ok["regime"] == "UP"
    assert build_regime_4h_v0(rows, BASE_ASOF, source_identity="binance_um_1m")["status_reason"] == "available_after_asof"


def test_same_market_time_is_calculated_once_for_multiple_consumers():
    rows = rows_for_closes(BASE_ASOF, closes_from_returns([0.001] * 48))
    got = build_regimes_4h_v0(rows, [BASE_ASOF, BASE_ASOF, BASE_ASOF + FIVE_MINUTE_MS], source_identity="binance_um_1m")

    assert [row["as_of_ms"] for row in got] == [BASE_ASOF, BASE_ASOF + FIVE_MINUTE_MS]


def test_schema_validation_rejects_missing_columns_and_non_um_sources():
    pd = pytest.importorskip("pandas")
    good = pd.DataFrame(
        {
            "open_time_ms": [0],
            "close_time_ms": [MINUTE_MS - 1],
            "close": [100.0],
            "source": [RAW_UM_SOURCE],
            "market": ["um"],
            "available_at_ms": [MINUTE_MS],
            "source_sha256": ["rawhash"],
        }
    )
    missing = pd.DataFrame({"open_time_ms": [0], "close": [100.0]})
    spot = pd.DataFrame(
        {"open_time_ms": [0], "close_time_ms": [MINUTE_MS - 1], "close": [100.0], "source": [RAW_SPOT_SOURCE]}
    )

    assert validate_um_minute_schema(good, source_identity="binance_um_1m") is good
    with pytest.raises(ValueError, match="missing required UM 1m columns"):
        validate_um_minute_schema(missing, source_identity="binance_um_1m")
    with pytest.raises(ValueError, match="non-UM"):
        validate_um_minute_schema(spot, source_identity="binance_um_1m")


def test_build_from_facts_reads_cross_month_window_and_preserves_source_hashes(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    asof = utc_ms(2026, 2, 1, 1, 0)
    rows = rows_for_closes(asof, closes_from_returns([0.001] * 48), source=RAW_UM_SOURCE, market="um")
    facts_um = tmp_path / "facts" / "um"
    facts_um.mkdir(parents=True)

    frames_by_month = {}
    for row in rows:
        month = datetime.fromtimestamp(row["open_time_ms"] / 1000.0, tz=timezone.utc).strftime("%Y-%m")
        item = dict(row)
        item["source"] = rf"raw\binance\um\monthly\BTCUSDT-1m-{month}.zip"
        item["source_sha256"] = f"hash-{month}"
        item["available_at_ms"] = item["close_time_ms"] + 1
        frames_by_month.setdefault(month, []).append(item)
    for month, month_rows in frames_by_month.items():
        pd.DataFrame(month_rows).to_parquet(facts_um / f"{month}.parquet", index=False)
    pd.DataFrame([{**frames_by_month["2026-02"][0], "source": RAW_SPOT_SOURCE}]).to_parquet(
        facts_um / "2026-02-01.parquet",
        index=False,
    )

    got = build_from_facts(tmp_path / "facts", [asof, asof])

    assert len(got) == 1
    assert got.loc[0, "regime"] == "UP"
    assert got.loc[0, "rv4_annualized"] == pytest.approx(got.loc[0, "RV_ANN"])
    assert set(Path(path).name for path in got.attrs["source_hashes"]) == {"2026-01.parquet", "2026-02.parquet"}
    assert got.attrs["raw_source_hashes"] == ["hash-2026-01", "hash-2026-02"]
    assert [Path(path).name for path in got.attrs["files_read"]] == ["2026-01.parquet", "2026-02.parquet"]


def utc_ms(year, month, day, hour=0, minute=0):
    return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp() * 1000)
