"""Decision Path narrative view (docs/opus/24): templates, footnotes, asks, graded evidence,
notes and choices in the store, and the narrative served (never stored) by the workbench."""
import json
import re
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tools import astra_decision_path_narrative_v1 as nv
from tools import astra_decision_path_store_v1 as store
from tools import astra_decision_path_v1 as dp
from tools import astra_workbench_v20 as workbench
from test_astra_decision_path import EXPIRY, H1, NOW, QUIET_VETOES, BUSY_NOTE, as_v2_session, candidate, frame, setup_wb, v2frame

NUM = re.compile(r"\d+(?:[.,]\d+)*")
U, S, D = "toward_underwrite", "unchanged", "toward_decline"


def priced():
    cand = candidate()
    cand["decision_reduction"]["questions"][0]["numbers"].update(market_payout_btc=0.0025, spread_cost_usd=-33.0)
    return cand


def readings():
    expired = frame()
    expired["at_ms"] = EXPIRY + 1
    no_walls = frame()
    no_walls["expiry_walls"] = None
    morning = v2frame(now=EXPIRY - 18 * H1)
    morning["at_ms"] = EXPIRY - 18 * H1
    state = v2frame(hour=16, warnings=[{"code": "x", "label_cn": "期限倒挂", "triggered": False}])
    state["realized_vol"] = {"rv_24h_pct": 60.0, "rv_7d_pct": 40.0}
    frames = [frame(), v2frame(priced()), v2frame(candidate("call", short=84_000.0, long_=86_000.0)),
              v2frame(hour=9, warnings=[{"code": "premium_thickening", "label_cn": "保费突然变厚", "triggered": True},
                                         {"code": "term", "label_cn": "期限倒挂", "triggered": False}]),
              v2frame(hour=12, warnings=QUIET_VETOES, notes=[BUSY_NOTE]), v2frame(expiry=EXPIRY + 2 * H1),
              v2frame(candidate(spot=82_400.0)), frame(candidate(spot=90_000.0)), expired, no_walls, morning, state,
              {"at_ms": NOW}]
    return [dp.build(f) for f in frames]


def text_of(n):
    return " ".join([n["lead"]] + [s["text"] for s in n["sentences"]] + [b["text"] for b in n["background"]])


def test_every_line_is_covered_and_keeps_its_numbers():
    for reading in readings():
        narrative = nv.narrate(reading)
        assert narrative["schema"] == "dp_narrative@0.1" and set(narrative["steps"]) == set(reading["steps"])
        for sid, step in reading["steps"].items():
            n = narrative["steps"][sid]
            lines = step.get("lines") or []
            assert n["covered"] == list(range(len(lines))), sid
            body = " ".join([s["text"] for s in n["sentences"]] + [b["text"] for b in n["background"]])
            for line in lines:
                for number in NUM.findall(line["v"]):
                    assert number in body, (sid, line, number)
                assert line["v"].rstrip("。") in body or all(p in body for p in line["v"].split("：")), (sid, line)
            for s in n["sentences"]:
                assert s["tone"] in ("", "warn", "bad") and s["text"].endswith("。")


def test_lines_read_as_prose_not_key_value():
    n = nv.narrate(dp.build(v2frame(priced(), hour=16)))["steps"]
    assert n["D2"]["lead"] == "缓冲在标准附近。"
    assert n["D2"]["sentences"][0]["text"].startswith("缓冲在标准附近：短腿在现价下方")
    assert any(s["text"].startswith("从现价到短腿之间：本期 Put Gamma 墙 83,000") for s in n["D2"]["sentences"])
    assert any(s["text"] == "下一个高波动窗口是美股开盘，2.5 小时后。" for s in n["T"]["sentences"])
    assert n["D1"]["sentences"][0]["text"] == "到期价跌破 82,500 美元开始赔，责任区是 82,500 → 80,500 美元（宽 2,000 美元）。"
    assert n["Q2"]["sentences"][0]["text"] == "扣掉入场费后净收 161 美元（0.0019 BTC），毛信用占价差宽度的 9.7%。"
    assert n["D0"]["lead"] == "资料可读。" and n["AU"]["ask"] is None and n["AU"]["sentences"] == []
    # a joined sentence carries the worst tone of its lines
    vetoed = nv.narrate(dp.build(v2frame(hour=9, warnings=[{"code": "a", "label_cn": "保费突然变厚", "triggered": True},
                                                            {"code": "b", "label_cn": "期限倒挂", "triggered": False}])))
    veto = next(s for s in vetoed["steps"]["T"]["sentences"] if s["text"].startswith("否决层逐项看"))
    assert veto["text"] == "否决层逐项看：保费突然变厚（触发）；期限倒挂（未触发）。" and veto["tone"] == "bad"


