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


_SEED_STRIKES = {
    "short_strike": 97_000,
    "long_strike": 95_000,
    "call_short_strike": 103_000,
    "call_long_strike": 105_000,
}
_BASE_SRC_STRIKES = {
    "short_strike": 66_000,
    "long_strike": 64_000,
    "call_short_strike": 67_000,
    "call_long_strike": 69_000,
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
    # 6. Finding #5: natural negation with 不把/切勿把 + explicit 1-minute index sampling vs 5-minute clock
    "不把保护腿67,500当作破坏上限；采用 entry_not_returned，每5分钟检查，严格高于65,500即计为进入。",
    "切勿把65,500至67,500解释为区间限制，每5分钟检查一次，严格高于65,500即进入，采用 entry_not_returned。",
    "按每1分钟指数采样判定进入，但检查时钟固定为每5分钟（300000ms）一次；采用 entry_not_returned，严格高于65,500即计为进入。",
    "每1分钟采样用于触发校验，每5分钟检查一次，采用 entry_not_returned，严格高于65,500即计为进入。",
    "逐分钟采集指数样本，固定每5分钟检查，采用 entry_not_returned，严格高于65,500即计为进入。",
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
    ("采用 entry_not_returned，每5分钟检查，按每1分钟固定检查判定，严格高于65,500即进入。", "clock_interval_mismatch"),
    ("采用 new_entry 规则，每5分钟检查，严格高于65,500即进入。", "invalidation_rule_mismatch"),
])
def test_c1_true_mechanical_violations_still_rejected(bad_prose, expected_v):
    res = condition.validate_confirmation_prose(
        bad_prose, CALL_CONTRACT, expected_invalidation="entry_not_returned", expected_interval_ms=300_000
    )
    assert res["valid"] is False
    assert expected_v in res["violations"], res

    res_new_entry_bypass = condition.validate_confirmation_prose(
        "采用 new_entry，每5分钟检查，严格高于65,500即进入，直到检查点仍在责任区内才触发。",
        CALL_CONTRACT,
        expected_invalidation="new_entry",
        expected_interval_ms=300_000,
    )
    assert res_new_entry_bypass["valid"] is False
    assert "invalidation_rule_mismatch" in res_new_entry_bypass["violations"], res_new_entry_bypass


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

    # Finding #5: numbered list items inside C remain complete, cross-section fallback from 承担条件： is blocked,
    # and unlabeled USD/% natural paragraphs without a competing 承担条件： header remain complete.
    c_numbered = "1. 冻结余量约 1,200 USD（1.8%），当前远离 65,500 短腿。\n2. 若入场后 SPOT_FLOW_FLIP 转净买则独立触发重审。"
    text_numbered = (
        "## Accept / Call\n"
        "承担条件：\n"
        f"{c_numbered}\n\n"
        "余量 1,200 USD（1.8%）尚未耗尽，未越过短腿且保留缓冲。\n\n"
        "有效破坏标准：采用 entry_not_returned，每5分钟检查，严格高于65,500即计为进入。\n"
    )
    chk_num = dossier.inspect_excerpt_integrity(c_numbered, text_numbered, expected_section="accepted_conditions")
    assert chk_num["complete"] is True, chk_num
    unlabeled_usd = "余量 1,200 USD（1.8%）尚未耗尽，未越过短腿且保留缓冲。"
    chk_cross_blocked = dossier.inspect_excerpt_integrity(unlabeled_usd, text_numbered, expected_section="accepted_conditions")
    assert chk_cross_blocked["complete"] is False, chk_cross_blocked
    unlabeled_doc = f"## Accept / Call\n\n{unlabeled_usd}\n\n其余背景说明。"
    chk_unlab = dossier.inspect_excerpt_integrity(unlabeled_usd, unlabeled_doc, expected_section="accepted_conditions")
    assert chk_unlab["complete"] is True, chk_unlab


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
    parsed_ok = review.parse_initial_reply({"text": _build_valid_p5_reply(**_SEED_STRIKES)}, copy.deepcopy(req_d))
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

    # Finding #3: @1.0 (and @1.1-candidate when plan expects @1.2) downgrade on p0_strict@1 dossier MUST fail closed!
    att_ap0 = d.respond(
        hand, "manual-ap0", "RECEIVED", parsed_ok["raw_bytes"], parsed_ok["annotation"],
        model="m", requested=now[0] - 5, decided=now[0], channel="MANUAL_IMPORT",
        prompt_version=review.INITIAL_SYSTEM_PROMPT_VERSION,
        system_sha256=hashlib.sha256(review.INITIAL_SYSTEM_PROMPT.encode()).hexdigest(),
        input_profile=player.INPUT_PROFILE_P0_STRICT,
    )
    assert att_ap0["status"] == "PARSE_FAILED"
    assert att_ap0["error_code"] == "CONTRACT_VALIDATION_FAILED"
    assert att_ap0["error"] == "p0_strict_forbids_legacy_v10_prompt"

    # Meanwhile, stateless dual-arm shadow comparison (@1.0 on p0_strict@1 User input) remains explicitly supported:
    req_stateless_a = review.build_initial_request_payload(
        (hand / "evidence" / "input.md").read_text(encoding="utf-8"),
        contract_pair=cpair,
        model="deepseek-v4-pro",
        prompt_version=review.INITIAL_SYSTEM_PROMPT_VERSION,
        input_profile=player.INPUT_PROFILE_P0_STRICT,
        requested_at_ms=now[0] - 5,
    )
    parsed_stateless_a = review.parse_initial_reply({"text": _build_valid_p5_reply(**_SEED_STRIKES)}, req_stateless_a)
    assert parsed_stateless_a["decision"]["action"] == "Accept"

    # 6. H3: tracking.link rejects bad condition_plan & prompt_version mismatch BEFORE calling service('lock') -> ZERO orphaned locks!
    wb2, d2, hand2, src2, cpair2, req_d2, now2, exp_ms2 = _seed_locked_candidate_episode(tmp_path / "g2", SLOT)
    now2[0] += 10
    p2_ok = review.parse_initial_reply({"text": _build_valid_p5_reply(**_SEED_STRIKES)}, copy.deepcopy(req_d2))
    att2_v10_blocked = d2.respond(
        hand2, "prelock-v10-blocked", "RECEIVED", p2_ok["raw_bytes"], p2_ok["annotation"],
        model="m", requested=now2[0] - 5, decided=now2[0], channel="API_AUTO",
        request_payload={**req_d2, "prompt_version": review.INITIAL_SYSTEM_PROMPT_VERSION},
        prompt_version=review.INITIAL_SYSTEM_PROMPT_VERSION,
        condition_plan=p2_ok["condition_plan"],
        input_profile=player.INPUT_PROFILE_P0_STRICT,
    )
    assert att2_v10_blocked["status"] == "PARSE_FAILED"
    assert att2_v10_blocked["error_code"] == "CONTRACT_VALIDATION_FAILED"

    att2 = d2.respond(
        hand2, "prelock-h3", "RECEIVED", p2_ok["raw_bytes"], p2_ok["annotation"],
        model="deepseek-v4-pro", requested=now2[0] - 5, decided=now2[0], channel="API_AUTO",
        request_payload=req_d2, condition_plan=p2_ok["condition_plan"],
        prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
        system_sha256=req_d2["system_sha256"], input_profile=player.INPUT_PROFILE_P0_STRICT,
        contract_validation=p2_ok["contract_validation"],
    )
    assert att2["status"] == "VALID"
    lock_calls = []

    def spy_service(action, payload):
        lock_calls.append((action, payload))
        return {"state": "SHADOW_LOCK_CREATED"}

    res_bad_cp = tracking.link(
        d2, hand2, spy_service,
        {**p2_ok["condition_plan"], "confirmation_excerpt": "破坏确认口径：每60分钟检查一次，回到65,500及以下返回。"},
    )
    assert res_bad_cp["state"] == "LOCK_BLOCKED"
    assert lock_calls == []

    # Simulate tampered request.json prompt_version vs attempt.json -> Blocked('prompt_version_mismatch')
    req_json_path = hand2 / "responses" / dossier.key("prelock-h3") / "request.json"
    req_json = json.loads(req_json_path.read_text(encoding="utf-8"))
    req_json["prompt_version"] = review.INITIAL_SYSTEM_PROMPT_VERSION
    req_json_path.write_text(json.dumps(req_json), encoding="utf-8")

    res_mismatch = tracking.link(d2, hand2, spy_service, p2_ok["condition_plan"])
    assert res_mismatch["state"] == "LOCK_BLOCKED"
    assert res_mismatch["reason"] == "prompt_version_mismatch"
    assert lock_calls == []

    # 7. Manual freeze / Dossiers.episode() fails closed on wrong t0 price, wrong first_at_ms, and non-matching response prose
    wb3, d3, hand3, src3, cpair3, req_d3, now3, exp_ms3 = _seed_locked_candidate_episode(tmp_path / "g3", SLOT)
    now3[0] += 10
    p_valid = review.parse_initial_reply({"text": _build_valid_p5_reply(**_SEED_STRIKES)}, copy.deepcopy(req_d3))
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


