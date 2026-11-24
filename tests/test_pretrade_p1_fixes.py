"""Deterministic failure-reproducing regression tests for all six Pre-Trade P1 fixes."""

from __future__ import annotations

import copy
import datetime as dt
import json
import sys
import urllib.error
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from tools import astra_case_file_v1 as cf
from tools import astra_deribit_cooldown_v1 as deribit_cooldown
from tools import astra_kpf_light_v1 as light
from tools import astra_player_dossier_v1 as dossier
from tools import astra_player_tracking_v13 as tracking
from tools import astra_research_archive_v1 as archive
from tools import astra_research_selection_v1 as selection
from tools import astra_signal_producer_v1 as sp
from tools import astra_spatial_position_v1 as spatial
from tools import astra_underwriting_fact_context_v20 as fact_context
from tools import xxproject_server_health_v1 as health_check

from test_astra_kpf_light_v1 import _ms, _write_day, _write_full_window
from test_astra_player_tracking_v13 import EXCERPTS, RULE, post, sample, serving
from test_astra_workbench_v20 import make_workbench


# ---------------------------------------------------------------------------
# P1-1: Deribit pacing, bounded cooldown wait, and force=True cooldown guard
# ---------------------------------------------------------------------------

def test_p1_1_bound_method_pacing_and_retries_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = sp.NativeSignalRuntime(
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        force_deribit_pacing=False,  # CRITICAL: must work even without force flag
        overrides={
            "native_deribit_ticker_interval_ms": 50,
            "deribit_option_ticker_retries": 0,
        },
    )
    # Python bound-method lookup allocates a new wrapper each access: `is` was False, `==` must match
    assert runner.runtime.http.get_json == runner._orig_http_get_json

    sleep_calls: list[float] = []
    seen_retries: list[tuple[str, Any]] = []
    monkeypatch.setattr(runner, "_sleep_interruptible", lambda ms: sleep_calls.append(float(ms)))

    def fake_urlopen(req: Any, timeout: float = 5.0):  # pragma: no cover - replaced at HttpClient._open if used
        raise AssertionError("network must not be touched")

    class _FakeResp:
        def __init__(self, payload: dict[str, Any]):
            self._raw = json.dumps(payload).encode("utf-8")

        def read(self) -> bytes:
            return self._raw

        def __enter__(self):
            return self

        def __exit__(self, *_a: Any):
            return False

    orig_open = runner.runtime.deribit.public_get

    # Spy on HttpClient get_json via underlying class __func__ so rt.http.get_json stays bound-equal to _orig
    orig_cls_get_json = type(runner.runtime.http).get_json

    def spy_cls_get_json(self_http: Any, url: str, params=None, headers=None, timeout_sec=None, retries=None):
        seen_retries.append((url, retries))
        if "public/get_instruments" in url:
            return {"quality": "OK", "data": {"jsonrpc": "2.0", "result": []}, "error": None}
        if "public/get_index_price" in url:
            return {"quality": "OK", "data": {"jsonrpc": "2.0", "result": {"index_price": 90_000.0}}, "error": None}
        return {"quality": "OK", "data": {"jsonrpc": "2.0", "result": {"instrument_name": "X"}}, "error": None}

    monkeypatch.setattr(type(runner.runtime.http), "get_json", spy_cls_get_json)
    runner._orig_http_get_json = runner.runtime.http.get_json

    runner.runtime.deribit.public_get("public/get_index_price", {"index_name": "btc_usd"})
    runner.runtime.deribit.public_get("public/get_instruments", {"currency": "BTC", "kind": "option"})
    runner.runtime.deribit.public_get("public/ticker", {"instrument_name": "BTC-16OCT26-90000-C"})

    assert len(seen_retries) == 3
    assert all(r == 0 for _u, r in seen_retries), f"all public Deribit calls must pass retries=0: {seen_retries}"
    # 2nd and 3rd calls must wait for _deribit_ticker_interval_ms (~50ms)
    assert len(sleep_calls) >= 2 and all(0 < ms <= 50.0 for ms in sleep_calls)

    # Monkeypatching rt.http.get_json directly still yields to caller/tests when force_deribit_pacing=False
    monkeypatch.setattr(type(runner.runtime.http), "get_json", orig_cls_get_json)
    runner.runtime.http.get_json = lambda url, params=None, headers=None, timeout_sec=None, retries=None: (
        seen_retries.append((url, retries)) or {"quality": "OK", "data": {"jsonrpc": "2.0", "result": {}}}
    )
    sleep_calls.clear()
    runner.runtime.deribit.public_get("public/ticker", {"instrument_name": "BTC-16OCT26-90000-P"})
    assert sleep_calls == []


