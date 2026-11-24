"""Comprehensive adversarial, consumer-gate, and replay tests for P5 evidence pipeline fixes.

Covers Workstream A + B + C across:
- tools/astra_player_input_v1.py (spot STALE/UNALIGNED/ALIGNED/future, RV24/IV vol background, legacy_v1 parity)
- tools/astra_player_condition_v1.py (compare_ard_basis 20m live Pin SLA & pin_eval_mode)
- tools/astra_player_tracking_v13.py (live 5m Pin, archive merge, no-rollback, band_half, snapshot, channel_readiness, idempotency)
- tools/astra_player_review_v1.py (Cat C/D fail-closed guards, exact closed-bar delta, blind rendering, prompt invariants)
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from test_astra_player_dossier_v1 import Clock, SLOT, source as base_source
from test_astra_player_review_v1 import _make_series_point
from test_astra_player_tracking_v13 import RULE as LEGACY_RULE, post as wb_post, sample as wb_sample, serving
from test_astra_p5_candidate import _find_workspace_root, _set_case_layer
from test_astra_underwriting_v20 import T as UNDERWRITING_T
from tools import astra_anchor_relative_v1 as ard
from tools import astra_evidence_qual_v1 as evidence_qual
from tools import astra_locked_candidate_compare_v1 as locked_compare
from tools import astra_player_condition_v1 as condition
from tools import astra_player_dossier_v1 as dossier
from tools import astra_player_input_v1 as player
from tools import astra_player_review_v1 as review
from tools import astra_player_tracking_v13 as tracking


M = 60_000
HOUR = 3_600_000
DAY = 24 * HOUR


def _spot_window(end_ms: int, *, hours: int = 4, source: str = "binance", symbol: str = "BTCUSDT",
                 bar_complete: bool = True, cvd_sum: float = -45.0, mom_pct: float = -0.55,
                 available_at_ms: int | None = None, coverage_frac: float = 1.0,
                 extra: dict | None = None) -> dict:
    out = {
        "source": source,
        "symbol": symbol,
        "cvd_unit": "BTC",
        "data_ready": True,
        "bar_complete": bar_complete,
        "coverage_hours": float(hours),
        "coverage_frac": coverage_frac,
        "window_start_ms": end_ms - hours * HOUR,
        "first_ts_ms": end_ms - hours * HOUR,
        "end_ts_ms": end_ms,
        "available_at_ms": (end_ms + 1_000) if available_at_ms is None else available_at_ms,
        "cvd_sum": cvd_sum,
        "momentum_return_pct": mom_pct,
    }
    if extra:
        out.update(extra)
    return out


def _rv24_payload(end_ms: int, *, computed_at_ms: int | None = None, rv_pct: float = 48.25,
                  bars: int = 24, instrument: str = evidence_qual.RV_INSTRUMENT,
                  source: str = evidence_qual.RV_SOURCE, bar_spec: str = evidence_qual.RV_BAR) -> dict:
    out = {
        "instrument": instrument,
        "source": source,
        "bar": bar_spec,
        "bars": bars,
        "rv_24h_pct": rv_pct,
        "last_bar_end_ms": end_ms,
    }
    if computed_at_ms is not None:
        out["rv_computed_at_ms"] = computed_at_ms
    return out


def _attach_valid_iv_layers(put_case: dict, call_case: dict, put_card: dict, call_card: dict,
                            read_at: int, expiry_ms: int) -> None:
    for side_case, card in ((put_case, put_card), (call_case, call_card)):
        short_inst = player.entry(side_case, "L1.contract")["values"]["short_instrument"]
        _set_case_layer(side_case, "L3.expiry_walls", {
            "expiry_ms": expiry_ms,
            "walls": {"pin": 99_000.0},
            "atm_iv_pct": 52.40,
            "stale": False,
        }, asof_ms=read_at - 10_000)
        _set_case_layer(side_case, "L2.book.short", {
            "instrument_name": short_inst,
            "index_price": 100_000.0,
            "mark_iv": 55.10 if card["side"] == "put" else 51.80,
            "timestamp": read_at - 1_500,
        }, asof_ms=read_at - 1_000)


# ---------------------------------------------------------------------------
# 1. P0 Spot Qualification & Strict Section 5
# ---------------------------------------------------------------------------

def test_p0_spot_stale_unaligned_aligned_future_and_windows():
    cutoff = SLOT + 10 * M
    src = base_source(Clock(cutoff))
    put_case, call_case = copy.deepcopy(src["cases"])
    put_card, call_card = copy.deepcopy(src["cards"])
    read_at = put_card["read_at_ms"]

    # Case A: both windows STALE (>60m) -> STALE and NEVER active counter-evidence
    stale_end = read_at - 75 * M
    _set_case_layer(put_case, "L5.volume_price", {
        "micro_flow": {
            "fast_4h": _spot_window(stale_end, hours=4, cvd_sum=-210.0, mom_pct=-1.8),
            "slow_12h": _spot_window(stale_end, hours=12, cvd_sum=-320.0, mom_pct=-2.2),
        }
    }, asof_ms=stale_end)
    q_both_stale = player.inspect_spot_qualification(player.entry(put_case, "L5.volume_price"), cutoff=read_at)
    assert q_both_stale["status"] == "STALE"
    md_both_stale = player.build_input(
        put_case, call_case, put_card, call_card, index_path=src["index_path"], input_profile=player.INPUT_PROFILE_P0_STRICT,
    )
    assert "【STALE】" in md_both_stale
    assert "须明确作为当前已成立的反证" not in md_both_stale

    # Case B: fast_4h STALE (>60m) + slow_12h OBSERVED_ALIGNED -> MUST emit STALE, NOT OBSERVED_ALIGNED / UNALIGNED
    _set_case_layer(put_case, "L5.volume_price", {
        "micro_flow": {
            "fast_4h": _spot_window(read_at - 70 * M, hours=4, cvd_sum=-85.0, mom_pct=-0.8),
            "slow_12h": _spot_window(read_at - 5 * M, hours=12, cvd_sum=-40.0, mom_pct=-0.3),
        }
    }, asof_ms=read_at - 5 * M)
    q_fast_stale = player.inspect_spot_qualification(player.entry(put_case, "L5.volume_price"), cutoff=read_at)
    assert q_fast_stale["status"] == "STALE"
    md_fast_stale = player.build_input(
        put_case, call_case, put_card, call_card, index_path=src["index_path"], input_profile=player.INPUT_PROFILE_P0_STRICT,
    )
    assert "【STALE】" in md_fast_stale
    assert "须明确作为当前已成立的反证" not in md_fast_stale

    # Case C: missing provenance / incomplete bar / degraded flag -> OBSERVED_UNALIGNED
    for bad_w in (
        {"data_ready": True, "end_ts_ms": read_at - M, "cvd_unit": "BTC", "cvd_sum": -10.0, "momentum_return_pct": -0.5},
        _spot_window(read_at - M, hours=4, bar_complete=False),
        _spot_window(read_at - M, hours=4, extra={"cvd_degraded": True}),
        _spot_window(read_at - M, hours=4, extra={"gap": True}),
    ):
        row = {"ref": "L5.volume_price", "asof_ms": read_at - M, "quality": "ok",
               "values": {"micro_flow": {"fast_4h": bad_w, "slow_12h": _spot_window(read_at - M, hours=12)}}}
        assert player.inspect_spot_qualification(row, cutoff=read_at)["status"] == "OBSERVED_UNALIGNED"

    # Case D: OBSERVED_ALIGNED renders verified bar completion clock stamp(fast_q['end_ms'])
    aligned_end = read_at - 2 * M
    _set_case_layer(put_case, "L5.volume_price", {
        "micro_flow": {
            "fast_4h": _spot_window(aligned_end, hours=4, cvd_sum=-95.0, mom_pct=-0.9),
            "slow_12h": _spot_window(aligned_end, hours=12, cvd_sum=-180.0, mom_pct=-1.1),
        }
    }, asof_ms=aligned_end)
    q_ok = player.inspect_spot_qualification(player.entry(put_case, "L5.volume_price"), cutoff=read_at)
    assert q_ok["status"] == "OBSERVED_ALIGNED"
    md_ok = player.build_input(
        put_case, call_case, put_card, call_card, index_path=src["index_path"], input_profile=player.INPUT_PROFILE_P0_STRICT,
    )
    assert "【OBSERVED_ALIGNED】" in md_ok
    assert f"完成柱截止{player.stamp(aligned_end)}" in md_ok

    # Case E: future fast_4h + aligned slow_12h -> OBSERVED_ALIGNED_SLOW_ONLY without leaking future values
    _set_case_layer(put_case, "L5.volume_price", {
        "micro_flow": {
            "fast_4h": _spot_window(read_at + 10 * M, hours=4, cvd_sum=-777.77, mom_pct=-7.77),
            "slow_12h": _spot_window(aligned_end, hours=12, cvd_sum=-12.0, mom_pct=-0.2),
        }
    }, asof_ms=read_at - M)
    q_slow = player.inspect_spot_qualification(player.entry(put_case, "L5.volume_price"), cutoff=read_at)
    assert q_slow["status"] == "OBSERVED_ALIGNED_SLOW_ONLY"
    md_slow = player.build_input(
        put_case, call_case, put_card, call_card, index_path=src["index_path"], input_profile=player.INPUT_PROFILE_P0_STRICT,
    )
    assert "【OBSERVED_ALIGNED（仅12h可用；近4h为UNKNOWN）】" in md_slow
    assert "-777.77" not in md_slow

    # Case F: derive_non_overlapping_spot_windows enforces source/symbol, bar_complete, window_start_ms
    base_good = {
        "asof_ms": read_at - M,
        "quality": "ok",
        "values": {"micro_flow": {
            "fast_4h": _spot_window(aligned_end, hours=4, cvd_sum=-50.0, mom_pct=-0.5),
            "slow_12h": _spot_window(aligned_end, hours=12, cvd_sum=-170.0, mom_pct=-1.7),
        }},
    }
    derived = player.derive_non_overlapping_spot_windows(base_good, cutoff=read_at)
    assert derived["comparable"] is True
    assert derived["previous_8h"]["cvd_sum"] == pytest.approx(-120.0)

    mismatch_sym = copy.deepcopy(base_good)
    mismatch_sym["values"]["micro_flow"]["slow_12h"]["symbol"] = "ETHUSDT"
    assert player.derive_non_overlapping_spot_windows(mismatch_sym, cutoff=read_at)["comparable"] is False

    bad_start = copy.deepcopy(base_good)
    bad_start["values"]["micro_flow"]["fast_4h"]["window_start_ms"] = aligned_end + 1_000
    assert player.derive_non_overlapping_spot_windows(bad_start, cutoff=read_at)["comparable"] is False

    incomplete = copy.deepcopy(base_good)
    incomplete["values"]["micro_flow"]["fast_4h"]["bar_complete"] = False
    assert player.derive_non_overlapping_spot_windows(incomplete, cutoff=read_at)["comparable"] is False


# ---------------------------------------------------------------------------
# 2. P0 Volatility Background (RV24 / ATM IV / mark_iv) & legacy_v1 Parity
# ---------------------------------------------------------------------------

def test_p0_vol_background_rv24_qualification_scale_and_legacy_parity():
    clk = Clock(SLOT + 10 * M)
    src = base_source(clk)
    put_case, call_case = copy.deepcopy(src["cases"])
    put_card, call_card = copy.deepcopy(src["cards"])
    read_at = put_card["read_at_ms"]
    expiry_ms = put_card["expiry_ms"]

    # Baseline legacy output BEFORE adding L4/RV24, L2 mark_iv, or 5m Pin extras
    legacy_before = player.build_input(
        put_case, call_case, put_card, call_card,
        index_path=src["index_path"], input_profile=player.INPUT_PROFILE_LEGACY,
    )

    _attach_valid_iv_layers(put_case, call_case, put_card, call_card, read_at, expiry_ms)
    rv_bar_end = read_at - 10 * M
    _set_case_layer(put_case, "L4.expiry_vol", {
        "realized_vol": _rv24_payload(rv_bar_end, computed_at_ms=read_at - 5 * M, rv_pct=47.80),
        "p_touch_short": 0.19,
        "rv_4h_pct": 63.5,
    }, asof_ms=read_at - 5 * M)
    _set_case_layer(put_case, "L5.volume_price", {
        "micro_flow": {
            "fast_4h": _spot_window(read_at - 2 * M, hours=4),
            "slow_12h": _spot_window(read_at - 2 * M, hours=12),
        }
    }, asof_ms=read_at - 2 * M)

    # Strict renders ATM IV + mark_iv + BTC-PERPETUAL 1h RV24 (no RECONSTRUCTED_OFFLINE), zero p_touch_short / RV4h
    md_strict = player.build_input(
        put_case, call_case, put_card, call_card,
        index_path=src["index_path"], input_profile=player.INPUT_PROFILE_P0_STRICT,
    )
    assert "交易所本期ATM IV 52.40%" in md_strict
    assert "短腿盘口mark_iv Put 55.10%/Call 51.80%" in md_strict
    assert "BTC-PERPETUAL 1h闭合柱RV24 47.80%" in md_strict
    assert f"末柱截至{player.stamp(rv_bar_end)}" in md_strict
    assert "RECONSTRUCTED_OFFLINE" not in md_strict
    assert "不替代上述expected_move_usd距离尺度" in md_strict
    assert "严禁将不可核验RV规则写入持仓条件[C]" in md_strict
    assert "p_touch_short" not in md_strict and "RV4h" not in md_strict and "rv_4h_pct" not in md_strict

    # Unclocked RV24: production (offline_reconstructed_rv_clock_ms=None) -> UNKNOWN; offline replay -> RECONSTRUCTED_OFFLINE
    _set_case_layer(put_case, "L4.expiry_vol", {
        "realized_vol": _rv24_payload(rv_bar_end, computed_at_ms=None, rv_pct=47.80),
    }, asof_ms=read_at - 5 * M)
    md_unclocked_prod = player.build_input(
        put_case, call_case, put_card, call_card,
        index_path=src["index_path"], input_profile=player.INPUT_PROFILE_P0_STRICT,
    )
    assert "历史实现波动率RV24为UNKNOWN" in md_unclocked_prod
    assert "交易所本期ATM IV 52.40%" in md_unclocked_prod
    assert "47.80%" not in md_unclocked_prod

    md_unclocked_replay = player.build_input(
        put_case, call_case, put_card, call_card,
        index_path=src["index_path"], input_profile=player.INPUT_PROFILE_P0_STRICT,
        offline_reconstructed_rv_clock_ms=read_at,
    )
    assert "BTC-PERPETUAL 1h闭合柱RV24 47.80%" in md_unclocked_replay
    assert "RECONSTRUCTED_OFFLINE" in md_unclocked_replay

    # Future L4 -> excluded UNKNOWN without leaking value
    _set_case_layer(put_case, "L4.expiry_vol", {
        "realized_vol": _rv24_payload(rv_bar_end, computed_at_ms=read_at + 120_000, rv_pct=199.99),
    }, asof_ms=read_at + 120_000)
    md_future_rv = player.build_input(
        put_case, call_case, put_card, call_card,
        index_path=src["index_path"], input_profile=player.INPUT_PROFILE_P0_STRICT,
    )
    assert "历史实现波动率RV24为UNKNOWN" in md_future_rv
    assert "199.99%" not in md_future_rv

    # Stale RV24 (>90m) -> STALE
    _set_case_layer(put_case, "L4.expiry_vol", {
        "realized_vol": _rv24_payload(read_at - 110 * M, computed_at_ms=read_at - 105 * M, rv_pct=41.0),
    }, asof_ms=read_at - 105 * M, quality="stale")
    md_stale_rv = player.build_input(
        put_case, call_case, put_card, call_card,
        index_path=src["index_path"], input_profile=player.INPUT_PROFILE_P0_STRICT,
    )
    assert "历史实现波动率RV24为STALE" in md_stale_rv

    # Unaligned RV24 (wrong instrument) -> OBSERVED_UNALIGNED
    _set_case_layer(put_case, "L4.expiry_vol", {
        "realized_vol": _rv24_payload(rv_bar_end, computed_at_ms=read_at - 5 * M, instrument="ETH-PERPETUAL", rv_pct=41.0),
    }, asof_ms=read_at - 5 * M)
    md_unaligned_rv = player.build_input(
        put_case, call_case, put_card, call_card,
        index_path=src["index_path"], input_profile=player.INPUT_PROFILE_P0_STRICT,
    )
    assert "历史实现波动率RV24为OBSERVED_UNALIGNED" in md_unaligned_rv

    # Adding L4 / mark_iv / L5 changes ZERO bytes of legacy_v1 vs untouched baseline
    put_legacy_base, call_legacy_base = copy.deepcopy(src["cases"])
    _set_case_layer(put_legacy_base, "L2.book.short", {
        "instrument_name": player.entry(put_legacy_base, "L1.contract")["values"]["short_instrument"],
        "index_price": 69_400.0,
        "timestamp": read_at - 1_500,
    }, asof_ms=read_at - 1_000)
    legacy_before_l2 = player.build_input(
        put_legacy_base, call_legacy_base, put_card, call_card,
        index_path=src["index_path"], input_profile=player.INPUT_PROFILE_LEGACY,
    )
    put_legacy_clean, call_legacy_clean = copy.deepcopy(put_legacy_base), copy.deepcopy(call_legacy_base)
    _set_case_layer(put_legacy_clean, "L4.expiry_vol", {
        "realized_vol": _rv24_payload(rv_bar_end, computed_at_ms=read_at - 5 * M, rv_pct=47.80),
        "p_touch_short": 0.19,
    }, asof_ms=read_at - 5 * M)
    player.entry(put_legacy_clean, "L2.book.short")["values"]["mark_iv"] = 55.10
    legacy_after = player.build_input(
        put_legacy_clean, call_legacy_clean, put_card, call_card,
        index_path=src["index_path"], input_profile=player.INPUT_PROFILE_LEGACY,
    )
    assert legacy_after == legacy_before_l2
    assert "p_touch_short" not in legacy_after and "RV24" not in legacy_after and legacy_before == legacy_before


def test_real_corpus_frozen_rv24_and_spot_replay():
    root = _find_workspace_root()
    if root is None:
        pytest.skip("local p4-manual-ab-corpus not present")
    manifest = json.loads((root / "p4-manual-ab-corpus" / "manifest.json").read_text(encoding="utf-8"))
    checked = 0
    for item in manifest.get("hands", ()):
        src_path = root / str(item.get("source_dir", "")) / "evidence" / "source.json"
        if not src_path.is_file():
            continue
        checked += 1
        src = json.loads(src_path.read_text(encoding="utf-8"))
        put_case, call_case = src["cases"]
        put_card, call_card = src["cards"]
        read_at = max(put_card["read_at_ms"], call_card["read_at_ms"])

        # Production strict marks unclocked historical RV24 as UNKNOWN; offline replay reconstructs if present
        md_prod = player.build_input(
            put_case, call_case, put_card, call_card,
            index_path=src.get("index_path"),
            ard_history=src.get("structure_history"),
            ard_band_snapshot=src.get("band_snapshot"),
            input_profile=player.INPUT_PROFILE_P0_STRICT,
        )
        assert "p_touch_short" not in md_prod and "RV4h" not in md_prod
        l4 = player.entry(put_case, "L4.expiry_vol")
        if isinstance(l4, dict) and isinstance((l4.get("values") or {}).get("realized_vol"), dict):
            assert "历史实现波动率RV24为UNKNOWN" in md_prod
            md_replay = player.build_input(
                put_case, call_case, put_card, call_card,
                index_path=src.get("index_path"),
                ard_history=src.get("structure_history"),
                ard_band_snapshot=src.get("band_snapshot"),
                input_profile=player.INPUT_PROFILE_P0_STRICT,
                offline_reconstructed_rv_clock_ms=put_card["read_at_ms"],
            )
            assert "RECONSTRUCTED_OFFLINE" in md_replay
            if call_card["read_at_ms"] > put_card["read_at_ms"]:
                md_future_clock = player.build_input(
                    put_case, call_case, put_card, call_card,
                    index_path=src.get("index_path"),
                    ard_history=src.get("structure_history"),
                    ard_band_snapshot=src.get("band_snapshot"),
                    input_profile=player.INPUT_PROFILE_P0_STRICT,
                    offline_reconstructed_rv_clock_ms=call_card["read_at_ms"],
                )
                assert "历史实现波动率RV24为UNKNOWN" in md_future_clock

        # L5 spot on frozen corpus lacks end_ts_ms -> compare fails closed
        l5 = player.entry(put_case, "L5.volume_price")
        assert player.derive_non_overlapping_spot_windows(l5, cutoff=read_at)["comparable"] is False
    if checked == 0:
        pytest.skip("local p4-manual-ab-corpus source fixtures not materialized on disk")


# ---------------------------------------------------------------------------
# 3. Hold 5m Live Pin, 20m SLA, Archive Merge, No-Rollback, band_half & Cat C
# ---------------------------------------------------------------------------

def _frozen_condition_doc(expiry_ms: int, entry_ms: int) -> dict:
    return {
        "schema": "astra_player_condition@1.0",
        "mechanical_contract_version": condition.MECHANICAL_CONTRACT_VERSION,
        "contract": {
            "venue": "Deribit",
            "underlying": "BTC",
            "settlement_currency": "BTC",
            "quantity_btc": 1.0,
            "side": "put",
            "short_instrument": "BTC-26SEP26-97000-P",
            "long_instrument": "BTC-26SEP26-95000-P",
            "short_strike_usd": 97_000.0,
            "long_strike_usd": 95_000.0,
            "expiry_ms": expiry_ms,
        },
        "entry_at_ms": entry_ms,
        "entry_index_usd": 100_000.0,
        "check_clock": {"first_at_ms": entry_ms + 5 * M, "interval_ms": 5 * M},
        "invalidation": "entry_not_returned",
        "depends_on_ard": True,
    }


def test_hold_5m_live_pin_20m_sla_no_rollback_and_band_half(tmp_path):
    tick = ((UNDERWRITING_T + 2 * HOUR) // M) * M
    expiry_ms = tick + 8 * HOUR
    entry_ms = tick - HOUR

    # 1) _source_entry_context extracts band_half from ev_src['band_snapshot']
    hand_dir = tmp_path / "dossiers" / "Deribit_BTC_test" / "hands" / "hand_1"
    (hand_dir / "evidence").mkdir(parents=True)
    synth_case = {"build": {"built_at_ms": entry_ms}, "layers": {}, "refs": {}}
    _set_case_layer(synth_case, "L1.contract", {"side": "put", "expiry_ms": expiry_ms}, asof_ms=entry_ms - M)
    _set_case_layer(synth_case, "L3.expiry_walls", {
        "expiry_ms": expiry_ms,
        "expected_move_usd": 1800.0,
        "walls": {"pin": 100_000.0, "pin_status": "ok", "pin_method": evidence_qual.PIN_METHOD, "pin_source": evidence_qual.PIN_SOURCE},
        "pin_status": "ok",
        "pin_method": evidence_qual.PIN_METHOD,
        "pin_source": evidence_qual.PIN_SOURCE,
    }, asof_ms=entry_ms - M)
    ev_src = {
        "band_snapshot": {
            "source_available_at_ms": entry_ms - 4 * HOUR - 5 * M,
            "snapshot_exported_ms": entry_ms - 4 * HOUR - 6 * M,
            "factor_snapshot": {
                "anchor": {
                    "gex_source_ts_ms": entry_ms - 4 * HOUR - 7 * M,
                    "band_half": 600.0,
                    "ready": True,
                    "freshness": "FRESH",
                }
            },
        },
        "cards": [{"side": "put", "expiry_ms": expiry_ms, "read_at_ms": entry_ms, "expected_move_usd": 1800.0,
                   "walls": {"pin": 100_000.0, "pin_status": "ok", "pin_method": evidence_qual.PIN_METHOD, "pin_source": evidence_qual.PIN_SOURCE}}],
        "cases": [synth_case],
    }
    (hand_dir / "evidence" / "source.json").write_text(json.dumps(ev_src), encoding="utf-8")
    (hand_dir / "evidence" / "identity.json").write_text(json.dumps({"input_profile": player.INPUT_PROFILE_P0_STRICT}), encoding="utf-8")
    src_ctx = tracking._source_entry_context(hand_dir, side="put", expiry_ms=expiry_ms)
    assert src_ctx["entry_pin_snapshot"]["band_half"] is not None
    assert src_ctx["entry_pin_snapshot"]["band_half"] > 0

    ep_dir = tmp_path / "dossiers" / "Deribit_BTC_test" / "episodes" / "ep_1"
    ep_dir.mkdir(parents=True)
    (ep_dir / "episode.json").write_text(json.dumps({
        "hand_id": "hand_1",
        "entry_pin_snapshot": src_ctx["entry_pin_snapshot"],
    }), encoding="utf-8")
    index_path = {
        "schema": "index_path@0.1", "index_name": "btc_usd", "expiry_ms": expiry_ms,
        "points": [[t, 99_600.0] for t in range(entry_ms - 5 * HOUR, tick + 1, M)],
    }
    archive_root = tmp_path / "research_archive"

    # 2) Live 5m Pin ONLY (NO structure/*.jsonl file!) -> position_only & same_measure_ard work cleanly
    live_pts = [
        _make_series_point(asof_ms=tick - 10 * M, pin=100_000.0, expiry_ms=expiry_ms, computed_at_ms=tick - 10 * M, available_at_ms=tick - 9 * M),
        _make_series_point(asof_ms=tick - 5 * M, pin=99_200.0, expiry_ms=expiry_ms, computed_at_ms=tick - 5 * M, available_at_ms=tick - 4 * M),
    ]
    series_rows = [{"metrics": {"asof_ms": p["recorded_at_ms"] - 50, "levels": p["candidate"]["expiry_levels"]}} for p in live_pts]
    latest_pt = live_pts[-1]

    frozen_pos = _frozen_condition_doc(expiry_ms, entry_ms)
    st_pos = tracking._episode_ard_state(
        frozen_pos, ep_dir, archive_root, index_path, tick,
        entry_pin_snapshot=src_ctx["entry_pin_snapshot"], series_rows=series_rows, latest_point=latest_pt,
    )
    assert st_pos["comparison"]["pin_eval_mode"] == "position_only"
    assert st_pos["comparison"]["pin_lineage"] == "live_5m"
    assert st_pos["comparison"]["current_sample_available"] is True
    assert st_pos["comparison"]["same_measure"] is False
    assert st_pos["comparison"]["source_method_comparable"] is True
    assert st_pos["comparison"]["pin_price_changed"] is True
    assert st_pos["comparison"]["new_pin"] == 99_200.0
    assert st_pos["comparison"]["new_neighborhood_events"] is None

    entry_hist = [{
        "schema": "structure_snapshot@0.1", "expiry_ms": expiry_ms,
        "at_ms": entry_ms - 10 * M, "available_at_ms": entry_ms - 9 * M,
        "profile": {"walls": {"pin": 100_000.0}}, "pin_status": "ok",
        "pin_method": evidence_qual.PIN_METHOD, "pin_source": evidence_qual.PIN_SOURCE,
    }]
    entry_facts = ard.analyze(
        entry_hist, index_path, expiry_ms=expiry_ms, cutoff_ms=entry_ms,
        band_snapshot=ev_src["band_snapshot"], window_ms=4 * HOUR, strict_asof=True,
    )
    frozen_same = condition.freeze_condition(
        ep_dir / "condition.json",
        contract=frozen_pos["contract"],
        entry_at_ms=entry_ms,
        entry_index_usd=100_000.0,
        historical_facts="前窗未进责任区",
        accepted_conditions="维持责任外",
        invalidation="entry_not_returned",
        first_check_ms=entry_ms + 5 * M,
        check_interval_ms=5 * M,
        ard_basis={
            "facts": entry_facts,
            "responsibility_meaning": "关注100000 Pin位置与半宽600邻域",
            "review_on": "Pin向短腿迁移或离返变化触发复核",
        },
    )
    frozen_same["depends_on_ard"] = True
    st_same = tracking._episode_ard_state(
        frozen_same, ep_dir, archive_root, index_path, tick,
        series_rows=series_rows, latest_point=latest_pt,
    )
    assert st_same["comparison"]["pin_eval_mode"] == "same_measure_ard"
    assert st_same["comparison"]["same_measure"] is True
    assert st_same["comparison"]["current_sample_available"] is True

    # 3) Late older 30m archive row NEVER rolls back newer 5m Pin, and live_5m beats compat_backfill at same at_ms
    struct_dir = archive_root / "structure"
    struct_dir.mkdir(parents=True)
    struct_file = struct_dir / f"{dossier.utc(expiry_ms).date().isoformat()}.jsonl"
    arch_rows = [
        # Same timestamp as tick-5m compat_backfill with stale value -> must NOT beat live_5m
        {"schema": "structure_snapshot@0.1", "expiry_ms": expiry_ms, "at_ms": tick - 5 * M,
         "profile": {"walls": {"pin": 95_000.0}},
         "pin_method": evidence_qual.PIN_METHOD, "pin_source": evidence_qual.PIN_SOURCE,
         "compat_backfill_applied": True, "available_at_ms": tick - 4 * M},
        # Older 30m snapshot arriving later -> must NOT roll back 99,200 to 101,500
        {"schema": "structure_snapshot@0.1", "expiry_ms": expiry_ms, "at_ms": tick - 25 * M,
         "profile": {"walls": {"pin": 101_500.0}},
         "pin_method": evidence_qual.PIN_METHOD, "pin_source": evidence_qual.PIN_SOURCE,
         "available_at_ms": tick - M},
    ]
    struct_file.write_text("\n".join(json.dumps(r) for r in arch_rows) + "\n", encoding="utf-8")
    st_merge = tracking._episode_ard_state(
        frozen_pos, ep_dir, archive_root, index_path, tick,
        entry_pin_snapshot=src_ctx["entry_pin_snapshot"], series_rows=series_rows, latest_point=latest_pt,
    )
    assert st_merge["comparison"]["new_pin"] == 99_200.0
    assert st_merge["comparison"]["pin_lineage"] == "live_5m"

    # 4) 30m archive 20m < age <= 40m (inside ARD GAP_MS=40m!) without live 5m MUST be STALE
    arch_only_25m = [{
        "schema": "structure_snapshot@0.1", "expiry_ms": expiry_ms, "at_ms": tick - 25 * M,
        "profile": {"walls": {"pin": 100_000.0}},
        "pin_method": evidence_qual.PIN_METHOD, "pin_source": evidence_qual.PIN_SOURCE,
        "available_at_ms": tick - 24 * M,
    }]
    struct_file.write_text(json.dumps(arch_only_25m[0]) + "\n", encoding="utf-8")
    st_stale_30m = tracking._episode_ard_state(
        frozen_same, ep_dir, archive_root, index_path, tick, series_rows=[], latest_point=None,
    )
    assert st_stale_30m["comparison"]["pin_eval_mode"] == "STALE"
    assert st_stale_30m["comparison"]["pin_price_changed"] is None
    assert st_stale_30m["comparison"]["current_sample_available"] is False

    # Direct compare_ard_basis 20m SLA enforcement check against frozen condition.json
    stale_facts = ard.analyze(
        arch_only_25m, index_path, expiry_ms=expiry_ms, cutoff_ms=tick,
        band_snapshot=ev_src["band_snapshot"], window_ms=tick - entry_facts["start_ms"], strict_asof=True,
    )
    cond_cmp_stale = condition.compare_ard_basis(ep_dir / "condition.json", stale_facts, tick)
    assert cond_cmp_stale["pin_eval_mode"] == "STALE"
    assert cond_cmp_stale["pin_price_changed"] is None
    assert cond_cmp_stale["current_sample_available"] is False

    # 5) Cross-expiry / wrong-method / future live Pin -> UNALIGNED / rejected
    struct_file.unlink()
    bad_method_pt = _make_series_point(
        asof_ms=tick - 2 * M, pin=96_000.0, expiry_ms=expiry_ms,
        pin_method="wrong_method@0.1", computed_at_ms=tick - 2 * M, available_at_ms=tick - M,
    )
    st_bad_method = tracking._episode_ard_state(
        frozen_pos, ep_dir, archive_root, index_path, tick,
        entry_pin_snapshot=src_ctx["entry_pin_snapshot"],
        series_rows=[{"metrics": {"asof_ms": tick - 2 * M, "levels": bad_method_pt["candidate"]["expiry_levels"]}}],
        latest_point=bad_method_pt,
    )
    assert st_bad_method["comparison"]["pin_eval_mode"] == "UNALIGNED"
    assert st_bad_method["comparison"]["pin_price_changed"] is None
    assert st_bad_method["comparison"]["current_sample_available"] is False

    future_pt = _make_series_point(
        asof_ms=tick + 5 * M, pin=96_000.0, expiry_ms=expiry_ms,
        computed_at_ms=tick + 5 * M, available_at_ms=tick + 6 * M,
    )
    st_future = tracking._episode_ard_state(
        frozen_pos, ep_dir, archive_root, index_path, tick,
        entry_pin_snapshot=src_ctx["entry_pin_snapshot"],
        series_rows=[{"metrics": {"asof_ms": tick - M, "levels": future_pt["candidate"]["expiry_levels"]}}],
        latest_point=future_pt,
    )
    assert st_future["comparison"]["pin_eval_mode"] in ("UNKNOWN", "STALE")
    assert st_future["comparison"]["pin_price_changed"] is None
    assert st_future["comparison"]["new_pin"] is None


def test_category_c_position_only_and_degraded_guards():
    now_ms = SLOT + 3 * HOUR
    frozen = {"depends_on_ard": True}
    rows_ok = [{"asof_ms": now_ms - M, "spot_usd": 99_600.0, "short_distance_pct": 2.68}]
    t0 = {"asof_ms": now_ms - 10 * M, "spot_usd": 100_000.0, "short_distance_pct": 3.09, "expected_move_usd": 1800.0}

    # Accepts position_only material shift toward short strike (put 100k -> 99.4k)
    ard_pos_only = {
        "same_measure": False,
        "pin_eval_mode": "position_only",
        "source_method_comparable": True,
        "current_sample_available": True,
        "pin_status": "ok",
        "pin_price_changed": True,
        "old_pin": 100_000.0,
        "new_pin": 99_400.0,
        "new_neighborhood_events": None,
    }
    trigs = review.detect_hold_triggers(
        frozen_condition=frozen, side="put", short_strike_usd=97_000.0,
        entry_at_ms=now_ms - 10 * M, t0_metrics=t0, series_rows=rows_ok,
        ard_comparison=ard_pos_only, now_ms=now_ms,
    )
    assert [t["category"] for t in trigs] == ["C"]

    # Legacy same_measure=False WITHOUT pin_eval_mode='position_only' MUST remain rejected
    ard_unmarked_false = {k: v for k, v in ard_pos_only.items() if k != "pin_eval_mode"}
    assert review.detect_hold_triggers(
        frozen_condition=frozen, side="put", short_strike_usd=97_000.0,
        entry_at_ms=now_ms - 10 * M, t0_metrics=t0, series_rows=rows_ok,
        ard_comparison=ard_unmarked_false, now_ms=now_ms,
    ) == []

    # Rejects STALE / UNALIGNED / UNKNOWN regardless of old/new pin fields
    for mode in ("STALE", "UNALIGNED", "UNKNOWN"):
        bad = {**ard_pos_only, "pin_eval_mode": mode, "same_measure": True}
        assert review.detect_hold_triggers(
            frozen_condition=frozen, side="put", short_strike_usd=97_000.0,
            entry_at_ms=now_ms - 10 * M, t0_metrics=t0, series_rows=rows_ok,
            ard_comparison=bad, now_ms=now_ms,
        ) == []


# ---------------------------------------------------------------------------
# 4. Hold Cat D, Exact Closed-Bar Delta, Rendering & Snapshot Safety
# ---------------------------------------------------------------------------

def test_hold_cat_d_basis_exact_closed_bars_and_fail_closed():
    now_ms = SLOT + 4 * HOUR
    entry_ms = now_ms - 20 * M
    frozen = {"depends_on_ard": False}
    t0 = {
        "asof_ms": entry_ms,
        "spot_usd": 100_000.0,
        "short_distance_pct": 3.09,
        "expected_move_usd": 1800.0,
        "flow_ready": True,
        "flow_direction": "bearish",
        "flow_cvd_4h": -20.0,
        "flow_momentum_4h_pct": -0.10,
        "flow_asof_ms": entry_ms,
        "flow_window_basis": {
            "source": "binance",
            "symbol": "BTCUSDT",
            "start_ms": entry_ms - 4 * HOUR,
            "cum_session_id": "sess_1",
            "cum_gap_count": 0,
            "cum_cvd_btc": 100.0,
            "last_bar_index": 10,
        },
    }
    base_rows = [
        {"asof_ms": now_ms - 2 * M, "spot_usd": 99_800.0, "short_distance_pct": 2.88,
         "flow_ready": True, "flow_fast_4h_ready": True, "flow_direction": "bearish",
         "flow_cvd_4h": -80.0, "flow_momentum_4h_pct": -0.35, "flow_asof_ms": now_ms - 2 * M,
         "flow_window_basis": {
             "source": "binance", "symbol": "BTCUSDT", "start_ms": entry_ms - 4 * HOUR + M,
             "cum_session_id": "sess_1", "cum_gap_count": 0, "cum_cvd_btc": -10.0, "last_bar_index": 14,
         }},
        {"asof_ms": now_ms - M, "spot_usd": 99_400.0, "short_distance_pct": 2.47,
         "flow_ready": True, "flow_fast_4h_ready": True, "flow_direction": "bearish",
         "flow_cvd_4h": -90.0, "flow_momentum_4h_pct": -0.45, "flow_asof_ms": now_ms - M,
         "flow_window_basis": {
             "source": "binance", "symbol": "BTCUSDT", "start_ms": entry_ms - 4 * HOUR + 2 * M,
             "cum_session_id": "sess_1", "cum_gap_count": 0, "cum_cvd_btc": -10.0, "last_bar_index": 15,
         }},
    ]
    trigs = review.detect_hold_triggers(
        frozen_condition=frozen, side="put", short_strike_usd=97_000.0,
        entry_at_ms=entry_ms, t0_metrics=t0, series_rows=base_rows, now_ms=now_ms,
    )
    assert [t["category"] for t in trigs] == ["D"]
    assert trigs[0]["code"] == "FLOW_ADVERSE_CONTINUATION"
    assert trigs[0]["facts"]["incremental_basis"] == "exact_closed_bars_post_ref"
    assert trigs[0]["facts"]["incremental_flow_cvd_btc"] == pytest.approx(-110.0)

    # Gap count increase MUST fail closed
    gap_rows = copy.deepcopy(base_rows)
    gap_rows[-1]["flow_window_basis"]["cum_gap_count"] = 1
    assert review.detect_hold_triggers(
        frozen_condition=frozen, side="put", short_strike_usd=97_000.0,
        entry_at_ms=entry_ms, t0_metrics=t0, series_rows=gap_rows, now_ms=now_ms,
    ) == []

    # Session ID mismatch MUST fail closed
    sess_rows = copy.deepcopy(base_rows)
    sess_rows[-1]["flow_window_basis"]["cum_session_id"] = "sess_2"
    assert review.detect_hold_triggers(
        frozen_condition=frozen, side="put", short_strike_usd=97_000.0,
        entry_at_ms=entry_ms, t0_metrics=t0, series_rows=sess_rows, now_ms=now_ms,
    ) == []

    # Non-advancing bar index MUST fail closed
    bar_rows = copy.deepcopy(base_rows)
    bar_rows[-1]["flow_window_basis"]["last_bar_index"] = 10
    assert review.detect_hold_triggers(
        frozen_condition=frozen, side="put", short_strike_usd=97_000.0,
        entry_at_ms=entry_ms, t0_metrics=t0, series_rows=bar_rows, now_ms=now_ms,
    ) == []

    # Source/symbol mismatch MUST fail closed on BOTH reversal and continuation
    sym_rows = copy.deepcopy(base_rows)
    sym_rows[-1]["flow_window_basis"]["symbol"] = "ETHUSDT"
    assert review.detect_hold_triggers(
        frozen_condition=frozen, side="put", short_strike_usd=97_000.0,
        entry_at_ms=entry_ms, t0_metrics=t0, series_rows=sym_rows, now_ms=now_ms,
    ) == []

    # flow_fast_4h_ready=False or degraded flow_qualification_status MUST fail closed
    for mut in (
        {"flow_fast_4h_ready": False},
        {"flow_qualification_status": "STALE"},
        {"flow_qualification_status": "OBSERVED_UNALIGNED"},
        {"flow_qualification_status": "UNKNOWN"},
    ):
        bad_rows = copy.deepcopy(base_rows)
        bad_rows[-1].update(mut)
        assert review.detect_hold_triggers(
            frozen_condition=frozen, side="put", short_strike_usd=97_000.0,
            entry_at_ms=entry_ms, t0_metrics=t0, series_rows=bad_rows, now_ms=now_ms,
        ) == []


def test_hold_rendering_unknowns_channel_readiness_and_prompt_preserved():
    t_now = SLOT + 2 * HOUR
    # 1) Never print "是否迁移：否" or "0 次" on degraded / position_only / None
    for ard_case in (
        {"pin_eval_mode": "STALE", "pin_price_changed": None, "old_pin": 100_000.0, "new_neighborhood_events": None},
        {"pin_eval_mode": "UNALIGNED", "pin_price_changed": None, "old_pin": 100_000.0, "new_neighborhood_events": None},
        {"pin_eval_mode": "UNKNOWN", "pin_price_changed": None, "old_pin": 100_000.0, "new_neighborhood_events": None},
        {"pin_eval_mode": "position_only", "same_measure": False, "pin_price_changed": False, "old_pin": 100_000.0, "new_pin": 100_000.0, "new_neighborhood_events": None},
    ):
        lines = "\n".join(review._format_ard_lines(ard_case))
        assert "0 次" not in lines
        assert "不适用/未测量" in lines
        if ard_case["pin_eval_mode"] != "position_only":
            assert "是否迁移：否" not in lines
        else:
            assert "仅位置迁移口径" in lines

    # 2) _format_flow_lines & build_hold_review_input surface flow_window_basis, incremental_flow_cvd_btc, channel_readiness, unknown_items
    flow_txt = "\n".join(review._format_flow_lines({
        "flow_ready": True, "flow_fast_4h_ready": True, "flow_asof_ms": t_now,
        "flow_qualification_status": "OBSERVED_ALIGNED",
        "flow_window_basis": {"source": "binance", "symbol": "BTCUSDT", "start_ms": t_now - 4 * HOUR, "cum_session_id": "s1", "last_bar_index": 20},
        "flow_direction": "bearish", "flow_fast_4h": "bearish", "flow_slow_12h": "neutral",
        "flow_cvd_4h": -80.0, "flow_momentum_4h_pct": -0.4,
    }, triggers=[{
        "category": "D", "code": "FLOW_ADVERSE_CONTINUATION",
        "facts": {"incremental_flow_cvd_btc": -45.5, "incremental_basis": "exact_closed_bars_post_ref"},
    }]))
    assert "flow_window_basis" in flow_txt and "binance" in flow_txt and "BTCUSDT" in flow_txt
    assert "incremental_flow_cvd_btc=-45.50 BTC" in flow_txt and "exact_closed_bars_post_ref" in flow_txt

    rev_flow_txt = "\n".join(review._format_flow_lines({
        "flow_ready": True, "flow_asof_ms": t_now, "flow_direction": "bearish",
        "flow_window_basis": {"source": "binance", "symbol": "BTCUSDT", "start_ms": t_now - 4 * HOUR},
    }, triggers=[{"category": "D", "code": "FLOW_REVERSAL", "facts": {"reversal_confirmed": True}}]))
    assert "reversal_confirmed=True" in rev_flow_txt

    # Canonical summarize_channel_capabilities & _derive_hold_channel_readiness (QUALIFIED_NO_EVENT vs BLIND_STALE)
    derived_blind = review._derive_hold_channel_readiness(
        flow_summary={"flow_ready": False, "flow_fast_4h_ready": False, "flow_qualification_status": "STALE", "flow_qualification_reasons": ["age>60m"]},
        ard_summary={"pin_eval_mode": "STALE", "pin_price_changed": None, "old_pin": 100_000.0, "new_neighborhood_events": None},
        triggers=[{"category": "B"}],
    )
    assert derived_blind["channels"]["spot_flow"]["capability"] == evidence_qual.CAP_BLIND_STALE
    assert derived_blind["channels"]["pin_structure"]["capability"] == evidence_qual.CAP_BLIND_STALE

    md_hold = review.build_hold_review_input(
        contract={"side": "put", "short_strike_usd": 97_000.0, "long_strike_usd": 95_000.0, "expiry_ms": t_now + 6 * HOUR},
        entry_at_ms=t_now - HOUR,
        initial_decision={"judgment": {"action": "Accept", "side": "put", "comparison": "Put", "put_counter": "反证"}},
        frozen_condition={"depends_on_ard": True, "invalidation": "entry_not_returned", "check_clock": {"interval_ms": 5 * M}},
        triggers=[{"category": "B", "code": "MARGIN_DETERIORATION", "summary_cn": "收缩", "observed_at_ms": t_now - M}],
        t0_summary={"locked_at": t_now - HOUR, "entry_index_usd": 100_000.0, "short_distance_pct": 3.0, "expected_move_usd": 1800.0},
        post_entry_summary={"sample_count": 3, "latest_spot_usd": 98_500.0, "min_short_distance_pct": 1.5, "latest_short_distance_pct": 1.5},
        condition_summary={"program_triggered": False, "mechanical_condition_state": "INTACT", "checks_count": 3, "latest_check_at_ms": t_now},
        ard_summary={"pin_eval_mode": "STALE", "pin_price_changed": None, "old_pin": 100_000.0, "new_neighborhood_events": None},
        flow_summary={"flow_ready": False, "flow_fast_4h_ready": False, "flow_qualification_status": "STALE", "flow_qualification_reasons": ["age>60m"]},
        quote_summary={"short_buyback_gaps": []},
        channel_readiness=None,
    )
    assert "BLIND_STALE" in md_hold
    assert "通道观测能力" in md_hold and "是否迁移：否" not in md_hold

    md_no_event = review.build_hold_review_input(
        contract={"side": "put", "short_strike_usd": 97_000.0, "long_strike_usd": 95_000.0, "expiry_ms": t_now + 6 * HOUR},
        entry_at_ms=t_now - HOUR,
        initial_decision={"judgment": {"action": "Accept", "side": "put", "comparison": "Put", "put_counter": "反证"}},
        frozen_condition={"depends_on_ard": True, "invalidation": "entry_not_returned", "check_clock": {"interval_ms": 5 * M}},
        triggers=[{"category": "B", "code": "MARGIN_DETERIORATION", "summary_cn": "收缩", "observed_at_ms": t_now - M}],
        t0_summary={"locked_at": t_now - HOUR, "entry_index_usd": 100_000.0, "short_distance_pct": 3.0, "expected_move_usd": 1800.0},
        post_entry_summary={"sample_count": 3, "latest_spot_usd": 98_500.0, "min_short_distance_pct": 1.5, "latest_short_distance_pct": 1.5},
        condition_summary={"program_triggered": False, "mechanical_condition_state": "INTACT", "checks_count": 3, "latest_check_at_ms": t_now},
        ard_summary={"pin_eval_mode": "same_measure_ard", "same_measure": True, "pin_price_changed": False, "old_pin": 100_000.0, "new_pin": 100_000.0, "new_neighborhood_events": []},
        flow_summary={"flow_ready": True, "flow_fast_4h_ready": True, "flow_asof_ms": t_now - M, "flow_qualification_status": "OBSERVED_ALIGNED",
                      "flow_window_basis": {"source": "binance", "symbol": "BTCUSDT", "start_ms": t_now - 4 * HOUR - M}},
        quote_summary={"short_buyback_gaps": []},
        channel_readiness=None,
    )
    assert "QUALIFIED_NO_EVENT" in md_no_event and "关键盲区：无" in md_no_event

    # 3) Prompt constants preserved strictly
    assert review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION == "player_initial_system@1.2-p5-candidate"
    assert hashlib.sha256(review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT.encode("utf-8")).hexdigest() == (
        "b746c2d2f7f996d290992e01b11d9e4c4792a0e1afcacb900bbf0dfc678b6f34"
    )
    assert review.HOLD_REVIEW_SYSTEM_PROMPT == (
        "你是中性回路影子持仓复核 AI。只可基于增量牌面输出 HOLD、REVOKE 或 UNKNOWN，严禁改选 Put/Call，严禁推翻已触发的 CONDITION_TRIGGERED。"
    )


def test_hold_tracking_snapshot_readiness_no_old_pin_backfill_and_idempotency(tmp_path):
    wb, d, hand, now, expiry = wb_sample(tmp_path, "Accept / Call")
    with serving(wb) as base:
        service = wb_post(base, wb)
        tracking.link(d, hand, service, LEGACY_RULE)
        exported = service("export", dict(dossier_id=hand.parent.parent.name, hand_id=hand.name))
        ep_id = tracking.accepted(hand)["origin"]["dossier_episode_id"]
        ep_dir = hand.parent.parent / "episodes" / ep_id
        frozen = tracking.load(ep_dir / "condition.json")

        # Mark condition.json depends_on_ard=True & entry_pin_snapshot present, NO 30m structure archive
        frozen["depends_on_ard"] = True
        tracking.dossier.write(ep_dir / "condition.json", tracking.dossier.encode(frozen), replace=True)
        ep_meta = tracking.load(ep_dir / "episode.json")
        ep_meta["entry_pin_snapshot"] = {
            "expiry_ms": expiry, "pin": 100_000.0,
            "pin_method": evidence_qual.PIN_METHOD, "pin_source": evidence_qual.PIN_SOURCE, "band_half": 600.0,
        }
        tracking.dossier.write(ep_dir / "episode.json", tracking.dossier.encode(ep_meta), replace=True)

        # Tick 1: trigger Category B with ONLY unaligned Pin + no flow_window_basis -> HOLD, snapshot.pin MUST BE None (never old_pin!)
        now[0] += 15 * M
        short_k = float(frozen["contract"]["short_strike_usd"])
        bad_pt = _make_series_point(
            side="call", short_strike=103_000.0, long_strike=105_000.0,
            asof_ms=now[0] - M, spot=short_k - 150.0,
            pin=short_k - 3_000.0, expiry_ms=expiry, pin_method="bad_method",
        )
        exported_bad = copy.deepcopy(exported)
        exported_bad["latest_point"] = bad_pt
        exported_bad["points"] = [
            {"metrics": {"metrics_version": locked_compare.SERIES_METRICS_VERSION, "asof_ms": now[0] - 2 * M,
                         "spot_usd": short_k - 300.0, "short_distance_pct": round(300.0 / (short_k - 300.0) * 100, 4), "expected_move_usd": 5000.0,
                         "flow_ready": True, "flow_asof_ms": now[0] - 2 * M, "levels": bad_pt["candidate"]["expiry_levels"]}},
            {"metrics": {"metrics_version": locked_compare.SERIES_METRICS_VERSION, "asof_ms": now[0] - M,
                         "spot_usd": short_k - 150.0, "short_distance_pct": round(150.0 / (short_k - 150.0) * 100, 4), "expected_move_usd": 5000.0,
                         "flow_ready": True, "flow_asof_ms": now[0] - M, "levels": bad_pt["candidate"]["expiry_levels"]}},
        ]

        llm_calls = []

        def hold_transport(*_a, **_k):
            llm_calls.append(1)
            return {"choices": [{"finish_reason": "stop", "message": {
                "content": "## HOLD\n\n继续观察。\n<HOLD_REVIEW_JSON>{\"verdict\":\"HOLD\",\"affected_conditions\":[],\"public_reply\":\"HOLD\",\"key_facts\":[\"B\"]}</HOLD_REVIEW_JSON>"
            }}]}

        res1_q = tracking.evaluate_hold_management(
            d, hand, exported_bad,
            archive_root=tmp_path / "missing_archive",
            service=lambda *_: {"state": "QUOTE_UNAVAILABLE", "latest_point": None},
            llm_config={"api_key": "k", "model": "m", "transport": hold_transport},
            debounce_ms=0, cooldown_ms=0,
        )
        assert res1_q["action"] == "QUEUED" and len(llm_calls) == 0

        now[0] += review.MIN_DEBOUNCE_MS
        exported_bad["points"].append(
            {"metrics": {"metrics_version": locked_compare.SERIES_METRICS_VERSION, "asof_ms": now[0] - M,
                         "spot_usd": short_k - 140.0, "short_distance_pct": round(140.0 / (short_k - 140.0) * 100, 4), "expected_move_usd": 5000.0,
                         "flow_ready": True, "flow_asof_ms": now[0] - M, "levels": bad_pt["candidate"]["expiry_levels"]}},
        )
        res1 = tracking.evaluate_hold_management(
            d, hand, exported_bad,
            archive_root=tmp_path / "missing_archive",
            service=lambda *_: {"state": "QUOTE_UNAVAILABLE", "latest_point": None},
            llm_config={"api_key": "k", "model": "m", "transport": hold_transport},
            debounce_ms=0, cooldown_ms=0,
        )
        assert res1["action"] == "REVIEWED" and res1["verdict"] == "HOLD"
        snaps1 = sorted((ep_dir / "reviews").glob("*/review_snapshot.json"))
        assert len(snaps1) == 1
        snap1 = json.loads(snaps1[-1].read_text(encoding="utf-8"))
        assert snap1["pin"] is None
        assert snap1["pin_eval_mode"] == "UNALIGNED"
        assert snap1["channel_readiness"]["channels"]["pin_structure"]["capability"] == evidence_qual.CAP_BLIND_UNALIGNED
        assert snap1["channel_readiness"]["channels"]["spot_flow"]["capability"] == evidence_qual.CAP_BLIND_UNALIGNED

        # Repeated idempotent tick at same state -> does NOT re-call LLM unnecessarily
        res_repeat = tracking.evaluate_hold_management(
            d, hand, exported_bad,
            archive_root=tmp_path / "missing_archive",
            service=lambda *_: {"state": "QUOTE_UNAVAILABLE", "latest_point": None},
            llm_config={"api_key": "k", "model": "m", "transport": hold_transport},
            debounce_ms=0, cooldown_ms=0,
        )
        assert len(llm_calls) == 1 and res_repeat["action"] in ("NONE", "COOLDOWN", "DEBOUNCING", "NOOP", "NO_TRIGGER")

        # Tick 2: later valid 5m live Pin + valid Cat C/B -> snapshot.pin records live new_pin and keeps band_half
        now[0] += 20 * M
        ep_meta["entry_pin_snapshot"]["pin"] = short_k - 2_000.0
        tracking.dossier.write(ep_dir / "episode.json", tracking.dossier.encode(ep_meta), replace=True)
        good_pin = short_k - 800.0
        good_pt = _make_series_point(
            side="call", short_strike=103_000.0, long_strike=105_000.0,
            asof_ms=now[0] - M, spot=short_k - 80.0,
            pin=good_pin, expiry_ms=expiry, computed_at_ms=now[0] - M, available_at_ms=now[0] - M,
        )
        exported_good = copy.deepcopy(exported)
        exported_good["latest_point"] = good_pt
        exported_good["points"] = [
            *exported_bad["points"],
            {"metrics": {
                "metrics_version": locked_compare.SERIES_METRICS_VERSION,
                "asof_ms": now[0] - 2 * M, "spot_usd": short_k - 100.0,
                "short_distance_pct": round(100.0 / (short_k - 100.0) * 100, 4), "expected_move_usd": 6000.0,
                "flow_ready": True, "flow_fast_4h_ready": True, "flow_asof_ms": now[0] - 2 * M,
                "flow_window_basis": {"source": "binance", "symbol": "BTCUSDT", "start_ms": now[0] - 4 * HOUR},
                "levels": good_pt["candidate"]["expiry_levels"],
            }},
            {"metrics": {
                "metrics_version": locked_compare.SERIES_METRICS_VERSION,
                "asof_ms": now[0] - M, "spot_usd": short_k - 80.0,
                "short_distance_pct": round(80.0 / (short_k - 80.0) * 100, 4), "expected_move_usd": 6000.0,
                "flow_ready": True, "flow_fast_4h_ready": True, "flow_asof_ms": now[0] - M,
                "flow_window_basis": {"source": "binance", "symbol": "BTCUSDT", "start_ms": now[0] - 4 * HOUR + M},
                "levels": good_pt["candidate"]["expiry_levels"],
            }},
        ]
        res2_q = tracking.evaluate_hold_management(
            d, hand, exported_good,
            archive_root=tmp_path / "missing_archive",
            service=lambda *_: {"state": "QUOTE_UNAVAILABLE", "latest_point": None},
            llm_config={"api_key": "k", "model": "m", "transport": hold_transport},
            debounce_ms=0, cooldown_ms=0,
        )
        assert res2_q["action"] == "QUEUED" and len(llm_calls) == 1

        now[0] += review.MIN_DEBOUNCE_MS
        exported_good["points"].append(
            {"metrics": {
                "metrics_version": locked_compare.SERIES_METRICS_VERSION,
                "asof_ms": now[0] - M, "spot_usd": short_k - 70.0,
                "short_distance_pct": round(70.0 / (short_k - 70.0) * 100, 4), "expected_move_usd": 6000.0,
                "flow_ready": True, "flow_fast_4h_ready": True, "flow_asof_ms": now[0] - M,
                "flow_window_basis": {"source": "binance", "symbol": "BTCUSDT", "start_ms": now[0] - 4 * HOUR + 11 * M},
                "levels": good_pt["candidate"]["expiry_levels"],
            }},
        )
        res2 = tracking.evaluate_hold_management(
            d, hand, exported_good,
            archive_root=tmp_path / "missing_archive",
            service=lambda *_: {"state": "QUOTE_UNAVAILABLE", "latest_point": None},
            llm_config={"api_key": "k", "model": "m", "transport": hold_transport},
            debounce_ms=0, cooldown_ms=0,
        )
        assert res2["action"] == "REVIEWED" and res2["verdict"] == "HOLD"
        snaps2 = sorted((ep_dir / "reviews").glob("*/review_snapshot.json"))
        assert len(snaps2) == 2
        snap2 = json.loads((ep_dir / "reviews" / res2["review_id"] / "review_snapshot.json").read_text(encoding="utf-8"))
        assert snap2["pin"] == good_pin
        assert snap2["pin_eval_mode"] == "position_only"
        assert snap2["channel_readiness"]["channels"]["pin_structure"]["capability"] == evidence_qual.CAP_POSITION_ONLY
        assert snap2["channel_readiness"]["channels"]["spot_flow"]["capability"] == evidence_qual.CAP_NO_EVENT

        # Restart / fresh Dossiers instance preserves frozen band_half, trigger_state, and snapshots idempotently
        d_restarted = dossier.Dossiers(wb.config.shadow_dossier_root, clock=lambda: now[0])
        res_after_restart = tracking.evaluate_hold_management(
            d_restarted, hand, exported_good,
            archive_root=tmp_path / "missing_archive",
            service=lambda *_: {"state": "QUOTE_UNAVAILABLE", "latest_point": None},
            llm_config={"api_key": "k", "model": "m", "transport": hold_transport},
            debounce_ms=0, cooldown_ms=0,
        )
        assert len(llm_calls) == 2 and res_after_restart["action"] in ("NONE", "COOLDOWN", "DEBOUNCING", "NOOP", "NO_TRIGGER")
        assert json.loads((ep_dir / "episode.json").read_text(encoding="utf-8"))["entry_pin_snapshot"]["band_half"] == 600.0
        assert not any(tracking.load(p)["kind"] == "SIMULATED_ORDER" for p in (ep_dir / "events").glob("*.json"))

        # audit_record() -> real serialized hold_reviews -> renderPlayerAudit (Node) end-to-end verification
        rec_detail = tracking.audit_record(hand, now[0], {ep_dir.name: "ACTIVE"}, detail=True)
        h_revs = rec_detail["hold_reviews"]
        assert len(h_revs) == 2
        by_id = {r["review_id"]: r for r in h_revs}
        assert by_id[res1["review_id"]]["pin_eval_mode"] == "UNALIGNED"
        assert by_id[res2["review_id"]]["pin_eval_mode"] == "position_only"
        assert by_id[res2["review_id"]]["channel_readiness"]["channels"]["pin_structure"]["capability"] == evidence_qual.CAP_POSITION_ONLY
        assert by_id[res2["review_id"]]["evidence_snapshot"]["pin"] == good_pin

        import shutil
        import subprocess
        node = shutil.which("node")
        if node:
            pa_url = (Path(__file__).resolve().parents[1] / "deploy" / "astra_workbench_v3" / "player-audit.js").as_uri()
            payload_json = json.dumps(rec_detail, ensure_ascii=False)
            js = f"""
