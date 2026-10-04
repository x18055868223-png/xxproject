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
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import re
import smtplib
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping
from urllib.parse import unquote, urlparse

if __package__:
    from . import astra_underwriting_discovery_v20 as discovery
    from . import astra_underwriting_fact_context_v20 as fact_context
    from . import astra_underwriting_guidance_v20 as guidance
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
    from . import signal_llm_review
else:  # direct CLI execution from tools/
    import astra_underwriting_discovery_v20 as discovery
    import astra_underwriting_fact_context_v20 as fact_context
    import astra_underwriting_guidance_v20 as guidance
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
    import signal_llm_review

SCHEMA = "astra_workbench@2.0.0"
STATE_SCHEMA = "astra_workbench_state@2.0.0"
TRANSITION_SCHEMA = "astra_workbench_space_gate_transition@2.0.0"
DAILY_SNAPSHOT_SCHEMA = "astra_workbench_daily_snapshot@2.0.0"

SPACE_ELIGIBLE = "STRUCTURE_ELIGIBLE"
SPACE_BLOCKED = "STRUCTURE_BLOCKED"
REVIEW_COOLDOWN_MS = 600_000
EMAIL_COOLDOWN_MS = 14_400_000
DEFAULT_DAILY_LIMIT = 15
DEFAULT_HTTP_TIMEOUT = 180
DEFAULT_MODEL = getattr(signal_llm_review, "DEFAULT_MODEL", "deepseek-reasoner")
_BJT_OFFSET_MS = 8 * 3_600_000
MANUAL_REVIEW_COOLDOWN_REASON = "manual_review_cooldown_active"
MANUAL_REVIEW_INFLIGHT_REASON = "manual_review_inflight"
MANUAL_REVIEW_BUDGET_EXHAUSTED_REASON = "manual_review_budget_exhausted"
_POST_ATOMIC_REASONS = {MANUAL_REVIEW_COOLDOWN_REASON, MANUAL_REVIEW_INFLIGHT_REASON}


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
    if (out.get("status") in {"OK", "RESEARCH_OK", "attached"}
            and body.get("response_protocol") != review_core.RESPONSE_PROTOCOL):
        out.update(status="stale_protocol_review", legacy_status=value.get("status"),
                   current_protocol_qualified=False,
                   expected_response_protocol=review_core.RESPONSE_PROTOCOL,
                   reason_cn="旧协议历史回执，未按当前有限目录复核；未自动重发。")
        if body is not out:
            out["review"] = _review_receipt_for_display(body)
    return out


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
    })