def test_p1_1_archive_deribit_bounded_cooldown_wait_and_workbench_force_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    cd_file = tmp_path / "deribit_cooldown.json"
    monkeypatch.setenv(deribit_cooldown.ENV_KEY, str(cd_file))

    fake_now = [1_700_000_000_000]
    monkeypatch.setattr(deribit_cooldown, "_now_ms", lambda v=None: fake_now[0] if v is None else int(v))
    deribit_cooldown.record_cooldown(cd_file, cooldown_until_ms=fake_now[0] + 3_000, source="test", now_ms=fake_now[0])

    slept: list[float] = []

    def fake_sleep(sec: float) -> None:
        slept.append(sec)
        fake_now[0] += int(sec * 1000)

    monkeypatch.setattr(archive.Deribit, "_get", staticmethod(lambda path, params: {"instrument_name": params["instrument_name"], "mark_price": 0.01}))
    live_deribit = archive.Deribit(min_book_interval_s=0.1, max_cooldown_wait_s=15.0, sleep=fake_sleep)
    book = live_deribit.book("BTC-20NOV26-90000-P")
    assert book["instrument_name"] == "BTC-20NOV26-90000-P"
    assert slept == [pytest.approx(3.25)]

    # Cooldown > max_cooldown_wait_s still fast-fails without sleeping
    deribit_cooldown.record_cooldown(cd_file, cooldown_until_ms=fake_now[0] + 30_000, source="test", now_ms=fake_now[0])
    slept.clear()
    with pytest.raises(ValueError, match="cooldown active"):
        live_deribit.book("BTC-20NOV26-90000-P")
    assert slept == []

    # Workbench refresh_expiry_gamma(force=True) MUST NOT bypass active shared cooldown
    wb, _ = make_workbench(tmp_path / "wb")
    fake_wall = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    deribit_cooldown.record_cooldown(cd_file, cooldown_until_ms=fake_wall + 30_000, source="test", now_ms=fake_wall)
    wb.expiry_gamma_get_json = lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("HTTP must defer under cooldown"))
    wb._expiry_gamma = {"schema": "expiry_gamma@1", "status": "ok"}
    res = wb.refresh_expiry_gamma(now_ms=fake_wall, force=True)
    assert res == {"schema": "expiry_gamma@1", "status": "ok"}


# ---------------------------------------------------------------------------
# P1-2: AI shadow vs human attribution, legacy exclusion, outcome modes, coexistence
# ---------------------------------------------------------------------------

def test_p1_2_actor_isolation_legacy_exclusion_and_coexistence(tmp_path: Path):
    wb, _d, _hand, now, _expiry = sample(tmp_path / "wb", "Accept / Call", manual=True)
    human_ep = next(ep for ep in wb.locked_candidates.list() if ep["lock_actor"] == "USER")
    put_point = wb.locked_candidates.read_point(human_ep["t0_baseline_point_id"])

    # Formal scheduled AI shadow lock coexists on the SAME Put contract without modifying the human episode
    sched_origin = {
        "plan_id": "plan-daily-r16",
        "hand_id": "hand_sched_1",
        "dossier_id": "Deribit_BTC_test",
        "decision_id": "dec_1",
        "decided_at_ms": now[0],
        "decision_recorded_at_ms": now[0],
        "condition_sha256": "a" * 64,
        "lock_mode": "DELAYED_LOCK",
    }
    ep2_res = wb.locked_candidates.lock(
        put_point,
        request_key="shadow_key_" + "2" * 16,
        now_ms=now[0] + 1_000,
        shadow_origin=sched_origin,
    )
    assert ep2_res["created"] is True
    shadow_ep = ep2_res["episode"]
    assert shadow_ep["episode_id"] != human_ep["episode_id"]
    assert shadow_ep["lock_actor"] == "AI_SHADOW"

    # Symmetric case: formal scheduled AI shadow locks Call FIRST, then human locks Call -> gets separate USER episode
    wb2, d2, call_hand, now2, _ = sample(tmp_path / "sym", "Accept / Call", manual=False)
    with serving(wb2) as base:
        svc = post(base, wb2)
        call_rec = tracking.link(d2, call_hand, svc, RULE)
        call_pt = wb2.locked_candidates.read_point(call_rec["t0"]["point_id"])
        call_shadow_ep = call_rec["t0"]["tracker_episode_id"]
        hu_res = wb2.locked_candidates.lock(call_pt, request_key="human_after_shadow_" + "4" * 10, now_ms=now2[0] + 2_000)
        assert hu_res["episode"]["episode_id"] != call_shadow_ep
        assert hu_res["episode"]["lock_actor"] == "USER"

    # collect_decisions emits actor="ai_shadow" for shadow episodes and actor="human" for human episodes
    full_eps, _ = archive._workbench_decisions(wb.config.state_dir)
    dec_events = archive.collect_decisions(full_eps, [])
    by_ref = {ev["ref_id"]: ev for ev in dec_events}
    assert by_ref[human_ep["episode_id"]]["actor"] == "human"
    assert by_ref[shadow_ep["episode_id"]]["actor"] == "ai_shadow"
    assert by_ref[shadow_ep["episode_id"]]["detail"]["plan_id"] == "plan-daily-r16"

    # human_locks excludes BOTH actor="ai_shadow" AND historical chained actor="human" belonging to ai_shadow episode
    arch_root = tmp_path / "research_archive"
    (arch_root / "decisions").mkdir(parents=True)
    legacy_polluted = {**by_ref[shadow_ep["episode_id"]], "actor": "human", "detail": {"candidate_id": human_ep["candidate_id"]}}
    (arch_root / "decisions" / "2026-10.jsonl").write_text(
        json.dumps(by_ref[human_ep["episode_id"]]) + "\n"
        + json.dumps(by_ref[shadow_ep["episode_id"]]) + "\n"
        + json.dumps(legacy_polluted) + "\n",
        encoding="utf-8",
    )
    humans = selection.human_locks(arch_root, {}, workbench_dir=wb.config.state_dir)
    assert [row["ref_id"] for row in humans] == [human_ep["episode_id"]]


