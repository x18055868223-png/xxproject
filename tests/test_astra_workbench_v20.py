import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.request import urlopen

import pytest

from tools import astra_underwriting_discovery_v20 as discovery
from tools import astra_underwriting_public_quote_v20 as quote
from tools import astra_underwriting_v20 as underwriting
from tools import astra_workbench_v20 as workbench
from test_astra_underwriting_review_v20 import good_answer


T = 1_790_323_200_000
EXPIRY = T + 86_400_000


def option_meta(name, side, strike):
    return {
        "instrument_name": name,
        "kind": "option",
        "base_currency": "BTC",
        "settlement_currency": "BTC",
        "quote_currency": "BTC",
        "counter_currency": "USD",
        "price_index": "btc_usd",
        "instrument_type": "reversed",
        "option_type": side,
        "state": "open",
        "expiration_timestamp": EXPIRY,
        "contract_size": 1,
        "strike": strike,
        "min_trade_amount": 0.1,
        "settlement_period": "day",
        "taker_commission": 0.0,
    }


def instruments():
    rows = []
    for side, suffix, strikes in (
        ("put", "P", (97000, 95000)),
        ("call", "C", (103000, 105000)),
    ):
        for strike in strikes:
            row = option_meta(f"BTC-26SEP26-{strike}-{suffix}", side, strike)
            row["greeks"] = {"delta": -0.35 if side == "put" else 0.35}
            row["delta_observed_at_ms"] = T - 1000
            rows.append(row)
    return rows


def fake_discovery(*, now_ms):
    return discovery.discover(instruments(), 100000, now_ms, quantity_btc=1.0, width_usd=2000.0)


def fake_quote_capture(short_instrument, long_instrument, quantity_btc, *, now_ms):
    metas = {row["instrument_name"]: row for row in instruments()}

    def fetch(path, params):
        name = params["instrument_name"]
        if path == "public/get_instrument":
            return dict(metas[name])
        if name.endswith("-P"):
            bid, ask = (0.007, 0.008) if name == short_instrument else (0.002, 0.003)
        else:
            bid, ask = (0.007, 0.008) if name == short_instrument else (0.002, 0.003)
        return {
            "instrument_name": name,
            "state": "open",
            "timestamp": now_ms,
            "index_price": 100000,
            "bids": [[bid, 1.0]],
            "asks": [[ask, 1.0]],
        }

    return quote.capture_candidate(
        short_instrument, long_instrument, quantity_btc, transport=fetch, clock_ms=lambda: now_ms
    )


def native_gate(state="STRUCTURE_ELIGIBLE", *, ts=T - 1000):
    good = state == "STRUCTURE_ELIGIBLE"
    return {
        "state": state,
        "observed_at_ms": ts,
        "since_ts_ms": ts - 60_000,
        "previous_state": "STRUCTURE_BLOCKED" if good else "STRUCTURE_ELIGIBLE",
        "last_transition_ts_ms": ts - 60_000,
        "reason_codes": [] if good else ["NET_GAMMA_NON_POSITIVE"],
        "anchor": {"state": "Valid", "ok": True, "score": 70.0, "source": "anchor"},
        "net_gamma": {
            "ok": good,
            "valid": True,
            "transition": False,
            "conflict": False,
            "value": 123.0 if good else -1.0,
            "unit": "USD",
            "sign": "POSITIVE" if good else "NEGATIVE",
            "source": "gexmonitorapi.gex_board.total_net_gex",
            "source_ts_ms": ts - 500,
        },
    }


def write_fmz(path: Path, gate=None, *, now=T, symbol="BTC"):
    row = {
        "schema_name": "EvaluationSnapshot",
        "schema_version": "1.6.2",
        "decision": {
            "ts_ms": now - 1000,
            "demo_version": "2.0.0",
            "symbol": symbol,
            "runtime_facts": {
                "runtime_mode": "live_public_read_only",
                "current_price": 100000,
                "current_price_source": "binance_spot",
                "current_price_observed_ms": now - 1500,
            },
        },
        "factor_snapshot": {
            "anchor": {"effective_flip_point": 99000, "anchor_gravity_ref_score": 70},
            "flow": {"direction": "NEUTRAL", "tmv_blend": 0.0},
            "gamma_regime": {"regime": "POSITIVE_GAMMA_PINNING", "net_gamma_notional": 1.0},
            "neutral_repair_signal": {"state": "IDLE", "is_active": False},
        },
    }
    if gate is not None:
        row["factor_snapshot"]["space_gate"] = gate
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    return row


