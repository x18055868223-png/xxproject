# -*- coding: utf-8 -*-
"""VRP forward-vol hurdle walk-forward replay on a real index price path.

This replay answers a narrower, DATA-FEASIBLE question than full edge validation.
Given only the realized BTC index path (no historical option IV), it tests whether the
``forward_vol_hurdle`` -- built from trailing RV + percentile + cold-start, strictly
leak-guarded to data at-or-before each time t -- behaves PROTECTIVELY against the
volatility that actually realizes over the next 24/48/72h.

What it validates:
  - The hurdle's protective calibration vs realized RV (does realized vol breach the
    hurdle, and does the low-percentile guard reduce breaches where expansion risk is
    highest?).
  - Whether the expansion asymmetry the guard targets is real on this path (does low
    trailing-vol regime actually precede higher realized vol?).

What it does NOT validate (be honest):
  - IV-vs-RV seller edge. That requires multi-time-point option IV the project does not
    yet have, and must come from forward snapshot collection. The single same-day
    snapshot only fixes IV at one instant. See docs/VRP阶段性封版说明_v1.1.md.
"""
from __future__ import annotations

from statistics import mean
from typing import Any, Dict, List, Optional, Sequence

from vrp_model import ScenarioConfig, forward_vol_hurdle
from deribit_snapshot import annualized_realized_vol_from_closes, rv_percentile


DEFAULT_HORIZONS_HOURS = (24, 48, 72)
MIN_BARS_FOR_7D = 169  # rv_7d needs ~169 hourly closes (168 returns)


def _trailing_rv(closes: Sequence[float], end_idx: int) -> Dict[str, Optional[float]]:
    """Trailing 24h/72h/7d annualized RV using only closes[:end_idx+1] (leak-guarded)."""
    upto = closes[: end_idx + 1]
    return {
        "rv_24h": annualized_realized_vol_from_closes(upto[-25:]),
        "rv_72h": annualized_realized_vol_from_closes(upto[-73:]),
        "rv_7d": annualized_realized_vol_from_closes(upto[-169:]),
    }


def _precompute_rolling_24h(closes: Sequence[float]) -> List[Optional[float]]:
    """rolling[i] = annualized 24h RV of closes[i-24:i+1] for i>=24 else None."""
    rolling: List[Optional[float]] = [None] * len(closes)
    for i in range(24, len(closes)):
        rolling[i] = annualized_realized_vol_from_closes(closes[i - 24 : i + 1])
    return rolling


def forward_realized_vol(closes: Sequence[float], t: int, horizon_hours: int) -> Optional[float]:
    """Annualized RV over (t, t+horizon] using strictly-future closes (leak-guarded)."""
    end = t + horizon_hours
    if end >= len(closes):
        return None
    return annualized_realized_vol_from_closes(closes[t : end + 1])


def _percentile_bucket(pct: Optional[float], config: ScenarioConfig) -> str:
    if pct is None:
        return "unknown"
    if pct <= config.low_percentile_threshold:
        return "low"
    if pct >= config.high_percentile_threshold:
        return "high"
    return "mid"


def walk_forward_hurdle_replay(
    closes: Sequence[float],
    config: ScenarioConfig,
    horizons_hours: Sequence[int] = DEFAULT_HORIZONS_HOURS,
    min_history_hours: int = MIN_BARS_FOR_7D,
    step: int = 1,
) -> Dict[str, Any]:
    """Run a leak-guarded walk-forward over a single hourly close path."""
    closes = [float(c) for c in closes if c and float(c) > 0]
    n = len(closes)
    horizons_hours = tuple(int(h) for h in horizons_hours)
    max_h = max(horizons_hours)
    rolling = _precompute_rolling_24h(closes)
    records: List[Dict[str, Any]] = []

    for t in range(max(min_history_hours, 24), n - max_h, step):
        rv = _trailing_rv(closes, t)
        if rv["rv_24h"] is None or rv["rv_72h"] is None or rv["rv_7d"] is None:
            continue
        hist_rolling = [r for r in rolling[24 : t + 1] if r is not None][-2160:]
        current = rolling[t] if rolling[t] is not None else rv["rv_24h"]
        pct = rv_percentile(hist_rolling, current)
        history_days = (t + 1) // 24
        hurdle, meta = forward_vol_hurdle(
            rv["rv_24h"], rv["rv_72h"], rv["rv_7d"], pct, history_days, config
        )
        anchor_only = meta.get("rv_regime_anchor")
        if hurdle is None or anchor_only is None:
            continue

        rec: Dict[str, Any] = {
            "t": t,
            "history_days": history_days,
            "rv_regime_anchor": anchor_only,
            "rv_percentile": pct,
            "percentile_bucket": _percentile_bucket(pct, config),
            "forward_vol_hurdle": hurdle,
            "cold_start": history_days < config.min_history_days,
        }
        for h in horizons_hours:
            fwd = forward_realized_vol(closes, t, h)
            rec[f"realized_fwd_{h}h"] = fwd
            rec[f"breach_{h}h"] = fwd is not None and fwd > hurdle
            rec[f"breach_anchor_only_{h}h"] = fwd is not None and fwd > anchor_only
        records.append(rec)

    summary = _summarise(records, config, horizons_hours)
    return {
        "schema_name": "VrpHurdleReplayResult",
        "schema_version": "nrd.integration.vrp.replay.v1.1",
        "vrp_factor_version": "1.1.0",
        "method": "single_path_walk_forward_leak_guarded",
        "validates": "forward_vol_hurdle protective calibration vs realized RV",
        "does_not_validate": (
            "IV-vs-RV seller edge: needs multi-time-point option IV (forward collection required)"
        ),
        "config_brief": {
            "low_percentile_threshold": config.low_percentile_threshold,
            "high_percentile_threshold": config.high_percentile_threshold,
            "low_percentile_multiplier": config.low_percentile_multiplier,
            "high_percentile_multiplier": config.high_percentile_multiplier,
            "cold_start_multiplier": config.cold_start_multiplier,
            "min_history_days": config.min_history_days,
            "rv_weights": list(config.rv_weights),
        },
        "n_closes": n,
        "n_steps": len(records),
        "horizons_hours": list(horizons_hours),
        "summary": summary,
        "records": records,
    }


