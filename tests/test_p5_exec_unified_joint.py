"""Unified P5 + Execution End-to-End Joint Verification Suite.

Drives authentic P5 runtime entrypoints (`tracking.prewarm`, `tracking.auto_deal`,
`tracking.recover_initial(..., allow_post_window_retry=True)`, `tracking.sync_open(..., run_review=True)`,
`tracking.main(['sync-open', ...])`, `d.event`, `d.settle_due`) directly into `ExecutionCoordinator`
(`scan_p5_dossier_root`, `read_hand_p5_state`, `check_new_exposure_permission`, `run_tick`,
`ExecutionStore`, `SimExchangeAdapter`, `IndependentSettlementOracle`).

Also verifies all three formal merge landings from `P5接手包.md`:
  1. Single `sync_open()` CLI/direct parity with `--exec-status-path` & `--live-budget-path`;
  2. Bad-dossier sorting fault isolation in `_open_hand_sync_priority_key` (`DEGRADED`, exit code 2,
     while healthy open hands still complete hold review and emit `AI_REVOKE`);
  3. Zero-short-liability residual hold (`SHORT_LIABILITY_CLEARED` / `INVENTORY_CLEARED` /
     `short_pos=0, prot_pos>0` / `stale` / `SHADOW_ONLY`) never consumes live hold-review quota,
     while active `TP_MANAGED` (`short_pos>0`) routes cleanly to the isolated 60/day live ledger.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "tests"), str(ROOT / "tools"), str(ROOT / "deploy" / "xxproject_server")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools import astra_exec_contracts_v1 as contracts  # noqa: E402
from tools import astra_exec_policy_v1 as policy  # noqa: E402
from tools import astra_exec_store_v1 as store  # noqa: E402
from tools import astra_execution_coordinator_v1 as coord_mod  # noqa: E402
from tools import astra_player_dossier_v1 as dossier  # noqa: E402
from tools import astra_player_review_v1 as review  # noqa: E402
from tools import astra_player_tracking_v13 as tracking  # noqa: E402
from tools import signal_llm_review  # noqa: E402
from tests import exec_sim_oracle as sim  # noqa: E402
from tests import test_astra_exec_p2_integration as exec_p2  # noqa: E402
from tests import test_player_e2e_robustness_matrix as p5_e2e  # noqa: E402
from test_astra_player_tracking_v13 import M, post, serving  # noqa: E402

D = contracts.D
ZERO = contracts.ZERO

REVOKE_REPLY_TEXT = (
    "## REVOKE\n\n卖出短腿60,000已被跌破且确认触发M1，立即撤销。\n"
    '<HOLD_REVIEW_JSON>{"verdict":"REVOKE","affected_conditions":["M1"],'
    '"public_reply":"确认触发M1撤销","key_facts":["跌破60000"]}</HOLD_REVIEW_JSON>'
)

HOLD_REPLY_TEXT = (
    "## HOLD\n\n缓冲仍在可承受范围，维持观察。\n"
    '<HOLD_REVIEW_JSON>{"verdict":"HOLD","affected_conditions":[],'
    '"public_reply":"维持观察","key_facts":["余量尚存"]}</HOLD_REVIEW_JSON>'
)

HOLD_UNKNOWN_REPLY_TEXT = (
    "## UNKNOWN\n\n短时采样噪声，维持观察但结论暂不确定。\n"
    '<HOLD_REVIEW_JSON>{"verdict":"UNKNOWN","affected_conditions":[],'
    '"public_reply":"暂维持观察","key_facts":["采样不足"]}</HOLD_REVIEW_JSON>'
)


def _venue_pos_map(venue: sim.SimulatedDeribitVenue) -> dict[str, Decimal]:
    return {p.instrument_name: D(p.size) for p in venue.get_positions() if D(p.size) != ZERO}


def _reject_reason_from_tick(out: dict[str, Any], coord: coord_mod.ExecutionCoordinator) -> str | None:
    p5b = out.get("p5_bridge")
    if isinstance(p5b, dict) and p5b.get("last_reject_reason"):
        return str(p5b["last_reject_reason"])
    return coord._last_reject_reason


def _new_venue_from_p5_contract(cond_contract: dict[str, Any], *, now_ms: int, spot_usd: float) -> sim.SimulatedDeribitVenue:
    short_inst = str(cond_contract["short_instrument"])
    long_inst = str(cond_contract["long_instrument"])
    venue = exec_p2._setup_venue(
        t0=now_ms,
        expiry_ms=int(cond_contract["expiry_ms"]),
        short_inst=short_inst,
        long_inst=long_inst,
        short_strike=int(cond_contract["short_strike_usd"]),
        long_strike=int(cond_contract["long_strike_usd"]),
        option_type=str(cond_contract["side"]).lower(),
    )
    venue.set_spot_usd(str(spot_usd))
    return venue


def _new_armed_coord(
    base_dir: Path,
    venue: sim.SimulatedDeribitVenue,
    dossier_root: Path,
    *,
    armed: bool = True,
    qty_btc: str = "0.2",
    slice_btc: str = "0.2",
    budget_btc: str = "0.50",
) -> tuple[store.ExecutionStore, exec_p2.SimExchangeAdapter, coord_mod.ExecutionCoordinator]:
    base_dir.mkdir(parents=True, exist_ok=True)
    st = store.ExecutionStore(base_dir / "exec.sqlite3")
    adp = exec_p2.SimExchangeAdapter(venue)
    coord = coord_mod.ExecutionCoordinator(
        mode="TESTNET",
        env_tag="tn",
        subaccount_id="sub-tn-1",
        store=st,
        adapter=adp,
        status_path=base_dir / "public_status.json",
        dossier_root=dossier_root,
        config=contracts.ExecConfig(initial_qty_btc=D(qty_btc), max_entry_slice_btc=D(slice_btc)),
    )
    coord.startup_init(now_ms=venue.server_time_ms)
    if armed:
        coord.handle_capital_policy(
            {"entry_armed": True, "budget_cap_btc": budget_btc, "max_qty_btc": qty_btc, "operator": "joint_e2e"},
            now_ms=venue.server_time_ms,
            run_tick_after=False,
        )
    return st, adp, coord


def _make_real_p5_accept(
    base_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    slot_0: int,
    accept_put: str,
    tag: str,
) -> tuple[Any, dossier.Dossiers, Path, Path, Path, Path, dict[str, Any], list[int], Any, str]:
    clk = [slot_0 + 2 * M]
    wb, arch, dos, plan, _sm, exp_ms, _wc, sync_fn = p5_e2e._setup_nov26_workbench_and_archive(
        base_dir, monkeypatch, clock=clk
    )
    with serving(wb) as wb_url, p5_e2e._sse_mock_server(
        lambda _i, _b: {"status": 200, "text": accept_put, "finish_reason": "stop"}
    ) as (llm_ep, _calls):
        svc = post(wb_url, wb)
        d_obj = dossier.Dossiers(dos, clock=lambda: clk[0], require_reply_provenance=True)
        res, = tracking.auto_deal(
            d_obj,
            plan,
            arch,
            svc,
            llm_config={
                "api_key": f"k-{tag}",
                "model": "deepseek-reasoner",
                "endpoint": llm_ep,
                "prompt_version": review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
                "input_profile": "p0_strict@1",
            },
        )
        assert res["status"] == "VALID" and res["decision"] == "Accept"
    hand_id = str(res["hand_id"])
    ep_id = str(res["link"]["origin"]["dossier_episode_id"])
    dos_dir = d_obj.path(exp_ms)
    hand_dir = dos_dir / "hands" / hand_id
    ep_dir = dos_dir / "episodes" / ep_id
    cond = json.loads((ep_dir / "condition.json").read_text(encoding="utf-8"))["contract"]
    return wb, d_obj, arch, dos, hand_dir, ep_dir, cond, clk, sync_fn, hand_id


def _seed_m1_trigger(wb: Any, d: dossier.Dossiers, ep_dir: Path, clock: list[int], sync_aux: Any) -> None:
    sync_aux()
    wb.refresh(now_ms=clock[0], record_daily=False)
    d.event(
        ep_dir,
        dossier.identity("chk_joint", clock[0] - 1_000),
        "CONDITION_TRIGGERED",
        {"result": {"status": "triggered", "rule": "entry_not_returned", "breach_usd": 58_900.0}},
        observed=clock[0] - 1_000,
        available=clock[0],
    )
    if (ep_dir / "trigger_state.json").is_file():
        ts_doc = dossier.read(ep_dir / "trigger_state.json")
        ts_doc["dispatched_signatures"] = {}
        ts_doc["cooldown_until_ms"] = None
        dossier.write(ep_dir / "trigger_state.json", dossier.encode(ts_doc), replace=True)


def _emit_real_p5_sync_open_review(
    wb: Any,
    d: dossier.Dossiers,
    ep_dir: Path,
    arch_root: Path,
    clock: list[int],
    sync_aux: Any,
    *,
    reply_text: str = REVOKE_REPLY_TEXT,
    exec_status_path: Path | None = None,
    live_budget_path: Path | None = None,
) -> list[dict[str, Any]]:
    _seed_m1_trigger(wb, d, ep_dir, clock, sync_aux)
    with serving(wb) as wb_url, p5_e2e._sse_mock_server(
        lambda _idx, _body: {"status": 200, "text": reply_text, "finish_reason": "stop"}
    ) as (llm_ep, llm_calls):
        svc = post(wb_url, wb)
        rows = tracking.sync_open(
            d,
            svc,
            check_due=False,
            archive_root=arch_root,
            run_review=True,
            llm_config={"api_key": "k-joint-hold", "model": "deepseek-v4-pro", "endpoint": llm_ep},
            exec_status_path=exec_status_path,
            live_budget_path=live_budget_path,
        )
        assert len(llm_calls) >= 1
    return rows


def _run_tracking_cli(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    argv: list[str],
    clock_box: list[int],
) -> tuple[int, Any, str]:
    orig_dossiers = dossier.Dossiers

    class _ClockedDossiers(orig_dossiers):
        def __init__(self, root_p: Path, clock: Any = None, **kwargs: Any) -> None:
            kwargs.setdefault("require_reply_provenance", True)
            super().__init__(root_p, clock=(clock or (lambda: clock_box[0])), **kwargs)

    capsys.readouterr()
    rc = 0
    with monkeypatch.context() as mp:
        mp.setattr(dossier, "Dossiers", _ClockedDossiers)
        try:
            tracking.main(argv)
        except SystemExit as exc:
            rc = int(exc.code) if isinstance(exc.code, int) else 1
    cap = capsys.readouterr()
    parsed = json.loads(cap.out) if cap.out.strip().startswith(("{", "[")) else cap.out
    return rc, parsed, cap.err


# ---------------------------------------------------------------------------
# U1: Normal Lifecycle (`prewarm -> auto_deal -> TP -> LX` & `16:40Z` Deferred Recovery -> Expiry)
# ---------------------------------------------------------------------------


def test_u1_normal_and_deferred_recovery_real_p5_to_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "k-u1-env")
    monkeypatch.setenv("ASTRA_PLAYER_USAGE_LEDGER", str(tmp_path / "u1.json"))
    slot_0, _expiry_ms, spot, _rows, _cd = p5_e2e._nov26_27nov26_chain()
    accept_put = p5_e2e._strict_p5_reply_text("## Accept / Put", short_strike=60_000, long_strike=58_000, require_outside_at_t0=True)

    # U1a: prewarm -> auto_deal Accept -> EP -> ES -> TP -> LX -> EARLY_FLAT_SETTLED
    clk_a = [slot_0 - 8 * M]
    wb_a, arch_a, dos_a, plan_a, slot_ms, exp_a, _, sync_a = p5_e2e._setup_nov26_workbench_and_archive(
        tmp_path / "a", monkeypatch, clock=clk_a
    )
    with serving(wb_a) as wb_url_a, p5_e2e._sse_mock_server(
        lambda _i, _b: {"status": 200, "text": accept_put, "finish_reason": "stop"}
    ) as (llm_ep_a, llm_calls_a):
        svc_a = post(wb_url_a, wb_a)
        rep_pre = tracking.prewarm(plan_a, arch_a, svc_a, clock=lambda: clk_a[0], public=dossier.public)
        assert rep_pre["business_readiness"]["ready_for_deal"] is True

        clk_a[0] = slot_ms + 2 * M
        sync_a()
        wb_a.refresh(now_ms=clk_a[0], record_daily=False)
        d_a = dossier.Dossiers(dos_a, clock=lambda: clk_a[0], require_reply_provenance=True)
        res_a, = tracking.auto_deal(
            d_a,
            plan_a,
            arch_a,
            svc_a,
            llm_config={
                "api_key": "k-u1a",
                "model": "deepseek-reasoner",
                "endpoint": llm_ep_a,
                "prompt_version": review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
                "input_profile": "p0_strict@1",
            },
        )
        assert res_a["status"] == "VALID" and res_a["decision"] == "Accept" and len(llm_calls_a) == 1

    ep_dir_a = d_a.path(exp_a) / "episodes" / str(res_a["link"]["origin"]["dossier_episode_id"])
    cond_a = json.loads((ep_dir_a / "condition.json").read_text(encoding="utf-8"))["contract"]
    short_inst, long_inst = str(cond_a["short_instrument"]), str(cond_a["long_instrument"])

    venue_a = _new_venue_from_p5_contract(cond_a, now_ms=clk_a[0] + 2_000, spot_usd=spot)
    st_a, _adp_a, coord_a = _new_armed_coord(tmp_path / "ax", venue_a, dos_a, qty_btc="0.2", slice_btc="0.2")
    try:
        exec_p2._drive(coord_a, venue_a, short_inst, long_inst, n=4, dt_ms=50)
        ep_a = st_a.get_latest_episode()
        assert ep_a is not None and ep_a.phase == contracts.LifecyclePhase.TP_MANAGED
        assert _venue_pos_map(venue_a) == {short_inst: D("-0.2"), long_inst: D("0.2")}

        tp_cmd = next(c for c in st_a.get_commands(ep_a.episode_id) if c.purpose == "TP" and c.status == contracts.CommandStatus.OPEN)
        venue_a.fill_resting_order(tp_cmd.label, D("0.2"), price=tp_cmd.limit_price, liquidity="M")
        exec_p2._drive(coord_a, venue_a, short_inst, long_inst, n=6, dt_ms=500)

        ep_a_end = st_a.get_episode(ep_a.episode_id)
        assert ep_a_end is not None and ep_a_end.phase == contracts.LifecyclePhase.CASHFLOW_SETTLED
        assert ep_a_end.terminal_outcome == "EARLY_FLAT_SETTLED"
        oracle_a = sim.IndependentSettlementOracle(initial_balance_btc=D("1.0"), trades=venue_a.export_raw_trades())
        assert D(st_a.rebuild_episode_ledger(ep_a.episode_id)["accounting"]["episode_total_realized_pnl_btc"]) == oracle_a.expected_episode_realized_pnl_btc
    finally:
        st_a.close()

    # U1b: 16:27Z deferred -> 16:40Z recover_initial(allow_post_window_retry=True) -> Expiry two-stage + P5 settle_due
    clk_b = [slot_0 + 27 * M]
    wb_b, arch_b, dos_b, plan_b, _, exp_b, _, sync_b = p5_e2e._setup_nov26_workbench_and_archive(
        tmp_path / "b", monkeypatch, clock=clk_b
    )
    fake_mono, call_idx = [1_000.0], [0]

    def _deferred_then_valid(_k: Any, _m: Any, _b: Any, timeout_s: float, endpoint: Any = None) -> dict[str, Any]:
        del timeout_s, endpoint
        call_idx[0] += 1
        if call_idx[0] == 1:
            clk_b[0] = slot_0 + 36 * M + 30_000
            fake_mono[0] += 630.0
            sync_b()
            return {"choices": [{"finish_reason": "length", "message": {"content": "## Accept / Put\n截断"}}]}
        sync_b()
        wb_b.refresh(now_ms=clk_b[0], record_daily=False)
        return {"choices": [{"finish_reason": "stop", "message": {"content": accept_put}}]}

    with serving(wb_b) as wb_url_b:
        svc_b = post(wb_url_b, wb_b)
        d_b = dossier.Dossiers(dos_b, clock=lambda: clk_b[0], require_reply_provenance=True)
        monkeypatch.setattr(tracking.time, "monotonic", lambda: fake_mono[0])
        cfg_b = {
            "api_key": "k-u1b",
            "model": "deepseek-v4-pro",
            "transport": _deferred_then_valid,
            "service_budget_s": 810.0,
            "prompt_version": review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
            "input_profile": "p0_strict@1",
        }
        assert tracking.auto_deal(d_b, plan_b, arch_b, svc_b, llm_config=cfg_b)[0]["status"] == "PARSE_FAILED"
        fake_mono[0] = 5_000.0
        clk_b[0] = slot_0 + 40 * M
        rec_b = tracking.recover_initial(d_b, svc_b, llm_config=cfg_b, allow_post_window_retry=True)
        assert len(rec_b) == 1 and rec_b[0]["status"] == "VALID" and rec_b[0]["link"]["state"] == "SHADOW_LOCK_CREATED"

    cond_b = json.loads((d_b.path(exp_b) / "episodes" / str(rec_b[0]["link"]["origin"]["dossier_episode_id"]) / "condition.json").read_text(encoding="utf-8"))["contract"]
    venue_b = _new_venue_from_p5_contract(cond_b, now_ms=clk_b[0] + 5_000, spot_usd=spot)
    st_b, _adp_b, coord_b = _new_armed_coord(tmp_path / "bx", venue_b, dos_b, qty_btc="0.1", slice_btc="0.1")
    try:
        exec_p2._drive(coord_b, venue_b, short_inst, long_inst, n=4, dt_ms=50)
        ep_b = st_b.get_latest_episode()
        assert ep_b is not None and ep_b.phase == contracts.LifecyclePhase.TP_MANAGED
        venue_b.set_server_time(ep_b.expiry_ms + 1_000)
        venue_b.inject_delayed_tx_log(True)
        venue_b.expire_at_settlement(D("63000"))
        exec_p2._drive(coord_b, venue_b, short_inst, long_inst, n=2, dt_ms=100)
        assert st_b.get_episode(ep_b.episode_id).phase == contracts.LifecyclePhase.INVENTORY_CLEARED  # type: ignore[union-attr]
        venue_b.settle_delayed_tx_log(flush_pending=True)
        exec_p2._drive(coord_b, venue_b, short_inst, long_inst, n=2, dt_ms=100)
        ep_b_done = st_b.get_episode(ep_b.episode_id)
        assert ep_b_done is not None and ep_b_done.phase == contracts.LifecyclePhase.CASHFLOW_SETTLED and ep_b_done.terminal_outcome == "EXPIRY_SETTLED"
        clk_b[0] = exp_b + 5 * M
        assert len(list(d_b.settle_due())) == 1
    finally:
        st_b.close()


# ---------------------------------------------------------------------------
# U2: Landing 1 — Single `sync_open()` CLI/Direct Parity with `--exec-status-path` & `--live-budget-path`
# ---------------------------------------------------------------------------


def test_u2_landing1_sync_open_cli_and_direct_parity_with_live_context_and_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    slot_0, _exp_ms, spot, _rows, _cd = p5_e2e._nov26_27nov26_chain()
    accept_put = p5_e2e._strict_p5_reply_text("## Accept / Put", short_strike=60_000, long_strike=58_000, require_outside_at_t0=True)

    results_by_mode: dict[str, dict[str, Any]] = {}
    for mode in ("direct", "cli"):
        base = tmp_path / mode
        shadow_ledger = base / "sh.json"
        live_ledger = base / "lv.json"
        monkeypatch.setenv("LLM_API_KEY", f"k-u2-{mode}")
        monkeypatch.setenv("ASTRA_PLAYER_USAGE_LEDGER", str(shadow_ledger))
        monkeypatch.setenv("ASTRA_PLAYER_DAILY_HTTP_LIMIT", "15")
        monkeypatch.setenv("ASTRA_PLAYER_LIVE_DAILY_HTTP_LIMIT", "60")

        wb, d, arch, dos, hand_dir, ep_dir, cond, clk, sync_fn, hand_id = _make_real_p5_accept(
            base / "p5", monkeypatch, slot_0=slot_0, accept_put=accept_put, tag=f"u2_{mode}"
        )
        short_inst, long_inst = str(cond["short_instrument"]), str(cond["long_instrument"])
        venue = _new_venue_from_p5_contract(cond, now_ms=clk[0] + 5_000, spot_usd=spot)
        st, _adp, coord = _new_armed_coord(base / "ex", venue, dos, qty_btc="0.1", slice_btc="0.1")
        try:
            exec_p2._drive(coord, venue, short_inst, long_inst, n=4, dt_ms=50)
            ep_tp = st.get_latest_episode()
            assert ep_tp is not None and ep_tp.phase == contracts.LifecyclePhase.TP_MANAGED
            st_path = base / "ex" / "public_status.json"

            # Exhaust shared shadow budget completely (15/15) so ONLY live isolated 60/day ledger can succeed!
            sh_budget = signal_llm_review.DailyHttpBudget(shadow_ledger, limit=15)
            for _ in range(15 - sh_budget.snapshot()["http_calls_used"]):
                sh_budget.reserve(1, role="exhaust_shadow_test")
            assert sh_budget.snapshot()["http_calls_used"] == 15

            clk[0] = venue.server_time_ms + 30_000
            venue.advance_time(30_000)
            exec_p2._refresh_books(venue, short_inst, long_inst)
            coord.run_tick(now_ms=venue.server_time_ms)
            coord.handle_health_fresh(now_ms=venue.server_time_ms)
            _seed_m1_trigger(wb, d, ep_dir, clk, sync_fn)

            with serving(wb) as wb_url, p5_e2e._sse_mock_server(
                lambda _i, _b: {"status": 200, "text": REVOKE_REPLY_TEXT, "finish_reason": "stop"}
            ) as (llm_ep, llm_calls):
                monkeypatch.setenv("ASTRA_AI_SHADOW_LOCK_TOKEN", str(wb.config.shadow_service_token))
                monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro")
                monkeypatch.setenv("LLM_BASE_URL", llm_ep)
                if mode == "direct":
                    svc = post(wb_url, wb)
                    rows = tracking.sync_open(
                        d,
                        svc,
                        check_due=True,
                        archive_root=arch,
                        run_review=True,
                        exec_status_path=st_path,
                        live_budget_path=live_ledger,
                    )
                    rc = 0 if tracking.classify_sync_open_batch(rows)["batch_status"] == "ALL_OK" else 2
                else:
                    rc, rows, err = _run_tracking_cli(
                        monkeypatch,
                        capsys,
                        argv=[
                            "--url", wb_url, "sync-open",
                            "--root", str(dos),
                            "--archive", str(arch),
                            "--check-due", "--review",
                            "--exec-status-path", str(st_path),
                            "--live-budget-path", str(live_ledger),
                        ],
                        clock_box=clk,
                    )
                    assert err == ""
                assert rc == 0 and len(llm_calls) == 1

            # Verify live_execution_active kept tracker running, isolated live ledger incremented, shadow untouched,
            # and ExecutionCoordinator unwinds cleanly to CASHFLOW_SETTLED!
            sx_doc = json.loads((hand_dir / "tracking" / "shadow_exit.json").read_text(encoding="utf-8"))
            assert sx_doc["tracker_stopped"] is False
            lv_snap = signal_llm_review.DailyHttpBudget(live_ledger, limit=60).snapshot()
            assert lv_snap["http_calls_used"] == 1
            assert lv_snap["events"][-1]["role"] == "astra_player_live_hold_review"
            assert sh_budget.snapshot()["http_calls_used"] == 15

            exec_p2._drive(coord, venue, short_inst, long_inst, n=8, dt_ms=500)
            ep_done = st.get_episode(ep_tp.episode_id)
            assert ep_done is not None and ep_done.phase == contracts.LifecyclePhase.CASHFLOW_SETTLED
            assert ep_done.exit_latch_reason == "AI_REVOKE"

            hr = rows[0]["hold_review"]
            results_by_mode[mode] = {
                "rc": rc,
                "row_count": len(rows),
                "state": rows[0]["state"],
                "verdict": hr["verdict"],
                "live_execution_active": hr["live_execution_active"],
                "live_calls": lv_snap["http_calls_used"],
                "live_role": lv_snap["events"][-1]["role"],
                "tracker_stopped": sx_doc["tracker_stopped"],
            }
        finally:
            st.close()

    assert results_by_mode["cli"] == results_by_mode["direct"]


# ---------------------------------------------------------------------------
# U3: Landing 2 — Bad-Dossier Sorting Isolation in `sync-open` + Execution Isolation
# ---------------------------------------------------------------------------


def test_u3_landing2_corrupt_sibling_sorting_isolation_in_sync_open_and_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "k-u3-env")
    monkeypatch.setenv("ASTRA_PLAYER_USAGE_LEDGER", str(tmp_path / "u3.json"))
    slot_0, exp_ms, spot, _rows, _cd = p5_e2e._nov26_27nov26_chain()
    accept_put = p5_e2e._strict_p5_reply_text("## Accept / Put", short_strike=60_000, long_strike=58_000, require_outside_at_t0=True)

    wb, d, arch, dos_root, hand_dir, ep_dir, cond, clk, sync_fn, hand_id = _make_real_p5_accept(
        tmp_path / "p5", monkeypatch, slot_0=slot_0, accept_put=accept_put, tag="u3"
    )
    dos_cur_dir = d.path(exp_ms)
    short_inst, long_inst = str(cond["short_instrument"]), str(cond["long_instrument"])

    # Seed 1: Legacy D-2 dossier with `NOTE` timestamp = "unavailable"
    exec_p2._build_p5_dossier(
        dos_root,
        dossier_id="Deribit_BTC_20261125T080000Z",
        hand_id="hand_legacy_bad_note",
        ep_id="episode_legacy_bad_note",
        expiry_ms=exp_ms - 2 * 86_400_000,
        decided_at_ms=clk[0] - 2 * 86_400_000,
    )
    (dos_root / "dossiers" / "Deribit_BTC_20261125T080000Z" / "episodes" / "episode_legacy_bad_note" / "events" / "note_unavail.json").write_text(
        json.dumps({"kind": "NOTE", "recorded_at_ms": "unavailable", "observed_at_ms": "unavailable", "body": {}}),
        encoding="utf-8",
    )

    # Seed 2: Corrupt sibling hands covering ALL 3 `_open_hand_sync_priority_key` fault files:
    #   (a) truncated UTF-8 `events/000_trunc.json` + invalid UTF-8 `decision.json`
    #   (b) malformed JSON `expected.json`
    #   (c) malformed JSON `link.json`
    sib_a = dos_cur_dir / "hands" / "hand_bad_ev_001"
    sib_a_ep = dos_cur_dir / "episodes" / "episode_bad_ev_001"
    (sib_a / "tracking").mkdir(parents=True, exist_ok=True)
    (sib_a_ep / "events").mkdir(parents=True, exist_ok=True)
    (sib_a / "decision.json").write_bytes(b"\xff\xfe\x00bad")
    (sib_a / "expected.json").write_text(json.dumps({"expiry_ms": exp_ms}), encoding="utf-8")
    (sib_a / "tracking" / "link.json").write_text(
        json.dumps(
            {
                "state": "SHADOW_LOCK_CREATED",
                "recorded_at_ms": clk[0] - 5_000,
                "origin": {"dossier_id": dos_cur_dir.name, "hand_id": "hand_bad_ev_001", "dossier_episode_id": "episode_bad_ev_001"},
            }
        ),
        encoding="utf-8",
    )
    (sib_a_ep / "events" / "000_trunc.json").write_bytes(b'{"kind": "OBSERVATION", \x80')

    sib_b = dos_cur_dir / "hands" / "hand_bad_exp_002"
    (sib_b / "tracking").mkdir(parents=True, exist_ok=True)
    (sib_b / "expected.json").write_bytes(b"{not-valid-json")
    (sib_b / "tracking" / "link.json").write_text(
        json.dumps({"state": "SHADOW_LOCK_CREATED", "recorded_at_ms": clk[0] - 6_000, "origin": {"dossier_episode_id": "ep_missing"}}),
        encoding="utf-8",
    )

    sib_c = dos_cur_dir / "hands" / "hand_bad_link_003"
    (sib_c / "tracking").mkdir(parents=True, exist_ok=True)
    (sib_c / "expected.json").write_text(json.dumps({"expiry_ms": exp_ms}), encoding="utf-8")
    (sib_c / "tracking" / "link.json").write_bytes(b"\xff\xfecorrupt_link")

    # (d) valid JSON `link.json` with non-dict truthy `origin` + (e) corrupt `trigger_state.json`
    sib_d = dos_cur_dir / "hands" / "hand_bad_origin_004"
    (sib_d / "tracking").mkdir(parents=True, exist_ok=True)
    (sib_d / "expected.json").write_text(json.dumps({"expiry_ms": exp_ms}), encoding="utf-8")
    (sib_d / "tracking" / "link.json").write_text(json.dumps({"state": "SHADOW_LOCK_CREATED", "origin": "corrupt_str"}), encoding="utf-8")

    sib_e = dos_cur_dir / "hands" / "hand_bad_trig_005"
    sib_e_ep = dos_cur_dir / "episodes" / "episode_bad_trig_005"
    (sib_e / "tracking").mkdir(parents=True, exist_ok=True)
    sib_e_ep.mkdir(parents=True, exist_ok=True)
    (sib_e / "expected.json").write_text(json.dumps({"expiry_ms": exp_ms}), encoding="utf-8")
    (sib_e / "tracking" / "link.json").write_text(
        json.dumps({"state": "SHADOW_LOCK_CREATED", "origin": {"dossier_episode_id": "episode_bad_trig_005"}}),
        encoding="utf-8",
    )
    (sib_e_ep / "trigger_state.json").write_bytes(b"\xff{bad_trigger_state")

    # Open a real Execution position on the healthy hand AND verify unarmed coordinator cannot open from cache
    venue = _new_venue_from_p5_contract(cond, now_ms=clk[0] + 5_000, spot_usd=spot)
    st, _adp, coord = _new_armed_coord(tmp_path / "ex_open", venue, dos_root, qty_btc="0.1", slice_btc="0.1")
    st_empty, _adp_empty, coord_empty = _new_armed_coord(tmp_path / "ex_empty", venue, dos_root, armed=False)
    try:
        exec_p2._drive(coord, venue, short_inst, long_inst, n=4, dt_ms=50)
        ep_open = st.get_latest_episode()
        assert ep_open is not None and ep_open.phase == contracts.LifecyclePhase.TP_MANAGED
        coord_empty.run_tick(now_ms=venue.server_time_ms)
        assert not hasattr(coord_empty, "_cached_contracts")

        # Run real P5 CLI `sync-open --check-due --review` WITH all 3 corrupt siblings present!
        clk[0] = venue.server_time_ms + 30_000
        venue.advance_time(30_000)
        exec_p2._refresh_books(venue, short_inst, long_inst)
        _seed_m1_trigger(wb, d, ep_dir, clk, sync_fn)
        with serving(wb) as wb_url, p5_e2e._sse_mock_server(
            lambda _i, _b: {"status": 200, "text": REVOKE_REPLY_TEXT, "finish_reason": "stop"}
        ) as (llm_ep, llm_calls):
            monkeypatch.setenv("ASTRA_AI_SHADOW_LOCK_TOKEN", str(wb.config.shadow_service_token))
            monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro")
            monkeypatch.setenv("LLM_BASE_URL", llm_ep)
            rc, rows, err = _run_tracking_cli(
                monkeypatch,
                capsys,
                argv=[
                    "--url", wb_url, "sync-open",
                    "--root", str(dos_root),
                    "--archive", str(arch),
                    "--check-due", "--review",
                ],
                clock_box=clk,
            )
            assert len(llm_calls) == 1

        # Healthy open hand STILL completed REVOKE; corrupt siblings recorded TRACKER_EXPORT_UNAVAILABLE; batch=DEGRADED, rc=2!
        assert rc == 2
        batch_err = json.loads(err.strip().splitlines()[-1])
        assert batch_err["batch_status"] == "DEGRADED"
        assert batch_err["ok_count"] >= 1 and batch_err["failed_count"] >= 5
        assert any((r.get("hold_review") or {}).get("verdict") == "REVOKE" for r in rows if isinstance(r, dict))
        failed_ids = {r.get("hand_id") for r in rows if isinstance(r, dict) and r.get("state") == "TRACKER_EXPORT_UNAVAILABLE"}
        assert {"hand_bad_ev_001", "hand_bad_exp_002", "hand_bad_link_003", "hand_bad_origin_004", "hand_bad_trig_005"}.issubset(failed_ids)

        # Open execution position unwinds via AI_REVOKE; empty unarmed->armed coordinator refuses to open!
        exec_p2._drive(coord, venue, short_inst, long_inst, n=8, dt_ms=500)
        ep_done = st.get_episode(ep_open.episode_id)
        assert ep_done is not None and ep_done.phase == contracts.LifecyclePhase.CASHFLOW_SETTLED and ep_done.exit_latch_reason == "AI_REVOKE"

        coord_empty.handle_capital_policy(
            {"entry_armed": True, "budget_cap_btc": "0.50", "max_qty_btc": "0.2", "operator": "u3_op"},
            now_ms=venue.server_time_ms,
            run_tick_after=False,
        )
        out_empty = coord_empty.run_tick(now_ms=venue.server_time_ms)
        assert _reject_reason_from_tick(out_empty, coord_empty) == "P5_ALREADY_REVOKED"
        assert st_empty.get_latest_episode() is None
    finally:
        st.close()
        st_empty.close()


# ---------------------------------------------------------------------------
# U4: Landing 3 — Zero-Short-Liability Residual Hold Budget Ownership
# ---------------------------------------------------------------------------


def test_u4_landing3_zero_short_liability_residual_hold_never_consumes_live_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot_0, _exp_ms, spot, _rows, _cd = p5_e2e._nov26_27nov26_chain()
    accept_put = p5_e2e._strict_p5_reply_text("## Accept / Put", short_strike=60_000, long_strike=58_000, require_outside_at_t0=True)

    # 1) Direct unit matrix on `_resolve_live_execution_context_for_hand` across all status shapes
    hand_id_unit = "hand_66a598b4790172fa3fd56276"

    def _write_status(name: str, payload: dict[str, Any]) -> Path:
        p = tmp_path / f"{name}.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        return p

    base_ep = {
        "episode_id": "ep_test_01",
        "hand_id": hand_id_unit,
        "cashflow_settled": False,
        "short_liability_cleared": False,
        "phase": "TP_MANAGED",
        "matched_qty_btc": "0.1",
        "short_pos_btc": "-0.1",
        "prot_pos_btc": "0.1",
        "entry_started_at_ms": 1_795_700_000_000,
        "entry_spot_usd": "63500",
        "matched_net_credit_btc": "0.0003",
    }
    # Positive controls: active TP_MANAGED (short_pos > 0) for TESTNET and LIVE, ENTRY (even when short_liability_cleared=True before ES!),
    # cold-restart TP_MANAGED (`facts_asof_ms=None`, `matched_qty > 0`), and malformed entry_ms does NOT raise.
    ref_now = 1_795_700_006_000
    assert tracking._resolve_live_execution_context_for_hand(
        _write_status("ok_tp", {"mode": "TESTNET", "stale": False, "generated_at_ms": 1_795_700_005_000, "stale_after_ms": 15_000, "facts_asof_ms": 1_795_700_005_000, "active_episode": dict(base_ep)}),
        hand_id_unit,
        now_ms=ref_now,
    ) is not None
    assert tracking._resolve_live_execution_context_for_hand(
        _write_status("ok_live", {"mode": "LIVE", "stale": False, "generated_at_ms": 1_795_700_005_000, "stale_after_ms": 15_000, "facts_asof_ms": 1_795_700_005_000, "active_episode": dict(base_ep)}),
        hand_id_unit,
        now_ms=ref_now,
    ) is not None
    assert tracking._resolve_live_execution_context_for_hand(
        _write_status("ok_entry", {"mode": "TESTNET", "stale": False, "generated_at_ms": 1_795_700_005_000, "stale_after_ms": 15_000, "facts_asof_ms": 1_795_700_005_000, "active_episode": {**base_ep, "phase": "ENTRY", "short_liability_cleared": True, "short_pos_btc": "0", "matched_qty_btc": "0", "prot_pos_btc": "0.1"}}),
        hand_id_unit,
        now_ms=ref_now,
    ) is not None
    assert tracking._resolve_live_execution_context_for_hand(
        _write_status("ok_cold_restart", {"mode": "TESTNET", "stale": False, "generated_at_ms": 1_795_700_005_000, "stale_after_ms": 15_000, "facts_asof_ms": None, "active_episode": {**base_ep, "phase": "TP_MANAGED", "short_pos_btc": "0", "matched_qty_btc": "0.1"}}),
        hand_id_unit,
        now_ms=ref_now,
    ) is not None
    ctx_bad_ts = tracking._resolve_live_execution_context_for_hand(
        _write_status("ok_bad_ts", {"mode": "TESTNET", "stale": False, "generated_at_ms": 1_795_700_005_000, "stale_after_ms": 15_000, "facts_asof_ms": 1_795_700_005_000, "active_episode": {**base_ep, "entry_started_at_ms": "not_an_int"}}),
        hand_id_unit,
        now_ms=ref_now,
    )
    assert ctx_bad_ts is not None and "actual_entry_at_ms" not in ctx_bad_ts

    # Negative controls: residual hold / zero short liability / stale flag / expired TTL (even stale=False!) / SHADOW_ONLY -> MUST return None
    neg_cases = {
        "short_liability_cleared_phase": {"mode": "TESTNET", "stale": False, "generated_at_ms": 1_795_700_005_000, "stale_after_ms": 15_000, "facts_asof_ms": 1_795_700_005_000, "active_episode": {**base_ep, "phase": "SHORT_LIABILITY_CLEARED", "short_liability_cleared": True, "short_pos_btc": "0", "prot_pos_btc": "0.1"}},
        "inventory_cleared_phase": {"mode": "TESTNET", "stale": False, "generated_at_ms": 1_795_700_005_000, "stale_after_ms": 15_000, "facts_asof_ms": 1_795_700_005_000, "active_episode": {**base_ep, "phase": "INVENTORY_CLEARED", "short_liability_cleared": True, "short_pos_btc": "0", "prot_pos_btc": "0"}},
        "zero_short_only_prot_tp": {"mode": "TESTNET", "stale": False, "generated_at_ms": 1_795_700_005_000, "stale_after_ms": 15_000, "facts_asof_ms": 1_795_700_005_000, "active_episode": {**base_ep, "phase": "TP_MANAGED", "short_liability_cleared": False, "short_pos_btc": "0", "prot_pos_btc": "0.1"}},
        "stale_status": {"mode": "TESTNET", "stale": True, "generated_at_ms": 1_795_700_005_000, "stale_after_ms": 15_000, "facts_asof_ms": 1_795_700_005_000, "active_episode": dict(base_ep)},
        "expired_ttl_live": {"mode": "LIVE", "stale": False, "generated_at_ms": 1_795_700_005_000, "stale_after_ms": 15_000, "facts_asof_ms": 1_795_700_005_000, "active_episode": dict(base_ep)},
        "shadow_only_mode": {"mode": "SHADOW_ONLY", "stale": False, "generated_at_ms": 1_795_700_005_000, "stale_after_ms": 15_000, "facts_asof_ms": 1_795_700_005_000, "active_episode": dict(base_ep)},
    }
    for cname, payload in neg_cases.items():
        case_now = (1_795_700_005_000 + 20_000) if cname == "expired_ttl_live" else ref_now
        assert tracking._resolve_live_execution_context_for_hand(_write_status(cname, payload), hand_id_unit, now_ms=case_now) is None, cname

    # 2) Real ExecutionCoordinator produces a genuine `SHORT_LIABILITY_CLEARED` (`RESIDUAL_HOLD`) `public_status.json`
    #    after partial EP + dust bid -> `sync_open --review` uses shadow ledger and leaves live ledger at 0!
    shadow_ledger = tmp_path / "u4_sh.json"
    live_ledger = tmp_path / "u4_lv.json"
    monkeypatch.setenv("LLM_API_KEY", "k-u4-env")
    monkeypatch.setenv("ASTRA_PLAYER_USAGE_LEDGER", str(shadow_ledger))
    monkeypatch.setenv("ASTRA_PLAYER_DAILY_HTTP_LIMIT", "15")
    monkeypatch.setenv("ASTRA_PLAYER_LIVE_DAILY_HTTP_LIMIT", "60")

    wb, d, arch, dos, _hand_dir, ep_dir, cond, clk, sync_fn, hand_id = _make_real_p5_accept(
        tmp_path / "p5_res", monkeypatch, slot_0=slot_0, accept_put=accept_put, tag="u4"
    )
    short_inst, long_inst = str(cond["short_instrument"]), str(cond["long_instrument"])
    venue = _new_venue_from_p5_contract(cond, now_ms=clk[0] + 5_000, spot_usd=spot)
    st, adp, coord = _new_armed_coord(tmp_path / "ex_res", venue, dos, qty_btc="0.2", slice_btc="0.2")
    try:
        # Partial EP fill (0.1 BTC) + dust bid (0.0001) + operator_exit_all -> genuine SHORT_LIABILITY_CLEARED (prot=0.1, short=0)
        venue.inject_partial_fill_cap(long_inst, D("0.1"))
        orig_sub = adp.submit_order
        tripped = {"done": False}

        def _part_ep_then_dust(**kw: Any) -> contracts.ExchangeOrder:
            vo = orig_sub(**kw)
            if not tripped["done"] and ":EP:" in str(kw.get("label") or ""):
                tripped["done"] = True
                venue.inject_partial_fill_cap(long_inst, None)
                venue.set_book(long_inst, bid="0.0001", bid_size="5.0", ask="0.0003", ask_size="5.0", mark_price="0.0002", min_price="0.0001", max_price="0.05")
                venue.set_book(short_inst, bid=None, bid_size="0", ask="0.0046", ask_size="5.0", mark_price="0.0045", min_price="0.0001", max_price="0.05")
            return vo

        monkeypatch.setattr(adp, "submit_order", _part_ep_then_dust)
        coord.run_tick(now_ms=venue.server_time_ms)

        # Mid-ENTRY (real coordinator output: phase="ENTRY", short_liability_cleared=True, prot=0.1, short=0)
        # MUST STILL resolve live_ctx so urgent AI_REVOKE during ENTRY stays on isolated live budget!
        st_path = tmp_path / "ex_res" / "public_status.json"
        pub_entry = json.loads(st_path.read_text(encoding="utf-8"))
        assert pub_entry["active_episode"]["phase"] == "ENTRY"
        assert pub_entry["active_episode"]["short_liability_cleared"] is True
        assert tracking._resolve_live_execution_context_for_hand(st_path, hand_id, now_ms=venue.server_time_ms) is not None

        coord.handle_exit_all(operator="u4_op", reason="OPERATOR_EXIT_ALL", now_ms=venue.server_time_ms)
        exec_p2._drive(coord, venue, short_inst, long_inst, n=3, dt_ms=100)

        ep_res = st.get_latest_episode()
        assert ep_res is not None and ep_res.phase == contracts.LifecyclePhase.SHORT_LIABILITY_CLEARED
        assert ep_res.short_liability_cleared is True and ep_res.inventory_cleared is False
        pub = json.loads(st_path.read_text(encoding="utf-8"))
        assert pub["active_episode"]["phase"] == "SHORT_LIABILITY_CLEARED"
        assert D(pub["active_episode"]["prot_pos_btc"]) == D("0.1") and D(pub["active_episode"]["short_pos_btc"]) == ZERO
        assert tracking._resolve_live_execution_context_for_hand(st_path, hand_id, now_ms=venue.server_time_ms) is None

        # Trigger a hold review via `sync_open(..., exec_status_path=st_path, live_budget_path=live_ledger)`:
        # Must NOT touch `live_ledger` (0 calls); runs on shadow ledger instead!
        clk[0] = venue.server_time_ms + 30_000
        venue.advance_time(30_000)
        rows = _emit_real_p5_sync_open_review(
            wb, d, ep_dir, arch, clk, sync_fn,
            reply_text=HOLD_REPLY_TEXT,
            exec_status_path=st_path,
            live_budget_path=live_ledger,
        )
        assert any((r.get("hold_review") or {}).get("verdict") == "HOLD" for r in rows)
        assert not live_ledger.exists() or signal_llm_review.DailyHttpBudget(live_ledger, limit=60).snapshot()["http_calls_used"] == 0
        sh_snap = signal_llm_review.DailyHttpBudget(shadow_ledger, limit=15).snapshot()
        assert sh_snap["http_calls_used"] >= 1 and sh_snap["events"][-1]["role"] == "astra_player_hold_review"
    finally:
        st.close()


# ---------------------------------------------------------------------------
# U5: Revoke & `>180s` Blocking at Slow Query & Send Boundary + `EP` Filled Revoke Convergence
# ---------------------------------------------------------------------------


def test_u5_revoke_expiry_blocking_and_ep_filled_convergence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "k-u5-env")
    monkeypatch.setenv("ASTRA_PLAYER_USAGE_LEDGER", str(tmp_path / "u5.json"))
    slot_0, _exp_ms, spot, _rows, _cd = p5_e2e._nov26_27nov26_chain()
    accept_put = p5_e2e._strict_p5_reply_text("## Accept / Put", short_strike=60_000, long_strike=58_000, require_outside_at_t0=True)

    # U5a: Mid-slow-query real P5 `sync_open` `AI_REVOKE` -> 0 orders
    wb_1, d_1, arch_1, dos_1, _h1, ep_1, cond_1, clk_1, sync_1, _hid1 = _make_real_p5_accept(
        tmp_path / "c1", monkeypatch, slot_0=slot_0, accept_put=accept_put, tag="u5a"
    )
    venue_1 = _new_venue_from_p5_contract(cond_1, now_ms=clk_1[0] + 10_000, spot_usd=spot)
    st_1, adp_1, coord_1 = _new_armed_coord(tmp_path / "x1", venue_1, dos_1)
    try:
        orig_f1, done_1 = adp_1.fetch_facts, {"v": False}

        def _slow_f1(si: str, li: str, *, need_tx_logs: bool = True) -> contracts.ExchangeFacts:
            if si and li and not done_1["v"]:
                done_1["v"] = True
                clk_1[0] += 25_000
                venue_1.advance_time(15_000)
                exec_p2._refresh_books(venue_1, si, li)
                _emit_real_p5_sync_open_review(wb_1, d_1, ep_1, arch_1, clk_1, sync_1)
            return orig_f1(si, li, need_tx_logs=need_tx_logs)

        monkeypatch.setattr(adp_1, "fetch_facts", _slow_f1)
        out_1 = coord_1.run_tick(now_ms=venue_1.server_time_ms)
        assert _reject_reason_from_tick(out_1, coord_1) == "P5_ALREADY_REVOKED"
        assert st_1.get_latest_episode() is None and venue_1.export_raw_trades() == []
    finally:
        st_1.close()

    # U5b: Slow query crosses 179.5s -> 182.0s (>180s) -> 0 orders
    _wb_2, d_2, _a2, dos_2, h2, _e2, cond_2, _c2, _s2, _hid2 = _make_real_p5_accept(
        tmp_path / "c2", monkeypatch, slot_0=slot_0, accept_put=accept_put, tag="u5b"
    )
    dec_ms_2 = json.loads((h2 / "decision.json").read_text(encoding="utf-8"))["decided_at_ms"]
    venue_2 = _new_venue_from_p5_contract(cond_2, now_ms=int(dec_ms_2) + 179_500, spot_usd=spot)
    st_2, adp_2, coord_2 = _new_armed_coord(tmp_path / "x2", venue_2, dos_2)
    try:
        orig_f2, done_2 = adp_2.fetch_facts, {"v": False}

        def _slow_f2(si: str, li: str, *, need_tx_logs: bool = True) -> contracts.ExchangeFacts:
            if si and li and not done_2["v"]:
                done_2["v"] = True
                venue_2.advance_time(2_500)
                exec_p2._refresh_books(venue_2, si, li)
            return orig_f2(si, li, need_tx_logs=need_tx_logs)

        monkeypatch.setattr(adp_2, "fetch_facts", _slow_f2)
        out_2 = coord_2.run_tick(now_ms=venue_2.server_time_ms)
        assert _reject_reason_from_tick(out_2, coord_2) == "DECISION_TIMESTAMP_OUT_OF_WINDOW"
        assert st_2.get_latest_episode() is None and venue_2.export_raw_trades() == []
    finally:
        st_2.close()

    # U5c: Send-boundary (`_execute_actions`) blocks `EP` when real P5 `sync_open` `AI_REVOKE` lands AFTER `evaluate_step`
    wb_3, d_3, arch_3, dos_3, _h3, ep_3, cond_3, clk_3, sync_3, _hid3 = _make_real_p5_accept(
        tmp_path / "c3", monkeypatch, slot_0=slot_0, accept_put=accept_put, tag="u5c"
    )
    venue_3 = _new_venue_from_p5_contract(cond_3, now_ms=clk_3[0] + 5_000, spot_usd=spot)
    st_3, _adp_3, coord_3 = _new_armed_coord(tmp_path / "x3", venue_3, dos_3)
    try:
        orig_p3, done_3 = st_3.apply_episode_patch, {"v": False}

        def _patch_then_rev(episode_id: str, patch: Any, **kw: Any) -> Any:
            r = orig_p3(episode_id, patch, **kw)
            ph = patch.get("phase") if isinstance(patch, dict) else getattr(patch, "phase", None)
            if not done_3["v"] and ph == contracts.LifecyclePhase.ENTRY:
                done_3["v"] = True
                clk_3[0] = venue_3.server_time_ms
                _emit_real_p5_sync_open_review(wb_3, d_3, ep_3, arch_3, clk_3, sync_3)
            return r

        monkeypatch.setattr(st_3, "apply_episode_patch", _patch_then_rev)
        coord_3.run_tick(now_ms=venue_3.server_time_ms)
        ep_3_out = st_3.get_latest_episode()
        assert ep_3_out is not None and st_3.get_commands(ep_3_out.episode_id) == []
        assert venue_3.export_raw_trades() == [] and ep_3_out.terminal_outcome == "ABORTED_ZERO_FILL"
    finally:
        st_3.close()

    # U5d: `EP` already filled (0.2 BTC) when real P5 `sync_open` emits `AI_REVOKE` before `ES` -> `["EP", "LX"]`, 0 `ES`, exact Oracle PnL
    wb_4, d_4, arch_4, dos_4, _h4, ep_4, cond_4, clk_4, sync_4, _hid4 = _make_real_p5_accept(
        tmp_path / "c4", monkeypatch, slot_0=slot_0, accept_put=accept_put, tag="u5d"
    )
    short_inst, long_inst = str(cond_4["short_instrument"]), str(cond_4["long_instrument"])
    venue_4 = _new_venue_from_p5_contract(cond_4, now_ms=clk_4[0] + 5_000, spot_usd=spot)
    st_4, adp_4, coord_4 = _new_armed_coord(tmp_path / "x4", venue_4, dos_4, qty_btc="0.2", slice_btc="0.2")
    try:
        orig_s4, done_4 = adp_4.submit_order, {"v": False}

        def _ep_then_rev(**kw: Any) -> contracts.ExchangeOrder:
            vo = orig_s4(**kw)
            if not done_4["v"] and ":EP:" in str(kw.get("label") or ""):
                done_4["v"] = True
                clk_4[0] = venue_4.server_time_ms
                _emit_real_p5_sync_open_review(wb_4, d_4, ep_4, arch_4, clk_4, sync_4)
            return vo

        monkeypatch.setattr(adp_4, "submit_order", _ep_then_rev)
        exec_p2._drive(coord_4, venue_4, short_inst, long_inst, n=6, dt_ms=500)
        ep_4_done = st_4.get_latest_episode()
        assert ep_4_done is not None and ep_4_done.phase == contracts.LifecyclePhase.CASHFLOW_SETTLED
        assert ep_4_done.terminal_outcome == "EARLY_FLAT_SETTLED"
        assert [c.purpose for c in st_4.get_commands(ep_4_done.episode_id)] == ["EP", "LX"]
        assert not venue_4.get_positions()
        oracle_4 = sim.IndependentSettlementOracle(initial_balance_btc=D("1.0"), trades=venue_4.export_raw_trades())
        assert D(st_4.rebuild_episode_ledger(ep_4_done.episode_id)["accounting"]["episode_total_realized_pnl_btc"]) == oracle_4.expected_episode_realized_pnl_btc
    finally:
        st_4.close()


# ---------------------------------------------------------------------------
# U6: Entry Re-Evaluation Bridge for Stale (>180s) Original Contract
# ---------------------------------------------------------------------------


def test_u6_entry_revalidation_bridge_preserves_identity_and_security(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "k-u6-env")
    monkeypatch.setenv("ASTRA_PLAYER_USAGE_LEDGER", str(tmp_path / "u6.json"))
    slot_0, _exp_ms, spot, _rows, _cd = p5_e2e._nov26_27nov26_chain()
    accept_put = p5_e2e._strict_p5_reply_text("## Accept / Put", short_strike=60_000, long_strike=58_000, require_outside_at_t0=True)

    wb, d_obj, arch, dos, hand_dir, ep_dir, cond, clk, sync_fn, hand_id = _make_real_p5_accept(
        tmp_path / "p5", monkeypatch, slot_0=slot_0, accept_put=accept_put, tag="u6"
    )
    short_inst, long_inst = str(cond["short_instrument"]), str(cond["long_instrument"])
    orig_dec = json.loads((hand_dir / "decision.json").read_text(encoding="utf-8"))
    orig_link = json.loads((hand_dir / "tracking" / "link.json").read_text(encoding="utf-8"))
    orig_decided_ms = int(orig_dec["decided_at_ms"])
    orig_link_ms = int(orig_link["recorded_at_ms"])

    # Advance 45 minutes (> 180s global TTL) while spot remains outside short strike ($60,000)
    stale_now = orig_link_ms + 45 * M
    clk[0] = stale_now
    sync_fn()
    wb.refresh(now_ms=stale_now, record_daily=False)
    # Provide an offline minute-grid index_path up to stale_now so revalidate_entry runs without network
    entry_at_ms = int(json.loads((ep_dir / "condition.json").read_text(encoding="utf-8"))["entry_at_ms"])
    idx_pts = [
        [t, float(spot)]
        for t in range((entry_at_ms // M + 1) * M, stale_now + 1, M)
    ]
    idx_path = {"index_name": "btc_usd", "expiry_ms": int(cond["expiry_ms"]), "points": idx_pts}

    # 1) Stale hand WITHOUT ENTRY_REVALIDATED is rejected
    venue_1 = _new_venue_from_p5_contract(cond, now_ms=stale_now, spot_usd=spot)
    st_1, _adp_1, coord_1 = _new_armed_coord(tmp_path / "e1", venue_1, dos, qty_btc="0.1", slice_btc="0.1")
    try:
        out_1 = coord_1.run_tick(now_ms=venue_1.server_time_ms)
        assert _reject_reason_from_tick(out_1, coord_1) in {"DECISION_TIMESTAMP_OUT_OF_WINDOW", "LINK_TIMESTAMP_OUT_OF_WINDOW"}
        assert st_1.get_latest_episode() is None and venue_1.export_raw_trades() == []
    finally:
        st_1.close()

    # 2) Formal revalidate_entry appends ENTRY_REVALIDATED without mutating original decision/link timestamps,
    #    then coordinator admits via formal ADMIT -> EP -> ES -> TP_MANAGED, and disarming entry_armed=False keeps TP/REVOKE intact.
    with serving(wb) as wb_url:
        svc = post(wb_url, wb)
        reval = tracking.revalidate_entry(d_obj, hand_dir, svc, archive_root=arch, index_path=idx_path)
    assert reval["ok"] is True and reval["state"] == "ENTRY_REVALIDATED"
    assert json.loads((hand_dir / "decision.json").read_text(encoding="utf-8"))["decided_at_ms"] == orig_decided_ms
    assert json.loads((hand_dir / "tracking" / "link.json").read_text(encoding="utf-8"))["recorded_at_ms"] == orig_link_ms

    p5_parsed = coord_mod.read_hand_p5_state(
        dos, dossier_id=hand_dir.parent.parent.name, hand_id=hand_id, p5_episode_id=ep_dir.name
    )
    assert p5_parsed is not None
    assert p5_parsed.entry_revalidated_at_ms == stale_now
    assert p5_parsed.entry_revalidated_hand_id == hand_id

    # Mismatched contract / hand identity on P5DecisionContract must fail freshness bridge
    bad_hand_c = contracts.P5DecisionContract(
        **{**p5_parsed.__dict__, "entry_revalidated_hand_id": "hand_other"}
    )
    assert policy.check_new_exposure_permission(
        purpose="ADMIT",
        p5_contract=bad_hand_c,
        episode=None,
        capital_auth=contracts.CapitalAuthorization(entry_armed=True, budget_cap_btc=D("0.5"), max_qty_btc=D("0.1")),
        effective_budget_btc=D("0.5"),
        writes_permitted=True,
        now_ms=stale_now + 5_000,
        config=contracts.ExecConfig(),
        require_p5_facts=True,
    ).allowed is False

    bad_leg_c = contracts.P5DecisionContract(
        **{**p5_parsed.__dict__, "short_instrument": "BTC-29NOV26-59000-P"}
    )
    assert policy.check_new_exposure_permission(
        purpose="ADMIT",
        p5_contract=bad_leg_c,
        episode=None,
        capital_auth=contracts.CapitalAuthorization(entry_armed=True, budget_cap_btc=D("0.5"), max_qty_btc=D("0.1")),
        effective_budget_btc=D("0.5"),
        writes_permitted=True,
        now_ms=stale_now + 5_000,
        config=contracts.ExecConfig(),
        require_p5_facts=True,
    ).allowed is False

    # Revalidation itself expired (>180s) at ADMIT -> rejected
    assert policy.check_new_exposure_permission(
        purpose="ADMIT",
        p5_contract=p5_parsed,
        episode=None,
        capital_auth=contracts.CapitalAuthorization(entry_armed=True, budget_cap_btc=D("0.5"), max_qty_btc=D("0.1")),
        effective_budget_btc=D("0.5"),
        writes_permitted=True,
        now_ms=stale_now + 185_000,
        config=contracts.ExecConfig(),
        require_p5_facts=True,
    ).allowed is False

    # Revalidation expiring between ADMIT and EP pre-send check -> EP halted, 0 orders
    venue_exp = _new_venue_from_p5_contract(cond, now_ms=stale_now + 179_000, spot_usd=spot)
    st_exp, _adp_exp, coord_exp = _new_armed_coord(tmp_path / "e_exp", venue_exp, dos, qty_btc="0.1", slice_btc="0.1")
    try:
        orig_create = st_exp.create_episode
        slipped = {"done": False}

        def _slip_after_admit(ep_obj: Any) -> Any:
            r = orig_create(ep_obj)
            if not slipped["done"]:
                slipped["done"] = True
                venue_exp.advance_time(3_000)
                exec_p2._refresh_books(venue_exp, short_inst, long_inst)
            return r

        monkeypatch.setattr(st_exp, "create_episode", _slip_after_admit)
        monkeypatch.setattr(coord_exp, "_refresh_now_ms", lambda _cur, **_kw: venue_exp.server_time_ms)
        coord_exp.run_tick(now_ms=venue_exp.server_time_ms)
        coord_exp.run_tick(now_ms=venue_exp.server_time_ms)
        ep_exp = st_exp.get_latest_episode()
        assert ep_exp is not None and ep_exp.entry_halted is True and ep_exp.entry_closed is True
        assert ep_exp.entry_halt_reason in {"DECISION_TIMESTAMP_OUT_OF_WINDOW", "LINK_TIMESTAMP_OUT_OF_WINDOW"}
        assert ep_exp.entry_closure_reason == f"ABORTED_ZERO_FILL:{ep_exp.entry_halt_reason}"
        assert st_exp.get_commands(ep_exp.episode_id) == []
        assert venue_exp.export_raw_trades() == []
    finally:
        st_exp.close()

    # Happy path with fresh ENTRY_REVALIDATED -> EP -> ES -> TP_MANAGED, then disarm prevents replay/spillover while keeping TP alive
    venue_2 = _new_venue_from_p5_contract(cond, now_ms=stale_now + 5_000, spot_usd=spot)
    st_2, _adp_2, coord_2 = _new_armed_coord(tmp_path / "e2", venue_2, dos, qty_btc="0.1", slice_btc="0.1")
    try:
        exec_p2._drive(coord_2, venue_2, short_inst, long_inst, n=4, dt_ms=50)
        ep_2 = st_2.get_latest_episode()
        assert ep_2 is not None and ep_2.phase == contracts.LifecyclePhase.TP_MANAGED
        assert ep_2.entry_closure_reason == "FULL_TARGET_FILLED"
        assert [c.purpose for c in st_2.get_commands(ep_2.episode_id)] == ["EP", "ES", "TP"]
        assert st_2.has_consumed_hand(hand_id) is True

        # Disarm entry_armed=False after entry_closed=True -> resting TP remains OPEN and no new hand can enter
        disarm_res = coord_2.handle_capital_policy(
            {"entry_armed": False, "budget_cap_btc": "0.50", "max_qty_btc": "0.1", "operator": "u6_disarm"},
            now_ms=venue_2.server_time_ms,
        )
        assert disarm_res["entry_armed"] is False
        assert any(c.purpose == "TP" and c.status == contracts.CommandStatus.OPEN for c in st_2.get_commands(ep_2.episode_id))

        # Re-arming NEVER re-opens already-consumed hand even with ENTRY_REVALIDATED
        tp_cmd = next(c for c in st_2.get_commands(ep_2.episode_id) if c.purpose == "TP" and c.status == contracts.CommandStatus.OPEN)
        venue_2.fill_resting_order(tp_cmd.label, D("0.1"), price=tp_cmd.limit_price, liquidity="M")
        exec_p2._drive(coord_2, venue_2, short_inst, long_inst, n=4, dt_ms=200)
        assert st_2.get_episode(ep_2.episode_id).phase == contracts.LifecyclePhase.CASHFLOW_SETTLED
        coord_2.handle_capital_policy(
            {"entry_armed": True, "budget_cap_btc": "0.50", "max_qty_btc": "0.1", "operator": "u6_rearm"},
            now_ms=venue_2.server_time_ms,
            run_tick_after=True,
        )
        assert len(st_2.conn.execute("SELECT episode_id FROM episodes").fetchall()) == 1
    finally:
        st_2.close()

    # 3) CONDITION_TRIGGERED / AI_REVOKE must reject revalidate_entry AND block ADMIT even with prior ENTRY_REVALIDATED
    _seed_m1_trigger(wb, d_obj, ep_dir, clk, sync_fn)
    with serving(wb) as wb_url:
        svc = post(wb_url, wb)
        reval_trig = tracking.revalidate_entry(d_obj, hand_dir, svc, archive_root=arch, index_path=idx_path)
    assert reval_trig["ok"] is False and reval_trig["state"] == "REVALIDATE_REJECTED"
    assert reval_trig["reason"] == "condition_triggered"

    venue_3 = _new_venue_from_p5_contract(cond, now_ms=stale_now + 10_000, spot_usd=spot)
    st_3, _adp_3, coord_3 = _new_armed_coord(tmp_path / "e3", venue_3, dos, qty_btc="0.1", slice_btc="0.1")
    try:
        out_3 = coord_3.run_tick(now_ms=venue_3.server_time_ms)
        assert _reject_reason_from_tick(out_3, coord_3) == "P5_PRE_ENTRY_CONDITION_TRIGGERED"
        assert st_3.get_latest_episode() is None and venue_3.export_raw_trades() == []
    finally:
        st_3.close()

    _emit_real_p5_sync_open_review(wb, d_obj, ep_dir, arch, clk, sync_fn, reply_text=REVOKE_REPLY_TEXT)
    venue_4 = _new_venue_from_p5_contract(cond, now_ms=stale_now + 12_000, spot_usd=spot)
    st_4, _adp_4, coord_4 = _new_armed_coord(tmp_path / "e4", venue_4, dos, qty_btc="0.1", slice_btc="0.1")
    try:
        out_4 = coord_4.run_tick(now_ms=venue_4.server_time_ms)
        assert _reject_reason_from_tick(out_4, coord_4) == "P5_ALREADY_REVOKED"
        assert st_4.get_latest_episode() is None and venue_4.export_raw_trades() == []
    finally:
        st_4.close()


def test_u6_cli_revalidate_entry_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("LLM_API_KEY", "k-u6cli")
    monkeypatch.setenv("ASTRA_PLAYER_USAGE_LEDGER", str(tmp_path / "u6cli.json"))
    slot_0, _exp_ms, spot, _rows, _cd = p5_e2e._nov26_27nov26_chain()
    accept_put = p5_e2e._strict_p5_reply_text("## Accept / Put", short_strike=60_000, long_strike=58_000, require_outside_at_t0=True)
    wb, _d, arch, dos, hand_dir, ep_dir, cond, clk, sync_fn, hand_id = _make_real_p5_accept(
        tmp_path / "cli", monkeypatch, slot_0=slot_0, accept_put=accept_put, tag="u6cli"
    )
    stale_now = clk[0] + 20 * M
    clk[0] = stale_now
    sync_fn()
    wb.refresh(now_ms=stale_now, record_daily=False)
    entry_at_ms = int(json.loads((ep_dir / "condition.json").read_text(encoding="utf-8"))["entry_at_ms"])
    idx_pts = [[t, float(spot)] for t in range((entry_at_ms // M + 1) * M, stale_now + 1, M)]
    monkeypatch.setattr(dossier.archive.Deribit, "index_path", lambda _self: idx_pts)
    monkeypatch.setenv("ASTRA_AI_SHADOW_LOCK_TOKEN", wb.config.shadow_service_token)
    with serving(wb) as wb_url:
        rc, out, _err = _run_tracking_cli(
            monkeypatch,
            capsys,
            argv=[
                "--url", wb_url,
                "revalidate-entry",
                "--root", str(dos),
                "--dossier", hand_dir.parent.parent.name,
                "--hand", hand_id,
                "--archive", str(arch),
            ],
            clock_box=clk,
        )
    assert rc == 0 and isinstance(out, dict) and out["ok"] is True and out["state"] == "ENTRY_REVALIDATED"
