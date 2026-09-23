from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import astra_edge_prior as prior


DAY_MS = 24 * 60 * 60 * 1000
MINUTE_MS = 60_000
BASE_EXPIRY_MS = 1_800_000_000_000


def history_row(day: int, *, dte_hours: float = 12.0, ratio: float = 1.0, suffix: str = "") -> dict[str, object]:
    expiry_ms = BASE_EXPIRY_MS + day * DAY_MS
    entry_ms = expiry_ms - int(dte_hours * 60 * 60 * 1000)
    return {
        "clock_id": f"CLOCK-{day}{suffix}",
        "entry_ms": entry_ms,
        "expiry_ms": expiry_ms,
        "delivery_date": f"2026-07-{day:02d}",
        "dte_hours": dte_hours,
        "entry_price": 100_000.0,
        "delivery_price": 100_000.0 * ratio,
        "settlement_entry_ratio": ratio,
        "entry_beijing": None,
        "expiry_beijing": None,
    }


def current_trade(side: str = "put_credit") -> dict[str, object]:
    return {
        "card_id": "CARD-1",
        "episode_id": "EP-1",
        "version": "test",
        "direction": "BULLISH",
        "side": side,
        "relation": "directional",
        "entry_ms": BASE_EXPIRY_MS + 40 * DAY_MS,
        "asof_ms": BASE_EXPIRY_MS + 40 * DAY_MS - MINUTE_MS,
        "entry_price": 100_000.0,
        "expiry_ms": BASE_EXPIRY_MS + 40 * DAY_MS + 12 * 60 * 60 * 1000,
        "delivery_date": "2026-08-09",
        "dte_hours": 12.0,
        "short_instrument": "short",
        "long_instrument": "long",
        "short_strike": 99_000.0 if side == "put_credit" else 102_000.0,
        "long_strike": 97_000.0 if side == "put_credit" else 104_000.0,
        "target_width": 2_000.0,
        "actual_width": 2_000.0,
        "credit_fraction": 0.1,
        "net_credit_btc": 0.002,
        "entry_beijing": None,
        "expiry_beijing": None,
    }


def test_select_training_rows_excludes_future_outcomes_uses_latest_28_days_and_tie_earlier_entry() -> None:
    history = [history_row(day) for day in range(1, 29)]
    history.append(history_row(29, dte_hours=11.0, ratio=0.5, suffix="-later"))
    history.append(history_row(29, dte_hours=13.0, ratio=1.5, suffix="-earlier"))
    history.append(history_row(41, ratio=999.0, suffix="-future"))

    selected, available_days = prior.select_training_rows(
        history,
        current_asof_ms=BASE_EXPIRY_MS + 40 * DAY_MS,
        target_dte_hours=12.0,
        required_days=28,
    )

    assert available_days == 29
    assert len(selected) == 28
    assert selected[0]["delivery_date"] == "2026-07-02"
    assert selected[-1]["delivery_date"] == "2026-07-29"
    assert all(row["delivery_date"] != "2026-07-41" for row in selected)
    day_29 = [row for row in selected if row["delivery_date"] == "2026-07-29"]
    assert len(day_29) == 1
    assert day_29[0]["clock_id"] == "CLOCK-29-earlier"


def test_forecast_requires_28_completed_delivery_days_and_keeps_gap_status() -> None:
    prediction, paths = prior.forecast_one(
        current_trade(),
        [history_row(day) for day in range(1, 28)],
        required_days=28,
    )

    assert paths == []
    assert prediction["forecast_status"] == "insufficient_training_days"
    assert prediction["available_completed_delivery_days"] == 27
    assert prediction["training_days"] == 27
    assert prediction["empirical_win_probability"] is None