def test_p1_2_dossier_settle_explicit_lock_and_management_modes(tmp_path: Path):
    wb, d, hand_ok, now, expiry = sample(tmp_path, "Accept / Call", manual=False)
    # Add a second unfilled Accept hand blocked at shadow_handoff_expired
    source_rec = dossier.read(hand_ok / "evidence" / "source.json")
    raw_bytes = (hand_ok / "responses" / "fixture-reply" / "response.raw").read_bytes()
    hand_blocked = d.expect(expiry, "STARTUP_AD_HOC", reference="unfilled-expired")
    d.deal(hand_blocked, source_rec)
    d.respond(hand_blocked, "reply-blk", "RECEIVED", raw_bytes, EXCERPTS, model="fixture-model-1.3", requested=now[0], decided=now[0])
    blk_dir = hand_blocked / "tracking"
    blk_dir.mkdir(parents=True, exist_ok=True)
    dossier.write(blk_dir / "pending_lock.json", dossier.encode({"state": "LOCK_BLOCKED", "last_reason": "quote_unavailable"}))
    dossier.write(blk_dir / "final_block.json", dossier.encode({"state": "LOCK_BLOCKED", "reason": "shadow_handoff_expired", "recorded_at_ms": now[0] + 60_000}))

    with serving(wb) as base:
        service = post(base, wb)
        pending_dir = hand_ok / "tracking"
        pending_dir.mkdir(parents=True, exist_ok=True)
        dossier.write(pending_dir / "pending_lock.json", dossier.encode({"state": "PENDING_LOCK", "attempts": [{"reason": "tracking_quote_stale_or_unsynced"}], "last_reason": "tracking_quote_stale_or_unsynced"}))
        rec = tracking.link(d, hand_ok, service, RULE)
        assert rec["lock_mode"] == "DELAYED_LOCK"

    now[0] = expiry + dossier.MINUTE
    d.summary(expiry)
    manifest = dossier.read(d.path(expiry) / "manifest.json")
    assert manifest["lock_counts"] == {"DELAYED_LOCK": 1, "LOCK_BLOCKED": 1}

    pts = [[m, 95_000.0] for m in range(now[0] - 20 * 60_000, expiry + 1, dossier.MINUTE)]
    out_dir = d.official(expiry, {"index_name": "btc_usd", "expiry_ms": expiry, "points": pts})
    outcome = dossier.read(out_dir / "outcome.json")
    by_hand = {row["hand_id"]: row for row in outcome["hands"]}
    assert by_hand[hand_ok.name]["lock_status"] == "DELAYED_LOCK"
    assert by_hand[hand_ok.name]["delayed_lock"] is True
    assert by_hand[hand_ok.name]["management_status"] == "HOLD_TO_EXPIRY"
    assert by_hand[hand_ok.name]["t0_net_credit_after_entry_fee_btc"] is not None

    assert by_hand[hand_blocked.name]["lock_status"] == "LOCK_BLOCKED"
    assert by_hand[hand_blocked.name]["lock_reason"] == "shadow_handoff_expired"
    assert by_hand[hand_blocked.name]["management_status"] == "NOT_APPLICABLE"
    assert by_hand[hand_blocked.name]["t0_net_credit_after_entry_fee_btc"] is None
    assert by_hand[hand_blocked.name]["hold_to_expiry_shadow_net_btc"] is None


# ---------------------------------------------------------------------------
# P1-3: Closed-bar temporal causality in condition_at_t0, episode, _merge_index_points, _run_due_checks
# ---------------------------------------------------------------------------

