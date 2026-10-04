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


def function_body(app: str, name: str) -> str:
    marker = f"function {name}("
    start = app.index(marker)
    next_function = app.find("\n  function ", start + len(marker))
    if next_function == -1:
        return app[start:]
    return app[start:next_function]


def test_map_version_and_dom_order():
    index = read_text("index.html")
    version = json.loads(read_text("VERSION.json"))
    assert_true(version["frontend_cache_token"] == "20260928-round5-repairs-v249-local",
                "the current local revision should own a fresh cache token")
    assert_true(f'app.js?v={version["frontend_cache_token"]}"' in index,
                "index should use the declared local cache token")
    assert_true("btc_map@1" in version["btc_map_contract"],
                "VERSION should document the frontend btc_map@1 read contract")
    order = [index.index(f'id="{item}"') for item in ("currentConclusion", "candidateArea", "manualReviewState", "btcMap", "marketContext", "spaceGate")]
    assert_true(order == sorted(order), "candidates and two-question review should precede MAP and full source details")
    assert_true("BTC MAP：背景、价格轴与保单责任" in index,
                "MAP section should have a visible human heading")


def test_map_reads_only_workbench_payload_and_switches_locally():
    app = read_text("app.js")
    body = function_body(app, "renderBtcMap")
    assert_true("globalBtcMap()" in app and "candidateBtcMap(candidate)" in app,
                "MAP should read both global and candidate-scoped payloads")
    assert_true("view.btc_map" in app,
                "global MAP should accept the backend view.btc_map shape")
    assert_true("state.selectedMapCandidateKey" in app and "mapCandidateSelect" in app,
                "policy selector should be a local UI state")
    for forbidden in ("fetchJson", "fetch(", "API_REVIEW", "API_REVIEW_STATE", "beginReview", "loadWorkbench("):
        assert_true(forbidden not in body, f"renderBtcMap must not call external or review APIs: {forbidden}")
    assert_true("renderBtcMap();" in body,
                "candidate selector should refresh only the local MAP section")
    assert_true("未选保单" in app and "Put" in app and "Call" in app,
                "selector should support no policy plus Put/Call policies")


def test_map_content_layers_are_complete_and_bounded():
    app = read_text("app.js")
    for text in ("四行背景", "数值价格轴", "区域响应", "保单责任关联",
                 "配置", "库存与兑现", "杠杆与资金成本", "外部资本条件"):
        assert_true(text in app, f"MAP should render core layer: {text}")
    for text in ("本轮四问摘要", "供需与背景推动", "主要矛盾和缺口", "本单所在位置",
                 "优先观察及原因", "尚未生成四问摘要", "尚未生成背景推动摘要",
                 "尚未生成同版保单位置摘要", "尚未生成优先观察重点", "定位该区域",
                 "服务端仅返回依据摘要；详细来源见下方背景明细。"):
        assert_true(text in app, f"MAP should render v2.4.2 decision brief layer: {text}")
    brief_body = function_body(app, "renderBriefRows")
    assert_true(".slice(0, 3)" not in brief_body and "briefQualificationText" in app,
                "four-question rows must not silently drop the fourth push or later gaps")
    for text in ("map-price-svg", "map-fact-summary", "map-region-grid", "__astraMapAxisV241",
                 "buildMapAxisModel", "data-map-region-id", "data-map-axis-mode"):
        assert_true(text in app + read_text("index.html"), f"v2.4.2 MAP UI should include: {text}")
    assert_true(".slice(0, 6)" in app and "默认最多六个市场参照" in app,
                "reference axis should default to at most six market references")
    assert_true("region_liability_rows" in app and ".slice(0, 3)" in app,
                "policy liability table should use the backend rows and cap the display at three")
    assert_true("region_liability_full_rows" in app and "selectedPolicyRegion" in app,
                "selected region display should read the backend full liability pool")
    assert_true("显示价、原价、价系和期限分开" in app + read_text("index.html"),
                "axis should distinguish display price, original price, basis and tenor")
    assert_true("ref.family !== \"CONTRACT_LIABILITY\"" in app and "合同线独立叠层" in app,
                "contract liability lines should not consume the six-reference market quota")
    assert_true("合同支付与静态净结果；不是盘中触区盈亏、平仓或退出账" in app,
                "policy mapping should not relabel inverse scenarios as exit PnL")
    assert_true("terminal_scenario_basis" in app and "名义数值情景，非精确合同映射" in app,
                "nominal scenario identity should be shown as its own policy-table column")
    assert_true("terminal_payout_range" in app and "static_net_range" in app,
                "policy table should bind the real backend range fields")


