from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import astra_joint_inference as inference
import astra_joint_shadow as shadow
from astra_joint_dataset import canonical_hash, expiry_ms


MINUTE = 60_000


def ms(year: int, month: int, day: int, hour: int, minute: int = 0) -> int:
    return int(dt.datetime(year, month, day, hour, minute, tzinfo=dt.UTC).timestamp() * 1000)


def kline(open_ms: int, price: float = 80_000.0) -> list[object]:
    return [
        open_ms,
        str(price),
        str(price + 10),
        str(price - 10),
        str(price + 1),
        "10",
        open_ms + MINUTE - 1,
        str(price * 10),
        12,
        "5",
        str(price * 5),
        "0",
    ]


def constant_mdie(value: float):
    def _inner(klines, config=None, now_ms=None):
        return {
            "last_closed_bar_time": klines[-1]["close_time_ms"],
            "m_die": value,
            "score": abs(value),
            "direction": "UP" if value > 0 else "DOWN" if value < 0 else "NO_DIRECTION",
        }

    return _inner


def observation(as_of_ms: int, *, entry_ms: int | None = None, identity: str = "obs-1") -> dict[str, object]:
    return {
        "schema": "price_rebalance_candidate@1.0.0",
        "event_family": "price_rebalance_candidate",
        "episode_id": "episode-1",
        "observation_id": identity,
        "observation_kind": "cooldown",
        "as_of_ms": as_of_ms,
        "entry_ms": entry_ms if entry_ms is not None else as_of_ms + 1,
        "ret_15": 0.001,
        "ret_30": 0.002,
    }


def artifact() -> dict[str, object]:
    return {
        "schema": inference.MODEL_SCHEMA,
        "status": "available",
        "model_kind": "two_part_gam",
        "model_version": "unit-shadow-model",
        "feature_group": "joint",
        "training_cutoff": "2021-12-31",
        "scope": {"note": "unit"},
        "design_size": 4,
        "preprocess": {
            "version": "unit",
            "numeric_features": [
                {
                    "name": "ret_15",
                    "median": 0.0,
                    "mean": 0.0,
                    "scale": 1.0,
                    "spline": {"degree": 0, "knots": [-1.0, 1.0], "coefficient_matrix": [[1.0]], "basis_count": 1},
                },
                {
                    "name": "side_sign",
                    "median": -1.0,
                    "mean": 0.0,
                    "scale": 1.0,
                    "spline": {"degree": 0, "knots": [-2.0, 2.0], "coefficient_matrix": [[1.0]], "basis_count": 1},
                },
            ],
        },
        "logistic_model": {"link": "logit", "intercept": 0.0, "coef": [0.0, 0.0, 0.0, 0.0]},
        "gamma_model": {"link": "log", "intercept": math.log(0.25), "coef": [0.0, 0.0, 0.0, 0.0]},
    }


def write_artifact(tmp_path: Path) -> Path:
    path = tmp_path / "model.json"
    path.write_text(json.dumps(artifact()), encoding="utf-8")
    return path


def contracts(entry_ms: int, price: float = 80_000.0, *, settlement_period: str = "day", fee: float | None = 0.0003):
    expiry = expiry_ms(entry_ms)
    rows = []
    for option_type, strike in (
        ("put", price - 1000),
        ("put", price - 3000),
        ("call", price + 1000),
        ("call", price + 3000),
    ):
        name = f"BTC-UNIT-{int(strike)}-{'P' if option_type == 'put' else 'C'}"
        row = {
            "instrument_name": name,
            "option_type": option_type,
            "strike": float(strike),
            "expiration_timestamp": expiry,
            "creation_timestamp": 0,
            "settlement_period": settlement_period,
        }
        if fee is not None:
            row["taker_commission"] = fee
        rows.append(row)
    return rows


def books_for_contracts(rows, checked_at_ms: int, *, gross_negative: bool = False, tiny_credit: bool = False, future: bool = False):
    result = {}
    for item in rows:
        stamp = checked_at_ms + 1 if future else checked_at_ms - 1000
        if item["option_type"] == "put" and item["strike"] == 79_000:
            bid = 0.001 if gross_negative else 0.0021 if tiny_credit else 0.004
        elif item["option_type"] == "call" and item["strike"] == 81_000:
            bid = 0.004
        else:
            bid = 0.002
        result[item["instrument_name"]] = {"timestamp": stamp, "bids": [[bid, 1.5]], "asks": [[0.002, 1.5]]}
    return result


