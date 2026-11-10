"""Rulebook v0.2 numbers (docs/opus/27 §5-§9, docs/opus/28 steps 1-2): the round information, the
net-income veto, the selection summary of a reading and its place in the frozen case and the ledger."""
import json
import math
import re
from pathlib import Path

import pytest

from tools import astra_case_file_v1 as cf
from tools import astra_decision_path_v1 as dp
from tools import astra_round_info_v1 as ri
from tools import astra_session_clock_v1 as session_clock
from tools import astra_unsuitable_v1 as unsuitable
from test_astra_decision_path import EXPIRY, NOW, candidate, chain_frame

H = 3_600_000
DAY = 24 * H
ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ round_info

def test_required_edge_from_the_reservation():
    res = unsuitable.reservation(0.0025, 0.0004, 0.0023, {"bid": 0.0060, "ask": 0.0066}, {"bid": 0.0030, "ask": 0.0032})
    edge = ri.required_edge(res)
    # hurdle at the bid = reservation 0.0029 - credit 0.0023 = 0.0006 over the mark spread 0.0025
    assert edge["reliable"] is True and edge["note_cn"] is None
    assert edge["bid"] == pytest.approx(0.0006 / 0.0025)
    # the mid credit 0.0063 - 0.0031 = 0.0032 is above the reservation: no hurdle
    assert edge["mid"] == 0.0
    assert ri.required_edge_at(0.0027, res) == pytest.approx(0.0002 / 0.0025)
    assert ri.required_edge_at(0.0040, res) == 0.0


def test_required_edge_is_withheld_when_it_means_nothing():
    assert ri.required_edge(unsuitable.reservation(None, 0.0004, 0.0023))["bid"] is None
    flat = ri.required_edge(unsuitable.reservation(0.0, 0.0004, 0.0023))
    assert flat["bid"] is None and "不大于 0" in flat["note_cn"]
    empty_long = unsuitable.reservation(0.0025, 0.0004, 0.0023, {"bid": 0.0060, "ask": 0.0066}, {"bid": 0.0, "ask": 0.0032})
    unreliable = ri.required_edge(empty_long)
    assert unreliable["bid"] is None and unreliable["mid"] is None and "不可靠" in unreliable["note_cn"]
    assert ri.required_edge_at(0.0027, empty_long) is None
    assert ri.required_edge({})["note_cn"] and ri.required_edge(None)["bid"] is None


def test_max_loss_is_the_width_at_spot_minus_the_net_income():
    out = ri.max_loss(1.0, 2000.0, 80_000.0, 0.0020)
    assert out["btc"] == pytest.approx(0.025 - 0.0020) and out["usd"] == pytest.approx(0.023 * 80_000)
    assert ri.max_loss(1.0, 2000.0, None, 0.002) == {"btc": None, "usd": None}
    assert ri.max_loss(1.0, None, 80_000.0, 0.002)["btc"] is None
    assert ri.max_loss(1.0, 2000.0, 80_000.0, None)["btc"] is None


def test_two_rounds_per_expiry_with_the_pass_cost_in_round_one():
    expiry = EXPIRY                                   # an 08:00 UTC expiry
    round1 = expiry - 30 * H                          # 02:00 UTC the day before
    r = ri.rounds(round1 + 10 * 60_000, expiry, mark_spread_btc=0.0025, spot_usd=80_000.0)
    assert r["current"] == 1 and r["current_at_ms"] == round1 and r["rounds_utc"] == [2, 14]
    assert r["next"] == {"round": 2, "at_ms": round1 + 12 * H, "same_expiry": True, "hours_left_at": 18.0}
    cost = r["pass_cost"]
    total = session_clock.variance_between(round1 + 10 * 60_000, expiry)
    after = session_clock.variance_between(round1 + 12 * H, expiry)
    assert cost["variance_share"] == pytest.approx((total - after) / total)
    assert cost["premium_btc"] == pytest.approx(0.0025 * (1 - math.sqrt(after / total)))
    assert cost["premium_usd"] == pytest.approx(cost["premium_btc"] * 80_000)
    assert 0 < cost["variance_share"] < 1 and "近似" in cost["note_cn"]
    # round 2: no round after it for this expiry; the next one shows the following expiry
    r2 = ri.rounds(round1 + 12 * H + 60_000, expiry)
    assert r2["current"] == 2 and r2["pass_cost"] is None
    assert r2["next"]["same_expiry"] is False and r2["next"]["round"] == 1 and r2["next"]["hours_left_at"] == 30.0
    # outside a window: no current round, the window closes 45 minutes after the hour
    between = ri.rounds(round1 + 46 * 60_000, expiry)
    assert between["current"] is None and between["next"]["round"] == 2 and between["pass_cost"] is None
    # 20:00 UTC: this expiry has 12 h left and is about to be replaced; the next round is the new one's first
    late = ri.rounds(expiry - 12 * H + 60_000, expiry)
    assert late["current"] is None and late["next"]["same_expiry"] is False and late["next"]["round"] == 1
    assert ri.rounds(None, expiry) is None and ri.rounds(round1, None) is None


