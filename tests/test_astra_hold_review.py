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
    clock[0] += 120_000
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
    assert state["enabled"] is True  # a failed answer does not start the cooldown


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


@pytest.mark.parametrize("status,settled,metrics,verdict,basis", [
    ("expired", {"status": "settled", "settlement_result_usd": 175.0}, None, "profit", "settlement"),
    ("expired", {"status": "settled", "settlement_result_usd": -9.5}, None, "flat", "settlement"),
    ("expired", {"status": "settled", "settlement_result_usd": -40.0}, None, "loss", "settlement"),
    ("expired", {"status": "delivery_price_unavailable"}, None, None, "settlement"),
    ("expired", {"status": "settled", "settlement_result_usd": None},
     {"float_pnl_btc": 0.0034, "spot_usd": 83000.0}, "profit", "floating_at_expiry"),
    ("stopped", {}, {"float_pnl_btc": -0.0026, "spot_usd": 83000.0}, "loss", "floating_at_stop"),
    ("active", {}, {"float_pnl_btc": 0.0001, "spot_usd": 83000.0}, "flat", "floating_now"),
    ("active", {}, {"float_pnl_btc": None, "spot_usd": 83000.0}, None, "floating_now"),
])
def test_lock_outcome_verdict(tmp_path, status, settled, metrics, verdict, basis):
    wb, _, episode_id, _ = setup(tmp_path)
    wb._series_tail_metrics = lambda _id: metrics
    out = wb._lock_outcome({"episode_id": episode_id, "status": status}, {episode_id: settled})
    assert out["verdict"] == verdict and out["basis"] == basis and out["flat_band_usd"] == 10.0


def test_lock_list_carries_outcome_from_the_series_tail(tmp_path):
    wb, _, episode_id, _ = setup(tmp_path)
    tail = wb._series_tail_metrics(episode_id)
    assert tail is not None and "float_pnl_btc" in tail
    episode = next(e for e in wb.locked_candidate_list()["episodes"] if e["episode_id"] == episode_id)
    assert episode["outcome"]["basis"] == "floating_now"