def positive_risk(payload, fmz_fact, now_ms):
    identity = underwriting.candidate_identity(payload["candidate"], payload["metadata"], now_ms)
    return {
        "status": "supported",
        "support_verified": True,
        "candidate_id": identity["candidate_id"],
        "unit": "BTC",
        "model_id": "test-frozen-model",
        "model_hash": "a" * 64,
        "calibration_id": "test-calibration",
        "support_scope": "test-current-candidate-only",
        "source_kind": "frozen_model",
        "source_fact_hash": fmz_fact["source_hash"],
        "input_ts_ms": now_ms,
        "feature_end_ts_ms": now_ms,
        "reference_price_usd": 100000,
        "input_row_hash": "b" * 64,
        "normalization_basis": "test",
        "training_cutoff": "2026-08-31",
        "label_known_cutoff": "2026-09-13",
        "quantity_btc": 1.0,
        "horizon_hours": (EXPIRY - now_ms) / 3_600_000,
        "mu_btc": 0.001,
    }


def make_workbench(tmp_path, *, risk_provider=None, sender=None, transport=None, token="secret", api_key="key", clock=lambda: T):
    snapshots = tmp_path / "snapshots.jsonl"
    tmp_path.mkdir(parents=True, exist_ok=True)
    shared_ledger = tmp_path / "existing_shared_http_budget.json"
    if not shared_ledger.exists():
        shared_ledger.write_text(json.dumps({"date_bjt": "2026-09-25", "http_calls_used": 0}), encoding="utf-8")
    config = workbench.WorkbenchConfig(
        snapshots_jsonl=snapshots,
        state_dir=tmp_path / "state",
        frontend_dir=tmp_path,
        server_token=token,
        api_key=api_key,
        shared_usage_ledger=shared_ledger,
        model="fake-model",
        review_cooldown_ms=600_000,
        email_cooldown_ms=14_400_000,
    )
    wb = workbench.AstraWorkbench(
        config,
        discovery_capture=fake_discovery,
        quote_capture=fake_quote_capture,
        risk_provider=risk_provider,
        email_sender=sender,
        llm_transport=transport,
        clock_ms=clock,
    )
    return wb, snapshots


def good_review(snapshot):
    answer = good_answer(snapshot)
    if snapshot["risk"]["status"] == "supported":
        answer["applicability"] = "EXPLAINED"
    return answer


def test_refresh_uses_native_space_gate_and_missing_mu_keeps_review_disabled(tmp_path):
    wb, snapshots = make_workbench(tmp_path, api_key=None)
    before = json.dumps(write_fmz(snapshots, native_gate()), ensure_ascii=False).encode("utf-8")

    view = wb.refresh(now_ms=T)

    assert snapshots.read_bytes().strip() == before
    assert view["market_context"]["status"] == "current"
    assert view["market_context"]["market_facts"]["flow"]["direction"] == "NEUTRAL"
    assert view["market_context"]["source_hash"] == view["space_gate"]["fmz_source_hash"]
    assert view["space_gate"]["state"] == "STRUCTURE_ELIGIBLE"
    assert len(view["candidates"]) == 2
    assert {item["side"] for item in view["candidates"]} == {"put", "call"}
    for item in view["candidates"]:
        assert item["snapshot"]["economics"]["mu_ref_btc"] is None
        assert item["review_gate"]["enabled"] is False
        assert "risk_reference_missing_or_unqualified" in item["review_gate"]["reasons"]
    assert view["llm_call_count"] == 0


