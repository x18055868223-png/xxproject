"""Scorecards: the two short feedback lines of the research loop (docs/opus/08 §6, 2026-10-20).

Both are derived files, rebuilt from the chained archive once a day after the validation; no
judgment is made here beyond fixed arithmetic (scorecard_rules@0.1).

- View scorecard (scorecards/views.json): every registered episode_view is joined to the
  settled outcome of its contract. The entry is the latest ledger row of that contract at or
  before the view's server registration (at most 40 minutes earlier) gives the prices and
  baselines; path outcomes are measured from the registration itself, with the same metrics
  as the forward validation, and touch / loss are
  also given as excess over the entry baseline (p_touch_short, p_loss). Views are grouped by
  risk_vs_baseline, grade, lean and confidence; a Spearman rank correlation between the
  ordinal level and each metric is signed so that positive means the researcher's ordering
  matched what happened. Samples within one expiry day share a settlement, so the
  correlation is descriptive; `days` says how many independent settlements stand behind it.
- Inquiry scorecard (scorecards/inquiry.json): every inquiry_review item in registered
  postmortems is joined to the question it names (case_id + question_id) in inquiry/. Counts
  of verdicts by prompt version, question type and priority, and review coverage: questions
  asked on cases whose contract has settled versus questions reviewed. A later review of the
  same question replaces an earlier one.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

if __package__:
    from . import astra_research_validation_v1 as research_validation
    from . import astra_research_intraday_exit_v1 as intraday_exit
else:
    import astra_research_validation_v1 as research_validation
    import astra_research_intraday_exit_v1 as intraday_exit

SCORECARD_RULES = "scorecard_rules@0.1"
SCHEMA_VIEWS = "view_scorecard@0.1"
SCHEMA_INQUIRY = "inquiry_scorecard@0.1"
ENTRY_LOOKBACK_MS = 40 * 60_000      # ledger rows are written at least every 30 minutes
MIN_DAYS_FOR_READING = 30
RISK_ORDER = {"much_safer": -2, "safer": -1, "in_line": 0, "riskier": 1, "much_riskier": 2}
GRADE_ORDER = {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5}          # U is excluded from the ordering
LEAN_ORDER = {"underwrite": -1, "watch": 0, "decline": 1}
CONFIDENCE = ("high", "medium", "low")
# one researcher, one name: early submissions were signed with the model name
AGENT_ALIASES = {"GrokBot": "Cici", "grok-bot": "Cici", "grokbot": "Cici"}
VERDICTS = ("pointed_to_realized_risk", "not_material", "wrong_premise", "unclear")
# metric -> sign that turns "riskier" into positive
METRICS = {"touched_short_after_entry": 1.0, "excess_touch": 1.0, "loss_at_expiry": 1.0, "excess_loss": 1.0,
           "max_adverse_after_entry_pct": 1.0, "settle_vs_short_pct": 1.0, "result_btc_at_entry_credit": -1.0}


def _utc_day(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def _lines(path: Path) -> list[dict[str, Any]]:
    """Rows of a JSONL file; an unreadable line (e.g. truncated by a crash) is skipped, since
    the cards are derived and rebuilt every day."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def _many(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        rows += _lines(path)
    return rows


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _write(path: Path, body: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx, vy = sum((a - mx) ** 2 for a in rx), sum((b - my) ** 2 for b in ry)
    return cov / (vx * vy) ** 0.5 if vx > 0 and vy > 0 else None


# ---------------------------------------------------------------- views

def _agent(doc: Mapping[str, Any]) -> Any:
    name = (doc.get("agent") or {}).get("name")
    return AGENT_ALIASES.get(name, name) or "unknown"


def score_view(entry: Mapping[str, Any], ledger_by_contract: Mapping[str, list[Mapping[str, Any]]],
               outcomes: Mapping[str, Mapping[str, Any]], unavailable: set[str],
               paths: Mapping[str, list[list[float]]]) -> dict[str, Any]:
    doc = entry["document"]
    view = doc.get("view") or {}
    key, registered = doc["contract_key"], int(entry["received_at_ms"])
    row: dict[str, Any] = {
        "view_id": doc["id"], "submission_id": entry["submission_id"], "agent": _agent(doc),
        "agent_signed": (doc.get("agent") or {}).get("name"),
        "contract_key": key, "registered_at_ms": registered,
        **{k: view.get(k) for k in ("risk_vs_baseline", "grade", "lean", "confidence")}}
    outcome = outcomes.get(key)
    if outcome is None:
        row["status"] = "outcome_unavailable" if key in unavailable else "pending_outcome"
        return row
    expiry = int(outcome["expiry_ms"])
    row.update({"side": outcome.get("side"), "expiry_day": _utc_day(expiry)})
    if registered >= expiry:
        row["status"] = "after_expiry"
        return row
    before = [r for r in ledger_by_contract.get(key, []) if int(r["at_ms"]) <= registered]
    if not before or registered - int(before[-1]["at_ms"]) > ENTRY_LOOKBACK_MS:
        row["status"] = "no_entry_row"
        return row
    entry_row = before[-1]
    # prices and baselines from the entry row; the path is measured from registration, so a
    # touch that had already happened when the view was registered is not counted
    metrics = research_validation.sample_metrics({**entry_row, "at_ms": registered}, outcome,
                                                 paths.get(row["expiry_day"]))
    touch, p_touch = metrics.get("touched_short_after_entry"), _number(entry_row.get("p_touch_short"))
    loss, p_loss = metrics.get("loss_at_expiry"), _number(entry_row.get("p_loss"))
    metrics["excess_touch"] = touch - p_touch if touch is not None and p_touch is not None else None
    metrics["excess_loss"] = loss - p_loss if loss is not None and p_loss is not None else None
    row.update({"status": "scored", "entry_row_at_ms": int(entry_row["at_ms"]),
                "entry_gap_ms": registered - int(entry_row["at_ms"]),
                "p_touch_short": p_touch, "p_loss": p_loss, "metrics": metrics})
    return row


def _group_means(rows: list[Mapping[str, Any]], field: str, order: Iterable[str]) -> list[dict[str, Any]]:
    out = []
    for level in order:
        members = [r for r in rows if r.get(field) == level]
        if not members:
            continue
        means = {}
        for metric in METRICS:
            values = [r["metrics"][metric] for r in members if r["metrics"].get(metric) is not None]
            means[metric] = sum(values) / len(values) if values else None
        out.append({"level": level, "n": len(members), "days": len({r["expiry_day"] for r in members}),
                    "contracts": len({r["contract_key"] for r in members}), "mean": means})
    return out


def _ordering(rows: list[Mapping[str, Any]], field: str, codes: Mapping[str, int]) -> dict[str, Any]:
    usable = [r for r in rows if r.get(field) in codes]
    result: dict[str, Any] = {"n": len(usable), "days": len({r["expiry_day"] for r in usable})}
    for metric, sign in METRICS.items():
        pairs = [(float(codes[r[field]]), sign * r["metrics"][metric]) for r in usable
                 if r["metrics"].get(metric) is not None]
        rho = spearman([a for a, _ in pairs], [b for _, b in pairs])
        result[metric] = {"spearman": rho, "n": len(pairs)}
    result["enough_days"] = result["days"] >= MIN_DAYS_FOR_READING
    return result


def _outcomes(root: Path) -> list[dict[str, Any]]:
    return _many(sorted((root / "outcomes").glob("*.jsonl")))


def view_scorecard(root: Path, *, now_ms: int, all_outcomes: list[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    entries = _lines(root / "registry" / "episode_views.jsonl")
    body: dict[str, Any] = {"schema": SCHEMA_VIEWS, "rules": SCORECARD_RULES, "built_at_ms": now_ms, "derived": True,
                            "note_cn": "研究员判断与到期结果的对照；每份判断从其登记时刻起计；同一到期日共享交割，"
                                       "相关系数仅作描述，days 为独立交割日数。正的相关系数表示研究员的排序与结果一致。"}
    if not entries:
        return {**body, "views": [], "by": {}, "ordering": {}, "counts": {}}
    all_outcomes = _outcomes(root) if all_outcomes is None else all_outcomes
    outcomes = {o["contract_key"]: o for o in all_outcomes if o.get("status") == "settled"}
    unavailable = {o["contract_key"] for o in all_outcomes if o.get("status") != "settled"}
    keys = {e["document"]["contract_key"] for e in entries}
    # only the ledger days that can hold an entry row, and only the paths of viewed expiries
    ledger_days = set()
    for e in entries:
        if e["document"]["contract_key"] in outcomes:
            at = int(e["received_at_ms"])
            ledger_days |= {_utc_day(at), _utc_day(at - ENTRY_LOOKBACK_MS)}
    by_contract: dict[str, list[Mapping[str, Any]]] = {}
    for r in _many(sorted(root / "ledger" / f"{d}.jsonl" for d in ledger_days)):
        if r.get("contract_key") in keys:
            by_contract.setdefault(r["contract_key"], []).append(r)
    for rows in by_contract.values():
        rows.sort(key=lambda r: int(r["at_ms"]))
    days = {_utc_day(int(outcomes[k]["expiry_ms"])) for k in keys if k in outcomes}
    paths = {}
    for day in days:
        path = root / "paths" / f"{day}.json.gz"
        if path.exists():
            paths[day] = json.loads(gzip.decompress(path.read_bytes()))["points"]
    views = [score_view(e, by_contract, outcomes, unavailable, paths) for e in entries]
    scored = [v for v in views if v["status"] == "scored"]
    counts: dict[str, int] = {}
    for v in views:
        counts[v["status"]] = counts.get(v["status"], 0) + 1
    return {**body, "views": views, "counts": counts,
            "by": {"agent": _group_means(scored, "agent", sorted({v["agent"] for v in scored})),
                   "risk_vs_baseline": _group_means(scored, "risk_vs_baseline", RISK_ORDER),
                   "grade": _group_means(scored, "grade", [*GRADE_ORDER, "U"]),
                   "lean": _group_means(scored, "lean", LEAN_ORDER),
                   "confidence": _group_means(scored, "confidence", CONFIDENCE)},
            "ordering": {"risk_vs_baseline": _ordering(scored, "risk_vs_baseline", RISK_ORDER),
                         "grade": _ordering(scored, "grade", GRADE_ORDER),
                         "lean": _ordering(scored, "lean", LEAN_ORDER)}}


# ---------------------------------------------------------------- inquiry

def _inquiries(root: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "inquiry").glob("*/*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        questions = {q.get("id"): q for q in ((doc.get("sheet") or {}).get("questions") or [])
                     if isinstance(q, Mapping) and q.get("id")}
        out[str(doc.get("case_id"))] = {"prompt_version": doc.get("prompt_version"), "status": doc.get("status"),
                                        "file": str(path.relative_to(root)).replace("\\", "/"),
                                        "questions": questions}
    return out


def _bucket(counter: dict[str, dict[str, Any]], key: str, verdict: str) -> None:
    slot = counter.setdefault(key, {"reviewed": 0, **{v: 0 for v in VERDICTS}})
    slot["reviewed"] += 1
    slot[verdict] += 1


def _rates(counter: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    out = {}
    for key, slot in sorted(counter.items()):
        decided = slot["reviewed"] - slot["unclear"]
        out[key] = {**slot, "pointed_rate": slot["pointed_to_realized_risk"] / decided if decided else None,
                    "wrong_premise_rate": slot["wrong_premise"] / decided if decided else None}
    return out


def inquiry_scorecard(root: Path, *, now_ms: int, all_outcomes: list[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    inquiries = _inquiries(root)
    all_outcomes = _outcomes(root) if all_outcomes is None else all_outcomes
    settled_cases = {c.get("case_id") for o in all_outcomes
                     if o.get("status") == "settled" for c in (o.get("cases") or [])}
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in sorted(_lines(root / "registry" / "postmortems.jsonl"), key=lambda e: int(e["received_at_ms"])):
        for item in entry["document"].get("inquiry_review") or []:
            latest[(str(item["case_id"]), str(item["question_id"]))] = {
                "verdict": item["verdict"], "postmortem_id": entry["document"]["id"],
                "reviewed_at_ms": int(entry["received_at_ms"]), "note_cn": item.get("note_cn")}
    rows, unmatched = [], []
    by_version: dict[str, dict[str, Any]] = {}
    by_type: dict[str, dict[str, Any]] = {}
    by_priority: dict[str, dict[str, Any]] = {}
    for (case_id, qid), review in sorted(latest.items()):
        run = inquiries.get(case_id)
        question = (run or {}).get("questions", {}).get(qid)
        if question is None:
            unmatched.append({"case_id": case_id, "question_id": qid, **review})
            continue
        version = str(run["prompt_version"])
        rows.append({"case_id": case_id, "question_id": qid, "prompt_version": version, "type": question.get("type"),
                     "priority": question.get("priority"), "answer_source": question.get("answer_source"),
                     "bears_on": question.get("bears_on"), **review})
        _bucket(by_version, version, review["verdict"])
        _bucket(by_type, f"{version}|{question.get('type')}", review["verdict"])
        _bucket(by_priority, f"{version}|{question.get('priority')}", review["verdict"])
    coverage: dict[str, dict[str, Any]] = {}
    reviewed_keys = {(r["case_id"], r["question_id"]) for r in rows}
    for case_id, run in inquiries.items():
        if case_id not in settled_cases:
            continue
        slot = coverage.setdefault(str(run["prompt_version"]), {"cases": 0, "cases_without_questions": 0,
                                                                "questions_asked": 0, "questions_reviewed": 0})
        if not run["questions"]:
            slot["cases_without_questions"] += 1   # the inquirer run failed or returned no sheet
            continue
        slot["cases"] += 1
        slot["questions_asked"] += len(run["questions"])
        slot["questions_reviewed"] += sum(1 for q in run["questions"] if (case_id, q) in reviewed_keys)
    for slot in coverage.values():
        slot["coverage"] = slot["questions_reviewed"] / slot["questions_asked"] if slot["questions_asked"] else None
    return {"schema": SCHEMA_INQUIRY, "rules": SCORECARD_RULES, "built_at_ms": now_ms, "derived": True,
            "note_cn": "复盘对提问者问题的逐题评定汇总；pointed_rate = 指向已兑现风险的问题 ÷（已评定 − unclear）；"
                       "coverage = 已结算合约案卷上的问题中被复盘评定的比例",
            "by_prompt_version": _rates(by_version), "by_type": _rates(by_type), "by_priority": _rates(by_priority),
            "coverage": coverage, "reviews": rows, "unmatched": unmatched}


def run(root: str | Path, *, now_ms: int) -> dict[str, Any]:
    root = Path(root)
    all_outcomes = _outcomes(root)
    views = view_scorecard(root, now_ms=now_ms, all_outcomes=all_outcomes)
    _write(root / "scorecards" / "views.json", views)
    questions = inquiry_scorecard(root, now_ms=now_ms, all_outcomes=all_outcomes)
    _write(root / "scorecards" / "inquiry.json", questions)
    report = {"views_scored": views["counts"].get("scored", 0), "reviews": len(questions["reviews"]),
              "unmatched_reviews": len(questions["unmatched"])}
    try:   # the research study never takes the two cards down with it
        study = intraday_exit.build(root, now_ms=now_ms)
        _write(root / "scorecards" / "intraday_exit.json", study)
        report["intraday_exit_rows"] = study["overall"]["rows"]
    except Exception as exc:
        report["intraday_exit_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        (root / "scorecards" / "intraday_exit.json").unlink(missing_ok=True)   # never leave a stale card
    return report
