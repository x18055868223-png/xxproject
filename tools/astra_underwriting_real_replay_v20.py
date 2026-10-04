#!/usr/bin/env python3
"""Build evaluate()-compatible payloads from a frozen real quote snapshot.

This is a historical replay adapter only. It reads an existing local snapshot,
reuses the captured metadata, books, receipts, and timing evidence, and never
queries a network endpoint, fabricates FMZ facts, or supplies a mu reference.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__:
    from . import astra_underwriting_v20 as core
else:  # direct execution from tools/
    import astra_underwriting_v20 as core


SCHEMA = "astra_underwriting_real_replay_payload@2.0.0"
DEFAULT_SOURCE = Path(".artifacts/astra-credit-space-20260914/snapshot/snapshot.json")
FEE_BASIS_CONFIGURED = "configured_assumption"


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path: str | Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_canonical(value) + "\n", encoding="utf-8")


def resolve_snapshot_path(path: str | Path = DEFAULT_SOURCE) -> Path:
    raw = Path(path)
    candidates = [raw]
    if not raw.is_absolute():
        cwd = Path.cwd().resolve()
        candidates.extend(base / raw for base in (cwd, *cwd.parents))
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError(str(path))


def _number(name: str, value: Any, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(f"{name} must be {'positive ' if positive else ''}finite")
    return result


def _candidate(snapshot: Mapping[str, Any], candidate_id: str) -> dict[str, Any]:
    for item in snapshot.get("candidates") or []:
        if isinstance(item, Mapping) and item.get("id") == candidate_id:
            if item.get("status") != "selected_without_viewing_quotes":
                raise ValueError("candidate is not an available same-width historical candidate")
            if not item.get("short_name") or not item.get("long_name"):
                raise ValueError("candidate does not have both legs")
            return deepcopy(dict(item))
    raise KeyError(f"candidate_id not found in snapshot: {candidate_id}")


def _receipt(snapshot: Mapping[str, Any], instrument_name: str) -> dict[str, Any]:
    expected = instrument_name + ".json"
    for item in snapshot.get("receipts") or []:
        if isinstance(item, Mapping) and item.get("file") == expected:
            if item.get("error") is not None:
                raise ValueError(f"receipt has error for {instrument_name}")
            if item.get("http_status") != 200:
                raise ValueError(f"receipt is not HTTP 200 for {instrument_name}")
            return deepcopy(dict(item))
    raise KeyError(f"receipt missing for {instrument_name}")


def _consume_fee(levels: Any, quantity: float, taker_commission: float, *, bid: bool) -> tuple[float, list[dict[str, float]]]:
    clean: list[tuple[float, float]] = []
    for level in levels if isinstance(levels, list) else []:
        if not isinstance(level, (list, tuple)) or len(level) != 2:
            continue
        price = _number("book price", level[0], positive=True)
        amount = _number("book amount", level[1], positive=True)
        clean.append((price, amount))
    clean.sort(key=lambda item: item[0], reverse=bid)
    remaining = quantity
    total_fee = 0.0
    fills: list[dict[str, float]] = []
    for price, amount in clean:
        take = min(amount, remaining)
        if take <= 0:
            continue
        fee_rate = min(taker_commission, 0.125 * price)
        fee = fee_rate * take
        fills.append({"price_btc": price, "amount_btc": take, "fee_rate_btc": fee_rate, "fee_btc": fee})
        total_fee += fee
        remaining -= take
        if remaining <= 1e-12:
            break
    if remaining > 1e-12:
        raise ValueError("insufficient displayed depth for configured fee replay")
    return total_fee, fills


def _entry_fee(candidate: Mapping[str, Any], books: Mapping[str, Mapping[str, Any]],
               quantity_btc: float) -> dict[str, Any]:
    short_meta = candidate["short_metadata"]
    long_meta = candidate["long_metadata"]
    short_fee, short_fills = _consume_fee(
        books[candidate["short_name"]].get("bids"),
        quantity_btc,
        _number("short taker_commission", short_meta.get("taker_commission")),
        bid=True,
    )
    long_fee, long_fills = _consume_fee(
        books[candidate["long_name"]].get("asks"),
        quantity_btc,
        _number("long taker_commission", long_meta.get("taker_commission")),
        bid=False,
    )
    if short_meta.get("settlement_period") != "day" or long_meta.get("settlement_period") != "day":
        raise ValueError("only explicit daily-option zero-delivery-fee replay is supported")
    return {
        "entry_fee_btc": short_fee + long_fee,
        "delivery_fee_btc": 0.0,
        "total_fee_btc": short_fee + long_fee,
        "delivery_fee_rule": "daily_option_expiry_delivery_fee_zero_explicit",
        "legs": {
            "short": {"action": "sell_bid", "fills": short_fills, "fee_btc": short_fee},
            "long": {"action": "buy_ask", "fills": long_fills, "fee_btc": long_fee},
        },
        "formula": "sum(min(taker_commission, 12.5% * option_price) * consumed_quantity) per entry leg; daily delivery fee = 0",
    }


def _parse_iso_ms(value: Any) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int(parsed.timestamp() * 1000)


def build_payload(
    snapshot_path: str | Path,
    candidate_id: str,
    quantity_btc: float,
    *,
    fee_basis: str = FEE_BASIS_CONFIGURED,
    origin: str = "manual",
) -> dict[str, Any]:
    path = resolve_snapshot_path(snapshot_path)
    raw_hash = sha256_file(path)
    snapshot = read_json(path)
    if not isinstance(snapshot, Mapping) or snapshot.get("schema") != "astra_credit_space_snapshot@1.0.0":
        raise ValueError("unsupported credit-space snapshot")
    if origin not in core.ORIGINS:
        raise ValueError("invalid entry origin")
    quantity = _number("quantity_btc", quantity_btc, positive=True)
    candidate = _candidate(snapshot, candidate_id)
    books = snapshot.get("books") if isinstance(snapshot.get("books"), Mapping) else {}
    short_name = str(candidate["short_name"])
    long_name = str(candidate["long_name"])
    if short_name not in books or long_name not in books:
        raise KeyError("selected candidate books are missing")
    short_receipt = _receipt(snapshot, short_name)
    long_receipt = _receipt(snapshot, long_name)
    leg_receipts = {
        "short": int(short_receipt["received_ms"]),
        "long": int(long_receipt["received_ms"]),
    }
    valuation_ms = max(leg_receipts.values())
    captured_ms = _parse_iso_ms(snapshot.get("captured_at_utc"))
    if captured_ms is not None:
        valuation_ms = max(valuation_ms, captured_ms)
    fee_detail: dict[str, Any]
    if fee_basis == "unknown":
        fee = {"basis": "unknown"}
        fee_detail = {"status": "not_configured"}
    elif fee_basis == FEE_BASIS_CONFIGURED:
        fee_detail = _entry_fee(candidate, books, quantity)
        fee = {
            "basis": FEE_BASIS_CONFIGURED,
            "assumption_basis": FEE_BASIS_CONFIGURED,
            "amount_btc": fee_detail["total_fee_btc"],
            "entry_fee_btc": fee_detail["entry_fee_btc"],
            "delivery_fee_btc": fee_detail["delivery_fee_btc"],
            "covers": ["entry", "delivery"],
            "detail": fee_detail,
        }
    else:
        raise ValueError("unsupported fee_basis")

    payload = {
        "schema": SCHEMA,
        "data_identity": {"kind": "historical_replay", "source_sha256": raw_hash},
        "origins": [origin],
        "candidate": {
            "source_candidate_id": candidate_id,
            "short_instrument": short_name,
            "long_instrument": long_name,
            "quantity_btc": quantity,
            "exit_policy": "hold_to_expiry",
        },
        "metadata": {
            "short": deepcopy(candidate["short_metadata"]),
            "long": deepcopy(candidate["long_metadata"]),
        },
        "market": {
            "valuation_ts_ms": valuation_ms,
            "valuation_basis": "max_selected_leg_receipt_and_snapshot_capture_time",
            "received_ts_ms": max(leg_receipts.values()),
            "leg_received_ts_ms": leg_receipts,
            "reference_price_usd": _number("index_price", snapshot.get("index_price"), positive=True),
            "reference_source": "deribit_index",
            "short_book": deepcopy(books[short_name]),
            "long_book": deepcopy(books[long_name]),
        },
        "fee": fee,
        "fmz_fact_context": {
            "schema": "astra_fmz_fact_context@2.0.0",
            "status": "missing",
            "source_hash": None,
            "snapshot_ts_ms": None,
            "market_facts": {},
            "gap_reasons": ["historical_replay_does_not_fabricate_fmz_fact_context"],
        },
        "replay_identity": {
            "schema": SCHEMA,
            "source_kind": "frozen_real_quote_snapshot",
            "source_snapshot_path": str(path),
            "source_snapshot_sha256": raw_hash,
            "source_snapshot_schema": snapshot.get("schema"),
            "source_candidate_id": candidate_id,
            "quantity_btc": quantity,
            "fee_basis": fee_basis,
            "fee_detail": fee_detail,
            "exchange_clock_ms": snapshot.get("exchange_clock_ms"),
            "captured_at_utc": snapshot.get("captured_at_utc"),
            "valuation_ts_ms": valuation_ms,
            "valuation_basis": "max_selected_leg_receipt_and_snapshot_capture_time",
            "limits": [
                "historical_public_books_only",
                "sequential_leg_books_not_combo_execution",
                "no_fmz_fact_fabrication",
                "no_mu_reference_fabrication",
                "no_network_request",
            ],
        },
    }
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--quantity-btc", type=float, required=True)
    parser.add_argument("--fee-basis", choices=[FEE_BASIS_CONFIGURED, "unknown"], default=FEE_BASIS_CONFIGURED)
    parser.add_argument("--origin", choices=sorted(core.ORIGINS), default="manual")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_payload(
        args.snapshot,
        args.candidate_id,
        args.quantity_btc,
        fee_basis=args.fee_basis,
        origin=args.origin,
    )
    if args.output:
        write_json(args.output, payload)
    print(_canonical(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