def test_x_and_c_lines_fold_into_background():
    n = nv.narrate(dp.build(v2frame(priced())))["steps"]
    assert [b["text"].split("：")[0] for b in n["D2"]["background"]] == ["背景（研究不支持作为依据）"]
    assert "空间位置" in n["D2"]["background"][0]["text"] and not any("空间位置" in s["text"] for s in n["D2"]["sentences"])
    assert n["Q2"]["background"][0]["text"].startswith("背景（研究不支持作为依据）：隐含与实现波动只列数：")
    reading = {"steps": {"D9": {"verdict": None, "lines": [
        {"k": "甲", "v": "读数 1.5", "tone": "warn", "basis": "C · 候选"},
        {"k": "乙", "v": "读数 2", "tone": "", "basis": "A/C · 不是单独的 C"},
        {"k": "丙", "v": "读数 3", "tone": "", "basis": "X · 不再用"},
        {"k": "丁", "v": "读数 4", "tone": "", "basis": "B/C · 混合"}]}}}
    n = nv.narrate(reading)["steps"]["D9"]
    assert [b["text"] for b in n["background"]] == ["背景（候选，未验证）：甲：读数 1.5。", "背景（研究不支持作为依据）：丙：读数 3。"]
    assert [s["text"] for s in n["sentences"]] == ["乙：读数 2。", "丁：读数 4。"]
    assert n["lead"] == "" and n["ask"] is None and n["evidence"] == [] and n["covered"] == [0, 1, 2, 3]
    assert nv.line_grade("X · 17 号") == "X" and nv.line_grade("C · x") == "C"
    # by key: D3's same-source direction line, and T's market state when its backtest showed no difference
    d3 = nv.narrate(dp.build(v2frame()))["steps"]["D3"]
    assert d3["background"][0]["text"].startswith("背景（研究不支持作为依据）：方向读数彼此同源，不计票：量价 24h")
    assert not any("方向读数" in x["text"] for x in d3["sentences"])
    f = v2frame(hour=16, warnings=QUIET_VETOES)
    f["realized_vol"] = {"rv_24h_pct": 20.0, "rv_7d_pct": 30.0}            # 收敛/高于: no difference
    t = nv.narrate(dp.build(f))["steps"]["T"]
    assert any(b["text"].startswith("背景（研究不支持作为依据）：本期状态为波动收敛") for b in t["background"])
    assert not any("本期状态" in x["text"] for x in t["sentences"])
    f["realized_vol"] = {"rv_24h_pct": 60.0, "rv_7d_pct": 40.0}            # 扩张/低于: the marginal state stays
    t = nv.narrate(dp.build(f))["steps"]["T"]
    assert any(x["text"].startswith("本期状态为波动扩张") for x in t["sentences"]) and t["background"] == []
    for basis in ("A/C · x", "B/C · x", "A（方向）/ C（幅度）· 17", "口径 · x", "回测 · x", None, ""):
        assert nv.line_grade(basis) is None


def test_footnotes_are_numbered_once_and_all_referenced():
    for reading in readings():
        for sid, n in nv.narrate(reading)["steps"].items():
            numbers = [f["n"] for f in n["footnotes"]]
            assert numbers == list(range(1, len(numbers) + 1)), sid
            assert len({f["text"] for f in n["footnotes"]}) == len(numbers)
            refs = {r for part in n["sentences"] + n["background"] for r in part["fn"]}
            assert refs == set(numbers), sid
            bases = {l["basis"] for l in (reading["steps"][sid].get("lines") or []) if l.get("basis")}
            assert {f["text"] for f in n["footnotes"]} == bases
    t = nv.narrate(dp.build(v2frame()))["steps"]["T"]
    clock = next(f["n"] for f in t["footnotes"] if f["text"] == dp.B_CLOCK)
    assert sum(clock in s["fn"] for s in t["sentences"]) >= 2          # one footnote, cited by several sentences


ASKS = {
    "D0": ("资料够你判断吗？", [("enough", "够判断", S, False), ("gaps", "有缺口，让我犹豫", D, False)]),
    "D1": ("这样的赔付形状，你能承担吗？", [("ok", "能承担", U, False), ("heavy", "偏重", D, False), ("same", "没影响", S, False)]),
    "T": ("要扛过的这些时段，对你：", [("fine", "可以接受", U, False), ("hesitant", "让我更犹豫", D, False), ("same", "没影响", S, False)]),
    "D2": ("这个缓冲距离，你觉得：", [("enough", "够用", U, False), ("close", "偏近", D, False), ("same", "没影响", S, False)]),
    "D3": ("最近的走势，对你：", [("same", "不改变判断", S, False), ("hesitant", "让我更犹豫", D, False), ("keen", "让我更想做", U, True)]),
    "D4": ("持有期的外生事件，对你：", [("fine", "可以接受", U, False), ("hesitant", "让我更犹豫", D, False), ("same", "没影响", S, False)]),
    "H": ("这个盯盘安排，你做得到吗？", [("yes", "做得到", U, False), ("no", "做不到", D, False), ("same", "没影响", S, False)]),
}


def flat(ask):
    return ask["question"], [(o["key"], o["label"], o["lean"], o["needs_note"]) for o in ask["options"]]


