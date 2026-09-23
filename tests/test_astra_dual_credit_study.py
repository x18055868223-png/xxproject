from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import astra_dual_credit_study as dual


ENTRY_MS = 1_787_000_000_000
EXPIRY_MS = ENTRY_MS + 12 * 60 * 60 * 1000
ENTRY_PRICE = 100_000.0
PUT_SHORT = 99_000.0
PUT_LONG = 97_000.0
CALL_SHORT = 102_000.0
CALL_LONG = 104_000.0
CREDIT_FRACTION = 0.1
TARGET_WIDTH = 2_000.0
MARGIN_LAMBDA = 0.9516590272


def _side_payout(side: str, settlement: float) -> float:
    if side == "put_credit":
        intrinsic = max(PUT_SHORT - settlement, 0.0) - max(PUT_LONG - settlement, 0.0)
    else:
        intrinsic = max(settlement - CALL_SHORT, 0.0) - max(settlement - CALL_LONG, 0.0)
    return intrinsic / settlement


def _row(
    side: str,
    *,
    card_id: str = "CARD-1",
    entry_ms: int = ENTRY_MS,
    expiry_ms: int = EXPIRY_MS,
    entry_price: float = ENTRY_PRICE,
    settlement: float | None = 98_700.0,
    is_matured: bool = True,
    target_width: float = TARGET_WIDTH,
    credit_fraction: float = CREDIT_FRACTION,
    put_short: float = PUT_SHORT,
    put_long: float = PUT_LONG,
    call_short: float = CALL_SHORT,
    call_long: float = CALL_LONG,
) -> dict:
    short_strike = put_short if side == "put_credit" else call_short
    long_strike = put_long if side == "put_credit" else call_long
    actual_width = abs(short_strike - long_strike)
    credit = credit_fraction * actual_width / entry_price
    if is_matured:
        assert settlement is not None
        if side == "put_credit":
            intrinsic = max(short_strike - settlement, 0.0) - max(long_strike - settlement, 0.0)
        else:
            intrinsic = max(settlement - short_strike, 0.0) - max(settlement - long_strike, 0.0)
        payout = intrinsic / settlement
        pnl = credit - payout
        result = "win" if pnl > 0 else "loss" if pnl < 0 else "breakeven"
    else:
        payout = None
        pnl = None
        result = "not_matured"
    return {
        "card_id": card_id,
        "entry_ms": entry_ms,
        "expiry_ms": expiry_ms,
        "entry_price": entry_price,
        "delivery_price": settlement,
        "dte_hours": 12.0,
        "target_width": target_width,
        "credit_fraction": credit_fraction,
        "side": side,
        "short_instrument": f"{card_id}-{side}-short",
        "long_instrument": f"{card_id}-{side}-long",
        "short_strike": short_strike,
        "long_strike": long_strike,
        "actual_width": actual_width,
        "net_credit_btc": credit,
        "payout_btc": payout,
        "net_pnl_btc": pnl,
        "result": result,
        "is_matured": is_matured,
        "entry_beijing": "2026-06-20 12:00:00",
        "expiry_beijing": "2026-06-21 00:00:00",
        "delivery_date": "2026-06-21",
        "entry_date": "2026-06-20",
    }


def _pair(card_id: str = "CARD-1", settlement: float | None = 98_700.0, matured: bool = True) -> tuple[dict, dict]:
    return (
        _row("put_credit", card_id=card_id, settlement=settlement, is_matured=matured),
        _row("call_credit", card_id=card_id, settlement=settlement, is_matured=matured),
    )


def test_combine_pair_sums_two_protected_spreads_and_uses_settlement_for_inverse_payoff() -> None:
    put, call = _pair(settlement=98_700.0)

    combined = dual.combine_pair(put, call, MARGIN_LAMBDA)

    expected_side_credit = CREDIT_FRACTION * TARGET_WIDTH / ENTRY_PRICE
    expected_put_payout = (PUT_SHORT - 98_700.0) / 98_700.0
    assert combined["side"] == "dual_credit"
    assert combined["quantity_per_side_btc"] == 1
    assert combined["put_net_credit_btc"] == pytest.approx(expected_side_credit)
    assert combined["call_net_credit_btc"] == pytest.approx(expected_side_credit)
    assert combined["put_payout_btc"] == pytest.approx(expected_put_payout)
    assert combined["call_payout_btc"] == pytest.approx(0.0)
    assert combined["payout_btc"] == pytest.approx(expected_put_payout)
    assert combined["net_pnl_btc"] == pytest.approx(2 * expected_side_credit - expected_put_payout)
    assert combined["net_pnl_btc"] > 0
    assert combined["result"] == "win"
    assert combined["one_side_loss_rescued"] is True
    assert combined["both_sides_win"] is False
    assert combined["both_short_legs_otm_at_expiry"] is False

    entry_price_payout = (PUT_SHORT - 98_700.0) / ENTRY_PRICE
    assert not math.isclose(combined["put_payout_btc"], entry_price_payout)


