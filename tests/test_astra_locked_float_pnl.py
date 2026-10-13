"""Lock follow-up PnL is Deribit's floating PnL: per leg (mark - entry) x signed size,
without fees or a close-cost estimate."""
import pytest

from tools import astra_locked_candidate_compare_v1 as compare
from test_astra_locked_followup import lock, setup


def test_floating_pnl_is_entry_against_leg_marks():
    baseline = {"candidate": {"snapshot": {"contract": {"quantity_btc": 2.0},
                                          "market": {"quote": {"short_sale_btc": 0.010, "long_purchase_btc": 0.004}}}}}
    point = {"packet": {"raw_payload": {"market": {"short_book": {"mark_price": 0.003},
                                                    "long_book": {"mark_price": 0.001}}}}}
    out = compare.floating_pnl(point, baseline)
    # short: sold 0.010, now marked 0.006 -> +0.004; long: bought 0.004, now marked 0.002 -> -0.002
    assert out["float_pnl_btc"] == pytest.approx(0.002) and out["float_gaps"] == []
    # max profit is the entry credit; what is left is the spread's mark value
    assert out["max_profit_btc"] == pytest.approx(0.006)
    assert out["remaining_credit_btc"] == pytest.approx(0.004)
    # a lock-time quote withheld for a quality gap falls back to the lock-time top of book
    gapped = {"candidate": {"snapshot": {"contract": {"quantity_btc": 2.0}, "market": {"quote": {}}}},
              "packet": {"raw_payload": {"market": {"short_book": {"best_bid_price": 0.005},
                                                    "long_book": {"best_ask_price": 0.002}}}}}
    fallback = compare.floating_pnl(point, gapped)
    assert fallback["entry_basis"] == "lock_top_of_book"
    assert fallback["float_pnl_btc"] == pytest.approx(0.002) and fallback["max_profit_btc"] == pytest.approx(0.006)
    point["packet"]["raw_payload"]["market"]["long_book"] = {}
    assert compare.floating_pnl(point, baseline)["float_pnl_btc"] is None
    assert compare.floating_pnl(point, baseline)["float_gaps"] == ["long_mark_missing"]


def test_series_carries_floating_pnl_and_recomputes_outdated_rows(tmp_path):
    wb, _, clock, snapshot = setup(tmp_path)
    episode_id = lock(wb, snapshot)["episode"]["episode_id"]
    clock[0] += 120_000
    wb.refresh(now_ms=clock[0], record_daily=False)
    points = wb.locked_candidate_series(episode_id)["points"]
    assert len(points) == 2 and all("float_pnl_btc" in p for p in points)
    assert all(p["metrics_version"] == compare.SERIES_METRICS_VERSION for p in points)
    # a row written before marks were read is recomputed while its point file exists
    old = {p["point_id"]: {k: v for k, v in p.items() if k not in {"float_pnl_btc", "metrics_version"}} for p in points}
    wb.locked_candidates._series_path(episode_id).unlink()
    wb.locked_candidates.append_series(episode_id, [{"point_id": pid, "step": None, "recorded_at_ms": None,
                                                     "metrics": m} for pid, m in old.items()])
    wb._series_cache = {pid: dict(m) for pid, m in old.items()}
    again = wb.locked_candidate_series(episode_id)["points"]
    assert all(p.get("metrics_version") == compare.SERIES_METRICS_VERSION for p in again)
    report = wb.compact_locked(clock[0])
    assert report["backfilled"] == 2