def test_asks_map_each_option_to_exactly_one_lean():
    steps = nv.narrate(dp.build(v2frame(priced())))["steps"]
    for sid, expected in ASKS.items():
        assert flat(steps[sid]["ask"]) == expected, sid
    assert flat(steps["Q1"]["ask"]) == ("没有否决项（这不是做的理由）。继续看价格。", [("go", "继续看价格", S, False)])
    assert flat(steps["Q2"]["ask"]) == ("补偿低于市场估价，你有比市场更有把握的理由吗？",
                                        [("yes", "有（写下理由）", U, True), ("no", "没有", D, False), ("mid", "先挂中间价再看", S, False)])
    veto = nv.narrate(dp.build(v2frame(candidate(spot=82_400.0))))["steps"]
    assert flat(veto["Q1"]["ask"]) == ("有否决项。", [("stop", "按否决，不做", D, False), ("look", "仍想看价格（写下理由）", S, True)])
    above = candidate()
    above["decision_reduction"]["questions"][0]["numbers"]["edge_usd"] = 12.0
    assert flat(nv.narrate(dp.build(v2frame(above)))["steps"]["Q2"]["ask"]) == (
        "补偿不低于市场估价，你有比市场更有把握的理由吗？",
        [("yes", "有（写下理由）", U, True), ("no", "没有", D, False), ("mid", "先挂中间价再看", S, False)])
    missing = candidate()
    missing["decision_reduction"]["questions"] = []
    assert nv.narrate(dp.build(v2frame(missing)))["steps"]["Q2"]["ask"]["question"].startswith("市场估价缺失")
    failed = {"steps": {"Q2": {"verdict": {"code": "error", "cn": "读数失败"}, "lines": [{"k": "说明", "v": "失败"}]},
                        "Q1": {"verdict": {"code": "error", "cn": "读数失败"}, "items": [], "lines": []}}}
    n = nv.narrate(failed)["steps"]
    assert flat(n["Q2"]["ask"]) == ("价格与执行读数失败，请回看前面各步再判断。", [("go", "继续", S, False)])
    assert flat(nv.narrate({"steps": {"Q2": {"verdict": None, "lines": []}}})["steps"]["Q2"]["ask"])[1] == [("go", "继续", S, False)]
    assert n["Q1"]["ask"]["question"].startswith("否决汇总读数失败")
    for reading in readings():
        for sid, n in nv.narrate(reading)["steps"].items():
            if n["ask"] is None:
                assert sid not in ASKS and sid not in {"Q1", "Q2"}
                continue
            keys = [o["key"] for o in n["ask"]["options"]]
            assert len(keys) == len(set(keys)) and all(re.fullmatch(r"[a-z]+", k) for k in keys)
            for o in n["ask"]["options"]:             # every option shown is in the store's table
                assert nv.OPTIONS[sid][o["key"]] == (o["lean"], o["needs_note"])


def test_options_table_is_the_union_of_the_variants():
    assert nv.OPTIONS["Q1"] == {"stop": (D, False), "look": (S, True), "go": (S, False)}
    assert nv.OPTIONS["Q2"] == {"yes": (U, True), "no": (D, False), "mid": (S, False), "go": (S, False)}
    for sid, (_q, options) in ASKS.items():
        assert nv.OPTIONS[sid] == {k: (lean, needs) for k, _l, lean, needs in options}
    assert set(nv.OPTIONS) == set(ASKS) | {"Q1", "Q2", "V", "R", "P", "PL"} and "AU" not in nv.OPTIONS


NEVER_SUPPORT = {"direction_flow", "pin_walls_side", "spatial_position", "move_15m"}


def test_evidence_is_graded_honestly():
    seen = set()
    for reading in readings():
        for sid, n in nv.narrate(reading)["steps"].items():
            for e in n["evidence"]:
                seen.add(e["key"])
                assert set(e) == {"key", "label", "value", "grade", "verdict_cn", "direction", "source"}
                assert e["grade"] in nv.GRADES and e["verdict_cn"] in nv.VERDICTS and e["direction"] in nv.DIRECTIONS
                assert e["value"] is None or isinstance(e["value"], str)
                if e["key"] in NEVER_SUPPORT:
                    assert e["verdict_cn"] == "研究不支持作为依据" and e["direction"] == "neutral", e
                if e["grade"] in ("X", "未检验"):
                    assert e["verdict_cn"] == "研究不支持作为依据"
            if sid not in {"Q2", "D2", "D3", "T", "V", "R", "P"}:
                assert n["evidence"] == []
            assert {e["key"] for e in n["evidence"]} <= nv.EVIDENCE_KEYS.get(sid, frozenset())
    assert {"execution_mid", "events_priced", "iv_vs_rv24", "win_loss_asymmetry", "cushion_vs_standard",
            "early_touch", "busy_window_note", "entry_hour_1600", "market_state"} | NEVER_SUPPORT <= seen
    q2 = {e["key"]: e for e in nv.narrate(dp.build(v2frame(priced(), hour=16)))["steps"]["Q2"]["evidence"]}
    assert q2["execution_mid"]["value"].startswith("按买卖价成交比按 mark 少收约 33 美元")
    assert q2["execution_mid"]["direction"] == "underwrite" and q2["win_loss_asymmetry"]["direction"] == "decline"
    assert q2["win_loss_asymmetry"]["value"].startswith("0.0229 BTC")
    assert q2["entry_hour_1600"]["direction"] == "underwrite"             # 16 UTC is the better entry hour
    assert q2["iv_vs_rv24"]["direction"] == "decline"                     # IV 40 vs RV24 20: far above (17 §3.3, C)
    assert q2["pin_walls_side"]["value"].startswith("本期 Put Gamma 墙 83,000")
    assert q2["market_state"]["verdict_cn"] == "研究不支持作为依据"          # 收敛/高于: no difference in 21 §6
    plain = {e["key"] for e in nv.narrate(dp.build(frame(priced())))["steps"]["Q2"]["evidence"]}
    assert not plain & {"market_state", "entry_hour_1600"}               # no T lines without a session clock


