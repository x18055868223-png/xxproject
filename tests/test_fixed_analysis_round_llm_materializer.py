import importlib.util
import json
import pathlib
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
LLM_TOOL = ROOT / "tools" / "signal_llm_review.py"
MATERIALIZER_TOOL = ROOT / "tools" / "materialize_signal_cards.py"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def fixed_round_metadata(slot="20260724_2300_bjt"):
    return {
        "schema_name": "SignalFixedAnalysisRound",
        "schema_version": "1.0.0",
        "label_cn": "固定轮次分析",
        "trigger_clock": "Asia/Shanghai 23:00",
        "scheduled_time_utc8": "2026-07-24T23:00:00+08:00",
        "snapshot_collected_time_utc8": "2026-07-24T23:00:03+08:00",
        "ny_time": "2026-07-24T11:00:03-04:00",
        "dst_mode": "EDT",
        "slot_id": slot,
        "bypassed_gate": "DIE_ANCHOR_TRIGGER_ONLY",
        "does_not_override_producer_decision": True,
        "audit_only": True,
    }


def signal_card(card_id, ts_ms, *, fixed_round=False, direction="BEARISH_WEAK",
                support="TRADE_SUPPORT_WEAK", confidence=52,
                funding_rate=-0.000004, conflict_ratio=0.18):
    event_type = "FIXED_ANALYSIS_ROUND" if fixed_round else "NR_REPAIR_CONFIRMED"
    tags = ["FIXED_ROUND_ANALYSIS"] if fixed_round else ["NEUTRAL_REPAIR_CONFIRMED"]
    card = {
        "schema": {
            "name": "SIGNAL_REVIEW_CARD",
            "version": "nrd.schema.v1.0.0",
            "record_type": (
                "fixed_analysis_round_audit"
                if fixed_round else "signal_review_card"
            ),
        },
        "identity": {
            "card_id": card_id,
            "short_id": card_id[-6:],
            "symbol": "BTC",
            "confirmed_at": "2026-07-24T23:00:03+08:00",
            "confirmed_time_ms": ts_ms,
            "event_type": event_type,
            "tags": tags,
            "strategy_name": "中性回路信号层",
            "strategy_version": "1.5.7",
        },
        "market_context": {"market_price": 65609.02, "quote": "USDT"},
        "decision": {
            "direction": direction,
            "action": "WAIT_CONFIRMATION",
            "support_label": support,
            "confidence": confidence,
            "trade_allowed": False,
        },
        "decision_matrix": {
            "decision_state": "WAIT_CONFIRMATION",
            "execution_allowed": False,
            "support_label": support,
        },
        "signal_window": {
            "neutral_repair": {
                "is_active": False,
                "state": "WAITING_ANCHOR_DIE",
            }
        },
        "signal_durability": {"headline_state": "WATCH"},
        "comfort_window": {"state": "US_SESSION"},
        "price_anchor_durability": {"durability_state": "WATCH"},
        "reasoning": {"evidence": []},
        "conflict": {"ratio": conflict_ratio, "level": "MILD"},
        "blocking": {"blocking": False, "hard_blockers": []},
        "quality": {"overall": "OK", "required_ready": True},
        "factor_cross_section": {
            "tmvf": {
                "tmv_blend": -0.42,
                "direction": "BEARISH",
            },
            "funding": {
                "last_rate": funding_rate,
                "canonical_funding_semantics": {
                    "funding_state": "healthy_short_bias",
                    "crowding_state": "NOT_CROWDED",
                    "direction_bias": "TEMPERATE_SHORT",
                    "reflexivity_importance": "NOISE",
                    "edb_vote_role": "NON_VOTING",
                    "threshold_abs_rate": 0.0001,
                },
            },
            "gex_info": {
                "rank": 0.91,
                "rank_quality": "ok",
                "window_days": 29.9,
                "net_gamma_notional_usd": 264000000.0,
            },
        },
        "provenance": {
            "transition_audit_source": {
                "schema_name": "SignalTransitionProducerAnchor",
                "schema_version": "signal_transition_producer_anchor@1.0.0",
                "native": True,
                "transition_computation_owner": "PRODUCER_NATIVE",
                "event_time_ms": ts_ms,
            }
        },
        "producer_integrity": {
            "record_hash": "sha256:" + card_id.lower().replace("-", "")[:64].ljust(64, "0")
        },
    }
    if fixed_round:
        card["analysis_round"] = fixed_round_metadata()
    return card


def test_fixed_round_card_packet_reaches_regular_llm_packets():
    tool = load_module(LLM_TOOL, "signal_llm_review_fixed_round_packet")
    card = signal_card("FIXED-ROUND-CARD", 1784905203000, fixed_round=True)

    packet = tool.build_review_packet(card)
    identity = packet["identity"]
    assert_true(identity["event_type"] == "FIXED_ANALYSIS_ROUND",
                "card review packet should preserve fixed round event_type")
    assert_true("FIXED_ROUND_ANALYSIS" in identity["tags"],
                "card review packet should preserve fixed round tag")
    assert_true(packet["analysis_round"]["label_cn"] == "固定轮次分析",
                "card review packet should carry producer-native analysis_round")
    assert_true(packet["analysis_round"]["bypassed_gate"] == "DIE_ANCHOR_TRIGGER_ONLY",
                "analysis_round should disclose the only bypassed gate")
    evidence = {item["id"]: item for item in packet["evidence_catalog"]}
    assert_true(evidence["EV_ANALYSIS_ROUND"]["pointer"] == "analysis_round",
                "analysis_round should be an explicit evidence catalog row")

    blind = tool.build_blind_theoretical_packet(packet)
    assert_true(blind["identity"]["event_type"] == "FIXED_ANALYSIS_ROUND",
                "blind packet should preserve event_type without adding decisions")
    assert_true(blind["analysis_round"]["slot_id"] == "20260724_2300_bjt",
                "blind packet should know this is the fixed 23:00 snapshot")


