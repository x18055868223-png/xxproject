import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "signal_evidence_v2.py"
REVIEW_TOOL = ROOT / "tools" / "signal_llm_review.py"
AS_OF_MS = 1788377846281


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_tool():
    return load_module(TOOL, "signal_evidence_v2")


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def clone(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def schema():
    return {
        "name": "SIGNAL_REVIEW_CARD",
        "version": "nrd.schema.v1.0.0",
        "record_type": "signal_review_card",
    }


def record_hash(card_id):
    return "sha256:" + card_id.lower().replace("-", "").ljust(64, "0")[:64]


def base_card(card_id="EVT-1", ts_ms=AS_OF_MS, price=100.0):
    return {
        "schema": schema(),
        "identity": {
            "card_id": card_id,
            "symbol": "BTC",
            "confirmed_time_ms": ts_ms,
            "confirmed_at": "2026-09-02T15:37:26.281000-04:00",
            "event_type": "NR_REPAIR_CONFIRMED",
            "tags": ["NEUTRAL_REPAIR_CONFIRMED"],
            "strategy_version": "1.6.0",
        },
        "market_context": {"price": price, "quote_currency": "USDT"},
        "decision": {
            "lean": "BULLISH",
            "confidence": 77,
            "support_label": "MODEL_SUPPORT",
        },
        "decision_matrix": {
            "direction": "BULLISH",
            "execution_allowed": False,
        },
        "blocking": {"has_block": False, "hard_veto": None},
        "quality": {
            "overall": "OK",
            "sources": {
                "gex": "ok",
                "tmvf": "ok",
                "micro_flow": "ok",
                "funding": "ok",
            },
        },
        "factor_cross_section": {
            "anchor": {
                "score": 72,
                "effective_flip_point": 100.0,
                "band_half": 2.0,
                "normalized_deviation": 0.2,
                "gex_source_ts_ms": ts_ms - 60000,
                "freshness": "FRESH",
            },
            "gamma_regime": {
                "regime": "positive_gamma",
                "flip_point": 99.0,
                "call_wall": 106.0,
                "put_wall": 94.0,
                "pin": {"pin_strike": 101.0},
                "net_gamma_notional_usd": 0.39,
            },
            "gex_info": {
                "market_state": "positive_gamma",
                "net_gamma_notional_usd": 220000000.0,
                "rank": {
                    "window": {"window_days": 5.0, "sample_count": 14},
                    "quality": "warming_up",
                    "full_grid": [{"strike": 90000, "rank": 0.97}],
                },
                "source_ref": "GEX_MONITOR_API",
            },
            "tmvf": {
                "direction": "Bullish",
                "tmv_blend": 0.35,
                "data_ready": True,
            },
            "micro_flow": {
                "fast_4h": {
                    "horizon_hours": 4,
                    "data_ready": True,
                    "cvd_norm": 0.42,
                    "cvd_sum": 1200.0,
                    "cvd_unit": "BTC",
                    "price_return_pct": 0.18,
                },
                "slow_12h": {
                    "horizon_hours": 12,
                    "data_ready": False,
                    "cvd_norm": -0.12,
                    "price_return_pct": -0.04,
                },
                "combined": {"direction": "bullish", "data_ready": True},
            },
            "macro_pressure": {
                "macro_score": 0.12,
                "macro_shock": {"block": False, "state": "CLEAR"},
            },
            "funding": {
                "last_rate": 0.00004431,
                "canonical_funding_semantics": {
                    "raw_funding_rate": 0.00004431,
                    "edb_vote_allowed": False,
                    "edb_participation": "NON_VOTING",
                    "canonical_text_cn": "资金费率 +0.0044%：温和多头费率倾向；EDB 不计票。",
                },
            },
            "skew": {"vote": 0.12, "rr_blend": 0.08},
        },
        "signal_durability": {
            "headline_score": 99,
            "durability_score": 99,
            "price_points": [100.0, 100.3, 100.5],
            "sublayers": {"price_efficiency": {"score": 1.0}},
        },
        "integrated_trade_advisory": {
            "side_comfort_ratings": {
                "put_credit": {"final_grade": "S"},
                "call_credit": {"final_grade": "A"},
            },
            "future_24h_bayesian_report": {"base_case": "UP"},
        },
        "producer_integrity": {"record_hash": record_hash(card_id)},
    }


def facts_by_id(packet):
    return {item["id"]: item for item in packet["facts"]}


def assert_chinese(text, message):
    assert_true(any("\u4e00" <= char <= "\u9fff" for char in text), message)


def test_packet_whitelist_and_legacy_exclusion(tool):
    packet = tool.build_evidence_packet(base_card())
    assert_true(packet["schema"] == "signal_evidence_packet@2.0.0",
                "schema")
    assert_true(set(packet["identity"]) == {
        "card_id", "symbol", "strategy_version", "as_of_ms",
        "source_record_hash",
    }, "identity whitelist")
    assert_true(packet["identity"]["source_record_hash"] == record_hash("EVT-1"),
                "source record hash")
    for fact in packet["facts"]:
        assert_true(tuple(fact.keys()) == tool.FACT_KEYS,
                    "fact whitelist and key order")
        assert_chinese(fact["label_cn"], "label should be Chinese")
        assert_chinese(fact["summary_cn"], "summary should be Chinese")
        assert_true(not isinstance(fact["value"], (dict, list)),
                    "value must be scalar")

    body = json.dumps(packet, ensure_ascii=False, sort_keys=True)
    for forbidden in (
            "confidence",
            "side_comfort_ratings",
            "future_24h_bayesian_report",
            "headline_score",
            "durability_score",
            "full_grid",
            "strength_pctl"):
        assert_true(forbidden not in body, f"forbidden legacy field: {forbidden}")

    facts = facts_by_id(packet)
    assert_true(
        facts["structure.gex.net_gamma_notional_usd"]["value"] == 220000000.0,
        "real gex_info notional should outrank tiny gamma proxy")
    assert_true(
        facts["structure.anchor.axis_price"]["source_refs"] == ["factor_cross_section.anchor"],
        "source refs should be traceable source paths")
    assert_true("structure.anchor.score" not in facts,
                "legacy anchor score is not source quality or a new structural fact")
    changed_score = clone(base_card())
    changed_score["factor_cross_section"]["anchor"]["score"] = 100
    changed_score["factor_cross_section"]["anchor"]["anchor_score"] = 0
    assert_true(packet == tool.build_evidence_packet(changed_score),
                "changing only the legacy anchor score must not alter the new input")
    assert_true(
        any("分位样本不足" in item
            for item in facts["structure.gex.net_gamma_notional_usd"]["limitations_cn"]),
        "rank warmup should be a limitation, not source rejection")
    assert_true(facts["pressure.funding.vote_role"]["value"] == "非计票",
                "Funding non-voting should remain explicit")
    assert_true(facts["response.price.path_source"]["value"] == "精确价格点",
                "raw price points should be preserved")

    hash_a = tool.packet_hash(packet)
    hash_b = tool.packet_hash(tool.build_evidence_packet(clone(base_card())))
    assert_true(hash_a == hash_b and hash_a.startswith("sha256:"),
                "packet hash should be canonical and stable")
    tools_dir = str(ROOT / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    review_tool = load_module(REVIEW_TOOL, "signal_review_hash_for_v2_tests")
    assert_true(hash_a == review_tool._sha256_json(packet),
                "packet hash should match existing review canonical hash")


def test_flat_price_is_not_strong_trend(tool):
    card = base_card()
    card["factor_cross_section"]["micro_flow"]["fast_4h"]["price_return_pct"] = 0.0
    packet = tool.build_evidence_packet(card)
    facts = facts_by_id(packet)
    assert_true(facts["side.put.adverse_progress"]["value"] == "平盘",
                "flat put adverse progress")
    assert_true(facts["side.call.adverse_progress"]["value"] == "平盘",
                "flat call adverse progress")
    assert_true("不判定为强趋势" in facts["side.call.adverse_progress"]["summary_cn"],
                "flat summary should reject strong trend wording")
    assert_true(facts["response.flow_price.relation"]["value"] == "传导弱",
                "directional CVD plus flat price should be weak transmission")


def test_unclear_direction_is_chinese(tool):
    card = base_card()
    card["factor_cross_section"]["tmvf"]["direction"] = "Unclear"
    packet = tool.build_evidence_packet(card)
    fact = facts_by_id(packet)["pressure.tmv.direction"]
    assert_true(fact["value"] == "不明", "unclear direction should be Chinese")
    assert_true("Unclear" not in fact["summary_cn"],
                "summary should not expose raw direction enum")


def test_missing_price_response_does_not_infer_from_tmv(tool):
    card = base_card()
    card["factor_cross_section"]["tmvf"]["direction"] = "Bearish"
    for window in ("fast_4h", "slow_12h"):
        card["factor_cross_section"]["micro_flow"][window].pop(
            "price_return_pct", None)
    card["signal_durability"].pop("price_points", None)
    packet = tool.build_evidence_packet(card)
    facts = facts_by_id(packet)
    assert_true(facts["response.price.primary_return_pct"]["value"] == "未知",
                "price response should be unknown")
    assert_true(facts["side.put.adverse_progress"]["value"] == "未知",
                "TMV alone should not become adverse progress")
    assert_true(facts["response.price.primary_return_pct"]["usable"] is False,
                "missing response should be unusable only for dependent facts")


def test_ohlc_proxy_declares_sequence_limitation(tool):
    card = base_card()
    for window in ("fast_4h", "slow_12h"):
        card["factor_cross_section"]["micro_flow"][window].pop(
            "price_return_pct", None)
    card["signal_durability"].pop("price_points", None)
    card["price_anchor_durability"] = {
        "ohlc": {"open": 100.0, "high": 110.0, "low": 90.0, "close": 101.0},
        "durability_score": 88,
    }
    packet = tool.build_evidence_packet(card)
    facts = facts_by_id(packet)
    assert_true(facts["response.price.path_source"]["value"] == "OHLC代理",
                "OHLC should be marked as proxy")
    assert_true(any("路径先后" in item
                    for item in facts["response.price.path_source"]["limitations_cn"]),
                "OHLC sequence limitation")
    assert_true("durability_score" not in json.dumps(packet, ensure_ascii=False),
                "durability score should still be excluded")


def test_missing_explicit_anchor_band_does_not_use_default(tool):
    card = base_card()
    card["factor_cross_section"]["anchor"].pop("band_half", None)
    packet = tool.build_evidence_packet(card)
    facts = facts_by_id(packet)
    assert_true("structure.anchor.band_half" not in facts,
                "no explicit band fact")
    assert_true(facts["structure.anchor.band_position"]["value"] == "未知",
                "band position should be unknown without explicit band")
    assert_true(facts["structure.anchor.band_position"]["usable"] is False,
                "default band should not be usable")
    assert_true("显式带宽" in facts["structure.anchor.band_position"]["summary_cn"],
                "summary should disclose missing explicit band")


def transition_for(tool, previous, current):
    prev_id = previous["identity"]["card_id"]
    curr_id = current["identity"]["card_id"]
    return {
        "schema_version": "signal_transition_record@1.0.0",
        "symbol": "BTC",
        "previous_card_id": prev_id,
        "current_card_id": curr_id,
        "previous_ts_ms": previous["identity"]["confirmed_time_ms"],
        "current_ts_ms": current["identity"]["confirmed_time_ms"],
        "elapsed_ms": (
            current["identity"]["confirmed_time_ms"]
            - previous["identity"]["confirmed_time_ms"]
        ),
        "producer_record_hashes": {
            "previous": previous["producer_integrity"]["record_hash"],
            "current": current["producer_integrity"]["record_hash"],
        },
        "previous_strategy_version": previous["identity"]["strategy_version"],
        "current_strategy_version": current["identity"]["strategy_version"],
        "previous_card_schema": tool.packet_hash(previous["schema"]),
        "current_card_schema": tool.packet_hash(current["schema"]),
    }


def test_transition_requires_full_identity_match(tool):
    previous = base_card("PREV-1", AS_OF_MS - 600000, price=100.0)
    current = base_card("CURR-1", AS_OF_MS, price=102.0)
    current["factor_cross_section"]["gamma_regime"]["flip_point"] = 100.0
    transition = transition_for(tool, previous, current)

    packet = tool.build_evidence_packet(current, previous, transition)
    facts = facts_by_id(packet)
    assert_true(facts["change.context.status"]["value"] == "变化可用",
                "matching transition")
    assert_true(facts["change.price.delta_pct"]["value"] == 2.0,
                "price delta")
    assert_true("change.structure.flip_delta_pct" in facts,
                "structure migration should be separated")

    bad = clone(transition)
    bad["producer_record_hashes"]["current"] = "sha256:" + "bad".ljust(64, "0")
    bad_packet = tool.build_evidence_packet(current, previous, bad)
    bad_facts = facts_by_id(bad_packet)
    assert_true(bad_facts["change.context.status"]["value"] == "变化不可用",
                "bad hash should block change facts")
    assert_true("change.price.delta_pct" not in bad_facts,
                "bad transition should not emit deltas")
    assert_true("market.price.current" in bad_facts,
                "current snapshot facts should remain available")


def run_all():
    tool = load_tool()
    test_packet_whitelist_and_legacy_exclusion(tool)
    test_flat_price_is_not_strong_trend(tool)
    test_unclear_direction_is_chinese(tool)
    test_missing_price_response_does_not_infer_from_tmv(tool)
    test_ohlc_proxy_declares_sequence_limitation(tool)
    test_missing_explicit_anchor_band_does_not_use_default(tool)
    test_transition_requires_full_identity_match(tool)
    print("signal_evidence_v2: PASS")


if __name__ == "__main__":
    run_all()
