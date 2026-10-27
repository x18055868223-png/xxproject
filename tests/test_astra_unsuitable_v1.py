"""Unsuitable situations (unsuitable@0.1): warnings, buffer, data, the reservation price and the page card."""
import pytest

from tools import astra_candidate_readings_v1 as readings
from tools import astra_decision_path_v1 as dp
from tools import astra_unsuitable_v1 as un

KEYS = {"code", "kind", "title_cn", "evidence_cn", "why_cn", "clears_cn", "basis"}
INVERSION = {"code": "term_inversion", "label_cn": "近端倒挂", "triggered": True,
             "evidence": {"contract_expiry": "28OCT26", "contract_atm_iv_clock_adjusted": 41.25, "week_expiry": "06NOV26",
                          "week_atm_iv_clock_adjusted": 38.0, "gap_vol_pts": 3.25}}
THICK = {"code": "premium_thickening", "label_cn": "保费突然变厚（单侧）", "triggered": True,
         "evidence": {"atm_jump_vol_pts": 0.4, "skew_jump_vol_pts": 3.6, "other_side_skew_jump_vol_pts": -0.2, "one_sided": True}}


def test_one_narrow_constant():
    assert un.NARROW_MOVES == dp.NARROW_MOVES == readings.NARROW_MOVES == 0.25


def test_fired_warnings_are_vetoes_with_their_numbers():
    out = un.warning_items({"warnings": [INVERSION, THICK, {"code": "x", "label_cn": "其他", "triggered": False}]})
    inv, thick = out["items"]
    assert set(inv) == KEYS and inv["kind"] == "veto" and inv["code"] == "term_inversion"
    assert inv["evidence_cn"] == "时钟校正后：本期 28OCT26 41.2%，一周 06NOV26 38.0%，差 3.2 点"
    assert "一种解释" in inv["why_cn"] and "未检验" in inv["why_cn"] and "未验证" in inv["basis"] and "只作否决" in inv["basis"]
    assert thick["evidence_cn"] == "本期 IV（时钟校正）较近 6 小时 +0.4 点，本侧偏斜 +3.6 点，另一侧偏斜 -0.2 点"
    assert thick["why_cn"].startswith("保费突然变厚，首先是有人在买保护的警告，不是") and "另一侧没动" in thick["why_cn"]
    assert [c["status_cn"] for c in out["checks"]] == ["触发", "触发", "未触发"] and out["unknown_cn"] == []


def test_unknown_warnings_and_unavailable_blocks():
    out = un.warning_items({"warnings": [{**THICK, "triggered": None, "evidence": {}},
                                         {"code": "busy_window_thin_cushion", "triggered": True}]})
    assert out["items"] == [] and out["unknown_cn"] == ["保费突然变厚（单侧）（数据不足）"]
    assert [c["status_cn"] for c in out["checks"]] == ["无法判断"]          # the @0.1 busy item is a note, not read
    assert un.warning_items(None)["unknown_cn"] == ["不建仓警示不可用"]
    assert un.warning_items({"error_type": "X"})["items"] == []
    assert un.warning_items({"warnings": []})["unknown_cn"] == ["不建仓警示为空"]
    missing_other = un.warning_items({"warnings": [{**THICK, "evidence": {"atm_jump_vol_pts": 3.4}}]})["items"][0]
    assert missing_other["evidence_cn"].endswith("本侧偏斜 无数据，另一侧无数据")


def test_buffer_item():
    assert un.buffer_item(1.0, 0.45, 2.2, "put") is None                    # standard: no item
    assert un.buffer_item(None, None, None, "put") is None and un.buffer_item(1.0, None, None, "put") is None
    narrow = un.buffer_item(0.4, 0.18, 2.2, "put")
    assert narrow["code"] == "narrow" and narrow["kind"] == "veto"
    assert "0.40%" in narrow["evidence_cn"] and "0.18 倍" in narrow["evidence_cn"] and "±2.20%" in narrow["evidence_cn"]
    assert "未验证" in narrow["why_cn"] and "更远的短腿" in narrow["clears_cn"]
    inside = un.buffer_item(-0.3, -0.1, 2.2, "call")
    assert inside["code"] == "inside" and inside["evidence_cn"] == "短腿在现价下方 0.30%"


def test_data_item():
    assert un.data_item([]) is None
    item = un.data_item(["报价缺口", "盘口深度不足"])
    assert item["kind"] == "veto" and item["evidence_cn"] == "缺口：报价缺口、盘口深度不足" and "刷新" in item["clears_cn"]


