import json
import pathlib
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "deploy" / "signal_audit" / "frontend"


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def render_contract(root):
    script = r"""
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const root = __ROOT__;
const app = fs.readFileSync(path.join(root, "app.js"), "utf8");
const manifest = JSON.parse(fs.readFileSync(path.join(root, "signal_cards/index.json"), "utf8"));

function renderCard(card) {
  const elements = {};
  function element(id) {
    if (!elements[id]) {
      elements[id] = {
        id,
        value: "",
        innerHTML: "",
        textContent: "",
        dataset: {},
        classList: { add() {}, remove() {}, toggle() {} },
        addEventListener() {},
        insertAdjacentHTML(_where, html) { this.innerHTML += html; },
        focus() {}
      };
    }
    return elements[id];
  }
  const document = {
    getElementById(id) {
      if (id === "signal-data") return { textContent: JSON.stringify([card]) };
      return element(id);
    },
    querySelector(selector) {
      return element(selector.startsWith("#") ? selector.slice(1) : selector);
    },
    querySelectorAll() { return []; }
  };
  const context = {
    window: { location: { protocol: "file:" }, SIGNAL_CARD_FIXTURES: [card] },
    document,
    console,
    Intl,
    setTimeout,
    clearTimeout,
    fetch: () => Promise.reject(new Error("unexpected fetch"))
  };
  vm.createContext(context);
  vm.runInContext(app, context);
  return new Promise((resolve) => setTimeout(() => {
    resolve(elements.documentView ? elements.documentView.innerHTML : "");
  }, 20));
}

(async () => {
  const rows = [];
  for (const item of manifest.cards) {
    const cardPath = path.join(root, item.path);
    const card = JSON.parse(fs.readFileSync(cardPath, "utf8"));
    const html = await renderCard(card);
    const text = html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");
    const evidence = Array.isArray(card.reasoning && card.reasoning.evidence)
      ? card.reasoning.evidence
      : [];
    const sourceRefs = evidence.map((entry) => entry.source_ref).filter(Boolean);
    rows.push({
      card_id: card.identity && card.identity.card_id,
      synthetic: !!(card.identity && card.identity.is_synthetic),
      sourceRefs,
      objectObject: text.includes("[object Object]"),
      compactLedger: html.includes("evidence-ledger") && html.includes("evidence-item"),
      oldWideTable: html.includes("evidence-table"),
      fundingRateSide: html.includes("费率端倾向"),
      reflexiveFunding: html.includes("反身性辅助倾向"),
      macroRawScore: html.includes("宏观背景") && html.includes("分数"),
      sourceRefLinks: (html.match(/class="source-ref-link/g) || []).length,
      rawTraceNav: html.includes("raw-trace-nav"),
      rawTargets: (html.match(/id="raw-/g) || []).length,
      flowConfirm: /FLOW_CONFIRM|combined_weight|absorption_state|fast_4h|slow_12h/.test(text),
      llmSection: text.includes("LLM 复核意见"),
      llmPending: text.includes("PENDING_LLM") || text.includes("LLM 复核尚未生成"),
      macroProxyFacts: /VOLQ|DXY|US10Y|纳斯达克|美元|美债/.test(text),
      macroUnknown: text.includes("UNKNOWN") && text.includes("宏观背景"),
      ggrSpatialConstraint: text.includes("空间约束") || text.includes("空间安全")
    });
  }
  process.stdout.write(JSON.stringify(rows));
})();
"""
    script = script.replace("__ROOT__", json.dumps(str(root)))
    result = subprocess.run(
        ["node", "-e", script],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    assert_true(result.returncode == 0, result.stderr or result.stdout)
    return json.loads(result.stdout)


def render_transition_contract(root):
    sample = {
        "schema": {"name": "signal_review_card", "version": "1.0.0"},
        "identity": {
            "card_id": "TRANSITION-CONTRACT-CARD",
            "short_id": "TCC",
            "symbol": "BTC",
            "strategy_version": "1.5.0",
            "confirmed_at": "2026-06-18T11:00:00+08:00",
        },
        "quality": {"overall": "OK"},
        "decision": {
            "lean": "LONG_BIAS",
            "support_label": "NO_TRADE_BLOCKED",
            "confidence": 64,
        },
        "reasoning": {"evidence": []},
        "transition_context": {
            "audit_scope": "AUDIT_ONLY",
            "transition_id": "tr-contract",
            "previous_card_id": "PREV-CARD",
            "current_card_id": "TRANSITION-CONTRACT-CARD",
            "elapsed_ms": 2700000,
            "comparison_quality": "HIGH",
            "materiality_score": 91,
            "llm_review_required": True,
            "cross_domain_flags": [
                "MACRO_SHOCK",
                "FUNDING_CROWDING_ESCALATION",
            ],
            "decision_transition": {
                "lean_before": "NEUTRAL",
                "lean_after": "LONG_BIAS",
                "support_before": "TRADE_SUPPORT_WEAK",
                "support_after": "NO_TRADE_BLOCKED",
                "confidence_before": 58,
                "confidence_after": 64,
            },
            "top_material_changes": [
                {
                    "domain": "FUNDING",
                    "field": "factor_cross_section.funding.last_rate",
                    "previous": 0.000052,
                    "current": 0.000087,
                    "delta_abs": 0.000035,
                    "role_before": "NON_VOTING",
                    "role_after": "NON_VOTING",
                    "materiality": "HIGH",
                    "meaning": "FUNDING_CROWDING_UP",
                    "source_ref": "factor_cross_section.funding",
                }
            ],
            "recent_5_trajectory": [
                {
                    "card_id": "PREV-CARD",
                    "lean": "NEUTRAL",
                    "support_label": "TRADE_SUPPORT_WEAK",
                    "macro_score": 0.26,
                    "funding_last_rate": 0.000052,
                    "gamma_regime": "GAMMA_TRANSITION",
                }
            ],
            "baseline_24h": {"event_count": 5, "macro_score_min": 0.1},
            "episode_anchor": {"card_id": "EP-ANCHOR", "macro_score": 0.12},
        },
        "transition_llm_review": {
            "status": "OK",
            "model": "gemini-3.5-flash",
            "input_packet_hash": "sha256:contract",
            "transition_summary_cn": "LLM 先解释程序化变化链，不重算 delta。",
            "trajectory_state": "DETERIORATING",
            "signal_continuity": "BLOCKED",
            "observed_changes": [
                {
                    "domain": "FUNDING",
                    "fact_cn": "资金费率原始值上升。",
                    "materiality": "HIGH",
                }
            ],
            "cross_factor_interactions": ["资金与宏观风险同向恶化。"],
            "operator_focus": ["确认变化链而不是执行交易。"],
            "invalid_if": ["模拟数据不能外推。"],
            "language_guard": {
                "no_trading_instruction": True,
                "no_external_data": True,
                "distinguishes_observation_from_causality": True,
            },
            "not_trading_advice": True,
        },
    }
    script = r"""
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const root = __ROOT__;
const card = __CARD__;
const app = fs.readFileSync(path.join(root, "app.js"), "utf8");

function renderCard(card) {
  const elements = {};
  function element(id) {
    if (!elements[id]) {
      elements[id] = {
        id,
        value: "",
        innerHTML: "",
        textContent: "",
        dataset: {},
        classList: { add() {}, remove() {}, toggle() {} },
        addEventListener() {},
        insertAdjacentHTML(_where, html) { this.innerHTML += html; },
        focus() {}
      };
    }
    return elements[id];
  }
  const document = {
    getElementById(id) {
      if (id === "signal-data") return { textContent: JSON.stringify([card]) };
      return element(id);
    },
    querySelector(selector) {
      return element(selector.startsWith("#") ? selector.slice(1) : selector);
    },
    querySelectorAll() { return []; }
  };
  const context = {
    window: { location: { protocol: "file:" }, SIGNAL_CARD_FIXTURES: [card] },
    document,
    console,
    Intl,
    setTimeout,
    clearTimeout,
    fetch: () => Promise.reject(new Error("unexpected fetch"))
  };
  vm.createContext(context);
  vm.runInContext(app, context);
  return new Promise((resolve) => setTimeout(() => {
    resolve(elements.documentView ? elements.documentView.innerHTML : "");
  }, 20));
}

(async () => {
  const html = await renderCard(card);
  const text = html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");
  process.stdout.write(JSON.stringify({ html, text }));
})();
"""
    script = (
        script
        .replace("__ROOT__", json.dumps(str(root)))
        .replace("__CARD__", json.dumps(sample, ensure_ascii=False))
    )
    result = subprocess.run(
        ["node", "-e", script],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    assert_true(result.returncode == 0, result.stderr or result.stdout)
    return json.loads(result.stdout)


def main():
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert_true("function renderTransitionContext(doc)" in app,
                "frontend should render materialized transition_context")
    assert_true("function renderIndexTransitionBadges(doc)" in app,
                "index should render transition badges from materialized data")
    assert_true("function renderTransitionLlmReview(doc)" in app,
                "frontend should render transition LLM sidecar reviews")
    session_idx = app.find("${renderSignalSessionContext(doc)}")
    transition_idx = app.find("${renderTransitionContext(doc)}")
    llm_idx = app.find("${renderLlmReview(doc)}")
    assert_true(session_idx != -1 and transition_idx != -1 and llm_idx != -1,
                "document render flow should include session, transition, and LLM sections")
    assert_true(session_idx < transition_idx < llm_idx,
                "transition context should render after session context and before card LLM review")
    for marker in (
            "状态转移审计",
            "上一状态",
            "当前状态",
            "比较质量",
            "原始字段变化",
            "LLM 变化链解释",
            "AUDIT_ONLY",
    ):
        assert_true(marker in app,
                    "frontend should expose transition audit label: " + marker)
    assert_true("delta_abs" in app and "comparison_quality" in app,
                "frontend should consume materialized deltas, not calculate them")

    index_html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert_true('"is_synthetic": true' not in index_html,
                "inline signal-data fallback should not embed synthetic preview cards")
    assert_true("GEMINI-LOCAL-PREVIEW" not in index_html,
                "inline signal-data fallback should not embed local Gemini preview cards")

    manifest = read_json(FRONTEND / "signal_cards" / "index.json")
    cards = []
    for item in manifest["cards"]:
        card = read_json(FRONTEND / item["path"])
        cards.append(card)
        assert_true(item["card_id"] == card["identity"]["card_id"],
                    "manifest card_id should match card identity")
    assert_true(not any(card["identity"].get("is_synthetic") for card in cards),
                "default deploy manifest should exclude synthetic preview cards")

    fallback = (FRONTEND / "signal_cards" / "fallback.js").read_text(encoding="utf-8")
    assert_true("GEMINI-LOCAL-PREVIEW" not in fallback,
                "default fallback.js should exclude synthetic preview cards")

    rows = render_contract(FRONTEND)
    assert_true(rows, "render contract should cover cards")
    for row in rows:
        assert_true(not row["synthetic"], row["card_id"] + " should not be synthetic")
        assert_true(not row["objectObject"], row["card_id"] + " rendered [object Object]")
        assert_true(row["compactLedger"], row["card_id"] + " should render compact evidence ledger")
        assert_true(not row["oldWideTable"], row["card_id"] + " should not render old wide evidence table")
        assert_true(row["fundingRateSide"], row["card_id"] + " should show fee-side funding tendency")
        assert_true(row["reflexiveFunding"], row["card_id"] + " should show reflexive funding tendency")
        assert_true(row["macroRawScore"], row["card_id"] + " should show raw macro score")
        assert_true(row["llmSection"], row["card_id"] + " should always render the LLM review section")
        assert_true(row["llmPending"], row["card_id"] + " should explain pending/missing LLM sidecar reviews")
        assert_true(row["macroProxyFacts"], row["card_id"] + " should show macro proxy component facts")
        assert_true(not row["macroUnknown"], row["card_id"] + " should not show UNKNOWN macro stance when raw score exists")
        assert_true(row["ggrSpatialConstraint"], row["card_id"] + " should describe GGR as spatial/gate context")
        assert_true(row["flowConfirm"], row["card_id"] + " should expose flow confirmation details")
        assert_true(row["rawTraceNav"], row["card_id"] + " should render raw trace navigation")
        assert_true(row["sourceRefLinks"] >= len(row["sourceRefs"]),
                    row["card_id"] + " should link every evidence source_ref")
        assert_true(row["rawTargets"] >= len(row["sourceRefs"]),
                    row["card_id"] + " should expose raw trace targets")

    transition_render = render_transition_contract(FRONTEND)
    full_transition_text = transition_render["text"]
    transition_html = transition_render["html"]
    start = full_transition_text.find("状态转移审计")
    end = full_transition_text.find("LLM 复核意见")
    transition_text = full_transition_text[start:end] if start != -1 and end != -1 else full_transition_text
    llm_pos = transition_text.find("LLM 变化链解释")
    raw_pos = transition_text.find("原始字段变化")
    assert_true(llm_pos != -1, "transition LLM explanation should use Chinese title")
    assert_true(raw_pos != -1, "raw field changes should use Chinese title")
    assert_true(llm_pos < raw_pos,
                "transition LLM explanation should render before raw field changes")
    for label in (
            "观察到的变化",
            "跨因子相互作用",
            "人工观察重点",
            "失效条件",
            "轨迹状态",
            "连续性",
            "不含交易建议",
            "不使用外部数据",
            "区分观察与因果",
            "最近 5 次轨迹",
            "24 小时基线",
            "同片段锚点",
            "上一值",
            "当前值",
            "变化量",
            "字段角色",
    ):
        assert_true(label in transition_text,
                    "transition UI should show Chinese semantic label: " + label)
    for raw_label in (
            "Top material changes",
            "observed_changes",
            "cross_factor_interactions",
            "operator_focus",
            "invalid_if",
            "no_trading_instruction",
            "not_trading_advice",
            "recent-5 trajectory",
            "24h baseline",
            "episode anchor",
    ):
        assert_true(raw_label not in transition_text,
                    "transition UI should not expose raw English label: " + raw_label)
    assert_true("[object Object]" not in transition_text
                and "[object Object]" not in transition_html,
                "transition UI should not leak object stringification")

    print("signal_audit_frontend_render_contract: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_audit_frontend_render_contract: FAIL - " + str(exc))
        sys.exit(1)
