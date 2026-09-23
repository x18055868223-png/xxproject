"""Read-only display projection for Astra joint research assessments.

This module does not call models, trading APIs, or production materializers. It
turns a sealed ``astra_statistical_assessment@1.0.0`` object into a small reader
projection that the signal-audit page can show without exposing hashes or model
internals in the main text.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any

try:
    from astra_joint_contract import ASSESSMENT_SCHEMA
except ImportError:  # pragma: no cover - package-style imports in tests
    from tools.astra_joint_contract import ASSESSMENT_SCHEMA


DISPLAY_SCHEMA = "astra_joint_display@1.0.0"
SUMMARY_SCHEMA = "astra_joint_display_summary@1.0.0"
VALID_STATUS = {"available", "insufficient", "unavailable"}
QUOTE_STATUS = {"available", "insufficient", "unavailable", "not_collected"}
SIDES = ("put", "call")
MAX_QUOTE_AGE_MS = 10_000
MAX_LEG_SKEW_MS = 5_000



def canonical_payload(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_payload(value).encode("utf-8")).hexdigest()


def assessment_without_hash(assessment: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(assessment)
    payload.pop("assessment_hash", None)
    validation = payload.get("validation")
    if isinstance(validation, dict):
        validation.pop("assessment_hash", None)
    return payload


def compute_assessment_hash(assessment: dict[str, Any]) -> str:
    return _sha256(assessment_without_hash(assessment))


def projection_hash(payload: dict[str, Any]) -> str:
    clone = copy.deepcopy(payload)
    clone.pop("display_projection_hash", None)
    return _sha256(clone)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _optional_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if not _is_number(value):
        raise ValueError("numeric field must be finite")
    return float(value)


def _required_number(value: Any, field: str) -> float:
    if not _is_number(value):
        raise ValueError(f"{field} must be a finite number")
    return float(value)

def _close_enough(left: float, right: float, *, abs_tol: float = 1e-10, rel_tol: float = 1e-6) -> bool:
    return abs(left - right) <= max(abs_tol, abs(left) * rel_tol, abs(right) * rel_tol)


def _required_ms_number(mapping: dict[str, Any], field: str, owner: str) -> float:
    value = mapping.get(field)
    if not _is_number(value):
        raise ValueError(f"{owner}.{field} must be finite milliseconds")
    numeric = float(value)
    if numeric < 0:
        raise ValueError(f"{owner}.{field} must be non-negative")
    return numeric


def _validate_quote_timing(side_name: str, quote: dict[str, Any]) -> None:
    owner = f"{side_name}.quote"
    checked_at_ms = _epoch_ms(quote.get("checked_at_ms"), f"{owner}.checked_at_ms")
    observed_at = quote.get("observed_at_ms")
    if observed_at is not None and _epoch_ms(observed_at, f"{owner}.observed_at_ms") > checked_at_ms:
        raise ValueError(f"{owner}.observed_at_ms must not be later than checked_at_ms")
    if "age" in quote and "max_age_ms" not in quote and "age_ms" not in quote:
        raise ValueError(f"{owner}.age must declare millisecond units")
    if "skew" in quote and "leg_timestamp_skew_ms" not in quote and "skew_ms" not in quote:
        raise ValueError(f"{owner}.skew must declare millisecond units")
    max_age_ms = _required_ms_number(quote, "max_age_ms" if "max_age_ms" in quote else "age_ms", owner)
    skew_ms = _required_ms_number(quote, "leg_timestamp_skew_ms" if "leg_timestamp_skew_ms" in quote else "skew_ms", owner)
    if max_age_ms > MAX_QUOTE_AGE_MS:
        raise ValueError(f"{owner}.max_age_ms exceeds strict quote age limit")
    if skew_ms > MAX_LEG_SKEW_MS:
        raise ValueError(f"{owner}.leg_timestamp_skew_ms exceeds strict quote skew limit")


def _epoch_ms(value: Any, field: str) -> int:
    if isinstance(value, bool) or value is None or value == "":
        raise ValueError(f"{field} is required")
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        numeric = float(value)
    elif isinstance(value, str) and value.strip().replace(".", "", 1).isdigit():
        numeric = float(value.strip())
    else:
        raise ValueError(f"{field} must be epoch milliseconds")
    if numeric <= 0:
        raise ValueError(f"{field} must be positive")
    return int(numeric * 1000) if numeric < 100000000000 else int(numeric)


def _iso_from_ms(value: int | None) -> str:
    if not value:
        return ""
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()


def _card_as_of_ms(card: dict[str, Any] | None) -> int | None:
    if not card:
        return None
    identity = _as_dict(card.get("identity"))
    for value in (
        identity.get("as_of_ms"),
        identity.get("confirmed_time_ms"),
        card.get("confirmed_time_ms"),
        identity.get("confirmed_at"),
        card.get("created_at"),
    ):
        if value is None or value == "":
            continue
        if isinstance(value, str) and not value.strip().replace(".", "", 1).isdigit():
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
            return int(parsed.timestamp() * 1000)
        return _epoch_ms(value, "card_as_of_ms")
    return None


def _card_source_hash(card: dict[str, Any] | None) -> str:
    if not card:
        return ""
    identity = _as_dict(card.get("identity"))
    summary = _as_dict(card.get("signal_evidence_summary"))
    return str(
        identity.get("source_record_hash")
        or card.get("source_record_hash")
        or summary.get("source_record_hash")
        or _as_dict(card.get("producer_integrity")).get("record_hash")
        or _as_dict(card.get("integrity")).get("record_hash")
        or ""
    )


def _validate_reference(side_name: str, reference: dict[str, Any], as_of_ms: int) -> dict[str, float]:
    short = _required_number(reference.get("short_strike"), f"{side_name}.reference.short_strike")
    long = _required_number(reference.get("long_strike"), f"{side_name}.reference.long_strike")
    width = _required_number(reference.get("width"), f"{side_name}.reference.width")
    entry = _required_number(reference.get("entry_price"), f"{side_name}.reference.entry_price")
    expiry_ms = _epoch_ms(reference.get("expiry_ms"), f"{side_name}.reference.expiry_ms")
    if width <= 0:
        raise ValueError(f"{side_name}.reference.width must be positive")
    if entry <= 0:
        raise ValueError(f"{side_name}.reference.entry_price must be positive")
    if expiry_ms <= as_of_ms:
        raise ValueError(f"{side_name}.reference.expiry_ms must be after as_of_ms")
    if abs(abs(long - short) - width) > max(1e-8, width * 1e-6):
        raise ValueError(f"{side_name}.reference.width must match strikes")
    if side_name == "put" and not long < short:
        raise ValueError("put reference requires long_strike below short_strike")
    if side_name == "call" and not long > short:
        raise ValueError("call reference requires long_strike above short_strike")
    return {"short": short, "long": long, "width": width, "entry": entry, "expiry_ms": float(expiry_ms)}


def _validate_side(side_name: str, side: dict[str, Any], as_of_ms: int, assessment_status: str) -> None:
    status = str(side.get("status") or assessment_status).lower()
    if status not in VALID_STATUS:
        raise ValueError(f"{side_name}.status is invalid")
    expected_payout = None
    if status == "available":
        probability = _required_number(side.get("probability_positive"), f"{side_name}.probability_positive")
        if probability < 0 or probability > 1:
            raise ValueError(f"{side_name}.probability_positive must be between 0 and 1")
        conditional_loss = _required_number(side.get("conditional_positive_loss"), f"{side_name}.conditional_positive_loss")
        expected_loss = _required_number(side.get("expected_loss_normalized"), f"{side_name}.expected_loss_normalized")
        expected_payout = _required_number(side.get("expected_payout_btc"), f"{side_name}.expected_payout_btc")
        for field, value in (("conditional_positive_loss", conditional_loss), ("expected_loss_normalized", expected_loss), ("expected_payout_btc", expected_payout)):
            if value < 0:
                raise ValueError(f"{side_name}.{field} must be non-negative")
        if not _close_enough(probability * conditional_loss, expected_loss, abs_tol=1e-8, rel_tol=1e-5):
            raise ValueError(f"{side_name}.expected_loss_normalized must equal probability_positive times conditional_positive_loss")
        reference_values = _validate_reference(side_name, _as_dict(side.get("reference")), as_of_ms)
        scaled_payout = expected_loss * reference_values["width"] / reference_values["entry"]
        if not _close_enough(scaled_payout, expected_payout, abs_tol=1e-10, rel_tol=1e-5):
            raise ValueError(f"{side_name}.expected_payout_btc must equal expected_loss_normalized times width over entry_price")
    else:
        for field in ("probability_positive", "conditional_positive_loss", "expected_loss_normalized", "expected_payout_btc"):
            value = _optional_number(side.get(field))
            if value is not None and value < 0:
                raise ValueError(f"{side_name}.{field} must be non-negative")
        if side.get("reference"):
            _validate_reference(side_name, _as_dict(side.get("reference")), as_of_ms)

    quote = _as_dict(side.get("quote"))
    quote_status = str(quote.get("status") or "unavailable").lower()
    if quote_status not in QUOTE_STATUS:
        raise ValueError(f"{side_name}.quote.status is invalid")
    if quote_status == "available":
        net_credit = _required_number(quote.get("net_credit_btc"), f"{side_name}.quote.net_credit_btc")
        expected_net = _required_number(quote.get("expected_net_btc"), f"{side_name}.quote.expected_net_btc")
        if 'credit_eligible' in quote and quote['credit_eligible'] is not (net_credit > 0):
            raise ValueError(f'{side_name}.quote.credit_eligible conflicts with net_credit_btc')
        if net_credit <= 0 and quote.get('strict_net_result_ready'):
            raise ValueError(f'{side_name}.quote.strict_net_result_ready requires positive net credit')
        _validate_quote_timing(side_name, quote)
        if expected_payout is not None and not _close_enough(net_credit - expected_payout, expected_net, abs_tol=1e-10, rel_tol=1e-5):
            raise ValueError(f"{side_name}.quote.expected_net_btc must equal net_credit_btc minus expected_payout_btc")


def validate_assessment(assessment: dict[str, Any], card: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a validated deep clone or raise ``ValueError``.

    Unknown or insufficient assessments may omit prediction values. Available
    side predictions must be finite, time-safe, and geometrically coherent.
    """
    if not isinstance(assessment, dict):
        raise ValueError("assessment must be an object")
    clone = copy.deepcopy(assessment)
    schema = clone.get("schema") or clone.get("schema_version")
    if schema not in (ASSESSMENT_SCHEMA, "astra_statistical_assessment@1.1.0"):
        raise ValueError("assessment schema mismatch")
    status = str(clone.get("status") or "").lower()
    if status not in VALID_STATUS:
        raise ValueError("assessment status is invalid")
    provenance = _as_dict(clone.get("provenance"))
    as_of_ms = _epoch_ms(provenance.get("as_of_ms"), "provenance.as_of_ms")
    training_cutoff_ms = _epoch_ms(provenance.get("training_cutoff_ms"), "provenance.training_cutoff_ms")
    if training_cutoff_ms > as_of_ms:
        raise ValueError("training_cutoff_ms must not be later than as_of_ms")
    for field in ("source_record_hash", "input_hash", "model_id", "model_hash"):
        if not str(provenance.get(field) or "").strip():
            raise ValueError(f"provenance.{field} is required")
    card_time = _card_as_of_ms(card)
    if card_time is not None and as_of_ms > card_time:
        raise ValueError("assessment as_of_ms is later than card time")
    card_hash = _card_source_hash(card)
    if card is not None and not card_hash:
        raise ValueError("card source_record_hash is required for a bound statistical result")
    if card_hash and provenance.get("source_record_hash") != card_hash:
        raise ValueError("assessment source_record_hash does not match card")
    expected_card_id = _as_dict(card).get('card_id') or _as_dict(_as_dict(card).get('identity')).get('card_id')
    if expected_card_id and (clone.get('source_card_id') or clone.get('event_id')) != expected_card_id:
        raise ValueError("assessment event_id does not match card")

    sides = _as_dict(clone.get("sides"))
    expiries = []
    for side_name in SIDES:
        side = _as_dict(sides.get(side_name))
        if not side:
            if status == "available":
                raise ValueError(f"{side_name} side is required")
            continue
        _validate_side(side_name, side, as_of_ms, status)
        reference = _as_dict(side.get("reference"))
        if reference.get("expiry_ms"):
            expiries.append(_epoch_ms(reference.get("expiry_ms"), f"{side_name}.reference.expiry_ms"))
    if len(set(expiries)) > 1:
        raise ValueError("put and call references must share the same target expiry")

    claimed_hash = clone.get("assessment_hash") or _as_dict(clone.get("validation")).get("assessment_hash")
    if not claimed_hash:
        raise ValueError("assessment_hash is required")
    expected_hash = compute_assessment_hash(clone)
    if claimed_hash != expected_hash:
        raise ValueError("assessment_hash mismatch")
    return clone