@dataclass
class WorkbenchConfig:
    snapshots_jsonl: Path
    state_dir: Path
    frontend_dir: Path | None = None
    server_token: str | None = None
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

    @property
    def state_path(self) -> Path:
        return self.state_dir / "workbench_state.json"

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
        self._reconcile_interrupted_review()

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
            if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", candidate_id):
                path = (review_core._review_state_path(self.config.review_state_dir, candidate_id, snapshot_id)
                        if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", snapshot_id)
                        else self.config.review_state_dir / ("candidate-" + candidate_id + ".json"))
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
                "idempotency_key": key,
                "review": deepcopy(dict(settled)) if settled else None,
                "llm_call_count": len(persisted.get("attempts") or []),
            }
            review["inflight"] = None
            review["last_completed_ts_ms"] = self.clock_ms()
            review["last_result"] = result
            if key:
                review.setdefault("idempotency", {})[key] = {
                    "status": "settled", "result": deepcopy(result), "updated_at_ms": self.clock_ms()}

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
                raise ValueError("discovery input unavailable for current use")
            return {"result": deepcopy(record["content"]["values"]["result"]),
                    "data_engine_receipt": {"record_id": record["record_id"],
                                            "retrieved_at_ms": record["time"]["retrieved_at_ms"],
                                            "observation_end_ms": record["time"]["observation_end_ms"]}}
        def loader():
            values = discovery.capture_public_deribit_discovery(captured_ts_ms=now_ms, transport=transport)
            captured = values["captured_ts_ms"]
            return {"values": values, "observation_end_ms": captured,
                    "retrieved_at_ms": captured, "ingested_at_ms": captured}
        record = self.data_engine.fetch(data_projection.DISCOVERY, loader, now_ms=now_ms,
            scope={"asset": "BTC", "target_hours": 24, "width_usd": 2000,
                   "quantity_btc": 1, "delta_target": .35, "delta_max": .5,
                   "capture_contract": "complete_delta_population@1"})
        if not record.get("usage_decision", {"can_use": True}).get("can_use"):
            raise ValueError("discovery cache unavailable for current use")
        return deepcopy(record["content"]["values"])

    def _default_quote_capture(
        self,
        short_instrument: str,
        long_instrument: str,
        quantity_btc: float,
        *,
        now_ms: int,
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
                                                  transport=transport, clock_ms=self.clock_ms)
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
        state.setdefault("btc_map", {})
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

    def _map_prior_state(self) -> dict[str, Any]:
        state = self._load_state().get("btc_map")
        return deepcopy(dict(state)) if isinstance(state, Mapping) else {}

    def _store_btc_map_state(self, btc_map: Mapping[str, Any] | None) -> None:
        if not isinstance(btc_map, Mapping):
            return

        def update(state: dict[str, Any]) -> None:
            state["btc_map"] = map_projection.persistable_state(btc_map, state.get("btc_map"))

        self._locked_state_update(update)

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
            config=self._map_source_config(), prior_state=self._map_prior_state())
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
        if not isinstance(native, Mapping):
            gaps.extend(str(item) for item in (fmz_fact.get("space_gate_gap_reasons") or []))
            gaps.append("producer_native_space_gate_missing")
            native_state = SPACE_BLOCKED
        else:
            native_state = str(native.get("state") or SPACE_BLOCKED)
            if native_state != SPACE_ELIGIBLE:
                gaps.extend(str(item) for item in native.get("reason_codes") or [])
        effective_state = SPACE_ELIGIBLE if not gaps and native_state == SPACE_ELIGIBLE else SPACE_BLOCKED
        anchor = deepcopy(native.get("anchor")) if isinstance(native, Mapping) and isinstance(native.get("anchor"), Mapping) else None
        net_gamma = deepcopy(native.get("net_gamma")) if isinstance(native, Mapping) and isinstance(native.get("net_gamma"), Mapping) else None
        body = {
            "state": effective_state,
            "producer_state": native_state if native_state in {SPACE_ELIGIBLE, SPACE_BLOCKED} else SPACE_BLOCKED,
            "eligible": effective_state == SPACE_ELIGIBLE,
            "source": ("producer_native_space_gate" if isinstance(native, Mapping)
                       else "producer_native_space_gate_missing"),
            "observed_at_ms": native.get("observed_at_ms") if isinstance(native, Mapping) else None,
            "since_ts_ms": native.get("since_ts_ms") if isinstance(native, Mapping) else None,
            "previous_state": native.get("previous_state") if isinstance(native, Mapping) else None,
            "last_transition_ts_ms": native.get("last_transition_ts_ms") if isinstance(native, Mapping) else None,
            "reason_codes": sorted(set(str(item) for item in ((native or {}).get("reason_codes") or [])))
            if isinstance(native, Mapping) else [],
            "anchor": anchor,
            "net_gamma": net_gamma,
            "fmz_fact_status": fmz_fact.get("status"),
            "fmz_source_hash": fmz_fact.get("source_hash"),
            "fmz_snapshot_ts_ms": fmz_fact.get("snapshot_ts_ms"),
            "strategy_version": fmz_fact.get("strategy_version"),
            "valuation_ts_ms": valuation_ms,
            "gap_reasons": sorted(set(reason for reason in gaps if reason)),
            "note_cn": "基础空间门通过仅表示可以继续核算当前保单，不代表补偿充分、LLM支持或交易批准。",
        }
        body["gate_hash"] = _hash({key: body[key] for key in (
            "state", "producer_state", "observed_at_ms", "since_ts_ms",
            "last_transition_ts_ms", "reason_codes", "anchor", "net_gamma",
            "fmz_source_hash", "gap_reasons"
        )})
        return body

    def _email_template(self, transition: Mapping[str, Any]) -> tuple[str, str]:
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
        def update(state: dict[str, Any]) -> None:
            sg = state.setdefault("space_gate", {})
            previous = sg.get("last_state")
            current = gate.get("state")
            sg["last_state"] = current
            sg["last_gate_hash"] = gate.get("gate_hash")
            sg["last_seen_ts_ms"] = now_ms
            if previous in {SPACE_ELIGIBLE, SPACE_BLOCKED} and current != previous:
                transition = {
                    "schema": TRANSITION_SCHEMA,
                    "transition_id": _hash({"previous": previous, "current": current,
                                             "gate_hash": gate.get("gate_hash"), "ts": now_ms})[:24],
                    "transition_ts_ms": now_ms,
                    "previous_state": previous,
                    "current_state": current,
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
        if not isinstance(snapshot, Mapping):
            reasons.append("snapshot_missing")
            return {"enabled": False, "reasons": sorted(set(reasons))}
        quote = ((snapshot.get("market") or {}).get("quote") or {})
        economics = snapshot.get("economics") if isinstance(snapshot.get("economics"), Mapping) else {}
        risk = snapshot.get("risk") if isinstance(snapshot.get("risk"), Mapping) else {}
        if snapshot.get("status") != "reviewable":
            reasons.append("snapshot_not_reviewable")
        if quote.get("credit_btc") is None:
            reasons.append("quote_invalid_or_stale")
        if (snapshot.get("fee") or {}).get("amount_btc") is None:
            reasons.append("fee_missing_or_incomplete")
        if risk.get("status") != "supported" or economics.get("mu_ref_btc") is None:
            reasons.append("risk_reference_missing_or_unqualified")
        margin = economics.get("reference_margin_btc")
        if margin is None:
            reasons.append("m_ref_null")
        else:
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
            payload = dict(self.quote_capture(
                raw_candidate["short_instrument"],
                raw_candidate["long_instrument"],
                raw_candidate["quantity_btc"],
                now_ms=now_ms,
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
            prior = self.ledger.state()["snapshots"].get(snapshot["snapshot_id"])
            annex = (store.build_card_annex(self.ledger.state(), snapshot["snapshot_id"])
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
            discovered: Mapping[str, Any] | None = None
            candidates: list[dict[str, Any]] = []
            try:
                discovered = self.discovery_capture(now_ms=now)
            except Exception as exc:
                gaps.append("discovery_refresh_error")
                candidates = [
                    {"side": side, "status": "error", "snapshot": None,
                     "review": {"status": "not_attached"},
                     "review_gate": {"enabled": False, "reasons": ["discovery_refresh_error", type(exc).__name__]},
                     "reading_guidance": guidance.build_reading_guidance(None, fmz_fact),
                     "error": str(exc)[:240]}
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
            if record_daily:
                self._maybe_record_daily_snapshot(view, finished_at_ms)
                view["daily_snapshots"] = self.recent_daily_snapshots(limit=self.config.daily_snapshot_limit)
            with self._view_lock:
                self._current_view = deepcopy(view)
                self._last_refresh_error = None
            self._store_btc_map_state(btc_projection.get("btc_map"))
            return self._apply_current_review_availability(view, finished_at_ms)

    def cached_view(self) -> dict[str, Any]:
        """Read the collector's cache. Page reads never cause external requests."""
        now = self.clock_ms()
        with self._view_lock:
            view = deepcopy(self._current_view)
            failure = deepcopy(self._last_refresh_error)
        if view is None:
            fact = self.latest_fact(now)
            warm = {"schema": SCHEMA, "updated_at_ms": None, "served_at_ms": now,
                    "market_context": fact, "space_gate": self.space_gate_view(fact, now),
                    "candidates": [], "gaps": ["data_collector_warming_up"],
                    "daily_snapshots": self.recent_daily_snapshots(),
                    "transitions": self.recent_transitions(),
                    "manual_review_state": self.manual_review_state(now), "llm_call_count": 0,
                    "data_health": self._data_health(now)}
            return self._apply_current_review_availability(warm, now)
        # Recheck facts and current use, without overwriting frozen candidate evidence.
        fact = self.latest_fact(now)
        gate = self.space_gate_view(fact, now)
        view.update({"served_at_ms": now, "market_context": fact, "space_gate": gate,
                     "manual_review_state": self.manual_review_state(now)})
        view["btc_map_current_usage"] = self._map_current_usage(view.get("btc_map"), now)
        # Global display is a current cache projection. Candidate MAPs stay frozen.
        current_projection = self._build_btc_map_projection(now, fact, collect_slow_sources=False)
        view["btc_map"] = deepcopy(current_projection.get("btc_map"))
        view["data_health"] = self._data_health(now, current_projection)
        prior_map_gaps = set((view.get("btc_map_projection") or {}).get("gaps") or [])
        view["gaps"] = sorted((set(view.get("gaps") or []) - prior_map_gaps)
                              | set(current_projection.get("gaps") or []))
        view["btc_map_projection"] = deepcopy(current_projection)
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
            item["btc_map_current_usage"] = self._map_current_usage(
                (snapshot.get("data_identity") or {}).get("btc_map"), now)
            item["btc_map_review_delta"] = self._map_review_delta(snapshot, view["manual_review_state"])
            if reasons:
                item["review_gate"] = {"enabled": False, "reasons": sorted(set(reasons))}
        if failure:
            view["collector_failure"] = failure
            view["gaps"] = sorted(set(view.get("gaps", []) + ["data_collector_refresh_failed"]))
        return self._apply_current_review_availability(view, now)

    def _map_current_usage(self, mapping: Mapping[str, Any] | None, now_ms: int) -> dict[str, Any]:
        """Requalify source identities without mutating the frozen MAP or calling HTTP."""
        if not isinstance(mapping, Mapping):
            return {"status": "MISSING", "checked_at_ms": now_ms, "changed_sources": [], "unusable_sources": []}
        consumer = mapping.get("source_consumer") if isinstance(mapping.get("source_consumer"), Mapping) else {}
        if self._map_source_mode() == map_projection.SOURCE_MODE_PRODUCER_MANIFEST and consumer.get("status") != "OK":
            return {"status": "ERROR", "checked_at_ms": now_ms, "changed_sources": [], "unusable_sources": [],
                    "error": deepcopy(consumer.get("error") or {"code": "PRODUCER_SOURCE_UNQUALIFIED"})}
        try:
            engine = self._map_engine_for_projection(now_ms, {"source_consumer": mapping.get("source_consumer")})
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

    def _map_review_delta(self, snapshot: Mapping[str, Any], review_state: Mapping[str, Any]) -> dict[str, Any]:
        receipt = review_state.get("last_result") or {}
        if receipt.get("candidate_id") != snapshot.get("candidate_id") or not receipt.get("snapshot_id"):
            return {}
        reviewed = (self.ledger.state().get("snapshots", {}).get(receipt["snapshot_id"]) or {}).get("snapshot")
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

    def recent_transitions(self, *, limit: int = 30) -> list[dict[str, Any]]:
        rows = _read_jsonl(self.config.transitions_path)
        return rows[-max(0, int(limit)):]

    def recent_daily_snapshots(self, *, limit: int = DEFAULT_DAILY_LIMIT) -> list[dict[str, Any]]:
        rows = _read_jsonl(self.config.daily_snapshots_path)
        return rows[-max(0, int(limit)):]

    def manual_review_state(self, now_ms: int | None = None) -> dict[str, Any]:
        now = self.clock_ms() if now_ms is None else int(now_ms)
        state = self._load_state()
        review = state.get("manual_review") if isinstance(state.get("manual_review"), Mapping) else {}
        last_started = int(review.get("last_started_ts_ms") or 0)
        cooldown_until = max(0, last_started + self.config.review_cooldown_ms)
        if cooldown_until <= now:
            cooldown_until = 0
        return {
            "cooldown_until_ms": cooldown_until,
            "cooldown_remaining_ms": max(0, cooldown_until - now),
            "inflight": deepcopy(review.get("inflight")),
            "last_started_ts_ms": review.get("last_started_ts_ms"),
            "last_completed_ts_ms": review.get("last_completed_ts_ms"),
            "last_result": _review_receipt_for_display(review.get("last_result")),
            "daily_http_budget": self.budget.snapshot() if self._shared_budget_ready() else None,
        }

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

    def _apply_current_review_availability(self, view: dict[str, Any], now_ms: int) -> dict[str, Any]:
        state = view.get("manual_review_state") if isinstance(view.get("manual_review_state"), Mapping) else None
        if state is None:
            state = self.manual_review_state(now_ms)
            view["manual_review_state"] = state
        availability = self.review_availability(now_ms, manual_review_state=state)
        view["manual_review_availability"] = availability
        for item in view.get("candidates") or []:
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
    ) -> dict[str, Any]:
        if not self._authorized(authorization):
            raise WorkbenchError(401, "unauthorized", "valid Authorization: Bearer token is required")
        if not candidate_id or not idempotency_key:
            raise WorkbenchError(400, "bad_request", "candidate_id and idempotency_key are required")
        now = self.clock_ms()
        existing = self._idempotency_result(idempotency_key)
        if existing is not None:
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
                                               btc_map_projection=btc_projection)
                if (item.get("snapshot") or {}).get("candidate_id") != candidate_id:
                    item = None
                if item:
                    cached["candidates"] = [item if entry.get("side") == item["side"] else entry
                                             for entry in cached["candidates"]]
                    cached.update({"updated_at_ms": self.clock_ms(), "market_context": fact,
                                   "space_gate": gate, "btc_map_projection": deepcopy(btc_projection),
                                   "btc_map": deepcopy((btc_projection or {}).get("btc_map")),
                                   "data_health": self._data_health(self.clock_ms(), btc_projection)})
                    with self._view_lock:
                        self._current_view = deepcopy(cached)
                    self._store_btc_map_state((btc_projection or {}).get("btc_map"))
        if item is None:
            result = {"schema": SCHEMA, "status": "rejected", "reason": "candidate_not_current",
                      "candidate_id": candidate_id, "idempotency_key": idempotency_key,
                      "llm_call_count": 0}
            self._store_idempotency(idempotency_key, result)
            return result
        base_gate = item.get("review_gate") or {}
        availability = self.review_availability(now)
        gate = self._merge_review_availability(base_gate, availability)
        availability_reasons = set(availability.get("reasons") or [])
        atomic_reason_active = bool(availability_reasons.intersection(_POST_ATOMIC_REASONS))
        budget_exhausted = MANUAL_REVIEW_BUDGET_EXHAUSTED_REASON in availability_reasons
        if (not base_gate.get("enabled")) or (budget_exhausted and not atomic_reason_active):
            result = {"schema": SCHEMA, "status": "rejected", "reason": "review_gate_disabled",
                      "candidate_id": candidate_id, "snapshot_id": item["snapshot"]["snapshot_id"],
                      "side": item.get("side"),
                      "review_gate": deepcopy(gate), "idempotency_key": idempotency_key,
                      "llm_call_count": 0}
            self._store_idempotency(idempotency_key, result)
            return result
        if not self.config.api_key or not self._shared_budget_ready():
            raise WorkbenchError(503, "llm_not_configured",
                                 "LLM key and existing shared usage ledger are required")
        snapshot = item["snapshot"]
        self.ledger.record_snapshot(snapshot, now_ms=now)
        self._begin_review(candidate_id, snapshot["snapshot_id"], item.get("side"), idempotency_key, now)
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
            )
            wrapped = {"schema": SCHEMA, "status": result.get("status") or "review_failed", "candidate_id": candidate_id,
                       "side": item.get("side"),
                       "snapshot_id": snapshot["snapshot_id"], "idempotency_key": idempotency_key,
                       "review": result, "llm_call_count": result.get("http_calls", 0)}
            self._finish_review(idempotency_key, wrapped, self.clock_ms())
            return wrapped
        except WorkbenchError as exc:
            wrapped = {"schema": SCHEMA, "status": "ERROR", "candidate_id": candidate_id,
                       "side": item.get("side"),
                       "idempotency_key": idempotency_key, "error": exc.as_payload(),
                       "llm_call_count": 0}
            self._finish_review(idempotency_key, wrapped, self.clock_ms())
            raise
        except Exception as exc:
            wrapped = {"schema": SCHEMA, "status": "ERROR", "candidate_id": candidate_id,
                       "side": item.get("side"),
                       "idempotency_key": idempotency_key,
                       "error": {"code": "review_failed", "message": str(exc)[:240],
                                 "error_type": type(exc).__name__},
                       "llm_call_count": 0}
            self._finish_review(idempotency_key, wrapped, self.clock_ms())
            return wrapped

    def _idempotency_result(self, key: str) -> dict[str, Any] | None:
        state = self._load_state()
        review = state.get("manual_review") if isinstance(state.get("manual_review"), Mapping) else {}
        entry = (review.get("idempotency") or {}).get(key)
        return _review_receipt_for_display(entry.get("result")) if isinstance(entry, Mapping) and isinstance(entry.get("result"), Mapping) else None

    def _store_idempotency(self, key: str, result: Mapping[str, Any]) -> None:
        def update(state: dict[str, Any]) -> None:
            review = state.setdefault("manual_review", {})
            idem = review.setdefault("idempotency", {})
            idem[key] = {"status": "settled", "result": deepcopy(dict(result)),
                         "updated_at_ms": self.clock_ms()}
            if len(idem) > 200:
                for old_key in sorted(idem, key=lambda k: int((idem[k] or {}).get("updated_at_ms") or 0))[:-200]:
                    idem.pop(old_key, None)

        self._locked_state_update(update)

    def _begin_review(self, candidate_id: str, snapshot_id: str, side: str | None,
                      key: str, now_ms: int) -> None:
        def update(state: dict[str, Any]) -> None:
            review = state.setdefault("manual_review", {})
            idem = review.setdefault("idempotency", {})
            if key in idem and isinstance(idem[key].get("result"), Mapping):
                raise WorkbenchError(409, "idempotency_race", "idempotency key already settled")
            inflight = review.get("inflight")
            if isinstance(inflight, Mapping):
                raise WorkbenchError(409, "review_inflight", "another manual review is already running")
            last_started = int(review.get("last_started_ts_ms") or 0)
            if last_started and now_ms - last_started < self.config.review_cooldown_ms:
                raise WorkbenchError(429, "review_cooldown", "manual review cooldown is still active",
                                     detail={"cooldown_until_ms": last_started + self.config.review_cooldown_ms})
            review["inflight"] = {"candidate_id": candidate_id, "snapshot_id": snapshot_id,
                                  "side": side, "idempotency_key": key,
                                  "started_ts_ms": now_ms}
            review["last_started_ts_ms"] = now_ms
            idem[key] = {"status": "inflight", "updated_at_ms": now_ms}

        self._locked_state_update(update)

    def _finish_review(self, key: str, result: Mapping[str, Any], now_ms: int) -> None:
        def update(state: dict[str, Any]) -> None:
            review = state.setdefault("manual_review", {})
            review["inflight"] = None
            review["last_completed_ts_ms"] = now_ms
            review["last_result"] = deepcopy(dict(result))
            review.setdefault("idempotency", {})[key] = {
                "status": "settled",
                "result": deepcopy(dict(result)),
                "updated_at_ms": now_ms,
            }

        self._locked_state_update(update)

    def start_background(self) -> None:
        if self._background_thread and self._background_thread.is_alive():
            return
        self._background_stop.clear()

        def run() -> None:
            while not self._background_stop.is_set():
                try:
                    self.refresh()
                except Exception as exc:
                    with self._view_lock:
                        self._last_refresh_error = {"failed_at_ms": self.clock_ms(),
                                                    "error_type": type(exc).__name__}
                if self._background_stop.wait(max(1.0, float(self.config.refresh_interval_s))):
                    break

        self._background_thread = threading.Thread(target=run, name="astra-workbench-v20", daemon=True)
        self._background_thread.start()

    def stop_background(self) -> None:
        self._background_stop.set()
        if self._background_thread:
            self._background_thread.join(timeout=5)


def make_handler(workbench: AstraWorkbench):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AstraWorkbenchV20/1.0"

        def _json(self, status: int, payload: Mapping[str, Any]) -> None:
            body = (_canonical(dict(payload)) + "\n").encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/workbench":
                try:
                    self._json(200, workbench.cached_view())
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
            self._serve_static(parsed.path)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/api/workbench/review":
                self._json(404, {"schema": SCHEMA, "status": "ERROR", "code": "not_found"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 < length <= 8192:
                    self._json(413, {"schema": SCHEMA, "status": "ERROR", "code": "request_size_invalid"})
                    return
                body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                result = workbench.review_candidate(
                    str(body.get("candidate_id") or ""),
                    str(body.get("idempotency_key") or ""),
                    authorization=self.headers.get("Authorization"),
                )
                self._json(200, result)
            except WorkbenchError as exc:
                self._json(exc.status_code, exc.as_payload())
            except json.JSONDecodeError:
                self._json(400, {"schema": SCHEMA, "status": "ERROR", "code": "bad_json"})

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
        map_sources_enabled=os.environ.get("ASTRA_MAP_SOURCES_ENABLED") == "1",
        map_source_mode=os.environ.get("ASTRA_MAP_SOURCE_MODE") or map_projection.SOURCE_MODE_COLLECTOR,
        map_producer_current_path=Path(producer_path) if producer_path else None,
        map_producer_max_age_ms=int(os.environ.get("ASTRA_MAP_PRODUCER_MAX_AGE_MS") or str(map_projection.DEFAULT_PRODUCER_MAX_AGE_MS)),
        risk_model_path=Path(os.environ["ASTRA_RISK_MODEL_PATH"]) if os.environ.get("ASTRA_RISK_MODEL_PATH") else None,
        risk_protocol_path=Path(os.environ["ASTRA_RISK_PROTOCOL_PATH"]) if os.environ.get("ASTRA_RISK_PROTOCOL_PATH") else None,
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
    server = ThreadingHTTPServer((host, int(port)), make_handler(workbench))
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
