import importlib.util
import json
import pathlib
import subprocess
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


def native_signal_durability_record():
    item = mixed_time_record("NATIVE-SIGNAL-DURABILITY",
                             confirmed_at="2026-06-24T10:00:00+08:00")
    item["signal_durability"] = {
        "schema_name": "SignalDurabilityLayer",
        "schema_version": "nrd.signal.durability_layer.v1",
        "audit_scope": "AUDIT_ONLY",
        "headline_score": 72,
        "headline_state": "ANCHOR_DURABLE",
        "comfort_window": {
            "tag": "US_T2_CORE_COMFORT",
            "state": "COMFORTABLE",
            "brief_token": "T2C",
        },
        "temporal_session": {"state": "MEDIUM", "score": 0.61},
        "session_context": {"state": "MEDIUM", "score": 0.61},
        "price_anchor_durability": {
            "schema_name": "SignalPriceAnchorDurability",
            "schema_version": "nrd.signal.price_anchor_durability.v1",
            "durability_state": "ANCHOR_DURABLE",
            "durability_score": 72,
            "state": "ANCHOR_DURABLE",
            "score": 72,
            "layer_scores": {
                "anchor_native": {"score": 0.72},
                "price_efficiency": {"score": 0.65},
                "options_gamma": {"score": 0.80},
                "perp_funding": {"score": 0.70},
            },
        },
        "layer_scores": {
            "anchor_native": {"score": 0.72},
            "price_efficiency": {"score": 0.65},
            "options_gamma": {"score": 0.80},
            "perp_funding": {"score": 0.70},
        },
        "reason_codes": ["NATIVE_REASON"],
        "data_gaps": ["NATIVE_GAP"],
        "producer_marker": {"keep": True},
    }
    return item


def alias_signal_durability_record():
    item = mixed_time_record("ALIAS-SIGNAL-DURABILITY",
                             confirmed_at="2026-06-24T10:05:00+08:00")
    item["comfort_window"] = {
        "tag": "US_T2_CORE_COMFORT",
        "state": "COMFORTABLE",
        "brief_token": "T2C",
    }
    item["price_anchor_durability"] = {
        "durability_state": "ANCHOR_DURABLE",
        "durability_score": 72,
        "state": "ANCHOR_DURABLE",
        "score": 72,
        "anchor_price": 101000,
        "layer_scores": {
            "anchor_native": {"score": 0.72},
            "price_efficiency": {"score": 0.65},
            "options_gamma": {"score": 0.80},
            "perp_funding": {"score": 0.70},
        },
    }
    item["temporal_session"] = {"state": "MEDIUM", "score": 0.61}
    item["durability_layer_scores"] = {
        "anchor_native": {"score": 0.72},
        "price_efficiency": {"score": 0.65},
        "options_gamma": {"score": 0.80},
        "perp_funding": {"score": 0.70},
    }
    item["durability_reason_codes"] = ["ALIAS_REASON"]
    item["durability_data_gaps"] = ["ALIAS_GAP"]
    return item