def test_cushion_is_a_candidate_and_the_busy_note_is_neutral():
    d2 = {e["key"]: e for e in nv.narrate(dp.build(v2frame()))["steps"]["D2"]["evidence"]}
    assert (d2["cushion_vs_standard"]["grade"], d2["cushion_vs_standard"]["verdict_cn"]) == ("C", "候选，未验证")
    assert d2["cushion_vs_standard"]["direction"] == "neutral"
    narrow = {e["key"]: e for e in nv.narrate(dp.build(v2frame(candidate(spot=82_600.0))))["steps"]["D2"]["evidence"]}
    assert narrow["cushion_vs_standard"]["direction"] == "decline"
    t = {e["key"]: e for e in nv.narrate(dp.build(v2frame(hour=12, warnings=QUIET_VETOES, notes=[BUSY_NOTE])))["steps"]["T"]["evidence"]}
    assert t["busy_window_note"]["direction"] == "neutral" and t["busy_window_note"]["grade"] == "B"


def test_market_state_evidence_is_marginal_only_for_the_passing_state():
    f = v2frame(hour=16, warnings=[{"code": "x", "label_cn": "期限倒挂", "triggered": False}])
    f["realized_vol"] = {"rv_24h_pct": 60.0, "rv_7d_pct": 40.0}
    t = {e["key"]: e for e in nv.narrate(dp.build(f))["steps"]["T"]["evidence"]}
    assert t["market_state"]["verdict_cn"] == "边缘" and t["market_state"]["grade"] == "C"
    f["realized_vol"] = {"rv_24h_pct": 20.0, "rv_7d_pct": 30.0}
    t = {e["key"]: e for e in nv.narrate(dp.build(f))["steps"]["T"]["evidence"]}
    assert t["market_state"]["verdict_cn"] == "研究不支持作为依据"


def test_narrative_of_old_and_empty_readings():
    assert nv.narrate(None) is None
    v1 = {"steps": {"D5": {"verdict": {"code": "x", "cn": "变化"}, "lines": [{"k": "现价", "v": "83,000"}]}}}
    n = nv.narrate(v1)["steps"]["D5"]
    assert n["sentences"] == [{"text": "现价：83,000。", "fn": [], "tone": "", "keys": ["现价"]}] and n["ask"] is None and n["lead"] == "变化。"


# ------------------------------------------------------------------ store

def started(tmp_path):
    """A session of dp-2026-10-26.3: its steps, options, evidence and notes keep working."""
    s = store.DecisionPathStore(tmp_path)
    vid = s.start(candidate=candidate(), reading=dp.build(frame()), now_ms=NOW)[0]["verification_id"]
    return as_v2_session(tmp_path, vid), vid


def test_store_keeps_choices_and_notes(tmp_path):
    s, vid = started(tmp_path)
    assert "notes" not in s.get(vid)                                      # old-shaped sessions keep working
    s.step(vid, "D3", lean=U, choice="keen", note="  早段回落，我认为  ",
           reasons=["direction_flow", {"key": "early_touch"}, "direction_flow"], now_ms=NOW + 1)
    got = s.get(vid)
    assert got["marks"]["D3"] == {"lean": U, "at_ms": NOW + 1, "choice": "keen"}
    cited = [{"key": "direction_flow", "grade": "X", "verdict_cn": "研究不支持作为依据",
              "label": "量价、主动成交朝有利方向（背离责任区）"},
             {"key": "early_touch", "grade": "A", "verdict_cn": "研究支持",
              "label": "早段被触及后约四分之一走到保护腿（方向 A；幅度 C）"}]
    assert got["notes"]["D3"] == {"text": "早段回落，我认为", "reasons": cited, "at_ms": NOW + 1}
    s.step(vid, "D2", lean=D, now_ms=NOW + 2)
    assert s.get(vid)["marks"]["D2"] == {"lean": D, "at_ms": NOW + 2}
    # note-only update: the mark stays, reasons stay when not given
    s.step(vid, "D3", lean=None, note="改写", now_ms=NOW + 3)
    got = s.get(vid)
    assert got["marks"]["D3"]["choice"] == "keen" and got["notes"]["D3"]["text"] == "改写"
    assert got["notes"]["D3"]["reasons"] == cited
    s.step(vid, "Q1", lean=None, note="只写备注", now_ms=NOW + 4)
    assert "Q1" not in s.get(vid)["marks"] and s.get(vid)["notes"]["Q1"]["reasons"] == []
    assert "Q1" not in s.get(vid)["opened"]                               # a note does not open the step
    s.step(vid, "Q1", lean=None, note="", now_ms=NOW + 5)                 # empty note clears it
    assert "Q1" not in s.get(vid)["notes"]
    # a needs-note option is accepted against an existing note; an evidence key the start never offered grades null
    s.step(vid, "Q2", lean=U, choice="yes", note="挂中间价", reasons=["market_state"], now_ms=NOW + 6)
    assert s.get(vid)["notes"]["Q2"]["reasons"] == [{"key": "market_state", "grade": None, "verdict_cn": None, "label": None}]
    s.step(vid, "Q2", lean=U, choice="yes", now_ms=NOW + 7)
    s.conclude(vid, "decline", now_ms=NOW + 9)
    rec = s.records()[-1]
    assert rec["choices"] == {"D3": "keen", "Q2": "yes"} and set(rec["notes"]) == {"D3", "Q2"}
    assert rec["notes"]["D3"]["text"] == "改写" and rec["notes"]["D3"]["reasons"] == cited


