#!/usr/bin/env python3
"""Materialize signal_review.jsonl into the finalized static frontend layout.

Output layout:
  <output>/signal_cards/index.json
  <output>/signal_cards/<card_id>.json
  <output>/signal_cards/fallback.js
"""

import argparse
from collections import deque
import datetime as _dt
import json
import os
from pathlib import Path
import re
import tempfile


DEFAULT_FMZ_JSONL = "/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl"
MANIFEST_SCHEMA = {
    "name": "signal_cards_manifest",
    "version": "1.0.0",
    "card_schema": "signal_review_card@1.0.0",
}
LEGACY_CONFIDENCE_REMINDER_RE = re.compile(r"(置信度?\s*[0-9]+)\s*未校准")


def materialize(source, output, max_cards=200, llm_reviews=None,
                include_synthetic=False):
    source = Path(source)
    output = Path(output)
    cards_dir = output / "signal_cards"
    cards_dir.mkdir(parents=True, exist_ok=True)
    _chmod_public_dir(output)
    _chmod_public_dir(cards_dir)

    tail_limit = _read_tail_limit(max_cards)
    records, skipped = _read_jsonl(source, max_records=tail_limit)
    records = _dedupe_by_card_id(records)
    review_map = _read_llm_reviews(llm_reviews, max_records=tail_limit)
    merged_review_count = 0
    if review_map:
        for record in records:
            card_id = _identity(record).get("card_id") or record.get("card_id")
            review = review_map.get(card_id)
            if review:
                existing = record.get("llm_review")
                if (_review_status(existing) == "OK"
                        and _review_status(review) != "OK"):
                    continue
                record["llm_review"] = review
                merged_review_count += 1
    synthetic_count = sum(1 for record in records if _is_synthetic(record))
    if not include_synthetic:
        records = [record for record in records if not _is_synthetic(record)]
    records = sorted(records, key=_sort_key, reverse=True)
    if max_cards and max_cards > 0:
        records = records[:max_cards]

    manifest_cards = []
    expected_card_files = set()
    for record in records:
        _enrich_auxiliary_evidence(record)
        _sanitize_legacy_display_text(record)
        identity = _identity(record)
        card_id = identity.get("card_id") or record.get("card_id")
        filename = _filename_for_card(card_id)
        expected_card_files.add(filename)
        rel_path = "signal_cards/" + filename
        _write_json(cards_dir / filename, record)
        manifest_cards.append({
            "card_id": card_id,
            "confirmed_at": identity.get("confirmed_at") or record.get("created_at"),
            "symbol": identity.get("symbol") or record.get("symbol"),
            "quality": _quality(record),
            "path": rel_path,
        })

    manifest = {
        "schema": dict(MANIFEST_SCHEMA),
        "generated_at": _now_iso(),
        "cards": manifest_cards,
    }
    _prune_stale_card_json(cards_dir, expected_card_files)
    _write_json(cards_dir / "index.json", manifest)
    _write_fallback(cards_dir / "fallback.js", records)
    return {
        "source": str(source),
        "output": str(output),
        "written_cards": len(records),
        "skipped_lines": skipped,
        "manifest": str(cards_dir / "index.json"),
        "fallback": str(cards_dir / "fallback.js"),
        "llm_reviews": str(llm_reviews) if llm_reviews else "",
        "merged_review_count": merged_review_count,
        "filtered_synthetic_count": 0 if include_synthetic else synthetic_count,
        "include_synthetic": bool(include_synthetic),
    }


def _read_tail_limit(max_cards):
    if not max_cards or max_cards <= 0:
        return None
    return max(500, max_cards * 5)


def _read_jsonl(source, max_records=None, require_identity=True):
    if max_records and max_records > 0:
        records = deque(maxlen=max_records)
    else:
        records = []
    skipped = 0
    if not source.exists():
        return [], skipped
    with source.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if (isinstance(value, dict)
                    and (not require_identity
                         or _identity(value).get("card_id"))):
                records.append(value)
            else:
                skipped += 1
    return list(records), skipped


