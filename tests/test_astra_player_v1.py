"""AI player shadow track (docs/opus/35)."""
import gzip
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import pytest

import astra_player_v1 as pl
import astra_research_archive_v1 as ar
import astra_research_selection_v1 as sel

ROOT = Path(__file__).resolve().parents[1]
H = 3_600_000
M = 60_000
E = 1_794_470_400_000                 # 2026-11-12 08:00 UTC
R1, R2 = E - 30 * H, E - 18 * H
SCHEMAS = ROOT / "deploy" / "research_archive" / "schemas"
PRICING = {"reservation_btc": 0.0040, "mark_spread_btc": 0.0034, "fee_btc": 0.0006, "visible_credit_btc": 0.0033,
           "mid_credit_btc": 0.0038, "bid_gap_btc": 0.0007, "mid_gap_btc": 0.0002, "reservation_reliable": True,
           "required_edge_bid": 0.2059, "required_edge_mid": 0.0588, "required_edge_note_cn": None,
           "max_loss_btc": 0.0222, "max_loss_usd": 1790.0}


def _case(cid="cf_p", side="put", built=R1 + 10_000, eligible=True, expiry=E):
    entries = [("L1", "L1.contract", {"side": side, "short_instrument": "BTC-12NOV26-79000-P",
                                      "long_instrument": "BTC-12NOV26-77000-P", "expiry_ms": expiry, "quantity_btc": 1.0}),
               ("L2", "L2.quote", {"short_bid": 0.0061, "long_ask": 0.0028}),
               ("L3", "L3.expiry_walls", {"pin": 80500.0}),
               ("L6", "L6.session_clock", {"remaining_variance_share": 0.62}),
               ("L8", "L8.guidance", {"notes": ["旧的程序解读"]}),
               ("L8", "L8.signal_recommendation", {"signal": "Neutral"})]
    layers, refs = {}, {}
    for layer, ref, values in entries:
        layers.setdefault(layer, []).append({"ref": ref, "label_cn": ref, "values": values})
        refs[ref] = {"layer": layer, "index": len(layers[layer]) - 1}
    return {"case_id": cid, "asof_ms": built - 20_000, "build": {"built_at_ms": built}, "layers": layers, "refs": refs,
            "selection": {"schema": "selection@0.1", "path_version": "dp-test", "eligible": eligible, "vetoes": [],
                          "cautions": [{"code": "mid_short", "title_cn": "按中间价成交仍低于保留价"}],
                          "unknown_cn": ["本期各行权价的成交方向"], "pricing": dict(PRICING),
                          "round": {"current": 1 if expiry - built >= 24 * H else 2, "pass_cost": {"premium_btc": 0.0011, "premium_usd": 89.0,
                                                                "variance_share": 0.41, "note_cn": "粗略近似"}}}}


def _reject(**over):
    d = {"action": "reject", "reject_reason": "pricing_unfavourable", "deviation_types": [], "limit_btc": None,
         "valid_minutes": None, "required_edge": None, "limit_minus_reservation_btc": None,
         "below_reservation_why": None, "pass_cost": None, "next_round_needs": None,
         "basis": [{"ref": "本轮", "reading": "所需优势按对手价 0.2059", "grade": "-"}],
         "counter": "剩余风险占比 0.62 仍高", "invalid_if": "现价回到短腿外更远处", "unknown": ["成交方向"]}
    d.update(over)
    return d


def _accept(**over):
    fields = dict(action="accept", reject_reason=None, deviation_types=["fill"], limit_btc=0.0038, valid_minutes=90,
                  required_edge=0.0588, limit_minus_reservation_btc=-0.0002,
                  below_reservation_why="中间价附近的限价，愿付 0.0002 的摩擦",
                  edge_why="按中间价成交的所需优势 0.0588 很小，日内剩余风险占比 0.62 与保费衰减相当",
                  basis=[{"ref": "本轮", "reading": "按中间价毛信用 0.0038", "grade": "-"}])
    fields.update(over)
    return _reject(**fields)


def _transport(decision, finish="stop", calls=None):
    def post(key, model, request, timeout, endpoint):
        if calls is not None:
            calls.append(request)
        return {"model": "deepseek-flash", "usage": {"total_tokens": 1}, "choices": [
            {"message": {"content": json.dumps(decision, ensure_ascii=False)}, "finish_reason": finish}]}
    return post


# ---------------------------------------------------------------- rulebook and view


