"""Local, immutable candidate evidence packets; no inference or network calls.

The archive is a bounded research consumer of existing frozen snapshots. It
does not participate in the workbench's day/manual ledger or review policy.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__:
    from . import astra_underwriting_v20 as core
    from . import astra_underwriting_store_v20 as store
    from . import astra_data_engine_v23 as data_engine
else:
    import astra_underwriting_v20 as core
    import astra_underwriting_store_v20 as store
    import astra_data_engine_v23 as data_engine

SCHEMA = "astra_candidate_asof_packet@1.0.0"
INDEX_SCHEMA = "astra_candidate_research_index@1.0.0"
WINDOW_VERSION = "candidate_window_coverage@1.0.0"
DEFAULT_INTERVAL_MS = 300_000
# Bump only when candidate replay/validation semantics change.
REPLAY_METHOD_VERSION = "candidate_replay@1"
DEFAULT_MAX_BYTES = 128 * 1024 * 1024
DEFAULT_MAX_SAMPLES = 4096
WINDOWS_MS = (900_000, 3_600_000)
IDENTITY_KEYS = ("venue", "underlying", "settlement_currency", "short_instrument",
                 "long_instrument", "side", "quantity_btc", "expiry_ms")


def _clock(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("invalid_asof_clock")
    return value


def _assert_packet_safe_value(value: Any) -> None:
    """Recognize the producer's JSON state signature without exempting secrets.

    This is an export deduplication record, not an authentication signature.
    Keep original bytes in the packet/hash; inspect its decoded finite fields.
    The general DataEngine secret guard is unchanged.
    """
    fields = {'state','since_ts_ms','previous_state','last_transition_ts_ms','reason_codes',
              'anchor_ok','net_gamma_ok','net_gamma_sign','net_gamma_transition','net_gamma_conflict'}
    def inspection(item, parent=None):
        if isinstance(item, Mapping):
            checked={}
            for key, child in item.items():
                if parent in {'export_health','data_export_bridge'} and key in {
                        'spacegate_signature','last_exported_spacegate_signature'}:
                    decoded=json.loads(child) if isinstance(child,str) else child
                    if decoded is not None and (not isinstance(decoded,dict) or set(decoded)!=fields):
                        raise ValueError('producer_state_signature_schema_mismatch')
                    # Decode before applying all ordinary field/value checks.
                    checked['producer_state_record_'+key.removesuffix('_signature')]=inspection(decoded)
                else:
                    checked[key]=inspection(child,key)
            return checked
        if isinstance(item,list): return [inspection(child) for child in item]
        return item
    data_engine._assert_safe_value(inspection(value))


def _manifests(snapshot: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    identity = snapshot["data_identity"]
    candidates = [identity.get("source_manifest"),
                  (identity.get("btc_map") or {}).get("source_manifest")]
    return [value for value in candidates if isinstance(value, Mapping)]


_ACQUISITION_CLOCKS = frozenset({"first_seen_at_ms", "retrieved_at_ms"})


def _record_basis(row: Mapping[str, Any]) -> dict[str, Any]:
    time = row.get("time") if isinstance(row.get("time"), Mapping) else {}
    return {"identity": row.get("identity"), "version": row.get("version"), "content": row.get("content"),
            "time": {key: value for key, value in time.items() if key not in _ACQUISITION_CLOCKS}}


def _source_records(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    records = {}
    for manifest in _manifests(snapshot):
        # One freeze holds one version per source identity. Separate freezes (the
        # candidate's own and its MAP's) may legitimately hold different evaluations
        # of one stream, e.g. GEX-derived values re-scored on a newer FMZ snapshot
        # under the same GEX clock; each consumer reads its own manifest.
        identities = {}
        for row in manifest.get("source_records", []):
            record_id = row["record_id"]
            # The same facts can carry distinct role-specific qualifications.
            # Identity, original clocks and payload may not change under an ID.
            # Each collector stamps its own acquisition clock; the first manifest's
            # record (the workbench's own) is kept for receipt checks.
            if record_id in records and _record_basis(row) != _record_basis(records[record_id]):
                raise ValueError("source_record_identity_conflict")
            identity_hash = core._hash(row.get("identity"))
            basis_hash = core._hash(_record_basis(row))
            if identity_hash in identities and identities[identity_hash] != basis_hash:
                raise ValueError("source_identity_version_conflict")
            identities[identity_hash] = basis_hash
            records.setdefault(record_id, deepcopy(row))
    return records


def collect_source_receipts(snapshot: Mapping[str, Any], engine: Any) -> dict[str, Any]:
    """Read matching existing cache records, without fetch or renewed clocks.

    Frozen producer-only records may have no matching local cache record. Their
    acquisition clocks remain missing rather than borrowing a different source.
    """
    receipts = {}
    cutoff = snapshot["market"]["valuation_ts_ms"]
    for manifest in _manifests(snapshot):
        for frozen in manifest.get("source_records", []):
            identity = frozen.get("identity") or {}
            try:
                record = engine.read(identity["product_id"], now_ms=cutoff,
                    usage=(frozen.get("usage_qualification") or {}).get("usage") or "freeze", scope=identity.get("scope"))
            except (data_engine.DataEngineError, KeyError, ValueError):
                continue
            times = record.get("time") or {}
            if record.get("record_id") != frozen["record_id"] or any(
                    name.endswith("_ms") and value is not None and _clock(value) > cutoff for name, value in times.items()):
                continue
            receipt = {"record_id": frozen["record_id"], "payload_hash": record["version"]["payload_hash"],
                       "time": deepcopy(times), "meaning": "matching_cache_record_obtained_by_decision_cutoff"}
            receipts[frozen["record_id"]] = receipt
    return receipts


def _validate_receipts(snapshot: Mapping[str, Any], receipts: Mapping[str, Any]) -> None:
    records = _source_records(snapshot)
    cutoff = snapshot["market"]["valuation_ts_ms"]
    for record_id, receipt in receipts.items():
        frozen = records.get(record_id)
        if frozen is None or receipt.get("record_id") != record_id or receipt.get("payload_hash") != frozen["version"]["payload_hash"]:
            raise ValueError("source_receipt_identity_mismatch")
        for name, value in receipt["time"].items():
            if name.endswith("_ms") and value is not None and _clock(value) > cutoff:
                raise ValueError("future_source_receipt")
        for name, value in frozen["time"].items():
            if value is not None and receipt["time"].get(name) != value:
                raise ValueError("source_receipt_original_clock_mismatch")


def _validate_snapshot(snapshot: Mapping[str, Any]) -> None:
    core.llm_applicability_packet(snapshot)
    contract = snapshot["contract"]
    candidate_id = core._hash({key: contract[key] for key in IDENTITY_KEYS})[:24]
    if candidate_id != snapshot["candidate_id"] or candidate_id != contract["candidate_id"]:
        raise ValueError("candidate_identity_mismatch")
    cutoff = _clock(snapshot["market"]["valuation_ts_ms"])
    if snapshot.get("execution_allowed") is not False:
        raise ValueError("research_packet_cannot_grant_execution")
    for manifest in _manifests(snapshot):
        if manifest.get("manifest_hash") != core._hash({
                key: value for key, value in manifest.items() if key != "manifest_hash"}):
            raise ValueError("source_manifest_hash_mismatch")
        if _clock(manifest.get("cutoff_at_ms")) > cutoff:
            raise ValueError("future_manifest_cutoff")
        if manifest.get("candidate_id") not in (None, candidate_id):
            raise ValueError("source_manifest_candidate_mismatch")
        for record in manifest.get("source_records", []):
            for name, value in (record.get("time") or {}).items():
                if name.endswith("_ms") and value is not None and _clock(value) > cutoff:
                    raise ValueError("future_source_clock:" + name)
    _source_records(snapshot)
    quote = snapshot["market"]["quote"]
    for value in list((quote.get("exchange_times_ms") or {}).values()) + list(
            (quote.get("leg_received_ts_ms") or {}).values()) + [quote.get("received_ts_ms")]:
        if value is not None and _clock(value) > cutoff:
            raise ValueError("future_quote_clock")
    for part, names in ((snapshot["fact_context"], ("snapshot_ts_ms", "price_observed_ms", "valuation_ts_ms")),
                        (snapshot["risk"], ("input_ts_ms", "feature_end_ts_ms"))):
        for name in names:
            value = part.get(name)
            if value is not None and _clock(value) > cutoff:
                raise ValueError("future_input_clock:" + name)


def _financial_replay(snapshot: Mapping[str, Any], raw_payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    contract, economics, fee = snapshot["contract"], snapshot["economics"], snapshot["fee"]
    credit = snapshot["market"]["quote"]["credit_btc"]
    expected = credit - fee["amount_btc"] if credit is not None and fee["amount_btc"] is not None else None
    # Older genuine freezes have loss_budget_btc only. Validate that native
    # field without backfilling or upgrading the historical snapshot.
    for name in ("loss_budget_btc", "net_credit_after_fee_btc"):
        if name in economics and economics[name] != expected:
            raise ValueError("replay_credit_fee_mismatch")
    if economics["visible_credit_btc"] != credit:
        raise ValueError("replay_visible_credit_mismatch")
    mu = snapshot["risk"].get("mu_btc")
    margin = expected - mu if expected is not None and mu is not None else None
    if economics["mu_ref_btc"] != mu or (margin is None) != (economics["reference_margin_btc"] is None):
        raise ValueError("replay_risk_mismatch")
    if margin is not None and not math.isclose(margin, economics["reference_margin_btc"], abs_tol=1e-12):
        raise ValueError("replay_margin_mismatch")
    for row in snapshot["liability"]["payout_table"]:
        payout = core.inverse_spread_payout(contract["side"], contract["short_strike_usd"],
            contract["long_strike_usd"], row["settlement_usd"], contract["quantity_btc"])
        if not math.isclose(payout, row["payout_btc"], rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("replay_native_payout_mismatch")
        net = core.static_cashflow(credit, payout, fee["amount_btc"])["static_net_btc"]
        if (net is None) != (row["static_net_btc"] is None) or (net is not None and not math.isclose(
                net, row["static_net_btc"], abs_tol=1e-12)):
            raise ValueError("replay_static_cashflow_mismatch")
    book_records = [record for manifest in _manifests(snapshot) for record in manifest.get("source_records", [])
                    if (record.get("identity") or {}).get("product_id") == "deribit.btc.vertical.two_leg_book.v1"]
    raw_status = "raw_leg_books_missing"
    if raw_payload is not None:
        metadata_times = raw_payload.get("metadata_received_ts_ms")
        if metadata_times is not None:
            if not isinstance(metadata_times, Mapping):
                raise ValueError("instrument_metadata_receipt_clock_shape_invalid")
            if any(value is not None and _clock(value) > snapshot["market"]["valuation_ts_ms"]
                   for value in metadata_times.values()):
                raise ValueError("future_instrument_metadata_receipt")
        reproduced = core.evaluate(raw_payload)
        if reproduced["contract"] != contract or reproduced["fact_context"] != snapshot["fact_context"]:
            raise ValueError("raw_payload_contract_or_fact_mismatch")
        # Validate original native fields, including fee provenance. New display
        # aliases need not exist in an older immutable snapshot.
        for part in ("fee", "risk", "economics", "liability"):
            for name, value in snapshot[part].items():
                other = reproduced[part].get(name)
                equal = (math.isclose(value, other, rel_tol=1e-12, abs_tol=1e-12)
                         if isinstance(value, (int, float)) and not isinstance(value, bool)
                         and isinstance(other, (int, float)) and not isinstance(other, bool) else value == other)
                if not equal:
                    raise ValueError("raw_payload_" + part + "_mismatch:" + name)
        if (raw_payload.get("data_identity") or {}).get("source_sha256") != snapshot["data_identity"].get("source_sha256"):
            raise ValueError("raw_payload_source_mismatch")
        market = raw_payload["market"]
    elif book_records:
        values = book_records[0]["content"]["values"]
        market = {**snapshot["market"], **values, "received_ts_ms": snapshot["market"]["quote"].get("received_ts_ms")}
    else:
        market = None
    if market is not None:
        replayed_quote = core.visible_credit(contract, market, snapshot["market"]["valuation_ts_ms"])
        if replayed_quote != snapshot["market"]["quote"]:
            raise ValueError("replay_raw_quote_mismatch")
        raw_status = "raw_leg_books_replayed"
    return {"status": "PASS", "raw_quote_status": raw_status,
            "native_payout_rows": len(snapshot["liability"]["payout_table"]),
            "quantity_btc": contract["quantity_btc"], "mu_ref_btc": mu,
            "reference_margin_btc": economics["reference_margin_btc"]}


def build_packet(snapshot: Mapping[str, Any], *, feature_catalog: Mapping[str, Any] | None = None,
                 feature_catalog_error: str | None = None,
                 raw_payload: Mapping[str, Any] | None = None,
                 source_receipts: Mapping[str, Any] | None = None,
                 history: Sequence[Mapping[str, Any]] = (), interval_ms: int = DEFAULT_INTERVAL_MS) -> dict[str, Any]:
    _validate_snapshot(snapshot)
    interval_ms = _clock(interval_ms)
    if interval_ms == 0:
        raise ValueError("sampling_interval_must_be_positive")
    cutoff = snapshot["market"]["valuation_ts_ms"]
    records = _source_records(snapshot)
    receipts = deepcopy(dict(source_receipts or {}))
    _validate_receipts(snapshot, receipts)
    gaps = list(snapshot.get("gap_reasons") or [])
    if not records:
        gaps.append("frozen_source_manifest_missing")
    clock_gaps, manifest_clock_gaps = [], []
    for record_id, record in records.items():
        original_missing = [name for name in ("observation_end_ms", "first_seen_at_ms", "retrieved_at_ms", "ingested_at_ms")
                            if (record.get("time") or {}).get(name) is None]
        if original_missing:
            manifest_clock_gaps.append({"record_id": record_id, "missing_clocks": original_missing,
                "supplemental_cache_receipt_found": record_id in receipts,
                "reason": "original_freeze_did_not_retain_clock"})
        retained_clocks = {**(record.get("time") or {}), **(receipts.get(record_id, {}).get("time") or {})}
        missing = [name for name in ("observation_end_ms", "first_seen_at_ms", "retrieved_at_ms", "ingested_at_ms")
                   if retained_clocks.get(name) is None]
        if missing:
            clock_gaps.append({"record_id": record_id, "missing_clocks": missing,
                               "reason": "clock_missing_in_manifest_and_matching_receipt"})
    if manifest_clock_gaps:
        gaps.append("original_source_acquisition_clocks_not_frozen")
    if feature_catalog is None:
        reason = "risk_asset_export_failed:" + feature_catalog_error if feature_catalog_error else "risk_asset_not_configured"
        model_input = {"status": "risk_asset_unavailable" if feature_catalog_error else "risk_asset_not_configured",
                       "features": [], "row_qualified": False, "gap_reasons": [reason]}
        gaps.append(reason)
    else:
        if __package__:
            from . import astra_candidate_features_v1 as features
        else:
            import astra_candidate_features_v1 as features
        model_input = features.build_feature_input(snapshot, feature_catalog)
        gaps.extend(model_input.get("gap_reasons") or [])
    versions = {"snapshot_schema": snapshot["schema"], "packet_schema": SCHEMA,
                "window_method": WINDOW_VERSION, "sampling_interval_ms": interval_ms,
                "source_manifest_hashes": [manifest["manifest_hash"] for manifest in _manifests(snapshot)],
                "map_projection_hash": (snapshot["data_identity"].get("btc_map") or {}).get("map_id"),
                "feature_catalog_hash": (feature_catalog or {}).get("catalog_hash"),
                "risk_model_hash": snapshot["risk"].get("model_hash"),
                "research_asset_sha256": ((feature_catalog or {}).get("asset") or {}).get("sha256"),
                "research_protocol_sha256": ((feature_catalog or {}).get("protocol") or {}).get("sha256"),
                "replay_validator_code_hashes": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                    for name in ("astra_underwriting_v20.py", "astra_commercial_math_v17.py")}}
    comparison = core._hash({"candidate_id": snapshot["candidate_id"], "window_method": WINDOW_VERSION,
                            "interval_ms": interval_ms, "catalog": versions["feature_catalog_hash"],
                            "source_definitions": sorted({store._canonical({
                                "identity": row.get("identity"),
                                "normalizer_version": (row.get("version") or {}).get("normalizer_version")})
                                for row in records.values()}),
                            # Semantic replay identity, not source bytes: a comment-only or
                            # unrelated code release must not restart the window.
                            "replay_method": {"underwriting_schema": core.SCHEMA,
                                              "method": REPLAY_METHOD_VERSION},
                            "candidate_price_basis": snapshot["market"]["reference_source"],
                            "model_price_identity": (feature_catalog or {}).get("price_identity"),
                            "model_hash": versions["risk_model_hash"]})
    same = []
    for row in history:
        if row.get("candidate_id") != snapshot["candidate_id"]:
            continue
        asof = _clock(row.get("decision_asof_ms"))
        if asof > cutoff:
            raise ValueError("future_candidate_history")
        same.append(dict(row))
    same.sort(key=lambda row: row["decision_asof_ms"])
    boundary = max((i + 1 for i, row in enumerate(same) if row.get("comparability_hash") != comparison), default=0)
    if boundary:
        gaps.append("candidate_history_method_boundary")
    same = same[boundary:]
    current = {"candidate_id": snapshot["candidate_id"], "snapshot_id": snapshot["snapshot_id"],
               "decision_asof_ms": cutoff, "comparability_hash": comparison}
    same = [row for row in same if row["snapshot_id"] != snapshot["snapshot_id"]] + [current]
    windows = []
    for window_ms in WINDOWS_MS:
        start = cutoff - window_ms
        anchor = next((row for row in reversed(same) if row["decision_asof_ms"] <= start), None)
        inside = ([anchor] if anchor is not None else []) + [
            row for row in same if start < row["decision_asof_ms"] <= cutoff]
        times = [row["decision_asof_ms"] for row in inside]
        coverage = cutoff - min(times)
        largest_gap = max((b - a for a, b in zip(times, times[1:])), default=0)
        windows.append({"requested_window_ms": window_ms, "actual_coverage_ms": coverage,
                        "sample_count": len(inside), "largest_gap_ms": largest_gap,
                        "boundary_anchor": ({key: anchor[key] for key in ("snapshot_id", "decision_asof_ms")}
                                            if anchor is not None else None),
                        "status": "covered" if coverage >= window_ms and largest_gap <= interval_ms * 2 else "insufficient",
                        "snapshot_refs": [{key: row[key] for key in ("snapshot_id", "decision_asof_ms")} for row in inside]})
    if any(window["status"] == "insufficient" for window in windows):
        gaps.append("candidate_window_coverage_insufficient")
    replay = _financial_replay(snapshot, raw_payload)
    if replay["raw_quote_status"] == "raw_leg_books_missing":
        gaps.append("raw_two_leg_books_not_retained")
    if raw_payload is not None and any(
            (raw_payload.get("metadata_received_ts_ms") or {}).get(leg) is None for leg in ("short", "long")):
        gaps.append("original_instrument_metadata_receipt_clock_not_retained")
    body = {"schema": SCHEMA, "identity": {"candidate_id": snapshot["candidate_id"],
            "snapshot_id": snapshot["snapshot_id"], "contract": deepcopy(snapshot["contract"])},
            "cutoff": {"decision_asof_ms": cutoff, "knowledge_rule": "observed_and_first_available_by_cutoff"},
            "snapshot": deepcopy(dict(snapshot)), "raw_payload": deepcopy(raw_payload),
            "sources": {"record_ids": sorted(records), "clock_gaps": clock_gaps,
                        "manifest_clock_gaps": manifest_clock_gaps,
                        "acquisition_receipts": receipts,
                        "raw_payload_hash": core._hash(raw_payload) if raw_payload is not None else None},
            "valuation": {key: deepcopy(snapshot[key]) for key in ("market", "fee", "economics", "liability")},
            "model_input": model_input, "binding": {"status": "not_validated",
                "candidate_price_basis": snapshot["market"]["reference_source"],
                "reason": "Binance_model_target_to_Deribit_native_payout_not_validated"},
            "window": {"method_version": WINDOW_VERSION, "comparability_hash": comparison, "coverage": windows},
            "risk": deepcopy(snapshot["risk"]), "versions": versions,
            "provenance": {"source_kind": snapshot["data_identity"]["kind"],
                "current_at_export": False, "meaning": "frozen_asof_evidence_not_a_current_quote"},
            "gaps": sorted(set(gaps)), "replay": replay,
            "inference_calls": 0, "llm_http_calls": 0, "execution_allowed": False}
    _assert_packet_safe_value(body)
    body["packet_hash"] = core._hash(body)
    return body


def replay_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    _assert_packet_safe_value(packet)
    if packet.get("schema") != SCHEMA or packet.get("packet_hash") != core._hash({
            key: value for key, value in packet.items() if key != "packet_hash"}):
        raise ValueError("packet_hash_mismatch")
    _validate_snapshot(packet["snapshot"])
    snapshot = packet["snapshot"]
    _validate_receipts(snapshot, packet["sources"].get("acquisition_receipts") or {})
    if packet["identity"] != {"candidate_id": snapshot["candidate_id"], "snapshot_id": snapshot["snapshot_id"],
                              "contract": snapshot["contract"]} or packet["risk"] != snapshot["risk"]:
        raise ValueError("packet_binding_mismatch")
    if packet["valuation"] != {key: snapshot[key] for key in ("market", "fee", "economics", "liability")}:
        raise ValueError("packet_valuation_mismatch")
    raw_payload = packet.get("raw_payload")
    if packet["sources"].get("raw_payload_hash") != (core._hash(raw_payload) if raw_payload is not None else None):
        raise ValueError("raw_payload_hash_mismatch")
    result = _financial_replay(snapshot, raw_payload)
    if result != packet["replay"] or packet["cutoff"]["decision_asof_ms"] != snapshot["market"]["valuation_ts_ms"]:
        raise ValueError("packet_replay_mismatch")
    return result


class CandidateResearchArchive:
    """Content-deduplicated packets and a capped append-only reference ledger.

    Budget exhaustion stops new evidence; it never deletes existing samples.
    A failed/partial index is rejected, and refresh callers isolate this error.
    """
    def __init__(self, path: str | Path, *, interval_ms: int = DEFAULT_INTERVAL_MS,
                 max_bytes: int = DEFAULT_MAX_BYTES, max_samples: int = DEFAULT_MAX_SAMPLES):
        self.path = Path(path)
        self.interval_ms, self.max_bytes, self.max_samples = map(_clock, (interval_ms, max_bytes, max_samples))
        if not all((self.interval_ms, self.max_bytes, self.max_samples)):
            raise ValueError("research_budgets_must_be_positive")
        self.index_path = self.path / "index.jsonl"

    def rows(self) -> list[dict[str, Any]]:
        rows = store.read_jsonl(self.index_path)
        for row in rows:
            if row.get("schema") != INDEX_SCHEMA or row.get("entry_hash") != core._hash({
                    key: value for key, value in row.items() if key != "entry_hash"}):
                raise ValueError("research_index_integrity_error")
        return rows

    def sample(self, snapshot: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        """Record the outcome so a stopped archive is visible, never silent."""
        result = self._sample(snapshot, **kwargs)
        self.last_sample_status = result if isinstance(result, Mapping) else {"status": "unknown"}
        return result

    def usage(self) -> dict[str, Any]:
        used = sum(item.stat().st_size for item in self.path.rglob('*') if item.is_file()) if self.path.exists() else 0
        last = getattr(self, "last_sample_status", None) or {}
        return {"used_bytes": int(used), "max_bytes": self.max_bytes,
                "status": last.get("status"), "reason": last.get("reason")}

    def _sample(self, snapshot: Mapping[str, Any], *, recorded_at_ms: int,
               feature_catalog: Mapping[str, Any] | None = None,
               feature_catalog_error: str | None = None,
               raw_payload: Mapping[str, Any] | None = None, source_receipts: Mapping[str, Any] | None = None) -> dict[str, Any]:
        _validate_snapshot(snapshot)
        recorded_at_ms = _clock(recorded_at_ms)
        cutoff = snapshot["market"]["valuation_ts_ms"]
        if recorded_at_ms < cutoff:
            raise ValueError("research_recorded_before_asof")
        with store._APPEND_LOCK, store._exclusive_file_lock(self.index_path):
            rows = self.rows()
            same = [row for row in rows if row["candidate_id"] == snapshot["candidate_id"]]
            if any(row["snapshot_id"] == snapshot["snapshot_id"] for row in rows):
                return {"status": "skipped", "reason": "snapshot_already_archived"}
            if same and cutoff < max(row["decision_asof_ms"] for row in same):
                return {"status": "skipped", "reason": "asof_not_monotonic"}
            if same and cutoff - max(row["decision_asof_ms"] for row in same) < self.interval_ms:
                return {"status": "skipped", "reason": "sampling_interval_not_elapsed"}
            if len(rows) >= self.max_samples:
                return {"status": "stopped", "reason": "research_sample_budget_exhausted"}
            packet = build_packet(snapshot, feature_catalog=feature_catalog, raw_payload=raw_payload,
                                  feature_catalog_error=feature_catalog_error,
                                  source_receipts=source_receipts, history=same, interval_ms=self.interval_ms)
            destination = self.path / (packet["packet_hash"] + ".json")
            prior = next((row for row in reversed(rows) if row["side"] == snapshot["contract"]["side"]), None)
            known_sources = {record_id for row in same for record_id in row["source_record_ids"]}
            record_ids = packet["sources"]["record_ids"]
            event = {"schema": INDEX_SCHEMA, "candidate_id": snapshot["candidate_id"], "snapshot_id": snapshot["snapshot_id"],
                     "side": snapshot["contract"]["side"], "decision_asof_ms": cutoff, "recorded_at_ms": recorded_at_ms,
                     "packet_hash": packet["packet_hash"], "packet_file": destination.name,
                     "comparability_hash": packet["window"]["comparability_hash"],
                     "source_record_ids": record_ids, "new_source_record_ids": sorted(set(record_ids) - known_sources),
                     "repeated_source_count": len(set(record_ids) & known_sources),
                     "rotated_from_candidate_id": prior["candidate_id"] if prior and prior["candidate_id"] != snapshot["candidate_id"] else None,
                     "trigger": "background_refresh_after_verified_snapshot", "sampling_interval_ms": self.interval_ms}
            event["entry_hash"] = core._hash(event)
            raw = (store._canonical(packet) + "\n").encode("utf-8")
            event_bytes = len((store._canonical(event) + "\n").encode("utf-8"))
            used = sum(item.stat().st_size for item in self.path.rglob('*') if item.is_file())
            if used + (0 if destination.exists() else len(raw)) + event_bytes > self.max_bytes:
                return {"status": "stopped", "reason": "research_byte_budget_exhausted"}
            if destination.exists():
                if destination.read_bytes() != raw:
                    raise ValueError("research_blob_hash_collision")
            else:
                tmp = destination.with_suffix(".tmp")
                with tmp.open("wb") as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, destination)
            store._append_jsonl_unlocked(self.index_path, [event])
            return {"status": "recorded", "packet_hash": packet["packet_hash"], "snapshot_id": snapshot["snapshot_id"],
                    "new_source_count": len(event["new_source_record_ids"]),
                    "repeated_source_count": event["repeated_source_count"]}

    def replay(self, snapshot_id: str) -> dict[str, Any]:
        row = next((row for row in self.rows() if row["snapshot_id"] == snapshot_id), None)
        if row is None:
            raise ValueError("research_snapshot_not_found")
        if row["packet_file"] != row["packet_hash"] + ".json" or len(row["packet_hash"]) != 64:
            raise ValueError("research_blob_path_invalid")
        packet = store.read_json(self.path / row["packet_file"])
        replay_packet(packet)
        if packet["packet_hash"] != row["packet_hash"] or packet["identity"]["snapshot_id"] != snapshot_id:
            raise ValueError("research_index_packet_mismatch")
        return packet


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True, help="Verify a saved packet offline; does not collect or call a model")
    args = parser.parse_args(argv)
    print(json.dumps(replay_packet(store.read_json(args.packet)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