def _dedupe_by_card_id(records):
    by_id = {}
    for record in records:
        by_id[_identity(record).get("card_id")] = record
    return list(by_id.values())


def _read_llm_reviews(path, max_records=None):
    if not path:
        return {}
    path = Path(path)
    if not path.exists():
        return {}
    reviews = {}
    records, _skipped = _read_jsonl(path, max_records=max_records,
                                    require_identity=False)
    for value in records:
        card_id = value.get("card_id") or _identity(value).get("card_id")
        review = value.get("llm_review")
        if card_id and isinstance(review, dict):
            reviews[card_id] = review
    return reviews


def _review_status(review):
    if not isinstance(review, dict):
        return ""
    return str(review.get("status") or "").upper()


def _identity(record):
    identity = record.get("identity")
    return identity if isinstance(identity, dict) else {}


def _quality(record):
    quality = record.get("quality")
    if isinstance(quality, dict):
        return quality.get("overall")
    return quality


def _is_synthetic(record):
    marker = _identity(record).get("is_synthetic")
    if isinstance(marker, str):
        return marker.strip().lower() in {"1", "true", "yes", "synthetic"}
    return bool(marker)


def _sanitize_legacy_display_text(value):
    if isinstance(value, dict):
        for key, item in list(value.items()):
            value[key] = _sanitize_legacy_display_text(item)
        return value
    if isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = _sanitize_legacy_display_text(item)
        return value
    if isinstance(value, str):
        return LEGACY_CONFIDENCE_REMINDER_RE.sub(r"\1", value)
    return value


def _enrich_auxiliary_evidence(record):
    if not isinstance(record, dict):
        return record
    reasoning = record.get("reasoning")
    if not isinstance(reasoning, dict):
        return record
    rows = reasoning.get("evidence")
    if not isinstance(rows, list):
        return record
    cross = record.get("factor_cross_section")
    if not isinstance(cross, dict):
        cross = {}
    for row in rows:
        if isinstance(row, dict):
            _enrich_auxiliary_evidence_row(row, cross)
    return record


def _enrich_auxiliary_evidence_row(row, cross):
    key = str(row.get("key") or "").upper()
    detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
    factor = _factor_for_evidence(key, cross)
    raw_values = _auxiliary_raw_values(key, detail, factor)
    if raw_values:
        existing = row.get("raw_values")
        if isinstance(existing, dict):
            for name, value in raw_values.items():
                if existing.get(name) in (None, ""):
                    existing[name] = value
        else:
            row["raw_values"] = raw_values
    role = _auxiliary_role(key)
    if role and not row.get("auxiliary_role"):
        row["auxiliary_role"] = role
    lean = _auxiliary_lean(key, row, detail, factor)
    if lean and not row.get("auxiliary_lean"):
        row["auxiliary_lean"] = lean
    return row


def _factor_for_evidence(key, cross):
    if key == "FUNDING":
        factor = dict(_dict(cross.get("funding")))
        tmvf = _dict(cross.get("tmvf"))
        tmvf_48h = _dict(tmvf.get("tmvf_48h"))
        funding_48h = _dict(tmvf_48h.get("funding"))
        for name, value in funding_48h.items():
            if factor.get(name) in (None, ""):
                factor[name] = value
        if factor.get("funding_state") in (None, ""):
            factor["funding_state"] = tmvf_48h.get("funding_state")
        if factor.get("last_rate") in (None, ""):
            factor["last_rate"] = factor.get("last_funding_rate")
        if factor.get("effect") in (None, ""):
            factor["effect"] = factor.get("tmvf_funding_effect")
        return factor
    if key == "SRD":
        return _dict(cross.get("skew"))
    if key == "GGR_SPATIAL":
        return _dict(cross.get("gamma_regime"))
    if key == "TMV":
        return _dict(cross.get("tmvf"))
    if key == "FLOW_CONFIRM":
        return _dict(cross.get("micro_flow"))
    if key == "MACRO":
        return _dict(cross.get("macro_pressure"))
    if key in ("CVD_4H", "CVD_12H"):
        micro = _dict(cross.get("micro_flow"))
        return _dict(micro.get("fast_4h" if key == "CVD_4H" else "slow_12h"))
    return {}


