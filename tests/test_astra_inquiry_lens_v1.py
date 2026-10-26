"""Experience reading of inquirer questions (docs/opus/19)."""
import json
import re
from pathlib import Path

import pytest

from tools import astra_inquiry_lens_v1 as lens
from tools import astra_inquiry_v1 as inquiry

ROOT = Path(__file__).resolve().parents[1]
H = 3_600_000
EXPIRY = 1792742400000            # 2026-10-23 08:00 UTC
V4 = "inquiry_prompt@0.4"


def q(text, why="", qid="K1"):
    return {"id": qid, "question_cn": text, "why_material_cn": why}


@pytest.mark.parametrize("text,card", [
    ("可见信用 0.0044 BTC 比两腿标记价差 0.0048 BTC 少 0.0004，这是我们按买价卖出的成交口径代价（挂单接近中间价即可改善），还是这一侧风险本就没有被加价？", "execution"),
    ("短腿 83000 一带的 Gamma 集中，会让上冲减速并钉在该处，还是被穿越后顺势加速？", "gamma_pin"),
    ("到期前 26 小时里，穿越短腿 83000 的路径会集中在 2026-10-22 13:30 UTC 之后的美股时段，还是在当前的安静时段也能穿？", "session"),
    ("0DTE 在 2026-10-22 08:00 UTC 结算、早于本合约到期，若价格在 08:00 UTC 前下探，82500 一带的 0DTE 墙会把下跌加速送向短腿，还是随 0DTE 结算消散？", "zero_dte"),
    ("这些偏多读数（量价 24 小时、48 小时、EDB）是同一次快照里的独立证据，还是同源被重复计票？", "direction"),
    ("本期 ATM IV 29.75% 低于近 24 小时实现波动 33.31%，这是一次性抬升，还是剩余时段的风险被低估？", "iv_vs_rv"),
])
def test_classify_picks_the_card_the_question_asks_about(text, card):
    got = lens.classify(q(text))
    assert got and got["card"] == card
    assert got["category"] in lens.cards()["categories"] and got["basis"]


def test_a_single_weak_word_is_left_unclassified():
    assert lens.classify(q("这张单子的盘口怎么样？")) is None
    assert lens.classify(q("")) is None


def test_reason_is_read_only_when_the_question_matches_nothing():
    got = lens.classify(q("这会怎样？", why="若挂单接近中间价可以收回价差成本，补偿改善"))
    assert got and got["card"] == "execution"


def test_only_prompt_0_4_and_later_sheets_get_a_reading():
    sheet = {"questions": [q("短腿 83000 的 Gamma 集中会钉住还是加速？")]}
    off = lens.sheet_lens(sheet, prompt_version="inquiry_prompt@0.3")
    assert off["enabled"] is False and "0.4" in off["reason_cn"]
    assert lens.sheet_lens(sheet, prompt_version=None)["enabled"] is False
    on = lens.sheet_lens(sheet, prompt_version=V4)
    assert on["enabled"] is True and on["questions"]["K1"]["card"] == "gamma_pin"
    assert on["counts"] == {"observe": 1} and on["order"] == ["observe"] and on["observe"][0]["question"] == "K1"


def _case(**values):
    layers, refs = {}, {}
    for ref, (v, extra) in values.items():
        layer = ref.split(".")[0]
        layers.setdefault(layer, []).append({"ref": ref, "values": v, "quality": "ok", **extra})
        refs[ref] = {"layer": layer, "index": len(layers[layer]) - 1}
    return {"layers": layers, "refs": refs}


def _clock(now_ms, expiry=EXPIRY, windows=()):
    return {"expiry_ms": expiry, "hours_to_expiry": (expiry - now_ms) / H, "now_ms": now_ms, "windows": list(windows)}


