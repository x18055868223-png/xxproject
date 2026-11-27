"""Shared universal evidence time-qualification & provenance contract (`evidence_qual@1`).

Pure stdlib helpers unifying identity, event/completion/availability/consumption clocks,
comparability, staleness, anti-lookahead exclusion, and operational capability diagnosis
across spot active micro-flow, live 5m Pin/ARD structure, and realized volatility channels.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import math
import re
from typing import Any

SCHEMA = "evidence_qual@1"

QUAL_ALIGNED = "OBSERVED_ALIGNED"
QUAL_UNALIGNED = "OBSERVED_UNALIGNED"
QUAL_STALE = "STALE"
QUAL_UNKNOWN = "UNKNOWN"

CAP_ACTIVE = "QUALIFIED_ACTIVE"
CAP_NO_EVENT = "QUALIFIED_NO_EVENT"
CAP_POSITION_ONLY = "DEGRADED_POSITION_ONLY"
CAP_WARMING_UP = "WARMING_UP"
CAP_BLIND_UNALIGNED = "BLIND_UNALIGNED"
CAP_BLIND_STALE = "BLIND_STALE"
CAP_BLIND_MISSING = "BLIND_MISSING"

DEFAULT_FLOW_MAX_AGE_MS = 60 * 60_000
DEFAULT_PIN_MAX_AGE_MS = 20 * 60_000
DEFAULT_RV_BAR_MAX_AGE_MS = 90 * 60_000
DEFAULT_RV_COMPUTE_MAX_AGE_MS = 20 * 60_000

PIN_SOURCE = "deribit_public/get_book_summary_by_currency"
PIN_METHOD = "astra_expiry_gamma_v1@1.0.0:profile.expected_move_max_unsigned_gamma"
RV_SOURCE = "deribit_public/get_tradingview_chart_data"
RV_INSTRUMENT = "BTC-PERPETUAL"
RV_BAR = "1h close-to-close"

SPOT_END_CLOCK_KEYS = (
    "end_ts_ms",
    "complete_ts_ms",
    "window_end_ms",
    "end_ms",
    "window_end_time",
    "last_closed_bar_time",
)
SPOT_START_CLOCK_KEYS = ("window_start_ms", "first_ts_ms")
SPOT_DEGRADED_FLAGS = (
    "gap",
    "truncated",
    "window_truncated",
    "cvd_degraded",
    "backfill_mixed",
    "window_backfill",
    "out_of_order",
    "source_conflict",
    "complete_ts_synthetic",
)
_INVALID_TEXT_TOKENS = frozenset({"unknown", "none", "null", "n/a", "na", "unset"})


def is_clock(value: Any) -> bool:
    return type(value) is int and value > 0


def finite_num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped if stripped else None


def is_valid_provenance_token(value: Any) -> bool:
    txt = clean_text(value)
    return txt is not None and txt.lower() not in _INVALID_TEXT_TOKENS


def extract_clock_alias(
    mapping: Mapping[str, Any] | None,
    keys: Sequence[str],
    *,
    label_cn: str,
) -> tuple[int | None, str | None]:
    """Extract a single verified positive-int clock across aliases; reject conflicts."""
    if not isinstance(mapping, Mapping):
        return None, "missing"
    found: dict[str, int] = {}
    for key in keys:
        if key not in mapping or mapping[key] is None:
            continue
        val = mapping[key]
        if type(val) is not int or val <= 0:
            return None, f"{label_cn}({key})无效"
        found[key] = val
    if not found:
        return None, "missing"
    unique = set(found.values())
    if len(unique) > 1:
        return None, f"单窗存在互相冲突的{label_cn}"
    return next(iter(unique)), None


def extract_spot_end_clock(window: Mapping[str, Any] | None) -> tuple[int | None, str | None]:
    return extract_clock_alias(window, SPOT_END_CLOCK_KEYS, label_cn="窗口完成时钟")


def extract_spot_start_clock(window: Mapping[str, Any] | None) -> tuple[int | None, str | None]:
    return extract_clock_alias(window, SPOT_START_CLOCK_KEYS, label_cn="窗口起始时钟")


def _flag_triggered(scope: Mapping[str, Any], flag: str) -> bool:
    val = scope.get(flag)
    if flag == "gap":
        return val is True
    return val is True or (isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0) or (
        isinstance(val, str) and bool(val.strip())
    )


def qualify_spot_window(
    window: Mapping[str, Any] | None,
    *,
    cutoff_ms: int | None = None,
    max_age_ms: int = DEFAULT_FLOW_MAX_AGE_MS,
    expected_symbol: str = "BTCUSDT",
    require_provenance: bool = True,
    extra_scopes: Sequence[Mapping[str, Any]] = (),
    window_name: str = "spot_window",
) -> dict[str, Any]:
    """Qualify one spot micro-flow window under universal identity/clock/freshness rules."""
    if not isinstance(window, Mapping):
        return {
            "schema": SCHEMA,
            "channel": "spot_flow",
            "status": QUAL_UNKNOWN,
            "capability": CAP_BLIND_MISSING,
            "future_excluded": False,
            "reason_codes": ["missing_window"],
            "reason": f"缺少{window_name}现货窗口",
            "identity": {"window_name": window_name, "symbol": None, "source": None, "unit": None},
            "clock": {"start_ms": None, "end_ms": None, "available_at_ms": None, "consume_at_ms": cutoff_ms},
        }

    raw_avail = window.get("available_at_ms")
    if cutoff_ms is not None:
        future_raw = (
            any(type(window.get(k)) is int and window[k] > cutoff_ms for k in SPOT_END_CLOCK_KEYS)
            or any(type(window.get(k)) is int and window[k] > cutoff_ms for k in SPOT_START_CLOCK_KEYS)
            or (type(raw_avail) is int and raw_avail > cutoff_ms)
        )
        if future_raw:
            return {
                "schema": SCHEMA,
                "channel": "spot_flow",
                "status": QUAL_UNKNOWN,
                "capability": CAP_BLIND_MISSING,
                "future_excluded": True,
                "reason_codes": ["future_clock_excluded"],
                "reason": f"{window_name}完成、起始或可得时钟晚于截止（排除未来数据）",
                "identity": {
                    "window_name": window_name,
                    "symbol": clean_text(window.get("symbol")),
                    "source": clean_text(window.get("source")),
                    "unit": clean_text(window.get("cvd_unit")),
                },
                "clock": {"start_ms": None, "end_ms": None, "available_at_ms": None, "consume_at_ms": cutoff_ms},
            }

    ready = window.get("data_ready")
    unit = window.get("cvd_unit")
    cvd = finite_num(window.get("cvd_sum"))
    ret = finite_num(window.get("momentum_return_pct"))
    if ret is None:
        ret = finite_num(window.get("price_return_pct"))

    source = clean_text(window.get("source"))
    symbol = clean_text(window.get("symbol"))
    end_ms, end_err = extract_spot_end_clock(window)
    start_ms, start_err = extract_spot_start_clock(window)
    avail = raw_avail if is_clock(raw_avail) else None

    if ready is not True or unit != "BTC":
        cap = CAP_WARMING_UP if ready is False else CAP_BLIND_MISSING
        return {
            "schema": SCHEMA,
            "channel": "spot_flow",
            "status": QUAL_UNKNOWN,
            "capability": cap,
            "future_excluded": False,
            "reason_codes": ["not_ready" if ready is False else "invalid_readiness_or_unit"],
            "reason": f"{window_name}未就绪或单位非BTC",
            "identity": {"window_name": window_name, "symbol": symbol, "source": source, "unit": clean_text(unit)},
            "clock": {"start_ms": start_ms, "end_ms": end_ms, "available_at_ms": avail, "consume_at_ms": cutoff_ms},
        }
    if cvd is None or ret is None:
        return {
            "schema": SCHEMA,
            "channel": "spot_flow",
            "status": QUAL_UNKNOWN,
            "capability": CAP_BLIND_MISSING,
            "future_excluded": False,
            "reason_codes": ["missing_flow_values"],
            "reason": f"{window_name}净主动量或涨跌幅缺失",
            "identity": {"window_name": window_name, "symbol": symbol, "source": source, "unit": unit},
            "clock": {"start_ms": start_ms, "end_ms": end_ms, "available_at_ms": avail, "consume_at_ms": cutoff_ms},
        }

    reasons: list[str] = []
    reason_codes: list[str] = []

    if window.get("bar_complete") is False:
        reasons.append(f"{window_name}存在未完成柱(bar_complete=false)")
        reason_codes.append("bar_incomplete")
    elif require_provenance and window.get("bar_complete") is not True:
        reasons.append(f"{window_name}缺少已完成柱确认(bar_complete!=true)")
        reason_codes.append("missing_bar_complete")

    scopes = [s for s in (*extra_scopes, window) if isinstance(s, Mapping)]
    for flag in SPOT_DEGRADED_FLAGS:
        for scope in scopes:
            if _flag_triggered(scope, flag):
                reasons.append(f"{window_name}存在质量降级标记({flag})")
                reason_codes.append(f"degraded:{flag}")
                break

    if end_err == "missing" or end_ms is None:
        reasons.append(f"{window_name}缺少窗口完成时钟")
        reason_codes.append("missing_end_clock")
    elif end_err is not None:
        reasons.append(f"{window_name}{end_err}")
        reason_codes.append("invalid_end_clock")

    if start_err == "missing" or start_ms is None:
        if require_provenance:
            reasons.append(f"{window_name}缺少窗口起始时钟")
            reason_codes.append("missing_start_clock")
    elif start_err is not None:
        reasons.append(f"{window_name}{start_err}")
        reason_codes.append("invalid_start_clock")

    if start_ms is not None and end_ms is not None and start_ms >= end_ms:
        reasons.append(f"{window_name}起止时钟非法(start>=end)")
        reason_codes.append("invalid_clock_order")

    if raw_avail is not None and not is_clock(raw_avail):
        reasons.append(f"{window_name}可得时钟格式无效")
        reason_codes.append("invalid_available_at_ms")
    elif avail is not None and end_ms is not None and avail < end_ms:
        reasons.append(f"{window_name}可得时钟早于窗口完成时钟")
        reason_codes.append("available_before_end")

    if require_provenance and not is_valid_provenance_token(source):
        reasons.append(f"{window_name}缺少或无效来源(source)")
        reason_codes.append("missing_source")
    elif source is not None and not is_valid_provenance_token(source):
        reasons.append(f"{window_name}来源标识无效({source})")
        reason_codes.append("invalid_source")

    if require_provenance and not is_valid_provenance_token(symbol):
        reasons.append(f"{window_name}缺少或无效标的(symbol)")
        reason_codes.append("missing_symbol")
    elif symbol is not None and expected_symbol and symbol.upper() != expected_symbol.upper():
        reasons.append(f"{window_name}标的不匹配({symbol}!={expected_symbol})")
        reason_codes.append("symbol_mismatch")

    age_ms = (cutoff_ms - end_ms) if (cutoff_ms is not None and end_ms is not None) else None
    if reasons:
        return {
            "schema": SCHEMA,
            "channel": "spot_flow",
            "status": QUAL_UNALIGNED,
            "capability": CAP_BLIND_UNALIGNED,
            "future_excluded": False,
            "reason_codes": reason_codes,
            "reason": "；".join(reasons),
            "cvd_sum": cvd,
            "return_pct": ret,
            "end_ms": end_ms,
            "start_ms": start_ms,
            "available_at_ms": avail,
            "bar_complete": window.get("bar_complete"),
            "source": source,
            "symbol": symbol,
            "age_ms": age_ms,
            "identity": {"window_name": window_name, "symbol": symbol, "source": source, "unit": unit},
            "clock": {
                "start_ms": start_ms,
                "end_ms": end_ms,
                "available_at_ms": avail,
                "consume_at_ms": cutoff_ms,
                "max_age_ms": max_age_ms,
            },
        }

    if cutoff_ms is not None and age_ms is not None and age_ms > max_age_ms:
        stale_min = age_ms // 60_000
        limit_min = max_age_ms // 60_000
        return {
            "schema": SCHEMA,
            "channel": "spot_flow",
            "status": QUAL_STALE,
            "capability": CAP_BLIND_STALE,
            "future_excluded": False,
            "reason_codes": ["flow_stale"],
            "reason": f"{window_name}完成时钟已过期（{stale_min}分钟 > {limit_min}分钟）",
            "cvd_sum": cvd,
            "return_pct": ret,
            "end_ms": end_ms,
            "start_ms": start_ms,
            "available_at_ms": avail,
            "bar_complete": window.get("bar_complete"),
            "source": source,
            "symbol": symbol,
            "age_ms": age_ms,
            "identity": {"window_name": window_name, "symbol": symbol, "source": source, "unit": unit},
            "clock": {
                "start_ms": start_ms,
                "end_ms": end_ms,
                "available_at_ms": avail,
                "consume_at_ms": cutoff_ms,
                "max_age_ms": max_age_ms,
            },
        }

    return {
        "schema": SCHEMA,
        "channel": "spot_flow",
        "status": QUAL_ALIGNED,
        "capability": CAP_ACTIVE,
        "future_excluded": False,
        "reason_codes": [],
        "reason": "ok",
        "cvd_sum": cvd,
        "return_pct": ret,
        "end_ms": end_ms,
        "start_ms": start_ms,
        "available_at_ms": avail,
        "bar_complete": window.get("bar_complete"),
        "source": source,
        "symbol": symbol,
        "age_ms": age_ms,
        "identity": {"window_name": window_name, "symbol": symbol, "source": source, "unit": unit},
        "clock": {
            "start_ms": start_ms,
            "end_ms": end_ms,
            "available_at_ms": avail,
            "consume_at_ms": cutoff_ms,
            "max_age_ms": max_age_ms,
        },
    }


def qualify_live_pin(
    levels: Mapping[str, Any] | None,
    *,
    expiry_ms: int | None,
    cutoff_ms: int | None,
    max_age_ms: int = DEFAULT_PIN_MAX_AGE_MS,
    expected_method: str | None = PIN_METHOD,
    expected_source: str | None = PIN_SOURCE,
) -> dict[str, Any]:
    """Qualify a live 5m / archived Pin structure record with strict clock & identity checks."""
    if not isinstance(levels, Mapping):
        return {
            "schema": SCHEMA,
            "channel": "pin_structure",
            "status": QUAL_UNKNOWN,
            "capability": CAP_BLIND_MISSING,
            "future_excluded": False,
            "reason_codes": ["pin_levels_missing"],
            "reason": "缺少同到期Pin结构读数",
        }

    raw_computed = levels.get("computed_at_ms")
    if raw_computed is None:
        raw_computed = levels.get("at_ms")
    raw_avail = levels.get("available_at_ms")

    if cutoff_ms is not None and (
        (type(raw_computed) is int and raw_computed > cutoff_ms)
        or (type(raw_avail) is int and raw_avail > cutoff_ms)
    ):
        return {
            "schema": SCHEMA,
            "channel": "pin_structure",
            "status": QUAL_UNKNOWN,
            "capability": CAP_BLIND_MISSING,
            "future_excluded": True,
            "reason_codes": ["future_clock_excluded"],
            "reason": "Pin观测或可得时钟晚于消费时刻（排除未来数据）",
        }

    lev_exp = levels.get("expiry_ms")
    method = clean_text(levels.get("pin_method"))
    source = clean_text(levels.get("pin_source"))
    pin_status = clean_text(levels.get("pin_status"))
    pin = finite_num(levels.get("pin"))
    if pin is None and isinstance(levels.get("walls"), Mapping):
        pin = finite_num(levels["walls"].get("pin"))
    if pin is None and isinstance(levels.get("profile"), Mapping):
        pin = finite_num((levels["profile"].get("walls") or {}).get("pin"))

    if levels.get("compat_backfill_applied") is True:
        return {
            "schema": SCHEMA,
            "channel": "pin_structure",
            "status": QUAL_UNALIGNED,
            "capability": CAP_BLIND_UNALIGNED,
            "future_excluded": False,
            "reason_codes": ["compat_backfill_rejected"],
            "reason": "仅含历史兼容回填时钟，无原生可得时间戳",
        }

    if lev_exp is None or (is_clock(expiry_ms) and lev_exp != expiry_ms):
        return {
            "schema": SCHEMA,
            "channel": "pin_structure",
            "status": QUAL_UNALIGNED,
            "capability": CAP_BLIND_UNALIGNED,
            "future_excluded": False,
            "reason_codes": ["expiry_mismatch" if lev_exp is not None else "missing_expiry_ms"],
            "reason": "Pin到期日与合约到期不匹配或缺少expiry_ms",
        }

    if expected_method and method != expected_method:
        return {
            "schema": SCHEMA,
            "channel": "pin_structure",
            "status": QUAL_UNALIGNED,
            "capability": CAP_BLIND_UNALIGNED,
            "future_excluded": False,
            "reason_codes": ["pin_method_mismatch" if method else "missing_pin_method"],
            "reason": "Pin计算方法缺失或与同口径不一致",
        }
    if expected_source and source != expected_source:
        return {
            "schema": SCHEMA,
            "channel": "pin_structure",
            "status": QUAL_UNALIGNED,
            "capability": CAP_BLIND_UNALIGNED,
            "future_excluded": False,
            "reason_codes": ["pin_source_mismatch" if source else "missing_pin_source"],
            "reason": "Pin数据来源缺失或与同口径不一致",
        }

    if not is_clock(raw_computed):
        return {
            "schema": SCHEMA,
            "channel": "pin_structure",
            "status": QUAL_UNKNOWN,
            "capability": CAP_BLIND_MISSING,
            "future_excluded": False,
            "reason_codes": ["missing_or_invalid_computed_at_ms"],
            "reason": "缺少有效的Pin计算时钟",
        }
    if raw_avail is not None and (not is_clock(raw_avail) or raw_avail < raw_computed):
        return {
            "schema": SCHEMA,
            "channel": "pin_structure",
            "status": QUAL_UNALIGNED,
            "capability": CAP_BLIND_UNALIGNED,
            "future_excluded": False,
            "reason_codes": ["invalid_pin_available_at_ms"],
            "reason": "Pin可得时钟早于计算时钟或格式无效",
        }

    if pin_status != "ok" or pin is None or pin <= 0:
        return {
            "schema": SCHEMA,
            "channel": "pin_structure",
            "status": QUAL_UNKNOWN,
            "capability": CAP_BLIND_MISSING,
            "future_excluded": False,
            "reason_codes": [f"pin_status:{pin_status or 'missing'}"],
            "reason": f"Pin状态不可用({pin_status or 'missing'})",
        }

    age_ms = (cutoff_ms - raw_computed) if cutoff_ms is not None else levels.get("age_ms")
    is_stale = levels.get("stale") is True or (is_clock(age_ms) and age_ms > max_age_ms) or (
        type(age_ms) is int and age_ms > max_age_ms
    )
    if is_stale:
        return {
            "schema": SCHEMA,
            "channel": "pin_structure",
            "status": QUAL_STALE,
            "capability": CAP_BLIND_STALE,
            "future_excluded": False,
            "reason_codes": ["pin_stale"],
            "reason": f"Pin观测已过期（age_ms={age_ms} > {max_age_ms}）",
            "pin": pin,
            "computed_at_ms": raw_computed,
            "available_at_ms": raw_avail or raw_computed,
            "age_ms": age_ms,
        }

    return {
        "schema": SCHEMA,
        "channel": "pin_structure",
        "status": QUAL_ALIGNED,
        "capability": CAP_ACTIVE,
        "future_excluded": False,
        "reason_codes": [],
        "reason": "ok",
        "pin": pin,
        "expiry_ms": lev_exp,
        "pin_status": pin_status,
        "pin_method": method,
        "pin_source": source,
        "computed_at_ms": raw_computed,
        "available_at_ms": raw_avail or raw_computed,
        "age_ms": age_ms,
    }


def qualify_expiry_realized_vol(
    rv: Mapping[str, Any] | None,
    *,
    cutoff_ms: int | None,
    max_bar_age_ms: int = DEFAULT_RV_BAR_MAX_AGE_MS,
    max_compute_age_ms: int = DEFAULT_RV_COMPUTE_MAX_AGE_MS,
    offline_reconstructed_clock_ms: int | None = None,
) -> dict[str, Any]:
    """Qualify BTC-PERPETUAL closed-1h realized volatility strictly as-of `cutoff_ms`."""
    if not isinstance(rv, Mapping):
        return {
            "schema": SCHEMA,
            "channel": "rv24_background",
            "status": QUAL_UNKNOWN,
            "capability": CAP_BLIND_MISSING,
            "future_excluded": False,
            "reconstructed_offline": False,
            "reason_codes": ["missing_realized_vol"],
            "reason": "未提供已实现波动率容器",
        }

    last_bar = rv.get("last_bar_end_ms")
    rv_comp = rv.get("rv_computed_at_ms")
    rv_avail = rv.get("rv_available_at_ms")
    if cutoff_ms is not None and (
        (type(last_bar) is int and last_bar > cutoff_ms)
        or (type(rv_comp) is int and rv_comp > cutoff_ms)
        or (type(rv_avail) is int and rv_avail > cutoff_ms)
        or (type(offline_reconstructed_clock_ms) is int and offline_reconstructed_clock_ms > cutoff_ms)
    ):
        return {
            "schema": SCHEMA,
            "channel": "rv24_background",
            "status": QUAL_UNKNOWN,
            "capability": CAP_BLIND_MISSING,
            "future_excluded": True,
            "reconstructed_offline": False,
            "reason_codes": ["future_clock_excluded"],
            "reason": "RV末柱或计算可用时钟晚于读取截止（排除未来数据）",
        }

    status_raw = clean_text(rv.get("status"))
    if status_raw in {"fetch_failed", "duplicate_conflict", "insufficient_bars"}:
        return {
            "schema": SCHEMA,
            "channel": "rv24_background",
            "status": QUAL_UNKNOWN,
            "capability": CAP_BLIND_MISSING,
            "future_excluded": False,
            "reconstructed_offline": False,
            "reason_codes": [f"rv_{status_raw}"],
            "reason": f"RV上游采集失败或数据冲突({status_raw})",
        }

    if rv.get("cached_fallback") is True:
        return {
            "schema": SCHEMA,
            "channel": "rv24_background",
            "status": QUAL_STALE,
            "capability": CAP_BLIND_STALE,
            "future_excluded": False,
            "reconstructed_offline": False,
            "reason_codes": ["rv_cached_fallback"],
            "reason": "RV处于失败回退缓存状态，未取得最新K线",
        }

    instrument = clean_text(rv.get("symbol")) or clean_text(rv.get("instrument"))
    bar_spec = clean_text(rv.get("bar"))
    if instrument != RV_INSTRUMENT or bar_spec != RV_BAR:
        return {
            "schema": SCHEMA,
            "channel": "rv24_background",
            "status": QUAL_UNALIGNED,
            "capability": CAP_BLIND_UNALIGNED,
            "future_excluded": False,
            "reconstructed_offline": False,
            "reason_codes": ["rv_instrument_or_bar_mismatch"],
            "reason": f"RV标的非{RV_INSTRUMENT}或非1h闭合柱口径",
        }

    rv24 = finite_num(rv.get("rv_24h_pct"))
    rv7d = finite_num(rv.get("rv_7d_pct"))
    if rv24 is None or rv24 <= 0 or not is_clock(last_bar):
        return {
            "schema": SCHEMA,
            "channel": "rv24_background",
            "status": QUAL_UNKNOWN,
            "capability": CAP_BLIND_MISSING,
            "future_excluded": False,
            "reconstructed_offline": False,
            "reason_codes": ["missing_rv24_or_last_bar_end_ms"],
            "reason": "RV24数值或last_bar_end_ms缺失",
        }

    reconstructed = False
    eff_compute = rv_comp
    eff_avail = rv_avail if is_clock(rv_avail) else rv_comp
    if not is_clock(eff_compute):
        if is_clock(offline_reconstructed_clock_ms):
            reconstructed = True
            eff_compute = offline_reconstructed_clock_ms
            eff_avail = offline_reconstructed_clock_ms
        else:
            return {
                "schema": SCHEMA,
                "channel": "rv24_background",
                "status": QUAL_UNKNOWN,
                "capability": CAP_BLIND_MISSING,
                "future_excluded": False,
                "reconstructed_offline": False,
                "reason_codes": ["missing_rv_availability_clock"],
                "reason": "L4.expiry_vol缺少rv_computed_at_ms可用时间凭据",
            }

    if eff_compute < last_bar or (eff_avail is not None and eff_avail < eff_compute):
        return {
            "schema": SCHEMA,
            "channel": "rv24_background",
            "status": QUAL_UNALIGNED,
            "capability": CAP_BLIND_UNALIGNED,
            "future_excluded": False,
            "reconstructed_offline": reconstructed,
            "reason_codes": ["invalid_rv_clock_order"],
            "reason": "RV计算或可得时钟早于末根闭合柱结束时间",
        }

    if cutoff_ms is not None:
        if cutoff_ms - last_bar > max_bar_age_ms:
            return {
                "schema": SCHEMA,
                "channel": "rv24_background",
                "status": QUAL_STALE,
                "capability": CAP_BLIND_STALE,
                "future_excluded": False,
                "reconstructed_offline": reconstructed,
                "reason_codes": ["rv_bar_stale"],
                "reason": f"RV末根1h闭合柱已过期（{(cutoff_ms - last_bar) // 60_000}分钟 > {max_bar_age_ms // 60_000}分钟）",
            }
        if cutoff_ms - eff_compute > max_compute_age_ms:
            return {
                "schema": SCHEMA,
                "channel": "rv24_background",
                "status": QUAL_STALE,
                "capability": CAP_BLIND_STALE,
                "future_excluded": False,
                "reconstructed_offline": reconstructed,
                "reason_codes": ["rv_compute_stale"],
                "reason": f"RV计算时钟已过期（{(cutoff_ms - eff_compute) // 60_000}分钟 > {max_compute_age_ms // 60_000}分钟）",
            }

    return {
        "schema": SCHEMA,
        "channel": "rv24_background",
        "status": QUAL_ALIGNED,
        "capability": CAP_ACTIVE,
        "future_excluded": False,
        "reconstructed_offline": reconstructed,
        "reason_codes": [],
        "reason": "RECONSTRUCTED_OFFLINE" if reconstructed else "ok",
        "rv_24h_pct": rv24,
        "rv_7d_pct": rv7d,
        "instrument": instrument,
        "bar": bar_spec,
        "source": clean_text(rv.get("source")) or RV_SOURCE,
        "last_bar_end_ms": last_bar,
        "rv_computed_at_ms": eff_compute,
        "rv_available_at_ms": eff_avail,
    }


def summarize_channel_capabilities(
    *,
    spot_qual: Mapping[str, Any] | None = None,
    pin_qual: Mapping[str, Any] | None = None,
    rv_qual: Mapping[str, Any] | None = None,
    pin_eval_mode: str | None = None,
    spot_event_fired: bool | None = None,
    pin_event_fired: bool | None = None,
    include_rv24: bool = True,
) -> dict[str, Any]:
    """Distinguish 'qualified with zero risk event' from 'data-blind / degraded'."""

    def _entry(q: Mapping[str, Any] | None, name: str, *, mode: str | None = None, fired: bool | None = None) -> dict[str, Any]:
        if not isinstance(q, Mapping):
            return {
                "channel": name,
                "status": QUAL_UNKNOWN,
                "capability": CAP_BLIND_MISSING,
                "operational": False,
                "reason_codes": ["not_checked"],
            }
        st = str(q.get("status") or QUAL_UNKNOWN)
        cap = str(q.get("capability") or CAP_BLIND_MISSING)
        if st == QUAL_ALIGNED:
            if name == "pin_structure" and mode == "position_only":
                cap = CAP_POSITION_ONLY
            elif fired is True:
                cap = CAP_ACTIVE
            elif fired is False:
                cap = CAP_NO_EVENT
            else:
                cap = CAP_ACTIVE
        operational = st == QUAL_ALIGNED and cap in {CAP_ACTIVE, CAP_NO_EVENT, CAP_POSITION_ONLY}
        return {
            "channel": name,
            "status": st,
            "capability": cap,
            "operational": operational,
            "reason_codes": list(q.get("reason_codes") or []),
            "reason": q.get("reason"),
        }

    channels = {
        "spot_flow": _entry(spot_qual, "spot_flow", fired=spot_event_fired),
        "pin_structure": _entry(pin_qual, "pin_structure", mode=pin_eval_mode, fired=pin_event_fired),
    }
    if include_rv24 or rv_qual is not None:
        channels["rv24_background"] = _entry(rv_qual, "rv24_background")
    blind = [k for k, v in channels.items() if not v["operational"] and v["capability"] != CAP_WARMING_UP]
    degraded = [k for k, v in channels.items() if v["capability"] == CAP_POSITION_ONLY]
    return {
        "schema": f"{SCHEMA}:capability_summary",
        "channels": channels,
        "blind_channels": blind,
        "degraded_channels": degraded,
        "all_critical_operational": len(blind) == 0,
    }


DEFAULT_DEAL_QUOTE_HOLD_MS = 180_000
DEFAULT_DEAL_PAIR_SKEW_MS = 60_000
DEFAULT_DEAL_L3_MAX_AGE_MS = 5 * 60_000
_CASE_ID_RE = re.compile(r"^cf_[A-Za-z0-9_-]+$")


def canonical_case_content_sha256(case: Mapping[str, Any]) -> str:
    body = {k: v for k, v in case.items() if k != "build"}
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _case_layer_raw(case: Mapping[str, Any], ref: str) -> Mapping[str, Any] | None:
    at = (case.get("refs") or {}).get(ref)
    if not isinstance(at, Mapping):
        return None
    try:
        entry = case["layers"][at["layer"]][at["index"]]
    except (KeyError, IndexError, TypeError):
        return None
    return entry if isinstance(entry, Mapping) else None


def _case_layer_values(case: Mapping[str, Any], ref: str) -> Mapping[str, Any] | None:
    entry = _case_layer_raw(case, ref)
    vals = entry.get("values") if isinstance(entry, Mapping) else None
    return vals if isinstance(vals, Mapping) else None


def qualify_deal_case_pair(
    put_case: Mapping[str, Any] | None,
    call_case: Mapping[str, Any] | None,
    *,
    target_expiry_ms: int | None = None,
    now_ms: int | None = None,
    slot_ms: int | None = None,
    require_in_slot: bool = True,
    require_live_fresh: bool = True,
    require_content_sha256: bool = True,
    require_l3: bool = True,
    min_valuation_at_slot: bool = False,
    input_profile: str | None = "p0_strict@1",
    quote_hold_ms: int = DEFAULT_DEAL_QUOTE_HOLD_MS,
    max_pair_skew_ms: int = DEFAULT_DEAL_PAIR_SKEW_MS,
    max_l3_age_ms: int = DEFAULT_DEAL_L3_MAX_AGE_MS,
) -> dict[str, Any]:
    """Single-source qualification of a target-expiry Put/Call native case pair."""
    if not isinstance(put_case, Mapping) or not isinstance(call_case, Mapping):
        return {
            "ok": False,
            "reason_codes": ["missing_case_pair"],
            "error": "cases_by_side must contain both 'put' and 'call' case mappings",
        }
    if target_expiry_ms is not None and not is_clock(target_expiry_ms):
        return {"ok": False, "reason_codes": ["invalid_target_expiry_ms"], "error": "target_expiry_ms must be a positive integer"}
    if slot_ms is not None and not is_clock(slot_ms):
        return {"ok": False, "reason_codes": ["invalid_slot_ms"], "error": "slot_ms must be a positive integer"}
    if now_ms is not None and not is_clock(now_ms):
        return {"ok": False, "reason_codes": ["invalid_now_ms"], "error": "now_ms must be a positive integer"}

    built_by_side: dict[str, int] = {}
    val_ts_by_side: dict[str, int] = {}
    contracts_by_side: dict[str, Mapping[str, Any]] = {}
    val_floor_ms = (
        (slot_ms if min_valuation_at_slot else (slot_ms - quote_hold_ms))
        if (require_in_slot and slot_ms is not None)
        else None
    )

    for side, case in (("put", put_case), ("call", call_case)):
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not _CASE_ID_RE.match(case_id):
            return {"ok": False, "reason_codes": [f"{side}_invalid_case_id"], "error": f"invalid {side} case_id"}
        build = case.get("build")
        if not isinstance(build, Mapping):
            return {"ok": False, "reason_codes": [f"{side}_missing_build"], "error": f"missing {side} build metadata"}
        built_ms = build.get("built_at_ms")
        if (
            not is_clock(built_ms)
            or (now_ms is not None and built_ms > now_ms)
            or (require_in_slot and slot_ms is not None and built_ms < slot_ms)
        ):
            return {
                "ok": False,
                "reason_codes": [f"{side}_built_outside_window"],
                "error": f"{side} built_at_ms outside [slot_ms, now_ms]",
            }
        content_sha = build.get("content_sha256")
        if require_content_sha256 or content_sha is not None:
            if not isinstance(content_sha, str) or content_sha != canonical_case_content_sha256(case):
                return {
                    "ok": False,
                    "reason_codes": [f"{side}_content_sha256_mismatch"],
                    "error": f"{side} content_sha256 mismatch",
                }
        asof_ms = case.get("asof_ms")
        if require_content_sha256 or asof_ms is not None:
            if (
                not is_clock(asof_ms)
                or not (0 <= (built_ms - asof_ms) <= quote_hold_ms)
                or (val_floor_ms is not None and asof_ms < val_floor_ms)
                or (require_live_fresh and now_ms is not None and (now_ms - asof_ms) > quote_hold_ms)
            ):
                return {
                    "ok": False,
                    "reason_codes": [f"{side}_asof_stale_or_outside_slot"],
                    "error": f"{side} asof_ms stale (>180s) or outside slot window",
                }
        contract = _case_layer_values(case, "L1.contract")
        if not isinstance(contract, Mapping):
            return {"ok": False, "reason_codes": [f"{side}_missing_contract"], "error": f"missing {side} L1.contract"}
        if contract.get("side") != side:
            return {"ok": False, "reason_codes": [f"{side}_side_mismatch"], "error": f"{side} L1.contract side mismatch"}
        c_exp = contract.get("expiry_ms")
        if not is_clock(c_exp) or (target_expiry_ms is not None and c_exp != target_expiry_ms):
            return {"ok": False, "reason_codes": [f"{side}_expiry_mismatch"], "error": f"{side} L1.contract expiry_ms mismatch"}
        if not clean_text(contract.get("short_instrument")) or not clean_text(contract.get("long_instrument")):
            return {"ok": False, "reason_codes": [f"{side}_missing_instruments"], "error": f"missing {side} contract instruments"}

        quote = _case_layer_values(case, "L2.quote")
        if not isinstance(quote, Mapping):
            return {"ok": False, "reason_codes": [f"{side}_missing_quote"], "error": f"missing {side} L2.quote"}
        q_credit = finite_num(quote.get("credit_btc"))
        if q_credit is None or q_credit <= 0:
            return {
                "ok": False,
                "reason_codes": [f"{side}_missing_positive_credit"],
                "error": f"{side} L2.quote missing positive credit_btc",
            }
        if (quote.get("gap_reasons") or []) or (quote.get("quote_gap_reasons") or []):
            return {"ok": False, "reason_codes": [f"{side}_quote_gaps"], "error": f"{side} L2.quote contains gap_reasons"}
        q_val_ts = quote.get("valuation_ts_ms")
        if q_val_ts is None and not require_content_sha256:
            q_val_ts = asof_ms
        if (
            not is_clock(q_val_ts)
            or not (0 <= (built_ms - q_val_ts) <= quote_hold_ms)
            or (val_floor_ms is not None and q_val_ts < val_floor_ms)
            or (require_live_fresh and now_ms is not None and (now_ms - q_val_ts) > quote_hold_ms)
        ):
            return {
                "ok": False,
                "reason_codes": [f"{side}_valuation_stale_or_outside_slot"],
                "error": f"{side} L2.quote valuation_ts_ms stale (>180s) or outside slot window",
            }

        if require_l3:
            l3_raw = _case_layer_raw(case, "L3.expiry_walls")
            l3_vals = l3_raw.get("values") if isinstance(l3_raw, Mapping) else None
            l3_asof = l3_raw.get("asof_ms") if isinstance(l3_raw, Mapping) else None
            l3_pin = (
                finite_num((l3_vals.get("walls") or {}).get("pin"))
                if isinstance(l3_vals, Mapping) and isinstance(l3_vals.get("walls"), Mapping)
                else None
            )
            eff_exp = target_expiry_ms if target_expiry_ms is not None else c_exp
            if (
                not isinstance(l3_raw, Mapping)
                or l3_raw.get("quality") in {"missing", "unknown", "stale"}
                or not is_clock(l3_asof)
                or not (0 <= (built_ms - l3_asof) <= max_l3_age_ms)
                or not isinstance(l3_vals, Mapping)
                or (eff_exp is not None and l3_vals.get("expiry_ms") != eff_exp)
                or l3_vals.get("stale") is True
                or l3_pin is None
                or l3_pin <= 0
            ):
                return {
                    "ok": False,
                    "reason_codes": [f"{side}_l3_walls_missing_or_stale"],
                    "error": f"{side} L3.expiry_walls missing or stale (>5m)",
                }

        if input_profile == "p0_strict@1":
            l4_vals = _case_layer_values(case, "L4.expiry_vol")
            rv_sub = l4_vals.get("realized_vol") if isinstance(l4_vals, Mapping) else None
            if qualify_expiry_realized_vol(rv_sub, cutoff_ms=built_ms).get("status") != QUAL_ALIGNED:
                return {
                    "ok": False,
                    "reason_codes": [f"{side}_l4_rv24_unaligned"],
                    "error": f"{side} L4.expiry_vol realized_vol not aligned",
                }

        built_by_side[side] = built_ms
        val_ts_by_side[side] = q_val_ts
        contracts_by_side[side] = contract

    if contracts_by_side["put"]["expiry_ms"] != contracts_by_side["call"]["expiry_ms"]:
        return {"ok": False, "reason_codes": ["pair_expiry_mismatch"], "error": "pair L1.contract expiry_ms mismatch"}
    if abs(built_by_side["put"] - built_by_side["call"]) > max_pair_skew_ms:
        return {"ok": False, "reason_codes": ["pair_built_skew_exceeded"], "error": "pair built_at_ms skew exceeds 60s"}
    if abs(val_ts_by_side["put"] - val_ts_by_side["call"]) > max_pair_skew_ms:
        return {"ok": False, "reason_codes": ["pair_valuation_skew_exceeded"], "error": "pair valuation_ts_ms skew exceeds 60s"}

    return {
        "ok": True,
        "reason_codes": [],
        "error": None,
        "built_by_side": built_by_side,
        "valuation_ts_by_side": val_ts_by_side,
        "contracts_by_side": contracts_by_side,
    }
