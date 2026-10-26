import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import astra_research_archive_v1 as ar
import astra_research_scorecards_v1 as sc

H = 3_600_000
EXPIRY = 1_792_569_600_000            # 2026-10-21 08:00 UTC
DAY = "2026-10-21"


def _jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _contract(i, side="put"):
    short = 81000 + 500 * i
    return f"BTC-21OCT26-{short}-P|BTC-21OCT26-{short - 2000}-P", short


def _view(vid, key, at, risk, grade, lean="watch"):
    return {"submission_id": f"episode_view:{vid}", "received_at_ms": at,
            "document": {"schema": "episode_view@0.1", "id": vid, "agent": {"name": "Cici", "model": "GrokBot"},
                         "contract_key": key, "view": {"risk_vs_baseline": risk, "grade": grade, "lean": lean,
                                                       "confidence": "low", "summary_cn": "x", "drivers_cn": []}}}


def _archive(tmp_path):
    root = tmp_path / "archive"
    entry_at = EXPIRY - 10 * H
    views, ledger, outcomes = [], [], []
    # put contracts at increasing strikes; delivery 81,900: the higher the short strike, the worse
    for i, (risk, grade) in enumerate([("much_safer", "A"), ("safer", "B"), ("in_line", "C"), ("riskier", "D")]):
        key, short = _contract(i)
        ledger.append({"contract_key": key, "at_ms": entry_at - 5 * 60_000, "expiry_ms": EXPIRY, "side": "put",
                       "displayed": True, "credit_bid_ask_btc": 0.003, "p_touch_short": 0.4, "p_loss": 0.2})
        views.append(_view(f"ev-{i}", key, entry_at, risk, grade))
        outcomes.append({"contract_key": key, "status": "settled", "side": "put", "short_strike_usd": short,
                         "long_strike_usd": short - 2000, "expiry_ms": EXPIRY, "delivery_price_usd": 81900 - 0,
                         "cases": [{"case_id": f"cf_{i}"}]})
    # a view without a ledger row near its registration, and one whose contract has not settled
    late_key, _ = _contract(9)
    views.append(_view("ev-noentry", outcomes[0]["contract_key"], entry_at - 5 * H, "in_line", "C"))
    views.append(_view("ev-open", late_key, entry_at, "in_line", "C"))
    _jsonl(root / "registry" / "episode_views.jsonl", views)
    _jsonl(root / "ledger" / "2026-10-20.jsonl", ledger)
    _jsonl(root / "outcomes" / f"{DAY}.jsonl", outcomes)
    points = [[entry_at + m * 60_000, 82600 - max(m, 0) * 1.2] for m in range(-10, 601)]
    (root / "paths").mkdir(parents=True)
    (root / "paths" / f"{DAY}.json.gz").write_bytes(gzip.compress(json.dumps({"points": points}).encode()))
    return root


def test_views_are_scored_from_their_registration_and_ordered(tmp_path):
    root = _archive(tmp_path)
    card = sc.view_scorecard(root, now_ms=EXPIRY + 2 * H)
    assert card["counts"] == {"scored": 4, "no_entry_row": 1, "pending_outcome": 1}
    scored = {v["view_id"]: v for v in card["views"] if v["status"] == "scored"}
    # short 82,500 put: settled 81,900 is below the short strike -> touched and a loss
    worst = scored["ev-3"]["metrics"]
    assert worst["touched_short_after_entry"] == 1.0 and worst["excess_touch"] == 1.0 - 0.4
    assert worst["loss_at_expiry"] == 1.0 and worst["excess_loss"] == 1.0 - 0.2
    assert scored["ev-0"]["metrics"]["touched_short_after_entry"] == 0.0
    assert scored["ev-0"]["entry_gap_ms"] == 5 * 60_000
    order = card["ordering"]["risk_vs_baseline"]
    assert order["settle_vs_short_pct"]["spearman"] == 1.0          # riskier views settled deeper
    assert order["result_btc_at_entry_credit"]["spearman"] > 0.9    # sign flipped: worse result is positive (one tie)
    assert order["days"] == 1 and order["enough_days"] is False
    grades = {g["level"]: g for g in card["by"]["grade"]}
    assert grades["A"]["n"] == 1 and grades["D"]["mean"]["loss_at_expiry"] == 1.0


def _inquiry(root, case_id, version, ids):
    path = root / "inquiry" / "2026-10-20" / f"02_put_{case_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    questions = [{"id": q, "type": "gap" if q == "K1" else "conflict", "priority": "high"} for q in ids]
    path.write_text(json.dumps({"case_id": case_id, "prompt_version": version, "status": "OK",
                                "sheet": {"questions": questions}}), encoding="utf-8")


