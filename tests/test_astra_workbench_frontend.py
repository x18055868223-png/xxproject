import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "deploy" / "signal_audit" / "frontend"


def read_text(name: str) -> str:
    return (FRONTEND / name).read_text(encoding="utf-8")


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def test_cache_token_and_workbench_contract():
    index = read_text("index.html")
    version = json.loads(read_text("VERSION.json"))
    token = version["frontend_cache_token"]
    assert_true(
        f'app.js?v={token}"' in index,
        "index.html should cache-bust app.js with VERSION frontend_cache_token",
    )
    assert_true(
        version["frontend_profile"] == "astra_spacegate_workbench_v2",
        "VERSION should identify the v2 dynamic workbench frontend",
    )
    assert_true(
        version["workbench_api"] == "/api/workbench"
        and version["manual_review_api"] == "/api/workbench/review",
        "VERSION should document the absolute workbench API routes",
    )
    assert_true(
        version["manual_review_state_api"] == "/api/workbench/review-state",
        "VERSION should document the read-only review state route",
    )


def test_current_data_comes_only_from_workbench_api():
    app = read_text("app.js")
    index = read_text("index.html")
    assert_true('const API_WORKBENCH = "/api/workbench";' in app, "frontend should read current state from /api/workbench")
    assert_true('const API_REVIEW = "/api/workbench/review";' in app, "manual review should post to /api/workbench/review")
    assert_true('const API_REVIEW_STATE = "/api/workbench/review-state";' in app,
                "manual review should poll the read-only review-state endpoint")
    assert_true("const POLL_MS = 60_000;" in app, "frontend should poll every 60 seconds")
    assert_true("const REVIEW_STATE_POLL_MS = 3_000;" in app, "review-state polling should stay between 2 and 5 seconds")
    assert_true("window.setInterval(loadWorkbench, POLL_MS)" in app, "frontend should schedule API polling")
    assert_true("当前数据不可得／仅能查看本地历史" in app, "API failure should be rendered as unavailable current data")
    assert_true("signal_cards/index.json" in app, "historical cards may remain available as raw JSON")
    assert_true("历史卡不会被展示成当前实时工作台" in index, "index should state old cards are not current data")
    for old_reader_token in ("loadDocuments", "renderSignalEvidenceReader", "window.signalAuditData"):
        assert_true(old_reader_token not in app, f"old reader token should not remain in app.js: {old_reader_token}")


def test_space_gate_producer_native_and_fail_closed_language():
    app = read_text("app.js")
    index = read_text("index.html")
    assert_true("FMZ 原生 SpaceGate" in app, "SpaceGate provenance should be visible in Chinese")
    assert_true("缺少 FMZ 原生 SpaceGate" in app, "missing producer-native SpaceGate should be explicit")
    assert_true("SPACE_GATE_CLOCK_INVALID_OR_STALE" in app, "SPACE_GATE reason codes should be translated")
    assert_true("producer-native SpaceGate" not in app + index, "visible runtime UI should not leak raw producer-native wording")
    assert_true("空间约束：当前不准入" in app, "missing or invalid SpaceGate should render blocked")
    assert_true("current data time" in read_text("VERSION.json"), "VERSION should record current-data time requirement")
    for token in ("Anchor", "NetGamma", "state start time", "blocking reasons"):
        assert_true(token in read_text("VERSION.json"), f"VERSION should document {token} in the SpaceGate view")
    assert_true("门槛 60" in app and "Anchor 评分低于 60" in app,
                "SpaceGate view should keep the Anchor 60 gate wording in the live renderer")


def test_audit_module_follows_main_workbench():
    index = read_text("index.html")
    version = read_text("VERSION.json")
    layout_css = index.split(".layout {", 1)[1].split("}", 1)[0]
    assert_true("display: block;" in layout_css and "grid-template-columns" not in layout_css,
                "workbench should occupy a single full-width column")
    for obsolete in ("side-stack", 'aria-label="快照与历史侧栏"', "height: 100vh"):
        assert_true(obsolete not in index, f"obsolete sidebar rule should be removed: {obsolete}")
    assert_true(".candidate-header {" in index and ".historical-reading-context {" in index,
                "long policy reading should retain its current or historical identity context")
    assert_true("@media (max-width: 860px)" in index, "narrow-screen layout should have an explicit breakpoint")
    mobile_css = index.split("@media (max-width: 860px)", 1)[1]
    assert_true(".audit-grid," in mobile_css and ".audit-history .history-list" in mobile_css,
                "audit cards and history should have a mobile stacking rule")
    assert_true('href="#candidatesTitle">跳到工作台内容' in index,
                "keyboard skip link should lead to the first workbench section")
    ids = ("candidateArea", "btcMap", "marketContext", "spaceGate", "currentConclusion", "manualReviewState",
           "dataHealth", "dailySnapshots", "transitions", "gapArea", "historyArea")
    positions = [index.index(f'id="{name}"') for name in ids]
    assert_true(positions == sorted(positions), "candidate, research, and global audit areas should keep the new reading order")
    for name in ids + ("historySearch", "reloadHistoryButton"):
        assert_true(index.count(f'id="{name}"') == 1, f"data and interaction ID must remain unique: {name}")
    assert_true('id="auditLibrary"' in index and 'id="auditLibraryTitle"' in index,
                "global audit records should live in the audit library disclosure")
    for title in ("snapshotTitle", "transitionTitle", "gapsTitle", "historyTitle"):
        assert_true(f'<h3 id="{title}">' in index, "audit subsections should use third-level headings")
    assert_true("one research entry and one audit/replay entry" in version,
                "VERSION should document the candidate research and audit entry contract")