def test_round_numbers_follow_the_hours_left():
    assert ri.round_number(EXPIRY - 30 * H, EXPIRY) == 1 and ri.round_number(EXPIRY - 18 * H, EXPIRY) == 2


# ------------------------------------------------------------------ net income veto

def test_credit_item_vetoes_a_non_positive_net_income():
    assert unsuitable.credit_item(0.0001) is None and unsuitable.credit_item(None) is None
    item = unsuitable.credit_item(-0.0002, -16.0)
    assert item["kind"] == "veto" and item["code"] == "credit" and "-0.0002 BTC" in item["evidence_cn"]
    assert unsuitable.credit_item(0.0)["code"] == "credit"


def _thin(net):
    cand = candidate()
    cand["snapshot"]["economics"].update(net_credit_after_fee_btc=net)
    return cand


def test_v_vetoes_a_non_positive_net_income_and_p_still_reads():
    steps = dp.build(chain_frame(cand=_thin(-0.0001)))["steps"]
    codes = [(i["code"], i["src"]) for i in steps["V"]["items"]]
    assert ("credit", "P") in codes and steps["V"]["verdict"]["code"] == "veto"
    assert not any(c == "credit" for c, _ in [(i["code"], 0) for i in dp.build(chain_frame())["steps"]["V"]["items"]])


def test_card_vetoes_a_non_positive_net_income():
    cand = {"snapshot": {"contract": {"side": "put", "quantity_btc": 1.0}, "market": {"quote": {"gap_reasons": []}}},
            "readings": {"pricing": {"net_credit_btc": -0.0001, "net_credit_usd": -8.0}}}
    card = unsuitable.card(cand)
    assert any(i["code"] == "credit" and i["kind"] == "veto" for i in card["items"])


def test_v_says_how_many_warnings_could_not_be_judged():
    unknown = [{"code": "term_inversion", "label_cn": "近端倒挂", "triggered": None},
               {"code": "premium_thickening", "label_cn": "保费突然变厚", "triggered": False}]
    v = dp.build(chain_frame(unknown, {"bid": 0.0062, "ask": 0.0070}, {"bid": 0.0034, "ask": 0.0036}))["steps"]["V"]
    assert "1 项无法判断" in v["verdict"]["cn"] and v["verdict"]["tone"] == "warn"


# ------------------------------------------------------------------ P step numbers

def test_p_carries_required_edge_max_loss_and_rounds():
    p = dp.build(chain_frame(None, {"bid": 0.0058, "ask": 0.0062}, {"bid": 0.0034, "ask": 0.0038}))["steps"]["P"]
    res = p["reservation"]
    assert res["required_edge_bid"] == pytest.approx(res["bid_gap_btc"] / res["mark_spread_btc"])
    assert res["required_edge_mid"] == pytest.approx(max(0.0, res["mid_gap_btc"]) / res["mark_spread_btc"])
    assert res["max_loss_btc"] == pytest.approx(2000.0 / 83_345.0 - 0.00193)
    assert p["round"]["schema"] == ri.SCHEMA and p["round"]["next"]["at_ms"] > NOW
    lines = {l["k"]: l for l in p["lines"]}
    assert "按买价" in lines["所需优势"]["v"] and "已含费用" in lines["所需优势"]["v"]
    assert lines["最大损失"]["v"].startswith(f"{res['max_loss_btc']:.4f} BTC")
    assert "下一轮" in lines["轮次"]["v"] and p["lines"][-1]["k"] == "对照否决"
    assert all(lines[k]["src"] == "P" for k in ("所需优势", "最大损失", "轮次"))