def _dict(value):
    return value if isinstance(value, dict) else {}


def _auxiliary_role(key):
    return {
        "FUNDING": "FUTURES_FUNDING_CROWDING",
        "SRD": "OPTION_SKEW_DIRECTION",
        "GGR_SPATIAL": "OPTION_GAMMA_STRUCTURE",
        "TMV": "DIRECTION_OWNER",
        "FLOW_CONFIRM": "FLOW_CONFIRMATION",
        "MACRO": "MACRO_CONTEXT",
        "CVD_4H": "FLOW_CONFIRM_COMPONENT",
        "CVD_12H": "FLOW_CONFIRM_COMPONENT",
    }.get(key)


def _auxiliary_raw_values(key, detail, factor):
    fields = {
        "FUNDING": (
            "last_rate", "last_funding_rate", "funding_norm", "funding_cum",
            "funding_count", "funding_state", "effect", "tmvf_funding_effect",
            "verdict", "hard_warning", "history_points", "rate_unit",
            "observed_at", "age_ms", "source_ref"),
        "SRD": (
            "vote", "rr_blend", "rr_25d", "delta_rr", "rr_z",
            "skew_norm_blend", "skew_slope", "term_slope", "vote_confidence",
            "target_expiry_hours", "expiry_count", "data_status",
            "data_state", "observed_at", "age_ms", "source_ref"),
        "GGR_SPATIAL": (
            "regime", "regime_strength", "confidence_multiplier", "veto",
            "veto_reason", "net_gamma_notional_usd", "net_gamma_notional",
            "flip_point", "distance_to_flip_pct", "pin_strike",
            "distance_to_pin_pct", "pin_pull_direction", "max_gamma_strike",
            "call_wall", "put_wall", "market_state", "observed_at",
            "age_ms", "source_ref"),
        "FLOW_CONFIRM": (
            "agreement", "absorption_state", "combined_vote",
            "combined_weight", "data_quality"),
        "CVD_4H": (
            "verdict", "cvd_norm", "cvd_sum", "price_return_pct",
            "strength", "strength_pctl", "data_ready", "vote", "weight"),
        "CVD_12H": (
            "verdict", "cvd_norm", "cvd_sum", "price_return_pct",
            "strength", "strength_pctl", "data_ready", "vote", "weight"),
        "MACRO": (
            "macro_score", "score", "macro_regime", "regime", "verdict",
            "data_status", "data_confidence"),
    }.get(key, tuple(detail.keys()))
    raw = {}
    for field in fields:
        value = detail.get(field)
        if (value is None or value == "") and field in factor:
            value = factor.get(field)
        if value is not None and value != "":
            raw[field] = value
    if key == "GGR_SPATIAL":
        pin = _dict(factor.get("pin"))
        for source, target in (
                ("pin_strike", "pin_strike"),
                ("distance_to_pin_pct", "distance_to_pin_pct"),
                ("pin_pull_direction", "pin_pull_direction")):
            if target not in raw and pin.get(source) is not None:
                raw[target] = pin.get(source)
    if key == "FUNDING":
        if "last_rate" not in raw and raw.get("last_funding_rate") is not None:
            raw["last_rate"] = raw.get("last_funding_rate")
        if "effect" not in raw and raw.get("tmvf_funding_effect") is not None:
            raw["effect"] = raw.get("tmvf_funding_effect")
    return raw


