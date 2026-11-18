"""End-to-end tests for the 16:00 UTC scheduled deal, grid checks, debounced hold review, and shadow exit."""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import astra_player_dossier_v1 as dossier
from tools import astra_player_review_v1 as review
from tools import astra_player_tracking_v13 as tracking
from tools import astra_research_archive_v1 as archive
from tools import astra_underwriting_discovery_v20 as discovery
from tools import astra_underwriting_public_quote_v20 as public_quote
from tools import astra_workbench_v20 as workbench
from tools import signal_llm_review
from test_astra_locked_followup import setup
from test_astra_player_tracking_v13 import EXCERPTS, M, RULE, post, serving
from test_astra_workbench_v20 import instruments, native_gate, write_fmz


DAY = dossier.DAY
HOUR = 3_600_000


def _plan_r16(slot_ms: int) -> dict:
    return {
        "plan_id": "daily_r16@2",
        "research": dossier.RESEARCH,
        "mode": "SHADOW_ONLY",
        "candidate_round": "r16",
        "effective_at_ms": slot_ms,
        "expiry_offset_ms": 16 * HOUR,
        "deal_grace_ms": 30 * M,
        "reply_grace_ms": 30 * M,
    }


def _seed_r16_archive(wb, arch_root: Path, slot_ms: int, expiry_ms: int, points: list[list[float]]) -> None:
    arch = archive.Archive(arch_root)
    exp_day = dossier.utc(expiry_ms).date().isoformat()
    slot_day = dossier.utc(slot_ms).date().isoformat()
    # 1. Non-lookahead FMZ band snapshot at cutoff - 4h (12:00 UTC)
    band_ts = slot_ms - 4 * HOUR
    fmz_jsonl = arch_root.parent / "fmz_live.jsonl"
    fmz_row = {
        "snapshot_exported_ms": band_ts - 600_000,
        "source_available_at_ms": band_ts - 540_000,
        "snapshot_exported_utc8": "2026-11-16 20:00:00",
        "factor_snapshot": {
            "anchor": {
                "ready": True,
                "freshness": "FRESH",
                "band_half": 240.0,
                "gex_source_ts_ms": band_ts - 650_000,
            },
            "flow": {"direction": "neutral", "cvd_sum": 12.0},
        },
    }
    fmz_jsonl.write_text(json.dumps(fmz_row) + "\n", encoding="utf-8")
    saved = archive.archive_band_snapshots(arch, fmz_jsonl, now_ms=slot_ms)
    assert saved == 1

    # 2. Structure snapshots before 16:00 UTC
    for t in (slot_ms - 4 * HOUR, slot_ms - 10 * M):
        arch.append_line(
            f"structure/{exp_day}.jsonl",
            {
                "schema": "structure_snapshot@0.1",
                "expiry_ms": expiry_ms,
                "at_ms": t,
                "available_at_ms": t + 30_000,
                "pin_source": "deribit_gex",
                "pin_method": "gamma_peak",
                "pin_status": "ok",
                "expected_move_usd": 1400.0,
                "profile": {
                    "walls": {"pin": 100_000.0},
                    "top": {"oi_put": [{"strike": 98_000.0}], "oi_call": [{"strike": 102_000.0}]},
                },
            },
            at_ms=t + 30_000,
            kind="structure",
        )

    # 3. Archived 16:00 UTC put & call cases + r16 native level cards
    case_dir = arch_root / "cases" / slot_day
    case_dir.mkdir(parents=True, exist_ok=True)
    for item in wb._current_view["candidates"]:
        snap = item["snapshot"]
        case = wb.case_file(snap["snapshot_id"])
        side = snap["contract"]["side"]
        # Refresh built_at_ms & L3 walls to match slot_ms (16:00 UTC)
        case["build"]["built_at_ms"] = slot_ms + 60_000
        dossier.player.entry(case, "L3.expiry_walls").update(
            values={
                "expiry_ms": expiry_ms,
                "pin_source": "deribit_gex",
                "pin_method": "gamma_peak",
                "pin_status": "ok",
                "walls": {"pin": 100_000.0},
                "top": {"oi_put": [{"strike": 98_000.0}], "oi_call": [{"strike": 102_000.0}]},
            },
            asof_ms=slot_ms + 60_000,
            quality="ok",
        )
        dossier.player.entry(case, "L5.volume_price").update(
            values={"micro_flow": {
                "combined": {"direction": "neutral", "data_ready": True},
                "fast_4h": {"direction": "neutral", "cvd_sum": 15.0, "momentum_return_pct": 0.05},
                "slow_12h": {"direction": "neutral", "cvd_sum": 18.0, "momentum_return_pct": 0.10},
            }},
            asof_ms=slot_ms + 60_000,
            quality="ok",
        )
        raw_gz = gzip.compress(json.dumps(case, ensure_ascii=False).encode("utf-8"))
        (case_dir / f"16_{side}_{case['case_id']}.json.gz").write_bytes(raw_gz)
        contract = dossier.player.values(case, "L1.contract")
        card = dossier.levels.build_card(
            expiry_ms=expiry_ms,
            rnd="r16",
            side=side,
            read_at_ms=slot_ms + 60_000,
            walls=dossier.player.values(case, "L3.expiry_walls"),
            points=points,
            case=case,
            contract=contract,
            snapshots=[],
        )
        arch.append_line(f"levels/{exp_day}.jsonl", card, at_ms=slot_ms + 60_000, kind="level")

    # 4. Archived 1-min index path
    paths_dir = arch_root / "paths"
    paths_dir.mkdir(parents=True, exist_ok=True)
    (paths_dir / f"{exp_day}.json.gz").write_bytes(gzip.compress(json.dumps({"points": points}).encode("utf-8")))