def test_prompt_is_the_rulebook_word_for_word():
    doc = next((ROOT / "docs" / "opus").glob("27_*.md")).read_text(encoding="utf-8").replace("\r\n", "\n")
    body = doc[doc.index("## 4. 你的角色"):doc.index("# 第三部分 尚未落地的部分")].rstrip().rstrip("-").rstrip() + "\n"
    assert pl.rulebook().replace("\r\n", "\n") == body
    assert "判断顺序" in body and "程序的否决层目前没有这一条" not in body and "有人急需" not in body
    assert "基本打法是承保" in body and "基本打法是拒绝" not in body


def test_view_drops_strategy_advice_and_selection_and_adds_the_round_block():
    view = pl.player_view(_case(), "r1")
    text = json.dumps(view, ensure_ascii=False)
    assert "L8.guidance" not in view["refs"] and "L8.signal_recommendation" not in view["refs"]
    assert "旧的程序解读" not in text and "Neutral" not in text and "selection" not in view
    block = view["本轮"]
    assert block["所需优势_按对手价"] == 0.2059 and block["保留价_btc"] == 0.004
    assert block["过到下一轮的代价"]["这段时间走掉的风险占比"] == 0.41 and "第一轮你的动作" not in block
    r2 = pl.player_view(_case(built=R2 + 10_000), "r2", {"action": "pass", "contract_key": "k", "next_round_needs": "偏斜回落"})
    assert "过到下一轮的代价" not in r2["本轮"] and "偏斜回落" in r2["本轮"]["第一轮你的动作"]


def test_request_carries_the_rulebook_as_system_prompt():
    calls = []
    pl.run(_case(), rnd="r1", api_key="k", model="deepseek-flash", transport=_transport(_reject(), calls=calls))
    req = calls[0]
    assert req["messages"][0] == {"role": "system", "content": pl.rulebook()}
    assert req["_local_prompt_version"] == pl.PROMPT_VERSION and '"action"' in req["messages"][1]["content"]


# ---------------------------------------------------------------- validation


def test_valid_reject_and_accept():
    for d in (_reject(), _accept()):
        r = pl.run(_case(), rnd="r1", api_key="k", model="m", transport=_transport(d))
        assert r["status"] == "OK", r["issues"]
        assert r["rulebook"] == pl.RULEBOOK_VERSION and r["decision"]["action"] == d["action"]


@pytest.mark.parametrize("decision, rnd, code", [
    (_accept(required_edge=0.2), "r1", "required_edge_mismatch"),           # recomputed: (0.0040-0.0038)/0.0034
    (_accept(limit_minus_reservation_btc=0.0), "r1", "limit_gap_mismatch"),
    (_accept(valid_minutes=30), "r1", "valid_minutes_invalid"),
    (_accept(below_reservation_why=None), "r1", "below_reservation_unexplained"),
    (_accept(deviation_types=["vibes"]), "r1", "deviation_type_invalid"),
    (_reject(reject_reason="vibes"), "r1", "reject_reason_invalid"),
    (_accept(edge_why=""), "r1", "accept_without_edge_why"),
    (_reject(basis=[]), "r1", "basis_count"),
    (_reject(basis=[{"ref": "L9.nope", "reading": "x", "grade": "-"}]), "r1", "basis_ref_invalid"),
    (_reject(counter=""), "r1", "missing_counter"),
    (_reject(action="pass", reject_reason=None, pass_cost="0.0011", next_round_needs="偏斜回落"), "r2", "pass_outside_round_1"),
    (_reject(action="pass", reject_reason=None), "r1", "pass_without_cost_or_needs"),
    (_reject(action="hold"), "r1", "action_invalid"),
])
def test_each_rule_breach_is_invalid_never_rewritten(decision, rnd, code):
    calls = []
    r = pl.run(_case(), rnd=rnd, api_key="k", model="m", transport=_transport(decision, calls=calls))
    assert r["status"] == "INVALID" and code in {i["code"] for i in r["issues"]} and len(calls) == 1


def test_fill_reason_needs_a_reliable_reservation():
    case = _case()
    case["selection"]["pricing"]["reservation_reliable"] = False
    r = pl.run(case, rnd="r1", api_key="k", model="m", transport=_transport(_accept()))
    assert "fill_reason_without_reliable_edge" in {i["code"] for i in r["issues"]}


def test_untraced_numbers_are_flagged_not_invalid():
    d = _reject(counter="现价可能跌到 71234 以下")
    r = pl.run(_case(), rnd="r1", api_key="k", model="m", transport=_transport(d))
    assert r["status"] == "OK" and any(i["code"] == "untraced_number_flagged" and "71234" in i["numbers"] for i in r["issues"])
    ok = pl.run(_case(), rnd="r1", api_key="k", model="m", transport=_transport(_accept()))
    assert not any(i["code"] == "untraced_number_flagged" for i in ok["issues"])     # 本轮 and own limit numbers trace


