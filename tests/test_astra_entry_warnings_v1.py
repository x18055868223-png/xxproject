import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import astra_entry_warnings_v1 as ew
import astra_session_clock_v1 as clock

H = 3_600_000
NOW = 1_792_490_400_000            # Tue 2026-10-20 10:00 UTC
EXPIRY = 1_792_569_600_000         # Wed 2026-10-21 08:00 UTC
TERMS = [{"expiry": "20OCT26", "days_to_expiry": 0.03, "atm_iv": 40.0}, {"expiry": "21OCT26", "days_to_expiry": 0.9, "atm_iv": 31.9},
         {"expiry": "24OCT26", "days_to_expiry": 3.9, "atm_iv": 31.9}, {"expiry": "30OCT26", "days_to_expiry": 9.9, "atm_iv": 33.3}]


def test_inversion_is_judged_per_unit_of_the_intraday_clock():
    # a weekday daily at the same raw IV as the week is NOT inverted once the busy weekday is divided out
    calm = ew.term_inversion([dict(t, atm_iv=33.3) if t["expiry"] == "21OCT26" else t for t in TERMS], "21OCT26", None, NOW, EXPIRY)
    assert calm["triggered"] is False and calm["evidence"]["contract_atm_iv_clock_adjusted"] < 33.3
    hot_terms = [dict(t, atm_iv=40.0) if t["expiry"] == "21OCT26" else t for t in TERMS]
    hot = ew.term_inversion(hot_terms, "21OCT26", 31.0, NOW, EXPIRY)           # like with like: the term structure wins
    assert hot["triggered"] is True and hot["evidence"]["week_expiry"] == "30OCT26"
    assert hot["evidence"]["contract_atm_iv_source"] == "gexmonitor.term_structure"
    no_own = [t for t in TERMS if t["expiry"] != "21OCT26"]
    book = ew.term_inversion(no_own, "21OCT26", 40.0, NOW, EXPIRY)               # the book IV only when it is missing
    assert book["triggered"] is True and book["evidence"]["contract_atm_iv_source"] == "deribit.expiry_gamma"
    assert ew.term_inversion([], "21OCT26", None, NOW, EXPIRY)["triggered"] is None
    assert ew.term_inversion(TERMS, "21oct26", None, NOW, EXPIRY)["evidence"]["contract_atm_iv"] == 31.9   # case-insensitive


KP, KC = "P1|P2", "C1|C2"


def _hist(side, atm, short, expiry=EXPIRY, n=8, step=30 * 60_000, key=None):
    return [{"side": side, "displayed": True, "expiry_ms": expiry, "at_ms": NOW - (i + 1) * step,
             "contract_key": key or (KP if side == "put" else KC),
             "expiry_atm_iv_pct": atm, "short_mark_iv": short} for i in range(n)]


def test_skew_thickening_one_sided_or_both_and_missing_other_side():
    hist = _hist("put", 30.0, 32.0) + _hist("call", 30.0, 29.0)
    pt = lambda short, oshort, **k: ew.premium_thickening("put", EXPIRY, NOW, 30.0, short, oshort, hist, KP, KC, **k)
    assert pt(32.5, 29.2)["triggered"] is False
    one = pt(36.0, 29.3)
    assert one["triggered"] is True and one["evidence"]["one_sided"] is True and "单侧" in one["label_cn"]
    down = pt(36.0, 24.0)                                                       # the other side fell: not "flat"
    assert down["evidence"]["one_sided"] is False and "两侧" in down["reason_cn"]
    alone = ew.premium_thickening("put", EXPIRY, NOW, 30.0, 36.0, None, hist, KP, None)
    assert alone["triggered"] is True and "另一侧无数据" in alone["reason_cn"]


def test_skew_is_compared_within_one_contract_only():
    # the same side earlier had another strike, further out on the smile: its skew is not history
    hist = _hist("put", 30.0, 38.0, key="OLD|X") + _hist("call", 30.0, 29.0)
    out = ew.premium_thickening("put", EXPIRY, NOW, 30.0, 33.0, 29.0, hist, KP, KC)
    assert out["evidence"]["skew_jump_vol_pts"] is None and out["triggered"] is False
    assert "偏斜记录不足" in out["reason_cn"]


def test_history_must_be_the_same_expiry_and_span_two_hours():
    other_expiry = _hist("put", 30.0, 32.0, expiry=EXPIRY - 24 * H)
    assert ew.premium_thickening("put", EXPIRY, NOW, 30.0, 40.0, 29.0, other_expiry)["triggered"] is None
    short_span = _hist("put", 30.0, 32.0, n=8, step=10 * 60_000)              # 80 minutes only
    assert ew.premium_thickening("put", EXPIRY, NOW, 30.0, 40.0, 29.0, short_span)["triggered"] is None


def test_whole_expiry_repricing_is_clock_adjusted():
    hist = _hist("call", 30.0, 29.0)
    up = ew.premium_thickening("call", EXPIRY, NOW, 36.0, 35.0, None, hist)
    assert up["triggered"] is True and "整条到期" in up["reason_cn"]