def test_market_context_skeleton_is_non_llm_and_gap_explicit():
    app = read_text("app.js")
    index = read_text("index.html")
    version = read_text("VERSION.json")
    for text in ("量价 / 宏观 / 因果证据", "空间层与基础空间约束", "量价倾向", "宏观层", "空间层", "因果链与反证", "GEX 净 Gamma（USD）", "证据质量分"):
        assert_true(text in app + index, f"market skeleton should expose {text}")
    for text in ("源事实", "解释 / 反证", "合同影响", "分数或标签，缺少可验分量",
                 "side_hint=CALL 本身不能单独推出偏多", "价格倾向"):
        assert_true(text in app + index, f"market skeleton should expose structured fact reading: {text}")
    for token in ("market_context", "market_facts", "volume_price", "space_context", "edb_reasoning", "conflicts", "macro_pressure", "neutral_repair", "space_gate"):
        assert_true(token in app + version, f"frontend should bind market_context field {token}")
    assert_true("服务端未提供本层事实，工作台不使用历史卡或前端推断补齐" in app,
                "missing market layers must render as data gaps")
    assert_true("冲突摘要" in app and "renderConflictSummary" in app,
                "market context should display backend conflict summaries")
    assert_true("Put/Call 仍并列，不在页面选边，也不重复计票" in app,
                "market context must not become a duplicated voting layer")
    assert_true("same-source facts are not independent votes" in version and "score-only fields stay explicit gaps" in version,
                "VERSION should freeze no duplicate vote semantics")
    assert_true("gamma_regime.net_gamma_notional\", fmtUsd" not in app,
                "Greeks-times-OI proxy must not be mislabeled as USD net Gamma")


def test_manual_review_button_is_backend_gated_and_token_is_memory_only():
    app = read_text("app.js")
    index = read_text("index.html")
    assert_true("const gate = reviewGate(candidate);" in app, "candidate should read backend review_gate")
    assert_true("const gateEnabled = gate.enabled === true && secureReviewOrigin();" in app,
                "button should require both backend gate and a secure origin")
    assert_true("const enabled = gateEnabled && !reviewBusy;" in app,
                "button should also close while a manual review is inflight")
    assert_true("candidate_id: candidateIdValue" in app, "manual review POST should include candidate_id")
    assert_true("const key = makeIdempotencyKey();" in app and "idempotency_key: key" in app,
                "manual review POST should include a random idempotency key retained for receipt matching")
    assert_true("headers.Authorization = `Bearer ${inMemoryToken}`" in app,
                "manual review POST should use an Authorization Bearer header outside preview")
    assert_true("if (!previewMode) headers.Authorization = `Bearer ${inMemoryToken}`" in app,
                "loopback synthetic preview should be the only tokenless POST path")
    assert_true("let inMemoryToken = \"\";" in app, "service token should be memory-only")
    assert_true("localStorage" not in app, "service token must not be persisted in localStorage")
    assert_true('const DISCLOSURE_STORAGE_KEY = "astra-v261-disclosures";' in app,
                "sessionStorage should be limited to page disclosure state")
    assert_true("DISCLOSURE_STORAGE_FORBIDDEN" in app and "authorization" in app and "token" in app,
                "disclosure persistence must filter auth/token-like keys")
    for line in app.splitlines():
        if "sessionStorage" in line or "setItem(DISCLOSURE_STORAGE_KEY" in line:
            assert_true("inMemoryToken" not in line and "Authorization" not in line,
                        "session storage must not write service token or authorization")
    assert_true("锁定候选并综合评审" in index and "锁定候选并综合评审" in app, "manual button label should be exact")
    assert_true("正在推理" in app and "startReviewPolling()" in app and "loadReviewStateOnce" in app,
                "manual review should show busy state and poll for receipt")
    assert_true("Put/Call 两侧按钮同步禁用" in app,
                "busy manual review should disable both side buttons")
    assert_true("renderReviewResultDetail" in app and "同版当前保单" in app and "此前有效评审" in app,
                "manual review receipt should distinguish current-version and previous frozen policy")
    assert_true('`${label(side)} 保单`' in app,
                "manual review receipt should identify which side it audited")
    assert_true("支持" in app and "反证" in app and "缺口" in app and "改变判断的事实" in app,
                "manual review receipt should display support, counter evidence, gaps and reassessment triggers")
    assert_true('OK: "完成"' in app and 'rejected: "拒绝"' in app,
                "manual LLM outer state should not render rejection as a pass")
    assert_true("该结果只适用于提交时的保单，不代替刷新后的当前结构门或候选判断" in app,
                "a completed review must not imply it still applies to the current candidate")


