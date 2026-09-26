"""BTC MAP v2.4 source adapters and four-row background builder.

This module is deliberately thin: all external reads go through
``DataEngine.fetch`` loaders, and the returned records keep the source clocks
and normalized values needed for later freezing.
"""

from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import re
import time
import urllib.parse
import urllib.request
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any, Callable, Iterable, Mapping

if __package__:
    from . import astra_data_engine_v23 as data_engine
else:  # pragma: no cover - direct script use
    import astra_data_engine_v23 as data_engine


SCHEMA = "btc_map_sources@2.4.0"

ETF_FLOW = "btc.etf.flow.daily.v1"
BRK_COST = "btc.brk.cost_basis.v1"
BRK_PNL = "btc.brk.realized_pnl.daily.v1"
OI_NATIVE = "binance.um.btcusdt.oi.native.v1"
FUNDING_SETTLED = "binance.um.btcusdt.funding.settled.v1"
BORROW_RATE = "cex.usdt.borrow_rate.v1"
MACRO_DXY = "macro.dxy.close.v1"
MACRO_USD_BROAD = "macro.usd.broad.close.v1"
MACRO_US10Y_NOMINAL = "macro.us10y.nominal.v1"
MACRO_US10Y_REAL = "macro.us10y.real.v1"

UTC = timezone.utc
MS_DAY = 86_400_000
BITVIEW_DAY1_EPOCH = date(2009, 1, 1)
BITVIEW_PNL_HEIGHT_KEYS = {"profit", "loss"}
BITVIEW_DEFAULT_BOUNDARY_LOOKBACK_DAYS = 20
BITVIEW_DEFAULT_MAX_HEIGHT_SPAN = 10_000
_HTTP_BUDGET: ContextVar[dict[str, Any] | None] = ContextVar("astra_map_http_budget", default=None)
_HTTP_PRODUCT: ContextVar[str | None] = ContextVar("astra_map_http_product", default=None)


class MapSourceError(ValueError):
    """Raised for invalid source payloads that should not be normalized."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class HttpResponse:
    url: str
    status: int | None
    text: str
    headers: Mapping[str, str]


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            value = html.unescape(" ".join("".join(self._cell).split()))
            self._row.append(value)
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(cell.strip() for cell in self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None


def collect_sources(engine: Any, now_ms: int, *, transport: Any = None, config: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Collect a bounded BTC MAP source sample through the data engine.

    The current registry may still mark slow products as planned. Those
    products are skipped without blocking products that are already connected.
    """

    cfg = dict(config or {})
    products = cfg.get("products")
    if not products:
        products = [
            ETF_FLOW,
            OI_NATIVE,
            FUNDING_SETTLED,
            BORROW_RATE,
            MACRO_USD_BROAD,
            MACRO_US10Y_NOMINAL,
            MACRO_US10Y_REAL,
            BRK_COST,
            BRK_PNL,
        ]
    budget = {"limit": int(cfg.get("shared_http_budget_per_collect") or 24), "used": 0, "engine": engine, "now_ms": now_ms, "wire_counts": {}}
    token = _HTTP_BUDGET.set(budget)
    try:
        records: list[dict[str, Any]] = []
        for product_id in products:
            try:
                raw_loader = _loader_for(product_id, transport=transport, config=cfg)

                def product_loader(product_id: str = product_id, raw_loader: Callable[[], Mapping[str, Any]] = raw_loader) -> Mapping[str, Any]:
                    product_token = _HTTP_PRODUCT.set(product_id)
                    try:
                        result = dict(raw_loader())
                        # Retrieval and first availability follow the completed
                        # response/normalization, not request initiation. Source
                        # observation/publication clocks remain untouched.
                        if transport is None:
                            result["retrieved_at_ms"] = _clock_ms(cfg)
                        return result
                    finally:
                        _HTTP_PRODUCT.reset(product_token)

                record = engine.fetch(
                    product_id,
                    product_loader,
                    now_ms=now_ms,
                    scope=_scope_for(product_id, cfg),
                )
                time_part = record.get("time") if isinstance(record.get("time"), Mapping) else {}
                read_now = max(
                    now_ms,
                    _as_int(time_part.get("retrieved_at_ms")) or now_ms,
                    _as_int(time_part.get("first_seen_at_ms")) or now_ms,
                )
                record = engine.read(product_id, now_ms=read_now, usage=str(cfg.get("read_usage") or "background"), scope=_scope_for(product_id, cfg))
            except data_engine.DataEngineError as exc:
                if exc.code in {"PRODUCT_NOT_CONNECTED", "UNKNOWN_PRODUCT", "FETCH_BACKOFF_ACTIVE", "FETCH_BUDGET_EXHAUSTED"}:
                    continue
                continue
            records.append(record)
        return records
    finally:
        _HTTP_BUDGET.reset(token)


