"""Astra v2.0 local underwriting workbench.

This server-side layer keeps the v2.0 boundary deliberately narrow: it reads a
fresh producer-native SpaceGate fact, refreshes the current two read-only
Deribit vertical candidates, freezes deterministic underwriting snapshots, and
only runs a causal LLM review after an authenticated manual POST passes every
local gate.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from email.message import EmailMessage
import argparse
import gc
import gzip
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import re
import smtplib
import socket
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import unquote, urlparse, parse_qs

if __package__:
    from . import astra_underwriting_discovery_v20 as discovery
    from . import astra_underwriting_fact_context_v20 as fact_context
    from . import astra_underwriting_guidance_v20 as guidance
    from . import astra_decision_reduction_v1 as decision_reduction
    from . import astra_spatial_position_v1 as spatial_position
    from . import astra_cross_asset_v1 as cross_asset
    from . import astra_event_calendar_v1 as event_calendar
    from . import astra_underwriting_public_quote_v20 as public_quote
    from . import astra_underwriting_review_v20 as review_core
    from . import astra_underwriting_store_v20 as store
    from . import astra_underwriting_v20 as underwriting_core
    from . import astra_data_engine_v23 as data_engine
    from . import astra_data_projection_v23 as data_projection
    from . import astra_map_sources_v24 as map_sources
    from . import astra_map_v24 as map_core
    from . import astra_map_projection_v24 as map_projection
    from . import astra_risk_qualification_v244 as risk_qualification
    from . import astra_candidate_asof_v1 as candidate_asof
    from . import astra_wpem_workbench_v1 as wpem_workbench
    from . import astra_thesis_ledger_v1 as thesis_ledger
    from . import astra_hold_review_v1 as hold_review
    from . import astra_wpem_product_review_v1 as wpem_review
    from . import astra_release_identity_v1 as release_core
    from . import astra_chain_audit_v1 as chain_audit
    from . import astra_locked_candidate_store_v1 as locked_store
    from . import astra_locked_candidate_compare_v1 as locked_compare
    from . import astra_workbench_reading_v1 as reading
    from .astra_workbench_session_v1 import WorkbenchSessions
    from . import signal_llm_review
else:  # direct CLI execution from tools/
    import astra_underwriting_discovery_v20 as discovery
    import astra_underwriting_fact_context_v20 as fact_context
    import astra_underwriting_guidance_v20 as guidance
    import astra_decision_reduction_v1 as decision_reduction
    import astra_spatial_position_v1 as spatial_position
    import astra_cross_asset_v1 as cross_asset
    import astra_event_calendar_v1 as event_calendar
    import astra_underwriting_public_quote_v20 as public_quote
    import astra_underwriting_review_v20 as review_core
    import astra_underwriting_store_v20 as store
    import astra_underwriting_v20 as underwriting_core
    import astra_data_engine_v23 as data_engine
    import astra_data_projection_v23 as data_projection
    import astra_map_sources_v24 as map_sources
    import astra_map_v24 as map_core
    import astra_map_projection_v24 as map_projection
    import astra_risk_qualification_v244 as risk_qualification
    import astra_candidate_asof_v1 as candidate_asof
    import astra_wpem_workbench_v1 as wpem_workbench
    import astra_thesis_ledger_v1 as thesis_ledger
    import astra_hold_review_v1 as hold_review
    import astra_wpem_product_review_v1 as wpem_review
    import astra_release_identity_v1 as release_core
    import astra_chain_audit_v1 as chain_audit
    import astra_locked_candidate_store_v1 as locked_store
    import astra_locked_candidate_compare_v1 as locked_compare
    import astra_workbench_reading_v1 as reading
    from astra_workbench_session_v1 import WorkbenchSessions
    import signal_llm_review

SCHEMA = "astra_workbench@2.0.0"
STATE_SCHEMA = "astra_workbench_state@2.0.0"
TRANSITION_SCHEMA = "astra_workbench_space_gate_transition@2.0.0"
# Since 2026-10-13 the transition log follows the flip anchor's utility (information only);
# admission is net gamma and is tracked in memory with its own since.
ANCHOR_TRANSITION_SCHEMA = "astra_workbench_anchor_utility_transition@1.0.0"
ANCHOR_EFFECTIVE = "ANCHOR_EFFECTIVE"
ANCHOR_INEFFECTIVE = "ANCHOR_INEFFECTIVE"
DAILY_SNAPSHOT_SCHEMA = "astra_workbench_daily_snapshot@2.0.0"

SPACE_ELIGIBLE = "STRUCTURE_ELIGIBLE"
SPACE_BLOCKED = "STRUCTURE_BLOCKED"
REVIEW_COOLDOWN_MS = 600_000
# Load and memory control (2026-10-14, after an 11 h hang under a 1G cgroup limit).
VIEW_BODY_TTL_MS = 5_000          # page reads within this share one serialized body
VIEW_BODY_WAIT_S = 15.0           # a reader waits this long for the body being built, then 503
MAX_ACTIVE_REQUESTS = 24          # concurrent HTTP handlers; more get an immediate 503
REQUEST_SOCKET_TIMEOUT_S = 30     # a stalled client cannot pin a handler thread
MEMORY_EVENT_INTERVAL_MS = 900_000
HARD_PASSES_BEFORE_RESTART = 10   # ~5 min at hard level -> stop the watchdog ping -> restart
EMAIL_COOLDOWN_MS = 14_400_000
DEFAULT_DAILY_LIMIT = 15
DEFAULT_HTTP_TIMEOUT = 180
DEFAULT_MODEL = getattr(signal_llm_review, "DEFAULT_MODEL", "deepseek-reasoner")
_BJT_OFFSET_MS = 8 * 3_600_000
MANUAL_REVIEW_COOLDOWN_REASON = "manual_review_cooldown_active"
MANUAL_REVIEW_INFLIGHT_REASON = "manual_review_inflight"
MANUAL_REVIEW_BUDGET_EXHAUSTED_REASON = "manual_review_budget_exhausted"
_POST_ATOMIC_REASONS = {MANUAL_REVIEW_COOLDOWN_REASON, MANUAL_REVIEW_INFLIGHT_REASON}
REVIEW_PURPOSE = "review"
OBSERVATION_PURPOSE = "observation"
_REVIEW_PURPOSES = {REVIEW_PURPOSE, OBSERVATION_PURPOSE}


class UpstreamUnavailable(ValueError):
    """A data source has nothing current; `upstream` names the provider's own reason."""

    def __init__(self, message: str, *, upstream: str | None, last_success_ms: int | None):
        super().__init__(message)
        self.upstream = upstream
        self.last_success_ms = last_success_ms


def _upstream_code(exc: BaseException | None) -> str | None:
    """deribit_maintenance / deribit_unavailable from an exception chain, else None."""
    texts, seen = [], set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        texts.append(f"{getattr(exc, 'code', '')} {type(exc).__name__} {exc}")
        exc = exc.__cause__ or exc.__context__
    text = " ".join(texts)
    if "DERIBIT_MAINTENANCE" in text or "system_maintenance" in text:
        return "deribit_maintenance"
    if any(k in text for k in ("DERIBIT_HTTP_5", "URLError", "timed out", "TimeoutError", "ConnectionError")):
        return "deribit_unavailable"
    return None


class WorkbenchError(Exception):
    def __init__(self, status_code: int, code: str, message: str, *, detail: Any | None = None):
        super().__init__(message)
        self.status_code = int(status_code)
        self.code = code
        self.detail = detail

    def as_payload(self) -> dict[str, Any]:
        payload = {"schema": SCHEMA, "status": "ERROR", "code": self.code, "message": str(self)}
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload


def _now_ms() -> int:
    return int(time.time() * 1000)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


_PRIVATE_REVIEW_DISPLAY_KEYS = {
    "raw_response",
    "raw_provider_response",
    "provider_raw",
    "provider_response",
    "provider_response_raw",
    "provider_raw_response",
    "provider_reasoning",
    "provider_reasoning_content",
    "raw_reasoning",
    "raw_reasoning_content",
    "reasoning_content",
}


def _strip_private_review_display(value: Any) -> Any:
    if isinstance(value, Mapping):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text in _PRIVATE_REVIEW_DISPLAY_KEYS:
                continue
            if key_text.endswith("_raw_response") or key_text.endswith("_reasoning_content"):
                continue
            out[key] = _strip_private_review_display(item)
        return out
    if isinstance(value, list):
        return [_strip_private_review_display(item) for item in value]
    return deepcopy(value)


def _write_json_atomic(path: str | Path, value: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(_canonical(dict(value)) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: str | Path, default: Mapping[str, Any] | None = None) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return dict(default or {})
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return dict(default or {})
    return dict(value) if isinstance(value, dict) else dict(default or {})


FULL_RECEIPTS_KEPT = 6
DAILY_SNAPSHOT_ROTATE_BYTES = 64 * 1024 * 1024
DAILY_SNAPSHOT_KEEP_AFTER_ROTATE = 15


def _compact_receipt(result: Mapping[str, Any]) -> dict[str, Any]:
    """Identity and outcome of an older receipt; its full body stays in review_state/."""
    review = result.get("review") if isinstance(result.get("review"), Mapping) else {}
    keep = ("schema", "status", "candidate_id", "snapshot_id", "side", "purpose", "idempotency_key",
            "llm_call_count", "reason", "execution_allowed", "production_attach_allowed")
    out = {key: result.get(key) for key in keep if key in result}
    if review:
        out["review"] = {key: review.get(key) for key in (
            "status", "applicability", "reviewed_snapshot_id", "candidate_id", "response_protocol",
            "requested_model", "response_model", "research_only") if key in review}
    out["compacted"] = True
    return out


def _slim_receipt(result: Any) -> Any:
    """The receipt as kept in workbench_state.json: WPEM replay blobs (composition_inputs,
    source_bindings; ~98% of a 7.7 MB receipt) are dropped. Nothing reads them back from the
    state: validation replays the in-memory package, display uses the public evidence, and the
    full proof stays in review_state/ and wpem_evidence/."""
    if not isinstance(result, Mapping):
        return deepcopy(result)
    out = deepcopy(dict(result))
    body = out.get("review") if isinstance(out.get("review"), Mapping) else None
    if isinstance(body, dict) and isinstance(body.get("wpem_evidence"), Mapping):
        body["wpem_evidence"] = wpem_workbench.public_evidence(body["wpem_evidence"])
    return out


def _receipt_has_blobs(result: Any) -> bool:
    body = result.get("review") if isinstance(result, Mapping) and isinstance(result.get("review"), Mapping) else {}
    evidence = body.get("wpem_evidence") if isinstance(body.get("wpem_evidence"), Mapping) else {}
    return "composition_inputs" in evidence or "source_bindings" in evidence


def _slim_review_state(review: dict[str, Any]) -> bool:
    """Drop replay blobs from every receipt kept in the state; True when anything changed."""
    changed = False
    if _receipt_has_blobs(review.get("last_result")):
        review["last_result"] = _slim_receipt(review["last_result"])
        changed = True
    for key, entry in list((review.get("idempotency") or {}).items()):
        if isinstance(entry, Mapping) and _receipt_has_blobs(entry.get("result")):
            review["idempotency"][key] = {**entry, "result": _slim_receipt(entry["result"])}
            changed = True
    return changed


def _compact_review_history(review: dict[str, Any]) -> None:
    """Keep full receipts for the newest few settled entries only."""
    _slim_review_state(review)
    idem = review.get("idempotency")
    if not isinstance(idem, dict):
        return
    settled = sorted((key for key, entry in idem.items()
                      if isinstance(entry, Mapping) and isinstance(entry.get("result"), Mapping)
                      and not entry["result"].get("compacted")),
                     key=lambda key: int((idem[key] or {}).get("updated_at_ms") or 0))
    for key in settled[:-FULL_RECEIPTS_KEPT]:
        idem[key] = {**idem[key], "result": _compact_receipt(idem[key]["result"])}


def _read_jsonl_tail(path: str | Path, limit: int) -> list[dict[str, Any]]:
    """The last `limit` JSON lines without parsing the rest of a large file."""
    path = Path(path)
    if limit <= 0 or not path.exists():
        return []
    lines: list[bytes] = []
    with path.open("rb") as handle:
        handle.seek(0, 2)
        position, remainder = handle.tell(), b""
        while position > 0 and len(lines) <= limit:
            step = min(1 << 20, position)
            position -= step
            handle.seek(position)
            chunk = handle.read(step) + remainder
            parts = chunk.split(b"\n")
            remainder = parts[0]
            lines = [line for line in parts[1:] if line.strip()] + lines
        if position == 0 and remainder.strip():
            lines = [remainder] + lines
    rows = []
    for line in lines[-limit:]:
        try:
            value = json.loads(line.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _append_jsonl(path: str | Path, row: Mapping[str, Any]) -> None:
    store.append_jsonl(path, [dict(row)])


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    try:
        return store.read_jsonl(path)
    except ValueError:
        return []


def _bjt_day(ts_ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime((int(ts_ms) + _BJT_OFFSET_MS) / 1000))


def _bjt_hour(ts_ms: int) -> int:
    return int(time.strftime("%H", time.gmtime((int(ts_ms) + _BJT_OFFSET_MS) / 1000)))


def _iso_utc(ts_ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ts_ms) / 1000))


def _review_receipt_for_display(value: Any) -> Any:
    """Label historical success at read time, without rewriting its evidence."""
    if not isinstance(value, Mapping):
        return deepcopy(value)
    out = deepcopy(dict(value))
    body = out.get("review") if isinstance(out.get("review"), Mapping) else out
    if isinstance(body.get("wpem_evidence"), Mapping):
        body["wpem_evidence"] = wpem_workbench.public_evidence(body["wpem_evidence"])
    for key in ("decision_trace", "llm_explanation", "q1", "q2", "selection_origin"):
        if isinstance(body.get(key), Mapping):
            body[key] = wpem_workbench.public_evidence(body[key])
    if (out.get("status") in {"OK", "RESEARCH_OK", "attached"}
            and body.get("response_protocol") not in {
                review_core.RESPONSE_PROTOCOL,
                wpem_review.RESPONSE_PROTOCOL,
                wpem_review.ROUTING_RESPONSE_PROTOCOL,
                "evidence_points@2.6.4",
            }):
        out.update(status="stale_protocol_review", legacy_status=value.get("status"),
                   current_protocol_qualified=False,
                   expected_response_protocol=review_core.RESPONSE_PROTOCOL,
                   reason_cn="旧协议历史回执，未按当前有限目录复核；未自动重发。")
        if body is not out:
            out["review"] = _review_receipt_for_display(body)
    return _strip_private_review_display(out)


def _accepted_review_status(value: Mapping[str, Any]) -> bool:
    outer_status = value.get("status")
    if isinstance(value.get("review"), Mapping):
        return (outer_status in {"OK", "RESEARCH_OK", "attached"}
                and value["review"].get("status") in {"OK", "RESEARCH_OK"})
    return outer_status in {"OK", "RESEARCH_OK", "attached"}


