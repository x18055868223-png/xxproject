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
