"""Comprehensive zero-cost deterministic tests for the isolated P5 candidate (`codex/player-p5-candidate`).

Covers:
1. Prompt & request receipt SHA-256 identity (`@1.0`, `@1.1-candidate`, `@1.2-p5-candidate`)
2. P0 strict input (`p0_strict@1`), Reviewer C2/H4/LOW regressions, and H2/H3/H4 frozen source invariants
3. P2 mechanical boundaries, non-5m-aligned UTC check clock, and Reviewer C1/H1 natural-prose anti-false-rejection
4. All anti-bypass gates (H2/H3/H5/M3) and legacy `@1.0` / 120s handoff compatibility
5. Runtime reliability, fault injection (1-attempt paid cap on ContractValidationError, crash recovery, multi-hand isolation),
   and Hold Review C/M/UNKNOWN/OBSERVE separation (Reviewer M1)
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from test_astra_locked_followup import setup as wb_setup
from test_astra_player_dossier_v1 import Clock, SLOT, plan as base_plan, source as base_source
from test_astra_player_review_v1 import _make_series_point
from test_astra_player_tracking_v13 import EXCERPTS as LEGACY_EXCERPTS, RULE as LEGACY_RULE, post as wb_post, serving
from tools import astra_anchor_relative_v1 as ard
from tools import astra_locked_candidate_compare_v1 as locked_compare
from tools import astra_player_condition_v1 as condition
from tools import astra_player_dossier_v1 as dossier
from tools import astra_player_input_v1 as player
from tools import astra_player_review_v1 as review
from tools import astra_player_tracking_v13 as tracking


M = dossier.MINUTE
DAY = dossier.DAY
HOUR = 3_600_000

CALL_CONTRACT = {
    "side": "call",
    "short_strike_usd": 65_500.0,
    "long_strike_usd": 67_500.0,
    "expiry_ms": 1794816000000,
    "quantity_btc": 1.0,
    "venue": "Deribit",
    "underlying": "BTC",
    "settlement_currency": "BTC",
    "short_instrument": "BTC-16NOV26-65500-C",
    "long_instrument": "BTC-16NOV26-67500-C",
}
PUT_CONTRACT = {
    "side": "put",
    "short_strike_usd": 63_000.0,
    "long_strike_usd": 61_000.0,
    "expiry_ms": 1794816000000,
    "quantity_btc": 1.0,
    "venue": "Deribit",
    "underlying": "BTC",
    "settlement_currency": "BTC",
    "short_instrument": "BTC-16NOV26-63000-P",
    "long_instrument": "BTC-16NOV26-61000-P",
}


def _find_workspace_root() -> Path | None:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "p4-manual-ab-corpus" / "manifest.json").is_file():
            return p
    return None


def _build_valid_p5_reply(
    *,
    action_header: str = "## Accept / Call",
    invalidation: str = "entry_not_returned",
    require_outside_at_t0: bool | None = True,
    depends_on_ard: bool = False,
    omit_confirmation: bool = False,
    custom_confirmation: str | None = None,
    custom_accepted: str | None = None,
    truncate_comparison: bool = False,
    include_json: bool = True,
    short_strike: int = 98_000,
    long_strike: int = 96_000,
    call_short_strike: int = 102_000,
    call_long_strike: int = 104_000,
    side: str = "call",
) -> str:
    put_txt = f"Put 侧入场前位置在外，短腿 {short_strike:,}、保护腿 {long_strike:,} 责任路径清晰，未见不可承受压力。"
    call_txt = f"Call 侧入场前位置在外，短腿 {call_short_strike:,}、保护腿 {call_long_strike:,} 路径距离可控，历史测试后仍保持责任外。"
    put_cnt = "Put 最强反证为近4小时现货主动流未对齐，需保持审慎。"
    call_cnt = "Call 最强反证为剩余期限内仍可能上测短腿。"
    comp_txt = "两侧对比择优：Call 侧入区前距离与路径表现更稳健，因此优先选择 Call 侧承担。"
    hist_txt = "冻结历史事实：截至读取窗口末点，双侧短腿当前均处于责任外，且部分辅助流向存在未对齐限制。"
    acc_txt = custom_accepted or (
        "（1）冻结时正面承担近4小时现货流未对齐或上测回撤压力的不利事实；（2）该不利事实尚未推翻短腿外侧路径余量与结构边界；"
        "（3）关键不确定性在于现货完成时钟未证实（OBSERVED_UNALIGNED）；"
        "（4）独立于机械 M 的未来复核事实：入场后短腿外侧剩余余量实质收缩，且时钟对齐后的同源4h/12h现货主动流出现同向净买。"
    )
    if side == "call":
        s_str, l_str = f"{call_short_strike:,}", f"{call_long_strike:,}"
        cmp_word = "严格高于"
        ret_word = "严格回到"
    else:
        s_str, l_str = f"{short_strike:,}", f"{long_strike:,}"
        cmp_word = "严格低于"
        ret_word = "严格回到"
    if custom_confirmation is not None:
        conf_txt = custom_confirmation
    elif invalidation == "new_entry":
        conf_txt = (
            f"破坏确认口径：采用 new_entry，首次检查为入场后下一UTC整分钟，之后每5分钟（check_interval_ms=300000）执行一次，"
            f"btc_usd 分钟采样{cmp_word}卖出短腿{s_str}即记为进入（包含保护腿{l_str}边界及其外侧），触及短腿不算进入也不算返回。"
        )
    else:
        conf_txt = (
            f"破坏确认口径：采用 entry_not_returned，首次检查为入场后下一UTC整分钟，之后每5分钟（check_interval_ms=300000）执行一次，"
            f"btc_usd 分钟采样{cmp_word}卖出短腿{s_str}即记为进入（包含保护腿{l_str}边界及其外侧）且至检查点仍未{ret_word}短腿外侧才触发，"
            f"触及短腿不算进入也不算返回。"
        )

    paragraphs = [
        action_header,
        put_txt,
        call_txt,
        put_cnt,
        call_cnt,
        comp_txt,
    ]
    ann = {
        "put": put_txt,
        "call": call_txt,
        "put_counter": put_cnt,
        "call_counter": call_cnt,
        "comparison": comp_txt[:-6] if truncate_comparison else comp_txt,
        "depends_on_ard": depends_on_ard,
    }
    if action_header.startswith("## Accept"):
        paragraphs.extend([hist_txt, acc_txt])
        if not omit_confirmation:
            paragraphs.append(conf_txt)
        plan = {
            "invalidation": invalidation,
            "check_interval_ms": 300_000,
            "historical_facts": hist_txt,
            "accepted_conditions": acc_txt,
            "confirmation_excerpt": conf_txt,
        }
        if require_outside_at_t0 is not None:
            plan["require_outside_at_t0"] = require_outside_at_t0
        if depends_on_ard:
            ard_meaning = "ARD 责任相关性：历史围绕同到期 Pin 徘徊但近期未持续压向短腿。"
            ard_rev = "ARD 复核变化：入场后新增同口径可用快照显示 Pin 向短腿迁移或邻域反复失守时触发复核。"
            paragraphs.extend([ard_meaning, ard_rev])
            plan["ard_responsibility_meaning"] = ard_meaning
            plan["ard_review_on"] = ard_rev
        ann["condition_plan"] = plan
    body = "\n\n".join(paragraphs)
    if not include_json:
        return body
    return body + "\n\n<ANNOTATION_JSON>\n" + json.dumps(ann, ensure_ascii=False) + "\n</ANNOTATION_JSON>"


def _extract_source_contracts(src: dict) -> dict[str, dict]:
    return {
        c["side"]: player.bind(cs, c, c["side"])
        for cs, c in zip(src["cases"], src["cards"])
        if c.get("side") in {"put", "call"}
    }


def _seed_locked_candidate_episode(tmp_path: Path, now_ms: int):
    wb, _, now, first = wb_setup(tmp_path / "tracker")
    wb.config.shadow_experiment = True
    wb.config.shadow_dossier_root = tmp_path / "dossier"
    wb.config.shadow_service_token = "fixture-token"
    wb.config.frontend_dir = Path(__file__).resolve().parents[1] / "deploy/astra_workbench_v3"
    d = dossier.Dossiers(wb.config.shadow_dossier_root, clock=lambda: now[0])
    expiry = first["contract"]["expiry_ms"]
    cases, cards, native = [], [], []
    points = [[t, 100_000.0] for t in range(now[0] - DAY, now[0] + 1, M)]
    for c in wb._current_view["candidates"]:
        s = c["snapshot"]
        case = wb.case_file(s["snapshot_id"])
        read_ms = case["build"]["built_at_ms"]
        walls = dict(expiry_ms=expiry, walls={"pin": 99_000.0}, top={"oi_put": [{"strike": 98_000}], "oi_call": [{"strike": 102_000}]})
        player.entry(case, "L3.expiry_walls").update(values=walls, asof_ms=read_ms, quality="ok")
        contract = player.values(case, "L1.contract")
        cards.append(dossier.levels.build_card(expiry_ms=expiry, rnd="r2", side=contract["side"],
                     read_at_ms=read_ms, walls=walls, points=points, case=case, contract=contract, snapshots=[]))
        cases.append(case)
        native.extend(dict(instrument_name=contract[f"{leg}_instrument"], expiration_timestamp=expiry,
                           settlement_currency="BTC", is_active=True) for leg in ("short", "long"))
    path = dict(schema="index_path@0.1", index_name="btc_usd", expiry_ms=expiry, points=points, fetched_at_ms=now[0],
                timestamp_semantics="unverified_minute_label")
    hand = d.expect(expiry, "AD_HOC", reference="P5-TEST")
    src = dossier.seal_source(cases, cards, path, [], native, received_at_ms=now[0], source="TEST_FROZEN")
    d.deal(hand, src, input_profile=player.INPUT_PROFILE_P0_STRICT)
    input_md = (hand / "evidence" / "input.md").read_text(encoding="utf-8")
    contracts_pair = _extract_source_contracts(src)
    req_payload = review.build_initial_request_payload(
        input_md,
        contract_pair=contracts_pair,
        model="deepseek-v4-pro",
        prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
        input_profile=player.INPUT_PROFILE_P0_STRICT,
        requested_at_ms=now[0],
        fidelity="EXACT",
    )
    return wb, d, hand, src, contracts_pair, req_payload, now, expiry


# ---------------------------------------------------------------------------
# 1. Prompt & Receipt Identity
# ---------------------------------------------------------------------------

def test_prompt_sha256_and_receipt_identity():
    assert review.INITIAL_SYSTEM_PROMPT_VERSION == "player_initial_system@1.0"
    assert review.CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION == "player_initial_system@1.1-candidate"
    assert review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION == "player_initial_system@1.2-p5-candidate"

    sha_v10 = hashlib.sha256(review.INITIAL_SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    sha_v11 = hashlib.sha256(review.CANDIDATE_INITIAL_SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    sha_v12 = hashlib.sha256(review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT.encode("utf-8")).hexdigest()

    assert sha_v10 == "653168703cd80d6ef6a0c0243d8a380bd2c2b8dbf8c76de45045124fb66a88e6"
    assert sha_v11 == "98ae4aaae928f5d6dd9914aad2832df6642d0530d4ad0317231a987ea6c83137"
    assert sha_v12 == "b746c2d2f7f996d290992e01b11d9e4c4792a0e1afcacb900bbf0dfc678b6f34"

    req1 = review.build_initial_request_payload(
        "# mock user prompt",
        contract_pair={"put": PUT_CONTRACT, "call": CALL_CONTRACT},
        model="deepseek-v4-pro",
        prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
    )
    req2 = review.build_initial_request_payload(
        "# mock user prompt",
        contract_pair={"put": PUT_CONTRACT, "call": CALL_CONTRACT},
        model="deepseek-v4-pro",
        prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
    )
    assert req1["system_sha256"] == sha_v12
    assert req1["user_sha256"] == hashlib.sha256(req1["messages"][1]["content"].encode("utf-8")).hexdigest()
    assert "# mock user prompt" in req1["messages"][1]["content"]
    assert "候选合约对：" in req1["messages"][1]["content"]
    assert req1["messages_sha256"] == req2["messages_sha256"]
    assert req1["body_sha256"] == req2["body_sha256"]
    assert req1["input_profile"] == player.INPUT_PROFILE_P0_STRICT
    assert req1["mechanical_contract_version"] == condition.MECHANICAL_CONTRACT_VERSION == "p2_strict@1"
    assert set(req1["metadata"]["contract_pair"]) == {"put", "call"}

    with pytest.raises(ValueError, match="unsupported initial prompt_version"):
        review.build_initial_request_payload("# u", model="m", prompt_version="player_initial_system@9.9")
    with pytest.raises(ValueError, match="unsupported input_profile"):
        review.build_initial_request_payload("# u", model="m", input_profile="bad_profile@0")


# ---------------------------------------------------------------------------
# 2. P0 Strict Input (`p0_strict@1`), Reviewer C2/H4/LOW, and Real H2/H3/H4 Regressions
# ---------------------------------------------------------------------------

def test_p0_spot_qualification_and_degraded_mixed_windows():
    cutoff = 1_794_931_200_000
    base_w = {
        "data_ready": True, "cvd_unit": "BTC", "cvd_sum": -12.0, "momentum_return_pct": -0.8,
        "source": "binance", "symbol": "BTCUSDT", "coverage_frac": 1.0, "bar_complete": True,
        "end_ts_ms": cutoff - 60_000, "available_at_ms": cutoff - 1_000,
        "window_start_ms": cutoff - 4 * HOUR, "coverage_hours": 4.0,
    }
    base_s = {**base_w, "cvd_sum": -30.0, "momentum_return_pct": -1.5, "window_start_ms": cutoff - 12 * HOUR, "coverage_hours": 12.0}
    row_ok = {"asof_ms": cutoff - 500, "quality": "ok", "values": {"micro_flow": {"fast_4h": base_w, "slow_12h": base_s}}}
    q_ok = player.inspect_spot_qualification(row_ok, cutoff=cutoff)
    assert q_ok["status"] == "OBSERVED_ALIGNED"

    # H4.1: degraded flag must downgrade to OBSERVED_UNALIGNED (even with valid end_ts_ms <= cutoff)
    for flag in ("gap", "truncated", "cvd_degraded", "backfill_mixed", "window_backfill"):
        r_bad = copy.deepcopy(row_ok)
        r_bad["values"]["micro_flow"]["fast_4h"][flag] = True
        assert player.inspect_spot_qualification(r_bad, cutoff=cutoff)["status"] == "OBSERVED_UNALIGNED"

    # Future clock must be excluded cleanly as UNKNOWN with zero leaked numeric fields
    r_fut = copy.deepcopy(row_ok)
    r_fut["values"]["micro_flow"]["fast_4h"]["end_ts_ms"] = cutoff + 60_000
    r_fut["values"]["micro_flow"]["slow_12h"]["available_at_ms"] = cutoff + 1_000
    q_fut = player.inspect_spot_qualification(r_fut, cutoff=cutoff)
    assert q_fut["status"] == "UNKNOWN"
    assert q_fut["fast_4h"].get("future_excluded") is True
    assert "cvd_sum" not in q_fut["fast_4h"]

    # Mixed windows: fast_4h UNALIGNED + slow_12h ALIGNED -> overall OBSERVED_UNALIGNED (reason != 'ok')
    r_mix1 = copy.deepcopy(row_ok)
    del r_mix1["values"]["micro_flow"]["fast_4h"]["end_ts_ms"]
    q_mix1 = player.inspect_spot_qualification(r_mix1, cutoff=cutoff)
    assert q_mix1["status"] == "OBSERVED_UNALIGNED"
    assert q_mix1["reason"] != "ok"

    # Mixed windows: fast_4h UNKNOWN + slow_12h ALIGNED -> overall OBSERVED_ALIGNED_SLOW_ONLY
    r_mix2 = copy.deepcopy(row_ok)
    del r_mix2["values"]["micro_flow"]["fast_4h"]
    q_mix2 = player.inspect_spot_qualification(r_mix2, cutoff=cutoff)
    assert q_mix2["status"] == "OBSERVED_ALIGNED_SLOW_ONLY"


def test_p0_strict_ard_rejects_compat_backfill_and_matches_rendered_markdown(tmp_path):
    cutoff = 1_794_931_200_000
    exp_ms = 1_794_988_800_000
    src_copy = copy.deepcopy(base_source(Clock(cutoff)))
    # Legacy row backfilled by _normalize_legacy_structure_row
    legacy_row = {
        "schema": "structure_snapshot@0.1", "expiry_ms": exp_ms, "at_ms": cutoff - 600_000,
        "profile": {"walls": {"pin": 100_000}},
    }
    normalized_row = dossier._normalize_legacy_structure_row(legacy_row)
    assert normalized_row.get("compat_backfill_applied") is True

    # Under strict_asof=True, compat_backfill_applied MUST still be excluded (C2)!
    facts_strict = ard.analyze([normalized_row], src_copy["index_path"], expiry_ms=exp_ms, cutoff_ms=cutoff, strict_asof=True)
    assert facts_strict["observations"] == []
    assert facts_strict["unclocked_rows"] == 1
    assert "缺可按当时可用时间对齐的本期Pin历史" in ard.render(facts_strict, disclose_backfill=True)

    # Under strict_asof=False (legacy replay compat), output dict stays compatible with b3d79695
    facts_legacy = ard.analyze([legacy_row], src_copy["index_path"], expiry_ms=exp_ms, cutoff_ms=cutoff, strict_asof=False)
    assert facts_legacy.get("legacy_clock_backfill") is True
    assert "（含历史回填可用时钟，非原生可得收据）" in ard.render(facts_legacy, disclose_backfill=True)
    assert "（含历史回填可用时钟，非原生可得收据）" not in ard.render(facts_legacy, disclose_backfill=False)

    # Shared helper ensures build_input and condition_at_t0 / Dossiers.episode compute identical ARD facts
    p0_facts = player.ard_facts_for_evidence(
        [normalized_row], src_copy["index_path"], expiry_ms=exp_ms, cutoff_ms=cutoff, input_profile=player.INPUT_PROFILE_P0_STRICT
    )
    assert json.dumps(p0_facts, sort_keys=True) == json.dumps(facts_strict, sort_keys=True)


def _set_case_layer(case: dict, layer_name: str, values: dict, *, asof_ms: int, quality: str = "ok") -> None:
    layer_group = layer_name.split(".", 1)[0]
    layers = case.setdefault("layers", {})
    bucket = layers.setdefault(layer_group, [])
    row = {"ref": layer_name, "asof_ms": asof_ms, "quality": quality, "values": dict(values)}
    refs = case.setdefault("refs", {})
    if layer_name in refs and isinstance(refs[layer_name], dict):
        bucket[refs[layer_name]["index"]] = row
    else:
        refs[layer_name] = {"layer": layer_group, "index": len(bucket)}
        bucket.append(row)


def test_p0_strict_l2_and_mixed_spot_and_extremum_rendering():
    clk = Clock(SLOT + 10 * M)
    src = base_source(clk)
    put_case, call_case = copy.deepcopy(src["cases"])
    put_card, call_card = copy.deepcopy(src["cards"])
    read_at = put_card["read_at_ms"]

    # Inject Put short+long sequential skew & Call long_only valid L2 observation (LOW fix)
    _set_case_layer(put_case, "L2.book.short", {"index_price": 100_000.0, "timestamp": read_at - 2_000}, asof_ms=read_at - 2_000)
    _set_case_layer(put_case, "L2.book.long", {"index_price": 100_002.49, "timestamp": read_at - 1_401}, asof_ms=read_at - 1_401)
    _set_case_layer(put_case, "L2.quote", {"received_ts_ms": read_at - 500, "leg_received_ts_ms": {"short": read_at - 1_500, "long": read_at - 500}}, asof_ms=read_at - 500)

    _set_case_layer(call_case, "L2.book.short", {"index_price": 100_005.0, "timestamp": read_at + 60_000}, asof_ms=read_at - 1_000)  # invalid future short
    _set_case_layer(call_case, "L2.book.long", {"index_price": 99_998.0, "timestamp": read_at - 1_000}, asof_ms=read_at - 1_000)
    _set_case_layer(call_case, "L2.quote", {"received_ts_ms": read_at - 200}, asof_ms=read_at - 200)

    # Mixed spot: fast_4h unaligned (missing end_ts_ms), slow_12h aligned
    _set_case_layer(put_case, "L5.volume_price", {
        "micro_flow": {
            "fast_4h": {"data_ready": True, "cvd_unit": "BTC", "cvd_sum": -25.0, "momentum_return_pct": -1.2},
            "slow_12h": {"data_ready": True, "cvd_unit": "BTC", "cvd_sum": -10.0, "momentum_return_pct": -0.4, "end_ts_ms": read_at - 60_000},
        }
    }, asof_ms=read_at - 1_000)

    # Render strict with index_path=None -> L2 still appears (not gated on boundary_replaced), Section 5 stays UNALIGNED
    md_no_path = player.build_input(put_case, call_case, put_card, call_card, index_path=None, input_profile=player.INPUT_PROFILE_P0_STRICT)
    assert "输入规范：p0_strict@1" in md_no_path
    assert "串行两腿时差599ms、价差+2.49 USD" in md_no_path
    assert "Call保护腿原生盘口" in md_no_path
    assert "【OBSERVED_UNALIGNED】" in md_no_path
    assert "须明确作为当前已成立的反证" not in md_no_path
    assert "不可升格为当前已成立反证" in md_no_path

    # Future fast_4h + aligned slow_12h -> fast_4h excluded as UNKNOWN without leaking numbers, slow_12h rendered
    _set_case_layer(put_case, "L5.volume_price", {
        "micro_flow": {
            "fast_4h": {"data_ready": True, "cvd_unit": "BTC", "cvd_sum": -999.9, "momentum_return_pct": -9.9, "end_ts_ms": read_at + 120_000},
            "slow_12h": {"data_ready": True, "cvd_unit": "BTC", "cvd_sum": -10.0, "momentum_return_pct": -0.4, "end_ts_ms": read_at - 60_000},
        }
    }, asof_ms=read_at - 1_000)
    md_slow_only = player.build_input(put_case, call_case, put_card, call_card, index_path=src["index_path"], input_profile=player.INPUT_PROFILE_P0_STRICT)
    assert "【OBSERVED_ALIGNED（仅12h可用；近4h为UNKNOWN）】" in md_slow_only
    assert "-999.9" not in md_slow_only
    assert "须明确作为当前已成立的反证" not in md_slow_only

    # Incomplete index path explicitly refuses negative claim on protection touch
    incomplete_path = copy.deepcopy(src["index_path"])
    incomplete_path["points"] = incomplete_path["points"][20:]
    md_inc = player.build_input(put_case, call_case, put_card, call_card, index_path=incomplete_path, input_profile=player.INPUT_PROFILE_P0_STRICT)
    assert "覆盖不足，无法确认全窗是否曾触及保护腿" in md_inc
    assert "全窗已覆盖分钟采样未触及保护腿" not in md_inc


def test_real_frozen_h2_h3_h4_sources_and_legacy_b3d79695_parity():
    root = _find_workspace_root()
    if root is None:
        pytest.skip("local p4-manual-ab-corpus/manifest.json not present in container")
    manifest = json.loads((root / "p4-manual-ab-corpus" / "manifest.json").read_text(encoding="utf-8"))
    b3d_src = subprocess.check_output(["git", "show", "b3d79695:tools/astra_player_input_v1.py"], cwd=Path(__file__).resolve().parents[1], text=True, encoding="utf-8")
    ns: dict = {"__name__": "tools.b3d_player_input_v1", "__package__": "tools"}
    wt_root = str(Path(__file__).resolve().parents[1])
    if wt_root not in sys.path:
        sys.path.insert(0, wt_root)
    exec(compile(b3d_src, "b3d79695:tools/astra_player_input_v1.py", "exec"), ns)
    b3d_build_input = ns["build_input"]

    for h in manifest["hands"]:
        if h["sample_id"] not in {"H2", "H3", "H4"}:
            continue
        src = json.loads((root / h["source_dir"] / "evidence" / "source.json").read_text(encoding="utf-8"))
        # Strictly NEVER read outcomes/; read only frozen evidence/source.json
        legacy_out = player.build_input(
            *src["cases"], *src["cards"],
            index_path=src.get("index_path"),
            ard_history=src.get("history"),
            ard_band_snapshot=src.get("band_snapshot"),
            input_profile=player.INPUT_PROFILE_LEGACY,
        )
        b3d_out = b3d_build_input(
            *src["cases"], *src["cards"],
            index_path=src.get("index_path"),
            ard_history=src.get("history"),
            ard_band_snapshot=src.get("band_snapshot"),
        )
        assert legacy_out == b3d_out, f"legacy_v1 drifted from b3d79695 on {h['sample_id']}"

        p0_out = player.build_input(
            *src["cases"], *src["cards"],
            index_path=src.get("index_path"),
            ard_history=src.get("history"),
            ard_band_snapshot=src.get("band_snapshot"),
            input_profile=player.INPUT_PROFILE_P0_STRICT,
        )
        req_p5 = review.build_initial_request_payload(
            p0_out,
            contract_pair=_extract_source_contracts(src),
            model="deepseek-v4-pro",
            prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
        )
        assert req_p5["input_profile"] == "p0_strict@1"
        assert len(req_p5["body_sha256"]) == 64

        if h["sample_id"] == "H2":
            assert "串行两腿时差599ms、价差-2.49 USD" in p0_out
            assert "【OBSERVED_UNALIGNED】" in p0_out
            assert "须明确作为当前已成立的反证" not in p0_out
        elif h["sample_id"] == "H4":
            assert "缺可按当时可用时间对齐的本期Pin历史" in p0_out


# ---------------------------------------------------------------------------
# 3. P2 Mechanical Boundaries, Non-5m-Aligned UTC Clock & Reviewer C1/H1 Anti-False-Rejection
# ---------------------------------------------------------------------------

def test_p2_five_price_states_and_non_5m_aligned_utc_clock(tmp_path):
    # Put & Call state classification
    for price, exp_put in [
        (63_000.01, "OUTSIDE"),
        (63_000.0, "SHORT_TOUCH"),
        (62_500.0, "BETWEEN_LEGS"),
        (61_000.0, "PROTECTION_TOUCH"),
        (60_500.0, "BEYOND_PROTECTION"),
    ]:
        assert condition.classify_price_state(price, PUT_CONTRACT)["state"] == exp_put

    for price, exp_call in [
        (65_499.99, "OUTSIDE"),
        (65_500.0, "SHORT_TOUCH"),
        (66_500.0, "BETWEEN_LEGS"),
        (67_500.0, "PROTECTION_TOUCH"),
        (68_000.0, "BEYOND_PROTECTION"),
    ]:
        assert condition.classify_price_state(price, CALL_CONTRACT)["state"] == exp_call

    # Non-5m-aligned entry clock: 16:02:17 UTC -> first_at_ms = 16:03:00, steps 16:08:00, 16:13:00
    base_min = 1_794_758_400_000  # 2026-11-12 16:00:00 UTC (exact minute)
    entry_at = base_min + 2 * M + 17_000  # 16:02:17 UTC
    first_at = base_min + 3 * M           # 16:03:00 UTC
    cond_new = tmp_path / "cond_new.json"
    cond_enr = tmp_path / "cond_enr.json"
    common_kw = dict(
        contract=CALL_CONTRACT, entry_at_ms=entry_at, entry_index_usd=65_000.0,
        historical_facts="hist", accepted_conditions="acc", first_check_ms=first_at,
        check_interval_ms=300_000, require_outside_at_t0=True,
    )
    condition.freeze_condition(cond_new, invalidation="new_entry", **common_kw)
    condition.freeze_condition(cond_enr, invalidation="entry_not_returned", **common_kw)

    # 16:03..16:08 path: enters strictly > 65,500 at 16:05 (68,000 beyond protection!), returns to exact 65,500 (touch != return!) at 16:07 & 16:08
    pts_touch_at_check = [
        [base_min + 3 * M, 65_100.0],
        [base_min + 4 * M, 65_400.0],
        [base_min + 5 * M, 68_000.0],  # beyond protection -> strict entry
        [base_min + 6 * M, 66_000.0],
        [base_min + 7 * M, 65_500.0],  # exact short touch -> NOT return!
        [base_min + 8 * M, 65_500.0],
    ]
    idx_touch = {"index_name": "btc_usd", "expiry_ms": CALL_CONTRACT["expiry_ms"], "points": pts_touch_at_check}
    # Wall-clock 16:05:00 MUST return not_due because check clock is anchored to first_at (16:03 + 5m*k)
    assert condition.check_condition(cond_enr, idx_touch, base_min + 5 * M)["status"] == "not_due"
    # At 16:08:00: touch != return -> BOTH entry_not_returned and new_entry trigger
    assert condition.check_condition(cond_new, idx_touch, base_min + 8 * M)["status"] == "triggered"
    assert condition.check_condition(cond_enr, idx_touch, base_min + 8 * M)["status"] == "triggered"

    # Now strictly return outside (< 65,500) at 16:08:00 -> new_entry STILL triggers, entry_not_returned does NOT trigger
    pts_strict_ret = [*pts_touch_at_check[:-1], [base_min + 8 * M, 65_499.0]]
    idx_ret = {"index_name": "btc_usd", "expiry_ms": CALL_CONTRACT["expiry_ms"], "points": pts_strict_ret}
    assert condition.check_condition(cond_new, idx_ret, base_min + 8 * M)["status"] == "triggered"
    assert condition.check_condition(cond_enr, idx_ret, base_min + 8 * M)["status"] == "not_triggered"

    # require_outside_at_t0=True fails closed on SHORT_TOUCH / BETWEEN_LEGS / PROTECTION_TOUCH / BEYOND_PROTECTION
    for bad_t0 in (65_500.0, 66_000.0, 67_500.0, 68_500.0):
        bad_p = tmp_path / f"bad_{int(bad_t0)}.json"
        with pytest.raises(ValueError):
            condition.freeze_condition(
                bad_p, contract=CALL_CONTRACT, entry_at_ms=entry_at, entry_index_usd=bad_t0,
                historical_facts="h", accepted_conditions="a", invalidation="new_entry",
                first_check_ms=first_at, check_interval_ms=300_000, require_outside_at_t0=True,
            )


@pytest.mark.parametrize("prose", [
    # 1. Echoing prompt's own ":00/:05" prohibition
    "采用 entry_not_returned，首次检查为入场后下一UTC整分钟，之后每5分钟（300000ms）步进，不要写成自然:00/:05整点网格；btc_usd严格高于65,500即计为进入（包含保护腿67,500边界及外侧），触及短腿不算进入也不算返回。",
    # 2. Natural 1-minute sampling phrases
    "按 entry_not_returned 规则，入场后下一UTC整分钟首检，之后每5分钟（300000ms）检查一次；基于每1分钟UTC采样与逐分钟记录价格判断，严格高于65,500即进入（含保护腿67,500及其外侧），严格回到65,500外才返回。",
    # 3. Negating unused invalidation rule
    "本次采用 entry_not_returned 规则，不采用 new_entry；首次检查为入场后下一UTC整分钟，每5分钟检查，btc_usd严格高于短腿65,500即进入（包含保护腿及外侧）。",
    # 4. '不构成' negation on short touch
    "采用 entry_not_returned 规则，仅触及短腿65,500不构成进入，也不构成返回；严格高于65,500（包括保护腿67,500及外侧）才构成进入，每5分钟（300000ms）固定检查一次。",
    # 5. Explaining geometric band with negation before unilateral threshold (in single sentence)
    "短腿65,500至67,500不是机械上限，严格高于65,500即计为进入（触及/越过保护腿67,500及外侧均计入）；每5分钟检查一次，采用 entry_not_returned。",
])
def test_c1_natural_prose_anti_false_rejection(prose):
    res = condition.validate_confirmation_prose(
        prose, CALL_CONTRACT, expected_invalidation="entry_not_returned", expected_interval_ms=300_000
    )
    assert res["valid"] is True, f"False-rejected valid prose: {res}"


@pytest.mark.parametrize("bad_prose,expected_v", [
    ("采用 entry_not_returned，每5分钟检查，回到65,500及以下才算返回责任外。", "non_strict_return_boundary"),
    ("采用 entry_not_returned，每5分钟检查，触及短腿65,500即进入责任区，严格高于65,500持续有效。", "non_strict_entry_boundary"),
    ("采用 entry_not_returned，每5分钟检查，指数进入65,500至67,500区间即触发。", "restricts_entry_to_finite_interval"),
    ("采用 entry_not_returned，每60分钟固定检查一次，严格高于65,500即进入。", "clock_interval_mismatch"),
    ("采用 entry_not_returned，逐分钟判定、不设额外检查时钟，严格高于65,500即进入。", "clock_interval_mismatch"),
    ("采用 entry_not_returned，按自然5分钟整点检查，严格高于65,500即进入。", "clock_interval_mismatch"),
    ("采用 new_entry 规则，每5分钟检查，严格高于65,500即进入。", "invalidation_rule_mismatch"),
])
def test_c1_true_mechanical_violations_still_rejected(bad_prose, expected_v):
    res = condition.validate_confirmation_prose(
        bad_prose, CALL_CONTRACT, expected_invalidation="entry_not_returned", expected_interval_ms=300_000
    )
    assert res["valid"] is False
    assert expected_v in res["violations"], res


def test_h1_j1_natural_prose_sections_and_c1_band_advisory_not_blocking():
    # Covers all 3 H1 J1 natural-prose edge cases + C1.5 spread-band mention inside C
    natural_c = (
        "（1）冻结时已承担现货流未对齐与价格靠近上侧的风险，当前距65,500亏损带仍有余量（未进入65,500–67,500亏损带）。\n\n"
        "（2）该不利事实尚未排除承保依据，且（3）关键时钟不确定性已标为OBSERVED_UNALIGNED，（4）若入场后余量实质消耗或对齐现货流转净买将独立触发复核。"
    )
    text_body = (
        "## Accept / Call\n"
        "Put路径（14:00 UTC截面）：现价99,200远在Put短腿97,000之外，缓冲充足。\n"
        "Call路径（14:00 UTC截面）：现价与短腿65,500保持距离，此前进入已回收。\n"
        "Put最强反证：无新增重大不利流向，但远端结构存在不确定性。\n"
        "Call最强反证：短腿距现价较近，需跟踪后续测试。\n"
        "两侧对比择优：Call 侧已回收且综合余量可承受，因此优先选 Call。\n"
        "历史事实冻结：前24小时窗口曾短暂进入后已严格返回。\n"
        "承担条件（独立于M）：\n"
        + natural_c + "\n"
        "有效破坏标准：采用 entry_not_returned，首次检查为入场后下一UTC整分钟，之后每5分钟（300000ms）步进，严格高于65,500即计为进入（含保护腿67,500及外侧），严禁触及即返回。\n"
        "ARD责任相关性：历史围绕同到期Pin波动但未推翻Call路径。"
    )
    put_ex = "现价99,200远在Put短腿97,000之外，缓冲充足。"  # body-only without timestamp label!
    call_ex = "Call路径（14:00 UTC截面）：现价与短腿65,500保持距离，此前进入已回收。"
    put_c_ex = "Put最强反证：无新增重大不利流向，但远端结构存在不确定性。"
    call_c_ex = "Call最强反证：短腿距现价较近，需跟踪后续测试。"
    comp_ex = "两侧对比择优：Call 侧已回收且综合余量可承受，因此优先选 Call。"
    hist_ex = "历史事实冻结：前24小时窗口曾短暂进入后已严格返回。"
    conf_ex = "有效破坏标准：采用 entry_not_returned，首次检查为入场后下一UTC整分钟，之后每5分钟（300000ms）步进，严格高于65,500即计为进入（含保护腿67,500及外侧），严禁触及即返回。"

    for k, ex in [("put", put_ex), ("call", call_ex), ("put_counter", put_c_ex), ("call_counter", call_c_ex),
                  ("comparison", comp_ex), ("historical_facts", hist_ex), ("accepted_conditions", natural_c),
                  ("confirmation_excerpt", conf_ex)]:
        chk = dossier.inspect_excerpt_integrity(ex, text_body, expected_section=k)
        assert chk["complete"] is True, (k, chk)

    cv = review.validate_initial_reply_contract(
        {
            "raw_text": text_body,
            "decision": {
                "action": "Accept", "side": "call",
                "put": put_ex, "call": call_ex, "put_counter": put_c_ex, "call_counter": call_c_ex, "comparison": comp_ex,
                "depends_on_ard": False,
            },
            "condition_plan": {
                "invalidation": "entry_not_returned", "check_interval_ms": 300_000, "require_outside_at_t0": True,
                "historical_facts": hist_ex, "accepted_conditions": natural_c, "confirmation_excerpt": conf_ex,
            },
        },
        {"put": PUT_CONTRACT, "call": CALL_CONTRACT},
        strict_clock_5m=True,
        check_excerpt_completeness=True,
        require_outside_at_t0=True,
    )
    assert cv["status"] == "PASSED", cv
    # C mentioning 65,500-67,500 band must stay advisory only, NEVER blocking Accept!
    assert "c1_mechanical_advisory" in cv["layer2_semantic_risk"]


# ---------------------------------------------------------------------------
# 4. Anti-Bypass Gates (H2/H3/H5/M3) & Legacy `@1.0` Compatibility
# ---------------------------------------------------------------------------

def test_all_six_anti_bypass_gates_and_legacy_compat(tmp_path):
    pairs = {"put": {**PUT_CONTRACT, "short_strike_usd": 98_000.0, "long_strike_usd": 96_000.0},
             "call": {**CALL_CONTRACT, "short_strike_usd": 102_000.0, "long_strike_usd": 104_000.0}}
    req_p5 = review.build_initial_request_payload(
        "# u", contract_pair=pairs, model="deepseek-v4-pro",
        prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
    )

    # 1. Explicit strict_contract=False & strict_clock_5m=False CANNOT bypass @1.2-p5-candidate!
    bad_t0_reply = _build_valid_p5_reply(require_outside_at_t0=False)
    with pytest.raises(review.ContractValidationError, match="t0_outside_guard_required:False"):
        review.parse_initial_reply({"text": bad_t0_reply}, copy.deepcopy(req_p5), strict_contract=False, strict_clock_5m=False)

    omitted_t0_reply = _build_valid_p5_reply(require_outside_at_t0=None)
    with pytest.raises(review.ContractValidationError, match="t0_outside_guard_required:omitted"):
        review.parse_initial_reply({"text": omitted_t0_reply}, copy.deepcopy(req_p5), strict_contract=False)

    # H5: Route C (@1.1-candidate) preserves omitted -> fail-closed default True, explicit False still blocked
    req_v11 = review.build_initial_request_payload(
        "# u", contract_pair=pairs, model="deepseek-v4-pro",
        prompt_version=review.CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
    )
    parsed_v11 = review.parse_initial_reply({"text": omitted_t0_reply}, copy.deepcopy(req_v11))
    assert parsed_v11["condition_plan"]["require_outside_at_t0"] is True
    with pytest.raises(review.ContractValidationError, match="t0_outside_guard_required:False"):
        review.parse_initial_reply({"text": bad_t0_reply}, copy.deepcopy(req_v11))

    # 2. H2.4 Bidirectional prompt_version spoofing / missing version fails closed
    req_fwd_spoof = copy.deepcopy(req_p5)
    req_fwd_spoof["prompt_version"] = review.INITIAL_SYSTEM_PROMPT_VERSION
    req_fwd_spoof["messages"][0]["content"] = review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT + " "
    with pytest.raises(review.ContractValidationError, match="prompt_version_system_mismatch"):
        review.parse_initial_reply({"text": _build_valid_p5_reply()}, req_fwd_spoof)

    req_rev_spoof = copy.deepcopy(req_p5)
    req_rev_spoof["messages"][0]["content"] = review.INITIAL_SYSTEM_PROMPT
    with pytest.raises(review.ContractValidationError, match="prompt_version_system_mismatch"):
        review.parse_initial_reply({"text": _build_valid_p5_reply()}, req_rev_spoof)

    req_missing_pv = copy.deepcopy(req_p5)
    del req_missing_pv["prompt_version"]
    with pytest.raises(review.ContractValidationError, match="missing_prompt_version"):
        review.parse_initial_reply({"text": _build_valid_p5_reply()}, req_missing_pv)

    # 3. Top-level-only ARD fields fail closed when depends_on_ard=True
    top_ard_reply = _build_valid_p5_reply(depends_on_ard=True)
    # Remove ard_* from condition_plan and place at top-level JSON
    body_part, j_part = top_ard_reply.split("<ANNOTATION_JSON>")
    j_obj = json.loads(j_part.split("</ANNOTATION_JSON>")[0])
    j_obj["ard_responsibility_meaning"] = j_obj["condition_plan"].pop("ard_responsibility_meaning")
    j_obj["ard_review_on"] = j_obj["condition_plan"].pop("ard_review_on")
    crafted_ard = body_part + "<ANNOTATION_JSON>" + json.dumps(j_obj, ensure_ascii=False) + "</ANNOTATION_JSON>"
    with pytest.raises(review.ContractValidationError, match="ard_responsibility_meaning") as exc_ard:
        review.parse_initial_reply({"text": crafted_ard}, copy.deepcopy(req_p5))
    assert any("ard_condition_excerpt_missing" in err for err in exc_ard.value.contract_validation["layer1_deterministic"]["blocking_errors"])

    # 4. Reject / Unknown audit_scope='NON_ACCEPT_J1_ONLY', fails closed when truncated
    parsed_rej = review.parse_initial_reply({"text": _build_valid_p5_reply(action_header="## Reject")}, copy.deepcopy(req_p5))
    assert parsed_rej["contract_validation"]["audit_scope"] == "NON_ACCEPT_J1_ONLY"
    assert parsed_rej["contract_validation"]["status"] == "PASSED"
    with pytest.raises(review.ContractValidationError, match="j1_excerpt_incomplete"):
        review.parse_initial_reply({"text": _build_valid_p5_reply(action_header="## Reject", truncate_comparison=True)}, copy.deepcopy(req_p5))

    # 5. Dossiers.respond() closes spoofed contract_validation={'status':'PASSED'}, unknown version, and M3 malformed condition_plan without crashing
    wb, d, hand, src, cpair, req_d, now, exp_ms = _seed_locked_candidate_episode(tmp_path / "g1", SLOT)
    now[0] += 10
    parsed_ok = review.parse_initial_reply({"text": _build_valid_p5_reply()}, copy.deepcopy(req_d))
    bad_ann = dict(parsed_ok["annotation"], condition_plan=None)
    # Spoofed PASSED cannot bypass internal recomputation -> PARSE_FAILED
    att_spoof = d.respond(
        hand, "manual-spoof", "RECEIVED", parsed_ok["raw_bytes"], bad_ann,
        model="m", requested=now[0] - 5, decided=now[0], channel="MANUAL_IMPORT",
        prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
        system_sha256=req_d["system_sha256"], input_profile=player.INPUT_PROFILE_P0_STRICT,
        condition_plan=None, contract_validation={"status": "PASSED"},
    )
    assert att_spoof["status"] == "PARSE_FAILED"
    assert att_spoof["error_code"] == "CONTRACT_VALIDATION_FAILED"

    # M3: malformed invalidation in condition_plan records PARSE_FAILED cleanly instead of raising ValueError
    att_bad_plan = d.respond(
        hand, "manual-m3", "RECEIVED", parsed_ok["raw_bytes"], parsed_ok["annotation"],
        model="m", requested=now[0] - 5, decided=now[0], channel="MANUAL_IMPORT",
        prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
        system_sha256=req_d["system_sha256"], input_profile=player.INPUT_PROFILE_P0_STRICT,
        condition_plan=dict(parsed_ok["condition_plan"], invalidation="bogus_rule"),
    )
    assert att_bad_plan["status"] == "PARSE_FAILED"
    assert att_bad_plan["error_code"] == "CONTRACT_VALIDATION_FAILED"

    # Unknown prompt_version on Dossiers.respond() fails closed
    att_unk = d.respond(
        hand, "manual-unk", "RECEIVED", parsed_ok["raw_bytes"], parsed_ok["annotation"],
        model="m", requested=now[0] - 5, decided=now[0], channel="MANUAL_IMPORT",
        prompt_version="custom@9.9",
    )
    assert att_unk["status"] == "PARSE_FAILED"
    assert "unknown_prompt_version" in att_unk["error"]

    # Explicit @1.0 on p0_strict@1 hand (A+P0 shadow arm) succeeds without forcing candidate M/C prose rules!
    att_ap0 = d.respond(
        hand, "manual-ap0", "RECEIVED", parsed_ok["raw_bytes"], parsed_ok["annotation"],
        model="m", requested=now[0] - 5, decided=now[0], channel="MANUAL_IMPORT",
        prompt_version=review.INITIAL_SYSTEM_PROMPT_VERSION,
        system_sha256=hashlib.sha256(review.INITIAL_SYSTEM_PROMPT.encode()).hexdigest(),
        input_profile=player.INPUT_PROFILE_P0_STRICT,
    )
    assert att_ap0["status"] == "VALID"

    # 6. H3: tracking.link rejects bad candidate prose in condition_plan BEFORE calling service('lock') -> ZERO orphaned locks!
    wb2, d2, hand2, src2, cpair2, req_d2, now2, exp_ms2 = _seed_locked_candidate_episode(tmp_path / "g2", SLOT)
    now2[0] += 10
    bad_m_reply = _build_valid_p5_reply(custom_confirmation="破坏确认口径：每60分钟检查一次，触及短腿即进入。")
    # Bypass parse check temporarily by simulating raw with old respond so condition_plan gate is tested independently
    bad_ann_only = {k: LEGACY_EXCERPTS[k] for k in LEGACY_EXCERPTS}
    raw_bad = ("## Accept / Call\n" + "\n".join(LEGACY_EXCERPTS.values()) + "\n"
               + "\n".join([LEGACY_RULE["historical_facts"], LEGACY_RULE["accepted_conditions"],
                            "破坏确认口径：每60分钟检查一次，回到65,500及以下返回。"])).encode("utf-8")
    # Record using @1.0 attempt, then tamper request prompt_version to test accepted/condition_plan pre-lock defense
    att2 = d2.respond(
        hand2, "prelock-h3", "RECEIVED", raw_bad, {**bad_ann_only, "depends_on_ard": False},
        model="m", requested=now2[0] - 5, decided=now2[0], channel="API_AUTO",
        request_payload={**req_d2, "prompt_version": review.INITIAL_SYSTEM_PROMPT_VERSION},
        prompt_version=review.INITIAL_SYSTEM_PROMPT_VERSION,
    )
    assert att2["status"] == "VALID"
    # Simulate mismatched attempt vs request prompt_version -> Blocked('prompt_version_mismatch')
    req_json_path = hand2 / "responses" / dossier.key("prelock-h3") / "request.json"
    req_json = json.loads(req_json_path.read_text(encoding="utf-8"))
    req_json["prompt_version"] = review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION
    req_json_path.write_text(json.dumps(req_json), encoding="utf-8")
    lock_calls = []

    def spy_service(action, payload):
        lock_calls.append((action, payload))
        return {"state": "SHADOW_LOCK_CREATED"}

    res_mismatch = tracking.link(d2, hand2, spy_service, {**LEGACY_RULE, "confirmation_excerpt": "破坏确认口径：每60分钟检查一次，回到65,500及以下返回。"})
    assert res_mismatch["state"] == "LOCK_BLOCKED"
    assert res_mismatch["reason"] == "prompt_version_mismatch"
    assert lock_calls == []

    # 7. Manual freeze / Dossiers.episode() fails closed on wrong t0 price, wrong first_at_ms, and non-matching response prose
    wb3, d3, hand3, src3, cpair3, req_d3, now3, exp_ms3 = _seed_locked_candidate_episode(tmp_path / "g3", SLOT)
    now3[0] += 10
    p_valid = review.parse_initial_reply({"text": _build_valid_p5_reply()}, copy.deepcopy(req_d3))
    d3.respond(
        hand3, "cand-ok", "RECEIVED", p_valid["raw_bytes"], p_valid["annotation"],
        model="deepseek-v4-pro", requested=now3[0] - 5, decided=now3[0], channel="API_AUTO",
        request_payload=req_d3, condition_plan=p_valid["condition_plan"],
        prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
        system_sha256=req_d3["system_sha256"], input_profile=player.INPUT_PROFILE_P0_STRICT,
        contract_validation=p_valid["contract_validation"],
    )
    bad_t0_cond = dict(
        contract={k: cpair3["call"][k] for k in condition.CONTRACT_FIELDS},
        entry_at_ms=now3[0] + 1000,
        entry_index_usd=cpair3["call"]["short_strike_usd"],  # AT SHORT_TOUCH -> must fail!
        historical_facts=p_valid["condition_plan"]["historical_facts"],
        accepted_conditions=p_valid["condition_plan"]["accepted_conditions"],
        confirmation_excerpt=p_valid["condition_plan"]["confirmation_excerpt"],
        invalidation="entry_not_returned",
        invalidation_meaning=condition.RULES["entry_not_returned"],
        check_clock={"first_at_ms": ((now3[0] + 1000) // M + 1) * M, "interval_ms": 300_000},
        sampling=condition.SAMPLING,
        require_outside_at_t0=True,
        mechanical_contract_version="p2_strict@1",
    )
    entry_src = {"index_name": "btc_usd", "expiry_ms": exp_ms3, "fetched_at_ms": now3[0] + 2000,
                 "points": [[now3[0] + 1000, cpair3["call"]["short_strike_usd"]]]}
    with pytest.raises(ValueError, match="outside short strike"):
        d3.episode(hand3, bad_t0_cond, entry_src)

    # 8. Legacy @1.0 handoff (120s) + require_outside_at_t0=False remains 100% compatible
    legacy_clk = Clock(SLOT + M)
    legacy_root = tmp_path / "legacy_compat"
    d_leg = dossier.Dossiers(legacy_root, legacy_clk)
    h_leg = d_leg.expect(SLOT + base_plan()["expiry_offset_ms"], "SCHEDULED", plan=base_plan(), slot=SLOT)
    l_src = base_source(legacy_clk)
    d_leg.deal(h_leg, l_src)
    req_leg = review.build_initial_request_payload(
        (h_leg / "evidence" / "input.md").read_text(encoding="utf-8"),
        model="deepseek-v4-pro",
        prompt_version=review.INITIAL_SYSTEM_PROMPT_VERSION,
        requested_at_ms=legacy_clk.ms,
    )
    legacy_reply_raw = (
        "Accept / Call\n" + "\n".join(LEGACY_EXCERPTS.values()) + "\n"
        + "\n".join(LEGACY_RULE[k] for k in ("historical_facts", "accepted_conditions", "confirmation_excerpt"))
        + "\n<ANNOTATION_JSON>" + json.dumps({
            **LEGACY_EXCERPTS, "depends_on_ard": False,
            "condition_plan": {**LEGACY_RULE, "check_interval_ms": 60_000, "require_outside_at_t0": False},
        }, ensure_ascii=False) + "</ANNOTATION_JSON>"
    )
    parsed_leg = review.parse_initial_reply({"text": legacy_reply_raw}, req_leg)
    assert parsed_leg["decision"]["action"] == "Accept"
    assert parsed_leg["condition_plan"]["require_outside_at_t0"] is False


# ---------------------------------------------------------------------------
# 5. Runtime Reliability, Fault Injection & Hold Review C/M Separation (M1)
# ---------------------------------------------------------------------------

def test_p5_contract_failure_1_attempt_cap_crash_recovery_and_isolation(tmp_path, monkeypatch):
    clock = Clock(SLOT + M)
    d = dossier.Dossiers(tmp_path, clock)
    # Plant a corrupt old expired hand alongside the active scheduled candidate hand
    old_plan = dict(base_plan(), plan_id="legacy_old@1", effective_at_ms=SLOT - DAY)
    old_hand = d.expect(SLOT - DAY + old_plan["expiry_offset_ms"], "SCHEDULED", plan=old_plan, slot=SLOT - DAY)
    dossier.write(old_hand / "evidence/identity.json", b"{corrupt")

    p5_plan = dict(base_plan(), plan_id="p5_cand@2", prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION, input_profile=player.INPUT_PROFILE_P0_STRICT)
    monkeypatch.setattr(dossier, "collect", lambda *a, **kw: base_source(clock))

    bad_reply_text = _build_valid_p5_reply(require_outside_at_t0=False)
    send_calls = []

    def transport_bad(*args, **kwargs):
        send_calls.append(args)
        return {"choices": [{"message": {"content": bad_reply_text}, "finish_reason": "stop"}]}

    res1 = tracking.auto_deal(d, p5_plan, tmp_path, None, llm_config={"transport": transport_bad, "model": "deepseek-v4-pro"})
    # Old corrupt hand isolated, active hand ran ONCE and recorded PARSE_FAILED / CONTRACT_VALIDATION_FAILED without retry
    assert len(send_calls) == 1
    active = next(r for r in res1 if r.get("status") == "PARSE_FAILED")
    attempt_path = next(tmp_path.glob("dossiers/*/hands/hand_*/responses/*/attempt.json"))
    attempt = dossier.read(attempt_path)
    hand_dir = attempt_path.parent.parent.parent
    assert attempt["status"] == "PARSE_FAILED"
    assert attempt["error_code"] == "CONTRACT_VALIDATION_FAILED"
    assert attempt["configuration"]["delivery_state"] == "RECEIVED"
    assert (attempt_path.parent / "response.full.raw").is_file()

    # Re-running auto_deal NEVER pays for a second attempt
    res2 = tracking.auto_deal(d, p5_plan, tmp_path, None, llm_config={"transport": transport_bad, "model": "deepseek-v4-pro"})
    assert len(send_calls) == 1
    assert any(r.get("status") == "AUTO_ATTEMPTED" for r in res2)

    # Crash recovery test: crash after saving reply.json, recover_initial validates contract strictly with ZERO LLM calls
    root2 = tmp_path / "crash_rec"
    clk2 = Clock(SLOT + M)
    d2 = dossier.Dossiers(root2, clk2)
    p5_plan2 = dict(base_plan(), plan_id="p5_cand_rec@2", prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION, input_profile=player.INPUT_PROFILE_P0_STRICT)
    monkeypatch.setattr(dossier, "collect", lambda *a, **kw: base_source(clk2))

    class Boom(BaseException):
        pass

    calls2 = []

    def transport_good(*a, **kw):
        calls2.append(1)
        return {"choices": [{"message": {
            "content": _build_valid_p5_reply(side="call", short_strike=98_000, long_strike=96_000, call_short_strike=102_000, call_long_strike=104_000)
        }, "finish_reason": "stop"}]}

    with patch.object(review, "parse_initial_reply", side_effect=Boom):
        with pytest.raises(Boom):
            tracking.auto_deal(d2, p5_plan2, root2, None, llm_config={"transport": transport_good, "model": "deepseek-v4-pro"})
    assert len(calls2) == 1
    tracking.recover_initial(d2, None)
    assert len(calls2) == 1
    rec_att = dossier.read(next(root2.glob("dossiers/*/hands/hand_*/responses/*/attempt.json")))
    assert rec_att["status"] == "VALID"
    assert rec_att["contract_validation_status"] == "PASSED"


def test_hold_review_cm_separation_and_new_qualified_data_present(tmp_path):
    wb, d, hand, src, cpair, req_d, now, exp_ms = _seed_locked_candidate_episode(tmp_path, SLOT)
    now[0] += 10
    p_ok = review.parse_initial_reply(
        {"text": _build_valid_p5_reply(side="call", short_strike=98_000, long_strike=96_000, call_short_strike=102_000, call_long_strike=104_000)},
        copy.deepcopy(req_d),
    )
    d.respond(
        hand, "hold-test", "RECEIVED", p_ok["raw_bytes"], p_ok["annotation"],
        model="deepseek-v4-pro", requested=now[0] - 5, decided=now[0], channel="API_AUTO",
        request_payload=req_d, condition_plan=p_ok["condition_plan"],
        prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
        system_sha256=req_d["system_sha256"], input_profile=player.INPUT_PROFILE_P0_STRICT,
        contract_validation=p_ok["contract_validation"],
    )
    with serving(wb) as base:
        link_res = tracking.link(d, hand, wb_post(base, wb), p_ok["condition_plan"])
        assert link_res["state"] == "SHADOW_LOCK_CREATED"

    ep = next((hand.parent.parent / "episodes").glob("episode_*"))
    frozen = json.loads((ep / "condition.json").read_text(encoding="utf-8"))
    assert frozen["require_outside_at_t0"] is True
    assert frozen["mechanical_contract_version"] == "p2_strict@1"

    entry_at = frozen["entry_at_ms"]
    decision_sel = d.select(hand)
    # 1. Reviewer M1: only t0 point / pre-entry flow / unverified boundary minute -> new_qualified_data_present=False!
    t0_pt = _make_series_point(side="call", short_strike=103_000.0, long_strike=105_000.0, asof_ms=entry_at - 1_000, flow_asof_ms=entry_at - 5_000)
    t0_row = locked_compare.point_metrics(t0_pt, t0_pt)
    exported_t0_only = {
        "latest_point": t0_pt,
        "points": [t0_row],
        "t0": {"metrics": dict(t0_row, short_distance_pct=2.0, expected_move_usd=1800.0)},
    }
    captured_inputs = []

    def spy_hold_llm(messages, **kwargs):
        captured_inputs.append(messages[-1]["content"])
        return {
            "text": "HOLD\n虽然 D 出现短窗波动提醒，但当前无入场后新增合格数据验证 C 被推翻。\n<HOLD_REVIEW_JSON>"
                    + json.dumps({"verdict": "HOLD", "affected_conditions": ["C1"], "public_reply": "暂继续观察，等待新增合格证据。", "key_facts": ["无新合格数据"]}, ensure_ascii=False)
                    + "</HOLD_REVIEW_JSON>",
            "model": "deepseek-v4-pro",
        }

    # Check M1 fallback directly first: pre-entry flow & sample_count=1 at t0 MUST yield False
    md_fallback = review.build_hold_review_input(
        contract=frozen["contract"], entry_at_ms=entry_at, initial_decision=decision_sel, frozen_condition=frozen,
        triggers=[{"category": "D", "code": "SPOT_FLOW", "summary_cn": "测试D", "observed_at_ms": entry_at}],
        t0_summary={"flow_asof_ms": entry_at - 1_000},
        post_entry_summary={"sample_count": 1, "latest_ts_ms": entry_at},
        condition_summary={"program_triggered": False, "mechanical_condition_state": "INTACT"},
        ard_summary={"same_measure": True, "current_sample_available": True},
        flow_summary={"flow_ready": True, "flow_asof_ms": entry_at - 1_000},
        quote_summary={},
    )
    assert "`new_qualified_data_present`）：`False`" in md_fallback
    assert "严禁声称 C 已被证实仍有效" in md_fallback
    assert "C 仅用于独立复核与留痕，不授权自动提前平仓、甩腿或交易" in md_fallback
    assert "MECHANICAL_M" not in md_fallback
    assert "C_REVIEW_ONLY" in md_fallback

    # Now advance clock past entry with ONLY t0-aged data and an unverified boundary minute -> still False!
    # Step evaluate_hold_management via mock triggers
    now[0] = entry_at + M

    def dynamic_boundary_index(*a, **kw):
        return {
            "schema": "index_path@0.1", "index_name": "btc_usd", "expiry_ms": exp_ms,
            "timestamp_semantics": "unverified_minute_label",
            "points": [[now[0], 100_050.0]],  # t + 60s > now[0] -> always unverified boundary bucket!
        }

    d_eval = dossier.Dossiers(wb.config.shadow_dossier_root, clock=lambda: now[0])
    with patch.object(review, "detect_hold_triggers", return_value=[{"category": "C", "code": "PIN_SHIFT", "summary_cn": "C测试", "observed_at_ms": now[0]}]), \
         patch.object(tracking, "_merge_index_points", side_effect=dynamic_boundary_index), \
         patch.object(tracking.review, "call_chat_llm", side_effect=spy_hold_llm):
        # First tick queues under MIN_DEBOUNCE_MS; advance past MAX_DEBOUNCE_MS -> fires REVIEWED!
        res_q = tracking.evaluate_hold_management(
            d_eval, hand, exported_t0_only, archive_root=tmp_path, index_path=None,
            llm_config={"api_key": "k", "model": "deepseek-v4-pro"}, debounce_ms=0, cooldown_ms=0,
        )
        assert res_q["action"] == "QUEUED"
        now[0] += review.DEFAULT_DEBOUNCE_MS + 60_000
        res_hold = tracking.evaluate_hold_management(
            d_eval, hand, exported_t0_only, archive_root=tmp_path, index_path=None,
            llm_config={"api_key": "k", "model": "deepseek-v4-pro"}, debounce_ms=0, cooldown_ms=0,
        )
    assert res_hold["action"] == "REVIEWED"
    assert res_hold["review_channel"] == "C_REVIEW_ONLY"
    assert res_hold["new_qualified_data_present"] is False
    assert "`new_qualified_data_present`）：`False`" in captured_inputs[-1]

    # Advance past cooldown with genuinely NEW post-entry tracker point -> True!
    now[0] += review.DEFAULT_COOLDOWN_MS + 60_000
    pt_new = _make_series_point(side="call", short_strike=103_000.0, long_strike=105_000.0, asof_ms=now[0] - 1_000, spot=100_200.0, flow_asof_ms=now[0] - 1_000)
    m_new = locked_compare.point_metrics(pt_new, t0_pt)
    exported_new = {"latest_point": pt_new, "points": [t0_row, m_new], "t0": {"metrics": dict(t0_row)}}
    d_eval2 = dossier.Dossiers(wb.config.shadow_dossier_root, clock=lambda: now[0])
    with patch.object(review, "detect_hold_triggers", return_value=[{"category": "D", "code": "SPOT_FLOW_FLIP", "summary_cn": "D测试2", "observed_at_ms": now[0]}]), \
         patch.object(tracking, "_merge_index_points", side_effect=dynamic_boundary_index), \
         patch.object(tracking.review, "call_chat_llm", side_effect=spy_hold_llm):
        res_q2 = tracking.evaluate_hold_management(
            d_eval2, hand, exported_new, archive_root=tmp_path, index_path=None,
            llm_config={"api_key": "k", "model": "deepseek-v4-pro"}, debounce_ms=0, cooldown_ms=0,
        )
        assert res_q2["action"] == "QUEUED"
        now[0] += review.DEFAULT_DEBOUNCE_MS + 60_000
        res_hold2 = tracking.evaluate_hold_management(
            d_eval2, hand, exported_new, archive_root=tmp_path, index_path=None,
            llm_config={"api_key": "k", "model": "deepseek-v4-pro"}, debounce_ms=0, cooldown_ms=0,
        )
    assert res_hold2["action"] == "REVIEWED"
    assert res_hold2["new_qualified_data_present"] is True
    assert "`new_qualified_data_present`）：`True`" in captured_inputs[-1]
