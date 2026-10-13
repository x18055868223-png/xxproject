"""Public follow-up view for one locked Astra underwriting candidate.

The module compares already-frozen points for the same candidate. It does not
call providers, choose a new side, update the store, run an LLM, or grant any
execution permission.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping, Sequence

if __package__:
    from .astra_workbench_reading_v1 import reading_value
    from . import astra_observation_replay_v1 as public_replay
    from . import astra_underwriting_real_replay_v20 as real_replay
    from . import astra_underwriting_v20 as core
else:
    from astra_workbench_reading_v1 import reading_value
    import astra_observation_replay_v1 as public_replay
    import astra_underwriting_real_replay_v20 as real_replay
    import astra_underwriting_v20 as core


SCHEMA = "locked_candidate_followup@1.0.0"

_CONTRACT_KEYS = (
    "venue",
    "underlying",
    "settlement_currency",
    "short_instrument",
    "long_instrument",
    "side",
    "quantity_btc",
    "expiry_ms",
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _dig(value: Mapping[str, Any], path: Sequence[str]) -> Any:
    current: Any = value
    for part in path:
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _point_snapshot(point: Mapping[str, Any]) -> Mapping[str, Any]:
    packet = _mapping(point.get("packet"))
    candidate = _mapping(point.get("candidate"))
    snapshot = packet.get("snapshot")
    if isinstance(snapshot, Mapping):
        return snapshot
    snapshot = candidate.get("snapshot")
    return snapshot if isinstance(snapshot, Mapping) else {}


def _contract_key(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    contract = _mapping(snapshot.get("contract"))
    return {key: contract.get(key) for key in _CONTRACT_KEYS}


def _point_id(point: Mapping[str, Any], snapshot: Mapping[str, Any]) -> Any:
    return point.get("point_id") or point.get("id") or point.get("store_point_id")


def _point_asof(point: Mapping[str, Any], snapshot: Mapping[str, Any]) -> int | None:
    packet = _mapping(point.get("packet"))
    return _int(_dig(snapshot, ("market", "valuation_ts_ms"))) or _int(_dig(packet, ("cutoff", "decision_asof_ms")))


def _point_recorded(point: Mapping[str, Any]) -> int | None:
    return _int(point.get("recorded_at_ms")) or _int(point.get("recorded_ms"))


def _public_packet(point: Mapping[str, Any]) -> dict[str, Any] | None:
    packet = _mapping(point.get("packet"))
    if not packet:
        return None
    raw_payload = _mapping(packet.get("raw_payload"))
    public_packet = {
        "schema": packet.get("schema"),
        "packet_hash": packet.get("packet_hash"),
        "identity": deepcopy(packet.get("identity")),
        "cutoff": deepcopy(packet.get("cutoff")),
        "valuation": deepcopy(packet.get("valuation")),
        "window": deepcopy(packet.get("window")),
        "risk": deepcopy(packet.get("risk")),
        "gaps": deepcopy(packet.get("gaps")),
        "replay": deepcopy(packet.get("replay")),
        "sources": {
            key: deepcopy(_mapping(packet.get("sources")).get(key))
            for key in ("record_ids", "clock_gaps", "manifest_clock_gaps", "acquisition_receipts", "raw_payload_hash")
        },
    }
    if raw_payload:
        public_packet["raw_payload"] = {
            "schema": raw_payload.get("schema"),
            "candidate": deepcopy(raw_payload.get("candidate")),
            "metadata": deepcopy(raw_payload.get("metadata")),
            "metadata_received_ts_ms": deepcopy(raw_payload.get("metadata_received_ts_ms")),
            "market": deepcopy(raw_payload.get("market")),
            "fee": deepcopy(raw_payload.get("fee")),
            "data_identity": deepcopy(raw_payload.get("data_identity")),
        }
    return public_replay.scrub_public(public_packet)


def _snapshot_candidate(point: Mapping[str, Any], snapshot: Mapping[str, Any]) -> dict[str, Any]:
    candidate = _mapping(point.get("candidate"))
    source = candidate if candidate else {"candidate_id": snapshot.get("candidate_id"), "snapshot": snapshot}
    allowed = {
        "candidate_id",
        "side",
        "status",
        "remaining_to_expiry_ms",
        "snapshot",
        "current_questions",
        "wpem_summary",
        "wpem_evidence",
        "review_display",
        "research_archive",
        "source_manifest",
    }
    public_candidate = {key: deepcopy(source[key]) for key in allowed if key in source}
    public_snapshot = _mapping(public_candidate.get("snapshot"))
    if public_snapshot:
        public_candidate["snapshot"] = {
            key: deepcopy(public_snapshot[key])
            for key in (
                "schema",
                "snapshot_id",
                "candidate_id",
                "contract",
                "data_identity",
                "fact_context",
                "market",
                "fee",
                "risk",
                "economics",
                "liability",
                "status",
                "gap_reasons",
                "execution_allowed",
            )
            if key in public_snapshot
        }
    return public_replay.scrub_public(public_candidate)


def _public_point(point: Mapping[str, Any], *, point_id: Any | None = None, include_raw: bool = True) -> dict[str, Any]:
    snapshot = _point_snapshot(point)
    return {
        "point_id": point_id if point_id is not None else _point_id(point, snapshot),
        "snapshot_id": snapshot.get("snapshot_id"),
        "asof_ms": _point_asof(point, snapshot),
        "recorded_at_ms": _point_recorded(point),
        **({"public_candidate": _snapshot_candidate(point, snapshot),
            "public_packet": _public_packet(point)} if include_raw else {"evidence_deferred": True}),
    }


def _format_value(value: Any, unit: str | None = None) -> str:
    if value is None:
        return "缺失"
    if isinstance(value, (int, float)) and unit in {"pct", "个百分点"}:
        text = f"{value:.3f}".rstrip("0").rstrip(".")
        if text == "-0":
            text = "0"
    elif isinstance(value, float):
        text = f"{value:.10g}"
    else:
        text = str(value)
    return f"{text} {unit}" if unit else text


def _fact(value: Any, *, unit: str | None, asof_ms: int | None, text_cn: str | None,
          meta: Mapping[str, Any], raw: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "text_cn": text_cn or _format_value(value, unit),
        "value": deepcopy(value),
        "unit": unit,
        "asof_ms": asof_ms,
        "meta": {key: deepcopy(item) for key, item in meta.items() if item is not None},
        "raw": public_replay.scrub_public(raw or {}),
    }


def _wire_fact(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return {key: deepcopy(item.get(key)) for key in ("text_cn", "value", "unit", "asof_ms")}


def _meta_compatible(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    for key in ("semantic_key", "method", "product", "source", "window", "unit"):
        left = a.get(key)
        right = b.get(key)
        if left is None or right is None:
            return False
        if left != right:
            return False
    return True


def _numeric(value: Any) -> float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) else None


def _change_cn(t0: dict[str, Any] | None, t1: dict[str, Any] | None) -> tuple[str, str]:
    if t0 is None and t1 is None:
        return "两端均缺失。", "missing"
    if t0 is None:
        return "基线未记录，后续首次出现；不按 0 计算。", "new_fact"
    if t1 is None:
        return "后续未记录；不按 0 计算。", "latest_missing"
    if t0.get("value") is None and t1.get("value") is None:
        return "两端该值均缺失；未把缺失当作保持不变。", "missing"
    if t0.get("value") is None:
        return "基线该值缺失，最近首次可读；不按 0 计算。", "new_fact"
    if t1.get("value") is None:
        return "最近该值缺失，不能判断原值是否保持。", "latest_missing"
    if not _meta_compatible(_mapping(t0.get("meta")), _mapping(t1.get("meta"))):
        return "来源、方法、产品、窗口或单位不同；两端可读，但不给精确 delta。", "not_precise"
    if t0.get("asof_ms") is not None and t0.get("asof_ms") == t1.get("asof_ms"):
        return "来源时钟未变化，不计为新增确认。", "same_clock"
    if t0.get("asof_ms") is None or t1.get("asof_ms") is None:
        return "来源时点未留存；两端原值可读，不给精确变化。", "not_precise"
    left = _numeric(t0.get("value"))
    right = _numeric(t1.get("value"))
    if left is not None and right is not None:
        delta = right - left
        if delta == 0:
            return "数值未变化。", "unchanged"
        direction = "增加" if delta > 0 else "减少"
        unit = t0.get('unit') or t1.get('unit')
        if unit == "pct" and abs(delta) < 0.001:
            return ("微增" if delta > 0 else "微降") + "（小于 0.001 个百分点）。", "changed"
        return f"{direction} {_format_value(abs(delta), '个百分点' if unit == 'pct' else unit)}。", "changed"
    if t0.get("value") == t1.get("value"):
        return "内容未变化。", "unchanged"
    return "内容发生变化；仅按原字段并列，不推断支持强弱。", "changed"


def _row(key: str, group: str, label_cn: str, t0: dict[str, Any] | None,
         t1: dict[str, Any] | None, meaning_cn: str) -> dict[str, Any]:
    change_cn, status = _change_cn(t0, t1)
    return {
        "key": key,
        "group": group,
        "label_cn": label_cn,
        "t0": _wire_fact(t0),
        "t1": _wire_fact(t1),
        "change_cn": change_cn,
        "meaning_cn": meaning_cn,
        "status": status,
        "raw": public_replay.scrub_public({
            "t0": _mapping(t0).get("raw") if t0 else None,
            "t1": _mapping(t1).get("raw") if t1 else None,
            "meta0": _mapping(t0).get("meta") if t0 else None,
            "meta1": _mapping(t1).get("meta") if t1 else None,
        }),
    }


def _spot(snapshot: Mapping[str, Any]) -> float | None:
    return _number(_dig(snapshot, ("economics", "valuation_spot_usd"))) or _number(_dig(snapshot, ("market", "reference_price_usd")))


def _quote_source_clock(snapshot: Mapping[str, Any]) -> int | None:
    quote = _mapping(_dig(snapshot, ("market", "quote")))
    times = [
        value for value in (_mapping(quote.get("exchange_times_ms")) or {}).values()
        if isinstance(value, int) and not isinstance(value, bool)
    ]
    if times:
        return max(times)
    return None


def _distance_fact(snapshot: Mapping[str, Any], boundary: Any, kind: str) -> dict[str, Any] | None:
    contract = _mapping(snapshot.get("contract"))
    quote = _mapping(_dig(snapshot, ("market", "quote")))
    distance = core.signed_boundary_distance(
        contract.get("side"),
        _spot(snapshot),
        boundary,
        boundary_kind=kind,
    )
    if distance is None:
        return None
    key = f"liability.{kind}_distance_pct"
    return _fact(
        distance["signed_distance_pct"],
        unit="pct",
        asof_ms=_quote_source_clock(snapshot),
        text_cn=f"{_format_value(distance['signed_distance_pct'], 'pct')}；边界 {_format_value(distance['boundary_usd'], 'USD')}",
        meta={"semantic_key": key, "method": "signed_boundary_distance", "product": "underwriting_snapshot",
              "source": _dig(snapshot, ("market", "reference_source")) or "snapshot.market",
              "window": quote.get("kind") or "reference_quote", "unit": "pct"},
        raw={**distance, "quote_gap_reasons": quote.get("gap_reasons"), "exchange_times_ms": quote.get("exchange_times_ms")},
    )


def _value_fact(snapshot: Mapping[str, Any], path: Sequence[str], key: str, label: str, unit: str | None,
                *, method: str = "snapshot_field", asof_ms: int | None = None,
                source: str = "snapshot", window: str = "point") -> dict[str, Any] | None:
    value = _dig(snapshot, path)
    if value is None:
        return None
    return _fact(
        value,
        unit=unit,
        asof_ms=asof_ms,
        text_cn=f"{label}：{_format_value(value, unit)}",
        meta={"semantic_key": key, "method": method, "product": "underwriting_snapshot",
              "source": source, "window": window, "unit": unit if unit is not None else "mixed"},
        raw={"path": ".".join(path), "value": value},
    )


def _research_items(candidate: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    context = _mapping(_mapping(candidate.get("current_questions")).get("research_context"))
    items = context.get("items")
    return [item for item in items if isinstance(item, Mapping)] if isinstance(items, list) else []


def _research_item(candidate: Mapping[str, Any], *, item_id: str | None = None,
                   source_asset: str | None = None) -> Mapping[str, Any] | None:
    for item in _research_items(candidate):
        if item_id is not None and item.get("id") == item_id:
            return item
        if source_asset is not None and item.get("source_asset") == source_asset:
            return item
    return None


def _item_source_clock(item: Mapping[str, Any]) -> int | None:
    if item.get("source_clock_status") == "RETAINED":
        return _int(item.get("source_observed_ms")) or _int(item.get("asof_ms"))
    return None


def _research_context_fact(candidate: Mapping[str, Any], key: str, label: str,
                           *, item_id: str | None = None, source_asset: str | None = None) -> dict[str, Any] | None:
    item = _research_item(candidate, item_id=item_id, source_asset=source_asset)
    if item is None:
        return None
    value = deepcopy(item.get("readings")) if isinstance(item.get("readings"), Mapping) else item.get("text_cn")
    return _fact(
        value,
        unit=None,
        asof_ms=_item_source_clock(item),
        text_cn=str(item.get("text_cn") or item.get("producer_label_cn") or label),
        meta={
            "semantic_key": key,
            "method": item.get("source_ref") or item.get("source_asset"),
            "product": item.get("source_asset"),
            "source": item.get("source_ref"),
            "window": item.get("source_clock_status") or item.get("freshness_status"),
            "unit": "mixed",
        },
        raw=item,
    )


def _routing_relations_fact(candidate: Mapping[str, Any]) -> dict[str, Any] | None:
    routing = _mapping(_mapping(candidate.get("wpem_evidence")).get("evidence_routing"))
    if not routing:
        return None
    value = {
        "selection_summary": deepcopy(routing.get("selection_summary")),
        "dependency_blocks": deepcopy(routing.get("dependency_blocks")),
        "limitations": deepcopy(routing.get("limitations")),
    }
    return _fact(
        value,
        unit=None,
        asof_ms=_int(_dig(routing, ("binding", "decision_asof_ms"))),
        text_cn="已保留同一输入的支持、反证、资料限制和直接引用。",
        meta={"semantic_key": "context.routing_relations", "method": routing.get("schema"),
              "product": "wpem_evidence.evidence_routing", "source": routing.get("routing_hash"),
              "window": routing.get("loop_digest") or "current_frozen_input", "unit": "mixed"},
        raw=routing,
    )


def _window_fact(candidate: Mapping[str, Any]) -> dict[str, Any] | None:
    evidence = _mapping(candidate.get("wpem_evidence"))
    window = _mapping(evidence.get("same_candidate_window"))
    if not window:
        return None
    coverage = _mapping(window.get("coverage_summary"))
    value = {
        "path": _dig(window, ("LevelDeltaPath", "path")),
        "coverage_summary": deepcopy(coverage),
        "summary_cn": _mapping(candidate.get("wpem_summary")).get("path_cn"),
    }
    return _fact(
        value,
        unit=None,
        asof_ms=_int(evidence.get("decision_asof_ms")),
        text_cn=str(_mapping(candidate.get("wpem_summary")).get("path_cn") or _dig(window, ("LevelDeltaPath", "path", "status")) or "同候选窗口"),
        meta={"semantic_key": "context.latest_window", "method": "same_candidate_window",
              "product": "wpem_evidence", "source": evidence.get("evidence_hash"),
              "window": _dig(window, ("LevelDeltaPath", "path", "status")) or "window_status_unknown",
              "unit": "mixed"},
        raw=window,
    )


def _question_fact(candidate: Mapping[str, Any], question: str, asof_ms: int | None) -> dict[str, Any] | None:
    current_questions = _mapping(candidate.get("current_questions"))
    body = _mapping(current_questions.get(question))
    if not body:
        return None
    value = {
        "summary_cn": body.get("summary_cn"),
        "evidence_refs": deepcopy(body.get("evidence_refs") or body.get("routing_evidence_ids") or []),
    }
    if not value["summary_cn"] and not value["evidence_refs"]:
        return None
    return _fact(
        value,
        unit=None,
        asof_ms=asof_ms,
        text_cn=str(value["summary_cn"] or value["evidence_refs"]),
        meta={"semantic_key": f"opinion.{question}.direct_refs", "method": "direct_refs_only",
              "product": "current_questions", "source": "candidate.current_questions",
              "window": question, "unit": "mixed"},
        raw=value,
    )


def _close_fee(point: Mapping[str, Any]) -> dict[str, Any]:
    packet = _mapping(point.get("packet"))
    raw = _mapping(packet.get("raw_payload"))
    candidate = _mapping(raw.get("candidate"))
    metadata = _mapping(raw.get("metadata"))
    market = _mapping(raw.get("market"))
    try:
        short_name = str(candidate["short_instrument"])
        long_name = str(candidate["long_instrument"])
        fee_detail = real_replay._entry_fee(
            {
                "short_metadata": _mapping(metadata.get("long")),
                "long_metadata": _mapping(metadata.get("short")),
                "short_name": long_name,
                "long_name": short_name,
            },
            {
                long_name: _mapping(market.get("long_book")),
                short_name: _mapping(market.get("short_book")),
            },
            float(candidate["quantity_btc"]),
        )
        detail = deepcopy(fee_detail)
        detail.pop("delivery_fee_btc", None)
        detail.pop("delivery_fee_rule", None)
        detail.pop("total_fee_btc", None)
        detail["meaning"] = "same configured taker-fee support range, applied to close legs: sell long bid and buy short ask"
        return {
            "basis": "configured_assumption",
            "close_fee_btc": fee_detail["entry_fee_btc"],
            "covers": ["early_exit"],
            "detail": detail,
        }
    except (KeyError, TypeError, ValueError):
        return {"basis": "unknown", "gap_reasons": ["close_fee_unavailable"]}


def _close_quote(point: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = _point_snapshot(point)
    packet = _mapping(point.get("packet"))
    raw = _mapping(packet.get("raw_payload"))
    market = _mapping(raw.get("market"))
    asof = _point_asof(point, snapshot) or _dig(snapshot, ("market", "valuation_ts_ms")) or 0
    return core.reference_close_quote(_mapping(snapshot.get("contract")), market, int(asof), fee=_close_fee(point))


def _quote_clock_from_quote(quote: Mapping[str, Any]) -> int | None:
    times = [
        value for value in (_mapping(quote.get("exchange_times_ms")) or {}).values()
        if isinstance(value, int) and not isinstance(value, bool)
    ]
    return max(times) if times else None


def _close_fact(close_quote: Mapping[str, Any], path: str, key: str, label: str) -> dict[str, Any] | None:
    value = close_quote.get(path)
    if value is None and path not in close_quote:
        return None
    return _fact(
        value,
        unit="BTC",
        asof_ms=_quote_clock_from_quote(close_quote),
        text_cn=f"{label}：{_format_value(value, 'BTC')}",
        meta={"semantic_key": key, "method": "close_book_estimate", "product": "two_leg_public_book",
              "source": "close_quote.public_book", "window": close_quote.get("kind") or "close_quote",
              "unit": "BTC"},
        raw=close_quote,
    )


def _facts(point: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    snapshot = _point_snapshot(point)
    candidate = _mapping(point.get("candidate"))
    asof = _point_asof(point, snapshot)
    contract = _mapping(snapshot.get("contract"))
    quote = _mapping(_dig(snapshot, ("market", "quote")))
    quote_clock = _quote_source_clock(snapshot)
    quote_source = _dig(snapshot, ("market", "reference_source")) or "snapshot.market.quote"
    quote_window = quote.get("kind") or "reference_quote"
    fee = _mapping(snapshot.get("fee"))
    risk = _mapping(snapshot.get("risk"))
    close = _close_quote(point)
    out: dict[str, dict[str, Any]] = {}
    for key, fact in {
        "liability.short_distance": _distance_fact(snapshot, contract.get("short_strike_usd"), "short"),
        "liability.protection_distance": _distance_fact(snapshot, contract.get("long_strike_usd"), "protection"),
        "quote.visible_credit_btc": _value_fact(snapshot, ("economics", "visible_credit_btc"), "quote.visible_credit_btc", "当前新开可见信用", "BTC", asof_ms=quote_clock, source=str(quote_source), window=str(quote_window)),
        "quote.net_credit_after_entry_fee_btc": _value_fact(snapshot, ("economics", "net_credit_after_entry_fee_btc"), "quote.net_credit_after_entry_fee_btc", "新开扣入场费后信用", "BTC", asof_ms=quote_clock, source=str(quote_source), window=str(quote_window)),
        "fee.entry_fee_btc": _value_fact(snapshot, ("fee", "entry_fee_btc"), "fee.entry_fee_btc", "入场费", "BTC", asof_ms=quote_clock, source=str(fee.get("basis") or "snapshot.fee"), window=",".join(sorted(str(item) for item in fee.get("covers") or [])) or str(quote_window)),
        "risk.mu_ref_btc": _value_fact(snapshot, ("economics", "mu_ref_btc"), "risk.mu_ref_btc", "冻结 μ_ref", "BTC", asof_ms=_int(risk.get("input_ts_ms")), source=str(risk.get("source_kind") or "snapshot.risk"), window=str(risk.get("model_hash") or risk.get("source_fact_hash") or "risk_identity_missing")),
        "risk.reference_margin_btc": _value_fact(snapshot, ("economics", "reference_margin_btc"), "risk.reference_margin_btc", "冻结 M_ref", "BTC", asof_ms=_int(risk.get("input_ts_ms")), source=str(risk.get("source_kind") or "snapshot.risk"), window=str(risk.get("model_hash") or risk.get("source_fact_hash") or "risk_identity_missing")),
        "risk.status": _value_fact(snapshot, ("risk", "status"), "risk.status", "风险参考状态", None, asof_ms=_int(risk.get("input_ts_ms")), source=str(risk.get("source_kind") or "snapshot.risk"), window=str(risk.get("model_hash") or risk.get("source_fact_hash") or "risk_identity_missing")),
        "close.debit_btc": _close_fact(close, "debit_btc", "close.debit_btc", "平仓借记估计"),
        "close.net_debit_after_close_fee_btc": _close_fact(close, "net_debit_after_close_fee_btc", "close.net_debit_after_close_fee_btc", "含平仓费借记估计"),
        "context.latest_window": _window_fact(candidate),
        "context.tmv_price_path": _research_context_fact(candidate, "context.tmv_price_path", "TMV 路径", item_id="context.tmv_price_path"),
        "context.funding": _research_context_fact(candidate, "context.funding", "资金费率", item_id="context.funding"),
        "context.skew": _research_context_fact(candidate, "context.skew", "Skew", item_id="context.skew"),
        "context.active_flow": _research_context_fact(candidate, "context.active_flow", "主动流", item_id="context.active_flow"),
        "context.option_space": _research_context_fact(candidate, "context.option_space", "期权空间/Gamma", item_id="context.option_space"),
        "context.macro": _research_context_fact(candidate, "context.macro", "宏观压力", item_id="context.macro"),
        "context.routing_relations": _routing_relations_fact(candidate),
        "opinion.q1.direct_refs": _question_fact(candidate, "q1", asof),
        "opinion.q2.direct_refs": _question_fact(candidate, "q2", asof),
    }.items():
        if fact is not None:
            out[key] = fact
    return out


def _baseline_breakevens(snapshot: Mapping[str, Any]) -> list[float]:
    roots = _dig(snapshot, ("liability", "breakeven_usd"))
    if not isinstance(roots, list):
        return []
    return [value for value in (_number(item) for item in roots) if value is not None]


def _unknown_baseline_breakeven_row(baseline: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    key = "liability.baseline_breakeven_unknown"
    t0 = _fact(
        None,
        unit="USD",
        asof_ms=_quote_source_clock(baseline),
        text_cn="基线未形成原生盈亏平衡价",
        meta={"semantic_key": key, "method": "fixed_baseline_breakeven", "product": "underwriting_snapshot",
              "source": "snapshot.liability.breakeven_usd", "window": "missing", "unit": "USD"},
        raw={"breakeven_usd": _dig(baseline, ("liability", "breakeven_usd"))},
    )
    row = _row(
        key,
        "liability",
        "基线原生盈亏平衡价",
        t0,
        None,
        "t0 未形成原生 BE；后续新开信用不会回写这一冻结事实。",
    )
    row["status"] = "unknown"
    row["change_cn"] = "t0 未形成原生 BE；不按 0 计算，也不由 t1 新信用补写。"
    overview = {"boundary_usd": None, "status": "unknown", "t0": _wire_fact(t0), "t1": None}
    return row, overview


def _breakeven_rows(baseline: Mapping[str, Any], latest: Mapping[str, Any] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    overview: list[dict[str, Any]] = []
    roots = _baseline_breakevens(baseline)
    if not roots:
        row, item = _unknown_baseline_breakeven_row(baseline)
        return [row], [item]
    for index, boundary in enumerate(roots):
        t0 = _distance_fact(baseline, boundary, f"baseline_breakeven_{index + 1}")
        t1 = _distance_fact(latest, boundary, f"baseline_breakeven_{index + 1}") if latest is not None else None
        key = f"liability.baseline_breakeven_{index + 1}_distance"
        if t0 is not None:
            t0["meta"]["semantic_key"] = key
        if t1 is not None:
            t1["meta"]["semantic_key"] = key
        rows.append(_row(
            key,
            "liability",
            f"基线原生盈亏平衡线 {index + 1}",
            t0,
            t1,
            "该边界固定来自 t0 原生快照；t1 新信用不改写冻结 BE。",
        ))
        overview.append({
            "boundary_usd": boundary,
            "t0": _wire_fact(t0),
            "t1": _wire_fact(t1),
        })
    return rows, overview


def _reference_result(baseline: Mapping[str, Any], close_quote: Mapping[str, Any]) -> dict[str, Any]:
    entry_credit = _number(_dig(baseline, ("economics", "visible_credit_btc")))
    entry_fee = _number(_dig(baseline, ("fee", "entry_fee_btc")))
    entry_after_fee = _number(_dig(baseline, ("economics", "net_credit_after_entry_fee_btc")))
    gaps: list[str] = []
    if entry_credit is None:
        gaps.append("baseline_entry_credit_missing")
    if entry_fee is None:
        gaps.append("baseline_entry_fee_missing")
    if entry_after_fee is None and entry_credit is not None and entry_fee is not None:
        entry_after_fee = entry_credit - entry_fee
    if entry_after_fee is None:
        gaps.append("baseline_entry_after_fee_missing")
    close_debit = _number(close_quote.get("debit_btc"))
    close_fee = _number(close_quote.get("close_fee_btc"))
    if close_debit is None:
        gaps.append("close_debit_missing")
    if close_fee is None:
        gaps.append("close_fee_missing")
    result = None
    if entry_after_fee is not None and close_debit is not None and close_fee is not None:
        result = entry_after_fee - close_debit - close_fee
    return public_replay.scrub_public({
        "kind": "entry_minus_reference_close_estimate",
        "formula": "t0_entry_credit_btc - t0_entry_fee_btc - reference_close_debit_btc - reference_close_fee_btc",
        "entry_credit_btc": entry_credit,
        "entry_fee_btc": entry_fee,
        "entry_after_fee_btc": entry_after_fee,
        "close_debit_btc": close_debit,
        "close_fee_btc": close_fee,
        "reference_result_btc": result,
        "asof_ms": _quote_clock_from_quote(close_quote),
        "source_book_hash": close_quote.get("source_book_hash"),
        "gap_reasons": sorted(set([*gaps, *(close_quote.get("gap_reasons") or [])])),
        "actual_trade_pnl_recorded": False,
        "meaning_cn": "这是按冻结 t0 入场信用和当前参考平仓报价估算的账本差额，不是实际成交 PnL。",
    })


def _reference_result_row(reference: Mapping[str, Any]) -> dict[str, Any]:
    fact = _fact(
        reference.get("reference_result_btc"),
        unit="BTC",
        asof_ms=_int(reference.get("asof_ms")),
        text_cn=f"参考结果：{_format_value(reference.get('reference_result_btc'), 'BTC')}",
        meta={"semantic_key": "reference.reference_result_btc", "method": "entry_minus_close_estimate",
              "product": "locked_candidate_followup", "source": "t0_snapshot_and_reference_close_quote",
              "window": reference.get("source_book_hash") or "quote_clock_missing", "unit": "BTC"},
        raw=reference,
    )
    row = _row(
        "reference.reference_result_btc",
        "quote_fee",
        "t0信用 - 入场费 - 当前平仓借记/费",
        None,
        fact,
        "仅为引用账本估算，不是实际成交 PnL；缺任一腿、费用或深度则显示 unknown。",
    )
    row["status"] = "reference_only" if reference.get("reference_result_btc") is not None else "unknown"
    row["change_cn"] = "按 t0 入场信用 - 入场费 - 参考平仓借记 - 平仓费计算；未记录实际成交。"
    return row


_ROW_SPECS = (
    ("liability.short_distance", "liability", "短腿责任边界距离", "正值表示仍在该侧不利边界外；负值表示已经越过短腿责任边界。"),
    ("liability.protection_distance", "liability", "保护腿边界距离", "保护腿开始抵消短腿行权内在责任，仍非清算线。"),
    ("quote.visible_credit_btc", "quote_fee", "新开可见信用", "这是同一保单如果重新新开的公开盘口信用；不是已经成交，也不是平仓成本。"),
    ("quote.net_credit_after_entry_fee_btc", "quote_fee", "新开扣入场费后信用", "只说明新开视角的可见信用，不用于改写 t0 原生 BE。"),
    ("fee.entry_fee_btc", "quote_fee", "入场费", "入场费、delivery fee、平仓费分开；缺失时只限制本行。"),
    ("close.debit_btc", "quote_fee", "平仓借记估计", "使用 short ask 买回、long bid 卖出；数值越大表示平仓支出越高。"),
    ("close.net_debit_after_close_fee_btc", "quote_fee", "含平仓费借记估计", "只在平仓费支持范围明确时计算；不把 delivery fee 当平仓费。"),
    ("risk.mu_ref_btc", "risk", "冻结 μ_ref", "风险参考缺失只限制本行和 M_ref，不触发新的风险判断。"),
    ("risk.reference_margin_btc", "risk", "冻结 M_ref", "M_ref 是冻结风险参考下的补偿余量，不是交易许可。"),
    ("risk.status", "risk", "风险参考状态", "状态仅按既有 risk 字段并列。"),
    ("context.latest_window", "context", "同候选观察窗口", "只报告已保留的同候选观察点与窗口限制，不称完整 Path。"),
    ("context.tmv_price_path", "context", "TMV 价格路径", "按 research_context 的既有来源、方法和窗口显示；换源换窗不给精确 delta。"),
    ("context.funding", "context", "资金费率", "只保留已有来源事实，不补造缺失源钟。"),
    ("context.skew", "context", "Skew", "只保留已有来源事实，不补造缺失源钟。"),
    ("context.active_flow", "context", "主动流", "只保留已有来源事实，不按中文情绪推断支持变强或变弱。"),
    ("context.option_space", "context", "期权空间/Gamma", "只比较同源同窗口的期权空间和 Gamma 背景。"),
    ("context.macro", "context", "宏观压力", "宏观背景只作为已有事实并列，不升级为保单结论。"),
    ("context.routing_relations", "context", "证据路由关系", "展示已有 WPEM 证据关系和依赖组，不新增评分或风险灯。"),
    ("opinion.q1.direct_refs", "opinion", "原 Q1 观点与直接 refs", "只保留原观点和直接引用，不从中文情绪或数组位置推断支持变化。"),
    ("opinion.q2.direct_refs", "opinion", "原 Q2 观点与直接 refs", "只保留原观点和直接引用，不从中文情绪或数组位置推断支持变化。"),
)


def _observation_point_id(item: Mapping[str, Any]) -> Any:
    for key in ("point_id", "id", "store_point_id"):
        if item.get(key) is not None:
            return item.get(key)
    nested = item.get("point")
    if isinstance(nested, Mapping):
        return _point_id(nested, _point_snapshot(nested))
    return None


def _observation_clock(item: Mapping[str, Any]) -> int | None:
    for key in ("source_clock_ms", "asof_ms", "decision_asof_ms"):
        value = _int(item.get(key))
        if value is not None:
            return value
    nested = item.get("point")
    if isinstance(nested, Mapping):
        return _point_asof(nested, _point_snapshot(nested))
    return None


def _process_int(detail: Mapping[str, Any], key: str) -> int | None:
    summary = _mapping(detail.get("summary"))
    episode = _mapping(detail.get("episode"))
    return _int(detail.get(key)) or _int(summary.get(key)) or _int(episode.get(key))


def _process(detail: Mapping[str, Any]) -> dict[str, Any]:
    observations = detail.get("observations") if isinstance(detail.get("observations"), list) else []
    point_rows: list[Mapping[str, Any]] = []
    gap_rows: list[Mapping[str, Any]] = []
    other_rows: list[Mapping[str, Any]] = []
    seen: set[tuple[Any, int | None]] = set()
    times: list[int] = []
    for item in observations:
        if not isinstance(item, Mapping):
            continue
        kind = str(item.get("kind") or item.get("type") or "").lower()
        if kind == "point":
            point_id = _observation_point_id(item)
            clock = _observation_clock(item)
            identity = (point_id, clock)
            if identity in seen:
                continue
            seen.add(identity)
            point_rows.append(item)
            if clock is not None:
                times.append(clock)
        elif kind == "gap":
            gap_rows.append(item)
        else:
            other_rows.append(item)
    times = sorted(set(times))
    gaps = [b - a for a, b in zip(times, times[1:])]
    count = len(point_rows)
    replayed = detail.get("intermediate_packets_replayed")
    if not isinstance(replayed, bool):
        replayed = _mapping(detail.get("summary")).get("intermediate_packets_replayed")
    index_range = {
        "baseline_decision_asof_ms": _process_int(detail, "baseline_decision_asof_ms"),
        "latest_decision_asof_ms": _process_int(detail, "latest_decision_asof_ms"),
        "first_observed_point_ms": times[0] if times else None,
        "last_observed_point_ms": times[-1] if times else None,
        "intermediate_packets_replayed": replayed if isinstance(replayed, bool) else None,
    }
    baseline_id = _mapping(detail.get("episode")).get("t0_baseline_point_id")
    after_lock = [row for row in point_rows if _observation_point_id(row) != baseline_id]
    archived_times = sorted(t for row in after_lock if (t := _int(row.get("recorded_at_ms"))) is not None)
    return {
        "summary_cn": f"索引范围内已记录 {count} 个已锁定观察点；gap 单列展示，不把采样前等待、未回放中间包或 gap 当作完整 Path 覆盖。",
        "observed_count": count,
        "first_ms": times[0] if times else None,
        "last_ms": times[-1] if times else None,
        "max_gap_ms": max(gaps) if gaps else 0,
        "post_lock_observed_count": len(after_lock),
        "post_lock_first_recorded_ms": archived_times[0] if archived_times else None,
        "post_lock_last_recorded_ms": archived_times[-1] if archived_times else None,
        "post_lock_span_ms": archived_times[-1] - archived_times[0] if len(archived_times) > 1 else 0,
        "index_range": index_range,
        "observations": public_replay.scrub_public(point_rows),
        "gaps": public_replay.scrub_public(gap_rows),
        "other_events": public_replay.scrub_public(other_rows),
    }


def _review_status_accepted(value: Any) -> bool:
    text = str(value or "").strip().upper()
    if text in {"", "NOT_ATTACHED", "REJECTED", "STALE", "FAILED", "FAIL", "ERROR"}:
        return False
    return text in {"ACCEPTED", "OK", "PASS", "RESEARCH_OK", "APPROVED"}


def public_review_explanation(candidate: Mapping[str, Any]) -> dict[str, Any] | None:
    """Whitelisted current accepted-review body strictly bound to this snapshot."""
    review_display = _mapping(candidate.get("review_display"))
    review = review_display.get("current")
    if not isinstance(review, Mapping):
        return None
    snapshot = _mapping(candidate.get("snapshot"))
    binding = _mapping(review.get("display_binding")) or _mapping(_dig(review, ("review", "display_binding")))
    if binding.get("selector_status") != "CURRENT":
        return None
    if binding.get("same_input") is not True:
        return None
    candidate_id = candidate.get("candidate_id") or snapshot.get("candidate_id")
    snapshot_id = snapshot.get("snapshot_id")
    if candidate_id is None or snapshot_id is None:
        return None
    if binding.get("candidate_id") != candidate_id:
        return None
    if binding.get("snapshot_id") != snapshot_id:
        return None
    body = _mapping(review.get("review")) or review
    status_values = (review.get("status"), body.get("status"))
    if not any(_review_status_accepted(item) for item in status_values):
        return None
    rejected = {"NOT_ATTACHED", "REJECTED", "STALE", "FAILED", "FAIL", "ERROR"}
    if any(str(item or "").strip().upper() in rejected for item in status_values):
        return None
    allowed = {
        "status",
        "candidate_id",
        "snapshot_id",
        "reviewed_snapshot_id",
        "reviewed_at_ms",
        "response_protocol",
        "applicability",
        "summary_cn",
        "q1",
        "q2",
        "llm_explanation",
        "explanation",
        "selected_claim_ids",
        "selected_counter_ids",
        "selected_gap_ids",
        "selected_reassess_condition_ids",
        "display_binding",
        "production_attach_allowed",
        "execution_allowed",
    }
    out = {key: deepcopy(review[key]) for key in allowed if key in review}
    for key in allowed:
        if key not in out and key in body:
            out[key] = deepcopy(body[key])
    if not out:
        return None
    return public_replay.scrub_public(out)


def _explanations(point: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if point is None:
        return None
    candidate = _mapping(point.get("candidate"))
    return public_replay.scrub_public({
        "current_questions": deepcopy(candidate.get("current_questions")),
        "wpem_summary": deepcopy(candidate.get("wpem_summary")),
        "accepted_review": public_review_explanation(candidate),
        "direct_refs_only": True,
    })


SERIES_SCHEMA = "locked_candidate_series@1.0.0"
# 2: adds Deribit-style floating PnL from leg marks; rows of an older version are
# recomputed while the point file still exists.
SERIES_METRICS_VERSION = 2


def _leg_marks(point: Mapping[str, Any]) -> dict[str, float | None]:
    """Deribit mark price of each leg (BTC per contract) from the retained public books."""
    market = _mapping(_mapping(_mapping(point.get("packet")).get("raw_payload")).get("market"))
    return {leg: _number(_mapping(market.get(leg + "_book")).get("mark_price")) for leg in ("short", "long")}


def floating_pnl(point: Mapping[str, Any], baseline: Mapping[str, Any]) -> dict[str, Any]:
    """Floating PnL the way Deribit shows it for an open option position: per leg
    (mark - entry) x signed size, summed. Entry is the lock-time executable price of
    each leg (short sold at the bid, long bought at the ask). No fees, no close cost:
    a held position is settled at delivery, not closed."""
    base = _point_snapshot(baseline)
    quote = _mapping(_dig(base, ("market", "quote")))
    quantity = _number(_dig(base, ("contract", "quantity_btc")))
    entry_short = _number(quote.get("short_sale_btc"))
    entry_long = _number(quote.get("long_purchase_btc"))
    entry_basis = "lock_quote_depth"
    if (entry_short is None or entry_long is None) and quantity is not None:
        # The lock-time quote can be withheld for a quality gap (e.g. leg time skew) while
        # both books are still there: use their top of book, as a fill at lock would have.
        books = _mapping(_mapping(_mapping(baseline.get("packet")).get("raw_payload")).get("market"))
        bid = _number(_mapping(books.get("short_book")).get("best_bid_price"))
        ask = _number(_mapping(books.get("long_book")).get("best_ask_price"))
        if bid is not None and ask is not None:
            entry_short, entry_long, entry_basis = bid * quantity, ask * quantity, "lock_top_of_book"
    marks = _leg_marks(point)
    gaps = [name for name, value in (("entry_short_missing", entry_short), ("entry_long_missing", entry_long),
                                     ("quantity_missing", quantity), ("short_mark_missing", marks["short"]),
                                     ("long_mark_missing", marks["long"])) if value is None]
    value = remaining = None
    # A credit spread's profit is capped at the entry credit; what is still to be earned
    # is the spread's current mark value, i.e. max profit minus the floating PnL.
    max_profit = entry_short - entry_long if entry_short is not None and entry_long is not None else None
    if not gaps:
        remaining = (marks["short"] - marks["long"]) * quantity
        value = max_profit - remaining
    return {"float_pnl_btc": value, "short_mark_btc": marks["short"], "long_mark_btc": marks["long"],
            "entry_short_btc": entry_short, "entry_long_btc": entry_long, "entry_basis": entry_basis,
            "max_profit_btc": max_profit,
            "remaining_credit_btc": remaining, "float_gaps": gaps}


def _reading(candidate: Mapping[str, Any], prefix: str, path: Sequence[str]) -> Any:
    for item in _research_items(candidate):
        if str(item.get("id") or "").startswith(prefix):
            value = _dig(_mapping(item.get("readings")), path)
            if value is not None:
                return value
    return None


def point_metrics(point: Mapping[str, Any], baseline: Mapping[str, Any]) -> dict[str, Any]:
    """Compact, numbers-first reading of one retained point against the fixed t0.

    Distances follow core.signed_boundary_distance (percent of the boundary,
    positive = still outside). The reference result uses the t0 entry credit
    and this point's close quote, exactly as the follow-up row does.
    """
    snapshot = _point_snapshot(point)
    base = _point_snapshot(baseline)
    candidate = _mapping(point.get("candidate"))
    contract = _mapping(snapshot.get("contract"))
    side = contract.get("side")
    spot = _spot(snapshot)

    def distance(boundary: Any, kind: str) -> float | None:
        found = core.signed_boundary_distance(side, spot, boundary, boundary_kind=kind)
        return None if found is None else found["signed_distance_pct"]

    breakevens = _baseline_breakevens(base)
    close = _close_quote(point)
    reference = _reference_result(base, close)
    quote = _mapping(_dig(snapshot, ("market", "quote")))
    gate = _mapping(candidate.get("space_gate"))
    return {
        "asof_ms": _point_asof(point, snapshot),
        "spot_usd": spot,
        "short_distance_pct": distance(contract.get("short_strike_usd"), "short"),
        "protection_distance_pct": distance(contract.get("long_strike_usd"), "protection"),
        "breakeven_distance_pct": distance(breakevens[0], "breakeven") if breakevens else None,
        "visible_credit_btc": _number(_dig(snapshot, ("economics", "visible_credit_btc"))),
        "close_debit_btc": _number(close.get("debit_btc")),
        "close_fee_btc": _number(close.get("close_fee_btc")),
        "reference_result_btc": _number(reference.get("reference_result_btc")),
        "reference_gaps": list(reference.get("gap_reasons") or []),
        "quote_gaps": list(quote.get("gap_reasons") or []),
        "space_gate_state": gate.get("state") or ("STRUCTURE_ELIGIBLE" if gate.get("eligible") is True
                                                   else "STRUCTURE_BLOCKED" if gate.get("eligible") is False else None),
        "flow_direction": _reading(candidate, "context.active_flow", ("combined", "direction")),
        "flow_ready": _reading(candidate, "context.active_flow", ("combined", "data_ready")),
        "tmv_state": _reading(candidate, "context.tmv_price_path", ("tmv_state",)),
        "gamma_regime": _reading(candidate, "context.option_space", ("gamma_regime", "regime")),
        "macro_regime": _reading(candidate, "context.macro", ("macro_regime",)),
        **floating_pnl(point, baseline),
        "metrics_version": SERIES_METRICS_VERSION,
    }


def build_followup(detail: Mapping[str, Any], *, now_ms: int, include_raw: bool = True) -> dict[str, Any]:
    """Build the public same-candidate follow-up contract."""
    if not isinstance(detail, Mapping):
        raise ValueError("locked_candidate_detail_required")
    baseline = detail.get("baseline")
    if not isinstance(baseline, Mapping):
        raise ValueError("baseline_point_required")
    latest = detail.get("latest")
    latest_point = latest if isinstance(latest, Mapping) else None
    baseline_snapshot = _point_snapshot(baseline)
    if not baseline_snapshot:
        raise ValueError("baseline_snapshot_missing")
    latest_snapshot = _point_snapshot(latest_point) if latest_point else None
    if latest_snapshot:
        if _contract_key(latest_snapshot) != _contract_key(baseline_snapshot):
            raise ValueError("locked_candidate_identity_mismatch")
        if latest_snapshot.get("candidate_id") != baseline_snapshot.get("candidate_id"):
            raise ValueError("locked_candidate_identity_mismatch")

    # Compare original numeric/accounting inputs. Only the explanatory candidate
    # source bodies are projected before building display rows.
    fact_baseline = baseline if include_raw else {**baseline, "candidate": reading_value(dict(_mapping(baseline.get("candidate"))))}
    fact_latest = latest_point if include_raw or latest_point is None else {**latest_point, "candidate": reading_value(dict(_mapping(latest_point.get("candidate"))))}
    t0_facts = _facts(fact_baseline)
    t1_facts = _facts(fact_latest) if fact_latest else {}
    rows = [
        _row(key, group, label, t0_facts.get(key), t1_facts.get(key), meaning)
        for key, group, label, meaning in _ROW_SPECS
        if key in t0_facts or key in t1_facts
    ]
    breakeven_rows, breakeven_distances = _breakeven_rows(baseline_snapshot, latest_snapshot)
    rows[2:2] = breakeven_rows

    overview_snapshot = latest_snapshot or baseline_snapshot
    close_estimate = _close_quote(latest_point or baseline)
    reference_result = _reference_result(baseline_snapshot, close_estimate)
    rows.append(_reference_result_row(reference_result))
    contract = _mapping(baseline_snapshot.get("contract"))
    episode = _mapping(detail.get("episode"))
    baseline_point_id = episode.get("t0_baseline_point_id") or _point_id(baseline, baseline_snapshot)
    latest_point_id = episode.get("latest_point_id") or (_point_id(latest_point, latest_snapshot) if latest_point and latest_snapshot else None)
    remaining = None
    expiry = _int(contract.get("expiry_ms"))
    if expiry is not None:
        remaining = max(0, expiry - int(now_ms))
    output = {
        "schema": SCHEMA,
        "episode": public_replay.scrub_public(deepcopy(detail.get("episode"))),
        "fixed_contract": public_replay.scrub_public({key: deepcopy(contract.get(key)) for key in (*_CONTRACT_KEYS, "short_strike_usd", "long_strike_usd", "width_usd")}),
        "baseline": _public_point(baseline, point_id=baseline_point_id, include_raw=include_raw),
        "latest": _public_point(latest_point, point_id=latest_point_id, include_raw=include_raw) if latest_point else None,
        "overview": {
            "short_distance": _wire_fact(_distance_fact(overview_snapshot, contract.get("short_strike_usd"), "short")),
            "baseline_breakeven_usd": _baseline_breakevens(baseline_snapshot),
            "breakeven_distances": breakeven_distances,
            "remaining_to_expiry_ms": remaining,
            "close_estimate": public_replay.scrub_public(close_estimate),
            "reference_result": public_replay.scrub_public(reference_result),
        },
        "rows": rows,
        "process": _process(detail),
        "explanations": {
            "baseline": _explanations(fact_baseline),
            "latest": _explanations(fact_latest),
        },
        "limitations_cn": [
            "本视图只比较同一锁定候选的已观察点，不代表完整路径或连续监控已覆盖。",
            "t0 原生盈亏平衡价固定保留；t1 新开信用和平仓估计分行显示。",
            "缺少来源、窗口、费用、深度或时钟时只限制对应行；不会补零或改写冻结事实。",
            "本视图不调用 LLM、不生成交易许可、不记录实际成交。",
        ],
    }
    if latest_point is None:
        output["limitations_cn"].append("尚无后续观察点。")
    if close_estimate.get("gap_reasons"):
        output["limitations_cn"].append("平仓估计存在报价或费用缺口：" + "、".join(close_estimate["gap_reasons"]))
    return public_replay.scrub_public(output)

