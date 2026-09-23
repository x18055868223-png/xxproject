from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import astra_joint_card_statistics as card_stats
import astra_joint_shadow as shadow
from astra_joint_bridge import context_for_card
from astra_joint_dataset import expiry_ms


MINUTE = 60_000
HOUR_MS = 3_600_000


def test_live_contract_snapshot_precedes_seed_file(tmp_path):
    (tmp_path/'raw'/'deribit').mkdir(parents=True)
    seed = tmp_path/'raw'/'deribit'/'instruments.json'
    seed.write_text('[{"instrument_name":"OLD"}]', encoding='utf-8')
    (tmp_path/'raw'/'deribit_instruments-999.json').write_text('{"result":[{"instrument_name":"NEW"}]}', encoding='utf-8')
    assert card_stats.load_contracts(tmp_path)[0]['instrument_name'] == 'NEW'
    assert card_stats.load_contracts(seed)[0]['instrument_name'] == 'OLD'


def ms(year: int, month: int, day: int, hour: int, minute: int = 0, second: int = 0) -> int:
    return int(dt.datetime(year, month, day, hour, minute, second, tzinfo=dt.UTC).timestamp() * 1000)


def bar(open_ms: int, price: float = 80_000.0) -> dict[str, object]:
    return {
        "open_time_ms": open_ms,
        "open": price,
        "high": price,
        "low": price,
        "close": price,
        "volume": 10.0,
        "quote_volume": price * 10.0,
        "trade_count": 10.0,
        "taker_buy_base_volume": 5.0,
        "taker_buy_quote_volume": price * 5.0,
        "close_time_ms": open_ms + MINUTE - 1,
    }