def test_synthetic_preview_is_clearly_watermarked_and_not_real_llm():
    app = read_text("app.js")
    index = read_text("index.html")
    version = read_text("VERSION.json")
    assert_true("previewBanner" in app and "previewBanner" in index,
                "synthetic preview should have a prominent page banner")
    for text in ("本地模拟场景", "零真实 LLM", "非交易", "非当前事实"):
        assert_true(text in app + version, f"preview should visibly mark {text}")
    for text in ("本地模拟候选", "本地模拟评审演示", "模拟评审回执"):
        assert_true(text in app, f"synthetic candidate cards should use dedicated wording: {text}")
    assert_true("previewReviewAllowed()" in app and "isLoopbackHost()" in app,
                "tokenless preview review should require preview.kind and loopback")
    assert_true("simulated_space_gate" in app and "本地模拟 SpaceGate" in app,
                "simulated SpaceGate should never appear as producer-native current fact")
    assert_true("A simulated_space_gate with preview.kind=synthetic_fixture is always rendered as local synthetic preview" in version,
                "VERSION should freeze simulated gate semantics")


def test_replay_preview_is_not_worded_as_synthetic_or_current_candidate():
    app = read_text("app.js")
    assert_true("function isReplayPreview()" in app, "frontend should detect preview.mode=REPLAY")
    assert_true("冻结回放 · 非当前交易 · 查看不调用 LLM" in app
                and "真实公开来源的历史截面" in app,
                "REPLAY preview banner should not be described as the synthetic fixture service")
    assert_true("本机冻结回放已连接" in app,
                "status line should identify frozen replay separately from live/current and synthetic")
    assert_true("冻结回放候选；真实公开来源的历史截面" in app,
                "candidate header should describe replay candidates as frozen replay, not current server candidates")
    assert_true("本地模拟 · 查看不调用 LLM · 执行关闭" in app,
                "synthetic fixture wording should remain available for FIXTURE mode")


def test_main_visual_hides_raw_identifiers():
    app = read_text("app.js")
    index = read_text("index.html")
    assert_true("当前服务端候选；原始标识只保存在底层证据中。" in app,
                "candidate headers should use semantic text instead of raw ids")
    assert_true("candidate ${escapeHtml" not in app and "snapshot ${escapeHtml" not in app,
                "main candidate header should not interpolate raw candidate or snapshot ids")
    assert_true("schema ${" not in app and "hash} ·" not in app,
                "main visual strings should not show raw schema or hash")
    assert_true("原始标识" in index, "history search should be the explicit place for raw identifiers")


def test_candidate_accounting_uses_v2_server_fields_only():
    app = read_text("app.js")
    version = read_text("VERSION.json")
    for label in ("卖出腿权利金", "保护腿成本", "权利金差", "建仓费用 f_entry", "持有到期总费用 f_total", "净信用"):
        assert_true(label in app, f"candidate card should show accounting row: {label}")
    for field in (
        "premium_spread_btc",
        "net_credit_after_entry_fee_btc",
        "valuation_spot_usd",
        "theoretical_two_leg_fee_btc",
        "short_premium_btc",
        "long_premium_btc",
    ):
        assert_true(field in app, f"frontend should consume server field {field}")
    candidate_body = app.split("function renderCandidate(candidate)", 1)[1].split("function ", 1)[0]
    assert_true("visible_credit_btc" not in candidate_body and "economics.credit_btc" not in candidate_body,
                "net credit should not fall back to the old visible/credit fields")
    assert_true("fmtBtcWithUsd" in app and "按本次估值现价折合" in app,
                "BTC amounts should carry server-provided USD equivalents")
    assert_true("valuationSpotUsd *" not in app and "* valuationSpotUsd" not in app,
                "frontend must not calculate USD equivalents from spot")
    assert_true("Deribit 顺序分腿盘口只是理论估算，未成交" in app,
                "net credit must be described as theoretical and unfilled")
    assert_true("Net credit means short premium minus protection cost minus two-leg theoretical transaction fee" in version,
                "VERSION should freeze the new net-credit definition")


