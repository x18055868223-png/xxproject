"""ledger_research_fields@0.1: mechanism fields on every ledger row (user-approved 2026-10-20)."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import astra_research_archive_v1 as ar
import astra_research_inbox_v1 as inbox
import astra_research_validation_v1 as validation

H = 3_600_000
EXPIRY = 1_792_569_600_000            # 2026-10-21 08:00 UTC
NOW = EXPIRY - 24 * H


def _view():
    return {"expiry_gamma": {"by_expiry": {"21OCT26": {"expiry_ms": EXPIRY, "atm_iv_pct": 28.0,
                                                       "walls": {"gamma_call": 82000.0, "gamma_put": 81500.0, "pin": 81500.0}}},
                             "realized_vol": {"rv_24h_pct": 22.0, "rv_7d_pct": 31.0}},
            "market_context": {"market_facts": {"space_context": {"gex_info": {
                "dvol": 37.9, "iv_percentile": 26.0,
                "rr_skew": [{"tenor": "1d", "expiry": "21OCT26", "rr": -1.18}, {"tenor": "1w", "expiry": None, "rr": -2.4}]}}}}}


def _row(index=81400.0):
    return {"short_from_index_pct": (82500 / index - 1) * 100, "index_usd": index,
            "short": {"best_bid_price": 0.0029}, "long": {"best_ask_price": 0.0004},
            "credit_bid_ask_btc": 0.0025, "mark_spread_btc": 0.0029}


RECORD = {"side": "call", "short_strike_usd": 82500.0, "long_strike_usd": 84500.0, "expiry_ms": EXPIRY}


def test_fields_from_the_view_books_calendar_and_index_path():
    points = [[NOW - 4 * H, 80000.0], [NOW - H + 60_000, 81000.0], [NOW, 81400.0]]
    f = ar.research_fields(RECORD, _row(), ar.market_context(_view(), points), now_ms=NOW)
    cushion = (82500 / 81400 - 1) * 100
    move = 28.0 * math.sqrt(24 / 8760)
    assert abs(f["short_cushion_pct"] - cushion) < 1e-9 and abs(f["expected_move_pct"] - move) < 1e-9
    assert abs(f["distance_in_moves"] - cushion / move) < 1e-9
    assert f["iv_minus_rv_24h"] == 6.0 and f["rv_7d_pct"] == 31.0 and f["dvol"] == 37.9
    assert f["rr_25d_expiry"] == -1.18
    # fee per leg: min(0.0003, 12.5% x price) -> 0.0003 + 0.00005, as in the case file's L2.fee
    assert abs(f["entry_fee_btc"] - 0.00035) < 1e-12 and abs(f["credit_after_fee_btc"] - 0.00215) < 1e-12
    assert abs(f["comp_ratio_after_fee"] - 0.00215 / 0.0029) < 1e-9 and f["comp_tier"] == "fair"
    assert f["gamma_wall_usd"] == 82000.0 and f["gamma_wall_vs_short_pct"] < 0     # the wall sits before the short leg
    assert abs(f["index_chg_1h_pct"] - (81400 / 81000 - 1) * 100) < 1e-9
    assert abs(f["index_chg_4h_pct"] - (81400 / 80000 - 1) * 100) < 1e-9
    assert f["fields_version"] == "ledger_research_fields@0.3"


def test_put_side_and_missing_inputs_give_none_not_errors():
    put = {**RECORD, "side": "put", "short_strike_usd": 80500.0}
    row = {**_row(), "short_from_index_pct": (80500 / 81400 - 1) * 100}
    f = ar.research_fields(put, row, ar.market_context(_view(), None), now_ms=NOW)
    assert f["short_cushion_pct"] > 0 and f["gamma_wall_usd"] == 81500.0 and f["gamma_wall_vs_short_pct"] < 0   # above a put short: on the spot side
    assert f["index_chg_1h_pct"] is None
    empty = ar.research_fields(RECORD, {"short_from_index_pct": None}, ar.market_context(None, None), now_ms=NOW)
    for key in ("expiry_atm_iv_pct", "distance_in_moves", "rv_24h_pct", "rr_25d_expiry", "entry_fee_btc",
                "comp_ratio_after_fee", "comp_tier", "gamma_wall_usd", "pin_from_index_pct"):
        assert empty[key] is None, key


def test_new_fields_are_conditionable_and_survive_the_validation_slim_row():
    for name in ("distance_in_moves", "iv_minus_rv_24h", "comp_ratio_after_fee", "hours_to_next_event",
                 "index_chg_4h_pct", "comp_tier", "event_before_expiry"):
        assert "ledger." + name in inbox.FIELDS
        assert name in validation.KEEP_FIELDS
    cond = {"all": [{"field": "ledger.distance_in_moves", "op": "<", "value": 0.5},
                    {"field": "ledger.comp_tier", "op": "==", "value": "thin"}]}
    assert inbox.check_condition(cond) == []
    assert inbox.evaluate_condition(cond, {"ledger": {"distance_in_moves": 0.4, "comp_tier": "thin"}}) is True
    assert inbox.evaluate_condition(cond, {"ledger": {"comp_tier": "thin"}}) is None    # older rows: excluded


def test_versions_record_when_the_fields_started():
    assert ar.observed_versions()["ledger_fields"] == "ledger_research_fields@0.3"


def test_tick_writes_the_fields_and_survives_a_malformed_index_path(tmp_path):
    import json
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    from test_astra_research_archive_v1 import EXPIRY as E2, FakeDeribit, _tick, _view as view_of
    for path in ([[1, 2, 3], "x", [None, 5.0]], [[E2 - 20 * H, 81000.0], [E2 - 19 * H, 81800.0]]):
        archive = ar.Archive(tmp_path / str(len(path)))
        report = _tick(archive, E2 - 19 * H, view_of(), FakeDeribit(path=path))
        assert report["rows"] == 1 and not [e for e in report["errors"] if e["step"] == "ledger_context"]
        row = json.loads(next((archive.root / "ledger").glob("*.jsonl")).read_text(encoding="utf-8").splitlines()[0])
        assert row["fields_version"] == "ledger_research_fields@0.3" and "distance_in_moves" in row
    assert row["index_chg_1h_pct"] is not None


def test_a_quiet_week_is_a_value_not_missing_and_zero_credit_has_no_ratio():
    ctx = ar.market_context(_view(), None, now_ms=NOW)
    ctx["upcoming_events"], ctx["calendar_complete_7d"] = [], True
    f = ar.research_fields(RECORD, {**_row(), "credit_bid_ask_btc": 0.0002}, ctx, now_ms=NOW)
    assert f["hours_to_next_event"] == 168.0 and f["next_event_kind"] == "none"
    assert f["comp_ratio_after_fee"] is None and f["comp_tier"] is None      # credit after fee <= 0
    ctx["calendar_complete_7d"] = False
    assert ar.research_fields(RECORD, _row(), ctx, now_ms=NOW)["next_event_kind"] is None
