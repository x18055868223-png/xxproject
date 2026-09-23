"""Build frozen statistical registry rows for natural signal-audit cards.

This script is read-only with respect to production cards: it reads the shadow
ledger, a local model artifact, cached Deribit contracts, and a natural audit
JSONL file, then appends a separate registry item for cards that are genuinely
inside the forward window. It never sends HTTP requests, calls an LLM, starts a
timer, rewrites sidecars, or marks a quote as filled.

Example:
  python tools/astra_joint_card_statistics.py --ledger-folder .artifacts/astra-joint-shadow ^
    --source deploy/signal_audit/data/signal_cards.jsonl ^
    --artifact .artifacts/astra-joint/model.json ^
    --contracts .artifacts/astra-joint/raw/deribit/instruments.json ^
    --registry .artifacts/astra-joint-shadow/joint_card_registry.jsonl ^
    --after-ms 1789000000000
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Iterable

from astra_joint_contract import FEATURE_GROUPS
from astra_joint_data import HistoricalBars
from astra_joint_dataset import canonical_hash
from astra_joint_projection import compute_assessment_hash, validate_assessment
from astra_joint_shadow import Ledger, _insufficient_assessment, _ordinary_dte, make_assessment, now_ms


REGISTRY_ITEM_SCHEMA = "astra_joint_card_statistics_registry_item@1.0.0"
OBSERVATION_SCHEMA = "natural_signal_card_stat_observation@1.0.0"
DEFAULT_LOOKBACK_HOURS = 24.0
HOUR_MS = 3_600_000


def _finite(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _epoch_ms(value: Any) -> int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        numeric = float(value)
        return int(numeric * 1000) if 0 < numeric < 10_000_000_000 else int(numeric)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.replace(".", "", 1).isdigit():
            return _epoch_ms(float(text))
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1000)
    return None


def _iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()


def card_id(card: dict[str, Any]) -> str:
    identity = _as_dict(card.get("identity"))
    return str(identity.get("card_id") or card.get("card_id") or "").strip()


def card_time_ms(card: dict[str, Any]) -> int | None:
    identity = _as_dict(card.get("identity"))
    for value in (
        identity.get("confirmed_time_ms"),
        card.get("confirmed_time_ms"),
        identity.get("confirmed_at"),
        card.get("confirmed_at"),
        card.get("created_at"),
    ):
        parsed = _epoch_ms(value)
        if parsed:
            return parsed
    return None


def source_record_hash(card: dict[str, Any]) -> str:
    identity = _as_dict(card.get("identity"))
    summary = _as_dict(card.get("signal_evidence_summary"))
    producer_integrity = _as_dict(card.get("producer_integrity"))
    integrity = _as_dict(card.get("integrity"))
    value = (
        identity.get("source_record_hash")
        or card.get("source_record_hash")
        or summary.get("source_record_hash")
        or producer_integrity.get("record_hash")
        or integrity.get("record_hash")
    )
    if value:
        return str(value)
    return "sha256:" + canonical_hash(card)


def card_price_reference(card: dict[str, Any]) -> dict[str, Any] | None:
    market = _as_dict(card.get("market_context"))
    candidates = (
        ("market_context.price", market.get("price")),
        ("market_context.market_price", market.get("market_price")),
        ("market_context.current_price", market.get("current_price")),
        ("market_context.underlying_price", market.get("underlying_price")),
    )
    for path, value in candidates:
        price = _finite(value)
        if price is not None and price > 0:
            unit = market.get("quote_currency") or market.get("quote") or market.get("price_unit")
            if not unit:
                return None
            return {
                "price": price,
                "unit": str(unit),
                "source_path": path,
                "basis_cn": "卡片记录时点已有价格；仅作参考价差几何和赔付风险基准，不代表已成交。",
            }
    return None


def is_synthetic_card(card: dict[str, Any]) -> bool:
    identity = _as_dict(card.get("identity"))
    value = identity.get("is_synthetic", card.get("is_synthetic"))
    return str(value).strip().lower() in {"1", "true", "yes"}


def iter_cards(path: str | Path) -> Iterable[dict[str, Any]]:
    path = Path(path)
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("cards") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ValueError("JSON source must be a card list or object with cards")
        for row in rows:
            if isinstance(row, dict):
                yield row
        return
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            text = line.strip()
            if not text:
                continue
            row = json.loads(text)
            if isinstance(row, dict):
                yield row


def load_contracts(path: str | Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    source = Path(path)
    candidates: list[Path]
    if source.is_dir():
        fixed = [
            source / "contracts.json",
            source / "instruments.json",
            source / "raw" / "deribit" / "instruments.json",
        ]
        raw_dir = source / "raw"
        snapshots = [p for directory in (raw_dir, source) if directory.exists()
                     for p in directory.glob('deribit_instruments*.json') if not p.name.endswith('.receipt.json')]
        # Snapshot names contain the durable request minute. Explicit files
        # stay exact; directory mode prefers newer live snapshots over a seed.
        candidates = sorted(snapshots, key=lambda p: p.name, reverse=True)
        candidates.extend(sorted((p for p in fixed if p.exists()), key=lambda p: p.stat().st_mtime_ns, reverse=True))
    else:
        candidates = [source]
    for candidate in candidates:
        if not candidate.exists():
            continue
        payload = json.loads(candidate.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            result = payload.get("result")
            if isinstance(result, list):
                return [row for row in result if isinstance(row, dict)]
            data = _as_dict(result).get("data")
            if isinstance(data, list):
                return [row for row in data if isinstance(row, dict)]
            rows = payload.get("contracts") or payload.get("instruments") or payload.get("rows")
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def build_card_observation(card: dict[str, Any], ledger: Ledger, source_hash: str | None = None) -> tuple[dict[str, Any], dict[str, Any] | None]:
    cid = card_id(card)
    asof = card_time_ms(card)
    if not cid:
        raise ValueError("card_id is required")
    if asof is None:
        raise ValueError("card time is required")
    identity = _as_dict(card.get("identity"))
    price_ref = card_price_reference(card)
    features = HistoricalBars(ledger.bars("um")).feature_snapshot(asof)
    observation = {
        **features,
        "schema": OBSERVATION_SCHEMA,
        "event_family": "natural_signal_card",
        "episode_id": str(identity.get("episode_id") or cid),
        "observation_id": f"natural-card:{cid}",
        "observation_kind": str(identity.get("event_type") or card.get("event_type") or "signal_audit_card"),
        "as_of_ms": asof,
        "entry_ms": asof,
        "signal_card_id": cid,
        "source_record_hash": source_hash or source_record_hash(card),
        "source_strategy_version": identity.get("strategy_version"),
        "support_scope_cn": "自然中性回路信号卡作为独立统计域；不伪装为价格再平衡事件，历史支持不足时只给风险排序。",
    }
    return observation, price_ref


def _finalize_assessment(assessment: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
    assessment['source_card_id'] = card_id(card)
    assessment.pop("assessment_hash", None)
    assessment["assessment_hash"] = compute_assessment_hash(assessment)
    return validate_assessment(assessment, card)


def build_card_assessment(
    card: dict[str, Any],
    ledger: Ledger,
    artifact: dict[str, Any],
    contracts: list[dict[str, Any]],
) -> dict[str, Any]:
    if artifact.get("schema") == "astra_joint_v11_model_artifact@1.0.0":
        from astra_joint_v11_cards import build_assessment
        return build_assessment(card, ledger, artifact, contracts)
    src_hash = source_record_hash(card)
    observation, price_ref = build_card_observation(card, ledger, src_hash)
    if price_ref is None:
        assessment = _insufficient_assessment(observation, artifact, src_hash, "卡时已知价格缺失；不能建立参考价差几何。")
    elif not _ordinary_dte(int(observation["entry_ms"])):
        assessment = _insufficient_assessment(observation, artifact, src_hash, "自然卡普通轮只连接8至24小时期限；本卡统计保留为期限外。")
    elif not contracts:
        assessment = _insufficient_assessment(observation, artifact, src_hash, "本地合约缓存缺失；不能确认当时可用参考两腿。")
    else:
        assessment = make_assessment(observation, price_ref["price"], contracts, artifact, src_hash, books=None)
    assessment["scope_cn"] = "自然信号卡统计侧车；使用卡时已知价格和卡前闭合UM分钟事实估计参考价差赔付风险，不代表成交、报价、D-S评级或权限。"
    assessment["source_domain"] = {
        "kind": "natural_signal_card",
        "signal_card_id": observation["signal_card_id"],
        "event_type": observation["observation_kind"],
        "support_cn": "NR/原生中性回路信号作为独立统计域保留；未伪装成price-rebalance事件。",
    }
    assessment["market_reference"] = {
        "price": price_ref.get("price") if price_ref else None,
        "unit": price_ref.get("unit") if price_ref else None,
        "source_path": price_ref.get("source_path") if price_ref else None,
        "basis_cn": price_ref.get("basis_cn") if price_ref else "卡时价格缺失。",
    }
    for side in ("put", "call"):
        side_row = _as_dict(_as_dict(assessment.get("sides")).get(side))
        side_row["scope_cn"] = "自然卡影子统计，只用于赔付风险排序；缺报价时不能判断真实净优势。"
        reference = _as_dict(side_row.get("reference"))
        if reference:
            reference["price_basis_cn"] = "卡片时点已有参考价，不是下一分钟成交价。"
            side_row["reference"] = reference
        assessment.setdefault("sides", {})[side] = side_row
    return _finalize_assessment(assessment, card)


def registry_item(card: dict[str, Any], assessment: dict[str, Any], available_at_ms: int) -> dict[str, Any]:
    cid = card_id(card)
    source_hash = source_record_hash(card)
    item = {
        "schema": REGISTRY_ITEM_SCHEMA,
        "card_id": cid,
        "source_record_hash": source_hash,
        "card_as_of_ms": card_time_ms(card),
        "card_as_of_utc": _iso(card_time_ms(card)),
        "available_at_ms": int(available_at_ms),
        "available_at_utc": _iso(int(available_at_ms)),
        "assessment": assessment,
        "assessment_hash": assessment["assessment_hash"],
        "source_card_kind": str(_as_dict(card.get("identity")).get("event_type") or card.get("event_type") or ""),
        "registry_scope_cn": "独立append-only统计registry；迟到统计可供展示，不会触发已完成LLM重跑。",
    }
    payload = dict(item)
    item["registry_item_hash"] = "sha256:" + canonical_hash(payload)
    return item


def load_registry_index(path: str | Path) -> dict[str, dict[str, Any]]:
    registry = Path(path)
    if not registry.exists():
        return {}
    result: dict[str, dict[str, Any]] = {}
    with registry.open(encoding="utf-8") as stream:
        for line in stream:
            text = line.strip()
            if not text:
                continue
            item = json.loads(text)
            cid = str(item.get("card_id") or "")
            if not cid:
                raise ValueError("registry item missing card_id")
            prior = result.get(cid)
            if prior is not None and prior != item:
                raise ValueError(f"conflicting registry item for {cid}")
            result[cid] = item
    return result


def append_registry_item(path: str | Path, item: dict[str, Any]) -> bool:
    registry = Path(path)
    existing = load_registry_index(registry)
    cid = str(item.get("card_id") or "")
    if cid in existing:
        if existing[cid] != item:
            raise ValueError(f"registry already contains a different item for {cid}")
        return False
    registry.parent.mkdir(parents=True, exist_ok=True)
    with registry.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))
        stream.write("\n")
    return True


def effective_after_ms(ledger: Ledger, after_ms: int | None) -> tuple[int, dict[str, Any] | None]:
    window = ledger.window()
    if after_ms is not None:
        return int(after_ms), window
    if not window:
        raise ValueError("--after-ms is required when the shadow forward window has not been started")
    return int(window["start_ms"]), window


def generate_registry(
    *,
    ledger_folder: str | Path,
    source: str | Path,
    artifact_path: str | Path,
    contracts_path: str | Path | None,
    registry_path: str | Path,
    after_ms: int | None = None,
    clock: Any = now_ms,
    lookback_hours: float = DEFAULT_LOOKBACK_HOURS,
) -> dict[str, Any]:
    ledger = Ledger(ledger_folder)
    at = int(clock())
    after, window = effective_after_ms(ledger, after_ms)
    lower = max(after, at - int(float(lookback_hours) * HOUR_MS))
    artifact = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
    contracts = load_contracts(contracts_path)
    existing = load_registry_index(registry_path)
    stats = {
        "schema": "astra_joint_card_statistics_run@1.0.0",
        "status": "complete",
        "available_at_ms": at,
        "after_ms": after,
        "window_start_ms": window.get("start_ms") if window else None,
        "lookback_hours": float(lookback_hours),
        "lower_bound_ms": lower,
        "cards_seen": 0,
        "written": 0,
        "skipped_existing": 0,
        "skipped_old": 0,
        "skipped_future": 0,
        "skipped_synthetic": 0,
        "skipped_missing_identity": 0,
        "available_assessments": 0,
        "insufficient_assessments": 0,
        "invalid_card_assessments": 0,
        "skipped_cache_not_ready": 0,
        "skipped_contract_cache_not_ready": 0,
    }
    for card in iter_cards(source):
        stats["cards_seen"] += 1
        cid = card_id(card)
        ctime = card_time_ms(card)
        if not cid or ctime is None:
            stats["skipped_missing_identity"] += 1
            continue
        if cid in existing:
            stats["skipped_existing"] += 1
            continue
        if is_synthetic_card(card):
            stats["skipped_synthetic"] += 1
            continue
        if ctime < lower:
            stats["skipped_old"] += 1
            continue
        if ctime > at:
            stats["skipped_future"] += 1
            continue
        # A minute cache may arrive after the natural card. Leave this item
        # pending so a later tick can append one valid supplemental assessment;
        # it must not freeze all-missing imputation as a market prediction.
        native_v11 = artifact.get("schema") == "astra_joint_v11_model_artifact@1.0.0"
        if HistoricalBars(ledger.bars('um')).closed_window(ctime, 30 if native_v11 else 15) is None:
            stats['skipped_cache_not_ready'] += 1
            continue
        if native_v11 and HistoricalBars(ledger.bars('spot')).closed_window(ctime, 1) is None:
            stats['skipped_cache_not_ready'] += 1
            continue
        # A missing/empty instruments cache is a pending infrastructure state
        # for the registry writer. Do not freeze an immutable insufficient item;
        # once the cache arrives, the same card must remain appendable. A
        # non-empty, verified snapshot that simply lacks this expiry is handled
        # by make_assessment as terminal insufficient below.
        if not contracts:
            stats["skipped_contract_cache_not_ready"] += 1
            continue
        try:
            assessment = build_card_assessment(card, ledger, artifact, contracts)
        except (ValueError, KeyError, TypeError):
            stats['invalid_card_assessments'] += 1
            continue
        item = registry_item(card, assessment, int(clock()))
        if append_registry_item(registry_path, item):
            existing[cid] = item
            stats["written"] += 1
            if assessment["status"] == "available":
                stats["available_assessments"] += 1
            else:
                stats["insufficient_assessments"] += 1
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ledger-folder", "--folder", dest="ledger_folder", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True, help="Natural signal-audit JSONL or JSON card file")
    parser.add_argument("--artifact", dest="artifact_path", type=Path, required=True, help="Frozen stdlib model artifact")
    parser.add_argument("--contracts", dest="contracts_path", type=Path, help="Cached Deribit instruments JSON or folder")
    parser.add_argument("--registry", dest="registry_path", type=Path, required=True, help="Append-only joint registry JSONL")
    parser.add_argument("--after-ms", type=int, help="Forward lower bound; required unless ledger window exists")
    parser.add_argument("--now-ms", type=int, help="Testing override for available_at_ms")
    parser.add_argument("--lookback-hours", type=float, default=DEFAULT_LOOKBACK_HOURS)
    args = parser.parse_args()
    result = generate_registry(
        ledger_folder=args.ledger_folder,
        source=args.source,
        artifact_path=args.artifact_path,
        contracts_path=args.contracts_path,
        registry_path=args.registry_path,
        after_ms=args.after_ms,
        clock=(lambda: int(args.now_ms)) if args.now_ms is not None else now_ms,
        lookback_hours=args.lookback_hours,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