def _auxiliary_lean(key, row, detail, factor):
    if key == "FUNDING":
        rate = _first_number(detail, factor,
                             fields=("funding_norm", "last_rate",
                                     "last_funding_rate"))
        return _signed_lean(-rate if rate is not None else None)
    if key == "SRD":
        return _signed_lean(_first_number(row, detail, factor, fields=("vote",)))
    if key == "GGR_SPATIAL":
        if factor.get("veto"):
            return "ADVERSE"
        regime = str(factor.get("regime") or detail.get("regime") or "").upper()
        if "NEGATIVE" in regime or "AMPLIFY" in regime:
            return "ADVERSE"
        if "POSITIVE" in regime or "PINNING" in regime:
            return "SUPPORTIVE"
        multiplier = _first_number(factor, detail,
                                   fields=("confidence_multiplier",))
        if multiplier is not None:
            if multiplier > 1.0:
                return "SUPPORTIVE"
            if multiplier < 1.0:
                return "ADVERSE"
        return "NEUTRAL"
    return _signed_lean(_first_number(row, detail, factor, fields=("vote",)))


def _first_number(*objects, fields):
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        for field in fields:
            value = _number(obj.get(field))
            if value is not None:
                return value
    return None


def _number(value):
    if isinstance(value, bool) or value in ("", None):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _signed_lean(value):
    value = _number(value)
    if value is None:
        return None
    if value > 0:
        return "BULLISH"
    if value < 0:
        return "BEARISH"
    return "NEUTRAL"


def _sort_key(record):
    identity = _identity(record)
    ms = identity.get("confirmed_time_ms") or record.get("confirmed_time_ms")
    timestamp = _timestamp_sort_value(ms)
    if timestamp == 0.0:
        timestamp = _timestamp_sort_value(
            identity.get("confirmed_at") or record.get("created_at"))
    return (timestamp, identity.get("card_id") or "")


def _timestamp_sort_value(value):
    if isinstance(value, bool) or value in ("", None):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0.0
        try:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            parsed = _dt.datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=_dt.timezone.utc)
            return parsed.timestamp() * 1000.0
        except ValueError:
            return 0.0
    return 0.0


def _filename_for_card(card_id):
    safe = re.sub(r"[^A-Za-z0-9_.+@=-]+", "_", str(card_id or "").strip())
    safe = safe.strip("._")
    if not safe:
        safe = "card"
    return safe + ".json"


def _prune_stale_card_json(cards_dir, expected_card_files):
    expected = set(expected_card_files)
    for path in cards_dir.glob("*.json"):
        if path.name == "index.json" or path.name in expected:
            continue
        path.unlink()


def _write_json(path, payload):
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    _atomic_write_text(path, text + "\n")


def _write_fallback(path, records):
    text = ("window.SIGNAL_CARD_FIXTURES = "
            + json.dumps(records, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
            + ";\n")
    _atomic_write_text(path, text)


def _atomic_write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _chmod_public_dir(path.parent)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                                    dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, path)
        os.chmod(path, 0o644)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _chmod_public_dir(path):
    try:
        os.chmod(path, 0o755)
    except OSError:
        pass


def _now_iso():
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build signal_cards/ data for the finalized signal audit page.")
    parser.add_argument("--source", default=DEFAULT_FMZ_JSONL,
                        help="Path to FMZ signal_review.jsonl.")
    parser.add_argument("--output", required=True,
                        help="Static frontend root containing index.html/app.js.")
    parser.add_argument("--max-cards", type=int, default=200,
                        help="Maximum newest cards to publish; <=0 publishes all.")
    parser.add_argument("--llm-reviews", default="",
                        help="Optional sidecar JSONL generated by gemini_signal_llm_review.py.")
    parser.add_argument("--include-synthetic", action="store_true",
                        help="Include synthetic/local preview cards in the published manifest.")
    args = parser.parse_args(argv)
    result = materialize(args.source, args.output, args.max_cards,
                         llm_reviews=args.llm_reviews,
                         include_synthetic=args.include_synthetic)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
