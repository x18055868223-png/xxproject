"""Tests for isolated repeatable manual AD_HOC shadow-trial CLI (tools/astra_player_manual_trial_v1.py)."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
TESTS = ROOT / "tests"
for _p in (str(ROOT), str(TOOLS), str(TESTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools import astra_player_dossier_v1 as dossier  # noqa: E402
from tools import astra_player_manual_trial_v1 as manual_trial  # noqa: E402
from tools import signal_llm_review  # noqa: E402
from tests import test_player_e2e_robustness_matrix as p5_e2e  # noqa: E402
from test_astra_player_tracking_v13 import post, serving  # noqa: E402


def _setup_manual_trial_env(
    base_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    reply_text: str | None = None,
    llm_calls_log: list[dict] | None = None,
):
    slot_0, _exp_ms, _spot, _rows, _cd = p5_e2e._nov26_27nov26_chain()
    clk = [slot_0 + 2 * dossier.MINUTE]
    wb, arch, _dos, plan, _sm, exp_ms, _wc, _sync_fn = p5_e2e._setup_nov26_workbench_and_archive(
        base_dir, monkeypatch, clock=clk
    )
    monkeypatch.setattr(wb.config, "shadow_service_token", "fixture-shadow-lock-token")
    trial_root = base_dir / "isolated_trial"
    (trial_root / "shadow_dossiers").mkdir(parents=True, exist_ok=True)
    (trial_root / "exec_state").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(wb.config, "shadow_dossier_root", trial_root / "shadow_dossiers")
    monkeypatch.setenv("ASTRA_AI_SHADOW_LOCK_TOKEN", "fixture-shadow-lock-token")

    plan_path = base_dir / "plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    archive_view = trial_root / "archive_view"
    manual_trial._prepare_archive_readonly_view(arch, archive_view, service_identities=False)

    if reply_text is None:
        reply_text = p5_e2e._strict_p5_reply_text(
            "## Accept / Put",
            short_strike=60_000,
            long_strike=58_000,
            require_outside_at_t0=True,
        )

    ledger_path = base_dir / "usage_ledger.json"

    def _mock_llm(_idx, _body):
        if llm_calls_log is not None:
            llm_calls_log.append({"idx": _idx})
        return {"status": 200, "text": reply_text, "finish_reason": "stop"}

    return {
        "wb": wb,
        "arch": arch,
        "archive_view": archive_view,
        "trial_root": trial_root,
        "plan_path": plan_path,
        "clk": clk,
        "exp_ms": exp_ms,
        "ledger_path": ledger_path,
        "mock_llm": _mock_llm,
    }


def test_manual_trial_accept_locks_and_hands_off_to_shadow_coordinator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _setup_manual_trial_env(tmp_path, monkeypatch)
    budget = signal_llm_review.DailyHttpBudget(fx["ledger_path"], limit=20)
    with serving(fx["wb"]) as wb_url, p5_e2e._sse_mock_server(fx["mock_llm"]) as (llm_ep, llm_calls):
        svc = post(wb_url, fx["wb"])
        receipt = manual_trial.run_deal_worker(
            tools_dir=TOOLS,
            trial_root=fx["trial_root"],
            plan_path=fx["plan_path"],
            archive_view=fx["archive_view"],
            workbench_url=wb_url,
            reference="manual_shadow_trial_unit_accept",
            preflight_only=False,
            service=svc,
            llm_config={
                "api_key": "SECRET_TEST_API_KEY_NEVER_LEAK",
                "model": "deepseek-v4-pro",
                "endpoint": llm_ep,
                "reasoning_effort": "high",
                "budget": budget,
            },
            clock=lambda: fx["clk"][0],
        )
        assert receipt.get("stage") == "deal_completed", f"stage={receipt.get('stage')} block={receipt.get('block_state')} reason={receipt.get('reason')}"
        assert len(llm_calls) == 1

    assert receipt["ok"] is True
    assert receipt["sample"] == "AD_HOC"
    assert receipt["scheduled_at_ms"] is None
    assert receipt["llm_called"] is True
    assert receipt["decision_action"] == "Accept"
    assert receipt["decision_side"] == "put"
    assert receipt["link"]["state"] == "SHADOW_LOCK_CREATED"
    assert receipt["link"]["execution_allowed"] is False

    # Sanitization: never expose secrets, prompt rules/board, or raw model reply
    dumped = json.dumps(receipt, ensure_ascii=False)
    assert "SECRET_TEST_API_KEY_NEVER_LEAK" not in dumped
    assert "fixture-shadow-lock-token" not in dumped
    assert "本局牌面" not in dumped
    assert "## 怎么读" not in dumped
    assert "冻结历史事实" not in dumped

    # Real ExecutionCoordinator(SHADOW_ONLY) verifies handoff & stays non-writing
    exec_check = manual_trial.run_exec_verify_worker(
        tools_dir=TOOLS,
        trial_root=fx["trial_root"],
        dossier_id=receipt["dossier_id"],
        hand_id=receipt["hand_id"],
        p5_episode_id=receipt["link"]["dossier_episode_id"],
        expect_write_denied=False,
    )
    assert exec_check["ok"] is True
    assert "tracking/link.json" in exec_check["direct_reads"]
    assert f"episodes/{receipt['link']['dossier_episode_id']}/condition.json" in exec_check["direct_reads"]
    assert exec_check["handoff_contract"]["hand_id"] == receipt["hand_id"]
    assert exec_check["handoff_contract"]["decision"] == "Accept"
    assert exec_check["handoff_contract"]["facts_readable"] is True
    assert exec_check["coordinator_status"]["mode"] == "SHADOW_ONLY"
    assert exec_check["coordinator_status"]["writes_enabled"] is False
    assert exec_check["coordinator_status"]["active_episode"] is None


def test_manual_trial_reject_not_ready_and_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1) Reject path: zero lock attempt, honest Reject, 1 LLM call
    rej_text = p5_e2e._strict_p5_reply_text("## Reject")
    fx_rej = _setup_manual_trial_env(tmp_path / "rej", monkeypatch, reply_text=rej_text)
    budget_rej = signal_llm_review.DailyHttpBudget(fx_rej["ledger_path"], limit=20)
    with serving(fx_rej["wb"]) as wb_url, p5_e2e._sse_mock_server(fx_rej["mock_llm"]) as (llm_ep, llm_calls):
        svc = post(wb_url, fx_rej["wb"])
        rej = manual_trial.run_deal_worker(
            tools_dir=TOOLS,
            trial_root=fx_rej["trial_root"],
            plan_path=fx_rej["plan_path"],
            archive_view=fx_rej["archive_view"],
            workbench_url=wb_url,
            reference="manual_trial_reject",
            service=svc,
            llm_config={
                "api_key": "k-rej",
                "model": "deepseek-v4-pro",
                "endpoint": llm_ep,
                "budget": budget_rej,
            },
            clock=lambda: fx_rej["clk"][0],
        )
        assert rej.get("stage") == "deal_completed", f"stage={rej.get('stage')} block={rej.get('block_state')} reason={rej.get('reason')}"
        assert len(llm_calls) == 1
    assert rej["ok"] is True
    assert rej["decision_action"] == "Reject"
    assert rej["link"] is None
    assert not fx_rej["wb"].locked_candidates.list()

    # 2) Not-ready path: prepare_deal returns MARKET_SINGLE_SIDE_ONLY -> 0 LLM call
    fx_nr = _setup_manual_trial_env(tmp_path / "nr", monkeypatch)
    nr = manual_trial.run_deal_worker(
        tools_dir=TOOLS,
        trial_root=fx_nr["trial_root"],
        plan_path=fx_nr["plan_path"],
        archive_view=fx_nr["archive_view"],
        workbench_url="http://127.0.0.1:18895",
        reference="manual_trial_not_ready",
        service=lambda _action, _body: {
            "enabled": True,
            "ready_for_deal": False,
            "readiness_status": "MARKET_SINGLE_SIDE_ONLY",
            "blockers": ["put_market_candidate_absent"],
            "target_expiry_ms": fx_nr["exp_ms"],
        },
        clock=lambda: fx_nr["clk"][0],
    )
    assert nr["ok"] is False
    assert nr["stage"] == "prepare_deal_blocked"
    assert nr["llm_called"] is False
    assert nr["readiness_status"] == "MARKET_SINGLE_SIDE_ONLY"
    assert not fx_nr["ledger_path"].exists()

    # 3) Preflight-only: freezes evidence + runs exec_verify with 0 LLM calls
    fx_pf = _setup_manual_trial_env(tmp_path / "pf", monkeypatch)
    with serving(fx_pf["wb"]) as wb_url:
        svc = post(wb_url, fx_pf["wb"])
        pf = manual_trial.run_deal_worker(
            tools_dir=TOOLS,
            trial_root=fx_pf["trial_root"],
            plan_path=fx_pf["plan_path"],
            archive_view=fx_pf["archive_view"],
            workbench_url=wb_url,
            reference="manual_trial_preflight",
            preflight_only=True,
            service=svc,
            clock=lambda: fx_pf["clk"][0],
        )
    assert pf["ok"] is True
    assert pf["stage"] == "preflight_frozen"
    assert pf["llm_called"] is False
    assert not fx_pf["ledger_path"].exists()
    pf_exec = manual_trial.run_exec_verify_worker(
        tools_dir=TOOLS,
        trial_root=fx_pf["trial_root"],
        dossier_id=pf["dossier_id"],
        hand_id=pf["hand_id"],
    )
    assert pf_exec["ok"] is True
    assert "expected.json" in pf_exec["direct_reads"]
    assert "evidence/identity.json" in pf_exec["direct_reads"]


def test_manual_trial_hand4924_wrong_section_locks_in_one_call_and_projection_hardening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1) Reconstructed hand_4924 wrong-section confirmation_excerpt + prose-only minimal JSON -> 1 LLM call -> SHADOW_LOCK_CREATED
    clean_accept = p5_e2e._strict_p5_reply_text(
        "## Accept / Put",
        short_strike=60_000,
        long_strike=58_000,
        require_outside_at_t0=True,
    )
    prose, json_part = clean_accept.split("<ANNOTATION_JSON>")
    ann = json.loads(json_part.split("</ANNOTATION_JSON>")[0].strip())
    ann["condition_plan"]["confirmation_excerpt"] = ann["comparison"]
    wrong_sec_reply = prose + "<ANNOTATION_JSON>\n" + json.dumps(ann, ensure_ascii=False) + "\n</ANNOTATION_JSON>"

    calls_4924: list[dict] = []
    fx = _setup_manual_trial_env(tmp_path / "h4924", monkeypatch, reply_text=wrong_sec_reply, llm_calls_log=calls_4924)
    with serving(fx["wb"]) as wb_url, p5_e2e._sse_mock_server(fx["mock_llm"]) as (llm_ep, _):
        svc = post(wb_url, fx["wb"])
        rec = manual_trial.run_deal_worker(
            tools_dir=TOOLS,
            trial_root=fx["trial_root"],
            plan_path=fx["plan_path"],
            archive_view=fx["archive_view"],
            workbench_url=wb_url,
            reference="manual_trial_4924_in_place",
            service=svc,
            llm_config={
                "api_key": "k",
                "model": "deepseek-v4-pro",
                "endpoint": llm_ep,
                "budget": signal_llm_review.DailyHttpBudget(fx["ledger_path"], limit=20),
            },
            clock=lambda: fx["clk"][0],
        )
    assert len(calls_4924) == 1
    assert rec["ok"] is True
    assert rec["decision_action"] == "Accept"
    assert rec["link"]["state"] == "SHADOW_LOCK_CREATED"
    exec_chk = manual_trial.run_exec_verify_worker(
        tools_dir=TOOLS,
        trial_root=fx["trial_root"],
        dossier_id=rec["dossier_id"],
        hand_id=rec["hand_id"],
        p5_episode_id=rec["link"]["dossier_episode_id"],
    )
    assert exec_chk["ok"] is True
    assert exec_chk["handoff_contract"]["decision"] == "Accept"
    assert exec_chk["coordinator_status"]["mode"] == "SHADOW_ONLY"

    # 2) Read-only cooldown inheritance in astra_deribit_cooldown_v1
    from tools import astra_deribit_cooldown_v1 as cd_mod

    primary_cd = tmp_path / "isolated_cd.json"
    ro_cd = tmp_path / "prod_ro_cd.json"
    now_ms = int(fx["clk"][0])
    cd_mod.record_cooldown(ro_cd, cooldown_until_ms=now_ms + 15_000, source="prod", now_ms=now_ms)
    monkeypatch.setenv(cd_mod.ENV_KEY, str(primary_cd))
    monkeypatch.setenv(cd_mod.READONLY_ENV_KEY, str(ro_cd))
    active = cd_mod.read_cooldown(now_ms=now_ms)
    assert active is not None and active["remaining_ms"] == 15_000
    assert not primary_cd.exists()
    monkeypatch.delenv(cd_mod.ENV_KEY, raising=False)
    monkeypatch.delenv(cd_mod.READONLY_ENV_KEY, raising=False)

    # 3) _player_audit survives LockedCandidateError; audit_record prefers PENDING_LOCK & tolerates corrupt event
    from tools import astra_player_tracking_v13 as tr_mod
    from tools import astra_workbench_v20 as wb_mod
    import urllib.request

    hand_dir = fx["trial_root"] / "shadow_dossiers" / "dossiers" / rec["dossier_id"] / "hands" / rec["hand_id"]
    ep_id = rec["link"]["dossier_episode_id"]
    ev_dir = hand_dir.parent.parent / "episodes" / ep_id / "events"
    ev_dir.mkdir(parents=True, exist_ok=True)
    (ev_dir / "corrupt.json").write_text("{bad", encoding="utf-8")
    aud_detail = tr_mod.audit_record(hand_dir, fx["clk"][0], {ep_id: "ACTIVE"}, detail=True)
    assert aud_detail["state"] == "Accept"

    # Also verify active PENDING_LOCK takes precedence over earlier non-terminal LOCK_BLOCKED note
    from tools import astra_player_dossier_v1 as dos_mod
    pending_hand = dos_mod.Dossiers(fx["trial_root"] / "pending_precedence", clock=lambda: fx["clk"][0])
    h_pend = pending_hand.expect(fx["exp_ms"], "AD_HOC", reference="pend-precedence")
    (h_pend / "tracking").mkdir(parents=True, exist_ok=True)
    (h_pend / "issuance").mkdir(parents=True, exist_ok=True)
    dos_mod.write(h_pend / "issuance" / "lock_note.json", dos_mod.encode({"state": "LOCK_BLOCKED", "recorded_at_ms": fx["clk"][0] - 5000}))
    dos_mod.write(h_pend / "tracking" / "pending_lock.json", dos_mod.encode({"state": "PENDING_LOCK", "deadline_ms": fx["clk"][0] + 60_000}))
    aud_pend = tr_mod.audit_record(h_pend, fx["clk"][0], {})
    assert aud_pend["tracking_state"] == "PENDING_LOCK"

    def raise_locked_err():
        raise wb_mod.locked_store.LockedCandidateError("corrupt_index", "corrupt")

    monkeypatch.setattr(fx["wb"].locked_candidates, "list", raise_locked_err)
    with serving(fx["wb"]) as wb_url:
        req = urllib.request.Request(
            f"{wb_url}/api/workbench/player-audit",
            headers={"Authorization": f"Bearer {fx['wb'].config.server_token}"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            body = json.loads(resp.read().decode("utf-8"))
            assert any(h["hand_id"] == rec["hand_id"] for h in body["hands"])

    # 4) _prepare_shadow_deal avoids redundant refresh() when refresh_expiry_gamma already unblocks SOURCE_READY
    fx_nd = _setup_manual_trial_env(tmp_path / "nodup", monkeypatch)
    fx_nd["wb"].refresh(now_ms=fx_nd["clk"][0], record_daily=False)
    calls_refresh: list[int] = []
    orig_eg = fx_nd["wb"]._expiry_gamma
    monkeypatch.setattr(fx_nd["wb"], "refresh", lambda **_kw: calls_refresh.append(1) or {})
    monkeypatch.setattr(fx_nd["wb"].config, "expiry_gamma_enabled", True)
    monkeypatch.setattr(fx_nd["wb"], "refresh_expiry_gamma", lambda **_kw: setattr(fx_nd["wb"], "_expiry_gamma", orig_eg) or orig_eg)
    fx_nd["wb"]._expiry_gamma = None
    prep_no_dup = fx_nd["wb"].shadow_tracking(
        "prepare_deal",
        {"target_expiry_ms": fx_nd["exp_ms"], "allow_refresh": True, "input_profile": "p0_strict@1"},
    )
    assert prep_no_dup["readiness_status"] == "SOURCE_READY"
    assert calls_refresh == []
