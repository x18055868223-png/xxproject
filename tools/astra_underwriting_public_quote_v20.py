"""Opt-in, read-only Deribit capture for one explicitly selected BTC vertical.

Only public GET requests are possible: one instrument metadata and one order
book request for each supplied leg (a failed book read is retried once; a pair
whose books are already out of step when they land is read once more). No private endpoint, chain scan,
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


def _read_book_pair(fetch, now, names, allow_partial: bool):
    """One read of each leg's book, back to back, each with one immediate retry on failure.

    Returns (books, receipts, capture_gaps, wire_requests). The receipt is taken as soon as
    that leg's response arrives; nothing else runs between the two legs.
    """
    books: dict[str, dict[str, Any]] = {}
    receipts: dict[str, int] = {}
    capture_gaps: list[str] = []
    wire = 0
    for leg, name in names:
        failures: list[Exception] = []
        # One immediate retry: a single book read occasionally fails (2026-10-15/16: five
        # one-minute holes in lock tracking, each fine on the next read).
        for _ in range(BOOK_ATTEMPTS):
            wire += 1
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
    return books, receipts, capture_gaps, wire


def _pair_sync_reasons(books: dict[str, Any], receipts: dict[str, int], now_ms: int) -> list[str]:
    """The freshness and synchrony checks of core.visible_credit, applied to a pair as it lands."""
    reasons: list[str] = []
    times: dict[str, int] = {}
    for leg in ("short", "long"):
        stamp = (books.get(leg) or {}).get("timestamp")
        if isinstance(stamp, bool) or not isinstance(stamp, int):
            continue
        times[leg] = stamp
        received = receipts.get(leg)
        if (received is not None and stamp > received) or stamp > now_ms or now_ms - stamp > core.MAX_BOOK_AGE_MS:
            reasons.append(f"{leg}_book_stale_or_future")
    if len(times) == 2 and abs(times["short"] - times["long"]) > core.MAX_BOOK_SKEW_MS:
        reasons.append("leg_book_time_skew")
    return reasons


FEE_FROM_BOOKS = "public_taker_capped_daily_delivery_zero"
FEE_FROM_MARKS = "public_taker_capped_daily_delivery_zero_at_mark"


def configured_fee(metadata: dict[str, Any], books: dict[str, Any], short_instrument: str,
                   long_instrument: str, quantity_btc: float) -> dict[str, Any]:
    """Configured daily-option fee: per entry leg min(taker_commission, 12.5% x price) x qty, delivery 0.

    Priced on the displayed depth the entry would consume; when a book cannot fill the
    quantity, on each leg's mark price instead (said so in assumption_basis). Raises
    ValueError when neither is available or the expiry is not daily.
    """
    candidate = {"short_metadata": metadata["short"], "long_metadata": metadata["long"],
                 "short_name": short_instrument, "long_name": long_instrument}
    if not books.get("short") or not books.get("long"):
        raise ValueError("both leg books are required for the configured fee")
    try:
        detail = fee_math._entry_fee(candidate, {short_instrument: books["short"],
                                                 long_instrument: books["long"]}, quantity_btc)
        basis = FEE_FROM_BOOKS
    except ValueError:
        detail = _entry_fee_at_mark(metadata, books, quantity_btc)
        basis = FEE_FROM_MARKS
    if detail["total_fee_btc"] < 0:
        raise ValueError("negative configured fee")
    return {"basis": "configured_assumption", "assumption_basis": basis,
            "amount_btc": detail["total_fee_btc"], "entry_fee_btc": detail["entry_fee_btc"],
            "delivery_fee_btc": detail["delivery_fee_btc"], "covers": ["entry", "delivery"],
            "detail": detail}


def _entry_fee_at_mark(metadata: dict[str, Any], books: dict[str, Any], quantity_btc: float) -> dict[str, Any]:
    legs = {}
    for leg, action in (("short", "sell_bid"), ("long", "buy_ask")):
        meta = metadata[leg]
        if meta.get("settlement_period") != "day":
            raise ValueError("only explicit daily-option zero-delivery-fee replay is supported")
        taker = fee_math._number(f"{leg} taker_commission", meta.get("taker_commission"))
        mark = fee_math._number(f"{leg} mark_price", (books.get(leg) or {}).get("mark_price"), positive=True)
        rate = min(taker, 0.125 * mark)
        legs[leg] = {"action": action, "price_source": "mark_price", "mark_price_btc": mark,
                     "fee_rate_btc": rate, "fee_btc": rate * quantity_btc}
    total = legs["short"]["fee_btc"] + legs["long"]["fee_btc"]
    return {"entry_fee_btc": total, "delivery_fee_btc": 0.0, "total_fee_btc": total,
            "delivery_fee_rule": "daily_option_expiry_delivery_fee_zero_explicit", "legs": legs,
            "formula": "sum(min(taker_commission, 12.5% * mark_price) * quantity) per entry leg; daily delivery fee = 0"}


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
    metadata_receipts: dict[str, int] = {}
    for leg, name in (("short", short_instrument), ("long", long_instrument)):
        meta = fetch("public/get_instrument", {"instrument_name": name})
        metadata_receipts[leg] = int(now())
        if not isinstance(meta, dict) or meta.get("instrument_name") != name:
            raise ValueError(f"{leg} instrument metadata is missing or mismatched")
        metadata[leg] = meta
    names = (("short", short_instrument), ("long", long_instrument))
    # Both books are read back to back: their exchange clocks must agree within
    # MAX_BOOK_SKEW_MS. A pair that is already out of step when it lands is read
    # once more, as a pair (never one old leg beside one new leg).
    books, receipts, capture_gaps, wire = _read_book_pair(fetch, now, names, allow_partial)
    rounds, first_round = 1, _pair_sync_reasons(books, receipts, int(now()))
    if first_round:
        books, receipts, capture_gaps, more = _read_book_pair(fetch, now, names, allow_partial)
        rounds, wire = 2, wire + more
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
    # how the book pair was obtained: wire requests and, after a re-read, why the first pair was dropped
    payload["capture_reads"] = {"book_rounds": rounds, "book_requests": wire,
                                "first_round_sync_reasons": first_round}
    if allow_partial:
        payload["capture_gaps"] = capture_gaps
        payload["market"]["reference_leg"] = reference_leg
    # The same configured daily-option fee formula is used by the frozen real
    # replay. It needs only the two books and metadata, not a tradable quote:
    # a transient quote gap (books read out of step) keeps the credit empty but
    # not the fee. A non-daily expiry or a missing book keeps fees unknown.
    try:
        payload["fee"] = configured_fee(metadata, books, short_instrument, long_instrument, quantity_btc)
    except (KeyError, TypeError, ValueError):
        pass  # Keep the unsupported fee explicitly unknown; never infer account fees.
    return payload