def test_execution_numbers_use_the_same_basis_as_the_experience():
    after_fee = _case(**{"L2.compensation": ({"net_credit_to_mark": 0.78, "status": "ok"}, {}),
                         "L2.book.long": ({"best_bid_price": 0.0003, "best_ask_price": 0.0005, "mark_price": 0.0004}, {})})
    rows = lens.live("execution", after_fee)
    assert rows[0]["value"] == "78%" and "69–86%" in rows[0]["note_cn"]
    assert rows[1]["value"] == "50%" and "打折" in rows[1]["note_cn"]
    pre_fee = _case(**{"L2.compensation": ({"net_credit_to_mark": None}, {}),
                       "L1.economics": ({"visible_credit_btc": 0.0044}, {}),
                       "L2.book.short": ({"mark_price": 0.0052}, {}),
                       "L2.book.long": ({"mark_price": 0.0004, "best_bid_price": 0.0004, "best_ask_price": 0.0005}, {})})
    rows = lens.live("execution", pre_fee)
    assert rows[0]["label_cn"].endswith("（扣费前）") and rows[0]["value"] == "92%" and "不能与" in rows[0]["note_cn"]
    assert rows[1]["note_cn"] is None


def test_session_gives_the_next_survival_row_by_side_only_for_a_daily_08_expiry():
    now = EXPIRY - 18 * H - 30 * 60_000                      # 10-22 13:30 UTC
    case = _case(**{"L6.session_clock": (_clock(now, windows=[{"at_ms": now + H, "label_cn": "美股开盘"}]), {}),
                    "L1.contract": ({"side": "call"}, {}),
                    "L6.entry_warnings": ({"warnings": [{"code": "busy_window_thin_cushion", "label_cn": "高波动窗口且缓冲不足", "triggered": True},
                                                        {"code": "term_inversion", "triggered": False}]}, {})})
    rows = lens.live("session", case)
    assert rows[0]["value"] == "10-22 14:30 UTC"
    assert rows[1]["label_cn"] == "若到 16:00 UTC 仍未触及" and rows[1]["value"] == "之后触及约 32%，到期越过约 16%"
    # an @0.1 block's busy-window item is the holding note now: not a veto row, but a point to watch
    assert rows[2]["value"] == "未触发" and rows[2]["veto"] is True and "只作否决" in rows[2]["note_cn"]
    assert rows[3]["label_cn"] == "高波动时段提示（不是否决）" and "veto" not in rows[3] and "35%" in rows[3]["note_cn"]
    note = {"code": "busy_window", "role": "note", "active": True,
            "reason_cn": "美股开盘约 1.0 小时后。在这类时点按标准短腿建仓，2 小时内触及短腿约 35%（其他时点约 26%）；这是持有中的盯盘提示，不是否决。"}
    v02 = _case(**{"L6.session_clock": (_clock(now, windows=[{"at_ms": now + H, "label_cn": "美股开盘"}]), {}),
                   "L1.contract": ({"side": "call"}, {}),
                   "L6.entry_warnings": ({"rules": "entry_warnings@0.2", "notes": [note],
                                          "warnings": [{"code": "term_inversion", "label_cn": "近端倒挂", "triggered": False},
                                                       {"code": "premium_thickening", "label_cn": "保费突然变厚", "triggered": None}]}, {})})
    rows = lens.live("session", v02)
    assert rows[2]["value"] == "部分无法判断" and rows[3]["value"] == "美股开盘约 1.0 小时后"
    quiet = _case(**{"L6.session_clock": (_clock(now), {}), "L1.contract": ({"side": "call"}, {}),
                     "L6.entry_warnings": ({"notes": [dict(note, active=False)], "warnings": []}, {})})
    assert all("时段提示" not in r["label_cn"] for r in lens.live("session", quiet))
    far = _case(**{"L6.session_clock": (_clock(EXPIRY - 40 * H), {}), "L1.contract": ({"side": "put"}, {})})
    rows = lens.live("session", far)
    assert rows[0]["value"] == "不给" and "超过一天" in rows[0]["note_cn"]
    odd = _case(**{"L6.session_clock": (_clock(EXPIRY - 10 * H, expiry=EXPIRY + 2 * H), {}), "L1.contract": ({"side": "put"}, {})})
    assert "08:00" in lens.live("session", odd)[0]["note_cn"]


def test_a_case_just_before_08_utc_starts_the_new_cycle():
    case = _case(**{"L6.session_clock": (_clock(EXPIRY - 24 * H - 60_000), {}), "L1.contract": ({"side": "call"}, {})})
    assert lens.live("session", case)[0]["label_cn"] == "若到 12:00 UTC 仍未触及"