def test_current_settlement_and_pnl_do_not_change_forecast_projection() -> None:
    history = [history_row(day, ratio=0.987) for day in range(1, 29)]
    clean_current = current_trade()
    polluted_current = copy.deepcopy(clean_current)
    polluted_current["delivery_price"] = 1
    polluted_current["net_pnl_btc"] = 999
    polluted_current["result"] = "win"

    clean_prediction, clean_paths = prior.forecast_one(clean_current, history, required_days=28)
    polluted_prediction, polluted_paths = prior.forecast_one(polluted_current, history, required_days=28)

    ignored_prediction_keys = {"projection_hash"}
    assert {k: v for k, v in clean_prediction.items() if k not in ignored_prediction_keys} == {
        k: v for k, v in polluted_prediction.items() if k not in ignored_prediction_keys
    }
    assert clean_paths == polluted_paths


def test_candidate_loader_keeps_actual_outcomes_out_of_forecast_projection(tmp_path: Path) -> None:
    report_path = tmp_path / "ordinary_joined.csv"
    samples_path = tmp_path / "standard_signal_samples.jsonl"
    rows = []
    for side in ("put_credit", "call_credit"):
        row = current_trade(side)
        row.update(
            {
                "cohort": "ordinary",
                "delivery_price": 123_456.0,
                "net_pnl_btc": 999.0,
                "result": "win",
                "holding_return_on_margin_1x": 888.0,
            }
        )
        rows.append(row)
    prior.write_csv(report_path, rows)
    samples_path.write_text(
        json.dumps({"card_id": "CARD-1", "event_time_ms": BASE_EXPIRY_MS + 40 * DAY_MS - MINUTE_MS}) + "\n",
        encoding="utf-8",
    )

    projections = prior.load_current_candidates(report_path, samples_path, expected_cards=1, expected_rows=2)
    actual = prior.load_actual_outcomes(report_path, expected_cards=1, expected_rows=2)

    assert len(projections) == 2
    assert all("delivery_price" not in row for row in projections)
    assert all("net_pnl_btc" not in row for row in projections)
    assert all("result" not in row for row in projections)
    assert all("holding_return_on_margin_1x" not in row for row in projections)
    assert actual[("CARD-1", "put_credit")] == {
        "result": "win",
        "net_pnl_btc": "999.0",
        "holding_return_on_margin_1x": "888.0",
    }


def test_forecast_projects_historical_price_ratio_to_current_inverse_put_payoff() -> None:
    trade = current_trade("put_credit")
    history = [history_row(day, ratio=0.987) for day in range(1, 29)]

    prediction, paths = prior.forecast_one(trade, history, required_days=28)

    expected_settlement = 98_700.0
    expected_payout = (99_000.0 - expected_settlement) / expected_settlement
    expected_pnl = 0.002 - expected_payout
    expected_margin = prior.DEFAULT_MARGIN_LAMBDA * 2_000.0 / 100_000.0
    assert prediction["forecast_status"] == "available"
    assert prediction["empirical_win_probability"] == 0.0
    assert prediction["empirical_mean_net_pnl_btc"] == pytest.approx(expected_pnl)
    assert prediction["empirical_mean_margin_return"] == pytest.approx(expected_pnl / expected_margin)
    assert paths[0]["projected_settlement"] == pytest.approx(expected_settlement)
    assert paths[0]["payout_btc"] == pytest.approx(expected_payout)
    assert paths[0]["result"] == "loss"


def test_side_choice_ties_return_no_preference() -> None:
    put = {
        "forecast_status": "available",
        "empirical_win_probability": 0.75,
        "empirical_mean_margin_return": 0.03,
    }
    call = {
        "forecast_status": "available",
        "empirical_win_probability": 0.75,
        "empirical_mean_margin_return": 0.02,
    }

    assert prior.choose_side(put, call, "empirical_win_probability") == ("no_preference", "tie", 0.0)
    assert prior.choose_side(put, call, "empirical_mean_margin_return") == (
        "put_credit",
        "higher_put",
        pytest.approx(0.01),
    )