def write_bars(ledger: shadow.Ledger, card_ms: int, count: int = 40) -> None:
    start = (card_ms // MINUTE) * MINUTE - count * MINUTE
    with ledger.db:
        for index in range(count):
            ledger.insert_bar("um", bar(start + index * MINUTE, 80_000.0))


def artifact() -> dict[str, object]:
    return {
        "schema": "astra_two_part_payout_model@1.0.0",
        "status": "available",
        "model_kind": "two_part_gam",
        "model_version": "unit-natural-card-model",
        "feature_group": "joint",
        "training_cutoff": "2021-12-31",
        "scope": {"note": "unit"},
        "design_size": 4,
        "preprocess": {
            "version": "unit",
            "numeric_features": [
                {
                    "name": "ret_15",
                    "median": 0.0,
                    "mean": 0.0,
                    "scale": 1.0,
                    "spline": {"degree": 0, "knots": [-1.0, 1.0], "coefficient_matrix": [[1.0]], "basis_count": 1},
                },
                {
                    "name": "side_sign",
                    "median": -1.0,
                    "mean": 0.0,
                    "scale": 1.0,
                    "spline": {"degree": 0, "knots": [-2.0, 2.0], "coefficient_matrix": [[1.0]], "basis_count": 1},
                },
            ],
        },
        "logistic_model": {"link": "logit", "intercept": 0.0, "coef": [0.0, 0.0, 0.0, 0.0]},
        "gamma_model": {"link": "log", "intercept": math.log(0.25), "coef": [0.0, 0.0, 0.0, 0.0]},
    }


def contracts(entry_ms: int, price: float = 80_000.0) -> list[dict[str, object]]:
    expiry = expiry_ms(entry_ms)
    rows = []
    for option_type, strike in (
        ("put", price - 1000),
        ("put", price - 3000),
        ("call", price + 1000),
        ("call", price + 3000),
    ):
        rows.append(
            {
                "instrument_name": f"BTC-UNIT-{int(strike)}-{'P' if option_type == 'put' else 'C'}",
                "option_type": option_type,
                "strike": float(strike),
                "expiration_timestamp": expiry,
                "creation_timestamp": 0,
                "settlement_period": "day",
                "taker_commission": 0.0003,
            }
        )
    return rows


def card(card_ms: int, card_id: str = "CARD-NATURAL-1", price: float = 80_000.0) -> dict[str, object]:
    source_hash = "sha256:" + card_id.lower().replace("-", "")[:64].ljust(64, "0")
    return {
        "identity": {
            "card_id": card_id,
            "symbol": "BTC",
            "episode_id": "nr-episode",
            "event_type": "NR_REPAIR_CONFIRMED",
            "confirmed_time_ms": card_ms,
            "source_record_hash": source_hash,
            "strategy_version": "1.6.2",
        },
        "market_context": {"price": price, "quote_currency": "USDT"},
    }


def write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_build_card_observation_uses_only_pre_card_closed_um_and_keeps_natural_scope(tmp_path):
    card_ms = ms(2026, 9, 14, 23, 0, 2)
    ledger = shadow.Ledger(tmp_path)
    write_bars(ledger, card_ms, 20)
    current_open = (card_ms // MINUTE) * MINUTE
    with ledger.db:
        ledger.insert_bar(
            "um",
            {
                **bar(current_open, 120_000.0),
                "close_time_ms": current_open + MINUTE - 1,
            },
        )
        ledger.insert_bar("spot", {"open_time_ms": card_ms + MINUTE, "open": 99_999.0})

    observation, price_ref = card_stats.build_card_observation(card(card_ms), ledger)

    assert observation["event_family"] == "natural_signal_card"
    assert "price_rebalance" not in observation["event_family"]
    assert observation["ret_15"] == pytest.approx(0.0)
    assert price_ref["price"] == pytest.approx(80_000.0)
    assert "不代表已成交" in price_ref["basis_cn"]


def test_generate_registry_writes_only_forward_recent_cards_and_is_append_only(tmp_path):
    card_ms = ms(2026, 9, 14, 23)
    old_ms = card_ms - 3 * HOUR_MS
    now = card_ms + 10_000
    ledger = shadow.Ledger(tmp_path / "ledger")
    ledger.window(card_ms - HOUR_MS)
    write_bars(ledger, card_ms, 40)
    source = tmp_path / "cards.jsonl"
    source.write_text(
        json.dumps(card(old_ms, "OLD-CARD"), ensure_ascii=False)
        + "\n"
        + json.dumps(card(card_ms, "NEW-CARD"), ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    artifact_path = write_json(tmp_path / "model.json", artifact())
    contracts_path = write_json(tmp_path / "contracts.json", contracts(card_ms))
    registry = tmp_path / "registry.jsonl"

    first = card_stats.generate_registry(
        ledger_folder=tmp_path / "ledger",
        source=source,
        artifact_path=artifact_path,
        contracts_path=contracts_path,
        registry_path=registry,
        after_ms=card_ms - HOUR_MS,
        clock=lambda: now,
        lookback_hours=24,
    )
    second = card_stats.generate_registry(
        ledger_folder=tmp_path / "ledger",
        source=source,
        artifact_path=artifact_path,
        contracts_path=contracts_path,
        registry_path=registry,
        after_ms=card_ms - HOUR_MS,
        clock=lambda: now + 1,
        lookback_hours=24,
    )

    rows = read_jsonl(registry)
    assert first["written"] == 1
    assert first["skipped_old"] == 1
    assert second["written"] == 0
    assert second["skipped_existing"] == 1
    assert [row["card_id"] for row in rows] == ["NEW-CARD"]
    assessment = rows[0]["assessment"]
    assert assessment["status"] == "available"
    assert assessment["source_domain"]["kind"] == "natural_signal_card"
    assert assessment["sides"]["put"]["reference"]["entry_price"] == pytest.approx(80_000.0)
    assert assessment["sides"]["put"]["quote"]["status"] == "not_collected"


def test_registry_item_is_late_for_llm_freeze_but_available_for_later_display(tmp_path):
    card_ms = ms(2026, 9, 14, 23)
    now = card_ms + 30_000
    ledger = shadow.Ledger(tmp_path / "ledger")
    write_bars(ledger, card_ms, 40)
    assessment = card_stats.build_card_assessment(card(card_ms), ledger, artifact(), contracts(card_ms))
    item = card_stats.registry_item(card(card_ms), assessment, now)

    assert context_for_card(card(card_ms), {"CARD-NATURAL-1": item}, available_before_ms=card_ms + 1) is None
    assert context_for_card(card(card_ms), {"CARD-NATURAL-1": item}, available_before_ms=now + 1) is not None


def test_generate_registry_waits_for_contract_cache_then_appends_same_card(tmp_path):
    card_ms = ms(2026, 9, 14, 23)
    now = card_ms + 10_000
    ledger = shadow.Ledger(tmp_path / "ledger")
    ledger.window(card_ms - HOUR_MS)
    write_bars(ledger, card_ms, 40)
    source = tmp_path / "cards.jsonl"
    source.write_text(json.dumps(card(card_ms), ensure_ascii=False) + "\n", encoding="utf-8")
    artifact_path = write_json(tmp_path / "model.json", artifact())
    empty_contracts = write_json(tmp_path / "empty-contracts.json", [])
    registry = tmp_path / "registry.jsonl"

    pending = card_stats.generate_registry(
        ledger_folder=tmp_path / "ledger",
        source=source,
        artifact_path=artifact_path,
        contracts_path=empty_contracts,
        registry_path=registry,
        after_ms=card_ms - HOUR_MS,
        clock=lambda: now,
        lookback_hours=24,
    )

    assert pending["written"] == 0
    assert pending["skipped_contract_cache_not_ready"] == 1
    assert not registry.exists()

    ready_contracts = write_json(tmp_path / "ready-contracts.json", contracts(card_ms))
    ready = card_stats.generate_registry(
        ledger_folder=tmp_path / "ledger",
        source=source,
        artifact_path=artifact_path,
        contracts_path=ready_contracts,
        registry_path=registry,
        after_ms=card_ms - HOUR_MS,
        clock=lambda: now + 1,
        lookback_hours=24,
    )

    rows = read_jsonl(registry)
    assert ready["written"] == 1
    assert ready["available_assessments"] == 1
    assert rows[0]["card_id"] == "CARD-NATURAL-1"
    assert rows[0]["assessment"]["status"] == "available"


def test_generate_registry_writes_insufficient_when_snapshot_has_no_target_expiry(tmp_path):
    card_ms = ms(2026, 9, 14, 23)
    now = card_ms + 10_000
    ledger = shadow.Ledger(tmp_path / "ledger")
    ledger.window(card_ms - HOUR_MS)
    write_bars(ledger, card_ms, 40)
    source = tmp_path / "cards.jsonl"
    source.write_text(json.dumps(card(card_ms), ensure_ascii=False) + "\n", encoding="utf-8")
    artifact_path = write_json(tmp_path / "model.json", artifact())
    wrong_expiry = expiry_ms(card_ms) + 24 * HOUR_MS
    stale_but_nonempty = write_json(
        tmp_path / "wrong-expiry-contracts.json",
        [
            {
                **row,
                "expiration_timestamp": wrong_expiry,
            }
            for row in contracts(card_ms)
        ],
    )
    registry = tmp_path / "registry.jsonl"

    result = card_stats.generate_registry(
        ledger_folder=tmp_path / "ledger",
        source=source,
        artifact_path=artifact_path,
        contracts_path=stale_but_nonempty,
        registry_path=registry,
        after_ms=card_ms - HOUR_MS,
        clock=lambda: now,
        lookback_hours=24,
    )

    rows = read_jsonl(registry)
    assert result["written"] == 1
    assert result["insufficient_assessments"] == 1
    assert result["skipped_contract_cache_not_ready"] == 0
    assert rows[0]["assessment"]["status"] == "insufficient"
    assert "缺少当时有效的Put/Call参考合约" in rows[0]["assessment"]["reason_cn"]


def test_missing_contract_cache_keeps_card_as_insufficient_without_fake_quote(tmp_path):
    card_ms = ms(2026, 9, 14, 23)
    ledger = shadow.Ledger(tmp_path)
    write_bars(ledger, card_ms, 40)

    assessment = card_stats.build_card_assessment(card(card_ms), ledger, artifact(), [])

    assert assessment["status"] == "insufficient"
    assert "合约缓存缺失" in assessment["reason_cn"]
    assert assessment["sides"]["put"]["quote"]["status"] == "not_collected"
    assert "自然信号卡统计侧车" in assessment["scope_cn"]


def test_after_ms_or_forward_window_is_required(tmp_path):
    source = tmp_path / "cards.jsonl"
    source.write_text("", encoding="utf-8")
    artifact_path = write_json(tmp_path / "model.json", artifact())

    with pytest.raises(ValueError, match="after-ms"):
        card_stats.generate_registry(
            ledger_folder=tmp_path / "ledger",
            source=source,
            artifact_path=artifact_path,
            contracts_path=None,
            registry_path=tmp_path / "registry.jsonl",
            clock=lambda: ms(2026, 9, 14, 23),
        )
