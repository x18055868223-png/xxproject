import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]
ARCHIVE = pathlib.Path(
    r"C:\Users\Xu\Documents\信号审计前端页面设计\archives"
    r"\signal-audit-final-20260618"
)
DEPLOY_FRONTEND = ROOT / "deploy" / "signal_audit" / "frontend"


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def read(path):
    return path.read_text(encoding="utf-8")


def json_files(root):
    return sorted(path for path in (root / "signal_cards").glob("*.json")
                  if path.name != "index.json")


def first_fixture_with_active_view(root):
    for path in json_files(root):
        card = json.loads(path.read_text(encoding="utf-8"))
        review = card.get("llm_review")
        if (isinstance(review, dict)
                and review.get("status") == "OK"
                and isinstance(review.get("theoretical_active_view"), dict)):
            return path, card
    return None, None


def main():
    for root in (ARCHIVE, DEPLOY_FRONTEND):
        assert_true(root.exists(), "frontend root missing " + str(root))
        app = read(root / "app.js")
        html = read(root / "index.html")
        assert_true("function renderLlmReview(doc)" in app,
                    "app.js should define renderLlmReview")
        assert_true("function renderSignalSessionContext(doc)" in app,
                    "app.js should define renderSignalSessionContext")
        assert_true("function hasLlmGammaKeyLevel(doc, key)" in app,
                    "app.js should detect individual LLM gamma key levels before rendering repeated key levels")
        for marker in (
            "const showFlipPoint = !hasLlmGammaKeyLevel(doc, \"flip\")",
            "const showCallWall = !hasLlmGammaKeyLevel(doc, \"call_wall\")",
            "const showPutWall = !hasLlmGammaKeyLevel(doc, \"put_wall\")",
            "const showPinStrike = !hasLlmGammaKeyLevel(doc, \"pin\")",
            "const showMagnetLevel = !hasLlmGammaKeyLevel(doc, \"pin\")",
        ):
            assert_true(marker in app,
                        "gamma overview should merge repeated key level by field: " + marker)
        assert_true("关键点位已在 LLM Gamma 体制分析栏合并展示" in app,
                    "app.js should explain when repeated gamma key levels are merged")
        assert_true("llm_review" in app and "summary_cn" in app,
                    "app.js should read llm_review summary fields")
        for text in (
            "状态",
            "谨慎等级",
            "模型服务",
            "与系统结论关系",
            "支持系统结论",
            "理论主动倾向",
            "理论依据",
            "反向证据",
            "全局 Gamma 体制分析",
            "风险叠加，不是方向",
            "主要尾部风险",
            "体制极端度",
            "输入包哈希",
            "信号时区置信度 / 前提耐久度",
            "低转中缓冲带",
            "不改变系统方向、置信、门控或交易许可",
        ):
            assert_true(text in app, "app.js should localize LLM review text " + text)
        assert_true('statusBadge("Status"' not in app and 'statusBadge("Caution"' not in app,
                    "LLM review badges should use Chinese labels")
        rank_idx = app.find("${renderGexRank(doc)}")
        session_idx = app.find("${renderSignalSessionContext(doc)}")
        llm_idx = app.find("${renderLlmReview(doc)}")
        decision_idx = app.find("${renderDecision(doc)}")
        layers_idx = app.find("${renderDisplayLayers(doc)}")
        assert_true(rank_idx != -1 and session_idx != -1 and llm_idx != -1
                    and decision_idx != -1 and layers_idx != -1,
                    "renderDocument should call rank, session context, llm review, decision, and display layers")
        assert_true(rank_idx < session_idx < llm_idx < decision_idx < layers_idx,
                    "session context should render between rank and LLM review")
        assert_true(".llm-review-panel" in html and ".llm-review-summary" in html,
                    "index.html should include prominent LLM review styles")

        path, card = first_fixture_with_active_view(root)
        assert_true(path is not None,
                    "at least one local fixture should include llm_review theoretical active view")
        review = card["llm_review"]
        identity = card.get("identity", {})
        symbol = identity.get("symbol")
        card_id = identity.get("card_id")
        headline = card.get("display_layers", {}).get("headline", "")
        delivery_text = json.dumps(card.get("delivery", {}), ensure_ascii=False)
        assert_true(symbol and symbol in headline,
                    "fixture headline should match identity symbol")
        assert_true(card_id and card_id in delivery_text,
                    "fixture delivery references should point at current card_id")
        for stale_symbol in ("ETH", "SOL"):
            if stale_symbol != symbol:
                assert_true(stale_symbol not in headline,
                            "fixture headline should not contain stale symbol " + stale_symbol)
        for key in (
            "status",
            "summary_cn",
            "agreement_with_system",
            "theoretical_active_view",
            "gamma_regime_lens",
            "main_supporting_factors",
            "main_risks_or_conflicts",
            "operator_focus",
            "invalid_if",
            "caution_level",
            "not_trading_advice",
        ):
            assert_true(key in review, "fixture llm_review missing " + key)
        assert_true(review["not_trading_advice"] is True,
                    "fixture LLM review should keep disclaimer flag")
        active_view = review["theoretical_active_view"]
        assert_true(isinstance(active_view, dict),
                    "fixture LLM review should include theoretical active view object")
        assert_true("bias" in active_view and "basis_cn" in active_view,
                    "fixture theoretical active view should include bias and basis")
        gamma_lens = review["gamma_regime_lens"]
        assert_true(isinstance(gamma_lens, dict),
                    "fixture LLM review should include gamma regime lens object")
        assert_true("regime" in gamma_lens and "lens_is_risk_overlay_not_direction" in gamma_lens,
                    "fixture gamma regime lens should include regime and boundary")
        raw_gex = card.get("factor_cross_section", {}).get("gex_info", {})
        raw_gamma = card.get("factor_cross_section", {}).get("gamma_regime", {})
        assert_true(raw_gex.get("call_wall") is not None and raw_gex.get("put_wall") is not None,
                    "fixture should include raw GEX key levels to exercise dedupe")
        assert_true(raw_gex.get("flip_point") is not None or raw_gamma.get("flip_point") is not None,
                    "fixture should include raw flip point to exercise dedupe")
        assert_true(isinstance(gamma_lens.get("key_levels"), dict)
                    and gamma_lens["key_levels"].get("flip") is not None,
                    "fixture LLM gamma lens should include key levels to exercise dedupe")

        fallback = read(root / "signal_cards" / "fallback.js")
        assert_true("\"llm_review\"" in fallback and "\"summary_cn\"" in fallback,
                    "fallback.js should include LLM review fixture data for file mode")
        assert_true("\"gamma_regime_lens\"" in fallback,
                    "fallback.js should include gamma regime lens fixture data")

    assert_true(read(ARCHIVE / "app.js") == read(DEPLOY_FRONTEND / "app.js"),
                "archive and deploy app.js should stay mirrored")
    assert_true(read(ARCHIVE / "index.html") == read(DEPLOY_FRONTEND / "index.html"),
                "archive and deploy index.html should stay mirrored")
    print("signal_audit_frontend_llm_review_contract: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_audit_frontend_llm_review_contract: FAIL - " + str(exc))
        sys.exit(1)