def _pct(value: float | None) -> str:
    return "暂缺" if value is None else f"{value * 100:.1f}%"


def _btc(value: float | None) -> str:
    return "暂缺" if value is None else f"{value:.6f} BTC"


def _ratio_pct(value: float | None) -> str:
    return "暂缺" if value is None else f"{value * 100:.2f}%"


def _side_label(side_name: str) -> str:
    return "Put 信用价差" if side_name == "put" else "Call 信用价差"


def _side_projection(side_name: str, raw_side: dict[str, Any], assessment_status: str, as_of_ms: int) -> dict[str, Any]:
    side = _as_dict(raw_side)
    status = str(side.get("status") or assessment_status).lower()
    probability = _optional_number(side.get("probability_positive"))
    expected_loss = _optional_number(side.get("expected_loss_normalized"))
    expected_payout = _optional_number(side.get("expected_payout_btc"))
    conditional_loss = _optional_number(side.get("conditional_positive_loss"))
    reference = _as_dict(side.get("reference"))
    quote = copy.deepcopy(_as_dict(side.get("quote")))
    quote_status = str(quote.get("status") or "unavailable").lower()
    if quote_status == "available" and quote.get("checked_at_ms") is not None:
        checked_at_ms = _epoch_ms(quote["checked_at_ms"], "quote.checked_at_ms")
        age_ms = _optional_number(quote.get("max_age_ms", quote.get("age_ms")))
        skew_ms = _optional_number(quote.get("leg_timestamp_skew_ms", quote.get("skew_ms")))
        if checked_at_ms < as_of_ms or age_ms is None or skew_ms is None or age_ms > MAX_QUOTE_AGE_MS or skew_ms > MAX_LEG_SKEW_MS:
            quote_status = "unavailable"
            quote = {"status": "unavailable", "reason_cn": "同期补偿报价未通过严格时效检查，不能用于本卡净值判断。"}
    quote_net = _optional_number(quote.get("expected_net_btc")) if quote_status == "available" else None
    credit = _optional_number(quote.get("net_credit_btc")) if quote_status == "available" else None
    ref_cn = "参考两腿尚未确认"
    if reference:
        ref_cn = (
            f"研究参考：卖 {reference.get('short_strike'):,.0f} / 买 {reference.get('long_strike'):,.0f}，"
            f"宽度 {reference.get('width'):,.0f}，参考现货价 {reference.get('entry_price'):,.2f}（非成交）"
        )
    quote_cn = "没有同期补偿；只能比较赔付风险，不能说明该侧更值得卖。"
    if quote_status == "available":
        quote_cn = f"同期补偿 {_btc(credit)}；扣除期望赔付后的纸面净值 {_btc(quote_net)}。"
        if quote.get("delivery_fee_status", {}).get("status") == "unknown":
            quote_cn += "到期费用尚未确认，当前数值未完成全部费用核算。"
        if credit is not None and credit <= 0:
            quote_cn += "扣除入场费用后没有正净信用；保留盘口对照，不进入严格信用价差净表现组。"
    elif quote.get("reason_cn"):
        quote_cn = str(quote["reason_cn"])
    return {
        "status": status,
        "label_cn": _side_label(side_name),
        "probability_positive": probability,
        "conditional_positive_loss": conditional_loss,
        "expected_loss_normalized": expected_loss,
        "expected_payout_btc": expected_payout,
        "tail_probability": side.get("tail_probability") if side.get("tail_probability_status") == "available" else None,
        "tail_probability_status": side.get("tail_probability_status", "unavailable"),
        "tail_probability_note_cn": str(side.get("tail_probability_note_cn") or ""),
        "probability_cn": f"模型估计赔付发生概率 {_pct(probability)}",
        "expected_loss_cn": f"期望赔付 {_btc(expected_payout)}，约为宽度折算的 {_ratio_pct(expected_loss)}",
        "reference_cn": ref_cn,
        "reference": copy.deepcopy(reference),
        "quote": quote,
        "quote_status": quote_status,
        "quote_cn": quote_cn,
        "uncertainty_cn": str(side.get("uncertainty_cn") or _as_dict(side.get("uncertainty")).get("summary_cn") or "不确定性说明暂缺。"),
        "scope_cn": str(side.get("scope_cn") or "仅适用于本研究封存口径，不改变生产评级或执行权限。"),
    }


