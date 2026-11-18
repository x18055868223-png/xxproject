"""Offline tests for the A evidence pair and unchanged mechanical boundary."""

import json
import os
from pathlib import Path

import pytest

from tools import astra_player_condition_v1 as condition
from tools import astra_player_evidence_replay as replay
from tools import astra_player_replay_path as path
from test_astra_player_condition_v1 import freeze, path as check_path, T, M


CONTRACTS = {
    "put": {"side": "put", "short_strike_usd": 63_000, "long_strike_usd": 61_000},
    "call": {"side": "call", "short_strike_usd": 65_000, "long_strike_usd": 67_000},
}


def test_dual_cutoff_and_unverified_minute_semantics():
    input_path = {"index_name": "btc_usd", "fetched_at_ms": T + 2*M,
                  "points": [[T-M, 63_300], [T, 65_050], [T+M, 65_100]]}
    cutoff, acquired, frozen = T + 11_000, T + 2*M, T + 3*M
    points, audit = path.frozen_points(input_path, cutoff, frozen, acquired)
    assert points == [(T-M, 63_300)]
    assert [row["label_ms"] for row in audit["excluded_after_market_cutoff"]] == [T, T+M]
    with pytest.raises(ValueError, match="acquisition receipt"):
        path.frozen_points(input_path, cutoff, frozen, frozen + 1)
    with pytest.raises(ValueError, match="cannot carry"):
        path.frozen_points(input_path, cutoff, frozen, None, synthetic=True)
    duplicate = {**input_path, "points": input_path["points"] + [input_path["points"][-1]]}
    with pytest.raises(ValueError, match="duplicate"):
        path.frozen_points(duplicate, cutoff, frozen, acquired)


def test_synthetic_reversal_recovers_both_signed_margins():
    # Four hours of synthetic minute points. The reversal and step size are
    # constructed for behavior tests and carry no historical availability claim.
    anchors = [(0, 65_200), (51, 65_260), (235, 63_373), (240, 64_150)]
    raw = []
    for (i, p), (j, q) in zip(anchors, anchors[1:]):
        raw.extend((T + n*M, p + (q-p)*(n-i)/(j-i)) for n in range(i, j))
    raw.append((T+240*M, 64_150))
    points, audit = path.frozen_points(
        {"index_name": "btc_usd", "timestamp_semantics": "instant", "points": raw},
        T+240*M, None, None, synthetic=True)
    assert not audit["availability_checked"] and audit["bundle_acquired_ms"] is None
    summary = path.summarize(points, CONTRACTS, T+240*M)
    by_time = {n["label_ms"]: n for n in summary["nodes"]}
    high, low, last = (by_time[T+i*M] for i in (51, 235, 240))
    assert [r["index_usd"] for r in (high, low, last)] == [65_260, 63_373, 64_150]
    assert (low["put_margin_usd"], last["put_margin_usd"]) == (373, 1150)
    assert (low["call_margin_usd"], last["call_margin_usd"]) == (1627, 850)
    assert summary["windows"]["5"]["delta"] == 777
    assert summary["windows"]["240"]["max_down_usd"] == 1887
    assert summary["events"][0]["kind"] == "segment_starts_inside"


def test_path_retains_strict_contacts_and_breaks_beyond_six_nodes():
    prices = [(0, 65_200), (1, 65_000), (2, 64_000), (3, 60_000), (4, 61_000),
              (5, 64_000), (6, 62_000), (7, 64_000), (9, 68_000)]
    summary = path.summarize([(T+i*M, p) for i, p in prices], CONTRACTS, T+9*M)
    events = [(e["label_ms"], e["side"], e["kind"]) for e in summary["events"]]
    assert (T, "call", "segment_starts_inside") in events
    assert (T+M, "call", "short_touch") in events
    assert (T+2*M, "call", "return") in events
    assert (T+3*M, "put", "entry") in events
    assert (T+3*M, "put", "beyond_protection") in events
    assert (T+4*M, "put", "protection_touch") in events
    assert (T+7*M, "put", "return") in events
    assert (T+9*M, "call", "segment_starts_beyond_protection") in events
    assert (T+9*M, "call", "entry") not in events
    assert len(summary["nodes"]) == len(prices) > 6
    assert "delta" not in summary["windows"]["5"]