def test_needs_note_options_require_a_note(tmp_path):
    s, vid = started(tmp_path)
    for kwargs in ({}, {"note": "   "}, {"note": ""}):
        with pytest.raises(store.DecisionPathError) as exc:
            s.step(vid, "D3", lean=U, choice="keen", now_ms=NOW + 1, **kwargs)
        assert (exc.value.code, exc.value.status) == ("note_required", 400)
    assert "D3" not in s.get(vid)["marks"] and "D3" not in s.get(vid)["opened"]
    s.step(vid, "Q1", lean=S, choice="look", note="仍想看", now_ms=NOW + 2)
    assert s.get(vid)["marks"]["Q1"] == {"lean": S, "at_ms": NOW + 2, "choice": "look"}


def test_old_notes_with_string_reasons_still_read_and_update(tmp_path):
    s, vid = started(tmp_path)
    path = tmp_path / "sessions" / f"{vid}.json"
    session = json.loads(path.read_text(encoding="utf-8"))
    session["notes"] = {"D2": {"text": "旧", "reasons": ["pin_walls_side"], "at_ms": NOW}}
    path.write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
    s = store.DecisionPathStore(tmp_path)
    s.step(vid, "D2", lean=None, note="新", now_ms=NOW + 1)
    assert s.get(vid)["notes"]["D2"]["reasons"] == ["pin_walls_side"]
    s.conclude(vid, "decline", now_ms=NOW + 2)
    assert s.records()[-1]["notes"]["D2"]["text"] == "新"


def test_notes_on_a_step_that_takes_no_marks(tmp_path):
    s, vid = started(tmp_path)
    path = tmp_path / "sessions" / f"{vid}.json"
    session = json.loads(path.read_text(encoding="utf-8"))
    session["steps"] = [st if st["id"] != "AU" else {"id": "AU", "on_demand": True} for st in session["steps"]]
    path.write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
    s = store.DecisionPathStore(tmp_path)
    with pytest.raises(store.DecisionPathError):
        s.step(vid, "AU", lean=U, now_ms=NOW + 1)
    s.step(vid, "AU", lean=None, note="模型没看到 Pin", now_ms=NOW + 2)
    assert s.get(vid)["notes"]["AU"]["text"] == "模型没看到 Pin"


@pytest.mark.parametrize("kwargs", [
    {"lean": None, "choice": "enough"},                   # a choice belongs to a mark
    {"lean": D, "choice": "enough"},                      # D2 enough is toward_underwrite
    {"lean": U, "choice": "keen"},                        # not a D2 option
    {"lean": U, "choice": "x" * 33}, {"lean": U, "choice": "选项"}, {"lean": U, "choice": "enough\n"},
    {"lean": U, "choice": 3}, {"lean": U, "choice": ["enough"]},
    {"lean": None, "note": "长" * 301}, {"lean": None, "note": 5}, {"lean": None, "note": "坏\ud800"},
    {"lean": None, "reasons": "pin_walls_side"}, {"lean": None, "reasons": ["pin_walls_side"] * 9},
    {"lean": None, "reasons": ["early_touch"]},           # D3's evidence, not D2's
    {"lean": None, "reasons": ["x" * 41]}, {"lean": None, "reasons": ["理由"]}, {"lean": None, "reasons": [1]},
    {"lean": None, "reasons": [{"key": 1}]}, {"lean": None, "reasons": [{"grade": "A"}]},
])
def test_bad_choices_notes_and_reasons_are_refused(tmp_path, kwargs):
    s, vid = started(tmp_path)
    with pytest.raises(store.DecisionPathError) as exc:
        s.step(vid, "D2", now_ms=NOW + 1, **kwargs)
    assert exc.value.code == "bad_step_request" and exc.value.status == 400
    assert "D2" not in s.get(vid)["marks"] and "notes" not in s.get(vid) and "D2" not in s.get(vid)["opened"]
    assert not list((tmp_path / "sessions").glob("*.tmp"))


def test_choices_on_steps_without_options_are_refused(tmp_path):
    s, vid = started(tmp_path)
    with pytest.raises(store.DecisionPathError) as exc:
        s.step(vid, "AU", lean=U, choice="yes", now_ms=NOW + 1)
    assert exc.value.code == "bad_step_request"
    with pytest.raises(store.DecisionPathError):
        s.step(vid, "D0", lean=None, reasons=["pin_walls_side"], now_ms=NOW + 1)   # D0 offers no evidence


def test_a_note_of_300_characters_after_trimming_is_kept(tmp_path):
    s, vid = started(tmp_path)
    s.step(vid, "D2", lean=None, note="  " + "长" * 300 + "  ", reasons=["pin_walls_side"] * 2 + ["spatial_position"],
           now_ms=NOW + 1)
    note = s.get(vid)["notes"]["D2"]
    assert len(note["text"]) == 300 and [r["key"] for r in note["reasons"]] == ["pin_walls_side", "spatial_position"]
    assert note["reasons"][0]["grade"] == "未检验" and note["reasons"][1]["grade"] == "X"


# ------------------------------------------------------------------ workbench

