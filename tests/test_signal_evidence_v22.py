import importlib.util
import json
from pathlib import Path
import subprocess
import types

from test_signal_evidence_v2 import AS_OF_MS, base_card, clone, transition_for


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "signal_evidence_v2.py"
LIVE_EVIDENCE = (
    ROOT.parent.parent
    / ".artifacts"
    / "astra-v21-assessment-20260911"
    / "live_evidence.json"
)


def load_tool():
    spec = importlib.util.spec_from_file_location("signal_evidence_v22", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_head_tool():
    source = subprocess.check_output(
        ["git", "-C", str(ROOT), "show", "HEAD:tools/signal_evidence_v2.py"],
        encoding="utf-8",
    )
    module = types.ModuleType("signal_evidence_v2_head")
    exec(compile(source, "HEAD:tools/signal_evidence_v2.py", "exec"),
         module.__dict__)
    return module


def facts_by_id(packet):
    return {item["id"]: item for item in packet["facts"]}


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def test_default_packet_is_v21_with_fact_provenance(tool):
    packet = tool.build_evidence_packet(base_card())
    assert_true(packet["schema"] == "signal_evidence_packet@2.1.1",
                "default packet schema should be 2.1.1")
    for fact in packet["facts"]:
        assert_true(tuple(fact.keys()) == tool.FACT_KEYS,
                    "new fact key contract")
        provenance = fact["provenance"]
        assert_true(set(provenance) == {
            "selected_source", "method", "time_basis", "observed_at_ms",
            "generated_at_ms", "fetched_at_ms", "recorded_at_ms", "time_errors",
        }, "provenance contract")

    legacy = tool.build_evidence_packet(
        base_card(), packet_schema="signal_evidence_packet@2.0.0")
    assert_true(legacy["schema"] == "signal_evidence_packet@2.0.0",
                "explicit legacy packet schema")
    for fact in legacy["facts"]:
        assert_true(tuple(fact.keys()) == tool.LEGACY_FACT_KEYS,
                    "legacy fact contract should stay unchanged")
        assert_true("provenance" not in fact,
                    "legacy facts must not gain provenance")


def test_ggr_gex_split_source_time_and_zero_notional(tool):
    card = base_card()
    card["quality"]["sources"]["gex_info"] = {
        "status": "OK",
        "observed_at": "2026-09-02T15:31:00+00:00",
    }
    card["quality"]["sources"]["gamma_regime"] = {
        "status": "OK",
        "observed_at": "2026-09-02T15:35:00+00:00",
    }
    gamma = card["factor_cross_section"]["gamma_regime"]
    gamma.update({
        "regime": "NEGATIVE_GAMMA_AMPLIFYING",
        "observed_at": "2026-09-02T15:35:00+00:00",
        "net_gamma_notional_usd": -0.28439,
        "flip_point": 99.0,
    })
    gex = card["factor_cross_section"]["gex_info"]
    gex.update({
        "quality": "partial",
        "availability": "partial",
        "observed_at": "2026-09-02T15:31:00+00:00",
        "fetched_at_ms": AS_OF_MS - 30000,
        "net_gamma_notional_usd": 0.0,
        "total_net_gex": None,
        "market_state": "positive_gamma",
        "flip_point": 101.0,
        "call_wall": 107.0,
        "put_wall": 93.0,
        "max_gamma_strike": 102.0,
        "dvol": 40.68,
        "rank": {"quality": "missing", "window": {"window_days": 0}},
    })

    facts = facts_by_id(tool.build_evidence_packet(card))
    regime = facts["structure.gamma.regime"]
    assert_true(regime["source_refs"] == ["factor_cross_section.gamma_regime"],
                "GGR regime should not borrow gex source refs")
    assert_true(regime["provenance"]["method"] == "ggr_price_location_regime",
                "GGR regime method")

    net = facts["structure.gex.net_gamma_notional_usd"]
    assert_true(net["value"] == 0.0, "zero gex notional is a valid fact")
    assert_true(net["usable"] is True, "partial gex should allow present field")
    assert_true(net["provenance"]["selected_source"] == "factor_cross_section.gex_info",
                "net gex selected source")
    assert_true(net["provenance"]["fetched_at_ms"] == AS_OF_MS - 30000,
                "net gex fetched time")
    assert_true(any("分位" in item for item in net["limitations_cn"]),
                "rank missing should be a limitation only")

    proxy = facts["structure.gamma.net_gamma_proxy"]
    assert_true(proxy["value"] == -0.28439 and proxy["unit"] is None,
                "GGR proxy must not be labeled USD")
    flip = facts["structure.gamma.flip_point"]
    assert_true(flip["value"] == 101.0,
                "selected flip should use gex field when present")
    assert_true(flip["provenance"]["method"] == "gex_board_flip_point",
                "selected flip provenance")
    dvol = facts["pressure.volatility.dvol"]
    assert_true(dvol["value"] == 40.68 and dvol["unit"] == "DVOL",
                "DVOL should keep original scale")


def test_comparable_schema_key_allows_event_fixed_market_changes(tool):
    previous = base_card("PREV-1", AS_OF_MS - 600000, price=100.0)
    current = base_card("CURR-1", AS_OF_MS, price=102.0)
    previous["schema"]["record_type"] = "confirmed_signal_event_audit"
    current["schema"]["record_type"] = "fixed_analysis_round_audit"
    transition = transition_for(tool, previous, current)

    assert_true(tool._schema_fingerprint(previous) != tool._schema_fingerprint(current),
                "full source schema fingerprint should still include record_type")
    assert_true(tool.comparable_schema_key(previous) == tool.comparable_schema_key(current),
                "comparison key should ignore only record_type")

    new_facts = facts_by_id(tool.build_evidence_packet(current, previous, transition))
    assert_true(new_facts["change.context.status"]["value"] == "变化可用",
                "new comparable key should allow fixed/event comparison")

    legacy_facts = facts_by_id(tool.build_evidence_packet(
        current, previous, transition,
        packet_schema="signal_evidence_packet@2.0.0"))
    assert_true(legacy_facts["change.context.status"]["value"] == "变化不可用",
                "legacy schema comparison should remain strict")


def test_structure_change_closes_only_non_comparable_fact(tool):
    previous = base_card("PREV-2", AS_OF_MS - 600000, price=100.0)
    current = base_card("CURR-2", AS_OF_MS, price=101.0)
    previous["schema"]["record_type"] = "confirmed_signal_event_audit"
    current["schema"]["record_type"] = "fixed_analysis_round_audit"
    previous["factor_cross_section"]["gex_info"]["call_wall"] = 106.0
    current["factor_cross_section"]["gex_info"].pop("call_wall", None)
    current["factor_cross_section"]["gamma_regime"]["call_wall"] = 108.0
    transition = transition_for(tool, previous, current)

    facts = facts_by_id(tool.build_evidence_packet(current, previous, transition))
    assert_true(facts["change.context.status"]["value"] == "变化可用",
                "card-level comparison should be available")
    assert_true(facts["change.price.delta_pct"]["value"] == 1.0,
                "comparable price change should remain available")
    call_change = facts["change.structure.call_wall_delta_pct"]
    assert_true(call_change["value"] == "本项不可比",
                "only source-mismatched call wall change should close")
    assert_true(call_change["usable"] is False,
                "non-comparable field should be unusable")


def test_m_die_raw_15m_units_and_primary_response(tool):
    card = base_card()
    card["factor_cross_section"]["m_die"] = {
        "source_ref": "BINANCE_1M_KLINE",
        "last_closed_bar_time": AS_OF_MS - 1,
        "data_status": {"data_state": "OK"},
        "components": {
            "displacement": {
                "raw": {"window_return_pct": 0.0016051281254692018},
            },
            "path_efficiency": {
                "raw": {"efficiency": 0.27112177445345254},
            },
        },
        "m_die": 0.88,
        "score": 0.88,
    }
    facts = facts_by_id(tool.build_evidence_packet(card))
    raw_return = facts["response.m_die.15m.window_return_pct"]
    assert_true(raw_return["value"] == 0.16051281,
                "M-DIE ratio should be converted to percentage points")
    assert_true("综合分" in " ".join(raw_return["limitations_cn"]),
                "M-DIE score should not be consumed")
    primary = facts["response.price.primary_return_pct"]
    assert_true(primary["value"] == 0.16051281,
                "primary response should use M-DIE raw return when native near-term is absent")
    assert_true(primary["dependencies"] == ["response.m_die.15m.window_return_pct"],
                "primary M-DIE dependency")
    assert_true(facts["side.call.adverse_progress"]["value"] == "上行推进",
                "up move should be call-side adverse progress")


def test_native_near_term_context_summaries_without_raw_bars(tool):
    card = base_card()
    card["near_term_market_context"] = {
        "schema": "near_term_market_context@1.0.0",
        "source": "BINANCE_1M_KLINE_CACHE",
        "symbol": "BTCUSDT",
        "base_unit": "BTC",
        "quote_unit": "USDT",
        "as_of_ms": AS_OF_MS,
        "observed_at_ms": AS_OF_MS - 1,
        "bars": [{"close": 100.0}] * 30,
        "windows": {
            "15m": {
                "state": "OK",
                "requested_start_ms": AS_OF_MS - 15 * 60000,
                "requested_end_ms": AS_OF_MS,
                "observed_start_ms": AS_OF_MS - 15 * 60000,
                "observed_end_ms": AS_OF_MS - 1,
                "bar_count": 15,
                "expected_bar_count": 15,
                "missing_minutes": [],
                "ohlc": {"open": 100.0, "high": 101.2, "low": 99.7, "close": 100.6},
                "return_pct": 0.6,
                "range_pct": 1.5,
                "high_excursion_pct": 1.2,
                "low_excursion_pct": -0.3,
                "distance_from_high_pct": 0.6,
                "distance_from_low_pct": 0.9,
                "close_efficiency": 0.4,
                "total_volume": 18.0,
                "taker_buy_volume": 10.5,
                "net_active_volume": 3.0,
                "active_volume_state": "OK",
            },
            "30m": {
                "state": "PARTIAL",
                "observed_end_ms": AS_OF_MS - 1,
                "bar_count": 28,
                "expected_bar_count": 30,
                "missing_minutes": [AS_OF_MS - 60000, AS_OF_MS - 120000],
                "return_pct": -0.2,
                "active_volume_state": "MISSING",
            },
        },
    }
    packet = tool.build_evidence_packet(card)
    facts = facts_by_id(packet)
    assert_true(facts["response.price.primary_return_pct"]["value"] == 0.6,
                "native near-term should outrank M-DIE and 4h response")
    coverage_15m = facts["response.near_term.15m.coverage"]
    coverage_text = (
        coverage_15m["summary_cn"] + " "
        + " ".join(coverage_15m["limitations_cn"])
    )
    assert_true("实际覆盖" in coverage_15m["summary_cn"],
                "near-term coverage should expose actual observed window")
    for forbidden in ("near_term_market_context@1.0.0", "return_pct", "schema"):
        assert_true(forbidden not in coverage_text,
                    f"near-term readable text should not leak machine field: {forbidden}")
    coverage = facts["response.near_term.30m.coverage"]
    assert_true(coverage["value"] == "部分可用" and "缺口 2 分钟" in coverage["summary_cn"],
                "partial near-term coverage should preserve gap count")
    active = facts["pressure.near_term.15m.net_active_volume"]
    assert_true(active["value"] == 3.0 and active["unit"] == "BTC",
                "near-term active volume should use base unit")
    body = json.dumps(packet, ensure_ascii=False, sort_keys=True)
    assert_true('"bars"' not in body,
                "raw minute bars should not enter the LLM fact packet")


def test_near_term_active_state_and_unit_guards(tool):
    card = base_card()
    card["near_term_market_context"] = {
        "schema": "near_term_market_context@1.0.0",
        "source": "BINANCE_1M_KLINE_CACHE",
        "symbol": "BTCUSDT",
        "as_of_ms": AS_OF_MS,
        "observed_at_ms": AS_OF_MS - 1,
        "windows": {
            "15m": {
                "state": "OK",
                "observed_end_ms": AS_OF_MS - 1,
                "bar_count": 15,
                "expected_bar_count": 15,
                "return_pct": -0.4,
                "net_active_volume": -5.0,
                "active_volume_state": "BROKEN_STATE",
            },
        },
    }
    facts = facts_by_id(tool.build_evidence_packet(card))
    active = facts["pressure.near_term.15m.net_active_volume"]
    assert_true(active["unit"] is None,
                "near-term active volume should not guess base unit")
    assert_true(active["usable"] is False,
                "unknown active volume state should close active-flow fact")
    assert_true("不猜测基础币单位" in " ".join(active["limitations_cn"]),
                "unknown active unit should be stated as a limitation")
    assert_true("不是已知取值" in " ".join(active["limitations_cn"]),
                "unknown active state should be stated as a limitation")
    relation = facts["response.flow_price.relation"]
    assert_true(relation["usable"] is False,
                "unknown active state should also close same-window flow relation")


def test_cvd_sum_does_not_guess_unit_in_new_packet(tool):
    card = base_card()
    card["factor_cross_section"]["micro_flow"]["fast_4h"].pop("cvd_unit", None)
    facts = facts_by_id(tool.build_evidence_packet(card))
    cvd_sum = facts["pressure.cvd.fast_4h.cvd_sum"]
    assert_true(cvd_sum["unit"] is None,
                "new packet should not default missing CVD unit to BTC")
    assert_true("不猜测基础币单位" in " ".join(cvd_sum["limitations_cn"]),
                "missing CVD unit should be an explicit limitation")

    legacy_facts = facts_by_id(tool.build_evidence_packet(
        card, packet_schema="signal_evidence_packet@2.0.0"))
    assert_true(legacy_facts["pressure.cvd.fast_4h.cvd_sum"]["unit"] == "BTC",
                "legacy 2.0 packet should preserve old default unit")


def test_expiry_iv_and_dvol_units(tool):
    card = base_card()
    card["factor_cross_section"]["gex_info"]["dvol"] = 43.1
    card["factor_cross_section"]["skew"]["per_expiry"] = {
        "24h": {
            "data_state": "OK",
            "hours_to_expiry": 7.25,
            "atm_iv": 0.612,
            "rr_25": -0.082,
        },
        "48h": {
            "data_state": "OK",
            "hours_to_expiry": 31.5,
            "atm_iv": 58.4,
            "skew_25d": -6.2,
        },
    }
    facts = facts_by_id(tool.build_evidence_packet(card))
    assert_true(facts["structure.options.24h.hours_to_expiry"]["value"] == 7.25,
                "actual hours to expiry should be preserved")
    assert_true(facts["pressure.options.24h.atm_iv_pct"]["value"] == 61.2,
                "fractional ATM IV should convert to percent")
    assert_true(facts["pressure.options.24h.skew_25d_pct"]["value"] == -8.2,
                "fractional 25D skew should convert to percent")
    assert_true(facts["pressure.options.48h.atm_iv_pct"]["value"] == 58.4,
                "percent ATM IV should not be converted twice")
    assert_true(facts["pressure.volatility.dvol"]["unit"] == "DVOL",
                "DVOL is original scale, not USD")


def test_skew_greeks_epoch_ms_drives_new_packet_time(tool):
    greeks_ms = AS_OF_MS - 123000
    fetched_ms = AS_OF_MS - 45000
    card = base_card()
    skew = card["factor_cross_section"]["skew"]
    skew["greeks_epoch_ms"] = greeks_ms
    skew["fetched_at_ms"] = fetched_ms
    skew["per_expiry"] = {
        "24h": {
            "data_state": "OK",
            "hours_to_expiry": 6.5,
            "atm_iv": 0.55,
            "rr_25": -0.041,
        },
    }

    facts = facts_by_id(tool.build_evidence_packet(card))
    for fact_id in (
            "pressure.skew.vote",
            "structure.options.24h.hours_to_expiry",
            "pressure.options.24h.atm_iv_pct",
            "pressure.options.24h.skew_25d_pct"):
        fact = facts[fact_id]
        assert_true(fact["observed_at_ms"] == greeks_ms,
                    f"{fact_id} should use producer greeks observation time")
        assert_true(fact["provenance"]["observed_at_ms"] == greeks_ms,
                    f"{fact_id} provenance observed time")
        assert_true(fact["provenance"]["fetched_at_ms"] == fetched_ms,
                    f"{fact_id} provenance fetched time")
        assert_true(fact["provenance"]["recorded_at_ms"] == AS_OF_MS,
                    f"{fact_id} provenance card record time")
        assert_true(fact["provenance"]["time_basis"] == "source_greeks_observed_at",
                    f"{fact_id} provenance time basis")
        readable = fact["summary_cn"] + " " + " ".join(fact["limitations_cn"])
        assert_true("greeks_epoch_ms" not in readable,
                    f"{fact_id} should not leak producer field name in readable text")

    legacy = facts_by_id(tool.build_evidence_packet(
        card, packet_schema="signal_evidence_packet@2.0.0"))
    assert_true(legacy["pressure.skew.vote"]["observed_at_ms"] == AS_OF_MS,
                "legacy 2.0 should keep old skew time behavior")


def test_legacy_hash_matches_head_with_skew_greeks_epoch_ms(tool):
    card = base_card()
    card["factor_cross_section"]["skew"]["greeks_epoch_ms"] = AS_OF_MS - 123000
    card["factor_cross_section"]["skew"]["fetched_at_ms"] = AS_OF_MS - 45000
    head = load_head_tool()

    old_packet = head.build_evidence_packet(card, packet_schema="signal_evidence_packet@2.0.0")
    new_packet = tool.build_evidence_packet(
        card, packet_schema="signal_evidence_packet@2.0.0")
    assert_true(old_packet == new_packet,
                "legacy 2.0 packet should match HEAD packet exactly")
    assert_true(head.packet_hash(old_packet) == tool.packet_hash(new_packet),
                "legacy 2.0 packet hash should match HEAD hash")


def test_frozen_v21_cards_event_to_fixed_transition_works(tool):
    if not LIVE_EVIDENCE.exists():
        return
    data = json.loads(LIVE_EVIDENCE.read_text(encoding="utf-8"))
    cards = {
        item["identity"]["card_id"]: item
        for item in data.get("cards", [])
        if isinstance(item, dict) and item.get("identity")
    }
    transition = None
    for item in data.get("transitions", []):
        if (item.get("current_card_id")
                == "20260910T230000+0800-BTC-fixed_us_round_20260910_2300_bjt-4a14"):
            transition = item
            break
    assert_true(transition is not None, "frozen latest transition should exist")
    current = cards[transition["current_card_id"]]
    previous = cards[transition["previous_card_id"]]
    facts = facts_by_id(tool.build_evidence_packet(current, previous, transition))
    assert_true(facts["change.context.status"]["value"] == "变化可用",
                "frozen event-to-fixed transition should be comparable")
    assert_true("change.price.delta_pct" in facts,
                "frozen transition should expose price delta")
    assert_true(facts["structure.gex.net_gamma_notional_usd"]["unit"] == "USD",
                "frozen card should preserve GEX USD unit")
    assert_true(facts["response.m_die.15m.window_return_pct"]["unit"] == "%",
                "frozen card should include M-DIE 15m percent fact")


def run_all():
    tool = load_tool()
    test_default_packet_is_v21_with_fact_provenance(tool)
    test_ggr_gex_split_source_time_and_zero_notional(tool)
    test_comparable_schema_key_allows_event_fixed_market_changes(tool)
    test_structure_change_closes_only_non_comparable_fact(tool)
    test_m_die_raw_15m_units_and_primary_response(tool)
    test_native_near_term_context_summaries_without_raw_bars(tool)
    test_near_term_active_state_and_unit_guards(tool)
    test_cvd_sum_does_not_guess_unit_in_new_packet(tool)
    test_expiry_iv_and_dvol_units(tool)
    test_skew_greeks_epoch_ms_drives_new_packet_time(tool)
    test_legacy_hash_matches_head_with_skew_greeks_epoch_ms(tool)
    test_frozen_v21_cards_event_to_fixed_transition_works(tool)
    print("signal_evidence_v22: PASS")


if __name__ == "__main__":
    run_all()