def test_touch_at_segment_start_does_not_prove_cross_from_outside():
    summary = path.summarize([(T, 65_000), (T+M, 66_000)], CONTRACTS, T+M)
    assert [e["kind"] for e in summary["events"]] == ["short_touch", "segment_starts_inside"]
    assert summary["episodes"]["call"][0]["entered_ms"] is None


def spot_row():
    windows = {}
    for key, hours, cvd, ret in (("fast_4h", 4, -56, -2.18), ("slow_12h", 12, 1560.1, -2.33)):
        windows[key] = {"source": "binance_spot", "symbol": "BTCUSDT", "coverage_frac": 1.0,
                        "data_ready": True, "bar_complete": True, "cvd_unit": "BTC", "cvd_sum": cvd,
                        "momentum_return_pct": ret, "window_start_ms": T-hours*60*M, "end_ts_ms": T}
    return {"quality": "ok", "asof_ms": T, "values": {"micro_flow": windows}}


def test_spot_only_derives_valid_windows_and_preserves_missing_total():
    full = replay.inspect_spot(spot_row(), T, T+M, T)
    assert full["grade"] == "comparable"
    assert full["previous_8h"]["net_btc"] == pytest.approx(1616.1)
    assert full["previous_8h"]["return_pct"] == pytest.approx(((1-0.0233)/(1-0.0218)-1)*100)
    assert full["windows"]["fast_4h"]["total"] is None


@pytest.mark.parametrize("defect", ["both_missing", "one_missing", "symbol", "source", "gap",
                                    "future_close", "future_available", "conflicting_clock", "total_only"])
def test_spot_uncertain_fields_cannot_be_derived(defect):
    row = spot_row()
    fast, slow = row["values"]["micro_flow"].values()
    if defect == "both_missing":
        fast.pop("end_ts_ms")
        slow.pop("end_ts_ms")
    elif defect == "one_missing":
        slow.pop("end_ts_ms")
    elif defect in {"symbol", "source"}:
        fast[defect] = "different"
    elif defect == "gap":
        fast["gap"] = True
    elif defect == "future_close":
        fast["complete_ts_ms"] = T+M
    elif defect == "future_available":
        fast["available_at_ms"] = T+2*M
    elif defect == "conflicting_clock":
        fast["complete_ts_ms"] = T-M
    elif defect == "total_only":
        fast.pop("cvd_sum")
        fast["total_volume_btc"] = 370
    result = replay.inspect_spot(row, T, T+M, T)
    assert result["grade"] == "reported_only" and result["previous_8h"] is None
    assert "前8小时研究重算" not in replay.spot_text(result)
    if defect in {"future_close", "future_available"}:
        assert "fast_4h" not in result["windows"]
    if defect == "total_only":
        assert result["windows"]["fast_4h"]["net"] is None


@pytest.mark.parametrize("side,outer,beyond", [("put", 101, 89), ("call", 99, 111)])
@pytest.mark.parametrize("rule", ["new_entry", "entry_not_returned"])
def test_m_protection_jump_existing_fixed_grid(tmp_path, side, outer, beyond, rule):
    target = freeze(tmp_path, side=side, rule=rule, entry_price=outer)
    original = target.read_bytes()
    tick = T + 2*M
    assert condition.check_condition(target, check_path([beyond, beyond]), T+M)["status"] == "not_due"
    entered = condition.check_condition(target, check_path([beyond, beyond]), tick)
    assert entered["status"] == "triggered" and entered["latest_new_entry_ms"] == T+M
    assert entered["current_zone"] == "已越过保护腿"
    returned = condition.check_condition(target, check_path([beyond, outer]), tick)
    assert returned["status"] == ("triggered" if rule == "new_entry" else "not_triggered")
    assert condition.check_condition(target, check_path([beyond, outer]) | {"points": [[tick, beyond]]},
                                     tick)["status"] == "uncheckable"
    assert target.read_bytes() == original


