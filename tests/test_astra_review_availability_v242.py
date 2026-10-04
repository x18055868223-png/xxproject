import json
from copy import deepcopy
from http.server import ThreadingHTTPServer
import threading
from urllib.request import urlopen

import pytest

import test_astra_workbench_v20 as fx
from tools import astra_underwriting_discovery_v20 as discovery
from tools import astra_workbench_v20 as workbench


def fresh_discovery(*, now_ms):
    rows = fx.instruments()
    for row in rows:
        row["delta_observed_at_ms"] = now_ms - 1000
    return discovery.discover(rows, 100000, now_ms, quantity_btc=1.0, width_usd=2000.0)


def reason_set(item):
    return set((item.get("review_gate") or {}).get("reasons") or [])


def test_gate_tracks_cooldown_recovers_and_keeps_stale_quotes_disabled(tmp_path):
    clock = [fx.T]
    wb, snapshots = fx.make_workbench(
        tmp_path,
        risk_provider=fx.positive_risk,
        transport=lambda *a, **k: pytest.fail("GET/refesh must not call LLM"),
        clock=lambda: clock[0],
    )
    wb.discovery_capture = fresh_discovery
    fx.write_fmz(snapshots, fx.native_gate())

    fresh = wb.refresh(now_ms=clock[0], record_daily=False)
    assert fresh["candidates"][0]["review_gate"]["enabled"] is True

    wb._locked_state_update(
        lambda state: state.setdefault("manual_review", {}).update(last_started_ts_ms=fx.T)
    )
    clock[0] = fx.T + 1
    cooling = wb.refresh(now_ms=clock[0], record_daily=False)
    assert cooling["manual_review_state"]["cooldown_remaining_ms"] == 599_999
    assert all(not item["review_gate"]["enabled"] for item in cooling["candidates"])
    assert all("manual_review_cooldown_active" in reason_set(item) for item in cooling["candidates"])

    with wb._view_lock:
        stored = deepcopy(wb._current_view)
    assert stored["candidates"][0]["review_gate"]["enabled"] is True
    assert "manual_review_cooldown_active" not in reason_set(stored["candidates"][0])

    clock[0] = fx.T + wb.config.review_cooldown_ms + 1
    stale = wb.cached_view()
    assert all(not item["review_gate"]["enabled"] for item in stale["candidates"])
    assert all("cached_quote_not_current" in reason_set(item) for item in stale["candidates"])
    assert all("manual_review_cooldown_active" not in reason_set(item) for item in stale["candidates"])

    fx.write_fmz(snapshots, fx.native_gate(ts=clock[0] - 1000), now=clock[0])
    recovered = wb.refresh(now_ms=clock[0], record_daily=False)
    assert recovered["manual_review_state"]["cooldown_remaining_ms"] == 0
    assert recovered["candidates"][0]["review_gate"]["enabled"] is True


def test_exhausted_shared_budget_disables_gate_without_starting_review(tmp_path):
    calls = []
    wb, snapshots = fx.make_workbench(
        tmp_path,
        risk_provider=fx.positive_risk,
        transport=lambda *a, **k: calls.append(a),
    )
    today = wb.budget.snapshot()["date_bjt"]
    wb.config.shared_usage_ledger.write_text(
        json.dumps(
            {
                "date_bjt": today,
                "http_calls_used": wb.config.daily_http_limit,
                "http_calls_limit": wb.config.daily_http_limit,
            }
        ),
        encoding="utf-8",
    )
    fx.write_fmz(snapshots, fx.native_gate())

    view = wb.refresh(now_ms=fx.T, record_daily=False)
    item = view["candidates"][0]
    assert item["review_gate"]["enabled"] is False
    assert "manual_review_budget_exhausted" in reason_set(item)

    result = wb.review_candidate(item["snapshot"]["candidate_id"], "budget-empty", authorization="Bearer secret")
    assert result["status"] == "rejected"
    assert result["reason"] == "review_gate_disabled"
    assert "manual_review_budget_exhausted" in set(result["review_gate"]["reasons"])
    assert calls == []
    state = wb._load_state().get("manual_review") or {}
    assert state.get("inflight") is None
    assert state.get("last_started_ts_ms") is None


