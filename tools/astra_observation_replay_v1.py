"""Offline replay for the Astra observation lineage closure.

This is a thin replay harness over the existing frozen artifacts, WPEM
validators and review_display selector. It does not call providers, market APIs
or a second judgment path.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import time
from typing import Any, Callable, Mapping, Sequence
import urllib.request

if __package__:
    from . import astra_underwriting_review_v20 as review_core
    from . import astra_workbench_v20 as workbench
    from . import astra_wpem_product_review_v1 as wpem_review
    from . import astra_wpem_review_evidence_v1 as review_evidence
    from . import astra_wpem_workbench_v1 as wpem_workbench
    from . import signal_llm_review as signal_llm
else:
    import astra_underwriting_review_v20 as review_core
    import astra_workbench_v20 as workbench
    import astra_wpem_product_review_v1 as wpem_review
    import astra_wpem_review_evidence_v1 as review_evidence
    import astra_wpem_workbench_v1 as wpem_workbench
    import signal_llm_review as signal_llm

SCHEMA = "astra_observation_lineage_replay@1.0.0"
PUBLIC_SCHEMA = "astra_observation_replay_public@1.0.0"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_OUT = Path(".artifacts/observation_lineage_20261007_01/replay")

CASE_ORDER = ("fresh_3", "current_rejected", "historical_accepted")
ACCEPTED_SOURCE_SAMPLES = ("fresh_3", "fresh_1", "call_early_v105")
DEFAULT_CASE_SPECS = (
    {"name": "fresh_3", "kind": "accepted_current", "sample": "fresh_3"},
    {
        "name": "current_rejected",
        "kind": "current_rejected",
        "current_sample": "call_early_v105",
        "rejected_sample": "call_early_v105",
    },
    {
        "name": "historical_accepted",
        "kind": "historical_accepted",
        "current_sample": "fresh_1",
        "historical_sample": "fresh_3",
    },
)

PRIVATE_DROP_KEYS = {
    "source_bindings", "request_http_body", "response_http_body",
    "provider_request", "request_body", "request_payload",
    "api_key",
    "authorization",
    "budget_before",
    "endpoint",
    "local_path",
    "provider_http_body",
    "provider_raw",
    "provider_raw_response",
    "provider_reasoning",
    "provider_reasoning_content",
    "provider_response",
    "provider_response_raw",
    "raw_provider_response",
    "raw_reasoning",
    "raw_reasoning_content",
    "raw_response",
    "reasoning_content",
    "reservation_id",
    "server_path",
    "shared_ledger",
    "token",
    "usage",
    "workspace_path",
}

PROOF_LIMITS = (
    "重放只证明已保留冻结字节、既有校验器和 selector 消费一致；不证明外部交易所或 vendor 数据真实正确。",
    "Flow 源钟在冻结证据中为 NOT_RETAINED 时保持该边界；重放不补造缺失观测时间。",
    "v2.5.9 没有记录用户实际 consumed_at；这里保持 null，不用 replay 时间补填。",
    "语义 rejected 的 final 只作为拒绝证据保留，不进入 Q1/Q2 主判断。",
)


class ReplayError(RuntimeError):
    """Raised when a replay artifact is missing or cannot be verified."""


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def hash_value(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ReplayError(f"missing_artifact:{path}")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ReplayError(f"artifact_not_object:{path}")
    return value


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


@contextmanager
def offline_guard():
    original_create_connection = socket.create_connection
    original_urlopen = urllib.request.urlopen
    original_socket = socket.socket

    def blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("offline_replay_network_disabled")

    socket.create_connection = blocked  # type: ignore[assignment]
    urllib.request.urlopen = blocked  # type: ignore[assignment]

    class OfflineSocket(original_socket):  # type: ignore[misc, valid-type]
        def connect(self, *_args: Any, **_kwargs: Any) -> Any:  # noqa: D401
            raise RuntimeError("offline_replay_network_disabled")

        def connect_ex(self, *_args: Any, **_kwargs: Any) -> int:
            raise RuntimeError("offline_replay_network_disabled")

    socket.socket = OfflineSocket  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.create_connection = original_create_connection  # type: ignore[assignment]
        urllib.request.urlopen = original_urlopen  # type: ignore[assignment]
        socket.socket = original_socket  # type: ignore[assignment]


def install_offline_guard():
    return offline_guard()


def scrub_public(value: Any) -> Any:
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text in PRIVATE_DROP_KEYS:
                continue
            if key_text.endswith("_raw_response") or key_text.endswith("_reasoning_content"):
                continue
            out[str(key)] = scrub_public(item)
        return out
    if isinstance(value, list):
        return [scrub_public(item) for item in value]
    if isinstance(value, str) and looks_like_private_path(value):
        return "[redacted_path]"
    return deepcopy(value)


def looks_like_private_path(value: str) -> bool:
    text = value.replace("/", "\\")
    if len(text) >= 3 and text[1:3] == ":\\" and text[0].isalpha():
        return True
    if text.startswith("\\\\"):
        return True
    for prefix in ("\\root\\", "\\home\\", "\\opt\\", "\\var\\", "\\tmp\\", "\\Users\\Xu\\"):
        if text.startswith(prefix):
            return True
    return False


def rel(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("/", "\\")
    except ValueError:
        return str(path)


def artifact_ref(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": rel(root, path),
        "sha256": file_sha256(path),
        "bytes": path.stat().st_size,
    }


def sample_source(root: Path, name: str) -> Path:
    sealed = root / ".artifacts/semantic_contract_closure_20261007_01"
    synthesis = root / ".artifacts/semantic_synthesis_20261007_01"
    return sealed / "fresh_samples" / name if name.startswith("fresh_") else synthesis / "samples" / name


def parse_utc_ms(value: str | None) -> int | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(text).timestamp() * 1000)
    except ValueError:
        return None


def review_points(review: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(review, Mapping):
        return {"q1": [], "q2": []}
    body = review.get("review") if isinstance(review.get("review"), Mapping) else review
    explanation = body.get("llm_explanation") if isinstance(body.get("llm_explanation"), Mapping) else {}
    out: dict[str, Any] = {}
    for question in ("q1", "q2"):
        item = explanation.get(question) if isinstance(explanation.get(question), Mapping) else {}
        points = item.get("points") if isinstance(item.get("points"), list) else None
        if points is None:
            q_body = body.get(question) if isinstance(body.get(question), Mapping) else {}
            q_exp = q_body.get("llm_explanation") if isinstance(q_body.get("llm_explanation"), Mapping) else {}
            points = q_exp.get("points") if isinstance(q_exp.get("points"), list) else []
        out[question] = [
            {
                "text_cn": point.get("text_cn"),
                "evidence_refs": list(point.get("evidence_refs") or []),
            }
            for point in points
            if isinstance(point, Mapping)
        ]
    return out


def view_semantic_digest(view: Mapping[str, Any]) -> dict[str, Any]:
    candidates = view.get("candidates") if isinstance(view.get("candidates"), list) else []
    candidate = candidates[0] if candidates and isinstance(candidates[0], Mapping) else {}
    display = candidate.get("review_display") if isinstance(candidate.get("review_display"), Mapping) else {}
    questions = candidate.get("current_questions") if isinstance(candidate.get("current_questions"), Mapping) else {}

    def binding(review: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(review, Mapping) or not isinstance(review.get("display_binding"), Mapping):
            return None
        src = review["display_binding"]
        return {
            key: src.get(key)
            for key in ("candidate_id", "snapshot_id", "evidence_input_hash", "same_input", "review_input_version")
        }

    current = display.get("current") if isinstance(display.get("current"), Mapping) else None
    historical = display.get("historical") if isinstance(display.get("historical"), Mapping) else None
    return {
        "candidate_id": candidate.get("candidate_id"),
        "snapshot_id": (candidate.get("snapshot") or {}).get("snapshot_id") if isinstance(candidate.get("snapshot"), Mapping) else None,
        "display": {
            "has_current": current is not None,
            "has_historical": historical is not None,
            "reason_cn": display.get("reason_cn"),
            "current_binding": binding(current),
            "historical_binding": binding(historical),
            "current_points": review_points(current),
            "historical_points": review_points(historical),
        },
        "program_questions": {
            "source_kind": questions.get("source_kind"),
            "q1": deepcopy(questions.get("q1")),
            "q2": deepcopy(questions.get("q2")),
            "audit": {
                "window_status": ((questions.get("audit") or {}) if isinstance(questions.get("audit"), Mapping) else {}).get("window_status"),
                "llm_calls": ((questions.get("audit") or {}) if isinstance(questions.get("audit"), Mapping) else {}).get("llm_calls"),
            },
        },
        "wpem_summary": deepcopy(candidate.get("wpem_summary")),
        "fee_remainder": deepcopy((candidate.get("wpem_evidence") or {}).get("quote_fee_deterministic_responsibility")),
    }


def edge(name: str, status: str, reason_cn: str, **extra: Any) -> dict[str, Any]:
    row = {"edge": name, "status": status, "reason_cn": reason_cn}
    row.update({key: value for key, value in extra.items() if value is not None})
    return row


def first_divergence(checks: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    for item in checks:
        if item.get("status") in {"FAIL", "ERROR", "MISSING"}:
            return {"edge": item.get("edge"), "reason_cn": item.get("reason_cn")}
    return None


def compact_request_identity(request: Mapping[str, Any], binding: Mapping[str, Any], review_input: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "model": request.get("model"),
        "prompt": binding.get("_local_prompt_version"),
        "provider_config": binding.get("_local_provider_config_version"),
        "request_schema": review_input.get("schema"),
        "response_protocol": review_input.get("response_protocol"),
        "response_format": deepcopy(request.get("response_format")),
        "thinking": deepcopy(request.get("thinking")),
        "reasoning_effort": request.get("reasoning_effort"),
    }


def evidence_input_from_request(request: Mapping[str, Any]) -> dict[str, Any]:
    messages = request.get("messages") if isinstance(request.get("messages"), list) else []
    for item in messages:
        if not isinstance(item, Mapping):
            continue
        content = item.get("content")
        if not isinstance(content, str) or "\n本次证据：" not in content:
            continue
        _prompt, evidence_text = content.split("\n本次证据：", 1)
        parsed = json.loads(evidence_text)
        if not isinstance(parsed, dict):
            raise ReplayError("request_evidence_input_not_object")
        return parsed
    raise ReplayError("request_evidence_input_missing")


def wire_request_matches(snapshot: Mapping[str, Any], original_request: Mapping[str, Any],
                         review_input: Mapping[str, Any]) -> bool:
    rebuilt = review_core.build_request_from_input(
        snapshot,
        str(original_request.get("model") or DEFAULT_MODEL),
        review_input,
        research_only=(review_input.get("research_scope") or {}).get("research_only") is True,
    )
    wire = signal_llm._strip_local_request_fields(rebuilt)
    actual_prompt, actual_evidence = wire["messages"][1]["content"].split("\n本次证据：", 1)
    old_prompt, old_evidence = original_request["messages"][1]["content"].split("\n本次证据：", 1)
    if actual_prompt != old_prompt:
        return False
    if json.loads(actual_evidence) != json.loads(old_evidence):
        return False
    wire["messages"][1]["content"] = original_request["messages"][1]["content"]
    return wire == original_request


def point_body_digest(normalized: Mapping[str, Any]) -> dict[str, Any]:
    return review_points(normalized)


def rejected_final_hashes_match(folder: Path, semantic: Mapping[str, Any]) -> bool:
    """Bind the old semantic verdict to its files without reading private reasoning."""
    files = {
        "raw_http_body_sha256": "provider_http_body.bin",
        "message_content_sha256": "message_content.txt",
        "original_receipt_sha256": "receipt.json",
    }
    return all(
        isinstance(semantic.get(key), str)
        and (folder / "actual_llm" / filename).is_file()
        and file_sha256(folder / "actual_llm" / filename) == semantic[key]
        for key, filename in files.items()
    )


class ReplayContext:
    def __init__(self, root: Path, *, replayed_at_ms: int | None = None,
                 paths: Mapping[str, Any] | None = None) -> None:
        self.root = root.resolve()
        path_overrides = paths or {}
        self.sealed = self.resolve_manifest_path(
            path_overrides.get("sealed") or path_overrides.get("semantic_contract_closure")
            or ".artifacts/semantic_contract_closure_20261007_01"
        )
        self.synthesis = self.resolve_manifest_path(
            path_overrides.get("synthesis") or path_overrides.get("semantic_synthesis")
            or ".artifacts/semantic_synthesis_20261007_01"
        )
        self.consumption = self.resolve_manifest_path(
            path_overrides.get("consumption") or path_overrides.get("consumption_ui_audit")
            or ".artifacts/consumption_ui_audit_20261007_01"
        )
        self.observation_lineage = self.resolve_manifest_path(
            path_overrides.get("observation_lineage") or ".artifacts/observation_lineage_20261007_01"
        )
        self.replayed_at_ms = replayed_at_ms if replayed_at_ms is not None else int(time.time() * 1000)
        self._base_cache: dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]] = {}

    def resolve_manifest_path(self, value: Any) -> Path:
        if isinstance(value, Mapping):
            value = value.get("path")
        path = Path(str(value))
        return path if path.is_absolute() else self.root / path

    def sample_source(self, name: str) -> Path:
        return self.sealed / "fresh_samples" / name if name.startswith("fresh_") else self.synthesis / "samples" / name

    def accepted_base(self, name: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        if name in self._base_cache:
            return deepcopy(self._base_cache[name])

        snapshot_path = self.sample_source(name) / "snapshot.json"
        snapshot = read_json(snapshot_path)
        runtime = read_json(self.sealed / "results" / name / "runtime_result.json")
        transport = read_json(self.sealed / "results" / name / "transport_receipt.json")
        final = read_json(self.sealed / "results" / name / "parsed_wire_response.json")
        evidence = runtime.get("wpem_evidence")
        if not isinstance(evidence, Mapping):
            raise ReplayError(f"wpem_evidence_missing:{name}")

        decision_ms = ((snapshot.get("market") or {}) if isinstance(snapshot.get("market"), Mapping) else {}).get("valuation_ts_ms")
        review_ms = parse_utc_ms(transport.get("completed_at_utc")) or runtime.get("reviewed_at_ms")
        safe_review = wpem_workbench.public_evidence(runtime)
        for question in ("q1", "q2"):
            points = (((safe_review.get("llm_explanation") or {}) if isinstance(safe_review.get("llm_explanation"), Mapping) else {})
                      .get(question) or {}).get("points", [])
            expected = [{"text_cn": p.get("text_cn"), "evidence_refs": p.get("evidence_refs")} for p in points]
            if expected != final.get(question):
                raise ReplayError(f"final_points_mismatch:{name}:{question}")
        safe_review["reviewed_at_ms"] = review_ms
        safe_review["decision_asof_ms"] = decision_ms

        candidate = {
            "candidate_id": snapshot.get("candidate_id"),
            "side": (snapshot.get("contract") or {}).get("side") if isinstance(snapshot.get("contract"), Mapping) else None,
            "status": "snapshot",
            "snapshot": wpem_workbench.public_evidence(snapshot),
            "review": {"status": "not_attached", "reviewed_snapshot_id": None},
            "review_gate": {"enabled": False, "reasons": ["readonly_frozen_replay"]},
            "observation_review": {"enabled": False, "reasons": ["readonly_frozen_replay"]},
            "wpem_evidence": wpem_workbench.public_evidence(evidence),
            "wpem_summary": wpem_review.window_interpretation(evidence),
            "current_questions": wpem_workbench.program_questions(snapshot, evidence),
            "btc_map": deepcopy(((snapshot.get("data_identity") or {}) if isinstance(snapshot.get("data_identity"), Mapping) else {}).get("btc_map") or {}),
            "remaining_to_expiry_ms": ((snapshot.get("contract") or {}).get("expiry_ms") if isinstance(snapshot.get("contract"), Mapping) else 0) - decision_ms,
        }
        view = {
            "schema": "astra_workbench@2.0.0",
            "updated_at_ms": decision_ms,
            "served_at_ms": decision_ms,
            "candidates": [candidate],
            "release_identity": deepcopy(evidence.get("release_identity") or {}),
            "preview": {
                "kind": "frozen_real_fixture",
                "mode": "FROZEN_REPLAY",
                "scenario_id": name,
                "label": "真实归档冻结回放 · 与所示输入同版 · 非当前行情",
            },
            "space_gate": deepcopy((((snapshot.get("fact_context") or {}) if isinstance(snapshot.get("fact_context"), Mapping) else {})
                                    .get("market_facts") or {}).get("space_gate") or {}),
            "market_context": deepcopy(snapshot.get("fact_context") or {}),
            "btc_map": deepcopy(((snapshot.get("data_identity") or {}) if isinstance(snapshot.get("data_identity"), Mapping) else {}).get("btc_map") or {}),
            "manual_review_state": {
                "last_result": safe_review,
                "last_completed_ts_ms": review_ms,
                "previous_valid_result": None,
                "cooldown_remaining_ms": 0,
                "inflight": None,
                "daily_http_budget": None,
            },
            "manual_review_availability": {"enabled": False, "reasons": ["readonly_frozen_replay"]},
            "data_health": {},
            "daily_snapshots": [],
            "transitions": [],
            "llm_call_count": 0,
            "gaps": [],
        }
        packed = (view, snapshot, dict(evidence), safe_review)
        self._base_cache[name] = deepcopy(packed)
        return deepcopy(packed)

    def bind(self, view: dict[str, Any], evidence: Mapping[str, Any]) -> dict[str, Any]:
        candidate = view["candidates"][0]
        candidate["review_display"] = workbench.review_display_for_candidate(
            candidate,
            view.get("manual_review_state"),
            wpem_package=evidence,
            model=DEFAULT_MODEL,
            now_ms=candidate["snapshot"]["market"]["valuation_ts_ms"],
        )
        return candidate["review_display"]

    def original_renderer_identity(self) -> dict[str, Any]:
        backup_version = self.observation_lineage / "backup/deploy/signal_audit/frontend/VERSION.json"
        baseline_path = self.observation_lineage / "BASELINE.json"
        version = read_json(backup_version) if backup_version.exists() else {}
        baseline = read_json(baseline_path) if baseline_path.exists() else {}
        files = baseline.get("files") if isinstance(baseline.get("files"), Mapping) else {}
        return {
            "source": "observation_lineage_20261007_01/baseline_backup" if version else "not_recorded",
            "head": baseline.get("head"),
            "branch": baseline.get("branch"),
            "frontend_cache_token": version.get("frontend_cache_token"),
            "local_review_revision": version.get("local_review_revision"),
            "app_js_sha256": files.get("deploy/signal_audit/frontend/app.js"),
            "fixture_renderer_only": True,
            "does_not_prove_user_read_or_trade": True,
        }

    def public_summary(
        self,
        *,
        analysis_identity: Mapping[str, Any],
        checks: Sequence[Mapping[str, Any]],
        extra_limits: Sequence[str] = (),
    ) -> dict[str, Any]:
        return {
            "schema": PUBLIC_SCHEMA,
            "replayed_at_ms": self.replayed_at_ms,
            "original_analysis_identity": scrub_public(dict(analysis_identity)),
            "original_renderer": scrub_public(self.original_renderer_identity()),
            "original_consumed_at_ms": None,
            "checks": [
                {key: item.get(key) for key in ("edge", "status", "reason_cn") if key in item}
                for item in checks
            ],
            "first_divergence": first_divergence(checks),
            "proof_limits": list(dict.fromkeys([*PROOF_LIMITS, *extra_limits])),
            "llm_http_calls": 0,
        }

    def request_artifacts(self, name: str, *, source: str = "sealed") -> dict[str, Path]:
        if source == "synthesis":
            base = self.synthesis / "samples" / name
            return {
                "request": base / "request.json",
                "request_http_body": base / "actual_llm/request_http_body.bin",
                "binding": base / "local_request_binding.json",
                "review_input": base / "review_input.json",
                "wpem_evidence": base / "evidence.json",
                "semantic_review": base / "SEMANTIC_REVIEW.json",
                "displayed_receipt": base / "DISPLAYED_RECEIPT.json",
                "transport": base / "actual_llm/transport_receipt.json",
            }
        base = self.sealed / "requests" / name
        return {
            "request": base / "request.json",
            "request_http_body": base / "request_http_body.bin",
            "binding": base / "local_request_binding.json",
            "review_input": base / "review_input.json",
            "actual_evidence": base / "actual_evidence.json",
            "transport": self.sealed / "results" / name / "transport_receipt.json",
            "result_request_http_body": self.sealed / "results" / name / "request_http_body.bin",
            "runtime": self.sealed / "results" / name / "runtime_result.json",
            "final": self.sealed / "results" / name / "parsed_wire_response.json",
        }

    def replay_accepted_current(self, sample: str = "fresh_3", *, case_name: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        name = sample
        output_name = case_name or sample
        view, snapshot, evidence, _review = self.accepted_base(name)
        checks: list[dict[str, Any]] = []
        artifacts = self.request_artifacts(name)
        original_request = read_json(artifacts["request_http_body"])
        original_binding = read_json(artifacts["binding"])
        original_input = read_json(artifacts["review_input"])
        original_evidence = read_json(artifacts["actual_evidence"])
        original_final = read_json(artifacts["final"])
        original_transport = read_json(artifacts["transport"])

        try:
            review_evidence.validate_review_evidence(evidence, snapshot)
            native_count = len(((evidence.get("composition_inputs") or {})
                                if isinstance(evidence.get("composition_inputs"), Mapping) else {}).get("native_sources_b64") or {})
            checks.append(edge(
                "source/canonical -> WPEM evidence",
                "LIMIT",
                f"packet/composition 可重建 review evidence，且保留 native source bytes {native_count} 份；这不是外部源真实性证明。",
            ))
        except Exception as exc:
            checks.append(edge("source/canonical -> WPEM evidence", "FAIL", f"review evidence 重建失败：{type(exc).__name__}:{exc}"))

        rebuilt_input = wpem_review.build_review_input(snapshot, evidence, research_only=True, model=DEFAULT_MODEL)
        checks.append(edge(
            "attribution -> clean evidence",
            "PASS" if rebuilt_input == original_input else "FAIL",
            "当前代码重建 review_input 与冻结 review_input 逐字一致。" if rebuilt_input == original_input
            else "当前代码重建 review_input 与冻结 review_input 不一致。",
            original_hash=hash_value(original_input),
            rebuilt_hash=hash_value(rebuilt_input),
        ))
        checks.append(edge(
            "clean evidence -> actual LLM input",
            "PASS" if rebuilt_input.get("evidence_input") == original_evidence else "FAIL",
            "实际发送 clean evidence 与冻结 actual_evidence 一致。" if rebuilt_input.get("evidence_input") == original_evidence
            else "实际发送 clean evidence 与冻结 actual_evidence 不一致。",
            original_evidence_input_hash=hash_value(original_evidence),
            rebuilt_evidence_input_hash=hash_value(rebuilt_input.get("evidence_input")),
        ))
        request_evidence = evidence_input_from_request(original_request)
        request_body_hash = file_sha256(artifacts["request_http_body"])
        wire_matches = request_evidence == original_evidence and wire_request_matches(snapshot, original_request, original_input)
        checks.append(edge(
            "actual request wire -> clean evidence",
            "PASS" if wire_matches else "FAIL",
            "冻结 request wire 中的“本次证据”与 actual_evidence/review_input.evidence_input 同类一致。"
            if wire_matches
            else "冻结 request wire 中的 clean evidence 与 actual_evidence 或 review_input 不一致。",
        ))
        checks.append(edge(
            "actual request body -> transport receipt",
            "PASS" if request_body_hash == original_transport.get("request_http_body_sha256") else "FAIL",
            "冻结 request_http_body.bin 与 transport receipt 记录的 request_http_body_sha256 一致。"
            if request_body_hash == original_transport.get("request_http_body_sha256")
            else "冻结 request_http_body.bin 与 transport receipt 记录的 request_http_body_sha256 不一致。",
            request_http_body_sha256=request_body_hash,
        ))
        try:
            normalized = wpem_review.validate_wire_response(snapshot, original_final, review_input=original_input, evidence=evidence)
            normalized_points = point_body_digest(normalized)
            expected_points = {"q1": original_final.get("q1"), "q2": original_final.get("q2")}
            checks.append(edge(
                "accepted final -> wire validator",
                "PASS" if normalized_points == expected_points else "FAIL",
                "既有 wire validator 接受 frozen final，归一化后的点正文/refs 与原 final 一致。"
                if normalized_points == expected_points else "wire validator 归一化点与原 final 不一致。",
            ))
        except Exception as exc:
            checks.append(edge(
                "accepted final -> wire validator",
                "FAIL",
                f"既有 wire validator 拒绝 frozen final：{type(exc).__name__}:{exc}",
            ))

        display = self.bind(view, evidence)
        checks.append(edge(
            "accepted final -> review_display",
            "PASS" if display.get("current") and not display.get("historical") else "FAIL",
            "selector 将同 candidate/snapshot/evidence_input 的 accepted 回执选为 current。",
        ))
        new_digest = view_semantic_digest(view)
        original_view_path = self.consumption / "samples" / output_name / "view.json"
        if original_view_path.exists():
            original_view = read_json(original_view_path)
            old_digest = view_semantic_digest(original_view)
            checks.append(edge(
                "review_display -> Q1/Q2 consumption",
                "PASS" if old_digest == new_digest else "FAIL",
                "重放 view 的 current Q1/Q2、refs、binding 与 v2.5.9 冻结公开 view 语义一致。" if old_digest == new_digest
                else "重放 view 与 v2.5.9 冻结公开 view 的消费语义不一致。",
                original_digest_hash=hash_value(old_digest),
                replay_digest_hash=hash_value(new_digest),
            ))
        else:
            checks.append(edge(
                "review_display -> Q1/Q2 consumption",
                "LIMIT",
                "未提供旧公开 view 作为同屏比对对象；本次只输出当前 selector 的公开 replay view。",
                replay_digest_hash=hash_value(new_digest),
            ))

        analysis_identity = compact_request_identity(original_request, original_binding, original_input)
        analysis_identity.update({
            "sample": output_name,
            "source_sample": name,
            "candidate_id": snapshot.get("candidate_id"),
            "snapshot_id": snapshot.get("snapshot_id"),
            "original_disposition": "accepted",
        })
        view["observation_replay"] = self.public_summary(analysis_identity=analysis_identity, checks=checks)
        return view, {
            "case": output_name,
            "scenario": "current accepted",
            "status": "PASS" if first_divergence(checks) is None else "FAIL",
            "llm_http_calls": 0,
            "checks": checks,
            "first_divergence": first_divergence(checks),
            "artifacts": {key: artifact_ref(self.root, path) for key, path in artifacts.items() if path.exists()},
            "semantic_digest_hash": hash_value(new_digest),
            "public_view_hash": hash_value(scrub_public(view)),
        }

    def replay_historical_accepted(
        self,
        current_sample: str = "fresh_1",
        historical_sample: str = "fresh_3",
        *,
        case_name: str = "historical_accepted",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        current_view, current_snapshot, current_evidence, _ = self.accepted_base(current_sample)
        prior_view, prior_snapshot, prior_evidence, _ = self.accepted_base(historical_sample)
        current_view["manual_review_state"] = deepcopy(prior_view["manual_review_state"])
        current_view["preview"]["scenario_id"] = case_name
        current_view["preview"]["label"] = "真实归档冻结回放 · 新快照与历史解释对照 · 非当前行情"
        display = self.bind(current_view, current_evidence)
        try:
            review_evidence.validate_review_evidence(current_evidence, current_snapshot)
            current_evidence_status = "LIMIT"
            current_evidence_reason = "当前事实 WPEM 包可重建；这不是外部源真实性证明。"
        except Exception as exc:
            current_evidence_status = "FAIL"
            current_evidence_reason = f"当前事实 WPEM 包重建失败：{type(exc).__name__}:{exc}"
        try:
            review_evidence.validate_review_evidence(prior_evidence, prior_snapshot)
            prior_evidence_status = "LIMIT"
            prior_evidence_reason = "历史 accepted WPEM 包可重建；这不是外部源真实性证明。"
        except Exception as exc:
            prior_evidence_status = "FAIL"
            prior_evidence_reason = f"历史 accepted WPEM 包重建失败：{type(exc).__name__}:{exc}"
        checks = [
            edge(
                "current source/canonical -> WPEM evidence",
                current_evidence_status,
                current_evidence_reason,
            ),
            edge(
                "historical source/canonical -> WPEM evidence",
                prior_evidence_status,
                prior_evidence_reason,
            ),
            edge(
                "current facts -> historical accepted selector",
                "PASS" if display.get("current") is None and display.get("historical") else "FAIL",
                "selector 未把旧 snapshot 的 accepted 回执回填为当前，只放入 historical。",
            )
        ]
        new_digest = view_semantic_digest(current_view)
        original_view_path = self.consumption / "samples" / case_name / "view.json"
        if original_view_path.exists():
            original_view = read_json(original_view_path)
            old_digest = view_semantic_digest(original_view)
            checks.append(edge(
                "historical review_display -> folded Q1/Q2",
                "PASS" if old_digest == new_digest else "FAIL",
                "历史 accepted 的折叠消费语义与 v2.5.9 冻结公开 view 一致。" if old_digest == new_digest
                else "历史 accepted 的折叠消费语义与 v2.5.9 冻结公开 view 不一致。",
                original_digest_hash=hash_value(old_digest),
                replay_digest_hash=hash_value(new_digest),
            ))
        else:
            checks.append(edge(
                "historical review_display -> folded Q1/Q2",
                "LIMIT",
                "未提供旧公开 view 作为历史折叠比对对象；本次只输出当前 selector 的公开 replay view。",
                replay_digest_hash=hash_value(new_digest),
            ))
        prior_input = read_json(self.request_artifacts(historical_sample)["review_input"])
        prior_request = read_json(self.request_artifacts(historical_sample)["request"])
        prior_binding = read_json(self.request_artifacts(historical_sample)["binding"])
        identity = compact_request_identity(prior_request, prior_binding, prior_input)
        identity.update({
            "sample": case_name,
            "current_source_sample": current_sample,
            "historical_source_sample": historical_sample,
            "current_candidate_id": current_snapshot.get("candidate_id"),
            "current_snapshot_id": current_snapshot.get("snapshot_id"),
            "historical_snapshot_id": prior_snapshot.get("snapshot_id"),
            "original_disposition": "accepted_historical_only",
        })
        current_view["observation_replay"] = self.public_summary(analysis_identity=identity, checks=checks)
        return current_view, {
            "case": case_name,
            "scenario": "historical accepted folded",
            "status": "PASS" if first_divergence(checks) is None else "FAIL",
            "llm_http_calls": 0,
            "checks": checks,
            "first_divergence": first_divergence(checks),
            "semantic_digest_hash": hash_value(new_digest),
            "public_view_hash": hash_value(scrub_public(current_view)),
        }

    def replay_current_rejected(
        self,
        current_sample: str = "call_early_v105",
        rejected_sample: str = "call_early_v105",
        *,
        case_name: str = "current_rejected",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        view, snapshot, evidence, _ = self.accepted_base(current_sample)
        view["manual_review_state"] = read_json(self.consumption / "historical_manual_state.json")
        view["preview"]["scenario_id"] = case_name
        view["preview"]["label"] = "真实归档冻结回放 · 失败回执与程序事实 · 非当前行情"
        display = self.bind(view, evidence)
        rejected_dir = self.synthesis / "samples" / rejected_sample
        semantic = read_json(rejected_dir / "SEMANTIC_REVIEW.json")
        old_snapshot = read_json(rejected_dir / "snapshot.json")
        old_evidence = read_json(rejected_dir / "evidence.json")
        original_input = read_json(rejected_dir / "review_input.json")
        old_request_body = rejected_dir / "actual_llm/request_http_body.bin"
        old_transport = read_json(rejected_dir / "actual_llm/transport_receipt.json")
        actual_request = read_json(old_request_body)
        displayed_receipt = read_json(rejected_dir / "DISPLAYED_RECEIPT.json")
        final_bound = rejected_final_hashes_match(rejected_dir, semantic)
        try:
            review_evidence.validate_review_evidence(old_evidence, old_snapshot)
            old_evidence_status = "LIMIT"
            old_evidence_reason = "旧 rejected 的 WPEM 包可按原 snapshot 重建；仍只证明冻结包一致，不证明外部行情源正确。"
        except Exception as exc:
            old_evidence_status = "FAIL"
            old_evidence_reason = f"旧 rejected 的 WPEM 包重建失败：{type(exc).__name__}:{exc}"
        request_evidence = evidence_input_from_request(actual_request)
        receipt_binding = (((displayed_receipt.get("review") or {}).get("decision_trace") or {}).get("binding") or {})
        input_hash = hash_value(original_input.get("evidence_input"))
        try:
            review_evidence.validate_review_evidence(evidence, snapshot)
            current_evidence_status = "LIMIT"
            current_evidence_reason = "当前 fallback 事实 WPEM 包可重建；这不是外部源真实性证明。"
        except Exception as exc:
            current_evidence_status = "FAIL"
            current_evidence_reason = f"当前 fallback 事实 WPEM 包重建失败：{type(exc).__name__}:{exc}"
        checks = [
            edge(
                "source/canonical -> program fallback facts",
                current_evidence_status,
                current_evidence_reason,
            ),
            edge(
                "source/canonical -> rejected WPEM evidence",
                old_evidence_status,
                old_evidence_reason,
            ),
            edge(
                "clean evidence -> original rejected LLM input",
                "PASS" if request_evidence == original_input.get("evidence_input") and input_hash == receipt_binding.get("evidence_input_hash") else "FAIL",
                "旧 rejected 的 request wire clean evidence、review_input.evidence_input 与 rejected receipt 绑定 hash 同类一致。"
                if request_evidence == original_input.get("evidence_input") and input_hash == receipt_binding.get("evidence_input_hash")
                else "旧 rejected 的 request wire clean evidence、review_input.evidence_input 或 receipt 绑定 hash 不一致。",
                original_input_schema=original_input.get("schema"),
                evidence_input_hash=input_hash,
            ),
            edge(
                "actual LLM request body -> transport receipt",
                "PASS" if old_request_body.exists() and file_sha256(old_request_body) == old_transport.get("request_http_body_sha256") else "FAIL",
                "旧 rejected 的 request_http_body.bin 与 transport receipt 哈希一致。"
                if old_request_body.exists() and file_sha256(old_request_body) == old_transport.get("request_http_body_sha256")
                else "旧 rejected 的 request_http_body.bin 缺失或与 transport receipt 哈希不一致。",
            ),
            edge(
                "original final/raw/receipt -> semantic verdict binding",
                "PASS" if final_bound else "FAIL",
                "原 final、provider 原始字节与原回执逐文件匹配语义审查记录的同类 hash；私有返回只核验 hash。"
                if final_bound
                else "原 final、provider 原始字节或原回执缺失或 hash 不匹配，不能把旧语义结论绑定到这份返回。",
            ),
            edge(
                "LLM final -> semantic validation",
                "PASS" if semantic.get("verdict") == "FAIL" and semantic.get("accepted_for_display") is False else "FAIL",
                "保留原语义 FAIL：最早已定位为 final 中无依据责任/补偿推断，不由机械 validator 改写。",
                semantic_verdict=semantic.get("verdict"),
            ),
            edge(
                "rejected receipt -> review_display fallback",
                "PASS" if display.get("current") is None else "FAIL",
                "selector 未将 rejected 正文作为 current；程序 facts 接管 Q1/Q2。",
            ),
        ]
        new_digest = view_semantic_digest(view)
        original_view_path = self.consumption / "samples" / case_name / "view.json"
        if original_view_path.exists():
            original_view = read_json(original_view_path)
            old_digest = view_semantic_digest(original_view)
            checks.append(edge(
                "fallback -> rendered Q1/Q2",
                "PASS" if old_digest == new_digest else "FAIL",
                "失败回执 + 程序 fallback 的公开消费语义与 v2.5.9 冻结 view 一致。" if old_digest == new_digest
                else "失败回执 + 程序 fallback 的公开消费语义与 v2.5.9 冻结 view 不一致。",
                original_digest_hash=hash_value(old_digest),
                replay_digest_hash=hash_value(new_digest),
            ))
        else:
            checks.append(edge(
                "fallback -> rendered Q1/Q2",
                "LIMIT",
                "未提供旧公开 view 作为 fallback 比对对象；本次只输出当前 selector 的公开 replay view。",
                replay_digest_hash=hash_value(new_digest),
            ))
        req_paths = self.request_artifacts(rejected_sample, source="synthesis")
        binding = read_json(req_paths["binding"]) if req_paths["binding"].exists() else {}
        identity = compact_request_identity(actual_request, binding, original_input)
        identity.update({
            "sample": case_name,
            "current_source_sample": current_sample,
            "rejected_source_sample": rejected_sample,
            "candidate_id": snapshot.get("candidate_id"),
            "snapshot_id": snapshot.get("snapshot_id"),
            "original_disposition": "semantic_rejected",
            "semantic_verdict": semantic.get("verdict"),
            "accepted_for_display": semantic.get("accepted_for_display"),
        })
        view["observation_replay"] = self.public_summary(
            analysis_identity=identity,
            checks=checks,
            extra_limits=("本例保留已人工定位的 LLM final 语义失败；重放本身不自动判定市场结果的责任。",),
        )
        return view, {
            "case": case_name,
            "scenario": "rejected plus program fallback",
            "status": "PASS" if first_divergence(checks) is None else "FAIL",
            "llm_http_calls": 0,
            "checks": checks,
            "first_divergence": first_divergence(checks),
            "artifacts": {key: artifact_ref(self.root, path) for key, path in req_paths.items() if path.exists()},
            "semantic_digest_hash": hash_value(new_digest),
            "public_view_hash": hash_value(scrub_public(view)),
        }

    def replay_case(self, spec: Mapping[str, Any] | str) -> tuple[dict[str, Any], dict[str, Any]]:
        if isinstance(spec, str):
            spec = next((item for item in DEFAULT_CASE_SPECS if item["name"] == spec), {"name": spec, "kind": spec})
        kind = spec.get("kind")
        if kind == "accepted_current":
            return self.replay_accepted_current(str(spec.get("sample") or spec.get("name")), case_name=str(spec.get("name")))
        if kind == "historical_accepted":
            return self.replay_historical_accepted(
                str(spec.get("current_sample") or "fresh_1"),
                str(spec.get("historical_sample") or "fresh_3"),
                case_name=str(spec.get("name") or "historical_accepted"),
            )
        if kind == "current_rejected":
            return self.replay_current_rejected(
                str(spec.get("current_sample") or "call_early_v105"),
                str(spec.get("rejected_sample") or "call_early_v105"),
                case_name=str(spec.get("name") or "current_rejected"),
            )
        raise ReplayError(f"unknown_case_kind:{kind}")


def load_manifest(root: Path, manifest_path: Path | None) -> dict[str, Any]:
    if manifest_path is None:
        return {}
    manifest = read_json(manifest_path if manifest_path.is_absolute() else root / manifest_path)
    for item in manifest.get("files") or ():
        if not isinstance(item, Mapping):
            continue
        path_value = item.get("path")
        if not path_value:
            continue
        path = Path(str(path_value))
        path = path if path.is_absolute() else root / path
        if not path.exists():
            raise ReplayError(f"manifest_file_missing:{path_value}")
        expected = item.get("sha256")
        if expected and file_sha256(path) != expected:
            raise ReplayError(f"manifest_file_hash_mismatch:{path_value}")
    return manifest


def case_specs_from_manifest(manifest: Mapping[str, Any], cases: Sequence[str] | None) -> list[Mapping[str, Any]]:
    specs = manifest.get("cases") if isinstance(manifest.get("cases"), list) else list(DEFAULT_CASE_SPECS)
    normalized: list[Mapping[str, Any]] = []
    for item in specs:
        if isinstance(item, str):
            normalized.append(next((spec for spec in DEFAULT_CASE_SPECS if spec["name"] == item),
                                   {"name": item, "kind": item}))
        elif isinstance(item, Mapping):
            normalized.append(dict(item))
    if not cases:
        return normalized
    selected = []
    names = set(cases)
    for spec in normalized:
        if spec.get("name") in names:
            selected.append(spec)
    missing = names.difference({str(spec.get("name")) for spec in selected})
    if missing:
        raise ReplayError("manifest_case_missing:" + ",".join(sorted(missing)))
    return selected


def run_replay(
    root: Path,
    out_dir: Path,
    cases: Sequence[str] | None = None,
    *,
    replayed_at_ms: int | None = None,
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    with offline_guard():
        manifest = manifest or {}
        ctx = ReplayContext(root, replayed_at_ms=replayed_at_ms, paths=manifest.get("paths") if isinstance(manifest.get("paths"), Mapping) else None)
        specs = case_specs_from_manifest(manifest, cases)
        report_cases = []
        generated_views = []
        for spec in specs:
            name = str(spec.get("name"))
            started = time.perf_counter()
            view, case_report = ctx.replay_case(spec)
            public_view = scrub_public(view)
            view_path = out_dir / "samples" / name / "view.json"
            write_json(view_path, public_view)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            case_report["elapsed_ms"] = elapsed_ms
            case_report["public_view"] = rel(root, view_path)
            generated_views.append(case_report["public_view"])
            report_cases.append(case_report)
    report = {
        "schema": SCHEMA,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "root": str(root.resolve()),
        "case_specs": specs,
        "llm_http_calls": 0,
        "network_guard": "installed",
        "cases": report_cases,
        "generated_public_views": generated_views,
        "first_divergence": first_divergence([check for case in report_cases for check in case.get("checks", [])]),
        "proof_limits": list(PROOF_LIMITS),
    }
    write_json(out_dir / "replay_report.json", report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replay the frozen Astra observation lineage without network calls.")
    parser.add_argument("--root", type=Path, default=Path("."), help="Repository root.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT, help="Replay artifact output directory.")
    parser.add_argument("--case", action="append", help="Case name to replay; repeatable. Defaults to manifest/default cases.")
    parser.add_argument("--manifest", type=Path, help="Optional frozen bundle manifest with paths/files/cases.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = load_manifest(args.root, args.manifest)
    report = run_replay(args.root, args.out_dir, args.case, manifest=manifest)
    status = "PASS" if report.get("first_divergence") is None else "FAIL"
    print(json.dumps({
        "status": status,
        "cases": [case.get("case") for case in report.get("cases", [])],
        "report": str((args.out_dir / "replay_report.json").resolve()),
        "llm_http_calls": report.get("llm_http_calls"),
    }, ensure_ascii=False, sort_keys=True))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