def _rate(rows: List[Dict[str, Any]], key: str) -> float:
    return sum(1 for r in rows if r[key]) / len(rows) if rows else 0.0


def _summarise(
    records: List[Dict[str, Any]], config: ScenarioConfig, horizons_hours: Sequence[int]
) -> Dict[str, Any]:
    out: Dict[str, Any] = {"per_horizon": {}, "by_percentile_bucket": {}, "cold_start": {}, "findings": []}
    for h in horizons_hours:
        key = f"realized_fwd_{h}h"
        rows = [r for r in records if r.get(key) is not None]
        if not rows:
            continue
        out["per_horizon"][f"{h}h"] = {
            "n": len(rows),
            "hurdle_breach_rate": round(_rate(rows, f"breach_{h}h"), 4),
            "anchor_only_breach_rate": round(_rate(rows, f"breach_anchor_only_{h}h"), 4),
            "mean_realized_to_hurdle": round(
                mean(r[key] / r["forward_vol_hurdle"] for r in rows), 4
            ),
        }
        buckets: Dict[str, Any] = {}
        for b in ("low", "mid", "high"):
            brows = [r for r in rows if r["percentile_bucket"] == b]
            if not brows:
                continue
            buckets[b] = {
                "n": len(brows),
                "hurdle_breach_rate": round(_rate(brows, f"breach_{h}h"), 4),
                "anchor_only_breach_rate": round(_rate(brows, f"breach_anchor_only_{h}h"), 4),
                "mean_realized_to_anchor": round(
                    mean(r[key] / r["rv_regime_anchor"] for r in brows), 4
                ),
            }
        out["by_percentile_bucket"][f"{h}h"] = buckets

        cs = [r for r in rows if r["cold_start"]]
        if cs:
            out["cold_start"][f"{h}h"] = {
                "n": len(cs),
                "hurdle_breach_rate": round(_rate(cs, f"breach_{h}h"), 4),
                "anchor_only_breach_rate": round(_rate(cs, f"breach_anchor_only_{h}h"), 4),
            }

    out["findings"] = _derive_findings(out, horizons_hours)
    return out


def _derive_findings(summary: Dict[str, Any], horizons_hours: Sequence[int]) -> List[str]:
    """Report what the data says -- whichever way it points. No claim of seller edge."""
    findings: List[str] = []
    for h in horizons_hours:
        hk = f"{h}h"
        buckets = summary["by_percentile_bucket"].get(hk) or {}
        low = buckets.get("low")
        high = buckets.get("high")
        if low and high:
            if low["mean_realized_to_anchor"] > high["mean_realized_to_anchor"]:
                findings.append(
                    f"[{hk}] expansion asymmetry PRESENT: low-percentile regime realizes more "
                    f"vol vs its anchor ({low['mean_realized_to_anchor']}) than high-percentile "
                    f"({high['mean_realized_to_anchor']}) -- the low-percentile guard targets a real effect."
                )
            else:
                findings.append(
                    f"[{hk}] expansion asymmetry NOT confirmed on this path: low bucket "
                    f"realized/anchor {low['mean_realized_to_anchor']} <= high bucket "
                    f"{high['mean_realized_to_anchor']}; guard value is weaker than assumed here."
                )
        if low:
            if low["hurdle_breach_rate"] < low["anchor_only_breach_rate"]:
                findings.append(
                    f"[{hk}] low-percentile guard REDUCES breaches: hurdle breach "
                    f"{low['hurdle_breach_rate']} < anchor-only {low['anchor_only_breach_rate']}."
                )
            else:
                findings.append(
                    f"[{hk}] low-percentile guard did not reduce breaches on this path "
                    f"({low['hurdle_breach_rate']} vs anchor-only {low['anchor_only_breach_rate']})."
                )
        cs = summary["cold_start"].get(hk)
        if cs:
            findings.append(
                f"[{hk}] cold-start segment (n={cs['n']}): hurdle breach {cs['hurdle_breach_rate']} "
                f"vs anchor-only {cs['anchor_only_breach_rate']}."
            )
    findings.append(
        "SCOPE: this validates hurdle protective calibration vs realized RV only; it does NOT "
        "prove IV-vs-RV seller edge (needs multi-time-point option IV from forward collection)."
    )
    return findings