def test_unknown_shared_budget_disables_gate_without_creating_ledger(tmp_path):
    wb, snapshots = fx.make_workbench(tmp_path, risk_provider=fx.positive_risk)
    ledger_path = wb.config.shared_usage_ledger
    ledger_path.unlink()
    fx.write_fmz(snapshots, fx.native_gate())

    view = wb.refresh(now_ms=fx.T, record_daily=False)
    item = view["candidates"][0]
    assert item["review_gate"]["enabled"] is False
    assert "manual_review_service_not_configured" in reason_set(item)
    assert view["manual_review_state"]["daily_http_budget"] is None
    assert not ledger_path.exists()

    result = wb.review_candidate(item["snapshot"]["candidate_id"], "unknown-budget", authorization="Bearer secret")
    assert result["status"] == "rejected"
    assert "manual_review_service_not_configured" in set(result["review_gate"]["reasons"])
    assert not ledger_path.exists()


def test_review_state_api_reports_current_availability_without_market_refresh_or_http(tmp_path):
    wb, _ = fx.make_workbench(
        tmp_path,
        risk_provider=fx.positive_risk,
        transport=lambda *a, **k: pytest.fail("review-state GET must not call LLM"),
    )
    wb.discovery_capture = lambda **kwargs: pytest.fail("review-state GET refreshed discovery")
    wb._locked_state_update(
        lambda state: state.setdefault("manual_review", {}).update(last_started_ts_ms=fx.T)
    )

    server = ThreadingHTTPServer(("127.0.0.1", 0), workbench.make_handler(wb))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/workbench/review-state", timeout=3) as response:
            body = json.load(response)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

    assert body["manual_review_state"]["cooldown_remaining_ms"] == wb.config.review_cooldown_ms
    assert body["manual_review_availability"]["enabled"] is False
    assert body["manual_review_availability"]["reasons"] == ["manual_review_cooldown_active"]


def test_restart_idempotency_replays_before_cooldown_or_budget_checks(tmp_path):
    calls = []

    def transport(api_key, model, request, timeout, endpoint=None):
        calls.append(request)
        packet = json.loads(request["messages"][1]["content"].split("冻结候选：", 1)[1])
        snapshot = wb.ledger.state()["snapshots"][packet["snapshot_id"]]["snapshot"]
        return {"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(fx.good_review(snapshot), ensure_ascii=False)}}]}

    wb, snapshots = fx.make_workbench(tmp_path, risk_provider=fx.positive_risk, transport=transport)
    fx.write_fmz(snapshots, fx.native_gate())
    item = wb.refresh(now_ms=fx.T, record_daily=False)["candidates"][0]
    result = wb.review_candidate(item["snapshot"]["candidate_id"], "idem-restart", authorization="Bearer secret")
    assert result["status"] == "OK"
    assert len(calls) == 1

    today = wb.budget.snapshot()["date_bjt"]
    wb.config.shared_usage_ledger.write_text(
        json.dumps(
            {
                "date_bjt": today,
                "http_calls_used": wb.config.daily_http_limit,
                "http_calls_limit": wb.config.daily_http_limit,
            }
        ),
        encoding="utf-8",
    )
    restarted, _ = fx.make_workbench(
        tmp_path,
        risk_provider=fx.positive_risk,
        transport=lambda *a, **k: pytest.fail("idempotent replay must not call LLM"),
    )

    replay = restarted.review_candidate(
        item["snapshot"]["candidate_id"],
        "idem-restart",
        authorization="Bearer secret",
    )
    assert replay == result
    assert len(calls) == 1
