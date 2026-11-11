"""Selection scorecard (docs/opus/28 step 3, docs/opus/33 §4, docs/opus/34 §2): what taking every
hand of a group would have done, per expiry x side x round. A derived file
(scorecards/selection.json), rebuilt with the other scorecards; read-only on the archive;
descriptive only: nothing here passes or fails anything (selection_rules@0.1).

- Sample: for each settled expiry E and side, the case frozen in the UTC hour of E - 30 h (r1, the
  02:00 round) and of E - 18 h (r2, the 14:00 round): cases/<day>/<HH>_<side>_*.json.gz. Checked
  by the freeze instant (build.built_at_ms within 40 minutes of the hour; the case's asof is the
  quote valuation, often seconds before the hour, and only has to be at most 5 minutes old), the
  case's own expiry, and the case's round when it carries one.
- Verdict: the case's selection.eligible (no hard-rule veto). Cases without a selection (built
  before 0.1.9) or with an empty verdict are undetermined: never rebuilt (docs/opus/34 §6.1).
- Result, at the frozen case's immediate-fill credit after every fee, held to expiry, in
  multiples of the max loss (ML = q x width / S0 - net income, the case's selection.pricing; a case
  without one (built before 0.1.9, or its decision path failed at build) gets the same round_info.max_loss from its own L1 contract and
  economics, and the mark spread from its two legs' mark prices, as the decision path's own fallback;
  max_loss_basis says which):
  result_x = (credit - q x payout) / ML; expected_x = (credit - mark) / ML, what the market's
  estimate implied; luck_x = (mark - q x payout) / ML, so result_x = expected_x + luck_x. The
  payout is per 1 BTC (episode_outcome); credit and mark already carry q.
- Path, from the freeze instant to expiry, with the forward validation's own functions: short
  touched, long crossed, max adverse vs the short strike (%).
- Secondary: the short leg's mark IV change over 6 hours (ledger rows at or before each end,
  at most 40 minutes earlier), positive when IV fell (good for the seller).
- Groups: eligible_all (the main reference: take every hand the hard rules let through),
  all_incl_vetoed (every scored hand; only measures what the hard rules add), vetoed. Standard
  errors are clustered by expiry day (CR1): both sides of one expiry share a settlement.
- Human locks: scored at the lock instant against the latest frozen case of that contract at
  most 65 minutes earlier (cases freeze hourly; case_age_min says how old); listed apart, never
  merged with the rounds.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:
    from . import astra_research_validation_v1 as research_validation
    from . import astra_round_info_v1 as round_info
except ImportError:  # script-style import
    import astra_research_validation_v1 as research_validation
    import astra_round_info_v1 as round_info

RULES = "selection_rules@0.1"
SCHEMA = "selection_scorecard@0.1"
HOUR_MS = 3_600_000
ROUND_HOURS_LEFT = {"r1": 30, "r2": 18}    # astra_round_info_v1: 02:00 and 14:00 UTC for an 08:00 expiry
ENTRY_LOOKBACK_MS = 40 * 60_000           # = astra_research_scorecards_v1.ENTRY_LOOKBACK_MS (tested)
QUOTE_STALE_MS = 5 * 60_000
# a lock is scored against the frozen case before it; cases freeze once an hour, so one cadence plus slack
LOCK_LOOKBACK_MS = 65 * 60_000
IV_HORIZON_MS = 6 * HOUR_MS
STATUSES = ("ok", "missing_case", "expiry_mismatch", "outside_window", "round_mismatch",
            "no_outcome", "no_credit", "no_max_loss", "unreadable")
GROUPS = (("eligible_all", "可接受的全体一律接受", lambda s: s["verdict"] == "eligible"),
          ("all_incl_vetoed", "全部样本一律接受（含被否决与未判定）", lambda s: True),
          ("vetoed", "被硬规则否决的样本", lambda s: s["verdict"] == "vetoed"))
PENDING_GROUPS = (
    ("player_accept", "AI 牌手接受", "第 6 步开跑"), ("player_reject", "AI 牌手拒绝", "第 6 步开跑"),
    ("player_pass", "AI 牌手第一轮过牌", "第 6 步开跑"),
    ("screen_keep", "可接受 ∩ 位置筛维持", "第 7 步开跑"), ("screen_withdraw", "可接受 ∩ 位置筛撤回", "第 7 步开跑"),
    ("mech_distance", "机械对照甲：按短腿距离撤回", "第 7 步开跑"),
    ("mech_oi_short", "机械对照乙：短腿是同侧持仓前三即撤回", "第 4 步位置卡上线"),
    ("player_accept_screen_keep", "牌手接受 ∩ 位置筛维持", "第 7 步开跑"),
    ("player_accept_screen_withdraw", "牌手接受 ∩ 位置筛撤回", "第 7 步开跑"))
NOTE_CN = "描述，不判定；判定只在登记的判定时点进行（33 号 §4.3）。结果按冻结案卷的立即成交、扣全部费用、持有到期，单位是最大损失倍数。"


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _day(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def _lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                row = json.loads(line)
            except ValueError:
                continue   # a line cut by a crash; the card is rebuilt every day
            if isinstance(row, dict):
                rows.append(row)
    return rows


def case_entry(case: Mapping[str, Any], ref: str) -> Mapping[str, Any]:
    at = (case.get("refs") or {}).get(ref)
    if not at:
        return {}
    try:
        return case["layers"][at["layer"]][at["index"]].get("values") or {}
    except (KeyError, IndexError, TypeError):
        return {}


def load_case(path: Path) -> dict[str, Any]:
    return json.loads(gzip.decompress(path.read_bytes()))


# ---------------------------------------------------------------- statistics

def _inverse(m: list[list[float]]) -> list[list[float]] | None:
    n = len(m)
    a = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(m)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(a[r][c]))
        if abs(a[p][c]) < 1e-12:
            return None
        a[c], a[p] = a[p], a[c]
        piv = a[c][c]
        a[c] = [v / piv for v in a[c]]
        for r in range(n):
            if r != c and a[r][c]:
                f = a[r][c]
                a[r] = [v - f * w for v, w in zip(a[r], a[c])]
    return [row[n:] for row in a]


def clustered_ols(y: Sequence[float], X: Sequence[Sequence[float]], cluster: Sequence[str]) -> dict[str, Any]:
    """OLS with CR1 standard errors clustered by `cluster` (expiry day). X includes the intercept
    column. se is None with fewer than two clusters, n <= k or a singular design."""
    n, k = len(y), len(X[0]) if X else 0
    out: dict[str, Any] = {"n": n, "clusters": len(set(cluster)), "beta": None, "se": None}
    if n == 0 or k == 0:
        return out
    xtx = [[sum(X[r][i] * X[r][j] for r in range(n)) for j in range(k)] for i in range(k)]
    inv = _inverse(xtx)
    if inv is None:
        return out
    xty = [sum(X[r][i] * y[r] for r in range(n)) for i in range(k)]
    beta = [sum(inv[i][j] * xty[j] for j in range(k)) for i in range(k)]
    out["beta"] = beta
    G = len(set(cluster))
    if G < 2 or n <= k:
        return out
    scores: dict[str, list[float]] = {}
    for r in range(n):
        u = y[r] - sum(X[r][i] * beta[i] for i in range(k))
        s = scores.setdefault(cluster[r], [0.0] * k)
        for i in range(k):
            s[i] += X[r][i] * u
    meat = [[sum(s[i] * s[j] for s in scores.values()) for j in range(k)] for i in range(k)]
    scale = (G / (G - 1)) * ((n - 1) / (n - k))
    tmp = [[sum(inv[i][m] * meat[m][j] for m in range(k)) for j in range(k)] for i in range(k)]
    var = [[sum(tmp[i][m] * inv[m][j] for m in range(k)) * scale for j in range(k)] for i in range(k)]
    out["se"] = [math.sqrt(var[i][i]) if var[i][i] > 0 else None for i in range(k)]
    return out


def mean_stats(values: Sequence[tuple[float, str]]) -> dict[str, Any]:
    """n, distinct expiry days, mean, sd and the day-clustered standard error of the mean."""
    n = len(values)
    if not n:
        return {"n": 0, "expiry_days": 0, "mean": None, "sd": None, "se_cluster": None}
    ys = [v for v, _ in values]
    mean = sum(ys) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in ys) / (n - 1)) if n > 1 else None
    fit = clustered_ols(ys, [[1.0]] * n, [c for _, c in values])
    return {"n": n, "expiry_days": len({c for _, c in values}), "mean": mean, "sd": sd,
            "se_cluster": fit["se"][0] if fit["se"] else None}


def halves_same_sign(values: Sequence[tuple[float, str]]) -> bool | None:
    """Split by expiry day in order: do the two halves' means have the same sign?"""
    days = sorted({c for _, c in values})
    if len(days) < 2:
        return None
    first = set(days[:len(days) // 2])
    a = [v for v, c in values if c in first]
    b = [v for v, c in values if c not in first]
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    return (ma > 0) == (mb > 0) and ma != 0 and mb != 0


# ---------------------------------------------------------------- samples

def _slot_case(root: Path, slot_ms: int, side: str) -> dict[str, Any] | None:
    """The case of this hour and side. The archive freezes one per hour; should a reset state have
    frozen two, the earliest freeze inside the window wins (file names sort by case_id, not by time).
    Unreadable files count as missing; verify() reports the files themselves."""
    hour = dt.datetime.fromtimestamp(slot_ms / 1000, dt.timezone.utc)
    cases = []
    for path in sorted((root / "cases" / hour.strftime("%Y-%m-%d")).glob(f"{hour.strftime('%H')}_{side}_*.json.gz")):
        try:
            cases.append(load_case(path))
        except Exception:
            continue
    built = (lambda c: (c.get("build") or {}).get("built_at_ms"))
    timed = [c for c in cases if isinstance(built(c), int)]
    inside = [c for c in timed if slot_ms <= built(c) <= slot_ms + ENTRY_LOOKBACK_MS]
    if inside or timed:
        return min(inside or timed, key=built)
    return cases[0] if cases else None


def _ledger_row(rows: Sequence[Mapping[str, Any]], at_ms: int) -> Mapping[str, Any] | None:
    """The latest ledger row at or before at_ms, at most 40 minutes earlier (never a later price)."""
    best = None
    for r in rows:
        t = r.get("at_ms")
        if isinstance(t, int) and t <= at_ms and at_ms - t <= ENTRY_LOOKBACK_MS and (best is None or t > best["at_ms"]):
            best = r
    return best


def score_case(case: Mapping[str, Any], outcome: Mapping[str, Any] | None, path: list | None) -> dict[str, Any]:
    """Result and path readings of one frozen case against its contract's outcome (None = not settled)."""
    contract, econ = case_entry(case, "L1.contract"), case_entry(case, "L1.economics")
    sel = case.get("selection") if isinstance(case.get("selection"), Mapping) else None
    pricing = (sel or {}).get("pricing") or {}
    out: dict[str, Any] = {"result_btc": None, "result_x": None, "expected_x": None, "luck_x": None,
                           "touched_short": None, "crossed_long": None, "max_adverse_pct": None,
                           "max_loss_basis": None}
    if not outcome or outcome.get("status") != "settled" or _num(outcome.get("payout_btc")) is None:
        return {**out, "status": "no_outcome"}
    credit, q = _num(econ.get("net_credit_after_fee_btc")), _num(contract.get("quantity_btc"))
    if credit is None or q is None or q <= 0:
        return {**out, "status": "no_credit"}
    paid = q * float(outcome["payout_btc"])
    out["result_btc"] = credit - paid
    built = (case.get("build") or {}).get("built_at_ms")
    built = built if isinstance(built, int) else int(case["asof_ms"])
    metrics = research_validation.sample_metrics({"at_ms": built, "credit_bid_ask_btc": None}, outcome, path)
    after = research_validation._complete_after(path, built, int(outcome["expiry_ms"]))
    out["touched_short"] = None if metrics["touched_short_after_entry"] is None else metrics["touched_short_after_entry"] > 0
    out["crossed_long"] = (any(research_validation._beyond(outcome["side"], p, float(outcome["long_strike_usd"]))
                               for p in after) if after else None)
    out["max_adverse_pct"] = metrics["max_adverse_after_entry_pct"]
    ml, mark, basis = _num(pricing.get("max_loss_btc")), _num(pricing.get("mark_spread_btc")), "selection"
    if sel is None:   # built before 0.1.9: the same formula on the case's own fields
        width = _num(contract.get("width_usd"))
        if width is None and _num(contract.get("short_strike_usd")) is not None and _num(contract.get("long_strike_usd")) is not None:
            width = abs(float(contract["short_strike_usd"]) - float(contract["long_strike_usd"]))
        ml = round_info.max_loss(q, width, econ.get("valuation_spot_usd"), credit)["btc"]
        short_mark = _num(case_entry(case, "L2.book.short").get("mark_price"))
        long_mark = _num(case_entry(case, "L2.book.long").get("mark_price"))
        mark = (short_mark - long_mark) * q if short_mark is not None and long_mark is not None else None
        basis = "case_l1"
    if ml is None or ml <= 0:
        return {**out, "status": "no_max_loss"}
    out["result_x"], out["max_loss_basis"] = out["result_btc"] / ml, basis
    if mark is not None:
        out["expected_x"] = (credit - mark) / ml
        out["luck_x"] = (mark - paid) / ml
    return {**out, "status": "ok"}


def _empty_sample(expiry_ms: int, side: str, rnd: str) -> dict[str, Any]:
    return {"expiry_day": _day(expiry_ms), "expiry_ms": expiry_ms, "side": side, "round": rnd,
            "case_id": None, "status": "missing_case", "verdict": None, "stored": None,
            "veto_codes": None, "caution_codes": None, "required_edge_bid": None,
            "required_edge_mid": None, "distance_in_moves": None, "iv_change_6h_pp": None,
            "result_btc": None, "result_x": None, "expected_x": None, "luck_x": None,
            "touched_short": None, "crossed_long": None, "max_adverse_pct": None, "max_loss_basis": None}


def round_sample(root: Path, expiry_ms: int, side: str, rnd: str, outcomes_by_case: Mapping[str, Mapping[str, Any]],
                 paths: Mapping[str, list], ledger: Mapping[str, list]) -> dict[str, Any]:
    slot = expiry_ms - ROUND_HOURS_LEFT[rnd] * HOUR_MS
    sample = _empty_sample(expiry_ms, side, rnd)
    case = _slot_case(root, slot, side)
    if case is None:
        return sample
    contract = case_entry(case, "L1.contract")
    build = case.get("build") or {}
    built, asof = build.get("built_at_ms"), case.get("asof_ms")
    sel = case.get("selection") if isinstance(case.get("selection"), Mapping) else None
    sample.update(case_id=case.get("case_id"), stored=build.get("stored", True))
    if contract.get("expiry_ms") != expiry_ms:
        return {**sample, "status": "expiry_mismatch"}
    if (not isinstance(built, int) or not slot <= built <= slot + ENTRY_LOOKBACK_MS
            or not isinstance(asof, int) or built - asof > QUOTE_STALE_MS):
        return {**sample, "status": "outside_window"}
    # the round is the hour's (round_info.round_number on the freeze instant); a case carrying another
    # current round was read for another round and is not this hand
    current = ((sel or {}).get("round") or {}).get("current")
    if f"r{round_info.round_number(built, expiry_ms)}" != rnd or (current is not None and f"r{current}" != rnd):
        return {**sample, "status": "round_mismatch"}
    eligible = (sel or {}).get("eligible")
    sample["verdict"] = "undetermined" if eligible is None else "eligible" if eligible else "vetoed"
    if sel:
        pricing = sel.get("pricing") or {}
        sample.update(veto_codes=[v.get("code") for v in sel.get("vetoes") or []],
                      caution_codes=[c.get("code") for c in sel.get("cautions") or []],
                      required_edge_bid=pricing.get("required_edge_bid"),
                      required_edge_mid=pricing.get("required_edge_mid"))
    key = f"{contract.get('short_instrument')}|{contract.get('long_instrument')}"
    rows = ledger.get(key) or []
    row = _ledger_row(rows, built)
    sample["distance_in_moves"] = (row or {}).get("distance_in_moves")
    iv = (lambda r: _num(((r or {}).get("short") or {}).get("mark_iv")))
    iv0, iv1 = iv(row), iv(_ledger_row(rows, built + IV_HORIZON_MS))
    if iv0 is not None and iv1 is not None and built + IV_HORIZON_MS < expiry_ms:
        sample["iv_change_6h_pp"] = iv0 - iv1
    scored = score_case(case, outcomes_by_case.get(case.get("case_id")), paths.get(_day(expiry_ms)))
    return {**sample, **scored}


# ---------------------------------------------------------------- groups

def _group(samples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    def stats(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        x = mean_stats([(r["result_x"], r["expiry_day"]) for r in rows])
        share = (lambda f: sum(1 for r in rows if r.get(f)) / len([r for r in rows if r.get(f) is not None])
                 if any(r.get(f) is not None for r in rows) else None)
        mean = (lambda f: (lambda v: sum(v) / len(v) if v else None)([r[f] for r in rows if r.get(f) is not None]))
        return {"n": x["n"], "expiry_days": x["expiry_days"], "mean_x": x["mean"], "sd_x": x["sd"],
                "se_cluster_x": x["se_cluster"], "mean_expected_x": mean("expected_x"), "mean_luck_x": mean("luck_x"),
                "loss_share": (sum(1 for r in rows if r["result_x"] < 0) / len(rows)) if rows else None,
                "touched_short_share": share("touched_short"), "crossed_long_share": share("crossed_long")}
    return {**stats(samples),
            "by_round": {r: stats([s for s in samples if s["round"] == r]) for r in ROUND_HOURS_LEFT},
            "by_side": {side: stats([s for s in samples if s["side"] == side]) for side in ("put", "call")}}


def _secondary(scored: Sequence[Mapping[str, Any]], field: str) -> dict[str, Any]:
    values = [(s[field], s["expiry_day"]) for s in scored if s.get(field) is not None]
    st = mean_stats(values)
    return {"n": st["n"], "expiry_days": st["expiry_days"], "mean": st["mean"],
            "t": (st["mean"] / st["se_cluster"]) if st["se_cluster"] else None,
            "halves_same_sign": halves_same_sign(values)}


def human_locks(root: Path, outcomes_by_key: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Each lock_created decision, scored against the latest frozen case of its contract at most 65
    minutes before the lock. The outcome lists every frozen case of a contract with its quote time
    (asof), so the window is on asof, not on the freeze instant the rounds use: either way the price
    is from before the lock. A record that cannot be read is listed as unreadable."""
    items = []
    for path in sorted((root / "decisions").glob("*.jsonl")):
        for d in _lines(path):
            if d.get("source") != "lock" or d.get("event") != "lock_created" or d.get("actor", "human") != "human":
                continue
            at, key = d.get("at_ms"), d.get("contract_key")
            item: dict[str, Any] = {"ref_id": d.get("ref_id"), "at_ms": at, "contract_key": key, "side": d.get("side"),
                                    "case_id": None, "status": "no_outcome", "case_age_min": None,
                                    "result_btc": None, "result_x": None}
            try:
                items.append(_score_lock(root, item, outcomes_by_key.get(key), at))
            except Exception:
                items.append({**item, "status": "unreadable"})
    return items


def _score_lock(root: Path, item: dict[str, Any], outcome: Mapping[str, Any] | None, at: Any) -> dict[str, Any]:
    if outcome is None or outcome.get("status") != "settled" or not isinstance(at, int):
        return item
    frozen = [c for c in outcome.get("cases") or [] if isinstance(c, Mapping) and isinstance(c.get("asof_ms"), int)
              and c["asof_ms"] <= at and at - c["asof_ms"] <= LOCK_LOOKBACK_MS]
    if not frozen:
        return {**item, "status": "no_case_at_lock"}
    pick = max(frozen, key=lambda c: c["asof_ms"])
    try:
        case = load_case(root / pick["path"])
    except Exception:
        return {**item, "status": "no_case_at_lock"}
    scored = score_case(case, outcome, None)
    return {**item, "case_id": case.get("case_id"), "status": scored["status"],
            "case_age_min": round((at - pick["asof_ms"]) / 60_000, 1),
            "result_btc": scored["result_btc"], "result_x": scored["result_x"]}


def build(root: str | Path, *, now_ms: int) -> dict[str, Any]:
    root = Path(root)
    outcomes = [o for p in sorted((root / "outcomes").glob("*.jsonl")) for o in _lines(p)]
    by_case = {c["case_id"]: o for o in outcomes for c in o.get("cases") or []
               if isinstance(c, Mapping) and c.get("case_id")}
    by_key = {o["contract_key"]: o for o in outcomes if o.get("contract_key")}
    expiries = sorted({int(o["expiry_ms"]) for o in outcomes if isinstance(o.get("expiry_ms"), int)
                       and o["expiry_ms"] <= now_ms})
    paths, ledger = {}, {}
    for e in expiries:
        p = root / "paths" / f"{_day(e)}.json.gz"
        if p.exists():
            try:
                paths[_day(e)] = json.loads(gzip.decompress(p.read_bytes()))["points"]
            except Exception:
                pass
    days = {_day(e - h * HOUR_MS + d) for e in expiries for h in ROUND_HOURS_LEFT.values()
            for d in (-ENTRY_LOOKBACK_MS, 0, IV_HORIZON_MS)}
    for p in sorted(root / "ledger" / f"{d}.jsonl" for d in days):
        for r in _lines(p):
            if r.get("contract_key"):
                ledger.setdefault(r["contract_key"], []).append(r)
    samples = []
    for e in expiries:
        for side in ("put", "call"):
            for rnd in ROUND_HOURS_LEFT:
                try:   # one bad record never takes the card down
                    samples.append(round_sample(root, e, side, rnd, by_case, paths, ledger))
                except Exception:
                    samples.append({**_empty_sample(e, side, rnd), "status": "unreadable"})
    scored = [s for s in samples if s["status"] == "ok"]
    by_status = {k: sum(1 for s in samples if s["status"] == k) for k in STATUSES}
    by_verdict = {k: sum(1 for s in samples if s["verdict"] == k) for k in ("eligible", "vetoed", "undetermined")}
    locks = human_locks(root, by_key)
    return {"schema": SCHEMA, "rules": RULES, "derived": True, "built_at_ms": now_ms,
            "coverage": {"expiry_days": len(expiries), "samples": len(samples), "by_status": by_status,
                         "by_verdict": by_verdict},
            "groups": [{"id": gid, "title_cn": title, **_group([s for s in scored if keep(s)])}
                       for gid, title, keep in GROUPS],
            "pending_groups": [{"id": gid, "title_cn": title, "starts_at": when} for gid, title, when in PENDING_GROUPS],
            "human_locks": {"n": len(locks), "scored": sum(1 for i in locks if i["status"] == "ok"), "items": locks},
            "secondary": {"iv_change_6h": _secondary(scored, "iv_change_6h_pp"),
                          "result_x": _secondary(scored, "result_x")},
            "samples": samples, "note_cn": NOTE_CN}


# ---------------------------------------------------------------- text

STATUS_CN = {"ok": "可计分", "missing_case": "该小时没有案卷", "expiry_mismatch": "案卷不是这一期",
             "outside_window": "冻结时刻超出容差或报价陈旧", "round_mismatch": "轮次不符",
             "no_outcome": "未结算", "no_credit": "无扣费净信用", "no_max_loss": "无最大损失",
             "unreadable": "记录无法读取"}
VERDICT_CN = {"eligible": "可接受", "vetoed": "被否决", "undetermined": "未判定（无否决结论）"}


def summary_text(card: Mapping[str, Any]) -> str:
    def f(v: Any, nd: int = 2) -> str:
        return "—" if v is None else f"{v:+.{nd}f}"

    def pct(v: Any) -> str:
        return "—" if v is None else f"{v * 100:.0f}%"

    cov = card["coverage"]
    lines = [f"选择记分卡（{card['rules']}）", NOTE_CN, "",
             f"到期日 {cov['expiry_days']} 个，样本 {cov['samples']} 个（到期日 × 侧 × 两轮）。",
             "样本状态：" + "，".join(f"{STATUS_CN[k]} {v}" for k, v in cov["by_status"].items() if v),
             "判定：" + "，".join(f"{VERDICT_CN[k]} {v}" for k, v in cov["by_verdict"].items() if v), ""]
    for g in card["groups"]:
        lines.append(f"【{g['title_cn']}】n={g['n']}，到期日 {g['expiry_days']} 个")
        if g["n"]:
            lines.append(f"  平均 {f(g['mean_x'])} 倍最大损失（聚类标准误 {f(g['se_cluster_x'])}），"
                         f"其中市场估价预期 {f(g['mean_expected_x'])}、运气 {f(g['mean_luck_x'])}；"
                         f"亏损局 {pct(g['loss_share'])}，触及短腿 {pct(g['touched_short_share'])}，"
                         f"越过保护腿 {pct(g['crossed_long_share'])}")
            for label, part in (("第一轮", g["by_round"]["r1"]), ("第二轮", g["by_round"]["r2"]),
                                ("Put", g["by_side"]["put"]), ("Call", g["by_side"]["call"])):
                if part["n"]:
                    lines.append(f"  {label}：n={part['n']}，平均 {f(part['mean_x'])}，亏损局 {pct(part['loss_share'])}")
    lines += ["", "尚未开始的组：" + "、".join(f"{p['title_cn']}（{p['starts_at']}）" for p in card["pending_groups"])]
    hl = card["human_locks"]
    lines.append(f"人的锁定：{hl['n']} 次，可计分 {hl['scored']} 次（按锁定时刻单独计分，不并入轮次）。")
    iv = card["secondary"]["iv_change_6h"]
    lines.append(f"次级指标·短腿隐含波动 6 小时变化（下降为正）：n={iv['n']}，平均 {f(iv['mean'])} 个百分点，"
                 f"t={f(iv['t'])}，前后两半同号：{'—' if iv['halves_same_sign'] is None else '是' if iv['halves_same_sign'] else '否'}")
    return "\n".join(lines) + "\n"