def read_sources(engine: Any, now_ms: int, config: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Read cached MAP source records without issuing HTTP."""

    cfg = dict(config or {})
    products = cfg.get("products") or [
        ETF_FLOW,
        OI_NATIVE,
        FUNDING_SETTLED,
        BORROW_RATE,
        MACRO_USD_BROAD,
        MACRO_US10Y_NOMINAL,
        MACRO_US10Y_REAL,
        BRK_COST,
        BRK_PNL,
    ]
    usage = str(cfg.get("usage") or "background")
    records = []
    for product_id in products:
        try:
            records.append(engine.read(product_id, now_ms=now_ms, usage=usage, scope=_scope_for(product_id, cfg)))
        except data_engine.DataEngineError:
            continue
    return records


def build_background(
    records: list[Mapping[str, Any]],
    now_ms: int,
    *,
    current_price_usd: float | None = None,
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the four BTC MAP background rows from engine records."""

    by_product: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        product = _product_id(record)
        if product:
            by_product.setdefault(product, []).append(record)
    rows = {
        "allocation": _allocation_row(by_product.get(ETF_FLOW, []), now_ms),
        "inventory": _inventory_row(by_product.get(BRK_COST, []), by_product.get(BRK_PNL, []), now_ms, current_price_usd),
        "financing": _financing_row(
            by_product.get(OI_NATIVE, []),
            by_product.get(FUNDING_SETTLED, []),
            by_product.get(BORROW_RATE, []),
            now_ms,
        ),
        "external_conditions": _external_row(
            by_product.get(MACRO_USD_BROAD, []),
            by_product.get(MACRO_US10Y_NOMINAL, []),
            by_product.get(MACRO_US10Y_REAL, []),
            now_ms,
        ),
    }
    changed = []
    if isinstance(previous, Mapping):
        old_rows = previous.get("rows") if isinstance(previous.get("rows"), Mapping) else {}
        for name, row in rows.items():
            old = old_rows.get(name) if isinstance(old_rows, Mapping) else None
            if isinstance(old, Mapping) and old.get("metrics") != row.get("metrics"):
                changed.append(name)
    return {
        "schema": "btc_map_background@2.4.0",
        "generated_at_ms": now_ms,
        "rows": rows,
        "facts_changed_since_previous": changed,
        "interpretation_mode": "FACTS_UNTIL_MANUAL_REVIEW",
    }


def parse_etf_flow_table(source_text: str, *, cutoff_at_ms: int | None = None) -> dict[str, Any]:
    tables = _extract_tables(source_text)
    selected = _select_etf_table(tables)
    if selected is None:
        raise MapSourceError("ETF_TABLE_NOT_FOUND", "no ETF flow table with Date and Total columns was found")
    header = [_clean_header(cell) for cell in selected[0]]
    date_idx = _find_date_column(header)
    total_idx = _find_column(header, "total")
    if date_idx is None or total_idx is None:
        raise MapSourceError("ETF_HEADER_INVALID", "ETF table requires Date and Total columns")
    fund_indexes = [i for i, name in enumerate(header) if i not in {date_idx, total_idx} and name]
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    cutoff = cutoff_at_ms if isinstance(cutoff_at_ms, int) else None
    for raw_row in selected[1:]:
        cells = list(raw_row) + [""] * max(0, len(header) - len(raw_row))
        label = cells[date_idx].strip()
        parsed_date = parse_source_date(label)
        if parsed_date is None:
            if label:
                skipped.append({"label": label, "reason": "summary_or_non_day_row"})
            continue
        session_close = nyse_session_close_ms(parsed_date)
        if cutoff is not None and session_close > cutoff:
            skipped.append({"label": label, "reason": "after_cutoff", "session_close_ms": session_close})
            continue
        total_value, total_state = parse_flow_cell(cells[total_idx])
        funds: dict[str, Any] = {}
        missing_funds = []
        for i in fund_indexes:
            name = header[i]
            value, state = parse_flow_cell(cells[i] if i < len(cells) else "")
            funds[name] = {"value_usd_m": value, "state": state}
            if state not in {"numeric", "zero"}:
                missing_funds.append(name)
        rows.append(
            {
                "date": parsed_date.isoformat(),
                "observation_end_ms": session_close,
                "total_usd_m": total_value,
                "total_state": total_state,
                "funds": funds,
                "missing_funds": missing_funds,
                "complete_for_total": total_value is not None,
            }
        )
    rows.sort(key=lambda item: item["date"])
    metrics = etf_window_metrics(rows)
    return {
        "schema": "btc_etf_flow_normalized@2.4.0",
        "unit": "US$m",
        "fund_columns": [header[i] for i in fund_indexes],
        "rows": rows,
        "skipped_rows": skipped,
        "metrics": metrics,
    }


def parse_flow_cell(value: Any) -> tuple[float | None, str]:
    text = str(value or "").strip()
    if not text:
        return None, "blank"
    normalized = text.replace("\xa0", " ").strip()
    if normalized in {"-", "—", "–"}:
        return None, "not_disclosed"
    lowered = normalized.lower()
    if lowered in {"n/a", "na", "nan", "null"}:
        return None, "not_listed_or_na"
    negative = normalized.startswith("(") and normalized.endswith(")")
    normalized = normalized.strip("()").replace(",", "").replace("$", "").replace("US$m", "").replace("USD", "")
    try:
        amount = float(Decimal(normalized))
    except (InvalidOperation, ValueError):
        return None, "non_numeric"
    if negative:
        amount = -amount
    if amount == 0:
        return 0.0, "zero"
    return amount, "numeric"


def etf_window_metrics(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    by_day = {date.fromisoformat(str(row["date"])): row for row in rows}
    complete_days = [day for day, row in by_day.items() if row.get("complete_for_total")]
    if not complete_days:
        return {"status": "MISSING", "missing": ["no_complete_etf_days"]}
    latest = max(complete_days)
    first_needed = latest - timedelta(days=120)
    sessions = [day for day in nyse_trading_days(first_needed, latest) if day <= latest]
    if latest not in sessions:
        sessions.append(latest)
        sessions.sort()
    latest_pos = sessions.index(latest)
    last7 = sessions[max(0, latest_pos - 6) : latest_pos + 1]
    last3 = sessions[max(0, latest_pos - 2) : latest_pos + 1]
    prior60 = sessions[max(0, latest_pos - 66) : max(0, latest_pos - 6)]
    missing_3 = [day.isoformat() for day in last3 if day not in by_day or not by_day[day].get("complete_for_total")]
    missing_7 = [day.isoformat() for day in last7 if day not in by_day or not by_day[day].get("complete_for_total")]
    missing_60 = [day.isoformat() for day in prior60 if day not in by_day or not by_day[day].get("complete_for_total")]

    def total(day: date) -> float:
        return float(by_day[day]["total_usd_m"])

    f3 = sum(total(day) for day in last3) if len(last3) == 3 and not missing_3 else None
    f7 = sum(total(day) for day in last7) if len(last7) == 7 and not missing_7 else None
    q = None
    if len(prior60) == 60 and not missing_60:
        q = sum(abs(total(day)) for day in prior60) / 60.0
        if q <= 0:
            q = None
    v3 = f3 / 3.0 if f3 is not None else None
    v7 = f7 / 7.0 if f7 is not None else None
    summary = summarize_etf_flow(f3, f7, v3, v7)
    missing = []
    if missing_3:
        missing.append("etf_last3_has_missing_session")
    if missing_7:
        missing.append("etf_last7_has_missing_session")
    if missing_60 or len(prior60) < 60:
        missing.append("etf_prior60_incomplete")
    if q is None:
        missing.append("etf_u_denominator_missing_or_zero")
    return {
        "status": "OK" if not missing else "PARTIAL",
        "latest_complete_date": latest.isoformat(),
        "f3_usd_m": f3,
        "f7_usd_m": f7,
        "v3_usd_m_per_day": v3,
        "v7_usd_m_per_day": v7,
        "q_abs_usd_m_prior60": q,
        "u3": (v3 / q) if v3 is not None and q else None,
        "u7": (v7 / q) if v7 is not None and q else None,
        "summary_cn": summary,
        "windows": {
            "last3": [day.isoformat() for day in last3],
            "last7": [day.isoformat() for day in last7],
            "prior60_start": prior60[0].isoformat() if prior60 else None,
            "prior60_end": prior60[-1].isoformat() if prior60 else None,
        },
        "missing": missing,
    }


def summarize_etf_flow(f3: float | None, f7: float | None, v3: float | None, v7: float | None) -> str:
    if f3 is None or f7 is None or v3 is None or v7 is None:
        return "ETF披露窗口不完整，暂不合成速度判断。"
    if f3 > 0 and f7 > 0:
        if v3 < v7:
            return "ETF仍为净流入，但近3个交易日日均速度低于7日窗口。"
        if v3 > v7:
            return "ETF仍为净流入，近3个交易日日均速度高于7日窗口。"
        return "ETF仍为净流入，近3日与7日日均速度接近。"
    if f3 < 0 and f7 < 0:
        if v3 < v7:
            return "ETF仍为净流出，近3个交易日日均流出更快。"
        if v3 > v7:
            return "ETF仍为净流出，近3个交易日日均流出放缓。"
        return "ETF仍为净流出，近3日与7日日均速度接近。"
    if f3 == 0 or f7 == 0:
        return "ETF窗口出现零净额；只陈述零净额，不推断没有双向申赎。"
    return "ETF短窗与长窗方向分歧，暂不合成转势结论。"


def capitalized_price(entries: Iterable[tuple[float, float]]) -> dict[str, float | None]:
    quantity = Decimal("0")
    rp_num = Decimal("0")
    cp_num = Decimal("0")
    cp_den = Decimal("0")
    for qty_raw, price_raw in entries:
        qty = Decimal(str(qty_raw))
        price = Decimal(str(price_raw))
        quantity += qty
        rp_num += qty * price
        cp_num += qty * price * price
        cp_den += qty * price
    if quantity <= 0 or cp_den <= 0:
        return {"realized_price": None, "capitalized_price": None}
    return {
        "realized_price": float(rp_num / quantity),
        "capitalized_price": float(cp_num / cp_den),
    }


def realized_pnl_metrics(
    profit_points: list[Mapping[str, Any]],
    loss_points: list[Mapping[str, Any]],
    cap_points: list[Mapping[str, Any]] | None = None,
    *,
    now_ms: int | None = None,
    value_mode: str = "daily",
) -> dict[str, Any]:
    mode = value_mode.lower()
    profits = _series_daily_values(profit_points, mode=mode)
    losses = _series_daily_values(loss_points, mode=mode)
    common = sorted(set(profits).intersection(losses))[-14:]
    missing: list[str] = []
    if len(common) < 14:
        missing.append("less_than_14_complete_utc_days")
    profit_sum = sum(profits[day] for day in common)
    loss_sum = sum(losses[day] for day in common)
    denom = profit_sum + loss_sum
    loss_share = (loss_sum / denom) if denom > 0 else None
    cap_by_day = _series_daily_values(cap_points or [], mode="daily")
    cap_start = cap_by_day.get(common[0]) if common else None
    cap_end = cap_by_day.get(common[-1]) if common else None
    return {
        "mode": mode,
        "window_days": [day.isoformat() for day in common],
        "profit_14d_usd": profit_sum if common else None,
        "loss_14d_usd": loss_sum if common else None,
        "loss_share_14d": loss_share,
        "realized_cap_start_usd": cap_start,
        "realized_cap_end_usd": cap_end,
        "missing": missing,
    }


def oi_change_metrics(current_native: float, previous_native: float, current_price: float, previous_price: float) -> dict[str, Any]:
    native_change = current_native - previous_native
    current_notional = current_native * current_price
    previous_notional = previous_native * previous_price
    return {
        "native_change_btc": native_change,
        "native_change_pct": (native_change / previous_native * 100.0) if previous_native else None,
        "notional_change_usd": current_notional - previous_notional,
        "notional_change_pct": ((current_notional / previous_notional - 1.0) * 100.0) if previous_notional else None,
        "native_oi_unchanged": native_change == 0,
        "notional_is_price_dependent": True,
    }


def funding_settlement_metrics(records: list[Mapping[str, Any]], *, cutoff_at_ms: int | None = None) -> dict[str, Any]:
    actual: dict[int, Mapping[str, Any]] = {}
    predicted: list[Mapping[str, Any]] = []
    rejected = 0
    for item in records:
        if str(item.get("record_type") or "settled").lower() == "predicted":
            predicted.append(item)
            continue
        funding_time = _as_int(item.get("fundingTime") or item.get("funding_time_ms"))
        rate = _as_float(item.get("fundingRate") if "fundingRate" in item else item.get("funding_rate"))
        if funding_time is None or rate is None or (cutoff_at_ms is not None and funding_time > cutoff_at_ms):
            rejected += 1
            continue
        actual[funding_time] = item
    ordered = [actual[key] for key in sorted(actual)]
    total = sum(_as_float(item.get("fundingRate") or item.get("funding_rate")) or 0.0 for item in ordered)
    result = {
        "settled_count": len(ordered),
        "settled_funding_rate_sum": total,
        "settled_times_ms": sorted(actual),
        "predicted_count_excluded": len(predicted),
        "prediction_mixed_with_settlement": False,
        "rejected_record_count": rejected,
    }
    if cutoff_at_ms is not None:
        def window(start, end):
            rows = [item for ts, item in sorted(actual.items()) if start < ts <= end]
            return {"start_ms": start, "end_ms": end, "settled_count": len(rows),
                "settled_rate_sum": sum(float(item.get("fundingRate") if "fundingRate" in item else item["funding_rate"])
                                        for item in rows) if rows else None,
                "coverage": "OBSERVED_SETTLEMENTS_ONLY_NO_FIXED_FREQUENCY_ASSUMPTION"}
        result["window_24h"] = window(cutoff_at_ms - MS_DAY, cutoff_at_ms)
        result["previous_24h"] = window(cutoff_at_ms - 2 * MS_DAY, cutoff_at_ms - MS_DAY)
    return result


def _oi_24h_window(current_native: float | None, rows: list[Mapping[str, Any]], source_time_ms: int) -> dict[str, Any]:
    if current_native is None:
        return {"status": "MISSING", "missing": ["BINANCE_OI_CURRENT_VALUE_MISSING"]}
    parsed = []
    for row in rows:
        ts = _as_int(row.get("timestamp") or row.get("time"))
        native = _as_float(row.get("sumOpenInterest") or row.get("openInterest"))
        if ts is not None and native is not None:
            parsed.append((ts, native))
    parsed.sort()
    target = source_time_ms - MS_DAY
    eligible = [(ts, value) for ts, value in parsed if ts <= target + 5 * 60_000]
    if not eligible:
        return {"status": "MISSING", "missing": ["BINANCE_OI_24H_NATIVE_ENDPOINT_WINDOW_MISSING"], "sample_count": len(parsed)}
    ts, previous_native = min(eligible, key=lambda item: abs(item[0] - target))
    gap_ms = abs(ts - target)
    if gap_ms > 60 * 60_000:
        return {
            "status": "PARTIAL",
            "missing": ["BINANCE_OI_24H_ENDPOINT_NOT_CLOSE_TO_TARGET"],
            "sample_count": len(parsed),
            "target_ms": target,
            "matched_ms": ts,
            "gap_ms": gap_ms,
        }
    return {
        "status": "OK",
        "missing": [],
        "sample_count": len(parsed),
        "target_ms": target,
        "matched_ms": ts,
        "previous_native_btc": previous_native,
        "current_native_btc": current_native,
        "change_native_btc": current_native - previous_native,
        "change_native_pct": (current_native / previous_native - 1.0) * 100.0 if previous_native else None,
    }


def borrow_apr_from_hourly(hourly_rate_decimal: float | Decimal) -> dict[str, Any]:
    hourly = Decimal(str(hourly_rate_decimal))
    apr_decimal = hourly * Decimal(24) * Decimal(365)
    return {
        "hourly_rate_decimal": float(hourly),
        "simple_apr_decimal": float(apr_decimal),
        "simple_apr_pct": float(apr_decimal * Decimal(100)),
        "formula": "hourly_rate_decimal * 24 * 365",
    }


def macro_common_metrics(series: Mapping[str, list[Mapping[str, Any]]]) -> dict[str, Any]:
    parsed: dict[str, dict[date, float]] = {}
    for name, rows in series.items():
        parsed[name] = {
            date.fromisoformat(str(item["date"])): float(item["value"])
            for item in rows
            if item.get("value") is not None
        }
    if not parsed:
        return {"status": "MISSING", "missing": ["no_macro_series"]}
    common = sorted(set.intersection(*(set(values) for values in parsed.values())))
    common = common[-21:]
    missing = []
    if len(common) < 21:
        missing.append("less_than_21_common_observations")
    metrics: dict[str, Any] = {"common_dates": [day.isoformat() for day in common], "missing": missing}
    if common:
        latest = common[-1]
        metrics["latest_date"] = latest.isoformat()
        for name, values in parsed.items():
            current = values[latest]
            entry = {"latest": current}
            if len(common) >= 6:
                delta = current - values[common[-6]]
                entry["change_5obs"] = delta
                entry["change_5obs_bp"] = delta * 100.0 if "yield" in name or "10y" in name else None
                if not ("yield" in name or "10y" in name) and values[common[-6]] != 0:
                    entry["change_5obs_pct"] = (current / values[common[-6]] - 1) * 100
            if len(common) >= 21:
                delta = current - values[common[-21]]
                entry["change_20obs"] = delta
                entry["change_20obs_bp"] = delta * 100.0 if "yield" in name or "10y" in name else None
                if not ("yield" in name or "10y" in name) and values[common[-21]] != 0:
                    entry["change_20obs_pct"] = (current / values[common[-21]] - 1) * 100
            metrics[name] = entry
    return metrics


def nyse_trading_days(start: date, end: date) -> list[date]:
    days = []
    holidays = nyse_holidays(start.year, end.year)
    current = start
    while current <= end:
        if current.weekday() < 5 and current not in holidays:
            days.append(current)
        current += timedelta(days=1)
    return days


def nyse_holidays(start_year: int, end_year: int) -> set[date]:
    holidays: set[date] = set()
    for year in range(start_year, end_year + 1):
        fixed = [
            date(year, 1, 1),
            date(year, 6, 19),
            date(year, 7, 4),
            date(year, 12, 25),
        ]
        holidays.update(_observed(day) for day in fixed)
        holidays.add(_nth_weekday(year, 1, 0, 3))
        holidays.add(_nth_weekday(year, 2, 0, 3))
        holidays.add(_last_weekday(year, 5, 0))
        holidays.add(_nth_weekday(year, 9, 0, 1))
        holidays.add(_nth_weekday(year, 11, 3, 4))
        holidays.add(_easter(year) - timedelta(days=2))
    return holidays


def parse_source_date(value: str) -> date | None:
    text = " ".join(str(value).replace(",", " ").split())
    if not text:
        return None
    lowered = text.lower()
    if any(token in lowered for token in ("total", "average", "maximum", "minimum", "fee", "seed")):
        return None
    formats = ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y", "%m/%d/%Y", "%d/%m/%Y")
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def nyse_session_close_ms(day: date) -> int:
    return int(datetime.combine(day, dt_time(16, 0), tzinfo=_us_eastern_tz(day)).timestamp() * 1000)


def utc_day_end_ms(day: date) -> int:
    return int(datetime.combine(day + timedelta(days=1), dt_time(0, 0), tzinfo=UTC).timestamp() * 1000) - 1


def parse_fred_csv(text: str, *, series_id: str) -> dict[str, Any]:
    rows = []
    for row in csv.DictReader(text.splitlines()):
        raw_date = row.get("observation_date") or row.get("DATE") or row.get("date")
        if not raw_date:
            continue
        raw_value = row.get(series_id) or row.get("value") or row.get("VALUE")
        if raw_value in {None, ".", ""}:
            continue
        try:
            value = float(Decimal(str(raw_value)))
        except (InvalidOperation, ValueError):
            continue
        day = date.fromisoformat(raw_date)
        rows.append({"date": day.isoformat(), "value": value, "observation_end_ms": utc_day_end_ms(day)})
    if not rows:
        raise MapSourceError("FRED_EMPTY", f"{series_id} returned no observations")
    latest = rows[-1]
    return {"series_id": series_id, "observations": rows, "latest": latest}


def normalize_bitview_sample(payload: Any, *, value_mode: str | None = None, preserve_order: bool = False) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    source = payload.get("data") if isinstance(payload, Mapping) and isinstance(payload.get("data"), list) else payload
    index_name = str(payload.get("index") or payload.get("index_id") or "") if isinstance(payload, Mapping) else ""
    start_index = _as_int(payload.get("start")) if isinstance(payload, Mapping) else None
    start_day = _coerce_day(payload.get("start")) if isinstance(payload, Mapping) and index_name in {"date", "day"} else None
    if not isinstance(source, list):
        return points
    for offset, item in enumerate(source):
        raw_position: Any = None
        value: Any = None
        if isinstance(item, Mapping):
            raw_position = item.get("date") or item.get("time") or item.get("timestamp") or item.get("day1") or item.get("height") or item.get("index")
            value = item.get("value") if "value" in item else item.get("v")
        elif isinstance(item, list) and len(item) >= 2:
            raw_position, value = item[0], item[1]
        else:
            if start_index is not None:
                raw_position = start_index + offset
            elif start_day is not None:
                raw_position = start_day + timedelta(days=offset)
            else:
                continue
            value = item
        point = _bitview_sample_point(index_name, raw_position, value)
        if point:
            points.append(point)
    if not preserve_order:
        points.sort(key=lambda item: (str(item.get("date") or "9999-99-99"), int(item.get("index") or item.get("height") or 0)))
    return points


def _bitview_sample_point(index_name: str, raw_position: Any, raw_value: Any) -> dict[str, Any] | None:
    value = _json_scalar(raw_value)
    if value is None:
        return None
    point: dict[str, Any] = {"value": value}
    index_value = _as_int(raw_position)
    day = _coerce_day(raw_position)
    if day is None and index_name == "day1" and index_value is not None:
        day = BITVIEW_DAY1_EPOCH + timedelta(days=index_value)
    if day is not None:
        point["date"] = day.isoformat()
    if index_value is not None:
        if index_name == "height":
            point["height"] = index_value
        else:
            point["index"] = index_value
    return point if ("date" in point or "index" in point or "height" in point) else None


def _json_scalar(value: Any) -> Any:
    number = _as_float(value)
    if number is not None:
        return number
    if isinstance(value, str):
        text = value.strip()
        return text if text else None
    return value if value is not None and not isinstance(value, (dict, list, tuple, set)) else None


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _loader_for(product_id: str, *, transport: Any, config: Mapping[str, Any]) -> Callable[[], Mapping[str, Any]]:
    if product_id == ETF_FLOW:
        return lambda: _load_etf(transport, config)
    if product_id == BRK_COST:
        return lambda: _load_bitview_cost(transport, config)
    if product_id == BRK_PNL:
        return lambda: _load_bitview_pnl(transport, config)
    if product_id == OI_NATIVE:
        return lambda: _load_binance_oi(transport, config)
    if product_id == FUNDING_SETTLED:
        return lambda: _load_binance_funding(transport, config)
    if product_id == BORROW_RATE:
        return lambda: _load_borrow(transport, config)
    if product_id in {MACRO_USD_BROAD, MACRO_US10Y_NOMINAL, MACRO_US10Y_REAL}:
        return lambda: _load_macro(product_id, transport, config)
    raise MapSourceError("UNSUPPORTED_PRODUCT", f"no MAP v2.4 loader for {product_id}")


def _load_etf(transport: Any, config: Mapping[str, Any]) -> Mapping[str, Any]:
    url = str(config.get("etf_url") or "https://farside.co.uk/bitcoin-etf-flow-all-data/")
    response = _get(url, transport=transport, response_type="text")
    retrieved = _clock_ms(config)
    normalized = parse_etf_flow_table(response.text, cutoff_at_ms=config.get("cutoff_at_ms"))
    latest_ms = max((row["observation_end_ms"] for row in normalized["rows"]), default=retrieved)
    state = "OK" if normalized["rows"] else "MISSING"
    return {
        "values": {
            "schema": normalized["schema"],
            "source_url": response.url,
            "unit": normalized["unit"],
            "fund_columns": normalized["fund_columns"],
            "rows": normalized["rows"][-80:],
            "skipped_rows": normalized["skipped_rows"][-20:],
            "metrics": normalized["metrics"],
            "raw_response_sha256": hashlib.sha256(response.text.encode("utf-8")).hexdigest(),
            "source_limit_note": "Farside is a public third-party summary; no provider finality flag is invented.",
            "request_audit": {"actual_http_requests": 1, "max_http_requests": 1, "endpoints": [response.url]},
        },
        "observation_end_ms": latest_ms,
        "retrieved_at_ms": retrieved,
        "source_revision": hashlib.sha256(response.text.encode("utf-8")).hexdigest(),
        "source_ref": response.url,
        "is_final": None,
        "data_state": state,
        "reason_codes": normalized["metrics"].get("missing") or [],
    }


def _load_bitview_cost(transport: Any, config: Mapping[str, Any]) -> Mapping[str, Any]:
    retrieved = _clock_ms(config)
    cfg = _bitview_runtime_config(config, retrieved)
    try:
        found = _discover_bitview_series(
            transport,
            cfg,
            {
                "cp_sth": "short term holder capitalized price",
                "cp_lth": "long term holder capitalized price",
                "cp_all": "all capitalized price",
                "phase": "capital sentiment phase",
                "source_price": "bitcoin source price",
            },
        )
    except Exception as exc:
        return _blocked_values("bitview_cost_basis_blocked", retrieved, str(exc))
    found_missing = list(found["missing"])
    found_missing.extend(_filter_bitview_complete_daily_points(found["series"], retrieved))
    flat = _bitview_cost_flat_fields(found["series"])
    values = {
        "schema": "bitview_cost_basis@2.4.0",
        "series": found["series"],
        "missing": sorted(set(found_missing)),
        "request_audit": found["request_audit"],
        **flat,
    }
    latest_ms = _latest_series_ms(found["series"]) or 0
    reason_codes = list(values["missing"])
    if latest_ms == 0:
        reason_codes.append("BITVIEW_SOURCE_TIMESTAMP_MISSING")
    return {
        "values": values,
        "observation_end_ms": latest_ms,
        "retrieved_at_ms": retrieved,
        "source_revision": canonical_hash(values),
        "source_ref": "https://bitview.space/api/series/search",
        "is_final": None,
        "data_state": "OK" if not reason_codes else "PARTIAL",
        "reason_codes": reason_codes,
    }


def _load_bitview_pnl(transport: Any, config: Mapping[str, Any]) -> Mapping[str, Any]:
    retrieved = _clock_ms(config)
    cfg = _bitview_runtime_config(config, retrieved)
    try:
        found = _discover_bitview_pnl_series(
            transport,
            cfg,
            retrieved,
            {
                "profit": "realized profit",
                "loss": "realized loss",
                "realized_cap": "realized cap",
            },
        )
    except Exception as exc:
        return _blocked_values("bitview_realized_pnl_blocked", retrieved, str(exc))
    found_missing = list(found["missing"])
    found_missing.extend(_filter_bitview_complete_daily_points(found["series"], retrieved))
    profit = _last_points(found["series"].get("profit", {}).get("points") or [], 15)
    loss = _last_points(found["series"].get("loss", {}).get("points") or [], 15)
    cap = _last_points(found["series"].get("realized_cap", {}).get("points") or [], 15)
    metrics = realized_pnl_metrics(profit, loss, cap, value_mode=found.get("value_mode") or "daily")
    values = {
        "schema": "bitview_realized_pnl@2.4.0",
        "series": found["series"],
        "metrics": metrics,
        "missing": sorted(set(found_missing)),
        "request_audit": found["request_audit"],
    }
    latest_ms = _latest_series_ms(found["series"]) or 0
    reason_codes = sorted(set(values["missing"] + metrics["missing"]))
    if latest_ms == 0:
        reason_codes.append("BITVIEW_SOURCE_TIMESTAMP_MISSING")
    return {
        "values": values,
        "observation_end_ms": latest_ms,
        "retrieved_at_ms": retrieved,
        "source_revision": canonical_hash(values),
        "source_ref": "https://bitview.space/api/series/search",
        "is_final": None,
        "data_state": "OK" if not reason_codes else "PARTIAL",
        "reason_codes": sorted(set(reason_codes)),
    }


def _load_binance_oi(transport: Any, config: Mapping[str, Any]) -> Mapping[str, Any]:
    base = str(config.get("binance_fapi_base") or "https://fapi.binance.com")
    requests = 1
    current = _get_json(base + "/fapi/v1/openInterest", {"symbol": "BTCUSDT"}, transport=transport)
    retrieved = _clock_ms(config)
    value = _as_float(current.get("openInterest"))
    source_time = _as_int(current.get("time"))
    reason_codes = [] if source_time is not None else ["BINANCE_OI_SOURCE_TIME_MISSING"]
    values = {
        "schema": "binance_oi_native@2.4.0",
        "symbol": current.get("symbol") or "BTCUSDT",
        "open_interest_native_btc": value,
        "current_endpoint": "/fapi/v1/openInterest",
        "history_endpoint_24h": "/futures/data/openInterestHist",
        "source_time_basis": "exchange_time" if current.get("time") is not None else "missing_source_time",
        "notional_is_price_dependent": True,
    }
    include_history = config.get("include_binance_oi_24h_sample", True)
    if include_history and source_time is not None:
        requests += 1
        hist = _get_json(
            base + "/futures/data/openInterestHist",
            {"symbol": "BTCUSDT", "period": "5m", "startTime": source_time - MS_DAY, "endTime": source_time, "limit": 288},
            transport=transport,
        )
        hist_rows = hist if isinstance(hist, list) else []
        values["history_24h_sample_count"] = len(hist_rows)
        values["oi_24h_window"] = _oi_24h_window(value, hist_rows, source_time)
        if values["oi_24h_window"].get("status") != "OK":
            reason_codes.extend(values["oi_24h_window"].get("missing") or [])
    elif include_history:
        values["history_24h_sample_count"] = None
        values["oi_24h_window"] = {"status": "MISSING", "missing": ["BINANCE_OI_24H_REQUIRES_SOURCE_TIME"]}
        reason_codes.append("BINANCE_OI_24H_REQUIRES_SOURCE_TIME")
    else:
        values["oi_24h_window"] = {"status": "NOT_REQUESTED", "missing": ["BINANCE_OI_24H_NOT_REQUESTED"]}
        reason_codes.append("BINANCE_OI_24H_NOT_REQUESTED")
    values["request_audit"] = {
        "actual_http_requests": requests,
        "max_http_requests": 2 if include_history else 1,
        "endpoints": ["/fapi/v1/openInterest"] + (["/futures/data/openInterestHist"] if requests > 1 else []),
    }
    if value is None:
        reason_codes.append("BINANCE_OI_VALUE_MISSING")
    return {
        "values": values,
        "observation_end_ms": source_time or 0,
        "retrieved_at_ms": retrieved,
        "source_revision": canonical_hash(values),
        "source_ref": base + "/fapi/v1/openInterest?symbol=BTCUSDT",
        "is_final": None,
        "data_state": "OK" if not reason_codes else "PARTIAL",
        "reason_codes": sorted(set(reason_codes)),
    }


def _load_binance_funding(transport: Any, config: Mapping[str, Any]) -> Mapping[str, Any]:
    base = str(config.get("binance_fapi_base") or "https://fapi.binance.com")
    limit = min(1000, max(1, int(config.get("funding_limit") or 100)))
    request_cutoff = _clock_ms(config)
    payload = _get_json(base + "/fapi/v1/fundingRate", {"symbol": "BTCUSDT", "limit": limit,
        "startTime": request_cutoff - 2 * MS_DAY, "endTime": request_cutoff}, transport=transport)
    retrieved = _clock_ms(config)
    rows = payload if isinstance(payload, list) else []
    metrics = funding_settlement_metrics(rows, cutoff_at_ms=request_cutoff)
    observation = max(metrics["settled_times_ms"], default=0)
    values = {
        "schema": "binance_funding_settled@2.4.0",
        "symbol": "BTCUSDT",
        "settled_records": rows,
        "metrics": metrics,
        "settlement_endpoint": "/fapi/v1/fundingRate",
        "predicted_rate_policy": "excluded_from_settled_records",
        "request_audit": {"actual_http_requests": 1, "max_http_requests": 1, "endpoints": ["/fapi/v1/fundingRate"]},
    }
    return {
        "values": values,
        "observation_end_ms": observation,
        "retrieved_at_ms": retrieved,
        "source_revision": canonical_hash(values),
        "source_ref": base + "/fapi/v1/fundingRate?symbol=BTCUSDT",
        "is_final": True,
        "data_state": "PARTIAL" if metrics["rejected_record_count"] else "OK" if metrics["settled_count"] else "MISSING",
        "reason_codes": (["BINANCE_FUNDING_INVALID_OR_FUTURE_RECORD"] if metrics["rejected_record_count"] else [])
                        + ([] if metrics["settled_count"] else ["BINANCE_FUNDING_EMPTY"]),
    }


def _load_borrow(transport: Any, config: Mapping[str, Any]) -> Mapping[str, Any]:
    retrieved = _clock_ms(config)
    sample = config.get("borrow_public_sample")
    if not isinstance(sample, Mapping):
        values = {
            "schema": "usdt_borrow_rate@2.4.0",
            "status": "blocked",
            "reason": "no_public_machine_readable_or_authorized_readonly_source_configured",
            "private_user_data_paths_not_used": [
                "/sapi/v1/margin/interestRateHistory",
                "/sapi/v1/margin/next-hourly-interest-rate",
            ],
            "request_audit": {"actual_http_requests": 0, "max_http_requests": 0, "endpoints": []},
        }
        return {
            "values": values,
            "observation_end_ms": 0,
            "retrieved_at_ms": retrieved,
            "source_revision": canonical_hash(values),
            "source_ref": "public_or_authorized_borrow_source_not_configured",
            "is_final": None,
            "data_state": "PARTIAL",
            "reason_codes": ["BORROW_SOURCE_UNAUTHORIZED_OR_UNCONFIGURED"],
        }
    hourly = _as_float(sample.get("hourly_rate_decimal"))
    values = {
        "schema": "usdt_borrow_rate@2.4.0",
        "asset": sample.get("asset") or "USDT",
        "product": sample.get("product"),
        "vip_level": sample.get("vip_level"),
        "rate": borrow_apr_from_hourly(hourly) if hourly is not None else None,
        "rate_identity": sample.get("rate_identity") or "public_sample",
        "history_day_peak_not_mean": bool(sample.get("history_day_peak")),
        "request_audit": {"actual_http_requests": 0, "max_http_requests": 0, "endpoints": [str(sample.get("source_ref") or "configured_public_borrow_sample")]},
    }
    observation = _as_int(sample.get("observation_end_ms")) or 0
    reason_codes = [] if hourly is not None else ["BORROW_RATE_MISSING"]
    if observation == 0:
        reason_codes.append("BORROW_SOURCE_TIMESTAMP_MISSING")
    return {
        "values": values,
        "observation_end_ms": observation,
        "retrieved_at_ms": retrieved,
        "source_revision": canonical_hash(values),
        "source_ref": str(sample.get("source_ref") or "configured_public_borrow_sample"),
        "is_final": None,
        "data_state": "OK" if not reason_codes else "PARTIAL",
        "reason_codes": sorted(set(reason_codes)),
    }


def _load_macro(product_id: str, transport: Any, config: Mapping[str, Any]) -> Mapping[str, Any]:
    series_id = {
        MACRO_USD_BROAD: "DTWEXBGS",
        MACRO_US10Y_NOMINAL: "DGS10",
        MACRO_US10Y_REAL: "DFII10",
    }[product_id]
    url = str(config.get("fred_csv_url", "https://fred.stlouisfed.org/graph/fredgraph.csv"))
    retrieved = _clock_ms(config)
    coed = _coerce_day(config.get("fred_coed")) or datetime.fromtimestamp(retrieved / 1000, tz=UTC).date()
    cosd = _coerce_day(config.get("fred_cosd")) or (coed - timedelta(days=90))
    response = _get(url, {"id": series_id, "cosd": cosd.isoformat(), "coed": coed.isoformat()}, transport=transport, response_type="text")
    parsed = parse_fred_csv(response.text, series_id=series_id)
    values = {
        "schema": "fred_public_daily@2.4.0",
        "series_id": series_id,
        "source_url": response.url,
        "latest": parsed["latest"],
        "observations": parsed["observations"][-80:],
        "revision_identity": "public_current_download_no_vintage_lock",
        "observation_date_not_fetch_date": True,
        "raw_response_sha256": hashlib.sha256(response.text.encode("utf-8")).hexdigest(),
        "request_audit": {"actual_http_requests": 1, "max_http_requests": 1, "endpoints": [response.url]},
    }
    return {
        "values": values,
        "observation_end_ms": int(parsed["latest"]["observation_end_ms"]),
        "retrieved_at_ms": retrieved,
        "source_revision": values["raw_response_sha256"],
        "source_ref": response.url,
        "is_final": None,
        "data_state": "OK",
        "reason_codes": [],
    }


def _discover_bitview_series(transport: Any, config: Mapping[str, Any], concepts: Mapping[str, str]) -> dict[str, Any]:
    base = str(config.get("bitview_base") or "https://bitview.space")
    series: dict[str, Any] = {}
    missing: list[str] = []
    requests = 0
    max_requests = int(config.get("bitview_max_requests") or min(64, len(concepts) * 6))
    endpoints: list[str] = []

    def checked_get_json(path: str, params: Mapping[str, Any] | None = None) -> Any:
        nonlocal requests
        if requests >= max_requests:
            raise MapSourceError("BITVIEW_REQUEST_LIMIT", f"bitview request limit reached: {max_requests}")
        requests += 1
        endpoints.append(_url_with_params(path, dict(params or {})))
        return _get_json(base + path, params, transport=transport)

    concept_keys = list(concepts)
    for key, query_value in concepts.items():
        chosen_record = None
        unsupported_record = None
        try:
            for query in _bitview_queries_for(key, query_value):
                search = checked_get_json("/api/series/search", {"q": query, "limit": int(config.get("bitview_search_limit") or 5)})
                candidates = _extract_series_candidates(search)
                if not candidates:
                    continue
                for chosen in candidates[: int(config.get("bitview_candidate_limit") or 5)]:
                    series_id = chosen.get("series_id") or chosen.get("id")
                    if not series_id:
                        continue
                    metadata = checked_get_json("/api/series/" + urllib.parse.quote(str(series_id), safe=""))
                    if not _bitview_metadata_matches(key, metadata, chosen):
                        continue
                    index = _metadata_index(metadata, key=key)
                    if not index:
                        unsupported_record = {
                            "series_id": series_id,
                            "query": query,
                            "metadata": _compact_metadata(metadata),
                            "points": [],
                            "status": "metadata_without_supported_index",
                        }
                        continue
                    params, missing_reason = _bitview_sample_params(index, config, key=key)
                    if params is None:
                        unsupported_record = {
                            "series_id": series_id,
                            "query": query,
                            "index": index,
                            "metadata": _compact_metadata(metadata),
                            "points": [],
                            "value_mode": _metadata_value_mode(metadata),
                            "status": "sample_bounds_missing",
                            "missing_reason": missing_reason or f"bitview_sample_bounds_missing:{key}",
                        }
                        continue
                    sample = checked_get_json(
                        "/api/series/" + urllib.parse.quote(str(series_id), safe="") + "/" + urllib.parse.quote(str(index), safe=""),
                        params,
                    )
                    points = normalize_bitview_sample(sample)
                    chosen_record = {
                        "series_id": series_id,
                        "query": query,
                        "index": index,
                        "metadata": _compact_metadata(metadata),
                        "sample_window": params,
                        "points": points[-20:],
                        "value_mode": _metadata_value_mode(metadata),
                    }
                    if not points:
                        missing.append(f"bitview_sample_empty:{key}")
                    break
                if chosen_record is not None:
                    break
        except MapSourceError as exc:
            if exc.code not in {"BITVIEW_REQUEST_LIMIT", "SHARED_HTTP_BUDGET_EXHAUSTED"}:
                raise
            reason = "bitview_request_limit" if exc.code == "BITVIEW_REQUEST_LIMIT" else "bitview_shared_budget_exhausted"
            missing.append(f"{reason}:{key}")
            remaining = concept_keys[concept_keys.index(key) + 1 :]
            missing.extend(f"{reason}:{item}" for item in remaining)
            break
        if chosen_record is None and unsupported_record is not None:
            chosen_record = {item_key: item_value for item_key, item_value in unsupported_record.items() if item_key != "missing_reason"}
            if unsupported_record.get("status") == "sample_bounds_missing":
                missing.append(str(unsupported_record.get("missing_reason") or f"bitview_sample_bounds_missing:{key}"))
            else:
                missing.append(f"bitview_index_missing:{key}")
        if chosen_record is None:
            missing.append(f"bitview_search_empty:{key}")
        else:
            series[key] = chosen_record
    return {
        "series": series,
        "missing": sorted(set(missing)),
        "value_mode": _combined_bitview_value_mode(series),
        "request_audit": {"actual_http_requests": requests, "max_http_requests": max_requests, "endpoints": endpoints},
    }


def _discover_bitview_pnl_series(transport: Any, config: Mapping[str, Any], retrieved_ms: int, concepts: Mapping[str, str]) -> dict[str, Any]:
    base = str(config.get("bitview_base") or "https://bitview.space")
    series: dict[str, Any] = {}
    missing: list[str] = []
    requests = 0
    max_requests = int(config.get("bitview_max_requests") or 18)
    endpoints: list[str] = []
    bridge_cache: dict[str, Any] | None = None

    def checked_get_json(path: str, params: Mapping[str, Any] | None = None) -> Any:
        nonlocal requests
        if requests >= max_requests:
            raise MapSourceError("BITVIEW_REQUEST_LIMIT", f"bitview request limit reached: {max_requests}")
        requests += 1
        endpoints.append(_url_with_params(path, dict(params or {})))
        return _get_json(base + path, params, transport=transport)

    def bridge() -> dict[str, Any]:
        nonlocal bridge_cache
        if bridge_cache is None:
            bridge_cache = _load_bitview_daily_height_bridge(checked_get_json, config, retrieved_ms)
        return bridge_cache

    concept_keys = list(concepts)
    for key, query_value in concepts.items():
        chosen_record = None
        unsupported_record = None
        try:
            for query in _bitview_queries_for(key, query_value):
                search = checked_get_json("/api/series/search", {"q": query, "limit": int(config.get("bitview_search_limit") or 5)})
                candidates = _extract_series_candidates(search)
                if not candidates:
                    continue
                for chosen in candidates[: int(config.get("bitview_candidate_limit") or 5)]:
                    series_id = chosen.get("series_id") or chosen.get("id")
                    if not series_id:
                        continue
                    metadata = checked_get_json("/api/series/" + urllib.parse.quote(str(series_id), safe=""))
                    if not _bitview_metadata_matches(key, metadata, chosen):
                        continue
                    indexes = _metadata_indexes(metadata)
                    if key in BITVIEW_PNL_HEIGHT_KEYS:
                        if "height" not in indexes:
                            unsupported_record = {
                                "series_id": series_id,
                                "query": query,
                                "metadata": _compact_metadata(metadata),
                                "points": [],
                                "status": "height_index_required",
                            }
                            continue
                        if not _bitview_pnl_height_metadata_allowed(key, metadata, chosen):
                            unsupported_record = {
                                "series_id": series_id,
                                "query": query,
                                "index": "height",
                                "metadata": _compact_metadata(metadata),
                                "points": [],
                                "status": "height_series_semantics_rejected",
                            }
                            continue
                        bridge_info = bridge()
                        bridge_missing = list(bridge_info.get("missing") or [])
                        if bridge_missing:
                            unsupported_record = {
                                "series_id": series_id,
                                "query": query,
                                "index": "height",
                                "metadata": _compact_metadata(metadata),
                                "points": [],
                                "value_mode": "daily",
                                "status": "daily_height_bridge_missing",
                                "height_bridge": bridge_info.get("audit"),
                            }
                            missing.extend(f"{item}:{key}" for item in bridge_missing)
                            missing.append(f"bitview_sample_bounds_missing:{key}")
                            continue
                        sample = checked_get_json(
                            "/api/series/" + urllib.parse.quote(str(series_id), safe="") + "/height",
                            {"start": str(bridge_info["start_height"]), "end": str(bridge_info["end_height"])},
                        )
                        height_points = normalize_bitview_sample(sample, preserve_order=True)
                        daily_points, point_missing = _bitview_height_points_to_daily(height_points, bridge_info, key=key, aggregation="sum")
                        missing.extend(point_missing)
                        chosen_record = {
                            "series_id": series_id,
                            "query": query,
                            "index": "height",
                            "metadata": _compact_metadata(metadata),
                            "sample_window": {"start": str(bridge_info["start_height"]), "end": str(bridge_info["end_height"])},
                            "points": daily_points[-20:],
                            "value_mode": "daily",
                            "height_bridge": bridge_info.get("audit"),
                            "raw_height_sample_count": len(height_points),
                        }
                        break
                    index = _metadata_index(metadata, key=key)
                    if not index:
                        unsupported_record = {
                            "series_id": series_id,
                            "query": query,
                            "metadata": _compact_metadata(metadata),
                            "points": [],
                            "status": "metadata_without_supported_index",
                        }
                        continue
                    if key == "realized_cap":
                        bridge_info = bridge()
                        bridge_missing = list(bridge_info.get("missing") or [])
                        if bridge_missing:
                            unsupported_record = {
                                "series_id": series_id,
                                "query": query,
                                "index": index,
                                "metadata": _compact_metadata(metadata),
                                "points": [],
                                "value_mode": "daily",
                                "status": "daily_height_bridge_missing",
                                "height_bridge": bridge_info.get("audit"),
                            }
                            missing.extend(f"{item}:{key}" for item in bridge_missing)
                            continue
                        cap_day = _bitview_bridge_cap_day(bridge_info)
                        if cap_day is None:
                            unsupported_record = {
                                "series_id": series_id,
                                "query": query,
                                "index": index,
                                "metadata": _compact_metadata(metadata),
                                "points": [],
                                "value_mode": _metadata_value_mode(metadata),
                                "status": "cap_window_missing",
                            }
                            missing.append(f"bitview_cap_window_missing:{key}")
                            continue
                        params, missing_reason = _bitview_cap_sample_params(index, cap_day, key=key)
                        if params is None:
                            unsupported_record = {
                                "series_id": series_id,
                                "query": query,
                                "index": index,
                                "metadata": _compact_metadata(metadata),
                                "points": [],
                                "value_mode": _metadata_value_mode(metadata),
                                "status": "sample_bounds_missing",
                                "missing_reason": missing_reason or f"bitview_sample_bounds_missing:{key}",
                            }
                            continue
                        sample = checked_get_json(
                            "/api/series/" + urllib.parse.quote(str(series_id), safe="") + "/" + urllib.parse.quote(str(index), safe=""),
                            params,
                        )
                        points = normalize_bitview_sample(sample)
                        chosen_record = {
                            "series_id": series_id,
                            "query": query,
                            "index": index,
                            "metadata": _compact_metadata(metadata),
                            "sample_window": params,
                            "points": points[-1:],
                            "value_mode": _metadata_value_mode(metadata),
                            "cap_window_basis": "bridge_complete_period_end_day",
                            "cap_day": cap_day.isoformat(),
                        }
                        if not points:
                            missing.append(f"bitview_sample_empty:{key}")
                        break
                    params, missing_reason = _bitview_sample_params(index, config, key=key)
                    if params is None and index == "height":
                        bridge_info = bridge()
                        bridge_missing = list(bridge_info.get("missing") or [])
                        if bridge_missing:
                            unsupported_record = {
                                "series_id": series_id,
                                "query": query,
                                "index": "height",
                                "metadata": _compact_metadata(metadata),
                                "points": [],
                                "value_mode": "daily",
                                "status": "daily_height_bridge_missing",
                                "height_bridge": bridge_info.get("audit"),
                            }
                            missing.extend(f"{item}:{key}" for item in bridge_missing)
                            continue
                        params = {"start": str(bridge_info["start_height"]), "end": str(bridge_info["end_height"])}
                        sample = checked_get_json(
                            "/api/series/" + urllib.parse.quote(str(series_id), safe="") + "/height",
                            params,
                        )
                        height_points = normalize_bitview_sample(sample, preserve_order=True)
                        daily_points, point_missing = _bitview_height_points_to_daily(height_points, bridge_info, key=key, aggregation="last")
                        missing.extend(point_missing)
                        chosen_record = {
                            "series_id": series_id,
                            "query": query,
                            "index": "height",
                            "metadata": _compact_metadata(metadata),
                            "sample_window": params,
                            "points": daily_points[-20:],
                            "value_mode": "daily",
                            "height_bridge": bridge_info.get("audit"),
                            "raw_height_sample_count": len(height_points),
                        }
                        break
                    if params is None:
                        unsupported_record = {
                            "series_id": series_id,
                            "query": query,
                            "index": index,
                            "metadata": _compact_metadata(metadata),
                            "points": [],
                            "value_mode": _metadata_value_mode(metadata),
                            "status": "sample_bounds_missing",
                            "missing_reason": missing_reason or f"bitview_sample_bounds_missing:{key}",
                        }
                        continue
                    sample = checked_get_json(
                        "/api/series/" + urllib.parse.quote(str(series_id), safe="") + "/" + urllib.parse.quote(str(index), safe=""),
                        params,
                    )
                    points = normalize_bitview_sample(sample)
                    chosen_record = {
                        "series_id": series_id,
                        "query": query,
                        "index": index,
                        "metadata": _compact_metadata(metadata),
                        "sample_window": params,
                        "points": points[-20:],
                        "value_mode": _metadata_value_mode(metadata),
                    }
                    if not points:
                        missing.append(f"bitview_sample_empty:{key}")
                    break
                if chosen_record is not None:
                    break
        except MapSourceError as exc:
            if exc.code not in {"BITVIEW_REQUEST_LIMIT", "SHARED_HTTP_BUDGET_EXHAUSTED"}:
                raise
            reason = "bitview_request_limit" if exc.code == "BITVIEW_REQUEST_LIMIT" else "bitview_shared_budget_exhausted"
            missing.append(f"{reason}:{key}")
            remaining = concept_keys[concept_keys.index(key) + 1 :]
            missing.extend(f"{reason}:{item}" for item in remaining)
            break
        if chosen_record is None and unsupported_record is not None:
            chosen_record = {item_key: item_value for item_key, item_value in unsupported_record.items() if item_key != "missing_reason"}
            if unsupported_record.get("status") == "sample_bounds_missing":
                missing.append(str(unsupported_record.get("missing_reason") or f"bitview_sample_bounds_missing:{key}"))
            elif unsupported_record.get("status") == "height_index_required":
                missing.append(f"bitview_height_index_missing:{key}")
            elif unsupported_record.get("status") == "height_series_semantics_rejected":
                missing.append(f"bitview_height_series_semantics_rejected:{key}")
            elif unsupported_record.get("status") == "daily_height_bridge_missing":
                missing.append(f"bitview_daily_height_bridge_missing:{key}")
            else:
                missing.append(f"bitview_index_missing:{key}")
        if chosen_record is None:
            missing.append(f"bitview_search_empty:{key}")
        else:
            series[key] = chosen_record
    return {
        "series": series,
        "missing": sorted(set(missing)),
        "value_mode": "daily",
        "request_audit": {"actual_http_requests": requests, "max_http_requests": max_requests, "endpoints": endpoints},
    }


def _bitview_pnl_height_metadata_allowed(key: str, metadata: Any, candidate: Mapping[str, Any]) -> bool:
    text = (json.dumps(metadata, ensure_ascii=False) + " " + json.dumps(candidate, ensure_ascii=False)).lower()
    if any(token in text for token in ("sum_24h", "24h", "rolling", "cumulative", "moving average", "trailing")):
        return False
    if re.search(r"\bsum\b", text):
        return False
    if key == "profit" and "profit" not in text:
        return False
    if key == "loss" and "loss" not in text:
        return False
    if not any(token in text for token in ("usd", "dollar", "dollars")):
        return False
    return _metadata_value_mode(metadata) == "daily"


def _load_bitview_daily_height_bridge(checked_get_json: Callable[[str, Mapping[str, Any] | None], Any], config: Mapping[str, Any], retrieved_ms: int) -> dict[str, Any]:
    explicit_start = "bitview_pnl_start" in config or "bitview_start" in config
    explicit_end = "bitview_pnl_end" in config or "bitview_end" in config
    start_day = _coerce_day(config.get("bitview_pnl_start") or config.get("bitview_start"))
    end_day = _coerce_day(config.get("bitview_pnl_end") or config.get("bitview_end"))
    missing: list[str] = []
    current_day = datetime.fromtimestamp(retrieved_ms / 1000, tz=UTC).date()
    if start_day is not None and end_day is not None:
        if end_day <= start_day:
            return {"missing": ["bitview_daily_bridge_bounds_invalid"], "audit": {"status": "bounds_invalid", "start": start_day.isoformat(), "end": end_day.isoformat()}}
        if end_day > current_day:
            return {"missing": ["bitview_daily_bridge_future_day"], "audit": {"status": "future_day", "start": start_day.isoformat(), "end": end_day.isoformat(), "current_utc_day": current_day.isoformat()}}
        if (end_day - start_day).days != 14:
            return {
                "missing": ["bitview_daily_bridge_window_not_14_days"],
                "audit": {"status": "window_not_14_days", "start_day": start_day.isoformat(), "end_day_exclusive": end_day.isoformat(), "window_days": (end_day - start_day).days},
            }
    elif explicit_start or explicit_end:
        return {"missing": ["bitview_daily_bridge_bounds_missing"], "audit": {"status": "bounds_missing"}}
    record, record_missing = _find_bitview_first_height_series(checked_get_json, config)
    if record is None:
        return {"missing": record_missing or ["bitview_first_height_missing"], "audit": {"status": "first_height_missing"}}
    window_source = "explicit"
    if start_day is None or end_day is None:
        default_window = _default_bitview_height_window(checked_get_json, config, current_day, record)
        if default_window.get("missing"):
            return default_window
        start_day = default_window["start_day"]
        end_day = default_window["end_day"]
        window_source = "latest_official_day1_boundary"
    boundary_end = end_day + timedelta(days=1)
    expected_days = [start_day + timedelta(days=i) for i in range((end_day - start_day).days + 1)]
    sample = checked_get_json(
        "/api/series/" + urllib.parse.quote(str(record["series_id"]), safe="") + "/" + urllib.parse.quote(str(record["index"]), safe=""),
        {"start": str((start_day - BITVIEW_DAY1_EPOCH).days), "end": str((boundary_end - BITVIEW_DAY1_EPOCH).days)},
    )
    points = normalize_bitview_sample(sample)
    by_day: dict[date, int] = {}
    for point in points:
        day = _coerce_day(point.get("date"))
        height = _as_int(point.get("value"))
        if day is None or height is None:
            continue
        if day in by_day:
            missing.append("bitview_daily_bridge_duplicate_boundary")
        by_day[day] = height
    if any(day not in by_day for day in expected_days):
        missing.append("bitview_daily_bridge_incomplete_boundaries")
    ordered = [(day, by_day.get(day)) for day in expected_days if day in by_day]
    for (_, prev_height), (_, height) in zip(ordered, ordered[1:]):
        if prev_height is None or height is None or height <= prev_height:
            missing.append("bitview_daily_bridge_non_monotonic_height")
            break
    if start_day in by_day and end_day in by_day:
        span = int(by_day[end_day]) - int(by_day[start_day])
        max_span = int(config.get("bitview_height_max_span") or BITVIEW_DEFAULT_MAX_HEIGHT_SPAN)
        if span <= 0:
            missing.append("bitview_height_span_invalid")
        if span > max_span:
            missing.append("bitview_height_span_too_large")
    if missing:
        return {
            "missing": sorted(set(missing)),
            "audit": {
                "status": "invalid_boundaries",
                "start_day": start_day.isoformat(),
                "end_day_exclusive": end_day.isoformat(),
                "expected_boundary_count": len(expected_days),
                "actual_boundary_count": len(by_day),
                "series_id": record["series_id"],
                "window_source": window_source,
            },
        }
    days = expected_days[:-1]
    max_span = int(config.get("bitview_height_max_span") or BITVIEW_DEFAULT_MAX_HEIGHT_SPAN)
    return {
        "missing": [],
        "days": days,
        "boundaries": {day: int(by_day[day]) for day in expected_days},
        "start_height": int(by_day[start_day]),
        "end_height": int(by_day[end_day]),
        "max_height_span": max_span,
        "audit": {
            "status": "OK",
            "series_id": record["series_id"],
            "index": record["index"],
            "start_day": start_day.isoformat(),
            "end_day_exclusive": end_day.isoformat(),
            "window_source": window_source,
            "current_utc_day": current_day.isoformat(),
            "effective_window_days": [day.isoformat() for day in days],
            "day_count": len(days),
            "start_height": int(by_day[start_day]),
            "end_height": int(by_day[end_day]),
            "height_span": int(by_day[end_day]) - int(by_day[start_day]),
            "max_height_span": max_span,
            "epoch": BITVIEW_DAY1_EPOCH.isoformat(),
        },
    }


def _default_bitview_height_window(
    checked_get_json: Callable[[str, Mapping[str, Any] | None], Any],
    config: Mapping[str, Any],
    current_day: date,
    record: Mapping[str, Any],
) -> dict[str, Any]:
    lookback_days = int(config.get("bitview_boundary_lookback_days") or BITVIEW_DEFAULT_BOUNDARY_LOOKBACK_DAYS)
    lookback_days = max(16, min(lookback_days, 31))
    start_day = current_day - timedelta(days=lookback_days)
    request_end = current_day + timedelta(days=1)
    sample = checked_get_json(
        "/api/series/" + urllib.parse.quote(str(record["series_id"]), safe="") + "/day1",
        {"start": str((start_day - BITVIEW_DAY1_EPOCH).days), "end": str((request_end - BITVIEW_DAY1_EPOCH).days)},
    )
    points = normalize_bitview_sample(sample)
    by_day: dict[date, int] = {}
    missing: list[str] = []
    for point in points:
        day = _coerce_day(point.get("date"))
        height = _as_int(point.get("value"))
        if day is None or height is None or day > current_day:
            continue
        if day in by_day:
            missing.append("bitview_default_bridge_duplicate_boundary")
        by_day[day] = height
    if missing:
        return {"missing": sorted(set(missing)), "audit": {"status": "default_boundaries_invalid", "reason": sorted(set(missing))}}
    if len(by_day) < 15:
        return {
            "missing": ["bitview_default_bridge_less_than_15_boundaries"],
            "audit": {"status": "default_boundaries_incomplete", "actual_boundary_count": len(by_day), "current_utc_day": current_day.isoformat()},
        }
    latest_boundary = max(by_day)
    start_boundary = latest_boundary - timedelta(days=14)
    expected = [start_boundary + timedelta(days=i) for i in range(15)]
    if any(day not in by_day for day in expected):
        return {
            "missing": ["bitview_default_bridge_missing_contiguous_14d_boundaries"],
            "audit": {
                "status": "default_boundaries_not_contiguous",
                "start_day": start_boundary.isoformat(),
                "end_day_exclusive": latest_boundary.isoformat(),
                "available_start": min(by_day).isoformat(),
                "available_end": latest_boundary.isoformat(),
            },
        }
    for day, next_day in zip(expected, expected[1:]):
        if by_day[next_day] <= by_day[day]:
            return {
                "missing": ["bitview_default_bridge_non_monotonic_height"],
                "audit": {"status": "default_boundaries_non_monotonic", "day": day.isoformat(), "next_day": next_day.isoformat()},
            }
    return {
        "missing": [],
        "start_day": start_boundary,
        "end_day": latest_boundary,
        "audit": {
            "status": "OK",
            "source": "default_recent_official_boundaries",
            "lookback_days": lookback_days,
            "current_utc_day": current_day.isoformat(),
            "effective_start_day": start_boundary.isoformat(),
            "effective_end_day_exclusive": latest_boundary.isoformat(),
            "latest_official_boundary_day": latest_boundary.isoformat(),
        },
    }


def _find_bitview_first_height_series(checked_get_json: Callable[[str, Mapping[str, Any] | None], Any], config: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    saw_first_height = False
    for query in ("first height", "first_height", "first block height"):
        search = checked_get_json("/api/series/search", {"q": query, "limit": int(config.get("bitview_search_limit") or 5)})
        for candidate in _extract_series_candidates(search)[: int(config.get("bitview_candidate_limit") or 5)]:
            series_id = candidate.get("series_id") or candidate.get("id")
            if not series_id:
                continue
            metadata = checked_get_json("/api/series/" + urllib.parse.quote(str(series_id), safe=""))
            text = (json.dumps(metadata, ensure_ascii=False) + " " + json.dumps(candidate, ensure_ascii=False)).lower()
            if not (("first" in text or "start" in text) and "height" in text):
                continue
            saw_first_height = True
            indexes = _metadata_indexes(metadata)
            if "day1" not in indexes:
                continue
            return {"series_id": series_id, "index": "day1", "metadata": _compact_metadata(metadata)}, []
    if saw_first_height:
        return None, ["bitview_first_height_day1_index_missing"]
    return None, ["bitview_first_height_search_empty"]


def _bitview_height_points_to_daily(points: list[Mapping[str, Any]], bridge_info: Mapping[str, Any], *, key: str, aggregation: str) -> tuple[list[dict[str, Any]], list[str]]:
    days = list(bridge_info.get("days") or [])
    boundaries = bridge_info.get("boundaries") if isinstance(bridge_info.get("boundaries"), Mapping) else {}
    if not days or not boundaries:
        return [], [f"bitview_height_bridge_missing:{key}"]
    start_height = _as_int(bridge_info.get("start_height"))
    end_height = _as_int(bridge_info.get("end_height"))
    max_span = _as_int(bridge_info.get("max_height_span")) or BITVIEW_DEFAULT_MAX_HEIGHT_SPAN
    if start_height is None or end_height is None or end_height <= start_height:
        return [], [f"bitview_height_span_invalid:{key}"]
    span = end_height - start_height
    if span > max_span:
        return [], [f"bitview_height_span_too_large:{key}"]
    out: dict[date, dict[str, Any]] = {
        day: {
            "date": day.isoformat(),
            "value": 0.0 if aggregation == "sum" else None,
            "height_start": int(boundaries[day]),
            "height_end": int(boundaries[day + timedelta(days=1)]),
            "sample_count": 0,
        }
        for day in days
    }
    missing: list[str] = []
    ordered_days = sorted(days)
    day_index = 0
    seen_heights: set[int] = set()
    expected_height = start_height
    for point in points:
        height = _as_int(point.get("height") or point.get("index"))
        value = _as_float(point.get("value"))
        if height is None or value is None:
            missing.append(f"bitview_height_point_invalid:{key}")
            continue
        if height in seen_heights:
            missing.append(f"bitview_height_duplicate:{key}:{height}")
            continue
        if height < start_height or height >= end_height:
            missing.append(f"bitview_height_outside_bridge:{key}")
            continue
        if height != expected_height:
            if height < expected_height:
                missing.append(f"bitview_height_unordered:{key}:{height}")
            else:
                missing.append(f"bitview_height_gap:{key}:{expected_height}")
            expected_height = height + 1
        else:
            expected_height += 1
        seen_heights.add(height)
        if value < 0:
            missing.append(f"bitview_negative_value:{key}")
            continue
        while day_index < len(ordered_days) and height >= int(boundaries[ordered_days[day_index] + timedelta(days=1)]):
            day_index += 1
        if day_index >= len(ordered_days):
            missing.append(f"bitview_height_outside_bridge:{key}")
            continue
        day = ordered_days[day_index]
        if height < int(boundaries[day]):
            missing.append(f"bitview_height_outside_bridge:{key}")
            continue
        bucket = out[day]
        if aggregation == "sum":
            bucket["value"] = float(bucket["value"] or 0.0) + value
        else:
            bucket["value"] = value
        bucket["sample_count"] = int(bucket["sample_count"]) + 1
    if len(seen_heights) != span or expected_height != end_height:
        missing.append(f"bitview_height_span_incomplete:{key}")
    for day, bucket in out.items():
        if int(bucket["sample_count"]) <= 0:
            missing.append(f"bitview_height_day_missing:{key}:{day.isoformat()}")
        if bucket["value"] is None:
            missing.append(f"bitview_height_day_value_missing:{key}:{day.isoformat()}")
    if missing:
        return [], sorted(set(missing))
    return [out[day] for day in ordered_days], []


def _blocked_values(reason: str, retrieved: int, detail: str) -> Mapping[str, Any]:
    values = {
        "schema": "source_blocked@2.4.0",
        "status": "blocked",
        "reason": reason,
        "detail": detail[:240],
        "request_audit": {"actual_http_requests": 0, "max_http_requests": 0, "endpoints": []},
    }
    return {
        "values": values,
        "observation_end_ms": 0,
        "retrieved_at_ms": retrieved,
        "source_revision": canonical_hash(values),
        "source_ref": reason,
        "is_final": None,
        "data_state": "PARTIAL",
        "reason_codes": [reason],
    }


def _allocation_row(records: list[Mapping[str, Any]], now_ms: int) -> dict[str, Any]:
    record = _latest_record(records)
    if not record:
        return _row("allocation", "ETF配置来源未接通。", {}, now_ms, ["btc.etf.flow.daily.v1"], [], "missing")
    values = _values(record)
    metrics = deepcopy(values.get("metrics") or {})
    missing = list(metrics.get("missing") or [])
    blockers = _record_blockers(record, ETF_FLOW, now_ms)
    missing.extend(blockers)
    if blockers:
        summary = "ETF配置记录存在，但当前用途资格未通过。"
        qualified_metrics = {}
    else:
        summary = metrics.get("summary_cn") or "ETF配置窗口暂无可用摘要。"
        qualified_metrics = metrics
    fact_state = "usable" if not missing else "partial"
    return _row(
        "allocation",
        summary,
        qualified_metrics | {"source_values": _compact_values(values)},
        _cutoff(record, now_ms),
        sorted(set(missing)),
        [record.get("record_id")],
        fact_state,
    )


def _inventory_row(cost_records: list[Mapping[str, Any]], pnl_records: list[Mapping[str, Any]], now_ms: int, current_price_usd: float | None) -> dict[str, Any]:
    cost = _latest_record(cost_records)
    pnl = _latest_record(pnl_records)
    metrics: dict[str, Any] = {}
    missing: list[str] = []
    ids: list[str] = []
    parts = []
    if cost:
        ids.append(str(cost.get("record_id")))
        values = _values(cost)
        metrics["cost_basis_source_values"] = _compact_values(values)
        blockers = _record_blockers(cost, BRK_COST, now_ms)
        missing.extend(blockers)
        if not blockers:
            metrics["cost_basis"] = _compact_values(values)
            parts.append("链上成本样本已读取。")
        if not blockers:
            cp_sth = _last_bitview_value(values, "cp_sth")
            source_price = _as_float(values.get("source_price_usd"))
            dates = values.get("observation_dates") or {}
            # A contract index and a nominal chain cost are not a registered
            # conversion. Only compare the provider's own aligned endpoints.
            if cp_sth and source_price and dates.get("cp_sth") == dates.get("source_price") and dates.get("cp_sth"):
                metrics["gap_sth_source_price_pct"] = source_price / cp_sth * 100.0 - 100.0
                metrics["cost_comparison_basis"] = "BITVIEW_SAME_SOURCE_SAME_DAY_ONLY"
            elif current_price_usd is not None:
                metrics["cost_comparison_gap"] = "CONTRACT_INDEX_TO_CHAIN_COST_NOT_QUALIFIED"
        missing.extend(values.get("missing") or [])
    else:
        missing.append("btc.brk.cost_basis.v1")
    if pnl:
        ids.append(str(pnl.get("record_id")))
        values = _values(pnl)
        pnl_metrics = deepcopy(values.get("metrics") or {})
        metrics["realized_pnl_source_values"] = _compact_values(values)
        blockers = _record_blockers(pnl, BRK_PNL, now_ms)
        missing.extend(blockers)
        if not blockers:
            metrics["realized_pnl"] = pnl_metrics
        share = pnl_metrics.get("loss_share_14d")
        if not blockers and share is not None:
            parts.append(f"近14个完整UTC日实现亏损占比约{share:.1%}。")
        missing.extend(values.get("missing") or pnl_metrics.get("missing") or [])
    else:
        missing.append("btc.brk.realized_pnl.daily.v1")
    summary = " ".join(parts) if parts else "库存成本与兑现未形成当前合格背景。"
    return _row("inventory", summary, metrics, max([_cutoff(r, now_ms) for r in (cost, pnl) if r] or [now_ms]), sorted(set(missing)), ids, "usable" if not missing else "partial")


def _financing_row(oi_records: list[Mapping[str, Any]], funding_records: list[Mapping[str, Any]], borrow_records: list[Mapping[str, Any]], now_ms: int) -> dict[str, Any]:
    oi = _latest_record(oi_records)
    funding = _latest_record(funding_records)
    borrow = _latest_record(borrow_records)
    metrics: dict[str, Any] = {}
    missing: list[str] = []
    ids: list[str] = []
    parts = []
    if oi:
        ids.append(str(oi.get("record_id")))
        oi_values = _compact_values(_values(oi))
        metrics["oi_source_values"] = oi_values
        blockers = _record_blockers(oi, OI_NATIVE, now_ms)
        missing.extend(blockers)
        if not blockers:
            metrics["oi"] = oi_values
        native = oi_values.get("open_interest_native_btc")
        if not blockers and native is not None:
            parts.append(f"原生OI约{native:,.2f} BTC。")
    else:
        missing.append("binance.um.btcusdt.oi.native.v1")
    if funding:
        ids.append(str(funding.get("record_id")))
        funding_values = _values(funding)
        funding_metrics = funding_settlement_metrics(funding_values.get("settled_records") or [], cutoff_at_ms=now_ms)
        if not funding_values.get("settled_records"):
            funding_metrics = deepcopy(funding_values.get("metrics") or {})
        metrics["funding_source_values"] = _compact_values(funding_values)
        blockers = _record_blockers(funding, FUNDING_SETTLED, now_ms)
        missing.extend(blockers)
        if not blockers:
            metrics["funding"] = funding_metrics
        funding_window = funding_metrics.get("window_24h") or {}
        rate_sum = funding_window.get("settled_rate_sum")
        if not blockers and rate_sum is not None:
            parts.append(f"最近24小时观察到{funding_window.get('settled_count', 0)}次实际Funding结算，费率合计{rate_sum:+.4%}。")
        elif not blockers and funding_metrics.get("settled_funding_rate_sum") is not None:
            parts.append(f"所载实际Funding结算样本合计{funding_metrics['settled_funding_rate_sum']:+.4%}，未形成24小时覆盖。")
    else:
        missing.append("binance.um.btcusdt.funding.settled.v1")
    if borrow:
        ids.append(str(borrow.get("record_id")))
        borrow_values = _compact_values(_values(borrow))
        metrics["borrow_source_values"] = borrow_values
        blockers = _record_blockers(borrow, BORROW_RATE, now_ms)
        missing.extend(blockers)
        if not blockers:
            metrics["borrow"] = borrow_values
        rate = borrow_values.get("rate") if isinstance(borrow_values, Mapping) else None
        if not blockers and isinstance(rate, Mapping) and rate.get("simple_apr_pct") is not None:
            parts.append(f"USDT借款简单APR约{rate['simple_apr_pct']:.3f}%。")
        else:
            missing.append("cex.usdt.borrow_rate.v1")
    else:
        missing.append("cex.usdt.borrow_rate.v1")
    summary = " ".join(parts) if parts else "杠杆融资来源未形成当前合格背景。"
    return _row("financing", summary, metrics, max([_cutoff(r, now_ms) for r in (oi, funding, borrow) if r] or [now_ms]), sorted(set(missing)), ids, "usable" if not missing else "partial")


def _external_row(dollar_records: list[Mapping[str, Any]], nominal_records: list[Mapping[str, Any]], real_records: list[Mapping[str, Any]], now_ms: int) -> dict[str, Any]:
    recs = {"dollar": _latest_record(dollar_records), "nominal_10y": _latest_record(nominal_records), "real_10y": _latest_record(real_records)}
    ids = [str(record.get("record_id")) for record in recs.values() if record]
    series = {}
    missing = []
    source_values: dict[str, Any] = {}
    for name, record in recs.items():
        if record:
            values = _values(record)
            source_values[name + "_source_values"] = _compact_values(values)
            blockers = _record_blockers(record, name, now_ms)
            missing.extend(blockers)
            if not blockers:
                series[name] = values.get("observations") or []
        else:
            missing.append(name)
    metrics = macro_common_metrics(series) if series else {"missing": ["no_macro_series"]}
    metrics.update(source_values)
    missing.extend(metrics.get("missing") or [])
    latest = metrics.get("latest_date")
    summary = f"外部资本条件共同观察端点为{latest}。" if latest else "外部资本条件来源未形成共同观察端点。"
    return _row("external_conditions", summary, metrics, max([_cutoff(r, now_ms) for r in recs.values() if r] or [now_ms]), sorted(set(missing)), ids, "usable" if not missing else "partial")


def _row(kind: str, summary: str, metrics: Mapping[str, Any], cutoff: int, missing: list[str], ids: list[Any], state: str) -> dict[str, Any]:
    return {
        "summary_cn": summary,
        "metrics": deepcopy(dict(metrics)),
        "cutoff_at_ms": cutoff,
        "missing": missing,
        "source_record_ids": [item for item in ids if item],
        "fact_state": state,
    }


def _extract_tables(source_text: str) -> list[list[list[str]]]:
    if "<table" in source_text.lower():
        parser = _TableParser()
        parser.feed(source_text)
        return parser.tables
    rows = list(csv.reader(source_text.splitlines()))
    return [rows] if rows else []


def _select_etf_table(tables: list[list[list[str]]]) -> list[list[str]] | None:
    for table in tables:
        if not table:
            continue
        header = [_clean_header(cell) for cell in table[0]]
        if _find_date_column(header) is not None and _find_column(header, "total") is not None:
            return table
    return None


def _clean_header(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _find_date_column(header: list[str]) -> int | None:
    for i, name in enumerate(header):
        if name in {"date", "day"} or "date" in name:
            return i
    return 0 if header else None


def _find_column(header: list[str], needle: str) -> int | None:
    for i, name in enumerate(header):
        if name == needle or needle in name:
            return i
    return None


def _series_daily_values(points: list[Mapping[str, Any]], *, mode: str) -> dict[date, float]:
    parsed = []
    for point in points:
        day = _coerce_day(point.get("date"))
        value = _as_float(point.get("value"))
        if day is not None and value is not None:
            parsed.append((day, value))
    parsed.sort()
    if mode == "daily":
        return {day: value for day, value in parsed}
    if mode == "cumulative":
        out = {}
        for (prev_day, prev), (day, value) in zip(parsed, parsed[1:]):
            out[day] = max(0.0, value - prev)
        return out
    if mode == "rolling":
        return {day: value for day, value in parsed}
    raise MapSourceError("UNKNOWN_SERIES_MODE", f"unsupported series mode {mode}")


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    current = date(year, month, 1)
    while current.weekday() != weekday:
        current += timedelta(days=1)
    return current + timedelta(days=7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    next_month = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    current = next_month - timedelta(days=1)
    while current.weekday() != weekday:
        current -= timedelta(days=1)
    return current


def _easter(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _us_eastern_tz(day: date) -> timezone:
    # US equity sessions close during daylight time from the second Sunday in
    # March through the first Sunday in November. At 16:00 local time the
    # transition-day ambiguity has already passed.
    dst_start = _nth_weekday(day.year, 3, 6, 2)
    dst_end = _nth_weekday(day.year, 11, 6, 1)
    offset = -4 if dst_start <= day < dst_end else -5
    return timezone(timedelta(hours=offset))


def _scope_for(product_id: str, config: Mapping[str, Any]) -> Mapping[str, Any]:
    if product_id in {BRK_COST, BRK_PNL}:
        return {"asset": "BTC", "source": "bitview", "sample": "limited"}
    if product_id == ETF_FLOW:
        return {"asset": "BTC", "source": "farside", "table": "historical"}
    if product_id in {OI_NATIVE, FUNDING_SETTLED}:
        return {"symbol": "BTCUSDT", "venue": "binance_um"}
    if product_id == BORROW_RATE:
        return {"asset": "USDT", "source": "public_or_authorized"}
    if product_id in {MACRO_USD_BROAD, MACRO_US10Y_NOMINAL, MACRO_US10Y_REAL}:
        return {"series": {MACRO_USD_BROAD: "DTWEXBGS", MACRO_US10Y_NOMINAL: "DGS10", MACRO_US10Y_REAL: "DFII10"}[product_id]}
    return {}


def _get_json(url: str, params: Mapping[str, Any] | None = None, *, transport: Any = None) -> Any:
    response = _get(url, params, transport=transport, response_type="json")
    try:
        return json.loads(response.text)
    except json.JSONDecodeError as exc:
        raise MapSourceError("JSON_INVALID", f"{response.url} did not return JSON") from exc


def _consume_shared_http_budget(url: str, params: Mapping[str, Any]) -> None:
    budget = _HTTP_BUDGET.get()
    if budget is None:
        return
    product_id = _HTTP_PRODUCT.get()
    wire_counts = budget.setdefault("wire_counts", {})
    previous_product_wires = 0
    if product_id and isinstance(wire_counts, dict):
        previous_product_wires = int(wire_counts.get(product_id) or 0)
    used = int(budget.get("used") or 0)
    limit = int(budget.get("limit") or 0)
    if limit >= 0 and used >= limit:
        raise MapSourceError("SHARED_HTTP_BUDGET_EXHAUSTED", f"shared source HTTP budget exhausted before {url}")
    if product_id and previous_product_wires >= 1:
        engine = budget.get("engine")
        reserve = getattr(engine, "reserve_request", None)
        if callable(reserve):
            reserve(product_id, now_ms=int(budget.get("now_ms") or 0))
    budget["used"] = used + 1
    if product_id and isinstance(wire_counts, dict):
        wire_counts[product_id] = previous_product_wires + 1
    calls = budget.setdefault("calls", [])
    if isinstance(calls, list):
        calls.append({"url": _url_with_params(url, params)})


def _get(
    url: str,
    params: Mapping[str, Any] | None = None,
    *,
    transport: Any = None,
    response_type: str = "text",
) -> HttpResponse:
    params = dict(params or {})
    _consume_shared_http_budget(url, params)
    if transport is not None:
        result = _call_transport(transport, url, params, response_type)
        if isinstance(result, HttpResponse):
            return result
        if isinstance(result, Mapping):
            text = result.get("text")
            if text is None and "json" in result:
                text = json.dumps(result["json"], ensure_ascii=False)
            elif text is None and response_type == "json":
                text = json.dumps(result, ensure_ascii=False)
            return HttpResponse(str(result.get("url") or _url_with_params(url, params)), _as_int(result.get("status")), str(text or ""), dict(result.get("headers") or {}))
        if response_type == "json":
            return HttpResponse(_url_with_params(url, params), 200, json.dumps(result, ensure_ascii=False), {})
        return HttpResponse(_url_with_params(url, params), 200, str(result), {})
    full_url = _url_with_params(url, params)
    request = urllib.request.Request(full_url, headers={"User-Agent": "xxproject-map-v24/1.0"})
    with urllib.request.urlopen(request, timeout=20) as handle:  # nosec - public market data URLs only
        body = handle.read().decode("utf-8", errors="replace")
        status = getattr(handle, "status", None)
        headers = dict(handle.headers.items())
    return HttpResponse(full_url, status, body, headers)


def _call_transport(transport: Any, url: str, params: Mapping[str, Any], response_type: str) -> Any:
    if callable(transport):
        try:
            return transport(url, params=params, response_type=response_type)
        except TypeError:
            try:
                return transport(url, params)
            except TypeError:
                return transport(_url_with_params(url, params))
    method = "get_json" if response_type == "json" and hasattr(transport, "get_json") else "get"
    func = getattr(transport, method)
    return func(url, params=params)


def _url_with_params(url: str, params: Mapping[str, Any]) -> str:
    if not params:
        return url
    return url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)


def _clock_ms(config: Mapping[str, Any]) -> int:
    clock = config.get("clock_ms")
    if callable(clock):
        return int(clock())
    return int(time.time() * 1000)


def _bitview_runtime_config(config: Mapping[str, Any], retrieved_ms: int) -> dict[str, Any]:
    cfg = dict(config)
    cfg["bitview_retrieved_ms"] = retrieved_ms
    return cfg


def _bitview_current_utc_day(config: Mapping[str, Any]) -> date:
    retrieved = _as_int(config.get("bitview_retrieved_ms"))
    if retrieved is None:
        retrieved = _clock_ms(config)
    return datetime.fromtimestamp(retrieved / 1000, tz=UTC).date()


def _extract_series_candidates(payload: Any) -> list[Mapping[str, Any]]:
    def candidate(item: Any) -> Mapping[str, Any] | None:
        if isinstance(item, str) and item.strip():
            series_id = item.strip()
            return {"id": series_id, "series_id": series_id}
        if isinstance(item, Mapping):
            out = dict(item)
            raw_id = out.get("series_id") or out.get("id") or out.get("identifier") or out.get("slug")
            if isinstance(raw_id, str) and raw_id.strip():
                out.setdefault("id", raw_id.strip())
                out.setdefault("series_id", raw_id.strip())
            return out
        return None

    source: Any = payload
    if isinstance(payload, Mapping):
        for key in ("series", "results", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                source = value
                break
    if not isinstance(source, list):
        return []
    out = []
    for item in source:
        parsed = candidate(item)
        if parsed is not None:
            out.append(parsed)
    return out


def _bitview_queries_for(key: str, fallback: str) -> list[str]:
    queries = {
        "cp_sth": ["short term holder capitalized price", "sth capitalized", "capitalized short term", "short term capitalized", "capitalized price"],
        "cp_lth": ["long term holder capitalized price", "lth capitalized", "capitalized long term", "long term capitalized", "capitalized price"],
        "cp_all": ["capitalized price", "capitalized"],
        "phase": ["capital sentiment phase", "capital sentiment", "phase"],
        "source_price": ["price", "bitcoin price", "source price"],
        "profit": ["realized profit", "profit"],
        "loss": ["realized loss", "loss"],
        "realized_cap": ["realized cap", "capitalization"],
    }.get(key, [])
    if fallback and fallback not in queries:
        queries.append(fallback)
    return queries or [fallback]


def _bitview_metadata_matches(key: str, metadata: Any, candidate: Mapping[str, Any]) -> bool:
    text = (json.dumps(metadata, ensure_ascii=False) + " " + json.dumps(candidate, ensure_ascii=False)).lower()
    token_sets = {
        "cp_sth": [["capitalized", "short"], ["capitalized", "sth"]],
        "cp_lth": [["capitalized", "long"], ["capitalized", "lth"]],
        "cp_all": [["capitalized"]],
        "phase": [["capital", "phase"], ["sentiment", "phase"]],
        "source_price": [["price"]],
        "profit": [["profit"]],
        "loss": [["loss"]],
        "realized_cap": [["realized", "cap"]],
    }.get(key, [[key]])
    return any(all(token in text for token in tokens) for tokens in token_sets)


def _metadata_index(metadata: Any, *, key: str | None = None) -> str | None:
    indexes = _metadata_indexes(metadata)
    preferred = ["day1", "date", "day"]
    if key in {"profit", "loss", "realized_cap"}:
        preferred.append("height")
    for item in preferred:
        if item in indexes:
            return item
    if key in {"profit", "loss", "realized_cap"} and indexes:
        return indexes[0]
    return None


def _metadata_indexes(metadata: Any) -> list[str]:
    if not isinstance(metadata, Mapping):
        return []
    raw = metadata.get("indexes") or metadata.get("indices") or metadata.get("supported_indexes")
    items: list[Any]
    if isinstance(raw, Mapping):
        items = list(raw.keys())
    elif isinstance(raw, list):
        items = raw
    else:
        items = [metadata.get("index")] if metadata.get("index") else []
    out = []
    for item in items:
        if isinstance(item, Mapping):
            value = item.get("id") or item.get("name") or item.get("index")
        else:
            value = item
        if value is not None:
            out.append(str(value))
    return out


def _bitview_sample_params(index: str, config: Mapping[str, Any], *, key: str) -> tuple[dict[str, str] | None, str | None]:
    if index == "day1":
        explicit_start = "bitview_start" in config
        explicit_end = "bitview_end" in config
        current_day = _bitview_current_utc_day(config)
        end_default = current_day
        start_default = end_default - timedelta(days=30)
        start_day = _coerce_day(config.get("bitview_start")) if explicit_start else start_default
        end_day = _coerce_day(config.get("bitview_end")) if explicit_end else end_default
        if start_day is None or end_day is None:
            return None, f"bitview_day1_bounds_invalid:{key}"
        start = (start_day - BITVIEW_DAY1_EPOCH).days
        end = (end_day - BITVIEW_DAY1_EPOCH).days
        if end <= start:
            end = start + 1
        return {"start": str(start), "end": str(end)}, None
    if index in {"date", "day"}:
        explicit_start = "bitview_start" in config
        explicit_end = "bitview_end" in config
        current_day = _bitview_current_utc_day(config)
        end_default = current_day
        start_default = end_default - timedelta(days=30)
        start_day = _coerce_day(config.get("bitview_start")) if explicit_start else start_default
        end_day = _coerce_day(config.get("bitview_end")) if explicit_end else end_default
        if start_day is None or end_day is None:
            return None, f"bitview_date_bounds_invalid:{key}"
        if end_day <= start_day:
            end_day = start_day + timedelta(days=1)
        return {"start": start_day.isoformat(), "end": end_day.isoformat()}, None
    if index == "height":
        start = _as_int(config.get("bitview_height_start"))
        end = _as_int(config.get("bitview_height_end"))
        if start is None or end is None or end <= start:
            return None, f"bitview_sample_bounds_missing:{key}"
        return {"start": str(start), "end": str(end)}, None
    start = _as_int(config.get("bitview_index_start"))
    end = _as_int(config.get("bitview_index_end"))
    if start is None or end is None or end <= start:
        return None, f"bitview_sample_bounds_missing:{key}"
    return {"start": str(start), "end": str(end)}, None


def _bitview_bridge_cap_day(bridge_info: Mapping[str, Any]) -> date | None:
    days = list(bridge_info.get("days") or [])
    if not days:
        return None
    day = days[-1]
    return day if isinstance(day, date) else _coerce_day(day)


def _bitview_cap_sample_params(index: str, cap_day: date, *, key: str) -> tuple[dict[str, str] | None, str | None]:
    cap_end = cap_day + timedelta(days=1)
    if index == "day1":
        return {"start": str((cap_day - BITVIEW_DAY1_EPOCH).days), "end": str((cap_end - BITVIEW_DAY1_EPOCH).days)}, None
    if index in {"date", "day"}:
        return {"start": cap_day.isoformat(), "end": cap_end.isoformat()}, None
    return None, f"bitview_cap_index_unsupported:{key}"


def _combined_bitview_value_mode(series: Mapping[str, Any]) -> str:
    modes = {str(item.get("value_mode") or "daily") for item in series.values() if isinstance(item, Mapping)}
    if len(modes) == 1:
        return next(iter(modes))
    if "cumulative" in modes:
        return "cumulative"
    if "rolling" in modes:
        return "rolling"
    return "daily"


def _metadata_value_mode(metadata: Any) -> str:
    text = json.dumps(metadata, ensure_ascii=False).lower() if isinstance(metadata, Mapping) else ""
    if "cumulative" in text:
        return "cumulative"
    if "rolling" in text:
        return "rolling"
    return "daily"


def _compact_metadata(metadata: Any) -> Any:
    if not isinstance(metadata, Mapping):
        return metadata
    keep = {}
    for key in ("id", "series_id", "name", "title", "description", "unit", "indexes", "index", "frequency", "type"):
        if key in metadata:
            keep[key] = metadata[key]
    return keep


def _last_points(points: list[Mapping[str, Any]], n: int) -> list[Mapping[str, Any]]:
    return list(points)[-n:]


def _filter_bitview_complete_daily_points(series: Mapping[str, Any], retrieved_ms: int) -> list[str]:
    current_day = datetime.fromtimestamp(retrieved_ms / 1000, tz=UTC).date()
    missing: list[str] = []
    for key, item in series.items():
        if not isinstance(item, dict):
            continue
        index = str(item.get("index") or "")
        if index not in {"day1", "date", "day"}:
            continue
        kept = []
        filtered = []
        for point in item.get("points") or []:
            day = _coerce_day(point.get("date"))
            if day is not None and day >= current_day:
                filtered.append(day.isoformat())
                continue
            kept.append(point)
        if filtered:
            item["points"] = kept
            item["incomplete_utc_days_filtered"] = sorted(set(filtered))
            item["complete_utc_day_cutoff_exclusive"] = current_day.isoformat()
            missing.append(f"bitview_incomplete_utc_day_filtered:{key}")
    return missing


def _latest_series_ms(series: Mapping[str, Any]) -> int | None:
    latest: int | None = None
    for item in series.values():
        if not isinstance(item, Mapping):
            continue
        for point in item.get("points") or []:
            day = _coerce_day(point.get("date"))
            if day is not None:
                latest = max(latest or 0, utc_day_end_ms(day))
    return latest


def _coerce_day(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.astimezone(UTC).date()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        raw = int(value)
        if raw > 10_000_000_000:
            return datetime.fromtimestamp(raw / 1000, tz=UTC).date()
        if raw > 10_000_000:
            return datetime.fromtimestamp(raw, tz=UTC).date()
    if isinstance(value, str):
        text = value.strip()
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return parse_source_date(text)
    return None


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _product_id(record: Mapping[str, Any]) -> str | None:
    identity = record.get("identity") if isinstance(record.get("identity"), Mapping) else {}
    product = identity.get("product_id")
    return str(product) if product else None


def _values(record: Mapping[str, Any]) -> dict[str, Any]:
    content = record.get("content") if isinstance(record.get("content"), Mapping) else {}
    values = content.get("values") if isinstance(content.get("values"), Mapping) else {}
    return deepcopy(dict(values))


def _latest_record(records: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    if not records:
        return None
    return max(records, key=lambda item: item.get("time", {}).get("observation_end_ms") if isinstance(item.get("time"), Mapping) else -1)


def _cutoff(record: Mapping[str, Any] | None, default: int) -> int:
    if not record:
        return default
    time_part = record.get("time") if isinstance(record.get("time"), Mapping) else {}
    return _as_int(time_part.get("observation_end_ms")) or default


def _record_ok(record: Mapping[str, Any]) -> bool:
    decision = record.get("usage_decision")
    if isinstance(decision, Mapping):
        return bool(decision.get("can_use"))
    quality = record.get("quality") if isinstance(record.get("quality"), Mapping) else {}
    return str(quality.get("data_state") or "").upper() == "OK"


def _record_blockers(record: Mapping[str, Any], label: str, now_ms: int | None = None) -> list[str]:
    blockers: list[str] = []
    decision = record.get("usage_decision")
    if isinstance(decision, Mapping) and not decision.get("can_use"):
        status = str(decision.get("status") or "USAGE_REJECTED")
        blockers.append(f"{label}:usage:{status}")
        blockers.extend(f"{label}:usage_reason:{item}" for item in decision.get("reason_codes") or [])
    quality = record.get("quality") if isinstance(record.get("quality"), Mapping) else {}
    state = str(quality.get("data_state") or "MISSING").upper()
    if state != "OK":
        blockers.append(f"{label}:quality:{state}")
        blockers.extend(f"{label}:quality_reason:{item}" for item in quality.get("reason_codes") or [])
    if now_ms is not None:
        time_part = record.get("time") if isinstance(record.get("time"), Mapping) else {}
        observation = _as_int(time_part.get("observation_end_ms"))
        first_seen = _as_int(time_part.get("first_seen_at_ms"))
        if observation is not None and observation > now_ms:
            blockers.append(f"{label}:time:OBSERVATION_AFTER_CUTOFF")
        if first_seen is not None and first_seen > now_ms:
            blockers.append(f"{label}:time:FIRST_SEEN_AFTER_CUTOFF")
    return sorted(set(blockers))


def _compact_values(values: Mapping[str, Any]) -> dict[str, Any]:
    out = deepcopy(dict(values))
    if isinstance(out.get("rows"), list) and len(out["rows"]) > 10:
        out["rows"] = out["rows"][-10:]
        out["rows_truncated_to_last"] = 10
    for key in ("observations", "settled_records"):
        if isinstance(out.get(key), list) and len(out[key]) > 10:
            out[key] = out[key][-10:]
            out[key + "_truncated_to_last"] = 10
    return out


def _last_bitview_value(values: Mapping[str, Any], key: str) -> float | None:
    series = values.get("series") if isinstance(values.get("series"), Mapping) else {}
    item = series.get(key) if isinstance(series.get(key), Mapping) else {}
    points = item.get("points") if isinstance(item.get("points"), list) else []
    if not points:
        return None
    return _as_float(points[-1].get("value"))


def _bitview_cost_flat_fields(series: Mapping[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "cp_sth_usd": _latest_point_value(series, "cp_sth"),
        "cp_lth_usd": _latest_point_value(series, "cp_lth"),
        "cp_all_usd": _latest_point_value(series, "cp_all"),
        "source_phase": _latest_point_value(series, "phase"),
        "source_price_usd": _latest_point_value(series, "source_price"),
        "price_basis": "Bitview source series; no local CP/RP substitution",
    }
    dates = [_latest_point_date(series, key) for key in ("cp_sth", "cp_lth", "cp_all", "phase", "source_price")]
    fields["observation_dates"] = {key: _latest_point_date(series, key) for key in ("cp_sth", "cp_lth", "cp_all", "phase", "source_price")}
    fields["latest_observation_date"] = max((day for day in dates if day), default=None)
    return fields


def _latest_point_value(series: Mapping[str, Any], key: str) -> Any:
    item = series.get(key) if isinstance(series.get(key), Mapping) else {}
    points = item.get("points") if isinstance(item.get("points"), list) else []
    if not points:
        return None
    return points[-1].get("value")


def _latest_point_date(series: Mapping[str, Any], key: str) -> str | None:
    item = series.get(key) if isinstance(series.get(key), Mapping) else {}
    points = item.get("points") if isinstance(item.get("points"), list) else []
    if not points:
        return None
    value = points[-1].get("date")
    return str(value) if value is not None else None
