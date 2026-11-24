"""Full deterministic reproduction of ESF v0.1 real Episode experiments (A2 + B2 + B3).

Reads strictly from research/esf_v01/data/ on xx-eu sanitized bundle:
- 10 official Dossier Hand records (8 DEALT = 16 structures, 2 DATA_BLOCKED on 11-22 & 11-23 expiries)
- 755 GFRI v1.1 shadow cycles (2026-11-18T18:10:27Z -> 2026-11-24T07:47:43Z)
- 1-minute btc_usd path, 1-hour BTC-PERPETUAL bars, and official 08:00Z delivery prices
- 33 UNEXECUTED_ARCHIVE_CANDIDATE snapshots on 2026-11-21 & 2026-11-23 (strictly isolated)

Authority: RESEARCH_ONLY (live_veto_enabled=False, player_integration_enabled=False).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import json
import math
from pathlib import Path
import statistics
from typing import Any, Mapping, Sequence

from tools import astra_commercial_math_v17 as commercial_math

from .esf_core import (
    DECISION_AUTHORITY,
    DEFAULT_GFRI_MAX_AGE_MS,
    ESF_VERSION,
    LIVE_VETO_ENABLED,
    PARAM_CALIBRATION_STATUS,
    PLAYER_INTEGRATION_ENABLED,
    compute_hourly_rv_no_lookahead,
    evaluate_episode_esf,
    evaluate_side_esf,
    render_esf_card,
)

DATA_DIR = Path(__file__).resolve().parent / "data"
STUDY_FINAL_VERDICT = "ESF_PARTIALLY_FEASIBLE"


def _iso_sec(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rank_corr_spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Exact average-tie Spearman rank correlation in pure stdlib."""
    n = len(xs)
    if n != len(ys) or n < 3:
        return None

    def _ranks(vals: Sequence[float]) -> list[float]:
        indexed = sorted(enumerate(vals), key=lambda kv: kv[1])
        out = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and indexed[j + 1][1] == indexed[i][1]:
                j += 1
            avg_rank = 0.5 * (i + j) + 1.0
            for k in range(i, j + 1):
                out[indexed[k][0]] = avg_rank
            i = j + 1
        return out

    rx = _ranks(xs)
    ry = _ranks(ys)
    mx = sum(rx) / n
    my = sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx <= 0.0 or vy <= 0.0:
        return None
    return cov / math.sqrt(vx * vy)


def _auc_mann_whitney(scores_higher_more_fragile: Sequence[float], labels: Sequence[bool]) -> float | None:
    """Exact tie-aware ROC-AUC (probability a positive has higher fragility score than a negative)."""
    pos = [s for s, y in zip(scores_higher_more_fragile, labels) if y]
    neg = [s for s, y in zip(scores_higher_more_fragile, labels) if not y]
    if not pos or not neg:
        return None
    wins = 0.0
    for p in pos:
        for q in neg:
            if p > q:
                wins += 1.0
            elif p == q:
                wins += 0.5
    return wins / (len(pos) * len(neg))


