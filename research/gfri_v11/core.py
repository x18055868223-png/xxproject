"""GFRI v1.1 core evaluator, order-book depth checks, and 3-sample confirmation channel.

Pure Python 3.12 stdlib, deterministic, fail-closed shadow research module.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import math
from statistics import median
from typing import Any

from research.gfri_v11.config import (
    CODE_VERSION,
    DEFAULT_CONFIG,
    KNOWN_DEALER_MODEL,
    KNOWN_OI_LAG_NOTE,
    KNOWN_OI_SOURCE_LAG_HOURS,
    SCHEMA_EVAL,
    GfriConfig,
)

_QUALITY_RANK: dict[str, int] = {"VALID": 0, "DEGRADED": 1, "UNKNOWN": 2}
_DIRECTION_SPECS: tuple[tuple[str, int, str, str], ...] = (
    ("down", -1, "down_1pct", "put"),
    ("up", 1, "up_1pct", "call"),
)


def _is_finite_number(val: Any) -> bool:
    """Return True iff val is a real int/float (excluding bool) and finite."""
    return isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(val)


def _to_decimal(val: Any) -> Decimal | None:
    """Convert int/float/str/Decimal (excluding bool) to finite Decimal."""
    if isinstance(val, bool) or val is None:
        return None
    if isinstance(val, Decimal):
        return val if val.is_finite() else None
    if isinstance(val, int):
        return Decimal(val)
    if isinstance(val, float):
        if not math.isfinite(val):
            return None
        return Decimal(str(val))
    if isinstance(val, str):
        text = val.strip()
        if not text:
            return None
        try:
            dec = Decimal(text)
        except InvalidOperation:
            return None
        return dec if dec.is_finite() else None
    return None


def _parse_ts_ms(val: Any) -> int | None:
    """Parse epoch milliseconds (int/float/numeric str) or ISO-8601 UTC string into int ms."""
    if _is_finite_number(val):
        return int(round(float(val)))
    if isinstance(val, str):
        text = val.strip()
        if not text:
            return None
        try:
            num = float(text)
            if math.isfinite(num):
                return int(round(num))
        except ValueError:
            pass
        try:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(round(dt.timestamp() * 1000))
        except ValueError:
            return None
    return None


def _dedupe_reasons(reasons: list[str]) -> list[str]:
    """Return unique non-empty reason strings in first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for item in reasons:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _worst_quality(*statuses: str | None) -> str:
    """Return the strictest quality state among VALID < DEGRADED < UNKNOWN."""
    worst = "VALID"
    for st in statuses:
        if st in _QUALITY_RANK and _QUALITY_RANK[st] > _QUALITY_RANK[worst]:
            worst = st
    return worst


def _ts_future_error(
    ts_ms: int | None,
    *,
    limit_ms: int | None,
    label: str,
) -> str | None:
    """Return reason code if ts_ms exceeds limit_ms."""
    if ts_ms is not None and limit_ms is not None and ts_ms > limit_ms:
        return label
    return None


def check_deribit_instrument_unit(
    inst_payload: dict[str, Any] | None,
) -> tuple[bool, str | None, str | None]:
    """Validate Deribit BTC-PERPETUAL contract unit: quote=USD, type=reversed, contract_size=10."""
    if not isinstance(inst_payload, dict):
        return False, None, "missing_deribit_instrument"
    inst = inst_payload["result"] if isinstance(inst_payload.get("result"), dict) else inst_payload
    if not isinstance(inst, dict):
        return False, None, "missing_deribit_instrument"

    reasons: list[str] = []
    if inst.get("quote_currency") != "USD":
        reasons.append("unsupported_quote_currency")
    if inst.get("instrument_type") != "reversed":
        reasons.append("unsupported_instrument_type")
    cs = inst.get("contract_size")
    if not (_is_finite_number(cs) and float(cs) == 10.0):
        reasons.append("unsupported_contract_size")

    if reasons:
        return False, None, ";".join(reasons)
    return True, "usd", None


def _unknown_depth(
    *,
    reason: str,
    depth_unit: Any,
    ob_timestamp_ms: int | None = None,
    skew_ms: int | None = None,
    ref_price: float | None = None,
    spread_pct: float | None = None,
    price_divergence_pct: float | None = None,
    down_bound_usd: float | None = None,
    up_bound_usd: float | None = None,
    min_bid_usd: float | None = None,
    max_ask_usd: float | None = None,
    bids_count: int | None = None,
    asks_count: int | None = None,
) -> dict[str, Any]:
    return {
        "status": "UNKNOWN",
        "reason": reason,
        "reasons": [reason],
        "down_status": "UNKNOWN",
        "up_status": "UNKNOWN",
        "down_reason": reason,
        "up_reason": reason,
        "down_usd": None,
        "up_usd": None,
        "ref_price": ref_price,
        "spread_pct": spread_pct,
        "price_divergence_pct": price_divergence_pct,
        "down_bound_usd": down_bound_usd,
        "up_bound_usd": up_bound_usd,
        "min_bid_usd": min_bid_usd,
        "max_ask_usd": max_ask_usd,
        "bids_count": bids_count,
        "asks_count": asks_count,
        "depth_unit": depth_unit,
        "ob_timestamp_ms": ob_timestamp_ms,
        "skew_ms": skew_ms,
    }


def _parse_book_levels_decimal(
    levels: Any,
    *,
    notional_mode: str,
    ct_val: Decimal | None = None,
) -> list[tuple[Decimal, Decimal]] | None:
    """Parse order book levels into exact (price_dec, notional_usd_dec) tuples."""
    if not isinstance(levels, list) or len(levels) == 0:
        return None
    parsed: list[tuple[Decimal, Decimal]] = []
    for entry in levels:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            return None
        p_dec = _to_decimal(entry[0])
        a_dec = _to_decimal(entry[1])
        if p_dec is None or a_dec is None or p_dec <= Decimal("0") or a_dec < Decimal("0"):
            return None
        if notional_mode == "usd":
            notional = a_dec
        elif notional_mode == "btc":
            notional = p_dec * a_dec
        elif notional_mode == "okx_linear_btc":
            assert ct_val is not None
            notional = a_dec * ct_val * p_dec
        else:
            return None
        parsed.append((p_dec, notional))
    return parsed