def test_after_midnight_the_02_utc_row_is_next():
    now = EXPIRY - 7 * H                                       # 01:00 UTC
    case = _case(**{"L6.session_clock": (_clock(now), {}), "L1.contract": ({"side": "put"}, {})})
    assert lens.live("session", case)[0]["label_cn"] == "若到 02:00 UTC 仍未触及"


def test_touch_buckets_say_when_there_is_no_number():
    for hours, text in ((20, "约四分之一"), (4, "4–8%"), (9, "没有统计数字"), (1, "没有统计数字")):
        case = _case(**{"L6.session_clock": (_clock(EXPIRY - hours * H), {})})
        assert text in lens.live("touch_path", case)[0]["value"]


def test_zero_dte_and_tail_numbers():
    old = _case(**{"L3.spatial_position": ({}, {"relations": {"expiry_alignment": {}}})})
    assert lens.live("zero_dte", old)[0]["value"] == "不给"
    new = _case(**{"L3.spatial_position": ({}, {"relations": {"expiry_alignment": {
        "zero_dte_expiry_ms": EXPIRY - 24 * H, "zero_dte_expires_before_contract": True}}})})
    assert lens.live("zero_dte", new)[0]["value"] == "10-22 08:00 UTC"
    tail = _case(**{"L2.baseline.p_max_loss": ({"value": 0.0542}, {})})
    assert lens.live("deep_tail", tail)[0]["value"] == "5.4%"
    assert lens.live("gamma_pin", tail) == [] and lens.live("execution", None) == []
    legs = _case(**{"L1.contract": ({"short_strike_usd": 82500.0, "long_strike_usd": 84500.0}, {})})
    assert lens.live("gamma_pin", legs)[0]["value"] == "短腿 82,500；保护腿 84,500"


def test_malformed_case_says_nothing():
    assert lens.live("session", {"refs": {"L6.session_clock": {"layer": "L6", "index": 9}}, "layers": {"L6": []}}) == [
        {"label_cn": "未触及时的剩余风险", "value": "不给", "note_cn": "缺日内时段读数"},
        {"label_cn": "不建仓警示（否决层）", "value": "不可用", "note_cn": None}]


def test_card_texts_never_argue_for_taking_a_contract():
    data = lens.cards()
    texts = [c["reading_cn"] for c in data["cards"]] + [b["text_cn"] for c in data["cards"] for b in c["basis"]]
    texts += [v["phrase_cn"] for v in data["categories"].values()]
    for text in texts:
        assert inquiry.forbidden(text) is None, text
        assert not re.search(r"不必担心|放心|安全|可以做|建议做|值得做", text), text


def test_registry_is_consistent():
    data = lens.cards()
    ids = [c["id"] for c in data["cards"]]
    assert sorted(ids) == sorted(data["order"]) and len(set(ids)) == len(ids)
    assert all(c["category"] in data["categories"] for c in data["cards"])
    assert set(lens.LIVE) <= set(ids)
    assert data["doc17_version"].startswith("17_")


def test_delta_selection_and_skew_questions_stay_unclassified():
    assert lens.classify(q("短腿选取目标 |Delta| 是 0.35，实际选中的短腿 |Delta| 为 0.4049，这是为何？")) is None


def test_put_side_rows_zero_bid_nan_and_expired():
    now = EXPIRY - 18 * H - 30 * 60_000
    put = _case(**{"L6.session_clock": (_clock(now), {}), "L1.contract": ({"side": "put"}, {})})
    assert lens.live("session", put)[0]["value"] == "之后触及约 30%，到期越过约 13%"
    zero = _case(**{"L2.compensation": ({}, {}), "L2.book.long": ({"best_bid_price": 0, "best_ask_price": 0.0001}, {})})
    assert lens.live("execution", zero)[0]["value"].startswith("买价为 0")
    nan = _case(**{"L2.compensation": ({"net_credit_to_mark": float("nan")}, {})})
    assert lens.live("execution", nan) == []
    done = _case(**{"L6.session_clock": (_clock(EXPIRY + H), {})})
    assert lens.live("touch_path", done)[0]["note_cn"] == "合约已到期"
    other = _case(**{"L6.session_clock": ({**_clock(EXPIRY - 5 * H), "expiry_source": "next_daily_expiry"}, {})})
    assert "不是按本合约" in lens.live("touch_path", other)[0]["note_cn"]
    odd = _case(**{"L6.session_clock": (_clock(EXPIRY - 5 * H, windows=["bad", {"at_ms": EXPIRY - 4 * H, "label_cn": "美股开盘"}]), {}),
                   "L1.contract": ({"side": "call"}, {})})
    assert lens.live("session", odd)[0]["value"] == "10-23 04:00 UTC"


