import importlib.util
import json
import pathlib
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL_FILE = ROOT / "tools" / "materialize_signal_cards.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("materialize_signal_cards", TOOL_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def record(idx):
    return {
        "identity": {
            "card_id": "20260618T{:06d}+0800-BTC-X".format(idx),
            "confirmed_at": "2026-06-18T{:02d}:{:02d}:00+08:00".format(
                idx // 60, idx % 60),
            "symbol": "BTC",
        },
        "quality": {"overall": "OK"},
    }


def mixed_time_record(card_id, confirmed_at=None, confirmed_time_ms=None):
    identity = {
        "card_id": card_id,
        "symbol": "BTC",
    }
    if confirmed_at is not None:
        identity["confirmed_at"] = confirmed_at
    if confirmed_time_ms is not None:
        identity["confirmed_time_ms"] = confirmed_time_ms
    return {
        "identity": identity,
        "quality": {"overall": "OK"},
    }


def synthetic_record(card_id):
    item = mixed_time_record(card_id, confirmed_at="2026-06-18T18:00:00+08:00")
    item["identity"]["is_synthetic"] = True
    return item


def legacy_push_summary_record():
    item = mixed_time_record("LEGACY-PUSH-SUMMARY",
                             confirmed_at="2026-06-18T19:00:00+08:00")
    item["delivery"] = {
        "fmz_push_summary": "【信号】BTC #abcd 强偏多 置信76未校准 冲突7%",
    }
    item["display_layers"] = {
        "headline": "BTC｜置信度76未校准｜旧提示",
    }
    return item


def legacy_session_context_record():
    item = mixed_time_record("LEGACY-SESSION-CONTEXT",
                             confirmed_at="2026-06-24T09:07:04+08:00")
    item["decision"] = {"lean": "NEUTRAL"}
    item["signal_window"] = {
        "session_context": {
            "schema": "signal_session_context@1.0.0",
            "rationale_code": "ASIA_MORNING",
            "base_zone": "MEDIUM",
            "effective_zone": "MEDIUM",
            "display_label": "MEDIUM",
            "premise_durability": "MEDIUM",
            "affects_confidence": False,
            "affects_blocking": False,
            "affects_trade_allowed": False,
            "utc8_time": "2026-06-24T09:07:04+08:00",
        },
    }
    return item


def auxiliary_evidence_record():
    return {
        "identity": {
            "card_id": "AUX-EVIDENCE",
            "confirmed_at": "2026-06-18T16:00:00+08:00",
            "symbol": "BTC",
        },
        "quality": {"overall": "OK"},
        "reasoning": {
            "evidence": [
                {
                    "key": "FUNDING",
                    "participation_status": "NON_VOTING",
                    "vote": 0.0,
                    "configured_weight": 0.25,
                    "effective_weight": 0.0,
                    "weighted_contribution": 0.0,
                    "source_ref": "factor_cross_section.funding",
                    "exclusion_reason": "DIRECTION_VOTE_DISABLED",
                    "raw_values": {"last_rate": 0.000072},
                },
                {
                    "key": "SRD",
                    "participation_status": "ACTIVE",
                    "vote": -0.61,
                    "configured_weight": 0.70,
                    "effective_weight": 0.56,
                    "weighted_contribution": -0.3416,
                    "source_ref": "factor_cross_section.skew",
                },
                {
                    "key": "GGR_SPATIAL",
                    "participation_status": "GATE_ONLY",
                    "vote": 0.0,
                    "configured_weight": 0.25,
                    "effective_weight": 0.0,
                    "weighted_contribution": 0.0,
                    "source_ref": "factor_cross_section.gamma_regime",
                    "exclusion_reason": "CONFIDENCE_GATE_NOT_DIRECTIONAL_VOTE",
                },
                {
                    "key": "MACRO",
                    "participation_status": "EXCLUDED",
                    "vote": None,
                    "configured_weight": 0.30,
                    "effective_weight": 0.0,
                    "weighted_contribution": 0.0,
                    "source_ref": "factor_cross_section.macro_pressure",
                    "exclusion_reason": "MACRO_BLOCKING_GATE",
                },
            ],
        },
        "factor_cross_section": {
            "funding": {
                "last_funding_rate": 0.000072,
                "tmvf_funding_effect": "overcrowded",
                "source_ref": "BINANCE_FUNDING_RATE",
            },
            "tmvf": {
                "tmvf_48h": {
                    "funding": {
                        "funding_norm": 0.31,
                        "funding_cum": 0.62,
                        "funding_count": 25,
                        "funding_state": "crowded",
                    },
                },
            },
            "skew": {
                "vote": -0.61,
                "rr_blend": -0.059,
                "delta_rr": -0.0032,
                "rr_z": -1.0,
                "vote_confidence": 0.80,
                "source_ref": "DERIBIT_OPTIONS",
            },
            "gamma_regime": {
                "regime": "TRANSITION",
                "regime_strength": 0.112,
                "confidence_multiplier": 0.98,
                "net_gamma_notional_usd": 22870000.0,
                "distance_to_flip_pct": -0.44,
                "pin_strike": 64536.21,
                "distance_to_pin_pct": 0.85,
                "source_ref": "DERIBIT_OPTIONS",
            },
            "macro_pressure": {
                "macro_score": 0.457,
                "macro_regime": "Mild Headwind",
                "macro_data_confidence": 1.0,
                "data_status": "OK",
                "macro_shock": {
                    "block": False,
                    "state": "CLEAR",
                    "reason_codes": ["MACRO_STRONG_HEADWIND"],
                },
                "legacy_blocking_flags": ["MACRO_HEADWIND_BLOCK"],
                "components": [
                    {"key": "VOLQ", "scoring_bps": 150},
                    {"key": "DXY", "scoring_bps": 8},
                    {"key": "US10Y", "scoring_bps": 17.6},
                ],
                "source_ref": "YAHOO_FINANCE",
            },
        },
    }


def transition_record(card_id, confirmed_time_ms, lean, support, macro_score,
                      volq, dxy, us10y, funding_rate,
                      ggr_regime="POSITIVE_GAMMA_PINNING",
                      skew_vote="NEUTRAL", episode="EP-A",
                      tmv_blend=0.42, tmvf_24h_final=0.31,
                      tmvf_48h_final=0.49, net_gamma=12400000.0,
                      put_call_ratio=0.92, conflict_ratio=0.18):
    return {
        "schema": {"name": "signal_review_card", "version": "1.0.0"},
        "identity": {
            "card_id": card_id,
            "short_id": card_id[-4:],
            "episode_id": episode,
            "symbol": "BTC",
            "strategy_version": "1.5.1",
            "confirmed_time_ms": confirmed_time_ms,
            "confirmed_at": "2026-06-18T{:02d}:{:02d}:00+08:00".format(
                (confirmed_time_ms // 3600000) % 24,
                (confirmed_time_ms // 60000) % 60),
        },
        "provenance": {
            "transition_audit_source": {
                "schema_name": "SignalTransitionProducerAnchor",
                "schema_version": "1.0.0",
                "audit_scope": "AUDIT_ONLY",
                "event_time_ms": confirmed_time_ms,
                "event_time_basis": "identity.confirmed_time_ms",
                "transition_computation_owner": "MATERIALIZER_DERIVED",
            },
        },
        "quality": {"overall": "OK"},
        "decision": {
            "lean": lean,
            "support_label": support,
            "confidence": 76 if support == "TRADE_SUPPORT_STRONG" else 0,
            "trade_allowed": support == "TRADE_SUPPORT_STRONG",
        },
        "decision_matrix": {
            "direction": lean,
            "decision_state": "APPROVABLE" if support == "TRADE_SUPPORT_STRONG" else "BLOCKED",
            "model_trade_support": support,
            "execution_allowed": False,
        },
        "blocking": {
            "has_block": support == "NO_TRADE_BLOCKED",
            "block_kind": "HARD" if support == "NO_TRADE_BLOCKED" else None,
            "hard_veto": {"veto_reason": "MACRO_SHOCK"} if support == "NO_TRADE_BLOCKED" else {},
        },
        "reasoning": {
            "evidence": [
                {
                    "key": "TMV",
                    "participation_status": "ACTIVE",
                    "source_ref": "factor_cross_section.tmvf",
                    "raw_values": {
                        "tmv_blend": tmv_blend,
                        "tmvf_24h_final": tmvf_24h_final,
                        "tmvf_48h_final": tmvf_48h_final,
                    },
                },
                {
                    "key": "MACRO",
                    "participation_status": "EXCLUDED",
                    "source_ref": "factor_cross_section.macro_pressure",
                    "raw_values": {
                        "macro_score": macro_score,
                        "macro_regime": "Mild Headwind" if macro_score > 0.2 else "Neutral",
                    },
                },
                {
                    "key": "FUNDING",
                    "participation_status": "NON_VOTING",
                    "source_ref": "factor_cross_section.funding",
                    "raw_values": {"last_rate": funding_rate},
                },
            ],
        },
        "conflict": {
            "ratio": conflict_ratio,
            "level": "MATERIAL" if conflict_ratio >= 0.35 else "LOW",
            "aligned_keys": ["TMV"],
            "dissent_keys": ["MACRO", "SRD"] if conflict_ratio >= 0.35 else ["MACRO"],
        },
        "factor_cross_section": {
            "tmvf": {
                "direction": "Bullish" if tmv_blend > 0 else "Bearish",
                "tmv_blend": tmv_blend,
                "tmvf_24h": {"final": tmvf_24h_final, "tmv_final": tmvf_24h_final},
                "tmvf_48h": {"final": tmvf_48h_final, "tmv_final": tmvf_48h_final},
                "window_conflict": conflict_ratio >= 0.35,
                "source_ref": "BINANCE_1H_KLINE",
            },
            "macro_pressure": {
                "macro_score": macro_score,
                "macro_regime": "Mild Headwind" if macro_score > 0.2 else "Neutral",
                "macro_shock": {
                    "block": support == "NO_TRADE_BLOCKED",
                    "state": "BLOCK" if support == "NO_TRADE_BLOCKED" else "CLEAR",
                    "macro_score_delta": 0.4279 if support == "NO_TRADE_BLOCKED" else 0.0,
                    "volq_bps_delta": 442.4 if support == "NO_TRADE_BLOCKED" else 0.0,
                    "reason_codes": (
                        ["VOLQ_SHOCK_JUMP", "US10Y_PRESSURE_CONFIRM", "MACRO_SHOCK_BLOCKING"]
                        if support == "NO_TRADE_BLOCKED"
                        else ["MACRO_STRONG_HEADWIND"]
                    ),
                },
                "legacy_blocking_flags": (
                    ["MACRO_HEADWIND_BLOCK", "VOLATILITY_SHOCK_CONFIRMED"]
                    if support == "NO_TRADE_BLOCKED"
                    else []
                ),
                "components": [
                    {"key": "VOLQ", "scoring_bps": volq},
                    {"key": "DXY", "scoring_bps": dxy},
                    {"key": "US10Y", "scoring_bps": us10y},
                ],
            },
            "funding": {
                "last_rate": funding_rate,
                "funding_state": "MILD_CROWDED" if funding_rate > 0.00004 else "LOW",
                "effect": "overcrowded" if funding_rate > 0.00004 else "neutral",
            },
            "gamma_regime": {
                "regime": ggr_regime,
                "net_gamma_notional_usd": net_gamma,
                "distance_to_flip_pct": -0.31,
                "distance_to_pin_pct": 0.45,
            },
            "gex_info": {
                "market_state": ggr_regime,
                "net_gamma_notional_usd": net_gamma,
                "put_call_ratio": put_call_ratio,
                "source_ref": "GEX_MONITOR_API",
            },
            "skew": {
                "vote": skew_vote,
                "rr_25d": -0.012 if skew_vote == "BEARISH" else 0.003,
                "rr_z": -1.1 if skew_vote == "BEARISH" else 0.2,
            },
        },
        "integrity": {"record_hash": "sha256:" + card_id.lower()},
    }


def main():
    tool = load_tool()
    source_text = TOOL_FILE.read_text(encoding="utf-8")
    assert_true("deque(maxlen=max_records)" in source_text,
                "main JSONL should be read through a bounded deque")
    assert_true("_read_llm_reviews(llm_reviews, max_records=tail_limit)" in source_text,
                "LLM sidecar should use the same bounded tail limit")

    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        source = root / "signal_review.jsonl"
        reviews = root / "signal_llm_reviews.jsonl"
        output = root / "public"
        source.write_text("\n".join(json.dumps(record(idx), ensure_ascii=False)
                                    for idx in range(750)) + "\n",
                          encoding="utf-8")
        reviews.write_text(json.dumps({
            "card_id": "20260618T000749+0800-BTC-X",
            "llm_review": {"status": "OK", "summary_cn": "tail review"},
        }, ensure_ascii=False) + "\n", encoding="utf-8")

        result = tool.materialize(source, output, max_cards=20,
                                  llm_reviews=reviews)
        manifest = json.loads((output / "signal_cards" / "index.json")
                              .read_text(encoding="utf-8"))
        assert_true("source" not in manifest,
                    "public manifest should not expose server/local JSONL source path")
        assert_true(result["written_cards"] == 20,
                    "should publish requested newest cards")
        assert_true(manifest["cards"][0]["card_id"].endswith("000749+0800-BTC-X"),
                    "newest card should survive bounded tail")
        newest = json.loads((output / manifest["cards"][0]["path"])
                            .read_text(encoding="utf-8"))
        assert_true(newest["llm_review"]["summary_cn"] == "tail review",
                    "tail sidecar review should merge")

    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        source = root / "mixed_signal_review.jsonl"
        output = root / "public"
        records = [
            mixed_time_record("CARD-ISO", confirmed_at="2026-06-18T16:00:00+08:00"),
            mixed_time_record("CARD-MS", confirmed_time_ms=1781770200000),
        ]
        source.write_text("\n".join(json.dumps(item, ensure_ascii=False)
                                    for item in records) + "\n",
                          encoding="utf-8")
        tool.materialize(source, output, max_cards=20)
        manifest = json.loads((output / "signal_cards" / "index.json")
                              .read_text(encoding="utf-8"))
        assert_true([item["card_id"] for item in manifest["cards"]]
                    == ["CARD-MS", "CARD-ISO"],
                    "mixed numeric/ISO timestamps should sort newest first")

    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        source = root / "prune_signal_review.jsonl"
        output = root / "public"
        cards_dir = output / "signal_cards"
        cards_dir.mkdir(parents=True)
        stale = cards_dir / "STALE.json"
        stale.write_text("{}", encoding="utf-8")
        source.write_text(json.dumps(mixed_time_record("CURRENT"),
                                     ensure_ascii=False) + "\n",
                          encoding="utf-8")
        tool.materialize(source, output, max_cards=20)
        assert_true(not stale.exists(),
                    "materializer should remove stale card JSON files outside the current manifest")

    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        source = root / "synthetic_signal_review.jsonl"
        output = root / "public"
        preview_output = root / "preview"
        records = [
            mixed_time_record("REAL-CARD",
                              confirmed_at="2026-06-18T17:00:00+08:00"),
            synthetic_record("SYNTHETIC-CARD"),
        ]
        source.write_text("\n".join(json.dumps(item, ensure_ascii=False)
                                    for item in records) + "\n",
                          encoding="utf-8")

        result = tool.materialize(source, output, max_cards=20)
        manifest = json.loads((output / "signal_cards" / "index.json")
                              .read_text(encoding="utf-8"))
        ids = [item["card_id"] for item in manifest["cards"]]
        fallback = (output / "signal_cards" / "fallback.js").read_text(encoding="utf-8")
        assert_true(ids == ["REAL-CARD"],
                    "production materializer should exclude synthetic preview cards by default")
        assert_true(result["filtered_synthetic_count"] == 1,
                    "materializer should report filtered synthetic cards")
        assert_true(not (output / "signal_cards" / "SYNTHETIC-CARD.json").exists(),
                    "synthetic card JSON should not remain in default deploy output")
        assert_true("SYNTHETIC-CARD" not in fallback,
                    "fallback fixture should not publish synthetic preview cards by default")

        tool.materialize(source, preview_output, max_cards=20,
                         include_synthetic=True)
        preview_manifest = json.loads((preview_output / "signal_cards" / "index.json")
                                      .read_text(encoding="utf-8"))
        preview_ids = [item["card_id"] for item in preview_manifest["cards"]]
        assert_true(preview_ids[0] == "SYNTHETIC-CARD",
                    "explicit preview materialization may include synthetic cards")

    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        source = root / "legacy_push_summary_signal_review.jsonl"
        output = root / "public"
        source.write_text(json.dumps(legacy_push_summary_record(),
                                     ensure_ascii=False) + "\n",
                          encoding="utf-8")
        tool.materialize(source, output, max_cards=20)
        card_text = (output / "signal_cards" / "LEGACY-PUSH-SUMMARY.json").read_text(
            encoding="utf-8")
        fallback = (output / "signal_cards" / "fallback.js").read_text(encoding="utf-8")
        legacy_pattern = "未校准"
        assert_true(legacy_pattern not in card_text,
                    "materialized card JSON should remove legacy confidence calibration reminders")
        assert_true(legacy_pattern not in fallback,
                    "fallback fixture should remove legacy confidence calibration reminders")
        card = json.loads(card_text)
        assert_true("置信76 冲突7%" in card["delivery"]["fmz_push_summary"],
                    "summary should preserve confidence value while removing old reminder")

    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        source = root / "legacy_session_context_signal_review.jsonl"
        output = root / "public"
        source.write_text(json.dumps(legacy_session_context_record(),
                                     ensure_ascii=False) + "\n",
                          encoding="utf-8")
        tool.materialize(source, output, max_cards=20)
        card = json.loads((output / "signal_cards" / "LEGACY-SESSION-CONTEXT.json")
                          .read_text(encoding="utf-8"))
        ctx = card["signal_window"]["session_context"]
        assert_true(ctx["schema_name"] == "SignalSessionPremiseDurabilityContext",
                    "legacy session_context should be upgraded to full schema")
        assert_true(ctx["clock_window"] == "08:00-11:30",
                    "legacy Asia morning should backfill clock window")
        assert_true(ctx["backtest_delta_pp"] == 0.02,
                    "legacy Asia morning should backfill calibrated delta")
        assert_true(ctx["evidence_level"] == "NEUTRAL",
                    "legacy Asia morning should backfill evidence level")
        assert_true(ctx["validation_basis"]["source_document"]
                    == "结论档案_各时段信号耐久度_2023-2026_v1",
                    "legacy session_context should backfill validation source")
        assert_true(ctx["compat_backfill_applied"] is True,
                    "legacy session_context backfill should be explicit")
        assert_true(card["decision_matrix"]["temporal_durability"]
                    == ctx["premise_durability"],
                    "materializer should mirror temporal durability into decision matrix")

    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        source = root / "aux_signal_review.jsonl"
        output = root / "public"
        source.write_text(json.dumps(auxiliary_evidence_record(),
                                     ensure_ascii=False) + "\n",
                          encoding="utf-8")
        tool.materialize(source, output, max_cards=20)
        card = json.loads((output / "signal_cards" / "AUX-EVIDENCE.json")
                          .read_text(encoding="utf-8"))
        rows = {row["key"]: row for row in card["reasoning"]["evidence"]}
        funding = rows["FUNDING"]
        assert_true(funding["auxiliary_role"] == "FUTURES_FUNDING_CROWDING",
                    "materializer should enrich old funding rows with auxiliary role")
        assert_true(funding["auxiliary_lean"] == "BEARISH",
                    "positive funding should surface bearish crowding tendency")
        assert_true(funding["raw_values"]["last_rate"] == 0.000072,
                    "funding raw last_rate should be carried into the ledger")
        assert_true(funding["raw_values"]["funding_norm"] == 0.31,
                    "funding raw crowding norm should be filled from tmvf_48h")
        assert_true(funding["raw_values"]["funding_cum"] == 0.62,
                    "funding raw cumulative funding should be filled from tmvf_48h")
        assert_true(funding["raw_values"]["funding_count"] == 25,
                    "funding raw sample count should be filled from tmvf_48h")
        assert_true(funding["raw_values"]["funding_state"] == "crowded",
                    "funding raw state should be filled from tmvf_48h")
        assert_true(funding["raw_values"]["effect"] == "overcrowded",
                    "funding effect alias should be filled for the ledger")
        srd = rows["SRD"]
        assert_true(srd["auxiliary_role"] == "OPTION_SKEW_DIRECTION",
                    "SRD should expose option-skew role")
        assert_true(srd["auxiliary_lean"] == "BEARISH",
                    "negative SRD vote should surface bearish option-skew tendency")
        assert_true(srd["raw_values"]["rr_blend"] == -0.059,
                    "SRD raw rr_blend should be carried into the ledger")
        ggr = rows["GGR_SPATIAL"]
        assert_true(ggr["auxiliary_role"] == "OPTION_GAMMA_STRUCTURE",
                    "GGR should expose option gamma structure role")
        assert_true(ggr["auxiliary_lean"] == "CONSTRAINT",
                    "transition gamma with multiplier below 1 should surface spatial constraint, not directional adverse wording")
        assert_true(ggr["raw_values"]["confidence_multiplier"] == 0.98,
                    "GGR confidence multiplier should be carried into the ledger")
        assert_true(ggr["raw_values"]["distance_to_flip_pct"] == -0.44,
                    "GGR distance-to-flip context should be carried into the ledger")
        macro = rows["MACRO"]
        assert_true(macro["auxiliary_role"] == "MACRO_CONTEXT",
                    "MACRO should expose macro-context role even when excluded from vote")
        assert_true(macro["auxiliary_lean"] == "BEARISH",
                    "positive macro headwind score should surface bearish risk-asset context")
        assert_true(macro["raw_values"]["macro_score"] == 0.457,
                    "MACRO raw score should be carried into the ledger")
        assert_true(macro["raw_values"]["macro_data_confidence"] == 1.0,
                    "MACRO raw confidence should be carried into the ledger")
        assert_true(len(macro["raw_values"]["components"]) == 3,
                    "MACRO component proxies should be carried into the ledger")
        assert_true(macro["raw_values"]["macro_shock"]["state"] == "CLEAR",
                    "MACRO native shock gate should be carried into raw ledger values")
        assert_true(macro["raw_values"]["legacy_blocking_flags"] == ["MACRO_HEADWIND_BLOCK"],
                    "MACRO legacy blocking flags should remain auditable without becoming native blocking")

    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        source = root / "transition_signal_review.jsonl"
        reviews = root / "signal_transition_llm_reviews.jsonl"
        output = root / "public"
        ledger = root / "signal_transition_ledger.jsonl"
        state = root / "signal_transition_state.json"
        base_ms = 1781770200000
        records = [
            transition_record("CARD-A", base_ms, "BULLISH_STRONG",
                              "TRADE_SUPPORT_STRONG", 0.0309, 150.5, 7.6,
                              -1.8, 0.000015),
            transition_record("CARD-B", base_ms + 60 * 60 * 1000, "NEUTRAL",
                              "NO_TRADE_BLOCKED", 0.4588, 592.9, 14.7,
                              6.2, 0.000054, ggr_regime="TRANSITION",
                              skew_vote="BEARISH", tmv_blend=0.18,
                              tmvf_24h_final=0.11, tmvf_48h_final=0.24,
                              net_gamma=-7600000.0, put_call_ratio=1.22,
                              conflict_ratio=0.62),
        ]
        source.write_text("\n".join(json.dumps(item, ensure_ascii=False)
                                    for item in records) + "\n",
                          encoding="utf-8")
        result = tool.materialize(
            source,
            output,
            max_cards=20,
            transition_ledger=ledger,
            transition_state=state,
            transition_reviews=reviews,
        )
        assert_true(result["transition_records"] == 1,
                    "materializer should build one transition for the non-first card")
        latest = json.loads((output / "signal_cards" / "CARD-B.json")
                            .read_text(encoding="utf-8"))
        transition = latest.get("transition_context")
        assert_true(transition["schema_name"] == "SignalTransitionRecord",
                    "card should receive materialized transition context")
        assert_true(transition["schema_version"] == "signal_transition_record@1.0.0",
                    "transition schema version")
        assert_true(transition["audit_scope"] == "AUDIT_ONLY",
                    "transition context must be audit-only")
        assert_true(transition["producer_anchor"]["current"]["native"] is True,
                    "native producer anchor should be preserved on transition")
        assert_true(transition["compat_backfill_applied"] is False,
                    "native transition should not be marked as compat backfill")
        assert_true(transition["previous_card_id"] == "CARD-A",
                    "transition should link immediate predecessor")
        assert_true(transition["elapsed_ms"] == 60 * 60 * 1000,
                    "transition should expose exact elapsed time")
        assert_true(transition["comparison_quality"] == "HIGH",
                    "one hour comparison should be high quality")
        assert_true(transition["decision_transition"]["block_entered"] is True,
                    "decision support collapse should enter block")
        assert_true("DECISION_SUPPORT_COLLAPSE" in transition["cross_domain_flags"],
                    "decision support collapse flag")
        assert_true("MACRO_SHOCK" in transition["cross_domain_flags"],
                    "macro shock flag")
        assert_true("MULTI_DOMAIN_RISK_DETERIORATION" in transition["cross_domain_flags"],
                    "multi-domain risk deterioration flag")
        fields = {item["field"]: item for item in transition["top_material_changes"]}
        assert_true(fields["factor_cross_section.macro_pressure.macro_score"]["delta_abs"] == 0.4279,
                    "macro score delta should be calculated from canonical raw fields")
        assert_true(fields["factor_cross_section.macro_pressure.components.US10Y.scoring_bps"]["sign_flip"] is True,
                    "US10Y pressure should detect sign flip")
        assert_true(fields["factor_cross_section.funding.last_rate"]["role_before"] == "NON_VOTING",
                    "NON_VOTING raw funding should still be compared")
        assert_true(transition["llm_review_required"] is True,
                    "material event should request transition LLM review")
        assert_true("future" not in json.dumps(transition, ensure_ascii=False).lower(),
                    "real-time transition context must not include future outcome fields")
        skeleton = transition.get("core_skeleton")
        assert_true(skeleton and skeleton["schema_version"] == "transition_core_skeleton@1.0.0",
                    "transition should expose a stable multi-domain core skeleton")
        skeleton_domains = {item["domain"]: item for item in skeleton["domains"]}
        for domain in ("TMV", "MACRO", "FUNDING", "SKEW", "GAMMA",
                       "P_C_RATIO", "CONFLICT", "DECISION", "QUALITY"):
            assert_true(domain in skeleton_domains,
                        "core skeleton should include domain " + domain)
        assert_true(skeleton_domains["TMV"]["current"]["tmv_blend"] == 0.18,
                    "TMV skeleton should carry current canonical tmv_blend")
        assert_true(skeleton_domains["GAMMA"]["current"]["net_gamma_notional_usd"] == -7600000.0,
                    "Gamma skeleton should carry current net gamma")
        assert_true(skeleton_domains["P_C_RATIO"]["current"]["put_call_ratio"] == 1.22,
                    "P/C skeleton should carry current put-call ratio")
        assert_true(skeleton_domains["CONFLICT"]["current"]["ratio"] == 0.62,
                    "conflict skeleton should carry current conflict ratio")
        assert_true(skeleton_domains["MACRO"]["current"]["macro_shock_state"] == "BLOCK",
                    "MACRO skeleton should carry producer-native macro shock state when present")
        assert_true(skeleton_domains["MACRO"]["current"]["macro_shock_block"] is True,
                    "MACRO skeleton should carry producer-native macro shock block when present")
        summaries = transition.get("domain_change_summaries") or []
        macro_summaries = [item for item in summaries if item.get("domain") == "MACRO"]
        assert_true(len(macro_summaries) == 1,
                    "split macro component changes should collapse to one MACRO summary")
        assert_true(macro_summaries[0]["raw_change_count"] >= 3,
                    "MACRO summary should preserve child raw change count")
        macro_child_fields = {item["field"] for item in macro_summaries[0]["children"]}
        assert_true("factor_cross_section.macro_pressure.components.US10Y.scoring_bps" in macro_child_fields,
                    "MACRO summary should retain raw child field trace")
        raw_groups = {item["domain"]: item for item in transition.get("raw_change_groups") or []}
        assert_true("MACRO" in raw_groups and raw_groups["MACRO"]["raw_change_count"] >= 3,
                    "raw change groups should retain full grouped MACRO trace")
        recent = transition.get("recent_5_trajectory") or []
        assert_true(recent and "tmv_blend" in recent[-1]
                    and "net_gamma_notional_usd" in recent[-1]
                    and "put_call_ratio" in recent[-1]
                    and "conflict_ratio" in recent[-1],
                    "recent trajectory should include the multi-domain event skeleton")

        ledger_lines = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
        assert_true(len(ledger_lines) == 1,
                    "ledger should contain one transition record")
        assert_true(ledger_lines[0]["current_card_id"] == "CARD-B",
                    "ledger should align to latest transition card")
        assert_true(ledger_lines[0]["record_hash"].startswith("sha256:"),
                    "ledger should include hash-chain record hash")
        ledger_text = ledger.read_text(encoding="utf-8")
        tool.materialize(
            source,
            output,
            max_cards=20,
            transition_ledger=ledger,
            transition_state=state,
            transition_reviews=reviews,
        )
        assert_true(ledger.read_text(encoding="utf-8") == ledger_text,
                    "same input should replay to the same transition ledger hash chain")
        state_doc = json.loads(state.read_text(encoding="utf-8"))
        assert_true(state_doc["last_transition_hash"] == ledger_lines[0]["record_hash"],
                    "state should persist last transition hash")
        trajectory = json.loads((output / "signal_cards" / "trajectory" / "BTC.json")
                                .read_text(encoding="utf-8"))
        assert_true(trajectory["symbol"] == "BTC" and trajectory["event_count"] == 2,
                    "trajectory output should summarize symbol event history")

    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        source = root / "transition_legacy_signal_review.jsonl"
        output = root / "public"
        base_ms = 1781770200000
        legacy_records = [
            transition_record("LEGACY-A", base_ms, "BULLISH_STRONG",
                              "TRADE_SUPPORT_STRONG", 0.0309, 150.5, 7.6,
                              -1.8, 0.000015),
            transition_record("LEGACY-B", base_ms + 60 * 60 * 1000, "NEUTRAL",
                              "NO_TRADE_BLOCKED", 0.4588, 592.9, 14.7,
                              6.2, 0.000054, ggr_regime="TRANSITION",
                              skew_vote="BEARISH", tmv_blend=0.18,
                              tmvf_24h_final=0.11, tmvf_48h_final=0.24,
                              net_gamma=-7600000.0, put_call_ratio=1.22,
                              conflict_ratio=0.62),
        ]
        for item in legacy_records:
            item["provenance"].pop("transition_audit_source", None)
        source.write_text("\n".join(json.dumps(item, ensure_ascii=False)
                                    for item in legacy_records) + "\n",
                          encoding="utf-8")
        tool.materialize(source, output, max_cards=20)
        legacy_latest = json.loads((output / "signal_cards" / "LEGACY-B.json")
                                   .read_text(encoding="utf-8"))
        legacy_transition = legacy_latest["transition_context"]
        assert_true(legacy_transition["compat_backfill_applied"] is True,
                    "missing producer anchor should be explicit compat backfill")
        assert_true(legacy_transition["producer_anchor"]["current"]["native"] is False,
                    "missing producer anchor must not masquerade as native")
        assert_true("identity.confirmed_time_ms" in
                    legacy_transition["compat_source_fields"],
                    "compat transition should record source fields")

    print("materializer_tail_window: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("materializer_tail_window: FAIL - " + str(exc))
        sys.exit(1)
