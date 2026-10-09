"""WPEM maturity result attachment for frozen, offline evidence only."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

if __package__:
    from . import astra_candidate_asof_v1 as packets
    from .astra_commercial_math_v17 import inverse_spread_payout
else:
    import astra_candidate_asof_v1 as packets
    from astra_commercial_math_v17 import inverse_spread_payout

SCHEMA = "wpem_result_attachment@1.0.0"
QUALIFICATION_SCHEMA = "wpem_reference_qualification@1.0.0-shadow"
TERMINAL_NOT_QUALIFIED = "NOT_QUALIFIED_FINAL_FOR_THIS_ROUND"
OFFICIAL_IDENTITY = "RECONSTRUCTED_OFFICIAL_RESULT"
DERIBIT_PUBLIC_BASE = "https://www.deribit.com/api/v2/public"
EVENT_TIMESTAMP_TOLERANCE_MS = 600_000
BTC_EXPIRY_HOUR_UTC = 8

OFFICIAL_RULE = {
    "rule_version": "deribit_inverse_options_settlement_change_2026-08-01",
    "source_url": "https://support.deribit.com/hc/en-us/articles/31424939096093-Inverse-Options",
    "source_kind": "official_deribit_support_article",
    "recorded_as": "paraphrase",
    "paraphrase": (
        "For inverse BTC options expiring on or after 2026-08-01, ITM options first "
        "settle into the same-expiry inverse future at the strike; that future then "
        "settles in BTC. The net BTC result remains equivalent to direct cash "
        "settlement from the delivery price, and the generated future leg is not "
        "double-counted for delivery fees."
    ),
    "delivery_price_rule": "30-minute TWAP of the Deribit index ending at 08:00 UTC",
}


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(name + "_object_required")
    return value


def _number(value: Any, name: str, *, positive: bool = False, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool):
        raise ValueError(name + "_must_be_finite")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(name + "_must_be_finite") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(name + "_must_be_positive_finite" if positive else name + "_must_be_finite")
    return result


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(name + "_must_be_integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(name + "_must_be_integer") from exc
    if result != value and str(result) != str(value):
        raise ValueError(name + "_must_be_integer")
    return result


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _expiry_date(expiry_ms: int) -> str:
    value = datetime.fromtimestamp(expiry_ms / 1000, tz=timezone.utc)
    if value.hour != BTC_EXPIRY_HOUR_UTC or value.minute != 0 or value.second != 0 or value.microsecond != 0:
        raise ValueError("expiry_not_exact_08utc")
    return value.date().isoformat()


def _expiry_label(expiry_ms: int) -> str:
    value = datetime.fromtimestamp(expiry_ms / 1000, tz=timezone.utc)
    return value.strftime("%d%b%y").upper()


def _parse_json(raw: bytes, name: str) -> Mapping[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("official_raw_json_invalid:" + name) from exc
    data = _require_mapping(data, "official_raw")
    if data.get("error") is not None:
        raise ValueError("official_raw_response_error:" + name)
    if "result" not in data or data.get("result") is None:
        raise ValueError("official_raw_result_missing:" + name)
    return data


def _receipt_index(receipts: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    requests = receipts.get("requests")
    if not isinstance(requests, list):
        raise ValueError("official_receipts_requests_missing")
    if receipts.get("network_writes") not in (0, None):
        raise ValueError("official_receipts_network_writes_present")
    if receipts.get("provider_calls") not in (0, None):
        raise ValueError("official_receipts_provider_calls_present")
    if receipts.get("llm_calls") not in (0, None):
        raise ValueError("official_receipts_llm_calls_present")
    if receipts.get("actual_trade_evidence") not in (False, None):
        raise ValueError("official_receipts_actual_trade_evidence_present")
    indexed: dict[str, Mapping[str, Any]] = {}
    for row in requests:
        row = _require_mapping(row, "official_receipt")
        started = _integer(row.get("request_started_ms"), "request_started_ms")
        retrieved = _integer(row.get("retrieved_at_ms"), "retrieved_at_ms")
        if started < 0 or retrieved < 0 or started > retrieved:
            raise ValueError("official_receipt_client_time_invalid:" + str(row.get("name") or row.get("path")))
        for key in ("server_us_in", "server_us_out"):
            value = _integer(row.get(key), key)
            if value < 0:
                raise ValueError("official_receipt_server_time_negative:" + str(row.get("name") or row.get("path")))
        if row.get("server_us_in") > row.get("server_us_out"):
            raise ValueError("official_receipt_server_time_order_invalid:" + str(row.get("name") or row.get("path")))
        for key in (row.get("name"), row.get("path"), str(row.get("path") or "").split("/")[-1]):
            if key:
                if key in indexed and indexed[key] != row:
                    raise ValueError("official_receipt_duplicate_conflict:" + str(key))
                indexed[str(key)] = row
    return indexed


def _source_bytes(raw_sources: Mapping[str, bytes], *aliases: str) -> bytes:
    for alias in aliases:
        if alias in raw_sources:
            raw = raw_sources[alias]
            if not isinstance(raw, (bytes, bytearray)):
                raise ValueError("official_raw_bytes_required:" + alias)
            return bytes(raw)
    raise ValueError("official_raw_missing:" + aliases[0])


def _validate_receipt(
    receipt: Mapping[str, Any],
    raw: bytes,
    name: str,
    endpoint: str,
    required_params: Mapping[str, str],
    expiry_ms: int,
) -> dict[str, Any]:
    if receipt.get("method") != "GET":
        raise ValueError("official_receipt_method_not_get:" + name)
    if receipt.get("identity") != OFFICIAL_IDENTITY:
        raise ValueError("official_receipt_identity_not_reconstructed:" + name)
    if receipt.get("original_online_result_retained") is not False:
        raise ValueError("official_receipt_original_online_retained:" + name)
    if receipt.get("http_status") != 200:
        raise ValueError("official_receipt_http_status:" + name)
    if _integer(receipt.get("retrieved_at_ms"), "retrieved_at_ms") <= expiry_ms:
        raise ValueError("official_result_acquired_before_expiry:" + name)
    raw_hash = _sha256_bytes(raw)
    if receipt.get("sha256") != raw_hash:
        raise ValueError("official_raw_sha256_mismatch:" + name)
    if receipt.get("bytes") != len(raw):
        raise ValueError("official_raw_length_mismatch:" + name)
    parsed = urlparse(str(receipt.get("url") or ""))
    if parsed.scheme != "https" or parsed.netloc != "www.deribit.com":
        raise ValueError("official_receipt_domain_invalid:" + name)
    if parsed.path != "/api/v2/public/" + endpoint:
        raise ValueError("official_receipt_endpoint_invalid:" + name)
    query = parse_qs(parsed.query)
    for key, expected in required_params.items():
        if query.get(key, [None])[-1] != expected:
            raise ValueError("official_receipt_param_mismatch:" + name + ":" + key)
    return {
        "name": name,
        "path": receipt.get("path"),
        "url": receipt.get("url"),
        "method": receipt.get("method"),
        "endpoint": endpoint,
        "params": {key: query.get(key, [None])[-1] for key in sorted(required_params)},
        "http_status": receipt.get("http_status"),
        "bytes": len(raw),
        "sha256": raw_hash,
        "request_started_ms": receipt.get("request_started_ms"),
        "retrieved_at_ms": receipt.get("retrieved_at_ms"),
        "server_us_in": receipt.get("server_us_in"),
        "server_us_out": receipt.get("server_us_out"),
        "identity": receipt.get("identity"),
    }


def _receipt_for(index: Mapping[str, Mapping[str, Any]], name: str, path: str) -> Mapping[str, Any]:
    receipt = index.get(name) or index.get(path) or index.get(path.split("/")[-1])
    if receipt is None:
        raise ValueError("official_receipt_missing:" + name)
    return receipt


def _select_qualification(
    reference_qualification: Mapping[str, Any],
    candidate_id: str,
    snapshot_id: str,
    packet_hash: str,
    decision_asof_ms: int,
) -> Mapping[str, Any]:
    candidate = reference_qualification
    candidates = reference_qualification.get("candidates")
    if isinstance(candidates, list):
        matches = [row for row in candidates if isinstance(row, Mapping)
                   and row.get("candidate_id") == candidate_id]
        if len(matches) != 1:
            raise ValueError("reference_qualification_candidate_match_failed")
        candidate = matches[0]
    if candidate.get("schema") != QUALIFICATION_SCHEMA:
        raise ValueError("reference_qualification_schema_invalid")
    if candidate.get("candidate_id") != candidate_id:
        raise ValueError("reference_qualification_candidate_mismatch")
    if candidate.get("snapshot_id") != snapshot_id:
        raise ValueError("reference_qualification_snapshot_mismatch")
    if candidate.get("packet_hash") != packet_hash:
        raise ValueError("reference_qualification_packet_mismatch")
    if candidate.get("decision_asof_ms") != decision_asof_ms:
        raise ValueError("reference_qualification_asof_mismatch")
    if candidate.get("qualification_report_hash") != _hash(
        {key: value for key, value in candidate.items() if key != "qualification_report_hash"}
    ):
        raise ValueError("reference_qualification_report_hash_mismatch")
    if candidate.get("status") != TERMINAL_NOT_QUALIFIED or candidate.get("terminal_status") != TERMINAL_NOT_QUALIFIED:
        raise ValueError("reference_qualification_not_terminal_blocked")
    if candidate.get("shadow_only") is not True:
        raise ValueError("reference_qualification_not_shadow_only")
    if candidate.get("shadow_inference_allowed") is not False or candidate.get("row_qualified") is not False:
        raise ValueError("reference_qualification_fake_qualified")
    if candidate.get("production_risk_admitted") is not False or candidate.get("execution_allowed") is not False:
        raise ValueError("reference_qualification_production_admitted")
    if candidate.get("inference_calls") != 0 or candidate.get("llm_http_calls") != 0:
        raise ValueError("reference_qualification_calls_present")
    if any(candidate.get(key) is not None for key in ("mu_ref_btc", "M_ref_btc", "m_ref_btc")):
        raise ValueError("reference_qualification_finance_not_null")
    for key in ("feature_binding_manifest_hash", "target_price_basis_binding_hash"):
        if not isinstance(candidate.get(key), str) or len(candidate[key]) != 64:
            raise ValueError("reference_qualification_binding_hash_missing:" + key)
    return candidate


def _contract(packet: Mapping[str, Any]) -> tuple[Mapping[str, Any], str, str, int]:
    identity = _require_mapping(packet.get("identity"), "packet_identity")
    contract = _require_mapping(identity.get("contract"), "packet_contract")
    candidate_id = str(identity.get("candidate_id") or contract.get("candidate_id") or "")
    snapshot_id = str(identity.get("snapshot_id") or "")
    packet_hash = str(packet.get("packet_hash") or "")
    if not candidate_id or not snapshot_id or not packet_hash:
        raise ValueError("packet_identity_incomplete")
    cutoff = _require_mapping(packet.get("cutoff"), "packet_cutoff")
    decision_asof_ms = _integer(cutoff.get("decision_asof_ms"), "decision_asof_ms")
    return contract, candidate_id, snapshot_id, decision_asof_ms


def _validate_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    side = contract.get("side")
    if side not in {"put", "call"}:
        raise ValueError("contract_side_invalid")
    expiry_ms = _integer(contract.get("expiry_ms"), "expiry_ms")
    quantity = _number(contract.get("quantity_btc"), "quantity_btc", positive=True)
    if contract.get("settlement_currency") != "BTC":
        raise ValueError("contract_settlement_currency_not_btc")
    short_strike = _number(contract.get("short_strike_usd"), "short_strike_usd", positive=True)
    long_strike = _number(contract.get("long_strike_usd"), "long_strike_usd", positive=True)
    short_inst = str(contract.get("short_instrument") or "")
    long_inst = str(contract.get("long_instrument") or "")
    if not short_inst or not long_inst:
        raise ValueError("contract_instruments_missing")
    if side == "put" and short_strike <= long_strike:
        raise ValueError("contract_put_strike_order_invalid")
    if side == "call" and short_strike >= long_strike:
        raise ValueError("contract_call_strike_order_invalid")
    date = _expiry_date(expiry_ms)
    label = _expiry_label(expiry_ms)
    for inst in (short_inst, long_inst):
        if label not in inst:
            raise ValueError("contract_instrument_expiry_label_mismatch")
    return {
        "side": side,
        "expiry_ms": expiry_ms,
        "expiry_utc": _iso(expiry_ms),
        "expiry_date_utc": date,
        "expiry_label": label,
        "quantity_btc": quantity,
        "settlement_currency": "BTC",
        "short_instrument": short_inst,
        "long_instrument": long_inst,
        "short_strike_usd": short_strike,
        "long_strike_usd": long_strike,
    }


def _metadata(raw_json: Mapping[str, Any], expected_name: str, expected_side: str,
              expected_strike: float, expiry_ms: int) -> dict[str, Any]:
    result = _require_mapping(raw_json.get("result"), "instrument_result")
    if result.get("instrument_name") != expected_name:
        raise ValueError("instrument_name_mismatch:" + expected_name)
    if result.get("kind") != "option":
        raise ValueError("instrument_kind_not_option:" + expected_name)
    expected_option = "put" if expected_side == "put" else "call"
    if result.get("option_type") != expected_option:
        raise ValueError("instrument_option_type_mismatch:" + expected_name)
    if not math.isclose(_number(result.get("strike"), "instrument_strike", positive=True), expected_strike,
                        rel_tol=0, abs_tol=1e-12):
        raise ValueError("instrument_strike_mismatch:" + expected_name)
    if _integer(result.get("expiration_timestamp"), "instrument_expiration_timestamp") != expiry_ms:
        raise ValueError("instrument_expiry_mismatch:" + expected_name)
    if result.get("settlement_currency") != "BTC" or result.get("base_currency") != "BTC" or result.get("quote_currency") != "BTC":
        raise ValueError("instrument_btc_identity_mismatch:" + expected_name)
    if result.get("is_active") is not False:
        raise ValueError("instrument_not_expired:" + expected_name)
    return {
        "instrument_name": expected_name,
        "expiration_timestamp": result.get("expiration_timestamp"),
        "kind": result.get("kind"),
        "option_type": result.get("option_type"),
        "strike": result.get("strike"),
        "base_currency": result.get("base_currency"),
        "quote_currency": result.get("quote_currency"),
        "settlement_currency": result.get("settlement_currency"),
        "is_active": result.get("is_active"),
        "min_trade_amount": result.get("min_trade_amount"),
        "taker_commission": result.get("taker_commission"),
        "maker_commission": result.get("maker_commission"),
    }


def _instrument_identity_from_name(instrument_name: str, expiry_label: str) -> tuple[str, float]:
    parts = instrument_name.split("-")
    if len(parts) != 4 or parts[0] != "BTC" or parts[1] != expiry_label:
        raise ValueError("instrument_name_identity_invalid:" + instrument_name)
    try:
        strike = float(parts[2])
    except ValueError as exc:
        raise ValueError("instrument_name_strike_invalid:" + instrument_name) from exc
    if parts[3] == "P":
        return "put", strike
    if parts[3] == "C":
        return "call", strike
    raise ValueError("instrument_name_option_suffix_invalid:" + instrument_name)


def _delivery_price(raw_json: Mapping[str, Any], expiry_date: str) -> float:
    result = _require_mapping(raw_json.get("result"), "delivery_prices_result")
    data = result.get("data")
    if not isinstance(data, list):
        raise ValueError("delivery_prices_data_missing")
    rows = [row for row in data if isinstance(row, Mapping) and row.get("date") == expiry_date]
    if len(rows) != 1:
        raise ValueError("delivery_price_row_match_failed")
    return _number(rows[0].get("delivery_price"), "delivery_price", positive=True)


def _delivery_event(raw_json: Mapping[str, Any], expiry_label: str, expiry_ms: int,
                    delivery_price: float) -> dict[str, Any]:
    result = _require_mapping(raw_json.get("result"), "delivery_events_result")
    settlements = result.get("settlements")
    if not isinstance(settlements, list):
        settlements = result.get("data")
    if not isinstance(settlements, list):
        raise ValueError("delivery_events_data_missing")
    expected_instrument = "BTC-" + expiry_label
    rows = [row for row in settlements if isinstance(row, Mapping)
            and row.get("instrument_name") == expected_instrument
            and row.get("type") == "delivery"]
    if len(rows) != 1:
        raise ValueError("delivery_event_match_failed")
    row = rows[0]
    timestamp = _integer(row.get("timestamp"), "delivery_event_timestamp")
    if timestamp < expiry_ms or timestamp - expiry_ms > EVENT_TIMESTAMP_TOLERANCE_MS:
        raise ValueError("delivery_event_timestamp_not_near_expiry")
    index_price = _number(row.get("index_price"), "delivery_event_index_price", positive=True)
    mark_price = _number(row.get("mark_price"), "delivery_event_mark_price", positive=True)
    if not math.isclose(index_price, delivery_price, rel_tol=0, abs_tol=1e-12):
        raise ValueError("delivery_event_index_price_mismatch")
    if not math.isclose(mark_price, delivery_price, rel_tol=0, abs_tol=1e-12):
        raise ValueError("delivery_event_mark_price_mismatch")
    return {
        "instrument_name": expected_instrument,
        "timestamp": timestamp,
        "timestamp_utc": _iso(timestamp),
        "type": row.get("type"),
        "index_price": index_price,
        "mark_price": mark_price,
        "position": row.get("position"),
        "profit_loss": row.get("profit_loss"),
        "session_profit_loss": row.get("session_profit_loss"),
    }


def _oracle_inverse_spread(side: str, short: float, long: float, settlement: float, quantity: float) -> float:
    if side == "put":
        intrinsic = max(short - settlement, 0.0) - max(long - settlement, 0.0)
    else:
        intrinsic = max(settlement - short, 0.0) - max(settlement - long, 0.0)
    return quantity * intrinsic / settlement


def _accounting(packet: Mapping[str, Any], official_loss_btc: float) -> tuple[dict[str, Any], list[str]]:
    valuation = _require_mapping(packet.get("valuation"), "packet_valuation")
    economics = _require_mapping(valuation.get("economics"), "packet_economics")
    fee = valuation.get("fee")
    if fee is not None:
        fee = _require_mapping(fee, "packet_fee")
    gaps: list[str] = []
    credit = _number(economics.get("visible_credit_btc"), "visible_credit_btc", allow_none=True)
    fee_amount = _number((fee or {}).get("amount_btc"), "fee_amount_btc", allow_none=True)
    if credit is None:
        gaps.append("visible_credit_unknown")
    if fee_amount is None:
        gaps.append("fee_unknown")
    quote_hold = None if credit is None or fee_amount is None else credit - fee_amount - official_loss_btc
    quote_hold_net_loss = None if quote_hold is None else quote_hold < 0
    return {
        "c_visible_btc": credit,
        "fee_btc": fee_amount,
        "fee_basis": (fee or {}).get("basis"),
        "fee_assumption_basis": (fee or {}).get("assumption_basis"),
        "fee_covers": deepcopy((fee or {}).get("covers")),
        "entry_fee_btc": (fee or {}).get("entry_fee_btc"),
        "delivery_fee_btc": (fee or {}).get("delivery_fee_btc"),
        "L_official_btc": official_loss_btc,
        "payout_occurred": official_loss_btc > 0,
        "Pi_quote_hold_btc": quote_hold,
        "quote_hold_net_loss": quote_hold_net_loss,
        "Pi_actual_btc": None,
        "actual_net_loss_btc": None,
        "actual_net_loss_status": "UNKNOWN_NOT_RECORDED",
        "actual_trade_status": "UNKNOWN_NOT_RECORDED",
        "decision_status": "UNKNOWN_NOT_RECORDED",
        "actual_trade_evidence": False,
        "quote_hold_is_actual_trade": False,
    }, gaps


def result_attachment(
    packet: Mapping[str, Any],
    raw_sources: Mapping[str, bytes],
    receipts: Mapping[str, Any],
    reference_qualification: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach official maturity results without changing packet or risk identity."""
    packet = _require_mapping(packet, "packet")
    packets.replay_packet(packet)
    raw_sources = _require_mapping(raw_sources, "raw_sources")
    receipts = _require_mapping(receipts, "official_receipts")
    reference_qualification = _require_mapping(reference_qualification, "reference_qualification")
    contract_raw, candidate_id, snapshot_id, decision_asof_ms = _contract(packet)
    packet_hash = str(packet["packet_hash"])
    contract = _validate_contract(contract_raw)
    if decision_asof_ms >= contract["expiry_ms"]:
        raise ValueError("packet_decision_asof_not_before_expiry")
    receipt_index = _receipt_index(receipts)

    official_specs = [
        ("delivery_prices", "official_raw/delivery_prices.json", "delivery_prices.json",
         "get_delivery_prices", {"index_name": "btc_usd", "offset": "0", "count": "40"}),
        ("delivery_events", "official_raw/delivery_events.json", "delivery_events.json",
         "get_last_settlements_by_currency",
         {"currency": "BTC", "type": "delivery", "count": "1000", "search_start_timestamp": "1790496000000"}),
    ]
    raw_json: dict[str, Mapping[str, Any]] = {}
    official_sources: list[dict[str, Any]] = []
    for name, path, basename, endpoint, params in official_specs:
        raw = _source_bytes(raw_sources, path, basename, name)
        receipt = _receipt_for(receipt_index, name, path)
        source = _validate_receipt(receipt, raw, name, endpoint, params, contract["expiry_ms"])
        raw_json[name] = _parse_json(raw, name)
        official_sources.append(source)

    receipt_names = sorted(str(row.get("name")) for row in (receipts.get("requests") or [])
                           if str(row.get("name") or "").startswith("BTC-" + contract["expiry_label"] + "-"))
    if len(receipt_names) != 4:
        raise ValueError("official_instrument_metadata_count_mismatch")
    all_instrument_metadata: dict[str, dict[str, Any]] = {}
    for instrument_name in receipt_names:
        path = "official_raw/" + instrument_name + ".json"
        raw = _source_bytes(raw_sources, path, instrument_name + ".json", instrument_name)
        receipt = _receipt_for(receipt_index, instrument_name, path)
        source = _validate_receipt(
            receipt, raw, instrument_name, "get_instrument",
            {"instrument_name": instrument_name}, contract["expiry_ms"]
        )
        official_sources.append(source)
        parsed = _parse_json(raw, instrument_name)
        option_side, strike = _instrument_identity_from_name(instrument_name, contract["expiry_label"])
        all_instrument_metadata[instrument_name] = _metadata(
            parsed, instrument_name, option_side, strike, contract["expiry_ms"]
        )
    instrument_metadata = []
    for instrument_name, strike in (
        (contract["short_instrument"], contract["short_strike_usd"]),
        (contract["long_instrument"], contract["long_strike_usd"]),
    ):
        meta = all_instrument_metadata.get(instrument_name)
        if meta is None:
            raise ValueError("contract_instrument_metadata_missing:" + instrument_name)
        if not math.isclose(float(meta["strike"]), strike, rel_tol=0, abs_tol=1e-12):
            raise ValueError("contract_instrument_metadata_strike_mismatch:" + instrument_name)
        if meta["option_type"] != ("put" if contract["side"] == "put" else "call"):
            raise ValueError("contract_instrument_metadata_side_mismatch:" + instrument_name)
        instrument_metadata.append(meta)

    delivery_price = _delivery_price(raw_json["delivery_prices"], contract["expiry_date_utc"])
    event = _delivery_event(raw_json["delivery_events"], contract["expiry_label"], contract["expiry_ms"], delivery_price)
    official_loss = inverse_spread_payout(
        contract["side"], contract["short_strike_usd"], contract["long_strike_usd"],
        delivery_price, contract["quantity_btc"],
    )
    oracle_loss = _oracle_inverse_spread(
        contract["side"], contract["short_strike_usd"], contract["long_strike_usd"],
        delivery_price, contract["quantity_btc"],
    )
    if not math.isclose(official_loss, oracle_loss, rel_tol=0, abs_tol=1e-12):
        raise ValueError("official_inverse_payout_oracle_mismatch")

    accounting, gaps = _accounting(packet, official_loss)
    qualification = _select_qualification(
        reference_qualification, candidate_id, snapshot_id, packet_hash, decision_asof_ms
    )
    risk_reference = {
        "status": qualification.get("status"),
        "terminal_status": qualification.get("terminal_status"),
        "schema": qualification.get("schema"),
        "qualification_report_hash": qualification.get("qualification_report_hash"),
        "feature_binding_manifest_hash": qualification.get("feature_binding_manifest_hash"),
        "target_price_basis_binding_hash": qualification.get("target_price_basis_binding_hash"),
        "earliest_breakpoint": qualification.get("earliest_breakpoint"),
        "gap_reasons": deepcopy(qualification.get("gap_reasons") or []),
        "mu_ref_btc": None,
        "M_ref_btc": None,
        "m_ref_btc": None,
        "error_btc": None,
        "shadow_inference_allowed": False,
        "production_risk_admitted": False,
        "meaning": "B terminal not-qualified branch; official result closes contract liability only.",
    }
    body = {
        "schema": SCHEMA,
        "candidate_id": candidate_id,
        "snapshot_id": snapshot_id,
        "packet_hash": packet_hash,
        "decision_asof_ms": decision_asof_ms,
        "decision_asof_utc": _iso(decision_asof_ms),
        "result_identity": OFFICIAL_IDENTITY,
        "contract": contract,
        "official_result": {
            "source_status": "RECONSTRUCTED_OFFICIAL_RESULT",
            "index_name": "btc_usd",
            "currency": "BTC",
            "delivery_date_utc": contract["expiry_date_utc"],
            "delivery_price": delivery_price,
            "delivery_event": event,
            "instrument_metadata": instrument_metadata,
            "all_instrument_metadata": [all_instrument_metadata[name] for name in receipt_names],
            "raw_sources": official_sources,
            "official_rule": OFFICIAL_RULE,
            "receipt_times_do_not_fill_packet_asof": True,
        },
        "accounting": accounting,
        "risk_reference": risk_reference,
        "forbidden_claims": [
            "actual_trade_pnl",
            "execution_decision_recorded",
            "production_risk_provider_admitted",
            "strategy_alpha_or_expected_value",
            "mu_or_M_reconstructed_from_official_result",
        ],
        "gaps": sorted(set(gaps)),
        "validation_counts": {
            "raw_sources": len(official_sources),
            "instrument_metadata": len(instrument_metadata),
            "delivery_price_rows_used": 1,
            "delivery_events_used": 1,
            "llm_calls": 0,
            "provider_calls": 0,
            "network_writes": 0,
            "actual_trade_records": 0,
        },
    }
    return {**body, "result_attachment_hash": _hash(body)}