def test_served_sessions_carry_the_narrative_and_the_file_does_not(tmp_path):
    wb, _, snap = setup_wb(tmp_path)
    out = wb.decision_path_start(snap["candidate_id"], snap["snapshot_id"], authorization="Bearer secret")
    vid = out["session"]["verification_id"]
    assert out["session"]["readings"]["start"]["narrative"]["schema"] == nv.SCHEMA
    refreshed = wb.decision_path_refresh(vid, authorization="Bearer secret")["session"]
    assert refreshed["readings"]["now"]["narrative"]["steps"]["R"]["ask"]["question"] == "这些风险，你愿意扛吗？"
    with pytest.raises(workbench.WorkbenchError) as exc:
        wb.decision_path_step(vid, "R", "toward_decline", authorization="Bearer wrong", choice="heavy")
    assert exc.value.code == "unauthorized"
    stepped = wb.decision_path_step(vid, "R", "toward_decline", authorization="Bearer secret", choice="heavy",
                                    note="偏近", reasons=["cushion_vs_standard"])["session"]
    assert stepped["marks"]["R"]["choice"] == "heavy" and stepped["notes"]["R"]["text"] == "偏近"
    assert stepped["notes"]["R"]["reasons"][0]["key"] == "cushion_vs_standard"
    with pytest.raises(workbench.WorkbenchError) as exc:
        wb.decision_path_step(vid, "P", "toward_underwrite", authorization="Bearer secret", choice="below")
    assert (exc.value.code, exc.value.status_code) == ("note_required", 400)
    assert isinstance(stepped["readings"]["now"]["narrative"]["steps"]["R"]["changes"], list)
    assert "changes" not in stepped["readings"]["start"]["narrative"]["steps"]["R"]
    assert "narrative" in stepped["readings"]["start"]
    with pytest.raises(workbench.WorkbenchError) as exc:
        wb.decision_path_step(vid, "R", None, authorization="Bearer secret", reasons="x")
    assert exc.value.code == "bad_step_request" and exc.value.status_code == 400
    assert "narrative" in wb.decision_path_session(vid)["readings"]["now"]
    raw = (tmp_path / "state" / "decision_path" / "sessions" / f"{vid}.json")
    raw = raw if raw.exists() else next(tmp_path.rglob(f"{vid}.json"))
    assert "narrative" not in raw.read_text(encoding="utf-8")
    done = wb.decision_path_conclude(vid, "decline", "", authorization="Bearer secret")["session"]
    assert "narrative" in done["readings"]["start"]
    assert "narrative" not in raw.read_text(encoding="utf-8")


def test_a_broken_narrative_leaves_the_session_served(monkeypatch):
    def boom(reading):
        raise RuntimeError("x")
    monkeypatch.setattr(workbench.decision_path_narrative, "narrate", boom)
    session = {"readings": {"start": {"steps": {}}, "now": None}}
    served = workbench._dp_with_narrative(session)
    assert served == session and served is not session


def test_http_step_passes_choice_note_and_reasons(tmp_path):
    wb, _, snap = setup_wb(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), workbench.make_handler(wb))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}/api/workbench/decision-path"

    def post(url, body):
        req = Request(url, data=json.dumps(body).encode(), method="POST",
                      headers={"Authorization": "Bearer secret", "Content-Type": "application/json"})
        return json.loads(urlopen(req, timeout=20).read())
    try:
        vid = post(base, {"candidate_id": snap["candidate_id"], "snapshot_id": snap["snapshot_id"]})["session"]["verification_id"]
        out = post(f"{base}/{vid}/step", {"step": "P", "lean": U, "choice": "below", "note": "理由", "reasons": ["direction_flow"]})
        assert out["session"]["marks"]["P"]["choice"] == "below"
        assert out["session"]["notes"]["P"]["text"] == "理由"
        assert out["session"]["notes"]["P"]["reasons"][0]["key"] == "direction_flow"
        req = Request(f"{base}/{vid}/step", data=json.dumps({"step": "V", "lean": S, "choice": "look"}).encode(),
                      method="POST", headers={"Authorization": "Bearer secret", "Content-Type": "application/json"})
        with pytest.raises(HTTPError) as exc:
            urlopen(req, timeout=20)
        assert exc.value.code == 400 and json.loads(exc.value.read())["code"] == "note_required"
        got = json.loads(urlopen(f"{base}/{vid}", timeout=20).read())
        options = got["readings"]["start"]["narrative"]["steps"]["P"]["ask"]["options"]
        assert next(o for o in options if o["key"] == "below")["needs_note"] is True
    finally:
        server.shutdown()


# ------------------------------------------------------------------ now vs start

def test_changes_pair_lines_by_key_in_order():
    start = dp.build(v2frame(hour=9, warnings=[{"code": "a", "label_cn": "保费突然变厚", "triggered": False},
                                                {"code": "b", "label_cn": "期限倒挂", "triggered": False}]))
    moved = candidate(spot=83_000.0)
    moved["spatial_position"] = {}
    now = dp.build(v2frame(moved, hour=9, warnings=[{"code": "a", "label_cn": "保费突然变厚", "triggered": True},
                                                     {"code": "b", "label_cn": "期限倒挂", "triggered": False}]))
    n = nv.narrate(now, start)["steps"]
    plain = nv.narrate(now)["steps"]
    assert all("changes" not in step for step in plain.values())
    # 否决层 repeats: paired in order, the joined sentence is reported once
    t = n["T"]["changes"]
    assert [c["kind"] for c in t] == ["changed"] and t[0]["text"] == "否决层逐项看：保费突然变厚（触发）；期限倒挂（未触发）。"
    assert t[0]["fn"] == next(s["fn"] for s in n["T"]["sentences"] if s["text"] == t[0]["text"])
    d2 = n["D2"]["changes"]
    cushion = next(c for c in d2 if c["text"].startswith("缓冲"))
    assert cushion["kind"] == "changed" and cushion["fn"] == [1]
    assert [c["kind"] for c in d2][-1] == "removed"                       # removed come last
    # the X background line of the start reading is gone now
    assert d2[-1] == {"kind": "removed", "fn": [],
                             "text": "不再有：背景（研究不支持作为依据）：周度 / 0DTE 墙构成的空间位置，程序判读为相对不利；不作缓冲，也不计入否决。"}
    assert n["D1"]["changes"] == [] and n["AU"]["changes"] == []
    assert nv.narrate(start, start)["steps"]["D2"]["changes"] == []
    added = nv.narrate(start, {"steps": {"D2": {"lines": []}}})["steps"]
    assert {c["kind"] for c in added["D2"]["changes"]} == {"added"} and len(added["D2"]["changes"]) == len(added["D2"]["sentences"]) + len(added["D2"]["background"])
    assert {c["kind"] for c in added["T"]["changes"]} == {"added"}         # a step the start reading did not have


