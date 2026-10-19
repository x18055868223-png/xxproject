"""Opt-in, read-only Deribit capture for one explicitly selected BTC vertical.

Only public GET requests are possible: one instrument metadata and one order
book request for each supplied leg (a failed book read is retried once). No private endpoint, chain scan,
account access, order placement, or automatic timer is present.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from typing import Any, Callable

if __package__:
    from . import astra_underwriting_v20 as core
    from . import astra_underwriting_real_replay_v20 as fee_math
else:
    import astra_underwriting_v20 as core
    import astra_underwriting_real_replay_v20 as fee_math

BASE_URL = "https://www.deribit.com/api/v2"
PUBLIC_PATHS = frozenset({"public/get_instrument", "public/get_order_book", "public/get_delivery_prices"})
BOOK_ATTEMPTS = 2


def failure_code(exc: BaseException) -> str:
    """A short machine code for why a read failed: the error's own code when it carries
    one (data engine, workbench transport), else the exception type."""
    code = getattr(exc, "code", None)
    return str(code) if isinstance(code, str) and code else type(exc).__name__


def public_transport(path: str, params: dict[str, Any]) -> dict[str, Any]:
    if path not in PUBLIC_PATHS:
        raise ValueError("only audited public instrument and book endpoints are allowed")
    url = BASE_URL + "/" + path + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(request, timeout=8) as response:
        body = json.load(response)
    if not isinstance(body, dict) or body.get("error") or not isinstance(body.get("result"), dict):
        raise ValueError(f"Deribit public {path} returned an invalid result")
    return body["result"]


def delivery_price(expiry_ms: int, *, transport=public_transport, index_name: str = "btc_usd") -> dict[str, Any] | None:
    """The exchange's published delivery (settlement) price for an expiry, or None if not yet published.

    Deribit settles BTC options at 08:00 UTC against that day's delivery price.
    """
    from datetime import datetime, timezone
    day = datetime.fromtimestamp(int(expiry_ms) / 1000, timezone.utc).strftime("%Y-%m-%d")
    result = transport("public/get_delivery_prices", {"index_name": index_name, "count": 10})
    for row in result.get("data") or []:
        if isinstance(row, dict) and row.get("date") == day:
            price = row.get("delivery_price")
            if isinstance(price, (int, float)) and not isinstance(price, bool) and price > 0:
                return {"date": day, "delivery_price_usd": float(price), "index_name": index_name,
                        "source": "deribit public/get_delivery_prices"}
    return None


def capture_candidate(short_instrument: str, long_instrument: str, quantity_btc: float,
                      *, transport: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
                      clock_ms: Callable[[], int] | None = None,
                      allow_partial: bool = False) -> dict[str, Any]:
    """Build evaluate()-compatible input; no fee or physical-risk guess."""
    if not isinstance(short_instrument, str) or not isinstance(long_instrument, str):
        raise ValueError("two full instrument names are required")
    name_pattern = r"BTC-\d{1,2}(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{2}-\d+(?:\.\d+)?-[PC]"
    if not re.fullmatch(name_pattern, short_instrument) or not re.fullmatch(name_pattern, long_instrument):
        raise ValueError("two full Deribit BTC option names are required before any public request")
    if short_instrument == long_instrument:
        raise ValueError("two different instruments are required")
    if isinstance(quantity_btc, bool) or not isinstance(quantity_btc, (int, float)) or quantity_btc <= 0:
        raise ValueError("quantity_btc must be positive")
    fetch = transport or public_transport
    now = clock_ms or (lambda: int(time.time() * 1000))
    metadata: dict[str, dict[str, Any]] = {}
    books: dict[str, dict[str, Any]] = {}
    receipts: dict[str, int] = {}
    metadata_receipts: dict[str, int] = {}
    capture_gaps: list[str] = []
    for leg, name in (("short", short_instrument), ("long", long_instrument)):
        meta = fetch("public/get_instrument", {"instrument_name": name})
        metadata_receipts[leg] = int(now())
        if not isinstance(meta, dict) or meta.get("instrument_name") != name:
            raise ValueError(f"{leg} instrument metadata is missing or mismatched")
        metadata[leg] = meta
    for leg, name in (("short", short_instrument), ("long", long_instrument)):
        failures: list[Exception] = []
        # One immediate retry: a single book read occasionally fails (2026-10-15/16: five
        # one-minute holes in lock tracking, each fine on the next read).
        for _ in range(BOOK_ATTEMPTS):
            try:
                book = fetch("public/get_order_book", {"instrument_name": name, "depth": 5})
                if not isinstance(book, dict) or book.get("instrument_name") != name:
                    raise ValueError(f"{leg} order book is missing or mismatched")
                receipts[leg] = int(now())
                books[leg] = book
                failures = []
                break
            except Exception as exc:
                failures.append(exc)
        if failures:
            if not allow_partial:
                raise failures[-1]
            # No substitute book or zero price. A surviving leg can still carry
            # the same named index; the missing book keeps all quote math closed.
            # The first failure is the cause; a retry can only add a consequence of it.
            books[leg] = {}
            capture_gaps.append(f"{leg}_book_unavailable:{failure_code(failures[0])}")
    valuation_ms = int(now())
    reference_leg = "long" if allow_partial and not books["short"].get("index_price") else "short"
    reference_price = books[reference_leg].get("index_price")
    # The short-leg book supplies the explicitly identified Deribit BTC index.
    # The other leg's index, both exchange timestamps, and both local receipt
    # times stay in the input for independent synchrony/quality checks.
    payload = {"candidate": {"short_instrument": short_instrument,
                          "long_instrument": long_instrument,
                          "quantity_btc": quantity_btc,
                          "exit_policy": "hold_to_expiry"},
            "metadata": metadata,
            "market": {"valuation_ts_ms": valuation_ms,
                       "received_ts_ms": max(receipts.values(), default=valuation_ms),
                       "leg_received_ts_ms": receipts,
                       "reference_price_usd": reference_price,
                       "reference_source": "deribit_index",
                       "short_book": books["short"], "long_book": books["long"]},
            "fee": {"basis": "unknown"}, "origins": ["manual"],
            "data_identity": {"kind": "live_public_capture", "source_sha256": core._hash(
                {"metadata": metadata, "books": books, "receipts": receipts})}}
    payload["metadata_received_ts_ms"] = metadata_receipts
    if allow_partial:
        payload["capture_gaps"] = capture_gaps
        payload["market"]["reference_leg"] = reference_leg
    # The same configured daily-option fee formula is used by the frozen real
    # replay. A stale/unqualified quote or non-daily expiry keeps fees unknown.
    try:
        contract = core.candidate_identity(payload["candidate"], metadata, valuation_ms)
        visible = core.visible_credit(contract, payload["market"], valuation_ms)
        if visible["credit_btc"] is not None:
            fee_detail = fee_math._entry_fee(
                {"short_metadata": metadata["short"], "long_metadata": metadata["long"],
                 "short_name": short_instrument, "long_name": long_instrument},
                {short_instrument: books["short"], long_instrument: books["long"]},
                quantity_btc)
            if fee_detail["total_fee_btc"] < 0:
                raise ValueError("negative configured fee")
            payload["fee"] = {
                "basis": "configured_assumption",
                "assumption_basis": "public_taker_capped_daily_delivery_zero",
                "amount_btc": fee_detail["total_fee_btc"],
                "entry_fee_btc": fee_detail["entry_fee_btc"],
                "delivery_fee_btc": fee_detail["delivery_fee_btc"],
                "covers": ["entry", "delivery"],
                "detail": fee_detail,
            }
    except (KeyError, TypeError, ValueError):
        pass  # Keep the unsupported fee explicitly unknown; never infer account fees.
    return payload