def _risk_comparison(put: dict[str, Any], call: dict[str, Any], normalized=False) -> dict[str, str]:
    field = "expected_loss_normalized" if normalized else "expected_payout_btc"
    put_loss = put.get(field)
    call_loss = call.get(field)
    if put.get("status") != "available" or call.get("status") != "available" or put_loss is None or call_loss is None:
        return {"relative_side": "not_comparable", "summary_cn": "两侧统计结果暂不可比。"}
    diff = abs(float(put_loss) - float(call_loss))
    if diff <= 1e-12:
        return {"relative_side": "tie", "summary_cn": "两侧模型赔付估计相同，没有单一低赔付侧。"}
    winner = "put" if put_loss < call_loss else "call"
    label = "Put" if winner == "put" else "Call"
    other = call_loss if winner == "put" else put_loss
    best = put_loss if winner == "put" else call_loss
    if normalized:
        return {"relative_side": winner,
            "summary_cn": f"按各自宽度折算，{label} 期望赔付较低（{_ratio_pct(best)} 对 {_ratio_pct(other)}）。参考腿或宽度不同的比较不纳入主要选侧成绩；尚未比较净补偿。"}
    return {
        "relative_side": winner,
        "summary_cn": f"模型估计 {label} 赔付较低：每1 BTC名义参考价差约 {_btc(best)}，另一侧约 {_btc(other)}；差异本身不代表统计显著。",
    }