def test_clock_only_movements_are_not_changes():
    from tools import astra_decision_path_narrative_v1 as narrative
    assert narrative._settled("两腿盘口顺序估算，164 秒前") == narrative._settled("两腿盘口顺序估算，225 秒前")
    assert narrative._settled("美股开盘，6.8 小时后") == narrative._settled("美股开盘，6.7 小时后")
    assert narrative._settled("距到期还剩 25.3 小时") == narrative._settled("距到期还剩 25.2 小时")
    assert narrative._settled("约 0.48 倍到期前预期波动") != narrative._settled("约 0.47 倍到期前预期波动")
    assert narrative._settled("净收 177 美元") != narrative._settled("净收 175 美元")


# ------------------------------------------------------------------ the decision chain (dp-2026-10-27.1)

from test_astra_decision_path import INVERSION, chain_frame  # noqa: E402


def chain_readings():
    return [dp.build(f) for f in (
        chain_frame(), chain_frame([INVERSION, QUIET_VETOES[1]], {"bid": 0.006, "ask": 0.0066}, {"bid": 0.0, "ask": 0.0008}),
        chain_frame(None, {"bid": 0.0058, "ask": 0.0062}, {"bid": 0.0034, "ask": 0.0038}),
        chain_frame(cand=candidate(spot=82_400.0)), v2frame(), frame(), {"at_ms": NOW})]


def test_composite_lines_reach_a_sentence_or_the_background_by_their_component():
    for reading in chain_readings():
        n = nv.narrate(reading)["steps"]
        for sid in ("V", "R", "P", "PL"):
            lines = reading["steps"][sid]["lines"]
            assert n[sid]["covered"] == list(range(len(lines))), sid
            body = " ".join([s["text"] for s in n[sid]["sentences"]] + [b["text"] for b in n[sid]["background"]])
            told, where = nv._narrate_step(sid, reading["steps"][sid], reading["steps"])
            for i, line in enumerate(lines):
                for number in NUM.findall(line["v"]):
                    assert number in body, (sid, line, number)
                in_background = any(where[i] is b for b in told["background"])
                assert in_background == (nv.background_grade(sid, line) in ("X", "C")), (sid, line)
    n = nv.narrate(dp.build(chain_frame()))["steps"]
    # graded as its component: D3's same-source direction line, D2's spatial position, Q2's IV/RV line
    assert any(b["text"].startswith("背景（研究不支持作为依据）：方向读数彼此同源") for b in n["R"]["background"])
    assert any("空间位置" in b["text"] for b in n["R"]["background"])
    assert any(b["text"].startswith("背景（研究不支持作为依据）：隐含与实现波动只列数") for b in n["P"]["background"])
    # templates by component: D2's cushion, the contract, the reservation price, H's "若…"
    assert any(s["text"].startswith("缓冲在标准附近：短腿在现价下方") for s in n["R"]["sentences"])
    assert n["R"]["sentences"][0]["text"].startswith("这张保单：到期价跌破 82,500 美元开始赔；")
    assert any(s["text"].startswith("保留价是 0.0029 BTC") for s in n["P"]["sentences"])
    assert any(s["text"].startswith("若此刻被触及") for s in n["PL"]["sentences"])
    assert n["V"]["sentences"][-1]["text"].startswith("逐项检查：资料 可读；")
    assert n["R"]["lead"].startswith("风险：缓冲标准附近") and n["V"]["lead"] == "未见否决项（这不是做的理由）。"


