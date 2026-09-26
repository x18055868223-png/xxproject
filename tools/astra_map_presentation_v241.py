"""BTC MAP v2.4.1 presentation model.

This module is intentionally pure: it performs no I/O, fetches no data, calls no
LLM, and never recalculates financial values.  It translates an already-frozen
``btc_map@1`` object and an optional underwriting snapshot into Chinese-facing
presentation fields for the workbench UI.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping


SCHEMA = "btc_map_presentation@2.4.1"
RULE_VERSION = "astra_map_presentation_rules@2.4.1"
BACKGROUND_TITLES = {
    "allocation": "配置",
    "inventory": "库存",
    "financing": "融资",
    "external_conditions": "外部",
}
MODE_CN = {"LIVE": "实盘资料", "REPLAY": "历史回放", "FIXTURE": "合成测试"}
SIDE_CN = {"put": "Put", "call": "Call"}
POSITION_CN = {
    "ABOVE": "上方",
    "BELOW": "下方",
    "INSIDE": "区内",
    "AT": "正好位于参考点",
    "UNKNOWN": "路径未知",
}
ROLE_CN = {
    "PUT_REFERENCE": "Put 结构参考",
    "CALL_REFERENCE": "Call 结构参考",
    "KPF": "历史成交接受区 KPF",
    "CP_STH": "链上资本成本 CP_STH",
    "EFFECTIVE_FLIP": "期权结构 Effective Flip",
    "KPF_BASIN": "历史成交接受区 KPF",
    "SHORT_LEG": "合同卖出腿",
    "PROTECTIVE_LEG": "合同保护腿",
    "NEAR_BREAKEVEN": "合同近端盈亏平衡",
}
RELATION_CN = {
    "before_payout": "开始赔付前",
    "partial_payout": "部分赔付区",
    "beyond_protection": "保护腿之外",
    "near_breakeven": "近端盈亏平衡附近",
    "before_payout_at_near_be": "近端盈亏平衡前",
    "partial_payout_at_near_be": "近端盈亏平衡位于部分赔付区",
}


def build_presentation(
    map_obj: Mapping[str, Any],
    snapshot: Mapping[str, Any] | None = None,
    presentation_mode: str | None = None,
) -> dict[str, Any]:
    """Build a display-only v2.4.1 presentation payload.

    The returned object is safe to attach after MAP construction.  It references
    existing MAP/snapshot facts and responsibility rows; it does not alter or
    derive financial authority.
    """

    source = map_obj if isinstance(map_obj, Mapping) else {}
    snap = snapshot if isinstance(snapshot, Mapping) else None
    mode = _mode(snap, presentation_mode)
    identity = _candidate_identity(source, snap)
    background = _background_rows(source)
    liability = _liability_by_region(source)
    priorities = [row.get("region_id") for row in _as_list(_nested(source, "policy_link", "region_liability_rows")) if isinstance(row, Mapping)]
    focus_liability = sorted(liability, key=lambda row: priorities.index(row.get("region_id"))
                             if row.get("region_id") in priorities else len(priorities))
    responses = _response_by_region(source)
    return {
        "schema": SCHEMA,
        "rule_version": RULE_VERSION,
        "map_id": source.get("map_id"),
        "mode": mode,
        "mode_cn": MODE_CN[mode],
        "candidate_identity": identity,
        "fact_summary": _fact_summary(background, focus_liability, identity),
        "background_rows": background,
        "price_axis": _price_axis(source, snap, identity),
        "liability_by_region": liability,
        "response_by_region": responses,
        "review_binding": _review_binding(source, snap),
        "limitations": _limitations(source, identity, background),
    }


# ---------------------------------------------------------------------------
# Identity and axis


def _mode(snapshot: Mapping[str, Any] | None, presentation_mode: str | None) -> str:
    explicit = str(presentation_mode or "").strip().upper()
    if explicit in MODE_CN:
        return explicit
    kind = None
    if isinstance(snapshot, Mapping):
        identity = snapshot.get("data_identity") if isinstance(snapshot.get("data_identity"), Mapping) else {}
        kind = str(identity.get("kind") or "").lower()
    if kind in {"live_public_capture", "manual_input", "state_refresh", "natural_nr"}:
        return "LIVE"
    if kind in {"historical_replay", "replay", "daily_snapshot"}:
        return "REPLAY"
    return "FIXTURE"


def _candidate_identity(map_obj: Mapping[str, Any], snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    policy = map_obj.get("policy_link") if isinstance(map_obj.get("policy_link"), Mapping) else {}
    contract = _contract(snapshot, policy)
    market = snapshot.get("market") if isinstance(snapshot, Mapping) and isinstance(snapshot.get("market"), Mapping) else {}
    economics = snapshot.get("economics") if isinstance(snapshot, Mapping) and isinstance(snapshot.get("economics"), Mapping) else {}
    fee = snapshot.get("fee") if isinstance(snapshot, Mapping) and isinstance(snapshot.get("fee"), Mapping) else {}
    side = _lower(contract.get("side"))
    be = _near_be_from_reference_pool(map_obj)
    fee_scope = _fee_scope(snapshot, policy)
    net_entry = _num(economics.get("net_credit_after_entry_fee_btc"))
    quote = market.get("quote") if isinstance(market.get("quote"), Mapping) else {}
    return {
        "candidate_id": _first(policy.get("candidate_id"), snapshot.get("candidate_id") if isinstance(snapshot, Mapping) else None),
        "candidate_version": _first(policy.get("candidate_version"), snapshot.get("snapshot_id") if isinstance(snapshot, Mapping) else None),
        "valuation_version": policy.get("valuation_version"),
        "side": side,
        "side_cn": SIDE_CN.get(side, side or None),
        "short_strike_usd": _num(contract.get("short_strike_usd")),
        "long_strike_usd": _num(contract.get("long_strike_usd")),
        "quantity_btc": _num(contract.get("quantity_btc")),
        "expiry_ms": _int_or_none(contract.get("expiry_ms")),
        "current_price_usd": _num(_first(market.get("reference_price_usd"), map_obj.get("current_price_usd"))),
        "reference_source": _first(market.get("reference_source"), map_obj.get("price_basis"), (map_obj.get("price_identity") or {}).get("price_basis") if isinstance(map_obj.get("price_identity"), Mapping) else None),
        "reference_time_ms": _reference_time_ms(map_obj, market),
        "gross_credit_btc": _num(_first(economics.get("gross_credit_btc"), economics.get("visible_credit_btc"), economics.get("premium_spread_btc"), quote.get("credit_btc"))),
        "entry_fee_btc": _num(_first(economics.get("theoretical_two_leg_fee_btc"), economics.get("entry_fee_btc"), fee.get("entry_fee_btc"), fee.get("amount_btc"))),
        "net_credit_btc": net_entry,
        "net_credit_after_entry_fee_btc": net_entry,
        "net_credit_after_fee_btc": _num(economics.get("net_credit_after_fee_btc")),
        "M_ref_btc": _num(_first(economics.get("reference_margin_btc"), policy.get("M_ref_btc"))),
        "near_breakeven_usd": be,
        "fee_scope": fee_scope,
    }


def _contract(snapshot: Mapping[str, Any] | None, policy: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(snapshot, Mapping) and isinstance(snapshot.get("contract"), Mapping):
        return dict(snapshot["contract"])
    if isinstance(policy.get("contract"), Mapping):
        return dict(policy["contract"])
    return {}


def _near_be_from_reference_pool(map_obj: Mapping[str, Any]) -> float | None:
    for ref in _as_list(map_obj.get("reference_pool")):
        if not isinstance(ref, Mapping):
            continue
        if ref.get("family") != "CONTRACT_LIABILITY" or ref.get("role") != "NEAR_BREAKEVEN":
            continue
        if ref.get("eligible_for_display") is False:
            continue
        usage = ref.get("usage_decision") if isinstance(ref.get("usage_decision"), Mapping) else {}
        if usage and usage.get("can_use") is False:
            continue
        if ref.get("coordinate_qualification") not in {None, "COMPARABLE", "SAME_BASIS", "QUALIFIED", "QUALIFIED_CONVERSION"}:
            continue
        price = _num(_first(ref.get("raw_price"), ref.get("display_center")))
        if price is not None:
            return price
        lo = _num(ref.get("raw_low"))
        hi = _num(ref.get("raw_high"))
        if lo is not None and hi is not None and abs(lo - hi) < 1e-9:
            return lo
    return None


def _reference_time_ms(map_obj: Mapping[str, Any], market: Mapping[str, Any]) -> int | None:
    quote = market.get("quote") if isinstance(market.get("quote"), Mapping) else {}
    return _int_or_none(_first(
        _nested(quote, "exchange_times_ms", "short"),
        _nested(quote, "short_book", "timestamp"),
        _nested(market, "short_book", "timestamp"),
    ))


def _fee_scope(snapshot: Mapping[str, Any] | None, policy: Mapping[str, Any]) -> dict[str, Any]:
    rows = policy.get("region_liability_rows") if isinstance(policy.get("region_liability_rows"), list) else []
    for row in rows:
        if isinstance(row, Mapping) and isinstance(row.get("cost_scope"), Mapping):
            return deepcopy(dict(row["cost_scope"]))
    fee = snapshot.get("fee") if isinstance(snapshot, Mapping) and isinstance(snapshot.get("fee"), Mapping) else {}
    covers = list(fee.get("covers") or []) if isinstance(fee.get("covers"), list) else []
    return {"title": None, "covers": covers, "fee_basis": fee.get("basis")}


def _price_axis(map_obj: Mapping[str, Any], snapshot: Mapping[str, Any] | None, identity: Mapping[str, Any]) -> dict[str, Any]:
    refs = []
    for ref in _as_list(map_obj.get("reference_pool")):
        if not isinstance(ref, Mapping):
            continue
        refs.append({
            "reference_id": ref.get("reference_id"),
            "label_cn": _reference_label(ref),
            "family": ref.get("family"),
            "role": ref.get("role"),
            "geometry": ref.get("geometry"),
            "market": ref.get("market"),
            "price_basis": ref.get("price_basis"),
            "quote_currency": ref.get("quote_currency"),
            "expiry_scope": ref.get("expiry_scope"),
            "quality": ref.get("quality"),
            "usage_decision": deepcopy(ref.get("usage_decision") or {}),
            "eligible_for_display": ref.get("eligible_for_display") is True,
            "observation_end_ms": ref.get("observation_end_ms"),
            "raw_price": _num(ref.get("raw_price")),
            "raw_low": _num(ref.get("raw_low")),
            "raw_high": _num(ref.get("raw_high")),
            "coordinate_qualification": ref.get("coordinate_qualification"),
            "selected_for_display": bool(ref.get("selected_for_display")),
            "source_record_ids": list(ref.get("source_record_ids") or []),
            "track_cn": _track_for_ref(ref),
        })
    return {
        "scale": "linear_btc_usd",
        "current": {
            "price_usd": identity.get("current_price_usd"),
            "source": identity.get("reference_source"),
            "price_basis": map_obj.get("price_basis"),
            "time_ms": identity.get("reference_time_ms"),
        },
        "contract_markers": _contract_markers(identity),
        "references": refs,
        "notes_cn": ["同一视图内相同美元距离必须对应相同像素跨度；本对象只提供渲染输入，不改变价格。"],
    }


def _contract_markers(identity: Mapping[str, Any]) -> list[dict[str, Any]]:
    side = _lower(identity.get("side"))
    side_cn = SIDE_CN.get(side, "")
    markers: list[dict[str, Any]] = []
    if identity.get("short_strike_usd") is not None:
        markers.append({"kind": "short_leg", "label_cn": f"卖{side_cn} Ks" if side_cn else "卖腿 Ks", "price_usd": identity.get("short_strike_usd")})
    if identity.get("long_strike_usd") is not None:
        markers.append({"kind": "long_leg", "label_cn": f"买{side_cn} Kl" if side_cn else "保护腿 Kl", "price_usd": identity.get("long_strike_usd")})
    if identity.get("near_breakeven_usd") is not None:
        markers.append({"kind": "near_breakeven", "label_cn": "按所列费用假设的到期近端BE", "price_usd": identity.get("near_breakeven_usd")})
    return markers


def _reference_label(ref: Mapping[str, Any]) -> str:
    role = str(ref.get("role") or "")
    family = str(ref.get("family") or "")
    if role in ROLE_CN:
        return ROLE_CN[role]
    return " ".join(item for item in (family, role) if item)


def _track_for_ref(ref: Mapping[str, Any]) -> str:
    family = str(ref.get("family") or "")
    role = str(ref.get("role") or "")
    if role == "CP_STH" or family == "ONCHAIN_COST":
        return "链上成本"
    if family == "OPTION_STRUCTURE":
        return "期权结构"
    if family == "TRADED_ACCEPTANCE":
        return "历史接受区"
    return "其他参考"


# ---------------------------------------------------------------------------
# Background rows and summaries


def _background_rows(map_obj: Mapping[str, Any]) -> list[dict[str, Any]]:
    background = map_obj.get("background") if isinstance(map_obj.get("background"), Mapping) else {}
    rows = []
    for key in ("allocation", "inventory", "financing", "external_conditions"):
        row = background.get(key) if isinstance(background.get(key), Mapping) else {}
        rows.append(_background_row(key, row, map_obj))
    return rows


def _background_row(key: str, row: Mapping[str, Any], map_obj: Mapping[str, Any]) -> dict[str, Any]:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), Mapping) else {}
    source_ids = list(row.get("source_record_ids") or [])
    source_times = _source_times(map_obj, source_ids)
    return {
        "key": key,
        "title": BACKGROUND_TITLES[key],
        "summary_cn": row.get("summary_cn") or row.get("summary") or _missing_summary(key),
        "primary_metrics": _primary_metrics(key, metrics, map_obj),
        "comparison_cn": _comparison_cn(key, metrics),
        "meaning_cn": _meaning_cn(key, metrics, row),
        "cutoff_at_ms": _int_or_none(row.get("cutoff_at_ms")),
        "first_seen_at_ms": source_times.get("first_seen_at_ms"),
        "source_observation_end_ms": source_times.get("observation_end_ms"),
        "missing_cn": _missing_cn(row.get("missing")),
        "source_record_ids": source_ids,
        "fact_state": row.get("fact_state") or row.get("status") or "missing",
    }


def _source_times(map_obj: Mapping[str, Any], source_ids: list[str]) -> dict[str, int | None]:
    records = _manifest_records(map_obj)
    first_seen: list[int] = []
    obs_end: list[int] = []
    wanted = set(str(item) for item in source_ids)
    for record in records:
        rid = str(record.get("record_id") or "")
        if wanted and rid not in wanted:
            continue
        time_part = record.get("time") if isinstance(record.get("time"), Mapping) else {}
        fs = _int_or_none(time_part.get("first_seen_at_ms"))
        oe = _int_or_none(time_part.get("observation_end_ms"))
        if fs is not None:
            first_seen.append(fs)
        if oe is not None:
            obs_end.append(oe)
    return {"first_seen_at_ms": min(first_seen) if first_seen else None, "observation_end_ms": max(obs_end) if obs_end else None}


def _manifest_records(map_obj: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    manifest = map_obj.get("source_manifest") if isinstance(map_obj.get("source_manifest"), Mapping) else {}
    records = manifest.get("source_records") if isinstance(manifest.get("source_records"), list) else []
    return [item for item in records if isinstance(item, Mapping)]


def _primary_metrics(key: str, metrics: Mapping[str, Any], map_obj: Mapping[str, Any]) -> list[dict[str, Any]]:
    if key == "allocation":
        return _allocation_metrics(metrics)
    if key == "inventory":
        return _inventory_metrics(metrics, map_obj)
    if key == "financing":
        return _financing_metrics(metrics)
    if key == "external_conditions":
        return _external_metrics(metrics)
    return _generic_metrics(metrics)


def _allocation_metrics(metrics: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    _append_metric(out, "3交易日净配置", _first_key(metrics, "F3_usd_mn", "f3_usd_mn", "f3_usd_m", "flow_3d_usd_mn", "etf_flow_3d_usd_mn"), "US$m", "3交易日")
    _append_metric(out, "7交易日净配置", _first_key(metrics, "F7_usd_mn", "f7_usd_mn", "f7_usd_m", "flow_7d_usd_mn", "etf_flow_7d_usd_mn"), "US$m", "7交易日")
    _append_metric(out, "近期日均", _first_key(metrics, "V3_usd_mn", "v3_usd_mn", "v3_usd_m_per_day", "daily_avg_3d_usd_mn"), "US$m/日", "3交易日")
    _append_metric(out, "对照日均", _first_key(metrics, "V7_usd_mn", "v7_usd_mn", "v7_usd_m_per_day", "daily_avg_7d_usd_mn"), "US$m/日", "7交易日")
    _append_metric(out, "3日速度相对常态", _first_key(metrics, "u3"), "倍", "3交易日")
    _append_metric(out, "7日速度相对常态", _first_key(metrics, "u7"), "倍", "7交易日")
    return out or _generic_metrics(metrics)


def _inventory_metrics(metrics: Mapping[str, Any], map_obj: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    cp = _first_key(metrics, "cp_sth_usd", "CP_STH_usd", "cp_sth")
    if cp is None:
        cp = _nested(metrics, "cost_basis", "cp_sth_usd")
    source_price = _first_key(metrics, "source_price_usd", "bitview_source_price_usd")
    if source_price is None:
        source_price = _nested(metrics, "cost_basis", "source_price_usd")
    _append_metric(out, "CP_STH", cp, "USD", "最新完整样本")
    _append_metric(out, "Bitview同源价", source_price, "USD", "同源日样本")
    gap_label, gap_value, gap_window = _qualified_cp_gap(metrics, map_obj, cp, source_price)
    _append_metric(out, gap_label, gap_value, "%", gap_window)
    realized_metrics = metrics.get("realized_pnl") if isinstance(metrics.get("realized_pnl"), Mapping) else {}
    _append_metric(out, "亏损份额", _first_key(metrics, "loss_share_current_pct", "loss_share_14d_pct"), "%", "当前窗口")
    _append_metric(out, "前期亏损份额", _first_key(metrics, "loss_share_previous_pct"), "%", "对照窗口")
    _append_metric(out, "亏损金额", _first(_first_key(metrics, "realized_loss_current_usd_mn"), _mn(_nested(realized_metrics, "loss_14d_usd"))), "US$m", "当前窗口")
    _append_metric(out, "前期亏损金额", _first_key(metrics, "realized_loss_previous_usd_mn"), "US$m", "对照窗口")
    return out or _generic_metrics(metrics)


def _financing_metrics(metrics: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    _append_metric(out, "原生OI变化", _first(
        _first_key(metrics, "oi_24h_pct", "open_interest_24h_pct"),
        _nested(metrics, "oi_change", "oi_native_change_pct"),
        _nested(metrics, "oi", "oi_24h_window", "native_change_pct"),
        _nested(metrics, "oi", "oi_24h_window", "change_native_pct"),
        _nested(metrics, "oi", "change_24h", "native_change_pct"),
    ), "%", "24h")
    _append_metric(out, "实际Funding", _first(
        _first_key(metrics, "funding_24h", "funding_24h_rate", "settled_funding_rate_sum"),
        _nested(metrics, "funding", "settled_funding_rate_sum"),
    ), "rate_decimal", "已结算窗口")
    funding = metrics.get("funding") if isinstance(metrics.get("funding"), Mapping) else {}
    if isinstance(funding.get("window_24h"), Mapping):
        out = [item for item in out if item["label"] != "实际Funding"]
        _append_metric(out, "实际Funding", funding["window_24h"].get("settled_rate_sum"), "rate_decimal", "24h观察到的结算")
        _append_metric(out, "前段Funding", _nested(funding, "previous_24h", "settled_rate_sum"), "rate_decimal", "前一24h结算")
    apr = _first_key(metrics, "usdt_apr_pct", "borrow_apr_pct")
    if apr is None:
        apr = _nested(metrics, "borrow", "rate", "simple_apr_pct")
    _append_metric(out, "USDT借款APR", apr, "%", "可得样本")
    borrow_status = _nested(metrics, "borrow_source_values", "status")
    if borrow_status:
        _append_metric(out, "借款资料状态", "缺少合格来源" if str(borrow_status).lower() in {"blocked", "missing", "partial"} else "来源待核对", None, "公开/授权源")
    return out or _generic_metrics(metrics)


def _external_metrics(metrics: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    _append_metric(out, "美元广义指数", _first(
        _first_key(metrics, "usd_5d_pct", "dollar_5d_pct"),
        _nested(metrics, "dollar", "change_5obs_pct"),
    ), "%", "5观察")
    _append_metric(out, "名义10Y", _first(_first_key(metrics, "nominal_10y_5d_bp", "nominal_5d_bp"), _nested(metrics, "nominal_10y", "change_5obs_bp")), "bp", "5观察")
    _append_metric(out, "实际10Y", _first(_first_key(metrics, "real_10y_5d_bp", "real_5d_bp"), _nested(metrics, "real_10y", "change_5obs_bp")), "bp", "5观察")
    _append_metric(out, "BTC同期", _first_key(metrics, "btc_5d_pct"), "%", "对齐窗口")
    _append_metric(out, "美元广义指数·长窗", _first(_first_key(metrics, "usd_20d_pct", "dollar_20d_pct"), _nested(metrics, "dollar", "change_20obs_pct")), "%", "20观察")
    _append_metric(out, "名义10Y·长窗", _first(_first_key(metrics, "nominal_10y_20d_bp", "nominal_20d_bp"), _nested(metrics, "nominal_10y", "change_20obs_bp")), "bp", "20观察")
    _append_metric(out, "实际10Y·长窗", _first(_first_key(metrics, "real_10y_20d_bp", "real_20d_bp"), _nested(metrics, "real_10y", "change_20obs_bp")), "bp", "20观察")
    return out or _generic_metrics(metrics)


def _generic_metrics(metrics: Mapping[str, Any]) -> list[dict[str, Any]]:
    # Unmapped technical fields remain audit evidence, never invented UI labels.
    return []


def _append_metric(out: list[dict[str, Any]], label: str, value: Any, unit: str | None, window_cn: str | None) -> None:
    if value is None:
        return
    out.append({"label": label, "value": value, "unit": unit, "window_cn": window_cn})


def _qualified_cp_gap(metrics: Mapping[str, Any], map_obj: Mapping[str, Any], cp: Any, source_price: Any) -> tuple[str, Any, str | None]:
    cp_ref = _reference_by_role(map_obj, "CP_STH")
    cp_value = _num(cp)
    if _reference_is_comparable(cp_ref):
        gap = _first_key(metrics, "gap_sth_now_pct", "current_vs_cp_sth_pct")
        if gap is None:
            current = _num(_first_key(metrics, "current_price_usd", "spot_usd"))
            if current is None:
                current = _num(map_obj.get("current_price_usd"))
            if current is not None and cp_value not in (None, 0):
                gap = (current / cp_value - 1.0) * 100.0
        return "距CP_STH", gap, "当前Deribit可比价格"
    same_source_gap = _first_key(metrics, "gap_sth_source_price_pct")
    if same_source_gap is None:
        same_source_gap = _same_source_cp_gap(metrics, cp_value, _num(source_price))
    return "同源价距CP_STH", same_source_gap, "Bitview同源日" if same_source_gap is not None else None


def _same_source_cp_gap(metrics: Mapping[str, Any], cp: float | None, source_price: float | None) -> float | None:
    if cp in (None, 0) or source_price is None:
        return None
    dates = _nested(metrics, "cost_basis", "observation_dates")
    if dates is None:
        dates = _nested(metrics, "cost_basis_source_values", "observation_dates")
    if not isinstance(dates, Mapping):
        return None
    if not dates.get("cp_sth") or dates.get("cp_sth") != dates.get("source_price"):
        return None
    return (source_price / cp - 1.0) * 100.0


def _reference_by_role(map_obj: Mapping[str, Any], role: str) -> Mapping[str, Any] | None:
    for ref in _as_list(map_obj.get("reference_pool")):
        if isinstance(ref, Mapping) and ref.get("role") == role:
            return ref
    return None


def _reference_is_comparable(ref: Mapping[str, Any] | None) -> bool:
    if not isinstance(ref, Mapping):
        return False
    if ref.get("eligible_for_display") is False:
        return False
    qual = str(ref.get("coordinate_qualification") or "").upper()
    return qual in {"COMPARABLE", "SAME_BASIS", "QUALIFIED", "QUALIFIED_CONVERSION"}


def _mn(value: Any) -> float | None:
    number = _num(value)
    return None if number is None else number / 1_000_000.0


def _change_from_observations(source_values: Any) -> float | None:
    if not isinstance(source_values, Mapping):
        return None
    observations = source_values.get("observations")
    if not isinstance(observations, list) or len(observations) < 5:
        return None
    recent = [item for item in observations if isinstance(item, Mapping) and _num(item.get("value")) is not None]
    if len(recent) < 5:
        return None
    start = _num(recent[-5].get("value"))
    end = _num(recent[-1].get("value"))
    if start in (None, 0) or end is None:
        return None
    return (end / start - 1.0) * 100.0


def _comparison_cn(key: str, metrics: Mapping[str, Any]) -> str | None:
    if key == "allocation":
        f3 = _num(_first_key(metrics, "F3_usd_mn", "f3_usd_mn", "f3_usd_m", "flow_3d_usd_mn", "etf_flow_3d_usd_mn"))
        f7 = _num(_first_key(metrics, "F7_usd_mn", "f7_usd_mn", "f7_usd_m", "flow_7d_usd_mn", "etf_flow_7d_usd_mn"))
        v3 = _num(_first_key(metrics, "V3_usd_mn", "v3_usd_mn", "v3_usd_m_per_day", "daily_avg_3d_usd_mn"))
        v7 = _num(_first_key(metrics, "V7_usd_mn", "v7_usd_mn", "v7_usd_m_per_day", "daily_avg_7d_usd_mn"))
        if f3 is not None and f7 is not None and f3 > 0 and f7 > 0 and v3 is not None and v7 is not None and v3 < v7:
            return "ETF仍为净流入，但近期日均速度低于7日窗口，不写成流出。"
    if key == "inventory":
        share_now = _num(_first_key(metrics, "loss_share_current_pct", "loss_share_14d_pct"))
        share_prev = _num(_first_key(metrics, "loss_share_previous_pct"))
        loss_now = _num(_first_key(metrics, "realized_loss_current_usd_mn"))
        loss_prev = _num(_first_key(metrics, "realized_loss_previous_usd_mn"))
        if None not in (share_now, share_prev, loss_now, loss_prev) and share_now > share_prev and loss_now < loss_prev:
            return "亏损份额上升，但绝对亏损金额下降；两个事实并列，不能写成抛压金额增加。"
    if key == "financing":
        borrow_status = str(_nested(metrics, "borrow_source_values", "status") or "").lower()
        if borrow_status in {"blocked", "missing", "partial"}:
            return "USDT借款历史或当前样本缺失，只报实际Funding/OI，不确认温和或过热。"
        if any("borrow" in str(item).lower() or "借款" in str(item) for item in _as_list(metrics.get("missing"))):
            return "USDT借款历史或当前样本缺失，只报实际Funding/OI，不确认温和或过热。"
        if _first_key(metrics, "usdt_history_missing", "borrow_history_missing") is True:
            return "USDT历史基准缺失，不能确认温和或过热。"
    if key == "external_conditions":
        usd = _num(_first(_first_key(metrics, "usd_5d_pct", "dollar_5d_pct"), _nested(metrics, "dollar", "change_5obs_pct")))
        nom = _num(_first(_first_key(metrics, "nominal_10y_5d_bp", "nominal_5d_bp"), _nested(metrics, "nominal_10y", "change_5obs_bp")))
        btc = _num(_first_key(metrics, "btc_5d_pct"))
        if usd is not None and nom is not None and btc is not None and usd > 0 and nom > 0 and btc > 0:
            return "同窗美元、利率与BTC共同上升；该窗不支持机械的利率涨即币价跌解释。"
        real = _num(_first(_first_key(metrics, "real_10y_5d_bp", "real_5d_bp"), _nested(metrics, "real_10y", "change_5obs_bp")))
        if nom is not None and real is not None and nom > 0 and real > 0:
            return "名义与实际10Y在同窗上行；这里只作为外部环境背景，不直接推出保单方向。"
    return None


def _meaning_cn(key: str, metrics: Mapping[str, Any], row: Mapping[str, Any]) -> str:
    if row.get("fact_state") in {"missing", "unavailable"}:
        return "该背景缺当前合格事实，只保留缺口。"
    if key == "allocation":
        return "配置数据说明资金方向与速度，不直接给出保单盈亏。"
    if key == "inventory":
        return "成本位置与兑现活动是两个维度，不能互相替代。"
    if key == "financing":
        return "融资只描述杠杆与持仓成本背景；缺历史基准时不生成热度等级。"
    if key == "external_conditions":
        return "外部条件是环境背景；需要同窗比较，不能用端点方向直接推出因果。"
    return "仅作事实背景展示。"


def _fact_summary(background: list[Mapping[str, Any]], liability: list[Mapping[str, Any]], identity: Mapping[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    main = next((row for row in background if row.get("fact_state") in {"usable", "partial"} and row.get("summary_cn")), None)
    if main:
        out.append({"kind": "main_background", "text_cn": str(main["summary_cn"]), "evidence_ids": list(main.get("source_record_ids") or [])[:5]})
    divergence = next((row for row in background if row is not main and row.get("comparison_cn")), None)
    if divergence is None:
        divergence = next((row for row in background if row is not main and row.get("missing_cn")), None)
    if divergence:
        text = divergence.get("comparison_cn")
        if not text:
            missing = "、".join(str(item) for item in _as_list(divergence.get("missing_cn"))[:3] if item)
            text = f"{divergence.get('title') or '背景'}存在缺口：{missing}。" if missing else None
        if text and all(text != item.get("text_cn") for item in out):
            out.append({"kind": "key_divergence", "text_cn": str(text), "evidence_ids": list(divergence.get("source_record_ids") or [])[:5]})
    focus = _policy_focus(liability, identity)
    if focus:
        out.append(focus)
    return out[:3]


def _policy_focus(liability: list[Mapping[str, Any]], identity: Mapping[str, Any]) -> dict[str, Any] | None:
    if not liability:
        if identity.get("candidate_id"):
            return {"kind": "policy_focus", "text_cn": "本单责任区域缺失；只能展示合同身份，不能给区域关系。", "evidence_ids": []}
        return None
    row = liability[0]
    region_id = row.get("region_id")
    qualification = row.get("coordinate_qualification")
    label = _region_label(row)
    if qualification in {"NOMINAL_ONLY", "GLOBAL_ONLY"}:
        text = f"本张{identity.get('side_cn') or '候选'}的{label}只能按名义交割情景解释，不作精确共位或保护判断。"
    else:
        relation = "、".join(_relation_labels(row.get("relation_to_short_long_be")))
        text = f"本张{identity.get('side_cn') or '候选'}最相关的{label}与合同关系：{relation or '关系未明'}。"
    return {"kind": "policy_focus", "text_cn": text, "evidence_ids": [str(region_id)] if region_id else []}


# ---------------------------------------------------------------------------
# Liability and response


def _liability_by_region(map_obj: Mapping[str, Any]) -> list[dict[str, Any]]:
    policy = map_obj.get("policy_link") if isinstance(map_obj.get("policy_link"), Mapping) else {}
    rows = policy.get("region_liability_full_rows") if isinstance(policy.get("region_liability_full_rows"), list) else None
    if not rows:
        rows = policy.get("region_liability_rows") if isinstance(policy.get("region_liability_rows"), list) else []
    refs = {str(ref.get("reference_id")): ref for ref in _as_list(map_obj.get("reference_pool")) if isinstance(ref, Mapping) and ref.get("reference_id")}
    out = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        copied = deepcopy(dict(row))
        ref = refs.get(str(copied.get("region_id")))
        if isinstance(ref, Mapping):
            copied.setdefault("family", ref.get("family"))
            copied.setdefault("role", ref.get("role"))
            copied.setdefault("reference_label_cn", _reference_label(ref))
        copied["summary_cn"] = _liability_summary(copied)
        out.append(copied)
    return out


def _liability_summary(row: Mapping[str, Any]) -> str:
    region = _region_label(row)
    qual = row.get("coordinate_qualification")
    if qual in {"NOMINAL_ONLY", "GLOBAL_ONLY"}:
        return f"{region} 为名义参考；若假设到期交割等于该数字，可读取既有静态情景，但不能当作精确价格共位。"
    relation = "、".join(_relation_labels(row.get("relation_to_short_long_be")))
    if relation:
        return f"{region} 与本单合同关系：{relation}。赔付范围引用MAP既有责任行。"
    return f"{region} 责任关系未形成可读标签；保留原责任行。"


def _region_label(row: Mapping[str, Any]) -> str:
    explicit = row.get("reference_label_cn")
    if explicit:
        return str(explicit)
    role = str(row.get("role") or "")
    if role in ROLE_CN:
        return ROLE_CN[role]
    family = str(row.get("family") or "")
    if family:
        return {"OPTION_STRUCTURE": "期权结构参考", "TRADED_ACCEPTANCE": "历史成交接受区", "ONCHAIN_COST": "链上成本参考"}.get(family, "该参考区")
    return "该参考区"


def _relation_labels(value: Any) -> list[str]:
    out = []
    for item in _as_list(value):
        key = str(item or "")
        if not key:
            continue
        out.append(RELATION_CN.get(key, key.replace("_", " ")))
    return out


def _response_by_region(map_obj: Mapping[str, Any]) -> list[dict[str, Any]]:
    events = [event for event in _as_list(map_obj.get("response_events")) if isinstance(event, Mapping)]
    state = map_obj.get("response_state") if isinstance(map_obj.get("response_state"), Mapping) else {}
    segments = state.get("segments") if isinstance(state.get("segments"), Mapping) else {}
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for event in events:
        region = str(event.get("reference_id") or "")
        if not region:
            continue
        grouped.setdefault(region, []).append(event)
    for region, segment in segments.items():
        if isinstance(segment, Mapping):
            grouped.setdefault(str(region), [])
    out = []
    for region, region_events in sorted(grouped.items()):
        segment = segments.get(region) if isinstance(segments, Mapping) else None
        current_position = segment.get("last_position") if isinstance(segment, Mapping) else _last(region_events, "current_position")
        response_items = [_event_response(event) for event in region_events]
        out.append({
            "region_id": region,
            "current_position": current_position,
            "current_position_cn": POSITION_CN.get(str(current_position), str(current_position) if current_position else None),
            "summary_cn": _response_summary(region_events, current_position),
            "events": response_items,
        })
    return out


def _event_response(event: Mapping[str, Any]) -> dict[str, Any]:
    event_type = str(event.get("event_type") or "UNKNOWN")
    return {
        "event_type": event_type,
        "event_cn": _event_cn(event),
        "time_ms": _int_or_none(_first(event.get("event_at_ms"), event.get("observed_at_ms"), event.get("known_at_ms"), event.get("window_end_ms"))),
        "method": event.get("method"),
        "coverage": event.get("coverage"),
        "price": _num(event.get("price")),
        "previous_position": event.get("previous_position"),
        "current_position": event.get("current_position"),
    }


def _event_cn(event: Mapping[str, Any]) -> str:
    typ = str(event.get("event_type") or "")
    coverage = str(event.get("coverage") or "")
    method = str(event.get("method") or "")
    if typ == "REFERENCE_REVISION_CHANGED":
        return "区域版本更新，不记作价格突破。"
    if typ == "POSITION_OBSERVED":
        return f"当前在{POSITION_CN.get(str(event.get('current_position')), event.get('current_position') or '未知')}；只有一个价格样本，进入路径未知。"
    if typ == "SAMPLED_CROSSING_BETWEEN_SAMPLES":
        return "相邻样本跨过区域，只能证明跨区样本，不证明逐价成交。"
    if typ == "CLOSE_SIDE_CHANGE":
        return "闭合价跨区，只能说明收盘侧变化，不证明盘中逐价路径。"
    if typ == "RANGE_INTERSECTS":
        return "K线范围与区域相交；缺少区内先后顺序。"
    if typ == "INTERVAL_UNKNOWN" or coverage in {"GAP", "UNKNOWN", "DISCONNECTED"}:
        return "价格来源存在断档，该区间路径未知。"
    if typ == "POINT_SIDE_CHANGE":
        return "价格样本改变了参考点两侧位置。"
    if typ == "ENTERED_REFERENCE":
        return "样本从区域外进入区域；仍需连续样本确认持续性。"
    if typ == "LEFT_REFERENCE":
        return "样本离开区域；不等同于退出成交或现金流。"
    if typ == "OBSERVATION_PRICE_BASIS_MISMATCH":
        return "观察价系与参考价系不一致，不作精确响应。"
    if typ == "REFERENCE_NOT_QUALIFIED_FOR_RESPONSE":
        return "参考未通过用途资格，不生成区域响应。"
    if method or coverage:
        return "只有有限价格观察；未形成可证明的触达或回收过程。"
    return "保留原响应事件；未生成额外市场解释。"


def _response_summary(events: list[Mapping[str, Any]], current_position: Any) -> str:
    if not events:
        if current_position:
            return f"当前段状态为{POSITION_CN.get(str(current_position), current_position)}；没有可证明的触达或回收过程。"
        return "尚无可验证区域响应。"
    latest = events[-1]
    return _event_cn(latest)


# ---------------------------------------------------------------------------
# Binding and limitations


def _review_binding(map_obj: Mapping[str, Any], snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    policy = map_obj.get("policy_link") if isinstance(map_obj.get("policy_link"), Mapping) else {}
    review = snapshot.get("llm_review") if isinstance(snapshot, Mapping) and isinstance(snapshot.get("llm_review"), Mapping) else {}
    return {
        "map_id": map_obj.get("map_id"),
        "map_cutoff_at_ms": _int_or_none(map_obj.get("cutoff_at_ms")),
        "candidate_version": policy.get("candidate_version"),
        "valuation_version": policy.get("valuation_version"),
        "snapshot_id": snapshot.get("snapshot_id") if isinstance(snapshot, Mapping) else None,
        "manual_review_status": review.get("status") if review else "not_attached",
        "reviewed_snapshot_id": review.get("reviewed_snapshot_id") if review else None,
    }


def _limitations(map_obj: Mapping[str, Any], identity: Mapping[str, Any], background: list[Mapping[str, Any]]) -> list[str]:
    out = ["presentation_only_no_financial_recalculation", "no_probability_or_trade_permission_generated"]
    if identity.get("M_ref_btc") is None:
        out.append("M_ref_missing_or_not_bound")
    if any(row.get("fact_state") in {"missing", "unavailable"} for row in background):
        out.append("some_background_rows_missing")
    if not map_obj.get("reference_pool"):
        out.append("reference_pool_empty")
    return sorted(dict.fromkeys(out))


# ---------------------------------------------------------------------------
# Small helpers


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _copy(value: Any) -> Any:
    return deepcopy(value)


def _first(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or not number.is_integer():
        return None
    return int(number)


def _lower(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text or None


def _first_key(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _nested(mapping: Mapping[str, Any], *keys: str) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _last(items: list[Mapping[str, Any]], key: str) -> Any:
    for item in reversed(items):
        if item.get(key) is not None:
            return item.get(key)
    return None


def _missing_cn(value: Any) -> list[str]:
    out = []
    for item in _as_list(value):
        text = str(item or "")
        if not text:
            continue
        low = text.lower()
        if "borrow" in low:
            translated = "借款来源或历史基准缺失"
        elif "pnl" in low or "profit" in low or "loss" in low or "realized_cap" in low or "14_complete" in low:
            translated = "兑现损益的完整日窗口尚未取得"
        elif "timestamp" in low or "time" in low or "cutoff" in low:
            translated = "来源观察时刻或可得时刻不合格"
        elif "stale" in low or "ttl" in low:
            translated = "该分项已过用途时效"
        elif "budget" in low:
            translated = "有限采集预算内尚未取得"
        elif "dollar" in low or "dxy" in low:
            translated = "美元背景尚未形成合格共同窗口"
        elif "quality" in low or "usage" in low or "partial" in low:
            translated = "该分项质量或用途资格未通过"
        elif any(char.isascii() for char in text) and "_" in text:
            translated = "该分项来源或比较窗口不完整"
        else:
            translated = text
        if translated not in out:
            out.append(translated)
    return out


def _missing_summary(key: str) -> str:
    return f"{BACKGROUND_TITLES.get(key, key)}背景缺当前合格事实。"