def test_negative_reference_margin_keeps_manual_review_disabled(tmp_path):
    def high_risk(payload, fmz_fact, now_ms):
        risk = positive_risk(payload, fmz_fact, now_ms)
        risk["mu_btc"] = 0.02
        return risk

    wb, snapshots = make_workbench(tmp_path, risk_provider=high_risk)
    write_fmz(snapshots, native_gate())

    view = wb.refresh(now_ms=T)

    for item in view["candidates"]:
        assert item["snapshot"]["economics"]["reference_margin_btc"] < 0
        assert item["review_gate"]["enabled"] is False
        assert "m_ref_non_positive" in item["review_gate"]["reasons"]
    assert view["llm_call_count"] == 0


def test_old_fmz_rows_without_space_gate_are_not_current_eligible(tmp_path):
    wb, snapshots = make_workbench(tmp_path)
    write_fmz(snapshots, gate=None)

    view = wb.refresh(now_ms=T)

    assert view["space_gate"]["state"] == "STRUCTURE_BLOCKED"
    assert "producer_native_space_gate_missing" in view["space_gate"]["gap_reasons"]
    assert all("structure_gate_not_eligible" in item["review_gate"]["reasons"] for item in view["candidates"])


def test_quote_receipt_after_refresh_start_binds_fmz_to_actual_valuation(tmp_path):
    wb, snapshots = make_workbench(tmp_path)
    write_fmz(snapshots, native_gate())

    def delayed_quote(short_instrument, long_instrument, quantity_btc, *, now_ms):
        return fake_quote_capture(short_instrument, long_instrument, quantity_btc,
                                  now_ms=now_ms + 1000)

    wb.quote_capture = delayed_quote
    view = wb.refresh(now_ms=T)
    for item in view["candidates"]:
        snapshot = item["snapshot"]
        assert snapshot is not None
        assert snapshot["market"]["valuation_ts_ms"] == T + 1000
        assert snapshot["fact_context"]["valuation_ts_ms"] == T + 1000


class Sender:
    def __init__(self, fail=False):
        self.fail = fail
        self.sent = []

    def send(self, subject, body):
        if self.fail:
            raise OSError("smtp unavailable")
        self.sent.append({"subject": subject, "body": body})
        return {"status": "sent"}


def test_state_email_cooldown_pending_and_restart_recovery(tmp_path):
    sender = Sender()
    clock_now = {"value": T}
    wb, snapshots = make_workbench(tmp_path, sender=sender, clock=lambda: clock_now["value"])
    write_fmz(snapshots, native_gate("STRUCTURE_BLOCKED", ts=T - 1000), now=T)
    wb.refresh(now_ms=T)
    assert sender.sent == []

    clock_now["value"] = T + 10_000
    write_fmz(snapshots, native_gate("STRUCTURE_ELIGIBLE", ts=clock_now["value"] - 1000), now=clock_now["value"])
    wb.refresh(now_ms=clock_now["value"])
    assert len(sender.sent) == 1
    assert "可建立候选" in sender.sent[0]["subject"]
    sent_view = wb.refresh(now_ms=clock_now["value"])
    assert sent_view["notification_state"]["last_status"] == "sent"
    assert sent_view["notification_state"]["last_attempt"]["transition_id"] == sent_view["transitions"][-1]["transition_id"]

    clock_now["value"] += 10_000
    write_fmz(snapshots, native_gate("STRUCTURE_BLOCKED", ts=clock_now["value"] - 1000), now=clock_now["value"])
    wb.refresh(now_ms=clock_now["value"])
    assert len(sender.sent) == 1
    pending_view = wb.refresh(now_ms=clock_now["value"])
    assert pending_view["notification_state"]["pending_transition_id"] == pending_view["transitions"][-1]["transition_id"]

    restarted, _ = make_workbench(tmp_path, sender=sender, clock=lambda: clock_now["value"])
    write_fmz(snapshots, native_gate("STRUCTURE_BLOCKED", ts=clock_now["value"] - 1000), now=clock_now["value"])
    restarted.refresh(now_ms=clock_now["value"])
    assert len(sender.sent) == 1

    clock_now["value"] += 14_400_001
    write_fmz(snapshots, native_gate("STRUCTURE_BLOCKED", ts=clock_now["value"] - 1000), now=clock_now["value"])
    restarted.refresh(now_ms=clock_now["value"])
    assert len(sender.sent) == 2
    assert "当前不准入" in sender.sent[1]["subject"]