def test_p_says_why_the_required_edge_is_missing():
    # an empty protection-leg bid makes the reservation unreliable
    p = dp.build(chain_frame(None, {"bid": 0.0058, "ask": 0.0062}, {"bid": 0.0, "ask": 0.0038}))["steps"]["P"]
    line = next(l for l in p["lines"] if l["k"] == "所需优势")
    assert "不可靠" in line["v"] and line["tone"] == "warn" and p["reservation"]["required_edge_bid"] is None


# ------------------------------------------------------------------ selection summary

def test_selection_summarises_the_hard_rules_and_the_round_numbers():
    reading = dp.build(chain_frame(None, {"bid": 0.0058, "ask": 0.0062}, {"bid": 0.0034, "ask": 0.0038}))
    sel = dp.selection(reading)
    assert sel["schema"] == "selection@0.1" and sel["path_version"] == dp.PATH_VERSION and sel["at_ms"] == NOW
    assert sel["eligible"] is True and sel["vetoes"] == []
    assert sel["pricing"]["required_edge_bid"] == reading["steps"]["P"]["reservation"]["required_edge_bid"]
    assert sel["round"] == reading["steps"]["P"]["round"]
    vetoed = dp.selection(dp.build(chain_frame(cand=_thin(-0.0001))))
    assert vetoed["eligible"] is False and {"code": "credit", "title_cn": "净收入不为正", "src": "P"} in vetoed["vetoes"]
    assert dp.selection({"steps": {"V": {"verdict": {"code": "error"}}}})["eligible"] is None
    assert dp.selection({})["eligible"] is None


def test_selection_is_in_the_case_but_never_shown_to_the_inquirer():
    snapshot = {"snapshot_id": "a" * 64, "contract": {"side": "put", "expiry_ms": EXPIRY, "short_strike_usd": 82000.0,
                                                      "long_strike_usd": 80000.0, "quantity_btc": 1.0},
                "market": {"valuation_ts_ms": NOW}}
    sel = {"schema": "selection@0.1", "eligible": True, "vetoes": [], "pricing": {"required_edge_bid": 0.2},
           "round": {"current": 1}}
    case = cf.build(snapshot=snapshot, raw_market={}, asof_ms=NOW, selection=sel)
    assert case["selection"] == sel and case["build"]["builder"] == cf.BUILDER_VERSION
    plain = cf.build(snapshot=snapshot, raw_market={}, asof_ms=NOW)
    assert "selection" not in plain and plain["build"]["content_sha256"] != case["build"]["content_sha256"]
    inquirer = cf.for_inquirer(case)
    assert "selection" not in inquirer and "selection" in case          # the stored case keeps it
    assert "required_edge" not in json.dumps(inquirer, ensure_ascii=False)


# ------------------------------------------------------------------ wording

FORBIDDEN = re.compile(r"有人|知道些什么|内情|知情|买保护|急需")