def test_the_busy_window_is_a_note_active_in_busy_hours_and_windows_in_progress_or_ahead():
    busy_now = ew.busy_window_note({"hour_level": "busy", "windows": []}, 0.39)
    assert busy_now["active"] is True and busy_now["role"] == "note" and busy_now["code"] == "busy_window"
    assert busy_now["reason_cn"].startswith("当前处于高波动时段。") and "不是否决" in busy_now["reason_cn"]
    assert busy_now["evidence"]["distance_in_moves"] == 0.39 and busy_now["validated"] is False
    in_progress = ew.busy_window_note({"hour_level": "normal", "windows": [{"label_cn": "美股开盘", "in_hours": -0.2}]})
    assert in_progress["active"] is True and in_progress["reason_cn"].startswith("美股开盘正在进行。")
    assert in_progress["evidence"]["in_progress"] is True and in_progress["evidence"]["ahead"] is False
    ahead = ew.busy_window_note({"hour_level": "normal", "windows": [{"label_cn": "美股开盘", "in_hours": 1.5}]}, 0.8)
    assert ahead["active"] is True and ahead["reason_cn"].startswith("美股开盘约 1.5 小时后。")
    assert ahead["evidence"]["ahead"] is True and ahead["evidence"]["in_progress"] is False
    assert "35%" in ahead["reason_cn"] and "26%" in ahead["reason_cn"] and "22 号" in ahead["basis_cn"]
    quiet = ew.busy_window_note({"hour_level": "quiet", "windows": [{"label_cn": "美股开盘", "in_hours": 5}]}, 0.1)
    assert quiet["active"] is False and quiet["reason_cn"] == "2 小时内没有高波动窗口，当前也不在高波动时段。"
    for missing in (None, {"error_type": "RuntimeError"}):
        out = ew.busy_window_note(missing, 0.1)
        assert out["active"] is None and out["reason_cn"] == "缺少时钟读数，无法判断。"


def test_busy_window_counts_windows_in_progress_and_busy_hours():
    # the @0.1 veto, kept for the backtest of docs/opus/22 (research_22); evaluate no longer calls it
    in_progress = {"hour_level": "busy", "windows": [{"label_cn": "美股开盘", "in_hours": -0.2}]}
    assert ew.busy_window_thin_cushion(in_progress, 0.2)["triggered"] is True
    assert "正在进行" in ew.busy_window_thin_cushion(in_progress, 0.2)["reason_cn"]
    starting = {"hour_level": "normal", "windows": [{"label_cn": "CPI", "in_hours": 0.0}]}
    assert ew.busy_window_thin_cushion(starting, 0.2)["triggered"] is True
    busy_only = {"hour_level": "busy", "windows": []}
    assert ew.busy_window_thin_cushion(busy_only, 0.2)["triggered"] is True
    quiet = {"hour_level": "quiet", "windows": [{"label_cn": "美股开盘", "in_hours": 5}]}
    assert ew.busy_window_thin_cushion(quiet, 0.1)["triggered"] is False
    assert ew.busy_window_thin_cushion(starting, None)["triggered"] is None
    assert ew.busy_window_thin_cushion(None, 0.1)["triggered"] is None


def test_evaluate_and_chinese_thresholds():
    out = ew.evaluate(side="call", now_ms=NOW, contract_expiry_ms=EXPIRY, contract_expiry_label="21OCT26",
                      term_structure=TERMS, expiry_atm_iv=31.9, short_iv=30.0, other_short_iv=33.0, distance_in_moves=0.3,
                      history=[], clock_reading=clock.reading(NOW, EXPIRY))
    assert out["unknown"] == ["premium_thickening"] and out["rules"] == "entry_warnings@0.2"
    assert all(k in ew.THRESHOLD_CN.values() for w in out["warnings"] for k in w["threshold_cn"])


def test_evaluate_vetoes_only_inversion_and_thickening_and_the_note_never_counts():
    # 2026-10-20 14:00 UTC, a busy weekday hour with the US open just behind: the note is on
    busy = NOW + 4 * H
    out = ew.evaluate(side="call", now_ms=busy, contract_expiry_ms=EXPIRY, contract_expiry_label="21OCT26",
                      term_structure=TERMS, expiry_atm_iv=31.9, short_iv=30.0, other_short_iv=33.0, distance_in_moves=0.2,
                      history=[], clock_reading=clock.reading(busy, EXPIRY))
    assert [w["code"] for w in out["warnings"]] == ["term_inversion", "premium_thickening"]
    assert [n["code"] for n in out["notes"]] == ["busy_window"] and out["notes"][0]["active"] is True
    assert "busy_window" not in out["triggered"] + out["unknown"] and out["any_triggered"] is (out["triggered"] != [])
    assert out["notes"][0]["evidence"]["distance_in_moves"] == 0.2
    blind = ew.evaluate(side="call", now_ms=busy, contract_expiry_ms=EXPIRY, contract_expiry_label="21OCT26",
                        term_structure=TERMS, expiry_atm_iv=31.9, short_iv=30.0, other_short_iv=33.0, distance_in_moves=None,
                        history=[], clock_reading=None)
    assert blind["notes"][0]["active"] is None and blind["unknown"] == ["premium_thickening"]
    assert ew.WINDOW_HOURS == 2.0 and callable(ew.busy_window_thin_cushion)      # research_22 imports both
