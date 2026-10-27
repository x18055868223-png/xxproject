"""Hold review of a held lock: the model explains the ledger, never re-judges it."""
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tools import astra_hold_review_v1 as hold
from tools import astra_workbench_v20 as workbench
from test_astra_workbench_v20 import make_workbench, write_fmz, native_gate, T


def reply(answer):
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer, ensure_ascii=False)}}],
            "usage": {"total_tokens": 321}}


def setup(tmp_path, answer=None, fail=None):
    calls = []

    def transport(api_key, model, request, timeout, endpoint=None):
        calls.append(request)
        if fail:
            raise fail
        return reply(answer if answer is not None else GOOD)

    clock = [T]
    wb, snapshots = make_workbench(tmp_path, clock=lambda: clock[0], transport=transport)
    write_fmz(snapshots, native_gate())
    view = wb.refresh(now_ms=T, record_daily=False)
    snapshot = view["candidates"][0]["snapshot"]
    episode = wb.lock_candidate(snapshot["candidate_id"], snapshot["snapshot_id"], "lock-once",
                                authorization="Bearer secret")["episode"]
    for _ in range(4):  # 20 minutes of held history: a review needs at least 15
        clock[0] += 5 * 60_000
        wb.refresh(now_ms=clock[0], record_daily=False)
    return wb, clock, episode["episode_id"], calls


GOOD = {"summary": "当初的依据整体仍成立，短腿缓冲没有变薄。",
        "points": [{"thesis_id": "L1", "text": "短腿缓冲充足，期间没有变差。"}],
        "watch": ["到期前留意短腿缓冲是否变薄。"]}


def test_review_explains_the_ledger_with_one_budgeted_call(tmp_path):
    wb, _, episode_id, calls = setup(tmp_path)
    out = wb.hold_review_locked(episode_id, "hold-key-0001", authorization="Bearer secret")
    assert out["status"] == "OK" and out["llm_call_count"] == 1 and out["execution_allowed"] is False
    assert out["answer"]["points"][0]["thesis_id"] == "L1"
    request = calls[0]
    assert request["response_format"] == {"type": "json_object"} and request["max_tokens"] == 800
    sent = json.loads(request["messages"][1]["content"])
    assert sent["schema"] == hold.INPUT_SCHEMA and sent["依据账本"]["逐条"][0]["id"] == "L1"
    # basis lanes and background lanes are both sent, each labelled (docs/opus/23 §1.1)
    roles = {t["id"]: t["角色"] for t in sent["依据账本"]["逐条"]}
    assert roles["L1"] == "依据" and all(roles[k] == "背景" for k in ("L4", "L5", "L6") if k in roles)
    assert isinstance(sent["依据账本"]["背景"], list) and "背景" in hold.SYSTEM_PROMPT
    # the series endpoint shows it; a second call within 5 minutes is refused, a replay is free
    state = wb.locked_candidate_series(episode_id)["hold_review"]
    assert state["last"]["answer"]["summary"] == GOOD["summary"]
    assert state["enabled"] is False and "hold_review_cooldown" in state["reasons"]
    assert wb.hold_review_locked(episode_id, "hold-key-0001", authorization="Bearer secret")["status"] == "OK"
    assert len(calls) == 1
    with pytest.raises(workbench.WorkbenchError) as exc:
        wb.hold_review_locked(episode_id, "hold-key-0002", authorization="Bearer secret")
    assert exc.value.code == "hold_review_cooldown"


@pytest.mark.parametrize("answer,reason", [
    ({**GOOD, "summary": "依据仍成立，建议平仓锁定利润。"}, "summary_banned_word"),
    ({**GOOD, "summary": "短腿缓冲还有 9.87%，依据仍成立。"}, "summary_number_not_in_input"),
    ({**GOOD, "points": [{"thesis_id": "L9", "text": "不存在的依据。"}]}, "point_thesis_id"),
    ({**GOOD, "extra": 1}, "answer_keys"),
    ({**GOOD, "watch": ["一" * 60]}, "watch_too_long"),
])
def test_invalid_answers_are_kept_out(tmp_path, answer, reason):
    wb, _, episode_id, _ = setup(tmp_path, answer=answer)
    out = wb.hold_review_locked(episode_id, "hold-key-0001", authorization="Bearer secret")
    assert out["status"] == "INVALID_RESPONSE" and out["reason"] == reason and "answer" not in out
    state = wb.hold_review_state(episode_id)
    assert state["last"] is None and state["last_failed"]["reason"] == reason
    # a failed paid call starts only a short cooldown (no rapid re-clicks)
    assert state["enabled"] is False and 0 < state["cooldown_remaining_ms"] <= 60_000


