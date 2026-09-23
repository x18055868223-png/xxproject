from __future__ import annotations

import csv
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import astra_control_study as control
import astra_light_study_analysis as light


BJT = timezone(timedelta(hours=8))
MINUTE_MS = 60_000
HOUR_MS = 3_600_000
RESULTS = WORKSPACE / ".artifacts" / "astra-control-study-20260913" / "results"
FIRST_STUDY = WORKSPACE / ".artifacts" / "astra-light-study-20260913"
CONFIG_PATH = WORKSPACE / ".artifacts" / "astra-control-study-20260913" / "study_config.json"


def bjt_ms(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=BJT).timestamp() * 1000)


def require_artifacts() -> None:
    if not RESULTS.exists():
        pytest.skip("frozen control-study artifacts are not available")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def dec(value: str | int | float | None) -> Decimal:
    assert value not in (None, "")
    return Decimal(str(value))


def assert_dec_close(actual: str | int | float, expected: Decimal, tolerance: str = "1e-12") -> None:
    delta = abs(dec(actual) - expected)
    assert delta <= Decimal(tolerance), f"{actual} != {expected} within {tolerance}"


def metric_row(
    card_id: str,
    *,
    side: str = "put_credit",
    delivery_date: str = "2026-06-20",
    tenor_bin: int = 0,
    pnl: float = 0.01,
    margin: float = 0.1,
    dte_hours: float = 12,
) -> dict:
    return {
        "card_id": card_id,
        "entry_ms": bjt_ms("2026-06-20T01:00:00"),
        "delivery_date": delivery_date,
        "side": side,
        "target_width": 2000.0,
        "credit_fraction": 0.1,
        "tenor_bin": tenor_bin,
        "net_pnl_btc": pnl,
        "margin_btc": margin,
        "dte_hours": dte_hours,
        "net_credit_btc": 0.02,
        "payout_btc": 0.02 - pnl,
    }


def test_dte_delivery_and_tenor_bin_boundaries() -> None:
    assert not control.valid_dte(8)
    assert control.valid_dte(8 + 1 / 60)
    assert control.valid_dte(24)
    assert not control.valid_dte(24 + 1 / 60)

    assert control.tenor_bin(8 + 1 / 60) == 0
    assert control.tenor_bin(10) == 0
    assert control.tenor_bin(10 + 1e-6) == 1
    assert control.tenor_bin(24) == 7
    with pytest.raises(ValueError):
        control.tenor_bin(8)

    assert light._next_delivery_ms(bjt_ms("2026-06-20T23:30:00")) == bjt_ms("2026-06-21T16:00:00")
    assert light._next_delivery_ms(bjt_ms("2026-06-20T16:00:00")) == bjt_ms("2026-06-21T16:00:00")


def test_clock_grid_offsets_and_event_distance() -> None:
    zero_offset = list(control.grid_times(bjt_ms("2026-06-20T07:30:00"), bjt_ms("2026-06-20T16:30:00"), 0))
    fifteen_offset = list(control.grid_times(bjt_ms("2026-06-20T07:30:00"), bjt_ms("2026-06-20T16:30:00"), 15))

    assert [datetime.fromtimestamp(ms / 1000, BJT).strftime("%H:%M") for ms in zero_offset] == [
        "07:30",
        "16:00",
        "16:30",
    ]
    assert [datetime.fromtimestamp(ms / 1000, BJT).strftime("%H:%M") for ms in fifteen_offset] == [
        "07:45",
        "16:15",
    ]

    events = [
        bjt_ms("2026-06-20T01:00:00"),
        bjt_ms("2026-06-20T03:00:00"),
        bjt_ms("2026-06-20T06:00:00"),
    ]
    assert control.near_distance_ms(bjt_ms("2026-06-20T02:20:00"), events) == 40 * MINUTE_MS
    assert control.near_distance_ms(bjt_ms("2026-06-20T05:45:00"), events) == 15 * MINUTE_MS


def test_pair_matching_keeps_same_key_and_preserves_unmatched_ids() -> None:
    signal = metric_row("signal", side="put_credit", delivery_date="2026-06-20", tenor_bin=2)
    unmatched = metric_row("missing", side="put_credit", delivery_date="2026-06-21", tenor_bin=2)
    matching_a = metric_row("control-a", side="put_credit", delivery_date="2026-06-20", tenor_bin=2)
    matching_b = metric_row("control-b", side="put_credit", delivery_date="2026-06-20", tenor_bin=2)
    wrong_side = metric_row("wrong-side", side="call_credit", delivery_date="2026-06-20", tenor_bin=2)
    wrong_tenor = metric_row("wrong-tenor", side="put_credit", delivery_date="2026-06-20", tenor_bin=3)

    pairs, missing = control.pair_group(
        [signal, unmatched],
        control.pool_index([matching_a, matching_b, wrong_side, wrong_tenor]),
    )

    assert missing == ["missing"]
    assert len(pairs) == 1
    assert pairs[0][0]["card_id"] == "signal"
    assert {row["card_id"] for row, _ in pairs[0][1]} == {"control-a", "control-b"}
    assert sum(weight for _, weight in pairs[0][1]) == 1
    assert {weight for _, weight in pairs[0][1]} == {0.5}