def _compute_bands_from_parsed_book(
    *,
    bids: list[tuple[Decimal, Decimal]],
    asks: list[tuple[Decimal, Decimal]],
    best_bid_raw: Any,
    best_ask_raw: Any,
    shock_dec: Decimal,
    gex_spot_usd: float | None,
    ob_ts_ms: int,
    skew_ms: int | None,
    depth_unit: str,
    cfg: GfriConfig,
) -> dict[str, Any]:
    """Compute exact Decimal band bounds, spread/basis checks, and DOWN/UP USD depth."""
    best_bid = _to_decimal(best_bid_raw)
    best_ask = _to_decimal(best_ask_raw)
    if best_bid is None or best_ask is None:
        return _unknown_depth(
            reason="invalid_best_bid_ask",
            depth_unit=depth_unit,
            ob_timestamp_ms=ob_ts_ms,
            skew_ms=skew_ms,
            bids_count=len(bids),
            asks_count=len(asks),
        )
    if best_bid <= Decimal("0") or best_ask <= Decimal("0"):
        return _unknown_depth(
            reason="non_positive_best_bid_ask",
            depth_unit=depth_unit,
            ob_timestamp_ms=ob_ts_ms,
            skew_ms=skew_ms,
            bids_count=len(bids),
            asks_count=len(asks),
        )
    if best_bid > best_ask:
        return _unknown_depth(
            reason="crossed_order_book",
            depth_unit=depth_unit,
            ob_timestamp_ms=ob_ts_ms,
            skew_ms=skew_ms,
            bids_count=len(bids),
            asks_count=len(asks),
        )

    ref_price_dec = (best_bid + best_ask) / Decimal("2")
    if ref_price_dec <= Decimal("0"):
        return _unknown_depth(
            reason="invalid_ref_price",
            depth_unit=depth_unit,
            ob_timestamp_ms=ob_ts_ms,
            skew_ms=skew_ms,
            bids_count=len(bids),
            asks_count=len(asks),
        )

    spread_pct_dec = (best_ask - best_bid) / ref_price_dec
    max_spread_dec = _to_decimal(cfg.max_spread_pct)
    if max_spread_dec is not None and spread_pct_dec > max_spread_dec:
        return _unknown_depth(
            reason="spread_exceeds_max_threshold",
            depth_unit=depth_unit,
            ob_timestamp_ms=ob_ts_ms,
            skew_ms=skew_ms,
            ref_price=float(ref_price_dec),
            spread_pct=float(spread_pct_dec),
            bids_count=len(bids),
            asks_count=len(asks),
        )

    price_div_pct: float | None = None
    basis_status = "VALID"
    basis_reason: str | None = None
    if gex_spot_usd is not None:
        gex_spot_dec = _to_decimal(gex_spot_usd)
        if gex_spot_dec is None or gex_spot_dec <= Decimal("0"):
            return _unknown_depth(
                reason="invalid_gex_spot_usd",
                depth_unit=depth_unit,
                ob_timestamp_ms=ob_ts_ms,
                skew_ms=skew_ms,
                ref_price=float(ref_price_dec),
                spread_pct=float(spread_pct_dec),
                bids_count=len(bids),
                asks_count=len(asks),
            )
        div_dec = abs(ref_price_dec - gex_spot_dec) / gex_spot_dec
        price_div_pct = float(div_dec)
        fail_dec = _to_decimal(cfg.max_price_divergence_fail_pct)
        deg_dec = _to_decimal(cfg.max_price_divergence_degraded_pct)
        if fail_dec is not None and div_dec > fail_dec:
            return _unknown_depth(
                reason="price_basis_divergence_exceeded",
                depth_unit=depth_unit,
                ob_timestamp_ms=ob_ts_ms,
                skew_ms=skew_ms,
                ref_price=float(ref_price_dec),
                spread_pct=float(spread_pct_dec),
                price_divergence_pct=price_div_pct,
                bids_count=len(bids),
                asks_count=len(asks),
            )
        if deg_dec is not None and div_dec > deg_dec:
            basis_status = "DEGRADED"
            basis_reason = "price_basis_divergence_degraded"

    down_bound_dec = ref_price_dec * (Decimal("1") - shock_dec)
    up_bound_dec = ref_price_dec * (Decimal("1") + shock_dec)
    min_bid_dec = min(p for p, _ in bids)
    max_ask_dec = max(p for p, _ in asks)

    side_configs = (
        ("down", bids, min_bid_dec, down_bound_dec, ref_price_dec, min_bid_dec > down_bound_dec),
        ("up", asks, max_ask_dec, ref_price_dec, up_bound_dec, max_ask_dec < up_bound_dec),
    )

    side_results: dict[str, dict[str, Any]] = {}
    all_reasons: list[str] = [basis_reason] if basis_reason else []
    for side_name, levels, _ext_p, low_b, high_b, is_truncated in side_configs:
        if is_truncated:
            s_status = "UNKNOWN"
            s_reason: str | None = "order_book_truncated_before_band"
            s_usd: float | None = None
        else:
            band_sum = sum((notional for p, notional in levels if low_b <= p <= high_b), Decimal("0"))
            if not band_sum.is_finite() or band_sum <= Decimal("0"):
                s_status = "UNKNOWN"
                s_reason = "zero_or_invalid_depth"
                s_usd = None
            else:
                s_status = basis_status
                s_reason = basis_reason
                s_usd = float(band_sum)
        if s_reason:
            all_reasons.append(s_reason)
        side_results[side_name] = {
            "status": s_status,
            "reason": s_reason,
            "usd": s_usd,
        }

    overall_status = _worst_quality(side_results["down"]["status"], side_results["up"]["status"])
    deduped = _dedupe_reasons(all_reasons)
    return {
        "status": overall_status,
        "reason": ";".join(deduped) if deduped else None,
        "reasons": deduped,
        "down_status": side_results["down"]["status"],
        "up_status": side_results["up"]["status"],
        "down_reason": side_results["down"]["reason"],
        "up_reason": side_results["up"]["reason"],
        "down_usd": side_results["down"]["usd"],
        "up_usd": side_results["up"]["usd"],
        "ref_price": float(ref_price_dec),
        "spread_pct": float(spread_pct_dec),
        "price_divergence_pct": price_div_pct,
        "down_bound_usd": float(down_bound_dec),
        "up_bound_usd": float(up_bound_dec),
        "min_bid_usd": float(min_bid_dec),
        "max_ask_usd": float(max_ask_dec),
        "bids_count": len(bids),
        "asks_count": len(asks),
        "depth_unit": depth_unit,
        "ob_timestamp_ms": ob_ts_ms,
        "skew_ms": skew_ms,
    }