def test_materializer_preserves_fixed_round_and_marks_transition_context():
    tool = load_module(MATERIALIZER_TOOL, "materialize_signal_cards_fixed_round")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        source = root / "signal_review.jsonl"
        output = root / "public"
        ledger = root / "signal_transition_ledger.jsonl"
        base = signal_card("REGULAR-A", 1784901600000, fixed_round=False,
                           direction="BEARISH_WEAK", confidence=51,
                           conflict_ratio=0.11)
        fixed = signal_card("FIXED-B", 1784905203000, fixed_round=True,
                            direction="BEARISH_WEAK", confidence=51,
                            conflict_ratio=0.12)
        source.write_text(
            json.dumps(base, ensure_ascii=False) + "\n"
            + json.dumps(fixed, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        result = tool.materialize(source, output, max_cards=20,
                                  transition_ledger=ledger)
        assert_true(result["transition_records"] == 1,
                    "fixed round should still participate in the normal transition chain")

        fixed_card = json.loads((output / "signal_cards" / "FIXED-B.json")
                                .read_text(encoding="utf-8"))
        assert_true(fixed_card["analysis_round"]["label_cn"] == "固定轮次分析",
                    "materialized card should preserve the whole analysis_round object")
        assert_true(fixed_card["identity"]["event_type"] == "FIXED_ANALYSIS_ROUND",
                    "materialized card should preserve fixed round event_type")
        assert_true("FIXED_ROUND_ANALYSIS" in fixed_card["identity"]["tags"],
                    "materialized card should preserve fixed round tag")

        transition = fixed_card["transition_context"]
        assert_true(transition["previous_event_type"] == "NR_REPAIR_CONFIRMED",
                    "transition should explicitly expose previous event_type")
        assert_true(transition["current_event_type"] == "FIXED_ANALYSIS_ROUND",
                    "transition should explicitly expose current event_type")
        assert_true(transition["previous_tags"] == ["NEUTRAL_REPAIR_CONFIRMED"],
                    "transition should explicitly expose previous tags")
        assert_true(transition["current_tags"] == ["FIXED_ROUND_ANALYSIS"],
                    "transition should explicitly expose current tags")
        event_context = transition["event_context"]
        assert_true(event_context["transition_nature"] == "FIXED_TIME_SNAPSHOT_DIFF",
                    "fixed round transition should be marked as a fixed time snapshot diff")
        assert_true(event_context["fixed_time_snapshot_diff"] is True,
                    "fixed round transition should not masquerade as natural signal migration")
        assert_true(event_context["current"]["analysis_round"]["slot_id"] == "20260724_2300_bjt",
                    "transition event context should retain current round metadata")

        ledger_record = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
        assert_true(ledger_record["event_context"] == event_context,
                    "private transition ledger should carry the same event context as card")


def test_transition_review_packet_carries_fixed_round_delta_context():
    materializer = load_module(MATERIALIZER_TOOL, "materialize_signal_cards_for_llm_packet")
    gemini = load_module(LLM_TOOL, "signal_llm_review_fixed_transition_packet")
    previous = signal_card("REGULAR-A", 1784901600000, fixed_round=False)
    current = signal_card("FIXED-B", 1784905203000, fixed_round=True)

    transition = materializer._transition_record(previous, current, [previous, current], None)
    packet = gemini.build_transition_review_packet(transition)

    assert_true(packet["identity"]["previous_event_type"] == "NR_REPAIR_CONFIRMED",
                "transition LLM packet should expose previous event_type")
    assert_true(packet["identity"]["current_event_type"] == "FIXED_ANALYSIS_ROUND",
                "transition LLM packet should expose current event_type")
    assert_true(packet["event_context"]["transition_nature"] == "FIXED_TIME_SNAPSHOT_DIFF",
                "transition LLM packet should preserve fixed snapshot nature")
    evidence = {item["id"]: item for item in packet["evidence_catalog"]}
    assert_true(evidence["EV_TRANSITION_EVENT_CONTEXT"]["pointer"] == "event_context",
                "transition event context should be referenceable evidence")
    prompt_packet = gemini._transition_prompt_packet(packet)
    assert_true(prompt_packet["EVIDENCE"]["event_context"]["fixed_time_snapshot_diff"] is True,
                "transition prompt should include fixed snapshot context as evidence")


def main():
    test_fixed_round_card_packet_reaches_regular_llm_packets()
    test_materializer_preserves_fixed_round_and_marks_transition_context()
    test_transition_review_packet_carries_fixed_round_delta_context()
    print("fixed_analysis_round_llm_materializer: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("fixed_analysis_round_llm_materializer: FAIL - " + str(exc))
        raise
