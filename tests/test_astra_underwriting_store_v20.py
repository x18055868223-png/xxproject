import json
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import astra_underwriting_cli_v20 as cli
import astra_underwriting_store_v20 as store
import astra_underwriting_v20 as core


VALUATION_MS = 1_779_696_000_000
EXPIRY_MS = VALUATION_MS + 7 * 24 * 60 * 60 * 1000


def _instrument(name: str, strike: float) -> dict:
    return {
        "instrument_name": name,
        "kind": "option",
        "base_currency": "BTC",
        "settlement_currency": "BTC",
        "quote_currency": "BTC",
        "counter_currency": "USD",
        "price_index": "btc_usd",
        "option_type": "put",
        "state": "open",
        "expiration_timestamp": EXPIRY_MS,
        "contract_size": 1.0,
        "strike": strike,
        "min_trade_amount": 0.01,
    }


def payload(origin: str = "manual") -> dict:
    short_name = "BTC-1JUN26-100000-P"
    long_name = "BTC-1JUN26-90000-P"
    horizon = (EXPIRY_MS - VALUATION_MS) / 3_600_000
    return {
        "origins": [origin],
        "candidate": {
            "short_instrument": short_name,
            "long_instrument": long_name,
            "quantity_btc": 0.1,
            "exit_policy": "hold_to_expiry",
        },
        "metadata": {
            "short": _instrument(short_name, 100_000.0),
            "long": _instrument(long_name, 90_000.0),
        },
        "market": {
            "valuation_ts_ms": VALUATION_MS,
            "reference_price_usd": 110_000.0,
            "reference_source": "synthetic_fixture",
            "received_ts_ms": VALUATION_MS,
            "short_book": {
                "instrument_name": short_name,
                "state": "open",
                "timestamp": VALUATION_MS - 100,
                "bids": [[0.040, 0.5]],
                "asks": [[0.041, 0.5]],
            },
            "long_book": {
                "instrument_name": long_name,
                "state": "open",
                "timestamp": VALUATION_MS - 100,
                "bids": [[0.009, 0.5]],
                "asks": [[0.010, 0.5]],
            },
        },
        "fee": {
            "basis": "configured_assumption",
            "amount_btc": 0.0001,
            "covers": ["entry", "delivery"],
        },
        "risk": {
            "status": "supported",
            "support_verified": True,
            "candidate_id": core.candidate_identity(
                {
                    "short_instrument": short_name,
                    "long_instrument": long_name,
                    "quantity_btc": 0.1,
                    "exit_policy": "hold_to_expiry",
                },
                {
                    "short": _instrument(short_name, 100_000.0),
                    "long": _instrument(long_name, 90_000.0),
                },
                VALUATION_MS,
            )["candidate_id"],
            "unit": "BTC",
            "model_id": "local-risk-reference",
            "model_hash": "a" * 64,
            "input_ts_ms": VALUATION_MS,
            "feature_end_ts_ms": VALUATION_MS,
            "reference_price_usd": 110_000.0,
            "quantity_btc": 0.1,
            "horizon_hours": horizon,
            "mu_btc": 0.0005,
            "calibration_id": "unit-calibration",
            "support_scope": "synthetic_unit",
            "source_kind": "synthetic_fixture",
            "input_row_hash": "b" * 64,
            "normalization_basis": "same_snapshot_inverse_btc_fixture",
            "training_cutoff": "synthetic-not-trained",
            "label_known_cutoff": "synthetic-not-trained",
        },
    }


def test_snapshot_id_excludes_origin_and_ledger_merges_same_snapshot_origins(tmp_path: Path) -> None:
    first_payload = payload("manual")
    second_payload = payload("fixed_round")
    assert core.evaluate(first_payload)["snapshot_id"] == core.evaluate(second_payload)["snapshot_id"]

    ledger = store.UnderwritingLedger(tmp_path / "underwriting.jsonl")
    first = ledger.record_payload(first_payload, now_ms=1)
    second = ledger.record_payload(second_payload, now_ms=2)
    state = ledger.state()

    assert first["snapshot"]["risk"]["status"] == "supported"
    assert first["snapshot"]["economics"]["reference_margin_btc"] is not None
    assert first["snapshot"]["snapshot_id"] == second["snapshot"]["snapshot_id"]
    assert len(state["snapshots"]) == 1
    assert second["store"]["origins"] == ["fixed_round", "manual"]
    assert second["store"]["origin_count_collapsed"] == 2
    assert second["store"]["sample_unit"] == "candidate_snapshot"
    assert second["store"]["is_active_version"] is True


