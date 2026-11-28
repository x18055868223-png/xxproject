"""End-to-end counterexample & simplification verification matrix on `hand_66a598b4790172fa3fd56276`."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import gzip
import json
import re
import socket
import time

import pytest

from tools import astra_evidence_qual_v1 as evidence_qual
from tools import astra_player_dossier_v1 as dossier
from tools import astra_player_review_v1 as review
from tools import astra_player_tracking_v13 as tracking
from tools import astra_research_archive_v1 as archive
from tools import signal_llm_review
from test_astra_player_tracking_v13 import M, post, serving
from test_player_e2e_robustness_matrix import (
    HOUR,
    _setup_nov26_workbench_and_archive,
    _sse_mock_server,
    _strict_p5_reply_text,
)


EXPECTED_HAND_ID = "hand_66a598b4790172fa3fd56276"


def _persist_archive_case(arch_root: Path, case_obj: dict, *, day_ms: int, side: str, at_ms: int) -> Path:
    day = dossier.utc(day_ms).strftime("%Y-%m-%d")
    hh = dossier.utc(day_ms).strftime("%H")
    rel = f"cases/{day}/{hh}_{side}_{case_obj['case_id']}.json.gz"
    raw_gz = gzip.compress(
        json.dumps(case_obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        mtime=0,
    )
    arch = archive.Archive(arch_root)
    arch.write_new(rel, raw_gz, at_ms=at_ms, kind="case")
    state = arch.load_state()
    state.setdefault("cases", {})[f"{day}T{hh}|{side}"] = {
        "case_id": case_obj["case_id"],
        "path": rel,
    }
    arch.save_state(state)
    return arch_root / rel


def _rehash_case(case_obj: dict, *, built_at_ms: int | None = None) -> dict:
    cloned = json.loads(json.dumps(case_obj))
    if built_at_ms is not None:
        cloned["build"]["built_at_ms"] = built_at_ms
    cloned["build"]["content_sha256"] = evidence_qual.canonical_case_content_sha256(cloned)
    return cloned


# ---------------------------------------------------------------------------
# (a) Normal Path E2E (`Reject` & `Accept` + `SHADOW_LOCK_CREATED` -> `settle_due`)
# ---------------------------------------------------------------------------

def test_normal_path_e2e_reject_and_accept_lifecycle_on_hand_66a598b4(tmp_path, monkeypatch, capsys):
    slot_0 = int(datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc).timestamp() * 1000)
    monkeypatch.setenv("LLM_API_KEY", "k-e2e-norm")

    # 1) Full Accept lifecycle: prewarm -> :00 native cases on disk (no levels/*.jsonl yet) -> :02 immediate r16 build -> SSE -> SHADOW_LOCK_CREATED -> :07 ALREADY_DECIDED -> sync-open -> settle_due -> audit_record
    clock = [slot_0 - 8 * M + 4_166]  # 15:52:04.166Z
    wb, arch_root, dossier_root, plan_r16, slot_ms, expiry_ms, _, sync_market_aux = _setup_nov26_workbench_and_archive(
        tmp_path / "accept_flow", monkeypatch, clock=clock,
    )
    accept_text = _strict_p5_reply_text("## Accept / Put", short_strike=60_000, long_strike=58_000, require_outside_at_t0=True)

    with serving(wb) as wb_base, _sse_mock_server(
        lambda _idx, _body: {"status": 200, "reasoning": "核查两侧结构与余量...", "text": accept_text, "finish_reason": "stop"}
    ) as (llm_endpoint, llm_calls):
        service = post(wb_base, wb)

        capsys.readouterr()
        rep_pre = tracking.prewarm(plan_r16, arch_root, service, clock=lambda: clock[0], public=dossier.public)
        assert capsys.readouterr().err == ""
        assert rep_pre["state"] == "PREWARM"
        assert rep_pre["business_readiness"]["ready_for_deal"] is True
        assert rep_pre["business_readiness"]["status"] == "READY_FOR_DEAL"
        assert rep_pre["business_readiness"]["alert_level"] == "NONE"

        # Persist native :00 cases on disk WITHOUT native r16 level cards
        clock[0] = slot_ms + 5_000  # 16:00:05Z
        sync_market_aux()
        wb.refresh(now_ms=clock[0], record_daily=False)
        prep_00 = wb.shadow_tracking(
            "prepare_deal",
            {"target_expiry_ms": expiry_ms, "slot_ms": slot_ms, "allow_refresh": False, "input_profile": "p0_strict@1"},
        )
        assert prep_00["ready_for_deal"] is True
        archive.backfill_slot_cases(arch_root, prep_00["cases"], slot_ms=slot_ms, target_expiry_ms=expiry_ms, now_ms=clock[0])
        assert not list((arch_root / "levels").glob("*.jsonl"))

        # At 16:02:00Z (< 15m), auto_deal builds r16 cards immediately without waiting 15m
        clock[0] = slot_ms + 2 * M
        sync_market_aux()
        d = dossier.Dossiers(dossier_root, clock=lambda: clock[0], require_reply_provenance=True)
        llm_cfg = {
            "api_key": "k-e2e-norm",
            "model": "deepseek-reasoner",
            "endpoint": llm_endpoint,
            "prompt_version": review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
            "input_profile": "p0_strict@1",
        }
        res_02, = tracking.auto_deal(d, plan_r16, arch_root, service, llm_config=llm_cfg)
        assert res_02["hand_id"] == EXPECTED_HAND_ID
        assert res_02["status"] == "VALID"
        assert res_02["decision"] == "Accept"
        assert res_02["link"]["state"] == "SHADOW_LOCK_CREATED"
        assert res_02["link"]["execution_allowed"] is False
        assert len(llm_calls) == 1

        hand = d.path(expiry_ms) / "hands" / EXPECTED_HAND_ID
        src_02 = dossier.read(hand / "evidence" / "source.json")
        assert all(c["round"] == "r16" for c in src_02["cards"])

        # :07 tick -> ALREADY_DECIDED (idempotent)
        clock[0] = slot_ms + 7 * M
        res_07, = tracking.auto_deal(d, plan_r16, arch_root, service, llm_config=llm_cfg)
        assert res_07 == {"hand_id": EXPECTED_HAND_ID, "status": "ALREADY_DECIDED", "decision": "Accept", "link": None}
        assert len(llm_calls) == 1

        # :10 sync-open batch (recover_initial + sync check_due + classify_sync_open_batch)
        clock[0] = slot_ms + 10 * M
        sync_market_aux()
        wb.refresh(now_ms=clock[0], record_daily=False)
        sync_rows = tracking.recover_initial(d, service)
        sync_res = tracking.sync(d, hand, service, check_due=True, archive_root=arch_root)
        sync_rows.append({**sync_res, "hand_id": hand.name, "dossier_id": hand.parent.parent.name, "phase": "sync"})
        batch = tracking.classify_sync_open_batch(sync_rows)
        assert batch["batch_status"] == "ALL_OK"
        assert sync_res["state"] == "active"
        assert any(c["kind"] == "CONDITION_NOT_TRIGGERED" for c in sync_res["checks"])

        # Post-expiry 08:05Z -> settle_due + audit_record
        clock[0] = expiry_ms + 5 * M
        settled = list(d.settle_due())
        assert len(settled) == 1
        assert list(d.settle_due()) == []
        outcomes = [dossier.read(p) for p in (d.path(expiry_ms) / "outcomes").glob("outcome_*/outcome.json")]
        assert len(outcomes) == 1 and outcomes[0]["classification"] == "OFFICIAL"
        aud = tracking.audit_record(hand, clock[0], {}, detail=True)
        assert aud["hand_id"] == EXPECTED_HAND_ID
        assert aud["state"] == "Accept"
        assert aud["decision"]["judgment"]["side"] == "put"

    # 2) Reject E2E on the exact same scheduled hand identity (`hand_66a598b4790172fa3fd56276`)
    clock_rej = [slot_0 + 2 * M]
    wb_r, arch_r, dos_r, plan_r, _, exp_r, _, _ = _setup_nov26_workbench_and_archive(
        tmp_path / "reject_flow", monkeypatch, clock=clock_rej,
    )
    reject_text = _strict_p5_reply_text("## Reject")
    with serving(wb_r) as wb_r_base, _sse_mock_server(
        lambda _idx, _body: {"status": 200, "text": reject_text, "finish_reason": "stop"}
    ) as (ep_rej, calls_rej):
        svc_r = post(wb_r_base, wb_r)
        d_r = dossier.Dossiers(dos_r, clock=lambda: clock_rej[0], require_reply_provenance=True)
        cfg_r = {
            "api_key": "k-e2e-norm",
            "model": "deepseek-reasoner",
            "endpoint": ep_rej,
            "prompt_version": review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
            "input_profile": "p0_strict@1",
        }
        r_deal, = tracking.auto_deal(d_r, plan_r, arch_r, svc_r, llm_config=cfg_r)
        assert r_deal["hand_id"] == EXPECTED_HAND_ID
        assert r_deal["status"] == "VALID" and r_deal["decision"] == "Reject" and r_deal["link"] is None
        clock_rej[0] = slot_0 + 7 * M
        assert tracking.auto_deal(d_r, plan_r, arch_r, svc_r, llm_config=cfg_r)[0]["status"] == "ALREADY_DECIDED"
        assert len(calls_rej) == 1
        clock_rej[0] = exp_r + 5 * M
        assert len(list(d_r.settle_due())) == 1
        hand_r = d_r.path(exp_r) / "hands" / EXPECTED_HAND_ID
        aud_r = tracking.audit_record(hand_r, clock_rej[0], {})
        assert aud_r["state"] == "Reject" and aud_r["stage"] == "Reject"


# ---------------------------------------------------------------------------
# (b) Probe 3 Counterexamples Fixed: RPC Timeouts, Prewarm Timeout vs Prior-Hour Archive, & Unverified Cases
# ---------------------------------------------------------------------------

def test_probe3_local_service_timeouts_prewarm_timeout_no_archive_false_green_and_unverified_cases(
    tmp_path, monkeypatch, capsys
):
    slot_0 = int(datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc).timestamp() * 1000)
    clock = [slot_0 - 8 * M]  # 15:52:00Z
    wb, arch_root, _, plan_r16, slot_ms, expiry_ms, _, sync_market_aux = _setup_nov26_workbench_and_archive(
        tmp_path / "probe3", monkeypatch, clock=clock,
    )
    monkeypatch.setenv("LLM_API_KEY", "k-probe3")

    # 1) Verify local_service default RPC timeouts (90s on prepare_deal+allow_refresh, 10s on readiness, 30s on lock)
    #    AND slow ~1.2s prepare_deal advancing wall/clock time during refresh!
    seen_timeouts: list[tuple[str, float]] = []
    real_build_opener = tracking.build_opener

    def spy_build_opener(*handlers):
        opener = real_build_opener(*handlers)
        orig_open = opener.open

        def wrapped_open(req, timeout=None):
            action_name = req.full_url.rsplit("/", 1)[-1]
            seen_timeouts.append((action_name, float(timeout)))
            return orig_open(req, timeout=timeout)

        opener.open = wrapped_open
        return opener

    monkeypatch.setattr(tracking, "build_opener", spy_build_opener)

    orig_prepare = wb._prepare_shadow_deal

    def slow_prepare(body):
        time.sleep(1.2)
        clock[0] += 1_200
        sync_market_aux()
        return orig_prepare(body)

    monkeypatch.setattr(wb, "_prepare_shadow_deal", slow_prepare)

    with serving(wb) as wb_base:
        svc = tracking.local_service(wb_base, wb.config.shadow_service_token)
        capsys.readouterr()
        rep_slow = tracking.prewarm(plan_r16, arch_root, svc, clock=lambda: clock[0], public=dossier.public)
        assert capsys.readouterr().err == ""
        assert rep_slow["business_readiness"]["ready_for_deal"] is True
        assert rep_slow["business_readiness"]["status"] == "READY_FOR_DEAL"

        # Check explicit action timeout routing on local_service
        svc("prepare_deal", {"target_expiry_ms": expiry_ms, "slot_ms": slot_ms, "allow_refresh": False})
        with pytest.raises(tracking.Blocked):
            svc("lock", {"dossier_id": "Deribit_BTC_20261127T080000Z", "hand_id": EXPECTED_HAND_ID})

    by_call = dict(seen_timeouts[:2])
    assert by_call["prepare_deal"] == 90.0 and by_call["prepare_deal"] > 30.0
    assert by_call["readiness"] == 10.0
    assert seen_timeouts[2] == ("prepare_deal", 30.0)  # allow_refresh=False
    assert seen_timeouts[3] == ("lock", 30.0)

    # 2) Simulated prepare_deal timeout (`socket.timeout("timed out after 90s")`) WITH prior-hour 15:00Z Put+Call archive cases on disk
    monkeypatch.setattr(wb, "_prepare_shadow_deal", orig_prepare)
    prev_hour_ms = slot_ms - HOUR  # 15:00Z
    clock_15 = [prev_hour_ms + 10_000]
    wb_15, _, _, _, _, _, _, _ = _setup_nov26_workbench_and_archive(
        tmp_path / "seed_15", monkeypatch, clock=clock_15,
    )
    wb_15.refresh(now_ms=clock_15[0], record_daily=False)
    prep_15 = wb_15.shadow_tracking(
        "prepare_deal",
        {"target_expiry_ms": expiry_ms, "slot_ms": prev_hour_ms, "allow_refresh": False, "input_profile": "p0_strict@1"},
    )
    assert prep_15["ready_for_deal"] is True
    _persist_archive_case(arch_root, prep_15["cases"]["put"], day_ms=prev_hour_ms, side="put", at_ms=clock_15[0])
    _persist_archive_case(arch_root, prep_15["cases"]["call"], day_ms=prev_hour_ms, side="call", at_ms=clock_15[0])

    def timeout_service(action, _body):
        if action == "prepare_deal":
            raise socket.timeout("timed out after 90s")
        return {"enabled": True}

    capsys.readouterr()
    rep_to = tracking.prewarm(
        plan_r16,
        arch_root,
        timeout_service,
        clock=lambda: slot_ms - 5 * M,
        public=dossier.public,
    )
    err_to = capsys.readouterr().err
    br_to = rep_to["business_readiness"]
    assert br_to["archive_cases"]["current_hour"] == {"put": 1, "call": 1}
    assert br_to["ready_for_deal"] is False
    assert br_to["status"] == "PREPARE_DEAL_TIMEOUT"
    assert br_to["status"] != "ARCHIVE_PAIR_PRESENT"
    assert br_to["alert_level"] == "WARN"
    assert br_to["blockers"] == ["prepare_deal_timeout"]
    assert err_to.startswith("PREWARM_ALERT ")
    alert_to = json.loads(err_to[len("PREWARM_ALERT "):].strip())
    assert alert_to["status"] == "PREPARE_DEAL_TIMEOUT"
    assert alert_to["blockers"] == ["prepare_deal_timeout"]

    # 3) Fake prepare_deal returning ready_for_deal=True with sides={} OR missing cases -> SOURCE_CASES_UNVERIFIED
    valid_cases = prep_15["cases"]
    for bad_prep in (
        {"ready_for_deal": True, "readiness_status": "SOURCE_READY", "sides": {}, "cases": valid_cases},
        {"ready_for_deal": True, "readiness_status": "SOURCE_READY", "sides": {"put": {"ok": True}, "call": {"ok": True}}},
    ):
        capsys.readouterr()
        rep_unv = tracking.prewarm(
            plan_r16,
            arch_root,
            lambda action, _b, _p=bad_prep: _p if action == "prepare_deal" else {"enabled": True},
            clock=lambda: slot_ms - 5 * M,
            public=dossier.public,
        )
        err_unv = capsys.readouterr().err
        br_unv = rep_unv["business_readiness"]
        assert br_unv["ready_for_deal"] is False
        assert br_unv["status"] == "SOURCE_CASES_UNVERIFIED"
        assert "prepare_deal_cases_missing_or_unqualified" in br_unv["blockers"]
        assert err_unv.startswith("PREWARM_ALERT ")


# ---------------------------------------------------------------------------
# (c) Probe 2a & Probe 2c Counterexamples Fixed on `hand_66a598b4790172fa3fd56276`
# ---------------------------------------------------------------------------

def test_probe2a_orphan_disk_skew_and_probe2c_stale_l3_disk_pair_fallthrough(tmp_path, monkeypatch):
    slot_0 = int(datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc).timestamp() * 1000)
    accept_put = _strict_p5_reply_text("## Accept / Put", short_strike=60_000, long_strike=58_000, require_outside_at_t0=True)

    # --- Probe 2a: :20 single-side 16_put on disk, then :22 (+117s > 60s) Call recovers & prepare_deal returns fresh (put@:22, call@:22) ---
    clock_2a = [slot_0 + 20 * M + 3_000]  # 16:20:03Z
    wb_2a, arch_2a, dos_2a, plan_2a, slot_ms, expiry_ms, _, sync_aux_2a = _setup_nov26_workbench_and_archive(
        tmp_path / "probe2a", monkeypatch, clock=clock_2a,
    )
    wb_2a.refresh(now_ms=clock_2a[0], record_daily=False)
    prep_20 = wb_2a.shadow_tracking(
        "prepare_deal",
        {"target_expiry_ms": expiry_ms, "slot_ms": slot_ms, "allow_refresh": False, "input_profile": "p0_strict@1"},
    )
    put_20_path = _persist_archive_case(
        arch_2a, prep_20["cases"]["put"], day_ms=slot_ms, side="put", at_ms=clock_2a[0]
    )
    put_20_bytes = put_20_path.read_bytes()
    assert archive.Archive(arch_2a).verify()["ok"] is True

    # Advance +117s to 16:22:00Z (> 60s pair skew vs 16:20:03Z orphan on disk!)
    clock_2a[0] = slot_ms + 22 * M  # 16:22:00Z (+117,000ms)
    sync_aux_2a()
    wb_2a.refresh(now_ms=clock_2a[0], record_daily=False)

    with serving(wb_2a) as base_2a, _sse_mock_server(
        lambda _i, _b: {"status": 200, "text": accept_put, "finish_reason": "stop"}
    ) as (ep_2a, calls_2a):
        svc_2a = post(base_2a, wb_2a)
        d_2a = dossier.Dossiers(dos_2a, clock=lambda: clock_2a[0], require_reply_provenance=True)
        res_2a, = tracking.auto_deal(
            d_2a,
            plan_2a,
            arch_2a,
            svc_2a,
            llm_config={
                "api_key": "k-2a",
                "model": "deepseek-reasoner",
                "endpoint": ep_2a,
                "prompt_version": review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
                "input_profile": "p0_strict@1",
            },
        )
        assert res_2a["hand_id"] == EXPECTED_HAND_ID
        assert res_2a["status"] == "VALID" and res_2a["decision"] == "Accept"
        assert res_2a["link"]["state"] == "SHADOW_LOCK_CREATED"
        assert len(calls_2a) == 1

    # Single :20 disk file untouched and Archive SHA chain intact!
    case_dir_2a = arch_2a / "cases" / "2026-11-26"
    assert list(case_dir_2a.glob("16_put_*.json.gz")) == [put_20_path]
    assert list(case_dir_2a.glob("16_call_*.json.gz")) == []
    assert put_20_path.read_bytes() == put_20_bytes
    assert archive.Archive(arch_2a).verify()["ok"] is True
    hand_2a = d_2a.path(expiry_ms) / "hands" / EXPECTED_HAND_ID
    src_2a = dossier.read(hand_2a / "evidence" / "source.json")
    reads_2a = [c["build"]["built_at_ms"] for c in src_2a["cases"]]
    assert all(t >= slot_ms + 22 * M for t in reads_2a) and abs(reads_2a[0] - reads_2a[1]) <= 60_000

    # --- Probe 2c: :00 native disk pair has 6m-old stale L3.expiry_walls (plus native r16 cards so disk deal fails) -> falls through to prepare_deal ---
    clock_2c = [slot_0 + 5_000]  # 16:00:05Z
    wb_2c, arch_2c, dos_2c, plan_2c, _, exp_2c, _, sync_aux_2c = _setup_nov26_workbench_and_archive(
        tmp_path / "probe2c", monkeypatch, clock=clock_2c,
    )
    wb_2c.refresh(now_ms=clock_2c[0], record_daily=False)
    prep_2c_seed = wb_2c.shadow_tracking(
        "prepare_deal",
        {"target_expiry_ms": exp_2c, "slot_ms": slot_0, "allow_refresh": False, "input_profile": "p0_strict@1"},
    )
    stale_l3_asof = slot_0 - 6 * M  # 6m old (> 5m L3 deal cutoff!)
    exp_day = dossier.utc(exp_2c).date().isoformat()
    pts_00 = [[t, 60_476.0] for t in range(slot_0 - dossier.DAY, clock_2c[0] + 1, M)]
    arch_obj_2c = archive.Archive(arch_2c)
    for side in ("put", "call"):
        stale_case = json.loads(json.dumps(prep_2c_seed["cases"][side]))
        l3_entry = dossier.player.entry(stale_case, "L3.expiry_walls")
        l3_entry["asof_ms"] = stale_l3_asof
        stale_case = _rehash_case(stale_case, built_at_ms=clock_2c[0])
        _persist_archive_case(arch_2c, stale_case, day_ms=slot_0, side=side, at_ms=clock_2c[0])
        card_00 = dossier.levels.build_card(
            expiry_ms=exp_2c,
            rnd="r16",
            side=side,
            read_at_ms=clock_2c[0],
            walls=dossier.player.values(stale_case, "L3.expiry_walls"),
            points=pts_00,
            case=stale_case,
            contract=dossier.player.values(stale_case, "L1.contract"),
            snapshots=[],
            backfilled=False,
            timestamp_semantics="unverified_minute_label",
        )
        arch_obj_2c.append_line(f"levels/{exp_day}.jsonl", card_00, at_ms=clock_2c[0], kind="levels")

    # Confirm disk pair alone fails deal (`major_positions` -> >5m stale L3)
    disk_only_src = dossier.collect(arch_2c, exp_2c, slot_0, clock=lambda: clock_2c[0], candidate_round="r16")
    probe_d = dossier.Dossiers(tmp_path / "probe2c_verify_fail", clock=lambda: clock_2c[0])
    probe_h, = probe_d.tick(plan_2c, slots=[slot_0], input_profile="p0_strict@1")
    with pytest.raises(ValueError, match="current expiry structure required"):
        probe_d.deal(probe_h, disk_only_src, input_profile="p0_strict@1")

    # Advance to 16:02:00Z with stale in-memory L3 so prepare_deal(allow_refresh=True) refreshes L3 live!
    clock_2c[0] = slot_0 + 2 * M
    sync_aux_2c()
    wb_2c._current_view["updated_at_ms"] = slot_0 - 10 * M  # force prepare_deal allow_refresh=True to refresh

    with serving(wb_2c) as base_2c, _sse_mock_server(
        lambda _i, _b: {"status": 200, "text": accept_put, "finish_reason": "stop"}
    ) as (ep_2c, calls_2c):
        svc_2c = post(base_2c, wb_2c)
        d_2c = dossier.Dossiers(dos_2c, clock=lambda: clock_2c[0], require_reply_provenance=True)
        res_2c, = tracking.auto_deal(
            d_2c,
            plan_2c,
            arch_2c,
            svc_2c,
            llm_config={
                "api_key": "k-2c",
                "model": "deepseek-reasoner",
                "endpoint": ep_2c,
                "prompt_version": review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
                "input_profile": "p0_strict@1",
            },
        )
        assert res_2c["hand_id"] == EXPECTED_HAND_ID
        assert res_2c["status"] == "VALID" and res_2c["decision"] == "Accept"
        assert res_2c["link"]["state"] == "SHADOW_LOCK_CREATED"
        assert len(calls_2c) == 1
        assert archive.Archive(arch_2c).verify()["ok"] is True


# ---------------------------------------------------------------------------
# (d) Post-16:30Z (`deal_grace_ms`) Stranded Retry & 180s (`< 630s`) Inline Standby Failover
# ---------------------------------------------------------------------------

def test_post_deal_grace_inline_retry_and_180s_standby_failover(tmp_path, monkeypatch):
    slot_0 = int(datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc).timestamp() * 1000)
    accept_put = _strict_p5_reply_text("## Accept / Put", short_strike=60_000, long_strike=58_000, require_outside_at_t0=True)

    # 1) 16:27Z freeze + 320s slow LLM truncation finishing at 16:32:20Z (>= 16:30Z deal_grace_ms) -> 1 inline retry -> VALID + SHADOW_LOCK_CREATED
    clock = [slot_0 + 27 * M]  # 16:27:00Z
    wb, arch_root, dos_root, plan_r16, slot_ms, expiry_ms, _, sync_aux = _setup_nov26_workbench_and_archive(
        tmp_path / "post_grace", monkeypatch, clock=clock,
    )

    def post_grace_sse_script(idx: int, _body: dict) -> dict:
        if idx == 0:
            # Simulate 320s slow streaming truncation crossing 16:30:00Z to 16:32:20Z!
            clock[0] = slot_ms + 32 * M + 20_000
            sync_aux()
            return {"status": 200, "text": "## Accept / Put\n长推理耗时320秒截断缺JSON", "finish_reason": "length"}
        sync_aux()
        return {"status": 200, "text": accept_put, "finish_reason": "stop"}

    with serving(wb) as wb_base, _sse_mock_server(post_grace_sse_script) as (ep_pg, calls_pg):
        svc = post(wb_base, wb)
        d = dossier.Dossiers(dos_root, clock=lambda: clock[0], require_reply_provenance=True)
        res_pg, = tracking.auto_deal(
            d,
            plan_r16,
            arch_root,
            svc,
            llm_config={
                "api_key": "k-pg",
                "model": "deepseek-reasoner",
                "endpoint": ep_pg,
                "prompt_version": review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
                "input_profile": "p0_strict@1",
            },
        )
        assert clock[0] >= slot_ms + plan_r16["deal_grace_ms"]
        assert res_pg["hand_id"] == EXPECTED_HAND_ID
        assert res_pg["status"] == "VALID"
        assert res_pg["decision"] == "Accept"
        assert res_pg["link"]["state"] == "SHADOW_LOCK_CREATED"
        assert len(calls_pg) == 2
        hand_pg = d.path(expiry_ms) / "hands" / EXPECTED_HAND_ID
        atts_pg = [dossier.read(p) for p in dossier.attempts(hand_pg)]
        assert [a["status"] for a in atts_pg] == ["PARSE_FAILED", "VALID"]

    # 2) Inline standby failover when 180s (< 630s!) remains in reply_deadline
    clock_fb = [slot_0 + 2 * M]  # freeze at 16:02:00Z -> reply_deadline = 16:32:00Z
    wb_fb, arch_fb, dos_fb, plan_fb, _, exp_fb, _, sync_aux_fb = _setup_nov26_workbench_and_archive(
        tmp_path / "standby_180s", monkeypatch, clock=clock_fb,
    )
    wb_fb.refresh(now_ms=clock_fb[0], record_daily=False)
    prep_fb = wb_fb.shadow_tracking(
        "prepare_deal",
        {"target_expiry_ms": exp_fb, "slot_ms": slot_0, "allow_refresh": False, "input_profile": "p0_strict@1"},
    )
    src_fb = dossier.collect(
        arch_fb,
        exp_fb,
        slot_0,
        clock=lambda: clock_fb[0],
        candidate_round="r16",
        fallback_cases=prep_fb["cases"],
        provenance_override="targeted workbench verified native case + Deribit public read",
    )
    d_fb = dossier.Dossiers(dos_fb, clock=lambda: clock_fb[0], require_reply_provenance=True)
    h_fb, = d_fb.tick(plan_fb, slots=[slot_0], input_profile="p0_strict@1")
    assert h_fb.name == EXPECTED_HAND_ID
    d_fb.deal(h_fb, src_fb, input_profile="p0_strict@1")

    # Advance to 16:29:00Z -> exactly 180s (< 630s!) remaining before 16:32:00Z reply_deadline
    clock_fb[0] = slot_0 + 29 * M
    sync_aux_fb()
    wb_fb.refresh(now_ms=clock_fb[0], record_daily=False)
    seen_fb: list[tuple[str, float]] = []

    def transport_180s(_api_key, model, _body, timeout_s, endpoint=None):
        del endpoint
        seen_fb.append((model, float(timeout_s)))
        if model == "deepseek-reasoner":
            err = signal_llm_review.LlmApiError(503, "primary overloaded")
            err.retry_after = 0.0
            raise err
        assert model == "deepseek-reasoner-standby"
        return {"choices": [{"finish_reason": "stop", "message": {"content": accept_put}}]}

    cfg_fb = tracking._llm_settings({
        "api_key": "k-prim",
        "model": "deepseek-reasoner",
        "fallback_enabled": True,
        "fallback_model": "deepseek-reasoner-standby",  # heavy model -> default 600s timeout_s (was 630s room!)
        "transport": transport_180s,
    })
    with serving(wb_fb) as base_fb:
        svc_fb = post(base_fb, wb_fb)
        res_fb = tracking._deal_hand(
            d_fb,
            h_fb,
            svc_fb,
            cfg_fb,
            prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
            input_profile="p0_strict@1",
        )
        assert res_fb["hand_id"] == EXPECTED_HAND_ID
        assert res_fb["status"] == "VALID" and res_fb["decision"] == "Accept"
        assert res_fb["link"]["state"] == "SHADOW_LOCK_CREATED"
        assert [m for m, _ in seen_fb] == ["deepseek-reasoner", "deepseek-reasoner-standby"]
        assert 150.0 <= seen_fb[1][1] <= 165.0
        atts_fb = [dossier.read(p) for p in dossier.attempts(h_fb)]
        assert len(atts_fb) == 2 and atts_fb[1]["configuration"]["is_fallback"] is True


# ---------------------------------------------------------------------------
# (e) `DEAL_BLOCKED` Audit Alignment, SSE Drop `DELIVERY_UNKNOWN` Zero Replay, Crash Recovery & Tick Idempotency
# ---------------------------------------------------------------------------

def test_audit_deal_blocked_consistency_sse_drop_crash_recovery_and_tick_idempotency(tmp_path, monkeypatch):
    slot_0 = int(datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc).timestamp() * 1000)
    clock = [slot_0 + 2 * M]
    wb, arch_root, _, plan_r16, slot_ms, expiry_ms, _, sync_aux = _setup_nov26_workbench_and_archive(
        tmp_path / "matrix_e", monkeypatch, clock=clock,
    )
    wb.refresh(now_ms=clock[0], record_daily=False)
    prep = wb.shadow_tracking(
        "prepare_deal",
        {"target_expiry_ms": expiry_ms, "slot_ms": slot_ms, "allow_refresh": False, "input_profile": "p0_strict@1"},
    )
    archive.backfill_slot_cases(arch_root, prep["cases"], slot_ms=slot_ms, target_expiry_ms=expiry_ms, now_ms=clock[0])
    base_src = dossier.collect(
        arch_root,
        expiry_ms,
        slot_ms,
        clock=lambda: clock[0],
        candidate_round="r16",
        fallback_cases=prep["cases"],
        provenance_override="targeted workbench verified native case + Deribit public read",
    )
    accept_put = _strict_p5_reply_text("## Accept / Put", short_strike=60_000, long_strike=58_000, require_outside_at_t0=True)

    # 1) `audit_record` state == 'DEAL_BLOCKED' matches `Dossiers.summary` even when evidence is frozen
    d_blk = dossier.Dossiers(tmp_path / "deal_blocked_dos", clock=lambda: clock[0])
    h_blk, = d_blk.tick(plan_r16, slots=[slot_ms], input_profile="p0_strict@1")
    assert h_blk.name == EXPECTED_HAND_ID
    d_blk.deal(h_blk, base_src, input_profile="p0_strict@1")
    err_row = tracking._hand_error(d_blk, h_blk, tracking.Blocked("storage_fault"), slot_ms)
    assert err_row["status"] == "DEAL_BLOCKED"
    d_blk.summary(expiry_ms)
    manifest_blk = dossier.read(d_blk.path(expiry_ms) / "manifest.json")
    aud_blk = tracking.audit_record(h_blk, clock[0], {})
    assert manifest_blk["counts"] == {"SCHEDULED": {"DEAL_BLOCKED": 1}}
    assert aud_blk["state"] == "DEAL_BLOCKED"
    assert aud_blk["stage"] == "DEAL_BLOCKED"
    assert aud_blk["stage_reason"] == "storage_fault"

    # 2) SSE mid-stream drop (`DELIVERY_UNKNOWN`) -> ZERO replay (even across post-16:30Z inline check & repeat ticks)
    clock_drop = [slot_0 + 27 * M]
    wb_drop, arch_drop, dos_drop, plan_drop, _, exp_drop, _, sync_aux_drop = _setup_nov26_workbench_and_archive(
        tmp_path / "sse_drop_env", monkeypatch, clock=clock_drop,
    )
    d_drop = dossier.Dossiers(dos_drop, clock=lambda: clock_drop[0], require_reply_provenance=True)

    def drop_script(_idx: int, _body: dict) -> dict:
        clock_drop[0] = slot_ms + 32 * M + 10_000  # crosses 16:30Z deal_grace_ms!
        sync_aux_drop()
        return {"status": 200, "reasoning": "COT", "text": "## Accept / Put\n半截流式断开", "drop_before_done": True}

    with serving(wb_drop) as wb_drop_base, _sse_mock_server(drop_script) as (ep_drop, calls_drop):
        svc_drop = post(wb_drop_base, wb_drop)
        cfg_drop = {
            "api_key": "k-drop",
            "model": "deepseek-reasoner",
            "endpoint": ep_drop,
            "prompt_version": review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
            "input_profile": "p0_strict@1",
        }
        r_drop1, = tracking.auto_deal(d_drop, plan_drop, arch_drop, svc_drop, llm_config=cfg_drop)
        assert r_drop1["hand_id"] == EXPECTED_HAND_ID
        assert r_drop1["status"] == "REQUEST_FAILED"
        assert len(calls_drop) == 1
        h_drop = d_drop.path(exp_drop) / "hands" / EXPECTED_HAND_ID
        att_drop = dossier.read(dossier.attempts(h_drop)[0])
        assert att_drop["configuration"]["delivery_state"] == "DELIVERY_UNKNOWN"
        assert att_drop["configuration"]["partial_bytes"] > 0
        assert tracking.audit_record(h_drop, clock_drop[0], {})["stage"] == "DELIVERY_UNKNOWN"

        # Duplicate auto_deal & recover_initial ticks NEVER replay DELIVERY_UNKNOWN
        r_drop2, = tracking.auto_deal(d_drop, plan_drop, arch_drop, svc_drop, llm_config=cfg_drop)
        assert r_drop2 == {"hand_id": EXPECTED_HAND_ID, "status": "AUTO_ATTEMPTED", "last_status": "REQUEST_FAILED"}
        assert tracking.recover_initial(d_drop, svc_drop) == []
        assert len(calls_drop) == 1

    # 3) Crash after `reply.json` persisted -> `recover_initial` finishes VALID + SHADOW_LOCK_CREATED with ZERO duplicate HTTP + duplicate tick idempotency
    clock_cr = [slot_0 + 3 * M]
    wb_cr, arch_cr, crash_dos, plan_cr, _, _, _, _ = _setup_nov26_workbench_and_archive(
        tmp_path / "crash_env", monkeypatch, clock=clock_cr,
    )
    d_crash = dossier.Dossiers(crash_dos, clock=lambda: clock_cr[0], require_reply_provenance=True)

    orig_respond = dossier.Dossiers.respond
    crash_once = {"armed": True}

    def boom_respond(self_d, *args, **kwargs):
        if crash_once["armed"]:
            crash_once["armed"] = False
            raise RuntimeError("simulated crash after reply.json saved")
        return orig_respond(self_d, *args, **kwargs)

    with serving(wb_cr) as wb_cr_base, _sse_mock_server(
        lambda _i, _b: {"status": 200, "text": accept_put, "finish_reason": "stop"}
    ) as (ep_crash, calls_crash):
        svc = post(wb_cr_base, wb_cr)
        cfg_crash = {
            "api_key": "k-crash",
            "model": "deepseek-reasoner",
            "endpoint": ep_crash,
            "prompt_version": review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
            "input_profile": "p0_strict@1",
        }
        monkeypatch.setattr(dossier.Dossiers, "respond", boom_respond)
        with pytest.raises(RuntimeError, match="simulated crash"):
            tracking.auto_deal(d_crash, plan_cr, arch_cr, svc, llm_config=cfg_crash)
        assert len(calls_crash) == 1

        monkeypatch.setattr(dossier.Dossiers, "respond", orig_respond)
        rec_rows = tracking.recover_initial(d_crash, svc)
        assert len(rec_rows) == 1
        assert rec_rows[0]["hand_id"] == EXPECTED_HAND_ID
        assert rec_rows[0]["status"] == "ALREADY_DECIDED"
        assert rec_rows[0]["decision"] == "Accept"
        assert rec_rows[0]["link"]["state"] == "SHADOW_LOCK_CREATED"
        assert len(calls_crash) == 1

        # Duplicate timer ticks (both recover_initial and auto_deal) are 100% idempotent
        assert tracking.recover_initial(d_crash, svc) == []
        for extra_min in (5, 7, 12):
            clock_cr[0] = slot_0 + extra_min * M
            dup_res, = tracking.auto_deal(d_crash, plan_cr, arch_cr, svc, llm_config=cfg_crash)
            assert dup_res == {"hand_id": EXPECTED_HAND_ID, "status": "ALREADY_DECIDED", "decision": "Accept", "link": None}
        assert len(calls_crash) == 1


# ---------------------------------------------------------------------------
# (f) Supplement Item 1: 16:27Z Crossing :32 Four Subcases & `sync-open` Recovery Handoff
# ---------------------------------------------------------------------------

def test_1627z_crossing_1632z_four_subcases_and_sync_open_recovery_handoff(tmp_path, monkeypatch):
    slot_0 = int(datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc).timestamp() * 1000)
    accept_put = _strict_p5_reply_text("## Accept / Put", short_strike=60_000, long_strike=58_000, require_outside_at_t0=True)

    # Subcase 1a: 16:27Z freeze + slow 380s valid reply finishing at 16:33:20Z (> :32) -> VALID + SHADOW_LOCK_CREATED + zero dup lock
    clk_a = [slot_0 + 27 * M]
    wb_a, arch_a, dos_a, plan_a, slot_ms, exp_ms, _, sync_a = _setup_nov26_workbench_and_archive(
        tmp_path / "sub_1a_slow_valid", monkeypatch, clock=clk_a,
    )
    seen_timeouts_a: list[float] = []

    def slow_valid_transport(_k, _m, _b, timeout_s, endpoint=None):
        del endpoint
        seen_timeouts_a.append(float(timeout_s))
        clk_a[0] = slot_ms + 33 * M + 20_000  # 16:33:20Z (380s slow, past :32!)
        sync_a()
        wb_a.refresh(now_ms=clk_a[0], record_daily=False)
        return {"choices": [{"finish_reason": "stop", "message": {"content": accept_put}}]}

    with serving(wb_a) as base_a:
        svc_a = post(base_a, wb_a)
        d_a = dossier.Dossiers(dos_a, clock=lambda: clk_a[0], require_reply_provenance=True)
        cfg_a = {
            "api_key": "k-1a",
            "model": "deepseek-v4-pro",
            "transport": slow_valid_transport,
            "prompt_version": review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
            "input_profile": "p0_strict@1",
        }
        res_a, = tracking.auto_deal(d_a, plan_a, arch_a, svc_a, llm_config=cfg_a)
        assert res_a["status"] == "VALID" and res_a["decision"] == "Accept"
        assert res_a["link"]["state"] == "SHADOW_LOCK_CREATED"
        assert seen_timeouts_a == [600.0]
        clk_a[0] = slot_ms + 35 * M
        assert tracking.recover_initial(d_a, svc_a, llm_config=cfg_a, allow_post_window_retry=True) == []
        assert len(seen_timeouts_a) == 1
        assert len(list((d_a.path(exp_ms) / "episodes").glob("episode_*"))) == 1

    # Subcase 1b: 16:27Z freeze + 600s timeout with NO usable reply finishing at 16:37:00Z -> DELIVERY_UNKNOWN + zero replay + clean window close
    clk_b = [slot_0 + 27 * M]
    wb_b, arch_b, dos_b, plan_b, _, _, _, sync_b = _setup_nov26_workbench_and_archive(
        tmp_path / "sub_1b_timeout_none", monkeypatch, clock=clk_b,
    )
    calls_b: list[int] = []

    def timeout_no_reply_transport(*_a, **_kw):
        calls_b.append(1)
        clk_b[0] = slot_ms + 37 * M  # 16:37:00Z
        sync_b()
        raise TimeoutError("LLM request wall-clock deadline exceeded (600s)")

    with serving(wb_b) as base_b:
        svc_b = post(base_b, wb_b)
        d_b = dossier.Dossiers(dos_b, clock=lambda: clk_b[0], require_reply_provenance=True)
        cfg_b = {
            "api_key": "k-1b",
            "model": "deepseek-v4-pro",
            "transport": timeout_no_reply_transport,
            "prompt_version": review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
            "input_profile": "p0_strict@1",
        }
        res_b, = tracking.auto_deal(d_b, plan_b, arch_b, svc_b, llm_config=cfg_b)
        assert res_b["status"] == "TIMEOUT"
        clk_b[0] = slot_ms + 40 * M  # 16:40Z sync-open --review tick
        assert tracking.recover_initial(d_b, svc_b, llm_config=cfg_b, allow_post_window_retry=True) == []
        assert len(calls_b) == 1
        h_b = d_b.path(exp_ms) / "hands" / EXPECTED_HAND_ID
        clk_b[0] = slot_ms + 58 * M  # 16:58Z (> 16:57Z reply_deadline)
        aud_b = tracking.audit_record(h_b, clk_b[0], {})
        assert aud_b["state"] == "TIMEOUT" and aud_b["stage"] == "DELIVERY_UNKNOWN"

    # Subcase 1c: 16:27Z freeze + unknown receipt with partial reply crossing :32 -> DELIVERY_UNKNOWN + zero replay even on sync-open --review
    clk_c = [slot_0 + 27 * M]
    wb_c, arch_c, dos_c, plan_c, _, _, _, sync_c = _setup_nov26_workbench_and_archive(
        tmp_path / "sub_1c_partial_unknown", monkeypatch, clock=clk_c,
    )
    with serving(wb_c) as base_c, _sse_mock_server(
        lambda _i, _b: (
            clk_c.__setitem__(0, slot_ms + 33 * M + 10_000) or sync_c()
            or {"status": 200, "text": "## Accept / Put\n流式半截中断", "drop_before_done": True}
        )
    ) as (ep_c, calls_c):
        svc_c = post(base_c, wb_c)
        d_c = dossier.Dossiers(dos_c, clock=lambda: clk_c[0], require_reply_provenance=True)
        cfg_c = {
            "api_key": "k-1c",
            "model": "deepseek-v4-pro",
            "endpoint": ep_c,
            "prompt_version": review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
            "input_profile": "p0_strict@1",
        }
        res_c, = tracking.auto_deal(d_c, plan_c, arch_c, svc_c, llm_config=cfg_c)
        assert res_c["status"] == "REQUEST_FAILED"
        clk_c[0] = slot_ms + 35 * M
        assert tracking.recover_initial(d_c, svc_c, llm_config=cfg_c, allow_post_window_retry=True) == []
        assert len(calls_c) == 1

    # Subcase 1d: 16:27Z freeze + slow Attempt 1 consumes most of 16:27Z service_budget_s (leaving 180s < 320s heavy floor at 16:36:30Z)
    # -> 16:27Z defers with SERVICE_BUDGET_DEFERRED instead of strangling Attempt 2 -> 16:40Z sync-open recovers with full 600s budget & locks once!
    clk_d = [slot_0 + 27 * M]
    wb_d, arch_d, dos_d, plan_d, _, _, _, sync_d = _setup_nov26_workbench_and_archive(
        tmp_path / "sub_1d_sync_handoff", monkeypatch, clock=clk_d,
    )
    seen_timeouts_d: list[float] = []
    fake_mono = [1_000.0]

    def handoff_transport(_k, _m, _b, timeout_s, endpoint=None):
        del endpoint
        seen_timeouts_d.append(float(timeout_s))
        if len(seen_timeouts_d) == 1:
            clk_d[0] = slot_ms + 36 * M + 30_000  # 16:36:30Z (past :32, 20.5m left before 16:57:00Z reply_deadline!)
            fake_mono[0] += 630.0  # 810.0 - 630.0 = 180.0s remaining (< 320s heavy inline floor!)
            sync_d()
            return {"choices": [{"finish_reason": "length", "message": {"content": "## Accept / Put\n长推理570s截断"}}]}
        sync_d()
        wb_d.refresh(now_ms=clk_d[0], record_daily=False)
        return {"choices": [{"finish_reason": "stop", "message": {"content": accept_put}}]}

    with serving(wb_d) as base_d:
        svc_d = post(base_d, wb_d)
        d_d = dossier.Dossiers(dos_d, clock=lambda: clk_d[0], require_reply_provenance=True)
        monkeypatch.setattr(tracking.time, "monotonic", lambda: fake_mono[0])
        cfg_1627 = {
            "api_key": "k-1d",
            "model": "deepseek-v4-pro",
            "transport": handoff_transport,
            "service_budget_s": 810.0,
            "prompt_version": review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
            "input_profile": "p0_strict@1",
        }
        res_1627, = tracking.auto_deal(d_d, plan_d, arch_d, svc_d, llm_config=cfg_1627)
        assert res_1627["status"] == "PARSE_FAILED"
        assert len(seen_timeouts_d) == 1
        h_d = d_d.path(exp_ms) / "hands" / EXPECTED_HAND_ID
        notes_d = [dossier.read(p) for p in (h_d / "issuance").glob("*.json")]
        assert any(n["state"] == "SERVICE_BUDGET_DEFERRED" for n in notes_d)

        # Default recover_initial(d, svc) without allow_post_window_retry stays receipt-only (0 LLM calls)
        fake_mono[0] = 5_000.0  # fresh `sync-open` systemd oneshot starts at 16:40:00Z with its own 810s budget!
        clk_d[0] = slot_ms + 40 * M  # 16:40:00Z (`sync-open --review` tick, 17m left before 16:57:00Z!)
        assert tracking.recover_initial(d_d, svc_d) == []
        assert len(seen_timeouts_d) == 1

        # `sync-open --review` (`allow_post_window_retry=True`) gets fresh 810s service budget -> full 600s model budget -> VALID + SHADOW_LOCK_CREATED!
        cfg_sync = {
            "api_key": "k-1d",
            "model": "deepseek-v4-pro",
            "transport": handoff_transport,
            "service_budget_s": 810.0,
            "prompt_version": review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
            "input_profile": "p0_strict@1",
        }
        rec_out = tracking.recover_initial(d_d, svc_d, llm_config=cfg_sync, allow_post_window_retry=True)
        assert len(rec_out) == 1
        assert rec_out[0]["hand_id"] == EXPECTED_HAND_ID
        assert rec_out[0]["status"] == "VALID" and rec_out[0]["decision"] == "Accept"
        assert rec_out[0]["link"]["state"] == "SHADOW_LOCK_CREATED"
        assert seen_timeouts_d == [600.0, 600.0]

        # Subsequent 16:45Z sync-open & auto_deal ticks are 100% idempotent with ZERO duplicate locks
        clk_d[0] = slot_ms + 45 * M
        assert tracking.recover_initial(d_d, svc_d, llm_config=cfg_sync, allow_post_window_retry=True) == []
        assert len(seen_timeouts_d) == 2
        assert len(list((d_d.path(exp_ms) / "episodes").glob("episode_*"))) == 1


# ---------------------------------------------------------------------------
# (g) Supplement Item 2: Empirical Citation/Format Repair vs Substantive Bad Reply vs Real Reject
# ---------------------------------------------------------------------------

def test_empirical_citation_and_format_flaws_vs_substantive_bad_reply_and_real_reject(tmp_path, monkeypatch):
    slot_0 = int(datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc).timestamp() * 1000)
    clock = [slot_0 + 2 * M]
    wb, arch_root, dos_root, plan_r16, slot_ms, expiry_ms, _, sync_aux = _setup_nov26_workbench_and_archive(
        tmp_path / "item2_contrast", monkeypatch, clock=clock,
    )
    wb.refresh(now_ms=clock[0], record_daily=False)
    prep = wb.shadow_tracking(
        "prepare_deal",
        {"target_expiry_ms": expiry_ms, "slot_ms": slot_ms, "allow_refresh": False, "input_profile": "p0_strict@1"},
    )
    src = dossier.collect(
        arch_root, expiry_ms, slot_ms, clock=lambda: clock[0], candidate_round="r16",
        fallback_cases=prep["cases"],
        provenance_override="targeted workbench verified native case + Deribit public read",
    )
    d = dossier.Dossiers(dos_root, clock=lambda: clock[0], require_reply_provenance=True)
    clean_accept = _strict_p5_reply_text("## Accept / Put", short_strike=60_000, long_strike=58_000, require_outside_at_t0=True)
    pub_clean, json_clean = clean_accept.split("<ANNOTATION_JSON>")
    ann_clean = json.loads(json_clean.split("</ANNOTATION_JSON>")[0].strip())

    # 1) Valid Accept + non-critical clause-prefix citation (`hand_a050` pattern) + unclosed tag + trailing comma
    #    -> Losslessly repaired on Attempt 1 (1 LLM call, raw SHA preserved, warnings recorded, SHADOW_LOCK_CREATED)
    sections_clean = dossier.extract_labeled_sections(pub_clean)
    put_body = sections_clean["put"]["raw_full"]
    comma_idx = put_body.find("，")
    assert comma_idx > 24
    ann_clause_prefix = json.loads(json.dumps(ann_clean))
    ann_clause_prefix["put"] = put_body[:comma_idx]  # stops right before `，` (`hand_a050` pattern!)
    conf_body = sections_clean["confirmation_excerpt"]["raw_full"]
    conf_comma_idx = conf_body.rfind("，")
    assert conf_comma_idx > 30
    ann_clause_prefix["condition_plan"]["confirmation_excerpt"] = conf_body[:conf_comma_idx]
    # Add trailing comma before `}` AND omit `>` on closing `</ANNOTATION` tag
    json_with_trailing_comma = re.sub(r"\}\s*$", ",}", json.dumps(ann_clause_prefix, ensure_ascii=False))
    flawed_but_lossless_text = pub_clean + "<ANNOTATION_JSON>\n" + json_with_trailing_comma + "\n</ANNOTATION"

    h_lossless = d.expect(expiry_ms, "AD_HOC", reference="item2-lossless-accept")
    d.deal(h_lossless, src, input_profile="p0_strict@1")
    calls_lossless: list[int] = []
    with serving(wb) as base_wb:
        svc = post(base_wb, wb)
        cfg_lossless = tracking._llm_settings({
            "api_key": "k",
            "model": "deepseek-v4-pro",
            "transport": lambda *_a, **_kw: calls_lossless.append(1) or {
                "choices": [{"finish_reason": "stop", "message": {"content": flawed_but_lossless_text}}]
            },
        })
        r_loss = tracking._deal_hand(
            d, h_lossless, svc, cfg_lossless,
            prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION, input_profile="p0_strict@1",
        )
        assert r_loss["status"] == "VALID" and r_loss["decision"] == "Accept"
        assert r_loss["link"]["state"] == "SHADOW_LOCK_CREATED"
        assert len(calls_lossless) == 1
        att_loss = dossier.read(dossier.attempts(h_lossless)[0])
        expected_raw_bytes = (pub_clean.rstrip() + "\n").encode("utf-8")
        assert att_loss["raw_sha256"] == dossier.sha(expected_raw_bytes)
        cv_loss = att_loss["contract_validation"]
        assert cv_loss["status"] == "PASSED"
        assert any("excerpt_resolved_from_prose:put" in w for w in cv_loss["warnings"])
        assert any("excerpt_resolved_from_prose:condition_plan.confirmation_excerpt" in w for w in cv_loss["warnings"])
        assert any("annotation_closing_tag_repaired" in w for w in cv_loss["warnings"])
        assert any("annotation_json_trailing_comma_repaired" in w for w in cv_loss["warnings"])

        # 2) Valid Accept prose + wrong-section / duplicate `confirmation_excerpt` pointer (`hand_89df` / `hand_4924` pattern)
        #    -> Deterministically resolved from the single unambiguous M prose section on Attempt 1 (1 LLM call -> SHADOW_LOCK_CREATED!)
    clk_89df = [slot_0 + 2 * M]
    wb_89df, arch_89df, dos_89df, _, _, exp_89df, _, sync_89df = _setup_nov26_workbench_and_archive(
        tmp_path / "item2_89df_env", monkeypatch, clock=clk_89df,
    )
    wb_89df.refresh(now_ms=clk_89df[0], record_daily=False)
    prep_89df = wb_89df.shadow_tracking(
        "prepare_deal",
        {"target_expiry_ms": exp_89df, "slot_ms": slot_ms, "allow_refresh": False, "input_profile": "p0_strict@1"},
    )
    src_89df = dossier.collect(
        arch_89df, exp_89df, slot_ms, clock=lambda: clk_89df[0], candidate_round="r16",
        fallback_cases=prep_89df["cases"],
        provenance_override="targeted workbench verified native case + Deribit public read",
        input_profile="p0_strict@1",
    )
    d_89df = dossier.Dossiers(dos_89df, clock=lambda: clk_89df[0], require_reply_provenance=True)
    with serving(wb_89df) as base_89df:
        svc_89df = post(base_89df, wb_89df)
        ann_89df = json.loads(json.dumps(ann_clean))
        ann_89df["condition_plan"]["confirmation_excerpt"] = ann_clean["comparison"]
        text_89df_pointer_bug = pub_clean + "<ANNOTATION_JSON>\n" + json.dumps(ann_89df, ensure_ascii=False) + "\n</ANNOTATION_JSON>"
        h_89df = d_89df.expect(exp_89df, "AD_HOC", reference="item2-89df-wrong-section-in-place")
        d_89df.deal(h_89df, src_89df, input_profile="p0_strict@1")
        models_89df: list[str] = []

        def transport_89df(_k, model, *_a, **_kw):
            models_89df.append(model)
            return {"choices": [{"finish_reason": "stop", "message": {"content": text_89df_pointer_bug}}]}

        cfg_89df = tracking._llm_settings({
            "api_key": "k",
            "model": "deepseek-v4-pro",
            "fallback_enabled": True,
            "fallback_model": "deepseek-chat",
            "transport": transport_89df,
        })
        r_89df_1 = tracking._deal_hand(
            d_89df, h_89df, svc_89df, cfg_89df,
            prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION, input_profile="p0_strict@1",
        )
        assert r_89df_1["status"] == "VALID" and r_89df_1["decision"] == "Accept"
        assert r_89df_1["link"]["state"] == "SHADOW_LOCK_CREATED"
        assert models_89df == ["deepseek-v4-pro"]  # zero redundant LLM retries!
        att_89df_1 = dossier.read(dossier.attempts(h_89df)[0])
        assert any("excerpt_resolved_from_prose:condition_plan.confirmation_excerpt" in w for w in att_89df_1["contract_validation"]["warnings"])

    # 3) Substantively bad replies MUST stay hard-blocked with ZERO regeneration (`len(calls) == 1`):
    #    (i) `hand_a050` substantive M1 error (`严格进入Put责任区58,000至60,000美元` finite interval) even with clause prefix;
    #    (ii) Wrong-section `confirmation_excerpt` where prose `破坏确认口径` ALSO has 60-minute clock violation!
    bad_m_prose = (
        "破坏确认口径：采用 new_entry，每60分钟整点网格检查一次，check_interval_ms=300000，"
        "严格进入Put责任区58,000至60,000美元即触发，跌破58,000保护腿外侧不计入。"
    )
    pub_bad_m = pub_clean.replace(sections_clean["confirmation_excerpt"]["raw_full"], bad_m_prose)
    ann_bad_m_prefix = json.loads(json.dumps(ann_clean))
    ann_bad_m_prefix["condition_plan"]["confirmation_excerpt"] = bad_m_prose[: bad_m_prose.rfind("，")]
    bad_m_clause_text = pub_bad_m + "<ANNOTATION_JSON>\n" + json.dumps(ann_bad_m_prefix, ensure_ascii=False) + "\n</ANNOTATION_JSON>"

    ann_bad_m_wrong_sec = json.loads(json.dumps(ann_clean))
    ann_bad_m_wrong_sec["condition_plan"]["confirmation_excerpt"] = ann_clean["comparison"]
    bad_m_wrong_sec_text = pub_bad_m + "<ANNOTATION_JSON>\n" + json.dumps(ann_bad_m_wrong_sec, ensure_ascii=False) + "\n</ANNOTATION_JSON>"

    for label, substantive_bad_text in (
        ("a050-finite-interval-m1", bad_m_clause_text),
        ("wrong-sec-plus-bad-prose-m1", bad_m_wrong_sec_text),
    ):
        h_bad = d.expect(expiry_ms, "AD_HOC", reference=f"item2-substantive-{label}")
        d.deal(h_bad, src, input_profile="p0_strict@1")
        bad_calls: list[int] = []
        cfg_bad = tracking._llm_settings({
            "api_key": "k",
            "model": "deepseek-v4-pro",
            "transport": lambda *_a, _t=substantive_bad_text, **_kw: bad_calls.append(1) or {
                "choices": [{"finish_reason": "stop", "message": {"content": _t}}]
            },
        })
        r_b1 = tracking._deal_hand(
            d, h_bad, None, cfg_bad,
            prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION, input_profile="p0_strict@1",
        )
        assert r_b1["status"] == "PARSE_FAILED"
        att_b1 = dossier.read(dossier.attempts(h_bad)[0])
        assert att_b1["error_code"] == "CONTRACT_VALIDATION_FAILED"
        assert att_b1["contract_validation"]["recoverable_citation_only"] is False
        r_b2 = tracking._deal_hand(
            d, h_bad, None, cfg_bad,
            prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION, input_profile="p0_strict@1",
        )
        assert r_b2 == {"hand_id": h_bad.name, "status": "AUTO_ATTEMPTED", "last_status": "PARSE_FAILED"}
        assert len(bad_calls) == 1

    # 4) Real `Reject` with single clause-prefix J1 flaw -> stays `Reject` (VALID on Attempt 1, NEVER flipped to Accept!)
    clean_reject = _strict_p5_reply_text("## Reject")
    pub_rej, json_rej = clean_reject.split("<ANNOTATION_JSON>")
    ann_rej = json.loads(json_rej.split("</ANNOTATION_JSON>")[0].strip())
    rej_sections = dossier.extract_labeled_sections(pub_rej)
    rej_put_full = rej_sections["put"]["raw_full"]
    ann_rej["put"] = rej_put_full[: rej_put_full.find("，")]
    reject_clause_text = pub_rej + "<ANNOTATION_JSON>\n" + json.dumps(ann_rej, ensure_ascii=False) + "\n</ANNOTATION_JSON>"

    h_rej = d.expect(expiry_ms, "AD_HOC", reference="item2-reject-preserved")
    d.deal(h_rej, src, input_profile="p0_strict@1")
    rej_calls: list[int] = []
    cfg_rej = tracking._llm_settings({
        "api_key": "k",
        "model": "deepseek-v4-pro",
        "transport": lambda *_a, **_kw: rej_calls.append(1) or {
            "choices": [{"finish_reason": "stop", "message": {"content": reject_clause_text}}]
        },
    })
    r_rej = tracking._deal_hand(
        d, h_rej, None, cfg_rej,
        prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION, input_profile="p0_strict@1",
    )
    assert r_rej["status"] == "VALID"
    assert r_rej["decision"] == "Reject"
    assert r_rej["link"] is None
    assert len(rej_calls) == 1
    assert not (h_rej / "tracking" / "link.json").exists()

    # 5) Reviewer F2 & F3 regressions:
    #    - `HOLD_REVIEW_JSON` partial closing tag repaired symmetrically;
    #    - `dossier.collect(..., fallback_cases=..., input_profile="p0_strict@1")` prefers RV24-complete fallback over RV24-missing disk orphan.
    hold_pub, hold_obj, hold_reps = review._split_tagged_json_with_repairs(
        'HOLD\n维持观察。\n<HOLD_REVIEW_JSON>\n{"verdict":"HOLD","public_reply":"维持观察。",}\n</HOLD_REVIEW',
        "HOLD_REVIEW_JSON",
    )
    assert hold_pub == "HOLD\n维持观察。"
    assert hold_obj["verdict"] == "HOLD"
    assert "hold_closing_tag_repaired" in hold_reps
    assert "annotation_json_trailing_comma_repaired" in hold_reps

    rv_missing_put = json.loads(json.dumps(prep["cases"]["put"]))
    dossier.player.entry(rv_missing_put, "L4.expiry_vol")["values"]["realized_vol"]["rv_24h_pct"] = None
    rv_missing_put = _rehash_case(rv_missing_put, built_at_ms=clock[0])
    arch_f2 = tmp_path / "arch_f2"
    for sub in ("index", "structure", "band_snapshots", "levels"):
        if (arch_root / sub).exists():
            import shutil
            shutil.copytree(arch_root / sub, arch_f2 / sub)
    _persist_archive_case(arch_f2, rv_missing_put, day_ms=slot_ms, side="put", at_ms=clock[0])
    collected_f2 = dossier.collect(
        arch_f2,
        expiry_ms,
        slot_ms,
        clock=lambda: clock[0],
        candidate_round="r16",
        fallback_cases=prep["cases"],
        provenance_override="targeted workbench verified native case + Deribit public read",
        input_profile="p0_strict@1",
    )
    put_case_f2 = next(c for c in collected_f2["cases"] if dossier.player.values(c, "L1.contract")["side"] == "put")
    assert dossier.player.values(put_case_f2, "L4.expiry_vol")["realized_vol"]["rv_24h_pct"] is not None


# ---------------------------------------------------------------------------
# (h) Pre-Seal CLI Scheduler Entrypoints: Pre-Dispatch Exit, Unknown Receipt,
#     Persisted Receipt, Expired Never Dispatches, & Same-Hand Outcomes
# ---------------------------------------------------------------------------

def _run_tracking_cli(monkeypatch, capsys, *, argv: list[str], clock_box: list[int]):
    import sys

    orig_dossiers = dossier.Dossiers
    orig_prewarm = tracking.prewarm

    class PatchedDossiers(orig_dossiers):
        def __init__(self, root, clock=None, **kw):
            super().__init__(root, clock=(clock or (lambda: clock_box[0])), **kw)

    def patched_prewarm(plan_doc, arch_path, svc, **kw):
        kw_no_clk = {k: v for k, v in kw.items() if k not in {"clock", "public"}}
        return orig_prewarm(plan_doc, arch_path, svc, clock=lambda: clock_box[0], public=dossier.public, **kw_no_clk)

    capsys.readouterr()
    exit_code = 0
    with monkeypatch.context() as mp:
        mp.setattr(sys, "argv", ["astra_player_tracking_v13.py", *argv])
        mp.setattr(dossier, "Dossiers", PatchedDossiers)
        mp.setattr(tracking, "prewarm", patched_prewarm)
        try:
            tracking.main()
        except SystemExit as exc:
            exit_code = int(exc.code) if isinstance(exc.code, int) else 2
    captured = capsys.readouterr()
    parsed_out = json.loads(captured.out) if captured.out.strip().startswith(("{", "[")) else captured.out
    return exit_code, parsed_out, captured.err


def _run_dossier_cli(monkeypatch, capsys, *, argv: list[str], clock_box: list[int]):
    import sys

    orig_dossiers = dossier.Dossiers

    class PatchedDossiers(orig_dossiers):
        def __init__(self, root, clock=None, **kw):
            super().__init__(root, clock=(clock or (lambda: clock_box[0])), **kw)

    capsys.readouterr()
    exit_code = 0
    with monkeypatch.context() as mp:
        mp.setattr(sys, "argv", ["astra_player_dossier_v1.py", *argv])
        mp.setattr(dossier, "Dossiers", PatchedDossiers)
        try:
            dossier.main()
        except SystemExit as exc:
            exit_code = int(exc.code) if isinstance(exc.code, int) else 2
    captured = capsys.readouterr()
    parsed_out = json.loads(captured.out) if captured.out.strip().startswith(("{", "[")) else captured.out
    return exit_code, parsed_out, captured.err


def test_cli_scheduler_fault_injection_spine_and_same_hand_contrasts(tmp_path, monkeypatch, capsys):
    slot_0 = int(datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc).timestamp() * 1000)
    accept_put = _strict_p5_reply_text("## Accept / Put", short_strike=60_000, long_strike=58_000, require_outside_at_t0=True)
    unknown_watch = _strict_p5_reply_text("## Unknown")

    # 1) Frozen at 16:27Z -> exits BEFORE first dispatch (0 api_calls, 0 SERVICE_BUDGET_DEFERRED notes) -> misses 16:32Z ->
    #    wakes ONLY via scheduled `sync-open --check-due --review` CLI at 16:35, 16:40, 16:45, 16:50, 16:55Z ->
    #    dispatches EXACTLY ONCE inside original reply_deadline, locks once, settles via `settle-due` CLI!
    clk_1 = [slot_0 - 8 * M]  # 15:52:00Z
    wb_1, arch_1, dos_1, plan_1, slot_ms, exp_ms, _, sync_1 = _setup_nov26_workbench_and_archive(
        tmp_path / "cli_freeze_crash", monkeypatch, clock=clk_1,
    )
    plan_path_1 = tmp_path / "cli_freeze_crash" / "plan.json"
    dossier.write(plan_path_1, dossier.encode(plan_1), replace=True)

    with serving(wb_1) as wb_url_1, _sse_mock_server(
        lambda _idx, _body: {"status": 200, "reasoning": "补发推理...", "text": accept_put, "finish_reason": "stop"}
    ) as (llm_ep_1, llm_calls_1):
        monkeypatch.setenv("ASTRA_AI_SHADOW_LOCK_TOKEN", wb_1.config.shadow_service_token)
        monkeypatch.setenv("ASTRA_AI_SHADOW_EXPERIMENT", "1")
        monkeypatch.setenv("LLM_API_KEY", "k-cli-1")
        monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro")
        monkeypatch.setenv("LLM_BASE_URL", llm_ep_1)

        # 15:52Z CLI prewarm
        rc_pw, out_pw, err_pw = _run_tracking_cli(
            monkeypatch,
            capsys,
            argv=["--url", wb_url_1, "prewarm", "--plan", str(plan_path_1), "--archive", str(arch_1)],
            clock_box=clk_1,
        )
        assert rc_pw == 0 and err_pw == ""
        assert out_pw["business_readiness"]["ready_for_deal"] is True

        # 16:27Z CLI auto-deal crashes right after `Dossiers.deal` freezes `evidence/` (before `_deal_hand`!)
        clk_1[0] = slot_ms + 27 * M
        sync_1()
        wb_1.refresh(now_ms=clk_1[0], record_daily=False)
        crash_armed = {"on": True}
        orig_deal_hand = tracking._deal_hand

        def exit_before_dispatch(*a, **kw):
            if crash_armed["on"]:
                crash_armed["on"] = False
                raise SystemExit(137)  # SIGKILL / OOM / host restart before first dispatch
            return orig_deal_hand(*a, **kw)

        with monkeypatch.context() as mp_kill:
            mp_kill.setattr(tracking, "_deal_hand", exit_before_dispatch)
            rc_kill, _, _ = _run_tracking_cli(
                mp_kill,
                capsys,
                argv=["--url", wb_url_1, "auto-deal", "--root", str(dos_1), "--plan", str(plan_path_1), "--archive", str(arch_1)],
                clock_box=clk_1,
            )
        assert rc_kill == 137
        hand_1 = dos_1 / "dossiers" / f"Deribit_BTC_{dossier.utc(exp_ms):%Y%m%dT%H%M%SZ}" / "hands" / EXPECTED_HAND_ID
        assert (hand_1 / "evidence" / "identity.json").is_file()
        assert not (hand_1 / "api_calls").exists()
        assert not list((hand_1 / "issuance").glob("*.json"))  # ZERO SERVICE_BUDGET_DEFERRED note!
        assert len(llm_calls_1) == 0

        # Miss 16:32Z completely; wake via normal 5m `sync-open --check-due --review` CLI at 16:35, 40, 45, 50, 55Z
        for minute_offset in (35, 40, 45, 50, 55):
            clk_1[0] = slot_ms + minute_offset * M
            sync_1()
            wb_1.refresh(now_ms=clk_1[0], record_daily=False)
            rc_so, out_so, err_so = _run_tracking_cli(
                monkeypatch,
                capsys,
                argv=["--url", wb_url_1, "sync-open", "--root", str(dos_1), "--archive", str(arch_1), "--check-due", "--review"],
                clock_box=clk_1,
            )
            assert rc_so == 0, f"minute={minute_offset} err={err_so} out={out_so}"
            if minute_offset == 35:
                assert any(
                    r.get("hand_id") == EXPECTED_HAND_ID
                    and r.get("status") == "VALID"
                    and r.get("decision") == "Accept"
                    and (r.get("link") or {}).get("state") == "SHADOW_LOCK_CREATED"
                    for r in out_so
                )
            assert len(llm_calls_1) == 1  # Dispatched ONCE at :35; :40/:45/:50/:55 do NOT re-dispatch!

        assert len(list((hand_1.parent.parent / "episodes").glob("episode_*"))) == 1

        # Post-expiry `settle-due` CLI settles once and stays idempotent
        clk_1[0] = exp_ms + 5 * M
        rc_set1, out_set1, _ = _run_dossier_cli(
            monkeypatch, capsys, argv=["--root", str(dos_1), "settle-due"], clock_box=clk_1,
        )
        assert rc_set1 == 0 and len(out_set1) == 1
        rc_set2, out_set2, _ = _run_dossier_cli(
            monkeypatch, capsys, argv=["--root", str(dos_1), "settle-due"], clock_box=clk_1,
        )
        assert rc_set2 == 0 and out_set2 == []

    # 2) Dispatch credential exists (`dispatch.json` WITHOUT `request.json`, and `dispatch.json` + `request.json` + `partial_reply.json`)
    #    with unknown receipt (`reply.json` absent) -> ZERO resend across `auto-deal` and `sync-open --check-due --review` CLI!
    for sub_name, include_req, include_partial in (
        ("orphan_dispatch_only", False, False),
        ("dispatch_plus_partial", True, True),
    ):
        clk_2 = [slot_0 + 27 * M]
        wb_2, arch_2, dos_2, plan_2, _, exp_2, _, sync_2 = _setup_nov26_workbench_and_archive(
            tmp_path / f"cli_unk_{sub_name}", monkeypatch, clock=clk_2,
        )
        plan_path_2 = tmp_path / f"cli_unk_{sub_name}" / "plan.json"
        dossier.write(plan_path_2, dossier.encode(plan_2), replace=True)
        wb_2.refresh(now_ms=clk_2[0], record_daily=False)
        prep_2 = wb_2.shadow_tracking(
            "prepare_deal",
            {"target_expiry_ms": exp_2, "slot_ms": slot_0, "allow_refresh": False, "input_profile": "p0_strict@1"},
        )
        src_2 = dossier.collect(
            arch_2, exp_2, slot_0, clock=lambda: clk_2[0], candidate_round="r16",
            fallback_cases=prep_2["cases"],
            provenance_override="targeted workbench verified native case + Deribit public read",
            input_profile="p0_strict@1",
        )
        d_2 = dossier.Dossiers(dos_2, clock=lambda: clk_2[0], require_reply_provenance=True)
        h_2, = d_2.tick(plan_2, slots=[slot_0], input_profile="p0_strict@1")
        d_2.deal(h_2, src_2, input_profile="p0_strict@1")
        att_id_2 = dossier.identity("auto", h_2.name, clk_2[0], "deepseek-v4-pro", 0)
        call_dir_2 = h_2 / "api_calls" / att_id_2
        files_2 = {
            "dispatch.json": dossier.encode({
                "started_at_ms": clk_2[0],
                "requested_at_ms": clk_2[0],
                "model": "deepseek-v4-pro",
                "timeout_s": 600.0,
            }),
        }
        if include_req:
            meta_2 = d_2.verify_evidence(h_2)
            req_2 = review.build_initial_request_payload(
                (h_2 / "evidence" / "input.md").read_text(encoding="utf-8"),
                model="deepseek-v4-pro",
                prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
                input_profile="p0_strict@1",
                packet_hash=meta_2["input_sha256"],
                requested_at_ms=clk_2[0],
                fidelity="EXACT",
            )
            files_2["request.json"] = dossier.encode(review.sanitize_model_request(req_2))
        if include_partial:
            files_2["partial_reply.json"] = dossier.encode({"text": "## Accept / Put\n半截断流", "partial": True, "updated_at_ms": clk_2[0] + 1_000})
        dossier.publish(call_dir_2, files_2)

        # Before reconciliation, `audit_record` already sees the dispatch credential (never `AWAITING_DISPATCH`)
        aud_pre_2 = tracking.audit_record(h_2, clk_2[0] + 5_000, {})
        assert aud_pre_2["stage"] == "REQUEST_IN_PROGRESS"

        with serving(wb_2) as wb_url_2, _sse_mock_server(
            lambda *_a: pytest.fail(f"unknown receipt ({sub_name}) must NEVER resend LLM request")
        ) as (llm_ep_2, llm_calls_2):
            monkeypatch.setenv("ASTRA_AI_SHADOW_LOCK_TOKEN", wb_2.config.shadow_service_token)
            monkeypatch.setenv("LLM_API_KEY", "k-cli-2")
            monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro")
            monkeypatch.setenv("LLM_BASE_URL", llm_ep_2)

            # Reconciles to DELIVERY_UNKNOWN on 16:35Z & 16:40Z `sync-open --check-due --review` CLI with ZERO HTTP calls
            for m_off in (35, 40):
                clk_2[0] = slot_0 + m_off * M
                sync_2()
                rc_u, out_u, _ = _run_tracking_cli(
                    monkeypatch,
                    capsys,
                    argv=["--url", wb_url_2, "sync-open", "--root", str(dos_2), "--archive", str(arch_2), "--check-due", "--review"],
                    clock_box=clk_2,
                )
                if m_off == 35:
                    assert rc_u == 2  # first reconciliation surfaces REQUEST_FAILED/DELIVERY_UNKNOWN explicitly
                else:
                    assert rc_u == 0 and out_u == []
            assert len(llm_calls_2) == 0
            att_saved_2 = dossier.read(dossier.attempts(h_2)[0])
            assert att_saved_2["configuration"]["delivery_state"] == "DELIVERY_UNKNOWN"

    # 3) Persisted valid receipt (`reply.json` on disk without `attempt.json`/`decision.json`/`link.json`) ->
    #    `sync-open --check-due --review` CLI recovers verdict + SHADOW_LOCK_CREATED + runs due grid check in ONE tick with ZERO resend!
    clk_3 = [slot_0 + 27 * M]
    wb_3, arch_3, dos_3, plan_3, _, exp_3, _, sync_3 = _setup_nov26_workbench_and_archive(
        tmp_path / "cli_persisted_receipt", monkeypatch, clock=clk_3,
    )
    wb_3.refresh(now_ms=clk_3[0], record_daily=False)
    prep_3 = wb_3.shadow_tracking(
        "prepare_deal",
        {"target_expiry_ms": exp_3, "slot_ms": slot_0, "allow_refresh": False, "input_profile": "p0_strict@1"},
    )
    src_3 = dossier.collect(
        arch_3, exp_3, slot_0, clock=lambda: clk_3[0], candidate_round="r16",
        fallback_cases=prep_3["cases"],
        provenance_override="targeted workbench verified native case + Deribit public read",
        input_profile="p0_strict@1",
    )
    d_3 = dossier.Dossiers(dos_3, clock=lambda: clk_3[0], require_reply_provenance=True)
    h_3, = d_3.tick(plan_3, slots=[slot_0], input_profile="p0_strict@1")
    d_3.deal(h_3, src_3, input_profile="p0_strict@1")
    meta_3 = d_3.verify_evidence(h_3)
    att_id_3 = dossier.identity("auto", h_3.name, clk_3[0], "deepseek-v4-pro", 0)
    req_3 = review.build_initial_request_payload(
        (h_3 / "evidence" / "input.md").read_text(encoding="utf-8"),
        model="deepseek-v4-pro",
        prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
        input_profile="p0_strict@1",
        packet_hash=meta_3["input_sha256"],
        requested_at_ms=clk_3[0],
        fidelity="EXACT",
    )
    dossier.publish(h_3 / "api_calls" / att_id_3, {
        "request.json": dossier.encode(review.sanitize_model_request(req_3)),
        "dispatch.json": dossier.encode({"started_at_ms": clk_3[0], "requested_at_ms": clk_3[0], "model": "deepseek-v4-pro", "timeout_s": 600.0}),
        "reply.json": dossier.encode({"text": accept_put, "model": "deepseek-v4-pro", "http_status": 200, "decided_at_ms": clk_3[0] + 15_000}),
    })
    assert tracking.audit_record(h_3, clk_3[0] + 20_000, {})["stage"] == "REPLY_SAVED"

    with serving(wb_3) as wb_url_3, _sse_mock_server(
        lambda *_a: pytest.fail("persisted valid receipt must NEVER resend LLM request")
    ) as (llm_ep_3, llm_calls_3):
        monkeypatch.setenv("ASTRA_AI_SHADOW_LOCK_TOKEN", wb_3.config.shadow_service_token)
        monkeypatch.setenv("LLM_API_KEY", "k-cli-3")
        monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro")
        monkeypatch.setenv("LLM_BASE_URL", llm_ep_3)

        clk_3[0] = slot_0 + 35 * M  # 16:35Z
        sync_3()
        wb_3.refresh(now_ms=clk_3[0], record_daily=False)
        rc_3, out_3, err_3 = _run_tracking_cli(
            monkeypatch,
            capsys,
            argv=["--url", wb_url_3, "sync-open", "--root", str(dos_3), "--archive", str(arch_3), "--check-due", "--review"],
            clock_box=clk_3,
        )
        assert rc_3 == 0 and err_3 == ""
        assert len(llm_calls_3) == 0
        # Phase 1 promoted persisted reply to SHADOW_LOCK_CREATED AND Phase 3 already synced active episode in same tick!
        assert any((r.get("link") or {}).get("state") == "SHADOW_LOCK_CREATED" for r in out_3)
        assert any(r.get("state") == "active" for r in out_3)
        assert len(list((h_3.parent.parent / "episodes").glob("episode_*"))) == 1

    # 3b) Unrecorded truncated `reply.json` on disk -> Phase 1 reconciles `PARSE_FAILED`, Phase 3 syncs open hands,
    #     Phase 4 retries ONCE to `VALID + SHADOW_LOCK_CREATED` and supersedes the intermediate Phase 1 row (`rc == 0`, `ALL_OK`)!
    clk_3b = [slot_0 + 27 * M]
    wb_3b, arch_3b, dos_3b, plan_3b, _, exp_3b, _, sync_3b = _setup_nov26_workbench_and_archive(
        tmp_path / "cli_persisted_trunc_retry", monkeypatch, clock=clk_3b,
    )
    wb_3b.refresh(now_ms=clk_3b[0], record_daily=False)
    prep_3b = wb_3b.shadow_tracking(
        "prepare_deal",
        {"target_expiry_ms": exp_3b, "slot_ms": slot_0, "allow_refresh": False, "input_profile": "p0_strict@1"},
    )
    src_3b = dossier.collect(
        arch_3b, exp_3b, slot_0, clock=lambda: clk_3b[0], candidate_round="r16",
        fallback_cases=prep_3b["cases"],
        provenance_override="targeted workbench verified native case + Deribit public read",
        input_profile="p0_strict@1",
    )
    d_3b = dossier.Dossiers(dos_3b, clock=lambda: clk_3b[0], require_reply_provenance=True)
    h_3b, = d_3b.tick(plan_3b, slots=[slot_0], input_profile="p0_strict@1")
    d_3b.deal(h_3b, src_3b, input_profile="p0_strict@1")
    meta_3b = d_3b.verify_evidence(h_3b)
    att_id_3b = dossier.identity("auto", h_3b.name, clk_3b[0], "deepseek-v4-pro", 0)
    req_3b = review.build_initial_request_payload(
        (h_3b / "evidence" / "input.md").read_text(encoding="utf-8"),
        model="deepseek-v4-pro",
        prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
        input_profile="p0_strict@1",
        packet_hash=meta_3b["input_sha256"],
        requested_at_ms=clk_3b[0],
        fidelity="EXACT",
    )
    dossier.publish(h_3b / "api_calls" / att_id_3b, {
        "request.json": dossier.encode(review.sanitize_model_request(req_3b)),
        "dispatch.json": dossier.encode({"started_at_ms": clk_3b[0], "requested_at_ms": clk_3b[0], "model": "deepseek-v4-pro", "timeout_s": 600.0}),
        "reply.json": dossier.encode({
            "text": "## Accept / Put\n流式输出因length截断未完成JSON",
            "finish_reason": "length",
            "model": "deepseek-v4-pro",
            "http_status": 200,
            "decided_at_ms": clk_3b[0] + 10_000,
        }),
    })
    with serving(wb_3b) as wb_url_3b, _sse_mock_server(
        lambda _i, _b: {"status": 200, "text": accept_put, "finish_reason": "stop"}
    ) as (llm_ep_3b, llm_calls_3b):
        monkeypatch.setenv("ASTRA_AI_SHADOW_LOCK_TOKEN", wb_3b.config.shadow_service_token)
        monkeypatch.setenv("LLM_API_KEY", "k-cli-3b")
        monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro")
        monkeypatch.setenv("LLM_BASE_URL", llm_ep_3b)
        clk_3b[0] = slot_0 + 35 * M
        sync_3b()
        wb_3b.refresh(now_ms=clk_3b[0], record_daily=False)
        rc_3b, out_3b, err_3b = _run_tracking_cli(
            monkeypatch,
            capsys,
            argv=["--url", wb_url_3b, "sync-open", "--root", str(dos_3b), "--archive", str(arch_3b), "--check-due", "--review"],
            clock_box=clk_3b,
        )
        assert rc_3b == 0 and err_3b == ""
        assert len(llm_calls_3b) == 1
        hand_3b_rows = [r for r in out_3b if isinstance(r, dict) and r.get("hand_id") == EXPECTED_HAND_ID]
        assert len(hand_3b_rows) == 1 and hand_3b_rows[0]["status"] == "VALID"
        assert hand_3b_rows[0]["link"]["state"] == "SHADOW_LOCK_CREATED"

    # 4) Expired (`now >= reply_deadline` AND `< 45s` remaining) -> NEVER dispatches, seals `REPLY_WINDOW_CLOSED`, and cleans post-deadline `SERVICE_BUDGET_DEFERRED` state!
    clk_4 = [slot_0 + 2 * M]  # frozen at 16:02Z -> reply_deadline = 16:32:00Z
    wb_4, arch_4, dos_4, plan_4, _, exp_4, _, sync_4 = _setup_nov26_workbench_and_archive(
        tmp_path / "cli_expired", monkeypatch, clock=clk_4,
    )
    wb_4.refresh(now_ms=clk_4[0], record_daily=False)
    prep_4 = wb_4.shadow_tracking(
        "prepare_deal",
        {"target_expiry_ms": exp_4, "slot_ms": slot_0, "allow_refresh": False, "input_profile": "p0_strict@1"},
    )
    src_4 = dossier.collect(
        arch_4, exp_4, slot_0, clock=lambda: clk_4[0], candidate_round="r16",
        fallback_cases=prep_4["cases"],
        provenance_override="targeted workbench verified native case + Deribit public read",
        input_profile="p0_strict@1",
    )
    d_4 = dossier.Dossiers(dos_4, clock=lambda: clk_4[0], require_reply_provenance=True)
    h_4, = d_4.tick(plan_4, slots=[slot_0], input_profile="p0_strict@1")
    d_4.deal(h_4, src_4, input_profile="p0_strict@1")
    d_4.note(h_4, "SERVICE_BUDGET_DEFERRED", "remaining_service_s=20.0")
    assert tracking.audit_record(h_4, clk_4[0] + 1_000, {})["state"] == "SERVICE_BUDGET_DEFERRED"

    with serving(wb_4) as wb_url_4, _sse_mock_server(
        lambda *_a: pytest.fail("expired hand must NEVER dispatch")
    ) as (llm_ep_4, llm_calls_4):
        monkeypatch.setenv("ASTRA_AI_SHADOW_LOCK_TOKEN", wb_4.config.shadow_service_token)
        monkeypatch.setenv("LLM_API_KEY", "k-cli-4")
        monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro")
        monkeypatch.setenv("LLM_BASE_URL", llm_ep_4)

        for m_off in (35, 40):
            clk_4[0] = slot_0 + m_off * M  # >= 16:32Z reply_deadline!
            sync_4()
            rc_4, out_4, _ = _run_tracking_cli(
                monkeypatch,
                capsys,
                argv=["--url", wb_url_4, "sync-open", "--root", str(dos_4), "--archive", str(arch_4), "--check-due", "--review"],
                clock_box=clk_4,
            )
            assert rc_4 == 0 and out_4 == []
        assert len(llm_calls_4) == 0
        aud_4 = tracking.audit_record(h_4, clk_4[0], {})
        assert aud_4["state"] == "NO_RESPONSE" and aud_4["stage"] == "DISPATCH_EXPIRED"
        notes_4 = [dossier.read(p) for p in (h_4 / "issuance").glob("*.json")]
        assert sum(1 for n in notes_4 if n["state"] == "REPLY_WINDOW_CLOSED") == 1

    # Also verify `< 45s` remaining (`16:56:20Z` vs `16:57:00Z` deadline) never dispatches
    clk_4b = [slot_0 + 27 * M]
    wb_4b, arch_4b, dos_4b, plan_4b, _, exp_4b, _, sync_4b = _setup_nov26_workbench_and_archive(
        tmp_path / "cli_under_45s", monkeypatch, clock=clk_4b,
    )
    wb_4b.refresh(now_ms=clk_4b[0], record_daily=False)
    prep_4b = wb_4b.shadow_tracking(
        "prepare_deal",
        {"target_expiry_ms": exp_4b, "slot_ms": slot_0, "allow_refresh": False, "input_profile": "p0_strict@1"},
    )
    src_4b = dossier.collect(
        arch_4b, exp_4b, slot_0, clock=lambda: clk_4b[0], candidate_round="r16",
        fallback_cases=prep_4b["cases"],
        provenance_override="targeted workbench verified native case + Deribit public read",
        input_profile="p0_strict@1",
    )
    d_4b = dossier.Dossiers(dos_4b, clock=lambda: clk_4b[0], require_reply_provenance=True)
    h_4b, = d_4b.tick(plan_4b, slots=[slot_0], input_profile="p0_strict@1")
    d_4b.deal(h_4b, src_4b, input_profile="p0_strict@1")
    with serving(wb_4b) as wb_url_4b, _sse_mock_server(
        lambda *_a: pytest.fail("<45s remaining must NEVER dispatch")
    ) as (llm_ep_4b, llm_calls_4b):
        monkeypatch.setenv("ASTRA_AI_SHADOW_LOCK_TOKEN", wb_4b.config.shadow_service_token)
        monkeypatch.setenv("LLM_API_KEY", "k-cli-4b")
        monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro")
        monkeypatch.setenv("LLM_BASE_URL", llm_ep_4b)
        clk_4b[0] = slot_0 + 56 * M + 20_000  # 16:56:20Z -> 40s (< 45s) before 16:57:00Z
        sync_4b()
        rc_4b, out_4b, _ = _run_tracking_cli(
            monkeypatch,
            capsys,
            argv=["--url", wb_url_4b, "sync-open", "--root", str(dos_4b), "--archive", str(arch_4b), "--check-due", "--review"],
            clock_box=clk_4b,
        )
        assert rc_4b == 0 and out_4b == [] and len(llm_calls_4b) == 0

    # 5) Same-hand (`hand_66a598b4790172fa3fd56276`) contrast: Upstream single-side unavailability at :02 (`DATA_BLOCKED`, 0 LLM calls)
    #    -> recovers at :07 -> normal `Unknown` (`观望`) decision -> no shadow lock -> clean settlement!
    clk_5 = [slot_0 + 2 * M]
    wb_5, arch_5, dos_5, plan_5, _, exp_5, _, sync_5 = _setup_nov26_workbench_and_archive(
        tmp_path / "cli_unknown_and_partial_upstream", monkeypatch, clock=clk_5,
    )
    plan_path_5 = tmp_path / "cli_unknown_and_partial_upstream" / "plan.json"
    dossier.write(plan_path_5, dossier.encode(plan_5), replace=True)
    orig_prep_5 = wb_5._prepare_shadow_deal
    block_put_once = {"on": True}

    def partial_upstream_prepare(body):
        if block_put_once["on"]:
            return {
                "ready_for_deal": False,
                "readiness_status": "DERIBIT_429_COOLDOWN",
                "blockers": ["put_book_429_incomplete"],
                "sides": {"put": {"ok": False, "reason": "http_429"}, "call": {"ok": True}},
                "deribit_cooldown_remaining_ms": 12_000,
            }
        return orig_prep_5(body)

    monkeypatch.setattr(wb_5, "_prepare_shadow_deal", partial_upstream_prepare)
    with serving(wb_5) as wb_url_5, _sse_mock_server(
        lambda _i, _b: {"status": 200, "text": unknown_watch, "finish_reason": "stop"}
    ) as (llm_ep_5, llm_calls_5):
        monkeypatch.setenv("ASTRA_AI_SHADOW_LOCK_TOKEN", wb_5.config.shadow_service_token)
        monkeypatch.setenv("LLM_API_KEY", "k-cli-5")
        monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro")
        monkeypatch.setenv("LLM_BASE_URL", llm_ep_5)

        # :02 -> upstream Put unavailable -> exits 2 with DATA_BLOCKED and 0 LLM calls
        rc_5a, out_5a, _ = _run_tracking_cli(
            monkeypatch,
            capsys,
            argv=["--url", wb_url_5, "auto-deal", "--root", str(dos_5), "--plan", str(plan_path_5), "--archive", str(arch_5)],
            clock_box=clk_5,
        )
        assert rc_5a == 2
        assert out_5a[0]["hand_id"] == EXPECTED_HAND_ID and out_5a[0]["status"] == "NO_EVIDENCE"
        assert out_5a[0]["block_state"] == "DATA_BLOCKED"
        assert len(llm_calls_5) == 0

        # :07 -> upstream recovers -> freezes & decides `Unknown` (`观望`) -> no lock -> idempotent at :12 -> settles cleanly!
        block_put_once["on"] = False
        clk_5[0] = slot_0 + 7 * M
        sync_5()
        wb_5.refresh(now_ms=clk_5[0], record_daily=False)
        rc_5b, out_5b, _ = _run_tracking_cli(
            monkeypatch,
            capsys,
            argv=["--url", wb_url_5, "auto-deal", "--root", str(dos_5), "--plan", str(plan_path_5), "--archive", str(arch_5)],
            clock_box=clk_5,
        )
        assert rc_5b == 0
        assert out_5b[0] == {"hand_id": EXPECTED_HAND_ID, "status": "VALID", "decision": "Unknown", "error": None, "link": None}
        assert len(llm_calls_5) == 1
        h_5 = dos_5 / "dossiers" / f"Deribit_BTC_{dossier.utc(exp_5):%Y%m%dT%H%M%SZ}" / "hands" / EXPECTED_HAND_ID
        assert not (h_5 / "tracking" / "link.json").exists()

        clk_5[0] = slot_0 + 12 * M
        rc_5c, out_5c, _ = _run_tracking_cli(
            monkeypatch,
            capsys,
            argv=["--url", wb_url_5, "auto-deal", "--root", str(dos_5), "--plan", str(plan_path_5), "--archive", str(arch_5)],
            clock_box=clk_5,
        )
        assert rc_5c == 0 and out_5c[0]["status"] == "ALREADY_DECIDED" and out_5c[0]["decision"] == "Unknown"
        assert len(llm_calls_5) == 1

        clk_5[0] = exp_5 + 5 * M
        rc_5s, out_5s, _ = _run_dossier_cli(
            monkeypatch, capsys, argv=["--root", str(dos_5), "settle-due"], clock_box=clk_5,
        )
        assert rc_5s == 0 and len(out_5s) == 1
        aud_5 = tracking.audit_record(h_5, clk_5[0], {})
        assert aud_5["state"] == "Unknown" and aud_5["stage"] == "Unknown"


# ---------------------------------------------------------------------------
# (i) Contention Reproduction & Isolation: Slow New-Hand LLM Request NEVER
#     Blocks Urgent Hold Reviews (`HOLD` / `REVOKE` -> `SHADOW_EXITED`) of Open Hands
# ---------------------------------------------------------------------------

def test_new_hand_slow_request_never_blocks_existing_open_hand_hold_reviews(tmp_path, monkeypatch, capsys):
    import threading

    slot_0 = int(datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc).timestamp() * 1000)
    accept_put = _strict_p5_reply_text("## Accept / Put", short_strike=60_000, long_strike=58_000, require_outside_at_t0=True)
    hold_reply = (
        '## HOLD\n\n缓冲仍在可承受范围，维持观察。\n'
        '<HOLD_REVIEW_JSON>{"verdict":"HOLD","affected_conditions":[],"public_reply":"维持观察","key_facts":["余量尚存"]}</HOLD_REVIEW_JSON>'
    )
    revoke_reply = (
        '## REVOKE\n\n卖出短腿60,000已被跌破且确认触发M1，立即撤销。\n'
        '<HOLD_REVIEW_JSON>{"verdict":"REVOKE","affected_conditions":["M1"],"public_reply":"确认触发M1撤销","key_facts":["跌破60000"]}</HOLD_REVIEW_JSON>'
    )

    clk = [slot_0 + 2 * M]
    wb, arch_root, dos_root, plan_r16, slot_ms, exp_ms, _, sync_aux = _setup_nov26_workbench_and_archive(
        tmp_path / "priority_isolation", monkeypatch, clock=clk,
    )
    plan_path = tmp_path / "priority_isolation" / "plan.json"
    dossier.write(plan_path, dossier.encode(plan_r16), replace=True)
    wb.refresh(now_ms=clk[0], record_daily=False)
    prep = wb.shadow_tracking(
        "prepare_deal",
        {"target_expiry_ms": exp_ms, "slot_ms": slot_ms, "allow_refresh": False, "input_profile": "p0_strict@1"},
    )
    base_src = dossier.collect(
        arch_root, exp_ms, slot_ms, clock=lambda: clk[0], candidate_round="r16",
        fallback_cases=prep["cases"],
        provenance_override="targeted workbench verified native case + Deribit public read",
        input_profile="p0_strict@1",
    )

    # Create an already-open locked shadow hand `h_open` (`AD_HOC` on same dossier) at 16:02Z
    d = dossier.Dossiers(dos_root, clock=lambda: clk[0], require_reply_provenance=True)
    h_open = d.expect(exp_ms, "AD_HOC", reference="existing-open-position")
    d.deal(h_open, base_src, input_profile="p0_strict@1")

    with serving(wb) as wb_url:
        svc = post(wb_url, wb)
        cfg_open_init = tracking._llm_settings({
            "api_key": "k-iso",
            "model": "deepseek-v4-pro",
            "transport": lambda *_a, **_kw: {"choices": [{"finish_reason": "stop", "message": {"content": accept_put}}]},
        })
        res_open_init = tracking._deal_hand(
            d, h_open, svc, cfg_open_init,
            prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION, input_profile="p0_strict@1",
        )
        assert res_open_init["link"]["state"] == "SHADOW_LOCK_CREATED"
        open_ep_id = res_open_init["link"]["origin"]["dossier_episode_id"]
        open_ep_dir = h_open.parent.parent / "episodes" / open_ep_id

        # Step 1: Verify `HOLD` review on `h_open` at 16:15Z before testing contention
        clk[0] = slot_ms + 15 * M
        sync_aux()
        wb.refresh(now_ms=clk[0], record_daily=False)
        d.event(
            open_ep_dir,
            dossier.identity("chk_hold", clk[0] - M),
            "CONDITION_TRIGGERED",
            {"result": {"status": "triggered", "rule": "entry_not_returned"}},
            observed=clk[0] - M,
            available=clk[0],
        )
        with _sse_mock_server(
            lambda _i, _b: {"status": 200, "text": hold_reply, "finish_reason": "stop"}
        ) as (ep_hold, calls_hold):
            monkeypatch.setenv("ASTRA_AI_SHADOW_LOCK_TOKEN", wb.config.shadow_service_token)
            monkeypatch.setenv("LLM_API_KEY", "k-iso")
            monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro")
            monkeypatch.setenv("LLM_BASE_URL", ep_hold)
            # Verify `<45s` service-budget guard in `evaluate_hold_management` defers cleanly (`inflight=False`, `pending` preserved) before live HOLD call!
            exported_15 = svc("export", {"dossier_id": h_open.parent.parent.name, "hand_id": h_open.name})
            def_res = tracking.evaluate_hold_management(
                d,
                h_open,
                exported_15,
                archive_root=arch_root,
                service=svc,
                llm_config={
                    "api_key": "k-iso",
                    "model": "deepseek-v4-pro",
                    "service_deadline_mono": tracking.time.monotonic() + 20.0,  # < 45s -> defer without setting inflight!
                },
            )
            assert def_res["action"] == "QUEUED" and def_res.get("deferred_reason") == "service_budget_deferred"
            assert dossier.read(open_ep_dir / "trigger_state.json")["inflight"] is False
            assert len(calls_hold) == 0

            rc_h, out_h, err_h = _run_tracking_cli(
                monkeypatch,
                capsys,
                argv=["--url", wb_url, "sync-open", "--root", str(dos_root), "--archive", str(arch_root), "--check-due", "--review"],
                clock_box=clk,
            )
            assert rc_h == 0 and len(calls_hold) == 1, f"rc={rc_h} calls={len(calls_hold)} err={err_h!r} out={out_h!r}"
            assert any((r.get("hold_review") or {}).get("verdict") == "HOLD" for r in out_h)
            assert not (h_open / "tracking" / "shadow_exit.json").exists()

        # Step 2 (Mode A — Same `16:40Z` `sync-open --check-due --review` CLI batch):
        #   - `h_new` (`hand_66a598b4790172fa3fd56276`) froze at `16:27Z`, missed `:32`, and has a SLOW 540s LLM call;
        #   - `h_open` has a fresh `CONDITION_TRIGGERED` breach at `16:39Z` requiring urgent `REVOKE` -> `SHADOW_EXITED`!
        #   Verify `h_open` `hold_review` runs FIRST (at t=+0s, NOT behind `h_new`'s slow 540s initial call!), exits `SHADOW_EXITED`,
        #   and `h_new` then executes cleanly after `h_open`!
        clk[0] = slot_ms + 27 * M
        sync_aux()
        wb.refresh(now_ms=clk[0], record_daily=False)
        prep_27 = wb.shadow_tracking(
            "prepare_deal",
            {"target_expiry_ms": exp_ms, "slot_ms": slot_ms, "allow_refresh": False, "input_profile": "p0_strict@1"},
        )
        src_27 = dossier.collect(
            arch_root, exp_ms, slot_ms, clock=lambda: clk[0], candidate_round="r16",
            fallback_cases=prep_27["cases"],
            provenance_override="targeted workbench verified native case + Deribit public read",
            input_profile="p0_strict@1",
        )
        h_new, = d.tick(plan_r16, slots=[slot_ms], input_profile="p0_strict@1")
        assert h_new.name == EXPECTED_HAND_ID
        d.deal(h_new, src_27, input_profile="p0_strict@1")

        # Advance to 16:40Z (> 15m cooldown since 16:15Z HOLD) and record new severe M1 trigger on `h_open`
        clk[0] = slot_ms + 40 * M
        sync_aux()
        wb.refresh(now_ms=clk[0], record_daily=False)
        d.event(
            open_ep_dir,
            dossier.identity("chk_rev", clk[0] - M),
            "CONDITION_TRIGGERED",
            {"result": {"status": "triggered", "rule": "entry_not_returned", "breach_usd": 58_900.0}},
            observed=clk[0] - M,
            available=clk[0],
        )
        # Clear dispatched signature so fresh M1 breach triggers urgent review at 16:40Z
        st_open = dossier.read(open_ep_dir / "trigger_state.json")
        st_open["dispatched_signatures"] = {}
        st_open["cooldown_until_ms"] = None
        dossier.write(open_ep_dir / "trigger_state.json", dossier.encode(st_open), replace=True)

        call_order: list[tuple[str, int]] = []

        def priority_sse_script(_idx: int, body: dict) -> dict:
            sys_prompt = str(((body.get("messages") or [{}])[0]).get("content") or "")
            is_hold_call = ("HOLD_REVIEW_JSON" in sys_prompt) or ("持仓" in sys_prompt and "ANNOTATION_JSON" not in sys_prompt)
            if is_hold_call:
                call_order.append(("open_hand_hold_review", clk[0]))
                return {"status": 200, "text": revoke_reply, "finish_reason": "stop"}
            # Slow 420s new-hand initial reasoning! If this had run first, `open_hand_hold_review` would be delayed to 16:47Z!
            call_order.append(("new_hand_initial_deal_start", clk[0]))
            clk[0] = slot_ms + 47 * M
            sync_aux()
            wb.refresh(now_ms=clk[0], record_daily=False)
            return {"status": 200, "text": accept_put, "finish_reason": "stop"}

        with _sse_mock_server(priority_sse_script) as (ep_prio, _calls_prio):
            monkeypatch.setenv("ASTRA_AI_SHADOW_LOCK_TOKEN", wb.config.shadow_service_token)
            monkeypatch.setenv("LLM_API_KEY", "k-iso")
            monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro")
            monkeypatch.setenv("LLM_BASE_URL", ep_prio)

            rc_prio, out_prio, err_prio = _run_tracking_cli(
                monkeypatch,
                capsys,
                argv=["--url", wb_url, "sync-open", "--root", str(dos_root), "--archive", str(arch_root), "--check-due", "--review"],
                clock_box=clk,
            )
            assert rc_prio == 0, f"err={err_prio} out={out_prio}"

        assert call_order == [
            ("open_hand_hold_review", slot_ms + 40 * M),
            ("new_hand_initial_deal_start", slot_ms + 40 * M),
        ]
        sx_open = dossier.read(h_open / "tracking" / "shadow_exit.json")
        assert sx_open["exit_state"] == "SHADOW_EXITED" and sx_open["tracker_stopped"] is True
        assert (h_new / "tracking" / "link.json").is_file()

    # Step 3 (Mode B — Concurrent `16:05Z` `auto_deal` blocked inside a slow `h_new` LLM request WHILE `sync_open` runs on `h_open`):
    clk_b = [slot_0 + 2 * M]
    wb_b, arch_b, dos_b, plan_b, _, exp_b, _, sync_b = _setup_nov26_workbench_and_archive(
        tmp_path / "concurrent_isolation", monkeypatch, clock=clk_b,
    )
    wb_b.refresh(now_ms=clk_b[0], record_daily=False)
    prep_b = wb_b.shadow_tracking(
        "prepare_deal",
        {"target_expiry_ms": exp_b, "slot_ms": slot_0, "allow_refresh": False, "input_profile": "p0_strict@1"},
    )
    src_b = dossier.collect(
        arch_b, exp_b, slot_0, clock=lambda: clk_b[0], candidate_round="r16",
        fallback_cases=prep_b["cases"],
        provenance_override="targeted workbench verified native case + Deribit public read",
        input_profile="p0_strict@1",
    )
    d_b = dossier.Dossiers(dos_b, clock=lambda: clk_b[0], require_reply_provenance=True)
    h_open_b = d_b.expect(exp_b, "AD_HOC", reference="concurrent-open-hand")
    d_b.deal(h_open_b, src_b, input_profile="p0_strict@1")

    deal_entered_llm = threading.Event()
    allow_deal_finish = threading.Event()
    events_timeline: list[str] = []

    with serving(wb_b) as wb_url_b:
        svc_b = post(wb_url_b, wb_b)
        res_ob = tracking._deal_hand(
            d_b,
            h_open_b,
            svc_b,
            tracking._llm_settings({
                "api_key": "k-conc",
                "model": "deepseek-v4-pro",
                "transport": lambda *_a, **_kw: {"choices": [{"finish_reason": "stop", "message": {"content": accept_put}}]},
            }),
            prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
            input_profile="p0_strict@1",
        )
        ep_open_b = h_open_b.parent.parent / "episodes" / res_ob["link"]["origin"]["dossier_episode_id"]

        clk_b[0] = slot_0 + 5 * M
        sync_b()
        wb_b.refresh(now_ms=clk_b[0], record_daily=False)
        d_b.event(
            ep_open_b,
            dossier.identity("chk_conc", clk_b[0] - M),
            "CONDITION_TRIGGERED",
            {"result": {"status": "triggered", "rule": "entry_not_returned"}},
            observed=clk_b[0] - M,
            available=clk_b[0],
        )

        def slow_blocking_deal_transport(*_a, **_kw):
            events_timeline.append("new_deal_llm_started")
            deal_entered_llm.set()
            assert allow_deal_finish.wait(timeout=10.0), "timed out waiting for concurrent sync_open"
            events_timeline.append("new_deal_llm_finished")
            return {"choices": [{"finish_reason": "stop", "message": {"content": accept_put}}]}

        deal_out_holder: list = []

        def run_auto_deal_thread():
            d_thread = dossier.Dossiers(dos_b, clock=lambda: clk_b[0], require_reply_provenance=True)
            deal_out_holder.extend(
                tracking.auto_deal(
                    d_thread,
                    plan_b,
                    arch_b,
                    svc_b,
                    llm_config={
                        "api_key": "k-conc",
                        "model": "deepseek-v4-pro",
                        "transport": slow_blocking_deal_transport,
                        "prompt_version": review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
                        "input_profile": "p0_strict@1",
                    },
                )
            )

        t_deal = threading.Thread(target=run_auto_deal_thread, daemon=True)
        t_deal.start()
        assert deal_entered_llm.wait(timeout=10.0)

        # While `auto_deal` is blocked mid-HTTP on `h_new`, `sync_open` completes `h_open_b`'s `REVOKE` -> `SHADOW_EXITED`!
        sync_rows_b = tracking.sync_open(
            d_b,
            svc_b,
            check_due=True,
            archive_root=arch_b,
            run_review=True,
            llm_config={
                "api_key": "k-conc",
                "model": "deepseek-v4-pro",
                "transport": lambda *_a, **_kw: (
                    events_timeline.append("open_hold_revoke_completed")
                    or {"choices": [{"finish_reason": "stop", "message": {"content": revoke_reply}}]}
                ),
            },
        )
        assert any((r.get("hold_review") or {}).get("verdict") == "REVOKE" for r in sync_rows_b)
        assert (h_open_b / "tracking" / "shadow_exit.json").is_file()
        allow_deal_finish.set()
        t_deal.join(timeout=10.0)

        assert events_timeline == [
            "new_deal_llm_started",
            "open_hold_revoke_completed",
            "new_deal_llm_finished",
        ]
        assert deal_out_holder[0]["hand_id"] == EXPECTED_HAND_ID
        assert deal_out_holder[0]["link"]["state"] == "SHADOW_LOCK_CREATED"


# ---------------------------------------------------------------------------
# (j) Final Unification Parity: Real CLI (`main` + subprocess) vs Direct `sync_open()`
#     Single Implementation, Shared Deadline Scope, & Batch Failure / Exit Codes
# ---------------------------------------------------------------------------

def test_cli_vs_direct_sync_open_parity_deadline_scope_and_exit_codes(tmp_path, monkeypatch, capsys):
    import ast
    import inspect
    import subprocess
    import sys

    # 1) Structural single-responsibility check: `main()` delegates to `sync_open` (no duplicate loop or module deadline global)
    assert not hasattr(tracking, "_ACTIVE_SERVICE_DEADLINE_MONO")
    assert not hasattr(tracking, "_prioritized_open_link_files")
    assert not hasattr(tracking, "_merge_sync_open_phases")
    main_tree = ast.parse(inspect.getsource(tracking.main))
    called_names = {
        (node.func.id if isinstance(node.func, ast.Name) else node.func.attr)
        for node in ast.walk(main_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
    }
    assert "sync_open" in called_names
    assert "recover_initial" not in called_names
    assert "_open_hand_sync_priority_key" not in called_names

    slot_0 = int(datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc).timestamp() * 1000)
    accept_put = _strict_p5_reply_text("## Accept / Put", short_strike=60_000, long_strike=58_000, require_outside_at_t0=True)
    revoke_reply = (
        '## REVOKE\n\n卖出短腿60,000已被跌破且确认触发M1，立即撤销。\n'
        '<HOLD_REVIEW_JSON>{"verdict":"REVOKE","affected_conditions":["M1"],"public_reply":"确认触发M1撤销","key_facts":["跌破60000"]}</HOLD_REVIEW_JSON>'
    )

    def invoke_mode(mode: str, *, wb_url: str, wb_obj, dos_root: Path, arch_root: Path, clk_box: list[int], check_due: bool = True, run_review: bool = True):
        if mode == "cli":
            spy_calls: list[int] = []
            orig_so = tracking.sync_open

            def spy_sync_open(*a, **kw):
                spy_calls.append(1)
                return orig_so(*a, **kw)

            argv = ["--url", wb_url, "sync-open", "--root", str(dos_root), "--archive", str(arch_root)]
            if check_due:
                argv.append("--check-due")
            if run_review:
                argv.append("--review")
            with monkeypatch.context() as mp_spy:
                mp_spy.setattr(tracking, "sync_open", spy_sync_open)
                rc, rows, err_txt = _run_tracking_cli(mp_spy, capsys, argv=argv, clock_box=clk_box)
            assert spy_calls == [1], "CLI `sync-open` must invoke the single `tracking.sync_open` implementation"
            batch_err = json.loads(err_txt.strip()) if err_txt.strip().startswith("{") else None
            return rc, rows, batch_err

        svc = post(wb_url, wb_obj)
        d_inst = dossier.Dossiers(dos_root, clock=lambda: clk_box[0], require_reply_provenance=True)
        rows = tracking.sync_open(
            d_inst,
            svc,
            check_due=check_due,
            archive_root=arch_root,
            run_review=run_review,
        )
        batch = tracking.classify_sync_open_batch(rows)
        rc = 0 if batch["batch_status"] == "ALL_OK" else 2
        return rc, rows, (None if rc == 0 else batch)

    # 2) Side-by-side `"cli"` vs `"direct"` parity across all 5 core invariants + shared `service_deadline_mono` scope
    parity_summary: dict[str, dict] = {}
    for mode in ("cli", "direct"):
        mode_dir = tmp_path / mode[:3]

        # (A) Frozen at 16:27Z, exits before first dispatch -> misses :32 -> wakes :35, :40, :45 -> dispatches ONCE
        clk_a = [slot_0 + 27 * M]
        wb_a, arch_a, dos_a, plan_a, slot_ms, exp_ms, _, sync_a = _setup_nov26_workbench_and_archive(
            mode_dir / "a", monkeypatch, clock=clk_a,
        )
        wb_a.refresh(now_ms=clk_a[0], record_daily=False)
        prep_a = wb_a.shadow_tracking(
            "prepare_deal",
            {"target_expiry_ms": exp_ms, "slot_ms": slot_ms, "allow_refresh": False, "input_profile": "p0_strict@1"},
        )
        src_a = dossier.collect(
            arch_a, exp_ms, slot_ms, clock=lambda: clk_a[0], candidate_round="r16",
            fallback_cases=prep_a["cases"],
            provenance_override="targeted workbench verified native case + Deribit public read",
            input_profile="p0_strict@1",
        )
        d_a = dossier.Dossiers(dos_a, clock=lambda: clk_a[0], require_reply_provenance=True)
        h_a, = d_a.tick(plan_a, slots=[slot_ms], input_profile="p0_strict@1")
        d_a.deal(h_a, src_a, input_profile="p0_strict@1")

        with serving(wb_a) as url_a, _sse_mock_server(
            lambda _i, _b: {"status": 200, "text": accept_put, "finish_reason": "stop"}
        ) as (ep_a, calls_a):
            monkeypatch.setenv("ASTRA_AI_SHADOW_LOCK_TOKEN", wb_a.config.shadow_service_token)
            monkeypatch.setenv("ASTRA_AI_SHADOW_EXPERIMENT", "1")
            monkeypatch.setenv("LLM_API_KEY", f"k-{mode}-a")
            monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro")
            monkeypatch.setenv("LLM_BASE_URL", ep_a)
            rcs_a = []
            for m_off in (35, 40, 45):
                clk_a[0] = slot_ms + m_off * M
                sync_a()
                wb_a.refresh(now_ms=clk_a[0], record_daily=False)
                rc_a, rows_a, _ = invoke_mode(mode, wb_url=url_a, wb_obj=wb_a, dos_root=dos_a, arch_root=arch_a, clk_box=clk_a)
                rcs_a.append(rc_a)
                if m_off == 35:
                    assert any((r.get("link") or {}).get("state") == "SHADOW_LOCK_CREATED" for r in rows_a)
            assert rcs_a == [0, 0, 0] and len(calls_a) == 1

        # (B) Sent credential (`dispatch.json`) with unknown receipt -> zero resend + `:35` exit 2 (`FAILED` / `REQUEST_FAILED`), `:40` exit 0
        clk_b = [slot_0 + 27 * M]
        wb_b, arch_b, dos_b, plan_b, _, exp_b, _, sync_b = _setup_nov26_workbench_and_archive(
            mode_dir / "b", monkeypatch, clock=clk_b,
        )
        wb_b.refresh(now_ms=clk_b[0], record_daily=False)
        prep_b = wb_b.shadow_tracking(
            "prepare_deal",
            {"target_expiry_ms": exp_b, "slot_ms": slot_ms, "allow_refresh": False, "input_profile": "p0_strict@1"},
        )
        src_b = dossier.collect(
            arch_b, exp_b, slot_ms, clock=lambda: clk_b[0], candidate_round="r16",
            fallback_cases=prep_b["cases"],
            provenance_override="targeted workbench verified native case + Deribit public read",
            input_profile="p0_strict@1",
        )
        d_b = dossier.Dossiers(dos_b, clock=lambda: clk_b[0], require_reply_provenance=True)
        h_b, = d_b.tick(plan_b, slots=[slot_ms], input_profile="p0_strict@1")
        d_b.deal(h_b, src_b, input_profile="p0_strict@1")
        att_b = dossier.identity("auto", h_b.name, clk_b[0], "deepseek-v4-pro", 0)
        dossier.publish(h_b / "api_calls" / att_b, {
            "dispatch.json": dossier.encode({"started_at_ms": clk_b[0], "requested_at_ms": clk_b[0], "model": "deepseek-v4-pro", "timeout_s": 600.0}),
        })
        with serving(wb_b) as url_b, _sse_mock_server(
            lambda *_a: pytest.fail("unknown receipt must never resend")
        ) as (ep_b, calls_b):
            monkeypatch.setenv("ASTRA_AI_SHADOW_LOCK_TOKEN", wb_b.config.shadow_service_token)
            monkeypatch.setenv("LLM_API_KEY", f"k-{mode}-b")
            monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro")
            monkeypatch.setenv("LLM_BASE_URL", ep_b)
            clk_b[0] = slot_ms + 35 * M
            sync_b()
            rc_b1, _, batch_b1 = invoke_mode(mode, wb_url=url_b, wb_obj=wb_b, dos_root=dos_b, arch_root=arch_b, clk_box=clk_b)
            clk_b[0] = slot_ms + 40 * M
            sync_b()
            rc_b2, rows_b2, _ = invoke_mode(mode, wb_url=url_b, wb_obj=wb_b, dos_root=dos_b, arch_root=arch_b, clk_box=clk_b)
            assert rc_b1 == 2 and batch_b1["batch_status"] == "FAILED"
            assert batch_b1["failed_hands"][0]["state"] == "REQUEST_FAILED"
            assert rc_b2 == 0 and rows_b2 == [] and len(calls_b) == 0

        # (C) Persisted valid `reply.json` -> 0 LLM calls, recovers `Accept` + `SHADOW_LOCK_CREATED` + active sync in 1 tick (`rc == 0`)
        clk_c = [slot_0 + 27 * M]
        wb_c, arch_c, dos_c, plan_c, _, exp_c, _, sync_c = _setup_nov26_workbench_and_archive(
            mode_dir / "c", monkeypatch, clock=clk_c,
        )
        wb_c.refresh(now_ms=clk_c[0], record_daily=False)
        prep_c = wb_c.shadow_tracking(
            "prepare_deal",
            {"target_expiry_ms": exp_c, "slot_ms": slot_ms, "allow_refresh": False, "input_profile": "p0_strict@1"},
        )
        src_c = dossier.collect(
            arch_c, exp_c, slot_ms, clock=lambda: clk_c[0], candidate_round="r16",
            fallback_cases=prep_c["cases"],
            provenance_override="targeted workbench verified native case + Deribit public read",
            input_profile="p0_strict@1",
        )
        d_c = dossier.Dossiers(dos_c, clock=lambda: clk_c[0], require_reply_provenance=True)
        h_c, = d_c.tick(plan_c, slots=[slot_ms], input_profile="p0_strict@1")
        d_c.deal(h_c, src_c, input_profile="p0_strict@1")
        meta_c = d_c.verify_evidence(h_c)
        att_c = dossier.identity("auto", h_c.name, clk_c[0], "deepseek-v4-pro", 0)
        req_c = review.build_initial_request_payload(
            (h_c / "evidence" / "input.md").read_text(encoding="utf-8"),
            model="deepseek-v4-pro",
            prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
            input_profile="p0_strict@1",
            packet_hash=meta_c["input_sha256"],
            requested_at_ms=clk_c[0],
            fidelity="EXACT",
        )
        dossier.publish(h_c / "api_calls" / att_c, {
            "request.json": dossier.encode(review.sanitize_model_request(req_c)),
            "dispatch.json": dossier.encode({"started_at_ms": clk_c[0], "requested_at_ms": clk_c[0], "model": "deepseek-v4-pro", "timeout_s": 600.0}),
            "reply.json": dossier.encode({"text": accept_put, "model": "deepseek-v4-pro", "http_status": 200, "decided_at_ms": clk_c[0] + 5_000}),
        })
        with serving(wb_c) as url_c, _sse_mock_server(
            lambda *_a: pytest.fail("persisted valid receipt must never resend")
        ) as (ep_c, calls_c):
            monkeypatch.setenv("ASTRA_AI_SHADOW_LOCK_TOKEN", wb_c.config.shadow_service_token)
            monkeypatch.setenv("LLM_API_KEY", f"k-{mode}-c")
            monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro")
            monkeypatch.setenv("LLM_BASE_URL", ep_c)
            clk_c[0] = slot_ms + 35 * M
            sync_c()
            wb_c.refresh(now_ms=clk_c[0], record_daily=False)
            rc_c, rows_c, batch_c = invoke_mode(mode, wb_url=url_c, wb_obj=wb_c, dos_root=dos_c, arch_root=arch_c, clk_box=clk_c)
            assert rc_c == 0 and len(calls_c) == 0, f"mode={mode} rc={rc_c} rows={rows_c!r} batch={batch_c!r}"
            assert any((r.get("link") or {}).get("state") == "SHADOW_LOCK_CREATED" for r in rows_c)
            assert any(r.get("state") == "active" for r in rows_c)

        # (D) Expired (`now >= reply_deadline`) -> 0 dispatches, `REPLY_WINDOW_CLOSED`, `DISPATCH_EXPIRED`
        clk_d = [slot_0 + 2 * M]
        wb_d, arch_d, dos_d, plan_d, _, exp_d, _, sync_d = _setup_nov26_workbench_and_archive(
            mode_dir / "d", monkeypatch, clock=clk_d,
        )
        wb_d.refresh(now_ms=clk_d[0], record_daily=False)
        prep_d = wb_d.shadow_tracking(
            "prepare_deal",
            {"target_expiry_ms": exp_d, "slot_ms": slot_ms, "allow_refresh": False, "input_profile": "p0_strict@1"},
        )
        src_d = dossier.collect(
            arch_d, exp_d, slot_ms, clock=lambda: clk_d[0], candidate_round="r16",
            fallback_cases=prep_d["cases"],
            provenance_override="targeted workbench verified native case + Deribit public read",
            input_profile="p0_strict@1",
        )
        d_d = dossier.Dossiers(dos_d, clock=lambda: clk_d[0], require_reply_provenance=True)
        h_d, = d_d.tick(plan_d, slots=[slot_ms], input_profile="p0_strict@1")
        d_d.deal(h_d, src_d, input_profile="p0_strict@1")
        with serving(wb_d) as url_d, _sse_mock_server(
            lambda *_a: pytest.fail("expired hand must never dispatch")
        ) as (ep_d, calls_d):
            monkeypatch.setenv("ASTRA_AI_SHADOW_LOCK_TOKEN", wb_d.config.shadow_service_token)
            monkeypatch.setenv("LLM_API_KEY", f"k-{mode}-d")
            monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro")
            monkeypatch.setenv("LLM_BASE_URL", ep_d)
            clk_d[0] = slot_ms + 35 * M  # >= 16:32Z reply_deadline
            sync_d()
            rc_d, rows_d, _ = invoke_mode(mode, wb_url=url_d, wb_obj=wb_d, dos_root=dos_d, arch_root=arch_d, clk_box=clk_d)
            assert rc_d == 0 and rows_d == [] and len(calls_d) == 0
            assert tracking.audit_record(h_d, clk_d[0], {})["stage"] == "DISPATCH_EXPIRED"

        # (E) Hold review precedes slow new-hand recovery AND shares the single `service_deadline_mono` scope (`520s - 120s - 20s = 380s`)
        clk_e = [slot_0 + 2 * M]
        wb_e, arch_e, dos_e, plan_e, _, exp_e, _, sync_e = _setup_nov26_workbench_and_archive(
            mode_dir / "e", monkeypatch, clock=clk_e,
        )
        wb_e.refresh(now_ms=clk_e[0], record_daily=False)
        prep_e = wb_e.shadow_tracking(
            "prepare_deal",
            {"target_expiry_ms": exp_e, "slot_ms": slot_ms, "allow_refresh": False, "input_profile": "p0_strict@1"},
        )
        src_e = dossier.collect(
            arch_e, exp_e, slot_ms, clock=lambda: clk_e[0], candidate_round="r16",
            fallback_cases=prep_e["cases"],
            provenance_override="targeted workbench verified native case + Deribit public read",
            input_profile="p0_strict@1",
        )
        d_e = dossier.Dossiers(dos_e, clock=lambda: clk_e[0], require_reply_provenance=True)
        h_open_e = d_e.expect(exp_e, "AD_HOC", reference=f"open-{mode}")
        d_e.deal(h_open_e, src_e, input_profile="p0_strict@1")
        with serving(wb_e) as url_e:
            svc_e = post(url_e, wb_e)
            init_open = tracking._deal_hand(
                d_e,
                h_open_e,
                svc_e,
                tracking._llm_settings({
                    "api_key": f"k-{mode}-e",
                    "model": "deepseek-v4-pro",
                    "transport": lambda *_a, **_kw: {"choices": [{"finish_reason": "stop", "message": {"content": accept_put}}]},
                }),
                prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
                input_profile="p0_strict@1",
            )
            ep_open_e = h_open_e.parent.parent / "episodes" / init_open["link"]["origin"]["dossier_episode_id"]

            clk_e[0] = slot_ms + 27 * M
            sync_e()
            wb_e.refresh(now_ms=clk_e[0], record_daily=False)
            prep_e27 = wb_e.shadow_tracking(
                "prepare_deal",
                {"target_expiry_ms": exp_e, "slot_ms": slot_ms, "allow_refresh": False, "input_profile": "p0_strict@1"},
            )
            src_e27 = dossier.collect(
                arch_e, exp_e, slot_ms, clock=lambda: clk_e[0], candidate_round="r16",
                fallback_cases=prep_e27["cases"],
                provenance_override="targeted workbench verified native case + Deribit public read",
                input_profile="p0_strict@1",
            )
            h_new_e, = d_e.tick(plan_e, slots=[slot_ms], input_profile="p0_strict@1")
            d_e.deal(h_new_e, src_e27, input_profile="p0_strict@1")

            clk_e[0] = slot_ms + 40 * M
            sync_e()
            wb_e.refresh(now_ms=clk_e[0], record_daily=False)
            d_e.event(
                ep_open_e,
                dossier.identity("chk_e", clk_e[0] - M),
                "CONDITION_TRIGGERED",
                {"result": {"status": "triggered", "rule": "entry_not_returned"}},
                observed=clk_e[0] - M,
                available=clk_e[0],
            )

            mono_box = [10_000.0]
            seen_calls_e: list[tuple[str, float]] = []
            orig_call_llm = review.call_chat_llm

            def spy_call_chat_llm(messages, **kwargs):
                sys_txt = str((messages[0] if messages else {}).get("content") or "")
                kind_lbl = "hold_review" if ("HOLD_REVIEW_JSON" in sys_txt or "ANNOTATION_JSON" not in sys_txt) else "initial_deal"
                seen_calls_e.append((kind_lbl, round(float(kwargs["timeout_s"]), 1)))
                res = orig_call_llm(messages, **kwargs)
                if kind_lbl == "hold_review":
                    mono_box[0] += 120.0  # Simulate 120s spent inside Phase 3 hold review
                return res

            def sse_e(_idx: int, body: dict) -> dict:
                sys_txt = str(((body.get("messages") or [{}])[0]).get("content") or "")
                if "HOLD_REVIEW_JSON" in sys_txt or "ANNOTATION_JSON" not in sys_txt:
                    return {"status": 200, "text": revoke_reply, "finish_reason": "stop"}
                clk_e[0] = slot_ms + 44 * M
                sync_e()
                wb_e.refresh(now_ms=clk_e[0], record_daily=False)
                return {"status": 200, "text": accept_put, "finish_reason": "stop"}

            with _sse_mock_server(sse_e) as (ep_e_url, _), monkeypatch.context() as mp_e:
                mp_e.setenv("ASTRA_AI_SHADOW_LOCK_TOKEN", wb_e.config.shadow_service_token)
                mp_e.setenv("ASTRA_PLAYER_SERVICE_BUDGET_SEC", "520")
                mp_e.setenv("LLM_API_KEY", f"k-{mode}-e")
                mp_e.setenv("LLM_MODEL", "deepseek-v4-pro")
                mp_e.setenv("LLM_BASE_URL", ep_e_url)
                mp_e.setattr(tracking.time, "monotonic", lambda: mono_box[0])
                mp_e.setattr(review, "call_chat_llm", spy_call_chat_llm)
                rc_e, _, _ = invoke_mode(mode, wb_url=url_e, wb_obj=wb_e, dos_root=dos_e, arch_root=arch_e, clk_box=clk_e)
                assert rc_e == 0

            # Hold review ran FIRST (`min(600, 520 - 20) = 500.0s`), consumed 120s of the 520s scope, so Phase 4 got `520 - 120 - 20 = 380.0s`!
            assert seen_calls_e == [("hold_review", 500.0), ("initial_deal", 380.0)]
            assert (h_open_e / "tracking" / "shadow_exit.json").is_file()
            assert (h_new_e / "tracking" / "link.json").is_file()
            parity_summary[mode] = {
                "rcs_a": rcs_a,
                "rc_b": (rc_b1, rc_b2, batch_b1["batch_status"], batch_b1["failed_hands"][0]["state"]),
                "rc_c": rc_c,
                "rc_d": rc_d,
                "seen_calls_e": seen_calls_e,
            }

    assert parity_summary["cli"] == parity_summary["direct"]

    # 3) Batch failure classification & exit-code matrix (`DEGRADED` vs `FAILED` vs `ALL_OK`) including real OS subprocess CLI!
    clk_f = [slot_0 + 2 * M]
    wb_f, arch_f, dos_f, _, _, exp_f, _, sync_f = _setup_nov26_workbench_and_archive(
        tmp_path / "f", monkeypatch, clock=clk_f,
    )
    wb_f.refresh(now_ms=clk_f[0], record_daily=False)
    prep_f = wb_f.shadow_tracking(
        "prepare_deal",
        {"target_expiry_ms": exp_f, "slot_ms": slot_0, "allow_refresh": False, "input_profile": "p0_strict@1"},
    )
    src_f = dossier.collect(
        arch_f, exp_f, slot_0, clock=lambda: clk_f[0], candidate_round="r16",
        fallback_cases=prep_f["cases"],
        provenance_override="targeted workbench verified native case + Deribit public read",
        input_profile="p0_strict@1",
    )
    d_f = dossier.Dossiers(dos_f, clock=lambda: clk_f[0], require_reply_provenance=True)
    h_ok = d_f.expect(exp_f, "AD_HOC", reference="exit-ok-hand")
    d_f.deal(h_ok, src_f, input_profile="p0_strict@1")
    h_bad = d_f.expect(exp_f, "AD_HOC", reference="exit-corrupt-terminal-hand")
    d_f.deal(h_bad, src_f, input_profile="p0_strict@1")

    accept_call = _strict_p5_reply_text("## Accept / Call", short_strike=61_000, long_strike=63_000, require_outside_at_t0=True)
    with serving(wb_f) as url_f:
        svc_f = post(url_f, wb_f)
        for h_item, reply_txt in ((h_ok, accept_put), (h_bad, accept_call)):
            r_init = tracking._deal_hand(
                d_f,
                h_item,
                svc_f,
                tracking._llm_settings({
                    "api_key": "k-f",
                    "model": "deepseek-v4-pro",
                    "transport": lambda *_a, _t=reply_txt, **_kw: {"choices": [{"finish_reason": "stop", "message": {"content": _t}}]},
                }),
                prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
                input_profile="p0_strict@1",
            )
            assert r_init["link"]["state"] == "SHADOW_LOCK_CREATED"

        # ALL_OK via real OS subprocess (`exit 0`, empty stderr)
        script_path = Path(tracking.__file__).resolve()
        sub_env = {**dict(__import__("os").environ), "ASTRA_AI_SHADOW_LOCK_TOKEN": wb_f.config.shadow_service_token}
        proc_ok = subprocess.run(
            [sys.executable, str(script_path), "--url", url_f, "sync-open", "--root", str(dos_f)],
            env=sub_env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert proc_ok.returncode == 0, f"stderr={proc_ok.stderr} stdout={proc_ok.stdout}"
        assert proc_ok.stderr == ""

        # Inject corrupt `terminal.json` pointing to missing export manifest on `h_bad` -> `DEGRADED` (`ok_count=1, failed_count=1, exit 2`)
        dossier.write(h_bad / "tracking" / "terminal.json", dossier.encode({"export_id": "track_missing_000000", "full_series": True}), replace=True)
        proc_deg = subprocess.run(
            [sys.executable, str(script_path), "--url", url_f, "sync-open", "--root", str(dos_f)],
            env=sub_env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert proc_deg.returncode == 2
        deg_batch = json.loads(proc_deg.stderr.strip())
        assert deg_batch["batch_status"] == "DEGRADED"
        assert deg_batch["ok_count"] == 1 and deg_batch["failed_count"] == 1
        assert deg_batch["failed_hands"][0]["hand_id"] == h_bad.name
        assert deg_batch["failed_hands"][0]["state"] == "TRACKER_EXPORT_UNAVAILABLE"

        # Compare against direct `tracking.sync_open` on the exact same dossier state
        direct_deg_rows = tracking.sync_open(dossier.Dossiers(dos_f), svc_f)
        assert tracking.classify_sync_open_batch(direct_deg_rows) == deg_batch

        # Remove healthy `h_ok` link so only failing `h_bad` remains -> `FAILED` (`ok_count=0, failed_count=1, exit 2`)
        (h_ok / "tracking" / "link.json").unlink()
        proc_fail = subprocess.run(
            [sys.executable, str(script_path), "--url", url_f, "sync-open", "--root", str(dos_f)],
            env=sub_env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert proc_fail.returncode == 2
        fail_batch = json.loads(proc_fail.stderr.strip())
        assert fail_batch["batch_status"] == "FAILED"
        assert fail_batch["ok_count"] == 0 and fail_batch["failed_count"] == 1
        assert tracking.classify_sync_open_batch(tracking.sync_open(dossier.Dossiers(dos_f), svc_f)) == fail_batch


