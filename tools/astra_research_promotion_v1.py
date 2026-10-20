"""Promotion E4: supported hypotheses become program readings only with the user's approval
(docs/opus/08 §6, 2026-10-20).

- candidates: hypotheses whose latest evaluation is supported and that are not promoted yet;
  rewritten after each daily validation (derived, not chained).
- promote / retire: run by the operator only after the user approves in chat. Each action is
  an append-only, chained line in promotions/actions.jsonl that copies the hypothesis's
  condition and the evaluation it rested on, and bumps the research protocol version in
  protocols/versions.jsonl. Only a final (120-day) supported result can be promoted unless
  provisional is explicitly allowed; the reading is then marked provisional.
- readings: the active promoted conditions are evaluated on the current candidate with the
  same ledger-row fields the validation used, and placed in the case file as
  L8.research_readings. Nothing else in production changes automatically.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

if __package__:
    from . import astra_research_inbox_v1 as research_inbox
    from . import astra_research_validation_v1 as research_validation
else:
    import astra_research_inbox_v1 as research_inbox
    import astra_research_validation_v1 as research_validation

SCHEMA_ACTION = "promotion_action@0.1"
SCHEMA_READING = "research_reading@0.1"
PROMOTE_AS = {"program_reading"}
VERSION = re.compile(r"^research_protocol@(\d+)\.(\d+)$")
STATES_MAX_GAP_MS = 30 * 60_000   # the validation's checkpoint lookback
ACTIONS = "promotions/actions.jsonl"


def _lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def actions(root: str | Path) -> list[dict[str, Any]]:
    if not Path(root).is_dir():
        raise FileNotFoundError(f"research archive not found: {root}")
    return _lines(Path(root) / ACTIONS)


def active_readings(root: str | Path) -> list[dict[str, Any]]:
    current: dict[str, dict[str, Any]] = {}
    for action in actions(root):
        if action["action"] == "promote":
            current[action["reading_id"]] = action
        elif action["action"] == "retire":
            current.pop(action["reading_id"], None)
    return sorted(current.values(), key=lambda a: a["reading_id"])


def _latest_evaluations(root: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "validation").glob("*.jsonl")):
        for row in _lines(path):
            latest[row["hypothesis_id"]] = row
    return latest


def _hypotheses(root: Path) -> dict[str, dict[str, Any]]:
    return {e["document"]["id"]: e for e in _lines(root / "registry" / "hypotheses.jsonl")}


def write_candidates(root: str | Path, *, now_ms: int) -> dict[str, Any]:
    root = Path(root)
    active = active_readings(root)
    promoted = {a["hypothesis_id"] for a in active}
    latest = _latest_evaluations(root)
    # an active reading whose hypothesis is no longer supported needs the user's review
    needs_review = [{"reading_id": a["reading_id"], "hypothesis_id": a["hypothesis_id"],
                     "current_status": (latest.get(a["hypothesis_id"]) or {}).get("status")}
                    for a in active if (latest.get(a["hypothesis_id"]) or {}).get("status") != "supported"]
    rows = []
    for hid, ev in latest.items():
        if ev.get("status") == "supported" and hid not in promoted:
            final = str(research_validation.MILESTONES[-1]) in (ev.get("milestones") or {})
            rows.append({"hypothesis_id": hid, "final": final, "metric": ev.get("metric"),
                         "expected_direction": ev.get("expected_direction"), "effect": ev.get("effect"),
                         "t": ev.get("t"), "threshold": ev.get("threshold"), "milestones": ev.get("milestones"),
                         "evaluated_at_ms": ev.get("evaluated_at_ms")})
    body = {"schema": "promotion_candidates@0.1", "built_at_ms": now_ms, "derived": True,
            "note_cn": "经前向验证支持、尚未晋升的假设；晋升须用户批准。needs_review 为已晋升但当前不再是支持状态的读数",
            "candidates": rows, "needs_review": needs_review}
    path = root / "promotions" / "candidates.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("candidates.json.tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    return {"candidates": len(rows)}


def next_protocol_version(root: str | Path) -> str:
    """Bump the minor of the highest registered research_protocol@MAJOR.MINOR."""
    best = (1, 0)
    for row in _lines(Path(root) / "protocols" / "versions.jsonl"):
        if row.get("component") != "research_protocol":
            continue
        match = VERSION.match(str(row.get("version")))
        if not match:
            raise ValueError(f"unparsable research_protocol version {row.get('version')!r}")
        best = max(best, (int(match.group(1)), int(match.group(2))))
    return f"research_protocol@{best[0]}.{best[1] + 1}"


def _protocol_bump(archive: Any, now_ms: int, note_cn: str, version: str) -> str:
    archive.append_line("protocols/versions.jsonl", {
        "schema": "protocol_version@0.1", "at_ms": now_ms, "component": "research_protocol", "version": version,
        "source": "approved_by_user", "note_cn": note_cn}, at_ms=now_ms, kind="protocol")
    return version


def promote(archive: Any, hypothesis_id: str, *, now_ms: int, label_cn: str, approval_cn: str,
            promoted_as: str = "program_reading", allow_provisional: bool = False) -> dict[str, Any]:
    root = Path(archive.root)
    if promoted_as not in PROMOTE_AS:
        raise ValueError(f"promoted_as must be one of {sorted(PROMOTE_AS)}")
    if not label_cn.strip() or not approval_cn.strip():
        raise ValueError("label_cn and approval_cn (what the user approved, in their words) are required")
    hyp = _hypotheses(root).get(hypothesis_id)
    ev = _latest_evaluations(root).get(hypothesis_id)
    if hyp is None or ev is None:
        raise ValueError("unknown or never evaluated hypothesis")
    if ev.get("status") != "supported":
        raise ValueError(f"only a supported hypothesis can be promoted (status {ev.get('status')})")
    final = str(research_validation.MILESTONES[-1]) in (ev.get("milestones") or {})
    if not final and not allow_provisional:
        raise ValueError("supported is provisional (before 120 days); pass allow_provisional to promote anyway")
    if any(a["hypothesis_id"] == hypothesis_id for a in active_readings(root)):
        raise ValueError("already promoted")
    reading_id = f"R-{1 + len([a for a in actions(root) if a['action'] == 'promote']):03d}"
    doc = hyp["document"]
    version = next_protocol_version(root)
    action = {"schema": SCHEMA_ACTION, "action": "promote", "at_ms": now_ms, "reading_id": reading_id,
              "hypothesis_id": hypothesis_id, "submission_id": hyp["submission_id"], "promoted_as": promoted_as,
              "provisional": not final, "label_cn": label_cn, "approval_cn": approval_cn,
              "condition": doc["condition"], "metric": doc["metric"], "expected_direction": doc["expected_direction"],
              "statement_cn": doc.get("statement_cn"),
              "evaluation": {k: ev.get(k) for k in ("evaluated_at_ms", "scoring_rules", "status", "status_basis",
                                                    "effect", "se", "t",
                                                    "threshold", "k", "milestones", "days_condition", "days_control",
                                                    "n_condition", "n_control")},
              "protocol_version": version}
    # the action first, then the version line that names it
    archive.append_line(ACTIONS, action, at_ms=now_ms, kind="promotion")
    _protocol_bump(archive, now_ms, f"晋升 {reading_id}（{hypothesis_id}）：{label_cn}", version)
    return action


def retire(archive: Any, reading_id: str, *, now_ms: int, reason_cn: str) -> dict[str, Any]:
    root = Path(archive.root)
    current = {a["reading_id"]: a for a in active_readings(root)}
    if reading_id not in current:
        raise ValueError("no active reading with that id")
    if not reason_cn.strip():
        raise ValueError("reason_cn is required")
    version = next_protocol_version(root)
    action = {"schema": SCHEMA_ACTION, "action": "retire", "at_ms": now_ms, "reading_id": reading_id,
              "hypothesis_id": current[reading_id]["hypothesis_id"], "reason_cn": reason_cn,
              "protocol_version": version}
    archive.append_line(ACTIONS, action, at_ms=now_ms, kind="promotion")
    _protocol_bump(archive, now_ms, f"撤销 {reading_id}：{reason_cn}", version)
    return action


def evaluate_readings(readings: list[Mapping[str, Any]], row: Mapping[str, Any],
                      states_at_ms: int | None = None) -> dict[str, Any]:
    """Evaluate active readings on one ledger-shaped row of the current candidate. The program
    states may come from a later read than the quote; the gap is reported and, beyond the
    validation's 30-minute lookback, flagged."""
    gap = (states_at_ms - int(row["at_ms"])) if isinstance(states_at_ms, int) else None
    out = []
    context = {"ledger": row, "calendar": research_validation.calendar_fields(row)}
    for reading in readings:
        verdict = research_inbox.evaluate_condition(reading["condition"], context)
        ev = reading.get("evaluation") or {}
        out.append({"schema": SCHEMA_READING, "reading_id": reading["reading_id"], "label_cn": reading["label_cn"],
                    "applies": verdict, "provisional": reading.get("provisional"),
                    "direction_if_applies": reading["expected_direction"], "metric": reading["metric"],
                    "effect": ev.get("effect"), "t": ev.get("t"), "protocol_version": reading.get("protocol_version"),
                    "meaning_cn": ("条件成立：历史前向验证显示此类情形风险相对对照组" +
                                   ("更高" if reading["expected_direction"] == "riskier" else "更低"))
                    if verdict else ("条件不成立" if verdict is False else "字段缺失，无法判断")})
    return {"readings": out, "states_at_ms": states_at_ms, "states_gap_ms": gap,
            "states_stale": gap is None or abs(gap) > STATES_MAX_GAP_MS,
            "note_cn": "经前向验证支持并经用户批准的研究读数；与其他 L8 读数一样可被质疑" if out
            else "目前尚无经验证并批准的研究读数"}
