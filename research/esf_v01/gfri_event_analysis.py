"""Reproducible offline GFRI v1.1 runtime audit (A0) and event study (A1) module.

Reads only frozen local artifacts under ``research/esf_v01/data/``:
- ``gfri_cycles_20261118_20261124.jsonl`` (755 shadow cycles)
- ``gfri_runtime_audit_meta.json`` (systemd, release, ledger & SHA-256 capture audit)
- ``btc_market_paths_20261115_20261124.json.gz`` (1m BTC index path & delivery prices)
- ``dossiers_20261117_20261124.json`` (Episode freeze/expiry/lifecycle timestamps)

Key methodological invariants enforced:
1. Both ``side.status == "VALID"`` (696 cycles) and ``side.status == "DEGRADED"`` with
   finite ``gfri_confirmed`` (44 cycles, primarily ``price_basis_divergence_degraded``
   during fast BTC moves) are retained in numerical GFRI distributions and event windows
   (740 / 755 = 98.01%), with per-side quality status always carried alongside values.
2. Pre-event GFRI lookups (``-120m/-60m/-30m``) strictly enforce ``freeze_at_ms <= t0 - offset``
   so post-onset observations never leak backward.
3. Step log-change decomposition verifies the exact identity
   ``dln(GFRI_t) = dln(|HF_t|) - dln(L_eff_t)`` (where ``L_eff_t = |HF_t| / GFRI_t``
   matches ``liquidity_depth_usd`` to machine precision on all 3-sample cycles and
   absorbs the 2-sample harmonic-vs-arithmetic mean gap on 14 two-sample cycles).
"""

from __future__ import annotations

import bisect
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import json
import math
from pathlib import Path
import statistics
from typing import Any

DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data"


def parse_iso_to_ms(iso_str: str) -> int:
    """Convert an ISO-8601 UTC timestamp string into epoch milliseconds."""
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return int(round(dt.timestamp() * 1000))