class FakeNode {{
  constructor(tag) {{ this.tagName = tag; this.children = []; this.attrs = {{}}; this.text = ""; }}
  append(...kids) {{ for (const k of kids) if (k != null) this.children.push(typeof k === "string" ? {{ text: k, children: [] }} : k); }}
  setAttribute(k, v) {{ this.attrs[k] = String(v); }}
  addEventListener() {{}}
  set innerHTML(v) {{ this._html = v; this.children = []; }}
  set className(v) {{ this.attrs.class = v; }}
  set textContent(v) {{ this.text = String(v ?? ""); }}
  get textContent() {{ return this.text; }}
}}
globalThis.Node = FakeNode;
globalThis.document = {{ createElement: (t) => new FakeNode(t), createTextNode: (t) => Object.assign(new FakeNode("#text"), {{ text: String(t ?? "") }}) }};
const {{ renderPlayerAudit }} = await import({json.dumps(pa_url)});
const detail = {payload_json};
const box = new FakeNode("div");
renderPlayerAudit(box, {{ cards: [], ref: "d1/h1", expanded: new Set(), detail }}, {{ authenticated: true }});
function walk(n) {{ return (n.text || "") + " " + (n.children || []).map(walk).join(" "); }}
const out = walk(box);
if (!out.includes("Pin 口径：仅持仓参考")) throw new Error("missing position_only badge: " + out);
if (!out.includes("通道降级：pin_structure")) throw new Error("missing channel_readiness badge: " + out);
console.log("REAL_AUDIT_JS_OK");
"""
            proc = subprocess.run([node, "--input-type=module", "-e", js], capture_output=True, text=True, check=False)
            assert proc.returncode == 0 and "REAL_AUDIT_JS_OK" in proc.stdout, proc.stderr + proc.stdout


def test_unaligned_t0_and_last_review_flow_blocked_from_cat_d_and_prompts():
    """Unaligned t0/last_review flow_direction must NOT trigger FLOW_REVERSAL or leak into Hold Section 1/2."""
    now_ms = SLOT + 4 * HOUR
    entry_ms = now_ms - 20 * M
    frozen = {
        "contract": {"side": "put", "short_strike_usd": 97_000.0, "long_strike_usd": 95_000.0, "expiry_ms": SLOT + 16 * HOUR},
        "invalidation": "new_entry",
        "invalidation_meaning": "严格进入即触发",
        "accepted_conditions": ["保持 97,000 上方。"],
        "entry_index_usd": 100_000.0,
        "depends_on_ard": False,
    }
    unaligned_t0 = {
        "asof_ms": entry_ms,
        "spot_usd": 100_000.0,
        "short_distance_pct": 3.0,
        "expected_move_usd": 1800.0,
        "flow_ready": True,
        "flow_direction": "bullish",
        "flow_asof_ms": None,
        "flow_window_basis": None,
        "flow_qualification_status": "OBSERVED_UNALIGNED",
    }
    aligned_bearish_rows = [
        {
            "asof_ms": now_ms - M,
            "spot_usd": 99_400.0,
            "short_distance_pct": 2.4,
            "flow_ready": True,
            "flow_fast_4h_ready": True,
            "flow_qualification_status": "OBSERVED_ALIGNED",
            "flow_direction": "bearish",
            "flow_cvd_4h": -120.0,
            "flow_momentum_4h_pct": -0.5,
            "flow_asof_ms": now_ms - M,
            "flow_window_basis": {
                "source": "binance_spot_aggtrades",
                "symbol": "BTCUSDT",
                "start_ms": now_ms - 4 * HOUR - M,
                "cum_session_id": "s1",
                "cum_gap_count": 0,
                "cum_cvd_btc": -120.0,
                "last_bar_index": 20,
            },
        }
    ]
    trigs = review.detect_hold_triggers(
        frozen_condition=frozen, side="put", short_strike_usd=97_000.0,
        entry_at_ms=entry_ms, t0_metrics=unaligned_t0, series_rows=aligned_bearish_rows, now_ms=now_ms,
    )
    assert all(t["category"] != "D" for t in trigs)

    md_hold = review.build_hold_review_input(
        contract=frozen["contract"],
        entry_at_ms=entry_ms,
        initial_decision={"judgment": {"action": "Accept", "side": "put"}},
        frozen_condition=frozen,
        triggers=[],
        t0_summary=unaligned_t0,
        post_entry_summary={"sample_count": 1, "latest_spot_usd": 99_400.0, "latest_short_distance_pct": 2.4},
        condition_summary={"program_triggered": False, "mechanical_condition_state": "INTACT"},
        ard_summary="未开启",
        flow_summary={
            "flow_ready": True, "flow_fast_4h_ready": False,
            "flow_qualification_status": "OBSERVED_UNALIGNED", "flow_direction": "bearish", "flow_cvd_4h": -99.0,
        },
        quote_summary=None,
        prior_reviews=[{
            "review_id": "rev_prev",
            "verdict": "HOLD",
            "reviewed_at_ms": now_ms - 10 * M,
            "spot_usd": 99_700.0,
            "short_distance_pct": 2.7,
            "flow_direction": "bullish",
            "flow_asof_ms": None,
            "flow_qualification_status": "OBSERVED_UNALIGNED",
            "public_reply": "ok",
        }],
    )
    sec1_line = next(ln for ln in md_hold.splitlines() if "入场时主动流基线" in ln)
    sec2_line = next(ln for ln in md_hold.splitlines() if "上次复核时状态" in ln)
    assert "资格未通过" in sec1_line and "入场时非不利方向" not in sec1_line
    assert "未对齐/未核实" in sec2_line and "偏买" not in sec2_line


def test_hold_pin_latest_reason_not_tainted_and_unaligned_flow_hidden(tmp_path):
    """Older >20m series_rows must not taint fresh live_5m Pin qualification_reasons; unqualified flow hides raw CVD."""
    base_ms = 1_795_450_000_000
    tick_ms = base_ms + 30 * 60_000
    expiry_ms = base_ms + 16 * 3_600_000
    pin_method = tracking.dossier.archive.expiry_structure.PIN_METHOD
    pin_source = tracking.dossier.archive.expiry_structure.PIN_SOURCE
    frozen = {
        "contract": {
            "side": "put",
            "short_strike_usd": 62_000.0,
            "long_strike_usd": 60_000.0,
            "expiry_ms": expiry_ms,
        },
        "entry_at_ms": base_ms,
        "entry_index_usd": 62_785.0,
        "invalidation": "new_entry",
        "invalidation_meaning": "入场后严格进入责任区触发。",
        "accepted_conditions": ["保持 62,000 上方。"],
        "confirmation_excerpt": "每 5 分钟检查。",
        "check_clock": {"first_at_ms": base_ms + 60_000, "interval_ms": 300_000},
    }
    entry_pin = {
        "pin": 64_000.0,
        "expiry_ms": expiry_ms,
        "pin_method": pin_method,
        "pin_source": pin_source,
        "pin_status": "ok",
        "at_ms": base_ms - 60_000,
        "ready_at_ms": base_ms - 60_000,
    }
    series_rows = [
        {
            "metrics": {
                "asof_ms": base_ms,
                "levels": {
                    "pin": 64_000.0, "expiry_ms": expiry_ms,
                    "pin_method": pin_method, "pin_source": pin_source,
                    "pin_status": "ok", "computed_at_ms": base_ms, "available_at_ms": base_ms,
                },
            }
        },
        {
            "metrics": {
                "asof_ms": tick_ms,
                "levels": {
                    "pin": 60_500.0, "expiry_ms": expiry_ms,
                    "pin_method": pin_method, "pin_source": pin_source,
                    "pin_status": "ok", "computed_at_ms": tick_ms, "available_at_ms": tick_ms,
                },
            }
        },
    ]
    st = tracking._episode_ard_state(
        frozen, None, tmp_path / "no_arch", {"points": []}, tick_ms,
        entry_pin_snapshot=entry_pin, series_rows=series_rows, latest_point=None,
    )
    assert st is not None
    assert st["comparison"]["pin_eval_mode"] == "position_only"
    assert st["comparison"]["pin_lineage"] == "live_5m"
    assert st["comparison"]["pin_qualification_reasons"] == []

    # Unaligned / STALE flow hides raw directional & CVD readings in Hold card formatting
    for bad_status in ("OBSERVED_UNALIGNED", "STALE", "UNKNOWN"):
        hidden = "\n".join(review._format_flow_lines({
            "flow_ready": True,
            "flow_fast_4h_ready": True,
            "flow_asof_ms": None if bad_status == "OBSERVED_UNALIGNED" else base_ms,
            "flow_qualification_status": bad_status,
            "flow_direction": "bullish",
            "flow_fast_4h": "bullish",
            "flow_slow_12h": "bullish",
            "flow_cvd_4h": 232.01,
            "flow_momentum_4h_pct": 1.70,
            "flow_cvd_12h": 391.57,
            "flow_momentum_12h_pct": 1.93,
        }))
        assert "原始方向与 CVD 数值已降级隐藏" in hidden
        assert "232.01" not in hidden
        assert "偏买" not in hidden


def test_multi_round_hold_degraded_or_stale_flow_not_leaked_in_section3_delta():
    """Aligned prior review followed by OBSERVED_UNALIGNED or STALE flow must not leak 4h CVD or clock delta in Section 3."""
    now_ms = SLOT + 4 * HOUR
    entry_ms = now_ms - 60 * M
    frozen = {
        "contract": {"side": "put", "short_strike_usd": 97_000.0, "long_strike_usd": 95_000.0, "expiry_ms": SLOT + 16 * HOUR},
        "invalidation": "new_entry",
        "invalidation_meaning": "严格进入即触发",
        "accepted_conditions": ["保持 97,000 上方。"],
        "entry_index_usd": 100_000.0,
        "depends_on_ard": False,
    }
    aligned_basis = {
        "source": "binance_spot_aggtrades",
        "symbol": "BTCUSDT",
        "start_ms": now_ms - 4 * HOUR - M,
        "end_ms": now_ms - M,
        "available_at_ms": now_ms,
        "bar_complete": True,
    }
    prior_aligned = [{
        "review_id": "rev_1",
        "verdict": "HOLD",
        "reviewed_at_ms": now_ms - 30 * M,
        "evidence_asof_ms": now_ms - 30 * M,
        "spot_usd": 100_000.0,
        "short_distance_pct": 3.0,
        "flow_direction": "bullish",
        "flow_cvd_4h": 150.0,
        "flow_asof_ms": now_ms - 30 * M,
        "flow_fast_4h_ready": True,
        "flow_qualification_status": "OBSERVED_ALIGNED",
        "public_reply": "ok",
    }]
    common_kw = dict(
        contract=frozen["contract"],
        entry_at_ms=entry_ms,
        initial_decision={"judgment": {"action": "Accept", "side": "put"}},
        frozen_condition=frozen,
        triggers=[],
        t0_summary={"entry_index_usd": 100_000.0, "short_distance_pct": 3.0, "expected_move_usd": 1800.0},
        post_entry_summary={"sample_count": 2, "latest_spot_usd": 99_400.0, "latest_short_distance_pct": 2.4},
        condition_summary={"program_triggered": False, "mechanical_condition_state": "INTACT"},
        ard_summary="未开启",
        quote_summary=None,
        prior_reviews=prior_aligned,
    )

    md_unaligned = review.build_hold_review_input(
        **common_kw,
        flow_summary={
            "flow_ready": True,
            "flow_fast_4h_ready": True,
            "flow_qualification_status": "OBSERVED_UNALIGNED",
            "flow_asof_ms": now_ms - M,
            "flow_window_basis": None,
            "flow_direction": "bearish",
            "flow_cvd_4h": -987.65,
        },
    )
    assert "-987.65" not in md_unaligned
    assert "4h CVD" not in md_unaligned
    assert "流向时钟推进" not in md_unaligned
    assert "原始方向与 CVD 数值已降级隐藏" in md_unaligned

    md_stale = review.build_hold_review_input(
        **common_kw,
        flow_summary={
            "flow_ready": True,
            "flow_fast_4h_ready": True,
            "flow_qualification_status": "STALE",
            "flow_asof_ms": now_ms - 90 * M,
            "flow_window_basis": dict(aligned_basis, end_ms=now_ms - 90 * M),
            "flow_direction": "bearish",
            "flow_cvd_4h": -876.54,
        },
    )
    assert "-876.54" not in md_stale
    assert "4h CVD" not in md_stale
    assert "流向时钟推进" not in md_stale
    assert "原始方向与 CVD 数值已降级隐藏" in md_stale

    md_ok = review.build_hold_review_input(
        **common_kw,
        flow_summary={
            "flow_ready": True,
            "flow_fast_4h_ready": True,
            "flow_qualification_status": "OBSERVED_ALIGNED",
            "flow_asof_ms": now_ms - M,
            "flow_window_basis": aligned_basis,
            "flow_direction": "bearish",
            "flow_cvd_4h": -210.50,
            "flow_momentum_4h_pct": -0.45,
        },
    )
    assert "流向时钟推进 +29 分钟（偏买 -> 偏卖）" in md_ok
    assert "4h CVD +150.00 -> -210.50" in md_ok