def test_call_failure_and_guards(tmp_path):
    wb, _, episode_id, _ = setup(tmp_path, fail=RuntimeError("boom"))
    out = wb.hold_review_locked(episode_id, "hold-key-0001", authorization="Bearer secret")
    assert out["status"] == "CALL_FAILED" and out["llm_call_count"] == 1
    with pytest.raises(workbench.WorkbenchError) as exc:
        wb.hold_review_locked(episode_id, "hold-key-0002", authorization="Bearer wrong")
    assert exc.value.code == "unauthorized"
    with pytest.raises(workbench.WorkbenchError) as exc:
        wb.hold_review_locked(episode_id, "x", authorization="Bearer secret")
    assert exc.value.code == "idempotency_key_invalid"
    wb.stop_locked_candidate(episode_id, authorization="Bearer secret")
    with pytest.raises(workbench.WorkbenchError) as exc:
        wb.hold_review_locked(episode_id, "hold-key-0003", authorization="Bearer secret")
    assert exc.value.code == "lock_not_active"


def test_budget_exhausted_makes_no_call(tmp_path):
    wb, _, episode_id, calls = setup(tmp_path)
    wb.budget.reserve = lambda *a, **k: (_ for _ in ()).throw(
        workbench.signal_llm_review.DailyBudgetExceeded("full"))
    out = wb.hold_review_locked(episode_id, "hold-key-0001", authorization="Bearer secret")
    assert out["status"] == "BUDGET_EXHAUSTED" and out["llm_call_count"] == 0 and calls == []


def test_http_route_and_audit(tmp_path):
    wb, _, episode_id, calls = setup(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), workbench.make_handler(wb))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/workbench/locked-candidates/{episode_id}/hold-review"
        body = json.dumps({"idempotency_key": "hold-key-http1"}).encode()
        req = Request(url, data=body, method="POST", headers={
            "Authorization": "Bearer secret", "Content-Type": "application/json"})
        out = json.loads(urlopen(req, timeout=20).read())
        assert out["status"] == "OK"
        bad = Request(url.replace("hold-review", "hold-reviewx"), data=body, method="POST",
                      headers={"Authorization": "Bearer secret", "Content-Type": "application/json"})
        with pytest.raises(HTTPError) as exc:
            urlopen(bad, timeout=20)
        assert exc.value.code == 404
    finally:
        server.shutdown()


def test_validator_allows_numbers_from_the_input_only():
    review_input = {"依据账本": {"逐条": [{"id": "L1", "现值": "+1.03%"}]}, "到期前事件": [{"北京时间": "10/15 20:30"}]}
    ok = hold.validate({"summary": "短腿缓冲 1.03%，10/15 20:30 有 PPI。", "points": [], "watch": []}, review_input)
    assert ok["summary"].startswith("短腿缓冲")
    with pytest.raises(hold.HoldReviewInvalid):
        hold.validate({"summary": "短腿缓冲 2.5%。", "points": [], "watch": []}, review_input)


@pytest.mark.parametrize("status,settled,metrics,start,verdict,basis", [
    ("expired", {"status": "settled", "settlement_result_usd": 175.0}, None, None, "profit", "settlement"),
    ("expired", {"status": "settled", "settlement_result_usd": -9.5}, None, None, "flat", "settlement"),
    ("expired", {"status": "settled", "settlement_result_usd": -40.0}, None, None, "loss", "settlement"),
    ("expired", {"status": "delivery_price_unavailable"}, None, None, None, "floating_at_expiry"),
    ("expired", {"status": "settled", "settlement_result_usd": None},
     {"float_pnl_btc": 0.0034, "spot_usd": 83000.0}, -0.0005, "profit", "floating_at_expiry"),
    ("stopped", {}, {"float_pnl_btc": -0.0026, "spot_usd": 83000.0}, -0.0003, "loss", "floating_at_stop"),
    # a fresh lock: only the bid/ask spread paid at lock -> flat, not a loss
    ("active", {}, {"float_pnl_btc": -0.0003, "spot_usd": 83000.0}, -0.0003, "flat", "floating_now"),
    ("stopped", {}, {"float_pnl_btc": -0.00025, "spot_usd": 83000.0}, -0.00025, "flat", "floating_at_stop"),
    ("active", {}, {"float_pnl_btc": -0.0005, "spot_usd": 83000.0}, -0.0003, "loss", "floating_now"),
    ("active", {}, {"float_pnl_btc": 0.0001, "spot_usd": 83000.0}, -0.0003, "profit", "floating_now"),
    ("active", {}, {"float_pnl_btc": 0.0001, "spot_usd": 83000.0}, None, None, "floating_now"),
])
def test_lock_outcome_verdict(tmp_path, status, settled, metrics, start, verdict, basis):
    wb, _, episode_id, _ = setup(tmp_path)
    wb._series_tail_metrics = lambda _id: metrics
    wb._series_head_float_pnl = lambda _id: start
    out = wb._lock_outcome({"episode_id": episode_id, "status": status}, {episode_id: settled})
    assert out["verdict"] == verdict and out["basis"] == basis and out["flat_band_usd"] == 10.0