def test_email_failure_is_recorded_without_blocking_refresh_or_llm(tmp_path):
    sender = Sender(fail=True)
    wb, snapshots = make_workbench(tmp_path, sender=sender)
    write_fmz(snapshots, native_gate("STRUCTURE_BLOCKED"), now=T)
    wb.refresh(now_ms=T)
    write_fmz(snapshots, native_gate("STRUCTURE_ELIGIBLE", ts=T + 1000), now=T + 2000)

    view = wb.refresh(now_ms=T + 2000)

    assert view["space_gate"]["state"] == "STRUCTURE_ELIGIBLE"
    state = wb._load_state()
    assert state["email"]["last_status"] == "failed"
    assert view["llm_call_count"] == 0


def test_daily_2300_snapshot_is_append_only_and_recent_list_defaults_to_15(tmp_path):
    wb, snapshots = make_workbench(tmp_path)
    now = T + 7 * 3_600_000  # 23:00 UTC+8
    write_fmz(snapshots, native_gate(ts=now - 1000), now=now)

    first = wb.refresh(now_ms=now)
    second = wb.refresh(now_ms=now + 60_000)

    assert len(first["daily_snapshots"]) == 1
    assert len(second["daily_snapshots"]) == 1
    assert second["daily_snapshots"][0]["llm_call_count"] == 0
    extra = [
        {"schema": workbench.DAILY_SNAPSHOT_SCHEMA, "snapshot_id": str(i), "bjt_day": f"2026-10-{i:02d}"}
        for i in range(16)
    ]
    for row in extra:
        workbench._append_jsonl(wb.config.daily_snapshots_path, row)
    assert len(wb.recent_daily_snapshots()) == 15


def test_manual_review_auth_gate_idempotency_cooldown_and_single_task(tmp_path):
    calls = []

    def transport(api_key, model, request, timeout, endpoint=None):
        calls.append(request)
        packet = json.loads(request["messages"][1]["content"].split("冻结候选：", 1)[1])
        snapshot_id = packet["snapshot_id"]
        snapshot = wb.ledger.state()["snapshots"][snapshot_id]["snapshot"]
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(good_review(snapshot), ensure_ascii=False)}}]}

    wb, snapshots = make_workbench(tmp_path, risk_provider=positive_risk, transport=transport)
    write_fmz(snapshots, native_gate())
    view = wb.refresh(now_ms=T)
    candidate_id = view["candidates"][0]["snapshot"]["candidate_id"]
    assert view["candidates"][0]["review_gate"]["enabled"] is True

    with pytest.raises(workbench.WorkbenchError) as auth_error:
        wb.review_candidate(candidate_id, "idem-auth", authorization="Bearer wrong")
    assert auth_error.value.status_code == 401
    assert calls == []

    result = wb.review_candidate(candidate_id, "idem-1", authorization="Bearer secret")
    assert result["status"] == "OK"
    assert result["side"] == view["candidates"][0]["side"]
    assert result["review"]["status"] == "OK"
    assert len(calls) == 1

    replay = wb.review_candidate(candidate_id, "idem-1", authorization="Bearer secret")
    assert replay == result
    assert len(calls) == 1

    with pytest.raises(workbench.WorkbenchError) as cd_error:
        wb.review_candidate(candidate_id, "idem-2", authorization="Bearer secret")
    assert cd_error.value.status_code == 429
    assert len(calls) == 1

    busy, busy_snapshots = make_workbench(tmp_path / "busy", risk_provider=positive_risk, transport=transport)
    write_fmz(busy_snapshots, native_gate())
    busy_candidate = busy.refresh(now_ms=T)["candidates"][0]["snapshot"]["candidate_id"]
    busy._locked_state_update(lambda state: state.setdefault("manual_review", {}).update(
        {"inflight": {"candidate_id": "other", "idempotency_key": "old", "started_ts_ms": T}}
    ))
    with pytest.raises(workbench.WorkbenchError) as busy_error:
        busy.review_candidate(busy_candidate, "busy-1", authorization="Bearer secret")
    assert busy_error.value.status_code == 409