def test_negative_credit_quote_remains_available_but_not_credit_eligible():
    checked = ms(2026, 9, 15, 2)
    rows = contracts(checked)
    short = next(item for item in rows if item["option_type"] == "put" and item["strike"] == 79_000)
    long = next(item for item in rows if item["option_type"] == "put" and item["strike"] == 77_000)

    quote = shadow.quote_side("put", short, long, books_for_contracts(rows, checked, gross_negative=True), checked)

    assert quote["status"] == "available"
    assert quote["gross_credit_btc"] < 0
    assert quote["credit_eligible"] is False
    assert "不进入严格信用价差净表现组" in quote["credit_eligibility_reason_cn"]

    tiny = shadow.quote_side("put", short, long, books_for_contracts(rows, checked, tiny_credit=True), checked)
    assert tiny["status"] == "available"
    assert tiny["gross_credit_btc"] > 0
    assert tiny["net_credit_after_entry_fee_btc"] < 0
    assert tiny["credit_eligible"] is False


def test_quote_rejects_future_or_missing_fee_without_lowering_to_fake_market_result():
    checked = ms(2026, 9, 15, 2)
    rows = contracts(checked)
    names = {item["instrument_name"] for item in rows}
    assert "晚于检查时点" in shadow.validate_quote_group(names, books_for_contracts(rows, checked, future=True), checked)

    no_fee = contracts(checked, fee=None)
    short = next(item for item in no_fee if item["option_type"] == "call" and item["strike"] == 81_000)
    long = next(item for item in no_fee if item["option_type"] == "call" and item["strike"] == 83_000)
    quote = shadow.quote_side("call", short, long, books_for_contracts(no_fee, checked), checked)
    assert quote["status"] == "insufficient"
    assert "手续费" in quote["reason_cn"]


def test_make_assessment_does_not_include_outcome_or_settlement_fields():
    as_of = ms(2026, 9, 14, 23)
    entry = as_of + 1
    obs = observation(as_of, entry_ms=entry)
    rows = contracts(entry)
    checked = as_of + 2000
    names = {item["instrument_name"] for item in rows}
    group_error = shadow.validate_quote_group(names, books_for_contracts(rows, checked), checked)

    assessment = shadow.make_assessment(
        obs,
        80_000.0,
        rows,
        artifact(),
        canonical_hash(obs),
        books_for_contracts(rows, checked),
        checked,
        quote_group_error_cn=group_error,
    )

    assert assessment["status"] == "available"
    assert "settlement_price" not in json.dumps(assessment)
    assert assessment["sides"]["put"]["quote"]["delivery_fee_status"]["status"] == "exempt_daily_option"
    assert assessment["sides"]["put"]["quote"]["strict_net_result_ready"] is False


def test_collect_once_with_no_new_event_does_not_call_deribit(monkeypatch, tmp_path):
    at = ms(2026, 9, 15, 2, 1)
    calls = []

    def transport(base, params):
        calls.append((base, dict(params)))
        if "klines" in base:
            start = int(params.get("startTime", at - 2 * MINUTE))
            payload = [kline(start), kline(start + MINUTE), kline(start + 2 * MINUTE)]
            return payload, {"base": base}, json.dumps(payload).encode()
        raise AssertionError("Deribit should not be called without a new observation")

    monkeypatch.setattr(shadow, "_incremental_replay", lambda ledger, at: {"events": [], "observations": [], "clock_rows": []})

    result = shadow.collect_once(tmp_path, write_artifact(tmp_path), transport=transport, clock=lambda: at)

    assert result["status"] == "no_new_observation"
    assert result["options_http_attempts"] == 0
    assert all("deribit" not in base for base, _ in calls)