def _mock_initial_llm(accept_side: str = "Call"):
    counter = [0]

    def _call(api_key, model, request_body, timeout_s, endpoint=None):
        del api_key, model, request_body, timeout_s, endpoint
        counter[0] += 1
        public_lines = [
            "Put 侧路径核查：" + EXCERPTS["put"],
            EXCERPTS["put_counter"],
            "Call 侧路径核查：" + EXCERPTS["call"],
            EXCERPTS["call_counter"],
            EXCERPTS["comparison"],
            f"### Accept / {accept_side}",
            "",
            RULE["historical_facts"],
            RULE["accepted_conditions"],
            RULE["confirmation_excerpt"],
        ]
        ann = {
            **EXCERPTS,
            "depends_on_ard": False,
            "condition_plan": {
                **RULE,
                "check_interval_ms": 5 * M,
            },
        }
        content = "\n".join(public_lines) + "\n\n<ANNOTATION_JSON>\n" + json.dumps(ann, ensure_ascii=False) + "\n</ANNOTATION_JSON>\n"
        return {
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 80},
            "_http_status": 200,
        }

    return counter, _call


def test_r16_prewarm_and_rejection_of_mismatched_schedule(tmp_path):
    wb, _, now, first = setup(tmp_path / "tracker")
    expiry = first["contract"]["expiry_ms"]
    slot_r16 = expiry - 16 * HOUR
    plan_r16 = _plan_r16(slot_r16)
    arch_root = tmp_path / "archive"
    for d_name in ("cases", "levels", "structure", "band_snapshots"):
        (arch_root / d_name).mkdir(parents=True, exist_ok=True)

    # Mismatched offset (14:00 round paired with 16h offset) must be rejected
    bad_plan = {**plan_r16, "candidate_round": "r2"}
    with pytest.raises(ValueError, match="invalid daily r2 schedule"):
        dossier.schedule(bad_plan, slot_r16)

    wb.config.shadow_experiment = True
    wb.config.shadow_dossier_root = tmp_path / "dossier"
    wb.config.shadow_service_token = "tok"
    with serving(wb) as base:
        svc = tracking.local_service(base, "tok")
        prewarm_ts = slot_r16 - 5 * M
        report = tracking.prewarm(
            plan_r16,
            arch_root,
            svc,
            clock=lambda: prewarm_ts,
            public=lambda method, **kw: [
                {"instrument_name": "BTC-TEST-C", "expiration_timestamp": expiry, "settlement_currency": "BTC", "is_active": True}
            ],
        )
        assert report["state"] == "PREWARM"
        assert report["candidate_round"] == "r16"
        assert report["scheduled_at_ms"] == slot_r16
        assert report["archive_readable"] is True
        assert report["band_snapshots_readable"] is True


