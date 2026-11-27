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
        assert any("j1_labeled_section_clause_prefix_expanded:put" in w for w in cv_loss["warnings"])
        assert any("j1_labeled_section_clause_prefix_expanded:condition_plan.confirmation_excerpt" in w for w in cv_loss["warnings"])
        assert any("annotation_closing_tag_repaired" in w for w in cv_loss["warnings"])
        assert any("annotation_json_trailing_comma_repaired" in w for w in cv_loss["warnings"])

        # 2) Valid Accept prose + wrong-section / duplicate `confirmation_excerpt` pointer (`hand_89df` pattern)
        #    -> Attempt 1 fails closed (`CONTRACT_VALIDATION_FAILED`, `recoverable_citation_only=True`),
        #       regenerates ONCE on primary model (never switching to standby!), Attempt 2 locks cleanly!
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
        h_89df = d_89df.expect(exp_89df, "AD_HOC", reference="item2-89df-wrong-section-regen")
        d_89df.deal(h_89df, src_89df, input_profile="p0_strict@1")
        models_89df: list[str] = []

        def transport_89df(_k, model, *_a, **_kw):
            models_89df.append(model)
            txt = text_89df_pointer_bug if len(models_89df) == 1 else clean_accept
            return {"choices": [{"finish_reason": "stop", "message": {"content": txt}}]}

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
        assert r_89df_1["status"] == "PARSE_FAILED"
        att_89df_1 = dossier.read(dossier.attempts(h_89df)[0])
        assert att_89df_1["error_code"] == "CONTRACT_VALIDATION_FAILED"
        assert att_89df_1["contract_validation"]["recoverable_citation_only"] is True
        clk_89df[0] += 5_000
        sync_89df()
        wb_89df.refresh(now_ms=clk_89df[0], record_daily=False)
        r_89df_2 = tracking._deal_hand(
            d_89df, h_89df, svc_89df, cfg_89df,
            prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION, input_profile="p0_strict@1",
        )
        assert r_89df_2["status"] == "VALID" and r_89df_2["decision"] == "Accept"
        assert r_89df_2["link"]["state"] == "SHADOW_LOCK_CREATED"
        assert models_89df == ["deepseek-v4-pro", "deepseek-v4-pro"]  # stayed on primary model!

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