def test_collect_once_records_dte_excluded_without_option_requests(monkeypatch, tmp_path):
    at = ms(2026, 9, 15, 7, 58)
    obs = observation(at - 1, entry_ms=at, identity="excluded")
    calls = []

    def transport(base, params):
        calls.append((base, dict(params)))
        if "klines" in base:
            payload = [kline(at - 2 * MINUTE), kline(at - MINUTE), kline(at)]
            return payload, {"base": base}, json.dumps(payload).encode()
        raise AssertionError("DTE-excluded observation must not fetch options")

    monkeypatch.setattr(shadow, "_incremental_replay", lambda ledger, at: {"events": [], "observations": [obs], "clock_rows": []})

    result = shadow.collect_once(tmp_path, write_artifact(tmp_path), transport=transport, clock=lambda: at)

    assert result["new_decisions"] == 1
    assert result["excluded_observations"] == 1
    assert result["options_http_attempts"] == 0
    row = json.loads((tmp_path / "decisions.jsonl").read_text(encoding="utf-8").strip())
    assert row["assessment"]["status"] == "insufficient"
    assert "期限外" in row["assessment"]["reason_cn"]
    assert all("deribit" not in base for base, _ in calls)


def test_request_reservation_deduplicates_restarts_and_next_minute_is_incremental(monkeypatch, tmp_path):
    at = ms(2026, 9, 15, 2, 1)
    calls = []

    def transport(base, params):
        calls.append((base, dict(params)))
        if "klines" in base:
            start = int(params.get("startTime", at - 2 * MINUTE))
            payload = [kline(start), kline(start + MINUTE), kline(start + 2 * MINUTE)]
            return payload, {"base": base}, json.dumps(payload).encode()
        raise AssertionError("no option path expected")

    monkeypatch.setattr(shadow, "_incremental_replay", lambda ledger, at: {"events": [], "observations": [], "clock_rows": []})
    artifact_path = write_artifact(tmp_path)

    first = shadow.collect_once(tmp_path, artifact_path, transport=transport, clock=lambda: at)
    second = shadow.collect_once(tmp_path, artifact_path, transport=transport, clock=lambda: at)
    third = shadow.collect_once(tmp_path, artifact_path, transport=transport, clock=lambda: at + MINUTE)

    assert first["http_attempts"] == 2
    assert second["http_attempts"] == 0
    assert third["http_attempts"] == 2
    assert calls[0][1]["limit"] == 1500
    assert calls[1][1]["limit"] == 3
    assert calls[2][1]["limit"] == 1000
    assert "startTime" in calls[2][1]