def test_full_closed_loop_r16_deal_grid_debounce_revoke_and_settlement(tmp_path, monkeypatch):
    # Start setup so expiry is next-day 08:00 UTC and now can sit at 16:00 UTC (-16h)
    wb, snapshots, now, first = setup(tmp_path / "tracker")
    expiry = first["contract"]["expiry_ms"]
    slot_r16 = expiry - 16 * HOUR
    spot_box = [100_000.0]
    metas = {row["instrument_name"]: row for row in instruments()}

    def dynamic_quote(short_instrument, long_instrument, quantity_btc=1.0, **_kw):
        now_ms = now[0]
        idx = spot_box[0]

        def fetch(path, params):
            name = params["instrument_name"]
            if path == "public/get_instrument":
                return dict(metas[name])
            return {
                "instrument_name": name,
                "state": "open",
                "timestamp": now_ms,
                "index_price": idx,
                "bids": [[0.007 if name == short_instrument else 0.002, 1.0]],
                "asks": [[0.008 if name == short_instrument else 0.003, 1.0]],
            }

        return public_quote.capture_candidate(
            short_instrument, long_instrument, quantity_btc, transport=fetch, clock_ms=lambda: now_ms
        )

    def dynamic_discovery(*, now_ms):
        rows = [{**r, "delta_observed_at_ms": now_ms - 1000} for r in instruments()]
        return discovery.discover(rows, spot_box[0], now_ms, quantity_btc=1.0, width_usd=2000.0)

    def write_fmz_with_flow(ts_ms: int):
        row = write_fmz(snapshots, native_gate(ts=ts_ms - 1000), now=ts_ms)
        row["factor_snapshot"]["flow"]["micro_flow"] = {
            "fast_4h": {"data_ready": True, "direction": "neutral", "cvd_sum": 15.0, "momentum_return_pct": 0.05},
            "slow_12h": {"data_ready": True, "direction": "neutral", "cvd_sum": 18.0, "momentum_return_pct": 0.10},
            "combined": {"data_ready": True, "direction": "neutral", "ready_horizons": ["4h", "12h"]},
        }
        snapshots.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    def advance_market(ts_ms: int, idx: float | None = None):
        now[0] = ts_ms
        if idx is not None:
            spot_box[0] = idx
        write_fmz_with_flow(ts_ms)
        wb.refresh(now_ms=ts_ms, record_daily=False)

    wb.discovery_capture = dynamic_discovery
    wb.quote_capture = dynamic_quote
    wb.config.shadow_experiment = True
    wb.config.shadow_dossier_root = tmp_path / "dossier"
    wb.config.shadow_service_token = "tok"
    wb.config.frontend_dir = Path(__file__).resolve().parents[1] / "deploy/astra_workbench_v3"
    advance_market(slot_r16, 100_000.0)

    arch_root = tmp_path / "archive"
    points = [[t, 100_000.0] for t in range(slot_r16 - DAY, slot_r16 + M + 1, M)]
    _seed_r16_archive(wb, arch_root, slot_r16, expiry, points)

    contracts_proof = []
    for item in wb._current_view["candidates"]:
        c = item["snapshot"]["contract"]
        contracts_proof.extend([
            {"instrument_name": c["short_instrument"], "expiration_timestamp": expiry, "settlement_currency": "BTC", "is_active": True},
            {"instrument_name": c["long_instrument"], "expiration_timestamp": expiry, "settlement_currency": "BTC", "is_active": True},
        ])

    monkeypatch.setattr(
        dossier,
        "public",
        lambda method, **kw: contracts_proof if method == "get_instruments" else {"timestamps": [], "prices": []},
    )
    monkeypatch.setattr(
        dossier.archive,
        "Deribit",
        lambda *a, **kw: SimpleNamespace(index_path=lambda *x, **y: points),
    )

    plan_r16 = _plan_r16(slot_r16)
    d = dossier.Dossiers(wb.config.shadow_dossier_root, clock=lambda: now[0])
    init_calls, init_transport = _mock_initial_llm("Call")

    with serving(wb) as base:
        svc = post(base, wb)
        advance_market(slot_r16 + 2 * M, 100_000.0)
        dealt = tracking.auto_deal(
            d,
            plan_r16,
            arch_root,
            svc,
            llm_config={"api_key": "k", "model": "deepseek-v4.1-test", "transport": init_transport},
        )
        assert init_calls[0] == 1
        assert len(dealt) == 1
        assert dealt[0]["decision"] == "Accept"
        assert dealt[0]["link"]["state"] == "SHADOW_LOCK_CREATED"

        hand = next((wb.config.shadow_dossier_root / "dossiers").glob("Deribit_BTC_*/hands/hand_*"))
        lce_id = json.loads((hand / "tracking" / "link.json").read_text(encoding="utf-8"))["t0"]["tracker_episode_id"]

        # Step 2: Benign 5m fixed-grid checks with ZERO LLM calls
        hold_calls = [0]
        hold_verdict = ["HOLD"]

        def hold_transport(api_key, model, request_body, timeout_s, endpoint=None):
            del api_key, model, request_body, timeout_s, endpoint
            hold_calls[0] += 1
            v = hold_verdict[0]
            content = (
                f"### {v}\n\n"
                + ("原入场缓冲仍成立，维持持有。" if v == "HOLD" else "价格已突破并持续处于 Call 短腿责任区内，建议撤销影子承保。")
                + "\n<HOLD_REVIEW_JSON>"
                + json.dumps({
                    "verdict": v,
                    "affected_conditions": ["责任边界：102,000 Call 短腿"],
                    "public_reply": "继续维持" if v == "HOLD" else "边界规则已破坏且持续承压，建议 REVOKE。",
                    "key_facts": ["CONDITION_CHECK"],
                }, ensure_ascii=False)
                + "</HOLD_REVIEW_JSON>"
            )
            return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}], "usage": {"total_tokens": 90}, "_http_status": 200}

        for step in range(1, 4):
            t_cur = slot_r16 + 2 * M + step * 5 * M
            start_m = (points[-1][0] + M) if points else t_cur - 4 * M
            for minute in range(start_m, t_cur + 1, M):
                points.append([minute, 100_050.0])
            advance_market(t_cur, 100_050.0)
            res = tracking.sync(
                d, hand, svc, check_due=True, archive_root=arch_root, run_review=True,
                llm_config={"api_key": "k", "model": "deepseek-v4.1-hold", "transport": hold_transport},
            )
            assert hold_calls[0] == 0
            assert all(c["kind"] == "CONDITION_NOT_TRIGGERED" for c in res["checks"])

        # Step 3: Trigger B (buffer deterioration from ~2.9% down to ~0.97%) without breach -> waits inside 12m debounce first
        t_trig = slot_r16 + 22 * M
        for minute in range(slot_r16 + 18 * M, t_trig + 1, M):
            points.append([minute, 102_000.0])
        for step in range(2):
            advance_market(t_trig + step * 60_000, 102_000.0)
        res_queued = tracking.sync(
            d, hand, svc, check_due=True, archive_root=arch_root, run_review=True,
            llm_config={"api_key": "k", "model": "deepseek-v4.1-hold", "transport": hold_transport},
        )
        assert res_queued["hold_review"]["action"] == "QUEUED"
        assert hold_calls[0] == 0

        # Repeated B sample within 12m debounce still waits -> 0 calls
        points.extend([[t_trig + i * M, 102_020.0] for i in range(1, 6)])
        advance_market(t_trig + 5 * M, 102_020.0)
        res_still_queued = tracking.sync(
            d, hand, svc, check_due=True, archive_root=arch_root, run_review=True,
            llm_config={"api_key": "k", "model": "deepseek-v4.1-hold", "transport": hold_transport},
        )
        assert res_still_queued["hold_review"]["action"] == "QUEUED"
        assert hold_calls[0] == 0

        # After 12m debounce elapses -> coalesced single LLM HOLD call
        t_flush = t_trig + 13 * M
        points.extend([[m, 102_040.0] for m in range(t_trig + 6 * M, t_flush + 1, M)])
        advance_market(t_flush, 102_040.0)
        res_hold = tracking.sync(
            d, hand, svc, check_due=True, archive_root=arch_root, run_review=True,
            llm_config={"api_key": "k", "model": "deepseek-v4.1-hold", "transport": hold_transport},
        )
        assert res_hold["hold_review"]["action"] == "REVIEWED"
        assert res_hold["hold_review"]["verdict"] == "HOLD"
        assert hold_calls[0] == 1

        # Step 4: Spot enters Call short interior (>103,000) and remains unreturned -> CONDITION_TRIGGERED priority A bypasses cooldown!
        t_breach = t_flush + 10 * M
        for m in range(t_flush + M, t_breach + 1, M):
            points.append([m, 103_450.0])
        hold_verdict[0] = "REVOKE"
        advance_market(t_breach, 103_450.0)
        res_revoke = tracking.sync(
            d, hand, svc, check_due=True, archive_root=arch_root, run_review=True,
            llm_config={"api_key": "k", "model": "deepseek-v4.1-hold", "transport": hold_transport},
        )
        assert any(c["kind"] == "CONDITION_TRIGGERED" for c in res_revoke["checks"])
        assert res_revoke["hold_review"]["action"] == "REVIEWED"
        assert res_revoke["hold_review"]["verdict"] == "REVOKE"
        assert hold_calls[0] == 2

        # Step 5: Verify no further AI review calls after REVOKE, while fixed-grid & settlement continue cleanly
        t_after = t_breach + 20 * M
        for m in range(t_breach + M, t_after + 1, M):
            points.append([m, 103_500.0])
        advance_market(t_after, 103_500.0)
        res_after = tracking.sync(
            d, hand, svc, check_due=True, archive_root=arch_root, run_review=True,
            llm_config={"api_key": "k", "model": "deepseek-v4.1-hold", "transport": hold_transport},
        )
        assert hold_calls[0] == 2
        assert res_after["hold_review"]["action"] == "ALREADY_REVOKED"

        # Step 6: Inspect enriched Workbench v3 projections BEFORE expiry settlement
        audit = tracking.audit_record(hand, now[0], {lce_id: "active"}, detail=True)
        assert len(audit["hold_reviews"]) == 2
        assert audit["shadow_management"]["condition_triggered_at_ms"] is not None
        assert audit["shadow_management"]["ai_revoked_at_ms"] is not None
        assert audit["shadow_management"]["exit_status"] == "SIMULATED_BUY_BACK_SHORT"
        assert audit["shadow_management"]["buyback_cost_btc"] == pytest.approx(0.008)

        list_view = wb.locked_candidate_list()
        ai_ep = next(ep for ep in list_view["episodes"] if ep["episode_id"] == lce_id)
        assert ai_ep["ai_dossier_management"]["latest_verdict"] == "REVOKE"
        detail_view = wb.locked_candidate_detail(lce_id)
        assert detail_view["ai_dossier_management"]["condition_triggered"] is True
        assert detail_view["ai_dossier_management"]["latest_review"]["model"] == "deepseek-v4.1-hold"

        # Step 7: Settle official outcome at expiry -> verifies hold_to_expiry vs managed_cashflow comparison
        now[0] = expiry + M
        for m in range(t_after + M, expiry + 1, M):
            points.append([m, 104_200.0])
        d_settle = dossier.Dossiers(wb.config.shadow_dossier_root, clock=lambda: now[0])
        outcome_body = d_settle.settle(expiry, 104_200.0, dict(index_name="btc_usd", expiry_ms=expiry, points=points))
        d_settle.save_outcome(outcome_body, dict(source="test_deribit_delivery", price=104_200.0))
        audit_settled = tracking.audit_record(hand, now[0], {lce_id: "expired"}, detail=True)
        assert outcome_body["actual_execution_net_btc"] is None
        t0_credit = audit_settled["outcome"]["t0_net_credit_after_entry_fee_btc"]
        mc = audit_settled["outcome"]["managed_cashflow"]
        assert mc["mode"] == "SHADOW_MANAGED"
        assert mc["short_buyback_cost_btc"] == pytest.approx(0.008)
        assert mc["long_settlement_credit_btc"] == pytest.approx(0.0)
        assert mc["managed_net_btc"] == pytest.approx(round(t0_credit - mc["short_buyback_cost_btc"] - mc["fees_btc"]["buyback"] + mc["long_settlement_credit_btc"], 8))
        assert mc["delta_btc"] == pytest.approx(round(mc["managed_net_btc"] - audit_settled["outcome"]["hold_to_expiry_shadow_net_btc"], 8))