def compute_directional_depth(
    order_book: dict[str, Any] | None,
    *,
    shock_pct: float = 0.01,
    gex_spot_usd: float | None = None,
    gex_asof_ms: int | None = None,
    freeze_at_ms: int | None = None,
    requested_depth: int = 1000,
    depth_unit: str = "usd",
    cfg: GfriConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Compute Deribit directional USD liquidity within exact Decimal +/- shock_pct bounds."""
    shock_dec = _to_decimal(shock_pct)
    if shock_dec is None or not (Decimal("0") < shock_dec < Decimal("1")):
        return _unknown_depth(reason="invalid_shock_pct", depth_unit=depth_unit)
    if depth_unit not in ("usd", "btc"):
        return _unknown_depth(reason="invalid_or_missing_depth_unit", depth_unit=depth_unit)
    if not isinstance(requested_depth, int) or isinstance(requested_depth, bool) or requested_depth < cfg.min_deribit_depth_param:
        return _unknown_depth(reason="insufficient_requested_depth", depth_unit=depth_unit)
    if not isinstance(order_book, dict):
        return _unknown_depth(reason="missing_order_book", depth_unit=depth_unit)

    book = order_book["result"] if isinstance(order_book.get("result"), dict) else order_book
    if not isinstance(book, dict):
        return _unknown_depth(reason="missing_order_book", depth_unit=depth_unit)

    ob_ts_ms = _parse_ts_ms(book.get("timestamp"))
    if ob_ts_ms is None:
        return _unknown_depth(reason="missing_order_book_timestamp", depth_unit=depth_unit)

    freeze_ms = _parse_ts_ms(freeze_at_ms) if freeze_at_ms is not None else None
    if freeze_ms is not None and ob_ts_ms > freeze_ms + cfg.max_future_drift_ms:
        return _unknown_depth(
            reason="ob_future_timestamp_exceeds_freeze",
            depth_unit=depth_unit,
            ob_timestamp_ms=ob_ts_ms,
        )

    skew_ms: int | None = None
    if gex_asof_ms is not None:
        gex_ms = _parse_ts_ms(gex_asof_ms)
        if gex_ms is None:
            return _unknown_depth(
                reason="invalid_gex_asof_ms",
                depth_unit=depth_unit,
                ob_timestamp_ms=ob_ts_ms,
            )
        skew_ms = ob_ts_ms - gex_ms
        if abs(skew_ms) > cfg.max_ob_skew_ms:
            return _unknown_depth(
                reason="order_book_time_mismatch",
                depth_unit=depth_unit,
                ob_timestamp_ms=ob_ts_ms,
                skew_ms=skew_ms,
            )

    bids = _parse_book_levels_decimal(book.get("bids"), notional_mode=depth_unit)
    asks = _parse_book_levels_decimal(book.get("asks"), notional_mode=depth_unit)
    if bids is None or asks is None:
        return _unknown_depth(
            reason="empty_or_invalid_order_book_levels",
            depth_unit=depth_unit,
            ob_timestamp_ms=ob_ts_ms,
            skew_ms=skew_ms,
        )
    if len(bids) < cfg.min_book_levels_per_side or len(asks) < cfg.min_book_levels_per_side:
        return _unknown_depth(
            reason="insufficient_book_levels",
            depth_unit=depth_unit,
            ob_timestamp_ms=ob_ts_ms,
            skew_ms=skew_ms,
            bids_count=len(bids),
            asks_count=len(asks),
        )

    best_bid_raw = book["best_bid_price"] if "best_bid_price" in book else max(p for p, _ in bids)
    best_ask_raw = book["best_ask_price"] if "best_ask_price" in book else min(p for p, _ in asks)
    return _compute_bands_from_parsed_book(
        bids=bids,
        asks=asks,
        best_bid_raw=best_bid_raw,
        best_ask_raw=best_ask_raw,
        shock_dec=shock_dec,
        gex_spot_usd=gex_spot_usd,
        ob_ts_ms=ob_ts_ms,
        skew_ms=skew_ms,
        depth_unit=depth_unit,
        cfg=cfg,
    )


def compute_okx_perp_depth(
    okx_book: dict[str, Any] | None,
    okx_inst: dict[str, Any] | None,
    *,
    shock_pct: float = 0.01,
    gex_spot_usd: float | None = None,
    freeze_at_ms: int | None = None,
    cfg: GfriConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Compute non-blocking OKX linear swap USD depth reference from books-full + instruments."""
    def _okx_unknown(reason: str, *, inst_id: str | None = None, ob_ts_ms: int | None = None) -> dict[str, Any]:
        out = _unknown_depth(reason=reason, depth_unit="usd_okx_linear_ctval", ob_timestamp_ms=ob_ts_ms)
        out.update({"venue": "okx", "non_blocking": True, "inst_id": inst_id})
        return out

    shock_dec = _to_decimal(shock_pct)
    if shock_dec is None or not (Decimal("0") < shock_dec < Decimal("1")):
        return _okx_unknown("invalid_shock_pct")
    if not isinstance(okx_book, dict) or not isinstance(okx_inst, dict):
        return _okx_unknown("missing_okx_book_or_instrument")

    if "code" in okx_inst and str(okx_inst["code"]) != "0":
        return _okx_unknown("okx_instrument_error_code")
    if "code" in okx_book and str(okx_book["code"]) != "0":
        return _okx_unknown("okx_book_error_code")

    inst_data = okx_inst.get("data")
    inst = inst_data[0] if isinstance(inst_data, list) and inst_data else okx_inst
    book_data = okx_book.get("data")
    book = book_data[0] if isinstance(book_data, list) and book_data else okx_book
    if not isinstance(inst, dict) or not isinstance(book, dict):
        return _okx_unknown("invalid_okx_payload_shape")

    inst_id = inst.get("instId")
    ct_val = _to_decimal(inst.get("ctVal"))
    ct_val_ccy = inst.get("ctValCcy")
    ct_type = inst.get("ctType")
    if ct_val is None or ct_val <= Decimal("0") or ct_val_ccy != "BTC" or ct_type != "linear":
        return _okx_unknown("unsupported_okx_instrument_spec", inst_id=inst_id)

    ob_ts_ms = _parse_ts_ms(book.get("ts") if book.get("ts") is not None else book.get("timestamp"))
    if ob_ts_ms is None:
        return _okx_unknown("missing_okx_timestamp", inst_id=inst_id)

    freeze_ms = _parse_ts_ms(freeze_at_ms) if freeze_at_ms is not None else None
    if freeze_ms is not None and ob_ts_ms > freeze_ms + cfg.max_future_drift_ms:
        return _okx_unknown("okx_future_timestamp_exceeds_freeze", inst_id=inst_id, ob_ts_ms=ob_ts_ms)

    bids = _parse_book_levels_decimal(book.get("bids"), notional_mode="okx_linear_btc", ct_val=ct_val)
    asks = _parse_book_levels_decimal(book.get("asks"), notional_mode="okx_linear_btc", ct_val=ct_val)
    if bids is None or asks is None:
        return _okx_unknown("empty_or_invalid_okx_book_levels", inst_id=inst_id, ob_ts_ms=ob_ts_ms)

    best_bid = max(p for p, _ in bids)
    best_ask = min(p for p, _ in asks)
    res = _compute_bands_from_parsed_book(
        bids=bids,
        asks=asks,
        best_bid_raw=best_bid,
        best_ask_raw=best_ask,
        shock_dec=shock_dec,
        gex_spot_usd=gex_spot_usd,
        ob_ts_ms=ob_ts_ms,
        skew_ms=None,
        depth_unit="usd_okx_linear_ctval",
        cfg=cfg,
    )
    res.update(
        {
            "venue": "okx",
            "non_blocking": True,
            "inst_id": inst_id,
            "ct_val": float(ct_val),
            "ct_val_ccy": ct_val_ccy,
            "ct_type": ct_type,
        }
    )
    return res


def _extract_curve_points(
    rows: Any,
    *,
    pct_key: str,
    targets: tuple[float, ...] = (-1.0, 1.0),
) -> dict[float, float]:
    """Extract hedging_usd values for exact target move percentages."""
    found: dict[float, float] = {}
    if not isinstance(rows, list):
        return found
    for row in rows:
        if not isinstance(row, dict):
            continue
        pct = row.get(pct_key)
        h_usd = row.get("hedging_usd")
        if not (_is_finite_number(pct) and _is_finite_number(h_usd)):
            continue
        pct_f = float(pct)
        for tgt in targets:
            if abs(pct_f - tgt) < 1e-9:
                found[tgt] = float(h_usd)
    return found


def _extract_zero_dte_gex(gex_payload: dict[str, Any]) -> float | None:
    direct = gex_payload.get("zero_dte_gex")
    if _is_finite_number(direct):
        return float(direct)
    profiles = gex_payload.get("profiles")
    if not isinstance(profiles, dict):
        return None
    p0 = profiles.get("0dte")
    if not isinstance(p0, dict):
        return None
    chart = p0.get("chartData")
    if not isinstance(chart, list) or len(chart) == 0:
        return None
    total = 0.0
    for row in chart:
        if not isinstance(row, dict) or not _is_finite_number(row.get("netGex")):
            return None
        total += float(row["netGex"])
    return total if math.isfinite(total) else None


def _linear_hedge_flow(total_net_gex: float, shock_pct: float, sign_ds: int) -> float:
    """Linear projection: H_hat(x) = -GEX_1pct * (sign_ds * shock_pct / 0.01)."""
    return -float(total_net_gex) * (float(sign_ds) * float(shock_pct) / 0.01)


def extract_gex_snapshot(
    gex_payload: dict[str, Any] | None,
    *,
    now_ms: int | None = None,
    freeze_at_ms: int | None = None,
    received_at_ms: int | None = None,
    cfg: GfriConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Extract and strictly validate GEX snapshot, method priority, and research submetrics."""
    base_oi_meta = {
        "oi_source_lag_hours": KNOWN_OI_SOURCE_LAG_HOURS,
        "oi_lag_note": KNOWN_OI_LAG_NOTE,
    }
    if not isinstance(gex_payload, dict):
        return {
            "valid": False,
            "data_quality": "UNKNOWN",
            "reason": "missing_or_invalid_gex_payload",
            "reasons": ["missing_or_invalid_gex_payload"],
            "gex_data_ts_ms": None,
            "computed_at_ms": None,
            "received_at_ms": None,
            "age_ms": None,
            "spot_usd": None,
            "total_net_gex": None,
            "primary_1pct": {},
            "method_tier": None,
            "spot_linked_12h_diagnostic": None,
            "research_submetrics": None,
            **base_oi_meta,
        }

    raw_data_ts = gex_payload.get("data_timestamp")
    if raw_data_ts is None:
        raw_data_ts = gex_payload.get("timestamp")
    gex_data_ts_ms = _parse_ts_ms(raw_data_ts)

    raw_computed_at = gex_payload.get("computed_at")
    computed_at_ms = _parse_ts_ms(raw_computed_at)
    raw_rec_ms = received_at_ms if received_at_ms is not None else gex_payload.get("received_at_ms")
    rec_ms = _parse_ts_ms(raw_rec_ms)

    freeze_ms = _parse_ts_ms(freeze_at_ms) if freeze_at_ms is not None else None
    effective_now_ms = _parse_ts_ms(now_ms) if now_ms is not None else freeze_ms
    future_limit_ms = (
        freeze_ms + cfg.max_future_drift_ms
        if freeze_ms is not None
        else (effective_now_ms + cfg.max_future_drift_ms if effective_now_ms is not None else None)
    )

    quality = gex_payload.get("quality") if isinstance(gex_payload.get("quality"), dict) else {}
    reasons: list[str] = [str(r) for r in (quality.get("reasons") or []) if r]

    ts_errors: list[str] = []
    if gex_data_ts_ms is None:
        ts_errors.append("missing_gex_timestamp")
    if raw_computed_at is not None and computed_at_ms is None:
        ts_errors.append("invalid_computed_at_timestamp")
    for err in (
        _ts_future_error(gex_data_ts_ms, limit_ms=future_limit_ms, label="gex_source_future_timestamp"),
        _ts_future_error(computed_at_ms, limit_ms=future_limit_ms, label="gex_computed_at_future_timestamp"),
        _ts_future_error(rec_ms, limit_ms=future_limit_ms, label="gex_received_at_future_timestamp"),
    ):
        if err:
            ts_errors.append(err)

    age_ms: int | None = None
    if effective_now_ms is not None and gex_data_ts_ms is not None:
        age_ms = effective_now_ms - gex_data_ts_ms
        if age_ms > cfg.max_source_age_ms:
            ts_errors.append("gex_source_expired")

    raw_q_status = quality.get("status")
    stale_flag = gex_payload.get("stale") is True
    q_upper = raw_q_status.upper() if isinstance(raw_q_status, str) else None
    if stale_flag:
        reasons.append("gex_source_marked_stale")
        base_quality = "UNKNOWN"
    elif q_upper in ("ERROR", "FAILED", "UNAVAILABLE"):
        reasons.append(f"gex_quality_status_{q_upper.lower()}")
        base_quality = "UNKNOWN"
    elif raw_q_status is None or q_upper == "DEGRADED":
        if raw_q_status is None:
            reasons.append("gex_quality_status_missing")
        base_quality = "DEGRADED"
    elif q_upper in ("OK", "VALID", "HEALTHY", "GOOD"):
        base_quality = "VALID"
    else:
        reasons.append("gex_quality_status_unrecognized")
        base_quality = "UNKNOWN"

    spot_raw = gex_payload.get("asset_price")
    spot_usd = float(spot_raw) if (_is_finite_number(spot_raw) and float(spot_raw) > 0.0) else None
    if spot_usd is None:
        reasons.append("missing_or_invalid_spot_usd")

    hf = gex_payload.get("hedging_flows")
    hf_dict = hf if isinstance(hf, dict) else {}
    units = hf_dict.get("units") if isinstance(hf_dict.get("units"), dict) else {}
    total_gamma_unit_ok = units.get("total_gamma") == "usd_delta_per_1pct_spot_move"
    hedging_usd_unit_ok = units.get("hedging_usd") == "usd_notional"
    if not total_gamma_unit_ok:
        reasons.append("unverified_total_gamma_unit")
    if not hedging_usd_unit_ok:
        reasons.append("unverified_hedging_usd_unit")

    raw_total_gex = gex_payload["total_gex"] if gex_payload.get("total_gex") is not None else hf_dict.get("total_gamma")
    total_net_gex = float(raw_total_gex) if (total_gamma_unit_ok and _is_finite_number(raw_total_gex)) else None

    # Tier 1: Native price_only_curve (iv_mode == "fixed", time_forward_hours == 0, units.hedging_usd == "usd_notional")
    scenario_sets = hf_dict.get("scenario_sets") if isinstance(hf_dict.get("scenario_sets"), dict) else {}
    po_meta = scenario_sets.get("price_only") if isinstance(scenario_sets.get("price_only"), dict) else {}
    po_meta_ok = True
    if po_meta:
        po_meta_ok = po_meta.get("iv_mode") == "fixed" and po_meta.get("time_forward_hours") == 0
        if not po_meta_ok:
            reasons.append("invalid_price_only_curve_metadata")

    po_points: dict[float, float] = {}
    if hedging_usd_unit_ok and po_meta_ok:
        po_points = _extract_curve_points(hf_dict.get("price_only_curve"), pct_key="move_pct", targets=(-1.0, 1.0))
        if (-1.0 not in po_points or 1.0 not in po_points) and isinstance(po_meta.get("hedging_curve"), list):
            ss_points = _extract_curve_points(po_meta.get("hedging_curve"), pct_key="move_pct", targets=(-1.0, 1.0))
            for k, v in ss_points.items():
                po_points.setdefault(k, v)

    # Diagnostic only: spot_linked_12h (+/-1% from scenarios, 0% and fallback from hedging_curve)
    sc_diag = (
        _extract_curve_points(hf_dict.get("scenarios"), pct_key="price_change_pct", targets=(-1.0, 1.0))
        if hedging_usd_unit_ok
        else {}
    )
    hc_diag = (
        _extract_curve_points(hf_dict.get("hedging_curve"), pct_key="move_pct", targets=(-1.0, 0.0, 1.0))
        if hedging_usd_unit_ok
        else {}
    )
    sl_set = scenario_sets.get("spot_linked_12h") if isinstance(scenario_sets.get("spot_linked_12h"), dict) else {}
    sl_diag = (
        _extract_curve_points(sl_set.get("hedging_curve"), pct_key="move_pct", targets=(-1.0, 0.0, 1.0))
        if hedging_usd_unit_ok
        else {}
    )
    spot_linked_12h_diagnostic = {
        "down_1pct_usd": sc_diag.get(-1.0, hc_diag.get(-1.0, sl_diag.get(-1.0))),
        "zero_pct_usd": hc_diag.get(0.0, sl_diag.get(0.0)),
        "up_1pct_usd": sc_diag.get(1.0, hc_diag.get(1.0, sl_diag.get(1.0))),
        "default_scenario": hf_dict.get("default_scenario"),
        "diagnostic_only": True,
    }

    # Resolve primary 1% per direction cleanly (Tier 1 native -> Tier 2 NET_GEX_LINEAR -> Tier 3 UNKNOWN)
    primary_1pct: dict[str, dict[str, Any]] = {}
    used_tiers: set[str] = set()
    for side_name, sign_ds, key_name, _ in _DIRECTION_SPECS:
        pct_key = float(sign_ds)
        if pct_key in po_points:
            primary_1pct[key_name] = {
                "status": "VALID",
                "h_usd": po_points[pct_key],
                "method": "HEDGE_FLOW_NATIVE",
                "method_tier": "NATIVE_PRICE_ONLY",
                "curve_used": "price_only_curve",
            }
            used_tiers.add("NATIVE_PRICE_ONLY")
        elif total_net_gex is not None and total_gamma_unit_ok:
            primary_1pct[key_name] = {
                "status": "DEGRADED",
                "h_usd": _linear_hedge_flow(total_net_gex, 0.01, sign_ds),
                "method": "NET_GEX_LINEAR",
                "method_tier": "LINEAR_PROXY_FALLBACK",
                "curve_used": "total_net_gex_linear",
            }
            used_tiers.add("LINEAR_PROXY_FALLBACK")
        else:
            primary_1pct[key_name] = {
                "status": "UNKNOWN",
                "h_usd": None,
                "method": None,
                "method_tier": "UNAVAILABLE",
                "curve_used": None,
            }
            used_tiers.add("UNAVAILABLE")

    if "LINEAR_PROXY_FALLBACK" in used_tiers:
        reasons.append("price_only_curve_unavailable_linear_fallback")
    if "UNAVAILABLE" in used_tiers:
        reasons.append("insufficient_gex_or_hedge_flow")

    if ts_errors or spot_usd is None or base_quality == "UNKNOWN" or "UNAVAILABLE" in used_tiers:
        data_quality = "UNKNOWN"
    elif base_quality == "DEGRADED" or "LINEAR_PROXY_FALLBACK" in used_tiers:
        data_quality = "DEGRADED"
    else:
        data_quality = "VALID"

    reasons.extend(ts_errors)
    deduped = _dedupe_reasons(reasons)
    valid = data_quality != "UNKNOWN"

    zero_dte_gex = _extract_zero_dte_gex(gex_payload) if total_gamma_unit_ok else None
    zero_dte_share: dict[str, Any] | None = None
    if zero_dte_gex is not None and total_net_gex is not None and total_net_gex != 0.0:
        zero_dte_share = {
            "zero_dte_gex_usd": zero_dte_gex,
            "total_net_gex_usd": total_net_gex,
            "share_signed": zero_dte_gex / total_net_gex,
            "share_abs_pct": (abs(zero_dte_gex) / abs(total_net_gex)) * 100.0,
        }

    research_submetrics = {
        "down_0p5pct_flow_usd": (
            _linear_hedge_flow(total_net_gex, 0.005, -1)
            if (total_net_gex is not None and total_gamma_unit_ok)
            else None
        ),
        "up_0p5pct_flow_usd": (
            _linear_hedge_flow(total_net_gex, 0.005, 1)
            if (total_net_gex is not None and total_gamma_unit_ok)
            else None
        ),
        "zero_dte_share": zero_dte_share,
    }

    total_oi = gex_payload.get("total_open_interest")
    if not _is_finite_number(total_oi):
        total_oi = quality.get("total_open_interest")
    total_oi_val = float(total_oi) if _is_finite_number(total_oi) else None
    model = hf_dict.get("model") if isinstance(hf_dict.get("model"), dict) else {}

    method_tier = (
        "NATIVE_PRICE_ONLY"
        if used_tiers == {"NATIVE_PRICE_ONLY"}
        else ("LINEAR_PROXY_FALLBACK" if used_tiers == {"LINEAR_PROXY_FALLBACK"} else ",".join(sorted(used_tiers)))
    )

    return {
        "valid": valid,
        "data_quality": data_quality,
        "reason": deduped[0] if not valid and deduped else None,
        "reasons": deduped,
        "gex_data_ts_ms": gex_data_ts_ms,
        "computed_at_ms": computed_at_ms,
        "received_at_ms": rec_ms,
        "age_ms": age_ms,
        "spot_usd": spot_usd,
        "total_net_gex": total_net_gex,
        "total_gamma_unit_ok": total_gamma_unit_ok,
        "hedging_usd_unit_ok": hedging_usd_unit_ok,
        "primary_1pct": primary_1pct,
        "method_tier": method_tier,
        "spot_linked_12h_diagnostic": spot_linked_12h_diagnostic,
        "research_submetrics": research_submetrics,
        "snapshot_id": gex_payload.get("snapshot_id"),
        "universe_hash": gex_payload.get("universe_hash"),
        "methodology_version": gex_payload.get("methodology_version") or quality.get("methodology_version"),
        "universe_policy": gex_payload.get("universe_policy") or quality.get("universe_policy"),
        "total_open_interest": total_oi_val,
        "dealer_position_model": model.get("dealer_position_model") or KNOWN_DEALER_MODEL,
        "dealer_positions_observed": model.get("dealer_positions_observed"),
        "quality_status": raw_q_status,
        "stale": stale_flag,
        **base_oi_meta,
    }


def _evaluate_side_gfri(
    *,
    h_usd: float | None,
    sign_ds: int,
    l_usd: float | None,
    gex_side_info: dict[str, Any],
    depth_side_status: str,
    depth_side_reason: str | None,
    forced_reason: str | None = None,
) -> dict[str, Any]:
    """Compute signed_ratio, amplifying_pressure, and GFRI for one direction; fail closed if L <= 0."""
    method = gex_side_info.get("method")
    method_tier = gex_side_info.get("method_tier")
    curve_used = gex_side_info.get("curve_used")

    if forced_reason:
        return {
            "status": "UNKNOWN",
            "reason": forced_reason,
            "sign_ds": sign_ds,
            "method": method,
            "method_tier": method_tier,
            "curve_used": curve_used,
            "raw_hedge_flow_usd": h_usd,
            "signed_ratio": None,
            "amplifying_pressure_usd": None,
            "liquidity_depth_usd": l_usd,
            "gfri": None,
            "is_amplifying": None,
        }

    h_ok = _is_finite_number(h_usd)
    l_ok = _is_finite_number(l_usd) and float(l_usd) > 0.0
    if not h_ok or not l_ok or gex_side_info.get("status") == "UNKNOWN" or depth_side_status == "UNKNOWN":
        reason = depth_side_reason or ("missing_hedge_flow" if not h_ok else "invalid_or_non_positive_liquidity")
        return {
            "status": "UNKNOWN",
            "reason": reason,
            "sign_ds": sign_ds,
            "method": method,
            "method_tier": method_tier,
            "curve_used": curve_used,
            "raw_hedge_flow_usd": float(h_usd) if h_ok else None,
            "signed_ratio": None,
            "amplifying_pressure_usd": None,
            "liquidity_depth_usd": float(l_usd) if l_ok else None,
            "gfri": None,
            "is_amplifying": None,
        }

    h_f = float(h_usd)
    l_f = float(l_usd)
    signed_product = h_f * sign_ds
    signed_ratio = signed_product / l_f
    amp_pressure = max(0.0, signed_product)
    gfri = amp_pressure / l_f
    side_status = _worst_quality(gex_side_info.get("status"), depth_side_status)
    return {
        "status": side_status,
        "reason": depth_side_reason if side_status != "VALID" else None,
        "sign_ds": sign_ds,
        "method": method,
        "method_tier": method_tier,
        "curve_used": curve_used,
        "raw_hedge_flow_usd": h_f,
        "signed_ratio": signed_ratio,
        "amplifying_pressure_usd": amp_pressure,
        "liquidity_depth_usd": l_f,
        "gfri": gfri,
        "is_amplifying": signed_product > 0.0,
    }


def evaluate_single_observation(
    gex_payload: dict[str, Any] | None,
    order_book: dict[str, Any] | None,
    *,
    freeze_at_ms: int,
    gex_received_at_ms: int | None = None,
    ob_received_at_ms: int | None = None,
    gex_sha256: str | None = None,
    ob_sha256: str | None = None,
    deribit_instrument: dict[str, Any] | None = None,
    okx_book: dict[str, Any] | None = None,
    okx_instrument: dict[str, Any] | None = None,
    okx_sha256: str | None = None,
    depth_unit: str = "usd",
    requested_depth: int = 1000,
    cfg: GfriConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Evaluate a single GEX + order-book observation into unified gfri_eval_v11 schema."""
    cfg.flags.validate_locked()
    freeze_ms = _parse_ts_ms(freeze_at_ms)
    if freeze_ms is None:
        raise ValueError("freeze_at_ms must be a valid timestamp")

    reasons: list[str] = []
    effective_depth_unit = depth_unit
    inst_verified: bool | None = None
    inst_error: str | None = None
    if deribit_instrument is not None:
        inst_ok, inst_unit, inst_reason = check_deribit_instrument_unit(deribit_instrument)
        inst_verified = inst_ok
        if inst_ok and inst_unit is not None:
            effective_depth_unit = inst_unit
        else:
            inst_error = inst_reason or "invalid_deribit_instrument"
            reasons.append(inst_error)

    gex_snap = extract_gex_snapshot(
        gex_payload,
        now_ms=freeze_ms,
        freeze_at_ms=freeze_ms,
        received_at_ms=gex_received_at_ms,
        cfg=cfg,
    )
    reasons.extend(gex_snap.get("reasons") or [])

    ob_rec_ms = _parse_ts_ms(ob_received_at_ms) if ob_received_at_ms is not None else None
    ob_rec_err = _ts_future_error(
        ob_rec_ms,
        limit_ms=freeze_ms + cfg.max_future_drift_ms,
        label="ob_received_at_future_timestamp",
    )
    if ob_rec_err:
        reasons.append(ob_rec_err)

    depth_1pct = compute_directional_depth(
        order_book,
        shock_pct=0.01,
        gex_spot_usd=gex_snap.get("spot_usd"),
        gex_asof_ms=gex_snap.get("gex_data_ts_ms"),
        freeze_at_ms=freeze_ms,
        requested_depth=requested_depth,
        depth_unit=effective_depth_unit,
        cfg=cfg,
    )
    reasons.extend(depth_1pct.get("reasons") or [])

    forced_fail = inst_error or ob_rec_err or (gex_snap.get("reason") if not gex_snap.get("valid") else None)
    side_evals: dict[str, dict[str, Any]] = {}
    for side_name, sign_ds, key_name, _ in _DIRECTION_SPECS:
        gex_side = (gex_snap.get("primary_1pct") or {}).get(key_name, {})
        side_evals[key_name] = _evaluate_side_gfri(
            h_usd=gex_side.get("h_usd"),
            sign_ds=sign_ds,
            l_usd=depth_1pct.get(f"{side_name}_usd"),
            gex_side_info=gex_side,
            depth_side_status=depth_1pct.get(f"{side_name}_status", "UNKNOWN"),
            depth_side_reason=depth_1pct.get(f"{side_name}_reason"),
            forced_reason=forced_fail,
        )

    # Segregated 0.5% research submetrics
    depth_0p5 = compute_directional_depth(
        order_book,
        shock_pct=0.005,
        gex_spot_usd=gex_snap.get("spot_usd"),
        gex_asof_ms=gex_snap.get("gex_data_ts_ms"),
        freeze_at_ms=freeze_ms,
        requested_depth=requested_depth,
        depth_unit=effective_depth_unit,
        cfg=cfg,
    )
    sub_raw = gex_snap.get("research_submetrics") or {}
    sub_out: dict[str, Any] = {"zero_dte_share": sub_raw.get("zero_dte_share")}
    for side_name, sign_ds, _, _ in _DIRECTION_SPECS:
        sub_key = f"{side_name}_0p5pct"
        h_0p5 = sub_raw.get(f"{sub_key}_flow_usd")
        sub_out[sub_key] = _evaluate_side_gfri(
            h_usd=h_0p5,
            sign_ds=sign_ds,
            l_usd=depth_0p5.get(f"{side_name}_usd"),
            gex_side_info={
                "status": "DEGRADED" if h_0p5 is not None else "UNKNOWN",
                "method": "NET_GEX_LINEAR" if h_0p5 is not None else None,
                "method_tier": "RESEARCH_LINEAR_0P5PCT",
                "curve_used": "total_net_gex_linear",
            },
            depth_side_status=depth_0p5.get(f"{side_name}_status", "UNKNOWN"),
            depth_side_reason=depth_0p5.get(f"{side_name}_reason"),
            forced_reason=forced_fail,
        )

    cross_venue_ref: dict[str, Any] | None = None
    if okx_book is not None or okx_instrument is not None:
        cross_venue_ref = compute_okx_perp_depth(
            okx_book,
            okx_instrument,
            shock_pct=0.01,
            gex_spot_usd=gex_snap.get("spot_usd"),
            freeze_at_ms=freeze_ms,
            cfg=cfg,
        )
        cross_venue_ref["okx_sha256"] = okx_sha256
        okx_d = cross_venue_ref.get("down_usd")
        okx_u = cross_venue_ref.get("up_usd")
        db_d = depth_1pct.get("down_usd")
        db_u = depth_1pct.get("up_usd")
        cross_venue_ref["ratio_okx_to_deribit_down"] = (
            (okx_d / db_d) if (_is_finite_number(okx_d) and _is_finite_number(db_d) and float(db_d) > 0.0) else None
        )
        cross_venue_ref["ratio_okx_to_deribit_up"] = (
            (okx_u / db_u) if (_is_finite_number(okx_u) and _is_finite_number(db_u) and float(db_u) > 0.0) else None
        )
        h_d = side_evals["down_1pct"].get("raw_hedge_flow_usd")
        h_u = side_evals["up_1pct"].get("raw_hedge_flow_usd")
        cross_venue_ref["gfri_down_1pct_okx"] = (
            max(0.0, float(h_d) * -1.0) / float(okx_d)
            if (_is_finite_number(h_d) and _is_finite_number(okx_d) and float(okx_d) > 0.0)
            else None
        )
        cross_venue_ref["gfri_up_1pct_okx"] = (
            max(0.0, float(h_u) * 1.0) / float(okx_u)
            if (_is_finite_number(h_u) and _is_finite_number(okx_u) and float(okx_u) > 0.0)
            else None
        )

    overall_quality = _worst_quality(
        "UNKNOWN" if forced_fail else "VALID",
        gex_snap.get("data_quality"),
        side_evals["down_1pct"]["status"],
        side_evals["up_1pct"]["status"],
    )

    return {
        "schema": SCHEMA_EVAL,
        "code_version": CODE_VERSION,
        "decision_authority": cfg.flags.decision_authority,
        "data_quality": overall_quality,
        "reasons": _dedupe_reasons(reasons),
        "gfri_down_1pct": side_evals["down_1pct"]["gfri"] if not forced_fail else None,
        "gfri_up_1pct": side_evals["up_1pct"]["gfri"] if not forced_fail else None,
        "down_1pct": side_evals["down_1pct"],
        "up_1pct": side_evals["up_1pct"],
        "liquidity_depth": depth_1pct,
        "research_submetrics": sub_out,
        "spot_linked_12h_diagnostic": gex_snap.get("spot_linked_12h_diagnostic"),
        "cross_venue_ref": cross_venue_ref,
        "comparability": {
            "snapshot_id": gex_snap.get("snapshot_id"),
            "universe_hash": gex_snap.get("universe_hash"),
            "methodology_version": gex_snap.get("methodology_version"),
            "universe_policy": gex_snap.get("universe_policy"),
            "total_open_interest": gex_snap.get("total_open_interest"),
            "dealer_position_model": gex_snap.get("dealer_position_model"),
            "dealer_positions_observed": gex_snap.get("dealer_positions_observed"),
            "oi_source_lag_hours": gex_snap.get("oi_source_lag_hours"),
            "oi_lag_note": gex_snap.get("oi_lag_note"),
            "method_tier": gex_snap.get("method_tier"),
            "depth_unit": effective_depth_unit,
            "requested_depth": requested_depth,
            "price_divergence_pct": depth_1pct.get("price_divergence_pct"),
        },
        "timestamps": {
            "freeze_at_ms": freeze_ms,
            "gex_data_ts_ms": gex_snap.get("gex_data_ts_ms"),
            "gex_computed_at_ms": gex_snap.get("computed_at_ms"),
            "gex_received_at_ms": gex_snap.get("received_at_ms"),
            "gex_age_ms": gex_snap.get("age_ms"),
            "ob_timestamp_ms": depth_1pct.get("ob_timestamp_ms"),
            "ob_exchange_ts_ms": depth_1pct.get("ob_timestamp_ms"),
            "ob_received_at_ms": ob_rec_ms,
            "ob_skew_ms": depth_1pct.get("skew_ms"),
            "oi_source_lag_hours": gex_snap.get("oi_source_lag_hours"),
            "oi_lag_note": gex_snap.get("oi_lag_note"),
        },
        "provenance": {
            "gex_sha256": gex_sha256,
            "ob_sha256": ob_sha256,
            "okx_sha256": okx_sha256,
            "gex_snapshot_id": gex_snap.get("snapshot_id"),
            "gex_universe_hash": gex_snap.get("universe_hash"),
            "total_open_interest": gex_snap.get("total_open_interest"),
            "gex_spot_usd": gex_snap.get("spot_usd"),
            "ob_ref_price_usd": depth_1pct.get("ref_price"),
            "bid_levels": depth_1pct.get("bids_count"),
            "ask_levels": depth_1pct.get("asks_count"),
            "deribit_depth_param": requested_depth,
            "min_bid_usd": depth_1pct.get("min_bid_usd"),
            "max_ask_usd": depth_1pct.get("max_ask_usd"),
            "ob_bid_coverage_pct": (
                ((depth_1pct["ref_price"] - depth_1pct["min_bid_usd"]) / depth_1pct["ref_price"]) * 100.0
                if (_is_finite_number(depth_1pct.get("ref_price")) and float(depth_1pct["ref_price"]) > 0.0 and _is_finite_number(depth_1pct.get("min_bid_usd")))
                else None
            ),
            "ob_ask_coverage_pct": (
                ((depth_1pct["max_ask_usd"] - depth_1pct["ref_price"]) / depth_1pct["ref_price"]) * 100.0
                if (_is_finite_number(depth_1pct.get("ref_price")) and float(depth_1pct["ref_price"]) > 0.0 and _is_finite_number(depth_1pct.get("max_ask_usd")))
                else None
            ),
            "gex_quality_status": gex_snap.get("quality_status"),
            "gex_stale": gex_snap.get("stale"),
            "deribit_instrument_verified": inst_verified,
        },
    }


def _confirm_side(
    ordered_obs: list[dict[str, Any]],
    *,
    obs_key: str,
    cfg: GfriConfig,
    forced_unknown_reason: str | None = None,
) -> dict[str, Any]:
    """Run Fast + Confirmation evaluation for one direction (put=down_1pct, call=up_1pct)."""
    side_label = "PUT" if obs_key == "down_1pct" else "CALL"
    if forced_unknown_reason:
        return {
            "side": side_label,
            "shock": obs_key,
            "direction": obs_key,
            "status": "UNKNOWN",
            "confirmation_status": "UNKNOWN",
            "valid_sample_count": 0,
            "valid_count": 0,
            "sample_count": len(ordered_obs),
            "gfri_confirmed": None,
            "gfri_fast_max": None,
            "gfri_fast_min": None,
            "gfri_latest_raw": None,
            "raw_hedge_flow_usd": None,
            "liquidity_depth_usd": None,
            "signed_ratio": None,
            "is_amplifying": None,
            "spread_ratio": None,
            "single_blip_detected": False,
            "samples_gfri": [],
            "sample_values": [],
            "sample_depths_usd": [],
            "reasons": [forced_unknown_reason],
        }

    valid_gfri: list[float] = []
    valid_depths: list[float] = []
    valid_flows: list[float] = []
    valid_signed: list[float] = []
    sample_statuses: list[str] = []
    side_reasons: list[str] = []

    for obs in ordered_obs:
        side_obj = obs.get(obs_key) if isinstance(obs, dict) else None
        if not isinstance(side_obj, dict):
            side_reasons.append(f"missing_{obs_key}_payload")
            continue
        st = side_obj.get("status")
        gfri_v = side_obj.get("gfri")
        depth_v = side_obj.get("liquidity_depth_usd")
        if st in ("VALID", "DEGRADED") and _is_finite_number(gfri_v) and _is_finite_number(depth_v) and float(depth_v) > 0.0:
            valid_gfri.append(float(gfri_v))
            valid_depths.append(float(depth_v))
            if _is_finite_number(side_obj.get("raw_hedge_flow_usd")):
                valid_flows.append(float(side_obj["raw_hedge_flow_usd"]))
            if _is_finite_number(side_obj.get("signed_ratio")):
                valid_signed.append(float(side_obj["signed_ratio"]))
            sample_statuses.append(st)
            if side_obj.get("reason"):
                side_reasons.append(str(side_obj["reason"]))
        else:
            if side_obj.get("reason"):
                side_reasons.append(str(side_obj["reason"]))

    count = len(valid_gfri)
    if count == 0:
        return {
            "side": side_label,
            "shock": obs_key,
            "direction": obs_key,
            "status": "UNKNOWN",
            "confirmation_status": "UNKNOWN",
            "valid_sample_count": 0,
            "valid_count": 0,
            "sample_count": len(ordered_obs),
            "gfri_confirmed": None,
            "gfri_fast_max": None,
            "gfri_fast_min": None,
            "gfri_latest_raw": None,
            "raw_hedge_flow_usd": None,
            "liquidity_depth_usd": None,
            "signed_ratio": None,
            "is_amplifying": None,
            "spread_ratio": None,
            "single_blip_detected": False,
            "samples_gfri": [],
            "sample_values": [],
            "sample_depths_usd": [],
            "reasons": _dedupe_reasons(side_reasons or ["no_valid_samples"]),
        }

    fast_max = max(valid_gfri)
    fast_min = min(valid_gfri)
    latest_raw = valid_gfri[-1]
    spread_ratio = (fast_max / fast_min) if fast_min > 0.0 else None

    if count < cfg.min_confirm_valid_samples:
        side_reasons.append("insufficient_confirmations")
        rep_flow = float(median(valid_flows)) if valid_flows else None
        rep_signed = float(median(valid_signed)) if valid_signed else None
        return {
            "side": side_label,
            "shock": obs_key,
            "direction": obs_key,
            "status": "DEGRADED",
            "confirmation_status": "INSUFFICIENT_CONFIRMATIONS",
            "valid_sample_count": count,
            "valid_count": count,
            "sample_count": len(ordered_obs),
            "gfri_confirmed": None,
            "gfri_fast_max": fast_max,
            "gfri_fast_min": fast_min,
            "gfri_latest_raw": latest_raw,
            "raw_hedge_flow_usd": rep_flow,
            "liquidity_depth_usd": None,
            "signed_ratio": rep_signed,
            "is_amplifying": (rep_signed > 0.0) if rep_signed is not None else None,
            "spread_ratio": spread_ratio,
            "single_blip_detected": False,
            "samples_gfri": valid_gfri,
            "sample_values": valid_gfri,
            "sample_depths_usd": valid_depths,
            "reasons": _dedupe_reasons(side_reasons),
        }

    confirmed_gfri = float(median(valid_gfri))
    confirmed_depth = float(median(valid_depths))
    confirmed_flow = float(median(valid_flows)) if valid_flows else None
    confirmed_signed = float(median(valid_signed)) if valid_signed else None
    high_spike_count = sum(1 for val in valid_gfri if val >= cfg.trigger_abs_gfri)
    trigger_blip = (
        fast_max >= cfg.trigger_abs_gfri
        and confirmed_gfri < cfg.trigger_abs_gfri
        and high_spike_count == 1
    )
    upper_limit = fast_min * cfg.max_confirm_spread_ratio
    upper_outliers = sum(1 for val in valid_gfri if val > upper_limit)
    ratio_blip = (
        spread_ratio is not None
        and spread_ratio > cfg.max_confirm_spread_ratio
        and upper_outliers == 1
        and confirmed_gfri <= upper_limit
    )
    single_blip_detected = bool(trigger_blip or ratio_blip)
    conf_status = "SINGLE_BLIP_FILTERED" if single_blip_detected else "CONFIRMED"
    if single_blip_detected:
        side_reasons.append("single_blip_filtered")

    side_status = "DEGRADED" if "DEGRADED" in sample_statuses else "VALID"
    return {
        "side": side_label,
        "shock": obs_key,
        "direction": obs_key,
        "status": side_status,
        "confirmation_status": conf_status,
        "valid_sample_count": count,
        "valid_count": count,
        "sample_count": len(ordered_obs),
        "gfri_confirmed": confirmed_gfri,
        "gfri_fast_max": fast_max,
        "gfri_fast_min": fast_min,
        "gfri_latest_raw": latest_raw,
        "raw_hedge_flow_usd": confirmed_flow,
        "liquidity_depth_usd": confirmed_depth,
        "signed_ratio": confirmed_signed,
        "is_amplifying": (confirmed_signed > 0.0) if confirmed_signed is not None else (confirmed_gfri > 0.0),
        "spread_ratio": spread_ratio,
        "single_blip_detected": single_blip_detected,
        "samples_gfri": valid_gfri,
        "sample_values": valid_gfri,
        "sample_depths_usd": valid_depths,
        "reasons": _dedupe_reasons(side_reasons),
    }


def _extract_primary_method(obs_list: list[dict[str, Any]]) -> str:
    """Resolve representative primary method across observations."""
    for obs in reversed(obs_list):
        if not isinstance(obs, dict):
            continue
        for key in ("down_1pct", "up_1pct"):
            m = (obs.get(key) or {}).get("method")
            if m in ("HEDGE_FLOW_NATIVE", "NET_GEX_LINEAR"):
                return str(m)
    return "UNKNOWN"


def confirm_cycle_observations(
    observations: list[dict[str, Any]],
    *,
    freeze_at_ms: int,
    cfg: GfriConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Aggregate pre-freeze observations into Fast + Confirmation cycle outputs."""
    cfg.flags.validate_locked()
    freeze_ms = _parse_ts_ms(freeze_at_ms)
    if freeze_ms is None:
        raise ValueError("freeze_at_ms must be a valid timestamp")

    reasons: list[str] = []
    if not isinstance(observations, list) or len(observations) == 0:
        empty_put = _confirm_side([], obs_key="down_1pct", cfg=cfg, forced_unknown_reason="no_observations")
        empty_call = _confirm_side([], obs_key="up_1pct", cfg=cfg, forced_unknown_reason="no_observations")
        return {
            "schema": SCHEMA_EVAL,
            "code_version": CODE_VERSION,
            "decision_authority": cfg.flags.decision_authority,
            "freeze_at_ms": freeze_ms,
            "data_quality": "UNKNOWN",
            "confirmation_status": "UNKNOWN",
            "reasons": ["no_observations"],
            "sample_count": 0,
            "valid_observation_count": 0,
            "confirm_window_span_ms": None,
            "shares_same_gex_snapshot": False,
            "independent_gamma_estimates": 0,
            "primary_method": "UNKNOWN",
            "comparability": {
                "methodology_version": None,
                "universe_policy": None,
                "primary_method": "UNKNOWN",
                "depth_venue": "deribit_btc_perpetual",
                "code_version": CODE_VERSION,
            },
            "timestamps": {"freeze_at_ms": freeze_ms, "oi_source_lag_hours": KNOWN_OI_SOURCE_LAG_HOURS, "oi_lag_note": KNOWN_OI_LAG_NOTE},
            "provenance": {},
            "cross_venue_ref": {},
            "research_submetrics": {},
            "sample_refs": [],
            "put": empty_put,
            "call": empty_call,
            "gfri_put_confirmed": None,
            "gfri_call_confirmed": None,
            "gfri_put_fast_max": None,
            "gfri_call_fast_max": None,
        }

    future_limit = freeze_ms + cfg.max_future_drift_ms
    ob_timestamps: list[int] = []
    indexed_obs: list[tuple[int, int, dict[str, Any]]] = []
    gex_keys: list[tuple[Any, Any]] = []
    future_violation = False

    for idx, obs in enumerate(observations):
        if not isinstance(obs, dict):
            reasons.append("invalid_observation_entry")
            continue
        ts_block = obs.get("timestamps") if isinstance(obs.get("timestamps"), dict) else {}
        ob_ts = _parse_ts_ms(ts_block.get("ob_timestamp_ms"))
        gex_ts = _parse_ts_ms(ts_block.get("gex_data_ts_ms"))
        if ob_ts is not None and ob_ts > future_limit:
            future_violation = True
            reasons.append("ob_future_timestamp_exceeds_freeze")
        if gex_ts is not None and gex_ts > future_limit:
            future_violation = True
            reasons.append("gex_future_timestamp_exceeds_freeze")
        if ob_ts is not None:
            ob_timestamps.append(ob_ts)
        indexed_obs.append((ob_ts if ob_ts is not None else -1, idx, obs))

        has_valid_side = any(
            isinstance(obs.get(k), dict) and obs[k].get("status") in ("VALID", "DEGRADED")
            for k in ("down_1pct", "up_1pct")
        )
        comp = obs.get("comparability") if isinstance(obs.get("comparability"), dict) else {}
        snap_id = comp.get("snapshot_id")
        if has_valid_side and (snap_id is not None or gex_ts is not None):
            gex_keys.append((snap_id, gex_ts))

    ordered_obs = [item[2] for item in sorted(indexed_obs, key=lambda x: (x[0], x[1]))]
    span_ms = (max(ob_timestamps) - min(ob_timestamps)) if len(ob_timestamps) >= 2 else (0 if len(ob_timestamps) == 1 else None)
    window_exceeded = span_ms is not None and span_ms > cfg.confirm_window_ms
    if window_exceeded:
        reasons.append("confirm_window_exceeded")

    distinct_gex = set(gex_keys)
    independent_gamma_estimates = len(distinct_gex)
    shares_same_gex_snapshot = len(gex_keys) >= 2 and independent_gamma_estimates == 1

    forced_reason = (
        "future_timestamp_exceeds_freeze"
        if future_violation
        else ("confirm_window_exceeded" if window_exceeded else None)
    )
    put_res = _confirm_side(ordered_obs, obs_key="down_1pct", cfg=cfg, forced_unknown_reason=forced_reason)
    call_res = _confirm_side(ordered_obs, obs_key="up_1pct", cfg=cfg, forced_unknown_reason=forced_reason)
    reasons.extend(put_res["reasons"])
    reasons.extend(call_res["reasons"])
    for obs in ordered_obs:
        if isinstance(obs, dict) and obs.get("data_quality") in ("VALID", "DEGRADED"):
            reasons.extend(obs.get("reasons") or [])

    valid_obs_list = [
        obs for obs in ordered_obs if isinstance(obs, dict) and obs.get("data_quality") in ("VALID", "DEGRADED")
    ]
    if forced_reason or put_res["status"] == "UNKNOWN" or call_res["status"] == "UNKNOWN":
        overall_quality = "UNKNOWN"
    elif (
        put_res["confirmation_status"] == "INSUFFICIENT_CONFIRMATIONS"
        and call_res["confirmation_status"] == "INSUFFICIENT_CONFIRMATIONS"
    ):
        overall_quality = "UNKNOWN"
    else:
        obs_qualities = [obs.get("data_quality") for obs in valid_obs_list]
        overall_quality = _worst_quality(put_res["status"], call_res["status"], *obs_qualities)

    side_conf_states = {put_res["confirmation_status"], call_res["confirmation_status"]}
    if overall_quality == "UNKNOWN" or side_conf_states == {"UNKNOWN"}:
        cycle_conf_status = "UNKNOWN"
    elif "INSUFFICIENT_CONFIRMATIONS" in side_conf_states:
        cycle_conf_status = "INSUFFICIENT_CONFIRMATIONS"
    elif "SINGLE_BLIP_FILTERED" in side_conf_states:
        cycle_conf_status = "SINGLE_BLIP_FILTERED"
    else:
        cycle_conf_status = "CONFIRMED"
    rep_obs = valid_obs_list[-1] if valid_obs_list else (ordered_obs[-1] if ordered_obs else {})
    rep_comp = rep_obs.get("comparability") if isinstance(rep_obs.get("comparability"), dict) else {}
    primary_method = _extract_primary_method(ordered_obs)

    sample_refs = [
        {
            "ob_timestamp_ms": (obs.get("timestamps") or {}).get("ob_timestamp_ms"),
            "ob_sha256": (obs.get("provenance") or {}).get("ob_sha256"),
            "data_quality": obs.get("data_quality"),
            "gfri_down_1pct": obs.get("gfri_down_1pct"),
            "gfri_up_1pct": obs.get("gfri_up_1pct"),
            "reasons": obs.get("reasons", []),
        }
        for obs in ordered_obs
        if isinstance(obs, dict)
    ]

    return {
        "schema": SCHEMA_EVAL,
        "code_version": CODE_VERSION,
        "decision_authority": cfg.flags.decision_authority,
        "freeze_at_ms": freeze_ms,
        "data_quality": overall_quality,
        "confirmation_status": cycle_conf_status,
        "reasons": _dedupe_reasons(reasons),
        "sample_count": len(observations),
        "valid_observation_count": len(valid_obs_list),
        "confirm_window_span_ms": span_ms,
        "shares_same_gex_snapshot": shares_same_gex_snapshot,
        "independent_gamma_estimates": independent_gamma_estimates,
        "primary_method": primary_method,
        "comparability": {
            "methodology_version": rep_comp.get("methodology_version"),
            "universe_policy": rep_comp.get("universe_policy"),
            "primary_method": primary_method,
            "depth_venue": "deribit_btc_perpetual",
            "code_version": CODE_VERSION,
        },
        "timestamps": dict(rep_obs.get("timestamps") or {"freeze_at_ms": freeze_ms}),
        "provenance": dict(rep_obs.get("provenance") or {}),
        "cross_venue_ref": dict(rep_obs.get("cross_venue_ref") or {}),
        "research_submetrics": dict(rep_obs.get("research_submetrics") or {}),
        "sample_refs": sample_refs,
        "put": put_res,
        "call": call_res,
        "gfri_put_confirmed": put_res["gfri_confirmed"],
        "gfri_call_confirmed": call_res["gfri_confirmed"],
        "gfri_put_fast_max": put_res["gfri_fast_max"],
        "gfri_call_fast_max": call_res["gfri_fast_max"],
    }
