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
                "regime": "POSITIVE_GAMMA_PINNING",
                "regime_strength": 0.86,
                "confidence_multiplier": 0.98,
                "net_gamma_notional_usd": 22870000.0,
                "distance_to_flip_pct": -0.44,
                "pin_strike": 64536.21,
                "distance_to_pin_pct": 0.85,
                "source_ref": "DERIBIT_OPTIONS",
            },
        },
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
        assert_true(ggr["auxiliary_lean"] == "SUPPORTIVE",
                    "positive gamma pinning should surface supportive structure tendency")
        assert_true(ggr["raw_values"]["confidence_multiplier"] == 0.98,
                    "GGR confidence multiplier should be carried into the ledger")
        assert_true(ggr["raw_values"]["distance_to_flip_pct"] == -0.44,
                    "GGR distance-to-flip context should be carried into the ledger")

    print("materializer_tail_window: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("materializer_tail_window: FAIL - " + str(exc))
        sys.exit(1)