def test_replay_real_pair_reversible_and_same_system():
    root = os.environ.get("ASTRA_AE_FROZEN_BUNDLE")
    if not root:
        pytest.skip("read-only historical bundle available through ASTRA_AE_FROZEN_BUNDLE")
    input_dir = Path(root)
    raw, enhanced, proof = replay.replay(input_dir)
    original = json.loads(raw)
    assert original["messages"][0] == enhanced["messages"][0]
    assert {k: v for k, v in original.items() if k != "messages"} == {
        k: v for k, v in enhanced.items() if k != "messages"}

    def split(request):
        head, body = request["messages"][1]["content"].split("## 本局牌面\n", 1)
        board, tail = body.split("## 读完后另写", 1)
        return head, board, tail

    left, old, right = split(original)
    new_left, new, new_right = split(enhanced)
    assert (left, right) == (new_left, new_right)
    for change in reversed(proof["edits"]):
        i, j = change["old_span"]
        x, y = change["new_span"]
        new = new[:x] + old[i:j] + new[y:]
    assert new == old
    assert "1,616.1 BTC" in old
    assert "1,616.1 BTC" not in split(enhanced)[1]
    assert "可比不重叠前8h→近4h反映历史阶段切换或延续" in split(enhanced)[1]
    assert proof["facts"]["spot"]["previous_8h"] is None
    assert proof["facts"]["index"]["windows"]["5"]["delta"] == pytest.approx(499.955833)
    end = proof["facts"]["index"]["nodes"][-1]
    assert end["call_margin_usd"] == pytest.approx(119.662)
    assert proof == replay.replay(input_dir)[2]