def test_finding3_6_downgrade_spoof_blocking_and_cli_response_persistence(tmp_path):
    # Finding #3: expected_prompt_version mismatch fails closed inside review._resolve_reply_prompt_version
    wb, d, hand, src, cpair, req_d, now, exp_ms = _seed_locked_candidate_episode(tmp_path / "f36", SLOT)
    now[0] += 10
    bad_exp_req = copy.deepcopy(req_d)
    bad_exp_req["expected_prompt_version"] = review.INITIAL_SYSTEM_PROMPT_VERSION
    with pytest.raises(review.ContractValidationError, match="prompt_version_mismatch"):
        review.parse_initial_reply({"text": _build_valid_p5_reply()}, bad_exp_req)

    # Finding #3: tracking._is_candidate_proof rejects @1.0 on p0_strict@1 and plan_prompt_version mismatch
    with pytest.raises(tracking.Blocked, match="prompt_version_mismatch"):
        tracking._is_candidate_proof({
            "prompt_version": review.INITIAL_SYSTEM_PROMPT_VERSION,
            "input_profile": player.INPUT_PROFILE_P0_STRICT,
        })
    with pytest.raises(tracking.Blocked, match="prompt_version_mismatch"):
        tracking._is_candidate_proof({
            "prompt_version": review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
            "input_profile": player.INPUT_PROFILE_P0_STRICT,
            "plan_prompt_version": review.INITIAL_SYSTEM_PROMPT_VERSION,
        })

    # Finding #3: Dossiers.episode & tracking.accepted block if selected attempt or expected plan was tampered to @1.0
    p_ok = review.parse_initial_reply({"text": _build_valid_p5_reply(**_SEED_STRIKES)}, copy.deepcopy(req_d))
    d.respond(
        hand, "cand-f3", "RECEIVED", p_ok["raw_bytes"], p_ok["annotation"],
        model="deepseek-v4-pro", requested=now[0] - 5, decided=now[0], channel="API_AUTO",
        request_payload=req_d, condition_plan=p_ok["condition_plan"],
        prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
        system_sha256=req_d["system_sha256"], input_profile=player.INPUT_PROFILE_P0_STRICT,
        contract_validation=p_ok["contract_validation"],
    )
    att_file = hand / "responses" / dossier.key("cand-f3") / "attempt.json"
    req_file = hand / "responses" / dossier.key("cand-f3") / "request.json"
    orig_att = dossier.read(att_file)
    orig_req = dossier.read(req_file)
    tampered_att = dict(orig_att, prompt_version=review.INITIAL_SYSTEM_PROMPT_VERSION)
    dossier.write(att_file, dossier.encode(tampered_att), replace=True)
    with pytest.raises(tracking.Blocked, match="prompt_version_mismatch"):
        tracking.accepted(hand)
    valid_t0_cond = dict(
        contract={k: cpair["call"][k] for k in condition.CONTRACT_FIELDS},
        entry_at_ms=now[0] + 1000,
        entry_index_usd=cpair["call"]["short_strike_usd"] - 500.0,
        historical_facts=p_ok["condition_plan"]["historical_facts"],
        accepted_conditions=p_ok["condition_plan"]["accepted_conditions"],
        confirmation_excerpt=p_ok["condition_plan"]["confirmation_excerpt"],
        invalidation="entry_not_returned",
        invalidation_meaning=condition.RULES["entry_not_returned"],
        check_clock={"first_at_ms": ((now[0] + 1000) // M + 1) * M, "interval_ms": 300_000},
        sampling=condition.SAMPLING,
        require_outside_at_t0=True,
        mechanical_contract_version="p2_strict@1",
    )
    entry_src = {"index_name": "btc_usd", "expiry_ms": exp_ms, "fetched_at_ms": now[0] + 2000,
                 "points": [[now[0] + 1000, cpair["call"]["short_strike_usd"] - 500.0]]}
    with pytest.raises(ValueError, match="selected attempt prompt_version differs from request.json"):
        d.episode(hand, valid_t0_cond, entry_src)
    dossier.write(att_file, dossier.encode(orig_att), replace=True)
    dossier.write(req_file, dossier.encode(orig_req), replace=True)

    # Finding #6: CLI `response` exits 2 AND persists failed PARSE_FAILED/CONTRACT_VALIDATION_FAILED attempt
    # Use current wall clock so CLI `main()` evaluates within AD_HOC reply deadline.
    cli_now = dossier.now_ms() - 5_000
    cli_exp = cli_now + 2 * HOUR
    cli_src = _shift_source_for_r16_slot(cli_exp, cli_now - 2_000)
    cli_root = tmp_path / "cli_persist"
    d_cli = dossier.Dossiers(cli_root, clock=lambda: cli_now)
    h_cli = d_cli.expect(cli_exp, "AD_HOC", reference="P5-CLI-F6")
    d_cli.deal(h_cli, cli_src, input_profile=player.INPUT_PROFILE_P0_STRICT)
    raw_path = tmp_path / "cli_reply.raw"
    raw_path.write_text(_build_valid_p5_reply(**_BASE_SRC_STRIKES), encoding="utf-8")
    tool_path = Path(__file__).resolve().parents[1] / "tools" / "astra_player_dossier_v1.py"
    proc = subprocess.run(
        [
            sys.executable, str(tool_path),
            "--root", str(cli_root),
            "response",
            "--expiry-ms", str(cli_exp),
            "--hand", h_cli.name,
            "--attempt", "cli-downgrade-1",
            "--status", "RECEIVED",
            "--raw", str(raw_path),
            "--model", "deepseek-v4-pro",
            "--prompt-version", review.INITIAL_SYSTEM_PROMPT_VERSION,
            "--requested-ms", str(cli_now),
            "--decided-ms", str(cli_now),
        ],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 2
    saved_att_path = h_cli / "responses" / dossier.key("cli-downgrade-1") / "attempt.json"
    assert saved_att_path.is_file(), f"CLI response failed to persist audit attempt: {proc.stderr}"
    saved_att = dossier.read(saved_att_path)
    assert saved_att["status"] == "PARSE_FAILED"
    assert saved_att["error_code"] == "CONTRACT_VALIDATION_FAILED"
    assert saved_att["prompt_version"] == review.INITIAL_SYSTEM_PROMPT_VERSION


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
            "content": _build_valid_p5_reply(side="call", **_BASE_SRC_STRIKES)
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
        {"text": _build_valid_p5_reply(side="call", **_SEED_STRIKES)},
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


def test_hold_review_unverified_revoke_and_no_retry_on_120s_timeout(tmp_path):
    # Finding #4: (a) retries=0 even when timeout_s=120 in both initial auto_deal & hold review;
    # (b) DISCRETIONARY_REVOKE_UNVERIFIED never fires SIMULATED_ORDER / SHADOW_ACTION on initial review or sync recovery.
    wb, d, hand, src, cpair, req_d, now, exp_ms = _seed_locked_candidate_episode(tmp_path / "f4", SLOT)
    now[0] += 10

    plan_cfg = {
        **base_plan(),
        "plan_id": "daily_r2@p5test",
        "prompt_version": review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
        "input_profile": player.INPUT_PROFILE_P0_STRICT,
        "mechanical_contract_version": condition.MECHANICAL_CONTRACT_VERSION,
        "effective_at_ms": SLOT,
    }
    auto_exp_ms = SLOT + plan_cfg["expiry_offset_ms"]
    d_auto = dossier.Dossiers(tmp_path / "f4_auto", clock=lambda: SLOT + M)
    h_auto = d_auto.expect(auto_exp_ms, "SCHEDULED", plan=plan_cfg, slot=SLOT)
    d_auto.deal(h_auto, _shift_source_for_r16_slot(auto_exp_ms, SLOT + M), input_profile=player.INPUT_PROFILE_P0_STRICT)
    init_calls = []

    def spy_init_llm(messages, **kwargs):
        init_calls.append(dict(kwargs))
        return {"text": _build_valid_p5_reply(action_header="## Reject"), "model": "deepseek-v4-pro"}

    with patch.object(tracking.review, "call_chat_llm", side_effect=spy_init_llm):
        res_auto = tracking.auto_deal(
            d_auto, plan_cfg, tmp_path / "arch_unused", lambda *_: {},
            llm_config={"api_key": "k", "model": "deepseek-v4-pro", "timeout_s": 120},
        )
    assert len(res_auto) == 1
    assert res_auto[0]["status"] == "VALID"
    assert len(init_calls) == 1
    assert init_calls[0]["retries"] == 0

    p_ok = review.parse_initial_reply(
        {"text": _build_valid_p5_reply(side="call", **_SEED_STRIKES)},
        copy.deepcopy(req_d),
    )
    d.respond(
        hand, "hold-f4", "RECEIVED", p_ok["raw_bytes"], p_ok["annotation"],
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
        entry_at = frozen["entry_at_ms"]
        now[0] = entry_at + M

        t0_pt = _make_series_point(side="call", short_strike=103_000.0, long_strike=105_000.0, asof_ms=entry_at - 1_000, flow_asof_ms=entry_at - 5_000)
        t0_row = locked_compare.point_metrics(t0_pt, t0_pt)
        pt_post = _make_series_point(side="call", short_strike=103_000.0, long_strike=105_000.0, asof_ms=now[0] - 1_000, spot=100_400.0, flow_asof_ms=now[0] - 1_000)
        m_post = locked_compare.point_metrics(pt_post, t0_pt)
        exported_post = {"latest_point": pt_post, "points": [t0_row, m_post], "t0": {"metrics": dict(t0_row)}}
        hold_calls = []

        def spy_unverified_revoke_llm(messages, **kwargs):
            hold_calls.append(dict(kwargs))
            return {
                "text": "REVOKE\n因未映射条件 C999 异常，建议撤销。\n<HOLD_REVIEW_JSON>"
                        + json.dumps({
                            "verdict": "REVOKE",
                            "affected_conditions": ["C999_UNMAPPED"],
                            "public_reply": "建议撤销但未映射到具体冻结条款。",
                            "key_facts": ["未映射条目 C999"],
                        }, ensure_ascii=False)
                        + "</HOLD_REVIEW_JSON>",
                "model": "deepseek-v4-pro",
            }

        def dynamic_idx(*_a, **_kw):
            return {
                "schema": "index_path@0.1", "index_name": "btc_usd", "expiry_ms": exp_ms,
                "timestamp_semantics": "unverified_minute_label",
                "points": [[now[0] - M, 100_100.0]],
            }

        d_eval = dossier.Dossiers(wb.config.shadow_dossier_root, clock=lambda: now[0])
        with patch.object(review, "detect_hold_triggers", return_value=[{"category": "D", "code": "SPOT_FLOW_FLIP", "summary_cn": "D测试", "observed_at_ms": now[0]}]), \
             patch.object(tracking, "_merge_index_points", side_effect=dynamic_idx), \
             patch.object(tracking.review, "call_chat_llm", side_effect=spy_unverified_revoke_llm):
            r_q = tracking.evaluate_hold_management(
                d_eval, hand, exported_post, archive_root=tmp_path / "f4", index_path=None,
                llm_config={"api_key": "k", "model": "deepseek-v4-pro", "timeout_s": 120},
                debounce_ms=0, cooldown_ms=0,
            )
            assert r_q["action"] == "QUEUED"
            now[0] += review.DEFAULT_DEBOUNCE_MS + 60_000
            r_rev = tracking.evaluate_hold_management(
                d_eval, hand, exported_post, archive_root=tmp_path / "f4", index_path=None,
                llm_config={"api_key": "k", "model": "deepseek-v4-pro", "timeout_s": 120},
                debounce_ms=0, cooldown_ms=0,
            )

        assert len(hold_calls) == 1
        assert hold_calls[0]["retries"] == 0
        assert r_rev["action"] == "REVIEWED"
        assert r_rev["protocol_status"] == "DISCRETIONARY_REVOKE_UNVERIFIED"
        assert r_rev["shadow_action"] == "REVOKE_UNVERIFIED_ATTRIBUTION"
        events = [dossier.read(p) for p in (ep / "events").glob("*.json")]
        assert not any(e["kind"] in {"SIMULATED_ORDER", "SHADOW_ACTION"} for e in events), events

        # Recovery/sync path must likewise NEVER execute an unverified prior REVOKE decision.
        tracking.sync(d_eval, hand, wb_post(base, wb), check_due=False, run_review=False)
        events_after = [dossier.read(p) for p in (ep / "events").glob("*.json")]
        assert not any(e["kind"] in {"SIMULATED_ORDER", "SHADOW_ACTION"} for e in events_after), events_after


# ---------------------------------------------------------------------------
# 6. Step 1 Source Readiness Gate, Prewarm & Promotion/Rollback Verification
# ---------------------------------------------------------------------------

def _make_ready_workbench_view(*, now_ms: int) -> dict:
    def _item(side: str, snap_id: str) -> dict:
        return {
            "side": side,
            "status": "snapshot",
            "snapshot": {"snapshot_id": snap_id, "candidate_id": f"cand_{side}", "contract": {"side": side}},
            "formal_evidence_eligible": True,
            "live_lock_eligible": True,
            "discovery_status": {
                "capture_state": "COMPLETE",
                "side_complete": True,
                "upstream_code": None,
                "delta_population": {"complete": True, "expected": 10, "observed": 10},
            },
        }

    return {
        "updated_at_ms": now_ms,
        "discovery_status": {
            "capture_state": "COMPLETE",
            "upstream_code": None,
            "observed_at_ms": now_ms,
        },
        "data_health": {
            "products": {
                "deribit.btc.discovery.current.v1": {
                    "status": "OK",
                    "capture_state": "COMPLETE",
                }
            }
        },
        "candidates": [_item("put", "snap_put_1"), _item("call", "snap_call_1")],
    }


def _make_ready_source(now_ms: int) -> dict:
    src = copy.deepcopy(base_source(Clock(now_ms)))
    for case, card in zip(src["cases"], src["cards"]):
        read_at = card["read_at_ms"]
        _set_case_layer(case, "L2.book.short", {"index_price": 100_000.0, "timestamp": read_at - 2_000}, asof_ms=read_at - 2_000)
        _set_case_layer(case, "L2.book.long", {"index_price": 100_002.0, "timestamp": read_at - 1_000}, asof_ms=read_at - 1_000)
        _set_case_layer(
            case,
            "L2.quote",
            {
                "kind": "sequential_leg_book_estimate",
                "credit_btc": 0.0045,
                "valuation_ts_ms": read_at - 500,
                "received_ts_ms": read_at - 500,
                "exchange_times_ms": {"short": read_at - 1_500, "long": read_at - 700},
                "leg_received_ts_ms": {"short": read_at - 1_200, "long": read_at - 500},
                "executable_or_filled": False,
                "gap_reasons": [],
            },
            asof_ms=read_at - 500,
        )
    min_read = min(c["read_at_ms"] for c in src["cards"])
    exp_ms = src["cards"][0]["expiry_ms"]
    bs_at = min_read - 4 * HOUR - 5 * M
    src["band_snapshot"] = {
        "snapshot_exported_ms": bs_at,
        "source_available_at_ms": bs_at,
        "factor_snapshot": {
            "anchor": {
                "band_half": 1200.0,
                "ready": True,
                "band_clamped": False,
                "freshness": "FRESH",
                "gex_source_ts_ms": bs_at - M,
            }
        },
    }
    src["history"] = [
        {
            "schema": "structure_snapshot@0.1",
            "at_ms": min_read - offset_m * M,
            "available_at_ms": min_read - offset_m * M + 2_000,
            "expiry_ms": exp_ms,
            "compat_backfill_applied": False,
            "pin_source": "deribit_public/get_book_summary_by_currency",
            "profile": {"walls": {"pin": 66_000.0}},
        }
        for offset_m in (180, 150, 120, 90, 60, 30, 10)
    ]
    return src


def test_evaluate_source_readiness_source_ready_and_upstream_blocked():
    # 1. Missing or non-dict inputs -> UPSTREAM_BLOCKED(missing_input)
    res_none = tracking.evaluate_source_readiness()
    assert res_none["status"] == "UPSTREAM_BLOCKED"
    assert res_none["reasons"] == ["missing_input"]
    assert res_none["checks"]["accept_lock_ready"] is False

    res_nondict = tracking.evaluate_source_readiness(source="bad", workbench_view=["bad"])
    assert res_nondict["status"] == "UPSTREAM_BLOCKED"
    assert res_nondict["reasons"] == ["missing_input"]

    # 2. Healthy workbench_view -> SOURCE_READY & accept_lock_ready=True
    now_ms = SLOT + 5 * M
    wb_ok = _make_ready_workbench_view(now_ms=now_ms)
    res_wb_ok = tracking.evaluate_source_readiness(workbench_view=wb_ok)
    assert res_wb_ok["status"] == "SOURCE_READY"
    assert res_wb_ok["reasons"] == []
    assert res_wb_ok["checks"]["live_lock_eligible_by_side"] == {"put": True, "call": True}
    assert res_wb_ok["checks"]["accept_lock_ready"] is True

    # live_lock_eligible=False does NOT block initial SOURCE_READY, but accept_lock_ready becomes False
    wb_no_lock = copy.deepcopy(wb_ok)
    wb_no_lock["candidates"][1]["live_lock_eligible"] = False
    res_wb_nolock = tracking.evaluate_source_readiness(workbench_view=wb_no_lock)
    assert res_wb_nolock["status"] == "SOURCE_READY"
    assert res_wb_nolock["checks"]["live_lock_eligible_by_side"] == {"put": True, "call": False}
    assert res_wb_nolock["checks"]["accept_lock_ready"] is False

    # 3. Workbench UPSTREAM_BLOCKED variants (PARTIAL / rate limit / held / historical / last_valid / delta incomplete)
    wb_partial = copy.deepcopy(wb_ok)
    wb_partial["discovery_status"]["capture_state"] = "PARTIAL"
    r_part = tracking.evaluate_source_readiness(workbench_view=wb_partial)
    assert r_part["status"] == "UPSTREAM_BLOCKED"
    assert "discovery_capture_not_complete:PARTIAL" in r_part["reasons"]

    wb_rate = copy.deepcopy(wb_ok)
    wb_rate["discovery_status"]["upstream_code"] = "deribit_rate_limited"
    r_rate = tracking.evaluate_source_readiness(workbench_view=wb_rate)
    assert r_rate["status"] == "UPSTREAM_BLOCKED"
    assert "upstream_code:deribit_rate_limited" in r_rate["reasons"]

    wb_dh_bad = copy.deepcopy(wb_ok)
    wb_dh_bad["data_health"]["products"]["deribit.btc.discovery.current.v1"]["capture_state"] = "PARTIAL"
    assert tracking.evaluate_source_readiness(workbench_view=wb_dh_bad)["status"] == "UPSTREAM_BLOCKED"

    wb_held = copy.deepcopy(wb_ok)
    wb_held["candidates"][0]["held_capture"] = {"age_ms": 5000}
    r_held = tracking.evaluate_source_readiness(workbench_view=wb_held)
    assert r_held["status"] == "UPSTREAM_BLOCKED"
    assert "candidate_put_held_capture" in r_held["reasons"]

    wb_hist = copy.deepcopy(wb_ok)
    wb_hist["candidates"][1]["is_historical_summary"] = True
    assert tracking.evaluate_source_readiness(workbench_view=wb_hist)["status"] == "UPSTREAM_BLOCKED"

    wb_lvc = copy.deepcopy(wb_ok)
    wb_lvc["candidates"][0].update({
        "status": "error",
        "snapshot": None,
        "formal_evidence_eligible": False,
        "live_lock_eligible": False,
        "last_valid_candidate": {
            "side": "put",
            "is_historical_summary": True,
            "formal_evidence_eligible": False,
        },
    })
    r_lvc = tracking.evaluate_source_readiness(workbench_view=wb_lvc)
    assert r_lvc["status"] == "UPSTREAM_BLOCKED"
    assert "candidate_put_last_valid_candidate_summary" in r_lvc["reasons"]

    wb_delta_inc = copy.deepcopy(wb_ok)
    wb_delta_inc["candidates"][1]["discovery_status"]["delta_population"]["complete"] = False
    r_delta = tracking.evaluate_source_readiness(workbench_view=wb_delta_inc)
    assert r_delta["status"] == "UPSTREAM_BLOCKED"
    assert "candidate_call_delta_population_incomplete" in r_delta["reasons"]

    wb_one_side = copy.deepcopy(wb_ok)
    wb_one_side["candidates"] = [wb_one_side["candidates"][0]]
    r_one = tracking.evaluate_source_readiness(workbench_view=wb_one_side)
    assert r_one["status"] == "UPSTREAM_BLOCKED"
    assert "candidate_missing:call" in r_one["reasons"]

    # 4. Valid source bundle + combined view/source check -> SOURCE_READY
    src_ok = _make_ready_source(now_ms)
    src_ok_now = max(src_ok["received_at_ms"], src_ok["cards"][1]["read_at_ms"]) + 1_000
    res_src_ok = tracking.evaluate_source_readiness(src_ok, now_ms=src_ok_now)
    assert res_src_ok["status"] == "SOURCE_READY", res_src_ok
    assert res_src_ok["reasons"] == []
    assert res_src_ok["checks"]["source_instruments_proven"] == 4
    assert res_src_ok["checks"]["source_has_band_snapshot"] is True
    assert res_src_ok["checks"]["source_index_points_count"] > 0

    wb_both = _make_ready_workbench_view(now_ms=src_ok_now)
    res_both = tracking.evaluate_source_readiness(src_ok, workbench_view=wb_both, now_ms=src_ok_now)
    assert res_both["status"] == "SOURCE_READY"
    assert res_both["checks"]["accept_lock_ready"] is True

    # 5. Source UPSTREAM_BLOCKED variants
    src_missing_side = copy.deepcopy(src_ok)
    src_missing_side["cards"] = [src_missing_side["cards"][0]]
    assert tracking.evaluate_source_readiness(src_missing_side)["status"] == "UPSTREAM_BLOCKED"

    src_backfilled = copy.deepcopy(src_ok)
    src_backfilled["cards"][0]["backfilled"] = True
    assert tracking.evaluate_source_readiness(src_backfilled)["status"] == "UPSTREAM_BLOCKED"

    src_gaps = copy.deepcopy(src_ok)
    src_gaps["cards"][1]["data_gaps"] = ["startup_card_from_native_case"]
    assert tracking.evaluate_source_readiness(src_gaps)["status"] == "UPSTREAM_BLOCKED"

    src_no_l2 = copy.deepcopy(src_ok)
    _set_case_layer(src_no_l2["cases"][1], "L2.book.long", {}, asof_ms=src_no_l2["cards"][1]["read_at_ms"] - 1_000)
    assert tracking.evaluate_source_readiness(src_no_l2)["status"] == "UPSTREAM_BLOCKED"

    src_future_read = copy.deepcopy(src_ok)
    res_fut = tracking.evaluate_source_readiness(src_future_read, now_ms=src_future_read["cards"][0]["read_at_ms"] - 1)
    assert res_fut["status"] == "UPSTREAM_BLOCKED"

    src_no_band = copy.deepcopy(src_ok)
    src_no_band["band_snapshot"] = None
    assert tracking.evaluate_source_readiness(src_no_band)["status"] == "UPSTREAM_BLOCKED"

    src_inactive_inst = copy.deepcopy(src_ok)
    src_inactive_inst["instruments"][0]["is_active"] = False
    assert tracking.evaluate_source_readiness(src_inactive_inst)["status"] == "UPSTREAM_BLOCKED"

    # Finding #2 tightened fail-closed gates:
    # (a) Stale workbench_view & stale discovery observed_at_ms
    wb_stale = _make_ready_workbench_view(now_ms=now_ms - 360_000)
    r_stale = tracking.evaluate_source_readiness(workbench_view=wb_stale, now_ms=now_ms)
    assert r_stale["status"] == "UPSTREAM_BLOCKED"
    assert "workbench_view_stale" in r_stale["reasons"]
    assert "discovery_observed_stale" in r_stale["reasons"]

    # (b) Omitted per-side discovery_status
    wb_no_side_disc = copy.deepcopy(wb_ok)
    del wb_no_side_disc["candidates"][1]["discovery_status"]
    r_no_side_disc = tracking.evaluate_source_readiness(workbench_view=wb_no_side_disc)
    assert r_no_side_disc["status"] == "UPSTREAM_BLOCKED"
    assert "candidate_call_discovery_status_missing" in r_no_side_disc["reasons"]

    # (c) Toy / non-freezable band_snapshot
    src_toy_band = copy.deepcopy(src_ok)
    src_toy_band["band_snapshot"] = {"schema": "band_snapshot@0.1", "half_width_usd": 1200.0, "at_ms": src_ok["cards"][0]["read_at_ms"] - 60_000}
    r_toy_band = tracking.evaluate_source_readiness(src_toy_band)
    assert r_toy_band["status"] == "UPSTREAM_BLOCKED"
    assert "source_band_snapshot_unfreezable" in r_toy_band["reasons"]

    # (d) Missing / invalid-clock L2.quote
    src_bad_quote = copy.deepcopy(src_ok)
    _set_case_layer(src_bad_quote["cases"][0], "L2.quote", {"credit_btc": 0.0045}, asof_ms=src_bad_quote["cards"][0]["read_at_ms"] - 500)
    r_bad_quote = tracking.evaluate_source_readiness(src_bad_quote)
    assert r_bad_quote["status"] == "UPSTREAM_BLOCKED"
    assert "source_case_put_invalid_l2_quote_clocks" in r_bad_quote["reasons"]

    # (e) Backfilled-only history rows (no native clocked observations)
    src_backfill_hist = copy.deepcopy(src_ok)
    for hr in src_backfill_hist["history"]:
        hr["compat_backfill_applied"] = True
    r_bf_hist = tracking.evaluate_source_readiness(src_backfill_hist)
    assert r_bf_hist["status"] == "UPSTREAM_BLOCKED"
    assert "source_history_no_native_clocked_rows" in r_bf_hist["reasons"]

    # (f) Cross-view source vs workbench_view contract expiry mismatch
    wb_bad_exp = copy.deepcopy(wb_both)
    wb_bad_exp["candidates"][0]["snapshot"]["contract"]["expiry_ms"] = src_ok["cards"][0]["expiry_ms"] + DAY
    r_mismatch = tracking.evaluate_source_readiness(src_ok, workbench_view=wb_bad_exp, now_ms=src_ok["cards"][1]["read_at_ms"] + 1_000)
    assert r_mismatch["status"] == "UPSTREAM_BLOCKED"
    assert "source_workbench_expiry_mismatch" in r_mismatch["reasons"]


def _shift_source_for_r16_slot(expiry_ms: int, read_ms: int) -> dict:
    src = copy.deepcopy(base_source(Clock(read_ms)))
    orig_call_read = max(c["read_at_ms"] for c in src["cards"])
    dt = read_ms - orig_call_read

    for case in src["cases"]:
        case["build"]["built_at_ms"] += dt
        for rows_list in (case.get("layers") or {}).values():
            for row in rows_list:
                if isinstance(row.get("asof_ms"), int):
                    row["asof_ms"] += dt
                vals = row.get("values") or {}
                if "expiry_ms" in vals:
                    vals["expiry_ms"] = expiry_ms
                if isinstance(vals.get("from_ms"), int):
                    vals["from_ms"] += dt
                if isinstance(vals.get("received_ts_ms"), int):
                    vals["received_ts_ms"] += dt
                if isinstance(vals.get("valuation_ts_ms"), int):
                    vals["valuation_ts_ms"] += dt
                for key_map in ("exchange_times_ms", "leg_received_ts_ms"):
                    if isinstance(vals.get(key_map), dict):
                        for k, v in list(vals[key_map].items()):
                            if isinstance(v, int):
                                vals[key_map][k] = v + dt
                for item in vals.get("reference_pool") or ():
                    if isinstance(item, dict) and isinstance(item.get("observation_end_ms"), int):
                        item["observation_end_ms"] += dt

    exp_day = dossier.utc(expiry_ms).date().isoformat()
    for card in src["cards"]:
        card["expiry_ms"] = expiry_ms
        card["expiry_day"] = exp_day
        card["read_at_ms"] += dt
        for lv in card.get("levels") or ():
            for w in ("before_6h", "before_24h"):
                seg = lv.get(w)
                if isinstance(seg, dict) and isinstance(seg.get("first_cross_ms"), int):
                    seg["first_cross_ms"] += dt

    src["index_path"]["expiry_ms"] = expiry_ms
    src["index_path"]["points"] = [[t + dt, p] for t, p in src["index_path"]["points"]]
    for row in src["history"]:
        row["expiry_ms"] = expiry_ms
        if isinstance(row.get("at_ms"), int):
            row["at_ms"] += dt
        if isinstance(row.get("available_at_ms"), int):
            row["available_at_ms"] += dt
    for inst in src["instruments"]:
        inst["expiration_timestamp"] = expiry_ms
    src["received_at_ms"] = read_ms
    return src


def test_p5_plan_promotion_cutover_and_formal_a_rollback(tmp_path, monkeypatch):
    deploy_dir = Path(__file__).resolve().parents[1] / "deploy" / "player_dossier"
    v2_plan = json.loads((deploy_dir / "plan.daily_r16.v2.json").read_text(encoding="utf-8"))
    v3_plan = json.loads((deploy_dir / "plan.daily_r16.v3_p5.json").read_text(encoding="utf-8"))
    manifest = json.loads((deploy_dir / "production_manifest.json").read_text(encoding="utf-8"))

    assert v2_plan["plan_id"] == "daily_r16@2"
    assert v2_plan["research"] == dossier.RESEARCH
    assert v3_plan["plan_id"] == "daily_r16@3"
    assert v3_plan["supersedes"] == "daily_r16@2"
    assert v3_plan["research"] == dossier.RESEARCH
    assert v3_plan["prompt_version"] == review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION
    assert v3_plan["input_profile"] == player.INPUT_PROFILE_P0_STRICT
    assert v3_plan["mechanical_contract_version"] == condition.MECHANICAL_CONTRACT_VERSION
    assert v3_plan["execution_allowed"] is False

    expected_p5_rel = {
        "tools/astra_player_tracking_v13.py",
        "tools/astra_player_review_v1.py",
        "tools/astra_player_dossier_v1.py",
        "tools/astra_player_input_v1.py",
        "tools/astra_player_condition_v1.py",
        "tools/astra_anchor_relative_v1.py",
    }
    assert set(manifest["p5_release_candidate"]["p5_owned_runtime_files"]) == expected_p5_rel
    core_rels = {v["source_rel"] for v in manifest["deployed_core_files"].values()}
    assert expected_p5_rel <= core_rels
    assert manifest["deployed_core_files"]["/opt/signal-audit-tools/astra_anchor_relative_v1.py"]["deployed_sha256_prefix"] == "f4e7ffb137b2"

    arch_dir = tmp_path / "archive"
    for d_name in ("cases", "levels", "structure", "band_snapshots"):
        (arch_dir / d_name).mkdir(parents=True, exist_ok=True)

    slot_day1 = v3_plan["effective_at_ms"]  # 2026-11-23T16:00:00Z
    slot_day0 = slot_day1 - DAY             # 2026-11-22T16:00:00Z
    slot_day2 = slot_day1 + DAY             # 2026-11-24T16:00:00Z
    exp_day0 = slot_day0 + v2_plan["expiry_offset_ms"]
    exp_day1 = slot_day1 + v3_plan["expiry_offset_ms"]
    exp_day2 = slot_day2 + v2_plan["expiry_offset_ms"]

    warm_v3 = tracking.prewarm(
        v3_plan, arch_dir, lambda *_: {"enabled": True},
        clock=lambda: slot_day1 - 5 * M, public=lambda *a, **k: [],
    )
    assert warm_v3["state"] == "PREWARM"
    assert warm_v3["prompt_version"] == "player_initial_system@1.2-p5-candidate"
    assert warm_v3["input_profile"] == "p0_strict@1"
    assert warm_v3["mechanical_contract_version"] == "p2_strict@1"

    warm_v2 = tracking.prewarm(
        v2_plan, arch_dir, lambda *_: {"enabled": True},
        clock=lambda: slot_day2 - 5 * M, public=lambda *a, **k: [],
    )
    assert warm_v2["prompt_version"] == "player_initial_system@1.0"
    assert warm_v2["input_profile"] == "legacy_v1"
    assert warm_v2["mechanical_contract_version"] is None

    with pytest.raises(ValueError, match="unsupported plan prompt_version"):
        tracking.prewarm(dict(v3_plan, prompt_version="player_initial_system@9.9"), arch_dir, lambda *_: {}, dry_run=True)

    clock = Clock(slot_day0 + 2 * M)
    d = dossier.Dossiers(tmp_path / "dossiers", clock)
    monkeypatch.setattr(
        dossier,
        "collect",
        lambda _arch, expiry, slot, **_kw: _shift_source_for_r16_slot(expiry, clock()),
    )

    seen_prompts = []
    legacy_reject_text = (
        "Reject\n"
        + "\n".join(LEGACY_EXCERPTS.values())
        + "\n<ANNOTATION_JSON>"
        + json.dumps({**LEGACY_EXCERPTS, "depends_on_ard": False}, ensure_ascii=False)
        + "</ANNOTATION_JSON>"
    )
    p5_reject_text = _build_valid_p5_reply(action_header="## Reject")

    def transport(api_key, model, body, timeout, endpoint=None):
        sys_msg = body["messages"][0]["content"]
        seen_prompts.append(sys_msg)
        reply_txt = p5_reject_text if sys_msg == review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT else legacy_reject_text
        return {"choices": [{"message": {"content": reply_txt}, "finish_reason": "stop"}]}

    llm_cfg = {"transport": transport, "model": "deepseek-v4-pro"}

    # Day 0: run formal A (daily_r16@2) -> @1.0 + legacy_v1
    res_day0 = tracking.auto_deal(d, v2_plan, arch_dir, lambda *_: {}, llm_config=llm_cfg)
    assert len(res_day0) == 1
    assert res_day0[0]["status"] == "VALID"
    hand_day0 = d.path(exp_day0) / "hands" / res_day0[0]["hand_id"]
    req_day0 = dossier.read(next((hand_day0 / "responses").glob("*/request.json")))
    ident_day0 = dossier.read(hand_day0 / "evidence" / "identity.json")
    assert req_day0["prompt_version"] == review.INITIAL_SYSTEM_PROMPT_VERSION
    assert req_day0.get("input_profile") is None
    assert req_day0.get("mechanical_contract_version") is None
    assert "input_profile" not in ident_day0
    assert "输入规范：p0_strict@1" not in (hand_day0 / "evidence" / "input.md").read_text(encoding="utf-8")

    # Settle Day 0 before cutover
    clock.ms = exp_day0 + 5 * M
    out_day0 = d.settle(exp_day0, 66_500.0)
    assert out_day0["classification"] == "OFFICIAL"
    d.save_outcome(out_day0, {"delivery_price": 66_500.0})

    # Day 1: cutover to daily_r16@3 -> @1.2-p5-candidate + p0_strict@1 + p2_strict@1
    clock.ms = slot_day1 + 2 * M
    res_day1 = tracking.auto_deal(d, v3_plan, arch_dir, lambda *_: {}, llm_config=llm_cfg)
    assert len(res_day1) == 1
    assert res_day1[0]["status"] == "VALID"
    hand_day1 = d.path(exp_day1) / "hands" / res_day1[0]["hand_id"]
    req_day1 = dossier.read(next((hand_day1 / "responses").glob("*/request.json")))
    att_day1 = dossier.read(next((hand_day1 / "responses").glob("*/attempt.json")))
    ident_day1 = dossier.read(hand_day1 / "evidence" / "identity.json")
    assert req_day1["prompt_version"] == "player_initial_system@1.2-p5-candidate"
    assert req_day1["input_profile"] == "p0_strict@1"
    assert req_day1["mechanical_contract_version"] == "p2_strict@1"
    assert att_day1["prompt_version"] == "player_initial_system@1.2-p5-candidate"
    assert att_day1["input_profile"] == "p0_strict@1"
    assert att_day1["contract_validation_status"] == "PASSED"
    assert ident_day1["input_profile"] == "p0_strict@1"
    assert "输入规范：p0_strict@1" in (hand_day1 / "evidence" / "input.md").read_text(encoding="utf-8")

    # Settle Day 1 cleanly
    clock.ms = exp_day1 + 5 * M
    out_day1 = d.settle(exp_day1, 66_500.0)
    assert out_day1["classification"] == "OFFICIAL"
    d.save_outcome(out_day1, {"delivery_price": 66_500.0})

    # Day 2: 1-step rollback to formal A (daily_r16@2) -> resumes @1.0 + legacy_v1 without overwriting Day 1
    clock.ms = slot_day2 + 2 * M
    res_day2 = tracking.auto_deal(d, v2_plan, arch_dir, lambda *_: {}, llm_config=llm_cfg)
    assert len(res_day2) == 1
    assert res_day2[0]["status"] == "VALID"
    hand_day2 = d.path(exp_day2) / "hands" / res_day2[0]["hand_id"]
    req_day2 = dossier.read(next((hand_day2 / "responses").glob("*/request.json")))
    ident_day2 = dossier.read(hand_day2 / "evidence" / "identity.json")
    assert req_day2["prompt_version"] == review.INITIAL_SYSTEM_PROMPT_VERSION
    assert req_day2.get("input_profile") is None
    assert req_day2.get("mechanical_contract_version") is None
    assert "input_profile" not in ident_day2
    assert dossier.read(hand_day1 / "expected.json")["plan"]["plan_id"] == "daily_r16@3"

    # Settle Day 2 cleanly & verify audit_record reads all three hands (@1.0, @1.2-p5-candidate, @1.0)
    clock.ms = exp_day2 + 5 * M
    out_day2 = d.settle(exp_day2, 66_500.0)
    assert out_day2["classification"] == "OFFICIAL"
    d.save_outcome(out_day2, {"delivery_price": 66_500.0})

    assert len(seen_prompts) == 3
    assert seen_prompts[0] == review.INITIAL_SYSTEM_PROMPT
    assert seen_prompts[1] == review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT
    assert seen_prompts[2] == review.INITIAL_SYSTEM_PROMPT

    for hand_dir in (hand_day0, hand_day1, hand_day2):
        rec = tracking.audit_record(hand_dir, clock(), None, detail=True)
        assert rec["state"] == "Reject"
        assert rec["outcome"]["classification"] == "OFFICIAL"


def test_finding1_cross_plan_settled_tick_skip_and_v4_rollback_plan(tmp_path):
    deploy_dir = Path(__file__).resolve().parents[1] / "deploy" / "player_dossier"
    v2_plan = json.loads((deploy_dir / "plan.daily_r16.v2.json").read_text(encoding="utf-8"))
    v3_plan = json.loads((deploy_dir / "plan.daily_r16.v3_p5.json").read_text(encoding="utf-8"))
    v4_plan = json.loads((deploy_dir / "plan.daily_r16.v4_rollback_formal_a.json").read_text(encoding="utf-8"))

    assert "08:05 UTC–15:50 UTC" in v3_plan["cutover_note"]
    assert "NEVER during 16:18–08:00 UTC" in v3_plan["cutover_note"]
    assert v4_plan["plan_id"] == "daily_r16@4"
    assert v4_plan["supersedes"] == "daily_r16@3"
    assert v4_plan["prompt_version"] == review.INITIAL_SYSTEM_PROMPT_VERSION
    assert v4_plan["input_profile"] == player.INPUT_PROFILE_LEGACY
    assert v4_plan["execution_allowed"] is False

    slot0 = v2_plan["effective_at_ms"]
    exp0 = slot0 + v2_plan["expiry_offset_ms"]
    clk = Clock(slot0 + 2 * M)
    d = dossier.Dossiers(tmp_path / "f1_tick", clk)
    h0 = d.expect(exp0, "SCHEDULED", plan=v2_plan, slot=slot0)
    d.deal(h0, _shift_source_for_r16_slot(exp0, clk()))

    # Open (unexpired) slot with mismatched plan STILL raises ValueError during quiet cross-plan check.
    v3_open = dict(v3_plan, effective_at_ms=slot0)
    with pytest.raises(ValueError, match="different configuration"):
        d.tick(v3_open)

    # Once slot0 is settled (at >= exp0), quiet-window auto_walk skips the historical slot without raising.
    clk.ms = exp0 + 10 * M
    ticked = d.tick(v3_open)
    assert ticked == [] or all(p != h0 for p in ticked)

    # And rolling back cleanly using v4_plan on the next quiet slot initializes @1.0 + legacy_v1!
    slot1 = v4_plan["effective_at_ms"]
    clk.ms = slot1 + 2 * M
    out_v4 = d.tick(v4_plan)
    assert any((p / "expected.json").is_file() and dossier.read(p / "expected.json")["plan"]["plan_id"] == "daily_r16@4" for p in out_v4)


# ---------------------------------------------------------------------------
# Step 1 & Step 2 Hardening: Cross-Section Excerpt, Positive M, C->M,
# CLI MANUAL_IMPORT/LATE, 6311071c Readiness/Pending-Lock Interop, Fault Injection
# ---------------------------------------------------------------------------

_D959_CONTRACTS = {
    "put": {
        "side": "put",
        "short_strike_usd": 59_000.0,
        "long_strike_usd": 57_000.0,
        "expiry_ms": 1795248000000,
        "quantity_btc": 1.0,
        "venue": "Deribit",
        "underlying": "BTC",
        "settlement_currency": "BTC",
        "short_instrument": "BTC-21NOV26-59000-P",
        "long_instrument": "BTC-21NOV26-57000-P",
    },
    "call": {
        "side": "call",
        "short_strike_usd": 60_000.0,
        "long_strike_usd": 62_000.0,
        "expiry_ms": 1795248000000,
        "quantity_btc": 1.0,
        "venue": "Deribit",
        "underlying": "BTC",
        "settlement_currency": "BTC",
        "short_instrument": "BTC-21NOV26-60000-C",
        "long_instrument": "BTC-21NOV26-62000-C",
    },
}

_D959_PUT = (
    "Put路径判断：按严格as-of合格图表末点59,366.5 USD，Put短腿59,000 USD位于责任区外且入区前没有已列主要位置，只能直接审查短腿，不虚构缓冲。"
    "该路径的有利事实是窗口采样未见进入责任区，原生盘口在2026-11-20 15:59:44 UTC给出59,395.38 USD，高于短腿59,000；不利事实是短腿距离只有约366.5 USD，"
    "约0.17倍本期预期波动尺度，并且最后5分钟从59,530.4下降到59,366.5，正在向短腿方向收缩，4小时和12小时现货成交柱净主动卖与价格下行同向，"
    "但这些现货流标为OBSERVED_UNALIGNED，不能升格为已成立反证。因此Put路径当前仍在责任外，但剩余余量很薄，尚不足以排除，却不是优先选择。"
)
_D959_CALL = (
    "Call路径判断：按严格as-of合格图表末点59,366.5 USD，Call短腿60,000 USD位于责任区外且入区前没有已列主要位置；本期Pin位于60,000与短腿同位，不另算一站，直接审查短腿。"
    "有利事实是当前严格低于短腿，原生盘口在2026-11-20 15:59:46 UTC给出59,385.63 USD，低于60,000，距离约633.5 USD，约0.30倍本期预期波动尺度；"
    "近5分钟从59,530.4到59,366.5的下行对Call短腿是远离义务方向，且最近一次严格返回短腿外侧的采样确认时间为2026-11-20 15:43:00 UTC。"
    "不利事实是窗口内曾记录到责任区内甚至越过保护腿的采样，最深为2026-11-19 17:35:00 UTC的63,288.5 USD，进入起点未被覆盖。因此Call路径存在历史反证，但当前已回到责任外且路径未向短腿压缩。"
)
_D959_PUT_CNT = (
    "Put最强反证：最不利的是当前离短腿59,000只有约366.5 USD，约0.17倍尺度，近5分钟价格和同窗现货净主动卖均指向短腿方向；其中现货主动流为OBSERVED_UNALIGNED，"
    "不能升格为已成立反证，但也未见同源可核验的缓和。窗口采样未见进入责任区不等于存在可靠缓冲，且当前没有中间结构可依赖。该反证不自动排除Put，但意味着Put的承担更需要尚未出现的稳定或反弹，安全余量条件较差。"
)
_D959_CALL_CNT = (
    "Call最强反证：最不利的是窗口内曾出现深度进入责任区且越过保护腿的采样，首个已覆盖采样2026-11-19 16:01:00 UTC为63,054.6 USD，已高于保护腿；"
    "已覆盖最深采样为2026-11-19 17:35:00 UTC为63,288.5 USD，超过短腿3,288.5 USD。该历史越界不能因当前回到责任外而抹去，但其最初进入起点未被本窗口覆盖，不能描述为完整连续留区；"
    "最近一次返回责任外的采样确认时间为2026-11-20 15:43:00 UTC，当前仍严格低于60,000，且没有可核验的上行主动流确认同向增强。因此该反证须纳入承担条件，但不单独排除Call。"
)
_D959_COMP = (
    "两侧对比择优：Put虽然没有责任区内历史，但距离短腿仅约366.5 USD、约为0.17倍尺度，近端价格和未对齐现货主动流同向提示压向短腿，剩余路径较薄；"
    "Call虽有深度越界历史，但距离约633.5 USD、约为0.30倍尺度，当前严格低于短腿且近端5分钟下行路径是远离Call义务方向，未对齐同窗现货净卖仅作提示，"
    "返回责任外的记录较近并且当前没有可核验的上行逼近。Call的相对优势在于当前剩余余量更大且路径并未压缩向短腿，而不是把历史未进入或OI数量当作可靠阻挡；"
    "Call的最强反证是已发生的深度越界，但已由后续采样回到责任外。因此本次选择有条件接受Call侧，并保留后续严格越界的机械破坏确认。"
)
_D959_HIST = (
    "冻结历史事实：本次冻结接受Call侧，严格as-of合格图表末点为2026-11-20 15:59:00 UTC的59,366.5 USD，原生盘口指数59,385.63 USD，均严格低于Call短腿60,000；"
    "责任区60,000至62,000之间的下一处已列位置61,000不是入区前缓冲。窗口内责任历史为2026-11-19 16:01:00 UTC首个采样63,054.6 USD已越过保护腿，"
    "已覆盖最深采样为2026-11-19 17:35:00 UTC的63,288.5 USD，最近一次严格返回短腿外侧的采样确认时间为2026-11-20 15:43:00 UTC，读取时仍严格低于60,000；其最初进入时刻未被本窗口覆盖，不作为完整连续留区起点。"
)
_D959_C = (
    "承担条件（C）：其一，冻结时正面承担的最强已知不利事实是Call侧曾在窗口内深度进入责任区并越过保护腿，最深采样63,288.5 USD且进入起点未覆盖，说明该侧不是零历史风险。"
    "其二，尚未排除承保依据的理由是合格图表末点59,366.5 USD与原生盘口指数59,385.63 USD均严格低于60,000，当前没有可核验的上行逼近Call短腿，现货主动流主要表现为净卖，作为未对齐提示不是与Call义务方向不利的同向组合。"
    "其三，关键不确定性为4小时和12小时现货主动流是OBSERVED_UNALIGNED，图表分钟标签语义为UNKNOWN，历史进入起点未覆盖，ARD与现货量价分源不同口径。"
    "其四，独立于机械M且可由系统盘中真实数据源复核的未来证伪事实包括短腿60,000下方剩余外侧余量与路径演变、时钟对齐后的同源4小时或12小时现货主动流转为上行净买、以及同口径本期Pin和ARD迁移显示价格重新贴近Call短腿。"
    "上述复核触发仅启动独立复核与留痕，不授权自动退出或交易。"
)
_D959_M = (
    "机械破坏确认口径（M）：采用entry_not_returned。首次检查为入场后下一UTC整分钟，之后每5分钟步进，间隔为check_interval_ms=300000，不用自然整点网格或60分钟锚点；"
    "入场时要求指数采样严格低于60,000，即require_outside_at_t0=true。采用Deribit BTC/USD指数分钟采样，Call短腿的严格进入为分钟采样严格高于60,000，触及60,000不算进入也不算返回；"
    "返回短腿外侧为分钟采样严格低于60,000。若在固定检查点可确认已出现严格高于60,000的采样，并且至该固定检查点仍未出现严格低于60,000的返回采样，才触发机械破坏；检查间或分钟内的连续状态不凭空补造。"
)


def _d959_reply_text(*, confirmation_excerpt: str) -> str:
    prose = "\n\n".join([
        "## Accept / Call",
        _D959_PUT, _D959_CALL, _D959_PUT_CNT, _D959_CALL_CNT,
        _D959_COMP, _D959_HIST, _D959_C, _D959_M,
    ])
    ann = {
        "put": _D959_PUT,
        "call": _D959_CALL,
        "comparison": _D959_COMP,
        "put_counter": _D959_PUT_CNT,
        "call_counter": _D959_CALL_CNT,
        "depends_on_ard": False,
        "condition_plan": {
            "invalidation": "entry_not_returned",
            "check_interval_ms": 300_000,
            "require_outside_at_t0": True,
            "historical_facts": _D959_HIST,
            "accepted_conditions": _D959_C,
            "confirmation_excerpt": confirmation_excerpt,
        },
    }
    return prose + "\n\n<ANNOTATION_JSON>\n" + json.dumps(ann, ensure_ascii=False) + "\n</ANNOTATION_JSON>"


def test_step1_d959_cross_section_regression_and_positive_m_anchor(monkeypatch):
    # 1. Exact real hand_d9598e9b Arm B defect: confirmation_excerpt copied `comparison`
    bad_text = _d959_reply_text(confirmation_excerpt=_D959_COMP)
    req_p5 = review.build_initial_request_payload(
        "# mock user",
        contract_pair=_D959_CONTRACTS,
        model="deepseek-reasoner",
        prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
        input_profile=player.INPUT_PROFILE_P0_STRICT,
        fidelity="EXACT",
    )
    with pytest.raises(review.ContractValidationError) as exc_info:
        review.parse_initial_reply({"text": bad_text, "model": "deepseek-reasoner"}, req_p5, contracts=_D959_CONTRACTS)
    cv_fail = exc_info.value.contract_validation
    assert cv_fail["status"] == "CONTRACT_FAILED"
    errs = cv_fail["layer1_deterministic"]["blocking_errors"]
    assert "j1_excerpt_incomplete:condition_plan.confirmation_excerpt:WRONG_SECTION" in errs
    assert "j1_excerpt_duplicate_section:condition_plan.confirmation_excerpt" in errs
    assert "m1_contract_violation:missing_m_mechanical_anchor" in errs

    # 2. Swapping confirmation_excerpt to the true L17 M section passes deterministically
    good_text = _d959_reply_text(confirmation_excerpt=_D959_M)
    parsed_ok = review.parse_initial_reply({"text": good_text, "model": "deepseek-reasoner"}, req_p5, contracts=_D959_CONTRACTS)
    assert parsed_ok["contract_validation"]["status"] == "PASSED"

    # 3. Direct mock forcing inspect_excerpt_integrity(complete=True, section='comparison') triggers j1_excerpt_wrong_section
    orig_inspect = dossier.inspect_excerpt_integrity

    def fake_inspect(excerpt, text, *, expected_section=None):
        if expected_section == "confirmation_excerpt":
            return {
                "status": "COMPLETE_PARAGRAPH",
                "complete": True,
                "matched": True,
                "section": "comparison",
                "expected_section": expected_section,
                "coverage_ratio": 1.0,
            }
        return orig_inspect(excerpt, text, expected_section=expected_section)

    monkeypatch.setattr(dossier, "inspect_excerpt_integrity", fake_inspect)
    parsed_for_contract = {
        "raw_text": good_text.split("<ANNOTATION_JSON>")[0],
        "decision": {
            **json.loads(good_text.split("<ANNOTATION_JSON>")[1].split("</ANNOTATION_JSON>")[0]),
            "action": "Accept",
            "side": "call",
        },
    }
    parsed_for_contract["condition_plan"] = parsed_for_contract["decision"]["condition_plan"]
    cv_mock = review.validate_initial_reply_contract(parsed_for_contract, contracts=_D959_CONTRACTS)
    assert cv_mock["status"] == "CONTRACT_FAILED"
    assert "j1_excerpt_wrong_section:condition_plan.confirmation_excerpt:comparison" in cv_mock["layer1_deterministic"]["blocking_errors"]

    # 4. Positive M validation & invented UTC 5-minute grid vs negated valid prose
    c_call = _D959_CONTRACTS["call"]
    assert not condition.validate_confirmation_prose(
        "采用 entry_not_returned，每5分钟核验一次。", c_call,
        expected_invalidation="entry_not_returned", expected_interval_ms=300_000, require_positive_m=True,
    )["valid"]
    assert "missing_m_mechanical_anchor" in condition.validate_confirmation_prose(
        "采用 entry_not_returned，每5分钟核验一次。", c_call,
        expected_invalidation="entry_not_returned", expected_interval_ms=300_000, require_positive_m=True,
    )["violations"]
    for bad_grid in (
        "按UTC 5分钟网格核验 Call 短腿60,000，采用 entry_not_returned 规则。",
        "每5分钟UTC网格检查 Call 60,000，若 entry_not_returned 则破坏触发。",
    ):
        res_grid = condition.validate_confirmation_prose(
            bad_grid, c_call, expected_invalidation="entry_not_returned", expected_interval_ms=300_000, require_positive_m=True
        )
        assert not res_grid["valid"] and "clock_interval_mismatch" in res_grid["violations"]
    res_neg = condition.validate_confirmation_prose(
        "采用entry_not_returned，入场后下一UTC整分钟首检、每5分钟步进，不用UTC五分钟网格，指数严格高于60,000且检查点未返回触发破坏。",
        c_call, expected_invalidation="entry_not_returned", expected_interval_ms=300_000, require_positive_m=True,
    )
    assert res_neg["valid"]


def test_step1_8b12_c_degeneration_and_advisory_boundary():
    arm_a_c = (
        "承担条件：入场时确认合格价格在Put责任区外，剩余交割前不接受指数越过63,000短腿严格进入61,000至63,000责任区；"
        "若出现严格进入责任区即按破坏规则处理。"
    )
    sem_a = review.inspect_accepted_conditions_semantics(arm_a_c)
    assert sem_a["c_degenerates_to_m"] is True

    arm_b_c = (
        "承担条件（C）：一，冻结时正面承担的最强已知不利事实是近6小时普通Put净主动卖9.9 BTC，且OBSERVED_UNALIGNED的4小时现货净主动卖56 BTC与价格变化-2.18%同向，短腿最近曾接近至373美元。"
        "二，尚未排除承保依据的理由是严格图表路径仍以63,880.3美元处于63,000美元短腿上方责任外，距离约0.56倍预期波动，窗口内没有严格越过短腿的确认进入。"
        "三，关键来源、时钟或覆盖不确定性为图表分钟标签区间语义未证实，现货主动流窗口完成时钟或对齐/质量未证实，记作OBSERVED_UNALIGNED；原生盘口与图表分钟分源且跨源先后不可证实；资料缺失或未证实不写成从未发生。"
        "四，独立于机械M且可在越界前由盘中真实数据源复核的未来证伪事实是：短腿外侧剩余余量是否从当前880.3美元持续收窄并演变为反复贴近，同时源、同时钟的4小时/12小时现货主动流是否继续出现下行净卖和价格下移，"
        "以及同口径本期Pin/ARD是否向63,000美元方向迁移。上述复核触发仅启动独立复核与留痕，不授权自动退出或交易。"
    )
    sem_b = review.inspect_accepted_conditions_semantics(arm_b_c)
    assert sem_b["c_degenerates_to_m"] is False
    assert sem_b["has_borne_adverse"] is True
    assert sem_b["has_independent_falsifier"] is True
    assert sem_b["has_uncertainty_disclosure"] is True
    assert sem_b["has_grounded_hold_feed"] is True


def test_step1_daily_r16_v4_manual_import_late_and_parse_failed_cli(tmp_path, monkeypatch):
    deploy_dir = Path(__file__).resolve().parents[1] / "deploy" / "player_dossier"
    v2_plan = json.loads((deploy_dir / "plan.daily_r16.v2.json").read_text(encoding="utf-8"))
    v3_plan = json.loads((deploy_dir / "plan.daily_r16.v3_p5.json").read_text(encoding="utf-8"))
    v4_plan = json.loads((deploy_dir / "plan.daily_r16.v4_rollback_formal_a.json").read_text(encoding="utf-8"))

    legacy_accept_text = (
        "**Accept / Call**\n"
        + "\n".join(LEGACY_EXCERPTS.values())
        + "\n"
        + "\n".join(LEGACY_RULE[k] for k in ("historical_facts", "accepted_conditions", "confirmation_excerpt"))
    )
    legacy_raw_file = tmp_path / "legacy_reply.txt"
    legacy_raw_file.write_text(legacy_accept_text, encoding="utf-8")
    legacy_ann_file = tmp_path / "legacy_ann.json"
    legacy_ann_file.write_text(json.dumps(LEGACY_EXCERPTS, ensure_ascii=False), encoding="utf-8")

    orig_dossiers = dossier.Dossiers
    cli_clock = Clock(v4_plan["effective_at_ms"] + 2 * M)

    class _ClockedDossiers(orig_dossiers):
        def __init__(self, root, clock=None, **kw):
            super().__init__(root, cli_clock, **kw)

    monkeypatch.setattr(dossier, "Dossiers", _ClockedDossiers)

    # 1. daily_r16@4 MANUAL_IMPORT: omitting --prompt-version exits 2 AND persists PARSE_FAILED + error_code
    root_v4 = tmp_path / "cli_v4"
    slot_v4 = v4_plan["effective_at_ms"]
    exp_v4 = slot_v4 + v4_plan["expiry_offset_ms"]
    cli_clock.ms = slot_v4 + 2 * M
    d4 = orig_dossiers(root_v4, cli_clock)
    h4 = d4.expect(exp_v4, "SCHEDULED", plan=v4_plan, slot=slot_v4)
    d4.deal(h4, _shift_source_for_r16_slot(exp_v4, cli_clock()), input_profile=player.INPUT_PROFILE_LEGACY)
    cli_clock.ms = slot_v4 + 3 * M

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "astra_player_dossier_v1.py", "--root", str(root_v4), "response",
            "--expiry-ms", str(exp_v4), "--hand", h4.name, "--attempt", "manual_no_pv",
            "--status", "RECEIVED", "--raw", str(legacy_raw_file), "--annotation", str(legacy_ann_file),
            "--model", "deepseek-v4-pro",
            "--requested-ms", str(slot_v4 + 2 * M + 1_000), "--decided-ms", str(slot_v4 + 2 * M + 5_000),
        ],
    )
    with pytest.raises(SystemExit) as sysexc:
        dossier.main()
    assert sysexc.value.code == 2
    att_no_pv = dossier.read(h4 / "responses" / "manual_no_pv" / "attempt.json")
    assert att_no_pv["status"] == "PARSE_FAILED"
    assert att_no_pv["error_code"] == "CONTRACT_VALIDATION_FAILED"

    # Passing explicit --prompt-version player_initial_system@1.0 succeeds and episode()/accepted() preserve @1.0
    slot_v4_2 = slot_v4 + DAY
    exp_v4_2 = slot_v4_2 + v4_plan["expiry_offset_ms"]
    cli_clock.ms = slot_v4_2 + 2 * M
    h4_ok = d4.expect(exp_v4_2, "SCHEDULED", plan=v4_plan, slot=slot_v4_2)
    d4.deal(h4_ok, _shift_source_for_r16_slot(exp_v4_2, cli_clock()), input_profile=player.INPUT_PROFILE_LEGACY)
    cli_clock.ms = slot_v4_2 + 3 * M
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "astra_player_dossier_v1.py", "--root", str(root_v4), "response",
            "--expiry-ms", str(exp_v4_2), "--hand", h4_ok.name, "--attempt", "manual_with_pv",
            "--status", "RECEIVED", "--raw", str(legacy_raw_file), "--annotation", str(legacy_ann_file),
            "--model", "deepseek-v4-pro", "--prompt-version", review.INITIAL_SYSTEM_PROMPT_VERSION,
            "--requested-ms", str(slot_v4_2 + 2 * M + 1_000), "--decided-ms", str(slot_v4_2 + 2 * M + 5_000),
        ],
    )
    dossier.main()
    att_ok = dossier.read(h4_ok / "responses" / "manual_with_pv" / "attempt.json")
    assert att_ok["status"] == "VALID"
    assert att_ok["prompt_version"] == "player_initial_system@1.0"
    acc_v4 = tracking.accepted(h4_ok)
    assert acc_v4["contract"]["side"] == "call"
    t_entry = slot_v4_2 + 3 * M + 10_000
    cli_clock.ms = t_entry + 1_000
    frozen_cond = {
        "contract": {k: acc_v4["contract"][k] for k in condition.CONTRACT_FIELDS if k in acc_v4["contract"]},
        "entry_at_ms": t_entry,
        "entry_index_usd": 66_200.0,
        "historical_facts": LEGACY_RULE["historical_facts"],
        "accepted_conditions": LEGACY_RULE["accepted_conditions"],
        "invalidation": LEGACY_RULE["invalidation"],
        "invalidation_meaning": condition.RULES[LEGACY_RULE["invalidation"]],
        "check_clock": {"first_at_ms": (t_entry // M + 1) * M, "interval_ms": 5 * M},
        "sampling": condition.SAMPLING,
    }
    ep_v4 = d4.episode(
        h4_ok,
        frozen_cond,
        entry_path={
            "expiry_ms": exp_v4_2,
            "index_name": "btc_usd",
            "fetched_at_ms": t_entry,
            "points": [[t_entry, 66_200.0]],
        },
    )
    assert (ep_v4 / "condition.json").is_file() or any(ep_v4.iterdir())

    # 2. daily_r16@2 legacy CLI MANUAL_IMPORT without --prompt-version stays byte-compatible (omits prompt_version)
    root_v2 = tmp_path / "cli_v2"
    slot_v2 = v2_plan["effective_at_ms"]
    exp_v2 = slot_v2 + v2_plan["expiry_offset_ms"]
    cli_clock.ms = slot_v2 + 2 * M
    d2 = orig_dossiers(root_v2, cli_clock)
    h2 = d2.expect(exp_v2, "SCHEDULED", plan=v2_plan, slot=slot_v2)
    d2.deal(h2, _shift_source_for_r16_slot(exp_v2, cli_clock()))
    cli_clock.ms = slot_v2 + 3 * M
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "astra_player_dossier_v1.py", "--root", str(root_v2), "response",
            "--expiry-ms", str(exp_v2), "--hand", h2.name, "--attempt", "legacy_cli_01",
            "--status", "RECEIVED", "--raw", str(legacy_raw_file), "--annotation", str(legacy_ann_file),
            "--model", "deepseek-v4-pro",
            "--requested-ms", str(slot_v2 + 2 * M + 1_000), "--decided-ms", str(slot_v2 + 2 * M + 5_000),
        ],
    )
    dossier.main()
    att_v2 = dossier.read(h2 / "responses" / "legacy_cli_01" / "attempt.json")
    assert att_v2["status"] == "VALID"
    assert "prompt_version" not in att_v2

    # 3. CLI LATE with ContractValidationError preserves LATE + error + error_code, and select() returns None;
    #    missing <ANNOTATION_JSON> & pre-contract ValueError persist PARSE_FAILED (never VALID) + exit 2.
    root_v3 = tmp_path / "cli_v3"
    slot_v3 = v3_plan["effective_at_ms"]
    exp_v3 = slot_v3 + v3_plan["expiry_offset_ms"]
    cli_clock.ms = slot_v3 + 2 * M
    d3 = orig_dossiers(root_v3, cli_clock)
    h3 = d3.expect(exp_v3, "SCHEDULED", plan=v3_plan, slot=slot_v3)
    d3.deal(h3, _shift_source_for_r16_slot(exp_v3, cli_clock()), input_profile=player.INPUT_PROFILE_P0_STRICT)

    bad_cand_file = tmp_path / "bad_cand_d959_style.txt"
    bad_cand_file.write_text(
        _build_valid_p5_reply(
            **_BASE_SRC_STRIKES,
            custom_confirmation="两侧对比择优：Call 侧入区前距离与路径表现更稳健，因此优先选择 Call 侧承担。",
        ),
        encoding="utf-8",
    )
    no_ann_file = tmp_path / "no_ann.txt"
    no_ann_file.write_text("## Reject\n无标注 JSON 的候选答复。\n", encoding="utf-8")
    malformed_ann_file = tmp_path / "malformed_ann.txt"
    malformed_ann_file.write_text(
        "## Accept / Call\n正文。\n<ANNOTATION_JSON>\n{\"put\": \"缺右括号\n</ANNOTATION_JSON>",
        encoding="utf-8",
    )

    # Pre-deadline missing <ANNOTATION_JSON> -> PARSE_FAILED + CONTRACT_VALIDATION_FAILED + exit 2
    cli_clock.ms = slot_v3 + 3 * M
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "astra_player_dossier_v1.py", "--root", str(root_v3), "response",
            "--expiry-ms", str(exp_v3), "--hand", h3.name, "--attempt", "cand_no_ann",
            "--status", "RECEIVED", "--raw", str(no_ann_file),
            "--model", "deepseek-reasoner", "--prompt-version", review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
            "--requested-ms", str(slot_v3 + 2 * M + 1_000), "--decided-ms", str(slot_v3 + 2 * M + 5_000),
        ],
    )
    with pytest.raises(SystemExit) as sysexc_noann:
        dossier.main()
    assert sysexc_noann.value.code == 2
    att_noann = dossier.read(h3 / "responses" / "cand_no_ann" / "attempt.json")
    assert att_noann["status"] == "PARSE_FAILED"
    assert att_noann["error_code"] == "CONTRACT_VALIDATION_FAILED"

    # Pre-deadline pre-contract ValueError (malformed annotation JSON) -> PARSE_FAILED + CONTRACT_VALIDATION_FAILED + exit 2
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "astra_player_dossier_v1.py", "--root", str(root_v3), "response",
            "--expiry-ms", str(exp_v3), "--hand", h3.name, "--attempt", "cand_bad_json",
            "--status", "RECEIVED", "--raw", str(malformed_ann_file),
            "--model", "deepseek-reasoner", "--prompt-version", review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
            "--requested-ms", str(slot_v3 + 2 * M + 1_000), "--decided-ms", str(slot_v3 + 2 * M + 5_000),
        ],
    )
    with pytest.raises(SystemExit) as sysexc_badjson:
        dossier.main()
    assert sysexc_badjson.value.code == 2
    att_badjson = dossier.read(h3 / "responses" / "cand_bad_json" / "attempt.json")
    assert att_badjson["status"] == "PARSE_FAILED"
    assert att_badjson["error_code"] == "CONTRACT_VALIDATION_FAILED"

    # Past reply deadline -> ContractValidationError preserves LATE + error + error_code and select() returns None
    slot_late = slot_v3 + DAY
    exp_late = slot_late + v3_plan["expiry_offset_ms"]
    cli_clock.ms = slot_late + 2 * M
    h3_late = d3.expect(exp_late, "SCHEDULED", plan=v3_plan, slot=slot_late)
    d3.deal(h3_late, _shift_source_for_r16_slot(exp_late, cli_clock()), input_profile=player.INPUT_PROFILE_P0_STRICT)
    late_now = slot_late + v3_plan["deal_grace_ms"] + v3_plan["reply_grace_ms"] + 5 * M
    cli_clock.ms = late_now
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "astra_player_dossier_v1.py", "--root", str(root_v3), "response",
            "--expiry-ms", str(exp_late), "--hand", h3_late.name, "--attempt", "cand_late_cv",
            "--status", "RECEIVED", "--raw", str(bad_cand_file),
            "--model", "deepseek-reasoner", "--prompt-version", review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
            "--requested-ms", str(slot_late + 2 * M + 1_000), "--decided-ms", str(late_now - 1_000),
        ],
    )
    with pytest.raises(SystemExit) as sysexc_late:
        dossier.main()
    assert sysexc_late.value.code == 2
    att_late = dossier.read(h3_late / "responses" / "cand_late_cv" / "attempt.json")
    assert att_late["status"] == "LATE"
    assert att_late["error"] and att_late["error_code"] == "CONTRACT_VALIDATION_FAILED"
    assert att_late["contract_validation_status"] == "CONTRACT_FAILED"
    assert d3.select(h3_late) is None