def test_map_metrics_and_macro_health_labels_are_human_readable():
    app = read_text("app.js")
    version = read_text("VERSION.json")
    for text in ("FRED 广义美元 DTWEXBGS", "DXY 美元指数（未接通保留项）", "macro.usd.broad.close.v1"):
        assert_true(text in app + version, f"macro health should include explicit broad-dollar label: {text}")
    for text in ("近3个交易日净流", "短期持有者资本化价格", "同源价格距短期资本化价格", "14日亏损占比",
                 "币安 BTCUSDT U本位持仓量变化率（OI）", "已结算资金费率合计", "广义美元 DTWEXBGS",
                 "美国十年名义利率", "bp", "百万美元", "倍常态日均", "Funding 不是期权费用"):
        assert_true(text in app, f"MAP metric display should be Chinese and unit-aware: {text}")
    for raw_skip in ("request_audit", "source_ref", "source_record_ids", "payload_hash"):
        assert_true(raw_skip in app, "raw fields may appear only in the skip list")
    assert_true("来源记录" not in app, "main MAP metrics should not render raw source-record labels")
    for raw_identity in ("FIXTURE ·", "REPLAY ·", "CURRENT ·"):
        assert_true(raw_identity not in app, f"main MAP identity should not expose machine prefix: {raw_identity}")
    for text in ("合成演示", "冻结回放", "当前资料", "示例现价", "冻结现价", "当前现价", "current_price_fact"):
        assert_true(text in app, f"MAP current price and identity should include: {text}")


def test_map_frontend_covers_expected_preview_fixture_shape():
    app = read_text("app.js")
    btc_map = {
        "reference_pool": [
            {
                "family": "TRADED_ACCEPTANCE",
                "role": "OBSERVED_ZONE",
                "price_basis": "DERIBIT_BTC_USD_INDEX",
                "expiry_scope": "GLOBAL_ONLY",
                "limitations": ["synthetic_fixture_not_current_source"],
            },
            {
                "family": "OPTION_STRUCTURE",
                "role": "EFFECTIVE_FLIP",
                "price_basis": "SYNTHETIC_FIXTURE",
                "expiry_scope": 1790323200000,
                "limitations": ["nominal_only_not_contract_comparable"],
            },
            {
                "family": "ONCHAIN_COST",
                "role": "CP_STH",
                "price_basis": "ONCHAIN_COST_CP_STH",
                "expiry_scope": "GLOBAL_ONLY",
                "limitations": ["nominal_scenario_not_precise_contract_mapping"],
            },
            {"family": "CONTRACT_LIABILITY", "role": "SHORT_LEG", "expiry_scope": 1790323200000},
            {"family": "CONTRACT_LIABILITY", "role": "PROTECTIVE_LEG", "expiry_scope": 1790323200000},
            {"family": "CONTRACT_LIABILITY", "role": "NEAR_BREAKEVEN", "expiry_scope": 1790323200000},
        ],
        "response_events": [{"event_type": "RESPONSE_EVENT", "coverage": "PRICE_SAMPLE"}],
        "background": {
            "allocation": {"fact_state": "usable"},
            "inventory": {"fact_state": "partial"},
        },
    }
    references = btc_map["reference_pool"]
    events = btc_map["response_events"]
    policy = {
        "region_liability_full_rows": [
            {"limitations": ["early_exit_cashflow", "slippage", "margin_liquidation", "hedge_cashflow"]}
        ]
    }

    families = {item["family"] for item in references}
    roles = {item["role"] for item in references}
    fact_states = {item["fact_state"] for key, item in btc_map["background"].items() if isinstance(item, dict) and "fact_state" in item}
    price_bases = {item["price_basis"] for item in references if item.get("price_basis")}
    expiry_scopes = {item["expiry_scope"] for item in references if item.get("expiry_scope")}
    event_types = {item["event_type"] for item in events}
    limitations = set()
    for item in references + policy["region_liability_full_rows"]:
        limitations.update(item.get("limitations") or [])

    for token in sorted(families | roles | fact_states | event_types | limitations):
        assert_true(token in app, f"frontend should translate preview MAP token: {token}")
    assert_true("priceBasisText" in app and "Deribit BTC USD 指数" in app,
                "preview DERIBIT_BTC_USD_INDEX should render as a human price basis")
    assert_true("expiryScopeText" in app and "全市场期限未绑定" in app and "绑定到期" in app,
                "preview GLOBAL_ONLY and epoch-ms expiry scopes should render as human terms")
    for token in price_bases:
        assert_true(token in app, f"price-basis token should be handled explicitly: {token}")
    assert_true(any(isinstance(value, int) for value in expiry_scopes) and "GLOBAL_ONLY" in expiry_scopes,
                "fixture should exercise both epoch-ms and global expiry scopes")
    assert_true("event.price" in app and "event.coverage" in app and "event.method" in app,
                "preview response events use price/coverage/method rather than old price_usd/status fields")
    assert_true("referenceMapById" in app and "referenceDisplayTitle(ref)" in app,
                "events should recover the named region from reference_id")