def test_l2_snapshots_and_prepare_arms_isolation():
    root = os.environ.get("ASTRA_AE_FROZEN_BUNDLE")
    if not root:
        pytest.skip("read-only historical bundle available through ASTRA_AE_FROZEN_BUNDLE")
    input_dir = Path(root)
    arms, proof = replay.prepare_arms(input_dir)
    assert set(arms) == {"A0", "A1", "A2"}

    snapshots = proof["quote_snapshots"]
    assert len(snapshots) == 2
    put_snap, call_snap = snapshots
    assert put_snap["candidate"] == "put"
    assert put_snap["price_usd"] == pytest.approx(64_101.86)
    assert put_snap["call_short_depth_usd"] == pytest.approx(101.86)
    assert put_snap["evidence_status"] == "OBSERVED_UNALIGNED"
    assert call_snap["candidate"] == "call"
    assert call_snap["price_usd"] == pytest.approx(64_093.32)
    assert call_snap["call_short_depth_usd"] == pytest.approx(93.32)
    assert call_snap["evidence_status"] == "OBSERVED_UNALIGNED"

    l2_text = replay.l2_snapshot_text(snapshots, 64_000)
    assert "15:59:36.749 UTC -> 64,101.86 USD" in l2_text
    assert "101.86 USD" in l2_text
    assert "15:59:39.158 UTC -> 64,093.32 USD" in l2_text
    assert "93.32 USD" in l2_text
    assert (
        "OBSERVED_UNALIGNED（同源相隔 2.409s 仅计一次盘口快照证据；指数子字段无独立事件时钟，"
        "与图表 15:59 分钟标签不可排序，禁止推断 16:00 或冻结截止时已确认在责任内或已确认在责任外）"
    ) in l2_text

    def board_of(arm_name):
        _, body = arms[arm_name]["messages"][1]["content"].split("## 本局牌面\n", 1)
        board, _ = body.split("## 读完后另写", 1)
        return board

    b1 = board_of("A1")
    b2 = board_of("A2")
    for board in (b1, b2):
        sec2 = [line for line in board.splitlines() if line.startswith("2. **空间背景")][0]
        assert "现价" not in sec2
        assert "合格图表末点在其下方" in sec2
        assert "在合格图表末点上方119.7 USD" in sec2
        assert "64,101.86" in board and "64,093.32" in board
        sec4_idx = board.index("4. **Call责任路径")
        sec5_idx = board.index("5. **现货量价与主动流")
        sec4 = board[sec4_idx:sec5_idx]
        assert "64,101.86" in sec4 and "64,093.32" in sec4 and "OBSERVED_UNALIGNED" in sec4

    assert "**责任路径时间线（研究重算）**" not in b1
    assert "**责任路径时间线（研究重算）**" in b2
    timeline_block = replay.render_timeline(
        proof["facts"]["index"],
        proof["facts"]["shared_clock"],
        l2_snapshots=snapshots,
    )
    assert (
        "**异源盘口快照并列提醒（OBSERVED_UNALIGNED，与图表分钟标签不可排序，禁止遗漏或被时间线覆盖）**："
        "Put 候选盘口 15:59:36.749 UTC 所见指数 64,101.86 USD（Call 短腿 64,000 责任内深 101.86 USD）；"
        "Call 候选盘口 15:59:39.158 UTC 所见指数 64,093.32 USD（Call 短腿 64,000 责任内深 93.32 USD）。"
        "冻结截止时刻的当前区内外状态为 UNKNOWN，禁止将 15:59:00 图表末点延伸为“当前仍在责任外”。"
    ) in timeline_block
    assert b2.replace(timeline_block + "\n\n", "", 1) == b1

    orig_req = json.loads((input_dir / "request.json").read_bytes())
    assert arms["A0"]["messages"] == orig_req["messages"]
    diff_1_2 = replay.unified_board_diff(
        arms["A1"]["messages"][1]["content"],
        arms["A2"]["messages"][1]["content"],
        "A1",
        "A2",
    )
    diff_body_lines = [
        line for line in diff_1_2.splitlines()
        if not line.startswith(("--- ", "+++ ", "@@ "))
    ]
    assert not any(line.startswith("-") for line in diff_body_lines)
    added_lines = [line[1:] for line in diff_body_lines if line.startswith("+")]
    assert "\n".join(added_lines).strip() == timeline_block.strip()

    spot = proof["facts"]["spot"]
    assert spot["evidence_status"] == {
        "fast_4h": "OBSERVED_UNALIGNED",
        "slow_12h": "OBSERVED_UNALIGNED",
        "previous_8h": "UNKNOWN",
        "tail_5m_spot": "UNKNOWN",
    }
    spot_rendered = replay.spot_text(spot)
    assert (
        "证据等级：4h 单窗 OBSERVED_UNALIGNED（缺完成柱截止与完整覆盖凭据，未满足下方“可核验的近4小时不利价流同向压力”成立前提，"
        "仅作未对齐报告值，不得升格为当前已成立反证）"
    ) in spot_rendered
    assert "12h 单窗 OBSERVED_UNALIGNED" in spot_rendered
    assert "跨窗 12h−4h UNKNOWN（两窗不可比：" in spot_rendered
    assert "末段 5m 同期现货主动流 UNKNOWN（冻结包未提供与指数末段对齐的现货主动成交及总量，也未确认成交机制）" in spot_rendered


