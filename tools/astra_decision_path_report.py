"""Offline Decision Path report: which steps actually move the decision (2026-10-14).

Reads <state>/decision_path/records.jsonl and prints, per path version and step:

- change rate: share of verifications where the user marked the step as moving the lean;
- collinearity: share where the step's reading pointed the same way as the previous step;
- outcome separation: settled results grouped by the step's verdict (counts, mean USD,
  short-leg breaches). Few samples: look for an obvious split, no probabilities;
- conclusion source: after which step the lean stopped moving.

A step that rarely moves the lean and does not separate outcomes for four weeks is a
candidate to become background (design doc section 4). This script only reports.

    python tools/astra_decision_path_report.py /var/lib/xxproject/workbench/decision_path/records.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

if __package__:
    from . import astra_decision_path_v1 as dp
else:  # direct CLI execution from tools/
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import astra_decision_path_v1 as dp

MOVED = {"toward_underwrite", "toward_decline"}
# verdict codes that point toward the liability (+1) or away from it (-1); others 0
ADVERSE = {"partial": 1, "stop": 1, "inside": 1, "narrow": 1, "medium": 0, "wide": -1, "toward": 1,
           "toward_amplified": 1, "away": -1, "listed": 1, "back_and_forth": 1, "persistent": 0, "recent": 0,
           "flat": -1, "fading": 0, "calendar_partial": 0, "attention": 1, "clear": -1, "below_market": 1, "above_market": -1}
LOW_CHANGE = 0.10
MIN_SAMPLES = 8
# two reading groups count as separated only with enough members each and a mean result gap
# of at least this many USD (about 5% of the 2,000 USD width)
SEPARATION_USD = 100.0
SEPARATION_MIN_GROUP = 3


def load(path: str | Path) -> list[dict[str, Any]]:
    rows = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def join(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per verification: its conclusion record plus its settlement, if any."""
    ended, settled = {}, {}
    for row in rows:
        vid = row.get("verification_id")
        if row.get("event") in {"concluded", "expired_open"}:
            ended[vid] = dict(row)
        elif row.get("event") == "settled":
            settled[vid] = row.get("outcome") or {}
    return [{**row, "outcome": settled.get(vid)} for vid, row in ended.items()]


def report(sessions: list[Mapping[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    by_version = defaultdict(list)
    for s in sessions:
        by_version[s.get("path_version") or "unknown"].append(s)
    for version, items in sorted(by_version.items()):
        steps: dict[str, Any] = {}
        order = [sid for sid in dp.STEP_IDS if sid != "AU"]
        # the audit step counts only in sessions where it takes marks (au_mode) and was opened,
        # so "seen, no change" and "never opened" stay apart (docs/opus/14 §3.2)
        au_items = [s for s in items if s.get("au_mode") and "AU" in (s.get("opened_order") or [])]
        if au_items:
            order.append("AU")
        for i, sid in enumerate(order):
            members = au_items if sid == "AU" else items
            marks = [((s.get("marks") or {}).get(sid) or "unchanged") for s in members]
            moved = sum(m in MOVED for m in marks)
            same = total = 0
            if i > 0:
                prev = order[i - 1]
                for s in items:
                    a = ADVERSE.get((s.get("verdicts_start") or {}).get(prev), 0)
                    b = ADVERSE.get((s.get("verdicts_start") or {}).get(sid), 0)
                    if a and b:
                        total += 1
                        same += a == b
            groups: dict[str, list[float]] = defaultdict(list)
            breaches: Counter = Counter()
            for s in members:
                o = s.get("outcome") or {}
                if o.get("status") != "settled" or not isinstance(o.get("result_usd"), (int, float)):
                    continue
                # the audit step has no program verdict: its results are grouped by the mark
                code = (((s.get("marks") or {}).get(sid) or "unchanged") if sid == "AU"
                        else (s.get("verdicts_start") or {}).get(sid)) or "—"
                groups[code].append(float(o["result_usd"]))
                breaches[code] += bool(o.get("short_breached"))
            steps[sid] = {
                "n": len(members), "change_rate": moved / len(members) if members else None,
                "collinear_rate": same / total if total else None, "collinear_n": total,
                "outcomes": {code: {"n": len(v), "mean_usd": sum(v) / len(v), "breached": breaches[code]}
                             for code, v in sorted(groups.items())},
            }
            big = [sum(g) / len(g) for g in groups.values() if len(g) >= SEPARATION_MIN_GROUP]
            separated = len(big) >= 2 and max(big) - min(big) >= SEPARATION_USD
            steps[sid]["thinning_candidate"] = bool(
                len(members) >= MIN_SAMPLES and steps[sid]["change_rate"] is not None
                and steps[sid]["change_rate"] < LOW_CHANGE and not separated)
        source = Counter(s.get("settled_after") or "none" for s in items)
        conclusions = Counter(s.get("conclusion") for s in items)
        out[version] = {"sessions": len(items), "steps": steps, "conclusion_source": dict(source),
                        "conclusions": dict(conclusions),
                        "settled": sum(1 for s in items if (s.get("outcome") or {}).get("status") == "settled")}
    return out


def render(result: Mapping[str, Any]) -> str:
    lines = []
    for version, r in result.items():
        lines.append(f"# 读序 {version}：核验 {r['sessions']} 次，已回填结果 {r['settled']} 次")
        lines.append(f"结论：{r['conclusions']}；倾向定型于：{r['conclusion_source']}")
        lines.append("| 步 | 改变率 | 共线率 | 结果分组（读数: 次数 / 平均美元 / 击穿短腿） | 降级候选 |")
        lines.append("|---|---|---|---|---|")
        for sid, st in r["steps"].items():
            pct = lambda v: "—" if v is None else f"{v * 100:.0f}%"  # noqa: E731
            groups = "；".join(f"{code}: {g['n']} / {g['mean_usd']:+.0f} / {g['breached']}"
                              for code, g in st["outcomes"].items()) or "—"
            lines.append(f"| {sid} | {pct(st['change_rate'])} | {pct(st['collinear_rate'])}（{st['collinear_n']}） | {groups} | "
                         f"{'是' if st['thinning_candidate'] else ''} |")
        lines.append("")
    lines.append(f"样本少时只看是否明显分开，不计算概率。降级候选 = 至少 {MIN_SAMPLES} 次核验、改变率低于 "
                 f"{LOW_CHANGE * 100:.0f}% 且结果未分开；连续 4 周如此再降为背景。")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("records", help="decision_path/records.jsonl")
    parser.add_argument("--json", action="store_true", help="print the raw result instead of the table")
    args = parser.parse_args(argv)
    result = report(join(load(args.records)))
    print(json.dumps(result, ensure_ascii=False, indent=1) if args.json else render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