def test_restart_dedupes_existing_snapshot_and_adds_origin(tmp_path: Path) -> None:
    path = tmp_path / "underwriting.jsonl"
    store.UnderwritingLedger(path).record_payload(payload("manual"), now_ms=1)

    restarted = store.UnderwritingLedger(path)
    restarted.record_payload(payload("state_refresh"), now_ms=2)
    restarted.record_payload(payload("state_refresh"), now_ms=3)
    state = restarted.state()
    snapshot_id = next(iter(state["snapshots"]))

    assert len(state["snapshots"]) == 1
    assert sorted(state["snapshots"][snapshot_id]["origins"]) == ["manual", "state_refresh"]
    assert len([row for row in store.read_jsonl(path) if row["event_type"] == store.SNAPSHOT_EVENT]) == 1


def test_same_snapshot_threaded_origins_share_one_persistent_version(tmp_path: Path) -> None:
    ledger = store.UnderwritingLedger(tmp_path / "underwriting.jsonl")
    origins = ["manual", "fixed_round", "natural_nr", "state_refresh"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        cards = list(pool.map(lambda origin: ledger.record_payload(payload(origin)), origins))

    state = ledger.state()
    assert {card["snapshot"]["snapshot_id"] for card in cards} == {cards[0]["snapshot"]["snapshot_id"]}
    assert len(state["snapshots"]) == 1
    snapshot_id = cards[0]["snapshot"]["snapshot_id"]
    assert sorted(state["snapshots"][snapshot_id]["origins"]) == sorted(origins)
    assert len([row for row in store.read_jsonl(ledger.path) if row["event_type"] == store.SNAPSHOT_EVENT]) == 1


def test_quote_change_creates_new_version_and_invalidates_old_snapshot(tmp_path: Path) -> None:
    ledger = store.UnderwritingLedger(tmp_path / "underwriting.jsonl")
    first = ledger.record_payload(payload("manual"), now_ms=1)
    changed = payload("natural_nr")
    changed["market"]["short_book"]["bids"] = [[0.039, 0.5]]
    second = ledger.record_payload(changed, now_ms=2)
    state = ledger.state()

    assert first["snapshot"]["snapshot_id"] != second["snapshot"]["snapshot_id"]
    old_card = store.build_card_annex(state, first["snapshot"]["snapshot_id"])
    assert old_card["store"]["is_active_version"] is False
    assert old_card["store"]["invalidated_by_snapshot_id"] == second["snapshot"]["snapshot_id"]
    assert old_card["operator_attention"]["should_surface"] is True
    assert old_card["operator_attention"]["reminder_cooldown_can_hide"] is False
    assert second["store"]["version"] == 2


def test_stale_or_invalid_snapshot_surfaces_without_cooldown_suppression(tmp_path: Path) -> None:
    stale = payload("fixed_round")
    stale["market"]["received_ts_ms"] = VALUATION_MS - 6_000
    stale["market"]["short_book"]["timestamp"] = VALUATION_MS - 6_000
    stale["market"]["long_book"]["timestamp"] = VALUATION_MS - 6_000

    card = store.UnderwritingLedger(tmp_path / "underwriting.jsonl").record_payload(stale)

    assert card["snapshot"]["status"] == "expired_or_stale"
    assert "snapshot_status_expired_or_stale" in card["operator_attention"]["reasons"]
    assert card["operator_attention"]["should_surface"] is True
    assert card["operator_attention"]["suppression_allowed"] is False


def test_store_rejects_unvalidated_or_synthetic_review_without_changing_snapshot(tmp_path: Path) -> None:
    ledger = store.UnderwritingLedger(tmp_path / "underwriting.jsonl")
    card = ledger.record_payload(payload("manual"), now_ms=1)
    snapshot_id = card["snapshot"]["snapshot_id"]
    before_economics = deepcopy(card["snapshot"]["economics"])

    review = {
        "schema": "local_review@unit",
        "reviewed_snapshot_id": snapshot_id,
        "status": "read",
        "applicability": "no_numeric_change",
    }
    with pytest.raises(ValueError, match="complete v2 causal review"):
        ledger.attach_review(review, now_ms=2)
    assert ledger.state()["snapshots"][snapshot_id]["snapshot"]["economics"] == before_economics
    assert ledger.llm_packet(snapshot_id)["snapshot_id"] == snapshot_id

    with pytest.raises(ValueError, match="not in the ledger"):
        ledger.attach_review({"reviewed_snapshot_id": "missing", "status": "read"})
    with pytest.raises(ValueError, match="reviewed_snapshot_id"):
        ledger.attach_review({"status": "read"}, snapshot_id=snapshot_id)
    with pytest.raises(ValueError, match="cannot replace snapshot fields"):
        ledger.attach_review({"reviewed_snapshot_id": snapshot_id, "economics": {"mu_ref_btc": 0.0}})


def test_cli_writes_ledger_annex_and_packet_without_llm_usage(tmp_path: Path, capsys) -> None:
    input_path = tmp_path / "input.json"
    ledger_path = tmp_path / "ledger.jsonl"
    annex_path = tmp_path / "annex.json"
    packet_path = tmp_path / "packet.json"
    input_path.write_text(json.dumps(payload("manual"), ensure_ascii=False), encoding="utf-8")

    assert cli.main([
        "--input",
        str(input_path),
        "--ledger",
        str(ledger_path),
        "--origin",
        "natural_nr",
        "--annex-output",
        str(annex_path),
        "--packet-output",
        str(packet_path),
    ]) == 0
    capsys.readouterr()

    annex = json.loads(annex_path.read_text(encoding="utf-8"))
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    assert annex["schema"] == store.CARD_ANNEX_SCHEMA
    assert annex["store"]["origins"] == ["manual", "natural_nr"]
    assert packet["schema"] == "astra_underwriting_llm_packet@2.0.0"
    assert packet["snapshot_id"] == annex["snapshot"]["snapshot_id"]
    assert not list(tmp_path.glob("*usage*"))


def _plain(state):
    return json.loads(json.dumps(state, sort_keys=True, default=sorted))


def _versions():
    first = payload("manual")
    second = payload("fixed_round")
    changed = payload("natural_nr")
    changed["market"]["short_book"]["bids"] = [[0.039, 0.5]]
    again = payload("state_refresh")
    again["market"]["short_book"]["bids"] = [[0.038, 0.5]]
    return [first, second, changed, again]


def test_incremental_replay_equals_a_full_replay_after_every_append(tmp_path: Path) -> None:
    ledger = store.UnderwritingLedger(tmp_path / "underwriting.jsonl")
    held = []
    for step, item in enumerate(_versions(), start=1):
        card = ledger.record_payload(item, now_ms=step)
        state = ledger.state()
        held.append((_plain(state), state))
        assert _plain(state) == _plain(store.rebuild_state(ledger.path))
        assert card == store.build_card_annex(store.rebuild_state(ledger.path), card["snapshot"]["snapshot_id"])
    # copy-on-write: a state handed out earlier never changes under its reader
    for plain, state in held:
        assert _plain(state) == plain
    assert held[0][1]["snapshots"] is not held[-1][1]["snapshots"]


def test_record_snapshot_replays_only_the_new_rows(tmp_path: Path, monkeypatch) -> None:
    ledger = store.UnderwritingLedger(tmp_path / "underwriting.jsonl")
    items = _versions()
    for step, item in enumerate(items[:3], start=1):
        ledger.record_payload(item, now_ms=step)
    snapshot = core.evaluate(items[3])
    calls = []
    real = store._validate_snapshot
    monkeypatch.setattr(store, "_validate_snapshot", lambda value: calls.append(1) or real(value))
    ledger.state(), ledger.state()
    assert calls == []  # unchanged file: no replay at all
    ledger.record_snapshot(snapshot, now_ms=9)
    ledger.state()
    # the input itself once, the one new snapshot row once (was: a full replay before and after)
    assert len(calls) == 2
    # another ledger object on the same file replays it whole, once
    calls.clear()
    other = store.UnderwritingLedger(ledger.path)
    assert _plain(other.state()) == _plain(ledger.state())
    assert len(calls) == 3  # one per snapshot row (the first two items share a snapshot)


def test_a_rewritten_or_truncated_ledger_is_replayed_whole(tmp_path: Path) -> None:
    path = tmp_path / "underwriting.jsonl"
    ledger = store.UnderwritingLedger(path)
    for step, item in enumerate(_versions(), start=1):
        ledger.record_payload(item, now_ms=step)
    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(b"".join(lines[:2]))  # truncated in place
    assert _plain(ledger.state()) == _plain(store.rebuild_state(path))
    ledger.state()
    replaced = tmp_path / "other.jsonl"
    replaced.write_bytes(b"".join(lines[:1] + lines[3:4]))
    import os
    os.replace(replaced, path)  # same size class, different file
    assert _plain(ledger.state()) == _plain(store.rebuild_state(path))


def test_a_line_still_being_written_is_not_cached_and_a_bad_line_still_fails(tmp_path: Path) -> None:
    path = tmp_path / "underwriting.jsonl"
    ledger = store.UnderwritingLedger(path)
    items = _versions()
    ledger.record_payload(items[0], now_ms=1)
    before = _plain(ledger.state())
    other = store.UnderwritingLedger(tmp_path / "scratch.jsonl")
    other.record_payload(items[2], now_ms=2)
    row = other.path.read_bytes()
    with path.open("ab") as handle:
        handle.write(row[: len(row) // 2])  # half a row
    assert _plain(ledger.state()) == before
    with path.open("ab") as handle:
        handle.write(row[len(row) // 2:])
    assert len(ledger.state()["snapshots"]) == 2
    with path.open("ab") as handle:
        handle.write(b"not json\n")
    with pytest.raises(ValueError, match="line 3 is not valid JSON"):
        ledger.state()