def test_veto_state_is_shown_on_the_question_but_not_listed_as_an_observation_point():
    now = EXPIRY - 18 * H
    case = _case(**{"L6.session_clock": (_clock(now, windows=[{"at_ms": now + H, "label_cn": "美股开盘"}]), {}),
                    "L1.contract": ({"side": "call"}, {}),
                    "L6.entry_warnings": ({"warnings": [{"code": "x", "triggered": False}]}, {})})
    sheet = {"questions": [{"question_cn": "穿越短腿会集中在美股时段，还是安静时段也能穿？"}]}
    out = lens.sheet_lens(sheet, case, prompt_version=V4)
    rows = out["questions"]["#1"]["live"]
    assert any(r.get("veto") for r in rows)
    assert out["observe"][0]["question"] == "#1" and not any(p.get("veto") for p in out["observe"][0]["points"])


def test_full_view_through_the_reader_carries_numbers(tmp_path):
    import gzip
    from tools import astra_inquiry_view_v1 as iv
    short, long_ = "BTC-24OCT26-82000-C", "BTC-24OCT26-84000-C"
    rel = "inquiry/2026-10-23/02_call_cf_aaaaaaaaaaaaaaaaaaaaaaaa.json"
    (tmp_path / "_state").mkdir()
    (tmp_path / "_state" / "state.json").write_text(json.dumps({"contracts": {f"{short}|{long_}": {"inquiry": [rel]}}}))
    (tmp_path / rel).parent.mkdir(parents=True)
    (tmp_path / rel).write_text(json.dumps({"case_id": "cf_aaaaaaaaaaaaaaaaaaaaaaaa", "status": "OK", "prompt_version": V4,
        "sheet": {"questions": [{"id": "K1", "question_cn": "可见信用比两腿标记价差少，是成交口径吃亏，还是这一侧被加价？", "premises": []}]}}))
    case = _case(**{"L2.compensation": ({"net_credit_to_mark": 0.8}, {})})
    path = tmp_path / ("cases/" + rel[len("inquiry/"):] + ".gz")
    path.parent.mkdir(parents=True)
    path.write_bytes(gzip.compress(json.dumps(case).encode("utf-8")))
    reading = iv.InquiryReader(tmp_path).view(short, long_)["selected"]["lens"]
    assert reading["questions"]["K1"]["card"] == "execution" and reading["questions"]["K1"]["live"][0]["value"] == "80%"


def test_same_card_same_points_share_one_observation_line():
    case = _case(**{"L1.contract": ({"short_strike_usd": 79000.0, "long_strike_usd": 77000.0}, {})})
    sheet = {"questions": [{"id": "K1", "question_cn": "79000 的 Gamma 集中会钉住还是加速？"},
                           {"id": "K2", "question_cn": "本期墙会把价格吸住还是穿越后加速？"}]}
    out = lens.sheet_lens(sheet, case, prompt_version=V4)
    assert [o["question"] for o in out["observe"]] == ["K1、K2"]


def test_events_card_shows_the_busy_window_note_it_points_to():
    warn = {"rules": "entry_warnings@0.2", "warnings": [{"code": "term_inversion", "label_cn": "近端倒挂", "triggered": False}],
            "notes": [{"code": "busy_window", "role": "note", "active": True, "reason_cn": "美股开盘约 1.5 小时后。在这类时点……"}]}
    case = _case(**{"L7.events": ({"events": [], "gaps": []}, {}), "L6.entry_warnings": (warn, {})})
    rows = lens.LIVE["events"](case)
    veto = next(r for r in rows if r["label_cn"] == "不建仓警示（否决层）")
    assert veto["value"] == "未触发"                                       # the note never shows as a veto
    note = next(r for r in rows if r["label_cn"].startswith("高波动时段提示"))
    assert note["value"] == "美股开盘约 1.5 小时后"
    warn["notes"][0]["active"] = False
    assert not any(r["label_cn"].startswith("高波动时段提示") for r in lens.LIVE["events"](case))