def test_spot_forming_minute_supplies_open_only_without_closed_path(monkeypatch, tmp_path):
    at = ms(2026, 9, 14, 23, 1) + 2_000
    entry = (at // MINUTE) * MINUTE
    obs = observation(at - 1, entry_ms=entry, identity="forming-open")
    calls = []

    def transport(base, params):
        calls.append((base, dict(params)))
        if "klines" in base:
            start = int(params.get("startTime", entry - 2 * MINUTE))
            payload = [kline(start), kline(start + MINUTE), kline(entry, price=88_888.0)]
            return payload, {"base": base}, json.dumps(payload).encode()
        if "get_instruments" in base:
            return {"result": []}, {"base": base}, b'{"result":[]}'
        raise AssertionError("books should not be fetched when contracts are missing")

    monkeypatch.setattr(shadow, "_incremental_replay", lambda ledger, at: {"events": [], "observations": [obs], "clock_rows": []})

    result = shadow.collect_once(tmp_path, write_artifact(tmp_path), transport=transport, clock=lambda: at)

    assert result["new_decisions"] == 1
    assert result["options_http_attempts"] == 1
    ledger = shadow.Ledger(tmp_path)
    assert ledger.open_price("spot", entry) == pytest.approx(88_888.0)
    spot_row = next(row for row in ledger.bars("spot") if row["open_time_ms"] == entry)
    assert spot_row["forming_open_only"] is True
    assert spot_row["path_fields_usable"] is False
    assert "high" not in spot_row and "low" not in spot_row and "close" not in spot_row


def test_newest_closed_bar_open_requires_complete_available_minute(tmp_path):
    at = ms(2026, 9, 15, 2, 1)
    valid = shadow.normalize_kline(kline(at - 2 * MINUTE))
    assert valid is not None
    bad_newer = shadow.normalize_kline(kline(at - MINUTE))
    assert bad_newer is not None
    bad_newer["close_time_ms"] = int(bad_newer["open_time_ms"]) + MINUTE
    bad_newer["close_time"] = bad_newer["close_time_ms"]
    forming = {
        "open_time_ms": at,
        "open_time": at,
        "close_time_ms": at + MINUTE - 1,
        "close_time": at + MINUTE - 1,
        "open": 80_000.0,
        "forming_open_only": True,
        "path_fields_usable": False,
    }
    ledger = shadow.Ledger(tmp_path)
    with ledger.db:
        ledger.insert_bar("um", valid)
        ledger.insert_bar("um", bad_newer)
        ledger.insert_bar("um", forming)

    assert ledger.newest_closed_bar_open("um", at) == valid["open_time_ms"]


def test_closed_only_um_rejects_non_complete_minute_shape_and_keeps_raw(tmp_path):
    at = ms(2026, 9, 15, 2, 1)
    bad = kline(at - MINUTE)
    bad[6] = at
    ledger = shadow.Ledger(tmp_path)

    def transport(base, params):
        return [bad], {"base": base, "params": params}, json.dumps([bad]).encode()

    attempted, ok = shadow._fetch_market(ledger, tmp_path / "raw", "um", at, at // MINUTE, transport, closed_only=True)

    assert attempted == 1
    assert ok == 0
    assert ledger.bars("um") == []
    assert ledger.request_rows()[0]["status"] == "invalid_market_payload"
    assert list((tmp_path / "raw").glob("binance_um-*.json"))


def test_closed_only_um_ignores_forming_future_row_without_marking_market_ready(tmp_path):
    at = ms(2026, 9, 15, 2, 1) + 2_000
    forming = kline((at // MINUTE) * MINUTE)
    ledger = shadow.Ledger(tmp_path)

    def transport(base, params):
        return [forming], {"base": base, "params": params}, json.dumps([forming]).encode()

    attempted, ok = shadow._fetch_market(ledger, tmp_path / "raw", "um", at, at // MINUTE, transport, closed_only=True)

    assert attempted == 1
    assert ok == 0
    assert ledger.bars("um") == []
    assert ledger.request_rows()[0]["status"] == "latest_closed_minute_missing"


def test_forward_window_is_fixed_and_not_auto_extended(tmp_path):
    start = ms(2026, 9, 15, 0)
    ledger = shadow.Ledger(tmp_path)
    first = ledger.window(start)
    second = ledger.window(start + 10 * 86_400_000)

    assert first == second
    result = shadow.collect_once(tmp_path, tmp_path / "missing.json", transport=lambda *_: (_ for _ in ()).throw(AssertionError()), clock=lambda: first["end_ms"])
    assert result["status"] == "window_finished"
    assert result["window"]["end_ms"] == first["end_ms"]


def test_forward_window_refuses_model_replacement_before_any_http(tmp_path):
    at = ms(2026, 9, 15, 0)
    shadow.Ledger(tmp_path).window(at - 60_000)
    path = write_artifact(tmp_path)
    calls = []
    def transport(base, params):
        calls.append(base)
        return [], {}, b'[]'
    shadow.collect_once(tmp_path, path, transport=transport, clock=lambda: at)
    before = len(calls)
    data = json.loads(path.read_text('utf-8'))
    data['changed_model_metadata'] = True
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError, match='forward model changed'):
        shadow.collect_once(tmp_path, path, transport=transport, clock=lambda: at + 60_000)
    assert len(calls) == before


def test_forward_process_ledger_records_window_rows_and_deduplicates_restart(monkeypatch, tmp_path):
    start = ms(2026, 9, 15, 7, 50)
    at = ms(2026, 9, 15, 7, 58)
    shadow.Ledger(tmp_path).window(start)
    inside_obs = observation(at - 1, entry_ms=at, identity="forward-obs")
    inside_event = {
        "event_family": "price_rebalance_candidate",
        "episode_id": "episode-1",
        "event_type": "cooldown_confirmed",
        "as_of_ms": at - 30_000,
        "status": "candidate",
    }
    inside_clock = {
        "event_family": "price_rebalance_candidate",
        "episode_id": "episode-clock",
        "observation_id": "clock-1",
        "observation_kind": "clock_30m",
        "as_of_ms": at - 1,
    }
    warmup_event = {
        "event_family": "price_rebalance_candidate",
        "episode_id": "warmup",
        "event_type": "shock_started",
        "as_of_ms": start - MINUTE,
    }

    def transport(base, params):
        if "klines" in base:
            start_open = int(params.get("startTime", at - 2 * MINUTE))
            payload = [kline(start_open), kline(start_open + MINUTE), kline(start_open + 2 * MINUTE)]
            return payload, {"base": base}, json.dumps(payload).encode()
        raise AssertionError("DTE-excluded forward observation must not fetch Deribit")

    monkeypatch.setattr(
        shadow,
        "_incremental_replay",
        lambda ledger, at: {
            "events": [warmup_event, inside_event],
            "observations": [inside_obs],
            "clock_rows": [inside_clock],
        },
    )
    artifact_path = write_artifact(tmp_path)

    first = shadow.collect_once(tmp_path, artifact_path, transport=transport, clock=lambda: at)
    second = shadow.collect_once(tmp_path, artifact_path, transport=transport, clock=lambda: at)

    assert first["process_ledger"]["inserted"] == 3
    assert first["process_ledger"]["outside_window_ignored"] == 1
    assert first["process_ledger"]["by_kind"] == {"event": 1, "observation": 1, "clock": 1}
    assert second["status"] == "no_new_observation"
    assert second["process_ledger"]["duplicates"] == 3
    third = shadow.collect_once(tmp_path, artifact_path, transport=transport, clock=lambda: at + MINUTE)
    assert third["status"] == "no_new_observation"
    assert third["process_ledger"]["inserted"] == 0
    assert third["process_ledger"]["appended_revisions"] == 0
    assert third["process_ledger"]["prior_window_replayed"] == 3
    rows = shadow.Ledger(tmp_path).process_rows()
    assert [row["kind"] for row in rows] == ["event", "clock", "observation"]
    assert {row["process_id"] for row in rows} == {
        "event:price_rebalance_candidate:episode-1:cooldown_confirmed:candidate:" + str(at - 30_000),
        "clock:clock-1",
        "observation:forward-obs",
    }
    assert all(row["phase"] == "formal_forward" for row in rows)
    assert all(row["payload"]["forward_process_context"]["window_start_ms"] == start for row in rows)
    assert "observed_upper_ms" not in (tmp_path / "process_ledger.jsonl").read_text(encoding="utf-8")
    assert "warmup" not in (tmp_path / "process_ledger.jsonl").read_text(encoding="utf-8")


def test_sliding_replay_same_observation_identity_keeps_first_fact(monkeypatch, tmp_path):
    start = ms(2026, 9, 15, 7, 50)
    at = ms(2026, 9, 15, 7, 58)
    shadow.Ledger(tmp_path).window(start)
    calls = {"count": 0}

    def transport(base, params):
        if "klines" in base:
            payload = [kline(at - 2 * MINUTE), kline(at - MINUTE), kline(at)]
            return payload, {"base": base}, json.dumps(payload).encode()
        raise AssertionError("DTE-excluded forward observation must not fetch Deribit")

    def fake_replay(bars, as_of_upper_ms=None):
        calls["count"] += 1
        return {
            "events": [],
            "observations": [
                observation(
                    at - 1,
                    entry_ms=at,
                    identity="stable-sliding-id",
                )
                | {"ret_15": 0.001 if calls["count"] == 1 else None}
            ],
            "clock_rows": [],
        }

    monkeypatch.setattr(shadow, "_incremental_replay", fake_replay)
    artifact_path = write_artifact(tmp_path)

    first = shadow.collect_once(tmp_path, artifact_path, transport=transport, clock=lambda: at)
    second = shadow.collect_once(tmp_path, artifact_path, transport=transport, clock=lambda: at)

    assert first["process_ledger"]["inserted"] == 1
    assert second["process_ledger"]["identity_conflicts_ignored"] == 1
    rows = shadow.Ledger(tmp_path).process_rows()
    assert len(rows) == 1
    assert rows[0]["process_id"] == "observation:stable-sliding-id"
    assert rows[0]["payload"]["ret_15"] == 0.001
    assert rows[0]["revision"] == 1


def test_forward_window_prestart_current_observation_is_not_a_decision(monkeypatch, tmp_path):
    at = ms(2026, 9, 15, 7, 58)
    start = at - 30_000
    shadow.Ledger(tmp_path).window(start)
    obs = observation(start - 1, entry_ms=at, identity="before-window")

    def transport(base, params):
        if "klines" in base:
            payload = [kline(at - 2 * MINUTE), kline(at - MINUTE), kline(at)]
            return payload, {"base": base}, json.dumps(payload).encode()
        raise AssertionError("window-prestart observation must not fetch Deribit")

    monkeypatch.setattr(shadow, "_incremental_replay", lambda ledger, at: {"events": [], "observations": [obs], "clock_rows": []})

    result = shadow.collect_once(tmp_path, write_artifact(tmp_path), transport=transport, clock=lambda: at)

    assert result["status"] == "no_new_observation"
    assert result["new_decisions"] == 0
    assert result["process_ledger"]["outside_window_ignored"] == 1
    assert not (tmp_path / "decisions.jsonl").exists()


def test_incremental_replay_keeps_ids_across_3000_bar_sliding_restarts(tmp_path):
    start = ms(2026, 9, 1, 0)
    count = 3035
    rows = [shadow.normalize_kline(kline(start + index * MINUTE, 80_000.0 + index * 0.01)) for index in range(count)]
    assert all(row is not None for row in rows)
    mdie = constant_mdie(0.8)

    continuous_events = []
    continuous_observations = []
    continuous_clock = []
    runner = shadow.StreamingReplay(
        mdie_func=mdie,
        on_event=continuous_events.append,
        on_observation=continuous_observations.append,
        on_clock=continuous_clock.append,
    )
    for item in rows:
        runner.push(item)
    runner.finish()
    continuous_events = [item for item in continuous_events if item.get("event_type") != "open_at_replay_end"]

    ledger = shadow.Ledger(tmp_path)
    incremental_events = []
    incremental_observations = []
    incremental_clock = []
    warmup_count = 2995
    with ledger.db:
        for item in rows[:warmup_count]:
            ledger.insert_bar("um", item)
    warmup = shadow._incremental_replay(ledger, rows[warmup_count - 1]["close_time_ms"] + 1, mdie_func=mdie)
    incremental_events.extend(item for item in warmup["events"] if item.get("event_type") != "open_at_replay_end")
    incremental_observations.extend(warmup["observations"])
    incremental_clock.extend(warmup["clock_rows"])
    ledger.save_replay_snapshot(warmup["next_streaming_replay_snapshot"])

    for item in rows[warmup_count:]:
        ledger = shadow.Ledger(tmp_path)
        with ledger.db:
            ledger.insert_bar("um", item)
            ledger.trim_bars("um", int(item["open_time_ms"]) - shadow.MAX_CLOSED_CACHE_MINUTES * MINUTE)
        result = shadow._incremental_replay(ledger, item["close_time_ms"] + 1, mdie_func=mdie)
        incremental_events.extend(item for item in result["events"] if item.get("event_type") != "open_at_replay_end")
        incremental_observations.extend(result["observations"])
        incremental_clock.extend(result["clock_rows"])
        ledger.save_replay_snapshot(result["next_streaming_replay_snapshot"])

    assert len(shadow.Ledger(tmp_path).bars("um")) <= shadow.MAX_CLOSED_CACHE_MINUTES + 1
    assert json.dumps(incremental_events, sort_keys=True) == json.dumps(continuous_events, sort_keys=True)
    assert json.dumps(incremental_observations, sort_keys=True) == json.dumps(continuous_observations, sort_keys=True)
    assert json.dumps(incremental_clock, sort_keys=True) == json.dumps(continuous_clock, sort_keys=True)


def test_incremental_replay_requires_millisecond_after_db_bar_close(tmp_path):
    start = ms(2026, 9, 1, 0)
    bar = shadow.normalize_kline(kline(start, 80_000.0))
    assert bar is not None
    ledger = shadow.Ledger(tmp_path)
    with ledger.db:
        ledger.insert_bar("um", bar)

    at_close = shadow._incremental_replay(ledger, bar["close_time_ms"], mdie_func=constant_mdie(0.8))
    after_close = shadow._incremental_replay(ledger, bar["close_time_ms"] + 1, mdie_func=constant_mdie(0.8))

    assert at_close["streaming_snapshot"]["input_rows_processed"] == 0
    assert at_close["events"] == []
    assert after_close["streaming_snapshot"]["input_rows_processed"] == 1
    assert [item["event_type"] for item in after_close["events"]].count("shock_started") == 1


def test_incremental_replay_restores_active_episode_and_terminates_gap(tmp_path):
    start = ms(2026, 9, 1, 0)
    rows = [shadow.normalize_kline(kline(start + index * MINUTE, 80_000.0 + index)) for index in (0, 1, 5)]
    assert all(row is not None for row in rows)
    mdie = constant_mdie(0.8)
    ledger = shadow.Ledger(tmp_path)
    with ledger.db:
        for item in rows[:2]:
            ledger.insert_bar("um", item)
    first = shadow._incremental_replay(ledger, rows[1]["close_time_ms"] + 1, mdie_func=mdie)
    ledger.save_replay_snapshot(first["next_streaming_replay_snapshot"])

    ledger = shadow.Ledger(tmp_path)
    with ledger.db:
        ledger.insert_bar("um", rows[2])
        ledger.trim_bars("um", int(rows[2]["open_time_ms"]) - shadow.MAX_CLOSED_CACHE_MINUTES * MINUTE)
    second = shadow._incremental_replay(ledger, rows[2]["close_time_ms"] + 1, mdie_func=mdie)

    event_types = [item["event_type"] for item in second["events"]]
    assert "terminated_by_gap" in event_types
    gap = next(item for item in second["events"] if item["event_type"] == "terminated_by_gap")
    assert gap["gap_after_open_ms"] == rows[1]["open_time_ms"]
    assert gap["gap_next_open_ms"] == rows[2]["open_time_ms"]
    assert event_types.count("shock_started") == 1


def test_late_observation_is_gap_but_late_terminal_event_is_persisted(tmp_path):
    start = ms(2026, 9, 15, 7, 50)
    at = ms(2026, 9, 15, 7, 58)
    ledger = shadow.Ledger(tmp_path)
    window = ledger.window(start)
    replayed = {
        "events": [
            {
                "event_family": "price_rebalance_candidate",
                "episode_id": "episode-late",
                "event_type": "terminated_by_gap",
                "as_of_ms": at - 2 * MINUTE,
                "status": "terminated",
            }
        ],
        "observations": [observation(at - 2 * MINUTE, entry_ms=at - MINUTE, identity="late-observation")],
        "clock_rows": [],
    }

    summary = shadow._record_replay_process(ledger, replayed, at, window)
    rows = shadow.Ledger(tmp_path).process_rows()

    assert summary["by_kind"]["event"] == 1
    assert summary["late_or_recovered_gaps"] == 1
    assert [row["kind"] for row in rows] == ["event", "gap"]
    assert rows[1]["payload"]["missed_process_id"] == "observation:late-observation"


def test_open_at_end_process_snapshot_adds_distinct_later_status_without_overwriting(monkeypatch, tmp_path):
    start = ms(2026, 9, 15, 7, 50)
    at = ms(2026, 9, 15, 7, 58)
    shadow.Ledger(tmp_path).window(start)
    obs = observation(at - 1, entry_ms=at, identity="snapshot-obs")
    calls = {"count": 0}

    def transport(base, params):
        if "klines" in base:
            payload = [kline(at - 2 * MINUTE), kline(at - MINUTE), kline(at)]
            return payload, {"base": base}, json.dumps(payload).encode()
        raise AssertionError("DTE-excluded forward observation must not fetch Deribit")

    def fake_replay(bars, as_of_upper_ms=None):
        calls["count"] += 1
        status = "open_at_end" if calls["count"] == 1 else "reopened_after_more_bars"
        return {
            "events": [
                {
                    "event_family": "price_rebalance_candidate",
                    "episode_id": "episode-open",
                    "event_type": "episode_status",
                    "as_of_ms": at - 30_000,
                    "status": status,
                }
            ],
            "observations": [obs],
            "clock_rows": [],
        }

    monkeypatch.setattr(shadow, "_incremental_replay", fake_replay)
    artifact_path = write_artifact(tmp_path)

    first = shadow.collect_once(tmp_path, artifact_path, transport=transport, clock=lambda: at)
    second = shadow.collect_once(tmp_path, artifact_path, transport=transport, clock=lambda: at)

    assert first["process_ledger"]["inserted"] == 2
    assert second["process_ledger"]["inserted"] == 1
    assert second["process_ledger"]["appended_revisions"] == 0
    event_rows = [row for row in shadow.Ledger(tmp_path).process_rows() if row["kind"] == "event"]
    assert [row["revision"] for row in event_rows] == [1, 1]
    assert [row["payload"]["status"] for row in event_rows] == ["open_at_end", "reopened_after_more_bars"]


def test_outcome_is_append_only_and_does_not_overwrite_decision(tmp_path):
    as_of = ms(2026, 9, 14, 23)
    entry = as_of + 1
    obs = observation(as_of, entry_ms=entry)
    rows = contracts(entry)
    checked = as_of + 2000
    names = {item["instrument_name"] for item in rows}
    assessment = shadow.make_assessment(
        obs,
        80_000.0,
        rows,
        artifact(),
        canonical_hash(obs),
        books_for_contracts(rows, checked),
        checked,
        quote_group_error_cn=shadow.validate_quote_group(names, books_for_contracts(rows, checked), checked),
    )
    ledger = shadow.Ledger(tmp_path)
    decision = {"schema": "astra_joint_forward_decision@1.0.0", "observation": obs, "assessment": assessment}
    ledger.record_decision("obs-1", as_of, decision)
    before = (tmp_path / "decisions.jsonl").read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="not reached expiry"):
        shadow.settle_decision(tmp_path, "obs-1", 80_500.0, recorded_at_ms=as_of + 1000)

    expiry = assessment["sides"]["put"]["reference"]["expiry_ms"]
    outcome = shadow.settle_decision(tmp_path, "obs-1", 80_500.0, recorded_at_ms=expiry + 1)
    same = shadow.settle_decision(tmp_path, "obs-1", 82_000.0, recorded_at_ms=expiry + 2)

    assert outcome == same
    assert (tmp_path / "decisions.jsonl").read_text(encoding="utf-8") == before
    assert (tmp_path / "outcomes.jsonl").exists()
    assert outcome["source_provenance"]["kind"] == "manual_research_input"
    assert outcome["sides"]["put"]["strict_net_result_ready"] is False


def test_official_delivery_source_is_required_for_strict_net_result(tmp_path):
    as_of = ms(2026, 9, 14, 23)
    entry = as_of + 1
    obs = observation(as_of, entry_ms=entry, identity="official")
    rows = contracts(entry)
    checked = as_of + 2000
    names = {item["instrument_name"] for item in rows}
    assessment = shadow.make_assessment(
        obs,
        80_000.0,
        rows,
        artifact(),
        canonical_hash(obs),
        books_for_contracts(rows, checked),
        checked,
        quote_group_error_cn=shadow.validate_quote_group(names, books_for_contracts(rows, checked), checked),
    )
    shadow.Ledger(tmp_path).record_decision("official", as_of, {"observation": obs, "assessment": assessment})
    expiry = assessment["sides"]["put"]["reference"]["expiry_ms"]

    outcome = shadow.settle_decision(
        tmp_path,
        "official",
        80_500.0,
        recorded_at_ms=expiry + 1,
        source_provenance={"kind": "official_deribit_delivery", "date": "2026-09-15", "response": {"sha256": "unit"}},
    )

    assert outcome["source_provenance"]["kind"] == "official_deribit_delivery"
    assert outcome["sides"]["put"]["strict_net_result_ready"] is True


def test_publish_shadow_streams_recent_manifest_without_deleting_ledger(tmp_path):
    ledger = shadow.Ledger(tmp_path / "ledger")
    base = ms(2026, 9, 14, 0)
    for index in range(205):
        obs = observation(base + index * MINUTE, entry_ms=base + index * MINUTE + 1, identity=f"obs-{index:03d}")
        assessment = shadow._insufficient_assessment(obs, artifact(), canonical_hash(obs), "unit gap")
        ledger.record_decision(str(obs["observation_id"]), int(obs["as_of_ms"]), {"observation": obs, "assessment": assessment})

    result = shadow.publish_shadow(tmp_path / "ledger", tmp_path / "out")
    manifest = json.loads((tmp_path / "out" / "joint-shadow" / "manifest.json").read_text(encoding="utf-8"))
    decision_lines = (tmp_path / "ledger" / "decisions.jsonl").read_text(encoding="utf-8").splitlines()

    assert result["source_decisions"] == 205
    assert len(manifest["cards"]) == 200
    assert manifest["source_decisions"] == 205
    assert manifest["cards"][0]["card_id"] == "obs-204"
    assert manifest["cards"][-1]["card_id"] == "obs-005"
    assert len(decision_lines) == 205


def test_quantity_scaled_margin_return_is_invariant():
    one = shadow.return_on_margin(0.001, 2000, 80_000, quantity_btc=1.0)
    small = shadow.return_on_margin(0.001, 2000, 80_000, quantity_btc=0.3)

    assert small == pytest.approx(one)