def test_bootstrap_is_seed_deterministic() -> None:
    pairs = [
        (
            metric_row("signal-a", delivery_date="2026-06-20", pnl=0.03, margin=0.2),
            [(metric_row("control-a", delivery_date="2026-06-20", pnl=0.01, margin=0.2), 1)],
        ),
        (
            metric_row("signal-b", delivery_date="2026-06-21", pnl=-0.01, margin=0.2),
            [(metric_row("control-b", delivery_date="2026-06-21", pnl=0.02, margin=0.2), 1)],
        ),
        (
            metric_row("signal-c", delivery_date="2026-06-22", pnl=0.02, margin=0.2),
            [(metric_row("control-c", delivery_date="2026-06-22", pnl=-0.02, margin=0.2), 1)],
        ),
    ]

    first = control.bootstrap(pairs, draws=50, seed=20260913)
    second = control.bootstrap(pairs, draws=50, seed=20260913)

    assert first == second
    assert first["clusters"] == 3
    assert first["win_delta_pp_interval"] is not None
    assert first["roi_delta_pp_interval"] is not None


def test_position_size_scaling_preserves_roi_and_apr() -> None:
    base = [
        metric_row("win", pnl=0.03, margin=0.2, dte_hours=12),
        metric_row("loss", pnl=-0.01, margin=0.1, dte_hours=24),
    ]
    scaled = []
    for row in base:
        item = dict(row)
        for key in ("net_pnl_btc", "margin_btc", "net_credit_btc", "payout_btc"):
            item[key] *= 7
        scaled.append(item)

    base_metrics = control.metrics([(row, 1) for row in base])
    scaled_metrics = control.metrics([(row, 1) for row in scaled])

    assert scaled_metrics["win_rate"] == base_metrics["win_rate"]
    assert scaled_metrics["intrusion_rate"] == base_metrics["intrusion_rate"]
    assert scaled_metrics["capital_roi"] == pytest.approx(base_metrics["capital_roi"])
    assert scaled_metrics["apr"] == pytest.approx(base_metrics["apr"])


def load_instrument_index() -> dict[str, dict]:
    require_artifacts()
    rows = json.loads((FIRST_STUDY / "market" / "instruments.json").read_text(encoding="utf-8-sig"))
    live_path = FIRST_STUDY / "market" / "cache" / "deribit" / "7af259df805b77d5_get_instruments.json"
    payload = json.loads(live_path.read_text(encoding="utf-8-sig"))
    rows.extend(payload.get("result", []))
    index: dict[str, dict] = {}
    for raw in rows:
        if isinstance(raw, dict) and raw.get("instrument_name"):
            index.setdefault(str(raw["instrument_name"]), raw)
    return index


