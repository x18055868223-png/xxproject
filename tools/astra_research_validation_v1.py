"""Forward validation E3: fixed, program-run scoring of registered hypotheses (docs/opus/08, 2026-10-20).

Evaluation is fixed here, not by the researcher (scoring_rules@0.2):

- Decision instants: fixed checkpoints every 4 hours (UTC 00, 04, ..., 20). At each checkpoint
  a contract is sampled from its latest ledger row in the 30 minutes before it, if that row
  is displayed. The condition is evaluated on that row alone, so both groups are decided at
  the same instants with only what was known then: true -> condition group, false -> control,
  undecidable (missing field) -> excluded. A sample is (contract, checkpoint).
- Only after registration: checkpoints strictly after the hypothesis's server registration
  time, and only contracts first seen after it (their state was never visible to the
  researcher before registering).
- Outcomes come from the archived delivery price and minute index path, measured from the
  checkpoint. Path metrics need a complete path (gaps at both ends within 2 x the median
  step); otherwise that sample is excluded for that metric.
- Effect, signed so that positive means riskier:
    touched_short_after_entry, loss_at_expiry: (observed - entry baseline) in the condition
        group minus the same excess in the control group, so a biased baseline cancels;
    settle_vs_short_pct, max_adverse_after_entry_pct: condition mean minus control mean;
    result_btc_at_entry_credit: control mean minus condition mean.
  Samples are weighted equally; OLS on the group indicator with standard errors clustered by
  expiry day (CR1), because contracts of one expiry share its settlement.
- Status is decided only at milestones, when both groups reach 60, 90 and 120 expiry days.
  The threshold is max(2, z(1 - 0.025 / K)), K = hypotheses ever registered.
    candidate   no samples yet
    observing   before the 60-day milestone, or passed only the 60-day one
    weakened    the latest milestone not passed
    supported   two consecutive milestones passed (provisional at 90, final at 120)
    rejected    significant in the opposite direction at any milestone, or not supported
                at 120 (final)
  A hypothesis named in a later one's `supersedes` keeps its statistics and shows
  superseded with its last status.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import math
from pathlib import Path
from statistics import NormalDist
from typing import Any, Iterable, Mapping

if __package__:
    from . import astra_commercial_math_v17 as commercial_math
    from . import astra_research_inbox_v1 as research_inbox
else:
    import astra_commercial_math_v17 as commercial_math
    import astra_research_inbox_v1 as research_inbox

SCORING_RULES = "scoring_rules@0.2"
SCHEMA_EVAL = "hypothesis_evaluation@0.2"
MILESTONES = (60, 90, 120)
MIN_T = 2.0
HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
CHECKPOINT_EVERY_MS = 4 * HOUR_MS
CHECKPOINT_LOOKBACK_MS = 30 * 60_000
BASELINE_FIELD = {"touched_short_after_entry": "p_touch_short", "loss_at_expiry": "p_loss"}
PATH_METRICS = {"touched_short_after_entry", "max_adverse_after_entry_pct"}
KEEP_FIELDS = ("contract_key", "at_ms", "expiry_ms", "displayed", "locked", "side", "states", "credit_bid_ask_btc",
               "mark_spread_btc", "index_usd", "forward_usd", "short_from_index_pct", "long_from_index_pct",
               "breakeven_from_index_pct", "p_itm_short", "p_touch_short", "p_loss", "p_max_loss", "short", "long",
               # ledger_research_fields@0.1: conditions may name them, so the slim row keeps them
               "expiry_atm_iv_pct", "expected_move_pct", "short_cushion_pct", "distance_in_moves", "rv_24h_pct",
               "rv_7d_pct", "iv_minus_rv_24h", "dvol", "iv_percentile", "rr_25d_expiry", "entry_fee_btc",
               "credit_after_fee_btc", "comp_ratio_after_fee", "comp_tier", "gamma_wall_usd", "gamma_wall_vs_short_pct",
               "pin_from_index_pct", "hours_to_next_event", "next_event_kind", "event_before_expiry",
               "index_chg_1h_pct", "index_chg_4h_pct", "fields_version")


def _utc(ms: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)


def threshold(k: int) -> float:
    return max(MIN_T, NormalDist().inv_cdf(1 - 0.025 / max(1, k)))


def calendar_fields(row: Mapping[str, Any]) -> dict[str, Any]:
    at, expiry = int(row["at_ms"]), int(row["expiry_ms"])
    moment = _utc(at)
    return {"hours_to_expiry": (expiry - at) / HOUR_MS, "hour_utc": moment.hour, "weekday": moment.weekday()}


def _read_jsonl(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _slim(row: Mapping[str, Any]) -> dict[str, Any]:
    out = {k: row.get(k) for k in KEEP_FIELDS if k in row}
    for leg in ("short", "long"):
        if isinstance(out.get(leg), Mapping):
            out[leg] = {k: out[leg].get(k) for k in ("mark_iv", "delta", "mark_price")}
    return out


def load_archive(root: Path, since_ms: int) -> dict[str, Any]:
    """Ledger rows from two days before the earliest registration (to know first sightings),
    settled outcomes, and minute paths of expiry days on or after that registration."""
    first_day = _utc(max(0, since_ms - 2 * DAY_MS)).strftime("%Y-%m-%d") if since_ms else "0000-00-00"
    since_day = _utc(since_ms).strftime("%Y-%m-%d") if since_ms else "0000-00-00"
    ledger = [_slim(r) for r in _read_jsonl(sorted(p for p in (root / "ledger").glob("*.jsonl") if p.stem >= first_day))]
    outcomes = {o["contract_key"]: o for o in _read_jsonl(sorted((root / "outcomes").glob("*.jsonl")))
                if o.get("status") == "settled"}
    paths: dict[str, list[list[float]]] = {}
    for path in sorted((root / "paths").glob("*.json.gz")):
        if path.name[:10] >= since_day:
            paths[path.name[:10]] = json.loads(gzip.decompress(path.read_bytes()))["points"]
    return {"ledger": ledger, "outcomes": outcomes, "paths": paths}


def _beyond(side: str, price: float, strike: float) -> bool:
    return price <= strike if side == "put" else price >= strike


def _complete_after(path: list[list[float]] | None, start: int, end: int) -> list[float] | None:
    series = sorted((int(t), float(p)) for t, p in (path or []) if start <= int(t) <= end and p)
    if len(series) < 2:
        return None
    steps = sorted(b[0] - a[0] for a, b in zip(series, series[1:]))
    limit = 2 * steps[len(steps) // 2]
    if series[0][0] - start > limit or end - series[-1][0] > limit:
        return None
    return [p for _, p in series]


def sample_metrics(entry: Mapping[str, Any], outcome: Mapping[str, Any],
                   path: list[list[float]] | None) -> dict[str, Any]:
    side, ks, kl = outcome["side"], float(outcome["short_strike_usd"]), float(outcome["long_strike_usd"])
    delivery = float(outcome["delivery_price_usd"])
    payout = commercial_math.inverse_spread_payout(side, ks, kl, delivery, 1.0)
    credit = entry.get("credit_bid_ask_btc")
    adverse = (lambda p: (ks - p) / ks * 100.0) if side == "put" else (lambda p: (p - ks) / ks * 100.0)
    after = _complete_after(path, int(entry["at_ms"]), int(outcome["expiry_ms"]))
    usable_credit = isinstance(credit, (int, float)) and not isinstance(credit, bool) and credit > 0
    return {
        "touched_short_after_entry": (1.0 if any(_beyond(side, p, ks) for p in after) else 0.0) if after else None,
        "loss_at_expiry": (1.0 if credit - payout < 0 else 0.0) if usable_credit else None,
        "settle_vs_short_pct": adverse(delivery),
        "max_adverse_after_entry_pct": max(adverse(p) for p in after) if after else None,
        "result_btc_at_entry_credit": (credit - payout) if usable_credit else None,
    }


def checkpoints_between(start_ms: int, end_ms: int) -> list[int]:
    first = (start_ms // CHECKPOINT_EVERY_MS + 1) * CHECKPOINT_EVERY_MS
    return list(range(first, end_ms + 1, CHECKPOINT_EVERY_MS))


def assign(hypothesis: Mapping[str, Any], ledger: list[Mapping[str, Any]], since_ms: int,
           until_ms: int) -> list[dict[str, Any]]:
    """Samples (contract, checkpoint) decided by the condition on the row at that instant."""
    by_contract: dict[str, list[Mapping[str, Any]]] = {}
    for row in ledger:
        by_contract.setdefault(row["contract_key"], []).append(row)
    samples = []
    for key, rows in by_contract.items():
        rows.sort(key=lambda r: int(r["at_ms"]))
        if int(rows[0]["at_ms"]) <= since_ms:
            continue   # first seen before registration: excluded
        expiry = int(rows[0]["expiry_ms"])
        index = 0
        for checkpoint in checkpoints_between(since_ms, min(until_ms, expiry)):
            while index + 1 < len(rows) and int(rows[index + 1]["at_ms"]) <= checkpoint:
                index += 1
            row = rows[index]
            at = int(row["at_ms"])
            if at > checkpoint or checkpoint - at > CHECKPOINT_LOOKBACK_MS or row.get("displayed") is not True:
                continue
            verdict = research_inbox.evaluate_condition(hypothesis["condition"],
                                                       {"ledger": row, "calendar": calendar_fields(row)})
            if verdict is None:
                continue
            samples.append({"contract_key": key, "checkpoint_ms": checkpoint,
                            "group": "condition" if verdict else "control", "entry": row})
    return samples


def clustered_difference(values: list[tuple[float, int, str]]) -> dict[str, Any]:
    """OLS y = a + b*group with CR1 standard errors clustered by expiry day.
    values: (y, group 0/1, cluster)."""
    n = len(values)
    groups = {0: [v for v in values if v[1] == 0], 1: [v for v in values if v[1] == 1]}
    days = {g: len({c for _, _, c in vs}) for g, vs in groups.items()}
    summary = {"n_condition": len(groups[1]), "n_control": len(groups[0]),
               "days_condition": days[1], "days_control": days[0]}
    if not groups[0] or not groups[1]:
        return {**summary, "effect": None, "se": None, "t": None}
    mean = {g: sum(y for y, _, _ in vs) / len(vs) for g, vs in groups.items()}
    b = mean[1] - mean[0]
    n1, n0 = len(groups[1]), len(groups[0])
    xtx_inv = [[1 / n0, -1 / n0], [-1 / n0, 1 / n0 + 1 / n1]]
    meat = [[0.0, 0.0], [0.0, 0.0]]
    clusters: dict[str, list[float]] = {}
    for y, g, c in values:
        resid = y - mean[g]
        s = clusters.setdefault(c, [0.0, 0.0])
        s[0] += resid
        s[1] += resid * g
    for s in clusters.values():
        for i in range(2):
            for j in range(2):
                meat[i][j] += s[i] * s[j]
    G = len(clusters)
    scale = (G / (G - 1)) * ((n - 1) / (n - 2)) if G > 1 and n > 2 else float("nan")
    tmp = [[sum(xtx_inv[i][k] * meat[k][j] for k in range(2)) for j in range(2)] for i in range(2)]
    var = [[sum(tmp[i][k] * xtx_inv[k][j] for k in range(2)) * scale for j in range(2)] for i in range(2)]
    se = math.sqrt(var[1][1]) if var[1][1] > 0 and math.isfinite(var[1][1]) else None
    return {**summary, "effect": b, "se": se, "t": (b / se) if se else None,
            "mean_condition": mean[1], "mean_control": mean[0]}


def decide(milestones: Mapping[str, Mapping[str, Any]], days: int, has_samples: bool) -> str:
    if not has_samples:
        return "candidate"
    reached = [m for m in MILESTONES if str(m) in milestones]
    if any(milestones[str(m)]["against"] for m in reached):
        return "rejected"
    if not reached:
        return "observing"
    last = reached[-1]
    passes = [milestones[str(m)]["pass"] for m in reached]
    two_in_a_row = len(passes) >= 2 and passes[-1] and passes[-2]
    if last == MILESTONES[-1]:
        return "supported" if two_in_a_row else "rejected"
    if two_in_a_row:
        return "supported"
    return "observing" if passes[-1] else "weakened"


def evaluate(hypothesis_entry: Mapping[str, Any], data: Mapping[str, Any], *, now_ms: int,
             superseded: set[str], k: int, prior: Mapping[str, Any] | None = None) -> dict[str, Any]:
    doc = hypothesis_entry["document"]
    since = int(hypothesis_entry["received_at_ms"])
    metric = doc["metric"]
    expected = 1.0 if doc["expected_direction"] == "riskier" else -1.0
    thr = threshold(k)
    base = {"schema": SCHEMA_EVAL, "scoring_rules": SCORING_RULES, "hypothesis_id": doc["id"],
            "submission_id": hypothesis_entry["submission_id"], "registered_at_ms": since, "evaluated_at_ms": now_ms,
            "metric": metric, "expected_direction": doc["expected_direction"], "threshold": thr, "k": k}
    ledger = [r for r in data["ledger"] if int(r["at_ms"]) > since - 2 * DAY_MS]
    samples = assign(doc, ledger, since, now_ms)
    values: list[tuple[float, int, str]] = []
    pending = 0
    for s in samples:
        outcome = data["outcomes"].get(s["contract_key"])
        if outcome is None:
            pending += 1
            continue
        day = _utc(int(outcome["expiry_ms"])).strftime("%Y-%m-%d")
        value = sample_metrics(s["entry"], outcome, data["paths"].get(day)).get(metric)
        if value is None:
            continue
        if metric in BASELINE_FIELD:
            baseline = s["entry"].get(BASELINE_FIELD[metric])
            if isinstance(baseline, bool) or not isinstance(baseline, (int, float)):
                continue
            value = value - baseline
        if metric == "result_btc_at_entry_credit":
            value = -value   # positive = riskier for every metric
        values.append((float(value), 1 if s["group"] == "condition" else 0, day))
    stats = clustered_difference(values)
    days = min(stats["days_condition"], stats["days_control"])
    milestones = dict((prior or {}).get("milestones") or {})
    for m in MILESTONES:
        if days >= m and str(m) not in milestones:
            t = stats.get("t")
            milestones[str(m)] = {"t": t, "effect": stats.get("effect"), "days": days, "at_ms": now_ms,
                                  "pass": t is not None and t * expected >= thr,
                                  "against": t is not None and t * expected <= -thr}
    # milestones are recorded once and never recomputed, so the 120-day outcome is final
    status = decide(milestones, days, bool(values))
    result = {**base, "status_basis": status, "milestones": milestones, "pending_samples": pending,
              "n_samples": len(values), **stats,
              "signed_note_cn": "effect 正值表示条件成立时更危险；触界与亏损两项为相对入场基线的超额之差；样本为（合约, 检查点）"}
    result["status"] = "superseded" if doc["id"] in superseded else status
    return result


def _latest_evaluations(root: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(sorted((root / "validation").glob("*.jsonl"))):
        latest[row["hypothesis_id"]] = row
    return latest


def run(archive: Any, *, now_ms: int) -> dict[str, Any]:
    """Evaluate every registered hypothesis; append one chained line each, rewrite the status table."""
    root = Path(archive.root)
    reg_path = root / "registry" / "hypotheses.jsonl"
    entries = _read_jsonl([reg_path]) if reg_path.exists() else []
    if not entries:
        return {"evaluated": 0}
    superseded = {e["document"].get("supersedes") for e in entries if e["document"].get("supersedes")}
    k = len({e["document"]["id"] for e in entries})
    prior = _latest_evaluations(root)
    data = load_archive(root, min(int(e["received_at_ms"]) for e in entries))
    day = _utc(now_ms).strftime("%Y-%m-%d")
    table = []
    for entry in entries:
        result = evaluate(entry, data, now_ms=now_ms, superseded=superseded, k=k,
                          prior=prior.get(entry["document"]["id"]))
        archive.append_line(f"validation/{day}.jsonl", result, at_ms=now_ms, kind="validation")
        table.append({key: result.get(key) for key in (
            "hypothesis_id", "status", "status_basis", "metric", "expected_direction", "effect", "t", "threshold",
            "days_condition", "days_control", "n_condition", "n_control", "milestones")})
    status_path = root / "validation" / "status.json"
    tmp = status_path.with_name("status.json.tmp")
    tmp.write_text(json.dumps({"schema": "validation_status@0.2", "scoring_rules": SCORING_RULES,
                               "evaluated_at_ms": now_ms, "derived": True, "hypotheses": table},
                              ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp.replace(status_path)
    return {"evaluated": len(table)}