def test_map_policy_full_pool_and_contract_source_details():
    app = read_text("app.js")
    for text in ("本地模拟资料，非当前真实来源", "名义参考，不是合同可比坐标", "名义数值不作精确合同映射"):
        assert_true(text in app, f"policy limitations should render in Chinese: {text}")
    for text in ("SHORT_LEG", "PROTECTIVE_LEG", "NEAR_BREAKEVEN", "卖出腿", "保护腿", "近端盈亏平衡"):
        assert_true(text in app, f"contract liability overlay should know role: {text}")
    for text in ("region_liability_full_rows", "展开完整责任池", "完整责任池来自同版", "renderPolicyLiabilityTable", "renderPolicyLiabilityRow"):
        assert_true(text in app, f"full policy pool should be openable without frontend recalculation: {text}")
    for text in ("renderReferenceSourceDetails", "来源详情", "来源实体", "观察截止", "首次可得"):
        assert_true(text in app, f"reference axis should expose readable source details: {text}")


def test_map_gap_identity_and_review_delta_are_visible():
    app = read_text("app.js")
    version = read_text("VERSION.json")
    for text in ("缺口", "过期", "仅历史", "仅展示参考", "本地模拟", "不代表交易许可"):
        assert_true(text in app + version, f"MAP should show identity boundary: {text}")
    for text in ("冻结回放不是当前事实", "FMZ 非未来快照缺失", "GEX 有效 Flip 不可得", "KPF 缓存制品不可得"):
        assert_true(text in app, f"MAP gap code should render in Chinese: {text}")
    assert_true("renderMapReviewDelta" in app and "不会因此补发 LLM" in app,
                "MAP should show frozen-review deltas without adding an LLM call")
    assert_true("页面不会用历史卡拼接当前 MAP" in app,
                "MAP should not backfill current state from historical cards")


def test_map_escapes_visible_content_and_hides_raw_source_ids():
    app = read_text("app.js")
    for renderer in ("renderMapBackground", "renderReferenceAxis", "renderMapResponses", "renderPolicyRows", "renderMapReviewDelta"):
        body = function_body(app, renderer)
        assert_true("escapeHtml" in body, f"{renderer} should escape visible text")
    for raw_visible in ("source_record_ids", "payload_hash", "source_manifest_ref", "map_id"):
        assert_true(raw_visible not in read_text("index.html"),
                    f"index visible text should not expose raw MAP metadata: {raw_visible}")
    assert_true("来源记录" not in app,
                "main MAP view should not print raw source record ids")


def test_map_frontend_syntax_when_node_is_available():
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
    test_map_version_and_dom_order()
    test_map_reads_only_workbench_payload_and_switches_locally()
    test_map_content_layers_are_complete_and_bounded()
    test_map_metrics_and_macro_health_labels_are_human_readable()
    test_map_frontend_covers_expected_preview_fixture_shape()
    test_map_policy_full_pool_and_contract_source_details()
    test_map_gap_identity_and_review_delta_are_visible()
    test_map_escapes_visible_content_and_hides_raw_source_ids()
    test_map_frontend_syntax_when_node_is_available()
    print("astra_map_frontend_v24: PASS")


if __name__ == "__main__":
    main()