def test_p1_3_closed_bar_causality_and_check_due_delay(tmp_path: Path):
    wb, d, hand, now, expiry = sample(tmp_path, "Accept / Call")
    proof = tracking.accepted(hand)
    proof_src = proof["source"]
    cutoff = min(c["read_at_ms"] for c in proof_src["cards"])
    proof_src["history"] = [
        {"schema": "structure_snapshot@0.1", "expiry_ms": expiry, "at_ms": cutoff - 60_000, "available_at_ms": cutoff - 60_000,
         "pin_status": "ok", "pin_method": "p", "pin_source": "s", "profile": {"walls": {"pin": 100_000.0}}}
    ]
    proof_src["index_path"] = dict(
        proof_src["index_path"],
        timestamp_semantics="unverified_minute_label",
    )
    # Inject a mutated open bucket AT cutoff (bucket [cutoff, cutoff + 60_000) is unclosed at cutoff)
    open_pts = [row for row in proof_src["index_path"]["points"] if row[0] < cutoff] + [[cutoff, 77_777.0]]
    proof_src["index_path"]["points"] = open_pts
    proof["decision"]["judgment"]["depends_on_ard"] = True

    plan = dict(RULE, acceptance="accept", ard_responsibility_meaning=EXCERPTS["put"], ard_review_on=EXCERPTS["call"])
    snap = wb.cached_view()["candidates"][1]["snapshot"]
    frozen = tracking.condition_at_t0(proof, plan, snap, now[0])
    # last observed point must exclude unclosed bucket at `cutoff` (last closed <= cutoff - 60_000)
    assert frozen["ard_basis"]["facts"]["last"]["t"] <= cutoff - dossier.MINUTE
    assert frozen["ard_basis"]["facts"]["last"]["price"] != 77_777.0

    # _merge_index_points & _run_due_checks exclude unclosed minute bucket [at, at + 60_000)
    merged = tracking._merge_index_points(None, expiry, now[0], public_fn=lambda: [[now[0] - dossier.MINUTE, 100_000.0], [now[0], 99_999.0]])
    assert merged["timestamp_semantics"] == "unverified_minute_label"
    assert [t for t, _ in merged["points"]] == [now[0] - dossier.MINUTE]

    first_tick = ((now[0] // dossier.MINUTE) + 1) * dossier.MINUTE
    ep_dir = tmp_path / "ep_check"
    ep_dir.mkdir()
    frozen_cond = {
        "contract": tracking.contract_fields(proof["contract"]),
        "entry_at_ms": now[0],
        "entry_index_usd": 100_000.0,
        "historical_facts": ["f"],
        "accepted_conditions": ["c"],
        "invalidation": "new_entry",
        "invalidation_meaning": dossier.condition.RULES["new_entry"],
        "check_clock": {"first_at_ms": first_tick, "interval_ms": dossier.MINUTE},
        "sampling": dossier.condition.SAMPLING,
    }
    (ep_dir / "condition.json").write_text(json.dumps(frozen_cond), encoding="utf-8")
    (ep_dir / "events").mkdir()

    pts_series = [[first_tick - dossier.MINUTE, 100_000.0], [first_tick, 100_100.0]]
    idx_closed = {"index_name": "btc_usd", "expiry_ms": expiry, "points": pts_series}
    now[0] = first_tick
    ev_early, _ = tracking._run_due_checks(d, ep_dir, first_tick, index_path=idx_closed)
    assert ev_early == []
    now[0] = first_tick + dossier.MINUTE
    ev_closed, _ = tracking._run_due_checks(d, ep_dir, first_tick + dossier.MINUTE, index_path=idx_closed)
    assert len(ev_closed) == 1 and ev_closed[0]["observed_at_ms"] == first_tick


# ---------------------------------------------------------------------------
# P1-4: Partial bar checkpoint + backlog fallback + GEX LKGV / enrichment stale
# ---------------------------------------------------------------------------

def test_p1_4_checkpoint_partial_bar_and_backlog_kline_fallback(tmp_path: Path):
    assert sp.DEFAULT_BARS_RESTORE_MAX_AGE_MS == 90_000
    logs_dir = tmp_path / "logs"
    state_dir = tmp_path / "state"
    now_box = [1_760_000_000_000]
    core1 = sp.load_signal_core(unique_module=True)[0]
    core1.now_ms = lambda: now_box[0]
    r1 = sp.NativeSignalRuntime(logs_dir=logs_dir, state_dir=state_dir, core_mod=core1, core_sha256="sha1")
    r1.runtime.bars.completed_bars.append({"bar_index": 7, "close": 90_000.0, "total_volume": 10.0, "cvd_delta": 1.0, "complete_ts_ms": now_box[0] - 5_000})
    r1.runtime.bars.bar_index = 7
    r1.runtime.bars.last_trade_id = 555
    r1.runtime.bars.current_open = 90_100.0
    r1.runtime.bars.current_high = 90_200.0
    r1.runtime.bars.current_low = 90_050.0
    r1.runtime.bars.current_close = 90_150.0
    r1.runtime.bars.current_volume = 4.25
    r1.runtime.bars.current_cvd = -1.75
    r1.runtime.bars.current_last_trade_ts_ms = now_box[0] - 1_000
    r1.save_checkpoint(now_ms=now_box[0])

    # Within 90s: partial bar restored intact
    now_box[0] += 60_000
    core2 = sp.load_signal_core(unique_module=True)[0]
    core2.now_ms = lambda: now_box[0]
    r2 = sp.NativeSignalRuntime(logs_dir=logs_dir, state_dir=state_dir, core_mod=core2, core_sha256="sha1")
    assert r2.checkpoint_restored["bars_restored"] is True
    assert r2.runtime.bars.current_volume == pytest.approx(4.25)
    assert r2.runtime.bars.current_cvd == pytest.approx(-1.75)
    assert r2.runtime.bars.current_open == pytest.approx(90_100.0)

    # First tick after restore: if drain stays backlogged, hook clears bars and rebuilds via 1m backfill
    backfill_called = [0]
    poll_calls = [0]

    def fake_backfill() -> dict[str, Any]:
        backfill_called[0] += 1
        r2.runtime.bars.bar_index = 1
        r2.runtime.bars.completed_bars.append({"bar_index": 1, "close": 90_200.0, "total_volume": 10.0, "cvd_delta": 2.0, "complete_ts_ms": now_box[0] - 60_000})
        return {"applied": True, "bar_count": 1}

    def fake_poll_only() -> list[Any]:
        poll_calls[0] += 1
        if poll_calls[0] == 1:
            r2.runtime.bars.last_cycle_metrics = {"quality": "OK", "backlogged": True}
            return []
        r2.runtime.bars.last_cycle_metrics = {"quality": "OK", "backlogged": False}
        return [{"bar_index": 2}]

    # Rewire inner drain target for unit verification by wrapping inner closure cleanly
    r2.runtime._backfill_volume_bars = fake_backfill
    r2._pending_bars_restore_catchup_check = True
    # Temporarily test fallback branch via fresh runtime wrapping orig_poll_with_drain
    r2.runtime.bars.poll = fake_poll_only
    # Simulate orig_poll_with_drain returning backlogged on 1st call and caught_up on 2nd
    orig_inner_calls = [0]

    def sim_fetch_agg_trades(from_id=None):
        orig_inner_calls[0] += 1
        if orig_inner_calls[0] == 1:
            # hit max_rounds (5 rounds of limit=1000) when from_id is set after restart
            return {"quality": "OK", "data": [{"id": i, "a": i, "p": "90000", "q": "0.001", "T": now_box[0] - 500, "m": False} for i in range(1000)]}
        return {"quality": "OK", "data": [{"id": 9999, "a": 9999, "p": "90200", "q": "0.01", "T": now_box[0] - 100, "m": False}]}

    core_fb = sp.load_signal_core(unique_module=True)[0]
    core_fb.now_ms = lambda: now_box[0]
    r_fb = sp.NativeSignalRuntime(logs_dir=logs_dir, state_dir=state_dir, core_mod=core_fb, core_sha256="sha1")
    r_fb.runtime._backfill_volume_bars = lambda: (backfill_called.__setitem__(0, backfill_called[0] + 1) or {"applied": True, "bar_count": 1})
    r_fb.runtime.bars.config["agg_trades_limit"] = 5
    r_fb.runtime.bars.config["max_drain_rounds"] = 2
    round_no = [0]

    def fake_agg_trades(from_id=None):
        round_no[0] += 1
        if from_id is not None and round_no[0] <= 2:
            return {"quality": "OK", "data": [{"a": 556 + round_no[0] * 10 + i, "p": "90100", "q": "0.1", "T": now_box[0] - 500, "m": False} for i in range(5)]}
        return {"quality": "OK", "data": [{"a": 9001, "p": "90200", "q": "0.1", "T": now_box[0] - 100, "m": False}]}

    r_fb.runtime.bars.fetch_agg_trades = fake_agg_trades
    r_fb.runtime.bars.poll_with_drain()
    assert backfill_called[0] >= 1
    assert r_fb.runtime.bars.last_cycle_metrics["backlogged"] is False
    assert r_fb.runtime.bars.last_trade_id == 9001

    # > 90s outage skips partial-bar restore
    now_box[0] += 120_000
    core3 = sp.load_signal_core(unique_module=True)[0]
    core3.now_ms = lambda: now_box[0]
    r3 = sp.NativeSignalRuntime(logs_dir=logs_dir, state_dir=state_dir, core_mod=core3, core_sha256="sha1")
    assert r3.checkpoint_restored["bars_restored"] is False


def test_p1_4_gex_lkgv_and_enrichment_stale_propagation(tmp_path: Path):
    core = sp.load_signal_core(unique_module=True)[0]
    runner = sp.NativeSignalRuntime(
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        core_mod=core,
        core_sha256="sha1",
        overrides={"gex_info_base_url": "http://127.0.0.1:8000"},
    )
    now_ms = 1_760_000_000_000
    runner.runtime.http.get_json = lambda *_a, **_kw: {
        "quality": "OK",
        "data": {
            "asset": "BTC",
            "fetched_at": "2026-10-15T12:00:00+00:00",
            "stale": False,
            "availability": "ready",
            "enrichment_stale": True,
            "enrichment_availability": "partial",
            "gex_board": {"total_net_gex": 1.2e8, "dvol": 45.0, "market_state": "positive_gamma"},
            "gamma_exposure": {"flip_point": 89000.0, "n1": 87000.0, "p1": 92000.0},
            "volatility": {"atm_iv": 48.0, "term_structure": [{"expiry": "20OCT26", "days_to_expiry": 1, "atm_iv": 48.0, "skew_25d": 1.0}]},
            "flow": {"pcr": 0.75},
        },
        "error": None,
    }
    snap = runner.runtime.gex_info.refresh()
    assert snap["stale"] is False
    assert snap["enrichment_stale"] is True
    assert snap["enrichment_availability"] == "partial"

    # Case file: core L3.gex.structure stays ok when core is live, while L4.iv and L5.options_flow are marked stale
    base_snapshot = {
        "snapshot_id": "a" * 64,
        "contract": {"side": "put", "short_instrument": "BTC-20OCT26-82000-P", "long_instrument": "BTC-20OCT26-80000-P",
                     "short_strike_usd": 82000.0, "long_strike_usd": 80000.0, "width_usd": 2000.0, "quantity_btc": 1.0, "expiry_ms": now_ms + 86_400_000},
        "market": {"valuation_ts_ms": now_ms, "quote": {"credit_btc": 0.002, "gap_reasons": []}},
        "fact_context": fact_context._clean({
            "status": "current",
            "snapshot_ts_ms": now_ms,
            "market_facts": {
                "space_context": fact_context._space_context({"gex_info": snap}, None, None),
            },
        }),
    }
    case = cf.build(snapshot=base_snapshot, raw_market={"short_book": {}, "long_book": {}}, asof_ms=now_ms)
    l3_gex = case["layers"]["L3"][case["refs"]["L3.gex.structure"]["index"]]
    l4_iv = case["layers"]["L4"][case["refs"]["L4.iv"]["index"]]
    l5_flow = case["layers"]["L5"][case["refs"]["L5.options_flow"]["index"]]
    assert l3_gex["quality"] == "ok"
    assert l4_iv["quality"] == "stale" and l4_iv["quality_reason"] == "gex_enrichment_stale"
    assert l5_flow["quality"] == "stale" and l5_flow["quality_reason"] == "gex_enrichment_stale"

    # Now upstream fails -> gex_info falls back to LKGV cache -> stale MUST become True!
    runner.runtime.http.get_json = lambda *_a, **_kw: {"quality": "ERROR", "data": None, "error": "down"}
    lkgv = runner.runtime.gex_info.refresh()
    assert lkgv["data_state"] == "lkgv_cache"
    assert lkgv["quality"] == "STALE"
    assert lkgv["stale"] is True

    # Spatial position must also reject data_state='lkgv_cache' even if stale flag was omitted
    sp_snap = {
        "contract": {"side": "put", "short_strike_usd": 83_000.0, "long_strike_usd": 81_000.0, "expiry_ms": now_ms + 86_400_000, "quantity_btc": 1.0},
        "market": {"reference_price_usd": 84_000.0, "valuation_ts_ms": now_ms},
        "fact_context": {"market_facts": {"space_context": {
            "anchor": {"effective_flip_point": 82_000.0, "gex_freshness": "FRESH"},
            "gex_info": {"data_state": "lkgv_cache", "quality": "STALE"},
        }}},
    }
    assert spatial.assess(sp_snap)["state"] == "unknown"


# ---------------------------------------------------------------------------
# P1-5: Release permissions, frontend symlink, window active guard, prune guard, templates
# ---------------------------------------------------------------------------

def test_p1_5_release_symlink_prune_guard_and_templates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import importlib.util

    build_spec = importlib.util.spec_from_file_location("b_rel", ROOT / "deploy" / "xxproject_server" / "build_release.py")
    dep_spec = importlib.util.spec_from_file_location("d_rel", ROOT / "deploy" / "xxproject_server" / "deploy_release.py")
    builder = importlib.util.module_from_spec(build_spec)
    deployer = importlib.util.module_from_spec(dep_spec)
    build_spec.loader.exec_module(builder)
    dep_spec.loader.exec_module(deployer)

    assert "deploy/astra_map_data/config.example.json" in deployer.ENV_TEMPLATES

    # Verify the 4 fixed templates have zero legacy FMZ / Ubuntu paths
    templates = [
        ROOT / "deploy" / "signal_audit" / "workbench-v20.env.example",
        ROOT / "deploy" / "signal_audit" / "signal-audit-llm.env.example",
        ROOT / "05_GEX监控API_数据增强接口" / "deploy" / "gexmonitorapi.env.example",
        ROOT / "deploy" / "astra_map_data" / "config.example.json",
    ]
    for path in templates:
        text = path.read_text(encoding="utf-8")
        assert "/home/bitnami/fmz2" not in text, f"legacy FMZ path in {path}"
        assert "/home/ubuntu" not in text, f"legacy ubuntu path in {path}"
        assert "/opt/astra-map-data/kpf/current.json" not in text

    assert "ASTRA_SIGNAL_BARS_RESTORE_MAX_AGE_MS=90000" in (ROOT / "deploy" / "signal_producer" / "signal-producer.env.example").read_text(encoding="utf-8")
    install_sh = (ROOT / "deploy" / "xxproject_server" / "install_release.sh").read_text(encoding="utf-8")
    assert "umask 022" in install_sh and "--apply-units" in install_sh

    # Test frontend symlink creation & pre-existing directory backup on install
    pkg = tmp_path / "pkg.tar.gz"
    builder.build_release(ROOT, pkg, source_commit="e" * 40, allow_dirty=True)
    prefix = tmp_path / "host"
    legacy_dir = prefix / "opt" / "signal-audit" / "workbench-v3"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "old.txt").write_text("legacy", encoding="utf-8")

    inst = deployer.cmd_install(pkg, prefix, apply_units=False, no_systemd=True)
    assert inst["frontend_link"]["synced"] is True
    assert inst["frontend_link"]["backup"] is not None
    assert (prefix / "opt" / "signal-audit" / "workbench-v3" / "index.html").is_file()

    # _prune_old_releases must NEVER delete a release listed in keep_ids (e.g. active PID cwd)
    app_root = prefix / "opt" / "xxproject"
    for name in ("rel-active", "rel-current", "rel-prev"):
        (app_root / "releases" / name).mkdir(parents=True, exist_ok=True)
    pruned = deployer._prune_old_releases(app_root, keep_ids={"rel-active", "rel-current", "rel-prev"}, keep_count=2)
    assert "rel-active" not in pruned and (app_root / "releases" / "rel-active").is_dir()

    # Active unit check refuses deploy when mocked active on Linux root
    monkeypatch.setattr(deployer, "_is_system_root", lambda _p: True)
    monkeypatch.setattr(deployer.os, "name", "posix")
    monkeypatch.setattr(deployer.Path, "is_dir", lambda p: True if str(p).endswith("/run/systemd/system") or "systemd" in str(p) else Path.exists(p))

    class _FakeSub:
        stdout = "active\n"

    monkeypatch.setattr(deployer.subprocess, "run", lambda *_a, **_k: _FakeSub())
    safe_ms = int(dt.datetime(2026, 11, 23, 10, 0, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
    with pytest.raises(RuntimeError, match="UNSAFE_ACTIVE_WINDOW_UNIT"):
        deployer._validate_deploy_window(prefix, allow_window_override=False, now_ms=safe_ms, no_systemd=False)


# ---------------------------------------------------------------------------
# P1-6: Health gates, KPF 404 backoff (zero 1.4GB hashing), registry TTL, systemd timers
# ---------------------------------------------------------------------------

def test_p1_6_health_gates_kpf_404_backoff_and_systemd_timers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    now_ms = 1_760_000_000_000

    # 1. Producer degraded must fail health check
    logs_dir, state_dir = tmp_path / "sig_logs", tmp_path / "sig_state"
    logs_dir.mkdir()
    state_dir.mkdir()
    (state_dir / "producer_health.json").write_text(json.dumps({
        "schema": health_check.PRODUCER_HEALTH_SCHEMA,
        "updated_at_ms": now_ms - 5_000,
        "fmz_platform_used": False,
        "read_only_demo": True,
        "state": "degraded",
        "core_version": "2.0.6",
    }), encoding="utf-8")
    p_res = health_check._check_signal_producer(logs_dir, state_dir, now_ms=now_ms, max_producer_age_ms=300_000, max_snapshot_age_ms=600_000, expected_core_version="2.0.6")
    assert p_res["ok"] is False and "producer_not_running:degraded" in p_res["reason"]

    # 2. MAP non-current fmz.status must fail
    map_p = tmp_path / "current_map.json"
    map_p.write_text(json.dumps({"schema": health_check.MAP_SCHEMA, "generated_at_ms": now_ms - 1000, "fmz": {"status": "background_only"}}), encoding="utf-8")
    assert health_check._check_map(map_p, now_ms=now_ms, max_age_ms=1_800_000)["reason"] == "map_fmz_not_current"

    # 3. Workbench market_context != current must fail
    wb_res = health_check._check_workbench(
        "http://127.0.0.1:8895",
        token="tok",
        expected_core_version="2.0.6",
        http_get=lambda url, **_kw: (200, {"ok": True}) if "session" in url else (200, {"market_context": {"status": "stale", "strategy_version": "2.0.6"}, "discovery": {}}),
    )
    assert wb_res["ok"] is False and wb_res["reason"] == "workbench_market_context_not_current"

    # 4. Archive stale or critical error must fail
    arch_root = tmp_path / "archive"
    for sub in ("cases", "structure", "levels", "band_snapshots", "_state"):
        (arch_root / sub).mkdir(parents=True)
    (arch_root / "_state" / "state.json").write_text(json.dumps({"last_tick_ms": now_ms - 3_600_000}), encoding="utf-8")
    assert health_check._check_archive(arch_root, now_ms=now_ms)["reason"] == "archive_tick_stale"
    (arch_root / "_state" / "state.json").write_text(json.dumps({
        "last_tick_ms": now_ms - 10_000,
        "last_tick_summary": {"at_ms": now_ms - 10_000, "errors_count": 3, "error_steps": ["ledger"]},
    }), encoding="utf-8")
    assert health_check._check_archive(arch_root, now_ms=now_ms)["reason"] == "archive_critical_tick_errors"

    # 5. KPF 404 backoff preserves T-2 READY manifest & does not hash existing ZIPs
    data_root = tmp_path / "kpf_data"
    out_root = tmp_path / "kpf_pub"
    day_prev = date(2026, 1, 30)
    day_today = date(2026, 1, 31)
    _write_full_window(data_root, day_prev)
    res_ready = light.run_oneshot(
        data_root=data_root,
        output_root=out_root,
        as_of_ms=_ms(day_prev + timedelta(days=1), 60),
        current_price=Decimal("89500"),
        allow_download=False,
        min_free_bytes=0,
        storage_budget_bytes=10 * 1024**3,
    )
    assert res_ready["ok"] is True

    # Next UTC day arrives (`day_today` missing): binance.vision returns 404
    sha_calls = [0]
    orig_sha = light.sha256_file

    def counting_sha(p: Path) -> str:
        if p.name.endswith(".zip"):
            sha_calls[0] += 1
        return orig_sha(p)

    monkeypatch.setattr(light, "sha256_file", counting_sha)
    dl_attempts = [0]

    def fail_404(*_a: Any, **_kw: Any):
        dl_attempts[0] += 1
        raise urllib.error.HTTPError("https://example.invalid", 404, "Not Found", {}, None)

    monkeypatch.setattr(light, "download_one_day", fail_404)
    as_of_next = _ms(day_today + timedelta(days=1), 60)
    r_404 = light.run_oneshot(
        data_root=data_root,
        output_root=out_root,
        as_of_ms=as_of_next,
        current_price=Decimal("89500"),
        allow_download=True,
        min_free_bytes=0,
        storage_budget_bytes=10 * 1024**3,
        remote_missing_backoff_ms=1_800_000,
    )
    assert r_404["ok"] is True
    assert r_404["unchanged"] is True
    assert r_404["reason"] == "remote_day_not_published_yet"
    assert sha_calls[0] == 0, "must not SHA-256 existing 89 ZIPs while checking missing day"
    assert dl_attempts[0] == 1
    assert json.loads((out_root / "current.json").read_text(encoding="utf-8"))["status"] == "READY"

    # Subsequent tick within 30m backoff skips network completely
    r_backoff = light.run_oneshot(
        data_root=data_root,
        output_root=out_root,
        as_of_ms=as_of_next + 300_000,
        current_price=Decimal("89500"),
        allow_download=True,
        min_free_bytes=0,
        storage_budget_bytes=10 * 1024**3,
        remote_missing_backoff_ms=1_800_000,
    )
    assert r_backoff["ok"] is True and dl_attempts[0] == 1

    # 6. Registry TTL & unified systemd timer/hardening alignment
    reg = json.loads((ROOT / "demo" / "shared" / "data_product_registry_v23.json").read_text(encoding="utf-8"))
    kpf_entry = next(p for p in reg["products"] if p["product_id"] == "btc.kpf.map.readonly.v1")
    assert kpf_entry["usage_policies"]["default"]["ttl_ms"] == 172_800_000
    assert kpf_entry["usage_policies"]["background"]["ttl_ms"] == 172_800_000

    units_dir = ROOT / "deploy" / "xxproject_server" / "systemd_units_unified"
    for tname in ("signal-audit-materialize.timer", "astra-kpf-light.timer", "astra-log-retention.timer"):
        txt = (units_dir / tname).read_text(encoding="utf-8")
        assert "OnUnitInactiveSec=" in txt and "OnUnitActiveSec=" not in txt
    mat_svc = (units_dir / "signal-audit-materialize.service").read_text(encoding="utf-8")
    assert "User=bitnami" in mat_svc and "Group=bitnami" in mat_svc
    assert "ProtectSystem=strict" in mat_svc
    assert "ReadWritePaths=/opt/signal-audit /var/lib/xxproject/signal_audit" in mat_svc