def load_study_inputs(data_dir: Path = DATA_DIR) -> dict[str, Any]:
    """Load all frozen offline research inputs from research/esf_v01/data/."""
    dossiers = json.loads((data_dir / "dossiers_20261117_20261124.json").read_text(encoding="utf-8"))
    gfri_records = [
        json.loads(line)
        for line in (data_dir / "gfri_cycles_20261118_20261124.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    gfri_records.sort(key=lambda r: int(r["freeze_at_ms"]))
    with gzip.open(data_dir / "btc_market_paths_20261115_20261124.json.gz", "rt", encoding="utf-8") as fh:
        market_paths = json.load(fh)
    archive_candidates = json.loads(
        (data_dir / "unexecuted_archive_candidates_1121_1123.json").read_text(encoding="utf-8")
    )
    return {
        "dossiers": dossiers,
        "gfri_records": gfri_records,
        "market_paths": market_paths,
        "archive_candidates": archive_candidates,
    }


def _spot_at_or_before(pts_1m: Sequence[Sequence[Any]], ts_ms: int) -> float | None:
    best_p: float | None = None
    best_t = -1
    for row in pts_1m:
        t = int(row[0])
        if t <= ts_ms and t > best_t:
            best_t = t
            best_p = float(row[1])
    return best_p


def reproduce_official_dossiers(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate all 10 official Dossiers (8 DEALT = 16 structures, 2 DATA_BLOCKED)."""
    dossiers = inputs["dossiers"]
    gfri_records = inputs["gfri_records"]
    pts_1m = inputs["market_paths"]["btc_1m_points"]

    dealt_episodes: list[dict[str, Any]] = []
    blocked_dossiers: list[dict[str, Any]] = []
    structure_rows: list[dict[str, Any]] = []

    for hand_idx, d in enumerate(dossiers):
        if d.get("status") != "DEALT":
            blocked_dossiers.append(
                {
                    "hand_index": hand_idx,
                    "dossier": d["dossier"],
                    "hand_id": d["hand_id"],
                    "status": d["status"],
                    "expiry_date": d["expiry_date"],
                    "expiry_ms": int(d["expiry_ms"]),
                    "recorded_at_ms": int(d["notes"][0]["recorded_at_ms"]) if d.get("notes") else None,
                    "recorded_iso": _iso_sec(int(d["notes"][0]["recorded_at_ms"])) if d.get("notes") else None,
                    "reason": d["notes"][0]["reason"] if d.get("notes") else None,
                    "official_delivery_usd": float(d["official_delivery_usd"]),
                }
            )
            continue

        freeze_ms = int(d["freeze_ms"])
        expiry_ms = int(d["expiry_ms"])
        delivery_usd = float(d["official_delivery_usd"])
        ep_esf = evaluate_episode_esf(
            episode_label=d["dossier"],
            freeze_ms=freeze_ms,
            expiry_ms=expiry_ms,
            put_side=d["sides"]["put"],
            call_side=d["sides"]["call"],
            index_points=d.get("frozen_index_path_points") or pts_1m,
            gfri_records=gfri_records,
            gfri_max_age_ms=DEFAULT_GFRI_MAX_AGE_MS,
        )
        ep_esf["hand_index"] = hand_idx
        ep_esf["hand_id"] = d["hand_id"]
        ep_esf["freeze_iso"] = d["freeze_iso"]
        ep_esf["decision"] = d.get("decision")
        ep_esf["shadow_link"] = d.get("shadow_link")
        ep_esf["lock_block"] = d.get("lock_block")
        ep_esf["official_delivery_usd"] = delivery_usd
        ep_esf["rendered_card"] = render_esf_card(ep_esf)
        dealt_episodes.append(ep_esf)

        window_1m = [(int(r[0]), float(r[1])) for r in pts_1m if freeze_ms <= int(r[0]) <= expiry_ms]
        min_1m = min(p for _, p in window_1m)
        max_1m = max(p for _, p in window_1m)

        for side in ("put", "call"):
            sv = d["sides"][side]
            esf_s = ep_esf[side]
            short_k = float(sv["contract"]["short_strike_usd"])
            long_k = float(sv["contract"]["long_strike_usd"])
            spot = float(sv["economics"]["valuation_spot_usd"])
            net_credit_btc = sv["economics"].get("net_credit_after_fee_btc")
            visible_credit_btc = sv["economics"].get("visible_credit_btc")

            is_shadow_locked = bool(
                d.get("shadow_link")
                and d.get("decision")
                and d["decision"].get("side") == side
            )
            ep_life = d.get("episode_lifecycle") if is_shadow_locked else None

            intraday_short_breached = (min_1m <= short_k) if side == "put" else (max_1m >= short_k)
            intraday_long_breached = (min_1m <= long_k) if side == "put" else (max_1m >= long_k)
            settlement_short_breached = (delivery_usd < short_k) if side == "put" else (delivery_usd > short_k)
            payout_btc = commercial_math.inverse_spread_payout(side, short_k, long_k, delivery_usd)
            hold_to_expiry_net_btc = (
                (float(net_credit_btc) - payout_btc) if net_credit_btc is not None else None
            )

            worst_price = min_1m if side == "put" else max_1m
            full_horizon_mae_log_pct = (
                max(0.0, math.log(spot / worst_price) * 100.0)
                if side == "put"
                else max(0.0, math.log(worst_price / spot) * 100.0)
            )

            active_shadow_summary: dict[str, Any] | None = None
            if is_shadow_locked and ep_life is not None:
                t_lock = int(ep_life["entry_at_ms"])
                t_term = int(ep_life["shadow_termination_ms"]) if ep_life.get("shadow_termination_ms") else expiry_ms
                s_lock = float(ep_life["entry_index_usd"])
                act_pts = [(int(r[0]), float(r[1])) for r in pts_1m if t_lock <= int(r[0]) <= t_term]
                act_worst = min(p for _, p in act_pts) if side == "put" else max(p for _, p in act_pts)
                first_br = next(
                    ((t, p) for t, p in act_pts if (p <= short_k if side == "put" else p >= short_k)),
                    None,
                )
                mae_deal_log = (
                    max(0.0, math.log(spot / act_worst) * 100.0)
                    if side == "put"
                    else max(0.0, math.log(act_worst / spot) * 100.0)
                )
                mae_lock_log = (
                    max(0.0, math.log(s_lock / act_worst) * 100.0)
                    if side == "put"
                    else max(0.0, math.log(act_worst / s_lock) * 100.0)
                )
                mae_lock_simple = (
                    max(0.0, (s_lock - act_worst) / s_lock * 100.0)
                    if side == "put"
                    else max(0.0, (act_worst - s_lock) / s_lock * 100.0)
                )
                active_shadow_summary = {
                    "episode_id": ep_life["episode_id"],
                    "entry_at_ms": t_lock,
                    "entry_iso": ep_life["entry_iso"],
                    "entry_index_usd": s_lock,
                    "revoked": bool(ep_life["revoked"]),
                    "active_end_ms": t_term,
                    "active_end_iso": _iso_sec(t_term),
                    "first_short_breach_1m_ms": first_br[0] if first_br else None,
                    "first_short_breach_1m_iso": _iso_sec(first_br[0]) if first_br else None,
                    "first_short_breach_1m_usd": first_br[1] if first_br else None,
                    "minutes_to_breach_from_lock": ((first_br[0] - t_lock) / 60_000.0) if first_br else None,
                    "active_worst_spot_usd": act_worst,
                    "active_mae_log_vs_deal_pct": mae_deal_log,
                    "active_mae_log_vs_lock_pct": mae_lock_log,
                    "active_mae_simple_vs_lock_pct": mae_lock_simple,
                    "termination_action": (
                        ep_life["shadow_termination_action"]["action"]
                        if isinstance(ep_life.get("shadow_termination_action"), Mapping)
                        else "HELD_TO_EXPIRY"
                    ),
                }

            structure_rows.append(
                {
                    "sample_identity": "OFFICIAL_SHADOW_LOCKED" if is_shadow_locked else "OFFICIAL_COUNTERFACTUAL_SIDE",
                    "hand_index": hand_idx,
                    "dossier": d["dossier"],
                    "freeze_ms": freeze_ms,
                    "freeze_iso": d["freeze_iso"],
                    "expiry_ms": expiry_ms,
                    "side": side,
                    "short_instrument": sv["contract"]["short_instrument"],
                    "long_instrument": sv["contract"]["long_instrument"],
                    "short_strike_usd": short_k,
                    "long_strike_usd": long_k,
                    "spot_usd": spot,
                    "distance_pct": esf_s["distance_pct"],
                    "boundary_state": esf_s["boundary_state"],
                    "remaining_hours": esf_s["remaining_hours"],
                    "rv_24h_1h_pct": esf_s["multi_window_rv"]["rv_24h_1h_pct"],
                    "rv_7d_1h_pct": esf_s["multi_window_rv"]["rv_7d_1h_pct"],
                    "rv24_clock_adjusted_pct": esf_s["multi_window_rv"]["rv24_clock_adjusted_pct"],
                    "rv_4h_5m_pct": esf_s["multi_window_rv"]["rv_4h_5m_pct"],
                    "rv_24h_5m_pct": esf_s["multi_window_rv"]["rv_24h_5m_pct"],
                    "buffer_rv24_sigma": esf_s["buffer_sigma"],
                    "buffer_rv7d_sigma": esf_s["multi_window_rv"]["buffer_rv7d_1h_sigma"],
                    "buffer_rv24_clock_sigma": esf_s["multi_window_rv"]["buffer_rv24_clock_sigma"],
                    "buffer_rv4h_5m_sigma": esf_s["multi_window_rv"]["buffer_rv4h_5m_sigma"],
                    "buffer_rv24h_5m_sigma": esf_s["multi_window_rv"]["buffer_rv24h_5m_sigma"],
                    "short_abs_delta": esf_s["option_comparators"]["short_abs_delta"],
                    "short_greeks_abs_delta": abs(float(sv["short_greeks"]["delta"])),
                    "short_mark_iv_pct": esf_s["option_comparators"]["short_mark_iv_pct"],
                    "iv_minus_rv24_vol_pts": esf_s["option_comparators"]["iv_minus_rv24_vol_pts"],
                    "live_gfri_era": esf_s["feedback"]["gfri_confirmed"] is not None,
                    "gfri_confirmed": esf_s["feedback"]["gfri_confirmed"],
                    "feedback_state": esf_s["feedback"]["feedback_state"],
                    "spatial_relevance": esf_s["feedback"]["spatial_relevance"],
                    "deribit_quote_flicker_suspect": esf_s["feedback"]["deribit_quote_flicker_suspect"],
                    "esf_buffer_band": esf_s["scheme1_two_axis"]["buffer_band"],
                    "esf_candidate_code": esf_s["scheme1_two_axis"]["candidate_code"],
                    "esf_candidate_status_cn": esf_s["scheme1_two_axis"]["candidate_status_cn"],
                    "esf_final_rank": esf_s["scheme1_two_axis"]["final_rank"],
                    "esf_lex_key": esf_s["scheme1_two_axis"]["lexicographic_key"],
                    "fast_vol_alert": esf_s["scheme1_two_axis"]["fast_vol_alert"],
                    "scheme2_stress_buffer_sigma": esf_s["scheme2_stress_buffer"]["buffer_stress_sigma"],
                    "is_shadow_locked": is_shadow_locked,
                    "visible_credit_btc": visible_credit_btc,
                    "net_credit_after_fee_btc": net_credit_btc,
                    "official_delivery_usd": delivery_usd,
                    "intraday_min_usd": min_1m,
                    "intraday_max_usd": max_1m,
                    "full_horizon_mae_log_pct": full_horizon_mae_log_pct,
                    "intraday_short_breached": intraday_short_breached,
                    "intraday_long_breached": intraday_long_breached,
                    "settlement_short_breached": settlement_short_breached,
                    "counterfactual_payout_btc": payout_btc,
                    "counterfactual_hold_net_btc": hold_to_expiry_net_btc,
                    "active_shadow_summary": active_shadow_summary,
                }
            )

    # Compute exact ordinal ranks across all 16 official structures
    by_dist_desc = sorted(range(len(structure_rows)), key=lambda i: -structure_rows[i]["distance_pct"])
    for rank, idx in enumerate(by_dist_desc, 1):
        structure_rows[idx]["rank_widest_distance_of_16"] = rank

    by_delta_asc = sorted(range(len(structure_rows)), key=lambda i: structure_rows[i]["short_abs_delta"])
    for rank, idx in enumerate(by_delta_asc, 1):
        structure_rows[idx]["rank_lowest_abs_delta_of_16"] = rank

    # Distinct-contract |Delta| rank to clarify repeated 11-16 65500-C deals
    seen_contracts: list[str] = []
    for idx in by_delta_asc:
        inst = structure_rows[idx]["short_instrument"]
        if inst not in seen_contracts:
            seen_contracts.append(inst)
        structure_rows[idx]["rank_lowest_abs_delta_distinct_contract"] = seen_contracts.index(inst) + 1

    by_b24_asc = sorted(range(len(structure_rows)), key=lambda i: structure_rows[i]["buffer_rv24_sigma"])
    for rank, idx in enumerate(by_b24_asc, 1):
        structure_rows[idx]["rank_most_fragile_b_rv24_of_16"] = rank

    return {
        "dealt_episodes": dealt_episodes,
        "blocked_dossiers": blocked_dossiers,
        "structure_rows": structure_rows,
    }


def reproduce_lifecycle_1121(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """A2 Lifecycle 1: Deribit_BTC_20261121T080000Z (dealt 11-20 16:02Z, unexecuted LOCK_BLOCKED)."""
    d = next(x for x in inputs["dossiers"] if x["dossier"] == "Deribit_BTC_20261121T080000Z")
    gfri_records = inputs["gfri_records"]
    pts_1m = inputs["market_paths"]["btc_1m_points"]
    perp_1h = inputs["market_paths"]["perp_1h_bars"]

    freeze_ms = int(d["freeze_ms"])
    expiry_ms = int(d["expiry_ms"])
    delivery_usd = float(d["official_delivery_usd"])

    checkpoint_timestamps = [
        1795190522966,  # 2026-11-20T16:02:02Z Official deal freeze
        1795190909256,  # 2026-11-20T16:08:29Z Deribit single-venue quote flicker (5.69x, OKX/Deribit=5.49x)
        1795191546226,  # 2026-11-20T16:19:06Z Deribit single-venue quote flicker (8.16x, OKX/Deribit=6.82x)
        1795219200000,  # 2026-11-21T00:00:00Z Midnight UTC
        1795222837203,  # 2026-11-21T01:00:37Z Pre-surge hover near 59,000
        1795226667213,  # 2026-11-21T02:04:27Z 5.21x OKX-corroborated GFRI surge
        1795230000000,  # 2026-11-21T03:00:00Z Pre-crash acceleration
        1795233692206,  # 2026-11-21T04:01:32Z Post-57k protection breach
        1795241991238,  # 2026-11-21T06:19:51Z Near intraday trough (55,339.78 at 06:17Z)
        1795244539218,  # 2026-11-21T07:02:19Z Late 6.04x surge before 08:00Z settlement
    ]

    trajectory: list[dict[str, Any]] = []
    for ts in checkpoint_timestamps:
        spot = _spot_at_or_before(pts_1m, ts)
        assert spot is not None
        rv_snap = compute_hourly_rv_no_lookahead(perp_1h, asof_ms=ts) or {}
        put_esf = evaluate_side_esf(
            side="put",
            spot_usd=spot,
            short_strike_usd=59000.0,
            long_strike_usd=57000.0,
            asof_ms=ts,
            expiry_ms=expiry_ms,
            rv_24h_pct=rv_snap.get("rv_24h_pct"),
            rv_7d_pct=rv_snap.get("rv_7d_pct"),
            last_bar_end_ms=rv_snap.get("last_bar_end_ms"),
            index_points=pts_1m,
            gfri_records=gfri_records,
            gfri_asof_ms=ts,
        )
        call_esf = evaluate_side_esf(
            side="call",
            spot_usd=spot,
            short_strike_usd=60000.0,
            long_strike_usd=62000.0,
            asof_ms=ts,
            expiry_ms=expiry_ms,
            rv_24h_pct=rv_snap.get("rv_24h_pct"),
            rv_7d_pct=rv_snap.get("rv_7d_pct"),
            last_bar_end_ms=rv_snap.get("last_bar_end_ms"),
            index_points=pts_1m,
            gfri_records=gfri_records,
            gfri_asof_ms=ts,
        )
        trajectory.append(
            {
                "asof_ms": ts,
                "asof_iso": _iso_sec(ts),
                "spot_usd": spot,
                "remaining_hours": put_esf["remaining_hours"],
                "rv_24h_pct": rv_snap.get("rv_24h_pct"),
                "rv_4h_5m_pct": put_esf["multi_window_rv"]["rv_4h_5m_pct"],
                "put_distance_pct": put_esf["distance_pct"],
                "put_buffer_sigma": put_esf["buffer_sigma"],
                "put_gfri": put_esf["feedback"]["gfri_confirmed"],
                "put_feedback_state": put_esf["feedback"]["feedback_state"],
                "put_okx_to_deribit": put_esf["feedback"]["okx_to_deribit_ratio"],
                "put_status_cn": put_esf["scheme1_two_axis"]["candidate_status_cn"],
                "call_distance_pct": call_esf["distance_pct"],
                "call_buffer_sigma": call_esf["buffer_sigma"],
                "call_gfri": call_esf["feedback"]["gfri_confirmed"],
                "call_feedback_state": call_esf["feedback"]["feedback_state"],
                "call_status_cn": call_esf["scheme1_two_axis"]["candidate_status_cn"],
            }
        )

    sub_1m = [(int(r[0]), float(r[1])) for r in pts_1m if freeze_ms <= int(r[0]) <= expiry_ms]
    min_t, min_p = min(sub_1m, key=lambda kv: kv[1])
    first_59k = next((t, p) for t, p in sub_1m if p <= 59000.0)
    first_57k = next((t, p) for t, p in sub_1m if p <= 57000.0)

    put_payout = commercial_math.inverse_spread_payout("put", 59000.0, 57000.0, delivery_usd)
    call_payout = commercial_math.inverse_spread_payout("call", 60000.0, 62000.0, delivery_usd)

    return {
        "dossier": d["dossier"],
        "freeze_iso": d["freeze_iso"],
        "decision": d["decision"],
        "lock_block": d["lock_block"],
        "execution_status": "UNEXECUTED_LOCK_BLOCKED",
        "deal_put_spot_usd": float(d["sides"]["put"]["economics"]["valuation_spot_usd"]),
        "deal_call_spot_usd": float(d["sides"]["call"]["economics"]["valuation_spot_usd"]),
        "first_59000_breach_iso": _iso_sec(first_59k[0]),
        "first_59000_breach_usd": first_59k[1],
        "first_57000_protection_breach_iso": _iso_sec(first_57k[0]),
        "first_57000_protection_breach_usd": first_57k[1],
        "intraday_low_iso": _iso_sec(min_t),
        "intraday_low_usd": min_p,
        "official_delivery_usd": delivery_usd,
        "counterfactual_put_payout_btc": put_payout,
        "counterfactual_call_payout_btc": call_payout,
        "trajectory": trajectory,
    }


def reproduce_data_blocked_dossiers(inputs: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Audit the 2 DATA_BLOCKED official Dossiers (11-22 & 11-23 expiries) without fabricating episodes."""
    perp_1h = inputs["market_paths"]["perp_1h_bars"]
    arch = inputs["archive_candidates"]
    out: list[dict[str, Any]] = []
    for d in inputs["dossiers"]:
        if d.get("status") != "DATA_BLOCKED":
            continue
        rec_ms = int(d["notes"][0]["recorded_at_ms"])
        rv_at_block = compute_hourly_rv_no_lookahead(perp_1h, asof_ms=rec_ms) or {}
        rv_prev_hour = compute_hourly_rv_no_lookahead(perp_1h, asof_ms=rec_ms - 3_600_000) or {}
        same_day_puts = [
            a for a in arch if a["expiry_ms"] == d["expiry_ms"] and a["side"] == "put"
        ]
        out.append(
            {
                "dossier": d["dossier"],
                "hand_id": d["hand_id"],
                "status": "DATA_BLOCKED",
                "recorded_at_ms": rec_ms,
                "recorded_iso": _iso_sec(rec_ms),
                "reason": d["notes"][0]["reason"],
                "rv_24h_at_1600_close_pct": rv_at_block.get("rv_24h_pct"),
                "rv_24h_at_1500_close_pct": rv_prev_hour.get("rv_24h_pct"),
                "archive_put_rows_count": len(same_day_puts),
                "archive_put_strikes": sorted({float(a["short_strike_usd"]) for a in same_day_puts}),
                "archive_any_displayed": any(bool(a["displayed"]) for a in same_day_puts),
                "official_delivery_usd": float(d["official_delivery_usd"]),
            }
        )
    return out


def reproduce_lifecycle_1123(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """A2 Lifecycle 2: Deribit_BTC_20261124T080000Z (shadow-locked 16:12Z, revoked 16:50:59.948Z)."""
    d = next(x for x in inputs["dossiers"] if x["dossier"] == "Deribit_BTC_20261124T080000Z")
    gfri_records = inputs["gfri_records"]
    pts_1m = inputs["market_paths"]["btc_1m_points"]
    perp_1h = inputs["market_paths"]["perp_1h_bars"]

    freeze_ms = int(d["freeze_ms"])
    expiry_ms = int(d["expiry_ms"])
    delivery_usd = float(d["official_delivery_usd"])
    ep = d["episode_lifecycle"]
    lock_ms = int(ep["entry_at_ms"])
    term_ms = int(ep["shadow_termination_ms"])
    lock_spot = float(ep["entry_index_usd"])
    deal_spot = float(d["sides"]["put"]["economics"]["valuation_spot_usd"])
    t0_net_credit_btc = float(d["shadow_link"]["t0_economy"]["net_credit_after_fee_btc"])

    # Pre-deal whipsaw check (13:00Z -> 16:12Z)
    pre_pts = [(int(r[0]), float(r[1])) for r in pts_1m if 1795438800000 <= int(r[0]) <= lock_ms]
    pre_min_t, pre_min_p = min(pre_pts, key=lambda kv: kv[1])
    pre_max_t, pre_max_p = max(pre_pts, key=lambda kv: kv[1])

    # Active shadow hold strictly truncated at term_ms (16:50:59.948Z)
    active_pts = [(int(r[0]), float(r[1])) for r in pts_1m if lock_ms <= int(r[0]) <= term_ms]
    first_62k = next((t, p) for t, p in active_pts if p <= 62000.0)
    act_min_t, act_min_p = min(active_pts, key=lambda kv: kv[1])

    # ESF at deal freeze (16:02:13Z) vs shadow lock (16:12:20Z)
    rv_lock = compute_hourly_rv_no_lookahead(perp_1h, asof_ms=lock_ms) or {}
    esf_at_deal = evaluate_side_esf(
        side="put",
        spot_usd=deal_spot,
        short_strike_usd=62000.0,
        long_strike_usd=60000.0,
        asof_ms=int(d["sides"]["put"]["asof_ms"]),
        expiry_ms=expiry_ms,
        rv_24h_pct=d["sides"]["put"]["expiry_vol"]["realized_vol"]["rv_24h_pct"],
        rv_7d_pct=d["sides"]["put"]["expiry_vol"]["realized_vol"]["rv_7d_pct"],
        last_bar_end_ms=d["sides"]["put"]["expiry_vol"]["realized_vol"]["last_bar_end_ms"],
        index_points=d["frozen_index_path_points"],
        index_asof_ms=freeze_ms,
        gfri_records=gfri_records,
        gfri_asof_ms=freeze_ms,
        short_abs_delta=d["sides"]["put"]["selection"]["short_abs_delta"],
        short_mark_iv_pct=d["sides"]["put"]["short_mark_iv"],
    )
    esf_at_lock = evaluate_side_esf(
        side="put",
        spot_usd=lock_spot,
        short_strike_usd=62000.0,
        long_strike_usd=60000.0,
        asof_ms=lock_ms,
        expiry_ms=expiry_ms,
        rv_24h_pct=rv_lock.get("rv_24h_pct"),
        rv_7d_pct=rv_lock.get("rv_7d_pct"),
        last_bar_end_ms=rv_lock.get("last_bar_end_ms"),
        index_points=pts_1m,
        gfri_records=gfri_records,
        gfri_asof_ms=lock_ms,
    )

    # Separately labeled post-revoke counterfactual path (t > term_ms -> expiry_ms)
    post_pts = [(int(r[0]), float(r[1])) for r in pts_1m if term_ms < int(r[0]) <= expiry_ms]
    first_60k = next((t, p) for t, p in post_pts if p <= 60000.0)
    post_min_t, post_min_p = min(post_pts, key=lambda kv: kv[1])
    counterfactual_payout_btc = commercial_math.inverse_spread_payout("put", 62000.0, 60000.0, delivery_usd)
    counterfactual_net_btc = t0_net_credit_btc - counterfactual_payout_btc

    rev_quote = ep["reviews"][0]["revoke_quote"]["quote"]
    return {
        "dossier": d["dossier"],
        "episode_id": ep["episode_id"],
        "pre_deal_whipsaw": {
            "trough_iso": _iso_sec(pre_min_t),
            "trough_usd": pre_min_p,
            "peak_iso": _iso_sec(pre_max_t),
            "peak_usd": pre_max_p,
        },
        "deal_snapshot": {
            "freeze_iso": d["freeze_iso"],
            "spot_usd": deal_spot,
            "distance_pct": esf_at_deal["distance_pct"],
            "short_abs_delta": esf_at_deal["option_comparators"]["short_abs_delta"],
            "short_mark_iv_pct": esf_at_deal["option_comparators"]["short_mark_iv_pct"],
            "rv_24h_pct": esf_at_deal["multi_window_rv"]["rv_24h_1h_pct"],
            "rv_4h_5m_pct": esf_at_deal["multi_window_rv"]["rv_4h_5m_pct"],
            "iv_minus_rv24_vol_pts": esf_at_deal["option_comparators"]["iv_minus_rv24_vol_pts"],
            "buffer_rv24_sigma": esf_at_deal["buffer_sigma"],
            "buffer_rv4h_5m_sigma": esf_at_deal["multi_window_rv"]["buffer_rv4h_5m_sigma"],
            "gfri_confirmed": esf_at_deal["feedback"]["gfri_confirmed"],
            "candidate_status_cn": esf_at_deal["scheme1_two_axis"]["candidate_status_cn"],
        },
        "active_shadow_hold_truncated": {
            "scope": "ACTIVE_SHADOW_HOLD_TRUNCATED_AT_REVOKE",
            "locked_at_ms": lock_ms,
            "locked_iso": ep["entry_iso"],
            "lock_spot_usd": lock_spot,
            "t0_net_credit_btc": t0_net_credit_btc,
            "lock_gfri_confirmed": esf_at_lock["feedback"]["gfri_confirmed"],
            "lock_buffer_rv24_sigma": esf_at_lock["buffer_sigma"],
            "first_62000_breach_1m_ms": first_62k[0],
            "first_62000_breach_1m_iso": _iso_sec(first_62k[0]),
            "first_62000_breach_1m_usd": first_62k[1],
            "minutes_from_lock_to_breach": (first_62k[0] - lock_ms) / 60_000.0,
            "condition_triggered_check_ms": int(ep["first_condition_triggered_check_ms"]),
            "condition_triggered_check_iso": _iso_sec(int(ep["first_condition_triggered_check_ms"])),
            "ai_revoke_observed_ms": int(ep["ai_revoke_observed_ms"]),
            "ai_revoke_decided_ms": int(ep["ai_revoke_decided_ms"]),
            "ai_revoke_decided_iso": (
                datetime.fromtimestamp(ep["ai_revoke_decided_ms"] / 1000.0, tz=timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
                + "Z"
            ),
            "shadow_termination_ms": term_ms,
            "shadow_termination_iso": (
                datetime.fromtimestamp(term_ms / 1000.0, tz=timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
                + "Z"
            ),
            "shadow_termination_action": ep["shadow_termination_action"]["action"],
            "quote_gap_reasons": ep["shadow_termination_action"]["quote_gap_reasons"],
            "stale_revoke_short_buyback_total_btc": float(rev_quote["short_buyback_total_btc"]),
            "stale_revoke_quote_asof_ms": int(rev_quote["short_quote_asof_ms"]),
            "active_min_spot_iso": _iso_sec(act_min_t),
            "active_min_spot_usd": act_min_p,
            "active_mae_log_vs_deal_pct": math.log(deal_spot / act_min_p) * 100.0,
            "active_mae_log_vs_lock_pct": math.log(lock_spot / act_min_p) * 100.0,
            "active_mae_simple_vs_lock_pct": (lock_spot - act_min_p) / lock_spot * 100.0,
        },
        "post_revoke_counterfactual": {
            "scope": "POST_REVOKE_COUNTERFACTUAL_PATH_NOT_HELD",
            "window_start_iso": "2026-11-23T16:51:00Z",
            "window_end_iso": "2026-11-24T08:00:00Z",
            "first_60000_protection_breach_iso": _iso_sec(first_60k[0]),
            "first_60000_protection_breach_usd": first_60k[1],
            "post_revoke_min_iso": _iso_sec(post_min_t),
            "post_revoke_min_usd": post_min_p,
            "official_delivery_usd": delivery_usd,
            "counterfactual_payout_btc": counterfactual_payout_btc,
            "counterfactual_hold_to_expiry_net_btc": counterfactual_net_btc,
        },
    }


def reproduce_archive_candidates(inputs: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Evaluate all 33 UNEXECUTED_ARCHIVE_CANDIDATE snapshots (strictly isolated from official Dossiers)."""
    gfri_records = inputs["gfri_records"]
    pts_1m = inputs["market_paths"]["btc_1m_points"]
    perp_1h = inputs["market_paths"]["perp_1h_bars"]
    rows: list[dict[str, Any]] = []

    for idx, a in enumerate(inputs["archive_candidates"]):
        assert a["sample_identity"] == "UNEXECUTED_ARCHIVE_CANDIDATE"
        at_ms = int(a["at_ms"])
        exp_ms = int(a["expiry_ms"])
        side = str(a["side"])
        short_k = float(a["short_strike_usd"])
        long_k = float(a["long_strike_usd"])
        spot = float(a["index_usd"])
        deliv = float(a["official_delivery_usd"])

        rv_snap = compute_hourly_rv_no_lookahead(perp_1h, asof_ms=at_ms) or {}
        esf_s = evaluate_side_esf(
            side=side,
            spot_usd=spot,
            short_strike_usd=short_k,
            long_strike_usd=long_k,
            asof_ms=at_ms,
            expiry_ms=exp_ms,
            rv_24h_pct=float(a["rv_24h_pct"]),
            rv_7d_pct=float(a["rv_7d_pct"]),
            last_bar_end_ms=rv_snap.get("last_bar_end_ms"),
            index_points=pts_1m,
            gfri_records=gfri_records,
            gfri_asof_ms=at_ms,
            short_abs_delta=abs(float(a["short_delta"])),
            short_mark_iv_pct=float(a["short_mark_iv"]),
        )
        sub_1m = [float(r[1]) for r in pts_1m if at_ms <= int(r[0]) <= exp_ms]
        lo, hi = min(sub_1m), max(sub_1m)
        intraday_br = (lo <= short_k) if side == "put" else (hi >= short_k)
        settle_br = (deliv < short_k) if side == "put" else (deliv > short_k)
        payout = commercial_math.inverse_spread_payout(side, short_k, long_k, deliv)

        rows.append(
            {
                "archive_index": idx,
                "sample_identity": "UNEXECUTED_ARCHIVE_CANDIDATE",
                "at_ms": at_ms,
                "at_iso": a["at_iso"],
                "expiry_date": a["expiry_date"],
                "side": side,
                "contract_key": a["contract_key"],
                "displayed": bool(a["displayed"]),
                "spot_usd": spot,
                "short_strike_usd": short_k,
                "long_strike_usd": long_k,
                "distance_pct": esf_s["distance_pct"],
                "buffer_rv24_sigma": esf_s["buffer_sigma"],
                "buffer_rv4h_5m_sigma": esf_s["multi_window_rv"]["buffer_rv4h_5m_sigma"],
                "short_abs_delta": abs(float(a["short_delta"])),
                "short_mark_iv_pct": float(a["short_mark_iv"]),
                "rv_24h_pct": float(a["rv_24h_pct"]),
                "rv_4h_5m_pct": esf_s["multi_window_rv"]["rv_4h_5m_pct"],
                "gfri_confirmed": esf_s["feedback"]["gfri_confirmed"],
                "feedback_state": esf_s["feedback"]["feedback_state"],
                "spatial_relevance": esf_s["feedback"]["spatial_relevance"],
                "deribit_quote_flicker_suspect": esf_s["feedback"]["deribit_quote_flicker_suspect"],
                "esf_candidate_code": esf_s["scheme1_two_axis"]["candidate_code"],
                "esf_candidate_status_cn": esf_s["scheme1_two_axis"]["candidate_status_cn"],
                "esf_final_rank": esf_s["scheme1_two_axis"]["final_rank"],
                "esf_lex_key": esf_s["scheme1_two_axis"]["lexicographic_key"],
                "scheme2_stress_buffer_sigma": esf_s["scheme2_stress_buffer"]["buffer_stress_sigma"],
                "intraday_short_breached": intraday_br,
                "settlement_short_breached": settle_br,
                "counterfactual_payout_btc": payout,
            }
        )
    return rows


def compute_head_to_head_metrics(
    official_rows: Sequence[Mapping[str, Any]],
    archive_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare Baseline A (D_d%), Baseline B (B_d), Option |Delta|, and Candidate C (ESF)."""
    def _eval_subset(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        y_settle = [bool(r["settlement_short_breached"]) for r in rows]
        y_intra = [bool(r["intraday_short_breached"]) for r in rows]
        payouts = [float(r["counterfactual_payout_btc"]) for r in rows]

        # Higher fragility score = more fragile
        s_dist = [-float(r["distance_pct"]) for r in rows]
        s_b24 = [-float(r["buffer_rv24_sigma"]) for r in rows]
        s_b4h = [-float(r["buffer_rv4h_5m_sigma"]) for r in rows]
        s_delta = [float(r["short_abs_delta"]) for r in rows]
        s_esf_s2 = [-float(r["scheme2_stress_buffer_sigma"]) for r in rows]
        # Lexicographic rank projected to monotone scalar for exact tie-free/tie-aware ordering
        lex_sorted = sorted({r["esf_lex_key"] for r in rows if r.get("esf_lex_key") is not None})
        s_esf_s1 = [
            float(lex_sorted.index(r["esf_lex_key"])) if r.get("esf_lex_key") is not None else -float(r["buffer_rv24_sigma"])
            for r in rows
        ]

        breached_b24 = [float(r["buffer_rv24_sigma"]) for r in rows if r["settlement_short_breached"]]
        safe_b24 = [float(r["buffer_rv24_sigma"]) for r in rows if not r["settlement_short_breached"]]
        return {
            "count": len(rows),
            "settlement_breach_count": sum(y_settle),
            "intraday_breach_count": sum(y_intra),
            "median_b_rv24_settlement_breached": statistics.median(breached_b24) if breached_b24 else None,
            "median_b_rv24_settlement_unbreached": statistics.median(safe_b24) if safe_b24 else None,
            "auc_settlement_breach": {
                "baseline_a_distance": _auc_mann_whitney(s_dist, y_settle),
                "option_abs_delta": _auc_mann_whitney(s_delta, y_settle),
                "baseline_b_rv24": _auc_mann_whitney(s_b24, y_settle),
                "baseline_b_rv4h_5m": _auc_mann_whitney(s_b4h, y_settle),
                "candidate_c_esf_scheme1": _auc_mann_whitney(s_esf_s1, y_settle),
                "candidate_c_esf_scheme2": _auc_mann_whitney(s_esf_s2, y_settle),
            },
            "auc_intraday_breach": {
                "baseline_a_distance": _auc_mann_whitney(s_dist, y_intra),
                "option_abs_delta": _auc_mann_whitney(s_delta, y_intra),
                "baseline_b_rv24": _auc_mann_whitney(s_b24, y_intra),
                "baseline_b_rv4h_5m": _auc_mann_whitney(s_b4h, y_intra),
                "candidate_c_esf_scheme1": _auc_mann_whitney(s_esf_s1, y_intra),
                "candidate_c_esf_scheme2": _auc_mann_whitney(s_esf_s2, y_intra),
            },
            "spearman_vs_payout_btc": {
                "baseline_a_distance": _rank_corr_spearman(s_dist, payouts),
                "option_abs_delta": _rank_corr_spearman(s_delta, payouts),
                "baseline_b_rv24": _rank_corr_spearman(s_b24, payouts),
                "candidate_c_esf_scheme1": _rank_corr_spearman(s_esf_s1, payouts),
            },
        }

    live_gfri_rows = [r for r in official_rows if r["live_gfri_era"]]
    pre_gfri_rows = [r for r in official_rows if not r["live_gfri_era"]]
    shadow_locked_rows = [r for r in official_rows if r["is_shadow_locked"]]
    counterfactual_rows = [r for r in official_rows if not r["is_shadow_locked"]]

    return {
        "official_all_16": _eval_subset(official_rows),
        "official_shadow_locked_4": _eval_subset(shadow_locked_rows),
        "official_counterfactual_12": _eval_subset(counterfactual_rows),
        "official_live_gfri_6": _eval_subset(live_gfri_rows),
        "official_pre_gfri_10": _eval_subset(pre_gfri_rows),
        "official_shadow_locked_4_count": len(shadow_locked_rows),
        "official_counterfactual_12_count": len(counterfactual_rows),
        "unexecuted_archive_33": _eval_subset(archive_rows),
    }


def run_full_study(data_dir: Path = DATA_DIR) -> dict[str, Any]:
    """Run complete deterministic ESF v0.1 reproduction study."""
    inputs = load_study_inputs(data_dir)
    official = reproduce_official_dossiers(inputs)
    lc_1121 = reproduce_lifecycle_1121(inputs)
    blocked = reproduce_data_blocked_dossiers(inputs)
    lc_1123 = reproduce_lifecycle_1123(inputs)
    archive_rows = reproduce_archive_candidates(inputs)
    metrics = compute_head_to_head_metrics(official["structure_rows"], archive_rows)

    return {
        "schema": ESF_VERSION,
        "decision_authority": DECISION_AUTHORITY,
        "live_veto_enabled": LIVE_VETO_ENABLED,
        "player_integration_enabled": PLAYER_INTEGRATION_ENABLED,
        "param_calibration_status": PARAM_CALIBRATION_STATUS,
        "final_verdict": STUDY_FINAL_VERDICT,
        "counts": {
            "official_dossiers_total": len(inputs["dossiers"]),
            "official_dealt_hands": len(official["dealt_episodes"]),
            "official_data_blocked_hands": len(blocked),
            "official_structures_total": len(official["structure_rows"]),
            "official_shadow_locked_positions": sum(1 for r in official["structure_rows"] if r["is_shadow_locked"]),
            "official_counterfactual_structures": sum(1 for r in official["structure_rows"] if not r["is_shadow_locked"]),
            "official_live_gfri_structures": sum(1 for r in official["structure_rows"] if r["live_gfri_era"]),
            "official_pre_gfri_structures": sum(1 for r in official["structure_rows"] if not r["live_gfri_era"]),
            "gfri_cycles_total": len(inputs["gfri_records"]),
            "unexecuted_archive_candidates_total": len(archive_rows),
        },
        "official": official,
        "lifecycle_1121": lc_1121,
        "data_blocked_dossiers": blocked,
        "lifecycle_1123": lc_1123,
        "unexecuted_archive_candidates": archive_rows,
        "head_to_head_metrics": metrics,
    }


def format_study_report(study: Mapping[str, Any]) -> str:
    """Format concise human-readable verification report for CLI output."""
    c = study["counts"]
    lines = [
        f"=== ESF v0.1 Real Episode Reproduction ({study['decision_authority']}) ===",
        (
            f"Authority: {study['decision_authority']} | live_veto={study['live_veto_enabled']} "
            f"| player_integration={study['player_integration_enabled']} | Verdict={study['final_verdict']}"
        ),
        (
            f"Official Dossiers: {c['official_dossiers_total']} total "
            f"({c['official_dealt_hands']} DEALT = {c['official_structures_total']} structures "
            f"[{c['official_shadow_locked_positions']} shadow-locked + {c['official_counterfactual_structures']} counterfactual; "
            f"{c['official_live_gfri_structures']} live-GFRI + {c['official_pre_gfri_structures']} pre-GFRI], "
            f"{c['official_data_blocked_hands']} DATA_BLOCKED)"
        ),
        f"GFRI v1.1 Cycles: {c['gfri_cycles_total']} | Isolated Archive Candidates: {c['unexecuted_archive_candidates_total']}",
        "",
        "--- 16 Official Dossier Structures (Strictly Separating 4 Shadow-Locked vs 12 Counterfactual) ---",
        "Dossier                   Freeze(UTC)          Side Lock  K_s/K_l     D%      |Delta| B_RV24  B_RV4h  GFRI   ESF_Status             IntraBr SettleBr PayoutBTC",
    ]
    for r in study["official"]["structure_rows"]:
        g_txt = f"{r['gfri_confirmed']:.2f}x" if r["gfri_confirmed"] is not None else "N/A"
        lines.append(
            f"{r['dossier']:25s} {r['freeze_iso']:20s} {r['side'].upper():4s} "
            f"{('YES' if r['is_shadow_locked'] else 'no'):4s}  "
            f"{r['short_strike_usd']:.0f}/{r['long_strike_usd']:.0f}  "
            f"{r['distance_pct']:+6.2f}%  {r['short_abs_delta']:6.3f}  "
            f"{r['buffer_rv24_sigma']:+6.3f}s {r['buffer_rv4h_5m_sigma']:+6.3f}s "
            f"{g_txt:6s} {r['esf_candidate_status_cn']:18s} "
            f"{str(r['intraday_short_breached']):7s} {str(r['settlement_short_breached']):8s} "
            f"{r['counterfactual_payout_btc']:.6f}"
        )

    lc23 = study["lifecycle_1123"]
    act23 = lc23["active_shadow_hold_truncated"]
    post23 = lc23["post_revoke_counterfactual"]
    lines.extend(
        [
            "",
            "--- Key Episode 2026-11-23 -> 2026-11-24 (episode_95ef7494c06605a6b7e59e9c) ---",
            (
                f"Active Hold [16:12:20Z -> {act23['shadow_termination_iso']}]: "
                f"Breach 62k at {act23['first_62000_breach_1m_iso']} ({act23['minutes_from_lock_to_breach']:.1f}m), "
                f"Action={act23['shadow_termination_action']}, Active MAE(vs deal)={act23['active_mae_log_vs_deal_pct']:.2f}%"
            ),
            (
                f"Post-Revoke Counterfactual [16:51Z -> 08:00Z]: "
                f"Min={post23['post_revoke_min_usd']:.2f} USD (<60k at {post23['first_60000_protection_breach_iso']}), "
                f"Delivery={post23['official_delivery_usd']:.2f} USD, "
                f"Payout={post23['counterfactual_payout_btc']:.6f} BTC, "
                f"HoldNet={post23['counterfactual_hold_to_expiry_net_btc']:+.6f} BTC"
            ),
            "",
            "--- Sample Reader Card (2026-11-23T16:02:13Z Deal) ---",
            study["official"]["dealt_episodes"][-1]["rendered_card"],
        ]
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reproduce ESF v0.1 real Episode experiments.")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary to stdout instead of text report.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    study = run_full_study()
    if args.json:
        print(json.dumps(study, ensure_ascii=False, indent=2))
    else:
        print(format_study_report(study))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