def test_candidate_expiry_breakeven_and_delta_display():
    app = read_text("app.js")
    assert_true("当前剩余到期时间" in app, "live expiry row should show current remaining time")
    assert_true("冻结时点剩余到期时间" in app, "replay expiry row should be explicit about frozen valuation time")
    assert_true("模拟时点剩余到期时间" in app, "fixture expiry row should be explicit about simulation time")
    assert_true("到期盈亏平衡价" in app, "breakeven row should be expiry-specific")
    assert_true("两处均为本组合到期盈亏平衡价" in app,
                "same-combination dual inverse roots should not be hidden")
    assert_true("liability.breakeven_usd.map" not in app,
                "frontend should not blindly list redundant breakeven arrays")
    assert_true("卖出腿 Delta" in app and "目标 |Delta|≈0.35" in app,
                "candidate card should show short-leg delta and target note")
    assert_true("本卡无合格卖出腿 Delta 证据" in app,
                "missing delta should be called out as no qualified delta evidence")


def test_frontend_has_no_trade_action_surface():
    app = read_text("app.js")
    index = read_text("index.html")
    forbidden = (
        "placeOrder",
        "submitOrder",
        "executeTrade",
        "createOrder",
        "trade_allowed = true",
        "execution_allowed = true",
    )
    for token in forbidden:
        assert_true(token not in app + index, f"frontend must not expose trading action token: {token}")
    assert_true("成交控制" not in index, "page should not add a trading-control section")
    assert_true("所示为开仓前资料；不代表成交、持仓或执行许可" in index,
                "workbench should state its pre-entry theoretical scope")


def test_numeric_sources_and_reading_guidance_are_explicit():
    app = read_text("app.js")
    for token in ("component_summaries", "source_symbol", "reference_ts_ms", "current_ts_ms",
                  "scoring_unit", "scoring_bps", "momentum_return_pct", "trend_strength_pct",
                  "coverage_hours", "cvd_unit", "funding_cum", "window_start_time", "window_end_time"):
        assert_true(token in app, f"numeric presentation should use producer field {token}")
    for text in ("相对 bp", "收益率变化", "源原始读数", "不等同于端点涨跌", "不计入期权净信用"):
        assert_true(text in app, f"numeric scope should be explicit: {text}")
    assert_true("candidate.reading_guidance" in app and "item.text_cn" in app,
                "policy suggestions should render server notes")
    assert_true("不生成统计数值、选侧结论或交易许可" in app,
                "reading guidance must remain explanatory")


def test_app_js_syntax_when_node_is_available():
    node = shutil.which("node")
    if not node:
        return
    result = subprocess.run(
        [node, "--check", str(FRONTEND / "app.js")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert_true(result.returncode == 0, result.stdout + result.stderr)


def main():
    test_cache_token_and_workbench_contract()
    test_current_data_comes_only_from_workbench_api()
    test_space_gate_producer_native_and_fail_closed_language()
    test_audit_module_follows_main_workbench()
    test_market_context_skeleton_is_non_llm_and_gap_explicit()
    test_manual_review_button_is_backend_gated_and_token_is_memory_only()
    test_synthetic_preview_is_clearly_watermarked_and_not_real_llm()
    test_replay_preview_is_not_worded_as_synthetic_or_current_candidate()
    test_main_visual_hides_raw_identifiers()
    test_candidate_accounting_uses_v2_server_fields_only()
    test_candidate_expiry_breakeven_and_delta_display()
    test_frontend_has_no_trade_action_surface()
    test_numeric_sources_and_reading_guidance_are_explicit()
    index, app = read_text("index.html"), read_text("app.js")
    assert_true('id="dataHealth"' in index and "function renderDataHealth()" in app,
                "workbench must show data health from backend cache")
    for text in ["data-health-grid", "FMZ 与空间门", "期权合同与保单报价",
                 "部分用途不可用", "汇总需刷新", "尚未接入",
                 "统一时效与状态规则", "来源 / 时钟 / 用途期限",
                 "冻结回放资料", "冻结时点可用", "刷新不会把它变成当前事实"]:
        assert_true(text in index + app, f"compact health view should include: {text}")
    assert_true(index.index('id="manualReviewState"') < index.index('id="dataHealth"') < index.index('id="historyArea"'),
                "maintenance follows the global review state before historical audit")
    test_app_js_syntax_when_node_is_available()
    print("astra_workbench_frontend: PASS")


if __name__ == "__main__":
    main()
