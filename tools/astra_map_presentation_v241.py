"""BTC MAP v2.4.2 presentation model.

This module is intentionally pure: it performs no I/O, fetches no data, calls no
LLM, and never recalculates financial values.  It translates an already-frozen
``btc_map@1`` object and an optional underwriting snapshot into Chinese-facing
presentation fields for the workbench UI.
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
from typing import Any, Mapping


SCHEMA = "btc_map_presentation@2.4.2"
RULE_VERSION = "astra_map_presentation_rules@2.4.2"
BACKGROUND_TITLES = {
    "allocation": "配置资金",
    "inventory": "持有者成本与兑现",
    "financing": "杠杆与资金成本",
    "external_conditions": "外部压力",
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
    "CP_STH": "短期持有者资本化价格",
    "CP_LTH": "长期持有者资本化价格",
    "CP_ALL": "全链资本化价格",
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
    """Build a display-only v2.4.2 presentation payload.

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
    decision_brief = _decision_brief(source, background, focus_liability, responses, identity, mode)
    return {
        "schema": SCHEMA,
        "rule_version": RULE_VERSION,
        "map_id": source.get("map_id"),
        "mode": mode,
        "mode_cn": MODE_CN[mode],
        "candidate_identity": identity,
        "fact_summary": _fact_summary(background, focus_liability, identity),
        "decision_brief": decision_brief,
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
        "entry_fee_btc": _num(_first(economics.get("theoretical_two_leg_fee_btc"), economics.get("entry_fee_btc"), fee.get("entry_fee_btc"))),
        "total_hold_fee_btc": _num(fee.get("amount_btc")),
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
    current_source = _current_axis_source(map_obj)
    return {
        "scale": "linear_btc_usd",
        "current": {
            "price_usd": identity.get("current_price_usd"),
            "source": identity.get("reference_source"),
            "price_basis": map_obj.get("price_basis"),
            "time_ms": current_source.get("time_ms"),
            "source_record_id": current_source.get("source_record_id"),
            "source_cn": current_source.get("source_cn"),
        },
        "contract_markers": _contract_markers(identity),
        "references": refs,
        "notes_cn": ["同一视图内相同美元距离必须对应相同像素跨度；本对象只提供渲染输入，不改变价格。"],
    }


def _current_axis_source(map_obj: Mapping[str, Any]) -> dict[str, Any]:
    record, reason = _qualified_conversion_price(map_obj)
    if record is None:
        return {
            "time_ms": None,
            "source_record_id": None,
            "source_cn": f"当前价指数源钟未提供或未通过同版资格：{_public_reason(reason or '缺少合格指数来源')}",
        }
    return {
        "time_ms": _record_observation_end_ms(record),
        "source_record_id": record.get("record_id"),
        "source_cn": "本地合成指数" if _record_is_simulation(record) else "Deribit BTC USD指数",
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
        return "持有者成本"
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
    source_ids = [str(item) for item in _as_list(row.get("source_record_ids")) if str(item or "")]
    records = _records_for_ids(map_obj, source_ids)
    source_times = _source_times(map_obj, records)
    source_explanation = _source_explanation(key, row, map_obj, source_ids)
    map_cutoff = _map_cutoff_ms(map_obj)
    return {
        "key": key,
        "title": BACKGROUND_TITLES[key],
        "summary_cn": row.get("summary_cn") or row.get("summary") or _missing_summary(key),
        "primary_metrics": _primary_metrics(key, metrics, map_obj, row),
        "comparison_cn": _comparison_cn(key, metrics),
        "meaning_cn": _meaning_cn(key, metrics, row),
        "source_explanation": source_explanation,
        "cutoff_at_ms": map_cutoff,
        "product_cutoff_at_ms": _int_or_none(row.get("cutoff_at_ms")),
        "first_seen_at_ms": source_times.get("first_seen_at_ms"),
        "source_observation_end_ms": source_times.get("observation_end_ms"),
        "source_clock_rows": _source_clock_rows(map_obj, records),
        "missing_cn": _missing_cn(row.get("missing")),
        "source_record_ids": source_ids,
        "fact_state": row.get("fact_state") or row.get("status") or "missing",
    }


def _source_times(map_obj: Mapping[str, Any], records: list[Mapping[str, Any]]) -> dict[str, int | None]:
    if len(records) != 1:
        return {"first_seen_at_ms": None, "observation_end_ms": None}
    record = records[0]
    cutoff, cutoff_reasons = _source_clock_cutoff(map_obj)
    reasons = cutoff_reasons + _source_clock_reasons(record, cutoff)
    if reasons:
        return {"first_seen_at_ms": None, "observation_end_ms": None}
    time_part = record.get("time") if isinstance(record.get("time"), Mapping) else {}
    return {
        "first_seen_at_ms": _int_or_none(time_part.get("first_seen_at_ms")),
        "observation_end_ms": _int_or_none(time_part.get("observation_end_ms")),
    }


def _source_clock_rows(map_obj: Mapping[str, Any], records: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    cutoff, cutoff_reasons = _source_clock_cutoff(map_obj)
    rows = []
    for record in records:
        time_part = record.get("time") if isinstance(record.get("time"), Mapping) else {}
        usage = record.get("usage_qualification") if isinstance(record.get("usage_qualification"), Mapping) else {}
        clock_reasons = cutoff_reasons + _source_clock_reasons(record, cutoff)
        usage_reasons = _source_usage_reasons(record)
        rows.append({
            "record_id": record.get("record_id"),
            "product_id": _record_product_id(record),
            "source_cn": _record_source_cn(record),
            "observation_start_ms": _int_or_none(time_part.get("observation_start_ms")),
            "observation_end_ms": _int_or_none(time_part.get("observation_end_ms")),
            "first_seen_at_ms": _int_or_none(time_part.get("first_seen_at_ms")),
            "usage_status": usage.get("status"),
            "eligible": _record_can_use(record) and not clock_reasons,
            "reasons": sorted(dict.fromkeys(clock_reasons + usage_reasons)),
        })
    return rows


def _source_clock_cutoff(map_obj: Mapping[str, Any]) -> tuple[int | None, list[str]]:
    map_cutoff = _map_cutoff_ms(map_obj)
    manifest = map_obj.get("source_manifest") if isinstance(map_obj.get("source_manifest"), Mapping) else {}
    manifest_cutoff = _int_or_none(manifest.get("cutoff_at_ms")) if manifest else None
    reasons = []
    if map_cutoff is None:
        reasons.append("map_cutoff_missing")
    if manifest_cutoff is None:
        reasons.append("source_manifest_cutoff_missing")
    elif map_cutoff is not None and manifest_cutoff != map_cutoff:
        reasons.append("source_manifest_cutoff_mismatch")
    return map_cutoff, sorted(dict.fromkeys(reasons))


def _source_clock_reasons(record: Mapping[str, Any], cutoff_ms: int | None) -> list[str]:
    time_part = record.get("time") if isinstance(record.get("time"), Mapping) else {}
    usage = record.get("usage_qualification") if isinstance(record.get("usage_qualification"), Mapping) else {}
    start = _int_or_none(time_part.get("observation_start_ms"))
    obs = _int_or_none(time_part.get("observation_end_ms"))
    first_seen = _int_or_none(time_part.get("first_seen_at_ms"))
    published = _int_or_none(time_part.get("published_at_ms"))
    qualified_at = _int_or_none(usage.get("qualified_at_ms"))
    reasons = []
    if obs is None or first_seen is None or obs <= 0 or first_seen <= 0:
        reasons.append("source_clock_missing")
    else:
        if obs > first_seen:
            reasons.append("observation_after_first_seen")
        if cutoff_ms is not None:
            if obs > cutoff_ms:
                reasons.append("observation_after_cutoff")
            if first_seen > cutoff_ms:
                reasons.append("first_seen_after_cutoff")
    if cutoff_ms is not None:
        if published is not None and published > cutoff_ms:
            reasons.append("published_after_cutoff")
        if qualified_at is not None and qualified_at > cutoff_ms:
            reasons.append("qualified_after_cutoff")
    if start is not None:
        if start <= 0:
            reasons.append("observation_start_invalid")
        if obs is not None and obs > 0 and start > obs:
            reasons.append("observation_start_after_end")
        if cutoff_ms is not None and start > cutoff_ms:
            reasons.append("observation_start_after_cutoff")
    return sorted(dict.fromkeys(reasons))


def _source_usage_reasons(record: Mapping[str, Any]) -> list[str]:
    usage = record.get("usage_qualification") if isinstance(record.get("usage_qualification"), Mapping) else {}
    reasons = [str(item) for item in _as_list(usage.get("reason_codes")) if str(item or "")]
    status = str(usage.get("status") or "")
    if usage.get("can_use") is not True and status:
        reasons.append(status)
    return sorted(dict.fromkeys(reasons))


def _record_source_cn(record: Mapping[str, Any]) -> str:
    product_id = _record_product_id(record)
    if "btc.etf.flow" in product_id:
        return "Farside 现货 BTC ETF 净流量"
    if "btc.brk.cost_basis" in product_id:
        return "Bitview 持有者资本化价格"
    if "btc.brk.realized_pnl" in product_id:
        return "Bitview 已实现盈亏"
    if "binance.um.btcusdt.oi" in product_id:
        return "Binance USD-M BTCUSDT 持仓量"
    if "binance.um.btcusdt.funding" in product_id:
        return "Binance USD-M BTCUSDT 已结算 Funding"
    if "cex.usdt.borrow" in product_id:
        return "USDT 借款利率"
    if product_id.startswith("macro."):
        return "宏观背景来源"
    if "deribit.btc.index" in product_id or "btc.index" in product_id:
        return "Deribit BTC USD 指数"
    return product_id or "未知来源"


def _manifest_records(map_obj: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    manifest = map_obj.get("source_manifest") if isinstance(map_obj.get("source_manifest"), Mapping) else {}
    records = manifest.get("source_records") if isinstance(manifest.get("source_records"), list) else []
    return [item for item in records if isinstance(item, Mapping)]


def _primary_metrics(key: str, metrics: Mapping[str, Any], map_obj: Mapping[str, Any], row: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    if key == "allocation":
        return _allocation_metrics(metrics, map_obj, row or {})
    if key == "inventory":
        return _inventory_metrics(metrics, map_obj)
    if key == "financing":
        return _financing_metrics(metrics)
    if key == "external_conditions":
        return _external_metrics(metrics)
    return _generic_metrics(metrics)


def _allocation_metrics(metrics: Mapping[str, Any], map_obj: Mapping[str, Any], row: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    f3 = _first_key(metrics, "F3_usd_mn", "f3_usd_mn", "f3_usd_m", "flow_3d_usd_mn", "etf_flow_3d_usd_mn")
    f7 = _first_key(metrics, "F7_usd_mn", "f7_usd_mn", "f7_usd_m", "flow_7d_usd_mn", "etf_flow_7d_usd_mn")
    _append_metric(out, "3交易日净配置", f3, "US$m", "3交易日")
    _append_metric(out, "7交易日净配置", f7, "US$m", "7交易日")
    _append_etf_btc_equivalent(out, "3交易日净配置名义折合", f3, "3交易日", map_obj, row)
    _append_etf_btc_equivalent(out, "7交易日净配置名义折合", f7, "7交易日", map_obj, row)
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
    _append_metric(out, "短期持有者资本化价格", cp, "USD", "最新完整样本")
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
    _append_metric(out, "币安 BTCUSDT USD-M 原生OI变化", _first(
        _first_key(metrics, "oi_24h_pct", "open_interest_24h_pct"),
        _nested(metrics, "oi_change", "oi_native_change_pct"),
        _nested(metrics, "oi", "oi_24h_window", "native_change_pct"),
        _nested(metrics, "oi", "oi_24h_window", "change_native_pct"),
        _nested(metrics, "oi", "change_24h", "native_change_pct"),
    ), "%", "24h")
    _append_metric(out, "币安 BTCUSDT USD-M 已结算Funding", _first(
        _first_key(metrics, "funding_24h", "funding_24h_rate", "settled_funding_rate_sum"),
        _nested(metrics, "funding", "settled_funding_rate_sum"),
    ), "rate_decimal", "已结算窗口")
    funding = metrics.get("funding") if isinstance(metrics.get("funding"), Mapping) else {}
    if isinstance(funding.get("window_24h"), Mapping):
        out = [item for item in out if item["label"] != "币安 BTCUSDT USD-M 已结算Funding"]
        _append_metric(out, "币安 BTCUSDT USD-M 已结算Funding", funding["window_24h"].get("settled_rate_sum"), "rate_decimal", "24h观察到的结算")
        _append_metric(out, "币安 BTCUSDT USD-M 前段Funding", _nested(funding, "previous_24h", "settled_rate_sum"), "rate_decimal", "前一24h结算")
    apr = _first_key(metrics, "usdt_apr_pct", "borrow_apr_pct")
    if apr is None:
        apr = _nested(metrics, "borrow", "rate", "simple_apr_pct")
    _append_metric(out, "USDT借款APR", apr, "%", "授权可得样本")
    borrow_status = _nested(metrics, "borrow_source_values", "status")
    if borrow_status:
        _append_metric(out, "借款资料状态", "缺少合格来源" if str(borrow_status).lower() in {"blocked", "missing", "partial"} else "来源待核对", None, "授权源")
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


def _append_metric(out: list[dict[str, Any]], label: str, value: Any, unit: str | None, window_cn: str | None, extra: Mapping[str, Any] | None = None) -> None:
    if value is None:
        return
    item = {"label": label, "value": value, "unit": unit, "window_cn": window_cn}
    if isinstance(extra, Mapping):
        item.update(deepcopy(dict(extra)))
    out.append(item)


def _append_etf_btc_equivalent(
    out: list[dict[str, Any]],
    label: str,
    usd_mn: Any,
    window_cn: str,
    map_obj: Mapping[str, Any],
    row: Mapping[str, Any],
) -> None:
    amount = _num(usd_mn)
    if amount is None:
        return
    price_record, reason = _qualified_conversion_price(map_obj)
    etf_record, etf_reason = _qualified_background_record(map_obj, row, "btc.etf.flow.daily.v1")
    if price_record is None or etf_record is None:
        why = "；".join(item for item in (reason, etf_reason) if item) or "缺少同版合格折算来源"
        _append_metric(out, label, f"不能折算：{_public_reason(why)}", None, window_cn, {
            "measure_note_cn": "ETF为美元净流量，不能在缺少合格冻结价格或来源钟时折成BTC。",
            "conversion_qualified": False,
        })
        return
    price = _num(_nested(price_record, "content", "values", "price_usd"))
    if price in (None, 0):
        _append_metric(out, label, "不能折算：同版冻结价格缺明确正值", None, window_cn, {
            "measure_note_cn": "ETF为美元净流量，不能在缺少合格冻结价格或来源钟时折成BTC。",
            "conversion_qualified": False,
        })
        return
    etf_time = _record_observation_end_ms(etf_record)
    price_time = _record_observation_end_ms(price_record)
    value_btc = amount * 1_000_000.0 / price
    _append_metric(out, label, value_btc, "BTC名义折合", window_cn, {
        "conversion_qualified": True,
        "conversion_price_usd": price,
        "conversion_price_source_record_id": price_record.get("record_id"),
        "conversion_price_observation_end_ms": price_time,
        "source_record_id": etf_record.get("record_id"),
        "source_observation_end_ms": etf_time,
        "measure_note_cn": "按ETF美元净流量 ÷ 同版合格冻结BTC价格得到名义BTC等值；不是ETF实际买卖BTC数量。",
    })


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
        return "距短期持有者资本化价格", gap, "当前Deribit可比价格"
    same_source_gap = _first_key(metrics, "gap_sth_source_price_pct")
    if same_source_gap is None:
        same_source_gap = _same_source_cp_gap(metrics, cp_value, _num(source_price))
    return "Bitview同源价距短期资本化价格", same_source_gap, "Bitview同源日" if same_source_gap is not None else None


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


def _source_explanation(key: str, row: Mapping[str, Any], map_obj: Mapping[str, Any], source_ids: list[str]) -> dict[str, str]:
    records = _records_for_ids(map_obj, source_ids)
    has_manifest = isinstance(map_obj.get("source_manifest"), Mapping)
    qualified = [record for record in records if _record_can_use(record)]
    if not has_manifest:
        qualification = "缺同版来源清单，只能显示目标来源与已有字段；不能证明当前来源已取得。"
    elif any(_record_is_simulation(record) for record in records):
        qualification = "合成示例口径，仅用于页面核算；不能证明真实来源已取得。"
    elif source_ids and not records:
        qualification = "同版来源清单未找到该背景行列出的来源记录。"
    elif records and not qualified:
        qualification = "来源记录存在，但未通过同版用途资格；不可补成当前事实。"
    elif records and len(qualified) < len(records):
        qualification = "仅部分来源通过冻结用途资格，其余缺口仍保留。"
    elif qualified and str(row.get("fact_state") or row.get("status") or "").lower() == "partial":
        qualification = "已绑定记录通过冻结用途资格，背景缺口另列。"
    elif qualified:
        qualification = "来源记录通过同版冻结用途资格。"
    else:
        qualification = "未绑定具体来源记录，只能作为目标来源说明。"
    if key == "allocation":
        return {
            "source_cn": "目标来源：Farside 公共汇总的美国现货 BTC ETF 每日净美元流量。",
            "market_cn": "美国现货 BTC ETF。",
            "measure_cn": "净流量以美元百万计；BTC列是同版合格冻结价下的名义折合，不是实际BTC申赎数量。",
            "window_cn": _allocation_window_cn(row),
            "qualification_cn": qualification,
        }
    if key == "inventory":
        return {
            "source_cn": "目标来源：Bitview 链上资本化价格与已实现盈亏日序列。",
            "market_cn": "BTC 链上持有者成本与兑现活动。",
            "measure_cn": "短期/长期/全链持有者资本化价格和严格完整UTC日兑现损益；不是普通RP替代口径。",
            "window_cn": _inventory_window_cn(row),
            "qualification_cn": qualification,
        }
    if key == "financing":
        return {
            "source_cn": "目标来源：Binance USD-M BTCUSDT 公开持仓与实际Funding结算；借款需授权或公开可读源。",
            "market_cn": "币安 BTCUSDT USD-M 永续。",
            "measure_cn": "原生BTC持仓OI变化与已结算Funding；不混入预测费率，也不把缺借款写成温和或过热。",
            "window_cn": "OI按24h窗口；Funding按已观察到的结算窗口；借款无授权则保持缺口。",
            "qualification_cn": qualification,
        }
    return {
        "source_cn": "目标来源：FRED美元广义指数、美国10年名义/实际收益率与同窗BTC背景。",
        "market_cn": "宏观背景。",
        "measure_cn": "美元指数用百分比变化，美债收益率用bp变化；只作环境反证或背景。",
        "window_cn": "5观察与20观察并列；过期分项不得补成当前事实。",
        "qualification_cn": qualification,
    }


def _allocation_window_cn(row: Mapping[str, Any]) -> str:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), Mapping) else {}
    windows = metrics.get("windows") if isinstance(metrics.get("windows"), Mapping) else {}
    last3 = _as_list(windows.get("last3"))
    last7 = _as_list(windows.get("last7"))
    if last3 or last7:
        parts = []
        if last3:
            parts.append(f"3交易日 {last3[0]} 至 {last3[-1]}")
        if last7:
            parts.append(f"7交易日 {last7[0]} 至 {last7[-1]}")
        return "；".join(parts)
    return "3/7交易日与前60交易日常态对照。"


def _inventory_window_cn(row: Mapping[str, Any]) -> str:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), Mapping) else {}
    latest = _first(_nested(metrics, "cost_basis", "latest_observation_date"), _first_key(metrics, "latest_observation_date"))
    realized = metrics.get("realized_pnl") if isinstance(metrics.get("realized_pnl"), Mapping) else {}
    days = _as_list(realized.get("window_days"))
    if latest and days:
        return f"成本样本截至 {latest}；兑现损益为最近{len(days)}个完整UTC日。"
    if latest:
        return f"成本样本截至 {latest}；兑现损益要求完整UTC日窗口。"
    return "成本样本与严格完整UTC日兑现窗口。"


def _qualified_conversion_price(map_obj: Mapping[str, Any]) -> tuple[Mapping[str, Any] | None, str | None]:
    cutoff = _manifest_cutoff_ms(map_obj)
    if cutoff is None:
        return None, "缺同版冻结截止"
    expected_price = _num(_first(_nested(map_obj, "price_identity", "current_price_usd"), map_obj.get("current_price_usd")))
    expected_basis = str(_first(_nested(map_obj, "price_identity", "price_basis"), map_obj.get("price_basis")) or "").upper()
    if not expected_basis:
        return None, "当前价格缺少价系"
    for record in _manifest_records(map_obj):
        pid = _record_product_id(record)
        if "deribit.btc.index.map_context" not in pid and "btc.index" not in pid:
            continue
        identity = record.get("identity") if isinstance(record.get("identity"), Mapping) else {}
        if str(identity.get("asset") or "").upper() != "BTC":
            return None, "折算价格资产不是BTC"
        if str(identity.get("currency") or "").upper() != "USD" or str(identity.get("unit") or "").upper() != "USD":
            return None, "折算价格币种或单位不是USD"
        price = _num(_nested(record, "content", "values", "price_usd"))
        if price is None or price <= 0:
            continue
        price_basis = str(_nested(record, "content", "values", "price_basis") or "").upper()
        if not price_basis:
            return None, "折算价格缺少价系"
        if price_basis != expected_basis:
            return None, "折算价格与当前价格基准不一致"
        if expected_price is not None and not _same_number(price, expected_price):
            return None, "折算价格与当前冻结价格不一致"
        if not _record_can_use(record):
            return None, "冻结价格来源未通过用途资格"
        if not _record_clock_order_ok(record, cutoff):
            return None, "冻结价格源钟不满足观察时刻早于读取时刻且不晚于冻结截止"
        return record, None
    return None, "缺同版合格Deribit BTC USD指数价格记录"


def _qualified_background_record(map_obj: Mapping[str, Any], row: Mapping[str, Any], product_id: str) -> tuple[Mapping[str, Any] | None, str | None]:
    cutoff = _manifest_cutoff_ms(map_obj)
    if cutoff is None:
        return None, "缺同版冻结截止"
    records = _records_for_ids(map_obj, [str(item) for item in _as_list(row.get("source_record_ids")) if str(item or "")])
    for record in records:
        if product_id not in _record_product_id(record):
            continue
        identity = record.get("identity") if isinstance(record.get("identity"), Mapping) else {}
        if str(identity.get("currency") or "").upper() != "USD" or str(identity.get("unit") or "").lower() not in {"us$m", "usd_m", "usd million"}:
            return None, "ETF来源币种或单位不是美元百万"
        if not _record_can_use(record):
            return None, "ETF来源未通过用途资格"
        if not _record_clock_order_ok(record, cutoff):
            return None, "ETF来源源钟不满足观察时刻早于读取时刻且不晚于冻结截止"
        return record, None
    return None, "缺同版合格ETF来源记录"


def _records_for_ids(map_obj: Mapping[str, Any], source_ids: list[str]) -> list[Mapping[str, Any]]:
    wanted = {str(item) for item in source_ids if str(item or "")}
    if not wanted:
        return []
    out = []
    seen: dict[str, str] = {}
    for record in _manifest_records(map_obj):
        record_id = str(record.get("record_id") or "")
        if record_id not in wanted:
            continue
        fingerprint = _record_fingerprint(record)
        if record_id in seen and seen[record_id] == fingerprint:
            continue
        seen.setdefault(record_id, fingerprint)
        out.append(record)
    return out


def _record_fingerprint(record: Mapping[str, Any]) -> str:
    try:
        return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return repr(sorted(record.items()))


def _record_product_id(record: Mapping[str, Any]) -> str:
    identity = record.get("identity") if isinstance(record.get("identity"), Mapping) else {}
    return str(identity.get("product_id") or "").lower()


def _record_can_use(record: Mapping[str, Any]) -> bool:
    usage = record.get("usage_qualification") if isinstance(record.get("usage_qualification"), Mapping) else {}
    status = str(usage.get("status") or "").upper()
    return usage.get("can_use") is True and status in {"USABLE", "OK", ""}


def _record_is_simulation(record: Mapping[str, Any]) -> bool:
    usage = record.get("usage_qualification") if isinstance(record.get("usage_qualification"), Mapping) else {}
    collector = record.get("collector") if isinstance(record.get("collector"), Mapping) else {}
    return usage.get("simulation_only") is True or collector.get("synthetic_fixture") is True


def _record_clock_order_ok(record: Mapping[str, Any], cutoff_ms: int) -> bool:
    return not _source_clock_reasons(record, cutoff_ms)


def _same_number(left: float, right: float) -> bool:
    return abs(left - right) <= max(1e-9, abs(right) * 1e-10)


def _public_reason(text: str) -> str:
    return (text.replace("source_manifest", "同版来源清单")
                .replace("manifest", "同版来源清单")
                .replace("freeze cutoff", "冻结截止")
                .replace("cutoff", "冻结截止")
                .replace("policy", "合同责任排序"))


def _record_observation_end_ms(record: Mapping[str, Any]) -> int | None:
    return _int_or_none(_nested(record, "time", "observation_end_ms"))


def _manifest_cutoff_ms(map_obj: Mapping[str, Any]) -> int | None:
    manifest_cutoff = _int_or_none(_nested(map_obj, "source_manifest", "cutoff_at_ms"))
    map_cutoff = _int_or_none(map_obj.get("cutoff_at_ms"))
    if manifest_cutoff is None or map_cutoff is None:
        return None
    return manifest_cutoff if manifest_cutoff == map_cutoff else None


def _map_cutoff_ms(map_obj: Mapping[str, Any]) -> int | None:
    return _int_or_none(map_obj.get("cutoff_at_ms"))


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


def _decision_brief(
    map_obj: Mapping[str, Any],
    background: list[Mapping[str, Any]],
    liability: list[Mapping[str, Any]],
    responses: list[Mapping[str, Any]],
    identity: Mapping[str, Any],
    mode: str,
) -> dict[str, Any]:
    return {
        "pushes": [_background_push(row) for row in background],
        "conflicts": _decision_conflicts(map_obj, background),
        "policy_position": _policy_position(identity),
        "watch": _watch_region(liability, responses, identity, mode),
    }


def _background_push(row: Mapping[str, Any]) -> dict[str, Any]:
    source = row.get("source_explanation") if isinstance(row.get("source_explanation"), Mapping) else {}
    state = _source_state_cn(row.get("fact_state"))
    qualification = str(source.get("qualification_cn") or "")
    if qualification:
        state = f"{state}；{qualification}"
    return {
        "title_cn": str(row.get("title") or "背景"),
        "text_cn": str(row.get("summary_cn") or "该背景暂缺合格事实。"),
        "basis_cn": "；".join(item for item in (source.get("measure_cn"), source.get("window_cn")) if item),
        "source_state_cn": state,
    }


def _decision_conflicts(map_obj: Mapping[str, Any], background: list[Mapping[str, Any]]) -> list[dict[str, str]]:
    conflicts: list[dict[str, str]] = []
    for row in background:
        comparison = row.get("comparison_cn")
        if comparison:
            conflicts.append({"kind_cn": _conflict_kind(row.get("key"), str(comparison)), "text_cn": str(comparison)})
        missing = "、".join(str(item) for item in _as_list(row.get("missing_cn")) if item)
        if missing:
            conflicts.append({"kind_cn": "数据缺口", "text_cn": f"{row.get('title') or '背景'}：{missing}。"})
    background_obj = map_obj.get("background") if isinstance(map_obj.get("background"), Mapping) else {}
    for item in _as_list(background_obj.get("missing_or_conflicting")) + _as_list(map_obj.get("map_gaps")):
        text = _gap_text_cn(item)
        if text and all(text != conflict.get("text_cn") for conflict in conflicts):
            conflicts.append({"kind_cn": "数据缺口", "text_cn": text})
    return conflicts


def _conflict_kind(key: Any, text: str) -> str:
    if key == "financing" or "缺失" in text or "缺口" in text or "尚未" in text:
        return "数据缺口"
    if "近期" in text or "同窗" in text or "窗口" in text:
        return "时窗限制"
    return "方向分歧"


def _gap_text_cn(value: Any) -> str | None:
    text = str(value or "")
    if not text:
        return None
    low = text.lower()
    if "kpf" in low:
        return "KPF当前没有合格市场区域，不能生成市场观察区。"
    if "gex" in low or "gamma" in low:
        return "GEX/Gamma空间结构当前不可用，不能作为观察区来源。"
    if "fmz" in low or "spacegate" in low:
        return "FMZ 原生连续事实或空间门缺失。"
    if "borrow" in low:
        return "借款来源或历史基准缺失。"
    if "dollar" in low or "dxy" in low:
        return "美元背景过期或缺少共同窗口。"
    if "_" in text or ":" in text:
        return "存在来源资格或共同窗口缺口。"
    return text


def _policy_position(identity: Mapping[str, Any]) -> dict[str, Any]:
    current = _num(identity.get("current_price_usd"))
    checkpoints = [
        _checkpoint("卖出腿", identity.get("short_strike_usd"), current),
        _checkpoint("盈亏平衡价", identity.get("near_breakeven_usd"), current),
        _checkpoint("保护腿", identity.get("long_strike_usd"), current),
    ]
    checkpoints = [item for item in checkpoints if item is not None]
    if current is None:
        summary = "缺少冻结现价，只能列出合同价格线，不能计算距离。"
    elif checkpoints:
        side = identity.get("side_cn") or "候选"
        summary = f"以冻结现价 {current:g} USD 为零点展示{side}合同线距离；这是几何位置，不含方向预测。"
    else:
        summary = "当前未绑定具体保单，暂无卖腿、盈亏平衡和保护腿距离。"
    return {"summary_cn": summary, "checkpoints": checkpoints}


def _checkpoint(label: str, price: Any, current: float | None) -> dict[str, Any] | None:
    point = _num(price)
    if point is None:
        return None
    distance = None if current in (None, 0) else point - current
    pct = None if distance is None or current in (None, 0) else distance / current * 100.0
    return {"label_cn": label, "price_usd": point, "distance_usd": distance, "distance_pct": pct}


def _watch_region(liability: list[Mapping[str, Any]], responses: list[Mapping[str, Any]], identity: Mapping[str, Any], mode: str) -> dict[str, Any]:
    if not _has_policy_markers(identity):
        return {
            "qualified": False,
            "status_cn": "当前无合格市场观察区",
            "primary_region_id": None,
            "title_cn": "保单未绑定",
            "reason_cn": "尚未选择或未取得具体保单，无法确定合同观察优先级；只保留全局背景。",
            "check_cn": "先取得当前具体保单的卖出腿、盈亏平衡价和保护腿，再建立合同观察优先级。",
            "change_cn": "若接入具体保单并出现同版合格市场区域，再生成市场观察区。",
        }
    for row in liability:
        if not _liability_is_qualified_market_region(row):
            continue
        label = _region_label(row)
        mode_prefix = "本地模拟" if mode == "FIXTURE" else "同版冻结"
        reason = _watch_reason(row, identity)
        response = _response_for_region(responses, row.get("region_id"))
        response_text = _watch_response_text(response)
        return {
            "qualified": True,
            "status_cn": f"{mode_prefix}观察区",
            "primary_region_id": row.get("region_id"),
            "title_cn": label,
            "reason_cn": f"合同责任排序中的首个合格市场区域：{reason} 不含方向预测。",
            "check_cn": f"观察冻结价相对该区、卖出腿、盈亏平衡价和保护腿的距离变化；当前响应状态：{response_text}",
            "change_cn": "若该区域失去同版资格、合同腿或冻结价改变，观察优先级需要重算。",
        }
    return {
        "qualified": False,
        "status_cn": "当前无合格市场观察区",
        "primary_region_id": None,
        "title_cn": "合同警戒线",
        "reason_cn": "没有可比合格市场区域，或现有区域仅为名义参考；不把名义行或合成行升级为实况观察。",
        "check_cn": "先看卖出腿、盈亏平衡价和保护腿三条合同线的几何距离。",
        "change_cn": "若KPF/GEX等来源重新通过同版资格，或合同线发生变化，再生成市场观察区。",
    }


def _has_policy_markers(identity: Mapping[str, Any]) -> bool:
    return any(identity.get(key) is not None for key in ("short_strike_usd", "long_strike_usd", "near_breakeven_usd"))


def _watch_reason(row: Mapping[str, Any], identity: Mapping[str, Any]) -> str:
    side = _lower(identity.get("side"))
    adverse = "Put不利方向为下行" if side == "put" else "Call不利方向为上行" if side == "call" else "不利方向取决于合同侧别"
    relation = "、".join(_relation_labels(row.get("relation_to_short_long_be"))) or "合同责任关系未形成可读标签"
    distance = _region_distance_to_short(row, identity)
    if distance:
        return f"{adverse}；该区处在{relation}；{distance}。"
    return f"{adverse}；该区处在{relation}。"


def _region_distance_to_short(row: Mapping[str, Any], identity: Mapping[str, Any]) -> str | None:
    short = _num(identity.get("short_strike_usd"))
    if short is None:
        return None
    price = _num(_first(row.get("raw_price"), row.get("display_center")))
    if price is not None:
        return f"参考点距卖出腿 {price - short:+.0f} USD"
    lo = _num(row.get("raw_low"))
    hi = _num(row.get("raw_high"))
    if lo is not None and hi is not None:
        return f"区域距卖出腿 {lo - short:+.0f} 至 {hi - short:+.0f} USD"
    return None


def _response_for_region(responses: list[Mapping[str, Any]], region_id: Any) -> Mapping[str, Any] | None:
    region = str(region_id or "")
    for response in responses:
        if isinstance(response, Mapping) and str(response.get("region_id") or "") == region:
            return response
    return None


def _watch_response_text(response: Mapping[str, Any] | None) -> str:
    if not isinstance(response, Mapping):
        return "暂无区域响应事实，只有合同位置关系可看。"
    events = [event for event in _as_list(response.get("events")) if isinstance(event, Mapping)]
    position = response.get("current_position_cn") or "未知"
    if not events:
        return f"当前只知道位置为{position}，暂无触达、进入或离开等响应事实。"
    event_types = {str(event.get("event_type") or "") for event in events}
    if event_types.issubset({"POSITION_OBSERVED", "REFERENCE_REVISION_CHANGED"}):
        return f"当前只记录位置为{position}及区域版本变化，尚未形成触达、进入或离开等响应事实。"
    return str(response.get("summary_cn") or "已有区域响应事实，需查看响应列表。")


def _liability_is_qualified_market_region(row: Mapping[str, Any]) -> bool:
    if str(row.get("family") or "") == "CONTRACT_LIABILITY":
        return False
    qual = str(row.get("coordinate_qualification") or "").upper()
    if qual not in {"COMPARABLE", "SAME_BASIS", "QUALIFIED", "QUALIFIED_CONVERSION"}:
        return False
    if row.get("eligible_for_display") is False:
        return False
    return bool(row.get("region_id"))


def _source_state_cn(value: Any) -> str:
    state = str(value or "").lower()
    if state == "usable":
        return "合格"
    if state == "partial":
        return "部分合格"
    if state in {"missing", "unavailable"}:
        return "缺失"
    return "待核对"


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
            for key in ("raw_price", "raw_low", "raw_high", "display_center"):
                if key in ref:
                    copied.setdefault(key, ref.get(key))
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