def test_inquiry_reviews_are_joined_counted_and_the_latest_wins(tmp_path):
    root = _archive(tmp_path)
    _inquiry(root, "cf_0", "inquiry_prompt@0.2", ["K1", "K2", "K3"])
    _inquiry(root, "cf_unsettled", "inquiry_prompt@0.2", ["K1"])
    pm = lambda pid, at, items: {"submission_id": f"postmortem:{pid}", "received_at_ms": at,
                                 "document": {"schema": "postmortem@0.1", "id": pid, "inquiry_review": items}}
    _jsonl(root / "registry" / "postmortems.jsonl", [
        pm("pm-1", 10, [{"case_id": "cf_0", "question_id": "K1", "verdict": "not_material"},
                        {"case_id": "cf_0", "question_id": "K2", "verdict": "wrong_premise"},
                        {"case_id": "cf_0", "question_id": "K9", "verdict": "unclear"}]),
        pm("pm-2", 20, [{"case_id": "cf_0", "question_id": "K1", "verdict": "pointed_to_realized_risk"}])])
    card = sc.inquiry_scorecard(root, now_ms=30)
    version = card["by_prompt_version"]["inquiry_prompt@0.2"]
    assert version["reviewed"] == 2 and version["pointed_to_realized_risk"] == 1 and version["not_material"] == 0
    assert version["pointed_rate"] == 0.5 and version["wrong_premise_rate"] == 0.5
    assert card["by_type"]["inquiry_prompt@0.2|gap"]["pointed_to_realized_risk"] == 1
    assert [u["question_id"] for u in card["unmatched"]] == ["K9"]
    # research-only re-reading by experience card, with the days behind it
    cards = card["by_card"]
    assert sum(v["reviewed"] for v in cards.values()) == 2
    assert all(k.startswith("inquiry_lens@0.1|inquiry_prompt@0.2|") for k in cards)
    assert all(v["expiry_days"] == 0 for v in cards.values())      # the fixture archive has no case files
    assert "by_card_error" not in card
    # only cases whose contract settled count toward coverage
    assert card["coverage"]["inquiry_prompt@0.2"] == {"cases": 1, "cases_without_questions": 0, "questions_asked": 3,
                                                      "questions_reviewed": 2,
                                                      "coverage": 2 / 3}