def ms_to_iso(ts_ms: int) -> str:
    """Format epoch milliseconds as a compact UTC ISO-8601 timestamp."""
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def linear_quantile(values: list[float], q: float) -> float | None:
    """Deterministic linear-interpolated quantile (matching numpy/statistics method='inclusive')."""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    pos = (len(s) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(s[lo])
    return float(s[lo] + (s[hi] - s[lo]) * (pos - lo))


def is_numerically_valid_cycle(cycle: dict[str, Any]) -> bool:
    """True iff both PUT and CALL carry finite numerical gfri_confirmed values."""
    p = cycle.get("put", {}).get("gfri_confirmed")
    c = cycle.get("call", {}).get("gfri_confirmed")
    return (
        isinstance(p, (int, float))
        and not isinstance(p, bool)
        and math.isfinite(p)
        and isinstance(c, (int, float))
        and not isinstance(c, bool)
        and math.isfinite(c)
    )


class BtcPriceSeries:
    """Fast binary-search 1-minute BTC index lookup with window extrema queries."""

    def __init__(self, btc_1m_points: list[list[Any]]) -> None:
        ordered = sorted(btc_1m_points, key=lambda row: int(row[0]))
        self.ts_ms: list[int] = [int(row[0]) for row in ordered]
        self.prices: list[float] = [float(row[1]) for row in ordered]
        self.sources: list[str] = [str(row[2]) for row in ordered]

    def price_at(self, target_ms: int, max_tol_ms: int = 180_000) -> tuple[float | None, int | None]:
        """Return ``(nearest_price, matched_ts_ms)`` within ``max_tol_ms``."""
        idx = bisect.bisect_left(self.ts_ms, target_ms)
        best_px: float | None = None
        best_ts: int | None = None
        best_dt = max_tol_ms + 1
        for cand in (idx - 1, idx):
            if 0 <= cand < len(self.ts_ms):
                dt = abs(self.ts_ms[cand] - target_ms)
                if dt <= max_tol_ms and dt < best_dt:
                    best_dt = dt
                    best_px = self.prices[cand]
                    best_ts = self.ts_ms[cand]
        return best_px, best_ts

    def price_asof(self, target_ms: int, max_lag_ms: int = 180_000) -> tuple[float | None, int | None]:
        """Strictly non-lookahead BTC price lookup (``matched_ts_ms <= target_ms``)."""
        idx = bisect.bisect_right(self.ts_ms, target_ms) - 1
        if 0 <= idx < len(self.ts_ms):
            dt = target_ms - self.ts_ms[idx]
            if 0 <= dt <= max_lag_ms:
                return self.prices[idx], self.ts_ms[idx]
        return None, None

    def window_extrema(self, start_ms: int, end_ms: int) -> tuple[float | None, float | None]:
        """Return ``(min_price, max_price)`` over ``[start_ms, end_ms]``."""
        i0 = bisect.bisect_left(self.ts_ms, start_ms)
        i1 = bisect.bisect_right(self.ts_ms, end_ms)
        sub = self.prices[i0:i1]
        if not sub:
            px, _ = self.price_at(start_ms)
            return px, px
        return min(sub), max(sub)


def load_dataset(data_dir: Path | None = None) -> dict[str, Any]:
    """Load read-only GFRI cycles, runtime audit metadata, BTC market paths, and dossiers."""
    base = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR
    cycles_path = base / "gfri_cycles_20261118_20261124.jsonl"
    meta_path = base / "gfri_runtime_audit_meta.json"
    mkt_path = base / "btc_market_paths_20261115_20261124.json.gz"
    dossiers_path = base / "dossiers_20261117_20261124.json"

    cycles = [
        json.loads(line)
        for line in cycles_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    cycles.sort(key=lambda c: int(c["freeze_at_ms"]))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    with gzip.open(mkt_path, "rt", encoding="utf-8") as fh:
        market_paths = json.load(fh)
    dossiers = json.loads(dossiers_path.read_text(encoding="utf-8"))
    return {
        "cycles": cycles,
        "meta": meta,
        "market_paths": market_paths,
        "dossiers": dossiers,
    }


def _summarize_quantiles(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "count": 0,
            "min": None,
            "p10": None,
            "p25": None,
            "p50": None,
            "p75": None,
            "p90": None,
            "p95": None,
            "p99": None,
            "max": None,
            "mean": None,
        }
    return {
        "count": len(values),
        "min": float(min(values)),
        "p10": linear_quantile(values, 0.10),
        "p25": linear_quantile(values, 0.25),
        "p50": linear_quantile(values, 0.50),
        "p75": linear_quantile(values, 0.75),
        "p90": linear_quantile(values, 0.90),
        "p95": linear_quantile(values, 0.95),
        "p99": linear_quantile(values, 0.99),
        "max": float(max(values)),
        "mean": float(statistics.mean(values)),
    }


def compute_runtime_audit(cycles: list[dict[str, Any]], meta: dict[str, Any]) -> dict[str, Any]:
    """Compute A0 engineering & data-integrity audit metrics across all 755 cycles."""
    total_cycles = len(cycles)
    first_ms = int(cycles[0]["freeze_at_ms"])
    last_ms = int(cycles[-1]["freeze_at_ms"])
    elapsed_sec = (last_ms - first_ms) / 1000.0
    elapsed_hours = elapsed_sec / 3600.0

    # True timer cadence: OnUnitInactiveSec=600s + median service run ~36.07s (+ ~2s systemd slack) = ~638s
    inactive_sec = float(meta["systemd_timer"]["on_unit_inactive_sec"])
    median_exec_sec = float(meta["systemd_execution_stats"]["duration_sec_p50"])
    theoretical_cadence_sec = inactive_sec + median_exec_sec

    intervals_all = [
        (int(cycles[i]["freeze_at_ms"]) - int(cycles[i - 1]["freeze_at_ms"])) / 1000.0
        for i in range(1, total_cycles)
    ]
    # Cycle #3 -> #4 (178.0s) was a manual service restart during the commit upgrade f3e565e4 -> 855ff4ba
    upgrade_intervals: list[dict[str, Any]] = []
    intervals_regular: list[float] = []
    for i in range(1, total_cycles):
        dt_sec = (int(cycles[i]["freeze_at_ms"]) - int(cycles[i - 1]["freeze_at_ms"])) / 1000.0
        if cycles[i - 1]["git_commit"] != cycles[i]["git_commit"]:
            upgrade_intervals.append(
                {
                    "from_cycle_index": i,
                    "to_cycle_index": i + 1,
                    "from_iso": cycles[i - 1]["freeze_iso"],
                    "to_iso": cycles[i]["freeze_iso"],
                    "from_commit": cycles[i - 1]["git_commit"],
                    "to_commit": cycles[i]["git_commit"],
                    "interval_sec": dt_sec,
                }
            )
        else:
            intervals_regular.append(dt_sec)

    observed_median_cadence_sec = linear_quantile(intervals_regular, 0.50) or theoretical_cadence_sec
    expected_cycles_per_day = 86400.0 / observed_median_cadence_sec
    expected_cycles_over_span = 1.0 + (elapsed_sec / observed_median_cadence_sec)

    # Quality & archive breakdowns
    raw_status_counts = Counter(c["raw_archive_status_label"] for c in cycles)
    replay_class_counts = Counter(c["sample_replay_class"] for c in cycles)
    global_dq_counts = Counter(c["data_quality"] for c in cycles)
    primary_method_counts = Counter(c["primary_method"] for c in cycles)
    shadow_decision_counts = Counter(c["shadow_decision"] for c in cycles)
    git_commit_counts = Counter(c["git_commit"] for c in cycles)

    def _side_breakdown(side_key: str) -> dict[str, int | float]:
        valid_num = sum(
            1 for c in cycles if c[side_key]["status"] == "VALID" and c[side_key]["gfri_confirmed"] is not None
        )
        deg_num = sum(
            1 for c in cycles if c[side_key]["status"] == "DEGRADED" and c[side_key]["gfri_confirmed"] is not None
        )
        deg_null = sum(
            1 for c in cycles if c[side_key]["status"] == "DEGRADED" and c[side_key]["gfri_confirmed"] is None
        )
        unk_null = sum(
            1 for c in cycles if c[side_key]["status"] == "UNKNOWN" and c[side_key]["gfri_confirmed"] is None
        )
        num_total = valid_num + deg_num
        null_total = deg_null + unk_null
        return {
            "valid_numeric": valid_num,
            "degraded_numeric": deg_num,
            "degraded_null": deg_null,
            "unknown_null": unk_null,
            "numerical_total": num_total,
            "numerical_pct": round(100.0 * num_total / total_cycles, 4),
            "null_total": null_total,
        }

    put_breakdown = _side_breakdown("put")
    call_breakdown = _side_breakdown("call")

    numerical_cycles = [c for c in cycles if is_numerically_valid_cycle(c)]
    numerical_replayable = sum(1 for c in numerical_cycles if c["sample_replay_class"] == "REPLAYABLE")
    numerical_non_replayable = sum(1 for c in numerical_cycles if c["sample_replay_class"] == "NON_REPLAYABLE")

    # Classify the 23 PARTIAL cycles
    partial_cycles = [c for c in cycles if c["raw_archive_status_label"] == "PARTIAL"]
    partial_outage_unknown = [c for c in partial_cycles if not is_numerically_valid_cycle(c)]
    partial_single_book_numeric = [c for c in partial_cycles if is_numerically_valid_cycle(c)]

    # Detail all 15 null cycles
    null_cycles_detail = []
    for idx, c in enumerate(cycles, start=1):
        if not is_numerically_valid_cycle(c):
            null_cycles_detail.append(
                {
                    "cycle_number": idx,
                    "freeze_iso": c["freeze_iso"],
                    "freeze_at_ms": c["freeze_at_ms"],
                    "data_quality": c["data_quality"],
                    "put_status": c["put"]["status"],
                    "call_status": c["call"]["status"],
                    "raw_archive_status_label": c["raw_archive_status_label"],
                    "primary_method": c["primary_method"],
                    "failure_reasons": list(c["failure_reasons"]),
                }
            )

    # Secondary venue degradation reason check on numerical cycles (comma order varies upstream)
    sec_venue_prefix = "secondary_venues_unavailable:"
    numerical_with_sec_venue_tag = sum(
        1
        for c in numerical_cycles
        if any(str(r).startswith(sec_venue_prefix) for r in c.get("failure_reasons", []))
    )
    degraded_numeric_reasons = Counter(
        tuple(r for r in c["failure_reasons"] if not str(r).startswith(sec_venue_prefix))
        for c in numerical_cycles
        if c["put"]["status"] == "DEGRADED"
    )

    # Freshness & skew quantiles (ms)
    ts_keys = ("gex_age_ms", "ob_age_ms", "ob_skew_ms", "ob_exchange_to_recv_ms")
    freshness_all = {
        k: _summarize_quantiles([float(c["timestamps"][k]) for c in cycles if c["timestamps"].get(k) is not None])
        for k in ts_keys
    }
    freshness_numerical = {
        k: _summarize_quantiles(
            [float(c["timestamps"][k]) for c in numerical_cycles if c["timestamps"].get(k) is not None]
        )
        for k in ts_keys
    }

    # Comparability & GEXMonitor stability
    gex_valid_cycles = [c for c in cycles if c["primary_method"] == "HEDGE_FLOW_NATIVE"]
    comparability_signatures = Counter(
        json.dumps(c["comparability"], sort_keys=True) for c in gex_valid_cycles
    )
    gex_snaps_all = [c["upstream_gex"]["snapshot_id"] for c in cycles if c["upstream_gex"].get("snapshot_id")]
    inst_counts = [
        int(c["upstream_gex"]["instrument_count"])
        for c in cycles
        if c["upstream_gex"].get("instrument_count") is not None
    ]
    exp_counts = [
        int(c["upstream_gex"]["expiry_count"])
        for c in cycles
        if c["upstream_gex"].get("expiry_count") is not None
    ]
    consecutive_dup_snaps_all = 0
    consecutive_dup_snaps_numerical = 0
    for i in range(1, total_cycles):
        s0 = cycles[i - 1]["upstream_gex"].get("snapshot_id")
        s1 = cycles[i]["upstream_gex"].get("snapshot_id")
        if s0 and s1 and s0 == s1:
            consecutive_dup_snaps_all += 1
            if is_numerically_valid_cycle(cycles[i - 1]) and is_numerically_valid_cycle(cycles[i]):
                consecutive_dup_snaps_numerical += 1

    # Daily breakdown table (UTC natural days)
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in cycles:
        by_day[c["freeze_iso"][:10]].append(c)

    daily_rows: list[dict[str, Any]] = []
    for day in sorted(by_day.keys()):
        cs = by_day[day]
        day_start_ms = parse_iso_to_ms(f"{day}T00:00:00Z")
        day_end_ms = day_start_ms + 86_400_000
        active_start_ms = max(day_start_ms, first_ms)
        active_end_ms = min(day_end_ms, last_ms)
        active_hours = (active_end_ms - active_start_ms) / 3_600_000.0
        exp_day = (active_end_ms - active_start_ms) / 1000.0 / observed_median_cadence_sec
        if day == cycles[0]["freeze_iso"][:10]:
            exp_day += 0.5  # boundary alignment for initial start tick
        num_day = sum(1 for c in cs if is_numerically_valid_cycle(c))
        daily_rows.append(
            {
                "utc_day": day,
                "first_iso": cs[0]["freeze_iso"],
                "last_iso": cs[-1]["freeze_iso"],
                "active_hours": round(active_hours, 2),
                "expected_cycles": round(exp_day, 1),
                "observed_cycles": len(cs),
                "saved_replayable": sum(1 for c in cs if c["sample_replay_class"] == "REPLAYABLE"),
                "partial_non_replayable": sum(1 for c in cs if c["sample_replay_class"] == "NON_REPLAYABLE"),
                "failed_archives": sum(1 for c in cs if c["raw_archive_status_label"] == "FAILED"),
                "numerical_gfri": num_day,
                "put_valid": sum(1 for c in cs if c["put"]["status"] == "VALID"),
                "put_degraded_numeric": sum(
                    1 for c in cs if c["put"]["status"] == "DEGRADED" and c["put"]["gfri_confirmed"] is not None
                ),
                "put_null": sum(1 for c in cs if c["put"]["gfri_confirmed"] is None),
                "hf_native": sum(1 for c in cs if c["primary_method"] == "HEDGE_FLOW_NATIVE"),
                "net_gex_linear": sum(1 for c in cs if c["primary_method"] == "NET_GEX_LINEAR"),
                "okx_valid_or_degraded": sum(1 for c in cs if c["okx_ref"]["status"] in ("VALID", "DEGRADED")),
            }
        )

    return {
        "overall": {
            "total_cycles": total_cycles,
            "first_freeze_iso": cycles[0]["freeze_iso"],
            "last_freeze_iso": cycles[-1]["freeze_iso"],
            "elapsed_hours": round(elapsed_hours, 3),
            "theoretical_cadence_sec": round(theoretical_cadence_sec, 2),
            "observed_median_cadence_sec": round(observed_median_cadence_sec, 2),
            "observed_median_cadence_min": round(observed_median_cadence_sec / 60.0, 3),
            "expected_cycles_per_full_day": round(expected_cycles_per_day, 2),
            "naive_10m_cycles_per_day": 144,
            "expected_cycles_over_span": round(expected_cycles_over_span, 2),
            "inter_cycle_intervals_all_sec": _summarize_quantiles(intervals_all),
            "inter_cycle_intervals_regular_sec": _summarize_quantiles(intervals_regular),
            "upgrade_restart_intervals": upgrade_intervals,
            "git_commit_counts": dict(git_commit_counts),
            "ledger_consistency": meta["ledger_consistency"],
            "raw_captures_hash_audit": meta["raw_captures_hash_audit"],
            "systemd_timer": meta["systemd_timer"],
            "systemd_execution_stats": meta["systemd_execution_stats"],
            "raw_archive_status_counts": {
                "SAVED": raw_status_counts.get("SAVED", 0),
                "PARTIAL": raw_status_counts.get("PARTIAL", 0),
                "FAILED": raw_status_counts.get("FAILED", 0),
            },
            "replay_class_counts": {
                "REPLAYABLE": replay_class_counts.get("REPLAYABLE", 0),
                "NON_REPLAYABLE": replay_class_counts.get("NON_REPLAYABLE", 0),
            },
            "partial_breakdown": {
                "total_partial": len(partial_cycles),
                "api_outage_fail_closed_unknown": len(partial_outage_unknown),
                "api_outage_window": (
                    f"{partial_outage_unknown[0]['freeze_iso']}..{partial_outage_unknown[-1]['freeze_iso']}"
                    if partial_outage_unknown
                    else None
                ),
                "single_book_hiccup_2of3_numeric": len(partial_single_book_numeric),
            },
            "global_data_quality_counts": {
                "VALID": global_dq_counts.get("VALID", 0),
                "DEGRADED": global_dq_counts.get("DEGRADED", 0),
                "UNKNOWN": global_dq_counts.get("UNKNOWN", 0),
            },
            "numerical_cycles_total": len(numerical_cycles),
            "numerical_cycles_pct": round(100.0 * len(numerical_cycles) / total_cycles, 4),
            "numerical_replayable_count": numerical_replayable,
            "numerical_non_replayable_count": numerical_non_replayable,
            "numerical_with_secondary_venue_unavailable": numerical_with_sec_venue_tag,
            "degraded_numeric_extra_reasons": {
                ",".join(k) if k else "none": v for k, v in degraded_numeric_reasons.items()
            },
            "put_quality_breakdown": put_breakdown,
            "call_quality_breakdown": call_breakdown,
            "null_cycles_detail": null_cycles_detail,
            "primary_method_counts": {
                "HEDGE_FLOW_NATIVE": primary_method_counts.get("HEDGE_FLOW_NATIVE", 0),
                "NET_GEX_LINEAR": primary_method_counts.get("NET_GEX_LINEAR", 0),
                "UNKNOWN": primary_method_counts.get("UNKNOWN", 0),
            },
            "shadow_decision_counts": dict(shadow_decision_counts),
            "freshness_quantiles_all_ms": freshness_all,
            "freshness_quantiles_numerical_ms": freshness_numerical,
            "comparability_gex_valid_unique_count": len(comparability_signatures),
            "comparability_gex_valid_counts": dict(comparability_signatures),
            "upstream_gex_stability": {
                "snapshots_present": len(gex_snaps_all),
                "distinct_snapshot_ids": len(set(gex_snaps_all)),
                "consecutive_duplicate_snapshots_all": consecutive_dup_snaps_all,
                "consecutive_duplicate_snapshots_numerical": consecutive_dup_snaps_numerical,
                "instrument_count_min": min(inst_counts),
                "instrument_count_p50": linear_quantile([float(x) for x in inst_counts], 0.50),
                "instrument_count_max": max(inst_counts),
                "expiry_count_min": min(exp_counts),
                "expiry_count_max": max(exp_counts),
                "expiry_count_distribution": {str(k): v for k, v in sorted(Counter(exp_counts).items())},
                "methodology_version": "deribit_coin_only_v1",
                "universe_policy": "coin_margined_only",
                "hf_model_version": "2.0",
            },
        },
        "daily": daily_rows,
    }


def build_episode_windows(dossiers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Construct Episode-aligned time slices from frozen dossier timestamps."""
    by_name = {d["dossier"]: d for d in dossiers}
    d21 = by_name["Deribit_BTC_20261121T080000Z"]
    d24 = by_name["Deribit_BTC_20261124T080000Z"]

    freeze_21_ms = int(d21["freeze_ms"])
    expiry_21_ms = int(d21["expiry_ms"])
    freeze_24_ms = int(d24["freeze_ms"])
    expiry_24_ms = int(d24["expiry_ms"])
    revoke_24_ms = int(d24["episode_lifecycle"]["shadow_termination_ms"])

    return [
        {
            "slice_id": "EP_21NOV_FULL",
            "label": "Episode 21NOV26 Full (2026-11-20T16:02:02Z..2026-11-21T08:00:00Z)",
            "start_ms": freeze_21_ms,
            "end_ms": expiry_21_ms,
            "start_iso": ms_to_iso(freeze_21_ms),
            "end_iso": ms_to_iso(expiry_21_ms),
        },
        {
            "slice_id": "EP_1121_PRE_EXPIRY",
            "label": "2026-11-21 Pre-Expiry 21NOV26 (00:00:00Z..08:00:00Z)",
            "start_ms": parse_iso_to_ms("2026-11-21T00:00:00Z"),
            "end_ms": expiry_21_ms,
            "start_iso": "2026-11-21T00:00:00Z",
            "end_iso": ms_to_iso(expiry_21_ms),
        },
        {
            "slice_id": "EP_1121_POST_EXPIRY",
            "label": "2026-11-21 Post-Expiry / 22NOV26 DATA_BLOCKED (08:00:00Z..24:00:00Z)",
            "start_ms": expiry_21_ms,
            "end_ms": parse_iso_to_ms("2026-11-22T00:00:00Z"),
            "start_iso": ms_to_iso(expiry_21_ms),
            "end_iso": "2026-11-22T00:00:00Z",
        },
        {
            "slice_id": "EP_1123_PRE_EXPIRY",
            "label": "2026-11-23 Pre-Expiry 23NOV26 DATA_BLOCKED (00:00:00Z..08:00:00Z)",
            "start_ms": parse_iso_to_ms("2026-11-23T00:00:00Z"),
            "end_ms": parse_iso_to_ms("2026-11-23T08:00:00Z"),
            "start_iso": "2026-11-23T00:00:00Z",
            "end_iso": "2026-11-23T08:00:00Z",
        },
        {
            "slice_id": "EP_1123_PRE_FREEZE_RALLY",
            "label": "2026-11-23 Pre-Freeze Rally & Reversal (08:00:00Z..16:02:13Z)",
            "start_ms": parse_iso_to_ms("2026-11-23T08:00:00Z"),
            "end_ms": freeze_24_ms,
            "start_iso": "2026-11-23T08:00:00Z",
            "end_iso": ms_to_iso(freeze_24_ms),
        },
        {
            "slice_id": "EP_24NOV_ACTIVE_HOLD",
            "label": "Episode 24NOV26 Active Hold to 62000-P Breach/Revoke (16:02:13Z..16:50:59Z)",
            "start_ms": freeze_24_ms,
            "end_ms": revoke_24_ms,
            "start_iso": ms_to_iso(freeze_24_ms),
            "end_iso": ms_to_iso(revoke_24_ms),
        },
        {
            "slice_id": "EP_1123_POST_FREEZE",
            "label": "2026-11-23 Post-Freeze 24NOV26 Window (16:02:13Z..24:00:00Z)",
            "start_ms": freeze_24_ms,
            "end_ms": parse_iso_to_ms("2026-11-24T00:00:00Z"),
            "start_iso": ms_to_iso(freeze_24_ms),
            "end_iso": "2026-11-24T00:00:00Z",
        },
        {
            "slice_id": "EP_24NOV_FULL_OBSERVED",
            "label": "Episode 24NOV26 Full Observed (2026-11-23T16:02:13Z..2026-11-24T08:00:00Z)",
            "start_ms": freeze_24_ms,
            "end_ms": expiry_24_ms,
            "start_iso": ms_to_iso(freeze_24_ms),
            "end_iso": ms_to_iso(expiry_24_ms),
        },
    ]


def _step_log_decomposition(cs: list[dict[str, Any]], side: str) -> dict[str, Any]:
    """Compute exact 1-step log-variance decomposition dln(GFRI) = dln(|HF|) - dln(L_eff)."""
    dln_gfri: list[float] = []
    dln_hf: list[float] = []
    dln_leff: list[float] = []
    max_identity_err = 0.0
    max_3sample_raw_depth_err = 0.0

    for i in range(1, len(cs)):
        c0, c1 = cs[i - 1], cs[i]
        if not (is_numerically_valid_cycle(c0) and is_numerically_valid_cycle(c1)):
            continue
        if int(c1["freeze_at_ms"]) - int(c0["freeze_at_ms"]) > 900_000:
            continue
        g0 = float(c0[side]["gfri_confirmed"])
        g1 = float(c1[side]["gfri_confirmed"])
        h0 = abs(float(c0[side]["raw_hedge_flow_usd"]))
        h1 = abs(float(c1[side]["raw_hedge_flow_usd"]))
        l0_raw = float(c0[side]["liquidity_depth_usd"])
        l1_raw = float(c1[side]["liquidity_depth_usd"])
        if g0 <= 0 or g1 <= 0 or h0 <= 0 or h1 <= 0 or l0_raw <= 0 or l1_raw <= 0:
            continue
        leff0 = h0 / g0
        leff1 = h1 / g1
        dg = math.log(g1 / g0)
        dh = math.log(h1 / h0)
        dl = math.log(leff1 / leff0)
        err = abs(dg - (dh - dl))
        max_identity_err = max(max_identity_err, err)
        if c0[side]["valid_sample_count"] == 3 and c1[side]["valid_sample_count"] == 3:
            raw_err = abs(dg - (dh - math.log(l1_raw / l0_raw)))
            max_3sample_raw_depth_err = max(max_3sample_raw_depth_err, raw_err)
        dln_gfri.append(dg)
        dln_hf.append(dh)
        dln_leff.append(dl)

    n_steps = len(dln_gfri)
    if n_steps < 2:
        return {
            "step_count": n_steps,
            "max_identity_abs_error": max_identity_err,
            "max_3sample_raw_depth_abs_error": max_3sample_raw_depth_err,
            "depth_cov_share": None,
            "hf_cov_share": None,
            "depth_unsplit_var_share": None,
        }

    mg = statistics.mean(dln_gfri)
    mh = statistics.mean(dln_hf)
    ml = statistics.mean(dln_leff)
    ss_g = sum((x - mg) ** 2 for x in dln_gfri)
    ss_h = sum((x - mh) ** 2 for x in dln_hf)
    ss_l = sum((x - ml) ** 2 for x in dln_leff)
    cov_hg = sum((h - mh) * (g - mg) for h, g in zip(dln_hf, dln_gfri))
    cov_neg_lg = sum((-(l - ml)) * (g - mg) for l, g in zip(dln_leff, dln_gfri))

    return {
        "step_count": n_steps,
        "max_identity_abs_error": max_identity_err,
        "max_3sample_raw_depth_abs_error": max_3sample_raw_depth_err,
        "depth_cov_share": (cov_neg_lg / ss_g) if ss_g > 0 else None,
        "hf_cov_share": (cov_hg / ss_g) if ss_g > 0 else None,
        "depth_unsplit_var_share": (ss_l / (ss_h + ss_l)) if (ss_h + ss_l) > 0 else None,
    }


def compute_slice_metrics(slice_id: str, label: str, cs: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute A1 distributional, jump, asymmetry, near_ref, log-variance, and OKX metrics."""
    num_cs = [c for c in cs if is_numerically_valid_cycle(c)]
    put_vals = [float(c["put"]["gfri_confirmed"]) for c in num_cs]
    call_vals = [float(c["call"]["gfri_confirmed"]) for c in num_cs]

    def _dist(vals: list[float]) -> dict[str, float | None]:
        if not vals:
            return {
                "min": None,
                "p25": None,
                "p50": None,
                "p75": None,
                "p90_descriptive": None,
                "max": None,
                "range": None,
            }
        return {
            "min": float(min(vals)),
            "p25": linear_quantile(vals, 0.25),
            "p50": linear_quantile(vals, 0.50),
            "p75": linear_quantile(vals, 0.75),
            "p90_descriptive": linear_quantile(vals, 0.90),
            "max": float(max(vals)),
            "range": float(max(vals) - min(vals)),
        }

    # Contiguous 1-cycle and 3-cycle jumps
    j1_put: list[float] = []
    j3_put: list[float] = []
    j1_call: list[float] = []
    for i in range(len(cs)):
        if not is_numerically_valid_cycle(cs[i]):
            continue
        if (
            i >= 1
            and is_numerically_valid_cycle(cs[i - 1])
            and int(cs[i]["freeze_at_ms"]) - int(cs[i - 1]["freeze_at_ms"]) <= 900_000
        ):
            j1_put.append(float(cs[i]["put"]["gfri_confirmed"]) - float(cs[i - 1]["put"]["gfri_confirmed"]))
            j1_call.append(float(cs[i]["call"]["gfri_confirmed"]) - float(cs[i - 1]["call"]["gfri_confirmed"]))
        if (
            i >= 3
            and is_numerically_valid_cycle(cs[i - 3])
            and int(cs[i]["freeze_at_ms"]) - int(cs[i - 3]["freeze_at_ms"]) <= 2_700_000
        ):
            j3_put.append(float(cs[i]["put"]["gfri_confirmed"]) - float(cs[i - 3]["put"]["gfri_confirmed"]))

    def _max_streak(threshold: float) -> int:
        best = cur = 0
        for c in cs:
            if is_numerically_valid_cycle(c) and float(c["put"]["gfri_confirmed"]) >= threshold:
                cur += 1
                best = max(best, cur)
            else:
                cur = 0
        return best

    hf_put_m = [abs(float(c["put"]["raw_hedge_flow_usd"])) / 1e6 for c in num_cs]
    der_put_m = [float(c["put"]["liquidity_depth_usd"]) / 1e6 for c in num_cs]
    okx_put_m = [
        float(c["okx_ref"]["down_usd"]) / 1e6
        for c in num_cs
        if c["okx_ref"].get("down_usd") is not None and float(c["okx_ref"]["down_usd"]) > 0
    ]
    okx_to_der_ratios = [
        float(c["okx_ref"]["down_usd"]) / float(c["put"]["liquidity_depth_usd"])
        for c in num_cs
        if c["okx_ref"].get("down_usd") is not None and float(c["okx_ref"]["down_usd"]) > 0
    ]

    asym_diff = [p - cl for p, cl in zip(put_vals, call_vals)]
    asym_ratio = [p / cl for p, cl in zip(put_vals, call_vals) if cl > 0]
    ln_corr: float | None = None
    if len(num_cs) >= 3:
        lp = [math.log(p) for p in put_vals]
        lc = [math.log(cl) for cl in call_vals]
        mlp, mlc = statistics.mean(lp), statistics.mean(lc)
        vp = sum((x - mlp) ** 2 for x in lp)
        vc = sum((x - mlc) ** 2 for x in lc)
        if vp > 0 and vc > 0:
            ln_corr = sum((x - mlp) * (y - mlc) for x, y in zip(lp, lc)) / math.sqrt(vp * vc)

    da_put = [
        float(c["near_ref"]["delta_abs_put"])
        for c in num_cs
        if c["near_ref"].get("delta_abs_put") is not None
    ]
    dr_put = [
        float(c["near_ref"]["delta_ratio_put"])
        for c in num_cs
        if c["near_ref"].get("delta_ratio_put") is not None
    ]
    driver_put_counts = Counter(c["near_ref"].get("driver_put") for c in num_cs)

    # OKX directional co-movement
    okx_same_all: list[bool] = []
    okx_same_drop15: list[bool] = []
    okx_same_drop25: list[bool] = []
    for i in range(1, len(cs)):
        c0, c1 = cs[i - 1], cs[i]
        if not (is_numerically_valid_cycle(c0) and is_numerically_valid_cycle(c1)):
            continue
        if int(c1["freeze_at_ms"]) - int(c0["freeze_at_ms"]) > 900_000:
            continue
        l0 = float(c0["put"]["liquidity_depth_usd"])
        l1 = float(c1["put"]["liquidity_depth_usd"])
        o0 = c0["okx_ref"].get("down_usd")
        o1 = c1["okx_ref"].get("down_usd")
        if o0 is None or o1 is None or float(o0) <= 0 or float(o1) <= 0 or l0 <= 0 or l1 <= 0:
            continue
        dl_pct = l1 / l0 - 1.0
        do_pct = float(o1) / float(o0) - 1.0
        if dl_pct == 0.0 or do_pct == 0.0:
            continue
        same = (dl_pct > 0 and do_pct > 0) or (dl_pct < 0 and do_pct < 0)
        okx_same_all.append(same)
        if dl_pct <= -0.15:
            okx_same_drop15.append(same)
        if dl_pct <= -0.25:
            okx_same_drop25.append(same)

    shadow_pause_count = sum(
        1 for c in num_cs if str(c.get("shadow_decision", "")).startswith("SHADOW_PAUSE")
    )

    return {
        "slice_id": slice_id,
        "label": label,
        "total_cycles": len(cs),
        "numerical_cycles": len(num_cs),
        "put_valid_count": sum(1 for c in num_cs if c["put"]["status"] == "VALID"),
        "put_degraded_numeric_count": sum(1 for c in num_cs if c["put"]["status"] == "DEGRADED"),
        "null_cycles": len(cs) - len(num_cs),
        "put_dist": _dist(put_vals),
        "call_dist": _dist(call_vals),
        "hf_put_musd_p50": linear_quantile(hf_put_m, 0.50),
        "deribit_depth_put_musd_p50": linear_quantile(der_put_m, 0.50),
        "okx_depth_put_musd_p50": linear_quantile(okx_put_m, 0.50),
        "okx_to_deribit_depth_ratio_p50": linear_quantile(okx_to_der_ratios, 0.50),
        "jumps_put": {
            "max_1cycle_up": max(j1_put) if j1_put else None,
            "max_1cycle_down": min(j1_put) if j1_put else None,
            "max_3cycle_up": max(j3_put) if j3_put else None,
            "max_3cycle_down": min(j3_put) if j3_put else None,
            "max_1cycle_call_up": max(j1_call) if j1_call else None,
        },
        "streaks_and_occupancy": {
            "put_ge_2_5x_count": sum(1 for v in put_vals if v >= 2.50),
            "put_ge_2_5x_pct": round(100.0 * sum(1 for v in put_vals if v >= 2.50) / len(put_vals), 2)
            if put_vals
            else 0.0,
            "put_ge_2_5x_max_streak": _max_streak(2.50),
            "put_ge_3_5x_count": sum(1 for v in put_vals if v >= 3.50),
            "put_ge_3_5x_max_streak": _max_streak(3.50),
            "shadow_pause_any_count": shadow_pause_count,
            "shadow_pause_any_pct": round(100.0 * shadow_pause_count / len(num_cs), 2) if num_cs else 0.0,
        },
        "put_call_asymmetry": {
            "diff_p50": linear_quantile(asym_diff, 0.50),
            "diff_p90": linear_quantile(asym_diff, 0.90),
            "ratio_p50": linear_quantile(asym_ratio, 0.50),
            "ln_pearson_r": ln_corr,
        },
        "near_ref_put": {
            "delta_abs_p50": linear_quantile(da_put, 0.50),
            "delta_abs_p90": linear_quantile(da_put, 0.90),
            "delta_abs_max": max(da_put) if da_put else None,
            "delta_ratio_p50": linear_quantile(dr_put, 0.50),
            "delta_ratio_p90": linear_quantile(dr_put, 0.90),
            "delta_ratio_max": max(dr_put) if dr_put else None,
            "driver_counts": dict(driver_put_counts),
        },
        "log_variance_put": _step_log_decomposition(cs, "put"),
        "log_variance_call": _step_log_decomposition(cs, "call"),
        "okx_comovement": {
            "all_steps_count": len(okx_same_all),
            "all_steps_same_sign": sum(okx_same_all),
            "all_steps_ratio": (sum(okx_same_all) / len(okx_same_all)) if okx_same_all else None,
            "deribit_drop_15pct_count": len(okx_same_drop15),
            "deribit_drop_15pct_same_sign": sum(okx_same_drop15),
            "deribit_drop_15pct_ratio": (
                sum(okx_same_drop15) / len(okx_same_drop15) if okx_same_drop15 else None
            ),
            "deribit_drop_25pct_count": len(okx_same_drop25),
            "deribit_drop_25pct_same_sign": sum(okx_same_drop25),
            "deribit_drop_25pct_ratio": (
                sum(okx_same_drop25) / len(okx_same_drop25) if okx_same_drop25 else None
            ),
        },
    }


def compute_all_distributions(
    cycles: list[dict[str, Any]], dossiers: list[dict[str, Any]]
) -> dict[str, Any]:
    """Compute overall, natural UTC daily (11-18..11-24), and Episode-window distributions."""
    overall = compute_slice_metrics("ALL_20261118_20261124", "All Observed Cycles (2026-11-18..24)", cycles)

    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in cycles:
        by_day[c["freeze_iso"][:10]].append(c)
    daily = [compute_slice_metrics(f"DAY_{d}", f"UTC Day {d}", by_day[d]) for d in sorted(by_day.keys())]

    episode_defs = build_episode_windows(dossiers)
    episodes = []
    for ed in episode_defs:
        sub = [c for c in cycles if ed["start_ms"] <= int(c["freeze_at_ms"]) <= ed["end_ms"]]
        m = compute_slice_metrics(ed["slice_id"], ed["label"], sub)
        m["start_iso"] = ed["start_iso"]
        m["end_iso"] = ed["end_iso"]
        episodes.append(m)

    return {
        "overall": overall,
        "daily": daily,
        "episodes": episodes,
    }


def _find_prior_cycle_asof(
    cycles: list[dict[str, Any]], freeze_ts_list: list[int], target_ms: int, max_lag_ms: int = 1_200_000
) -> dict[str, Any] | None:
    """Strictly non-lookahead lookup of latest numerical cycle with ``freeze_at_ms <= target_ms``."""
    idx = bisect.bisect_right(freeze_ts_list, target_ms) - 1
    while idx >= 0:
        cand = cycles[idx]
        lag = target_ms - int(cand["freeze_at_ms"])
        if lag > max_lag_ms:
            return None
        if is_numerically_valid_cycle(cand):
            return cand
        idx -= 1
    return None


def extract_price_blind_anomalies(
    cycles: list[dict[str, Any]],
    btc_points: list[list[Any]],
    cluster_gap_ms: int = 1_800_000,
) -> dict[str, Any]:
    """Extract ex-ante, price-blind GFRI shock clusters and evaluate post-event BTC paths.

    Ex-ante trigger rules (evaluated purely on GFRI ledger state at ``t=0`` without BTC prices):
    - ``ROC_DR_GE_1.30``: ``max(delta_ratio_put, delta_ratio_call) >= 1.30`` with ``max(put, call) >= 2.50``
    - ``DEPTH_DROP_25PCT``: 1-cycle Deribit depth drop ``<= -25%`` with ``max(put, call) >= 2.50``
    - ``ABS_GE_5.0``: tagged when ``max(put, call) >= 5.0`` accompanies a shock trigger
    """
    btc = BtcPriceSeries(btc_points)
    freeze_ts_list = [int(c["freeze_at_ms"]) for c in cycles]

    raw_triggers: list[tuple[int, dict[str, Any], list[str], float]] = []
    for i, c in enumerate(cycles):
        if not is_numerically_valid_cycle(c):
            continue
        gp = float(c["put"]["gfri_confirmed"])
        gc = float(c["call"]["gfri_confirmed"])
        dr_p = float(c["near_ref"].get("delta_ratio_put") or 0.0)
        dr_c = float(c["near_ref"].get("delta_ratio_call") or 0.0)
        dep_chg = 0.0
        if (
            i >= 1
            and is_numerically_valid_cycle(cycles[i - 1])
            and int(c["freeze_at_ms"]) - int(cycles[i - 1]["freeze_at_ms"]) <= 900_000
        ):
            dep_chg = min(
                float(c["put"]["liquidity_depth_usd"]) / float(cycles[i - 1]["put"]["liquidity_depth_usd"]) - 1.0,
                float(c["call"]["liquidity_depth_usd"]) / float(cycles[i - 1]["call"]["liquidity_depth_usd"]) - 1.0,
            )
        tags: list[str] = []
        if max(dr_p, dr_c) >= 1.30 and max(gp, gc) >= 2.50:
            tags.append("ROC_DR_GE_1.30")
        if dep_chg <= -0.25 and max(gp, gc) >= 2.50:
            tags.append("DEPTH_DROP_25PCT")
        if tags and max(gp, gc) >= 5.0:
            tags.append("ABS_GE_5.0")
        if tags and max(dr_p, dr_c) >= 1.50:
            tags.append("POLICY_RAPID_DR_GE_1.50")
        if tags:
            raw_triggers.append((i, c, tags, dep_chg))

    # Merge contiguous/nearby trigger cycles within cluster_gap_ms (30 min default)
    clusters: list[list[tuple[int, dict[str, Any], list[str], float]]] = []
    for item in raw_triggers:
        if not clusters or int(item[1]["freeze_at_ms"]) - int(clusters[-1][-1][1]["freeze_at_ms"]) > cluster_gap_ms:
            clusters.append([item])
        else:
            clusters[-1].append(item)

    events: list[dict[str, Any]] = []
    for k, cl in enumerate(clusters, start=1):
        idx0, c0, _, _ = cl[0]
        peak_put_item = max(cl, key=lambda x: float(x[1]["put"]["gfri_confirmed"]))
        peak_call_item = max(cl, key=lambda x: float(x[1]["call"]["gfri_confirmed"]))
        peak_item = max(
            cl,
            key=lambda x: max(float(x[1]["put"]["gfri_confirmed"]), float(x[1]["call"]["gfri_confirmed"])),
        )
        cp = peak_item[1]
        cp_put = peak_put_item[1]
        cp_call = peak_call_item[1]
        t0_ms = int(c0["freeze_at_ms"])

        # Strictly non-lookahead pre-event GFRI lookups
        pre_120 = _find_prior_cycle_asof(cycles, freeze_ts_list, t0_ms - 7_200_000)
        pre_60 = _find_prior_cycle_asof(cycles, freeze_ts_list, t0_ms - 3_600_000)
        pre_30 = _find_prior_cycle_asof(cycles, freeze_ts_list, t0_ms - 1_800_000)
        prev_cyc = cycles[idx0 - 1] if idx0 >= 1 and is_numerically_valid_cycle(cycles[idx0 - 1]) else None

        # Step changes at onset vs immediate prior numerical cycle
        hf0_m = abs(float(c0["put"]["raw_hedge_flow_usd"])) / 1e6
        der0_m = float(c0["put"]["liquidity_depth_usd"]) / 1e6
        okx0_m = (float(c0["okx_ref"]["down_usd"]) / 1e6) if c0["okx_ref"].get("down_usd") else None

        hf_chg_pct: float | None = None
        der_chg_pct: float | None = None
        okx_chg_pct: float | None = None
        gex_snapshot_updated: bool | None = None
        gex_inst_delta: int | None = None
        if prev_cyc is not None:
            hf_prev = abs(float(prev_cyc["put"]["raw_hedge_flow_usd"])) / 1e6
            der_prev = float(prev_cyc["put"]["liquidity_depth_usd"]) / 1e6
            hf_chg_pct = 100.0 * (hf0_m / hf_prev - 1.0) if hf_prev > 0 else None
            der_chg_pct = 100.0 * (der0_m / der_prev - 1.0) if der_prev > 0 else None
            if okx0_m and prev_cyc["okx_ref"].get("down_usd"):
                okx_prev = float(prev_cyc["okx_ref"]["down_usd"]) / 1e6
                if okx_prev > 0:
                    okx_chg_pct = 100.0 * (okx0_m / okx_prev - 1.0)
            s_prev = prev_cyc["upstream_gex"].get("snapshot_id")
            s_cur = c0["upstream_gex"].get("snapshot_id")
            if s_prev and s_cur:
                gex_snapshot_updated = s_cur != s_prev
            i_prev = prev_cyc["upstream_gex"].get("instrument_count")
            i_cur = c0["upstream_gex"].get("instrument_count")
            if i_prev is not None and i_cur is not None:
                gex_inst_delta = int(i_cur) - int(i_prev)

        # BTC path evaluation around t0 (strictly ts <= t0_ms at anchor t0)
        p0, p0_ts = btc.price_asof(t0_ms)
        if p0 is None:
            raise ValueError(f"Missing pre-event BTC price <= t0_ms ({t0_ms})")
        pm60, _ = btc.price_asof(t0_ms - 3_600_000)
        pm30, _ = btc.price_asof(t0_ms - 1_800_000)
        pp30, _ = btc.price_at(t0_ms + 1_800_000)
        pp60, _ = btc.price_at(t0_ms + 3_600_000)
        pp120, _ = btc.price_at(t0_ms + 7_200_000)
        lo60, hi60 = btc.window_extrema(t0_ms, t0_ms + 3_600_000)
        lo120, hi120 = btc.window_extrema(t0_ms, t0_ms + 7_200_000)
        pre60_lo, pre60_hi = btc.window_extrema(t0_ms - 3_600_000, t0_ms)

        assert p0 is not None
        r_m60 = 100.0 * (p0 / pm60 - 1.0) if pm60 else None
        r_m30 = 100.0 * (p0 / pm30 - 1.0) if pm30 else None
        r_p30 = 100.0 * (pp30 / p0 - 1.0) if pp30 else None
        r_p60 = 100.0 * (pp60 / p0 - 1.0) if pp60 else None
        r_p120 = 100.0 * (pp120 / p0 - 1.0) if pp120 else None
        dd_60 = 100.0 * (lo60 / p0 - 1.0) if lo60 else 0.0
        up_60 = 100.0 * (hi60 / p0 - 1.0) if hi60 else 0.0
        dd_120 = 100.0 * (lo120 / p0 - 1.0) if lo120 else 0.0
        up_120 = 100.0 * (hi120 / p0 - 1.0) if hi120 else 0.0
        pre60_range_pct = (
            100.0 * (pre60_hi / pre60_lo - 1.0)
            if (pre60_lo and pre60_hi and pre60_lo > 0)
            else 0.0
        )

        prior_abs = max(abs(r_m60 or 0.0), abs(r_m30 or 0.0))
        prior_active = prior_abs >= 1.00 or pre60_range_pct >= 1.50
        post60_abs = max(abs(dd_60), abs(up_60))
        post120_abs = max(abs(dd_120), abs(up_120))

        # Check whether GFRI reverted below 2.10x for >= 4 consecutive cycles before any >=1.25% move
        post_60m_cycles = [
            c
            for c in cycles
            if t0_ms < int(c["freeze_at_ms"]) <= t0_ms + 3_900_000 and is_numerically_valid_cycle(c)
        ]
        reverted_calm = len(post_60m_cycles) >= 4 and all(
            max(float(c["put"]["gfri_confirmed"]), float(c["call"]["gfri_confirmed"])) < 2.10
            for c in post_60m_cycles[:4]
        )

        # Deterministic post-hoc event classification
        if post120_abs < 1.25 and not prior_active:
            outcome_class = "FALSE_POSITIVE"
        elif post60_abs < 0.75 and reverted_calm and post120_abs >= 1.50:
            outcome_class = "INCONCLUSIVE"
        elif not prior_active and post120_abs >= 1.50:
            outcome_class = "LEADING"
        elif prior_active and post120_abs >= 1.30:
            outcome_class = "COINCIDENT"
        elif prior_active and post120_abs < 1.30:
            outcome_class = "LAGGING"
        else:
            outcome_class = "FALSE_POSITIVE"

        all_tags = sorted({t for item in cl for t in item[2]})
        venue_scope = (
            "CROSS_VENUE_STRESS"
            if (okx_chg_pct is not None and okx_chg_pct <= -15.0)
            else "DERIBIT_ONLY_QUOTE_PULL"
        )

        events.append(
            {
                "event_id": f"E{k:02d}",
                "onset_iso": c0["freeze_iso"],
                "onset_ms": t0_ms,
                "cluster_end_iso": cl[-1][1]["freeze_iso"],
                "cluster_cycle_count": len(cl),
                "peak_iso": cp["freeze_iso"],
                "trigger_tags": all_tags,
                "venue_scope": venue_scope,
                "data_quality": c0["data_quality"],
                "onset_put_status": c0["put"]["status"],
                "peak_put_status": cp_put["put"]["status"],
                "pre_120m_put": float(pre_120["put"]["gfri_confirmed"]) if pre_120 else None,
                "pre_120m_asof_ms": int(pre_120["freeze_at_ms"]) if pre_120 else None,
                "pre_60m_put": float(pre_60["put"]["gfri_confirmed"]) if pre_60 else None,
                "pre_60m_asof_ms": int(pre_60["freeze_at_ms"]) if pre_60 else None,
                "pre_30m_put": float(pre_30["put"]["gfri_confirmed"]) if pre_30 else None,
                "pre_30m_asof_ms": int(pre_30["freeze_at_ms"]) if pre_30 else None,
                "onset_put_gfri": float(c0["put"]["gfri_confirmed"]),
                "onset_call_gfri": float(c0["call"]["gfri_confirmed"]),
                "peak_put_gfri": float(cp_put["put"]["gfri_confirmed"]),
                "peak_call_gfri": float(cp_call["call"]["gfri_confirmed"]),
                "onset_delta_ratio_put": c0["near_ref"].get("delta_ratio_put"),
                "peak_delta_ratio_put": cp["near_ref"].get("delta_ratio_put"),
                "onset_shadow_decision": c0["shadow_decision"],
                "peak_shadow_decision": cp["shadow_decision"],
                "hf_put_musd": round(hf0_m, 3),
                "hf_step_chg_pct": round(hf_chg_pct, 2) if hf_chg_pct is not None else None,
                "deribit_depth_put_musd": round(der0_m, 3),
                "peak_deribit_depth_put_musd": round(float(cp["put"]["liquidity_depth_usd"]) / 1e6, 3),
                "deribit_depth_step_chg_pct": round(der_chg_pct, 2) if der_chg_pct is not None else None,
                "okx_depth_put_musd": round(okx0_m, 3) if okx0_m is not None else None,
                "okx_depth_step_chg_pct": round(okx_chg_pct, 2) if okx_chg_pct is not None else None,
                "gex_snapshot_updated": gex_snapshot_updated,
                "gex_instrument_count": c0["upstream_gex"].get("instrument_count"),
                "gex_instrument_delta": gex_inst_delta,
                "btc_price_t0": round(p0, 2),
                "btc_matched_ts_ms": p0_ts,
                "btc_ret_m60_pct": round(r_m60, 2) if r_m60 is not None else None,
                "btc_ret_m30_pct": round(r_m30, 2) if r_m30 is not None else None,
                "btc_ret_p30_pct": round(r_p30, 2) if r_p30 is not None else None,
                "btc_ret_p60_pct": round(r_p60, 2) if r_p60 is not None else None,
                "btc_ret_p120_pct": round(r_p120, 2) if r_p120 is not None else None,
                "btc_post120m_min_pct": round(dd_120, 2),
                "btc_post120m_max_pct": round(up_120, 2),
                "outcome_classification": outcome_class,
            }
        )

    outcome_counts = Counter(e["outcome_classification"] for e in events)
    return {
        "trigger_cycle_count": len(raw_triggers),
        "cluster_count": len(events),
        "outcome_counts": dict(outcome_counts),
        "events": events,
    }


def scan_major_btc_moves(
    cycles: list[dict[str, Any]],
    btc_points: list[list[Any]],
    min_impulse_pct: float = 0.015,
) -> dict[str, Any]:
    """Reverse scan of all non-overlapping >=1.5% 60m BTC impulses across the GFRI window."""
    btc = BtcPriceSeries(btc_points)
    t_first = int(cycles[0]["freeze_at_ms"])
    t_last = int(cycles[-1]["freeze_at_ms"])

    raw_moves: list[tuple[float, int, int, float, float, float]] = []
    for i1, t1 in enumerate(btc.ts_ms):
        if t1 < t_first or t1 > t_last + 900_000:
            continue
        p1 = btc.prices[i1]
        i0 = bisect.bisect_left(btc.ts_ms, t1 - 3_600_000)
        if i0 >= i1:
            continue
        sub_min_idx = min(range(i0, i1 + 1), key=lambda k: btc.prices[k])
        sub_max_idx = max(range(i0, i1 + 1), key=lambda k: btc.prices[k])
        up_ret = p1 / btc.prices[sub_min_idx] - 1.0
        dn_ret = p1 / btc.prices[sub_max_idx] - 1.0
        if up_ret >= min_impulse_pct and sub_min_idx < i1 and btc.ts_ms[sub_min_idx] >= t_first - 1_800_000:
            raw_moves.append((up_ret, btc.ts_ms[sub_min_idx], t1, btc.prices[sub_min_idx], p1, up_ret))
        if dn_ret <= -min_impulse_pct and sub_max_idx < i1 and btc.ts_ms[sub_max_idx] >= t_first - 1_800_000:
            raw_moves.append((abs(dn_ret), btc.ts_ms[sub_max_idx], t1, btc.prices[sub_max_idx], p1, dn_ret))

    raw_moves.sort(reverse=True)
    selected: list[tuple[float, int, int, float, float, float]] = []
    for item in raw_moves:
        _, t0, t1, _, _, r = item
        overlap = False
        for _, st0, st1, _, _, sr in selected:
            if r * sr > 0 and not (t1 < st0 - 3_600_000 or t0 > st1 + 3_600_000):
                overlap = True
                break
        if not overlap:
            selected.append(item)
    selected.sort(key=lambda x: x[1])

    def _is_confirmed_shock(idx: int) -> bool:
        c = cycles[idx]
        if not is_numerically_valid_cycle(c):
            return False
        gp = float(c["put"]["gfri_confirmed"])
        gc = float(c["call"]["gfri_confirmed"])
        dr = max(
            float(c["near_ref"].get("delta_ratio_put") or 0.0),
            float(c["near_ref"].get("delta_ratio_call") or 0.0),
        )
        dc = 0.0
        if (
            idx >= 1
            and is_numerically_valid_cycle(cycles[idx - 1])
            and int(c["freeze_at_ms"]) - int(cycles[idx - 1]["freeze_at_ms"]) <= 900_000
        ):
            dc = min(
                float(c["put"]["liquidity_depth_usd"]) / float(cycles[idx - 1]["put"]["liquidity_depth_usd"]) - 1.0,
                float(c["call"]["liquidity_depth_usd"]) / float(cycles[idx - 1]["call"]["liquidity_depth_usd"]) - 1.0,
            )
        return max(gp, gc) >= 2.50 and (dr >= 1.40 or dc <= -0.25)

    classified_moves: list[dict[str, Any]] = []
    for k, (ar, t0, t1, p0, p1, r) in enumerate(selected):
        prev_end = selected[k - 1][2] if k > 0 else 0
        next_start = selected[k + 1][1] if k + 1 < len(selected) else t_last + 3_600_000
        pre_lo = max(t0 - 4_500_000, prev_end)
        post_hi = min(t1 + 2_700_000, next_start)

        pre = [(i, cycles[i]) for i in range(len(cycles)) if pre_lo <= int(cycles[i]["freeze_at_ms"]) <= t0]
        dur = [(i, cycles[i]) for i in range(len(cycles)) if t0 < int(cycles[i]["freeze_at_ms"]) <= t1]
        post = [(i, cycles[i]) for i in range(len(cycles)) if t1 < int(cycles[i]["freeze_at_ms"]) <= post_hi]

        pre_60_raw = [c for c in cycles if t0 - 3_600_000 <= int(c["freeze_at_ms"]) <= t0]
        pre_60_num = [c for c in pre_60_raw if is_numerically_valid_cycle(c)]
        dur_num = [c for _, c in dur if is_numerically_valid_cycle(c)]

        pre_shocks = [c for i, c in pre if _is_confirmed_shock(i)]
        dur_shocks = [c for i, c in dur if _is_confirmed_shock(i)]
        post_shocks = [c for i, c in post if _is_confirmed_shock(i)]

        if (len(pre_60_raw) > 0 and len(pre_60_num) == 0) or (
            not pre_shocks and len(dur) > 0 and len(dur_num) == 0
        ):
            cls = "INCONCLUSIVE"
        elif pre_shocks:
            cls = "LEADING"
        elif dur_shocks:
            first_dur = dur_shocks[0]
            px_shock, _ = btc.price_at(int(first_dur["freeze_at_ms"]))
            frac_done = abs((px_shock or p1) / p0 - 1.0) / ar if ar > 0 else 1.0
            cls = "COINCIDENT" if frac_done <= 0.70 else "LAGGING"
        elif post_shocks:
            cls = "LAGGING"
        else:
            cls = "NO_SIGNAL"

        pre_put_max = max((float(c["put"]["gfri_confirmed"]) for c in pre_60_num), default=None)
        dur_put_max = max((float(c["put"]["gfri_confirmed"]) for c in dur_num), default=None)
        post_put_max = max(
            (float(c["put"]["gfri_confirmed"]) for _, c in post if is_numerically_valid_cycle(c)),
            default=None,
        )
        dur_statuses = sorted({c["put"]["status"] for c in dur_num})

        classified_moves.append(
            {
                "move_id": f"M{k + 1:02d}",
                "onset_iso": ms_to_iso(t0),
                "end_iso": ms_to_iso(t1),
                "duration_min": round((t1 - t0) / 60_000.0, 1),
                "direction": "UP" if r > 0 else "DOWN",
                "start_price": round(p0, 2),
                "end_price": round(p1, 2),
                "return_pct": round(100.0 * r, 2),
                "pre_60m_max_put_gfri": round(pre_put_max, 3) if pre_put_max is not None else None,
                "during_max_put_gfri": round(dur_put_max, 3) if dur_put_max is not None else None,
                "post_45m_max_put_gfri": round(post_put_max, 3) if post_put_max is not None else None,
                "during_put_statuses": dur_statuses,
                "classification": cls,
            }
        )

    counts = Counter(m["classification"] for m in classified_moves)
    return {
        "move_count": len(classified_moves),
        "classification_counts": {
            "LEADING": counts.get("LEADING", 0),
            "COINCIDENT": counts.get("COINCIDENT", 0),
            "LAGGING": counts.get("LAGGING", 0),
            "NO_SIGNAL": counts.get("NO_SIGNAL", 0),
            "INCONCLUSIVE": counts.get("INCONCLUSIVE", 0),
        },
        "moves": classified_moves,
    }


def run_full_analysis(data_dir: Path | None = None) -> dict[str, Any]:
    """Execute complete A0 runtime audit and A1 event study pipeline deterministically."""
    ds = load_dataset(data_dir)
    cycles = ds["cycles"]
    meta = ds["meta"]
    btc_points = ds["market_paths"]["btc_1m_points"]
    dossiers = ds["dossiers"]

    audit = compute_runtime_audit(cycles, meta)
    distributions = compute_all_distributions(cycles, dossiers)
    anomalies = extract_price_blind_anomalies(cycles, btc_points)
    move_scan = scan_major_btc_moves(cycles, btc_points)

    return {
        "runtime_audit": audit,
        "distributions": distributions,
        "anomalies": anomalies,
        "move_scan": move_scan,
    }


def main() -> None:
    """CLI entrypoint printing concise JSON/table verification summary."""
    res = run_full_analysis()
    ov = res["runtime_audit"]["overall"]
    dist_all = res["distributions"]["overall"]
    anom = res["anomalies"]
    mscan = res["move_scan"]

    summary = {
        "total_cycles": ov["total_cycles"],
        "span_utc": f"{ov['first_freeze_iso']} -> {ov['last_freeze_iso']}",
        "median_cadence_sec": ov["observed_median_cadence_sec"],
        "expected_cycles_per_day": ov["expected_cycles_per_full_day"],
        "replayable_saved": ov["replay_class_counts"]["REPLAYABLE"],
        "partial_non_replayable": ov["replay_class_counts"]["NON_REPLAYABLE"],
        "numerical_gfri_cycles": ov["numerical_cycles_total"],
        "put_valid_numeric": ov["put_quality_breakdown"]["valid_numeric"],
        "put_degraded_numeric": ov["put_quality_breakdown"]["degraded_numeric"],
        "null_cycles": ov["put_quality_breakdown"]["null_total"],
        "sha256_mismatches": ov["raw_captures_hash_audit"]["sha256_mismatch_count"],
        "ledger_consistent": ov["ledger_consistency"]["consistent"],
        "put_gfri_p50_overall": round(dist_all["put_dist"]["p50"], 3),
        "depth_log_var_cov_share_put_pct": round(100.0 * dist_all["log_variance_put"]["depth_cov_share"], 2),
        "depth_unsplit_var_share_put_pct": round(
            100.0 * dist_all["log_variance_put"]["depth_unsplit_var_share"], 2
        ),
        "okx_comove_all_pct": round(100.0 * dist_all["okx_comovement"]["all_steps_ratio"], 2),
        "price_blind_anomaly_clusters": anom["cluster_count"],
        "anomaly_outcomes": anom["outcome_counts"],
        "major_btc_moves": mscan["move_count"],
        "move_classifications": mscan["classification_counts"],
    }
    print("=== GFRI v1.1 Runtime Audit & Event Study Verification Summary ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("\n=== Daily PUT GFRI & Quality Summary ===")
    print("UTC_Day    | Obs | Replay | Num(Val+Deg) | HF_p50($M) | PUT_p50 | PUT_p90 | PUT_max  | DepthVar%")
    for d_row, d_dist in zip(res["runtime_audit"]["daily"], res["distributions"]["daily"]):
        v_cnt = d_row["put_valid"]
        dg_cnt = d_row["put_degraded_numeric"]
        p50 = d_dist["put_dist"]["p50"]
        p90 = d_dist["put_dist"]["p90_descriptive"]
        pmax = d_dist["put_dist"]["max"]
        hf50 = d_dist["hf_put_musd_p50"]
        dvar = 100.0 * (d_dist["log_variance_put"]["depth_unsplit_var_share"] or 0.0)
        print(
            f"{d_row['utc_day']} | {d_row['observed_cycles']:3d} | {d_row['saved_replayable']:6d} | "
            f"{d_row['numerical_gfri']:3d}({v_cnt:3d}+{dg_cnt:2d}) | {hf50:10.2f} | "
            f"{p50:7.3f} | {p90:7.3f} | {pmax:8.3f} | {dvar:8.2f}%"
        )


if __name__ == "__main__":
    main()