def test_generated_artifact_acceptance_and_control_entry_grid() -> None:
    require_artifacts()
    acceptance = json.loads((RESULTS / "acceptance.json").read_text(encoding="utf-8"))
    assert acceptance["signal_cards"] == 114
    assert acceptance["signal_main_rows"] == 228
    assert acceptance["control_grid_entries"] == {"0": 2709, "15": 2709}
    assert acceptance["control_trade_rows"] == 97_524
    assert acceptance["missing_price_or_contract_records"] == 0
    assert acceptance["all_control_dte_valid"] is True
    assert acceptance["all_control_in_range"] is True
    assert acceptance["main_matched_by_side"] == {"put_credit": 114.0, "call_credit": 114.0}

    entries = read_csv(RESULTS / "control_entries.csv")
    by_offset = defaultdict(list)
    for row in entries:
        if row["status"] == "complete":
            by_offset[int(row["grid_offset_minutes"])].append(row)
            entry_ms = int(row["entry_ms"])
            assert 8 < float(row["dte_hours"]) <= 24
            assert ((entry_ms // MINUTE_MS) - int(row["grid_offset_minutes"])) % 30 == 0
            assert row["near_event_30m"].lower() == str(float(row["nearest_event_minutes"]) <= 30).lower()
            assert int(row["expiry_ms"]) == light._next_delivery_ms(entry_ms)
    assert {offset: len(rows) for offset, rows in by_offset.items()} == {0: 2709, 15: 2709}


def test_all_generated_scenarios_use_existing_eligible_and_directional_legs() -> None:
    require_artifacts()
    instruments = load_instrument_index()
    rows_checked = 0
    with (RESULTS / "control_all_scenarios.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            rows_checked += 1
            entry_ms = int(row["entry_ms"])
            expiry_ms = int(row["expiry_ms"])
            side = row["side"]
            short = instruments[row["short_instrument"]]
            long = instruments[row["long_instrument"]]
            option_type = "put" if side == "put_credit" else "call"
            short_strike = dec(row["short_strike"])
            long_strike = dec(row["long_strike"])
            entry_price = dec(row["entry_price"])

            for leg in (short, long):
                assert str(leg["option_type"]).lower() == option_type
                assert str(leg["settlement_currency"]).upper() == "BTC"
                assert Decimal(str(leg["contract_size"])) == Decimal("1.0")
                assert int(leg["expiration_timestamp"]) == expiry_ms
                assert int(leg["creation_timestamp"]) <= entry_ms

            if side == "put_credit":
                assert long_strike < short_strike < entry_price
                expected_width = short_strike - long_strike
            else:
                assert entry_price < short_strike < long_strike
                expected_width = long_strike - short_strike
            assert_dec_close(row["actual_width"], expected_width)
    assert rows_checked == 97_524


def test_main_clock_payoff_is_independently_recomputed_with_decimal() -> None:
    require_artifacts()
    checked = 0
    with (RESULTS / "control_main.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["grid_offset_minutes"] != "0":
                continue
            checked += 1
            side = row["side"]
            settlement = dec(row["delivery_price"])
            entry_price = dec(row["entry_price"])
            short_strike = dec(row["short_strike"])
            long_strike = dec(row["long_strike"])
            actual_width = abs(long_strike - short_strike)
            if side == "call_credit":
                intrinsic = max(settlement - short_strike, Decimal("0")) - max(
                    settlement - long_strike,
                    Decimal("0"),
                )
            else:
                intrinsic = max(short_strike - settlement, Decimal("0")) - max(
                    long_strike - settlement,
                    Decimal("0"),
                )
            credit = dec(row["credit_fraction"]) * actual_width / entry_price
            payout = intrinsic / settlement
            pnl = credit - payout
            status = "win" if pnl > 0 else "loss" if pnl < 0 else "tie"

            assert row["target_width"] == "2000.0"
            assert row["credit_fraction"] == "0.1"
            assert_dec_close(row["spread_intrinsic_usd"], intrinsic, "1e-10")
            assert_dec_close(row["net_credit_btc"], credit)
            assert_dec_close(row["payout_btc"], payout)
            assert_dec_close(row["net_pnl_btc"], pnl)
            assert row["result"] == status
            assert row["breakeven_intruded"].lower() == str(pnl < 0).lower()
    assert checked == 5_418


def test_main_matching_links_equal_the_declared_same_key_control_pools() -> None:
    require_artifacts()
    signal_rows = read_csv(RESULTS / "signal_main.csv")
    control_rows = [
        row for row in read_csv(RESULTS / "control_main.csv")
        if row["grid_offset_minutes"] == "0"
    ]
    links = [
        row for row in read_csv(RESULTS / "matched_links.csv")
        if row["variant"] == "主时钟"
    ]

    pool = defaultdict(dict)
    control_by_id = {}
    for row in control_rows:
        key = (row["delivery_date"], row["tenor_bin"], row["side"], row["target_width"], row["credit_fraction"])
        pool[key][row["card_id"]] = row
        control_by_id[(row["card_id"], row["side"])] = row

    signal_by_key = {(row["card_id"], row["side"]): row for row in signal_rows}
    actual = defaultdict(dict)
    for link in links:
        signal = signal_by_key[(link["signal_card_id"], link["side"])]
        linked = control_by_id[(link["control_id"], link["side"])]
        assert link["delivery_date"] == signal["delivery_date"] == linked["delivery_date"]
        assert link["tenor_bin"] == signal["tenor_bin"] == linked["tenor_bin"]
        assert signal["target_width"] == linked["target_width"] == "2000.0"
        assert signal["credit_fraction"] == linked["credit_fraction"] == "0.1"
        actual[(link["signal_card_id"], link["side"])][link["control_id"]] = dec(link["weight"])

    assert len(signal_by_key) == 228
    assert set(actual) == set(signal_by_key)
    for signal_key, signal in signal_by_key.items():
        key = (signal["delivery_date"], signal["tenor_bin"], signal["side"], signal["target_width"], signal["credit_fraction"])
        expected_ids = set(pool[key])
        assert set(actual[signal_key]) == expected_ids
        expected_weight = Decimal("1") / Decimal(len(expected_ids))
        assert abs(sum(actual[signal_key].values(), Decimal("0")) - Decimal("1")) <= Decimal("1e-12")
        assert all(abs(weight - expected_weight) <= Decimal("1e-16") for weight in actual[signal_key].values())

    summary = [
        row for row in read_csv(RESULTS / "matched_summary.csv")
        if row["variant"] == "主时钟" and row["group"] == "全部信号"
    ]
    assert {(row["side"], int(float(row["missing_controls"]))) for row in summary} == {
        ("put_credit", 0),
        ("call_credit", 0),
    }