def test_no_reason_names_who_is_trading_or_what_they_know():
    """docs/opus/27 §2: a premium that moves is repricing, never evidence about a counterparty."""
    for rel in ("tools/astra_unsuitable_v1.py", "tools/astra_entry_warnings_v1.py", "tools/astra_decision_path_v1.py",
                "tools/astra_decision_path_narrative_v1.py", "tools/astra_round_info_v1.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        strings = re.findall(r'"([^"\n]*)"', text)
        hits = [s for s in strings if FORBIDDEN.search(s)]
        assert not hits, (rel, hits[:3])


def test_thickening_reasons_by_kind():
    base = {"code": "premium_thickening", "label_cn": "保费突然变厚", "triggered": True}
    why = lambda **e: unsuitable.thickening_why({**base, "evidence": e})   # noqa: E731
    assert "本侧的墙不能当缓冲读" in why(kind="one_sided")
    assert "本侧的墙不能当缓冲读" in why(one_sided=True)                    # an @0.2 block
    assert "普遍的风险重估" in why(kind="both_sides")
    assert "已实现波动也在上升" in why(kind="overall", rv_rising=True)
    assert "尚未发生的波动" in why(kind="overall", rv_rising=False)
    assert why(kind="overall") == unsuitable._WHY["premium_thickening"][0]
    assert "关键价位" in why(kind="overall", near_level={"kind": "pin", "strike": 80000.0, "distance_usd": 150.0})
    items = unsuitable.warning_items({"warnings": [{**base, "evidence": {"kind": "one_sided", "skew_jump_vol_pts": 3.4}}]})
    assert "本侧的墙" in items["items"][0]["why_cn"] and not FORBIDDEN.search(items["items"][0]["why_cn"])


# ------------------------------------------------------------------ ledger and protocol registry

def test_ledger_rows_carry_the_page_card_codes():
    import astra_research_archive_v1 as ar
    card = {"schema": "unsuitable@0.1", "items": [{"code": "narrow", "kind": "veto"}, {"code": "mid_short", "kind": "caution"}]}
    assert ar._veto_fields(card) == {"veto_any": True, "veto_codes": ["narrow"], "caution_codes": ["mid_short"],
                                     "veto_rules": "unsuitable@0.1"}
    assert ar._veto_fields({"schema": "unsuitable@0.1", "items": []})["veto_any"] is False
    assert ar._veto_fields(None)["veto_any"] is None and ar._veto_fields({"error_type": "X"})["veto_codes"] is None
    assert ar.LEDGER_FIELDS == "ledger_research_fields@0.4"


def test_protocol_registry_knows_the_selection_game():
    import astra_research_archive_v1 as ar
    versions = ar.observed_versions()
    assert versions["decision_path"] == dp.PATH_VERSION and versions["round_info"] == ri.SCHEMA
    assert versions["unsuitable"] == unsuitable.SCHEMA
    assert {"rulebook", "player_prompt", "selection_scorecard"} <= set(ar.REGISTRABLE)
    schema = json.loads((ROOT / "deploy/research_archive/schemas/protocol_version_0.1.schema.json").read_text(encoding="utf-8"))
    enum = set(schema["properties"]["component"]["enum"])
    assert set(versions) <= enum and set(ar.REGISTRABLE) <= enum


# ------------------------------------------------------------------ review fixes (2026-11-10)

def test_a_reading_on_another_expiry_is_not_a_round():
    round1 = EXPIRY - 30 * H
    # 02:00 UTC with only 6 hours left (a lock on the expiring contract): no current round
    assert ri.rounds(round1 + 24 * H + 60_000, EXPIRY)["current"] is None
    # a weekly expiry 3 days away at a round hour
    assert ri.rounds(round1 + 60_000, EXPIRY + 3 * DAY)["current"] is None
    assert ri.rounds(round1 + 60_000, EXPIRY)["current"] == 1


def test_the_card_names_an_unknown_fee_as_missing_data():
    cand = {"snapshot": {"contract": {"side": "put", "quantity_btc": 1.0}, "market": {"quote": {"gap_reasons": []}}},
            "readings": {"pricing": {"credit_btc": 0.0023, "fee_btc": None, "net_credit_btc": None}}}
    card = unsuitable.card(cand)
    data = next(i for i in card["items"] if i["code"] == "data")
    assert data["kind"] == "veto" and "费用未知" in data["evidence_cn"] and card["schema"] == "unsuitable@0.2"


def test_an_unreadable_version_is_not_recorded(tmp_path, monkeypatch):
    import astra_research_archive_v1 as ar
    real = ar.observed_versions
    monkeypatch.setattr(ar, "observed_versions", lambda: {**real(), "round_info": None})
    archive = ar.Archive(tmp_path)
    ar.tick(archive, now_ms=NOW, view=None, fetch_case=lambda sid: {}, deribit=ar.Deribit(lambda p, q: {}))
    rows = [json.loads(l) for l in (tmp_path / "protocols/versions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows and all(r["version"] for r in rows) and "round_info" not in {r["component"] for r in rows}