def test_step2_6311071c_live_readiness_and_pending_lock_p5_interop(tmp_path, monkeypatch):
    # 1. Live 6311071c workbench_view schema readiness checks (ttl_ms=300_000, usage_decisions.default.can_use)
    now_ms = 1795453269673
    wb_view = _make_ready_workbench_view(now_ms=now_ms - 18_000)
    wb_view["discovery_status"]["observed_at_ms"] = now_ms - 184_000
    wb_view["data_health"]["products"]["deribit.btc.discovery.current.v1"]["current"] = [
        {
            "data_state": "OK",
            "usage_decisions": {
                "default": {"age_ms": 183_919, "can_use": True, "reason_codes": [], "status": "USABLE", "ttl_ms": 300_000}
            },
        }
    ]
    res_ready = tracking.evaluate_source_readiness(workbench_view=wb_view, now_ms=now_ms)
    assert res_ready["status"] == "SOURCE_READY"
    assert res_ready["checks"]["accept_lock_ready"] is True
    assert res_ready["checks"]["discovery_max_age_ms"] == 300_000

    wb_stale_disc = copy.deepcopy(wb_view)
    wb_stale_disc["discovery_status"]["observed_at_ms"] = now_ms - 301_000
    res_stale = tracking.evaluate_source_readiness(workbench_view=wb_stale_disc, now_ms=now_ms)
    assert res_stale["status"] == "UPSTREAM_BLOCKED"
    assert "discovery_observed_stale" in res_stale["reasons"]

    wb_unusable = copy.deepcopy(wb_view)
    wb_unusable["data_health"]["products"]["deribit.btc.discovery.current.v1"]["current"][0]["usage_decisions"]["default"] = {
        "can_use": False, "status": "EXPIRED", "ttl_ms": 300_000
    }
    res_unusable = tracking.evaluate_source_readiness(workbench_view=wb_unusable, now_ms=now_ms)
    assert res_unusable["status"] == "UPSTREAM_BLOCKED"
    assert "data_health_discovery_unusable:EXPIRED" in res_unusable["reasons"]

    # 2. P5 candidate (daily_r16@3) Accept -> 429 PENDING_LOCK -> recovery DELAYED_LOCK interop
    import io
    import urllib.error
    from test_deribit_discovery_reliability import _book_payload, _wire_workbench_market, SLOT_16, write_fmz, native_gate
    from tools import astra_underwriting_discovery_v20 as discovery

    expiry = SLOT_16 + 16 * HOUR
    slot = expiry - 16 * HOUR
    clk = [slot + 2 * M]
    mode = {"on": False}
    d_root = tmp_path / "p5_pending_dossiers"

    def on_ob(name, ts):
        if mode["on"] and name.endswith("-C"):
            raise discovery.DeribitPublicError.from_http(
                urllib.error.HTTPError(
                    "https://www.deribit.com/api/v2/public/get_order_book",
                    429, "Too Many Requests", {"Retry-After": "10"}, io.BytesIO(b'{"error":{"code":10028}}'),
                ),
                "/public/get_order_book",
            )
        return None

    wb, snaps, _, _ = _wire_workbench_market(
        tmp_path / "wb_interop", monkeypatch, clock=clk, expiry=expiry, on_order_book=on_ob, shadow_dossier_root=d_root
    )
    v0 = wb.refresh(now_ms=clk[0], record_daily=False)
    d = dossier.Dossiers(d_root, lambda: clk[0])
    cases, cards, native = [], [], []
    points = [[t, 100_000.0] for t in range(clk[0] - DAY, clk[0] + 1, M)]
    for c in v0["candidates"]:
        s = c["snapshot"]
        case = wb.case_file(s["snapshot_id"])
        read_ms = case["build"]["built_at_ms"]
        walls = dict(expiry_ms=expiry, walls={"pin": 100_000.0}, top={"oi_put": [{"strike": 97_000.0}], "oi_call": [{"strike": 103_000.0}]})
        player.entry(case, "L3.expiry_walls").update(values=walls, asof_ms=read_ms, quality="ok")
        contract = player.values(case, "L1.contract")
        cards.append(dossier.levels.build_card(expiry_ms=expiry, rnd="r16", side=contract["side"],
                     read_at_ms=read_ms, walls=walls, points=points, case=case, contract=contract, snapshots=[]))
        cases.append(case)
        native.extend(dict(instrument_name=contract[f"{leg}_instrument"], expiration_timestamp=expiry,
                           settlement_currency="BTC", is_active=True) for leg in ("short", "long"))
    path = dict(schema="index_path@0.1", index_name="btc_usd", expiry_ms=expiry, points=points, fetched_at_ms=clk[0],
                timestamp_semantics="unverified_minute_label")
    deploy_dir = Path(__file__).resolve().parents[1] / "deploy" / "player_dossier"
    v3_plan = json.loads((deploy_dir / "plan.daily_r16.v3_p5.json").read_text(encoding="utf-8"))
    plan_p5 = dict(v3_plan, effective_at_ms=slot)
    hand = d.expect(expiry, "SCHEDULED", plan=plan_p5, slot=slot)
    src = dossier.seal_source(cases, cards, path, [], native, received_at_ms=clk[0], source="TEST_FROZEN")
    d.deal(hand, src, input_profile=player.INPUT_PROFILE_P0_STRICT)
    c_pair = _extract_source_contracts(src)
    p5_raw = _build_valid_p5_reply(
        **_SEED_STRIKES, action_header="## Accept / Call", invalidation="entry_not_returned", side="call"
    )
    req_stub = review.build_initial_request_payload(
        (hand / "evidence" / "input.md").read_text(encoding="utf-8"),
        contract_pair=c_pair, model="deepseek-reasoner",
        prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
        input_profile=player.INPUT_PROFILE_P0_STRICT, requested_at_ms=clk[0], fidelity="EXACT",
    )
    parsed = review.parse_initial_reply({"text": p5_raw, "model": "deepseek-reasoner"}, req_stub, contracts=c_pair)
    req_ms = clk[0]
    clk[0] += 2_000
    d.respond(
        hand, "auto_p5_pending", "RECEIVED", parsed["raw_bytes"], parsed["annotation"],
        model="deepseek-reasoner", requested=req_ms, decided=clk[0], channel="API_AUTO",
        request_payload=req_stub, condition_plan=parsed["condition_plan"],
        prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
        system_sha256=req_stub["system_sha256"], input_profile=player.INPUT_PROFILE_P0_STRICT,
        contract_validation=parsed["contract_validation"],
    )
    mode["on"] = True
    clk[0] += 305_000
    write_fmz(snaps, native_gate(ts=clk[0] - 1_000), now=clk[0])
    wb.refresh(now_ms=clk[0], record_daily=False)
    with serving(wb) as base_url:
        res_pending = tracking._link_with_fresh_quote(d, hand, wb_post(base_url, wb), parsed["condition_plan"])
        assert res_pending["state"] == "LINK_UNVERIFIED"
    pending_obj = dossier.read(hand / "tracking" / "pending_lock.json")
    assert pending_obj["state"] == "PENDING_LOCK"
    assert pending_obj["deadline_ms"] == min(expiry, req_ms + 2_000 + 60 * M)

    mode["on"] = False
    clk[0] += 10 * M
    write_fmz(snaps, native_gate(ts=clk[0] - 1_000), now=clk[0])
    wb.refresh(now_ms=clk[0], record_daily=False)
    link_rec = dossier.read(hand / "tracking" / "link.json")
    assert link_rec["state"] == "SHADOW_LOCK_CREATED"
    assert link_rec["lock_mode"] == "DELAYED_LOCK" and link_rec["delayed_lock"] is True
    audit = tracking.audit_record(hand, clk[0], {link_rec["t0"]["tracker_episode_id"]: {"state": "TRACKING"}}, detail=True)
    assert audit["tracking_state"] == "TRACKING"
    assert audit["lock_mode"] == "DELAYED_LOCK"