def test_audit_reply_semantics_on_round1_archive():
    contracts = {
        "put": {"side": "put", "short_strike_usd": 63_000, "long_strike_usd": 61_000},
        "call": {"side": "call", "short_strike_usd": 64_000, "long_strike_usd": 66_000},
    }
    env_bundle = os.environ.get("ASTRA_AE_FROZEN_BUNDLE")
    inputs_dir = Path(env_bundle) if env_bundle else Path(__file__).resolve().parents[3] / ".artifacts" / "player-ae-replay" / "inputs"
    archive_root = (
        Path(env_bundle).parent / "round-1" / "parsed"
        if env_bundle
        else Path(__file__).resolve().parents[3] / ".artifacts" / "player-ae-replay" / "round-1" / "parsed"
    )
    if archive_root.exists():
        r06_text = (archive_root / "06-A0" / "visible-reply.md").read_text(encoding="utf-8")
        r06_parsed = json.loads((archive_root / "06-A0" / "result.json").read_bytes())
        sem06 = replay.audit_reply_semantics(r06_text, r06_parsed, contracts, arm="A0")
        assert sem06["cm_internal_conflict"] is True
        assert sem06["c_degenerates_to_m"] is True
        assert sem06["Put_buffer_distinction"] is True

        r05_text = (archive_root / "05-A1" / "visible-reply.md").read_text(encoding="utf-8")
        r05_parsed = json.loads((archive_root / "05-A1" / "result.json").read_bytes())
        sem05 = replay.audit_reply_semantics(r05_text, r05_parsed, contracts, arm="A1")
        assert sem05["m_excludes_beyond_protection"] is True
        assert sem05["c_degenerates_to_m"] is True
        assert sem05["l2_in_zone_cited"] is True
        assert sem05["chart_endpoint_called_current"] is True
        assert sem05["unverified_4h_elevated"] is False
        assert sem05["Put_buffer_distinction"] is True

        r04_text = (archive_root / "04-A2" / "visible-reply.md").read_text(encoding="utf-8")
        r04_parsed = json.loads((archive_root / "04-A2" / "result.json").read_bytes())
        sem04 = replay.audit_reply_semantics(r04_text, r04_parsed, contracts, arm="A2")
        assert sem04["m_excludes_beyond_protection"] is True
        assert sem04["c_degenerates_to_m"] is True
        assert sem04["l2_in_zone_cited"] is False
        assert sem04["chart_endpoint_called_current"] is True
        assert sem04["unverified_4h_elevated"] is True
        assert sem04["Put_buffer_distinction"] is True

        if inputs_dir.exists():
            report = replay.evaluate_round1_replies(archive_root, inputs_dir)
            assert report["production_side_selection_modified"] is False
            assert report["production_mechanical_trigger_modified"] is False
            by_slot = {row["slot_dir"]: row for row in report["rows"]}
            for slot in ("04-A2", "05-A1", "06-A0"):
                assert by_slot[slot]["scm_semantic_status"] == "FAILED_SEMANTIC"
                assert by_slot[slot]["handoff_mechanical_status"] == "PASSED"
                assert by_slot[slot]["beyond_protection_triggered"] is True
            assert by_slot["04-A2"]["evidence_attention_status"] == "FAILED_EVIDENCE"
            assert by_slot["05-A1"]["evidence_attention_status"] == "FAILED_EVIDENCE"
            md = replay.render_cm_evaluation_md(report)
            assert "FAILED_SEMANTIC" in md and "已越过保护腿" in md

    clean_text = (
        "Put合格末点距短腿880.3美元，入区前没有已列主要位置，无中间结构缓冲。"
        "盘口快照所见64,101.86与64,093.32深101.86与93.32美元。"
        "近4小时报告值未满足可核验前提，不构成当前已成立的反证。"
    )
    clean_parsed = {
        "decision": {"action": "Accept", "side": "put"},
        "condition_plan": {
            "invalidation": "new_entry",
            "check_interval_ms": 300_000,
            "accepted_conditions": "入场后要求Put责任外余量不被同向现货净卖持续侵蚀，若安全余量实质消耗则由事中事件门独立复核。",
            "confirmation_excerpt": "按UTC每5分钟网格检查Deribit一分钟指数采样，采样严格低于63,000美元（含越过保护腿61,000外侧）即触发new_entry。",
        },
    }
    sem_clean = replay.audit_reply_semantics(clean_text, clean_parsed, contracts, arm="A2")
    assert sem_clean["m_excludes_beyond_protection"] is False
    assert sem_clean["cm_internal_conflict"] is False
    assert sem_clean["c_degenerates_to_m"] is False
    assert sem_clean["l2_in_zone_cited"] is True
    assert sem_clean["chart_endpoint_called_current"] is False
    assert sem_clean["unverified_4h_elevated"] is False
    assert sem_clean["Put_buffer_distinction"] is True


