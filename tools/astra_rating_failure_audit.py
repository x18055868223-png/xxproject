#!/usr/bin/env python3
"""Audit isolated Astra historical rating failures without rerunning reviews.

This is a read-only research helper.  It reads a completed
``astra_offline_regrade.py`` ratings directory, verifies the sealed bundle, and
extracts failure evidence for sides that the existing validator left UNRATED.
It never calls an LLM, never reads outcome or PnL tables, and never creates a
replacement grade.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


SCHEMA_VERSION = "astra_rating_failure_audit@1.0.0"
EXPECTED_COUNT = 114
SIDES = ("put_credit", "call_credit")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _hash_value(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path} line {line_no} is not an object")
            rows.append(value)
    return rows


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "card_id",
        "side",
        "review_status",
        "side_status",
        "validation_reasons_cn",
        "missing_refs",
        "unusable_refs",
        "negated_probability_expressions",
        "response_trace",
        "response_errors",
    ]
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                name: _csv_value(row.get(name))
                for name in fieldnames
            })
    tmp.replace(path)


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _iter_sealable_files(root: Path, *, ignore_dir: Path | None = None) -> set[str]:
    files: set[str] = set()
    resolved_ignore = ignore_dir.resolve() if ignore_dir is not None else None
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if resolved_ignore is not None:
            try:
                path.resolve().relative_to(resolved_ignore)
                continue
            except ValueError:
                pass
        if path.name == "seal.json" or path.name.endswith(".lock"):
            continue
        files.add(path.relative_to(root).as_posix())
    return files


def verify_seal(ratings_dir: Path, *, ignore_dir: Path | None = None) -> dict[str, Any]:
    seal_path = ratings_dir / "seal.json"
    if not seal_path.exists():
        raise ValueError(f"missing seal file: {seal_path}")
    seal = _read_json(seal_path)
    if not isinstance(seal, dict):
        raise ValueError("seal.json is not an object")
    files = seal.get("files")
    if not isinstance(files, list):
        raise ValueError("seal.json has no files list")
    expected_hash = _hash_value(files)
    if seal.get("seal_hash") != expected_hash:
        raise ValueError("seal hash mismatch")

    sealed_paths: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("seal file entry is not an object")
        rel = item.get("path")
        if not isinstance(rel, str) or rel.startswith("/") or ".." in Path(rel).parts:
            raise ValueError(f"invalid sealed path: {rel!r}")
        path = ratings_dir / rel
        if not path.exists() or not path.is_file():
            raise ValueError(f"sealed file missing: {rel}")
        if item.get("bytes") != path.stat().st_size:
            raise ValueError(f"sealed file size mismatch: {rel}")
        if item.get("sha256") != _hash_file(path):
            raise ValueError(f"sealed file hash mismatch: {rel}")
        sealed_paths.add(rel)

    current_paths = _iter_sealable_files(ratings_dir, ignore_dir=ignore_dir)
    extra = current_paths - sealed_paths
    missing = sealed_paths - current_paths
    if extra or missing:
        raise ValueError(
            "sealed file set mismatch: "
            f"extra={sorted(extra)[:5]}, missing={sorted(missing)[:5]}"
        )
    return {
        "schema": seal.get("schema"),
        "sealed_at_utc": seal.get("sealed_at_utc"),
        "seal_hash": seal.get("seal_hash"),
        "file_count": len(files),
    }


def load_completed_reviews(ratings_dir: Path, expected_count: int) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    index = _read_jsonl(ratings_dir / "freeze" / "index.jsonl")
    reviews = _read_jsonl(ratings_dir / "reviews.jsonl")
    if len(index) != expected_count:
        raise ValueError(f"freeze index count mismatch: expected {expected_count}, got {len(index)}")
    if len(reviews) != expected_count:
        raise ValueError(f"completed review count mismatch: expected {expected_count}, got {len(reviews)}")
    index_ids = {str(row.get("card_id") or "") for row in index}
    review_ids = {str(row.get("card_id") or "") for row in reviews}
    if "" in index_ids or "" in review_ids:
        raise ValueError("blank card_id in index or reviews")
    if index_ids != review_ids:
        raise ValueError("review card set does not match frozen index")
    return reviews, {str(row["card_id"]): row for row in index}


def load_attempt_states(ratings_dir: Path) -> dict[str, dict[str, Any]]:
    states_dir = ratings_dir / "reviews.jsonl.v2_attempts"
    states: dict[str, dict[str, Any]] = {}
    if not states_dir.exists():
        return states
    for path in sorted(states_dir.glob("*.json")):
        state = _read_json(path)
        if not isinstance(state, dict):
            continue
        card_id = str(((state.get("record") or {}).get("card_id")) or "")
        if not card_id:
            continue
        states[card_id] = {"path": path, "stem": path.stem, "state": state}
    return states


def parse_model_content(content: str) -> Any:
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def response_payloads_for_state(state_info: dict[str, Any], ratings_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    states_dir = ratings_dir / "reviews.jsonl.v2_attempts"
    responses_dir = states_dir / "responses"
    state = state_info["state"]
    stem = state_info["stem"]
    payloads: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for attempt in state.get("attempts") or []:
        number = attempt.get("number")
        if not isinstance(number, int):
            continue
        response_path = responses_dir / f"{stem}.{number}.json"
        trace = {
            "attempt": number,
            "state_file": state_info["path"].relative_to(ratings_dir).as_posix(),
            "response_file": response_path.relative_to(ratings_dir).as_posix(),
            "response_sha256": attempt.get("response_sha256"),
        }
        if not response_path.exists():
            errors.append({**trace, "error": "response file missing"})
            continue
        actual_sha = _hash_file(response_path)
        trace["response_sha256_matches"] = bool(actual_sha == attempt.get("response_sha256"))
        if not trace["response_sha256_matches"]:
            errors.append({**trace, "error": "response sha256 mismatch", "actual_sha256": actual_sha})
            continue
        try:
            response = _read_json(response_path)
            content = (((response.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
            payload = parse_model_content(content)
        except Exception as exc:  # noqa: BLE001 - retain parse failure in audit output.
            errors.append({**trace, "error": f"response content parse failed: {type(exc).__name__}: {exc}"})
            continue
        payloads.append({**trace, "payload": payload})
    return payloads, errors


def fact_index_from_state(state_info: dict[str, Any]) -> dict[str, dict[str, Any]]:
    packet = state_info.get("state", {}).get("packet") or {}
    return {
        str(fact.get("id")): fact
        for fact in packet.get("facts") or []
        if isinstance(fact, dict) and fact.get("id")
    }


def collect_fact_refs(obj: Any, path: str = "") -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            child_path = f"{path}.{key}" if path else key
            if key in {"ref", "primary_counter_ref"} and isinstance(value, str):
                refs.append({"path": child_path, "ref": value})
            elif key == "refs" and isinstance(value, list):
                for index, item in enumerate(value):
                    if isinstance(item, str):
                        refs.append({"path": f"{child_path}[{index}]", "ref": item})
            elif key.endswith("_refs") and isinstance(value, list):
                for index, item in enumerate(value):
                    if isinstance(item, str):
                        refs.append({"path": f"{child_path}[{index}]", "ref": item})
            else:
                refs.extend(collect_fact_refs(value, child_path))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            refs.extend(collect_fact_refs(value, f"{path}[{index}]"))
    return refs


def collect_strings(obj: Any, path: str = "") -> list[dict[str, str]]:
    strings: list[dict[str, str]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            strings.extend(collect_strings(value, f"{path}.{key}" if path else key))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            strings.extend(collect_strings(value, f"{path}[{index}]"))
    elif isinstance(obj, str):
        strings.append({"path": path, "text": obj})
    return strings


_PROBABILITY_WORD_RE = re.compile(r"收益概率|胜率|概率|后验\s*%|posterior|win\s*rate", re.I)
_NEGATION_RE = re.compile(r"不是|并非|非|不代表|不表示|不等于|不能|不可|无法|未(?:经|能|可)?")


def split_cn_clauses(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[。；;\n]", text) if part.strip()]


def collect_negated_probability_expressions(payload: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in collect_strings(payload):
        for clause in split_cn_clauses(item["text"]):
            if not _PROBABILITY_WORD_RE.search(clause):
                continue
            if not _NEGATION_RE.search(clause):
                continue
            key = (item["path"], clause)
            if key in seen:
                continue
            seen.add(key)
            rows.append({"path": item["path"], "text": clause})
    return rows


def side_payload(payload: Any, side: str) -> Any:
    if not isinstance(payload, dict):
        return None
    sides = payload.get("side_evidence_ratings")
    if not isinstance(sides, dict):
        return None
    return sides.get(side)


def classify_refs(payloads: list[dict[str, Any]], side: str, facts: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    missing: list[dict[str, Any]] = []
    unusable: list[dict[str, Any]] = []
    seen_missing: set[tuple[str, str, int]] = set()
    seen_unusable: set[tuple[str, str, int]] = set()
    for payload_info in payloads:
        payload = side_payload(payload_info.get("payload"), side)
        if payload is None:
            continue
        for ref_item in collect_fact_refs(payload):
            ref = ref_item["ref"]
            fact = facts.get(ref)
            base = {
                "attempt": payload_info["attempt"],
                "path": ref_item["path"],
                "ref": ref,
                "response_file": payload_info["response_file"],
                "response_sha256": payload_info["response_sha256"],
            }
            if fact is None:
                key = (ref_item["path"], ref, payload_info["attempt"])
                if key not in seen_missing:
                    seen_missing.add(key)
                    missing.append(base)
            elif fact.get("usable") is not True:
                key = (ref_item["path"], ref, payload_info["attempt"])
                if key not in seen_unusable:
                    seen_unusable.add(key)
                    unusable.append({
                        **base,
                        "label_cn": fact.get("label_cn"),
                        "summary_cn": fact.get("summary_cn"),
                        "usable": fact.get("usable"),
                    })
    return missing, unusable


def negated_probability_for_side(payloads: list[dict[str, Any]], side: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for payload_info in payloads:
        payload = side_payload(payload_info.get("payload"), side)
        if payload is None:
            continue
        for item in collect_negated_probability_expressions(payload):
            key = (item["path"], item["text"], payload_info["attempt"])
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "attempt": payload_info["attempt"],
                "path": item["path"],
                "text": item["text"],
                "response_file": payload_info["response_file"],
                "response_sha256": payload_info["response_sha256"],
            })
    return rows


def response_trace(payloads: list[dict[str, Any]], errors: list[dict[str, str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for payload_info in payloads:
        rows.append({
            "attempt": payload_info["attempt"],
            "response_file": payload_info["response_file"],
            "response_sha256": payload_info["response_sha256"],
            "response_sha256_matches": payload_info["response_sha256_matches"],
        })
    rows.extend(errors)
    return rows


def build_failure_rows(ratings_dir: Path, expected_count: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    reviews, _index = load_completed_reviews(ratings_dir, expected_count)
    states = load_attempt_states(ratings_dir)
    rows: list[dict[str, Any]] = []
    missing_state_count = 0
    response_error_count = 0
    for review_row in sorted(reviews, key=lambda row: str(row.get("card_id") or "")):
        card_id = str(review_row.get("card_id") or "")
        review = review_row.get("llm_review") or {}
        advisory = review.get("integrated_trade_advisory") or {}
        sides = advisory.get("side_evidence_ratings") or {}
        state_info = states.get(card_id)
        if state_info:
            facts = fact_index_from_state(state_info)
            payloads, errors = response_payloads_for_state(state_info, ratings_dir)
        else:
            missing_state_count += 1
            facts = {}
            payloads = []
            errors = [{"error": "attempt state missing"}]
        response_error_count += len(errors)

        for side in SIDES:
            side_result = sides.get(side) or {}
            if side_result.get("status") != "UNRATED":
                continue
            missing_refs, unusable_refs = classify_refs(payloads, side, facts)
            negated_probability = negated_probability_for_side(payloads, side)
            rows.append({
                "card_id": card_id,
                "side": side,
                "review_status": review.get("status"),
                "side_status": side_result.get("status"),
                "validation_reasons_cn": side_result.get("validation_reasons_cn") or [],
                "missing_refs": missing_refs,
                "unusable_refs": unusable_refs,
                "negated_probability_expressions": negated_probability,
                "response_trace": response_trace(payloads, errors),
                "response_errors": errors,
            })
    summary = {
        "expected_count": expected_count,
        "review_count": len(reviews),
        "unrated_side_count": len(rows),
        "cards_with_unrated_sides": len({row["card_id"] for row in rows}),
        "missing_ref_rows": sum(len(row["missing_refs"]) for row in rows),
        "unusable_ref_rows": sum(len(row["unusable_refs"]) for row in rows),
        "negated_probability_expression_rows": sum(len(row["negated_probability_expressions"]) for row in rows),
        "missing_attempt_state_count": missing_state_count,
        "response_error_count": response_error_count,
    }
    return rows, summary


def run_audit(ratings_dir: Path, output_dir: Path, expected_count: int) -> dict[str, Any]:
    ratings_dir = ratings_dir.resolve()
    output_dir = output_dir.resolve()
    try:
        output_dir.relative_to(ratings_dir)
        ignore_dir = output_dir
    except ValueError:
        ignore_dir = None
    seal = verify_seal(ratings_dir, ignore_dir=ignore_dir)
    rows, summary = build_failure_rows(ratings_dir, expected_count)
    payload = {
        "schema": SCHEMA_VERSION,
        "generated_at_utc": _now_iso(),
        "ratings_dir": str(ratings_dir),
        "seal": seal,
        "summary": summary,
        "rows": rows,
    }
    _write_json(output_dir / "failure_audit.json", payload)
    _write_csv(output_dir / "failure_audit.csv", rows)
    return {
        "schema": SCHEMA_VERSION,
        "output_dir": str(output_dir),
        "seal_hash": seal["seal_hash"],
        **summary,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ratings-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--expected-count",
        type=int,
        default=EXPECTED_COUNT,
        help="expected completed card count; keep default 114 for the real study",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_audit(args.ratings_dir, args.output_dir, args.expected_count)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
