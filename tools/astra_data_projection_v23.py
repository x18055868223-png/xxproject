"""Thin wrappers for already captured facts. No network or market inference."""
from copy import deepcopy
from typing import Any, Mapping

if __package__:
    from . import astra_underwriting_v20 as core
else:
    import astra_underwriting_v20 as core

BRIDGE = "fmz.bridge.snapshots.v1"
GATE = "fmz.spacegate.current.v1"
BOOK = "deribit.btc.vertical.two_leg_book.v1"
DISCOVERY = "deribit.btc.discovery.current.v1"


def _clock(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def fmz_records(engine, fact: Mapping[str, Any], now: int) -> list[dict]:
    ts = _clock(fact.get("snapshot_ts_ms"))
    if ts is None:
        return []
    fields = ("source_hash", "snapshot_ts_ms", "strategy_version", "underlying", "runtime_mode",
              "current_price_usd", "current_price_source", "price_observed_ms", "market_facts")
    bridge = engine.ingest(BRIDGE, {key: fact.get(key) for key in fields},
                           observation_end_ms=ts, retrieved_at_ms=None, ingested_at_ms=now,
                           source_revision=fact.get("source_hash"),
                           data_state="OK" if fact.get("status") == "current" else "PARTIAL",
                           reason_codes=fact.get("gap_reasons") or [])
    records = [engine.read(BRIDGE, now_ms=now, usage="current_fact")]
    facts = fact.get("market_facts") or {}
    gate = facts.get("space_gate") or {}
    gate_ts = _clock(gate.get("observed_at_ms"))
    if gate_ts is not None:
        engine.ingest(GATE, gate, observation_end_ms=gate_ts, retrieved_at_ms=None,
                      ingested_at_ms=now, source_revision=fact.get("source_hash"),
                      parent_record_ids=[bridge["record_id"]], data_state="OK")
        records.append(engine.read(GATE, now_ms=now, usage="space_gate"))
    gamma = gate.get("net_gamma") or {}
    gamma_ts = _clock(gamma.get("source_ts_ms"))
    if gamma_ts is not None:
        # Upstream clock and values stay stable when another FMZ tick repeats
        # the same response; a new bridge timestamp is not a GEX revision.
        stable_gamma = {key: gamma.get(key) for key in
                        ("value", "unit", "sign", "source", "source_ts_ms", "valid", "transition", "conflict")}
        engine.ingest("gex.netgamma.proxy.v1", stable_gamma, observation_end_ms=gamma_ts,
                      retrieved_at_ms=None, ingested_at_ms=now,
                      data_state="OK" if gamma.get("valid") and not gamma.get("conflict") else "INVALID")
        records.append(engine.read("gex.netgamma.proxy.v1", now_ms=now, usage="space_gate"))
    anchor = (facts.get("space_context") or {}).get("anchor") or {}
    anchor_ts = _clock(anchor.get("gex_source_ts_ms"))
    if anchor_ts is not None:
        engine.ingest("gex.effective.accepted.v1", anchor, observation_end_ms=anchor_ts,
                      retrieved_at_ms=None, ingested_at_ms=now,
                      data_state="OK" if anchor.get("ready") else "PARTIAL")
        records.append(engine.read("gex.effective.accepted.v1", now_ms=now, usage="space_gate"))
    macro = facts.get("macro_pressure") or {}
    macro_ts = _clock(macro.get("last_data_time"))
    if macro_ts is not None:
        stable_macro = {key: value for key, value in macro.items() if key not in {"data_age_ms"}}
        engine.ingest("macro.risk_state.components.v1", stable_macro, observation_end_ms=macro_ts,
                      retrieved_at_ms=None, ingested_at_ms=now, data_state="PARTIAL",
                      reason_codes=["FIELD_CLOCKS_AND_RELEASE_SCHEDULE_RETAINED_IN_COMPONENTS"])
        records.append(engine.read("macro.risk_state.components.v1", now_ms=now, usage="background"))
    return records


def qualify_fmz(engine, fact: Mapping[str, Any], now: int) -> dict:
    projected = deepcopy(dict(fact))
    records = fmz_records(engine, projected, now)
    bridge = next((r for r in records if r.get("identity", {}).get("product_id") == BRIDGE), None)
    projected["data_engine_usage"] = deepcopy((bridge or {}).get("usage_decision") or
                                             {"can_use": False, "status": "MISSING"})
    if projected.get("status") == "current" and not projected["data_engine_usage"].get("can_use"):
        projected["status"] = "background_only"
        projected["gap_reasons"] = sorted(set(projected.get("gap_reasons", []) + ["data_engine_fmz_usage_rejected"]))
    if projected.get("status") == "current":
        rejected = [record for record in records
                    if record.get("identity", {}).get("product_id") in {GATE, "gex.netgamma.proxy.v1"}
                    and not record.get("usage_decision", {}).get("can_use")]
        if rejected:
            projected["status"] = "background_only"
            projected["gap_reasons"] = sorted(set(projected.get("gap_reasons", []) + ["data_engine_space_input_usage_rejected"]))
    return projected


def candidate_manifest(engine, payload: Mapping[str, Any], fact: Mapping[str, Any],
                       discovered: Mapping[str, Any], now: int) -> tuple[dict, list[str]]:
    market = payload["market"]
    contract = core.candidate_identity(payload["candidate"], payload["metadata"], now)
    scope = {"candidate_id": contract["candidate_id"], "quantity_btc": contract["quantity_btc"],
             "short": contract["short_instrument"], "long": contract["long_instrument"]}
    records = fmz_records(engine, fact, now)
    quote = core.visible_credit(contract, market, now)
    book_times = [_clock((market.get(leg + "_book") or {}).get("timestamp")) for leg in ("short", "long")]
    observation = min(t for t in book_times if t is not None) if any(t is not None for t in book_times) else 0
    engine.ingest(BOOK, {"short_book": market.get("short_book"), "long_book": market.get("long_book"),
                         "quantity_btc": contract["quantity_btc"], "leg_received_ts_ms": market.get("leg_received_ts_ms"),
                         "combined_quote": quote}, observation_end_ms=observation,
                  retrieved_at_ms=market.get("received_ts_ms"), ingested_at_ms=now, scope=scope,
                  data_state="OK" if not quote["gap_reasons"] else "INVALID", reason_codes=quote["gap_reasons"])
    book = engine.read(BOOK, now_ms=now, usage="candidate_quote", scope=scope)
    records.append(book)
    records.append(engine.ingest("deribit.btc.option.instrument.v1", payload["metadata"],
                                observation_end_ms=max((payload.get("metadata_received_ts_ms") or {}).values(), default=now),
                                retrieved_at_ms=max((payload.get("metadata_received_ts_ms") or {}).values(), default=now),
                                ingested_at_ms=now, scope=scope))
    records.append(engine.ingest("deribit.btc.index.v1", {"index_price": market["reference_price_usd"],
                                  "reference_source": market["reference_source"],
                                  "short_index": (market.get("short_book") or {}).get("index_price"),
                                  "long_index": (market.get("long_book") or {}).get("index_price")},
                                observation_end_ms=observation, retrieved_at_ms=market.get("received_ts_ms"),
                                ingested_at_ms=now, scope=scope))
    fee = payload.get("fee") or {}
    records.append(engine.ingest("deribit.btc.option.fee_schedule.v1", fee,
                                observation_end_ms=now, retrieved_at_ms=None, ingested_at_ms=now, scope=scope,
                                data_state="OK" if fee.get("basis") in {"configured_assumption", "actual"} else "MISSING"))
    risk = payload.get("risk") or {}
    risk_ts = _clock(risk.get("input_ts_ms"))
    if risk_ts is not None:
        records.append(engine.ingest("risk.astra.frozen_mu.reference.v1", risk,
                                    observation_end_ms=risk_ts, retrieved_at_ms=None, ingested_at_ms=now,
                                    scope=scope, source_revision=risk.get("input_row_hash"),
                                    data_state="OK" if risk.get("support_verified") else "INVALID"))
    discovery_ts = _clock(discovered.get("captured_ts_ms"))
    if discovery_ts is not None:
        records.append(engine.ingest(DISCOVERY, discovered, observation_end_ms=discovery_ts,
                                    retrieved_at_ms=None, ingested_at_ms=now,
                                    scope={"asset": "BTC", "target_hours": 24, "width_usd": 2000,
                                           "quantity_btc": 1, "delta_target": .35, "delta_max": .5}))
    critical = {BOOK: "candidate_quote", "deribit.btc.option.instrument.v1": "contract_definition",
                "deribit.btc.index.v1": "candidate_quote", "deribit.btc.option.fee_schedule.v1": "underwriting_accounting",
                "risk.astra.frozen_mu.reference.v1": "underwriting_accounting"}
    qualified = []
    gaps = []
    for record in records:
        product_id = record.get("identity", {}).get("product_id")
        if product_id in critical:
            record = engine.read(product_id, now_ms=now, usage=critical[product_id], scope=scope)
            if not record.get("usage_decision", {}).get("can_use"):
                gaps.append("data_engine_quote_usage_rejected" if product_id == BOOK else "data_engine_required_source_usage_rejected")
        qualified.append(record)
    manifest = engine.freeze(qualified, cutoff_at_ms=now, candidate_id=contract["candidate_id"])
    return manifest, gaps