def test_new_snapshot_of_same_candidate_needs_new_manual_click_and_respects_cooldown(tmp_path):
    current = [T]
    calls = []

    def fresh_discovery(*, now_ms):
        rows = instruments()
        for row in rows:
            row["delta_observed_at_ms"] = now_ms - 1000
        return discovery.discover(rows, 100000, now_ms, quantity_btc=1.0, width_usd=2000.0)

    def transport(api_key, model, request, timeout, endpoint=None):
        calls.append(request)
        packet = json.loads(request["messages"][1]["content"].split("冻结候选：", 1)[1])
        snapshot = wb.ledger.state()["snapshots"][packet["snapshot_id"]]["snapshot"]
        return {"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(good_review(snapshot), ensure_ascii=False)}}]}

    wb, snapshots = make_workbench(
        tmp_path, risk_provider=positive_risk, transport=transport, clock=lambda: current[0])
    wb.discovery_capture = fresh_discovery
    write_fmz(snapshots, native_gate())
    first = wb.refresh(now_ms=current[0])["candidates"][0]
    first_snapshot = first["snapshot"]
    first_result = wb.review_candidate(first_snapshot["candidate_id"], "first-click", authorization="Bearer secret")
    assert first_result["status"] == "OK"
    assert len(calls) == 1

    current[0] += wb.config.review_cooldown_ms + 1
    write_fmz(snapshots, native_gate(ts=current[0] - 1000), now=current[0])
    second = wb.refresh(now_ms=current[0])["candidates"][0]
    second_snapshot = second["snapshot"]
    assert second_snapshot["candidate_id"] == first_snapshot["candidate_id"]
    assert second_snapshot["snapshot_id"] != first_snapshot["snapshot_id"]
    assert second["review_gate"]["enabled"] is True
    assert len(calls) == 1  # Passive refresh never calls an LLM.
    second_result = wb.review_candidate(second_snapshot["candidate_id"], "second-click", authorization="Bearer secret")
    assert second_result["status"] == "OK"
    assert second_result["snapshot_id"] == second_snapshot["snapshot_id"]
    assert len(calls) == 2


def test_review_restart_recovers_settled_receipt_without_new_llm(tmp_path):
    wb, snapshots = make_workbench(tmp_path, risk_provider=positive_risk)
    write_fmz(snapshots, native_gate())
    view = wb.refresh(now_ms=T)
    candidate = view["candidates"][0]["snapshot"]
    started = {"candidate_id": candidate["candidate_id"],
               "idempotency_key": "restart-settled", "started_ts_ms": T}
    wb._locked_state_update(lambda state: state.setdefault("manual_review", {}).update(
        {"inflight": started, "idempotency": {"restart-settled": {"status": "inflight"}}}))
    saved = wb.config.review_state_dir / ("candidate-" + candidate["candidate_id"] + ".json")
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text(json.dumps({"candidate_id": candidate["candidate_id"],
                                 "snapshot_id": candidate["snapshot_id"],
                                 "attempts": [{"status": "HTTP_OK"}], "settled": True,
                                 "result": {"status": "rejected", "http_calls": 1}}), encoding="utf-8")
    restarted = workbench.AstraWorkbench(
        wb.config, discovery_capture=fake_discovery, quote_capture=fake_quote_capture,
        risk_provider=positive_risk, llm_transport=lambda *a, **k: pytest.fail("LLM recalled"),
        clock_ms=lambda: T + 1000,
    )
    state = restarted.manual_review_state(T + 1000)
    assert state["inflight"] is None
    assert state["last_result"]["status"] == "rejected"
    assert state["last_result"]["snapshot_id"] == candidate["snapshot_id"]
    assert restarted._idempotency_result("restart-settled") == state["last_result"]


def test_review_restart_recovers_snapshot_scoped_receipt_without_new_llm(tmp_path):
    wb, snapshots = make_workbench(tmp_path, risk_provider=positive_risk)
    write_fmz(snapshots, native_gate())
    card = wb.refresh(now_ms=T)["candidates"][0]
    candidate = card["snapshot"]
    wb._locked_state_update(lambda state: state.setdefault("manual_review", {}).update(
        {"inflight": {"candidate_id": candidate["candidate_id"],
                      "snapshot_id": candidate["snapshot_id"], "side": card["side"],
                      "idempotency_key": "restart-new", "started_ts_ms": T}}))
    saved = workbench.review_core._review_state_path(
        wb.config.review_state_dir, candidate["candidate_id"], candidate["snapshot_id"])
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text(json.dumps({"candidate_id": candidate["candidate_id"],
                                 "snapshot_id": candidate["snapshot_id"],
                                 "attempts": [{"status": "HTTP_OK"}], "settled": True,
                                 "result": {"status": "rejected", "http_calls": 1}}), encoding="utf-8")
    restarted = workbench.AstraWorkbench(
        wb.config, discovery_capture=fake_discovery, quote_capture=fake_quote_capture,
        risk_provider=positive_risk, llm_transport=lambda *a, **k: pytest.fail("LLM recalled"),
        clock_ms=lambda: T + 1000,
    )
    state = restarted.manual_review_state(T + 1000)
    assert state["inflight"] is None
    assert state["last_result"]["snapshot_id"] == candidate["snapshot_id"]
    assert state["last_result"]["side"] == card["side"]
    assert restarted._idempotency_result("restart-new") == state["last_result"]


def test_review_restart_exposes_interruption_without_recall(tmp_path):
    wb, snapshots = make_workbench(tmp_path, risk_provider=positive_risk)
    write_fmz(snapshots, native_gate())
    candidate = wb.refresh(now_ms=T)["candidates"][0]["snapshot"]
    wb._locked_state_update(lambda state: state.setdefault("manual_review", {}).update({
        "inflight": {"candidate_id": candidate["candidate_id"],
                     "idempotency_key": "restart-incomplete", "started_ts_ms": T},
    }))
    restarted = workbench.AstraWorkbench(
        wb.config, discovery_capture=fake_discovery, quote_capture=fake_quote_capture,
        risk_provider=positive_risk, llm_transport=lambda *a, **k: pytest.fail("LLM recalled"),
        clock_ms=lambda: T + 1000,
    )
    state = restarted.manual_review_state(T + 1000)
    assert state["inflight"] is None
    assert state["last_result"]["status"] == "interrupted_no_automatic_recall"
    assert state["last_result"]["llm_call_count"] == 0


def test_review_state_api_reads_receipt_without_market_refresh(tmp_path):
    wb, _ = make_workbench(tmp_path)
    wb.discovery_capture = lambda **kwargs: pytest.fail("review state GET refreshed discovery")
    server = ThreadingHTTPServer(("127.0.0.1", 0), workbench.make_handler(wb))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/workbench/review-state", timeout=3) as response:
            body = json.load(response)
        assert body["schema"] == workbench.SCHEMA
        assert body["manual_review_state"]["inflight"] is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_review_post_rejects_disabled_gate_without_llm(tmp_path):
    calls = []
    wb, snapshots = make_workbench(tmp_path, risk_provider=positive_risk, transport=lambda *a, **k: calls.append(a))
    write_fmz(snapshots, native_gate("STRUCTURE_BLOCKED"))
    view = wb.refresh(now_ms=T)
    candidate_id = view["candidates"][0]["snapshot"]["candidate_id"]

    result = wb.review_candidate(candidate_id, "blocked-1", authorization="Bearer secret")

    assert result["status"] == "rejected"
    assert result["reason"] == "review_gate_disabled"
    assert calls == []


def test_missing_shared_budget_never_opens_review_or_creates_new_ledger(tmp_path):
    wb, snapshots = make_workbench(tmp_path, risk_provider=positive_risk)
    ledger_path = wb.config.shared_usage_ledger
    ledger_path.unlink()
    write_fmz(snapshots, native_gate())
    view = wb.refresh(now_ms=T)
    item = view["candidates"][0]
    assert item["review_gate"]["enabled"] is False
    assert "manual_review_service_not_configured" in item["review_gate"]["reasons"]
    assert view["manual_review_state"]["daily_http_budget"] is None
    result = wb.review_candidate(item["snapshot"]["candidate_id"], "no-budget", authorization="Bearer secret")
    assert result["status"] == "rejected"
    assert not ledger_path.exists()


def test_review_core_rejection_is_not_reported_as_success(tmp_path, monkeypatch):
    wb, snapshots = make_workbench(tmp_path, risk_provider=positive_risk)
    write_fmz(snapshots, native_gate())
    candidate_id = wb.refresh(now_ms=T)["candidates"][0]["snapshot"]["candidate_id"]
    monkeypatch.setattr(workbench.review_core, "run_review", lambda *args, **kwargs: {
        "status": "rejected", "http_calls": 1, "reviewed_snapshot_id": args[0]["snapshot_id"]})
    result = wb.review_candidate(candidate_id, "rejected-core", authorization="Bearer secret")
    assert result["status"] == "rejected"
    assert result["review"]["status"] == "rejected"
    assert result["llm_call_count"] == 1


def test_gate_blocks_current_view_while_old_snapshot_review_is_inflight(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    results = []
    errors = []

    def transport(api_key, model, request, timeout, endpoint=None):
        packet = json.loads(request["messages"][1]["content"].split("冻结候选：", 1)[1])
        snapshot = wb.ledger.state()["snapshots"][packet["snapshot_id"]]["snapshot"]
        entered.set()
        if not release.wait(timeout=3):
            raise TimeoutError("test transport was not released")
        return {"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(good_review(snapshot), ensure_ascii=False)}}]}

    wb, snapshots = make_workbench(tmp_path, risk_provider=positive_risk, transport=transport)
    write_fmz(snapshots, native_gate())
    before = wb.refresh(now_ms=T)
    candidate_id = before["candidates"][0]["snapshot"]["candidate_id"]
    old_snapshot_id = before["candidates"][0]["snapshot"]["snapshot_id"]

    def run_review():
        try:
            results.append(wb.review_candidate(candidate_id, "inflight-change", authorization="Bearer secret"))
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_review)
    worker.start()
    try:
        assert entered.wait(timeout=3)
        write_fmz(snapshots, native_gate("STRUCTURE_BLOCKED", ts=T), now=T + 1000)
        during = wb.refresh(now_ms=T + 1000)
        assert during["space_gate"]["state"] == "STRUCTURE_BLOCKED"
        assert all(not item["review_gate"]["enabled"] for item in during["candidates"])
        assert all(item["snapshot"]["snapshot_id"] != old_snapshot_id for item in during["candidates"])
    finally:
        release.set()
        worker.join(timeout=3)

    assert not worker.is_alive()
    assert errors == []
    assert results[0]["snapshot_id"] == old_snapshot_id
    latest = wb.refresh(now_ms=T + 1000)
    assert latest["space_gate"]["state"] == "STRUCTURE_BLOCKED"
    assert all(not item["review_gate"]["enabled"] for item in latest["candidates"])
    assert all(item["review"]["status"] == "not_attached" for item in latest["candidates"])


def test_old_producer_version_cannot_open_workbench_gate(tmp_path):
    wb, snapshots = make_workbench(tmp_path, risk_provider=positive_risk)
    row = write_fmz(snapshots, native_gate())
    row["decision"]["demo_version"] = "1.6.2"
    snapshots.write_text(json.dumps(row) + "\n", encoding="utf-8")
    view = wb.refresh(now_ms=T)
    assert view["space_gate"]["state"] == "STRUCTURE_BLOCKED"
    assert "fmz_producer_version_mismatch" in view["gaps"]
    assert all(not candidate["review_gate"]["enabled"] for candidate in view["candidates"])