def _review_body(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return value.get("review") if isinstance(value.get("review"), Mapping) else value


def _review_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    body = _review_body(value)
    validity = body.get("review_validity") if isinstance(body.get("review_validity"), Mapping) else {}
    return {
        "candidate_id": value.get("candidate_id") or body.get("candidate_id") or validity.get("candidate_id"),
        "snapshot_id": (value.get("snapshot_id") or body.get("reviewed_snapshot_id")
                        or body.get("snapshot_id") or validity.get("snapshot_id")),
    }


def _receipt_needs_full_proof(snapshot_id: str | None, receipt: Any) -> bool:
    return bool(snapshot_id and isinstance(receipt, Mapping)
                and _accepted_review_status(receipt)
                and _review_identity(receipt).get("snapshot_id") == snapshot_id)


def _first_int(*values: Any) -> int | None:
    for value in values:
        if isinstance(value, int):
            return value
    return None


def _reviewed_at_ms(value: Mapping[str, Any], fallback: Any = None) -> int | None:
    body = _review_body(value)
    return _first_int(
        value.get("reviewed_at_ms"),
        value.get("completed_at_ms"),
        value.get("recorded_ts_ms"),
        value.get("historical_completed_ts_ms"),
        body.get("reviewed_at_ms"),
        body.get("recorded_ts_ms"),
        fallback,
    )


def _receipt_id(value: Mapping[str, Any]) -> str | None:
    body = _review_body(value)
    for key in ("idempotency_key", "receipt_id", "review_hash", "event_id"):
        if value.get(key):
            return str(value[key])
        if body.get(key):
            return str(body[key])
    try:
        return _hash(value)[:24]
    except Exception:
        return None


def _display_binding(
    value: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    same_input: bool,
    reviewed_at_ms: int | None = None,
) -> dict[str, Any]:
    body = _review_body(value)
    snapshot = candidate.get("snapshot") if isinstance(candidate.get("snapshot"), Mapping) else {}
    validity = body.get("review_validity") if isinstance(body.get("review_validity"), Mapping) else {}
    trace = body.get("decision_trace") if isinstance(body.get("decision_trace"), Mapping) else {}
    trace_binding = trace.get("binding") if isinstance(trace.get("binding"), Mapping) else {}
    identity = _review_identity(value)
    binding = {
        "candidate_id": identity.get("candidate_id") or snapshot.get("candidate_id"),
        "snapshot_id": identity.get("snapshot_id") or snapshot.get("snapshot_id"),
        "evidence_input_hash": trace_binding.get("evidence_input_hash"),
        "frozen_at_ms": _first_int(
            value.get("frozen_at_ms"),
            body.get("frozen_at_ms"),
            validity.get("quote_timestamp_ms"),
            (snapshot.get("market") or {}).get("valuation_ts_ms"),
        ),
        "reviewed_at_ms": reviewed_at_ms if reviewed_at_ms is not None else _reviewed_at_ms(value),
        "receipt_id": _receipt_id(value),
        "same_input": bool(same_input),
    }
    for key in (
        "quote_hash",
        "risk_hash",
        "window_evidence_hash",
        "review_evidence_hash",
        "projection_hash",
        "review_input_version",
        "llm_protocol",
        "routing_schema",
        "routing_hash",
    ):
        if validity.get(key) is not None:
            binding[key] = deepcopy(validity[key])
    return binding


def _with_display_binding(
    value: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    same_input: bool,
    reviewed_at_ms: int | None = None,
) -> dict[str, Any]:
    display = _review_receipt_for_display(value)
    if not isinstance(display, Mapping):
        display = {}
    out = deepcopy(dict(display))
    binding = _display_binding(value, candidate, same_input=same_input, reviewed_at_ms=reviewed_at_ms)
    body = _review_body(value)
    for key in ("research_only", "production_attach_allowed", "execution_allowed"):
        if key not in out and key in body:
            out[key] = deepcopy(body[key])
    out["display_binding"] = binding
    if isinstance(out.get("review"), Mapping):
        nested = deepcopy(dict(out["review"]))
        nested["display_binding"] = deepcopy(binding)
        out["review"] = nested
    return out


def _review_has_display_explanation(value: Mapping[str, Any]) -> bool:
    body = _review_body(value)
    explanation = body.get("llm_explanation")
    if isinstance(explanation, Mapping):
        for key in ("q1", "q2"):
            item = explanation.get(key)
            if isinstance(item, Mapping) and (item.get("points") or item.get("text_cn")):
                return True
    for key in ("q1", "q2"):
        if isinstance(body.get(key), Mapping) and body[key]:
            return True
    return bool(body.get("summary_cn"))


def _decision_trace_evidence_input_hash(value: Mapping[str, Any]) -> str | None:
    body = _review_body(value)
    trace = body.get("decision_trace") if isinstance(body.get("decision_trace"), Mapping) else {}
    binding = trace.get("binding") if isinstance(trace.get("binding"), Mapping) else {}
    evidence_hash = binding.get("evidence_input_hash")
    return evidence_hash if isinstance(evidence_hash, str) and evidence_hash else None


def _current_review_display_binding(
    snapshot: Mapping[str, Any],
    wpem_package: Mapping[str, Any],
    body: Mapping[str, Any],
    *,
    model: str | None,
) -> dict[str, Any] | None:
    review_input = wpem_review.build_review_input(
        snapshot,
        wpem_package,
        research_only=body.get("research_only") is True,
        model=model,
    )
    evidence_input = review_input.get("evidence_input")
    if not isinstance(evidence_input, Mapping) or not evidence_input:
        return None
    expected = wpem_review.review_binding(snapshot, wpem_package, review_input, model=model)
    expected["evidence_input_hash"] = _hash(evidence_input)
    return expected


def _receipt_dedupe_key(value: Mapping[str, Any]) -> tuple[Any, ...]:
    body = _review_body(value)
    validity = body.get("review_validity") if isinstance(body.get("review_validity"), Mapping) else {}
    identity = _review_identity(value)
    return (
        value.get("status"),
        body.get("status"),
        identity.get("candidate_id"),
        identity.get("snapshot_id"),
        body.get("response_protocol"),
        body.get("packet_hash"),
        validity.get("projection_hash") or body.get("projection_hash"),
        validity.get("review_evidence_hash"),
        validity.get("window_evidence_hash"),
        _decision_trace_evidence_input_hash(value),
    )


def _candidate_data_asof_ms(candidate: Mapping[str, Any]) -> int | None:
    snapshot = candidate.get("snapshot") if isinstance(candidate.get("snapshot"), Mapping) else {}
    return _first_int((snapshot.get("market") or {}).get("valuation_ts_ms"))


def _history_receipt(entry: Mapping[str, Any]) -> tuple[Mapping[str, Any], int | None]:
    if isinstance(entry.get("result"), Mapping):
        # Shallow: only a top-level key is added here; display copies deeply later.
        receipt = dict(entry["result"])
        if entry.get("idempotency_key") and not receipt.get("idempotency_key"):
            receipt["idempotency_key"] = entry.get("idempotency_key")
        return receipt, _first_int(entry.get("updated_at_ms"))
    return entry, _reviewed_at_ms(entry)


def _safe_public_text(value: Any) -> Any:
    if not isinstance(value, str):
        return deepcopy(value)
    if re.search(r"[A-Za-z]:[\\/]", value):
        return None
    if value.startswith("\\\\"):
        return None
    if re.search(r"(^|[\s\"'=])/(home|srv|opt|var|tmp)(/|$)", value):
        return None
    return value


_PUBLIC_DROP = object()


def _scrub_public_value(value: Any, *, _usage_qualification: bool = False) -> Any:
    if isinstance(value, Mapping):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            key_norm = key_text.replace("-", "_")
            if key_text in _PRIVATE_REVIEW_DISPLAY_KEYS:
                continue
            if key_norm != "usage_qualification" and (
                    key_norm in ({"provider_usage", "headers", "x_api_key", "private_key"}
                                 if _usage_qualification else
                                 {"usage", "provider_usage", "headers", "x_api_key", "private_key"})
                    or any(part in key_norm for part in (
                        "api_key", "authorization", "password", "credential", "secret", "private_key"
                    ))):
                continue
            if key_text.endswith("_token") or key_text.endswith("_raw_response") or key_text.endswith("_reasoning_content"):
                continue
            scrubbed = _scrub_public_value(item, _usage_qualification=key_norm == "usage_qualification")
            if scrubbed is not _PUBLIC_DROP:
                out[key] = scrubbed
        return out
    if isinstance(value, list):
        return [
            item for item in (
                _scrub_public_value(item, _usage_qualification=_usage_qualification) for item in value
            )
            if item is not _PUBLIC_DROP
        ]
    if isinstance(value, str):
        safe = _safe_public_text(value)
        return _PUBLIC_DROP if safe is None else safe
    return deepcopy(value)


def _stable_receipt_id(value: Mapping[str, Any] | None) -> str | None:
    if not isinstance(value, Mapping):
        return None
    binding = value.get("display_binding") if isinstance(value.get("display_binding"), Mapping) else {}
    if binding.get("receipt_id"):
        return str(binding["receipt_id"])
    review = value.get("review") if isinstance(value.get("review"), Mapping) else {}
    nested = review.get("display_binding") if isinstance(review.get("display_binding"), Mapping) else {}
    if nested.get("receipt_id"):
        return str(nested["receipt_id"])
    return _receipt_id(value)


def _demotion_reason_cn(reason: Mapping[str, Any] | None) -> str | None:
    if not isinstance(reason, Mapping):
        return None
    code = str(reason.get("code") or "")
    messages = {
        "candidate_id_mismatch": "历史回执绑定的 candidate_id 与当前候选不同，只能折叠参考。",
        "snapshot_id_mismatch": "历史回执绑定的 snapshot_id 与当前快照不同，只能折叠参考。",
        "evidence_input_hash_mismatch": "历史回执的实际 LLM input hash 与当前冻结输入不同，只能折叠参考。",
        "quote_timestamp_not_current": "历史回执报价时间不覆盖当前读取时点，只能折叠参考。",
        "valid_until_expired": "历史回执已超过原记录有效窗口，只能折叠参考。",
        "model_mismatch": "历史回执使用的模型身份与当前配置不同，只能折叠参考。",
        "review_evidence_hash_mismatch": "历史回执的 WPEM evidence hash 与当前证据包不同，只能折叠参考。",
        "quote_hash_mismatch": "历史回执的报价 hash 与当前快照不同，只能折叠参考。",
        "risk_hash_mismatch": "历史回执的风险参考 hash 与当前快照不同，只能折叠参考。",
        "selector_exception": "当前审计重建遇到异常，回执不升级为当前解释；程序事实仍可阅读。",
    }
    return messages.get(code) or (f"历史回执未通过当前选择器检查：{code}。" if code else None)


def _first_demotion_reason(binding: Mapping[str, Any] | None) -> str | None:
    if not isinstance(binding, Mapping):
        return None
    reasons = binding.get("demotion_reasons")
    if isinstance(reasons, list) and reasons:
        return _demotion_reason_cn(reasons[0])
    return None


def _lineage_limitation(message_cn: str, *, error_type: str | None = None) -> dict[str, Any]:
    out = {
        "schema": "observation_lineage@1",
        "status": "LIMITED",
        "reason_cn": message_cn,
        "points": [],
        "evidence": [],
        "program_points": [],
        "source_records": [],
        "input_omissions": [],
        "hash_dictionary": [],
        "proof_limits": [message_cn],
    }
    if error_type:
        out["error_type"] = error_type
    return out


def _public_review_binding_metadata(
    value: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    same_input: bool,
    reviewed_at_ms: int | None = None,
    selector_diagnostics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    body = _review_body(value)
    out = _display_binding(value, candidate, same_input=same_input, reviewed_at_ms=reviewed_at_ms)
    out["status"] = value.get("status")
    if body is not value:
        out["review_status"] = body.get("status")
    for key in ("response_protocol", "packet_hash", "projection_hash", "requested_model"):
        if body.get(key) is not None:
            out[key] = deepcopy(body[key])
    if isinstance(selector_diagnostics, Mapping):
        out["selector_status"] = selector_diagnostics.get("status")
        reasons = selector_diagnostics.get("demotion_reasons")
        if isinstance(reasons, list):
            out["demotion_reasons"] = deepcopy(reasons)
    return _strip_private_review_display(out)


def _analysis_identity(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    body = _review_body(value)
    validity = body.get("review_validity") if isinstance(body.get("review_validity"), Mapping) else {}
    trace = body.get("decision_trace") if isinstance(body.get("decision_trace"), Mapping) else {}
    binding = trace.get("binding") if isinstance(trace.get("binding"), Mapping) else {}
    identity = {
        "model": body.get("requested_model") or validity.get("model") or body.get("model"),
        "review_input_schema": binding.get("schema") or validity.get("review_input_version"),
        "response_protocol": body.get("response_protocol") or binding.get("response_protocol"),
        "prompt_hash": body.get("prompt_hash") or validity.get("prompt_hash"),
        "prompt_id": body.get("prompt_id") or validity.get("prompt_id"),
    }
    return identity if any(item is not None for item in identity.values()) else None


def _is_rejected_review_attempt(value: Mapping[str, Any]) -> bool:
    body = _review_body(value)
    statuses = {
        str(item).lower()
        for item in (value.get("status"), body.get("status"))
        if item is not None
    }
    if statuses & {"rejected", "failed", "error", "timeout", "malformed"}:
        return True
    return bool(value.get("reason_cn") or body.get("reason_cn")) and "not_attached" not in statuses


def _candidate_source_manifest(candidate: Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(candidate.get("source_manifest"), Mapping):
        return candidate["source_manifest"]
    snapshot = candidate.get("snapshot") if isinstance(candidate.get("snapshot"), Mapping) else {}
    identity = snapshot.get("data_identity") if isinstance(snapshot.get("data_identity"), Mapping) else {}
    manifest = identity.get("source_manifest") if isinstance(identity.get("source_manifest"), Mapping) else {}
    return manifest


def _public_source_record(
    record: Mapping[str, Any],
    *,
    candidate_id: Any = None,
    snapshot_id: Any = None,
) -> dict[str, Any] | None:
    identity = record.get("identity") if isinstance(record.get("identity"), Mapping) else {}
    version = record.get("version") if isinstance(record.get("version"), Mapping) else {}
    quality = record.get("quality") if isinstance(record.get("quality"), Mapping) else {}
    collector = record.get("collector") if isinstance(record.get("collector"), Mapping) else {}
    usage = record.get("usage_qualification") if isinstance(record.get("usage_qualification"), Mapping) else {}
    time_info = record.get("time") if isinstance(record.get("time"), Mapping) else {}
    content = record.get("content") if isinstance(record.get("content"), Mapping) else {}
    values = content.get("values") if isinstance(content.get("values"), Mapping) else {}
    content_hashes = {
        key: _safe_public_text(content.get(key))
        for key in ("content_hash", "hash", "sha256", "raw_response_sha256")
        if content.get(key)
    }
    if values.get("source_hash"):
        content_hashes["values.source_hash"] = _safe_public_text(values.get("source_hash"))
    out = {
        "lane": "current_snapshot_source_manifest",
        "candidate_id": candidate_id,
        "snapshot_id": snapshot_id,
        "record_id": _safe_public_text(record.get("record_id")),
        "product_id": _safe_public_text(identity.get("product_id")),
        "provider_id": _safe_public_text(identity.get("provider_id")),
        "source_ref": _safe_public_text(identity.get("source_ref")),
        "definition_version": _safe_public_text(identity.get("definition_version")),
        "market": _safe_public_text(identity.get("market")),
        "asset": _safe_public_text(identity.get("asset")),
        "currency": _safe_public_text(identity.get("currency")),
        "aggregation": _safe_public_text(identity.get("aggregation")),
        "unit": _safe_public_text(identity.get("unit")),
        "time": {
            "observation_start_ms": time_info.get("observation_start_ms"),
            "observation_end_ms": time_info.get("observation_end_ms"),
            "published_at_ms": time_info.get("published_at_ms"),
            "first_seen_at_ms": time_info.get("first_seen_at_ms"),
        },
        "hashes": {
            "source_revision": _safe_public_text(version.get("source_revision")),
            "payload_hash": _safe_public_text(version.get("payload_hash")),
            "normalized_version": _safe_public_text(version.get("normalized_version")),
            "normalizer_version": _safe_public_text(version.get("normalizer_version")),
            "content_hashes": {key: value for key, value in content_hashes.items() if value is not None},
        },
        "quality": {
            "data_state": quality.get("data_state"),
            "reason_codes": deepcopy(quality.get("reason_codes") or []),
            "rule_version": quality.get("rule_version"),
        },
        "collector": {
            "collector_id": _safe_public_text(collector.get("collector_id")),
            "provider_id": _safe_public_text(collector.get("provider_id")),
            "primary_collector": _safe_public_text(collector.get("primary_collector")),
            "accepted_as_current": collector.get("accepted_as_current"),
            "historical_import": collector.get("historical_import"),
        },
        "usage_qualification": {
            "usage": usage.get("usage"),
            "status": usage.get("status"),
            "can_use": usage.get("can_use"),
            "age_ms": usage.get("age_ms"),
            "ttl_ms": usage.get("ttl_ms"),
            "reason_codes": deepcopy(usage.get("reason_codes") or []),
            "qualified_at_ms": usage.get("qualified_at_ms"),
        },
        "source_to_input_mapping": {
            "status": "not_recorded",
            "reason_cn": "source manifest record does not store an exact source-record to clean-input-ref mapping.",
        },
    }
    if not out["record_id"]:
        return None
    return _strip_private_review_display(out)


def _source_ref_record_ids(source_refs: Any) -> list[str]:
    ids: list[str] = []
    for item in source_refs or []:
        if isinstance(item, str):
            ids.append(item)
        elif isinstance(item, Mapping) and isinstance(item.get("record_id"), str):
            ids.append(item["record_id"])
    return ids


def _apply_source_record_forward_maps(
    source_records: list[dict[str, Any]],
    evidence: Sequence[Mapping[str, Any]],
    *,
    enabled: bool,
) -> list[dict[str, Any]]:
    if not enabled:
        return source_records
    linked: dict[str, set[str]] = {}
    known = {record.get("record_id") for record in source_records if record.get("record_id")}
    for item in evidence:
        ref = item.get("ref")
        if not isinstance(ref, str):
            continue
        pointer = item.get("source_pointer") if isinstance(item.get("source_pointer"), Mapping) else {}
        # Some background sources live in the frozen trace, not the current
        # snapshot manifest. Reuse those retained pointers in the forward index.
        for source in pointer.get("source_refs") or []:
            if not isinstance(source, Mapping) or not source.get("record_id"):
                continue
            record_id = source["record_id"]
            if record_id not in known:
                record = {key: deepcopy(source[key]) for key in (
                    "record_id", "product_id", "schema", "content_hash", "time", "usage_qualification"
                ) if key in source}
                record.update(lane=item.get("lane"), candidate_id=pointer.get("trace_candidate_id"),
                              snapshot_id=pointer.get("trace_snapshot_id"),
                              note_cn="来源指针取自这次冻结输入的 Decision Trace，未补造来源原文。")
                source_records.append(_scrub_public_value(record))
                known.add(record_id)
        for record_id in _source_ref_record_ids(pointer.get("source_refs")):
            if record_id in known:
                linked.setdefault(record_id, set()).add(ref)
    for record in source_records:
        record_id = record.get("record_id")
        if record_id in linked:
            record["source_to_input_mapping"] = {
                "status": "exact_record_id_match",
                "method": "trace.source_refs.record_id equality",
                "input_refs": sorted(linked[record_id]),
                "reason_cn": "当前输入 trace.source_refs 中记录了完全相同的 source record_id。",
            }
    return source_records


def _lineage_source_records(candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    manifest = _candidate_source_manifest(candidate)
    snapshot = candidate.get("snapshot") if isinstance(candidate.get("snapshot"), Mapping) else {}
    records: list[dict[str, Any]] = []
    for record in manifest.get("source_records") or []:
        if isinstance(record, Mapping):
            public = _public_source_record(
                record,
                candidate_id=snapshot.get("candidate_id"),
                snapshot_id=snapshot.get("snapshot_id"),
            )
            if public is not None:
                records.append(public)
    return records


def _trace_for_lineage(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    body = _review_body(value)
    trace = body.get("decision_trace") if isinstance(body.get("decision_trace"), Mapping) else {}
    return trace


def _public_trace_evidence_item(
    item: Mapping[str, Any],
    *,
    lane: str,
    trace_binding: Mapping[str, Any],
    candidate_snapshot: Mapping[str, Any],
    receipt_id: str | None,
    referenced_by: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    lineage = item.get("lineage") if isinstance(item.get("lineage"), Mapping) else {}
    source_refs = lineage.get("source_refs") if isinstance(lineage.get("source_refs"), list) else []
    out: dict[str, Any] = {
        "lane": lane,
        "ref": item.get("ref"),
        "label_cn": item.get("label_cn"),
        "group": item.get("group"),
        "referenced": bool(referenced_by),
        "referenced_by": deepcopy(list(referenced_by)),
        "source_pointer": {
            "ref": lineage.get("ref") or item.get("ref"),
            "source_path": _safe_public_text(lineage.get("source_path")),
            "source_refs": _scrub_public_value(source_refs),
            "trace_candidate_id": trace_binding.get("candidate_id"),
            "trace_snapshot_id": trace_binding.get("snapshot_id"),
            "receipt_id": receipt_id,
            "lane": lane,
        },
    }
    if (trace_binding.get("candidate_id") == candidate_snapshot.get("candidate_id")
            and trace_binding.get("snapshot_id") == candidate_snapshot.get("snapshot_id")):
        canonical_paths = {
            "contract": "snapshot.contract",
            "quote": "snapshot.market",
            "fee": "snapshot.fee",
            "risk": "snapshot.risk",
        }
        path = canonical_paths.get(str(item.get("ref")))
        root_key = path.split(".", 1)[1] if path else None
        if path and root_key in candidate_snapshot:
            out["source_pointer"]["canonical_snapshot_pointer"] = {
                "status": "current_trace_snapshot_match",
                "source_path": path,
                "reason_cn": "该 trace 绑定的 candidate/snapshot 与当前候选一致，可回到当前 snapshot 对应字段。",
            }
    for key in ("qualification", "relations", "asof", "scope", "limitations", "shared_dependency"):
        if key in item:
            out[key] = _scrub_public_value(item[key])
    facts = item.get("facts")
    if isinstance(facts, Mapping):
        try:
            if len(_canonical(facts)) <= 900:
                out["facts"] = _scrub_public_value(facts)
            else:
                out["facts_included"] = False
                out["facts_omission_reason_cn"] = "原输入事实过长，保留 ref 与 source_pointer 供页面回到 decision_trace.input_evidence。"
        except Exception:
            out["facts_included"] = False
    return _scrub_public_value(out)


def _lineage_from_trace(
    trace: Mapping[str, Any],
    *,
    receipt_id: str | None,
    lane: str,
    candidate_snapshot: Mapping[str, Any],
    include_points: bool,
) -> dict[str, Any]:
    trace_binding = trace.get("binding") if isinstance(trace.get("binding"), Mapping) else {}
    referenced_by_ref: dict[str, list[dict[str, Any]]] = {}
    if include_points:
        for fallback_index, point in enumerate(trace.get("points") or []):
            if not isinstance(point, Mapping):
                continue
            index = point.get("index")
            array_index = index - 1 if isinstance(index, int) and index > 0 else fallback_index
            for ref in point.get("evidence_refs") or []:
                if isinstance(ref, str):
                    referenced_by_ref.setdefault(ref, []).append({
                        "receipt_id": receipt_id,
                        "question": point.get("question"),
                        "array_index": array_index,
                    })
    evidence = [
        _public_trace_evidence_item(
            item,
            lane=lane,
            trace_binding=trace_binding,
            candidate_snapshot=candidate_snapshot,
            receipt_id=receipt_id,
            referenced_by=referenced_by_ref.get(str(item.get("ref")), ()),
        )
        for item in trace.get("input_evidence") or []
        if isinstance(item, Mapping)
    ]
    points: list[dict[str, Any]] = []
    if include_points:
        for fallback_index, point in enumerate(trace.get("points") or []):
            if not isinstance(point, Mapping):
                continue
            index = point.get("index")
            array_index = index - 1 if isinstance(index, int) and index > 0 else fallback_index
            points.append({
                "receipt_id": receipt_id,
                "lane": lane,
                "question": point.get("question"),
                "array_index": array_index,
                "text_cn": point.get("text_cn"),
                "evidence_refs": deepcopy(point.get("evidence_refs") or []),
            })
    return {
        "points": _strip_private_review_display(points),
        "evidence": evidence,
        "input_omissions": _strip_private_review_display(trace.get("audit_only") or []),
    }


def _append_program_point(
    points: list[dict[str, Any]],
    *,
    question: str,
    field: str,
    text: Any,
    owner: str,
    source_paths: Sequence[str],
    refs: Sequence[str] = (),
) -> None:
    if isinstance(text, str) and text.strip():
        points.append({
            "question": question,
            "field": field,
            "text_cn": text,
            "owner": owner,
            "source_paths": list(source_paths),
            "refs": list(refs),
        })


def _program_lineage_points(candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    questions = candidate.get("current_questions") if isinstance(candidate.get("current_questions"), Mapping) else {}
    owner = str(questions.get("source_kind") or "deterministic_program_no_llm")
    q1 = questions.get("q1") if isinstance(questions.get("q1"), Mapping) else {}
    q2 = questions.get("q2") if isinstance(questions.get("q2"), Mapping) else {}
    summary = candidate.get("wpem_summary") if isinstance(candidate.get("wpem_summary"), Mapping) else {}
    points: list[dict[str, Any]] = []
    _append_program_point(points, question="q1", field="summary_cn", text=q1.get("summary_cn"), owner=owner,
                          source_paths=("current_questions.q1.summary_cn", "snapshot.contract",
                                        "snapshot.liability", "snapshot.market"))
    for idx, text in enumerate(q1.get("routing_observations_cn") or []):
        _append_program_point(points, question="q1", field=f"routing_observations_cn[{idx}]", text=text, owner=owner,
                              source_paths=("current_questions.q1.routing_observations_cn",
                                            "current_questions.q1.routing_evidence_ids"),
                              refs=q1.get("routing_evidence_ids") or ())
    _append_program_point(points, question="q1", field="window_attribution_cn", text=q1.get("window_attribution_cn") or summary.get("path_cn"),
                          owner=owner,
                          source_paths=("current_questions.q1.window_attribution_cn",
                                        "wpem_summary.path_cn",
                                        "wpem_evidence.same_candidate_window"))
    _append_program_point(points, question="q2", field="summary_cn", text=q2.get("summary_cn"), owner=owner,
                          source_paths=("current_questions.q2.summary_cn", "snapshot.economics", "snapshot.fee"))
    for idx, text in enumerate(q2.get("routing_observations_cn") or []):
        _append_program_point(points, question="q2", field=f"routing_observations_cn[{idx}]", text=text, owner=owner,
                              source_paths=("current_questions.q2.routing_observations_cn",
                                            "current_questions.q2.routing_evidence_ids"),
                              refs=q2.get("routing_evidence_ids") or ())
    _append_program_point(points, question="q2", field="window_tradeoff_cn", text=q2.get("window_tradeoff_cn") or summary.get("compensation_cn"),
                          owner=owner,
                          source_paths=("current_questions.q2.window_tradeoff_cn",
                                        "wpem_summary.compensation_cn",
                                        "wpem_evidence.same_candidate_window"))
    for idx, text in enumerate((q2.get("observable_changes_cn") or [])[:3]):
        _append_program_point(points, question="q2", field=f"observable_changes_cn[{idx}]", text=text, owner=owner,
                              source_paths=("current_questions.q2.observable_changes_cn",
                                            "wpem_evidence.same_candidate_window"))
    _append_program_point(points, question="q2", field="waiting_cost_cn", text=q2.get("waiting_cost_cn"), owner=owner,
                          source_paths=("current_questions.q2.waiting_cost_cn", "snapshot.market"))
    evidence = candidate.get("wpem_evidence") if isinstance(candidate.get("wpem_evidence"), Mapping) else {}
    account = evidence.get("quote_fee_deterministic_responsibility")
    if isinstance(account, Mapping) and account.get("c_minus_f_btc") is not None:
        _append_program_point(
            points,
            question="q2",
            field="quote_fee_deterministic_responsibility.c_minus_f_btc",
            text=f"扣持有至到期费用后的信用 c-f={account.get('c_minus_f_btc')} BTC",
            owner=owner,
            source_paths=("wpem_evidence.quote_fee_deterministic_responsibility",
                          "snapshot.economics", "snapshot.fee"),
            refs=("quote", "fee"),
        )
    return _strip_private_review_display(points)


def _fallback_identity(candidate: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = candidate.get("snapshot") if isinstance(candidate.get("snapshot"), Mapping) else {}
    questions = candidate.get("current_questions") if isinstance(candidate.get("current_questions"), Mapping) else {}
    summary = candidate.get("wpem_summary") if isinstance(candidate.get("wpem_summary"), Mapping) else {}
    evidence = candidate.get("wpem_evidence") if isinstance(candidate.get("wpem_evidence"), Mapping) else {}
    return {
        "schema": "program_fallback_identity@1",
        "hash": _hash({"snapshot": snapshot, "current_questions": questions, "wpem_summary": summary,
                       "fee_remainder": evidence.get("quote_fee_deterministic_responsibility")}),
        "hash_kind": "sha256_canonical_json",
        "inputs": ["snapshot", "current_questions", "wpem_summary", "wpem_evidence.quote_fee_deterministic_responsibility"],
    }


def _hash_dictionary(
    candidate: Mapping[str, Any],
    *,
    source_records: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    snapshot = candidate.get("snapshot") if isinstance(candidate.get("snapshot"), Mapping) else {}
    identity = snapshot.get("data_identity") if isinstance(snapshot.get("data_identity"), Mapping) else {}
    rows = [
        ("source_sha256", "canonical source bundle bytes or normalized source object",
         "data engine / snapshot builder", "snapshot lineage readers"),
        ("source_record.payload_hash", "one source manifest record payload",
         "data engine normalizer", "source record replay and source audit"),
        ("snapshot_id", "canonical underwriting snapshot",
         "underwriting snapshot builder", "review selector and UI candidate binding"),
        ("packet_hash", "WPEM packet canonical serialization",
         "WPEM packet/replay producer", "WPEM replay validator"),
        ("review_evidence_hash", "WPEM review evidence package",
         "WPEM review evidence builder", "review validity selector"),
        ("window_evidence_hash", "same-candidate WPEM window evidence",
         "WPEM window builder", "review validity selector"),
        ("projection_hash", "complete review input projection",
         "review input builder", "review validity selector"),
        ("evidence_input_hash", "actual clean evidence input sent to LLM",
         "Decision Trace builder", "review selector and lineage replay"),
        ("routing_hash", "information attribution/routing projection",
         "evidence routing builder", "review validity selector"),
        ("receipt_id", "existing review receipt or deterministic receipt hash",
         "review store/read projection", "consumer binding receipt"),
        ("fallback_identity.hash", "program fallback snapshot, current_questions, WPEM summary and fee remainder projection",
         "workbench read projection", "consumer binding receipt"),
    ]
    present_names = {"snapshot_id", "fallback_identity.hash"}
    if identity.get("source_sha256"):
        present_names.add("source_sha256")
    if any((row.get("hashes") or {}).get("payload_hash") for row in source_records):
        present_names.add("source_record.payload_hash")
    result = []
    for name, obj, producer, consumer in rows:
        if name not in present_names and name not in {
            "packet_hash", "review_evidence_hash", "window_evidence_hash", "projection_hash",
            "evidence_input_hash", "routing_hash", "receipt_id",
        }:
            continue
        result.append({
            "name": name,
            "object": obj,
            "producer": producer,
            "consumer": consumer,
            "same_kind_comparison_only": "true",
        })
    return result


def _lineage_proof_limits() -> list[str]:
    return [
        "本追溯是只读消费投影，不是上游市场门或新的交易许可。",
        "旧视图没有记录 consumed_at_ms；页面渲染、用户展开、阅读或交易动作都不能从这里推断。",
        "source_records 只公开来源清单元数据，不公开完整 provider 原文或私有回执。",
        "source_clock_status=NOT_RETAINED 表示原始来源观察时钟未被保留，只能按该缺口阅读。",
        "Decision Trace 的 produced_refs 只覆盖 clean evidence 或模型点，不代表 Data/WPEM 曾生成的全部上游事实。",
        "Displayed 只表示工作台 payload 可展示，不证明用户已经打开折叠区或采纳结论。",
    ]


def _build_observation_lineage(
    candidate: Mapping[str, Any],
    *,
    current_display: Mapping[str, Any] | None,
    historical_display: Mapping[str, Any] | None,
    rejected_binding: Mapping[str, Any] | None,
    rejected_receipt: Mapping[str, Any] | None,
) -> dict[str, Any]:
    snapshot = candidate.get("snapshot") if isinstance(candidate.get("snapshot"), Mapping) else {}
    selected: Mapping[str, Any] | None = None
    lane = "program_fallback"
    include_points = False
    if isinstance(current_display, Mapping):
        selected = current_display
        lane = "current_accepted"
        include_points = True
    elif isinstance(historical_display, Mapping):
        selected = historical_display
        lane = "historical_accepted"
        include_points = True
    elif isinstance(rejected_receipt, Mapping):
        selected = rejected_receipt
        lane = "rejected_input"
        include_points = False
    if isinstance(rejected_receipt, Mapping) and selected is rejected_receipt and isinstance(rejected_binding, Mapping):
        receipt_id = rejected_binding.get("receipt_id") if isinstance(rejected_binding.get("receipt_id"), str) else None
    else:
        receipt_id = _stable_receipt_id(selected)
    trace_lineage = _lineage_from_trace(
        _trace_for_lineage(selected),
        receipt_id=receipt_id,
        lane=lane,
        candidate_snapshot=snapshot,
        include_points=include_points,
    )
    source_records = _lineage_source_records(candidate)
    source_records = _apply_source_record_forward_maps(
        source_records,
        trace_lineage["evidence"],
        enabled=(lane == "current_accepted"),
    )
    return {
        "schema": "observation_lineage@1",
        "candidate_id": snapshot.get("candidate_id"),
        "snapshot_id": snapshot.get("snapshot_id"),
        "selected_trace_lane": lane,
        "points": trace_lineage["points"],
        "evidence": trace_lineage["evidence"],
        "program_points": _program_lineage_points(candidate),
        "source_records": source_records,
        "input_omissions": trace_lineage["input_omissions"],
        "hash_dictionary": _hash_dictionary(candidate, source_records=source_records),
        "display_semantics": {
            "produced": "facts or clean evidence created by upstream builders",
            "included": "facts present in the frozen actual input or public program fallback",
            "referenced": "accepted model points explicitly cited these refs",
            "displayed": "available in the generated workbench payload, not proof of user reading or action",
        },
        "proof_limits": _lineage_proof_limits(),
    }


def _build_consumer_binding_receipt(
    candidate: Mapping[str, Any],
    *,
    data_asof_ms: int | None,
    reason_cn: str,
    current_display: Mapping[str, Any] | None,
    historical_display: Mapping[str, Any] | None,
    rejected_binding: Mapping[str, Any] | None,
    rejected_receipt: Mapping[str, Any] | None,
) -> dict[str, Any]:
    snapshot = candidate.get("snapshot") if isinstance(candidate.get("snapshot"), Mapping) else {}
    questions = candidate.get("current_questions") if isinstance(candidate.get("current_questions"), Mapping) else {}
    current_binding = current_display.get("display_binding") if isinstance(current_display, Mapping) else None
    historical_binding = historical_display.get("display_binding") if isinstance(historical_display, Mapping) else None
    primary_source = "current_accepted" if isinstance(current_display, Mapping) else "program_fallback"
    primary_owner = "accepted_llm_review" if primary_source == "current_accepted" else str(
        questions.get("source_kind") or "deterministic_program_no_llm")
    analysis_receipt = current_display or historical_display or rejected_receipt
    consumed_review_id = _stable_receipt_id(current_display)
    historical_reason_cn = _first_demotion_reason(historical_binding)
    if isinstance(historical_display, Mapping) and not historical_reason_cn:
        historical_reason_cn = "最近 accepted 回执未通过当前选择器，只作历史折叠参考。"
    return {
        "schema": "consumer_binding_receipt@1",
        "candidate_id": snapshot.get("candidate_id"),
        "snapshot_id": snapshot.get("snapshot_id"),
        "data_asof_ms": data_asof_ms,
        "primary_source": primary_source,
        "primary_owner": primary_owner,
        "reason_cn": reason_cn,
        "consumed_review_id": consumed_review_id,
        "fallback_identity": _fallback_identity(candidate),
        "current_binding": _strip_private_review_display(current_binding) if isinstance(current_binding, Mapping) else None,
        "historical_binding": (_strip_private_review_display(historical_binding)
                               if isinstance(historical_binding, Mapping) else None),
        "rejected_binding": _strip_private_review_display(rejected_binding) if isinstance(rejected_binding, Mapping) else None,
        "historical_reason_cn": historical_reason_cn,
        "consumed_at_ms": None,
        "consumed_at_status": "not_recorded",
        "analysis_identity": _analysis_identity(analysis_receipt),
    }


def _strict_current_review_status(
    receipt: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    wpem_package: Mapping[str, Any] | None,
    model: str | None,
    now_ms: int | None,
    diagnostics: dict[str, Any] | None = None,
) -> str:
    reasons: list[dict[str, Any]] = []

    def stale(code: str, **detail: Any) -> str:
        item = {"code": code}
        item.update({key: value for key, value in detail.items() if value is not None})
        reason_cn = _demotion_reason_cn(item)
        if reason_cn:
            item["reason_cn"] = reason_cn
        reasons.append(item)
        if diagnostics is not None:
            diagnostics["status"] = "STALE"
            diagnostics["demotion_reasons"] = deepcopy(reasons)
        return "STALE"

    snapshot = candidate.get("snapshot") if isinstance(candidate.get("snapshot"), Mapping) else None
    if not isinstance(snapshot, Mapping) or not isinstance(wpem_package, Mapping):
        return stale("snapshot_or_wpem_package_missing")
    identity = _review_identity(receipt)
    if identity.get("candidate_id") != snapshot.get("candidate_id"):
        return stale("candidate_id_mismatch",
                     receipt_candidate_id=identity.get("candidate_id"),
                     current_candidate_id=snapshot.get("candidate_id"))
    if identity.get("snapshot_id") != snapshot.get("snapshot_id"):
        return stale("snapshot_id_mismatch",
                     receipt_snapshot_id=identity.get("snapshot_id"),
                     current_snapshot_id=snapshot.get("snapshot_id"))
    body = _review_body(receipt)
    if not _review_has_display_explanation(receipt):
        return stale("display_explanation_missing")
    binding = body.get("review_validity") if isinstance(body.get("review_validity"), Mapping) else {}
    expected_quote_hash = _hash(snapshot.get("market"))
    expected_risk_hash = _hash(snapshot.get("risk"))
    for key, expected in (
        ("candidate_id", snapshot.get("candidate_id")),
        ("snapshot_id", snapshot.get("snapshot_id")),
        ("review_evidence_hash", wpem_package.get("evidence_hash")),
        ("quote_hash", expected_quote_hash),
        ("risk_hash", expected_risk_hash),
    ):
        if binding.get(key) != expected:
            return stale(f"{key}_mismatch", actual=binding.get(key), expected=expected)
    if model is not None and (body.get("requested_model") or binding.get("model")) != model:
        return stale("model_mismatch", actual=body.get("requested_model") or binding.get("model"), expected=model)
    if now_ms is not None:
        quote_ms = binding.get("quote_timestamp_ms")
        if type(quote_ms) is not int or now_ms < quote_ms:
            return stale("quote_timestamp_not_current", quote_timestamp_ms=quote_ms, now_ms=now_ms)
        valid_until = binding.get("valid_until_ms")
        if type(valid_until) is not int or now_ms > valid_until:
            return stale("valid_until_expired", valid_until_ms=valid_until, now_ms=now_ms)
    try:
        frozen_hash = _decision_trace_evidence_input_hash(receipt)
        expected = _current_review_display_binding(snapshot, wpem_package, body, model=model)
        if not isinstance(expected, Mapping):
            return stale("expected_binding_unavailable")
        if not frozen_hash or frozen_hash != expected.get("evidence_input_hash"):
            return stale("evidence_input_hash_mismatch", actual=frozen_hash,
                         expected=expected.get("evidence_input_hash"))
        for key in ("candidate_id", "snapshot_id", "quote_hash", "risk_hash",
                    "window_evidence_hash", "review_evidence_hash",
                    "review_input_version", "projection_hash", "llm_protocol",
                    "routing_schema", "routing_hash", "quote_timestamp_ms"):
            if binding.get(key) != expected.get(key):
                return stale(f"{key}_mismatch", actual=binding.get(key), expected=expected.get(key))
        if diagnostics is not None:
            diagnostics["status"] = "CURRENT"
            diagnostics["demotion_reasons"] = []
        return "CURRENT"
    except Exception as exc:
        return stale("selector_exception", error_type=type(exc).__name__)


def review_display_for_candidate(
    candidate: Mapping[str, Any],
    manual_review_state: Mapping[str, Any] | None = None,
    *,
    wpem_package: Mapping[str, Any] | None = None,
    model: str | None = None,
    now_ms: int | None = None,
    accepted_history: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Select a read-only current/historical review display without changing review authority."""
    data_asof_ms = _candidate_data_asof_ms(candidate)
    snapshot = candidate.get("snapshot") if isinstance(candidate.get("snapshot"), Mapping) else {}
    candidate_id = snapshot.get("candidate_id")
    receipts: list[tuple[Mapping[str, Any], int | None]] = []
    attached = candidate.get("review")
    if isinstance(attached, Mapping):
        receipts.append((attached, _reviewed_at_ms(attached)))
    if isinstance(manual_review_state, Mapping):
        for key, timestamp_key in (("last_result", "last_completed_ts_ms"),
                                   ("previous_valid_result", "historical_completed_ts_ms")):
            receipt = manual_review_state.get(key)
            if isinstance(receipt, Mapping):
                receipts.append((receipt, _first_int(receipt.get(timestamp_key),
                                                     manual_review_state.get(timestamp_key),
                                                     manual_review_state.get("last_completed_ts_ms"))))
    for entry in accepted_history or ():
        if isinstance(entry, Mapping):
            receipts.append(_history_receipt(entry))

    current: list[tuple[int, dict[str, Any]]] = []
    historical: list[tuple[int, dict[str, Any]]] = []
    saw_failure = False
    rejected_candidates: list[tuple[int, Mapping[str, Any], dict[str, Any]]] = []
    seen_receipts: set[tuple[Any, ...]] = set()
    for receipt, reviewed_ms in receipts:
        if not isinstance(receipt, Mapping):
            continue
        dedupe_key = _receipt_dedupe_key(receipt)
        if dedupe_key in seen_receipts:
            continue
        seen_receipts.add(dedupe_key)
        identity = _review_identity(receipt)
        candidate_matches = (not candidate_id
                             or not identity.get("candidate_id")
                             or identity.get("candidate_id") == candidate_id)
        if not _accepted_review_status(receipt):
            if candidate_matches and _is_rejected_review_attempt(receipt):
                saw_failure = saw_failure or bool(receipt.get("status"))
                rejected_candidates.append((
                    reviewed_ms or 0,
                    receipt,
                    _public_review_binding_metadata(
                        receipt,
                        candidate,
                        same_input=False,
                        reviewed_at_ms=reviewed_ms,
                        selector_diagnostics={"status": "REJECTED", "demotion_reasons": [
                            {"code": "receipt_not_accepted"}
                        ]},
                    ),
                ))
            continue
        if candidate_id and identity.get("candidate_id") != candidate_id:
            continue
        diagnostics: dict[str, Any] = {}
        status = _strict_current_review_status(
            receipt, candidate, wpem_package=wpem_package, model=model, now_ms=now_ms,
            diagnostics=diagnostics)
        if status == "CURRENT":
            display = _with_display_binding(
                receipt, candidate, same_input=True, reviewed_at_ms=reviewed_ms)
            display["display_binding"] = _public_review_binding_metadata(
                receipt, candidate, same_input=True, reviewed_at_ms=reviewed_ms,
                selector_diagnostics=diagnostics)
            if isinstance(display.get("review"), Mapping):
                nested = deepcopy(dict(display["review"]))
                nested["display_binding"] = deepcopy(display["display_binding"])
                display["review"] = nested
            current.append((reviewed_ms or 0, display))
        else:
            display = _with_display_binding(
                receipt, candidate, same_input=False, reviewed_at_ms=reviewed_ms)
            display["display_binding"] = _public_review_binding_metadata(
                receipt, candidate, same_input=False, reviewed_at_ms=reviewed_ms,
                selector_diagnostics=diagnostics)
            if isinstance(display.get("review"), Mapping):
                nested = deepcopy(dict(display["review"]))
                nested["display_binding"] = deepcopy(display["display_binding"])
                display["review"] = nested
            historical.append((reviewed_ms or 0, display))

    current_display = max(current, key=lambda item: item[0])[1] if current else None
    historical_display = max(historical, key=lambda item: item[0])[1] if historical else None
    rejected_entry = max(rejected_candidates, key=lambda item: item[0]) if rejected_candidates else None
    rejected_receipt = rejected_entry[1] if rejected_entry else None
    rejected_binding = rejected_entry[2] if rejected_entry else None
    if current_display is not None:
        reason_cn = "当前 accepted 回执与候选、快照、报价、证据输入及来源版本严格一致。"
    elif historical_display is not None:
        reason_cn = "当前无合格模型综合解释；最近 accepted 回执仅作历史参考，不能回填当前。"
    elif saw_failure:
        reason_cn = "当前尝试未形成合格模型综合解释；失败正文不进入消费层，请阅读程序事实底座。"
    else:
        reason_cn = "当前无合格模型综合解释；请阅读程序事实底座。"
    try:
        consumer_binding_receipt = _build_consumer_binding_receipt(
            candidate,
            data_asof_ms=data_asof_ms,
            reason_cn=reason_cn,
            current_display=current_display,
            historical_display=historical_display,
            rejected_binding=rejected_binding,
            rejected_receipt=rejected_receipt,
        )
    except Exception as exc:
        consumer_binding_receipt = {
            "schema": "consumer_binding_receipt@1",
            "status": "LIMITED",
            "candidate_id": snapshot.get("candidate_id"),
            "snapshot_id": snapshot.get("snapshot_id"),
            "data_asof_ms": data_asof_ms,
            "primary_source": "current_accepted" if current_display is not None else "program_fallback",
            "reason_cn": reason_cn,
            "projection_limitations": [{
                "reason_cn": "消费回执投影生成失败；当前/历史选择结果保持原样，程序事实仍可阅读。",
                "error_type": type(exc).__name__,
            }],
            "consumed_at_ms": None,
            "consumed_at_status": "not_recorded",
        }
    try:
        observation_lineage = _build_observation_lineage(
            candidate,
            current_display=current_display,
            historical_display=historical_display,
            rejected_binding=rejected_binding,
            rejected_receipt=rejected_receipt,
        )
    except Exception as exc:
        observation_lineage = _lineage_limitation(
            "追溯投影生成失败；当前/历史选择结果保持原样，程序事实仍可阅读。",
            error_type=type(exc).__name__,
        )
        observation_lineage.update({
            "candidate_id": snapshot.get("candidate_id"),
            "snapshot_id": snapshot.get("snapshot_id"),
        })
    return {
        "current": current_display,
        "historical": historical_display,
        "reason_cn": reason_cn,
        "data_asof_ms": data_asof_ms,
        "consumer_binding_receipt": consumer_binding_receipt,
        "observation_lineage": observation_lineage,
    }



def _normalize_review_purpose(purpose: str | None) -> str:
    value = (purpose or REVIEW_PURPOSE).strip().lower()
    if value not in _REVIEW_PURPOSES:
        raise WorkbenchError(400, "bad_request", "purpose must be 'review' or 'observation'")
    return value


def _compact_review(review_annex: Mapping[str, Any] | None) -> dict[str, Any]:
    review_annex = review_annex if isinstance(review_annex, Mapping) else {}
    if review_annex.get("status") != "attached":
        return {"status": review_annex.get("status") or "not_attached",
                "reviewed_snapshot_id": review_annex.get("reviewed_snapshot_id")}
    review = review_annex.get("review") if isinstance(review_annex.get("review"), Mapping) else {}
    return _review_receipt_for_display({
        "status": "attached",
        "response_protocol": review.get("response_protocol"),
        "reviewed_snapshot_id": review_annex.get("reviewed_snapshot_id"),
        "review_hash": review_annex.get("review_hash"),
        "applicability": review.get("applicability"),
        "summary_cn": review.get("summary_cn"),
        "q1": deepcopy(review.get("q1") or {}),
        "q2": deepcopy(review.get("q2") or {}),
        "supports": deepcopy(review.get("supports") or []),
        "counters": deepcopy(review.get("counters") or []),
        "gaps_cn": deepcopy(review.get("gaps_cn") or []),
        "reassess_if_cn": deepcopy(review.get("reassess_if_cn") or []),
        "http_calls": review.get("http_calls"),
        "recorded_ts_ms": review_annex.get("recorded_ts_ms"),
        **{key: deepcopy(review[key]) for key in (
            "review_validity", "llm_explanation", "decision_trace", "selection_origin") if key in review},
    })


@dataclass
class WorkbenchConfig:
    snapshots_jsonl: Path
    state_dir: Path
    frontend_dir: Path | None = None
    server_token: str | None = None
    public_origin: str | None = None
    session_ttl_seconds: int = 43_200
    max_sessions: int = 32
    api_key: str | None = None
    shared_usage_ledger: Path | None = None
    model: str = DEFAULT_MODEL
    endpoint: str | None = None
    daily_http_limit: int = signal_llm_review.DEFAULT_DAILY_HTTP_LIMIT
    review_timeout_s: int = DEFAULT_HTTP_TIMEOUT
    refresh_interval_s: float = 30.0
    review_cooldown_ms: int = REVIEW_COOLDOWN_MS
    email_cooldown_ms: int = EMAIL_COOLDOWN_MS
    daily_snapshot_limit: int = DEFAULT_DAILY_LIMIT
    map_sources_enabled: bool = False
    map_source_config: dict[str, Any] | None = None
    map_source_mode: str = map_projection.SOURCE_MODE_COLLECTOR
    map_producer_current_path: Path | None = None
    map_producer_max_age_ms: int = map_projection.DEFAULT_PRODUCER_MAX_AGE_MS
    risk_model_path: Path | None = None
    risk_protocol_path: Path | None = None
    research_archive_enabled: bool = False
    research_sample_interval_ms: int = candidate_asof.DEFAULT_INTERVAL_MS
    research_max_bytes: int = candidate_asof.DEFAULT_MAX_BYTES
    research_max_samples: int = candidate_asof.DEFAULT_MAX_SAMPLES
    wpem_enabled: bool = False
    # Off: each refresh composes WPEM from the current point only (no archive sample, no
    # history replay). The archive stays readable; the lock series is the time memory.
    wpem_history_replay: bool = False
    locked_sample_interval_ms: int = 60_000
    locked_max_active: int = 4
    locked_max_episodes: int = 64
    locked_max_bytes: int = 536_870_912
    locked_max_samples: int = 3000
    locked_compact_interval_ms: int = 10 * 60_000
    scheduled_review_hours_utc: tuple[int, ...] = (2, 14)
    scheduled_review_window_ms: int = 45 * 60_000
    chain_audit_enabled: bool = True
    chain_audit_interval_ms: int = 900_000
    chain_audit_retention_days: int = 30
    # BTC cross-asset reference (background card): Yahoo hourly bars after each US close.
    cross_asset_enabled: bool = False
    # Memory guard (RSS): above soft, drop caches and skip optional work; above hard, serve
    # only cached page bodies and refuse heavy reads with 503 until back under soft.
    memory_soft_mb: int = 1536
    memory_hard_mb: int = 2560

    @property
    def state_path(self) -> Path:
        return self.state_dir / "workbench_state.json"

    @property
    def btc_map_state_path(self) -> Path:
        # Map response state lives apart from workbench_state.json, so the 30 s refresh never
        # parses or rewrites the review history just to persist it.
        return self.state_dir / "btc_map_state.json"

    @property
    def ledger_path(self) -> Path:
        return self.state_dir / "underwriting.jsonl"

    @property
    def transitions_path(self) -> Path:
        return self.state_dir / "space_gate_transitions.jsonl"

    @property
    def daily_snapshots_path(self) -> Path:
        return self.state_dir / "daily_snapshots.jsonl"

    @property
    def review_state_dir(self) -> Path:
        return self.state_dir / "review_state"

    @property
    def usage_ledger_path(self) -> Path:
        if self.shared_usage_ledger is None:
            raise ValueError("shared LLM usage ledger must be configured")
        return self.shared_usage_ledger


class SmtpEmailSender:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        mail_from: str,
        mail_to: str,
        starttls: bool = True,
    ):
        self.host = host
        self.port = int(port)
        self.username = username
        self.password = password
        self.mail_from = mail_from
        self.mail_to = mail_to
        self.starttls = bool(starttls)

    def send(self, subject: str, body: str) -> dict[str, Any]:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.mail_from
        message["To"] = self.mail_to
        message.set_content(body)
        with smtplib.SMTP(self.host, self.port, timeout=15) as client:
            if self.starttls:
                client.starttls(context=ssl.create_default_context())
            if self.username:
                client.login(self.username, self.password or "")
            client.send_message(message)
        return {"status": "sent", "transport": "smtp"}


class AstraWorkbench:
    def __init__(
        self,
        config: WorkbenchConfig,
        *,
        discovery_capture: Callable[..., Mapping[str, Any]] | None = None,
        quote_capture: Callable[..., Mapping[str, Any]] | None = None,
        risk_provider: Callable[[Mapping[str, Any], Mapping[str, Any], int], Mapping[str, Any] | None] | None = None,
        email_sender: Any | None = None,
        llm_transport: Callable[..., Any] | None = None,
        clock_ms: Callable[[], int] | None = None,
        map_source_capture: Callable[..., Any] | None = None,
    ):
        self.config = config
        self.discovery_capture = discovery_capture or self._default_discovery_capture
        self.quote_capture = quote_capture or self._default_quote_capture
        self.risk_provider = risk_provider
        self.research_archive = None
        self._research_feature_catalog = None
        self._research_feature_catalog_error = None
        self._research_setup_error = None
        self._research_pending_payloads = {}
        self._wpem_pending_payloads = {}
        self._wpem_packages = reading.CompressedPoints(max_items=8, max_bytes=48 * 1024 * 1024)
        self._wpem_display_packages = {}
        self._locked_payloads: dict[str, Any] = {}
        self._lockable_points = reading.CompressedPoints()
        self._locked_detail_cache: dict[str, Any] = {}
        self._locked_detail_lock = threading.RLock()
        self.locked_candidates = locked_store.LockedCandidateStore(
            config.state_dir / "locked_candidates", max_active=config.locked_max_active,
            max_episodes=config.locked_max_episodes, max_bytes=config.locked_max_bytes,
            max_samples=config.locked_max_samples)
        self._locked_refresh_error: str | None = None
        self._wpem_archive = candidate_asof.CandidateResearchArchive(
            config.state_dir / "wpem_evidence", interval_ms=config.research_sample_interval_ms,
            max_bytes=config.research_max_bytes, max_samples=config.research_max_samples) if config.wpem_enabled else None
        if config.research_archive_enabled:
            try:
                self.research_archive = candidate_asof.CandidateResearchArchive(
                    config.state_dir / "candidate_research", interval_ms=config.research_sample_interval_ms,
                    max_bytes=config.research_max_bytes, max_samples=config.research_max_samples)
            except Exception as exc:
                self._research_setup_error = type(exc).__name__
            if config.risk_model_path or config.risk_protocol_path:
                try:
                    if not config.risk_model_path or not config.risk_protocol_path:
                        raise ValueError("both risk asset paths are required")
                    if __package__:
                        from . import astra_candidate_features_v1 as candidate_features
                    else:
                        import astra_candidate_features_v1 as candidate_features
                    self._research_feature_catalog = candidate_features.export_feature_catalog(
                        config.risk_model_path, config.risk_protocol_path)
                except Exception as exc:
                    self._research_feature_catalog_error = type(exc).__name__
        self._risk_profile = None
        self._risk_asset_error = None
        if config.risk_model_path or config.risk_protocol_path:
            try:
                if not config.risk_model_path or not config.risk_protocol_path:
                    raise ValueError("both risk asset paths are required")
                self._risk_profile = risk_qualification.load_profile(config.risk_model_path, config.risk_protocol_path)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self._risk_asset_error = type(exc).__name__
        self.email_sender = email_sender
        self.llm_transport = llm_transport
        self.clock_ms = clock_ms or _now_ms
        self.map_source_capture = map_source_capture
        self.ledger = store.UnderwritingLedger(config.ledger_path)
        self.chain_audit = (chain_audit.ChainAuditLog(
            config.state_dir / "chain_audit", interval_ms=config.chain_audit_interval_ms,
            retention_days=config.chain_audit_retention_days,
            prompt_versions={"review_prompt": wpem_review.EVIDENCE_SYNTHESIS_PROMPT_VERSION,
                             "review_protocol": wpem_review.EVIDENCE_POINTS_RESPONSE_PROTOCOL})
            if config.chain_audit_enabled else None)
        self._ledger_cache: tuple[Any, dict[str, Any]] | None = None
        self._state_cache: tuple[Any, dict[str, Any]] | None = None
        self._state_cache_lock = threading.Lock()
        # Retained lock points are immutable, so their compact metrics are cached by point id.
        self._series_cache: dict[str, dict[str, Any]] | None = None
        self._series_baselines: dict[str, Any] = {}
        # Settlement of expired locks: exchange delivery price, fetched in the background only.
        self.delivery_price_fetch = public_quote.delivery_price
        self._settlement_attempts: dict[str, int] = {}
        self._series_lock = threading.Lock()
        self._ledger_cache_lock = threading.Lock()
        self.data_engine = data_engine.DataEngine(config.state_dir / "data_engine")
        self.budget = (signal_llm_review.DailyHttpBudget(
            config.usage_ledger_path, limit=config.daily_http_limit)
            if config.shared_usage_ledger is not None else None)
        self._review_lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._view_lock = threading.Lock()
        self._current_view: dict[str, Any] | None = None
        self._last_refresh_error: dict[str, Any] | None = None
        self._background_stop = threading.Event()
        self._background_thread: threading.Thread | None = None
        # Lock sampling runs in its own thread so a slow full refresh cannot stretch it.
        # It never shares the refresh's payload buffers; WPEM package caches are mutated
        # under _wpem_lock; one sampling pass at a time under _locked_sample_lock.
        self._sampler_thread: threading.Thread | None = None
        self._sampler_running = False
        self._locked_sample_lock = threading.Lock()
        self._wpem_lock = threading.RLock()
        self._wpem_pinned: set[str] = set()
        self._sampler_marks: dict[str, int] = {}
        self._discovery_upstream: tuple[str, int] | None = None
        self._gate_since: tuple[str, int | None] | None = None  # (effective state, since ms)
        self._last_compaction: dict[str, Any] | None = None
        self._last_compaction_ms = 0
        self._map_state_lock = threading.RLock()
        self._map_state_cache: dict[str, Any] | None = None
        self._map_state_migrated = False
        # One serialized page body per view kind, shared by readers for VIEW_BODY_TTL_MS and
        # rebuilt by one reader at a time; _view_version moves on every refresh and action.
        self._body_lock = threading.Lock()        # guards version + cache only; never held while waiting
        self._body_build_lock = threading.Lock()  # one builder at a time; invalidators never take it
        self._body_cache: dict[str, tuple[int, int, bytes, bytes | None]] = {}
        self._view_version = 0
        self._hard_passes = 0
        self.rss_bytes: Callable[[], int | None] = _process_rss_bytes
        self._memory: dict[str, Any] = {"level": "ok", "rss_mb": None, "peak_mb": None}
        self._memory_event_ms = 0
        # Cross-asset reference: fetched in its own thread after each US close, never on a page read.
        self.cross_asset_get_json = cross_asset.default_get_json
        self._cross_asset_thread: threading.Thread | None = None
        self._cross_asset: dict[str, Any] | None = None
        try:
            loaded = json.loads((config.state_dir / "cross_asset.json").read_text(encoding="utf-8"))
            self._cross_asset = loaded if isinstance(loaded, dict) and loaded.get("schema") == cross_asset.SCHEMA else None
        except (OSError, ValueError):
            self._cross_asset = None
        self._reconcile_interrupted_review()
        self._reconcile_scheduled_reviews()
        self._compact_state_once()

    def _compact_state_once(self) -> None:
        """Shrink receipts written before compaction existed; a no-op once compact."""
        if not self.config.state_path.is_file():
            return
        review = self._read_state_cached().get("manual_review") or {}
        full = sum(1 for entry in (review.get("idempotency") or {}).values()
                   if isinstance(entry, Mapping) and isinstance(entry.get("result"), Mapping)
                   and not entry["result"].get("compacted"))
        heavy = _receipt_has_blobs(review.get("last_result")) or any(
            isinstance(entry, Mapping) and _receipt_has_blobs(entry.get("result"))
            for entry in (review.get("idempotency") or {}).values())
        if full > FULL_RECEIPTS_KEPT or heavy:
            self._locked_state_update(lambda state: _compact_review_history(state.setdefault("manual_review", {})))

    def _read_state_cached(self) -> dict[str, Any]:
        """Parse the state file once per version for read-only callers; writers parse fresh."""
        try:
            stat = self.config.state_path.stat()
            key: Any = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            key = None
        with self._state_cache_lock:
            if key is not None and self._state_cache is not None and self._state_cache[0] == key:
                return self._state_cache[1]
        state = self._load_state()
        if key is not None:
            with self._state_cache_lock:
                self._state_cache = (key, state)
        return state

    def _reconcile_interrupted_review(self) -> None:
        """On process start, expose a durable receipt without replaying an LLM call."""
        if not self.config.state_path.is_file():
            return
        prior = self._load_state().get("manual_review")
        if not isinstance(prior, Mapping):
            return
        if not isinstance(prior.get("inflight"), Mapping):
            return

        def update(state: dict[str, Any]) -> None:
            review = state.setdefault("manual_review", {})
            inflight = review.get("inflight")
            if not isinstance(inflight, Mapping):
                return
            key = str(inflight.get("idempotency_key") or "")
            candidate_id = str(inflight.get("candidate_id") or "")
            snapshot_id = str(inflight.get("snapshot_id") or "")
            purpose = str(inflight.get("purpose") or REVIEW_PURPOSE)
            research_only = purpose == OBSERVATION_PURPOSE
            state_root = review_core._review_state_root(self.config.review_state_dir, research_only)
            if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", candidate_id):
                path = (review_core._review_state_path(state_root, candidate_id, snapshot_id)
                        if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", snapshot_id)
                        else state_root / ("candidate-" + candidate_id + ".json"))
                persisted = _read_json(path)
            else:
                persisted = {}
            settled = persisted.get("result") if persisted.get("settled") and isinstance(persisted.get("result"), Mapping) else None
            result = {
                "schema": SCHEMA,
                "status": (settled.get("status") if settled else "interrupted_no_automatic_recall"),
                "candidate_id": candidate_id,
                "snapshot_id": persisted.get("snapshot_id") or snapshot_id or None,
                "side": inflight.get("side"),
                "purpose": purpose,
                "idempotency_key": key,
                "review": deepcopy(dict(settled)) if settled else None,
                "llm_call_count": len(persisted.get("attempts") or []),
            }
            review["inflight"] = None
            review["last_completed_ts_ms"] = self.clock_ms()
            review["last_result"] = _slim_receipt(result)
            if key:
                review.setdefault("idempotency", {})[key] = {
                    "status": "settled", "result": _slim_receipt(result), "updated_at_ms": self.clock_ms()}

        self._locked_state_update(update)

    def _default_discovery_capture(self, *, now_ms: int) -> Mapping[str, Any]:
        def transport(base, path, params, timeout):
            product = {"/public/get_index_price": "deribit.btc.index.v1",
                       "/public/get_instruments": "deribit.btc.option.universe.v1",
                       "/public/get_order_book": "deribit.btc.option.order_book.v1"}[path]
            scope = {**params, "permission": "public_read_only"}
            def acquire():
                body = discovery._public_get(base, path, params, timeout)
                received = self.clock_ms()
                values = body["result"]
                observed = values.get("timestamp") if path == "/public/get_order_book" else received
                return {"values": {"result": values}, "observation_end_ms": observed,
                        "retrieved_at_ms": received, "ingested_at_ms": received}
            record = self.data_engine.fetch(product, acquire, now_ms=self.clock_ms(), scope=scope)
            qualified = self.data_engine.read(product, now_ms=self.clock_ms(), usage="fetch", scope=scope)
            if not qualified.get("usage_decision", {}).get("can_use"):
                code = (record.get("fetch_decision") or {}).get("error_code")
                raise ValueError("discovery input unavailable for current use" + (f" ({code})" if code else ""))
            return {"result": deepcopy(record["content"]["values"]["result"]),
                    "data_engine_receipt": {"record_id": record["record_id"],
                                            "retrieved_at_ms": record["time"]["retrieved_at_ms"],
                                            "observation_end_ms": record["time"]["observation_end_ms"]}}
        failure: dict[str, Exception] = {}

        def loader():
            try:
                values = discovery.capture_public_deribit_discovery(captured_ts_ms=now_ms, transport=transport)
            except Exception as exc:
                failure["error"] = exc
                raise
            captured = values["captured_ts_ms"]
            return {"values": values, "observation_end_ms": captured,
                    "retrieved_at_ms": captured, "ingested_at_ms": captured}
        scope = {"asset": "BTC", "target_hours": 24, "width_usd": 2000,
                 "quantity_btc": 1, "delta_target": .35, "delta_max": .5,
                 "capture_contract": "complete_delta_population@1"}
        try:
            record = self.data_engine.fetch(data_projection.DISCOVERY, loader, now_ms=now_ms, scope=scope)
        except Exception as exc:
            # e.g. retry backoff after a failure: the last known upstream reason still applies
            raise self._discovery_unavailable(exc, None, now_ms, scope) from exc
        if not record.get("usage_decision", {"can_use": True}).get("can_use"):
            raise self._discovery_unavailable(failure.get("error"), record, now_ms, scope)
        self._discovery_upstream = None
        return deepcopy(record["content"]["values"])

    def _discovery_unavailable(self, exc: Exception | None, record: Mapping[str, Any] | None,
                               now_ms: int, scope: Mapping[str, Any]) -> "UpstreamUnavailable":
        """Name why discovery has nothing current: the exchange's own reason when known."""
        upstream = _upstream_code(exc) if exc is not None else None
        if upstream:
            self._discovery_upstream = (upstream, now_ms)
        elif self._discovery_upstream and now_ms - self._discovery_upstream[1] < 30 * 60_000:
            upstream = self._discovery_upstream[0]
        if record is None:
            try:
                record = self.data_engine.read(data_projection.DISCOVERY, now_ms=now_ms, usage="health", scope=scope)
            except Exception:
                record = None
        last_ok = ((record or {}).get("time") or {}).get("observation_end_ms")
        message = "discovery cache unavailable for current use"
        return UpstreamUnavailable(message + (f" ({upstream})" if upstream else ""),
                                   upstream=upstream, last_success_ms=last_ok if isinstance(last_ok, int) else None)

    def _default_quote_capture(
        self,
        short_instrument: str,
        long_instrument: str,
        quantity_btc: float,
        *,
        now_ms: int,
        allow_partial: bool = False,
    ) -> Mapping[str, Any]:
        metadata_clocks = {}
        def transport(path, params):
            product = ("deribit.btc.option.instrument.v1" if path == "public/get_instrument"
                       else "deribit.btc.option.order_book.v1")
            scope = {"instrument_name": params["instrument_name"], "depth": params.get("depth"),
                     "permission": "public_read_only"}
            def loader():
                values = public_quote.public_transport(path, params)
                received = self.clock_ms()
                observed = received if path == "public/get_instrument" else values.get("timestamp")
                return {"values": values, "observation_end_ms": observed,
                        "retrieved_at_ms": received, "ingested_at_ms": received}
            record = self.data_engine.fetch(product, loader, now_ms=self.clock_ms(), scope=scope)
            qualified = self.data_engine.read(product, now_ms=self.clock_ms(), usage="fetch", scope=scope)
            if not qualified.get("usage_decision", {}).get("can_use"):
                raise ValueError("public data unavailable for requested quote use")
            if path == "public/get_instrument":
                metadata_clocks[params["instrument_name"]] = record["time"]["retrieved_at_ms"]
            return deepcopy(record["content"]["values"])
        payload = public_quote.capture_candidate(short_instrument, long_instrument, quantity_btc,
                                                  transport=transport, clock_ms=self.clock_ms,
                                                  allow_partial=allow_partial)
        payload["metadata_received_ts_ms"] = {"short": metadata_clocks[short_instrument],
                                               "long": metadata_clocks[long_instrument]}
        return payload

    def _load_state(self) -> dict[str, Any]:
        state = _read_json(self.config.state_path, {"schema": STATE_SCHEMA})
        state.setdefault("schema", STATE_SCHEMA)
        state.setdefault("space_gate", {})
        state.setdefault("manual_review", {"idempotency": {}})
        state.setdefault("email", {})
        state.setdefault("daily_snapshots", {})
        return state

    def _save_state(self, state: Mapping[str, Any]) -> None:
        _write_json_atomic(self.config.state_path, state)

    def _locked_state_update(self, updater: Callable[[dict[str, Any]], Any]) -> Any:
        self.config.state_dir.mkdir(parents=True, exist_ok=True)
        with store._exclusive_file_lock(self.config.state_path):
            state = self._load_state()
            result = updater(state)
            self._save_state(state)
            return result

    def _map_state(self) -> dict[str, Any]:
        """Cached map state; the first read migrates it from the old place in workbench_state.json."""
        with self._map_state_lock:
            if self._map_state_cache is None:
                path = self.config.btc_map_state_path
                loaded = _read_json(path, None) if path.is_file() else None
                if not isinstance(loaded, Mapping) or not loaded:
                    loaded = self._read_state_cached().get("btc_map")
                self._map_state_cache = dict(loaded) if isinstance(loaded, Mapping) else {}
            return self._map_state_cache

    def _map_prior_state(self) -> dict[str, Any]:
        return deepcopy(self._map_state())

    def _store_btc_map_state(self, btc_map: Mapping[str, Any] | None) -> None:
        if not isinstance(btc_map, Mapping):
            return
        with self._map_state_lock:
            prior = self._map_state()
            updated = map_projection.persistable_state(btc_map, prior)
            if updated == prior and self.config.btc_map_state_path.is_file():
                return
            self.config.state_dir.mkdir(parents=True, exist_ok=True)
            _write_json_atomic(self.config.btc_map_state_path, updated)
            self._map_state_cache = updated
        if not self._map_state_migrated:
            # once the map state has its own file, drop the old copy from workbench_state.json
            self._map_state_migrated = True
            if self._read_state_cached().get("btc_map") is not None:
                self._locked_state_update(lambda state: state.pop("btc_map", None))

    def _map_source_mode(self) -> str:
        mode = str(self.config.map_source_mode or map_projection.SOURCE_MODE_COLLECTOR).strip().lower()
        if mode not in {map_projection.SOURCE_MODE_COLLECTOR, map_projection.SOURCE_MODE_PRODUCER_MANIFEST}:
            raise map_projection.SourceConsumerError("MAP_SOURCE_MODE_INVALID", f"unsupported MAP source mode: {mode}")
        return mode

    def _map_source_config(self) -> dict[str, Any]:
        cfg = dict(self.config.map_source_config or {})
        mode = self._map_source_mode()
        cfg["source_mode"] = mode
        if self.config.map_producer_current_path is not None:
            cfg["producer_current_path"] = str(self.config.map_producer_current_path)
        cfg["producer_max_age_ms"] = int(self.config.map_producer_max_age_ms)
        return cfg

    def _producer_source_engine(
        self,
        now_ms: int,
        *,
        expected_manifest_hash: str | None = None,
    ) -> map_projection.ProducerFreezeSourceEngine:
        if self.config.map_sources_enabled:
            raise map_projection.SourceConsumerError(
                "PRODUCER_SOURCE_SLOW_COLLECTION_CONFLICT",
                "producer_manifest source mode forbids ASTRA_MAP_SOURCES_ENABLED=1",
            )
        if self.config.map_producer_current_path is None:
            raise map_projection.SourceConsumerError(
                "PRODUCER_CURRENT_PATH_MISSING",
                "producer_manifest source mode requires ASTRA_MAP_PRODUCER_CURRENT_PATH",
            )
        return map_projection.load_producer_source_engine(
            self.config.map_producer_current_path,
            now_ms=now_ms,
            qualifier_engine=self.data_engine,
            max_age_ms=int(self.config.map_producer_max_age_ms),
            expected_manifest_hash=expected_manifest_hash,
        )

    def _map_engine_for_projection(
        self,
        now_ms: int,
        projection: Mapping[str, Any] | None = None,
    ) -> Any:
        if self._map_source_mode() != map_projection.SOURCE_MODE_PRODUCER_MANIFEST:
            return self.data_engine
        expected = None
        if isinstance(projection, Mapping):
            consumer = projection.get("source_consumer") if isinstance(projection.get("source_consumer"), Mapping) else {}
            if consumer.get("status") != "OK":
                return None
            expected = consumer.get("producer_manifest_hash")
        return self._producer_source_engine(now_ms, expected_manifest_hash=expected)

    def _data_health(self, now_ms: int, projection: Mapping[str, Any] | None = None) -> dict[str, Any]:
        health = self.data_engine.health(now_ms)
        if self._wpem_archive is not None:
            # A full archive stops WPEM memory; surface it instead of failing silently.
            health["wpem_archive"] = {**self._wpem_archive.usage(),
                                      "sampling": bool(self.config.wpem_history_replay)}
        if self._map_source_mode() != map_projection.SOURCE_MODE_PRODUCER_MANIFEST:
            return health
        projection = projection or {}
        producer = projection.get("producer_health") or {}
        products = producer.get("products") or {}
        # Use the existing source selection, without giving its private cache
        # authority over producer-owned background products.
        owned = {(row.get("identity") or {}).get("product_id") for row in
                 map_sources.read_sources(self.data_engine, now_ms, self._map_source_config())}
        owned.update({map_projection.GLOBAL_PRODUCT, map_projection.KPF_PRODUCT})
        owned.update(products)
        for product_id in owned:
            if product_id in self.data_engine.products:
                health["products"][product_id] = deepcopy(products.get(product_id)) or self.data_engine._health_product(
                    self.data_engine.products[product_id], [], now_ms)
        health["map_source_consumer"] = deepcopy(projection.get("source_consumer") or {"status": "NOT_READY"})
        health["map_source_health_schema"] = producer.get("schema")
        return health

    def _attach_source_consumer(
        self,
        projection: dict[str, Any],
        consumer: Mapping[str, Any],
    ) -> dict[str, Any]:
        projection["source_consumer"] = deepcopy(dict(consumer))
        if isinstance(projection.get("btc_map"), Mapping):
            projection["btc_map"]["source_consumer"] = deepcopy(dict(consumer))
            projection["btc_map"]["map_id"] = map_core._hash({
                key: value for key, value in projection["btc_map"].items() if key != "map_id"
            })
        return projection

    def _producer_failure_projection(self, now_ms: int, fmz_fact: Mapping[str, Any],
                                     exc: map_projection.SourceConsumerError) -> dict[str, Any]:
        projection = map_projection.build_projection(
            None, now_ms=now_ms, fmz_fact=fmz_fact, source_records=[],
            config=self._map_source_config(), prior_state=self._map_prior_state(),
            presentation_mode="LIVE")
        consumer = {
            "status": "ERROR", "mode": map_projection.SOURCE_MODE_PRODUCER_MANIFEST,
            "checked_at_ms": now_ms, "current_path": str(self.config.map_producer_current_path),
            "error": exc.as_dict(), "required_background_complete": False,
            "note_cn": "producer冻结来源不可用；本轮只保留合同、报价、风险和FMZ原生硬门的确定性核算。",
        }
        projection["gaps"] = sorted(set(list(projection.get("gaps") or []) + [
            "producer_map_source_unqualified", str(exc.code).lower()]))
        return self._attach_source_consumer(projection, consumer)

    def _build_btc_map_projection(
        self,
        now_ms: int,
        fmz_fact: Mapping[str, Any],
        *,
        collect_slow_sources: bool,
        collect_local_artifacts: bool = False,
    ) -> dict[str, Any]:
        cfg = self._map_source_config()
        if self._map_source_mode() == map_projection.SOURCE_MODE_PRODUCER_MANIFEST:
            if collect_slow_sources:
                raise map_projection.SourceConsumerError(
                    "PRODUCER_SOURCE_SLOW_COLLECTION_CONFLICT",
                    "producer_manifest source mode cannot collect slow sources",
                )
            try:
                producer_engine = self._producer_source_engine(now_ms)
            except map_projection.SourceConsumerError as exc:
                if exc.code in {"PRODUCER_SOURCE_SLOW_COLLECTION_CONFLICT", "PRODUCER_CURRENT_PATH_MISSING"}:
                    raise
                return self._producer_failure_projection(now_ms, fmz_fact, exc)
            records = map_projection.read_cached_sources(producer_engine, now_ms, cfg)
            projection = map_projection.build_projection(
                producer_engine,
                now_ms=now_ms,
                fmz_fact=fmz_fact,
                source_records=records,
                config=cfg,
                prior_state=self._map_prior_state(),
            )
            consumer = producer_engine.source_summary(now_ms)
            projection["producer_health"] = producer_engine.health(now_ms)
            background = (projection.get("btc_map") or {}).get("background") if isinstance(projection.get("btc_map"), Mapping) else {}
            states = {
                key: (background.get(key) or {}).get("fact_state")
                for key in ("allocation", "inventory", "financing", "external_conditions")
                if isinstance(background, Mapping)
            }
            consumer["background_fact_states"] = states
            consumer["required_background_complete"] = bool(states) and all(value == "usable" for value in states.values())
            self._attach_source_consumer(projection, consumer)
            if not consumer["required_background_complete"]:
                projection["gaps"] = sorted(set(list(projection.get("gaps") or []) + ["producer_map_background_incomplete"]))
            return projection

        gaps: list[str] = []
        records: list[dict[str, Any]] = []
        if collect_local_artifacts:
            try:
                result = map_projection.collect_kpf_artifacts(
                    self.data_engine, now_ms, cfg,
                )
                gaps.extend(result.get("gaps") or [])
            except Exception as exc:
                gaps.extend(["kpf_local_collection_failed", type(exc).__name__])
        try:
            records = map_projection.collect_or_read_sources(
                self.data_engine,
                now_ms,
                enabled=collect_slow_sources,
                config=cfg,
                capture=self.map_source_capture,
            )
        except Exception as exc:
            gaps.append("btc_map_source_read_failed")
            gaps.append(type(exc).__name__)
        projection = map_projection.build_projection(
            self.data_engine,
            now_ms=now_ms,
            fmz_fact=fmz_fact,
            source_records=records,
            config=cfg,
            prior_state=self._map_prior_state(),
        )
        projection["gaps"] = sorted(set(list(projection.get("gaps") or []) + gaps))
        return projection

    def latest_fact(self, valuation_ms: int) -> dict[str, Any]:
        fact = fact_context.latest_from_jsonl(self.config.snapshots_jsonl, valuation_ms)
        return data_projection.qualify_fmz(self.data_engine, fact, valuation_ms)

    def space_gate_view(self, fmz_fact: Mapping[str, Any], valuation_ms: int) -> dict[str, Any]:
        market_facts = fmz_fact.get("market_facts") if isinstance(fmz_fact.get("market_facts"), Mapping) else {}
        native = market_facts.get("space_gate") if isinstance(market_facts, Mapping) else None
        gaps: list[str] = []
        if fmz_fact.get("status") != "current":
            gaps.extend(str(item) for item in (fmz_fact.get("gap_reasons") or []))
            gaps.append("fmz_fact_not_current")
        gaps.extend(str(item) for item in (fmz_fact.get("space_input_gap_reasons") or []))
        if not isinstance(native, Mapping):
            gaps.extend(str(item) for item in (fmz_fact.get("space_gate_gap_reasons") or []))
            gaps.append("producer_native_space_gate_missing")
            native_state = SPACE_BLOCKED
        else:
            native_state = str(native.get("state") or SPACE_BLOCKED)
        rule = underwriting_core.structure_gate(native)
        # Evaluation only (2026-10-13): the space structure never blocks admission. Missing or
        # stale producer facts stay visible as gap reasons, without gating.
        effective_state = SPACE_ELIGIBLE
        anchor = deepcopy(native.get("anchor")) if isinstance(native, Mapping) and isinstance(native.get("anchor"), Mapping) else None
        net_gamma = deepcopy(native.get("net_gamma")) if isinstance(native, Mapping) and isinstance(native.get("net_gamma"), Mapping) else None
        body = {
            "state": effective_state,
            "producer_state": native_state if native_state in {SPACE_ELIGIBLE, SPACE_BLOCKED} else SPACE_BLOCKED,
            "eligible": effective_state == SPACE_ELIGIBLE,
            "source": ("producer_native_space_gate" if isinstance(native, Mapping)
                       else "producer_native_space_gate_missing"),
            "observed_at_ms": native.get("observed_at_ms") if isinstance(native, Mapping) else None,
            "since_ts_ms": self._effective_since(effective_state, native_state, native, valuation_ms),
            "producer_since_ts_ms": native.get("since_ts_ms") if isinstance(native, Mapping) else None,
            "previous_state": native.get("previous_state") if isinstance(native, Mapping) else None,
            "last_transition_ts_ms": native.get("last_transition_ts_ms") if isinstance(native, Mapping) else None,
            "reason_codes": [] if effective_state == SPACE_ELIGIBLE else rule["blocking_codes"],
            "producer_reason_codes": sorted(set(str(item) for item in ((native or {}).get("reason_codes") or [])))
            if isinstance(native, Mapping) else [],
            "anchor_reason_codes": rule["anchor_codes"],
            "gate_basis": rule["basis"],
            "anchor": anchor,
            "net_gamma": net_gamma,
            "fmz_fact_status": fmz_fact.get("status"),
            "fmz_source_hash": fmz_fact.get("source_hash"),
            "fmz_snapshot_ts_ms": fmz_fact.get("snapshot_ts_ms"),
            "strategy_version": fmz_fact.get("strategy_version"),
            "valuation_ts_ms": valuation_ms,
            "gap_reasons": sorted(set(reason for reason in gaps if reason)),
            "gating": False,
            "note_cn": "空间结构只作评价（锚点评分、净 Gamma、Gamma 体制），不作准入门控；不代表补偿充分、LLM支持或交易批准。",
        }
        body["gate_hash"] = _hash({key: body[key] for key in (
            "state", "producer_state", "observed_at_ms", "since_ts_ms",
            "last_transition_ts_ms", "reason_codes", "anchor", "net_gamma",
            "fmz_source_hash", "gap_reasons"
        )})
        return body

    def _effective_since(self, effective_state: str, native_state: str, native: Any, now_ms: int) -> int | None:
        """When the net-gamma admission last changed, as this workbench saw it. The producer's
        since follows its own anchor-inclusive rule, so it is not reused; after a restart the
        since is unknown until the next change."""
        if self._gate_since is None:
            self._gate_since = (effective_state, None)
        elif self._gate_since[0] != effective_state:
            self._gate_since = (effective_state, now_ms)
        return self._gate_since[1]

    def _email_template(self, transition: Mapping[str, Any]) -> tuple[str, str]:
        if transition.get("schema") == ANCHOR_TRANSITION_SCHEMA:
            current = transition.get("current_state")
            anchor = transition.get("anchor") if isinstance(transition.get("anchor"), Mapping) else {}
            label = "有效" if current == ANCHOR_EFFECTIVE else "失效"
            subject = f"[xxproject] 锚点效用：{label}（信息，不是准入）"
            body = "\n".join([
                f"时间：{_iso_utc(int(transition.get('transition_ts_ms') or self.clock_ms()))}",
                f"锚点：评分 {anchor.get('score')}，状态 {anchor.get('state')}，新鲜度 {anchor.get('freshness')}",
                f"准入（只看净 Gamma）：{'放行' if transition.get('admission_state') == SPACE_ELIGIBLE else '关闭'}",
                "",
                "说明：锚点评分描述现价相对 Flip 轴的贴近程度，自 2026-10-13 起只作信息，不再是准入条件。",
            ])
            return subject, body
        current = transition.get("current_state") or SPACE_BLOCKED
        gate = transition.get("space_gate") if isinstance(transition.get("space_gate"), Mapping) else {}
        anchor = gate.get("anchor") if isinstance(gate.get("anchor"), Mapping) else {}
        gamma = gate.get("net_gamma") if isinstance(gate.get("net_gamma"), Mapping) else {}
        label = "可建立候选" if current == SPACE_ELIGIBLE else "当前不准入"
        subject = f"[xxproject] 空间约束状态：{label}"
        body = "\n".join([
            f"时间：{_iso_utc(int(transition.get('transition_ts_ms') or self.clock_ms()))}",
            f"上一状态：{transition.get('previous_state')}",
            f"当前状态：{current}",
            f"Anchor：{anchor.get('score')} / {anchor.get('state')} / ok={anchor.get('ok')}",
            f"NetGamma：{gamma.get('value')} / valid={gamma.get('valid')} / ok={gamma.get('ok')}",
            "理由：" + ",".join(gate.get("reason_codes") or gate.get("gap_reasons") or []),
            "",
            "说明：仅表示基础空间结构门状态切换。",
            "未代表补偿充分、LLM支持或交易批准。请打开当前工作台查看候选与 M_ref。",
        ])
        return subject, body

    def _attempt_state_email(self, state: dict[str, Any], now_ms: int) -> None:
        email = state.setdefault("email", {})
        pending = email.get("pending_transition")
        if not isinstance(pending, Mapping):
            return
        last_emit = int(email.get("last_emit_ts_ms") or 0)
        if last_emit and now_ms - last_emit < self.config.email_cooldown_ms:
            return
        subject, body = self._email_template(pending)
        record = {
            "attempted_at_ms": now_ms,
            "subject": subject,
            "transition_id": pending.get("transition_id"),
            "current_state": pending.get("current_state"),
        }
        if pending.get("current_state") == email.get("last_sent_state"):
            email["pending_transition"] = None
            return
        email["last_emit_ts_ms"] = now_ms
        if self.email_sender is None:
            email["last_status"] = "not_configured"
            email["last_attempt"] = {**record, "status": "not_sent", "reason": "email_sender_not_configured"}
            return
        try:
            result = self.email_sender.send(subject, body)
        except Exception as exc:
            email["last_status"] = "failed"
            email["last_attempt"] = {**record, "status": "failed", "error_type": type(exc).__name__}
            return
        email["last_status"] = "sent"
        email["last_sent_ts_ms"] = now_ms
        email["last_sent_state"] = pending.get("current_state")
        email["last_attempt"] = {**record, "status": "sent", "result": result if isinstance(result, Mapping) else {}}
        email["pending_transition"] = None

    def _record_space_gate_transition(self, gate: Mapping[str, Any], now_ms: int) -> None:
        anchor = gate.get("anchor") if isinstance(gate.get("anchor"), Mapping) else None
        anchor_state = None if anchor is None else (ANCHOR_EFFECTIVE if anchor.get("ok") is True else ANCHOR_INEFFECTIVE)

        def update(state: dict[str, Any]) -> None:
            sg = state.setdefault("space_gate", {})
            sg["last_state"] = gate.get("state")
            sg["last_gate_hash"] = gate.get("gate_hash")
            sg["last_seen_ts_ms"] = now_ms
            previous = sg.get("last_anchor_state")
            if anchor_state is not None:
                sg["last_anchor_state"] = anchor_state
            if previous in {ANCHOR_EFFECTIVE, ANCHOR_INEFFECTIVE} and anchor_state not in {None, previous}:
                transition = {
                    "schema": ANCHOR_TRANSITION_SCHEMA,
                    "kind": "anchor_utility",
                    "transition_id": _hash({"previous": previous, "current": anchor_state,
                                             "gate_hash": gate.get("gate_hash"), "ts": now_ms})[:24],
                    "transition_ts_ms": now_ms,
                    "previous_state": previous,
                    "current_state": anchor_state,
                    "anchor": deepcopy(dict(anchor)),
                    "admission_state": gate.get("state"),
                    "space_gate": deepcopy(dict(gate)),
                    "llm_call_count": 0,
                }
                _append_jsonl(self.config.transitions_path, transition)
                state.setdefault("email", {})["pending_transition"] = transition
            self._attempt_state_email(state, now_ms)

        self._locked_state_update(update)

    def _review_gate(self, snapshot: Mapping[str, Any] | None, gate: Mapping[str, Any]) -> dict[str, Any]:
        reasons: list[str] = []
        if not self.config.server_token or not self.config.api_key or not self._shared_budget_ready():
            reasons.append("manual_review_service_not_configured")
        if gate.get("state") != SPACE_ELIGIBLE:
            reasons.append("structure_gate_not_eligible")
        # The space structure no longer gates, but a non-current or wrong-version FMZ fact is a
        # data qualification problem and still keeps review closed.
        if gate.get("fmz_fact_status") != "current" or "space_gate_producer_version_mismatch" in (gate.get("gap_reasons") or []):
            reasons.append("fmz_fact_not_current")
        if not isinstance(snapshot, Mapping):
            reasons.append("snapshot_missing")
            return {"enabled": False, "reasons": sorted(set(reasons))}
        quote = ((snapshot.get("market") or {}).get("quote") or {})
        economics = snapshot.get("economics") if isinstance(snapshot.get("economics"), Mapping) else {}
        # mu_ref is no longer a precondition (2026-10-12): it has no rigorous
        # producer, and a market-implied mu makes M_ref structurally negative.
        # The fields stay in the contract as null; if a qualified mu ever
        # arrives, a non-positive M_ref still closes the gate below.
        mu_missing = economics.get("mu_ref_btc") is None
        status = snapshot.get("status")
        mu_only_gap = (status == "information_gap" and mu_missing
                       and economics.get("net_credit_after_fee_btc") is not None)
        if status != "reviewable" and not mu_only_gap:
            reasons.append("snapshot_not_reviewable")
        if quote.get("credit_btc") is None:
            reasons.append("quote_invalid_or_stale")
        if (snapshot.get("fee") or {}).get("amount_btc") is None:
            reasons.append("fee_missing_or_incomplete")
        margin = economics.get("reference_margin_btc")
        if not mu_missing and margin is not None:
            try:
                if float(margin) <= 0:
                    reasons.append("m_ref_non_positive")
            except (TypeError, ValueError):
                reasons.append("m_ref_invalid")
        return {"enabled": not reasons, "reasons": sorted(set(reasons))}

    def _capture_candidate(
        self,
        side: str,
        discovered: Mapping[str, Any],
        fmz_fact: Mapping[str, Any],
        gate: Mapping[str, Any],
        now_ms: int,
        btc_map_projection: Mapping[str, Any] | None = None,
        tracking: bool = False,
        sink: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        candidate_info = ((discovered.get("candidates") or {}).get(side) or {})
        if candidate_info.get("status") != "candidate":
            reasons = list(candidate_info.get("gap_reasons") or ["candidate_not_discovered"])
            return {"side": side, "status": "gap", "discovery": deepcopy(candidate_info),
                    "snapshot": None, "review": {"status": "not_attached"},
                    "review_gate": {"enabled": False, "reasons": sorted(set(reasons + ["snapshot_missing"]))},
                    "reading_guidance": guidance.build_reading_guidance(None, fmz_fact)}
        try:
            raw_candidate = candidate_info["candidate"]
            quote_options = {"now_ms": now_ms}
            if tracking and getattr(self.quote_capture, "__func__", None) is AstraWorkbench._default_quote_capture:
                quote_options["allow_partial"] = True
            payload = dict(self.quote_capture(
                raw_candidate["short_instrument"],
                raw_candidate["long_instrument"],
                raw_candidate["quantity_btc"],
                **quote_options,
            ))
            quote_valuation_ms = int((payload.get("market") or {})["valuation_ts_ms"])
            candidate_fact = self.latest_fact(quote_valuation_ms)
            candidate_gate = self.space_gate_view(candidate_fact, quote_valuation_ms)
            payload["origins"] = ["state_refresh"]
            payload["fmz_fact_context"] = deepcopy(dict(candidate_fact))
            if self.risk_provider is not None:
                risk = self.risk_provider(payload, candidate_fact, quote_valuation_ms)
                if risk is not None:
                    payload["risk"] = dict(risk)
            manifest, source_gaps = data_projection.candidate_manifest(
                self.data_engine, payload, candidate_fact, discovered, quote_valuation_ms)
            payload.setdefault("data_identity", {})["source_manifest"] = manifest
            # Routine refreshes replace only the current cache. Persist actual
            # day/manual freezes, rather than every page/collector observation.
            snapshot = underwriting_core.evaluate(payload)
            if isinstance(btc_map_projection, Mapping):
                try:
                    map_engine = self._map_engine_for_projection(quote_valuation_ms, btc_map_projection)
                except map_projection.SourceConsumerError as exc:
                    # An atomic producer update during the quote capture must not
                    # mix MAP versions or erase already evaluated contract math.
                    btc_map_projection = self._producer_failure_projection(quote_valuation_ms, candidate_fact, exc)
                    map_engine = None
                snapshot = map_projection.attach_candidate_map(
                    map_engine,
                    btc_map_projection,
                    snapshot,
                )
            ledger_state = self._ledger_state()
            prior = ledger_state["snapshots"].get(snapshot["snapshot_id"])
            annex = (store.build_card_annex(ledger_state, snapshot["snapshot_id"])
                     if prior else {"review_annex": {"status": "not_attached"}})
            review_gate = self._review_gate(snapshot, candidate_gate)
            if source_gaps:
                review_gate["enabled"] = False
                review_gate["reasons"] = sorted(set(review_gate["reasons"] + source_gaps))
            reading_guidance = guidance.build_reading_guidance(snapshot, candidate_fact)
            risk_diagnosis = None
            if self.risk_provider is None:
                risk_diagnosis = risk_qualification.diagnose(snapshot, self._risk_profile)
                if self._risk_asset_error:
                    risk_diagnosis["gap_reasons"] = ["risk_model_asset_unusable"]
                    risk_diagnosis["asset_error_type"] = self._risk_asset_error
                if self.config.risk_model_path or self.config.risk_protocol_path:
                    reading_guidance["notes"].append({
                        "kind": "risk_reference_gap", "ref": "risk_consumption_diagnosis",
                        "text_cn": "配置的研究模型尚未通过当前合同消费资格；核对资产、期限、完整特征和价系绑定，μ仍缺失。",
                    })
            if (gate.get("fmz_source_hash") != candidate_gate.get("fmz_source_hash")
                    or gate.get("state") != candidate_gate.get("state")):
                review_gate["enabled"] = False
                review_gate["reasons"] = sorted(set(review_gate["reasons"] + ["candidate_gate_version_changed_during_refresh"]))
            if sink is not None:
                # a lock sample keeps its raw payload private to its own thread
                sink[snapshot["snapshot_id"]] = deepcopy(payload)
            else:
                if self.config.research_archive_enabled:
                    self._research_pending_payloads[snapshot["snapshot_id"]] = deepcopy(payload)
                if self.config.wpem_enabled:
                    self._wpem_pending_payloads[snapshot["snapshot_id"]] = deepcopy(payload)
                self._locked_payloads[snapshot["snapshot_id"]] = deepcopy(payload)
            return {
                "side": side,
                "status": "snapshot",
                "discovery": deepcopy(candidate_info),
                "snapshot": snapshot,
                "remaining_to_expiry_ms": max(0, int(snapshot["contract"]["expiry_ms"]) - quote_valuation_ms),
                "space_gate": candidate_gate,
                "review": _compact_review(annex.get("review_annex")),
                "review_gate": review_gate,
                "reading_guidance": reading_guidance,
                "risk_consumption_diagnosis": risk_diagnosis,
                "source_manifest": manifest,
                "capture_gaps": deepcopy(payload.get("capture_gaps") or []),
                # Deribit marks of both legs from the books this capture already read; kept
                # beside the frozen snapshot so its identity and evidence are unchanged.
                "market_marks": _market_marks(payload),
            }
        except Exception as exc:
            return {
                "side": side,
                "status": "error",
                "discovery": deepcopy(candidate_info),
                "snapshot": None,
                "review": {"status": "not_attached"},
                "review_gate": {"enabled": False,
                                "reasons": ["candidate_refresh_error", type(exc).__name__]},
                "reading_guidance": guidance.build_reading_guidance(None, fmz_fact),
                "error": str(exc)[:240],
            }

    def refresh(self, *, now_ms: int | None = None, record_daily: bool = True) -> dict[str, Any]:
        now = self.clock_ms() if now_ms is None else int(now_ms)
        with self._refresh_lock:
            # Wall time per step, so a slow cycle (and the lock sampling cadence that rides
            # on it) can be explained from the view and the audit log.
            timing: dict[str, int] = {}
            clock = [time.perf_counter()]

            def mark(step: str) -> None:
                current = time.perf_counter()
                timing[step] = int((current - clock[0]) * 1000)
                clock[0] = current

            self._research_pending_payloads.clear()
            self._locked_payloads.clear()
            gaps: list[str] = []
            fmz_fact = self.latest_fact(now)
            gate = self.space_gate_view(fmz_fact, now)
            self._record_space_gate_transition(gate, now)
            btc_projection = self._build_btc_map_projection(
                now,
                fmz_fact,
                collect_slow_sources=self.config.map_sources_enabled,
                collect_local_artifacts=True,
            )
            mark("fact_gate_map")
            discovered: Mapping[str, Any] | None = None
            candidates: list[dict[str, Any]] = []
            try:
                discovered = self.discovery_capture(now_ms=now)
                mark("discovery")
            except Exception as exc:
                gaps.append("discovery_refresh_error")
                upstream = getattr(exc, "upstream", None) or _upstream_code(exc)
                if upstream:
                    gaps.append("upstream_" + upstream)
                candidates = [
                    {"side": side, "status": "error", "snapshot": None,
                     "review": {"status": "not_attached"},
                     # reasons are readable codes; the exception type is kept apart
                     "review_gate": {"enabled": False, "reasons": ["discovery_refresh_error"]
                                     + (["upstream_" + upstream] if upstream else [])},
                     "reading_guidance": guidance.build_reading_guidance(None, fmz_fact),
                     "error": str(exc)[:240], "error_type": type(exc).__name__,
                     "upstream": {"code": upstream, "last_success_ms": getattr(exc, "last_success_ms", None)}}
                    for side in ("put", "call")
                ]
            if discovered is not None:
                for side in ("put", "call"):
                    candidates.append(self._capture_candidate(
                        side,
                        discovered,
                        fmz_fact,
                        gate,
                        now,
                        btc_map_projection=btc_projection,
                    ))
            mark("candidates")
            finished_at_ms = max(now, self.clock_ms())
            email_state = self._load_state().get("email") or {}
            last_attempt = email_state.get("last_attempt") or {}
            pending_transition = email_state.get("pending_transition") or {}
            view = {
                "schema": SCHEMA,
                "updated_at_ms": finished_at_ms,
                "market_context": deepcopy(dict(fmz_fact)),
                "space_gate": gate,
                "discovery": deepcopy(discovered),
                "candidates": candidates,
                "btc_map": deepcopy(btc_projection.get("btc_map")),
                "btc_map_projection": deepcopy(btc_projection),
                "transitions": self.recent_transitions(limit=30),
                "daily_snapshots": self.recent_daily_snapshots(limit=self.config.daily_snapshot_limit),
                "manual_review_state": self.manual_review_state(finished_at_ms),
                "notification_state": {
                    "last_status": email_state.get("last_status"),
                    "last_attempt": {key: last_attempt.get(key) for key in (
                        "transition_id", "status", "attempted_at_ms", "reason", "error_type")},
                    "pending_transition_id": pending_transition.get("transition_id"),
                    "cooldown_remaining_ms": max(0, self.config.email_cooldown_ms
                                                - (finished_at_ms - int(email_state.get("last_emit_ts_ms") or 0)))
                    if email_state.get("last_emit_ts_ms") else 0,
                },
                "gaps": sorted(set(gaps + list(gate.get("gap_reasons") or []) + list(btc_projection.get("gaps") or []))),
                "llm_call_count": 0,
                "data_health": self._data_health(finished_at_ms, btc_projection),
            }
            mark("view_health")
            self._attach_wpem(view, finished_at_ms)
            mark("wpem")
            if self._wpem_archive is not None and isinstance(view.get("data_health"), dict):
                view["data_health"]["wpem_archive"] = {**self._wpem_archive.usage(),
                                                       "sampling": bool(self.config.wpem_history_replay)}
            self._remember_lockable(view, finished_at_ms)
            if not self._sampler_running:
                self._refresh_locked_candidates(view, finished_at_ms)
            self._settle_expired_locks(finished_at_ms)
            view["locked_candidates"] = self.locked_candidate_list()
            mark("locked")
            if record_daily:
                self._maybe_record_daily_snapshot(view, finished_at_ms)
                view["daily_snapshots"] = self.recent_daily_snapshots(limit=self.config.daily_snapshot_limit)
            if self.config.research_archive_enabled:
                self._record_research_samples(view, finished_at_ms)
            mark("daily_research")
            view["refresh_timing"] = {"steps_ms": dict(timing), "total_ms": sum(timing.values()),
                                      "loop_wait_s": self.config.refresh_interval_s}
            with self._view_lock:
                # Daily raw snapshots remain on disk and in the explicit full
                # endpoint; do not retain repeated MAP bodies in the read cache.
                cached = {**view, "daily_snapshots": reading.daily_summaries(view.get("daily_snapshots"))}
                self._current_view = deepcopy(cached)
                self._last_refresh_error = None
            self.invalidate_view_body()
            self._store_btc_map_state(btc_projection.get("btc_map"))
            result = self._apply_current_review_availability(view, finished_at_ms)
            # the periodic chain-audit record reads memory from here (cached_view adds its own)
            result["runtime"] = {"memory": dict(self._memory), "threads": threading.active_count()}
            if self.chain_audit is not None:
                self.chain_audit.observe(result, finished_at_ms)
            return result

    def _wpem_view_for_candidate(
        self,
        cached: Mapping[str, Any] | None,
        item: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Rebuild WPEM against the current cached candidate set, replacing only the selected side."""
        candidates: list[dict[str, Any]] = []
        item_side = item.get("side")
        replaced = False
        if isinstance(cached, Mapping):
            for entry in cached.get("candidates") or []:
                if isinstance(entry, Mapping) and entry.get("side") == item_side:
                    candidates.append(deepcopy(dict(item)))
                    replaced = True
                elif isinstance(entry, Mapping):
                    candidates.append(deepcopy(dict(entry)))
            release_identity = deepcopy(cached.get("release_identity"))
        else:
            release_identity = None
        if not replaced:
            candidates.append(deepcopy(dict(item)))
        return {"candidates": candidates, "release_identity": release_identity}

    @staticmethod
    def _candidate_from_view(view: Mapping[str, Any], item: Mapping[str, Any]) -> dict[str, Any]:
        item_snapshot = item.get("snapshot") if isinstance(item.get("snapshot"), Mapping) else {}
        item_id = item_snapshot.get("candidate_id")
        for entry in view.get("candidates") or []:
            snapshot = entry.get("snapshot") if isinstance(entry, Mapping) else None
            if isinstance(snapshot, Mapping) and snapshot.get("candidate_id") == item_id:
                return deepcopy(dict(entry))
        return deepcopy(dict(item))

    def _attach_wpem(self, view: dict[str, Any], now_ms: int, *, prune: bool = True,
                     pending: dict[str, Any] | None = None, only: set[str] | None = None) -> None:
        pending_payloads = self._wpem_pending_payloads if pending is None else pending
        if not isinstance(view.get("release_identity"), Mapping):
            view["release_identity"] = release_core.release_identity()
        if not self.config.wpem_enabled:
            return
        for item in view.get("candidates") or []:
            snapshot = item.get("snapshot")
            if not isinstance(snapshot, Mapping):
                continue
            snapshot_id = snapshot["snapshot_id"]
            if only is not None and snapshot_id not in only:
                continue  # a peer of the composed candidate, not re-composed here
            prior = item.get("review") or {}
            raw_payload = pending_payloads.pop(snapshot_id, None)
            cross_section = []
            excluded_peers = []

            def attach_package(package: Mapping[str, Any], isolated: Mapping[str, Any] | None = None) -> None:
                item["wpem_evidence"] = wpem_workbench.public_evidence(package)
                with self._wpem_lock:
                    self._wpem_packages[snapshot_id] = package
                    self._wpem_display_packages[snapshot_id] = item["wpem_evidence"]
                summary = wpem_review.window_interpretation(package)
                questions = wpem_workbench.program_questions(snapshot, package)
                validity = {"status": wpem_review.review_status(
                    {**prior, "status": "OK" if prior.get("status") == "attached" else prior.get("status")},
                    snapshot, package, model=self.config.model, now_ms=now_ms),
                    "window_evidence_hash": package["same_candidate_window"]["source_window_evidence_hash"],
                    "evidence_hash": package["evidence_hash"],
                    "public_evidence_url": "/api/workbench/wpem-evidence/"+snapshot_id,
                    "full_proof_url": "/api/workbench/wpem-proof/"+snapshot_id}
                if excluded_peers:
                    validity["cross_section_excluded_peers"] = excluded_peers
                if isolated:
                    note = ("历史证据回放未通过（%s），已隔离；本次仅使用当前冻结截面，"
                            "不能形成完整同窗路径。" % isolated["code"])
                    summary = deepcopy(dict(summary))
                    breaks = list(summary.get("breaks_cn") or [])
                    if note not in breaks:
                        breaks.append(note)
                    summary["breaks_cn"] = breaks
                    questions = deepcopy(dict(questions))
                    audit = deepcopy(questions.get("audit") or {})
                    audit["temporal_scope"] = "current_snapshot_only"
                    audit["history_replay_isolated"] = True
                    audit["history_replay_error_code"] = isolated["code"]
                    audit["history_replay_error_type"] = isolated.get("error_type")
                    questions["audit"] = audit
                    validity.update({
                        "reason": "history_replay_isolated_current_only",
                        "history_replay_isolated": True,
                        "history_replay_error_code": isolated["code"],
                        "history_replay_error_type": isolated.get("error_type"),
                        "history_replay_message_cn": "历史证据不可用，已隔离为当前截面解释。",
                        "temporal_scope": "current_snapshot_only",
                    })
                item["wpem_summary"] = summary
                item["current_questions"] = questions
                item["review_validity"] = validity

            try:
                current_packet = candidate_asof.build_packet(snapshot, raw_payload=raw_payload)
                for peer in view.get("candidates") or []:
                    peer_snapshot = peer.get("snapshot")
                    if (not isinstance(peer_snapshot, Mapping)
                            or peer_snapshot["market"]["valuation_ts_ms"] > snapshot["market"]["valuation_ts_ms"]):
                        continue
                    try:
                        candidate_asof.build_packet(peer_snapshot)
                        cross_section.append(peer_snapshot)
                    except Exception as peer_exc:
                        excluded_peers.append(wpem_workbench.non_comparable_peer(peer_snapshot, peer_exc))
                try:
                    if self.config.wpem_history_replay:
                        package = wpem_workbench.compose(snapshot, self._wpem_archive, self.config.snapshots_jsonl,
                            raw_payload=raw_payload, engine=self.data_engine,
                            release=view["release_identity"], now_ms=now_ms,
                            cross_section=cross_section)
                    else:
                        package = wpem_workbench.compose_current(
                            snapshot, self.config.snapshots_jsonl, raw_payload=raw_payload,
                            engine=self.data_engine, release=view["release_identity"],
                            cross_section=cross_section, current_packet=current_packet,
                            native_cache=(self._wpem_archive.path / "native_sources"
                                          if self._wpem_archive is not None else None))
                    wpem_workbench.require_current_package_binding(snapshot, package)
                    attach_package(package)
                except Exception as history_exc:
                    isolated = wpem_workbench.replay_block(history_exc)
                    isolated["error_type"] = type(history_exc).__name__
                    try:
                        package = wpem_workbench.current_only_evidence(
                            snapshot, current_packet=current_packet,
                            release=view["release_identity"], cross_section=cross_section)
                        wpem_workbench.validate_evidence_package(snapshot, package)
                    except Exception as fallback_exc:
                        raise history_exc from fallback_exc
                    attach_package(package, isolated=isolated)
            except Exception as exc:
                block = wpem_workbench.replay_block(exc)
                with self._wpem_lock:
                    if snapshot_id in self._wpem_packages:
                        del self._wpem_packages[snapshot_id]
                    self._wpem_display_packages.pop(snapshot_id, None)
                item["wpem_evidence"] = {"status": "BLOCKED", "reason": block["code"], "error_code": block["code"]}
                item["wpem_summary"] = wpem_workbench.blocked_summary(snapshot, exc)
                item["current_questions"] = wpem_workbench.blocked_program_questions(snapshot, exc)
                has_review = (isinstance(prior, Mapping)
                              and prior.get("status") not in {None, "", "not_attached", "NOT_RUN", "missing"})
                item["review_validity"] = {"status": "STALE" if has_review else "NOT_RUN",
                                           "reason": "evidence_replay_failed",
                                           "error_code": block["code"]}
        pending_payloads.clear()
        current = {(item.get("snapshot") or {}).get("snapshot_id") for item in view.get("candidates") or []}
        if prune:
            with self._wpem_lock:
                # keep packages a lock sample is still turning into a point
                current |= self._wpem_pinned
                for key in list(self._wpem_packages):
                    if key not in current:
                        del self._wpem_packages[key]
                        self._wpem_display_packages.pop(key, None)
                for key in list(self._wpem_display_packages):
                    if key not in self._wpem_packages:
                        self._wpem_display_packages.pop(key, None)

    def _display_wpem_package(self, snapshot_id: str | None, state: Mapping[str, Any], history: Sequence,
                              attached: Mapping[str, Any] | None = None) -> Any:
        # Reconstruct full replay inputs only when an actual same-snapshot
        # receipt needs strict consumption validation. No receipt means no
        # opinion can gain authority from the small presence/hash descriptor.
        receipts = [attached, state.get("last_result"), state.get("previous_valid_result"),
                    *[_history_receipt(row)[0] for row in history if isinstance(row, Mapping)]]
        if any(_receipt_needs_full_proof(snapshot_id, row) for row in receipts):
            return self._wpem_packages.get(snapshot_id)
        return self._wpem_display_packages.get(snapshot_id)

    def wpem_evidence(self, snapshot_id: str, *, full: bool = True) -> dict[str, Any]:
        """Read existing proof only; this endpoint never refreshes or invokes a model."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", snapshot_id):
            raise WorkbenchError(400, "snapshot_id_invalid", "invalid snapshot identity")
        package = self._wpem_packages.get(snapshot_id) if full else self._wpem_display_packages.get(snapshot_id)
        if package is None:
            raise WorkbenchError(404, "wpem_evidence_not_current", "proof is no longer the current snapshot")
        return package if full else deepcopy(package)

    def _remember_lockable(self, view: dict[str, Any], now_ms: int, *,
                           payloads: Mapping[str, Any] | None = None) -> None:
        raw_payloads = self._locked_payloads if payloads is None else payloads
        """Keep a bounded set of exact displayed inputs across refresh races."""
        shown = self._apply_current_review_availability(
            {**view, "candidates": [dict(item) for item in view.get("candidates") or []]}, now_ms)
        for item, displayed in zip(view.get("candidates") or [], shown.get("candidates") or []):
            snapshot = displayed.get("snapshot")
            if not isinstance(snapshot, Mapping):
                item["lock_availability"] = {"enabled": False, "reason": "snapshot_missing"}
                continue
            snapshot_id = snapshot["snapshot_id"]
            try:
                if snapshot_id in self._lockable_points:
                    # An unchanged market repeats the same snapshot id; keep it newest so the
                    # bounded cache does not evict a point a lock is about to sample.
                    with self._view_lock:
                        self._lockable_points.touch(snapshot_id)
                else:
                    packet = candidate_asof.build_packet(
                        snapshot, raw_payload=raw_payloads.get(snapshot_id),
                        source_receipts=candidate_asof.collect_source_receipts(snapshot, self.data_engine),
                        interval_ms=max(1, self.config.locked_sample_interval_ms))
                    point = {
                        "candidate": deepcopy(displayed), "packet": packet,
                        "recorded_at_ms": max(now_ms, int(snapshot["market"]["valuation_ts_ms"]))}
                    with self._view_lock:
                        self._lockable_points[snapshot_id] = point
                item["lock_availability"] = {"enabled": True, "reason": None}
            except Exception as exc:
                item["lock_availability"] = {"enabled": False, "reason": "packet_validation_failed",
                                             "error_type": type(exc).__name__}
        with self._view_lock:
            while len(self._lockable_points) > 12:
                self._lockable_points.pop(next(iter(self._lockable_points)))

    @staticmethod
    def _locked_binding(contract: Mapping[str, Any]) -> dict[str, Any]:
        # No discovery timestamp: this routing binding must not be ingested as
        # a newly observed default-candidate discovery result.
        return {"selection_origin": "locked_contract", "candidates": {
            contract["side"]: {"status": "candidate", "selection_origin": "locked_contract",
                               "candidate": deepcopy(dict(contract))}}}

    def _capture_locked_point(self, episode: Mapping[str, Any], view: Mapping[str, Any], now_ms: int) -> dict[str, Any]:
        contract = episode["contract"]
        fact = self.latest_fact(now_ms)
        gate = self.space_gate_view(fact, now_ms)
        try:
            # The producer map rotates every few minutes; a capture must not bind to the
            # cached view's manifest once it has been replaced.
            projection = self._build_btc_map_projection(now_ms, fact, collect_slow_sources=False)
        except Exception:
            projection = view.get("btc_map_projection")
        sink: dict[str, Any] = {}
        item = self._capture_candidate(
            contract["side"], self._locked_binding(contract), fact, gate, now_ms,
            btc_map_projection=projection, tracking=True, sink=sink)
        if (item.get("snapshot") or {}).get("candidate_id") != episode["candidate_id"]:
            raise WorkbenchError(409, "locked_quote_unavailable", "original contract capture is unavailable")
        snapshot_id = item["snapshot"]["snapshot_id"]
        local_view = self._wpem_view_for_candidate(view, item)
        local_view["space_gate"] = gate
        with self._view_lock:
            had_point = snapshot_id in self._lockable_points
        with self._wpem_lock:
            self._wpem_pinned.add(snapshot_id)
            had_package = snapshot_id in self._wpem_packages
        try:
            # Displayed candidates are cross-section peers only: never re-composed or
            # re-packed from this capture's payloads.
            self._attach_wpem(local_view, max(now_ms, self.clock_ms()), prune=False,
                              pending=dict(sink), only={snapshot_id})
            mine = self._candidate_from_view(local_view, item)
            self._remember_lockable({**local_view, "candidates": [mine]}, max(now_ms, self.clock_ms()), payloads=sink)
        finally:
            with self._wpem_lock:
                self._wpem_pinned.discard(snapshot_id)
        # A capture that is not on the page must not crowd the bounded caches the page's
        # lock and review buttons read.
        with self._view_lock:
            point = self._lockable_points.get(snapshot_id)
            shown = {(entry.get("snapshot") or {}).get("snapshot_id")
                     for entry in (self._current_view or {}).get("candidates") or [] if isinstance(entry, Mapping)}
            if snapshot_id not in shown and not had_point and snapshot_id in self._lockable_points:
                del self._lockable_points[snapshot_id]
        if snapshot_id not in shown and not had_package:
            with self._wpem_lock:
                if snapshot_id in self._wpem_packages:
                    del self._wpem_packages[snapshot_id]
                self._wpem_display_packages.pop(snapshot_id, None)
        if point is None:
            cause = (mine.get("lock_availability") or {}).get("error_type")
            raise WorkbenchError(409, "locked_packet_unavailable" + (":" + cause if cause else ""),
                                 "original contract packet did not validate")
        return point

    def _refresh_locked_candidates(self, view: dict[str, Any], now_ms: int, *, from_refresh: bool = True) -> None:
        """Reuse the collector and exact contract; page reads never come here."""
        self._locked_refresh_error = None
        try:
            listed = self.locked_candidates.list()
        except locked_store.LockedCandidateError as exc:
            self._locked_refresh_error = exc.code
            return
        episodes = [summary for summary in listed if summary.get("status") == locked_store.ACTIVE]
        # A paused or failed policy is not sampled, but it still ends at expiry, so it is
        # settled and aged out by retention like any other.
        for summary in listed:
            expiry = (summary.get("contract") or {}).get("expiry_ms")
            if (summary.get("status") in {locked_store.PAUSED, locked_store.ERROR}
                    and isinstance(expiry, int) and now_ms >= expiry):
                try:
                    self.locked_candidates.record_gap(summary["episode_id"], now_ms=now_ms,
                        reason="contract_expired_settlement_unverified", expired=True)
                except Exception:
                    pass
        if not from_refresh:
            active_ids = {summary["episode_id"] for summary in episodes}
            self._sampler_marks = {k: v for k, v in self._sampler_marks.items() if k in active_ids}
        captured = {}
        for item in view.get("candidates") or []:
            snapshot = item.get("snapshot") or {}
            with self._view_lock:
                point = self._lockable_points.get(snapshot.get("snapshot_id"))
            # a refresh point older than one interval is not a new sample for the sampler
            if point and (from_refresh or now_ms - int(point.get("recorded_at_ms") or 0) < self.config.locked_sample_interval_ms):
                captured[snapshot["candidate_id"]] = point
        for episode in episodes:
            episode_id = episode["episode_id"]
            try:
                if now_ms >= episode["contract"]["expiry_ms"]:
                    self.locked_candidates.record_gap(episode_id, now_ms=now_ms,
                        reason="contract_expired_settlement_unverified", expired=True)
                    continue
                # The sampler wakes once per interval; allow it to run a little early so the
                # time a capture itself takes does not push every sample to two intervals.
                interval = max(1, self.config.locked_sample_interval_ms)
                slack = 0 if from_refresh else min(15_000, interval // 4)
                last = episode["last_attempt_at_ms"]
                if not from_refresh:
                    # Captures run one after another; gate on the pass start, not on when the
                    # previous capture of this policy happened to finish.
                    last = min(last, self._sampler_marks.get(episode_id, last))
                if now_ms - last < interval - slack:
                    continue
                if not from_refresh:
                    self._sampler_marks[episode_id] = now_ms
                point = captured.get(episode["candidate_id"])
                if point is None:
                    point = self._capture_locked_point(episode, view, self.clock_ms())
                    captured[episode["candidate_id"]] = point
                stored = self.locked_candidates.append(episode_id, point, now_ms=max(now_ms, self.clock_ms()))
                if (stored or {}).get("stale_point") and not from_refresh:
                    # the refreshed point predates this policy's last sample: take a fresh one
                    point = self._capture_locked_point(episode, view, self.clock_ms())
                    stored = self.locked_candidates.append(episode_id, point, now_ms=max(now_ms, self.clock_ms()))
                point_id = locked_store._hash(point)
                if ((stored or {}).get("latest_point_id") != point_id
                        or int((stored or {}).get("point_count") or 0) <= int(episode.get("point_count") or 0)):
                    continue  # budget pause or write failure: a gap was recorded, no sample
                metrics = self._warm_series_metrics(episode, point)
                if metrics is not None:
                    try:
                        self.locked_candidates.append_series(episode_id, [{
                            "point_id": point_id, "step": None,
                            "recorded_at_ms": point.get("recorded_at_ms"), "metrics": metrics}])
                    except Exception:
                        pass  # the next retention pass backfills it before thinning
            except Exception as exc:
                reason = getattr(exc, "code", None) or "locked_capture_failed:" + type(exc).__name__
                try:
                    self.locked_candidates.record_gap(episode_id, now_ms=max(now_ms, self.clock_ms()), reason=reason)
                except Exception as write_exc:
                    self._locked_refresh_error = "tracking_state_write_failed:" + type(write_exc).__name__
        if from_refresh:
            self._locked_payloads.clear()

    def normalize_locked_series_once(self) -> dict[str, Any]:
        """Dedupe and sort series files written before the upsert (2026-10-14). Runs once per
        state dir (marker file); originals are copied to series/.bak-<ts>/ first."""
        series_dir = self.locked_candidates.series_dir
        marker = series_dir / ".normalized-20261014"
        report: dict[str, Any] = {"files": 0, "removed": 0, "rewritten": 0}
        if marker.exists() or not series_dir.is_dir():
            return report
        backup = series_dir / (".bak-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(self.clock_ms() / 1000)))
        for path in sorted(series_dir.glob("lce_*.jsonl")):
            try:
                result = self.locked_candidates.normalize_series(path.stem, backup_dir=backup)
            except Exception as exc:
                report.setdefault("errors", []).append(path.stem + ":" + type(exc).__name__)
                continue
            report["files"] += 1
            report["removed"] += result["removed"]
            report["rewritten"] += result["rewritten"]
        if not report.get("errors"):
            marker.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        if self.chain_audit is not None:
            try:
                self.chain_audit.event("lock_series_normalized", self.clock_ms(), report)
            except Exception:
                pass
        return report

    def sample_locked_once(self, now_ms: int | None = None) -> None:
        """One sampling pass from the latest refreshed view, outside the refresh lock."""
        now = self.clock_ms() if now_ms is None else int(now_ms)
        with self._view_lock:
            view = self._current_view
        if not view:
            return
        with self._locked_sample_lock:
            self._refresh_locked_candidates(view, now, from_refresh=False)
            if now - self._last_compaction_ms >= self.config.locked_compact_interval_ms:
                self._last_compaction_ms = now
                self.compact_locked(now)

    def compact_locked(self, now_ms: int) -> dict[str, Any]:
        """Backfill missing metrics rows, then apply the store's retention. Never raises."""
        report: dict[str, Any] = {"at_ms": now_ms, "backfilled": 0}
        try:
            for summary in self.locked_candidates.list():
                episode_id = summary.get("episode_id")
                if summary.get("index_error") or not episode_id:
                    continue
                index = self.locked_candidates.episode_observations(episode_id)
                series = self.locked_candidates.read_series(episode_id)
                rows = [r for r in index["observations"] if r.get("kind") == "point" and r.get("point_id")
                        and not r.get("thinned") and not _current_metrics((series.get(r["point_id"]) or {}).get("metrics"))]
                if not rows:
                    continue
                cache = self._series_metrics_cache()
                need = [r["point_id"] for r in rows if not _current_metrics(cache.get(r["point_id"]))]
                if need:
                    baseline = None
                    for point_id, point, _error in self.locked_candidates.iter_points([index["baseline_point_id"], *need]):
                        if point is None:
                            continue
                        if baseline is None and point_id == index["baseline_point_id"]:
                            baseline = point
                        if baseline is not None and point_id in need:
                            try:
                                metrics = locked_compare.point_metrics(point, baseline)
                            except Exception:
                                continue
                            with self._series_lock:
                                cache[point_id] = metrics
                out = [{"point_id": r["point_id"], "step": r.get("step"), "recorded_at_ms": r.get("recorded_at_ms"),
                        "metrics": cache[r["point_id"]]} for r in rows if _current_metrics(cache.get(r["point_id"]))]
                self.locked_candidates.append_series(episode_id, out)
                report["backfilled"] += len(out)
            report.update(self.locked_candidates.compact(
                now_ms, series_current=lambda row: _current_metrics((row or {}).get("metrics"))))
        except Exception as exc:
            report["error"] = type(exc).__name__
        self._last_compaction = report
        if self.chain_audit is not None and (report.get("deleted_files") or report.get("dropped_episodes") or report.get("error")):
            try:
                self.chain_audit.event("lock_retention", now_ms, report)
            except Exception:
                pass
        return report

    def locked_retention_policy(self) -> dict[str, Any]:
        policy = dict(locked_store.RETENTION_DEFAULTS)
        return {**policy, "compact_interval_ms": self.config.locked_compact_interval_ms,
                "last_compaction": deepcopy(self._last_compaction)}

    LOCK_FLAT_USD = 10.0

    def _series_tail_metrics(self, episode_id: str) -> Mapping[str, Any] | None:
        """Metrics of the newest series row, read from the file's tail only (cached per file version)."""
        try:
            path = self.locked_candidates._series_path(episode_id)
            stat = path.stat()
            key = (stat.st_size, stat.st_mtime_ns)
            cache = self.__dict__.setdefault("_tail_cache", {})
            hit = cache.get(episode_id)
            if hit is not None and hit[0] == key:
                return hit[1]
            with path.open("rb") as handle:
                handle.seek(0, 2)
                handle.seek(max(0, handle.tell() - 16_384))
                lines = handle.read().decode("utf-8", "replace").splitlines()
        except OSError:
            return None
        for line in reversed(lines):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, Mapping) and isinstance(row.get("metrics"), Mapping):
                cache[episode_id] = (key, row["metrics"])
                if len(cache) > 128:
                    cache.pop(next(iter(cache)))
                return row["metrics"]
        return None

    def _series_head_float_pnl(self, episode_id: str) -> float | None:
        """Floating PnL of the first series row that has one: the lock-time value, which is
        already slightly negative (the bid/ask spread paid at lock). Cached once found."""
        cache = self.__dict__.setdefault("_head_float_cache", {})
        if episode_id in cache:
            return cache[episode_id]
        try:
            with self.locked_candidates._series_path(episode_id).open("r", encoding="utf-8") as handle:
                for _, line in zip(range(200), handle):
                    try:
                        metrics = (json.loads(line) or {}).get("metrics") or {}
                    except (ValueError, AttributeError):
                        continue
                    value = metrics.get("float_pnl_btc")
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        cache[episode_id] = float(value)
                        if len(cache) > 128:
                            cache.pop(next(iter(cache)))
                        return float(value)
        except OSError:
            pass
        return None

    def _lock_outcome(self, episode: Mapping[str, Any], settlements: Mapping[str, Any]) -> dict[str, Any]:
        """Profit / loss / flat of one lock, without the amount on the list.

        Expired with a settlement result: that result (the final outcome). Otherwise the
        latest Deribit floating PnL measured from the lock-time floating PnL, so the bid/ask
        spread paid at lock does not read as a loss; in USD at the latest spot."""
        episode_id = str(episode.get("episode_id") or "")
        settled = settlements.get(episode.get("episode_id")) or {}
        out: dict[str, Any] = {"verdict": None, "basis": None, "flat_band_usd": self.LOCK_FLAT_USD}
        usd_value = None
        if (episode.get("status") == "expired" and settled.get("status") == "settled"
                and isinstance(settled.get("settlement_result_usd"), (int, float))):
            out["basis"] = "settlement"
            usd_value = float(settled["settlement_result_usd"])
        else:
            if episode.get("status") == "expired":
                out["basis"] = "floating_at_expiry"
                out["fallback_reason"] = ("credit_missing" if settled.get("status") == "settled"
                                          else "delivery_unavailable" if settled.get("status")
                                          else "settlement_pending")
            else:
                out["basis"] = "floating_now" if episode.get("status") == "active" else "floating_at_stop"
            metrics = self._series_tail_metrics(episode_id) or {}
            pnl, spot = metrics.get("float_pnl_btc"), metrics.get("spot_usd")
            start = self._series_head_float_pnl(episode_id)
            if isinstance(pnl, (int, float)) and isinstance(spot, (int, float)) and start is not None:
                usd_value = (float(pnl) - start) * float(spot)
                out["measured_from"] = "lock_time_floating_pnl"
        if usd_value is not None:
            out["verdict"] = ("flat" if abs(usd_value) <= self.LOCK_FLAT_USD
                              else "profit" if usd_value > 0 else "loss")
        return out

    def locked_candidate_list(self) -> dict[str, Any]:
        try:
            episodes = self.locked_candidates.list()
            error = self._locked_refresh_error
        except locked_store.LockedCandidateError as exc:
            episodes, error = [], exc.code
        try:
            settlements = self.locked_settlements()
        except Exception:
            settlements = {}
        for episode in episodes:
            if isinstance(episode, dict):
                try:
                    episode["outcome"] = self._lock_outcome(episode, settlements)
                except Exception:
                    episode["outcome"] = {"verdict": None, "basis": None}
        return {"schema": "locked_candidate_list@1.0.0", "episodes": episodes,
                "error": error, "sample_interval_ms": self.config.locked_sample_interval_ms,
                "max_active": self.config.locked_max_active, "max_samples": self.config.locked_max_samples,
                "max_bytes": self.config.locked_max_bytes, "llm_call_count": 0}

    def locked_candidate_detail(self, episode_id: str, *, include_raw: bool = True,
                                baseline_point_id: str | None = None,
                                latest_point_id: str | None = None) -> dict[str, Any]:
        if not include_raw:
            with self._locked_detail_lock:
                return self._locked_candidate_reading(episode_id)
        return self._locked_candidate_projection(episode_id, include_raw=include_raw,
            baseline_point_id=baseline_point_id, latest_point_id=latest_point_id)

    def record_action(self, kind: str, result: Mapping[str, Any] | None = None, *,
                      request: Mapping[str, Any] | None = None, error_code: str | None = None) -> None:
        """Append one user action to the main-chain audit log; never raises."""
        if self.chain_audit is None:
            return
        request = request or {}
        result = result or {}
        review = result.get("review") if isinstance(result.get("review"), Mapping) else {}
        episode = result.get("episode") if isinstance(result.get("episode"), Mapping) else {}
        detail = {
            "ok": error_code is None,
            "error_code": error_code,
            "candidate_id": request.get("candidate_id") or result.get("candidate_id"),
            "snapshot_id": request.get("snapshot_id") or result.get("snapshot_id"),
            "purpose": request.get("purpose"),
            "idempotency_key": request.get("idempotency_key"),
            "status": result.get("status") or review.get("status"),
            "reason": result.get("reason"),
            "llm_call_count": result.get("llm_call_count"),
            "applicability": review.get("applicability"),
            "response_protocol": review.get("response_protocol"),
            "created": result.get("created"),
            "episode_id": episode.get("episode_id") or request.get("episode_id"),
            "episode_status": episode.get("status"),
            "new_reference": request.get("new_reference"),
            "source_episode_id": request.get("source_episode_id"),
        }
        with self._view_lock:
            view = self._current_view or {}
            context = {"market_context": {"current_price_usd": (view.get("market_context") or {}).get("current_price_usd"),
                                          "strategy_version": (view.get("market_context") or {}).get("strategy_version")},
                       "space_gate": {"state": (view.get("space_gate") or {}).get("state")}}
        self.chain_audit.event(kind, self.clock_ms(), {k: v for k, v in detail.items() if v is not None}, context)

    def locked_candidate_series(self, episode_id: str, *, max_points: int = 480) -> dict[str, Any]:
        """Per-point metrics of one locked policy over time; no LLM, no market refresh."""
        if not re.fullmatch(r"lce_[0-9a-f]{24}", str(episode_id or "")):
            raise WorkbenchError(400, "invalid_episode_id", "invalid locked policy id")
        try:
            index = self.locked_candidates.episode_observations(episode_id)
        except locked_store.LockedCandidateError as exc:
            raise WorkbenchError(404 if exc.code == "episode_not_found" else 409, exc.code, exc.code) from exc
        observations = index["observations"]
        stored_series = self.locked_candidates.read_series(episode_id)
        points = [row for row in observations if row.get("kind") == "point" and row.get("point_id")]
        # rows folded out of the index after retention live on in the series file
        known = {row["point_id"] for row in points}
        points += [{"kind": "point", "point_id": pid, "recorded_at_ms": row.get("recorded_at_ms"),
                    "step": row.get("step"), "thinned": True}
                   for pid, row in stored_series.items() if pid not in known]
        points.sort(key=lambda row: (int(row.get("recorded_at_ms") or 0), str(row.get("point_id"))))
        gaps = [{"recorded_at_ms": row.get("recorded_at_ms"), "reason": (row.get("gap") or {}).get("reason"),
                 "expired": bool((row.get("gap") or {}).get("expired"))}
                for row in observations if row.get("kind") != "point"]
        total_points = len(points)
        all_points = list(points)
        # Long episodes are thinned evenly; the first and last point are always kept.
        if len(points) > max_points:
            stride = (len(points) - 1) / (max_points - 1)
            points = [points[round(i * stride)] for i in range(max_points)]
        cache = self._series_metrics_cache()
        local = {pid: row["metrics"] for pid, row in stored_series.items()}
        with self._series_lock:
            missing = [row["point_id"] for row in points if not row.get("thinned")
                       and not _current_metrics(local.get(row["point_id"]))
                       and not _current_metrics(cache.get(row["point_id"]))]
        computed: dict[str, dict[str, Any]] = {}
        if missing:
            baseline = None
            wanted = [index["baseline_point_id"], *[pid for pid in missing if pid != index["baseline_point_id"]]]
            for point_id, point, error in self.locked_candidates.iter_points(wanted):
                if point is None:
                    continue
                if baseline is None and point_id == index["baseline_point_id"]:
                    baseline = point
                if baseline is None:
                    break
                if point_id in missing:
                    try:
                        computed[point_id] = locked_compare.point_metrics(point, baseline)
                    except Exception:
                        continue
            if computed:
                with self._series_lock:
                    cache.update(computed)
                    if len(cache) > 20_000:
                        for key in list(cache)[: len(cache) - 20_000]:
                            cache.pop(key, None)
                    snapshot = dict(cache)
                # A GET never writes the locked store; retention backfills the series file.
                self._save_series_cache(snapshot)
        series, unreadable = [], 0
        for row in points:
            pid = row["point_id"]
            metrics = next((m for m in (computed.get(pid), local.get(pid), cache.get(pid)) if _current_metrics(m)),
                           None) or local.get(pid) or cache.get(pid)
            if metrics is None:
                unreadable += 1
                continue
            series.append({"step": row.get("step"), "recorded_at_ms": row.get("recorded_at_ms"),
                           "point_id": pid, **metrics})
        ledger_rows = []
        for row in all_points:
            pid = row["point_id"]
            metrics = next((m for m in (computed.get(pid), local.get(pid), cache.get(pid)) if _current_metrics(m)),
                           None) or local.get(pid) or cache.get(pid)
            if metrics is not None:
                ledger_rows.append({"point_id": pid, "recorded_at_ms": row.get("recorded_at_ms"), "metrics": metrics})
        episode = index["episode"]
        contract = episode.get("contract") if isinstance(episode.get("contract"), Mapping) else {}
        try:
            ledger = thesis_ledger.build(
                ledger_rows, side=str(contract.get("side") or ""), now_ms=self.clock_ms(),
                interval_ms=self.config.locked_sample_interval_ms,
                baseline_point_id=episode.get("t0_baseline_point_id"),
                expiry_ms=contract.get("expiry_ms"), active=episode.get("status") == "active")
        except Exception as exc:
            ledger = {"schema": thesis_ledger.SCHEMA, "status": "ERROR", "error_type": type(exc).__name__,
                      "theses": [], "llm_call_count": 0}
        return {"schema": locked_compare.SERIES_SCHEMA, "episode": index["episode"],
                "settlement": self.locked_settlements().get(episode_id),
                "thesis_ledger": ledger,
                "hold_review": self.hold_review_state(episode_id, episode=episode, ledger=ledger),
                "points": series, "gaps": gaps, "thinned": len(points) < total_points,
                "retention": self.locked_retention_policy(),
                "unreadable_points": unreadable, "llm_call_count": 0, "execution_allowed": False}

    def _warm_series_metrics(self, episode: Mapping[str, Any], point: Mapping[str, Any]) -> dict[str, Any] | None:
        """The appended point is in memory: compute its metrics now instead of re-reading it later."""
        try:
            point_id = locked_store._hash(point)
            cache = self._series_metrics_cache()
            if _current_metrics(cache.get(point_id)):
                return cache[point_id]
            baseline_id = episode["t0_baseline_point_id"]
            baseline = self._series_baselines.get(baseline_id)
            if baseline is None:
                found = next(self.locked_candidates.iter_points([baseline_id]), (None, None, "missing"))
                if found[1] is None:
                    return None
                baseline = self._series_baselines[baseline_id] = found[1]
                if len(self._series_baselines) > 16:
                    self._series_baselines.pop(next(iter(self._series_baselines)))
            metrics = locked_compare.point_metrics(point, baseline)
            with self._series_lock:
                cache[point_id] = metrics
            return metrics
        except Exception:
            return None  # the series endpoint computes it on demand instead

    SETTLEMENT_SCHEMA = "locked_candidate_settlement@1.0.0"
    SETTLEMENT_RETRY_MS = 10 * 60_000
    SETTLEMENT_GIVE_UP_MS = 48 * 3_600_000

    def _settlements_path(self) -> Path:
        return self.config.state_dir / "locked_settlements.json"

    def locked_settlements(self) -> dict[str, Any]:
        loaded = _read_json(self._settlements_path(), {})
        rows = loaded.get("episodes") if loaded.get("schema") == self.SETTLEMENT_SCHEMA else None
        return dict(rows) if isinstance(rows, Mapping) else {}

    def _settle_expired_locks(self, now_ms: int) -> None:
        """After expiry: delivery price -> terminal payout -> t0 net credit minus payout. Never raises."""
        try:
            settled = self.locked_settlements()
            pending = [e for e in self.locked_candidates.list()
                       if e.get("status") == "expired" and e["episode_id"] not in settled
                       and isinstance((e.get("contract") or {}).get("expiry_ms"), int)
                       and now_ms >= e["contract"]["expiry_ms"] + 2 * 60_000]
        except Exception:
            return
        changed = False
        for episode in pending:
            episode_id = episode["episode_id"]
            if now_ms - self._settlement_attempts.get(episode_id, 0) < self.SETTLEMENT_RETRY_MS:
                continue
            self._settlement_attempts[episode_id] = now_ms
            contract = episode["contract"]
            row: dict[str, Any] = {"episode_id": episode_id, "expiry_ms": contract["expiry_ms"], "settled_at_ms": now_ms,
                                   "actual_trade_pnl_recorded": False, "llm_call_count": 0}
            try:
                delivery = self.delivery_price_fetch(contract["expiry_ms"])
            except Exception as exc:
                delivery, row["fetch_error"] = None, type(exc).__name__
            if delivery is None:
                if now_ms - contract["expiry_ms"] < self.SETTLEMENT_GIVE_UP_MS:
                    continue  # not published yet; retry later
                row.update(status="delivery_price_unavailable")
            else:
                quantity = float(contract.get("quantity_btc") or 1.0)
                payout = underwriting_core.inverse_spread_payout(contract["side"], contract["short_strike_usd"],
                                                          contract["long_strike_usd"], delivery["delivery_price_usd"], quantity)
                entry = None
                found = next(self.locked_candidates.iter_points([episode["t0_baseline_point_id"]]), (None, None, None))
                if found[1] is not None:
                    snapshot = locked_compare._point_snapshot(found[1])
                    entry = locked_compare._number(((snapshot.get("economics") or {}).get("net_credit_after_entry_fee_btc")))
                row.update(status="settled", delivery=delivery, payout_btc=payout,
                           entry_after_fee_btc=entry,
                           settlement_result_btc=None if entry is None else entry - payout,
                           settlement_result_usd=None if entry is None else (entry - payout) * delivery["delivery_price_usd"],
                           meaning_cn="按交易所交割价计算的到期静态结果：锁定时扣入场费后信用减去到期赔付（币本位）；交割费按 0；不是实际成交记录。")
            settled[episode_id] = row
            changed = True
            if self.chain_audit is not None:
                self.chain_audit.event("lock_settled", now_ms, {k: v for k, v in row.items() if v is not None})
        if changed:
            try:
                path = self._settlements_path()
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps({"schema": self.SETTLEMENT_SCHEMA, "episodes": settled},
                                          ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
                os.replace(tmp, path)
            except OSError:
                pass

    def _series_cache_path(self) -> Path:
        return self.config.state_dir / "locked_series_cache.json"

    def _series_metrics_cache(self) -> dict[str, dict[str, Any]]:
        """Metrics of immutable points survive restarts; a damaged file is simply rebuilt."""
        with self._series_lock:
            if self._series_cache is None:
                loaded = _read_json(self._series_cache_path(), {})
                points = loaded.get("points") if loaded.get("schema") == locked_compare.SERIES_SCHEMA else None
                self._series_cache = dict(points) if isinstance(points, Mapping) else {}
            return self._series_cache

    def _save_series_cache(self, points: Mapping[str, Any]) -> None:
        try:
            path = self._series_cache_path()
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"schema": locked_compare.SERIES_SCHEMA, "points": points},
                                      ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass  # the cache only saves time

    def _locked_candidate_reading(self, episode_id: str) -> dict[str, Any]:
        episode = next((item for item in self.locked_candidate_list()["episodes"]
                        if item["episode_id"] == episode_id), None)
        if episode is None:
            raise WorkbenchError(404, "episode_not_found", "locked policy not found")
        if episode.get("index_error"):
            raise WorkbenchError(409, "index_integrity_error", "this policy index is unavailable")
        # Only selected files are stat'ed; unchanged immutable points need not be
        # decompressed, replayed and projected for each reader/tab.
        paths = [self.locked_candidates.index_path, self.config.state_path]
        signature = []
        for path in paths:
            try:
                stat = path.stat()
                signature.append((str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
            except FileNotFoundError:
                signature.append((str(path), None))
        try:
            signature += [self.locked_candidates.point_read_signature(episode[key])
                          for key in ("t0_baseline_point_id", "latest_point_id")]
        except locked_store.LockedCandidateError as exc:
            raise WorkbenchError(409, exc.code, "this policy evidence is unavailable") from exc
        cached = self._locked_detail_cache.get(episode_id)
        if cached is None or cached[0] != signature:
            result = self._locked_candidate_projection(episode_id, include_raw=False)
            # The page reads each row's text and values. Per-row raw sources (hundreds of KB for
            # the context rows) stay behind the exact-pair evidence endpoint.
            for row in result.get("rows") or []:
                if isinstance(row, dict) and row.pop("raw", None) is not None:
                    row["raw_deferred"] = True
            self._locked_detail_cache[episode_id] = (signature, result)
            while len(self._locked_detail_cache) > 4:
                self._locked_detail_cache.pop(next(iter(self._locked_detail_cache)))
        else:
            result = cached[1]
        result = deepcopy(result)
        result["settlement"] = self.locked_settlements().get(episode_id)
        expiry = result.get("fixed_contract", {}).get("expiry_ms")
        if isinstance(expiry, int):
            result["overview"]["remaining_to_expiry_ms"] = max(0, expiry - self.clock_ms())
        result["sampling"]["collector_error"] = self._locked_refresh_error or self._last_refresh_error
        return result

    def _locked_candidate_projection(self, episode_id: str, *, include_raw: bool,
                                     baseline_point_id: str | None = None,
                                     latest_point_id: str | None = None) -> dict[str, Any]:
        try:
            detail = (self.locked_candidates.detail_pair(episode_id, baseline_point_id, latest_point_id)
                      if baseline_point_id is not None and latest_point_id is not None
                      else self.locked_candidates.detail(episode_id))
            result = locked_compare.build_followup(detail, now_ms=self.clock_ms(), include_raw=include_raw)
        except locked_store.LockedCandidateError as exc:
            raise WorkbenchError(404 if exc.code == "episode_not_found" else 409, exc.code, exc.code) from exc
        result["sampling"] = {"interval_ms": self.config.locked_sample_interval_ms,
                              "sampler_thread": self._sampler_running,
                              "retention": self.locked_retention_policy(),
                              "collector_error": self._locked_refresh_error or self._last_refresh_error,
                              "max_active": self.config.locked_max_active}
        latest = detail.get("latest") or {}
        candidate = deepcopy(latest.get("candidate") or {})
        snapshot = candidate.get("snapshot") or {}
        review_state, history = self.manual_review_state(), self._manual_review_receipt_history()
        candidate["review_display"] = review_display_for_candidate(
            candidate, review_state,
            wpem_package=self._display_wpem_package(snapshot.get("snapshot_id"), review_state, history, candidate.get("review")),
            model=self.config.model, now_ms=self.clock_ms(),
            accepted_history=history)
        accepted = locked_compare.public_review_explanation(candidate)
        if accepted is not None:
            # A later review is a separate annex. Neither immutable point nor
            # baseline's explanation is changed by a current display read.
            result["explanations"]["latest"]["accepted_review"] = accepted
            result["explanations"]["latest"]["review_added_after_point_capture"] = True
        result["llm_call_count"] = 0
        return result

    def candidate_evidence(self, snapshot_id: str) -> dict[str, Any]:
        with self._view_lock:
            point = self._lockable_points.get(snapshot_id)
        if point is None:
            raise WorkbenchError(409, "evidence_not_retained", "this exact displayed input is no longer retained")
        return locked_compare._public_point(point)

    def lock_candidate(self, candidate_id: str, snapshot_id: str, request_key: str, *,
                       authorization: str | None, new_reference: bool = False,
                       source_episode_id: str | None = None) -> dict[str, Any]:
        if not self._authorized(authorization):
            raise WorkbenchError(401, "unauthorized", "valid Authorization: Bearer token is required")
        if (not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", snapshot_id)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_key)):
            raise WorkbenchError(400, "bad_lock_request", "snapshot_id and idempotency_key are required")
        if source_episode_id is not None and (not isinstance(source_episode_id, str)
                or not re.fullmatch(r"lce_[0-9a-f]{24}", source_episode_id)):
            raise WorkbenchError(400, "bad_lock_request", "invalid source episode")
        if source_episode_id and not new_reference:
            raise WorkbenchError(400, "new_reference_required", "source episode requires explicit new_reference")
        # Cache publication is brief; local persistence must not wait for the
        # collector's network-wide refresh lock. Published points are immutable.
        try:
            if source_episode_id and not candidate_id:
                candidate_id = self.locked_candidates.detail(source_episode_id)["episode"]["candidate_id"]
            existing = self.locked_candidates.lookup_request(request_key, candidate_id=candidate_id,
                snapshot_id=snapshot_id, new_reference=new_reference, source_episode_id=source_episode_id)
            if existing is not None:
                return {**existing, "llm_call_count": 0, "execution_allowed": False}
            if source_episode_id:
                point = self.locked_candidates.retained_point(source_episode_id, snapshot_id)
            else:
                with self._view_lock:
                    point = self._lockable_points.get(snapshot_id)
            if point is None or point["candidate"]["snapshot"]["snapshot_id"] != snapshot_id:
                raise WorkbenchError(409, "lock_snapshot_not_retained", "displayed input is no longer retained; refresh explicitly")
            if point["candidate"]["snapshot"]["candidate_id"] != candidate_id:
                raise WorkbenchError(409, "lock_candidate_mismatch", "snapshot does not belong to selected contract")
            if self.clock_ms() >= point["candidate"]["snapshot"]["contract"]["expiry_ms"]:
                raise WorkbenchError(409, "contract_expired", "expired contract cannot start regular observation")
            result = self.locked_candidates.lock(point, request_key=request_key, now_ms=self.clock_ms(),
                                                 new_reference=new_reference, source_episode_id=source_episode_id)
        except locked_store.LockedCandidateError as exc:
            raise WorkbenchError(409, exc.code, exc.code) from exc
        return {**result, "llm_call_count": 0, "execution_allowed": False}

    def stop_locked_candidate(self, episode_id: str, *, authorization: str | None) -> dict[str, Any]:
        if not self._authorized(authorization):
            raise WorkbenchError(401, "unauthorized", "valid Authorization: Bearer token is required")
        try:
            episode = self.locked_candidates.stop(episode_id, now_ms=self.clock_ms())
        except locked_store.LockedCandidateError as exc:
            raise WorkbenchError(404 if exc.code == "episode_not_found" else 409, exc.code, exc.code) from exc
        return {"episode": episode, "llm_call_count": 0, "execution_allowed": False}

    HOLD_REVIEW_COOLDOWN_MS = 5 * 60_000
    HOLD_REVIEW_FAILED_COOLDOWN_MS = 60_000  # a paid call that failed: no rapid re-clicks
    HOLD_REVIEW_INFLIGHT_MS = 5 * 60_000
    # A review right after locking only restates the lock: require some held history.
    HOLD_REVIEW_MIN_HISTORY_MS = 15 * 60_000

    @staticmethod
    def _ledger_history_ms(ledger: Mapping[str, Any] | None) -> int:
        coverage = (ledger or {}).get("coverage") or {}
        t0, latest = coverage.get("t0_ms"), coverage.get("latest_ms")
        return latest - t0 if isinstance(t0, int) and isinstance(latest, int) else 0

    def _hold_cooldown_ms(self, slot: Mapping[str, Any], now: int) -> int:
        waits = []
        last_ok = slot.get("last_ok_at_ms")
        if isinstance(last_ok, int):
            waits.append(self.HOLD_REVIEW_COOLDOWN_MS - (now - last_ok))
        last_failed = slot.get("last_failed_at_ms")
        if isinstance(last_failed, int):
            waits.append(self.HOLD_REVIEW_FAILED_COOLDOWN_MS - (now - last_failed))
        return max([0, *waits])

    def hold_review_state(self, episode_id: str, *, now_ms: int | None = None,
                          episode: Mapping[str, Any] | None = None,
                          ledger: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Last hold review of one lock and whether a new one can run now (no model call)."""
        now = self.clock_ms() if now_ms is None else now_ms
        slot = ((self._read_state_cached().get("hold_review") or {}).get(episode_id) or {})
        reasons = []
        if not self.config.server_token or not self.config.api_key or not self._shared_budget_ready():
            reasons.append("manual_review_service_not_configured")
        if isinstance(episode, Mapping) and episode.get("status") != "active":
            reasons.append("lock_not_active")
        inflight = slot.get("inflight_since_ms")
        if isinstance(inflight, int) and now - inflight < self.HOLD_REVIEW_INFLIGHT_MS:
            reasons.append("hold_review_inflight")
        cooldown = self._hold_cooldown_ms(slot, now)
        if cooldown:
            reasons.append("hold_review_cooldown")
        early = 0
        if ledger is not None:
            early = max(0, self.HOLD_REVIEW_MIN_HISTORY_MS - self._ledger_history_ms(ledger))
            if early:
                reasons.append("hold_review_too_early")
        return {"schema": hold_review.RESULT_SCHEMA, "last": deepcopy(slot.get("last")),
                "history_needed_ms": early,
                "last_failed": deepcopy(slot.get("last_failed")),
                "enabled": not reasons, "reasons": reasons, "cooldown_remaining_ms": cooldown}

    def hold_review_locked(self, episode_id: str, idempotency_key: str, *,
                           authorization: str | None) -> dict[str, Any]:
        """One model explanation of a held lock's ledger. Synchronous; one budgeted call."""
        if not self._authorized(authorization):
            raise WorkbenchError(401, "unauthorized", "valid Authorization: Bearer token is required")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,128}", idempotency_key or ""):
            raise WorkbenchError(400, "idempotency_key_invalid", "idempotency_key is required")
        if not self.config.api_key or not self._shared_budget_ready():
            raise WorkbenchError(503, "manual_review_service_not_configured", "model review is not configured")
        # A replayed key returns its stored result even after the lock has ended.
        stored = (((self._read_state_cached().get("hold_review") or {}).get(episode_id) or {})
                  .get("keys") or {}).get(idempotency_key)
        if stored is not None:
            return deepcopy(stored)
        series = self.locked_candidate_series(episode_id, max_points=2)
        episode = series.get("episode") or {}
        ledger = series.get("thesis_ledger") or {}
        if episode.get("status") != "active":
            raise WorkbenchError(409, "lock_not_active", "only a lock that is still held can be reviewed")
        if ledger.get("status") != "OK":
            raise WorkbenchError(409, "thesis_ledger_unavailable", "the lock has no ledger yet")
        if self._ledger_history_ms(ledger) < self.HOLD_REVIEW_MIN_HISTORY_MS:
            raise WorkbenchError(409, "hold_review_too_early", "review after 15 minutes of held history")
        now = self.clock_ms()

        def begin(state: dict[str, Any]) -> dict[str, Any] | None:
            slots = state.setdefault("hold_review", {})
            # most recently used last, so trimming never drops the lock being reviewed
            slot = slots.pop(episode_id, None) or {}
            slots[episode_id] = slot
            done = (slot.get("keys") or {}).get(idempotency_key)
            if done is not None:
                return deepcopy(done)
            inflight = slot.get("inflight_since_ms")
            if isinstance(inflight, int) and now - inflight < self.HOLD_REVIEW_INFLIGHT_MS:
                raise WorkbenchError(409, "hold_review_inflight", "a hold review of this lock is running")
            if self._hold_cooldown_ms(slot, now):
                raise WorkbenchError(429, "hold_review_cooldown", "wait before reviewing this lock again")
            slot["inflight_since_ms"] = now
            for key in list(slots)[:-16]:  # keep the state small: at most 16 locks
                slots.pop(key, None)
            return None

        replay = self._locked_state_update(begin)
        if replay is not None:
            return replay
        result: dict[str, Any] = {"schema": hold_review.RESULT_SCHEMA, "episode_id": episode_id,
                                  "idempotency_key": idempotency_key, "requested_at_ms": now,
                                  "input_schema": hold_review.INPUT_SCHEMA,
                                  "prompt_version": hold_review.PROMPT_VERSION, "model": self.config.model,
                                  "ledger_at_ms": (ledger.get("coverage") or {}).get("latest_ms"),
                                  "llm_call_count": 0, "execution_allowed": False}
        # Everything after the in-flight flag is inside try/finally: the flag always clears.
        try:
            contract = episode.get("contract") if isinstance(episode.get("contract"), Mapping) else {}
            review_input = hold_review.build_input(
                ledger, contract, now_ms=now,
                events=event_calendar.contract_events(now, contract.get("expiry_ms")))
            digest = hold_review.input_hash(review_input)
            result["input_hash"] = digest
            try:
                reservation = self.budget.reserve(1, role="astra_hold_review", packet_hash=digest,
                                                  model=self.config.model)
            except signal_llm_review.DailyBudgetExceeded:
                result.update(status="BUDGET_EXHAUSTED", reason="daily_budget_exhausted")
                return result
            send = self.llm_transport or signal_llm_review._post_chat_completion
            result["llm_call_count"] = 1
            try:
                response = send(self.config.api_key, self.config.model,
                                hold_review.build_request(review_input, self.config.model),
                                self.config.review_timeout_s, endpoint=self.config.endpoint)
            except Exception as exc:
                self._complete_budget(reservation, "ERROR")
                result.update(status="CALL_FAILED", reason=type(exc).__name__)
                return result
            try:
                usage = signal_llm_review._response_usage(response)
            except Exception:
                usage = None
            self._complete_budget(reservation, "HTTP_OK", usage)
            try:
                answer = signal_llm_review.parse_chat_response(response, strict_json_object=True)
                result["answer"] = hold_review.validate(answer, review_input)
                result["status"] = "OK"
            except Exception as exc:  # any malformed answer is an invalid response, never a crash
                reason = str(exc) if isinstance(exc, hold_review.HoldReviewInvalid) else type(exc).__name__
                result.update(status="INVALID_RESPONSE", reason=reason[:80])
            return result
        except Exception as exc:
            result.update(status="ERROR", reason=type(exc).__name__)
            return result
        finally:
            result.setdefault("status", "ERROR")
            result["finished_at_ms"] = self.clock_ms()

            def finish(state: dict[str, Any]) -> None:
                slot = state.setdefault("hold_review", {}).setdefault(episode_id, {})
                slot["inflight_since_ms"] = None
                keys = slot.setdefault("keys", {})
                keys[idempotency_key] = deepcopy(result)
                for key in list(keys)[:-8]:
                    keys.pop(key, None)
                if result["status"] == "OK":
                    slot["last"] = deepcopy(result)
                    slot["last_ok_at_ms"] = result["finished_at_ms"]
                elif result.get("llm_call_count"):
                    slot["last_failed"] = deepcopy(result)
                    slot["last_failed_at_ms"] = result["finished_at_ms"]

            try:
                self._locked_state_update(finish)
            except Exception:
                pass

    def _complete_budget(self, reservation: Mapping[str, Any] | None, status: str, usage: Any = None) -> None:
        try:
            self.budget.complete((reservation or {}).get("reservation_id"), status, usage)
        except Exception:
            pass

    def _record_research_samples(self, view: dict[str, Any], recorded_at_ms: int) -> None:
        """Optional local retention; failures do not erase the current contract view."""
        view["research_archive"] = {"enabled": True, "setup_error_type": self._research_setup_error,
                                   "feature_catalog_error_type": self._research_feature_catalog_error}
        for candidate in view["candidates"]:
            if self._research_setup_error or self.research_archive is None:
                candidate["research_archive"] = {"status": "error", "reason": "research_archive_setup_failed"}
            elif not isinstance(candidate.get("snapshot"), Mapping):
                candidate["research_archive"] = {"status": "skipped", "reason": "verified_snapshot_missing"}
            else:
                try:
                    candidate["research_archive"] = self.research_archive.sample(
                        candidate["snapshot"], recorded_at_ms=recorded_at_ms,
                        feature_catalog=self._research_feature_catalog,
                        feature_catalog_error=self._research_feature_catalog_error,
                        raw_payload=self._research_pending_payloads.pop(candidate["snapshot"]["snapshot_id"], None),
                        source_receipts=candidate_asof.collect_source_receipts(candidate["snapshot"], self.data_engine))
                except Exception as exc:
                    candidate["research_archive"] = {"status": "error", "reason": "research_archive_write_failed",
                                                       "error_type": type(exc).__name__}
        self._research_pending_payloads.clear()

    def refresh_cross_asset(self, now_ms: int | None = None, *, force: bool = False) -> dict[str, Any] | None:
        now = self.clock_ms() if now_ms is None else now_ms
        if not force and not cross_asset.due(self._cross_asset, now):
            return self._cross_asset
        result = cross_asset.collect(self.cross_asset_get_json, now)
        try:
            json.dumps(result, allow_nan=False)  # a non-finite value must never reach the page
        except (TypeError, ValueError):
            result = {"schema": cross_asset.SCHEMA, "status": "invalid_values", "computed_at_ms": now}
        previous = self._cross_asset
        if result.get("status") != "ok" and isinstance(previous, dict) and previous.get("status") == "ok":
            # keep the last good reading on screen with its own clock; the failure is shown beside it
            result = {**previous, "last_attempt": {"computed_at_ms": now, "status": result.get("status"),
                                                   "error_type": result.get("error_type")}}
        self._cross_asset = result
        try:
            _write_json_atomic(self.config.state_dir / "cross_asset.json", result)
        except (OSError, TypeError, ValueError):
            pass
        return result

    def cross_asset_view(self, candidates: Sequence[Mapping[str, Any]], now_ms: int) -> dict[str, Any] | None:
        current = self._cross_asset
        expiries = []
        for item in candidates or []:
            snapshot = item.get("snapshot") if isinstance(item, Mapping) else None
            contract = snapshot.get("contract") if isinstance(snapshot, Mapping) else None
            expiry = contract.get("expiry_ms") if isinstance(contract, Mapping) else None
            if isinstance(expiry, int) and not isinstance(expiry, bool):
                expiries.append(expiry)
        try:
            window = cross_asset.contract_window(now_ms, min(expiries)) if expiries else None
        except Exception:  # a background card must never break the page
            window = None
        if current is None and window is None:
            return None
        return {**(current or {"schema": cross_asset.SCHEMA, "status": "not_collected"}), "contract_window": window}

    @staticmethod
    def _attach_exogenous_events(view: dict[str, Any], now_ms: int) -> None:
        """Scheduled US macro releases from the static calendar: the next two weeks for the page,
        and per candidate the ones inside its remaining life. Pure lookup, display only."""
        try:
            view["exogenous_events"] = event_calendar.upcoming(now_ms)
        except Exception as exc:  # a display module must never break the page
            view["exogenous_events"] = {"schema": event_calendar.SCHEMA, "error_type": type(exc).__name__}
        for item in view.get("candidates") or []:
            snapshot = item.get("snapshot") if isinstance(item, Mapping) else None
            contract = snapshot.get("contract") if isinstance(snapshot, Mapping) else None
            expiry = contract.get("expiry_ms") if isinstance(contract, Mapping) else None
            try:
                item["exogenous_events"] = event_calendar.contract_events(now_ms, expiry)
            except Exception:
                item["exogenous_events"] = None

    def invalidate_view_body(self) -> None:
        # Safe under any other lock: _body_lock is only ever held for a dict/int update.
        with self._body_lock:
            self._view_version += 1
            self._body_cache.clear()

    def _cached_body(self, view_kind: str, now: int, *, allow_stale_ms: int = 0) -> tuple[bytes, bytes | None] | None:
        with self._body_lock:
            hit = self._body_cache.get(view_kind)
            if hit is None:
                return None
            if hit[0] == self._view_version and 0 <= now - hit[1] < VIEW_BODY_TTL_MS:
                return hit[2], hit[3]
            if allow_stale_ms and 0 <= now - hit[1] < allow_stale_ms:
                return hit[2], hit[3]
            return None

    def view_body(self, view_kind: str) -> tuple[bytes, bytes | None]:
        """Serialized GET /api/workbench body (page or reading), built once per VIEW_BODY_TTL_MS
        and shared. Lock order: the builder takes _body_build_lock, then (inside cached_view)
        _view_lock; it holds _body_lock only for the final store, never while waiting. Under
        memory shedding a body up to 2 minutes old is served rather than building one."""
        now = self.clock_ms()
        hard = self._memory.get("level") == "hard"
        hit = self._cached_body(view_kind, now, allow_stale_ms=120_000 if hard else 0)
        if hit is not None:
            return hit
        if hard:
            raise WorkbenchError(503, "memory_pressure", "工作台内存紧张，稍后自动重试")
        if not self._body_build_lock.acquire(timeout=VIEW_BODY_WAIT_S):
            raise WorkbenchError(503, "server_busy", "工作台繁忙，稍后自动重试")
        try:
            hit = self._cached_body(view_kind, self.clock_ms())  # built while we waited
            if hit is not None:
                return hit
            with self._body_lock:
                version = self._view_version
            built_at = self.clock_ms()
            result = self.cached_view(reading_only=True)
            payload = reading.workbench_page(result) if view_kind == "page" else result
            body = (_canonical(dict(payload)) + "\n").encode("utf-8")
            packed = gzip.compress(body, compresslevel=1, mtime=0) if len(body) > 65536 else None
            with self._body_lock:
                if version == self._view_version:  # not invalidated meanwhile
                    self._body_cache[view_kind] = (version, built_at, body, packed)
            return body, packed
        finally:
            self._body_build_lock.release()

    def memory_guard(self, now_ms: int | None = None) -> dict[str, Any]:
        """Called after every refresh pass. Soft: drop caches, collect, skip optional work.
        Hard: page reads get the last body (or 503), heavy reads 503. Events are rate-limited."""
        now = self.clock_ms() if now_ms is None else now_ms
        rss = self.rss_bytes()
        if rss is None:
            return self._memory
        mb = rss / 1_048_576
        level = "hard" if mb >= self.config.memory_hard_mb else "soft" if mb >= self.config.memory_soft_mb else "ok"
        previous = self._memory.get("level")
        if level != "ok":
            with self._view_lock:
                current = {((item.get("snapshot") or {}).get("snapshot_id"))
                           for item in (self._current_view or {}).get("candidates") or []
                           if isinstance(item, Mapping) and isinstance(item.get("snapshot"), Mapping)}
            with self._wpem_lock:
                pinned = set(self._wpem_pinned)
                for key in list(self._wpem_packages):
                    if key not in current and key not in pinned:
                        del self._wpem_packages[key]
            with self._locked_detail_lock:
                self._locked_detail_cache.clear()
            with self._series_lock:
                self._series_baselines.clear()
            gc.collect()
            after = self.rss_bytes()
            mb_after = after / 1_048_576 if after is not None else mb
            level = "hard" if mb_after >= self.config.memory_hard_mb else "soft" if mb_after >= self.config.memory_soft_mb else "ok"
        peak = max(mb, self._memory.get("peak_mb") or 0)
        self._memory = {"level": level, "rss_mb": round(mb, 1), "peak_mb": round(peak, 1),
                        "soft_mb": self.config.memory_soft_mb, "hard_mb": self.config.memory_hard_mb,
                        "checked_at_ms": now}
        # level changes are recorded; flapping is limited to one record per interval (hard always)
        if level != previous and (level == "hard" or now - self._memory_event_ms >= MEMORY_EVENT_INTERVAL_MS):
            self._memory_event_ms = now
            audit = getattr(self, "chain_audit", None)
            if audit is not None:
                try:
                    audit.event("memory_" + level, now, dict(self._memory))
                except Exception:
                    pass
        return self._memory

    def cached_view(self, *, reading_only: bool = False) -> dict[str, Any]:
        """Read the collector's cache. Page reads never cause external requests."""
        now = self.clock_ms()
        with self._view_lock:
            view = ({**self._current_view,
                     "candidates": [dict(item) for item in self._current_view.get("candidates") or []]}
                    if reading_only and self._current_view is not None else deepcopy(self._current_view))
            failure = deepcopy(self._last_refresh_error)
        if view is None:
            fact = self.latest_fact(now)
            warm = {"schema": SCHEMA, "updated_at_ms": None, "served_at_ms": now,
                    "locked_candidates": self.locked_candidate_list(),
                    "market_context": fact, "space_gate": self.space_gate_view(fact, now),
                    "candidates": [], "gaps": ["data_collector_warming_up"],
                    "daily_snapshots": self.recent_daily_snapshots(),
                    "transitions": self.recent_transitions(),
                    "manual_review_state": self.manual_review_state(now), "llm_call_count": 0,
                    "data_health": self._data_health(now), "cross_asset": self.cross_asset_view([], now)}
            self._attach_exogenous_events(warm, now)
            result = self._attach_decision_reduction(self._apply_current_review_availability(warm, now))
            return reading.workbench_reading(result) if reading_only else result
        # Recheck facts and current use, without overwriting frozen candidate evidence.
        fact = self.latest_fact(now)
        gate = self.space_gate_view(fact, now)
        view.update({"served_at_ms": now, "market_context": fact, "space_gate": gate,
                     "locked_candidates": self.locked_candidate_list(),
                     "manual_review_state": self.manual_review_state(now),
                     "cross_asset": self.cross_asset_view(view.get("candidates") or [], now),
                     "runtime": {"memory": dict(self._memory), "threads": threading.active_count()}})
        self._attach_exogenous_events(view, now)
        usage_engines: dict[Any, Any] = {}
        view["btc_map_current_usage"] = self._map_current_usage(view.get("btc_map"), now, engines=usage_engines)
        # Global display is a current cache projection. Candidate MAPs stay frozen.
        current_projection = (view.get("btc_map_projection") or {}) if reading_only else self._build_btc_map_projection(now, fact, collect_slow_sources=False)
        view["btc_map"] = current_projection.get("btc_map") if reading_only else deepcopy(current_projection.get("btc_map"))
        if not reading_only:
            view["data_health"] = self._data_health(now, current_projection)
            view["daily_snapshots"] = self.recent_daily_snapshots(limit=self.config.daily_snapshot_limit)
        prior_map_gaps = set((view.get("btc_map_projection") or {}).get("gaps") or [])
        view["gaps"] = sorted((set(view.get("gaps") or []) - prior_map_gaps)
                              | set(current_projection.get("gaps") or []))
        view["btc_map_projection"] = current_projection if reading_only else deepcopy(current_projection)
        for item in view.get("candidates") or []:
            snapshot = item.get("snapshot") or {}
            quoted_at = (snapshot.get("market") or {}).get("valuation_ts_ms")
            reasons = list((item.get("review_gate") or {}).get("reasons") or [])
            if not isinstance(quoted_at, int) or not 0 <= now - quoted_at <= 5_000:
                reasons.append("cached_quote_not_current")
            if not gate.get("eligible"):
                reasons.append("space_gate_not_eligible")
            if gate.get("fmz_source_hash") != (item.get("space_gate") or {}).get("fmz_source_hash"):
                reasons.append("candidate_gate_version_changed_during_refresh")
            item["current_usage"] = {"checked_at_ms": now,
                                     "quote_current": isinstance(quoted_at, int) and 0 <= now - quoted_at <= 5_000}
            # One candidate's broken receipt or map identity must not fail every reader's page.
            try:
                item["btc_map_current_usage"] = self._map_current_usage(
                    (snapshot.get("data_identity") or {}).get("btc_map"), now, engines=usage_engines)
                item["btc_map_review_delta"] = self._map_review_delta(snapshot, view["manual_review_state"])
                prior=item.get('review') or {}
                package=(self._wpem_packages.get(snapshot.get('snapshot_id'))
                         if _receipt_needs_full_proof(snapshot.get('snapshot_id'), prior)
                         else self._wpem_display_packages.get(snapshot.get('snapshot_id'))) if self.config.wpem_enabled else None
                if package is not None:
                    prior=item.get('review') or {}
                    item['review_validity']={**(item.get('review_validity') or {}),
                        'status':wpem_review.review_status(
                            {**prior,'status':'OK' if prior.get('status')=='attached' else prior.get('status')},
                            snapshot,package,model=self.config.model,now_ms=now),'checked_at_ms':now}
            except Exception as exc:
                item["projection_error"] = type(exc).__name__
                reasons.append("candidate_projection_failed")
            if reasons:
                item["review_gate"] = {"enabled": False, "reasons": sorted(set(reasons))}
        if failure:
            view["collector_failure"] = failure
            view["gaps"] = sorted(set(view.get("gaps", []) + ["data_collector_refresh_failed"]))
        view["scheduled_review"] = self.scheduled_review_status(now)
        result = self._attach_decision_reduction(self._apply_current_review_availability(view, now))
        return reading.workbench_reading(result) if reading_only else result

    def _map_current_usage(self, mapping: Mapping[str, Any] | None, now_ms: int, *,
                           engines: dict[Any, Any] | None = None) -> dict[str, Any]:
        """Requalify source identities without mutating the frozen MAP or calling HTTP."""
        if not isinstance(mapping, Mapping):
            return {"status": "MISSING", "checked_at_ms": now_ms, "changed_sources": [], "unusable_sources": []}
        consumer = mapping.get("source_consumer") if isinstance(mapping.get("source_consumer"), Mapping) else {}
        if self._map_source_mode() == map_projection.SOURCE_MODE_PRODUCER_MANIFEST and consumer.get("status") != "OK":
            return {"status": "ERROR", "checked_at_ms": now_ms, "changed_sources": [], "unusable_sources": [],
                    "error": deepcopy(consumer.get("error") or {"code": "PRODUCER_SOURCE_UNQUALIFIED"})}
        try:
            key = consumer.get("producer_manifest_hash")
            if engines is not None and key in engines:
                engine = engines[key]
                if isinstance(engine, map_projection.SourceConsumerError):
                    raise engine
            else:
                try:
                    engine = self._map_engine_for_projection(now_ms, {"source_consumer": mapping.get("source_consumer")})
                except map_projection.SourceConsumerError as exc:
                    if engines is not None:
                        engines[key] = exc
                    raise
                if engines is not None:
                    engines[key] = engine
        except map_projection.SourceConsumerError as exc:
            return {"status": "ERROR", "checked_at_ms": now_ms, "changed_sources": [], "unusable_sources": [],
                    "error": exc.as_dict()}
        changed, unusable = [], []
        rows = (mapping.get("source_manifest") or {}).get("source_records") or []
        for row in rows:
            identity = row.get("identity") or {}
            product = identity.get("product_id")
            if not product:
                continue
            usage = (row.get("usage_qualification") or {}).get("usage") or "background"
            current = engine.read(product, now_ms=now_ms, usage=usage, scope=identity.get("scope"))
            if not current.get("usage_decision", {}).get("can_use"):
                unusable.append(product)
            if current.get("record_id") != row.get("record_id"):
                changed.append(product)
        return {"status": "REQUIRES_RECHECK" if changed or unusable else "UNCHANGED" if rows else "MISSING",
                "checked_at_ms": now_ms, "frozen_map_id": mapping.get("map_id"),
                "changed_sources": sorted(set(changed)), "unusable_sources": sorted(set(unusable)),
                "note_cn": "当前缓存重新核对，仅提示来源变更或失效；不覆盖冻结账，也不自动补发评审。"}

    def _ledger_state(self) -> dict[str, Any]:
        """Replay the append-only ledger once per file change; callers only read it."""
        try:
            st = Path(self.ledger.path).stat()
            key: Any = (st.st_size, st.st_mtime_ns)
        except OSError:
            key = None
        with self._ledger_cache_lock:
            if key is not None and self._ledger_cache is not None and self._ledger_cache[0] == key:
                return self._ledger_cache[1]
        state = self.ledger.state()
        if key is not None:
            with self._ledger_cache_lock:
                self._ledger_cache = (key, state)
        return state

    def _map_review_delta(self, snapshot: Mapping[str, Any], review_state: Mapping[str, Any]) -> dict[str, Any]:
        receipt = review_state.get("last_result") or {}
        if receipt.get("candidate_id") != snapshot.get("candidate_id") or not receipt.get("snapshot_id"):
            return {}
        reviewed = (self._ledger_state().get("snapshots", {}).get(receipt["snapshot_id"]) or {}).get("snapshot")
        if not isinstance(reviewed, Mapping):
            return {"summary_cn": "最近回执的冻结资料未读回；不套用到当前保单。"}
        differences = []
        for key, name in (("contract", "合同"), ("market", "报价与估值时间"), ("fee", "费用假设"),
                          ("risk", "冻结风险参考"), ("fact_context", "FMZ事实截面")):
            if reviewed.get(key) != snapshot.get(key):
                differences.append(name + "已有变化，旧评审不能覆盖当前版本。")
        old_map = (reviewed.get("data_identity") or {}).get("btc_map") or {}
        new_map = (snapshot.get("data_identity") or {}).get("btc_map") or {}
        for name, title in (("allocation", "配置"), ("inventory", "库存"),
                            ("financing", "融资"), ("external_conditions", "外部条件")):
            if (old_map.get("background") or {}).get(name) != (new_map.get("background") or {}).get(name):
                differences.append(title + "背景版本已有变化。")
        return {"summary_cn": "最近评审仍绑定原冻结版，当前资料已有变化。" if differences else
                "最近评审与本张候选冻结版一致；仍需核对当前来源资格。",
                "differences_cn": differences, "reviewed_at_ms": review_state.get("last_completed_ts_ms"),
                "same_snapshot": receipt.get("snapshot_id") == snapshot.get("snapshot_id")}

    def _maybe_record_daily_snapshot(self, view: Mapping[str, Any], now_ms: int) -> None:
        if _bjt_hour(now_ms) != 23:
            return
        day = _bjt_day(now_ms)

        def update(state: dict[str, Any]) -> None:
            daily = state.setdefault("daily_snapshots", {})
            if daily.get("last_bjt_day") == day:
                return
            for item in view.get("candidates") or []:
                if isinstance(item.get("snapshot"), Mapping):
                    self.ledger.record_snapshot(item["snapshot"], now_ms=now_ms)
            row = {
                "schema": DAILY_SNAPSHOT_SCHEMA,
                "snapshot_id": _hash({"day": day, "updated_at_ms": view.get("updated_at_ms"),
                                      "gate": view.get("space_gate")})[:24],
                "bjt_day": day,
                "recorded_ts_ms": now_ms,
                "space_gate": deepcopy(view.get("space_gate")),
                "candidates": [
                    {
                        "side": item.get("side"),
                        "snapshot_id": (item.get("snapshot") or {}).get("snapshot_id")
                        if isinstance(item.get("snapshot"), Mapping) else None,
                        "candidate_id": (item.get("snapshot") or {}).get("candidate_id")
                        if isinstance(item.get("snapshot"), Mapping) else None,
                        "status": item.get("status"),
                        "contract": deepcopy((item.get("snapshot") or {}).get("contract")),
                        "liability": deepcopy((item.get("snapshot") or {}).get("liability")),
                        "market": deepcopy((item.get("snapshot") or {}).get("market")),
                        "fee": deepcopy((item.get("snapshot") or {}).get("fee")),
                        "risk": deepcopy((item.get("snapshot") or {}).get("risk")),
                        "economics": deepcopy((item.get("snapshot") or {}).get("economics")),
                        "gap_reasons": deepcopy((item.get("snapshot") or {}).get("gap_reasons")),
                        "manual_decision": (item.get("snapshot") or {}).get("manual_decision"),
                        "btc_map": deepcopy((((item.get("snapshot") or {}).get("data_identity") or {}).get("btc_map"))),
                        "review": deepcopy(item.get("review")),
                        "review_gate": deepcopy(item.get("review_gate")),
                        "reading_guidance": deepcopy(item.get("reading_guidance")),
                    }
                    for item in view.get("candidates") or []
                ],
                "manual_review_state": deepcopy(view.get("manual_review_state")),
                "btc_map": deepcopy(view.get("btc_map")),
                "gaps": deepcopy(view.get("gaps")),
                "data_health": deepcopy(view.get("data_health")),
                "source_manifests": [deepcopy(item.get("source_manifest")) for item in view.get("candidates") or []],
                "btc_map_manifests": [
                    deepcopy(((((item.get("snapshot") or {}).get("data_identity") or {}).get("btc_map") or {}).get("source_manifest")))
                    for item in view.get("candidates") or []
                ],
                "versions": {"workbench": SCHEMA,
                             "fmz_strategy": ((view.get("space_gate") or {}).get("strategy_version"))},
                "llm_call_count": 0,
            }
            _append_jsonl(self.config.daily_snapshots_path, row)
            daily["last_bjt_day"] = day
            daily["last_snapshot_id"] = row["snapshot_id"]

        self._locked_state_update(update)
        self._rotate_daily_snapshots()

    def recent_transitions(self, *, limit: int = 30) -> list[dict[str, Any]]:
        rows = _read_jsonl(self.config.transitions_path)
        return rows[-max(0, int(limit)):]

    def recent_daily_snapshots(self, *, limit: int = DEFAULT_DAILY_LIMIT) -> list[dict[str, Any]]:
        return _read_jsonl_tail(self.config.daily_snapshots_path, max(0, int(limit)))

    def _rotate_daily_snapshots(self) -> None:
        """Move older daily snapshots to a gzip archive once the live file grows large."""
        path = self.config.daily_snapshots_path
        try:
            if path.stat().st_size <= DAILY_SNAPSHOT_ROTATE_BYTES:
                return
            keep = _read_jsonl_tail(path, DAILY_SNAPSHOT_KEEP_AFTER_ROTATE)
            archive = path.with_name(f"daily_snapshots-archive-{self.clock_ms()}.jsonl.gz")
            with path.open("rb") as source, gzip.open(archive, "wb", compresslevel=6) as target:
                while True:
                    block = source.read(1 << 20)
                    if not block:
                        break
                    target.write(block)
            tmp = path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8", newline="\n") as handle:
                for row in keep:
                    handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            os.replace(tmp, path)
        except OSError:
            return

    def manual_review_state(self, now_ms: int | None = None) -> dict[str, Any]:
        now = self.clock_ms() if now_ms is None else int(now_ms)
        state = self._read_state_cached()
        review = state.get("manual_review") if isinstance(state.get("manual_review"), Mapping) else {}
        last_started = int(review.get("last_started_ts_ms") or 0)
        cooldown_until = max(0, last_started + self.config.review_cooldown_ms)
        if cooldown_until <= now:
            cooldown_until = 0
        previous_valid = None
        last_result = review.get("last_result") or {}
        if isinstance(last_result, Mapping) and last_result and last_result.get("status") not in {"OK", "RESEARCH_OK", "attached"}:
            # Historical fallback is derived from existing immutable receipts.
            # It never replaces the failed attempt or applies to a new snapshot.
            successes = [row for row in (review.get("idempotency") or {}).values()
                         if isinstance(row, Mapping) and isinstance(row.get("result"), Mapping)
                         and not row["result"].get("compacted")
                         and row["result"].get("status") in {"OK", "RESEARCH_OK", "attached"}]
            if successes:
                saved = max(successes, key=lambda row: int(row.get("updated_at_ms") or 0))
                previous_valid = _review_receipt_for_display(saved["result"])
                previous_valid.update(historical_only=True,
                                      historical_completed_ts_ms=saved.get("updated_at_ms"))
        return {
            "cooldown_until_ms": cooldown_until,
            "cooldown_remaining_ms": max(0, cooldown_until - now),
            "inflight": deepcopy(review.get("inflight")),
            "last_started_ts_ms": review.get("last_started_ts_ms"),
            "last_completed_ts_ms": review.get("last_completed_ts_ms"),
            "last_result": _review_receipt_for_display(review.get("last_result")),
            "previous_valid_result": previous_valid,
            "daily_http_budget": self.budget.snapshot() if self._shared_budget_ready() else None,
        }

    def _manual_review_receipt_history(self) -> list[dict[str, Any]]:
        state = self._read_state_cached()
        review = state.get("manual_review") if isinstance(state.get("manual_review"), Mapping) else {}
        history: list[dict[str, Any]] = []
        for key, entry in (review.get("idempotency") or {}).items():
            if not isinstance(entry, Mapping) or not isinstance(entry.get("result"), Mapping):
                continue
            if entry["result"].get("compacted"):
                continue  # outcome only; the full receipt is in review_state/
            row = dict(entry)  # freshly parsed state; shallow copy is enough
            row["idempotency_key"] = key
            history.append(row)
        return history

    def review_availability(
        self,
        now_ms: int | None = None,
        *,
        manual_review_state: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = (deepcopy(dict(manual_review_state))
                 if isinstance(manual_review_state, Mapping)
                 else self.manual_review_state(now_ms))
        reasons: list[str] = []
        if isinstance(state.get("inflight"), Mapping):
            reasons.append(MANUAL_REVIEW_INFLIGHT_REASON)
        try:
            if int(state.get("cooldown_remaining_ms") or 0) > 0:
                reasons.append(MANUAL_REVIEW_COOLDOWN_REASON)
        except (TypeError, ValueError):
            pass
        if self._budget_exhausted(state.get("daily_http_budget")):
            reasons.append(MANUAL_REVIEW_BUDGET_EXHAUSTED_REASON)
        return {"enabled": not reasons, "reasons": sorted(set(reasons))}

    def _budget_exhausted(self, budget_state: Any) -> bool:
        if not isinstance(budget_state, Mapping):
            return False
        try:
            used = int(budget_state.get("http_calls_used") or 0)
            limit = int(budget_state.get("http_calls_limit", self.config.daily_http_limit))
        except (TypeError, ValueError):
            return False
        return limit >= 0 and used >= limit

    def _merge_review_availability(
        self,
        review_gate: Mapping[str, Any] | None,
        availability: Mapping[str, Any],
    ) -> dict[str, Any]:
        gate = dict(review_gate or {})
        reasons = list(gate.get("reasons") or [])
        reasons.extend(availability.get("reasons") or [])
        merged_reasons = sorted(set(reasons))
        gate["reasons"] = merged_reasons
        gate["enabled"] = bool(gate.get("enabled")) and not (availability.get("reasons") or [])
        return gate

    def _observation_review_gate(
        self,
        item: Mapping[str, Any],
        now_ms: int,
        *,
        current_gate: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        reasons: list[str] = []
        if not self.config.server_token or not self.config.api_key or not self._shared_budget_ready():
            reasons.append("manual_review_service_not_configured")
        snapshot = item.get("snapshot") if isinstance(item.get("snapshot"), Mapping) else None
        if not isinstance(snapshot, Mapping):
            reasons.append("snapshot_missing")
            return {"enabled": False, "reasons": sorted(set(reasons))}
        quoted_at = (snapshot.get("market") or {}).get("valuation_ts_ms")
        if not isinstance(quoted_at, int):
            reasons.append("snapshot_time_missing")
        if isinstance(current_gate, Mapping):
            current_hash = current_gate.get("fmz_source_hash")
            item_hash = (item.get("space_gate") or {}).get("fmz_source_hash")
            if current_hash and item_hash and current_hash != item_hash:
                reasons.append("candidate_gate_version_changed_during_refresh")
        if self.config.wpem_enabled and snapshot.get("snapshot_id") not in self._wpem_packages:
            reasons.append("wpem_evidence_unavailable")
        return {"enabled": not reasons, "reasons": sorted(set(reasons))}

    @staticmethod
    def _attach_decision_reduction(view: dict[str, Any]) -> dict[str, Any]:
        """Read-time only (after the review gate is current); never stored with a point."""
        for item in view.get("candidates") or []:
            try:
                item["decision_reduction"] = decision_reduction.reduce(item)
            except Exception as exc:
                item["decision_reduction"] = {"schema": decision_reduction.SCHEMA, "error": type(exc).__name__}
            if isinstance(item.get("snapshot"), Mapping):
                # a pure function of the frozen snapshot, so any recorded snapshot can be re-read later
                try:
                    item["spatial_position"] = spatial_position.assess(item["snapshot"])
                except Exception as exc:
                    item["spatial_position"] = {"schema": spatial_position.SCHEMA, "error": type(exc).__name__}
        return view

    def _apply_current_review_availability(self, view: dict[str, Any], now_ms: int) -> dict[str, Any]:
        state = view.get("manual_review_state") if isinstance(view.get("manual_review_state"), Mapping) else None
        if state is None:
            state = self.manual_review_state(now_ms)
            view["manual_review_state"] = state
        availability = self.review_availability(now_ms, manual_review_state=state)
        view["manual_review_availability"] = availability
        current_gate = view.get("space_gate") if isinstance(view.get("space_gate"), Mapping) else None
        accepted_history = self._manual_review_receipt_history()
        for item in view.get("candidates") or []:
            snapshot = item.get("snapshot") if isinstance(item.get("snapshot"), Mapping) else None
            package = (self._display_wpem_package(snapshot.get("snapshot_id"), state, accepted_history, item.get("review"))
                       if self.config.wpem_enabled and isinstance(snapshot, Mapping) else None)
            item["review_display"] = review_display_for_candidate(
                item,
                state,
                wpem_package=package,
                model=self.config.model,
                now_ms=now_ms,
                accepted_history=accepted_history,
            )
            item["observation_review"] = self._merge_review_availability(
                self._observation_review_gate(item, now_ms, current_gate=current_gate),
                availability,
            )
            item["review_gate"] = self._merge_review_availability(item.get("review_gate"), availability)
        return view

    def _shared_budget_ready(self) -> bool:
        """A configured path alone must never become a new, isolated quota."""
        if self.budget is None or self.config.shared_usage_ledger is None:
            return False
        path = self.config.shared_usage_ledger
        if not path.is_file():
            return False
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            return (isinstance(value, dict)
                    and isinstance(value.get("date_bjt"), str)
                    and isinstance(value.get("http_calls_used"), int)
                    and value["http_calls_used"] >= 0)
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False

    def _authorized(self, authorization: str | None) -> bool:
        if not self.config.server_token:
            return False
        prefix = "Bearer "
        return (isinstance(authorization, str) and authorization.startswith(prefix)
                and hmac.compare_digest(authorization[len(prefix):], self.config.server_token))

    def review_candidate(
        self,
        candidate_id: str,
        idempotency_key: str,
        *,
        authorization: str | None,
        purpose: str | None = REVIEW_PURPOSE,
        locked_episode_id: str | None = None,
        snapshot_id: str | None = None,
        _scheduled: bool = False,
    ) -> dict[str, Any]:
        # _scheduled is set only by this process's own scheduler, never from HTTP.
        if not _scheduled and not self._authorized(authorization):
            raise WorkbenchError(401, "unauthorized", "valid Authorization: Bearer token is required")
        if not candidate_id or not idempotency_key:
            raise WorkbenchError(400, "bad_request", "candidate_id and idempotency_key are required")
        purpose = _normalize_review_purpose(purpose)
        request_binding = ({"locked_episode_id": locked_episode_id, "requested_snapshot_id": snapshot_id}
                           if locked_episode_id else {})
        now = self.clock_ms()
        existing = self._idempotency_result(idempotency_key, candidate_id=candidate_id, purpose=purpose)
        if existing is not None:
            if any(existing.get(key) != value for key, value in request_binding.items()):
                raise WorkbenchError(409, "idempotency_key_conflict", "review request belongs to a different locked input")
            return existing
        # A manual lock refreshes only the chosen existing contract. Discovery
        # remains the collector's responsibility and cannot silently replace it.
        with self._refresh_lock:
            with self._view_lock:
                cached = deepcopy(self._current_view)
            previous = next((entry for entry in (cached or {}).get("candidates", [])
                             if (entry.get("snapshot") or {}).get("candidate_id") == candidate_id), None)
            discovered = (cached or {}).get("discovery") or {}
            btc_projection = (cached or {}).get("btc_map_projection") if isinstance(cached, Mapping) else None
            discovery_ts = discovered.get("captured_ts_ms")
            if locked_episode_id:
                try:
                    locked = self.locked_candidates.detail(locked_episode_id)
                except locked_store.LockedCandidateError as exc:
                    raise WorkbenchError(409, exc.code, exc.code) from exc
                episode = locked["episode"]
                if (episode["candidate_id"] != candidate_id or episode["latest_snapshot_id"] != snapshot_id
                        or episode["status"] != "active"):
                    raise WorkbenchError(409, "locked_review_target_changed", "refresh the selected locked observation")
                previous = locked["latest"]["candidate"]
                discovered = self._locked_binding(episode["contract"])
                discovery_ts = now
            item = None
            if previous and isinstance(discovery_ts, int) and 0 <= now - discovery_ts <= discovery.DEFAULT_DELTA_MAX_AGE_MS:
                fact = self.latest_fact(now)
                gate = self.space_gate_view(fact, now)
                if not isinstance(btc_projection, Mapping):
                    btc_projection = self._build_btc_map_projection(
                        now,
                        fact,
                        collect_slow_sources=False,
                    )
                item = self._capture_candidate(previous["side"], discovered, fact, gate, now,
                                               btc_map_projection=btc_projection, tracking=bool(locked_episode_id))
                if (item.get("snapshot") or {}).get("candidate_id") != candidate_id:
                    item = None
                if item:
                    cached = cached or {"candidates": []}
                    if locked_episode_id:
                        local_view = self._wpem_view_for_candidate(cached, item)
                        local_view["space_gate"] = gate
                        self._attach_wpem(local_view, self.clock_ms(), prune=False)
                        self._remember_lockable(local_view, self.clock_ms())
                        item = self._candidate_from_view(local_view, item)
                        point = self._lockable_points.get(item["snapshot"]["snapshot_id"])
                        if point is None:
                            raise WorkbenchError(409, "locked_packet_unavailable", "current locked input did not validate")
                        appended = self.locked_candidates.append(locked_episode_id, point, now_ms=self.clock_ms())
                        if appended["latest_snapshot_id"] != item["snapshot"]["snapshot_id"]:
                            raise WorkbenchError(409, "locked_sampling_paused", "current locked input was not retained")
                    else:
                        cached["candidates"] = [item if entry.get("side") == item["side"] else entry
                                                 for entry in cached["candidates"]]
                    cached.update({"updated_at_ms": self.clock_ms(), "market_context": fact,
                                   "space_gate": gate, "btc_map_projection": deepcopy(btc_projection),
                                   "btc_map": deepcopy((btc_projection or {}).get("btc_map")),
                                   "data_health": self._data_health(self.clock_ms(), btc_projection)})
                    if not locked_episode_id:
                        with self._view_lock:
                            self._current_view = deepcopy(cached)
                        self.invalidate_view_body()
                    self._store_btc_map_state((btc_projection or {}).get("btc_map"))
        if item is None:
            result = {"schema": SCHEMA, "status": "rejected", **request_binding,
                      "reason": "locked_quote_unavailable" if locked_episode_id else "candidate_not_current",
                      "candidate_id": candidate_id, "idempotency_key": idempotency_key,
                      "purpose": purpose,
                      "llm_call_count": 0}
            self._store_idempotency(idempotency_key, result, candidate_id=candidate_id, purpose=purpose)
            return result
        now = self.clock_ms()
        if purpose == OBSERVATION_PURPOSE and self.config.wpem_enabled and not locked_episode_id:
            local_view = self._wpem_view_for_candidate(cached, item)
            self._attach_wpem(local_view, now)
            item = self._candidate_from_view(local_view, item)
        if not locked_episode_id:
            self._remember_lockable({"candidates": [item], "space_gate": (cached or {}).get("space_gate")}, now)
        current_gate = (cached or {}).get("space_gate") if isinstance(cached, Mapping) else None
        base_gate = (self._observation_review_gate(item, now, current_gate=current_gate)
                     if purpose == OBSERVATION_PURPOSE else item.get("review_gate") or {})
        availability = self.review_availability(now)
        gate = self._merge_review_availability(base_gate, availability)
        availability_reasons = set(availability.get("reasons") or [])
        atomic_reason_active = bool(availability_reasons.intersection(_POST_ATOMIC_REASONS))
        budget_exhausted = MANUAL_REVIEW_BUDGET_EXHAUSTED_REASON in availability_reasons
        if (not base_gate.get("enabled")) or (budget_exhausted and not atomic_reason_active):
            result = {"schema": SCHEMA, "status": "rejected",
                      **request_binding,
                      "reason": "observation_review_unavailable" if purpose == OBSERVATION_PURPOSE else "review_gate_disabled",
                      "candidate_id": candidate_id, "snapshot_id": item["snapshot"]["snapshot_id"],
                      "side": item.get("side"),
                      "purpose": purpose,
                      ("observation_review" if purpose == OBSERVATION_PURPOSE else "review_gate"): deepcopy(gate),
                      "idempotency_key": idempotency_key,
                      "llm_call_count": 0}
            self._store_idempotency(idempotency_key, result, candidate_id=candidate_id, purpose=purpose)
            return result
        if not self.config.api_key or not self._shared_budget_ready():
            raise WorkbenchError(503, "llm_not_configured",
                                 "LLM key and existing shared usage ledger are required")
        snapshot = item["snapshot"]
        wpem_evidence = self._wpem_packages.get(snapshot["snapshot_id"])
        if self.config.wpem_enabled and wpem_evidence is None:
            local_view = self._wpem_view_for_candidate(cached, item)
            self._attach_wpem(local_view, now)
            item = self._candidate_from_view(local_view, item)
            snapshot = item["snapshot"]
            wpem_evidence = self._wpem_packages.get(snapshot["snapshot_id"])
            if wpem_evidence is None:
                raise WorkbenchError(409, "wpem_evidence_unavailable", "current evidence did not replay")
        self.ledger.record_snapshot(snapshot, now_ms=now)
        research_only = purpose == OBSERVATION_PURPOSE
        self._begin_review(candidate_id, snapshot["snapshot_id"], item.get("side"), idempotency_key, now, purpose=purpose,
                           ignore_cooldown=_scheduled)
        try:
            result = review_core.run_review(
                snapshot,
                self.ledger,
                self.config.review_state_dir,
                api_key=self.config.api_key,
                model=self.config.model,
                budget=self.budget,
                transport=self.llm_transport,
                timeout=self.config.review_timeout_s,
                endpoint=self.config.endpoint,
                wpem_evidence=wpem_evidence,
                research_only=research_only,
            )
            if research_only:
                result = deepcopy(dict(result))
                result["production_attach_allowed"] = False
                result["execution_allowed"] = False
            wrapped = {"schema": SCHEMA, "status": result.get("status") or "review_failed", "candidate_id": candidate_id,
                       **request_binding,
                       "side": item.get("side"),
                       "purpose": purpose,
                       "snapshot_id": snapshot["snapshot_id"], "idempotency_key": idempotency_key,
                       "review": result, "llm_call_count": result.get("http_calls", 0)}
            if research_only:
                wrapped["production_attach_allowed"] = False
                wrapped["execution_allowed"] = False
            self._finish_review(idempotency_key, wrapped, self.clock_ms())
            return _review_receipt_for_display(wrapped)
        except WorkbenchError as exc:
            wrapped = {"schema": SCHEMA, "status": "ERROR", "candidate_id": candidate_id,
                       **request_binding,
                       "side": item.get("side"),
                       "purpose": purpose,
                       "idempotency_key": idempotency_key, "error": exc.as_payload(),
                       "llm_call_count": 0}
            self._finish_review(idempotency_key, wrapped, self.clock_ms())
            raise
        except Exception as exc:
            wrapped = {"schema": SCHEMA, "status": "ERROR", "candidate_id": candidate_id,
                       **request_binding,
                       "side": item.get("side"),
                       "purpose": purpose,
                       "idempotency_key": idempotency_key,
                       "error": {"code": "review_failed", "message": str(exc)[:240],
                                 "error_type": type(exc).__name__},
                       "llm_call_count": 0}
            self._finish_review(idempotency_key, wrapped, self.clock_ms())
            return wrapped

    def _idempotency_result(
        self,
        key: str,
        *,
        candidate_id: str | None = None,
        purpose: str | None = None,
    ) -> dict[str, Any] | None:
        state = self._load_state()
        review = state.get("manual_review") if isinstance(state.get("manual_review"), Mapping) else {}
        entry = (review.get("idempotency") or {}).get(key)
        if not isinstance(entry, Mapping) or not isinstance(entry.get("result"), Mapping):
            return None
        result = _review_receipt_for_display(entry.get("result"))
        if candidate_id is not None or purpose is not None:
            stored_candidate = str(result.get("candidate_id") or entry.get("candidate_id") or "")
            stored_purpose = str(result.get("purpose") or entry.get("purpose") or REVIEW_PURPOSE)
            expected_purpose = _normalize_review_purpose(purpose)
            if ((candidate_id is not None and stored_candidate and stored_candidate != candidate_id)
                    or stored_purpose != expected_purpose):
                raise WorkbenchError(409, "idempotency_key_conflict",
                                     "idempotency key was already used for a different candidate or purpose",
                                     detail={"existing_candidate_id": stored_candidate or None,
                                             "existing_purpose": stored_purpose,
                                             "candidate_id": candidate_id,
                                             "purpose": expected_purpose})
        return result

    def _store_idempotency(
        self,
        key: str,
        result: Mapping[str, Any],
        *,
        candidate_id: str | None = None,
        purpose: str | None = None,
    ) -> None:
        def update(state: dict[str, Any]) -> None:
            review = state.setdefault("manual_review", {})
            idem = review.setdefault("idempotency", {})
            idem[key] = {"status": "settled", "result": _slim_receipt(result),
                         "candidate_id": candidate_id or result.get("candidate_id"),
                         "purpose": purpose or result.get("purpose") or REVIEW_PURPOSE,
                         "updated_at_ms": self.clock_ms()}
            _compact_review_history(review)
            if len(idem) > 200:
                for old_key in sorted(idem, key=lambda k: int((idem[k] or {}).get("updated_at_ms") or 0))[:-200]:
                    idem.pop(old_key, None)

        self._locked_state_update(update)

    def _begin_review(self, candidate_id: str, snapshot_id: str, side: str | None,
                      key: str, now_ms: int, *, purpose: str = REVIEW_PURPOSE, ignore_cooldown: bool = False) -> None:
        def update(state: dict[str, Any]) -> None:
            review = state.setdefault("manual_review", {})
            idem = review.setdefault("idempotency", {})
            if key in idem and isinstance(idem[key].get("result"), Mapping):
                raise WorkbenchError(409, "idempotency_race", "idempotency key already settled")
            inflight = review.get("inflight")
            if isinstance(inflight, Mapping):
                raise WorkbenchError(409, "review_inflight", "another manual review is already running")
            last_started = int(review.get("last_started_ts_ms") or 0)
            if not ignore_cooldown and last_started and now_ms - last_started < self.config.review_cooldown_ms:
                raise WorkbenchError(429, "review_cooldown", "manual review cooldown is still active",
                                     detail={"cooldown_until_ms": last_started + self.config.review_cooldown_ms})
            review["inflight"] = {"candidate_id": candidate_id, "snapshot_id": snapshot_id,
                                  "side": side, "idempotency_key": key,
                                  "purpose": purpose,
                                  "started_ts_ms": now_ms}
            review["last_started_ts_ms"] = now_ms
            idem[key] = {"status": "inflight", "candidate_id": candidate_id,
                         "purpose": purpose, "updated_at_ms": now_ms}

        self._locked_state_update(update)

    def _finish_review(self, key: str, result: Mapping[str, Any], now_ms: int) -> None:
        def update(state: dict[str, Any]) -> None:
            review = state.setdefault("manual_review", {})
            review["inflight"] = None
            review["last_completed_ts_ms"] = now_ms
            review["last_result"] = _slim_receipt(result)
            review.setdefault("idempotency", {})[key] = {
                "status": "settled",
                "result": _slim_receipt(result),
                "candidate_id": result.get("candidate_id"),
                "purpose": result.get("purpose") or REVIEW_PURPOSE,
                "updated_at_ms": now_ms,
            }
            _compact_review_history(review)

        self._locked_state_update(update)

    # ── scheduled reviews ─────────────────────────────
    _SCHEDULE_TRANSIENT = {"cached_quote_not_current", "candidate_gate_version_changed_during_refresh"}

    def _scheduled_slot(self, now_ms: int) -> str | None:
        """The UTC slot ('YYYY-MM-DDTHH') whose window contains now, if any."""
        from datetime import datetime, timezone
        moment = datetime.fromtimestamp(now_ms / 1000, timezone.utc)
        for hour in self.config.scheduled_review_hours_utc:
            start = moment.replace(hour=int(hour) % 24, minute=0, second=0, microsecond=0)
            if 0 <= now_ms - int(start.timestamp() * 1000) < self.config.scheduled_review_window_ms:
                return start.strftime("%Y-%m-%dT%H")
        return None

    def scheduled_review_status(self, now_ms: int | None = None) -> dict[str, Any]:
        from datetime import datetime, timedelta, timezone
        now = self.clock_ms() if now_ms is None else int(now_ms)
        hours = sorted({int(h) % 24 for h in self.config.scheduled_review_hours_utc})
        moment = datetime.fromtimestamp(now / 1000, timezone.utc)
        upcoming = []
        for day in (0, 1):
            for hour in hours:
                start = (moment + timedelta(days=day)).replace(hour=hour, minute=0, second=0, microsecond=0)
                if int(start.timestamp() * 1000) > now:
                    upcoming.append(int(start.timestamp() * 1000))
        slots = (self._read_state_cached().get("scheduled_review") or {}).get("slots") or {}
        last_key = max(slots) if slots else None
        return {"hours_utc": hours, "window_ms": self.config.scheduled_review_window_ms,
                "next_due_ms": min(upcoming) if upcoming else None,
                "last_slot": last_key, "last": slots.get(last_key) if last_key else None}

    def _reconcile_scheduled_reviews(self) -> None:
        """On process start a slot still 'running' was cut off by the restart: say so (the
        claim retries it once if its window is still open) instead of showing it forever."""
        slots = (self._read_state_cached().get("scheduled_review") or {}).get("slots") or {}
        if not any(isinstance(v, Mapping) and v.get("status") == "running" for v in slots.values()):
            return

        def update(state: dict[str, Any]) -> None:
            for entry in ((state.get("scheduled_review") or {}).get("slots") or {}).values():
                if isinstance(entry, dict) and entry.get("status") == "running":
                    entry.update(status="interrupted", interrupted_at_ms=self.clock_ms())

        self._locked_state_update(update)

    def _maybe_scheduled_review(self, now_ms: int, *, wait: bool = False) -> str | None:
        """Claim the current slot once (persisted, so restarts do not repeat it) and review both sides."""
        if not self.config.scheduled_review_hours_utc:
            return None
        slot = self._scheduled_slot(now_ms)
        if slot is None:
            return None

        def claim(state: dict[str, Any]) -> bool:
            sched = state.setdefault("scheduled_review", {})
            slots = sched.setdefault("slots", {})
            prior = slots.get(slot)
            if prior is not None and not (isinstance(prior, Mapping) and prior.get("status") in {"interrupted", "incomplete"}
                                          and int(prior.get("retries") or 0) < 1):
                return False
            retries = int(prior.get("retries") or 0) + 1 if isinstance(prior, Mapping) else 0
            slots[slot] = {"started_at_ms": now_ms, "status": "running", "sides": {},
                           **({"retries": retries, "interrupted_at_ms": prior.get("interrupted_at_ms")} if retries else {})}
            for old in sorted(slots)[:-28]:
                slots.pop(old, None)
            return True

        if not self._locked_state_update(claim):
            return None
        worker = threading.Thread(target=self._run_scheduled_review, args=(slot,), name="astra-scheduled-review", daemon=True)
        worker.start()
        if wait:
            worker.join()
        return slot

    _SCHEDULE_RETRYABLE = re.compile(r"snapshot_missing|quote|book|skew|depth|stale|unavailable|busy|timeout|discovery")

    def _run_scheduled_review(self, slot: str) -> None:
        with self._view_lock:
            items = [dict(item) for item in (self._current_view or {}).get("candidates") or []]
        entry_now = ((self._read_state_cached().get("scheduled_review") or {}).get("slots") or {}).get(slot) or {}
        attempt = int(entry_now.get("retries") or 0) if isinstance(entry_now, Mapping) else 0
        outcomes: dict[str, Any] = {}
        for item in items:
            snapshot = item.get("snapshot") if isinstance(item.get("snapshot"), Mapping) else None
            side = item.get("side")
            if snapshot is None:
                outcomes[side] = {"status": "skipped", "reason": "snapshot_missing"}
                continue
            reasons = set((item.get("review_gate") or {}).get("reasons") or [])
            purpose = REVIEW_PURPOSE if reasons <= self._SCHEDULE_TRANSIENT else OBSERVATION_PURPOSE
            # a retry must not replay the first attempt's stored (interrupted) result
            key = f"sched-{slot.replace(':', '')}-{side}" + (f"-r{attempt}" if attempt else "")
            try:
                result = self.review_candidate(snapshot["candidate_id"], key, authorization=None,
                                               purpose=purpose, _scheduled=True)
                outcomes[side] = {"status": result.get("status"), "purpose": purpose, "reason": result.get("reason"),
                                  "snapshot_id": result.get("snapshot_id"), "idempotency_key": key,
                                  "llm_call_count": result.get("llm_call_count")}
                self.record_action("scheduled_review", result, request={"candidate_id": snapshot["candidate_id"],
                                   "purpose": purpose, "idempotency_key": key})
            except WorkbenchError as exc:
                outcomes[side] = {"status": "error", "purpose": purpose, "reason": exc.code, "idempotency_key": key}
                self.record_action("scheduled_review_rejected", None, request={"candidate_id": snapshot["candidate_id"],
                                   "purpose": purpose, "idempotency_key": key}, error_code=exc.code)
            except Exception as exc:  # the scheduler must never take the workbench down
                outcomes[side] = {"status": "error", "purpose": purpose, "reason": type(exc).__name__, "idempotency_key": key}

        transient = bool(outcomes) and all(
            o.get("status") in {"skipped", "error", "rejected"}
            and self._SCHEDULE_RETRYABLE.search(str(o.get("reason") or "")) for o in outcomes.values())

        def finish(state: dict[str, Any]) -> None:
            entry = state.setdefault("scheduled_review", {}).setdefault("slots", {}).setdefault(slot, {})
            entry.update(status="incomplete" if transient else "done", finished_at_ms=self.clock_ms(), sides=outcomes)

        self._locked_state_update(finish)

    def start_background(self) -> None:
        if self._background_thread and self._background_thread.is_alive():
            return
        self._background_stop.clear()

        def run() -> None:
            while not self._background_stop.is_set():
                try:
                    self.refresh()
                    self._maybe_scheduled_review(self.clock_ms())
                except Exception as exc:
                    with self._view_lock:
                        self._last_refresh_error = {"failed_at_ms": self.clock_ms(),
                                                    "error_type": type(exc).__name__}
                try:
                    level = self.memory_guard().get("level")
                except Exception:
                    level = None
                self._hard_passes = self._hard_passes + 1 if level == "hard" else 0
                # liveness, not success: a failing refresh still loops; a hung one stops pinging.
                # Memory that stays hard for HARD_PASSES_BEFORE_RESTART passes is treated as hung
                # too: the page would otherwise keep serving old bodies or 503 indefinitely.
                if self._hard_passes < HARD_PASSES_BEFORE_RESTART:
                    _sd_notify("WATCHDOG=1")
                if self._background_stop.wait(max(1.0, float(self.config.refresh_interval_s))):
                    break

        self._background_thread = threading.Thread(target=run, name="astra-workbench-v20", daemon=True)
        self._background_thread.start()

        def sample() -> None:
            try:
                self.normalize_locked_series_once()
            except Exception:
                pass
            interval_s = max(5.0, self.config.locked_sample_interval_ms / 1000)
            while not self._background_stop.is_set():
                started = time.monotonic()
                try:
                    self.sample_locked_once()
                except Exception as exc:
                    self._locked_refresh_error = "sampler_failed:" + type(exc).__name__
                # wake once per interval, measured from the start of the previous pass
                if self._background_stop.wait(max(1.0, interval_s - (time.monotonic() - started))):
                    break

        def cross() -> None:
            while not self._background_stop.is_set():
                try:
                    self.refresh_cross_asset()
                except Exception:  # a background card must never take the workbench down
                    pass
                if self._background_stop.wait(300):
                    break

        if self.config.cross_asset_enabled and not (self._cross_asset_thread and self._cross_asset_thread.is_alive()):
            self._cross_asset_thread = threading.Thread(target=cross, name="astra-cross-asset", daemon=True)
            self._cross_asset_thread.start()

        self._sampler_running = True
        if not (self._sampler_thread and self._sampler_thread.is_alive()):
            self._sampler_thread = threading.Thread(target=sample, name="astra-locked-sampler", daemon=True)
            self._sampler_thread.start()

    def stop_background(self) -> None:
        self._background_stop.set()
        for thread in (self._background_thread, self._sampler_thread, self._cross_asset_thread):
            if thread:
                thread.join(timeout=5)
        self._sampler_running = bool(self._sampler_thread and self._sampler_thread.is_alive())


def _process_rss_bytes() -> int | None:
    """Resident set size of this process (Linux /proc); None elsewhere."""
    try:
        with open("/proc/self/statm", "rb") as handle:
            pages = int(handle.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError, AttributeError):
        return None


class WorkbenchHTTPServer(ThreadingHTTPServer):
    """At most MAX_ACTIVE_REQUESTS handlers at once; the rest get an immediate 503 instead of
    queueing threads behind a stuck one. Clients that hang up are not logged as errors."""

    daemon_threads = True
    request_queue_size = 64

    def __init__(self, *args: Any, max_active: int = MAX_ACTIVE_REQUESTS, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._active = threading.BoundedSemaphore(max_active)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._active.acquire(blocking=False):
            body = json.dumps({"code": "server_busy", "message": "工作台繁忙，稍后自动重试"}).encode("ascii") + b"\n"
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json; charset=utf-8\r\n"
                                b"Retry-After: 5\r\nCache-Control: no-store\r\nConnection: close\r\n"
                                b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._active.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._active.release()

    def handle_error(self, request: Any, client_address: Any) -> None:
        import sys  # noqa: PLC0415
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def _sd_notify(message: str) -> bool:
    """systemd notify protocol without the systemd package; a no-op outside systemd."""
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return False
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(message.encode("ascii"))
        return True
    except OSError:
        return False


def _market_marks(payload: Mapping[str, Any]) -> dict[str, Any]:
    market = payload.get("market") if isinstance(payload.get("market"), Mapping) else {}
    out: dict[str, Any] = {"source": "deribit_public_order_book.mark_price"}
    times = []
    for leg in ("short", "long"):
        book = market.get(leg + "_book") if isinstance(market.get(leg + "_book"), Mapping) else {}
        mark = book.get("mark_price")
        out[leg + "_mark_btc"] = float(mark) if isinstance(mark, (int, float)) and not isinstance(mark, bool) else None
        if isinstance(book.get("timestamp"), int):
            times.append(book["timestamp"])
    out["observed_ms"] = max(times) if times else None
    return out


def _current_metrics(metrics: Any) -> bool:
    """A metrics row of the current series version (older rows lack later readings)."""
    return isinstance(metrics, Mapping) and (metrics.get("metrics_version") or 1) >= locked_compare.SERIES_METRICS_VERSION


def make_handler(workbench: AstraWorkbench):
    sessions = WorkbenchSessions(workbench.config.server_token, workbench.config.public_origin,
                                 now_ms=workbench.clock_ms,
                                 ttl_seconds=workbench.config.session_ttl_seconds,
                                 max_sessions=workbench.config.max_sessions,
                                 store_path=workbench.config.state_dir / "sessions.json")
    proof_slots = threading.BoundedSemaphore(1)

    class Handler(BaseHTTPRequestHandler):
        server_version = "AstraWorkbenchV20/1.0"
        timeout = REQUEST_SOCKET_TIMEOUT_S

        def _json(self, status: int, payload: Mapping[str, Any], *, headers: Mapping[str, str] | None = None) -> None:
            body = (_canonical(dict(payload)) + "\n").encode("utf-8")
            if len(body) > 65536 and self._accepts_gzip():
                self._send_json_body(status, gzip.compress(body, compresslevel=1, mtime=0), content_encoding="gzip", headers=headers)
            else:
                self._send_json_body(status, body, headers=headers)

        def _send_json_body(self, status: int, body: bytes, *, content_encoding: str | None = None,
                            headers: Mapping[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            if content_encoding:
                self.send_header("Content-Encoding", content_encoding)
            self.send_header("Vary", "Accept-Encoding")
            self.send_header("Content-Length", str(len(body)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _accepts_gzip(self) -> bool:
            for token in (self.headers.get("Accept-Encoding") or "").split(","):
                name, *parameters = token.lower().split(";")
                if name.strip() != "gzip":
                    continue
                quality = next((part.strip()[2:] for part in parameters if part.strip().startswith("q=")), "1")
                try:
                    if 0 < float(quality) <= 1:
                        return True
                except ValueError:
                    pass
            return False

        def _wpem_proof_json(self, proof: Mapping[str, Any]) -> None:
            if self._accepts_gzip():
                compressed = reading.gzip_json(proof, compresslevel=6)
                if len(compressed) <= 8_000_000:
                    self._send_json_body(200, compressed, content_encoding="gzip")
                    return
            else:
                parts, size = [], 0
                for part in json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":")).iterencode(dict(proof)):
                    encoded = part.encode("utf-8")
                    size += len(encoded)
                    if size > 8_000_000:
                        break
                    parts.append(encoded)
                else:
                    self._send_json_body(200, b"".join(parts) + b"\n")
                    return
            self._json(413, {
                "schema": SCHEMA,
                "code": "proof_size_budget_exceeded",
                "evidence_hash": proof["evidence_hash"],
            })

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            heavy = (parsed.path.endswith("/evidence")
                     or parsed.path.startswith(("/api/workbench/candidate-evidence/", "/api/workbench/wpem-proof/"))
                     or parse_qs(parsed.query).get("view") == ["full"])
            if heavy and workbench._memory.get("level") == "hard":
                self._json(503, {"schema": SCHEMA, "code": "memory_pressure", "message": "工作台内存紧张，稍后自动重试"},
                           headers={"Retry-After": "30"})
                return
            if heavy and not proof_slots.acquire(blocking=False):
                self._json(429, {"schema": SCHEMA, "code": "evidence_export_busy",
                                 "message": "An evidence read is in progress; retry explicitly."})
                return
            try:
                self._get()
            finally:
                if heavy:
                    proof_slots.release()

        def _get(self) -> None:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if parsed.path == "/api/workbench/session/check":
                # For the public proxy's auth_request: 204 with a live session, 401 otherwise; never a body.
                access = sessions.authorize("GET", self.headers, self.client_address[0])
                self.send_response(204 if access.ok else 401)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            if parsed.path == "/api/workbench/session":
                self._json(200, sessions.status(self.headers, self.client_address[0]))
                return
            if parsed.path.startswith("/api/workbench/candidate-evidence/"):
                try:
                    self._json(200, workbench.candidate_evidence(unquote(parsed.path.rsplit("/", 1)[-1])))
                except WorkbenchError as exc:
                    self._json(exc.status_code, exc.as_payload())
                return
            if parsed.path == "/api/workbench/locked-candidates":
                self._json(200, workbench.locked_candidate_list())
                return
            if parsed.path == "/api/workbench/chain-audit":
                if workbench.chain_audit is None:
                    self._json(404, {"schema": SCHEMA, "code": "chain_audit_disabled"})
                else:
                    self._json(200, workbench.chain_audit.index())
                return
            if parsed.path.startswith("/api/workbench/chain-audit/"):
                name = unquote(parsed.path.rsplit("/", 1)[-1])
                try:
                    if workbench.chain_audit is None:
                        raise FileNotFoundError(name)
                    data = workbench.chain_audit.read_file(name)
                except ValueError:
                    self._json(400, {"schema": SCHEMA, "code": "invalid_audit_file"})
                    return
                except FileNotFoundError:
                    self._json(404, {"schema": SCHEMA, "code": "audit_file_not_found"})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(data)
                return
            if parsed.path.startswith("/api/workbench/locked-candidates/") and parsed.path.endswith("/series"):
                try:
                    self._json(200, workbench.locked_candidate_series(unquote(parsed.path.split("/")[-2])))
                except WorkbenchError as exc:
                    self._json(exc.status_code, exc.as_payload())
                except Exception as exc:
                    self._json(500, {"schema": SCHEMA, "status": "ERROR", "code": "locked_series_unavailable",
                                     "error_type": type(exc).__name__})
                return
            if parsed.path.startswith("/api/workbench/locked-candidates/"):
                try:
                    evidence = parsed.path.endswith("/evidence")
                    episode_id = unquote(parsed.path.split("/")[-2 if evidence else -1])
                    if evidence:
                        baseline_id = query.get("baseline_point_id", [None])[0]
                        latest_id = query.get("latest_point_id", [None])[0]
                        if not baseline_id or not latest_id:
                            raise WorkbenchError(400, "exact_pair_required", "both displayed point identities are required")
                        result = workbench.locked_candidate_detail(episode_id, include_raw=True,
                            baseline_point_id=baseline_id, latest_point_id=latest_id)
                    else:
                        result = workbench.locked_candidate_detail(episode_id, include_raw=False)
                    self._json(200, result)
                except WorkbenchError as exc:
                    self._json(exc.status_code, exc.as_payload())
                except Exception as exc:
                    self._json(500, {"schema": SCHEMA, "status": "ERROR",
                                     "code": "locked_projection_unavailable", "error_type": type(exc).__name__})
                return
            if parsed.path == "/api/workbench":
                try:
                    view_kind = (query.get("view") or ["reading"])[0]
                    if view_kind == "full":
                        self._json(200, workbench.cached_view(reading_only=False))
                    else:
                        body, packed = workbench.view_body("page" if view_kind == "page" else "reading")
                        if packed is not None and self._accepts_gzip():
                            self._send_json_body(200, packed, content_encoding="gzip")
                        else:
                            self._send_json_body(200, body)
                except WorkbenchError as exc:
                    self._json(exc.status_code, exc.as_payload(), headers={"Retry-After": "5"})
                except Exception as exc:
                    self._json(500, {"schema": SCHEMA, "status": "ERROR",
                                     "code": "refresh_failed", "message": str(exc)[:240]})
                return
            if parsed.path == "/api/workbench/review-state":
                state = workbench.manual_review_state()
                self._json(200, {"schema": SCHEMA,
                                 "manual_review_state": state,
                                 "manual_review_availability": workbench.review_availability(
                                     manual_review_state=state)})
                return
            if parsed.path.startswith("/api/workbench/wpem-evidence/"):
                try:
                    proof = workbench.wpem_evidence(unquote(parsed.path.rsplit("/", 1)[-1]), full=False)
                    self._json(200, wpem_workbench.public_evidence(proof))
                except WorkbenchError as exc:
                    self._json(exc.status_code, exc.as_payload())
                return
            if parsed.path.startswith("/api/workbench/wpem-proof/"):
                if not sessions.authorize("GET", self.headers, self.client_address[0]).ok:
                    self._json(401, {"schema":SCHEMA,"code":"unauthorized"})
                    return
                try:
                    proof = workbench.wpem_evidence(unquote(parsed.path.rsplit("/", 1)[-1]))
                    self._wpem_proof_json(proof)
                except WorkbenchError as exc:
                    self._json(exc.status_code,exc.as_payload())
                return
            self._serve_static(parsed.path)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/workbench/session":
                result = sessions.login("POST", self.headers, self.client_address[0])
                self._json(result.status_code, result.body, headers=result.headers)
                return
            lock_path = "/api/workbench/locked-candidates"
            stop_match = re.fullmatch(re.escape(lock_path) + r"/(lce_[0-9a-f]{24})/stop", parsed.path)
            hold_match = re.fullmatch(re.escape(lock_path) + r"/(lce_[0-9a-f]{24})/hold-review", parsed.path)
            if parsed.path not in {"/api/workbench/review", lock_path} and not stop_match and not hold_match:
                self._json(404, {"schema": SCHEMA, "status": "ERROR", "code": "not_found"})
                return
            body: dict[str, Any] = {}
            try:
                access = sessions.authorize("POST", self.headers, self.client_address[0])
                if not access.ok:
                    raise WorkbenchError(access.status_code, access.reason, "private access verification required")
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 < length <= 8192:
                    self._json(413, {"schema": SCHEMA, "status": "ERROR", "code": "request_size_invalid"})
                    return
                body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                if not isinstance(body, dict):
                    raise WorkbenchError(400, "bad_json", "request body must be an object")
                if parsed.path == lock_path:
                    if "new_reference" in body and not isinstance(body["new_reference"], bool):
                        raise WorkbenchError(400, "bad_lock_request", "new_reference must be boolean")
                    result = workbench.lock_candidate(
                        str(body.get("candidate_id") or ""), str(body.get("snapshot_id") or ""),
                        str(body.get("idempotency_key") or ""),
                        authorization=access.authorization_for_workbench,
                        new_reference=body.get("new_reference", False),
                        source_episode_id=body.get("source_episode_id"))
                elif stop_match:
                    result = workbench.stop_locked_candidate(stop_match.group(1),
                        authorization=access.authorization_for_workbench)
                elif hold_match:
                    result = workbench.hold_review_locked(hold_match.group(1),
                        str(body.get("idempotency_key") or ""),
                        authorization=access.authorization_for_workbench)
                else:
                    result = workbench.review_candidate(
                        str(body.get("candidate_id") or ""),
                        str(body.get("idempotency_key") or ""),
                        authorization=access.authorization_for_workbench,
                        purpose=str(body.get("purpose") or REVIEW_PURPOSE),
                        locked_episode_id=body.get("locked_episode_id"),
                        snapshot_id=body.get("snapshot_id"),
                    )
                workbench.invalidate_view_body()
                self._json(200, result)
                audit_kind = ("lock" if parsed.path == lock_path else "lock_stop" if stop_match
                              else "hold_review" if hold_match else "review")
                episode_ref = (stop_match or hold_match).group(1) if (stop_match or hold_match) else None
                workbench.record_action(audit_kind, result, request={**body, "episode_id": episode_ref})
            except WorkbenchError as exc:
                self._json(exc.status_code, exc.as_payload())
                if exc.status_code not in (401, 403):
                    audit_kind = ("lock" if parsed.path == lock_path else "lock_stop" if stop_match
                                  else "hold_review" if hold_match else "review")
                    episode_ref = (stop_match or hold_match).group(1) if (stop_match or hold_match) else None
                    workbench.record_action(audit_kind + "_rejected", None,
                                            request={**(body if isinstance(body, dict) else {}),
                                                     "episode_id": episode_ref},
                                            error_code=exc.code)
            except (ValueError, UnicodeError):
                self._json(400, {"schema": SCHEMA, "status": "ERROR", "code": "bad_json"})
            except locked_store.LockedCandidateError as exc:
                self._json(409, {"schema": SCHEMA, "status": "ERROR", "code": exc.code})
            except Exception as exc:  # answer with JSON instead of dropping the connection
                self._json(500, {"schema": SCHEMA, "status": "ERROR", "code": "internal_error",
                                 "error_type": type(exc).__name__})

        def do_DELETE(self) -> None:
            if urlparse(self.path).path != "/api/workbench/session":
                self._json(404, {"schema": SCHEMA, "code": "not_found"})
                return
            result = sessions.logout("DELETE", self.headers, self.client_address[0])
            self._json(result.status_code, result.body, headers=result.headers)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _serve_static(self, raw_path: str) -> None:
            root = workbench.config.frontend_dir
            if root is None:
                self._json(404, {"schema": SCHEMA, "status": "ERROR", "code": "static_not_configured"})
                return
            root = root.resolve()
            relative = unquote(raw_path.lstrip("/")) or "index.html"
            target = (root / relative).resolve()
            if target.is_dir():
                target = (target / "index.html").resolve()
            if root not in target.parents and target != root:
                self._json(403, {"schema": SCHEMA, "status": "ERROR", "code": "static_forbidden"})
                return
            if not target.is_file():
                fallback = root / "index.html"
                target = fallback if fallback.is_file() else target
            if not target.is_file():
                self._json(404, {"schema": SCHEMA, "status": "ERROR", "code": "static_not_found"})
                return
            data = target.read_bytes()
            content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            if content_type.startswith("text/") or target.suffix in {".js", ".json", ".css", ".html"}:
                content_type += "; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def _smtp_sender_from_env() -> SmtpEmailSender | None:
    host = os.environ.get("ASTRA_WORKBENCH_SMTP_HOST")
    mail_to = os.environ.get("ASTRA_WORKBENCH_SMTP_TO")
    mail_from = os.environ.get("ASTRA_WORKBENCH_SMTP_FROM")
    if not host or not mail_to or not mail_from:
        return None
    return SmtpEmailSender(
        host=host,
        port=int(os.environ.get("ASTRA_WORKBENCH_SMTP_PORT") or "587"),
        username=os.environ.get("ASTRA_WORKBENCH_SMTP_USERNAME"),
        password=os.environ.get("ASTRA_WORKBENCH_SMTP_PASSWORD"),
        mail_from=mail_from,
        mail_to=mail_to,
        starttls=os.environ.get("ASTRA_WORKBENCH_SMTP_STARTTLS", "1") not in {"0", "false", "False"},
    )


def config_from_env() -> WorkbenchConfig:
    state_dir = Path(os.environ.get("ASTRA_WORKBENCH_STATE_DIR") or ".artifacts/astra-workbench-v20")
    frontend = os.environ.get("ASTRA_WORKBENCH_FRONTEND_DIR") or "deploy/signal_audit/frontend"
    producer_path = os.environ.get("ASTRA_MAP_PRODUCER_CURRENT_PATH") or os.environ.get("ASTRA_MAP_PRODUCER_PATH")
    return WorkbenchConfig(
        snapshots_jsonl=Path(os.environ.get("ASTRA_WORKBENCH_SNAPSHOTS_JSONL") or "demo/logs/snapshots.jsonl"),
        state_dir=state_dir,
        frontend_dir=Path(frontend) if frontend else None,
        server_token=os.environ.get("ASTRA_WORKBENCH_TOKEN"),
        public_origin=os.environ.get("ASTRA_WORKBENCH_PUBLIC_ORIGIN"),
        session_ttl_seconds=int(os.environ.get("ASTRA_WORKBENCH_SESSION_TTL_SECONDS") or "43200"),
        max_sessions=int(os.environ.get("ASTRA_WORKBENCH_MAX_SESSIONS") or "32"),
        api_key=os.environ.get("LLM_API_KEY") or os.environ.get("ASTRA_WORKBENCH_LLM_API_KEY"),
        shared_usage_ledger=(Path(os.environ["ASTRA_WORKBENCH_USAGE_LEDGER"])
                             if os.environ.get("ASTRA_WORKBENCH_USAGE_LEDGER") else None),
        model=os.environ.get("ASTRA_WORKBENCH_LLM_MODEL") or DEFAULT_MODEL,
        endpoint=os.environ.get("LLM_BASE_URL") or os.environ.get("ASTRA_WORKBENCH_LLM_ENDPOINT"),
        daily_http_limit=int(os.environ.get("ASTRA_WORKBENCH_DAILY_HTTP_LIMIT")
                             or os.environ.get("LLM_DAILY_HTTP_CAP")
                             or str(signal_llm_review.DEFAULT_DAILY_HTTP_LIMIT)),
        review_timeout_s=int(os.environ.get("ASTRA_WORKBENCH_REVIEW_TIMEOUT_S") or str(DEFAULT_HTTP_TIMEOUT)),
        refresh_interval_s=float(os.environ.get("ASTRA_WORKBENCH_REFRESH_SECONDS") or "30"),
        wpem_enabled=os.environ.get("ASTRA_WORKBENCH_WPEM_ENABLED", "0") == "1",
        wpem_history_replay=os.environ.get("ASTRA_WPEM_HISTORY_REPLAY", "0") == "1",
        locked_sample_interval_ms=max(1000, int(os.environ.get("ASTRA_LOCKED_SAMPLE_INTERVAL_MS") or "60000")),
        locked_max_active=int(os.environ.get("ASTRA_LOCKED_MAX_ACTIVE") or "4"),
        locked_max_episodes=int(os.environ.get("ASTRA_LOCKED_MAX_EPISODES") or "64"),
        locked_max_bytes=int(os.environ.get("ASTRA_LOCKED_MAX_BYTES") or "536870912"),
        locked_max_samples=int(os.environ.get("ASTRA_LOCKED_MAX_SAMPLES") or "3000"),
        locked_compact_interval_ms=int(os.environ.get("ASTRA_LOCKED_COMPACT_INTERVAL_MS") or "600000"),
        scheduled_review_hours_utc=tuple(int(h) for h in (os.environ.get("ASTRA_SCHEDULED_REVIEW_HOURS_UTC", "2,14") or "").split(",") if h.strip()),
        chain_audit_enabled=os.environ.get("ASTRA_CHAIN_AUDIT_ENABLED", "1") == "1",
        chain_audit_interval_ms=int(os.environ.get("ASTRA_CHAIN_AUDIT_INTERVAL_MS") or "900000"),
        chain_audit_retention_days=int(os.environ.get("ASTRA_CHAIN_AUDIT_RETENTION_DAYS") or "30"),
        cross_asset_enabled=os.environ.get("ASTRA_CROSS_ASSET_ENABLED", "1") == "1",
        memory_soft_mb=int(os.environ.get("ASTRA_WORKBENCH_MEMORY_SOFT_MB") or "1536"),
        memory_hard_mb=int(os.environ.get("ASTRA_WORKBENCH_MEMORY_HARD_MB") or "2560"),
        map_sources_enabled=os.environ.get("ASTRA_MAP_SOURCES_ENABLED") == "1",
        map_source_mode=os.environ.get("ASTRA_MAP_SOURCE_MODE") or map_projection.SOURCE_MODE_COLLECTOR,
        map_producer_current_path=Path(producer_path) if producer_path else None,
        map_producer_max_age_ms=int(os.environ.get("ASTRA_MAP_PRODUCER_MAX_AGE_MS") or str(map_projection.DEFAULT_PRODUCER_MAX_AGE_MS)),
        risk_model_path=Path(os.environ["ASTRA_RISK_MODEL_PATH"]) if os.environ.get("ASTRA_RISK_MODEL_PATH") else None,
        risk_protocol_path=Path(os.environ["ASTRA_RISK_PROTOCOL_PATH"]) if os.environ.get("ASTRA_RISK_PROTOCOL_PATH") else None,
        research_archive_enabled=os.environ.get("ASTRA_RESEARCH_ARCHIVE_ENABLED") == "1",
        research_sample_interval_ms=int(os.environ.get("ASTRA_RESEARCH_SAMPLE_INTERVAL_MS") or str(candidate_asof.DEFAULT_INTERVAL_MS)),
        research_max_bytes=int(os.environ.get("ASTRA_RESEARCH_MAX_BYTES") or str(candidate_asof.DEFAULT_MAX_BYTES)),
        research_max_samples=int(os.environ.get("ASTRA_RESEARCH_MAX_SAMPLES") or str(candidate_asof.DEFAULT_MAX_SAMPLES)),
        map_source_config={
            name: os.environ[env_name]
            for name, env_name in (
                ("kpf_manifest_path", "ASTRA_MAP_KPF_MANIFEST_PATH"),
                ("kpf_snapshot_path", "ASTRA_MAP_KPF_SNAPSHOT_PATH"),
                ("kpf_audit_path", "ASTRA_MAP_KPF_AUDIT_PATH"),
                ("kpf_debug_path", "ASTRA_MAP_KPF_DEBUG_PATH"),
            ) if os.environ.get(env_name)
        },
    )


def serve(config: WorkbenchConfig, *, host: str = "127.0.0.1", port: int = 8890) -> None:
    workbench = AstraWorkbench(config, email_sender=_smtp_sender_from_env())
    workbench.start_background()
    server = WorkbenchHTTPServer((host, int(port)), make_handler(workbench))
    _sd_notify("READY=1")
    try:
        server.serve_forever()
    finally:
        workbench.stop_background()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Astra v2.0 local underwriting workbench API.")
    parser.add_argument("--host", default=os.environ.get("ASTRA_WORKBENCH_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ASTRA_WORKBENCH_PORT", "8895")))
    args = parser.parse_args(argv)
    serve(config_from_env(), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