def test_unpriced_revoke_and_llm_failure_backoff_keeps_grid_monitoring_alive(tmp_path, monkeypatch):
    """Verify quote-less REVOKE records REVOKE_RECORDED_NO_QUOTE without fabricating stop-loss PnL,
    and LLM failure records HOLD_REVIEW_FAILED while continuing fixed-grid checks and recovering cleanly."""
    wb, snapshots, now, first = setup(tmp_path / "tracker")
    expiry = first["contract"]["expiry_ms"]
    slot_r16 = expiry - 16 * HOUR
    spot_box = [100_000.0]
    empty_short_book = [False]
    metas = {row["instrument_name"]: row for row in instruments()}

    def dynamic_quote(short_instrument, long_instrument, quantity_btc=1.0, **_kw):
        now_ms = now[0]
        idx = spot_box[0]

        def fetch(path, params):
            name = params["instrument_name"]
            if path == "public/get_instrument":
                return dict(metas[name])
            asks = [] if (empty_short_book[0] and name == short_instrument) else [[0.008 if name == short_instrument else 0.003, 1.0]]
            return {
                "instrument_name": name,
                "state": "open",
                "timestamp": now_ms,
                "index_price": idx,
                "bids": [[0.007 if name == short_instrument else 0.002, 1.0]],
                "asks": asks,
            }

        return public_quote.capture_candidate(
            short_instrument, long_instrument, quantity_btc, transport=fetch, clock_ms=lambda: now_ms
        )

    def dynamic_discovery(*, now_ms):
        rows = [{**r, "delta_observed_at_ms": now_ms - 1000} for r in instruments()]
        return discovery.discover(rows, spot_box[0], now_ms, quantity_btc=1.0, width_usd=2000.0)

    def write_fmz_with_flow(ts_ms: int):
        row = write_fmz(snapshots, native_gate(ts=ts_ms - 1000), now=ts_ms)
        row["factor_snapshot"]["flow"]["micro_flow"] = {
            "fast_4h": {"data_ready": True, "direction": "neutral", "cvd_sum": 15.0, "momentum_return_pct": 0.05},
            "slow_12h": {"data_ready": True, "direction": "neutral", "cvd_sum": 18.0, "momentum_return_pct": 0.10},
            "combined": {"data_ready": True, "direction": "neutral", "ready_horizons": ["4h", "12h"]},
        }
        snapshots.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    def advance_market(ts_ms: int, idx: float | None = None):
        now[0] = ts_ms
        if idx is not None:
            spot_box[0] = idx
        write_fmz_with_flow(ts_ms)
        wb.refresh(now_ms=ts_ms, record_daily=False)

    wb.discovery_capture = dynamic_discovery
    wb.quote_capture = dynamic_quote
    wb.config.shadow_experiment = True
    wb.config.shadow_dossier_root = tmp_path / "dossier"
    wb.config.shadow_service_token = "tok"
    wb.config.frontend_dir = Path(__file__).resolve().parents[1] / "deploy/astra_workbench_v3"
    advance_market(slot_r16, 100_000.0)

    arch_root = tmp_path / "archive"
    points = [[t, 100_000.0] for t in range(slot_r16 - DAY, slot_r16 + M + 1, M)]
    _seed_r16_archive(wb, arch_root, slot_r16, expiry, points)
    contracts_proof = []
    for item in wb._current_view["candidates"]:
        c = item["snapshot"]["contract"]
        contracts_proof.extend([
            {"instrument_name": c["short_instrument"], "expiration_timestamp": expiry, "settlement_currency": "BTC", "is_active": True},
            {"instrument_name": c["long_instrument"], "expiration_timestamp": expiry, "settlement_currency": "BTC", "is_active": True},
        ])
    monkeypatch.setattr(
        dossier, "public",
        lambda method, **kw: contracts_proof if method == "get_instruments" else {"timestamps": [], "prices": []},
    )
    monkeypatch.setattr(dossier.archive, "Deribit", lambda *a, **kw: SimpleNamespace(index_path=lambda *x, **y: points))

    d = dossier.Dossiers(wb.config.shadow_dossier_root, clock=lambda: now[0])
    _, init_transport = _mock_initial_llm("Call")

    with serving(wb) as base:
        svc = post(base, wb)
        advance_market(slot_r16 + 2 * M, 100_000.0)
        dealt = tracking.auto_deal(
            d, _plan_r16(slot_r16), arch_root, svc,
            llm_config={"api_key": "k", "model": "deepseek-v4.1-test", "transport": init_transport},
        )
        assert dealt[0]["decision"] == "Accept"
        hand = next((wb.config.shadow_dossier_root / "dossiers").glob("Deribit_BTC_*/hands/hand_*"))
        lce_id = json.loads((hand / "tracking" / "link.json").read_text(encoding="utf-8"))["t0"]["tracker_episode_id"]

        # Trigger Category A breach + make short_book.asks empty -> fail first LLM call, recover on retry without quote
        should_fail = [True]

        def flaky_transport(api_key, model, request_body, timeout_s, endpoint=None):
            del api_key, model, request_body, timeout_s, endpoint
            if should_fail[0]:
                raise TimeoutError("upstream_timeout")
            content = (
                "### REVOKE\n\n短腿已入区且未回收，建议撤销。\n<HOLD_REVIEW_JSON>"
                + json.dumps({
                    "verdict": "REVOKE",
                    "affected_conditions": ["责任边界：103,000 Call 短腿"],
                    "public_reply": "短腿已入区，建议 REVOKE（当前无可靠卖腿报价）。",
                    "key_facts": ["CONDITION_TRIGGERED"],
                }, ensure_ascii=False)
                + "</HOLD_REVIEW_JSON>"
            )
            return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}], "usage": {}, "_http_status": 200}

        t_breach = slot_r16 + 15 * M
        for m in range(points[-1][0] + M, t_breach + 1, M):
            points.append([m, 103_400.0])
        empty_short_book[0] = True
        advance_market(t_breach, 103_400.0)
        # Keep short_book empty by writing latest point directly from dynamic_quote (which skips full snapshot quote if asks empty;
        # wait: tracker samples even if partial via allow_partial=True during _refresh_locked_candidates)
        res_fail = tracking.sync(
            d, hand, svc, check_due=True, archive_root=arch_root, run_review=True,
            llm_config={"api_key": "k", "model": "deepseek-v4.1-hold", "transport": flaky_transport},
        )
        assert any(c["kind"] == "CONDITION_TRIGGERED" for c in res_fail["checks"])
        assert res_fail["hold_review"]["action"] == "FAILED"

        # Monitoring keeps checking on next 5m grid tick even while backoff active, then recovers after backoff (>5m)
        should_fail[0] = False
        t_recover = t_breach + 6 * M
        for m in range(points[-1][0] + M, t_recover + 1, M):
            points.append([m, 103_500.0])
        advance_market(t_recover, 103_500.0)
        res_ok = tracking.sync(
            d, hand, svc, check_due=True, archive_root=arch_root, run_review=True,
            llm_config={"api_key": "k", "model": "deepseek-v4.1-hold", "transport": flaky_transport},
        )
        assert res_ok["hold_review"]["action"] == "REVIEWED"
        assert res_ok["hold_review"]["verdict"] == "REVOKE"

        audit = tracking.audit_record(hand, now[0], {lce_id: "active"}, detail=True)
        assert audit["shadow_management"]["ai_revoked_at_ms"] is not None
        assert audit["shadow_management"]["exit_status"] == "REVOKE_NO_QUOTE"
        assert any(r.get("status") == "FAILED" for r in audit["hold_reviews"])

        # Official expiry settlement reports UNPRICED_REVOKE with short_buyback_cost_btc=None (never fabricates PnL)
        now[0] = expiry + M
        for m in range(points[-1][0] + M, expiry + 1, M):
            points.append([m, 103_900.0])
        d_settle = dossier.Dossiers(wb.config.shadow_dossier_root, clock=lambda: now[0])
        outcome_body = d_settle.settle(expiry, 103_900.0, dict(index_name="btc_usd", expiry_ms=expiry, points=points))
        d_settle.save_outcome(outcome_body, dict(source="test_deribit_delivery", price=103_900.0))
        audit_settled = tracking.audit_record(hand, now[0], {lce_id: "expired"}, detail=True)
        assert audit_settled["outcome"]["managed_cashflow"]["mode"] == "UNPRICED_REVOKE"
        assert audit_settled["outcome"]["managed_cashflow"]["short_buyback_cost_btc"] is None


