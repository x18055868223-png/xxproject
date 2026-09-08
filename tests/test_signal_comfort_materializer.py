import importlib.util
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
MATERIALIZER_TOOL = ROOT / "tools" / "materialize_signal_cards.py"
REVIEW_TOOL = ROOT / "tools" / "signal_llm_review.py"
AS_OF_MS = 1788753590501


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def clone(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def core_tool():
    tools_dir = str(ROOT / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    return load_module(REVIEW_TOOL, "signal_comfort_core_for_tests")


def signal_rating(as_of_ms=AS_OF_MS):
    return {
        "schema": "signal_rating@1.0.0",
        "rating_scope": "side_environment_v1",
        "candidate_quote_economics": "not_evaluated",
        "as_of_ms": as_of_ms,
        "claims": {
            "structure": {
                "status": "SUPPORTED",
                "summary_cn": "结构来源可用，但不产生不可突破边界。",
                "required_inputs": ["factor_cross_section.anchor"],
                "support": [{
                    "source_ref": "factor_cross_section.anchor",
                    "source_group": "OPTIONS",
                    "basis_cn": "价格锚事实可用。",
                }],
                "opposition": [],
                "unknowns": [],
            },
            "put_pressure": {
                "status": "SUPPORTED",
                "summary_cn": "Put 侧下方侵入压力未增强，符合信号层复核条件。",
                "required_inputs": ["factor_cross_section.tmvf"],
                "support": [{
                    "source_ref": "factor_cross_section.tmvf",
                    "source_group": "PRICE",
                    "basis_cn": "量价主干没有给出下方侵入压力。",
                }],
                "opposition": [],
                "unknowns": [],
            },
            "call_pressure": {
                "status": "OPPOSED",
                "summary_cn": "Call 侧不适合作为当前信号层准入主侧。",
                "required_inputs": ["factor_cross_section.tmvf"],
                "support": [],
                "opposition": [{
                    "source_ref": "factor_cross_section.tmvf",
                    "source_group": "PRICE",
                    "basis_cn": "主干方向偏向上方，反对卖出 Call 信用价差。",
                }],
                "unknowns": [],
            },
        },
    }


def card(card_id, ts_ms=AS_OF_MS, include_rating=True):
    item = {
        "schema": {"name": "signal_review_card", "version": "1.0.0"},
        "identity": {
            "card_id": card_id,
            "short_id": card_id[-4:],
            "symbol": "BTC",
            "confirmed_time_ms": ts_ms,
            "confirmed_at": "2026-09-07T11:59:50+08:00",
            "event_type": "NR_REPAIR_CONFIRMED",
            "tags": ["NEUTRAL_REPAIR_CONFIRMED"],
            "strategy_version": "1.6.0",
        },
        "decision": {
            "lean": "BULLISH",
            "support_label": "MODEL_SUPPORT",
            "confidence": 62,
            "trade_allowed": False,
        },
        "decision_matrix": {
            "direction": "BULLISH",
            "decision_state": "MODEL_SUPPORT",
            "execution_allowed": False,
        },
        "quality": {"overall": "OK"},
        "factor_cross_section": {
            "anchor": {"score": 72, "normalized_deviation": -0.12},
            "tmvf": {"direction": "Bullish", "tmv_blend": 0.42},
            "micro_flow": {"combined": {"direction": "bullish"}},
            "macro_pressure": {"macro_shock": {"block": False, "state": "CLEAR"}},
            "funding": {"last_rate": 0.00001},
        },
        "producer_integrity": {
            "record_hash": "sha256:" + card_id.lower().ljust(64, "0")[:64],
        },
    }
    if include_rating:
        item["signal_rating"] = signal_rating(ts_ms)
    return item


def comfort_side(grade, basis_cn, cap_reasons_cn=None, model_grade=None,
                 s_upgrade_basis_cn="", s_upgrade_evidence_refs=None,
                 unresolved_conditions_cn=None, evidence_refs=None,
                 counter_evidence_refs=None):
    if evidence_refs is None:
        evidence_refs = ["EV_TMVF"] if grade else []
    if counter_evidence_refs is None:
        counter_evidence_refs = []
    return {
        "model_grade": grade if model_grade is None else model_grade,
        "final_grade": grade,
        "status": "RATED" if grade else "UNRATED",
        "basis_cn": basis_cn,
        "counter_evidence_cn": "主要反证已列出，但不足以覆盖当前侧别判断。",
        "next_observation_cn": "继续观察关键反证是否解除。",
        "unresolved_conditions_cn": unresolved_conditions_cn or [],
        "evidence_refs": evidence_refs,
        "counter_evidence_refs": counter_evidence_refs,
        "s_upgrade_basis_cn": s_upgrade_basis_cn,
        "s_upgrade_evidence_refs": s_upgrade_evidence_refs or [],
        "cap_reasons_cn": cap_reasons_cn or [],
    }


def comfort_ratings(put_grade="A", call_grade="C", as_of_ms=AS_OF_MS,
                    put_cap_reasons_cn=None, put_model_grade=None,
                    put_unresolved_conditions_cn=None):
    return {
        "schema": "signal_comfort_ratings@1.0.0",
        "rating_scope": "signal_side_admission",
        "candidate_quote_economics": "not_evaluated",
        "as_of_ms": as_of_ms,
        "put_credit": comfort_side(
            put_grade,
            "Put 侧达到信号层准入；候选报价仍需另行确认。",
            cap_reasons_cn=put_cap_reasons_cn,
            model_grade=put_model_grade,
            unresolved_conditions_cn=put_unresolved_conditions_cn,
        ),
        "call_credit": comfort_side(
            call_grade,
            "Call 侧可判断，但当前不是主动推进主侧。",
            cap_reasons_cn=["窗口未开，最终不超过 B。"] if call_grade == "B" else [],
        ),
        "headline": {
            "final_grade": put_grade,
            "focus_side": "put_credit",
            "action_cn": comfort_action_cn(put_grade),
        },
    }


def comfort_action_cn(grade):
    if grade == "S":
        return "S：优先准入，但候选补偿仍需复核。"
    if grade == "A":
        return "A：信号层准入，启动人工交易准备。"
    if grade == "B":
        return "B：启动关注，等待关键条件确认。"
    if grade == "C":
        return "C：普通观察，暂不主动推进。"
    if grade == "D":
        return "D：本轮回避该侧结构。"
    return "暂未完成有效评级。"


def llm_review(comfort=None, status="OK"):
    if comfort is None:
        comfort = comfort_ratings()
    return {
        "schema": "signal_llm_review@1.6.0",
        "status": status,
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "prompt_version": "signal_llm_review_prompt@1.6.0",
        "blind_review_mode": "two_call_strict",
        "llm_call_count": 2,
        "integrated_trade_advisory": {
            "recommendation": "SELL_PUT_SPREAD_REVIEW",
            "final_conclusion_cn": "当前只作为审计辅助，不授权交易。",
            "cross_loop_rationale_cn": "结构与压力需要合并复核。",
            "containment_assessment": {
                "state": "ESTABLISHED",
                "basis_cn": "接管结构已满足信号层复核条件。",
            },
            "premium_selling_fit": {
                "state": "CONDITIONAL",
                "basis_cn": "缺少候选报价，不评价具体成交。",
            },
            "side_basis_cn": "侧别只用于信号层准备。",
            "dominant_conflict_cn": "仍有局部分歧。",
            "key_premises": [{
                "premise_cn": "底层事实可追溯。",
                "evidence_refs": ["EV_TMVF"],
            }],
            "invalid_if": ["关键反证增强。"],
            "next_observation_cn": "继续观察窗口和压力变化。",
            "session_advisory": {
                "liquidity_assessment": "CAUTION",
                "warning_level": "INFO",
                "basis_cn": "时段只作背景。",
                "does_not_change_recommendation": True,
            },
            "source_alignment": "PARTIALLY_ALIGNED",
            "side_comfort_ratings": comfort,
            "audit_only": True,
            "trade_authorization": False,
            "future_24h_bayesian_report": {},
            "policy_validation": {
                "passed": True,
                "authorization_is_not_structure_gate": True,
            },
        },
    }


def materialize_cards(records, sidecars=None):
    tool = load_module(MATERIALIZER_TOOL, "signal_comfort_materializer")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        source = root / "signal_review.jsonl"
        output = root / "public"
        source.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
            encoding="utf-8",
        )
        sidecar_path = None
        if sidecars is not None:
            sidecar_path = root / "signal_llm_reviews.jsonl"
            sidecar_path.write_text(
                "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in sidecars),
                encoding="utf-8",
            )
        tool.materialize(source, output, max_cards=20, llm_reviews=sidecar_path)
        cards_dir = output / "signal_cards"
        manifest = json.loads((cards_dir / "index.json").read_text(encoding="utf-8"))
        cards = {
            item["card_id"]: json.loads(
                (output / item["path"]).read_text(encoding="utf-8"))
            for item in manifest["cards"]
        }
        return manifest, cards


def summary_by_id(manifest):
    return {item["card_id"]: item.get("summary") or {} for item in manifest["cards"]}


def wait_card(card_id):
    item = card(card_id)
    item["decision"]["lean"] = "NEUTRAL"
    item["decision"]["support_label"] = "WAIT_CONFIRMATION"
    item["decision"]["confidence"] = 38
    item["decision_matrix"]["direction"] = "NEUTRAL"
    item["decision_matrix"]["decision_state"] = "WAIT_CONFIRMATION"
    return item


def expired_card(card_id):
    item = card(card_id)
    item["signal_window"] = {
        "state": "EXPIRED",
        "is_active": False,
    }
    return item


def tied_comfort(grade, refs=None, counter_refs=None):
    comfort = comfort_ratings(put_grade=grade, call_grade=grade)
    for side_key in ("put_credit", "call_credit"):
        comfort[side_key] = comfort_side(
            grade,
            grade + " 级同侧测试说明。",
            evidence_refs=refs,
            counter_evidence_refs=counter_refs,
        )
    comfort["headline"] = {
        "final_grade": grade,
        "focus_side": "tie",
        "action_cn": comfort_action_cn(grade),
    }
    return comfort


def raw_side(grade, evidence_refs=None, counter_evidence_refs=None,
             s_upgrade_basis_cn="", s_upgrade_evidence_refs=None):
    return {
        "grade": grade,
        "basis_cn": grade + " 级模型侧别说明。",
        "counter_evidence_cn": "反证已经列入，但未覆盖当前模型等级。",
        "unresolved_conditions_cn": [],
        "next_observation_cn": "继续观察关键条件变化。",
        "evidence_refs": ["EV_TMVF"] if evidence_refs is None else evidence_refs,
        "counter_evidence_refs": (
            [] if counter_evidence_refs is None else counter_evidence_refs),
        "s_upgrade_basis_cn": s_upgrade_basis_cn,
        "s_upgrade_evidence_refs": s_upgrade_evidence_refs or [],
    }


def core_comfort(record, raw_ratings, recommendation):
    core = core_tool()
    packet = core.build_review_packet(clone(record))
    return core._finalize_side_comfort_ratings(
        raw_ratings, packet, {"recommendation": recommendation})


def summary_has_no_comfort(manifest, card_id):
    return "signal_comfort_summary" not in summary_by_id(manifest)[card_id]


def test_materializer_passes_comfort_object_and_compact_summary():
    source_card = card("COMFORT-A")
    review = llm_review()
    manifest, cards = materialize_cards(
        [source_card],
        [{"card_id": "COMFORT-A", "llm_review": review}],
    )
    summaries = summary_by_id(manifest)
    single_card_review = cards["COMFORT-A"]["llm_review"]

    assert_true(single_card_review == review,
                "single-card JSON should preserve normalized comfort review exactly")
    summary = summaries["COMFORT-A"]["signal_comfort_summary"]
    assert_true(set(summary) == {
        "schema", "rating_scope", "candidate_quote_economics", "as_of_ms",
        "headline", "put_credit", "call_credit",
    }, "comfort summary should expose only the frozen compact container")
    assert_true(summary["headline"] == {
        "final_grade": "A",
        "focus_side": "put_credit",
        "action_cn": "A：信号层准入，启动人工交易准备。",
    }, "manifest headline should use the final local grade")
    assert_true(summary["put_credit"] == {
        "status": "RATED",
        "final_grade": "A",
        "basis_cn": "Put 侧达到信号层准入；候选报价仍需另行确认。",
        "cap_reasons_cn": [],
    }, "put side summary should not expose model grade or evidence ids")
    assert_true(summary["call_credit"]["final_grade"] == "C"
                and summary["call_credit"]["cap_reasons_cn"] == [],
                "default A fixture should keep the non-focus side at ordinary observation")
    assert_true("model_grade" not in json.dumps(summary, ensure_ascii=False),
                "manifest summary must not expose model_grade")
    assert_true(cards["COMFORT-A"]["signal_comfort_summary"] == summary,
                "single-card derived summary should match the manifest summary")


def test_wait_cap_b_summary_is_separate_from_clean_admission():
    source_card = wait_card("WAIT-CAPPED")
    comfort = comfort_ratings(
        put_grade="B",
        put_model_grade="A",
        put_cap_reasons_cn=["producer 仍要求确认，最高保留 B 级关注。"],
    )
    review = llm_review(comfort)
    review["integrated_trade_advisory"]["recommendation"] = "WAIT_FOR_CONFIRMATION"
    review["integrated_trade_advisory"]["containment_assessment"] = {
        "state": "INCOMPLETE",
        "basis_cn": "接管仍需等待确认。",
    }
    manifest, cards = materialize_cards(
        [source_card],
        [{"card_id": "WAIT-CAPPED", "llm_review": review}],
    )
    summary = summary_by_id(manifest)["WAIT-CAPPED"]["signal_comfort_summary"]

    assert_true(cards["WAIT-CAPPED"]["llm_review"] == review,
                "capped full comfort object should remain in the single-card detail")
    assert_true(summary["headline"]["final_grade"] == "B",
                "WAIT cap case should expose only the final B grade in the manifest")
    assert_true(summary["put_credit"] == {
        "status": "RATED",
        "final_grade": "B",
        "basis_cn": "Put 侧达到信号层准入；候选报价仍需另行确认。",
        "cap_reasons_cn": ["producer 仍要求确认，最高保留 B 级关注。"],
    }, "WAIT cap summary should carry final grade and cap reason only")
    assert_true("model_grade" not in json.dumps(summary, ensure_ascii=False),
                "WAIT cap summary must not leak the uncapped model grade")


def test_materializer_allows_counter_only_d_summary():
    source_card = card("COUNTER-D")
    comfort = comfort_ratings(put_grade="D", call_grade="D")
    for side_key in ("put_credit", "call_credit"):
        comfort[side_key] = comfort_side(
            "D",
            "该侧本轮回避；反证已经足以说明机制不舒适。",
            evidence_refs=[],
            counter_evidence_refs=["EV_DECISION_MATRIX"],
        )
    comfort["headline"] = {
        "final_grade": "D",
        "focus_side": "tie",
        "action_cn": "D：本轮回避，保留为普通审计观察。",
    }
    review = llm_review(comfort)
    review["integrated_trade_advisory"]["recommendation"] = "NO_TRADE"
    manifest, cards = materialize_cards(
        [source_card],
        [{"card_id": "COUNTER-D", "llm_review": review}],
    )
    summary = summary_by_id(manifest)["COUNTER-D"]["signal_comfort_summary"]

    assert_true(cards["COUNTER-D"]["llm_review"] == review,
                "counter-only D detail should preserve the full review object")
    assert_true(summary["headline"]["final_grade"] == "D"
                and summary["headline"]["focus_side"] == "tie",
                "counter-only D should remain a valid compact summary")
    assert_true(summary["put_credit"]["final_grade"] == "D"
                and summary["call_credit"]["final_grade"] == "D",
                "both D sides should survive without support evidence_refs")


def test_materializer_accepts_core_generated_s_b_tie_and_d():
    s_record = card("CORE-S")
    b_record = card("CORE-B-TIE")
    d_record = card("CORE-D-TIE")
    s_comfort = core_comfort(s_record, {
        "put_credit": raw_side(
            "S",
            evidence_refs=["EV_TMVF"],
            s_upgrade_basis_cn="宏观来源提供了额外、非重复的优先处理依据。",
            s_upgrade_evidence_refs=["EV_MACRO"],
        ),
        "call_credit": raw_side("C", evidence_refs=["EV_TMVF"]),
    }, "SELL_PUT_SPREAD_REVIEW")
    b_comfort = core_comfort(b_record, {
        "put_credit": raw_side("B"),
        "call_credit": raw_side("B"),
    }, "WAIT_FOR_CONFIRMATION")
    d_comfort = core_comfort(d_record, {
        "put_credit": raw_side(
            "D", evidence_refs=[], counter_evidence_refs=["EV_DECISION_MATRIX"]),
        "call_credit": raw_side(
            "D", evidence_refs=[], counter_evidence_refs=["EV_DECISION_MATRIX"]),
    }, "NO_TRADE")
    s_review = llm_review(s_comfort)
    b_review = llm_review(b_comfort)
    b_review["integrated_trade_advisory"]["recommendation"] = (
        "WAIT_FOR_CONFIRMATION")
    d_review = llm_review(d_comfort)
    d_review["integrated_trade_advisory"]["recommendation"] = "NO_TRADE"

    manifest, cards = materialize_cards(
        [s_record, b_record, d_record],
        [
            {"card_id": "CORE-S", "llm_review": s_review},
            {"card_id": "CORE-B-TIE", "llm_review": b_review},
            {"card_id": "CORE-D-TIE", "llm_review": d_review},
        ],
    )
    summaries = summary_by_id(manifest)

    expected = {
        "CORE-S": ("S", "put_credit"),
        "CORE-B-TIE": ("B", "tie"),
        "CORE-D-TIE": ("D", "tie"),
    }
    for card_id, (grade, focus_side) in expected.items():
        summary = summaries[card_id]["signal_comfort_summary"]
        assert_true(summary["headline"]["final_grade"] == grade,
                    card_id + " core-generated final grade should publish")
        assert_true(summary["headline"]["focus_side"] == focus_side,
                    card_id + " core-generated focus side should publish")
        assert_true(cards[card_id]["signal_comfort_summary"] == summary,
                    card_id + " full card summary should match manifest summary")


def test_expired_window_accepts_only_d_or_unrated_summary():
    expired_b = expired_card("EXPIRED-B")
    expired_c = expired_card("EXPIRED-C")
    expired_d = expired_card("EXPIRED-D")
    b_review = llm_review(tied_comfort("B"))
    c_review = llm_review(tied_comfort("C"))
    d_review = llm_review(tied_comfort(
        "D", refs=[], counter_refs=["EV_DECISION_MATRIX"]))
    d_review["integrated_trade_advisory"]["recommendation"] = "NO_TRADE"

    manifest, cards = materialize_cards(
        [expired_b, expired_c, expired_d],
        [
            {"card_id": "EXPIRED-B", "llm_review": b_review},
            {"card_id": "EXPIRED-C", "llm_review": c_review},
            {"card_id": "EXPIRED-D", "llm_review": d_review},
        ],
    )
    summaries = summary_by_id(manifest)

    assert_true("signal_comfort_summary" not in summaries["EXPIRED-B"],
                "expired source window must reject final B comfort summary")
    assert_true("signal_comfort_summary" not in summaries["EXPIRED-C"],
                "expired source window must reject final C comfort summary")
    assert_true("signal_comfort_summary" in summaries["EXPIRED-D"],
                "expired source window may retain an explicit D comfort summary")
    assert_true(summaries["EXPIRED-D"]["signal_comfort_summary"]["headline"] == {
        "final_grade": "D",
        "focus_side": "tie",
        "action_cn": "D：本轮回避该侧结构。",
    }, "expired D headline should stay explicit and compact")
    for card_id, review in (
            ("EXPIRED-B", b_review),
            ("EXPIRED-C", c_review),
            ("EXPIRED-D", d_review)):
        assert_true(cards[card_id]["llm_review"] == review,
                    card_id + " detail should preserve the full review object")
    assert_true(cards["EXPIRED-B"]["signal_comfort_summary"] is None,
                "expired B full card should carry a null derived summary")
    assert_true(cards["EXPIRED-C"]["signal_comfort_summary"] is None,
                "expired C full card should carry a null derived summary")


def test_materializer_rejects_comfort_summary_when_final_grade_is_upgraded():
    source_card = card("UPGRADED-A")
    comfort = comfort_ratings()
    comfort["put_credit"]["model_grade"] = "B"
    review = llm_review(comfort)
    manifest, cards = materialize_cards(
        [source_card],
        [{"card_id": "UPGRADED-A", "llm_review": review}],
    )

    assert_true(summary_has_no_comfort(manifest, "UPGRADED-A"),
                "final A over model B must not publish a comfort summary")
    assert_true(cards["UPGRADED-A"]["llm_review"] == review,
                "upgraded invalid comfort object should remain in detail JSON")


def test_materializer_rejects_admission_summary_against_source_wait_or_window():
    wait_source = wait_card("WAIT-WITH-A")
    wait_review = llm_review()
    wait_review["integrated_trade_advisory"]["recommendation"] = (
        "WAIT_FOR_CONFIRMATION")
    inactive_window_source = card("WINDOW-WITH-A")
    inactive_window_source["signal_window"] = {
        "state": "EXPIRED",
        "is_active": False,
    }
    manifest, cards = materialize_cards(
        [wait_source, inactive_window_source],
        [
            {"card_id": "WAIT-WITH-A", "llm_review": wait_review},
            {"card_id": "WINDOW-WITH-A", "llm_review": llm_review()},
        ],
    )

    assert_true(summary_has_no_comfort(manifest, "WAIT-WITH-A"),
                "source WAIT with final A must not publish a comfort summary")
    assert_true(summary_has_no_comfort(manifest, "WINDOW-WITH-A"),
                "inactive source window with final A must not publish a summary")
    assert_true(cards["WAIT-WITH-A"]["llm_review"] == wait_review,
                "source WAIT invalid A detail should remain available")


def test_materializer_rejects_admission_summary_against_advisory_side():
    source_card = card("ADVISORY-MISMATCH")
    review = llm_review()
    review["integrated_trade_advisory"]["recommendation"] = (
        "SELL_CALL_SPREAD_REVIEW")
    manifest, cards = materialize_cards(
        [source_card],
        [{"card_id": "ADVISORY-MISMATCH", "llm_review": review}],
    )

    assert_true(summary_has_no_comfort(manifest, "ADVISORY-MISMATCH"),
                "Put A with call-side advisory must not publish a comfort summary")
    assert_true(cards["ADVISORY-MISMATCH"]["llm_review"] == review,
                "advisory mismatch detail should remain available")


def test_materializer_rejects_forged_refs_and_missing_required_sources():
    forged = card("FORGED-REFS")
    missing_source = card("MISSING-REQUIRED-SOURCE")
    missing_source["factor_cross_section"].pop("tmvf")

    forged_comfort = comfort_ratings()
    forged_comfort["put_credit"]["evidence_refs"] = ["EV_FAKE"]
    forged_review = llm_review(forged_comfort)
    missing_comfort = comfort_ratings()
    missing_comfort["put_credit"]["evidence_refs"] = ["EV_MACRO"]
    missing_comfort["call_credit"]["evidence_refs"] = ["EV_MACRO"]
    missing_review = llm_review(missing_comfort)

    manifest, cards = materialize_cards(
        [forged, missing_source],
        [
            {"card_id": "FORGED-REFS", "llm_review": forged_review},
            {
                "card_id": "MISSING-REQUIRED-SOURCE",
                "llm_review": missing_review,
            },
        ],
    )

    assert_true(summary_has_no_comfort(manifest, "FORGED-REFS"),
                "forged evidence refs must not publish a comfort summary")
    assert_true(summary_has_no_comfort(manifest, "MISSING-REQUIRED-SOURCE"),
                "missing native required source must not publish a summary")
    assert_true(cards["FORGED-REFS"]["signal_comfort_summary"] is None,
                "forged ref detail should carry a null derived summary")
    assert_true(cards["MISSING-REQUIRED-SOURCE"]["llm_review"] == missing_review,
                "missing-source review object should remain available")


def test_materializer_keeps_old_and_error_cards_readable_without_positive_summary():
    ok = card("COMFORT-OK")
    old = card("OLD-NO-COMFORT")
    error = card("ERROR-COMFORT")
    inline_error = llm_review(status="ERROR")
    error["llm_review"] = inline_error
    manifest, cards = materialize_cards(
        [ok, old, error],
        [{"card_id": "COMFORT-OK", "llm_review": llm_review()}],
    )
    summaries = summary_by_id(manifest)

    assert_true("signal_comfort_summary" in summaries["COMFORT-OK"],
                "valid OK comfort should publish a compact summary")
    assert_true("signal_comfort_summary" not in summaries["OLD-NO-COMFORT"],
                "old sidecars without comfort should remain ungraded in the manifest")
    assert_true("signal_comfort_summary" not in summaries["ERROR-COMFORT"],
                "ERROR reviews must not publish A/S comfort summaries")
    assert_true(cards["ERROR-COMFORT"]["llm_review"] == inline_error,
                "single-card JSON should preserve failed reviews for error isolation")
    assert_true(cards["OLD-NO-COMFORT"]["signal_comfort_summary"] is None,
                "old cards without comfort should carry null derived summary")
    assert_true(cards["ERROR-COMFORT"]["signal_comfort_summary"] is None,
                "ERROR reviews should carry null derived summary")


def test_materializer_rejects_mismatched_or_malformed_comfort_summaries():
    good = card("GOOD-COMFORT")
    mismatched = card("ASOF-MISMATCH")
    bad_headline = card("BAD-HEADLINE")
    bad_s = card("BAD-S")
    malformed = card("MALFORMED")

    mismatched_review = llm_review(comfort_ratings(as_of_ms=AS_OF_MS + 1))
    headline_review = llm_review()
    headline_review["integrated_trade_advisory"]["side_comfort_ratings"][
        "headline"]["focus_side"] = "call_credit"
    s_review = llm_review()
    s_comfort = s_review["integrated_trade_advisory"]["side_comfort_ratings"]
    s_comfort["put_credit"] = comfort_side("S", "Put 侧满足优先准入。")
    s_comfort["headline"] = {
        "final_grade": "S",
        "focus_side": "put_credit",
        "action_cn": "S：优先准入，但候选补偿仍需复核。",
    }
    malformed_review = llm_review()
    malformed_review["integrated_trade_advisory"]["side_comfort_ratings"][
        "put_credit"].pop("basis_cn")

    manifest, _cards = materialize_cards(
        [good, mismatched, bad_headline, bad_s, malformed],
        [
            {"card_id": "GOOD-COMFORT", "llm_review": llm_review()},
            {"card_id": "ASOF-MISMATCH", "llm_review": mismatched_review},
            {"card_id": "BAD-HEADLINE", "llm_review": headline_review},
            {"card_id": "BAD-S", "llm_review": s_review},
            {"card_id": "MALFORMED", "llm_review": malformed_review},
        ],
    )
    summaries = summary_by_id(manifest)

    assert_true("signal_comfort_summary" in summaries["GOOD-COMFORT"],
                "control card should publish comfort summary")
    for card_id in ("ASOF-MISMATCH", "BAD-HEADLINE", "BAD-S", "MALFORMED"):
        assert_true("signal_comfort_summary" not in summaries[card_id],
                    card_id + " must not publish a positive comfort summary")


def test_signal_comfort_does_not_affect_transition_materiality():
    tool = load_module(MATERIALIZER_TOOL, "signal_comfort_transition")
    before = card("TRANSITION-A", 1788750000000)
    after = card("TRANSITION-B", 1788750300000)
    after_with_review = clone(after)
    after_with_review["llm_review"] = llm_review()

    base_transition = tool._transition_record(before, after, [], None)
    comfort_transition = tool._transition_record(before, after_with_review, [], None)
    for key in ("materiality_score", "llm_review_required", "cross_domain_flags"):
        assert_true(comfort_transition.get(key) == base_transition.get(key),
                    "comfort review changed transition " + key)
    assert_true("side_comfort" not in json.dumps(
        comfort_transition.get("top_material_changes"), ensure_ascii=False),
        "comfort ratings must not enter transition materiality changes")


def main():
    test_materializer_passes_comfort_object_and_compact_summary()
    test_wait_cap_b_summary_is_separate_from_clean_admission()
    test_materializer_allows_counter_only_d_summary()
    test_materializer_accepts_core_generated_s_b_tie_and_d()
    test_expired_window_accepts_only_d_or_unrated_summary()
    test_materializer_rejects_comfort_summary_when_final_grade_is_upgraded()
    test_materializer_rejects_admission_summary_against_source_wait_or_window()
    test_materializer_rejects_admission_summary_against_advisory_side()
    test_materializer_rejects_forged_refs_and_missing_required_sources()
    test_materializer_keeps_old_and_error_cards_readable_without_positive_summary()
    test_materializer_rejects_mismatched_or_malformed_comfort_summaries()
    test_signal_comfort_does_not_affect_transition_materiality()
    print("signal_comfort_materializer: PASS")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("signal_comfort_materializer: FAIL - " + str(exc))
        raise