def test_step2_fault_injection_401_403_timeout_lost_receipt_budget_and_isolation(tmp_path, monkeypatch):
    import io
    import threading
    import urllib.error
    from tools import signal_llm_review

    deploy_dir = Path(__file__).resolve().parents[1] / "deploy" / "player_dossier"
    v2_plan = json.loads((deploy_dir / "plan.daily_r16.v2.json").read_text(encoding="utf-8"))
    v3_plan = json.loads((deploy_dir / "plan.daily_r16.v3_p5.json").read_text(encoding="utf-8"))

    # 1. Direct call_chat_llm with retries=3 still makes ONLY 1 attempt on HTTP 401 and HTTP 403
    for code in (401, 403):
        calls = [0]

        def fail_http(api_key, model, body, timeout_s, endpoint=None, _code=code, _calls=calls):
            _calls[0] += 1
            raise urllib.error.HTTPError("https://api.example/v1/chat/completions", _code, "AuthError", {}, io.BytesIO(b"{}"))

        with pytest.raises(urllib.error.HTTPError):
            review.call_chat_llm([{"role": "user", "content": "hi"}], api_key="k", model="deepseek-reasoner", retries=3, transport=fail_http)
        assert calls[0] == 1

    # 2. auto_deal with HTTP 401 / 403 and TimeoutError: strictly 1 call, 0 retries, accurate status, 0 duplicate calls on recover_initial
    monkeypatch.setattr(dossier, "collect", lambda _a, exp, sl, **_kw: _shift_source_for_r16_slot(exp, clk()))
    arch_dir = tmp_path / "arch"
    for dn in ("cases", "levels", "structure", "band_snapshots"):
        (arch_dir / dn).mkdir(parents=True, exist_ok=True)

    for idx, (err_factory, expected_status, expected_delivery) in enumerate((
        (lambda: urllib.error.HTTPError("https://api.example/v1", 401, "Unauthorized", {}, io.BytesIO(b"")), "REQUEST_FAILED", "PROVIDER_REJECTED"),
        (lambda: urllib.error.HTTPError("https://api.example/v1", 403, "Forbidden", {}, io.BytesIO(b"")), "REQUEST_FAILED", "PROVIDER_REJECTED"),
        (lambda: TimeoutError("read timed out"), "TIMEOUT", "DELIVERY_UNKNOWN"),
    )):
        slot_i = v3_plan["effective_at_ms"] + idx * DAY
        clk = Clock(slot_i + 2 * M)
        d_i = dossier.Dossiers(tmp_path / f"fault_{idx}", clk)
        plan_i = dict(v3_plan, effective_at_ms=slot_i)
        call_n = [0]

        def err_tr(api_key, model, body, timeout_s, endpoint=None, _ef=err_factory, _cn=call_n):
            _cn[0] += 1
            raise _ef()

        tracking.auto_deal(d_i, plan_i, arch_dir, None, llm_config={"api_key": "k", "model": "deepseek-reasoner", "transport": err_tr, "retries": 0})
        assert call_n[0] == 1
        hand_i = next((d_i.path(slot_i + plan_i["expiry_offset_ms"]) / "hands").glob("hand_*"))
        att_i = dossier.read(next((hand_i / "responses").glob("*/attempt.json")))
        assert att_i["status"] == expected_status
        assert att_i["configuration"]["delivery_state"] == expected_delivery
        assert att_i["prompt_version"] == review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION
        # Second tick & recover_initial make 0 extra LLM calls
        tracking.auto_deal(d_i, plan_i, arch_dir, None, llm_config={"api_key": "k", "model": "deepseek-reasoner", "transport": err_tr, "retries": 0})
        tracking.recover_initial(d_i, None)
        assert call_n[0] == 1

    # 3. Lost receipt after provider returns response (on_response fails before dossier save) -> 1 budget reservation, 0 blind resend
    b_path = tmp_path / "lost_budget.json"
    budget_lost = signal_llm_review.DailyHttpBudget(b_path, limit=5)
    lost_calls = [0]

    def ok_then_drop(api_key, model, body, timeout_s, endpoint=None):
        lost_calls[0] += 1
        return {"choices": [{"message": {"content": _build_valid_p5_reply(action_header="## Reject")}, "finish_reason": "stop"}]}

    def failing_on_response(_reply):
        raise OSError("disk full before receipt save")

    with pytest.raises(OSError, match="disk full"):
        review.call_chat_llm(
            [{"role": "user", "content": "hi"}],
            api_key="k",
            model="deepseek-reasoner",
            retries=1,
            budget=budget_lost,
            on_response=failing_on_response,
            transport=ok_then_drop,
        )
    assert lost_calls[0] == 1
    assert budget_lost.snapshot()["http_calls_used"] == 1

    # 4. Concurrent DailyHttpBudget contention never exceeds limit (limit=3 across 8 threads)
    conc_budget = signal_llm_review.DailyHttpBudget(tmp_path / "conc_budget.json", limit=3)
    results_lock = threading.Lock()
    reserved_ids: list[str] = []
    exceeded_count = [0]

    def worker_reserve(tid: int):
        try:
            r = conc_budget.reserve(count=1, role=f"t{tid}", packet_hash=f"h{tid}", provider="deepseek", model="deepseek-reasoner")
            with results_lock:
                reserved_ids.append(r["reservation_id"])
        except signal_llm_review.DailyBudgetExceeded:
            with results_lock:
                exceeded_count[0] += 1

    threads = [threading.Thread(target=worker_reserve, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(reserved_ids) == 3
    assert exceeded_count[0] == 5
    assert conc_budget.snapshot()["http_calls_used"] == 3

    # 5. Historical Formal A (@1.0) untouched + expired hand + Reject + Unknown states never trigger duplicate paid calls
    d_mix = dossier.Dossiers(tmp_path / "mixed_states", lambda: mix_clk[0])
    s0 = v3_plan["effective_at_ms"]
    e0 = s0 + v2_plan["expiry_offset_ms"]
    mix_clk = [s0 + 2 * M]
    h_formal_a = d_mix.expect(e0, "SCHEDULED", plan=dict(v2_plan, effective_at_ms=s0), slot=s0)
    d_mix.deal(h_formal_a, _shift_source_for_r16_slot(e0, mix_clk[0]))
    req_a_ms = mix_clk[0]
    mix_clk[0] += 2_000
    d_mix.respond(
        h_formal_a, "a_hist", "RECEIVED",
        ("Reject\n" + "\n".join(LEGACY_EXCERPTS.values())).encode("utf-8"),
        LEGACY_EXCERPTS, model="deepseek-v4-pro", requested=req_a_ms, decided=mix_clk[0],
    )
    snap_a_expected = (h_formal_a / "expected.json").read_bytes()
    snap_a_decision = (h_formal_a / "decision.json").read_bytes()

    # Advance past e0 + run P5 plan on s1 (expired unclaimed check on s_exp, Reject on s1)
    s1 = s0 + DAY
    plan_p5_s1 = dict(v3_plan, effective_at_ms=s1)
    mix_clk[0] = s1 + 2 * M
    monkeypatch.setattr(dossier, "collect", lambda _a, exp, sl, **_kw: _shift_source_for_r16_slot(exp, mix_clk[0]))
    mix_calls = [0]

    def p5_reject_tr(api_key, model, body, timeout_s, endpoint=None):
        mix_calls[0] += 1
        return {"choices": [{"message": {"content": _build_valid_p5_reply(action_header="## Reject")}, "finish_reason": "stop"}]}

    res_mix = tracking.auto_deal(
        d_mix, plan_p5_s1, arch_dir, None,
        llm_config={"api_key": "k", "model": "deepseek-reasoner", "transport": p5_reject_tr, "retries": 0},
    )
    assert mix_calls[0] == 1
    assert any(r.get("decision") == "Reject" or r.get("status") == "ALREADY_DECIDED" for r in res_mix)
    assert tracking.recover_initial(d_mix, None) == []
    assert mix_calls[0] == 1
    assert (h_formal_a / "expected.json").read_bytes() == snap_a_expected
    assert (h_formal_a / "decision.json").read_bytes() == snap_a_decision