def test_ard_depends_category_c_and_priority_a_backfill_safety(tmp_path, monkeypatch):
    """Verify depends_on_ard=True triggers REVIEW_REQUIRED + Category C on live check ticks,
    and CONDITION_TRIGGERED isn't masked by subsequent CONDITION_NOT_TRIGGERED backfill or earlier inflight completion."""
    wb, snapshots, now, first = setup(tmp_path / "tracker")
    expiry = first["contract"]["expiry_ms"]
    slot_r16 = expiry - 16 * HOUR
    spot_box = [100_000.0]
    metas = {row["instrument_name"]: row for row in instruments()}

    def dynamic_quote(short_instrument, long_instrument, quantity_btc=1.0, **_kw):
        now_ms = now[0]
        idx = spot_box[0]

        def fetch(path, params):
            name = params["instrument_name"]
            if path == "public/get_instrument":
                return dict(metas[name])
            return {
                "instrument_name": name, "state": "open", "timestamp": now_ms, "index_price": idx,
                "bids": [[0.007 if name == short_instrument else 0.002, 1.0]],
                "asks": [[0.008 if name == short_instrument else 0.003, 1.0]],
            }

        return public_quote.capture_candidate(short_instrument, long_instrument, quantity_btc, transport=fetch, clock_ms=lambda: now_ms)

    def dynamic_discovery(*, now_ms):
        rows = [{**r, "delta_observed_at_ms": now_ms - 1000} for r in instruments()]
        return discovery.discover(rows, spot_box[0], now_ms, quantity_btc=1.0, width_usd=2000.0)

    def write_fmz_with_flow(ts_ms: int):
        row = write_fmz(snapshots, native_gate(ts=ts_ms - 1000), now=ts_ms)
        row["factor_snapshot"]["flow"]["micro_flow"] = {
            "fast_4h": {"data_ready": True, "direction": "neutral", "cvd_sum": 15.0, "momentum_return_pct": 0.05},
            "slow_12h": {"data_ready": True, "direction": "neutral", "cvd_sum": 18.0, "momentum_return_pct": 0.10},
            "combined": {"data_ready": True, "direction": "neutral", "ready_horizons": ["4h", "12h"]},
        }
        snapshots.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    def advance_market(ts_ms: int, idx: float | None = None):
        now[0] = ts_ms
        if idx is not None:
            spot_box[0] = idx
        write_fmz_with_flow(ts_ms)
        wb.refresh(now_ms=ts_ms, record_daily=False)

    wb.discovery_capture = dynamic_discovery
    wb.quote_capture = dynamic_quote
    wb.config.shadow_experiment = True
    wb.config.shadow_dossier_root = tmp_path / "dossier"
    wb.config.shadow_service_token = "tok"
    wb.config.frontend_dir = Path(__file__).resolve().parents[1] / "deploy/astra_workbench_v3"
    advance_market(slot_r16, 100_000.0)

    arch_root = tmp_path / "archive"
    points = [[t, 100_000.0] for t in range(slot_r16 - DAY, slot_r16 + M + 1, M)]
    _seed_r16_archive(wb, arch_root, slot_r16, expiry, points)

    contracts_proof = []
    for item in wb._current_view["candidates"]:
        c = item["snapshot"]["contract"]
        contracts_proof.extend([
            {"instrument_name": c["short_instrument"], "expiration_timestamp": expiry, "settlement_currency": "BTC", "is_active": True},
            {"instrument_name": c["long_instrument"], "expiration_timestamp": expiry, "settlement_currency": "BTC", "is_active": True},
        ])
    monkeypatch.setattr(dossier, "public", lambda method, **kw: contracts_proof if method == "get_instruments" else {"timestamps": [], "prices": []})
    monkeypatch.setattr(dossier.archive, "Deribit", lambda *a, **kw: SimpleNamespace(index_path=lambda *x, **y: points))

    def ard_init_transport(api_key, model, request_body, timeout_s, endpoint=None):
        del api_key, model, request_body, timeout_s, endpoint
        ann = {
            **EXCERPTS,
            "depends_on_ard": True,
            "condition_plan": {
                **RULE,
                "check_interval_ms": 5 * M,
                "ard_responsibility_meaning": "参考锚 Pin 邻域变化仅作复核事实。",
                "ard_review_on": "Pin 迁移或邻域事件发生时复核。",
            },
        }
        content = (
            "\n".join([
                "Put 侧路径核查：" + EXCERPTS["put"], EXCERPTS["put_counter"],
                "Call 侧路径核查：" + EXCERPTS["call"], EXCERPTS["call_counter"], EXCERPTS["comparison"],
                "### Accept / Call", "",
                RULE["historical_facts"], RULE["accepted_conditions"], RULE["confirmation_excerpt"],
                ann["condition_plan"]["ard_responsibility_meaning"], ann["condition_plan"]["ard_review_on"],
            ]) + "\n\n<ANNOTATION_JSON>\n" + json.dumps(ann, ensure_ascii=False) + "\n</ANNOTATION_JSON>\n"
        )
        return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}], "usage": {}, "_http_status": 200}

    d = dossier.Dossiers(wb.config.shadow_dossier_root, clock=lambda: now[0])
    with serving(wb) as base:
        svc = post(base, wb)
        advance_market(slot_r16 + 2 * M, 100_000.0)
        dealt = tracking.auto_deal(
            d, _plan_r16(slot_r16), arch_root, svc,
            llm_config={"api_key": "k", "model": "deepseek-v4.1-ard", "transport": ard_init_transport},
        )
        assert dealt[0]["decision"] == "Accept"
        hand = next((wb.config.shadow_dossier_root / "dossiers").glob("Deribit_BTC_*/hands/hand_*"))
        ep_dir = next((hand.parent.parent / "episodes").glob("episode_*"))

        # Emit reliable post-entry structure Pin shift (+1500 USD toward Call short strike) on same measure
        exp_day = dossier.utc(expiry).date().isoformat()
        arch = archive.Archive(arch_root)
        t_shift = slot_r16 + 5 * M
        arch.append_line(
            f"structure/{exp_day}.jsonl",
            {
                "schema": "structure_snapshot@0.1", "expiry_ms": expiry, "at_ms": t_shift, "available_at_ms": t_shift + 10_000,
                "pin_source": "deribit_gex", "pin_method": "gamma_peak", "pin_status": "ok",
                "profile": {"walls": {"pin": 101_500.0}, "top": {"oi_put": [{"strike": 98_000.0}], "oi_call": [{"strike": 103_000.0}]}},
            },
            at_ms=t_shift + 10_000, kind="structure",
        )
        t_chk = slot_r16 + 22 * M
        for m in range(points[-1][0] + M, t_chk + 1, M):
            points.append([m, 100_050.0])
        advance_market(t_chk, 100_050.0)

        ard_calls = [0]

        def ard_hold_transport(api_key, model, request_body, timeout_s, endpoint=None):
            del api_key, model, request_body, timeout_s, endpoint
            ard_calls[0] += 1
            return {
                "choices": [{"message": {"content": "### HOLD\n\nPin 上移至 101,500 但未破 103,000。\n<HOLD_REVIEW_JSON>" + json.dumps({
                    "verdict": "HOLD", "affected_conditions": ["ARD 锚位移"], "public_reply": "Pin 迁移后仍保持短腿外。", "key_facts": ["ARD_PIN_SHIFT"]
                }, ensure_ascii=False) + "</HOLD_REVIEW_JSON>"}, "finish_reason": "stop"}],
                "usage": {}, "_http_status": 200,
            }

        res1 = tracking.sync(
            d, hand, svc, check_due=True, archive_root=arch_root, run_review=True,
            llm_config={"api_key": "k", "model": "deepseek-v4.1-ard", "transport": ard_hold_transport},
        )
        assert any(json.loads(p.read_text(encoding="utf-8")).get("kind") == "REVIEW_REQUIRED" for p in (ep_dir / "events").glob("*.json"))
        assert res1["hold_review"]["action"] == "QUEUED"

        # Test Finding #2: exact touch (spot == 103,000, d_now == 0) must NOT fire BOUNDARY_BREACH;
        # and backfilled CONDITION_TRIGGERED (T+5m) followed by CONDITION_NOT_TRIGGERED (T+10m) still bypasses cooldown!
        sched_res, action = review.schedule_review_action(
            {"revoked": False, "inflight": False, "pending": [{
                "category": "A", "code": "CONDITION_TRIGGERED", "signature": f"CONDITION_TRIGGERED:{t_chk}",
                "severity": 3, "observed_at_ms": t_chk, "summary_cn": "trigger"
            }], "pending_since_ms": t_chk, "cooldown_until_ms": t_chk + 1_800_000, "last_review_at_ms": t_chk + 30_000},
            [], now_ms=t_chk + 45_000,
        )
        assert action is not None and action["reason"] == "condition_triggered_priority_a"
        trigs_multi = review.detect_hold_triggers(
            frozen_condition=json.loads((ep_dir / "condition.json").read_text(encoding="utf-8")),
            side="call", short_strike_usd=103_000.0, entry_at_ms=slot_r16,
            t0_metrics={"short_distance_pct": 2.91},
            series_rows=[{"recorded_at_ms": t_chk + M, "metrics": {"asof_ms": t_chk + M, "spot_usd": 103_000.0, "short_distance_pct": 0.0}}],
            condition_events=[
                {"event_id": "e1", "kind": "CONDITION_TRIGGERED", "observed_at_ms": t_chk + 5 * M},
                {"event_id": "e2", "kind": "CONDITION_NOT_TRIGGERED", "observed_at_ms": t_chk + 10 * M},
            ],
            now_ms=t_chk + 11 * M,
        )
        assert trigs_multi and trigs_multi[0]["code"] == "CONDITION_TRIGGERED"
        # Touch (0.0%) without CONDITION_TRIGGERED does NOT emit BOUNDARY_BREACH
        trigs_touch = review.detect_hold_triggers(
            frozen_condition=json.loads((ep_dir / "condition.json").read_text(encoding="utf-8")),
            side="call", short_strike_usd=103_000.0, entry_at_ms=slot_r16,
            t0_metrics={"short_distance_pct": 2.91},
            series_rows=[
                {"recorded_at_ms": t_chk, "metrics": {"asof_ms": t_chk, "spot_usd": 102_500.0, "short_distance_pct": 0.485}},
                {"recorded_at_ms": t_chk + M, "metrics": {"asof_ms": t_chk + M, "spot_usd": 103_000.0, "short_distance_pct": 0.0}},
            ],
            condition_events=[],
            now_ms=t_chk + M,
        )
        assert not any(t["code"] == "BOUNDARY_BREACH" for t in trigs_touch)