def test_reservation_math_with_both_books():
    res = un.reservation(0.0025, 0.0004, 0.0023, {"bid": 0.0060, "ask": 0.0066}, {"bid": 0.0034, "ask": 0.0038},
                         qty=1.0, spot=80_000.0)
    assert res["reservation_btc"] == pytest.approx(0.0029)                 # mark spread + fee
    assert res["bid_gap_btc"] == pytest.approx(0.0006) and res["bid_gap_usd"] == pytest.approx(48.0)
    assert res["mid_credit_btc"] == pytest.approx(0.0063 - 0.0036)
    assert res["mid_gap_btc"] == pytest.approx(0.0002) and res["mid_gap_usd"] == pytest.approx(16.0)
    assert res["long_spread_to_mid"] == pytest.approx(0.0004 / 0.0036) and not res["long_book_wide"]
    assert res["reservation_reliable"] is True
    mid = un.mid_item(res)
    assert mid["kind"] == "caution" and "少约 16 美元" in mid["evidence_cn"] and "0.0029 BTC" in mid["evidence_cn"]
    assert un.long_book_item(res) is None
    covered = un.reservation(0.0025, 0.0004, 0.0023, {"bid": 0.0062, "ask": 0.0070}, {"bid": 0.0034, "ask": 0.0036})
    assert covered["mid_gap_btc"] < 0 and un.mid_item(covered) is None and covered["reservation_usd"] is None
    double = un.reservation(0.0025, 0.0004, 0.0023, {"bid": 0.0060, "ask": 0.0066}, {"bid": 0.0034, "ask": 0.0038}, qty=2.0)
    assert double["mid_credit_btc"] == pytest.approx(2 * (0.0063 - 0.0036))


def test_reservation_without_books_and_with_an_empty_long_book():
    res = un.reservation(0.0025, 0.0004, 0.0023)
    assert res["reservation_btc"] == pytest.approx(0.0029) and res["mid_credit_btc"] is None and res["mid_gap_btc"] is None
    assert res["reservation_reliable"] is True and un.mid_item(res) is None
    for bad in ({"bid": 0.001, "ask": 0.0}, {"bid": 0.002, "ask": 0.001}, {"bid": None, "ask": 0.001}, "x"):
        assert un.reservation(0.0025, 0.0004, 0.0023, {"bid": 0.006, "ask": 0.0066}, bad)["mid_credit_btc"] is None
    empty = un.reservation(0.0025, 0.0004, 0.0023, {"bid": 0.006, "ask": 0.0066}, {"bid": 0.0, "ask": 0.0008})
    assert empty["long_bid_zero"] and empty["long_book_wide"] and empty["reservation_reliable"] is False
    item = un.long_book_item(empty)
    assert item["kind"] == "caution" and item["evidence_cn"] == "保护腿买价为 0" and "失真" in item["why_cn"]
    wide = un.reservation(0.0025, 0.0004, 0.0023, None, {"bid": 0.0004, "ask": 0.0008})
    assert not wide["long_bid_zero"] and wide["long_book_wide"] and un.long_book_item(wide)["evidence_cn"] == "保护腿点差为中间价的 67%"
    none = un.reservation(None, 0.0004, 0.0023)
    assert none["reservation_btc"] is None and none["bid_gap_btc"] is None and none["reservation_reliable"] is False
    assert "12.5%" in un.B_RESERVATION and "假设" in un.B_RESERVATION


def test_card_reads_the_page_candidate():
    cand = {"side": "put", "snapshot": {"contract": {"side": "put", "quantity_btc": 1.0},
                                        "market": {"quote": {"gap_reasons": ["x"], "available_depth_btc": {"short": 0.5, "long": 3}}}},
            "entry_warnings": {"warnings": [INVERSION, {**THICK, "triggered": None}]},
            "readings": {"expected_move": {"short_distance_pct": 0.4, "distance_in_moves": 0.18, "move_pct": 2.2},
                         "pricing": un.reservation(0.0025, 0.0004, 0.0023, {"bid": 0.006, "ask": 0.0066},
                                                   {"bid": 0.0, "ask": 0.0008}, spot=80_000.0)}}
    card = un.card(cand)
    assert card["schema"] == "unsuitable@0.1"
    assert [i["code"] for i in card["items"]] == ["data", "term_inversion", "narrow", "long_book"]
    assert card["items"][0]["evidence_cn"] == "缺口：报价缺口、盘口深度不足"
    assert (card["veto_count"], card["caution_count"]) == (3, 1)
    assert card["unknown_cn"] == ["保费突然变厚（单侧）（数据不足）"]
    quiet = un.card({"snapshot": {"contract": {}}, "entry_warnings": {"warnings": [{**INVERSION, "triggered": False}]}})
    assert quiet["items"] == [] and quiet["unknown_cn"] == ["缓冲无法计算"]
    assert un.card(None)["items"][0]["code"] == "data"
    assert un.card({"snapshot": {"market": {"quote": "bad"}}})["schema"] == "unsuitable@0.1"