def test_dual_win_rate_is_not_the_average_single_side_win_rate() -> None:
    rescued_put, rescued_call = _pair(card_id="RESCUED", settlement=98_700.0)
    otm_put, otm_call = _pair(card_id="OTM", settlement=101_000.0)

    combined = [
        dual.combine_pair(rescued_put, rescued_call, MARGIN_LAMBDA),
        dual.combine_pair(otm_put, otm_call, MARGIN_LAMBDA),
    ]

    single_side_wins = [
        rescued_put["result"] == "win",
        rescued_call["result"] == "win",
        otm_put["result"] == "win",
        otm_call["result"] == "win",
    ]
    dual_wins = [row["result"] == "win" for row in combined]
    assert sum(single_side_wins) / len(single_side_wins) == 0.75
    assert sum(dual_wins) / len(dual_wins) == 1.0


def test_call_tail_payoff_is_settled_in_btc_with_delivery_price() -> None:
    put, call = _pair(settlement=102_300.0)

    combined = dual.combine_pair(put, call, MARGIN_LAMBDA)

    expected_call_payout = (102_300.0 - CALL_SHORT) / 102_300.0
    assert combined["call_payout_btc"] == pytest.approx(expected_call_payout)
    assert not math.isclose(combined["call_payout_btc"], (102_300.0 - CALL_SHORT) / ENTRY_PRICE)
    assert combined["put_payout_btc"] == pytest.approx(0.0)


def test_margin_is_the_sum_of_two_single_side_margin_estimates() -> None:
    put, call = _pair(settlement=101_000.0)

    combined = dual.combine_pair(put, call, MARGIN_LAMBDA)

    expected_side_margin = MARGIN_LAMBDA * TARGET_WIDTH / ENTRY_PRICE
    assert combined["put_margin_btc"] == pytest.approx(expected_side_margin)
    assert combined["call_margin_btc"] == pytest.approx(expected_side_margin)
    assert combined["margin_btc"] == pytest.approx(2 * expected_side_margin)
    assert combined["holding_return_on_margin"] == pytest.approx(combined["net_pnl_btc"] / combined["margin_btc"])


def test_pair_rows_keeps_only_valid_put_call_pairs_and_reports_missing_duplicate_and_mismatch() -> None:
    valid_put, valid_call = _pair(card_id="VALID", settlement=101_000.0)
    missing_put = _row("put_credit", card_id="MISSING", settlement=101_000.0)
    duplicate_put = _row("put_credit", card_id="DUPLICATE", settlement=101_000.0)
    duplicate_call = _row("call_credit", card_id="DUPLICATE", settlement=101_000.0)
    duplicate_extra_call = _row("call_credit", card_id="DUPLICATE", settlement=101_000.0)
    mismatch_put = _row("put_credit", card_id="MISMATCH", settlement=101_000.0)
    mismatch_call = _row("call_credit", card_id="MISMATCH", settlement=101_000.0, entry_price=100_100.0)

    combined, issues = dual.pair_rows(
        [
            valid_put,
            valid_call,
            missing_put,
            duplicate_put,
            duplicate_call,
            duplicate_extra_call,
            mismatch_put,
            mismatch_call,
        ],
        MARGIN_LAMBDA,
    )

    assert [row["card_id"] for row in combined] == ["VALID"]
    issue_by_id = {issue["card_id"]: issue for issue in issues}
    assert set(issue_by_id) == {"MISSING", "DUPLICATE", "MISMATCH"}
    assert issue_by_id["MISSING"]["reason"] == "missing or duplicate side"
    assert issue_by_id["DUPLICATE"]["reason"] == "missing or duplicate side"
    assert issue_by_id["MISMATCH"]["reason"] == "identity mismatch: entry_price"


def test_unmatured_pair_is_retained_without_payout_or_pnl() -> None:
    put, call = _pair(card_id="PENDING", settlement=None, matured=False)

    combined = dual.combine_pair(put, call, MARGIN_LAMBDA)

    assert combined["is_matured"] is False
    assert combined["result"] == "not_matured"
    assert combined["payout_btc"] is None
    assert combined["net_pnl_btc"] is None
    assert combined["holding_return_on_margin"] is None
    assert combined["combined_breakeven_intruded"] is None
    assert combined["net_credit_btc"] == pytest.approx(2 * CREDIT_FRACTION * TARGET_WIDTH / ENTRY_PRICE)


def test_combine_pair_rejects_non_otm_or_unprotected_leg_order() -> None:
    invalid_put = _row("put_credit", put_short=101_000.0, put_long=99_000.0, settlement=101_000.0)
    valid_call = _row("call_credit", settlement=101_000.0)

    with pytest.raises(ValueError, match="legs not ordered and strictly OTM"):
        dual.combine_pair(invalid_put, valid_call, MARGIN_LAMBDA)


def test_combine_pair_rejects_wrong_precomputed_credit_or_payoff() -> None:
    put, call = _pair(settlement=98_700.0)
    put["payout_btc"] = (PUT_SHORT - 98_700.0) / ENTRY_PRICE

    with pytest.raises(ValueError, match="inverse payoff mismatch"):
        dual.combine_pair(put, call, MARGIN_LAMBDA)

    put, call = _pair(settlement=98_700.0)
    call["net_credit_btc"] += 0.0001
    with pytest.raises(ValueError, match="credit mismatch"):
        dual.combine_pair(put, call, MARGIN_LAMBDA)