def _quote_comparison(put: dict[str, Any], call: dict[str, Any]) -> str:
    quote_sides = [item for item in (put, call) if _as_dict(item.get("quote")).get("status") == "available"]
    if len(quote_sides) != 2:
        if len(quote_sides) == 1:
            return "只有一侧取得同期补偿，不能做完整净值对照。"
        return "没有同期补偿；本卡只显示赔付风险排序，不判断实际卖出更优。"
    put_net = _optional_number(_as_dict(put.get("quote")).get("expected_net_btc"))
    call_net = _optional_number(_as_dict(call.get("quote")).get("expected_net_btc"))
    if put_net is None or call_net is None:
        return "同期补偿资料不完整，不能比较净值。"
    if any((_optional_number(_as_dict(item.get('quote')).get('net_credit_btc')) or 0) <= 0 for item in (put, call)):
        return "至少一侧扣费后没有正净信用；两侧盘口保留对照，不据此给出信用价差择优结论。"
    if abs(put_net - call_net) <= 1e-8:
        return "两侧纸面净值接近，需回到候选报价和风控人工确认。"
    label = "Put" if put_net > call_net else "Call"
    return f"同期补偿口径下，{label} 纸面净值较高；这仍不是成交确认。"


def build_joint_projection(assessment: dict[str, Any], record: dict[str, Any] | None = None) -> dict[str, Any]:
    validated = validate_assessment(assessment, record)
    provenance = _as_dict(validated["provenance"])
    as_of_ms = _epoch_ms(provenance.get("as_of_ms"), "provenance.as_of_ms")
    status = str(validated.get("status")).lower()
    sides = _as_dict(validated.get("sides"))
    put = _side_projection("put", _as_dict(sides.get("put")), status, as_of_ms)
    call = _side_projection("call", _as_dict(sides.get("call")), status, as_of_ms)
    risk = _risk_comparison(put, call, validated.get("schema") == "astra_statistical_assessment@1.1.0")
    quote_summary = _quote_comparison(put, call)
    card_id = str(_as_dict(record or {}).get("identity", {}).get("card_id") or validated.get("event_id") or "joint-research")
    symbol = str(_as_dict(record or {}).get("identity", {}).get("symbol") or validated.get("symbol") or "BTC")
    source_hash = str(provenance["source_record_hash"])
    assessment_hash = compute_assessment_hash(validated)
    headline = risk["summary_cn"] if status == "available" else str(validated.get("reason_cn") or "统计研究资料暂不足。")
    detail = {
        "schema_version": DISPLAY_SCHEMA,
        "assessment_schema": validated.get("schema", ASSESSMENT_SCHEMA),
        "status": status,
        "identity": {
            "card_id": card_id,
            "symbol": symbol,
            "confirmed_at": _iso_from_ms(as_of_ms),
            "strategy_name": "Astra 联合研究影子结果",
        },
        "summary_cn": headline,
        "risk_comparison": risk,
        "quote_comparison_cn": quote_summary,
        "sides": {"put": put, "call": call},
        "scope_cn": str(validated.get("scope_cn") or "统计研究只用于影子验证；不改变 D-S 评级、原信号窗口或交易权限。"),
        "provenance": {
            "as_of_ms": as_of_ms,
            "source_record_hash": source_hash,
            "input_hash": provenance["input_hash"],
            "model_id": provenance["model_id"],
            "model_hash": provenance["model_hash"],
            "training_cutoff_ms": _epoch_ms(provenance["training_cutoff_ms"], "provenance.training_cutoff_ms"),
        },
        "assessment_hash": assessment_hash,
    }
    detail["display_projection_hash"] = projection_hash(detail)
    summary = {
        "schema_version": SUMMARY_SCHEMA,
        "display_schema_version": DISPLAY_SCHEMA,
        "status": status,
        "identity": copy.deepcopy(detail["identity"]),
        "as_of_ms": as_of_ms,
        "source_record_hash": source_hash,
        "assessment_hash": assessment_hash,
        "detail_projection_hash": detail["display_projection_hash"],
        "summary_cn": headline,
        "risk_comparison": risk,
        "quote_comparison_cn": quote_summary,
        "put": {
            "status": put["status"],
            "probability_positive": put["probability_positive"],
            "expected_loss_normalized": put["expected_loss_normalized"],
            "expected_payout_btc": put["expected_payout_btc"],
            "quote_status": put["quote_status"],
        },
        "call": {
            "status": call["status"],
            "probability_positive": call["probability_positive"],
            "expected_loss_normalized": call["expected_loss_normalized"],
            "expected_payout_btc": call["expected_payout_btc"],
            "quote_status": call["quote_status"],
        },
    }
    summary["display_projection_hash"] = projection_hash(summary)
    return {"summary": summary, "detail": detail}
