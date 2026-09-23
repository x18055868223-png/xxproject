"""Offline revalidation of archived responses. This entrypoint makes no HTTP calls."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import signal_review_v2 as core


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replay(source, output):
    output.mkdir(parents=True, exist_ok=False)
    old = {r["card_id"]: r["llm_review"] for r in map(json.loads, (source / "reviews.jsonl").read_text(encoding="utf-8").splitlines())}
    cards = {r["identity"]["card_id"]: r for r in map(json.loads, (source / "source.jsonl").read_text(encoding="utf-8").splitlines())}
    reviews, checks, sources = [], [], {}
    for state_path in sorted((source / "states").iterdir()):
        if not state_path.is_file():
            continue
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or "packet" not in state:
            continue
        card_id = state["record"]["card_id"]
        card = cards[card_id]
        response_path = source / "states/responses" / f"{state_path.name}.1.json"
        response = json.loads(response_path.read_text(encoding="utf-8"))
        payload = json.loads(response["choices"][0]["message"]["content"])
        original = old[card_id]
        review = core.build_review(card, payload, state["packet"], model=original["model"],
                                   reviewed_at=original["reviewed_at"], prompt_version=original["prompt_version"],
                                   statistical_context=state.get("statistical_context"))
        for key in ("provider", "review_mode", "llm_call_count", "llm_http_calls", "call_audit", "retry_budget"):
            if key in original:
                review[key] = original[key]
        review["local_semantic_revalidation"] = {
            "schema": "astra_joint_local_semantic_revalidation@1.0.0",
            "at_utc": datetime.now(timezone.utc).isoformat(),
            "original_response_sha256": sha(response_path),
            "source_review_file_sha256": sha(source / "reviews.jsonl"),
            "new_http_calls": 0,
            "mode": "same archived raw payload and frozen packet; no new model opinion",
        }
        validation = core.revalidate_review(card, review)
        advisory = review["integrated_trade_advisory"]
        checks.append({"card_id": card_id, "old_status": original["status"], "new_status": review["status"],
                       "validation": validation,
                       "side_status": {k: {field: v.get(field) for field in ("status", "grade", "validation_reasons_cn")} for k, v in advisory["side_evidence_ratings"].items()},
                       "joint_review": advisory.get("joint_review"), "new_http_calls": 0,
                       "preserved_original_http_calls": review.get("llm_http_calls")})
        reviews.append({"card_id": card_id, "llm_review": review})
        sources[str(state_path.relative_to(source))] = sha(state_path)
        sources[str(response_path.relative_to(source))] = sha(response_path)
    if set(old) != {r["card_id"] for r in reviews}:
        raise ValueError("replay card coverage mismatch")
    (output / "reviews.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in reviews), encoding="utf-8")
    report = {"schema": "astra_joint_v12_offline_replay@1.0.0", "new_http_calls": 0, "cards": checks, "source_hashes": sources}
    (output / "replay_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"cards": [{k: x[k] for k in ("card_id", "old_status", "new_status", "side_status")} for x in checks], "new_http_calls": 0}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    replay(args.source, args.output)