def test_composite_asks_and_their_leans():
    def ask(f, sid):
        return nv.narrate(dp.build(f))["steps"][sid]["ask"]
    veto = ask(chain_frame([INVERSION]), "V")
    assert flat(veto) == ("这些否决项，你怎么处理？", [("stop", "按否决，不做", D, False), ("look", "仍想看价格（写下理由）", S, True)])
    caution = ask(chain_frame(None, {"bid": 0.0058, "ask": 0.0062}, {"bid": 0.0034, "ask": 0.0038}), "V")
    assert flat(caution) == ("这些提醒，对你：", [("noted", "知道了，继续", S, False), ("deter", "让我不想做", D, False)])
    assert ask(chain_frame(), "V") is None                                # no veto, no caution: no question
    assert flat(ask(chain_frame(), "R")) == ("这些风险，你愿意扛吗？", [("accept", "愿意扛", U, False), ("heavy", "偏重", D, False),
                                                                   ("same", "没影响", S, False)])
    assert flat(ask(chain_frame(), "P")) == ("你打算怎么挂单？", [
        ("reserve", "按保留价或更高挂单", U, False), ("below", "低于保留价也愿意做（写下理由）", U, True),
        ("pass", "价格不够，不做", D, False), ("later", "先不定", S, False)])
    missing = candidate()
    missing["decision_reduction"]["questions"] = []
    assert flat(ask(v2frame(missing), "P"))[1][0] == ("below", "仍愿意做（写下理由）", U, True)
    assert flat(ask(chain_frame(), "PL")) == ("若持有中短腿被触及，你会：", [
        ("hold", "持有到期", S, False), ("close", "触及即平仓", S, False), ("half", "平一半", S, False),
        ("away", "这段时间盯不了", D, False)])
    failed = nv.narrate({"steps": {"V": {"verdict": {"code": "error", "cn": "读数失败"}, "items": [], "lines": [{"k": "说明", "v": "x"}]},
                                   "P": {"verdict": {"code": "error", "cn": "读数失败"}, "lines": [{"k": "说明", "v": "x"}]}}})["steps"]
    assert flat(failed["V"]["ask"])[1] == [("go", "继续", S, False)] and flat(failed["P"]["ask"])[1] == [("go", "继续", S, False)]
    assert nv.OPTIONS["V"] == {"stop": (D, False), "look": (S, True), "noted": (S, False), "deter": (D, False), "go": (S, False)}
    assert nv.OPTIONS["P"] == {"reserve": (U, False), "below": (U, True), "pass": (D, False), "later": (S, False), "go": (S, False)}
    assert nv.OPTIONS["PL"] == {"hold": (S, False), "close": (S, False), "half": (S, False), "away": (D, False)}
    assert nv.option_label("PL", "half") == "平一半" and nv.option_label("P", "below") == "低于保留价也愿意做（写下理由）"
    assert nv.option_label("PL", "zz") is None and nv.option_label("ZZ", "half") is None
    for reading in chain_readings():
        for sid, n in nv.narrate(reading)["steps"].items():
            for o in (n["ask"] or {}).get("options") or []:
                assert nv.OPTIONS[sid][o["key"]] == (o["lean"], o["needs_note"])


def test_composite_evidence_is_its_components_and_graded_honestly():
    n = nv.narrate(dp.build(chain_frame(cand=candidate(spot=82_600.0), hour=16)))["steps"]
    for sid in ("V", "R", "P"):
        keys = [e["key"] for e in n[sid]["evidence"]]
        assert keys and len(keys) == len(set(keys)) and set(keys) <= nv.EVIDENCE_KEYS[sid], sid
        for e in n[sid]["evidence"]:
            if e["key"] in NEVER_SUPPORT:
                assert e["verdict_cn"] == "研究不支持作为依据" and e["direction"] == "neutral"
    v = {e["key"]: e for e in n["V"]["evidence"]}
    assert set(v) == {"cushion_vs_standard", "execution_mid"} and v["cushion_vs_standard"]["direction"] == "decline"
    assert {e["key"] for e in n["P"]["evidence"]} == {e["key"] for e in n["Q2"]["evidence"]}
    assert {"cushion_vs_standard", "direction_flow", "entry_hour_1600", "events_priced"} <= {e["key"] for e in n["R"]["evidence"]}
    assert n["PL"]["evidence"] == [] and "PL" not in nv.EVIDENCE_KEYS


def test_composite_notes_choices_and_needs_note_in_the_store(tmp_path):
    s = store.DecisionPathStore(tmp_path)
    vid = s.start(candidate=candidate(), reading=dp.build(chain_frame([INVERSION])), now_ms=NOW)[0]["verification_id"]
    with pytest.raises(store.DecisionPathError) as exc:
        s.step(vid, "V", lean=S, choice="look", now_ms=NOW + 1)
    assert exc.value.code == "note_required"
    s.step(vid, "V", lean=S, choice="look", note="倒挂幅度小", now_ms=NOW + 2)
    s.step(vid, "R", lean=U, choice="accept", reasons=["cushion_vs_standard", "events_priced"], now_ms=NOW + 3)
    s.step(vid, "P", lean=U, choice="below", note="有理由", reasons=["execution_mid"], now_ms=NOW + 4)
    for bad in ({"lean": U, "choice": "stop"}, {"lean": D, "choice": "half", "step": "PL"}, {"lean": None, "reasons": ["move_15m"]},
                {"lean": U, "choice": "yes", "step": "P"}):
        step = bad.pop("step", "V")
        with pytest.raises(store.DecisionPathError):
            s.step(vid, step, now_ms=NOW + 5, **bad)
    got = s.get(vid)
    assert {k: m["choice"] for k, m in got["marks"].items()} == {"V": "look", "R": "accept", "P": "below"}
    assert [r["grade"] for r in got["notes"]["R"]["reasons"]] == ["C", "B"]


def test_changes_of_composite_steps():
    start = dp.build(chain_frame())
    now = dp.build(chain_frame([INVERSION], cand=candidate(spot=83_000.0)))
    n = nv.narrate(now, start)["steps"]
    assert any(c["kind"] == "added" and c["text"].startswith("近端倒挂：") for c in n["V"]["changes"])
    assert any(c["kind"] == "changed" and c["text"].startswith("逐项检查") for c in n["V"]["changes"])
    assert any(c["kind"] == "changed" and c["text"].startswith("缓冲") for c in n["R"]["changes"])
    assert nv.narrate(start, start)["steps"]["P"]["changes"] == []