def test_slow_llm_revoke_captures_live_quote_flow_12h_and_stale_recovery(tmp_path, monkeypatch):
    """Verify slow LLM REVOKE (>5s clock jump) still records SIMULATED_ORDER(BUY_BACK_SHORT) via live
    shadow-tracking/quote, forwards 12h flow, recovers stale inflight state, and projects all audit fields."""
    wb, snapshots, now, first = setup(tmp_path / "tracker")
    expiry = first["contract"]["expiry_ms"]
    slot_r16 = expiry - 16 * HOUR
    spot_box = [100_000.0]
    metas = {row["instrument_name"]: row for row in instruments()}

    def dynamic_quote(short_instrument, long_instrument, quantity_btc=1.0, **_kw):
        now_ms = now[0]
        idx = spot_box[0]

        def fetch(path, params):
            name = params["instrument_name"]
            if path == "public/get_instrument":
                return dict(metas[name])
            return {
                "instrument_name": name,
                "state": "open",
                "timestamp": now_ms,
                "index_price": idx,
                "bids": [[0.007 if name == short_instrument else 0.002, 1.0]],
                "asks": [[0.008 if name == short_instrument else 0.003, 1.0]],
            }

        return public_quote.capture_candidate(
            short_instrument, long_instrument, quantity_btc, transport=fetch, clock_ms=lambda: now_ms
        )

    def dynamic_discovery(*, now_ms):
        rows = [{**r, "delta_observed_at_ms": now_ms - 1000} for r in instruments()]
        return discovery.discover(rows, spot_box[0], now_ms, quantity_btc=1.0, width_usd=2000.0)

    def write_fmz_with_flow(ts_ms: int):
        row = write_fmz(snapshots, native_gate(ts=ts_ms - 1000), now=ts_ms)
        row["factor_snapshot"]["flow"]["micro_flow"] = {
            "fast_4h": {"data_ready": True, "direction": "neutral", "cvd_sum": 15.0, "momentum_return_pct": 0.05},
            "slow_12h": {"data_ready": True, "direction": "neutral", "cvd_sum": 18.0, "momentum_return_pct": 0.10},
            "combined": {"data_ready": True, "direction": "neutral", "ready_horizons": ["4h", "12h"]},
        }
        snapshots.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    def advance_market(ts_ms: int, idx: float | None = None):
        now[0] = ts_ms
        if idx is not None:
            spot_box[0] = idx
        write_fmz_with_flow(ts_ms)
        wb.refresh(now_ms=ts_ms, record_daily=False)

    wb.discovery_capture = dynamic_discovery
    wb.quote_capture = dynamic_quote
    wb.config.shadow_experiment = True
    wb.config.shadow_dossier_root = tmp_path / "dossier"
    wb.config.shadow_service_token = "tok"
    wb.config.frontend_dir = Path(__file__).resolve().parents[1] / "deploy/astra_workbench_v3"
    advance_market(slot_r16, 100_000.0)

    arch_root = tmp_path / "archive"
    points = [[t, 100_000.0] for t in range(slot_r16 - DAY, slot_r16 + M + 1, M)]
    _seed_r16_archive(wb, arch_root, slot_r16, expiry, points)

    contracts_proof = []
    for item in wb._current_view["candidates"]:
        c = item["snapshot"]["contract"]
        contracts_proof.extend([
            {"instrument_name": c["short_instrument"], "expiration_timestamp": expiry, "settlement_currency": "BTC", "is_active": True},
            {"instrument_name": c["long_instrument"], "expiration_timestamp": expiry, "settlement_currency": "BTC", "is_active": True},
        ])
    monkeypatch.setattr(dossier, "public", lambda method, **kw: contracts_proof if method == "get_instruments" else {"timestamps": [], "prices": []})
    monkeypatch.setattr(dossier.archive, "Deribit", lambda *a, **kw: SimpleNamespace(index_path=lambda *x, **y: points))

    d = dossier.Dossiers(wb.config.shadow_dossier_root, clock=lambda: now[0])
    _, init_transport = _mock_initial_llm("Call")
    budget = signal_llm_review.DailyHttpBudget(tmp_path / "shared_budget.json", limit=5)

    with serving(wb) as base:
        svc = post(base, wb)
        advance_market(slot_r16 + 2 * M, 100_000.0)
        dealt = tracking.auto_deal(
            d,
            _plan_r16(slot_r16),
            arch_root,
            svc,
            llm_config={"api_key": "k", "model": "deepseek-v4.1-test", "reasoning_effort": "medium", "budget": budget, "transport": init_transport},
        )
        assert dealt[0]["decision"] == "Accept"
        assert budget.snapshot()["http_calls_used"] == 1
        hand = next((wb.config.shadow_dossier_root / "dossiers").glob("Deribit_BTC_*/hands/hand_*"))
        ep_dir = next((hand.parent.parent / "episodes").glob("episode_*"))
        lce_id = json.loads((hand / "tracking" / "link.json").read_text(encoding="utf-8"))["t0"]["tracker_episode_id"]

        # Seed a stale inflight state from 30m earlier to verify stale_inflight_recovered recovery
        t_breach = slot_r16 + 40 * M
        for m in range(points[-1][0] + M, t_breach + 1, M):
            points.append([m, 103_450.0])
        advance_market(t_breach, 103_450.0)
        stale_state = {
            "revoked": False,
            "inflight": True,
            "inflight_started_at_ms": t_breach - 30 * M,
            "inflight_batch": [{"category": "B", "code": "BUFFER_EROSION", "signature": "BUFFER_EROSION:stale", "severity": 1, "observed_at_ms": t_breach - 30 * M, "summary_cn": "stale"}],
            "pending": [],
            "dispatched_signatures": {},
        }
        (ep_dir / "trigger_state.json").write_text(json.dumps(stale_state), encoding="utf-8")

        def slow_revoke_transport(api_key, model, request_body, timeout_s, endpoint=None):
            del api_key, model, request_body, timeout_s, endpoint
            # Simulate a 90s slow LLM call without calling wb.refresh -> exported['latest_point'] becomes 90s stale!
            now[0] += 90_000
            write_fmz(snapshots, native_gate(ts=now[0] - 1000), now=now[0])
            content = (
                "### REVOKE\n\n短腿已入区且未回收，建议撤销。\n<HOLD_REVIEW_JSON>"
                + json.dumps({
                    "verdict": "REVOKE",
                    "affected_conditions": ["M1", "C1"],
                    "public_reply": "短腿已破位，建议 REVOKE。",
                    "key_facts": ["CONDITION_TRIGGERED"],
                }, ensure_ascii=False)
                + "</HOLD_REVIEW_JSON>"
            )
            return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}], "usage": {"total_tokens": 77}, "_http_status": 200}

        res_rev = tracking.sync(
            d,
            hand,
            svc,
            check_due=True,
            archive_root=arch_root,
            run_review=True,
            llm_config={"api_key": "k", "model": "deepseek-v4.1-hold", "reasoning_effort": "high", "budget": budget, "transport": slow_revoke_transport},
        )
        assert res_rev["hold_review"]["action"] == "REVIEWED"
        assert res_rev["hold_review"]["verdict"] == "REVOKE"
        assert budget.snapshot()["http_calls_used"] == 2

        audit = tracking.audit_record(hand, now[0], {lce_id: "active"}, detail=True)
        assert audit["shadow_management"]["exit_status"] == "SIMULATED_BUY_BACK_SHORT"
        assert audit["shadow_management"]["buyback_cost_btc"] == pytest.approx(0.008)
        assert audit["shadow_management"]["buyback_quote_at_ms"] > t_breach
        assert audit["entry_basis"]["schema"] == "entry_decision_basis@1"

        valid_rev = next(r for r in audit["hold_reviews"] if r["status"] == "VALID")
        assert valid_rev["reasoning_effort"] == "high"
        assert valid_rev["triggers"] and all({"category", "code", "summary_cn", "observed_at_ms"} <= set(t) for t in valid_rev["triggers"])
        assert "18.00" in valid_rev["input_md"] and "0.10%" in valid_rev["input_md"]

        failed_rev = next(r for r in audit["hold_reviews"] if r["status"] == "FAILED")
        assert failed_rev["error"] == "inflight_stale_recovered"
        assert failed_rev["reasoning_effort"] == "high"

        revoke_ev = next(e for e in audit["events"] if e["kind"] == "AI_REVOKE")
        assert revoke_ev["reasoning_effort"] == "high"
        assert revoke_ev["protocol_status"] == "ALIGNED"
        assert revoke_ev["shadow_action"] == "REVOKE_ALIGNED"
        assert revoke_ev["triggers"] and all({"category", "code", "summary_cn"} <= set(t) for t in revoke_ev["triggers"])

        detail_view = wb.locked_candidate_detail(lce_id)
        assert detail_view["ai_dossier_management"]["latest_review"]["reasoning_effort"] == "high"