def test_no_answer_is_an_error_not_a_reject():
    def broken(*a):
        raise TimeoutError()
    assert pl.run(_case(), rnd="r1", api_key="k", model="m", transport=broken)["status"] == "ERROR"
    assert pl.run(_case(), rnd="r1", api_key="k", model="m", transport=_transport(_reject(), finish="length"))["status"] == "ERROR"

    def prose(*a):
        return {"choices": [{"message": {"content": "我拒绝"}, "finish_reason": "stop"}]}
    assert pl.run(_case(), rnd="r1", api_key="k", model="m", transport=prose)["status"] == "ERROR"


def test_metrics_report_the_isolation_numbers():
    results = [{"status": "OK", "decision": {"action": "reject"}, "issues": []},
               {"status": "INVALID", "decision": {}, "issues": [{"code": "basis_count"}]}]
    m = pl.metrics(results)
    assert m["valid_share"] == 0.5 and m["by_action"] == {"reject": 1} and m["issue_codes"] == {"basis_count": 1}


# ---------------------------------------------------------------- archive shadow track


def _freeze(root, state, case, hour_key, side):
    import datetime as dt
    built = case["build"]["built_at_ms"]
    day = dt.datetime.fromtimestamp(built / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
    rel = f"cases/{day}/{hour_key[11:]}_{side}_{case['case_id']}.json.gz"
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(gzip.compress(json.dumps(case).encode("utf-8")))
    state.setdefault("cases", {})[f"{hour_key}|{side}"] = {"case_id": case["case_id"], "path": rel}


def _rows(root):
    return [json.loads(l) for f in sorted((root / "decisions").glob("*.jsonl")) for l in f.read_text(encoding="utf-8").splitlines()]


def test_players_run_once_on_eligible_round_cases_and_write_shadow_rows(tmp_path):
    archive = ar.Archive(tmp_path)
    state = archive.load_state()
    _freeze(tmp_path, state, _case("cf_put"), "2026-11-11T02", "put")
    _freeze(tmp_path, state, _case("cf_call", side="call", eligible=False), "2026-11-11T02", "call")
    calls = []

    def run_player(case, rnd, prev):
        calls.append((case["case_id"], rnd, prev))
        return pl.run(case, rnd=rnd, previous=prev, api_key="k", model="deepseek-flash", transport=_transport(_reject()))
    report = {"errors": []}
    assert ar.run_players(archive, state, now_ms=R1 + 15 * M, run_player=run_player, report=report) == 1
    assert calls == [("cf_put", "r1", None)]                                   # the vetoed side never reaches the model
    assert ar.run_players(archive, state, now_ms=R1 + 25 * M, run_player=run_player, report=report) == 0
    row = _rows(tmp_path)[0]
    assert (row["actor"], row["source"], row["action"], row["round"], row["case_id"]) == ("player", "shadow", "reject", "r1", "cf_put")
    assert row["reason_types"] == ["pricing_unfavourable"] and row["model"] == "deepseek-flash"
    assert row["prompt_version"] == pl.PROMPT_VERSION and row["rule_versions"]["rulebook"] == pl.RULEBOOK_VERSION
    assert state["player"] == {"2026-11-11T02|put": "OK", "2026-11-11T02|call": "not_eligible"}
    assert archive.verify()["ok"] is True
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(row, json.loads((SCHEMAS / "decision_event_0.2.schema.json").read_text(encoding="utf-8")))


def test_round_two_sees_round_one_and_never_plays_after_an_accept(tmp_path):
    archive = ar.Archive(tmp_path)
    state = archive.load_state()
    _freeze(tmp_path, state, _case("cf_p1"), "2026-11-11T02", "put")
    _freeze(tmp_path, state, _case("cf_c1", side="call"), "2026-11-11T02", "call")
    first = {"cf_p1": _reject(action="pass", reject_reason=None, pass_cost="0.0011", next_round_needs="偏斜回落"),
             "cf_c1": _accept()}
    seen = []

    def run_player(case, rnd, prev):
        seen.append((case["case_id"], prev))
        d = first.get(case["case_id"], _reject())
        return pl.run(case, rnd=rnd, previous=prev, api_key="k", model="m", transport=_transport(d))
    ar.run_players(archive, state, now_ms=R1 + 15 * M, run_player=run_player, report={"errors": []})
    _freeze(tmp_path, state, _case("cf_p2", built=R2 + 10_000), "2026-11-11T14", "put")
    _freeze(tmp_path, state, _case("cf_c2", side="call", built=R2 + 10_000), "2026-11-11T14", "call")
    ar.run_players(archive, state, now_ms=R2 + 15 * M, run_player=run_player, report={"errors": []})
    assert ("cf_c2", None) not in seen and not any(c == "cf_c2" for c, _ in seen)  # accepted in round 1
    prev = next(p for c, p in seen if c == "cf_p2")
    assert prev["action"] == "pass" and prev["next_round_needs"] == "偏斜回落"
    assert state["player"]["2026-11-11T14|call"] == "accepted_in_round_1"


def test_players_outside_the_window_without_a_key_and_on_failure(tmp_path):
    archive = ar.Archive(tmp_path)
    state = archive.load_state()
    _freeze(tmp_path, state, _case("cf_put"), "2026-11-11T02", "put")
    assert ar.run_players(archive, state, now_ms=R1 + 50 * M, run_player=lambda *a: 1 / 0, report={"errors": []}) == 0
    assert ar.run_players(archive, state, now_ms=R1 + 15 * M, run_player=lambda *a: None, report={"errors": []}) == 0
    assert "player" not in state or "2026-11-11T02|put" not in state["player"]    # no key: the slot stays open
    assert ar.run_players(archive, state, now_ms=R1 + 15 * M, run_player=lambda *a: 1 / 0, report={"errors": []}) == 1
    assert _rows(tmp_path)[0]["action"] == "missing"                              # a failure is missing, not a reject


def test_canary_writes_only_its_out_file(tmp_path):
    archive = ar.Archive(tmp_path)
    state = archive.load_state()
    _freeze(tmp_path, state, _case("cf_a"), "2026-11-11T02", "put")
    _freeze(tmp_path, state, _case("cf_b", side="call", built=R2 + 10_000), "2026-11-11T14", "call")
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    out = tmp_path.parent / "canary.json"
    m = ar.player_canary(tmp_path, limit=10, out=out, run_player=lambda c, r, p: pl.run(
        c, rnd=r, api_key="k", model="m", transport=_transport(_reject())))
    assert m["n"] == 2 and m["valid_share"] == 1.0 and m["all_same_action"] is True
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before
    assert json.loads(out.read_text(encoding="utf-8"))["schema"] == "player_canary@0.1"


def test_scorecard_groups_the_players_actions(tmp_path):
    from test_astra_research_selection_v1 import _write_case, _write_outcome, _write_path, _flat_path, _sample
    rel, c = _write_case(tmp_path, R1, "put", "cf_a")
    _write_outcome(tmp_path, c, [{"case_id": "cf_a", "asof_ms": R1, "path": rel}], 0.0)
    _write_path(tmp_path, _flat_path())
    d = tmp_path / "decisions" / "2026-11-11.jsonl"
    d.parent.mkdir(parents=True)
    d.write_text(json.dumps({"actor": "player", "case_id": "cf_a", "action": "accept"}) + "\n", encoding="utf-8")
    card = sel.build(tmp_path, now_ms=E + H)
    groups = {g["id"]: g for g in card["groups"]}
    assert groups["player_accept"]["n"] == 1 and groups["player_reject"]["n"] == 0
    assert card["coverage"]["player"]["accept"] == 1 and _sample(card)["player_action"] == "accept"
    assert not any(p["id"].startswith("player_accept") and p["id"] == "player_accept" for p in card["pending_groups"])


def test_a_basis_may_cite_several_entries_and_rulebook_numbers_trace():
    d = _reject(basis=[{"ref": "L2.quote / 本轮", "reading": "对手价毛信用 0.0033，所需优势 0.2059", "grade": "-"}],
                counter="缓冲高于 0.25 倍的否决线")
    r = pl.run(_case(), rnd="r1", api_key="k", model="m", transport=_transport(d))
    assert r["status"] == "OK" and not any(i["code"] == "untraced_number_flagged" for i in r["issues"])
    d = _reject(basis=[{"ref": "L2.quote / L9.nope", "reading": "x", "grade": "-"}])
    assert pl.run(_case(), rnd="r1", api_key="k", model="m", transport=_transport(d))["status"] == "INVALID"


def test_format_variants_are_normalised_and_stray_fields_only_flagged():
    d = _accept(valid_minutes=90.0, required_edge=5.88,
                basis=[{"ref": "「本轮」", "reading": "按中间价毛信用 0.0038", "grade": None}])
    r = pl.run(_case(), rnd="r1", api_key="k", model="m", transport=_transport(d))
    codes = {i["code"] for i in r["issues"]}
    assert r["status"] == "OK" and "edge_as_percent_flagged" in codes
    r = pl.run(_case(), rnd="r1", api_key="k", model="m", transport=_transport(_reject(required_edge=0.2059)))
    assert r["status"] == "OK" and any(i["code"] == "stray_fields_flagged" for i in r["issues"])


def test_accept_at_or_above_the_reservation_needs_zero_edge():
    d = _accept(limit_btc=0.0041, required_edge=0.0, limit_minus_reservation_btc=0.0001, below_reservation_why=None)
    assert pl.run(_case(), rnd="r1", api_key="k", model="m", transport=_transport(d))["status"] == "OK"


def test_round_block_keys_are_what_the_rulebook_lists():
    rule = pl.rulebook()
    row = next(l for l in rule.splitlines() if l.startswith("| 本轮 |"))
    for phrase in ("保留价", "两腿标记价差", "费用", "毛信用", "摩擦门槛", "所需优势", "保留价是否可靠", "过到下一轮的代价",
                   "最大损失", "提醒项", "不知道的", "第一轮的动作"):
        assert phrase in row
    keys = " ".join(pl.round_block(_case()["selection"], "r1")) + " ".join(pl.round_block(_case()["selection"], "r2"))
    for phrase in ("保留价", "两腿标记价差", "费用", "毛信用", "摩擦门槛", "所需优势", "保留价可靠", "过到下一轮的代价",
                   "最大损失", "提醒项", "不知道的", "第一轮你的动作"):
        assert phrase in keys


def test_players_skip_cases_the_scorecard_would_drop(tmp_path):
    archive = ar.Archive(tmp_path)
    state = archive.load_state()
    stale = _case("cf_stale")
    stale["asof_ms"] = stale["build"]["built_at_ms"] - 10 * M
    other = _case("cf_other", side="call", expiry=E + 24 * H)          # 54 h left: not a round of this expiry
    _freeze(tmp_path, state, stale, "2026-11-11T02", "put")
    _freeze(tmp_path, state, other, "2026-11-11T02", "call")
    assert ar.run_players(archive, state, now_ms=R1 + 15 * M, run_player=lambda *a: 1 / 0, report={"errors": []}) == 0
    assert state["player"] == {"2026-11-11T02|put": "stale_quote", "2026-11-11T02|call": "not_a_round"}


def test_an_exception_row_keeps_its_versions(tmp_path):
    archive = ar.Archive(tmp_path)
    state = archive.load_state()
    _freeze(tmp_path, state, _case("cf_put"), "2026-11-11T02", "put")
    ar.run_players(archive, state, now_ms=R1 + 15 * M, run_player=lambda *a: 1 / 0, report={"errors": []})
    row = _rows(tmp_path)[0]
    assert row["action"] == "missing" and row["prompt_version"] == pl.PROMPT_VERSION
    assert row["rule_versions"]["rulebook"] == pl.RULEBOOK_VERSION


def test_rulebook_grade_labels_with_notes_are_valid():
    for grade in ("B（边缘）", "方向 A，幅度 C", "—"):
        d = _reject(basis=[{"ref": "本轮", "reading": "所需优势按对手价 0.2059", "grade": grade}])
        assert pl.run(_case(), rnd="r1", api_key="k", model="m", transport=_transport(d))["status"] == "OK"
    d = _reject(basis=[{"ref": "本轮", "reading": "所需优势按对手价 0.2059", "grade": "高"}])
    assert pl.run(_case(), rnd="r1", api_key="k", model="m", transport=_transport(d))["status"] == "INVALID"


def test_accept_needs_no_reason_type_under_the_default_to_underwrite():
    r = pl.run(_case(), rnd="r1", api_key="k", model="m", transport=_transport(_accept(deviation_types=[])))
    assert r["status"] == "OK" and pl.RULEBOOK_VERSION == "rulebook@1.1"
    old = pl.run(_case(), rnd="r1", api_key="k", model="m", transport=_transport(_reject(reject_reason="no_deviation")))
    assert old["status"] == "INVALID"                                # 1.0's reasons are gone


def test_a_section_8_reading_in_an_accepts_grounds_is_flagged_only():
    d = _accept(basis=[{"ref": "L3.expiry_walls", "reading": "Pin 80500", "grade": "-"}])
    r = pl.run(_case(), rnd="r1", api_key="k", model="m", transport=_transport(d))
    assert r["status"] == "OK" and any(i["code"] == "forbidden_accept_basis" for i in r["issues"])