def test_execute_trial_plan_fault_isolation_and_no_retry(tmp_path):
    manifest = {
        "order": ["A0", "A1", "A2"],
        "groups": {
            "A0": {"body_bytes": b'{"arm":"A0"}', "body_sha256": "sha_a0"},
            "A1": {"body_bytes": b'{"arm":"A1"}', "body_sha256": "sha_a1"},
            "A2": {"body_bytes": b'{"arm":"A2"}', "body_sha256": "sha_a2"},
        },
    }
    ledger_path = tmp_path / "trials.jsonl"

    class UnserializablePayload:
        pass

    def send_fn(slot_number, arm, body_bytes):
        assert body_bytes == manifest["groups"][arm]["body_bytes"]
        if slot_number == 2:
            raise RuntimeError("injected slot 2 failure")
        if slot_number == 3:
            return UnserializablePayload()
        return {
            "status": "RECEIVED",
            "http_status": 200,
            "text": "ok",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    summary = replay.execute_trial_plan("run-001", manifest, send_fn, ledger_path, max_workers=3)
    lines = [json.loads(line) for line in ledger_path.read_bytes().splitlines()]
    intents = [r for r in lines if r["event"] == "DISPATCH_INTENT"]
    terminals = [r for r in lines if r["event"] == "TERMINAL_RECEIPT"]
    assert len(intents) == 3
    assert len(terminals) == 3
    assert all(r["status"] == "DISPATCHED_PENDING" and r["billing_status"] == "PROVIDER_BILLING_UNKNOWN" for r in intents)

    assert summary["by_number"][1]["status"] == "RECEIVED"
    assert summary["by_number"][1]["billing_status"] == "USAGE_RECORDED"
    assert summary["by_number"][2]["status"] == "WORKER_EXCEPTION"
    assert summary["by_number"][2]["billing_status"] == "PROVIDER_BILLING_UNKNOWN"
    assert summary["by_number"][2]["error_type"] == "RuntimeError"
    assert summary["by_number"][3]["status"] == "WORKER_EXCEPTION"
    assert summary["by_number"][3]["billing_status"] == "PROVIDER_BILLING_UNKNOWN"
    assert summary["by_number"][3]["error_type"] == "TypeError"

    with pytest.raises(FileExistsError, match="run_id already exists in ledger"):
        replay.execute_trial_plan("run-001", manifest, send_fn, ledger_path)

    ledger2 = tmp_path / "two_ok_one_fail.jsonl"

    def send_fn_two_ok(slot_number, arm, body_bytes):
        if slot_number == 2:
            raise RuntimeError("slot 2 boom")
        return {"status": "RECEIVED", "text": f"reply-{slot_number}", "usage": {"total_tokens": 100}}

    summary2 = replay.execute_trial_plan("run-002", manifest, send_fn_two_ok, ledger2, max_workers=3)
    assert summary2["counts"]["received"] == 2
    assert summary2["counts"]["worker_exception"] == 1
    assert summary2["by_number"][1]["status"] == "RECEIVED"
    assert summary2["by_number"][3]["status"] == "RECEIVED"
    assert summary2["by_number"][2]["status"] == "WORKER_EXCEPTION"
    assert summary2["by_number"][2]["billing_status"] == "PROVIDER_BILLING_UNKNOWN"

    crashed_ledger = tmp_path / "crashed.jsonl"
    crashed_intent = {
        "event": "DISPATCH_INTENT",
        "trial_id": "run-crash:01:A0",
        "run_id": "run-crash",
        "number": 1,
        "arm": "A0",
        "body_sha256": "sha_a0",
        "dispatched_at_utc": "2026-11-18T07:00:00+00:00",
        "status": "DISPATCHED_PENDING",
        "billing_status": "PROVIDER_BILLING_UNKNOWN",
    }
    crashed_ledger.write_text(json.dumps(crashed_intent) + "\n", encoding="utf-8")
    crash_summary = replay.summarize_trial_ledger(crashed_ledger, manifest, run_id="run-crash")
    assert crash_summary["by_number"][1]["status"] == "DISPATCHED_RECEIPT_LOST"
    assert crash_summary["by_number"][1]["billing_status"] == "PROVIDER_BILLING_UNKNOWN"
    assert crash_summary["by_number"][2]["status"] == "NOT_DISPATCHED"

