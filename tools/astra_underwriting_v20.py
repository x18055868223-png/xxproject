"""Read-only, contract-first Astra underwriting snapshot.

This module does not choose legs, trade, infer a physical loss distribution, or
call an LLM. It evaluates one explicitly selected BTC inverse vertical using
time-stamped public-book inputs and, only when independently supplied and
bound to the same candidate, a frozen expected-payout reference.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping

if __package__:
    from .astra_commercial_math_v17 import inverse_spread_payout, required_net_credit, static_cashflow
else:  # direct CLI execution from tools/
    from astra_commercial_math_v17 import inverse_spread_payout, required_net_credit, static_cashflow

SCHEMA = "astra_underwriting_snapshot@2.0.0"
ORIGINS = {"manual", "fixed_round", "natural_nr", "state_refresh"}
DATA_KINDS = {"live_public_capture", "historical_replay", "synthetic_fixture", "manual_input"}
FEE_COVERAGE = {"entry", "delivery", "early_exit", "slippage", "hedge"}
REQUIRED_HOLD_COVERAGE = {"entry", "delivery"}
MAX_BOOK_AGE_MS = 5_000
MAX_BOOK_SKEW_MS = 2_000
MAX_FMZ_AGE_MS = 300_000


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _number(name: str, value: Any, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    try:
        number = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError(f"{name} must be {'positive and ' if positive else ''}finite")
    return number


def _integer(name: str, value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    number = _number(name, value)
    if not number.is_integer():
        raise ValueError(f"{name} must be an integer")
    return int(number)


def _meta(meta: Mapping[str, Any], name: str, valuation_ms: int) -> dict[str, Any]:
    if not isinstance(meta, Mapping) or meta.get("instrument_name") != name:
        raise ValueError("instrument metadata must match the full leg name")
    if meta.get("kind") != "option" or meta.get("base_currency") != "BTC":
        raise ValueError("only BTC options are supported")
    if meta.get("settlement_currency") != "BTC" or meta.get("quote_currency") != "BTC":
        raise ValueError("only BTC-settled/BTC-quoted inverse options are supported")
    if meta.get("counter_currency") != "USD" or meta.get("price_index") != "btc_usd":
        raise ValueError("BTC/USD option metadata is required")
    if meta.get("option_type") not in {"put", "call"}:
        raise ValueError("metadata option type is missing")
    if meta.get("instrument_type") not in (None, "reversed"):
        raise ValueError("only inverse option instruments are supported")
    if meta.get("state") != "open":
        raise ValueError("instrument is not open")
    expiry = _integer("expiration_timestamp", meta.get("expiration_timestamp"))
    name_match = re.fullmatch(r"BTC-(\d{1,2})([A-Z]{3})(\d{2})-(\d+(?:\.\d+)?)-([PC])", name)
    if name_match is None:
        raise ValueError("invalid full Deribit BTC option name")
    months = {name: index for index, name in enumerate(
        ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}
    if name_match.group(2) not in months:
        raise ValueError("invalid instrument expiry month")
    named_expiry = datetime(2000 + int(name_match.group(3)), months[name_match.group(2)],
                            int(name_match.group(1)), 8, tzinfo=timezone.utc)
    if int(named_expiry.timestamp() * 1000) != expiry:
        raise ValueError("instrument name and metadata expiry disagree")
    if (name_match.group(5) == "P") != (meta["option_type"] == "put"):
        raise ValueError("instrument name and metadata option type disagree")
    if not math.isclose(float(name_match.group(4)), _number("strike", meta.get("strike"), positive=True),
                        rel_tol=0, abs_tol=1e-9):
        raise ValueError("instrument name and metadata strike disagree")
    if expiry <= valuation_ms:
        raise ValueError("instrument is expired")
    multiplier = _number("contract_size", meta.get("contract_size"), positive=True)
    if not math.isclose(multiplier, 1.0, rel_tol=0, abs_tol=1e-12):
        raise ValueError("unsupported contract multiplier")
    return {
        "instrument_name": name,
        "strike_usd": _number("strike", meta.get("strike"), positive=True),
        "option_type": meta["option_type"],
        "expiry_ms": expiry,
        "contract_size": multiplier,
        "quantity_unit": "BTC",
    }


def candidate_identity(candidate: Mapping[str, Any], metadata: Mapping[str, Any], valuation_ms: int) -> dict[str, Any]:
    """Validate an explicit short/long candidate; no chain search or rank."""
    if not isinstance(candidate, Mapping) or not isinstance(metadata, Mapping):
        raise ValueError("candidate and metadata are required")
    short_name = str(candidate.get("short_instrument") or "")
    long_name = str(candidate.get("long_instrument") or "")
    if not short_name or not long_name or short_name == long_name:
        raise ValueError("two different full leg names are required")
    short = _meta(metadata.get("short"), short_name, valuation_ms)
    long = _meta(metadata.get("long"), long_name, valuation_ms)
    if short["option_type"] != long["option_type"] or short["expiry_ms"] != long["expiry_ms"]:
        raise ValueError("legs must share option type and expiry")
    side = short["option_type"]
    ks, kl = short["strike_usd"], long["strike_usd"]
    if (side == "put" and ks <= kl) or (side == "call" and ks >= kl):
        raise ValueError("strikes do not form a credit vertical")
    quantity = _number("quantity_btc", candidate.get("quantity_btc"), positive=True)
    min_trade = max(_number("short min_trade_amount", metadata["short"].get("min_trade_amount"), positive=True),
                    _number("long min_trade_amount", metadata["long"].get("min_trade_amount"), positive=True))
    if quantity + 1e-12 < min_trade:
        raise ValueError("quantity is below an instrument minimum")
    if candidate.get("exit_policy") != "hold_to_expiry":
        raise ValueError("only explicit hold_to_expiry accounting is supported")
    key = {"venue": "Deribit", "underlying": "BTC", "settlement_currency": "BTC",
           "short_instrument": short_name, "long_instrument": long_name,
           "side": side, "quantity_btc": quantity, "expiry_ms": short["expiry_ms"]}
    return {**key, "candidate_id": _hash(key)[:24], "short_strike_usd": ks,
            "long_strike_usd": kl, "width_usd": abs(ks - kl),
            "contract_size": 1.0, "exit_policy": "hold_to_expiry",
            "short": short, "long": long}


def _consume_depth(levels: Any, quantity: float, *, bid: bool) -> tuple[float | None, float]:
    clean: list[tuple[float, float]] = []
    for level in levels if isinstance(levels, list) else []:
        if not isinstance(level, (list, tuple)) or len(level) != 2:
            continue
        try:
            price = _number("book price", level[0], positive=True)
            amount = _number("book amount", level[1], positive=True)
        except ValueError:
            continue
        clean.append((price, amount))
    clean.sort(key=lambda item: item[0], reverse=bid)
    remaining, total, available = quantity, 0.0, sum(amount for _, amount in clean)
    for price, amount in clean:
        take = min(amount, remaining)
        total += take * price
        remaining -= take
        if remaining <= 1e-12:
            break
    return (total if remaining <= 1e-12 else None), available


def visible_credit(contract: Mapping[str, Any], market: Mapping[str, Any], valuation_ms: int) -> dict[str, Any]:
    """Target-size short bid minus long ask; never substitute marks or last."""
    reasons: list[str] = []
    try:
        received_ms = _integer("received_ts_ms", market.get("received_ts_ms"))
    except ValueError:
        received_ms = None
        reasons.append("local_receipt_missing")
    if received_ms is not None and (received_ms > valuation_ms or valuation_ms - received_ms > MAX_BOOK_AGE_MS):
        reasons.append("local_receipt_outside_freshness")
    books = {}
    leg_receipts = market.get("leg_received_ts_ms") if isinstance(market.get("leg_received_ts_ms"), Mapping) else {}
    for leg in ("short", "long"):
        book = market.get(f"{leg}_book")
        if not isinstance(book, Mapping):
            reasons.append(f"{leg}_book_missing")
            continue
        if book.get("instrument_name") != contract[f"{leg}_instrument"]:
            reasons.append(f"{leg}_book_instrument_mismatch")
        if book.get("state") != "open":
            reasons.append(f"{leg}_book_not_open")
        try:
            timestamp = _integer(f"{leg} book timestamp", book.get("timestamp"))
        except ValueError:
            reasons.append(f"{leg}_book_timestamp_missing")
            continue
        leg_received = leg_receipts.get(leg, received_ms)
        try:
            leg_received = _integer(f"{leg} receipt timestamp", leg_received)
        except ValueError:
            leg_received = None
            reasons.append(f"{leg}_receipt_missing")
        if (leg_received is not None and timestamp > leg_received) or timestamp > valuation_ms or valuation_ms - timestamp > MAX_BOOK_AGE_MS:
            reasons.append(f"{leg}_book_stale_or_future")
        books[leg] = (book, timestamp)
    if len(books) == 2 and abs(books["short"][1] - books["long"][1]) > MAX_BOOK_SKEW_MS:
        reasons.append("leg_book_time_skew")
    if market.get("reference_source") == "deribit_index" and len(books) == 2:
        reference = market.get("reference_price_usd")
        try:
            reference = _number("reference_price_usd", reference, positive=True)
            for leg in ("short", "long"):
                index = _number(f"{leg} index_price", books[leg][0].get("index_price"), positive=True)
                if abs(index - reference) / reference > 0.001:
                    reasons.append("leg_index_mismatch")
        except ValueError:
            reasons.append("leg_index_missing")
    quantity = contract["quantity_btc"]
    leg_amounts: dict[str, float | None] = {}
    available_depth: dict[str, float] = {}
    for leg, direction, field in (("short", True, "bids"), ("long", False, "asks")):
        if leg not in books:
            continue
        book = books[leg][0]
        own, available = _consume_depth(book.get(field), quantity, bid=direction)
        leg_amounts[leg] = own
        available_depth[leg] = available
        if own is None:
            reasons.append(f"{leg}_depth_insufficient")
        best_bid, _ = _consume_depth(book.get("bids"), min(quantity, 1e-9), bid=True)
        best_ask, _ = _consume_depth(book.get("asks"), min(quantity, 1e-9), bid=False)
        if best_bid is not None and best_ask is not None and best_bid > best_ask + 1e-12:
            reasons.append(f"{leg}_crossed_book")
    if reasons:
        credit = None
    else:
        credit = leg_amounts["short"] - leg_amounts["long"]
    return {"kind": "sequential_leg_book_estimate", "credit_btc": credit,
            "source_book_hash": _hash({"short": market.get("short_book"), "long": market.get("long_book")}),
            "short_sale_btc": leg_amounts.get("short") if not reasons else None,
            "long_purchase_btc": leg_amounts.get("long") if not reasons else None,
            "available_depth_btc": available_depth,
            "exchange_times_ms": {key: item[1] for key, item in books.items()},
            "leg_received_ts_ms": dict(leg_receipts),
            "received_ts_ms": received_ms, "valuation_ts_ms": valuation_ms,
            "gap_reasons": sorted(set(reasons)), "executable_or_filled": False}


def breakeven_roots(contract: Mapping[str, Any], net_credit_btc: float | None) -> list[float] | None:
    if net_credit_btc is None:
        return None
    n = _number("net credit", net_credit_btc)
    if n <= 0:
        return []
    q, ks, kl = contract["quantity_btc"], contract["short_strike_usd"], contract["long_strike_usd"]
    width = contract["width_usd"]
    if contract["side"] == "put":
        middle = q * ks / (q + n)
        return [middle if middle >= kl - 1e-9 else q * width / n]
    peak = q * width / kl
    if n > peak + 1e-12:
        return []
    if math.isclose(n, peak, rel_tol=0, abs_tol=1e-12):
        return [kl]
    return [q * ks / (q - n), q * width / n]


def _risk_reference(risk: Any, contract: Mapping[str, Any], market: Mapping[str, Any],
                    valuation_ms: int, reference_price: float,
                    fact_context: Mapping[str, Any], data_kind: str) -> dict[str, Any]:
    if not isinstance(risk, Mapping):
        return {"status": "missing", "mu_btc": None, "gap_reasons": ["frozen_risk_reference_missing"]}
    raw = dict(risk)
    reasons = []
    if raw.get("status") != "supported" or raw.get("support_verified") is not True:
        reasons.append("risk_support_not_verified")
    if raw.get("source_kind") not in {"synthetic_fixture", "frozen_model", "frozen_geometry"}:
        reasons.append("risk_source_kind_unsupported")
    if raw.get("source_kind") == "synthetic_fixture" and data_kind != "synthetic_fixture":
        reasons.append("synthetic_risk_requires_synthetic_data_identity")
    if raw.get("source_kind") != "synthetic_fixture" and (
        fact_context.get("status") != "current"
        or not isinstance(raw.get("source_fact_hash"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", raw["source_fact_hash"])
        or raw.get("source_fact_hash") != fact_context.get("source_hash")
    ):
        reasons.append("risk_fmz_fact_binding_missing_or_stale")
    if raw.get("candidate_id") != contract["candidate_id"]:
        reasons.append("risk_candidate_mismatch")
    if raw.get("unit") != "BTC":
        reasons.append("risk_unit_mismatch")
    if raw.get("model_id") in (None, "") or not isinstance(raw.get("model_hash"), str) or not re.fullmatch(r"[0-9a-f]{64}", raw["model_hash"]):
        reasons.append("risk_model_identity_missing")
    try:
        risk_time = _integer("risk input timestamp", raw.get("input_ts_ms"))
        if risk_time != valuation_ms:
            reasons.append("risk_input_time_mismatch")
        feature_end = _integer("risk feature end", raw.get("feature_end_ts_ms"))
        if feature_end > valuation_ms:
            reasons.append("risk_uses_future_input")
        if not math.isclose(_number("risk reference price", raw.get("reference_price_usd"), positive=True),
                            reference_price, rel_tol=0, abs_tol=1e-8):
            reasons.append("risk_reference_price_mismatch")
        if not math.isclose(_number("risk quantity", raw.get("quantity_btc"), positive=True),
                            contract["quantity_btc"], rel_tol=0, abs_tol=1e-12):
            reasons.append("risk_quantity_mismatch")
        if not math.isclose(_number("risk horizon hours", raw.get("horizon_hours"), positive=True),
                            (contract["expiry_ms"] - valuation_ms) / 3_600_000, rel_tol=0, abs_tol=1e-8):
            reasons.append("risk_horizon_mismatch")
        mu = _number("mu_btc", raw.get("mu_btc"))
        if mu < 0:
            reasons.append("risk_mu_negative")
    except ValueError:
        reasons.append("risk_required_field_invalid")
        mu = None
    if not raw.get("calibration_id") or not raw.get("support_scope"):
        reasons.append("risk_calibration_or_scope_missing")
    if not raw.get("input_row_hash") or not raw.get("normalization_basis"):
        reasons.append("risk_input_or_scale_identity_missing")
    if not raw.get("training_cutoff") or not raw.get("label_known_cutoff"):
        reasons.append("risk_training_identity_missing")
    return {"status": "supported" if not reasons else "unusable", "mu_btc": mu if not reasons else None,
            "model_id": raw.get("model_id"), "model_hash": raw.get("model_hash"),
            "calibration_id": raw.get("calibration_id"), "support_scope": raw.get("support_scope"),
            "source_kind": raw.get("source_kind"), "input_ts_ms": raw.get("input_ts_ms"),
            "feature_end_ts_ms": raw.get("feature_end_ts_ms"),
            "input_row_hash": raw.get("input_row_hash"),
            "source_fact_hash": raw.get("source_fact_hash"),
            "normalization_basis": raw.get("normalization_basis"),
            "training_cutoff": raw.get("training_cutoff"),
            "label_known_cutoff": raw.get("label_known_cutoff"),
            "gap_reasons": sorted(set(reasons))}


def evaluate(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Freeze one read-only snapshot. Unsupported economics stay null."""
    market = payload.get("market")
    if not isinstance(market, Mapping):
        raise ValueError("market snapshot is required")
    valuation_ms = _integer("valuation_ts_ms", market.get("valuation_ts_ms"))
    contract = candidate_identity(payload.get("candidate"), payload.get("metadata"), valuation_ms)
    origins = sorted(set(payload.get("origins") or ["manual"]))
    if not origins or set(origins) - ORIGINS:
        raise ValueError("invalid entry origin")
    reference_price = _number("reference_price_usd", market.get("reference_price_usd"), positive=True)
    reference_source = str(market.get("reference_source") or "")
    if reference_source not in {"deribit_index", "external_index_snapshot", "synthetic_fixture"}:
        raise ValueError("reference price source must be explicit")
    provenance = payload.get("data_identity")
    if provenance is None:
        provenance = {"kind": "synthetic_fixture" if reference_source == "synthetic_fixture" else "manual_input"}
    if not isinstance(provenance, Mapping) or provenance.get("kind") not in DATA_KINDS:
        raise ValueError("data identity kind is required")
    source_manifest = provenance.get("source_manifest")
    provenance = {"kind": provenance["kind"], "source_sha256": provenance.get("source_sha256")}
    if source_manifest is not None:
        if (not isinstance(source_manifest, Mapping)
                or source_manifest.get("cutoff_at_ms") != valuation_ms
                or source_manifest.get("candidate_id") != contract["candidate_id"]
                or source_manifest.get("manifest_hash") != _hash({
                    key: value for key, value in source_manifest.items() if key != "manifest_hash"})):
            raise ValueError("source manifest must bind the exact candidate and valuation")
        provenance["source_manifest"] = dict(source_manifest)
    if (provenance["kind"] == "synthetic_fixture") != (reference_source == "synthetic_fixture"):
        raise ValueError("synthetic fixture identity and reference must agree")
    if provenance["kind"] == "historical_replay" and provenance["source_sha256"] is None:
        raise ValueError("historical replay requires a source hash")
    if provenance["source_sha256"] is not None and not re.fullmatch(r"[0-9a-f]{64}", str(provenance["source_sha256"])):
        raise ValueError("data source hash must be sha256")
    supplied_facts = payload.get("fmz_fact_context")
    if supplied_facts is None:
        fact_context = {"schema": "astra_fmz_fact_context@2.0.0", "status": "missing",
                        "source_hash": None, "snapshot_ts_ms": None, "market_facts": {},
                        "gap_reasons": ["fmz_fact_context_missing"]}
    elif (not isinstance(supplied_facts, Mapping)
          or supplied_facts.get("schema") != "astra_fmz_fact_context@2.0.0"
          or supplied_facts.get("status") not in {"current", "background_only", "missing"}):
        raise ValueError("invalid FMZ fact context")
    else:
        fact_context = dict(supplied_facts)
        fact_time = fact_context.get("snapshot_ts_ms")
        if fact_time is not None and _integer("FMZ snapshot time", fact_time) > valuation_ms:
            raise ValueError("future FMZ facts cannot be attached")
        if fact_context.get("status") == "current":
            if (fact_context.get("underlying") != "BTC"
                    or not isinstance(fact_context.get("source_hash"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", fact_context["source_hash"])):
                raise ValueError("current FMZ facts require BTC identity and a sha256 source hash")
            try:
                observed = _integer("FMZ price observed time", fact_context.get("price_observed_ms"))
                snapshot_time = _integer("FMZ snapshot time", fact_context.get("snapshot_ts_ms"))
                bound_valuation = _integer("FMZ valuation time", fact_context.get("valuation_ts_ms"))
                _number("FMZ current price", fact_context.get("current_price_usd"), positive=True)
            except ValueError as exc:
                raise ValueError("current FMZ facts need observed price and bound clocks") from exc
            if (bound_valuation != valuation_ms
                    or not 0 <= valuation_ms - snapshot_time <= MAX_FMZ_AGE_MS
                    or not 0 <= valuation_ms - observed <= MAX_FMZ_AGE_MS
                    or fact_context.get("runtime_mode") != "live_public_read_only"
                    or not fact_context.get("current_price_source")):
                raise ValueError("current FMZ facts are stale or not live read-only")
    quote = visible_credit(contract, market, valuation_ms)
    fee = payload.get("fee")
    fee_reasons = []
    fee_btc = None
    fee_basis = "unknown"
    fee_covers: list[str] = []
    entry_fee_btc = None
    delivery_fee_btc = None
    if isinstance(fee, Mapping):
        fee_basis = str(fee.get("basis") or "unknown")
        if fee_basis not in {"actual", "configured_assumption", "unknown"}:
            raise ValueError("unsupported fee basis")
        if fee_basis != "unknown":
            fee_btc = _number("fee amount_btc", fee.get("amount_btc"))
            if fee_btc < 0:
                raise ValueError("fee amount_btc must be nonnegative")
            covers = fee.get("covers")
            if not isinstance(covers, list) or not all(isinstance(item, str) for item in covers) or set(covers) - FEE_COVERAGE:
                fee_reasons.append("fee_coverage_invalid")
                fee_btc = None
            else:
                fee_covers = sorted(set(covers))
                if not REQUIRED_HOLD_COVERAGE.issubset(fee_covers):
                    fee_reasons.append("fee_coverage_incomplete")
                    fee_btc = None
            if fee.get("entry_fee_btc") is not None or fee.get("delivery_fee_btc") is not None:
                try:
                    entry_fee_btc = _number("entry_fee_btc", fee.get("entry_fee_btc"))
                    delivery_fee_btc = _number("delivery_fee_btc", fee.get("delivery_fee_btc"))
                    if entry_fee_btc < 0 or delivery_fee_btc < 0 or not math.isclose(
                        entry_fee_btc + delivery_fee_btc, _number("fee amount_btc", fee.get("amount_btc")),
                        rel_tol=0, abs_tol=1e-10):
                        raise ValueError("fee components disagree with total")
                except ValueError:
                    fee_reasons.append("fee_components_invalid")
                    fee_btc = None
                    entry_fee_btc = delivery_fee_btc = None
    if fee_btc is None:
        fee_reasons.append("fee_unknown")
    risk = _risk_reference(payload.get("risk"), contract, market, valuation_ms,
                           reference_price, fact_context, provenance["kind"])
    credit = quote["credit_btc"]
    short_premium = quote["short_sale_btc"]
    long_premium = quote["long_purchase_btc"]
    net_entry_credit = (credit - entry_fee_btc
                        if credit is not None and entry_fee_btc is not None else None)
    budget = credit - fee_btc if credit is not None and fee_btc is not None else None
    usd_at_spot = lambda amount: amount * reference_price if amount is not None else None
    mu = risk["mu_btc"]
    minimum_credit = required_net_credit(mu, fee_btc)
    margin = credit - minimum_credit if credit is not None and minimum_credit is not None else None
    roots = breakeven_roots(contract, budget)
    width = contract["width_usd"]
    ks, kl = contract["short_strike_usd"], contract["long_strike_usd"]
    settlement_points = ([reference_price, ks, (ks + kl) / 2, kl, kl / 2]
                         if contract["side"] == "put" else
                         [reference_price, ks, (ks + kl) / 2, kl, kl * 2])
    payout_table = []
    for price in dict.fromkeys(settlement_points):
        payout = inverse_spread_payout(contract["side"], ks, kl, price, contract["quantity_btc"])
        payout_table.append({"settlement_usd": price, "payout_btc": payout,
                             **static_cashflow(credit, payout, fee_btc)})
    gap_reasons = sorted(set(quote["gap_reasons"] + fee_reasons + risk["gap_reasons"]
                             + list(fact_context.get("gap_reasons") or [])))
    status = ("expired_or_stale" if valuation_ms >= contract["expiry_ms"] or
              any("stale" in reason or "future" in reason or "time_skew" in reason
                  for reason in quote["gap_reasons"]) else
              "information_gap" if budget is None or mu is None else
              "reference_insufficient" if margin < 0 else "reviewable")
    snapshot_body = {"contract": contract, "fact_context": fact_context,
                     "data_identity": provenance,
                     "market": {"valuation_ts_ms": valuation_ms, "reference_price_usd": reference_price,
                                "reference_source": reference_source, "quote": quote},
                     "fee": {"amount_btc": fee_btc, "basis": fee_basis,
                             "entry_fee_btc": entry_fee_btc,
                             "delivery_fee_btc": delivery_fee_btc,
                             "assumption_basis": fee.get("assumption_basis") if isinstance(fee, Mapping) else None,
                             "covers": fee_covers,
                             "gap_reasons": fee_reasons},
                     "risk": risk, "economics": {"visible_credit_btc": credit,
                         "valuation_spot_usd": reference_price,
                         "short_premium_btc": short_premium,
                         "short_premium_usd_at_spot": usd_at_spot(short_premium),
                         "long_premium_btc": long_premium,
                         "long_premium_usd_at_spot": usd_at_spot(long_premium),
                         "premium_spread_btc": credit,
                         "premium_spread_usd_at_spot": usd_at_spot(credit),
                         "theoretical_two_leg_fee_btc": entry_fee_btc,
                         "theoretical_two_leg_fee_usd_at_spot": usd_at_spot(entry_fee_btc),
                         "delivery_fee_usd_at_spot": usd_at_spot(delivery_fee_btc),
                         "total_hold_fee_usd_at_spot": usd_at_spot(fee_btc),
                         "net_credit_after_entry_fee_btc": net_entry_credit,
                         "net_credit_after_entry_fee_usd_at_spot": usd_at_spot(net_entry_credit),
                         "net_credit_after_fee_btc": budget,
                         "net_credit_after_fee_usd_at_spot": usd_at_spot(budget),
                         "loss_budget_btc": budget, "mu_ref_btc": mu,
                         "minimum_reference_credit_btc": minimum_credit,
                         "reference_margin_btc": margin,
                         "error_room_btc": max(margin, 0) if margin is not None else None},
                     "liability": {"starts_at_usd": ks, "protection_at_usd": kl,
                         "usd_intrinsic_cap": contract["quantity_btc"] * width,
                         "btc_reference_scale_at_spot": contract["quantity_btc"] * width / reference_price,
                         "btc_contract_cap": (None if contract["side"] == "put" else
                                              contract["quantity_btc"] * width / kl),
                         "breakeven_usd": roots,
                         "signed_distance_to_short_pct": (ks / reference_price - 1) * 100,
                         "signed_distance_to_long_pct": (kl / reference_price - 1) * 100,
                         "payout_table": payout_table,
                         "account_margin_btc": None},
                     "status": status, "gap_reasons": gap_reasons,
                     "execution_allowed": False}
    snapshot_id = _hash(snapshot_body)
    return {"schema": SCHEMA, "snapshot_id": snapshot_id,
            "candidate_id": contract["candidate_id"], "origins": origins,
            "manual_decision": "not_made",
            "llm_review": {"status": "not_requested", "reviewed_snapshot_id": None},
            **snapshot_body}


def attach_btc_map(snapshot: Mapping[str, Any], btc_map: Mapping[str, Any]) -> dict[str, Any]:
    """Bind an optional read-only MAP without recalculating the original account."""
    llm_applicability_packet(snapshot)
    _validate_btc_map(snapshot, btc_map)
    out = deepcopy(dict(snapshot))
    out["data_identity"]["btc_map"] = deepcopy(dict(btc_map))
    out["snapshot_id"] = _hash({key: out[key] for key in (
        "contract", "fact_context", "data_identity", "market", "fee", "risk", "economics",
        "liability", "status", "gap_reasons", "execution_allowed")})
    return out


def _validate_btc_map(snapshot: Mapping[str, Any], btc_map: Mapping[str, Any]) -> None:
    policy = btc_map.get("policy_link") or {}
    if (btc_map.get("schema") != "btc_map@1"
            or btc_map.get("cutoff_at_ms") != snapshot["market"]["valuation_ts_ms"]
            or policy.get("candidate_id") != snapshot["candidate_id"]
            or not btc_map.get("map_id")):
        raise ValueError("MAP must bind the exact candidate and valuation cutoff")
    if btc_map["map_id"] != _hash({k: v for k, v in btc_map.items() if k != "map_id"}):
        raise ValueError("MAP content hash does not match its freeze")
    manifest = btc_map.get("source_manifest")
    if (not isinstance(manifest, Mapping)
            or manifest.get("manifest_hash") != _hash({k: v for k, v in manifest.items() if k != "manifest_hash"})
            or manifest.get("cutoff_at_ms") != btc_map["cutoff_at_ms"]
            or manifest.get("candidate_id") != snapshot["candidate_id"]):
        raise ValueError("MAP source manifest does not bind the exact candidate and cutoff")
    if (not isinstance(btc_map.get("background"), Mapping)
            or not isinstance(btc_map.get("reference_pool"), list)
            or not isinstance(policy.get("region_liability_rows"), list)):
        raise ValueError("MAP structure is incomplete")
    original = {key: deepcopy(snapshot[key]) for key in (
        "contract", "fact_context", "data_identity", "market", "fee", "risk", "economics",
        "liability", "status", "gap_reasons", "execution_allowed")}
    original["data_identity"].pop("btc_map", None)
    if policy.get("valuation_version") != _hash(original):
        raise ValueError("MAP policy valuation version does not match the original account")
    contract = snapshot["contract"]
    if policy.get("contract") != {key: contract[key] for key in (
            "side", "short_strike_usd", "long_strike_usd", "quantity_btc", "expiry_ms")}:
        raise ValueError("MAP policy contract changed")


def llm_applicability_packet(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Version-bind a one-review causal-applicability input; no HTTP or approval."""
    if snapshot.get("schema") != SCHEMA or snapshot.get("snapshot_id") != _hash({
        key: snapshot[key] for key in ("contract", "fact_context", "data_identity", "market", "fee", "risk", "economics",
                                 "liability", "status", "gap_reasons", "execution_allowed")
    }):
        raise ValueError("underwriting snapshot has changed since freeze")
    if (snapshot.get("data_identity") or {}).get("btc_map"):
        _validate_btc_map(snapshot, snapshot["data_identity"]["btc_map"])
    return {"schema": "astra_underwriting_llm_packet@2.0.0", "snapshot_id": snapshot["snapshot_id"],
            "candidate_id": snapshot["candidate_id"], "contract": snapshot["contract"],
            "data_identity": snapshot["data_identity"],
            "fmz_fact_context": snapshot["fact_context"],
            "market": snapshot["market"], "fee": snapshot["fee"], "risk": snapshot["risk"],
            "economics": snapshot["economics"], "gap_reasons": snapshot["gap_reasons"],
            "review_task": "审查事实来源、因果适用性、同源重复、数据缺口和反证；不得改变数值、生成概率或授予交易许可。",
            "execution_allowed": False}