def test_lock_list_carries_outcome_from_the_series_tail(tmp_path):
    wb, _, episode_id, _ = setup(tmp_path)
    tail = wb._series_tail_metrics(episode_id)
    assert tail is not None and "float_pnl_btc" in tail
    episode = next(e for e in wb.locked_candidate_list()["episodes"] if e["episode_id"] == episode_id)
    assert episode["outcome"]["basis"] == "floating_now"
    if episode["outcome"]["verdict"] is not None:
        assert episode["outcome"]["measured_from"] == "lock_time_floating_pnl"


def test_review_waits_for_held_history(tmp_path):
    calls = []
    clock = [T]
    wb, snapshots = make_workbench(tmp_path, clock=lambda: clock[0],
                                   transport=lambda *a, **k: calls.append(a) or reply(GOOD))
    write_fmz(snapshots, native_gate())
    snapshot = wb.refresh(now_ms=T, record_daily=False)["candidates"][0]["snapshot"]
    episode_id = wb.lock_candidate(snapshot["candidate_id"], snapshot["snapshot_id"], "lock-once",
                                   authorization="Bearer secret")["episode"]["episode_id"]
    clock[0] += 120_000
    wb.refresh(now_ms=clock[0], record_daily=False)
    state = wb.locked_candidate_series(episode_id)["hold_review"]
    assert "hold_review_too_early" in state["reasons"] and state["history_needed_ms"] > 0
    with pytest.raises(workbench.WorkbenchError) as exc:
        wb.hold_review_locked(episode_id, "hold-key-0001", authorization="Bearer secret")
    assert exc.value.code == "hold_review_too_early" and calls == []


def test_malformed_answer_is_invalid_not_a_crash(tmp_path):
    bad = {**GOOD, "points": [{"thesis_id": ["L1"], "text": "列表 id。"}]}
    wb, _, episode_id, _ = setup(tmp_path, answer=bad)
    out = wb.hold_review_locked(episode_id, "hold-key-0001", authorization="Bearer secret")
    assert out["status"] == "INVALID_RESPONSE" and out["reason"] == "point_thesis_id"
    assert wb.hold_review_state(episode_id)["reasons"] == ["hold_review_cooldown"]  # not stuck in flight


def test_input_failure_clears_the_inflight_flag(tmp_path, monkeypatch):
    wb, clock, episode_id, calls = setup(tmp_path)
    monkeypatch.setattr(hold, "build_input", lambda *a, **k: (_ for _ in ()).throw(KeyError("x")))
    out = wb.hold_review_locked(episode_id, "hold-key-0001", authorization="Bearer secret")
    assert out["status"] == "ERROR" and out["llm_call_count"] == 0 and calls == []
    assert "hold_review_inflight" not in wb.hold_review_state(episode_id)["reasons"]
    # replay of a key returns the stored result even after the lock stopped
    wb.stop_locked_candidate(episode_id, authorization="Bearer secret")
    assert wb.hold_review_locked(episode_id, "hold-key-0001", authorization="Bearer secret")["status"] == "ERROR"


def test_validator_normalizes_numbers_and_words():
    review_input = {"依据账本": {"逐条": [{"id": "L1", "现值": "+1.23%"}]}, "合约": {"卖出腿": 85000.0}}
    ok = hold.validate({"summary": "短腿 85,000，缓冲 1.23%，保证金无变化。", "points": [], "watch": []}, review_input)
    assert ok["summary"]
    with pytest.raises(hold.HoldReviewInvalid):  # 8500 is not 85000
        hold.validate({"summary": "短腿 8500。", "points": [], "watch": []}, review_input)
    for word in ("建议离场。", "可考虑对冲。", "可能性不大。"):
        with pytest.raises(hold.HoldReviewInvalid):
            hold.validate({"summary": word, "points": [], "watch": []}, review_input)


def test_hold_reviews_reach_the_reasoning_audit_view(tmp_path):
    wb, _, episode_id, _ = setup(tmp_path)
    assert wb.cached_view(reading_only=True)["hold_reviews"] == []
    wb.hold_review_locked(episode_id, "hold-key-0001", authorization="Bearer secret")
    rows = wb.cached_view(reading_only=True)["hold_reviews"]
    assert len(rows) == 1 and rows[0]["episode_id"] == episode_id and rows[0]["lock_state"] == "locked"
    assert rows[0]["answer"]["points"][0]["thesis_id"] == "L1" and rows[0]["side"] in {"put", "call"}
    assert rows[0]["finished_at_ms"] >= rows[0]["locked_at_ms"]