def partial_native_signal_durability_record():
    item = mixed_time_record("PARTIAL-SIGNAL-DURABILITY",
                             confirmed_at="2026-06-24T10:03:00+08:00")
    item["signal_durability"] = {
        "audit_scope": "AUDIT_ONLY",
        "comfort_window": {"tag": "US_T2_CORE_COMFORT"},
        "price_anchor_durability": {
            "durability_score": 72,
            "durability_state": "ANCHOR_DURABLE",
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
        source = root / "bad_tail_signal_review.jsonl"
        output = root / "public"
        cards_dir = output / "signal_cards"
        cards_dir.mkdir(parents=True)
        old_manifest = '{"sentinel":"keep"}\n'
        (cards_dir / "index.json").write_text(old_manifest, encoding="utf-8")
        source.write_text(json.dumps(record(1), ensure_ascii=False)
                          + "\n{not-json\n\n",
                          encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                str(TOOL_FILE),
                "--source", str(source),
                "--output", str(output),
                "--max-cards", "20",
                "--require-valid-source-tail",
            ],
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        assert_true(result.returncode != 0,
                    "strict source tail mode should fail on a corrupt latest source line")
        assert_true("last non-empty source line" in result.stderr
                    and "not valid JSON" in result.stderr,
                    "strict source tail failure should explain the corrupt tail")
        assert_true((cards_dir / "index.json").read_text(encoding="utf-8")
                    == old_manifest,
                    "strict source tail failure must not overwrite the existing manifest")

    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        source = root / "missing_identity_tail_signal_review.jsonl"
        output = root / "public"
        source.write_text(json.dumps({"identity": {}, "quality": {"overall": "OK"}},
                                     ensure_ascii=False) + "\n",
                          encoding="utf-8")
        try:
            tool.materialize(source, output, max_cards=20,
                             require_valid_source_tail=True)
            raise AssertionError("missing identity.card_id tail should fail")
        except tool.SourceTailValidationError as exc:
            assert_true("missing identity.card_id" in str(exc),
                        "strict source tail failure should name identity.card_id")

    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        output = root / "public"
        cards_dir = output / "signal_cards"
        cards_dir.mkdir(parents=True)
        old_manifest = '{"sentinel":"keep"}\n'
        (cards_dir / "index.json").write_text(old_manifest, encoding="utf-8")
        for source, expected in (
                (root / "missing.jsonl", "does not exist"),
                (root / "empty.jsonl", "no non-empty records")):
            if source.name == "empty.jsonl":
                source.write_text("\n\n", encoding="utf-8")
            try:
                tool.materialize(source, output, max_cards=20,
                                 require_valid_source_tail=True)
                raise AssertionError("missing or empty strict source should fail")
            except tool.SourceTailValidationError as exc:
                assert_true(expected in str(exc),
                            "strict source failure should explain " + expected)
            assert_true((cards_dir / "index.json").read_text(encoding="utf-8")
                        == old_manifest,
                        "strict source failure must preserve the existing manifest")

    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        source = root / "middle_bad_valid_tail_signal_review.jsonl"
        output = root / "public"
        source.write_text(json.dumps(record(1), ensure_ascii=False)
                          + "\n{not-json\n"
                          + json.dumps(record(2), ensure_ascii=False) + "\n",
                          encoding="utf-8")
        result = tool.materialize(source, output, max_cards=20,
                                  require_valid_source_tail=True)
        manifest = json.loads((output / "signal_cards" / "index.json")
                              .read_text(encoding="utf-8"))
        ids = [item["card_id"] for item in manifest["cards"]]
        assert_true(result["skipped_lines"] == 1,
                    "middle corrupt source lines should still be reported as skipped")
        assert_true(ids == [record(2)["identity"]["card_id"],
                            record(1)["identity"]["card_id"]],
                    "valid tail should allow materialization despite earlier corrupt lines")

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
        source = root / "signal_durability_signal_review.jsonl"
        output = root / "public"
        source.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in [
            native_signal_durability_record(),
            partial_native_signal_durability_record(),
            alias_signal_durability_record(),
            mixed_time_record("OLD-CARD-NO-DURABILITY",
                              confirmed_at="2026-06-24T09:55:00+08:00"),
        ]) + "\n", encoding="utf-8")
        tool.materialize(source, output, max_cards=20)

        native = json.loads((output / "signal_cards"
                             / "NATIVE-SIGNAL-DURABILITY.json").read_text(
                                 encoding="utf-8"))
        native_durability = native["signal_durability"]
        assert_true(native_durability["headline_score"] == 72,
                    "native signal_durability headline score should pass through")
        assert_true(native_durability["producer_marker"]["keep"] is True,
                    "native signal_durability custom producer fields should be preserved")
        assert_true(native_durability["schema_name"] == "SignalDurabilityLayer",
                    "native signal_durability should keep canonical schema name")
        assert_true(native_durability["schema_version"]
                    == "nrd.signal.durability_layer.v1",
                    "native signal_durability should receive canonical schema version")
        assert_true(native_durability["confidence_policy"]
                    == "DO_NOT_MULTIPLY_CONFIDENCE",
                    "native signal_durability should receive harmless confidence policy default")
        assert_true(native_durability.get("compat_backfill_applied") is not True,
                    "native signal_durability must not be marked as materializer backfill")

        partial = json.loads((output / "signal_cards"
                              / "PARTIAL-SIGNAL-DURABILITY.json").read_text(
                                  encoding="utf-8"))
        partial_durability = partial["signal_durability"]
        assert_true(partial_durability["compat_backfill_applied"] is True,
                    "partial native-looking signal_durability should be marked compat")
        assert_true(partial_durability["compat_backfill_source"]
                    == "materializer_signal_durability_partial_v1",
                    "partial durability should expose partial compat source")
        assert_true("schema_name"
                    in partial_durability["compat_missing_native_fields"],
                    "partial durability should retain missing native-field evidence")

        alias = json.loads((output / "signal_cards"
                            / "ALIAS-SIGNAL-DURABILITY.json").read_text(
                                encoding="utf-8"))
        alias_durability = alias["signal_durability"]
        assert_true(alias_durability["compat_backfill_applied"] is True,
                    "alias signal_durability wrapper should be marked compat backfill")
        assert_true(alias_durability["compat_backfill_source"]
                    == "materializer_signal_durability_alias_v1",
                    "alias signal_durability wrapper should expose compat source")
        assert_true(alias_durability["comfort_window"]["tag"] == "US_T2_CORE_COMFORT",
                    "alias comfort_window should be wrapped into signal_durability")
        assert_true(alias_durability["price_anchor_durability"]["durability_state"]
                    == "ANCHOR_DURABLE",
                    "alias price_anchor_durability should be wrapped")
        assert_true(alias_durability["headline_score"] == 72,
                    "alias wrapper should copy existing score without inventing one")
        assert_true(alias_durability["layer_scores"]["price_efficiency"]["score"] == 0.65,
                    "alias layer scores should be retained")
        assert_true(alias_durability["audit_scope"] == "AUDIT_ONLY",
                    "alias durability wrapper should stay audit-only")

        old = json.loads((output / "signal_cards"
                          / "OLD-CARD-NO-DURABILITY.json").read_text(
                              encoding="utf-8"))
        assert_true("signal_durability" not in old,
                    "old cards without durability aliases should not invent scores")
        fallback = (output / "signal_cards" / "fallback.js").read_text(
            encoding="utf-8")
        assert_true('"signal_durability"' in fallback
                    and "NATIVE_REASON" in fallback
                    and "ALIAS_REASON" in fallback,
                    "fallback.js should retain materialized signal_durability fields")
        manifest = json.loads((output / "signal_cards" / "index.json")
                              .read_text(encoding="utf-8"))
        assert_true("source" not in manifest,
                    "public manifest should still not expose JSONL source path")

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
        assert_true(funding["auxiliary_role"] == "FUTURES_FUNDING_SEMANTICS",
                    "materializer should enrich old funding rows with canonical role")
        assert_true(funding["auxiliary_lean"] == "NEUTRAL",
                    "sub-threshold funding must remain non-voting")
        semantics = funding["canonical_funding_semantics"]
        assert_true(semantics["compat_backfill_applied"] is True,
                    "legacy Funding semantics must disclose compatibility backfill")
        assert_true(semantics["crowding_state"] == "NOT_CROWDED"
                    and semantics["reflexivity_importance"] == "NOISE",
                    "sub-threshold Funding must have one non-crowded meaning")
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
        pc_entry = next(item for item in tool.TRANSITION_FIELD_REGISTRY
                        if item["path"] == "factor_cross_section.gex_info.put_call_ratio")
        pc_change = tool._compare_continuous(
            pc_entry,
            {"value": -2.29, "role": "CONTEXT", "source_ref": "factor_cross_section.gex_info"},
            {"value": 2.18, "role": "CONTEXT", "source_ref": "factor_cross_section.gex_info"},
            60 * 60 * 1000,
        )
        assert_true(pc_change["sign_flip"] is False,
                    "P/C ratio is a non-negative ratio and must not emit sign-flip semantics")
        assert_true(pc_change["meaning"] != "RISK_HEADWIND_SIGN_FLIP",
                    "P/C ratio should not use generic risk-headwind sign-flip meaning")
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
        display_rows = {item["domain"]: item for item in transition.get("core_transition_display") or []}
        for domain in ("TMV", "MACRO", "FUNDING", "SKEW", "GAMMA",
                       "P_C_RATIO", "CONFLICT", "DECISION", "QUALITY"):
            assert_true(domain in display_rows,
                        "core transition display should include domain " + domain)
        assert_true(display_rows["FUNDING"]["value_key"] == "last_rate",
                    "Funding display must prioritize raw last_rate over funding_norm")
        assert_true(display_rows["FUNDING"]["previous_display"] == "0.0015%"
                    and display_rows["FUNDING"]["current_display"] == "0.0054%",
                    "Funding display should format decimal rates as percentages")
        assert_true("温和多头费率倾向" in display_rows["FUNDING"]["meaning_cn"]
                    and "未超过 ±0.0100% 拥挤阈值" in display_rows["FUNDING"]["meaning_cn"]
                    and "反身性影响可忽略" in display_rows["FUNDING"]["meaning_cn"],
                    "Funding below 0.01% should reuse canonical mild semantics")
        assert_true("-0M" not in display_rows["GAMMA"]["previous_display"]
                    and "-0M" not in display_rows["GAMMA"]["current_display"],
                    "Gamma display must never collapse small or scaled values to -0M")
        assert_true("符号翻转" not in display_rows["P_C_RATIO"]["meaning_cn"],
                    "P/C display should explain demand change, not sign flip")

        boundary_records = [
            transition_record("CARD-GEX-A", base_ms + 2 * 60 * 60 * 1000,
                              "BULLISH_STRONG", "TRADE_SUPPORT_STRONG",
                              0.0309, 150.5, 7.6, -1.8, 0.000099,
                              net_gamma=0.39, put_call_ratio=1.05),
            transition_record("CARD-GEX-B", base_ms + 3 * 60 * 60 * 1000,
                              "BULLISH_STRONG", "TRADE_SUPPORT_STRONG",
                              0.0409, 160.5, 8.6, -1.6, 0.0001,
                              net_gamma=0.39, put_call_ratio=1.06),
        ]
        for item, gex_value in zip(boundary_records, (100000000.0, 112000000.0)):
            item["factor_cross_section"]["gamma_regime"]["net_gamma_notional_usd"] = 0.39
            item["factor_cross_section"]["gamma_regime"]["net_gamma_notional"] = 0.39
            item["factor_cross_section"]["gex_info"]["net_gamma_notional_usd"] = gex_value
            item["factor_cross_section"]["gex_info"]["total_net_gex"] = gex_value
        boundary_source = root / "transition_boundary_signal_review.jsonl"
        boundary_output = root / "boundary_public"
        boundary_source.write_text("\n".join(json.dumps(item, ensure_ascii=False)
                                             for item in boundary_records) + "\n",
                                   encoding="utf-8")
        tool.materialize(boundary_source, boundary_output, max_cards=20)
        boundary_latest = json.loads((boundary_output / "signal_cards"
                                      / "CARD-GEX-B.json").read_text(
                                          encoding="utf-8"))
        boundary_transition = boundary_latest["transition_context"]
        boundary_domains = {item["domain"]: item
                            for item in boundary_transition["core_skeleton"]["domains"]}
        assert_true(boundary_domains["GAMMA"]["current"]["net_gamma_notional_usd"] == 112000000.0,
                    "transition skeleton should prefer real gex_info USD notional over tiny gamma proxy")
        boundary_display = {item["domain"]: item
                            for item in boundary_transition["core_transition_display"]}
        assert_true(boundary_display["GAMMA"]["current_display"] == "$112M",
                    "transition display should show real GEX USD notional, not 0.39 proxy")
        assert_true("温和多头费率倾向" in boundary_display["FUNDING"]["meaning_cn"]
                    and "未超过 ±0.0100% 拥挤阈值" in boundary_display["FUNDING"]["meaning_cn"]
                    and "EDB 不计票" in boundary_display["FUNDING"]["meaning_cn"],
                    "Funding exactly 0.0100% should stay canonical non-voting")

        ledger_lines = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
        assert_true(len(ledger_lines) == 1,
                    "ledger should contain one transition record")
        assert_true(ledger_lines[0]["current_card_id"] == "CARD-B",
                    "ledger should align to latest transition card")
        assert_true(ledger_lines[0]["record_hash"].startswith("sha256:"),
                    "ledger should include hash-chain record hash")
        ledger_text = ledger.read_text(encoding="utf-8")
        review_payload = {
            "transition_id": ledger_lines[0]["transition_id"],
            "current_card_id": "CARD-B",
            "symbol": "BTC",
            "transition_llm_review": {
                "schema_name": "SignalTransitionLlmReview",
                "schema_version": "signal_transition_llm_review@1.2.1",
                "status": "OK",
                "provider": "gemini",
                "model": "gemini-3.5-flash",
                "prompt_version": "gemini_signal_transition_review_prompt@1.2.1",
                "blind_review_mode": "single_call_evidence_first",
                "llm_call_count": 1,
                "evidence_catalog_schema_version": "transition_evidence_catalog@1.0.0",
                "evidence_catalog_hash": "sha256:test-catalog",
                "transition_summary_cn": "宏观压力与资金费率同步抬升，人工审计应优先核验约束是否持续。",
                "trajectory_state": "DETERIORATING",
                "signal_continuity": "BLOCKED",
                "observed_changes": [{
                    "domain": "MACRO",
                    "fact_cn": "macro_score 从 0.0309 升至 0.4588。",
                    "impact_cn": "宏观背景从轻压力转为硬约束，足以改变人工审计关注重点。",
                    "tendency_cn": "利空/风险约束",
                    "evidence_refs": ["/domain_change_summaries/0"],
                    "evidence_status": "SUFFICIENT",
                    "directional_role": "RISK_CONSTRAINT",
                    "magnitude_verdict": "changes_judgment",
                    "audit_attention_effect": "SHIFT_FOCUS",
                    "epistemic_status": "SUPPORTED_INFERENCE",
                }],
                "cross_factor_interactions": ["宏观压力与 Funding 升温共振。"],
                "cross_factor_assessments": [{
                    "domains": ["MACRO", "FUNDING"],
                    "relation": "REINFORCING",
                    "assessment_cn": "宏观压力与资金费率同步升温，共同提高风险约束。",
                    "evidence_refs": ["/domain_change_summaries/0", "/core_transition_display/2"],
                }],
                "operator_focus": ["观察宏观冲击门是否连续维持。"],
                "operator_checks": [{
                    "focus_cn": "核验宏观冲击门连续性。",
                    "why_cn": "连续性决定该约束是短噪声还是状态切换。",
                    "strengthens_if_cn": "若后续卡仍处于 BLOCK，则约束解释增强。",
                    "weakens_if_cn": "若后续卡回到 CLEAR，则约束解释减弱。",
                    "evidence_refs": ["/domain_change_summaries/0"],
                }],
                "invalid_if": ["两张卡不再属于可比较市场阶段。"],
                "language_guard": {
                    "distinguishes_observation_from_causality": True,
                    "no_external_data": True,
                    "no_trading_instruction": True,
                },
                "not_trading_advice": True,
                "policy_validation": {
                    "passed": True,
                    "severity": "OK",
                    "render_state": "DISPLAY_LLM_TEXT",
                    "invalid_evidence_refs": [],
                },
            },
        }
        reviews.write_text(json.dumps(review_payload, ensure_ascii=False) + "\n",
                           encoding="utf-8")
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
        latest_with_review = json.loads((output / "signal_cards" / "CARD-B.json")
                                        .read_text(encoding="utf-8"))
        review = latest_with_review["transition_llm_review"]
        assert_true(review["schema_version"] == "signal_transition_llm_review@1.2.1",
                    "materializer should merge current transition review sidecar")
        assert_true(review["evidence_catalog_hash"] == "sha256:test-catalog"
                    and review["policy_validation"]["render_state"] == "DISPLAY_LLM_TEXT",
                    "materializer should preserve v1.2.1 provenance and render-state fields")
        assert_true(review["operator_checks"][0]["focus_cn"] == "核验宏观冲击门连续性。",
                    "materializer should preserve operator checks without backfilling conclusions")
        assert_true(review["policy_validation"]["passed"] is True,
                    "materializer should preserve runner-side policy validation")
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

    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        source = root / "transition_historical_units_signal_review.jsonl"
        output = root / "public"
        base_ms = 1781770200000
        previous = transition_record("HIST-A", base_ms, "NEUTRAL",
                                     "NO_TRADE_BLOCKED", 0.6365, 120, 8,
                                     6, 0.0000038, ggr_regime="TRANSITION",
                                     skew_vote="BEARISH", tmv_blend=-0.2082,
                                     tmvf_24h_final=0.1218,
                                     tmvf_48h_final=-0.11,
                                     net_gamma=-1.5562, put_call_ratio=2.29,
                                     conflict_ratio=0.1321)
        current = transition_record("HIST-B", base_ms + 60 * 60 * 1000,
                                    "NEUTRAL", "NO_TRADE_BLOCKED", 0.6367,
                                    121, 8.2, 6.1, -0.00001438,
                                    ggr_regime="TRANSITION", skew_vote="BEARISH",
                                    tmv_blend=-0.2376,
                                    tmvf_24h_final=0.0609,
                                    tmvf_48h_final=-0.12,
                                    net_gamma=-1.6217, put_call_ratio=2.18,
                                    conflict_ratio=0.1760)
        previous["factor_cross_section"]["funding"]["funding_norm"] = -0.1359
        current["factor_cross_section"]["funding"]["funding_norm"] = -0.1359
        source.write_text(json.dumps(previous, ensure_ascii=False) + "\n"
                          + json.dumps(current, ensure_ascii=False) + "\n",
                          encoding="utf-8")
        tool.materialize(source, output, max_cards=20)
        latest = json.loads((output / "signal_cards" / "HIST-B.json")
                            .read_text(encoding="utf-8"))
        display_rows = {item["domain"]: item
                        for item in latest["transition_context"].get("core_transition_display") or []}
        assert_true(display_rows["FUNDING"]["previous_display"] == "0.00038%",
                    "historical funding display should preserve tiny positive rate as percent")
        assert_true(display_rows["FUNDING"]["current_display"] == "-0.001438%",
                    "historical funding display should preserve tiny negative rate as percent")
        assert_true("-0.1359" not in json.dumps(display_rows["FUNDING"], ensure_ascii=False),
                    "historical funding display should not promote funding_norm as the main rate")
        assert_true(display_rows["GAMMA"]["previous_display"] == "-1.5562"
                    and display_rows["GAMMA"]["current_display"] == "-1.6217",
                    "historical tiny Gamma values should remain precise instead of becoming -0M")
        assert_true("旧卡兼容推导" in display_rows["GAMMA"]["source_note"],
                    "small historical Gamma values should be labeled as compat metric, not USD notional")
        assert_true("符号翻转" not in display_rows["P_C_RATIO"]["meaning_cn"],
                    "historical P/C ratio decline should not be described as sign flip")

    print("materializer_tail_window: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("materializer_tail_window: FAIL - " + str(exc))
        sys.exit(1)