def test_run_writes_both_cards_and_empty_archive_is_fine(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert sc.run(empty, now_ms=1) == {"views_scored": 0, "reviews": 0, "unmatched_reviews": 0,
                                      "intraday_exit_rows": 0}
    root = _archive(tmp_path)
    assert sc.run(root, now_ms=2)["views_scored"] == 4
    assert json.loads((root / "scorecards" / "views.json").read_text(encoding="utf-8"))["schema"] == "view_scorecard@0.1"
    assert json.loads((root / "scorecards" / "inquiry.json").read_text(encoding="utf-8"))["derived"] is True


def test_tick_runs_scorecards_once_a_day_after_validation(tmp_path, monkeypatch):
    archive = ar.Archive(tmp_path / "a")
    calls = []
    monkeypatch.setattr(ar.research_validation, "run", lambda a, now_ms: {"evaluated": 0})
    monkeypatch.setattr(ar.research_scorecards, "run", lambda root, now_ms: calls.append(now_ms) or 1 / 0)
    deribit = ar.Deribit(lambda path, params: {"data": []})
    t_early = 1_792_486_800_000 + 20 * 60_000   # 2026-10-20 09:20 UTC
    ar.tick(archive, now_ms=t_early, view=None, fetch_case=lambda s: {}, deribit=deribit)
    assert calls == []
    r = ar.tick(archive, now_ms=t_early + 20 * 60_000, view=None, fetch_case=lambda s: {}, deribit=deribit)
    ar.tick(archive, now_ms=t_early + 30 * 60_000, view=None, fetch_case=lambda s: {}, deribit=deribit)
    assert len(calls) == 1 and any(e["step"] == "scorecards" for e in r["errors"])
    assert ar.observed_versions()["scorecard_rules"] == sc.SCORECARD_RULES


def test_touch_before_registration_is_not_credited_and_call_side_and_edge_statuses(tmp_path):
    root = tmp_path / "archive"
    entry_at, registered = EXPIRY - 10 * H, EXPIRY - 10 * H + 35 * 60_000
    put_key, call_key, gone_key = ("BTC-21OCT26-80000-P|BTC-21OCT26-78000-P",
                                   "BTC-21OCT26-83000-C|BTC-21OCT26-85000-C",
                                   "BTC-21OCT26-90000-C|BTC-21OCT26-92000-C")
    row = lambda key, side: {"contract_key": key, "at_ms": entry_at, "expiry_ms": EXPIRY, "side": side,
                             "displayed": True, "credit_bid_ask_btc": 0.003, "p_touch_short": 0.3, "p_loss": 0.1}
    _jsonl(root / "ledger" / "2026-10-20.jsonl", [row(put_key, "put"), row(call_key, "call")])
    _jsonl(root / "registry" / "episode_views.jsonl", [
        _view("ev-put", put_key, registered, "in_line", "U"),
        _view("ev-call", call_key, registered, "riskier", "E"),
        _view("ev-gone", gone_key, registered, "in_line", "C")])
    _jsonl(root / "outcomes" / f"{DAY}.jsonl", [
        {"contract_key": put_key, "status": "settled", "side": "put", "short_strike_usd": 80000,
         "long_strike_usd": 78000, "expiry_ms": EXPIRY, "delivery_price_usd": 85000, "cases": []},
        {"contract_key": call_key, "status": "settled", "side": "call", "short_strike_usd": 83000,
         "long_strike_usd": 85000, "expiry_ms": EXPIRY, "delivery_price_usd": 85000, "cases": []},
        {"contract_key": gone_key, "status": "outcome_unavailable", "expiry_ms": EXPIRY}])
    # the index sits below the put strike for the first 30 minutes, then at 85,000 until expiry
    points = [[entry_at + m * 60_000, 79000 if m < 30 else 85000] for m in range(0, 601)]
    (root / "paths").mkdir(parents=True)
    (root / "paths" / f"{DAY}.json.gz").write_bytes(gzip.compress(json.dumps({"points": points}).encode()))
    card = sc.view_scorecard(root, now_ms=EXPIRY + H)
    views = {v["view_id"]: v for v in card["views"]}
    assert views["ev-put"]["metrics"]["touched_short_after_entry"] == 0.0     # the early touch is not credited
    assert views["ev-put"]["entry_gap_ms"] == 35 * 60_000
    call = views["ev-call"]["metrics"]
    assert call["touched_short_after_entry"] == 1.0 and call["loss_at_expiry"] == 1.0
    assert call["settle_vs_short_pct"] > 0
    assert views["ev-gone"]["status"] == "outcome_unavailable"
    assert {g["level"] for g in card["by"]["grade"]} == {"U", "E"}
    assert card["ordering"]["grade"]["n"] == 1                               # U stays out of the ordering


def test_failed_inquiry_runs_and_bad_lines_do_not_break_the_card(tmp_path):
    root = _archive(tmp_path)
    path = root / "inquiry" / "2026-10-20" / "02_put_cf_1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"case_id": "cf_1", "prompt_version": "inquiry_prompt@0.2", "status": "ERROR"}),
                    encoding="utf-8")
    (root / "inquiry" / "2026-10-20" / "02_call_cf_x.json").write_text("{", encoding="utf-8")
    with (root / "registry" / "episode_views.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"truncated": \n')
    card = sc.inquiry_scorecard(root, now_ms=1)
    assert card["coverage"]["inquiry_prompt@0.2"]["cases_without_questions"] == 1
    assert card["coverage"]["inquiry_prompt@0.2"]["coverage"] is None
    assert sc.run(root, now_ms=2)["views_scored"] == 4


def test_a_broken_lens_never_takes_the_inquiry_card_down(tmp_path, monkeypatch):
    root = _archive(tmp_path)
    _inquiry(root, "cf_0", "inquiry_prompt@0.4", ["K1"])
    _jsonl(root / "registry" / "postmortems.jsonl", [{"submission_id": "postmortem:p", "received_at_ms": 1, "document": {
        "schema": "postmortem@0.1", "id": "p", "inquiry_review": [{"case_id": "cf_0", "question_id": "K1", "verdict": "unclear"}]}}])
    monkeypatch.setattr(sc.lens_core, "classify", lambda q: 1 / 0)
    card = sc.inquiry_scorecard(root, now_ms=5)
    assert card["by_card_error"] == "ZeroDivisionError" and card["by_card"] == {}
    assert card["by_prompt_version"]["inquiry_prompt@0.4"]["reviewed"] == 1
