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
    const transitionStart = text.indexOf("状态转移审计");
    const transitionEnd = text.indexOf("LLM 复核意见");
    const transitionMainText = transitionStart >= 0 && transitionEnd > transitionStart
      ? text.slice(transitionStart, transitionEnd)
      : "";
    const transitionLlmStart = transitionMainText.indexOf("LLM 变化链解释");
    const transitionCoreStart = transitionMainText.indexOf("关键变化骨架");
    const transitionLlmAndCoreText = transitionLlmStart >= 0
      ? transitionMainText.slice(
          transitionLlmStart,
          transitionCoreStart > transitionLlmStart ? transitionCoreStart : transitionMainText.length)
      : "";
    const observedStart = transitionLlmAndCoreText.indexOf("观察到的变化");
    const interactionStart = transitionLlmAndCoreText.indexOf("跨因子相互作用");
    const transitionObservedText = observedStart >= 0 && interactionStart > observedStart
      ? transitionLlmAndCoreText.slice(observedStart, interactionStart)
      : "";
    const evidence = Array.isArray(card.reasoning && card.reasoning.evidence)
      ? card.reasoning.evidence
      : [];
    const fundingItemHtml = html.split('<article class="evidence-item')
      .find((part) => part.includes('<strong class="evidence-key">FUNDING</strong>')) || "";
    const cvdItemHtml = html.split('<article class="evidence-item')
      .filter((part) => /<strong class="evidence-key">CVD_(4h|12h)<\/strong>/.test(part))
      .map((part) => part.split("</article>")[0]).join("");
    const hasLlmReview = !!(card.llm_review && Object.keys(card.llm_review).length);
    const sourceRefs = evidence.map((entry) => entry.source_ref).filter(Boolean);
    rows.push({
      card_id: card.identity && card.identity.card_id,
      synthetic: !!(card.identity && card.identity.is_synthetic),
      hasLlmReview,
      sourceRefs,
      objectObject: text.includes("[object Object]"),
      compactLedger: html.includes("evidence-ledger") && html.includes("evidence-item"),
      oldWideTable: html.includes("evidence-table"),
      redundantDecisionConclusion: text.includes("决策结论"),
      redundantDecisionMatrix: text.includes("封板决策矩阵"),
      redundantContextWarnings: text.includes("Context warnings"),
      redundantReasonCodes: text.includes("Reason codes"),
      gexRankSection: text.includes("GEX Rank 分位"),
      gammaOverviewSection: text.includes("期权 Gamma / GEX 重点"),
      completeEvidenceLedger: text.includes("完整证据账本"),
      factorCrossSection: text.includes("因子原始截面"),
      rawTraceJump: text.includes("原始截面跳转"),
      fundingSemanticContract: html.includes("费率规范语义"),
      legacyFundingRecompute: html.includes("反身性辅助倾向"),
      fundingHumanMachineCodeLeak:
        /NOT_CROWDED|NOISE|NON_VOTING|FUNDING_RAW_|TEMPERATE_(LONG|SHORT)_FUNDING/.test(
          fundingItemHtml.split("</article>")[0]),
      fundingHumanMachineCodes:
        fundingItemHtml.split("</article>")[0].match(
          /NOT_CROWDED|NOISE|NON_VOTING|FUNDING_RAW_|TEMPERATE_(LONG|SHORT)_FUNDING/g) || [],
      fundingMachineContexts:
        [...fundingItemHtml.split("</article>")[0].matchAll(
          /NOT_CROWDED|NOISE|NON_VOTING|FUNDING_RAW_|TEMPERATE_(LONG|SHORT)_FUNDING/g)]
          .map((match) => fundingItemHtml.slice(
            Math.max(0, match.index - 60), match.index + 100)),
      cvdHumanMachineCodeLeak:
        /BUY_CONFIRMS_UP|SELL_CONFIRMS_DOWN|BUY_ABSORBED_BEARISH|SELL_ABSORBED_BULLISH|FLOW_CONFIRM_COMPONENT|CVD_DATA_NOT_READY|CVD_HISTORY_WARMING/.test(
          cvdItemHtml),
      macroRawScore: html.includes("宏观背景") && html.includes("分数"),
      sourceRefLinks: (html.match(/class="source-ref-link/g) || []).length,
      rawTraceNav: html.includes("raw-trace-nav"),
      rawTargets: (html.match(/id="raw-/g) || []).length,
      flowConfirm: /FLOW_CONFIRM|combined_weight|absorption_state|fast_4h|slow_12h/.test(text),
      llmSection: text.includes("LLM 复核意见"),
      llmPending: text.includes("PENDING_LLM") || text.includes("LLM 复核尚未生成"),
      macroProxyFacts: /VOLQ|DXY|US10Y|纳斯达克|美元|美债/.test(text),
      macroUnknown: /宏观背景\s+UNKNOWN/.test(text),
      macroDirectionBackground: text.includes("方向背景"),
      macroShockGate: text.includes("冲击门"),
      macroShockMissingOrState: text.includes("历史卡未提供冲击门字段")
        || text.includes("CLEAR")
        || text.includes("WATCH")
        || text.includes("BLOCK")
        || text.includes("UNKNOWN"),
      ggrSpatialConstraint: text.includes("空间约束") || text.includes("空间安全"),
      transitionLlmRawEnumLeaks: [
        "Neutral",
        "Mild Headwind",
        "Strong Headwind",
        "Headwind",
        "NEUTRAL",
        "MACRO_BLOCKING",
        "WAIT_CONFIRMATION",
        "P_C_RATIO",
        "发生正负符号翻转"
      ].filter((token) => transitionLlmAndCoreText.includes(token)),
      transitionObservedBoilerplate: [
        "评估为",
        "材料性",
        "高材料性",
        "被评估为关键",
        "被评估为高"
      ].filter((token) => transitionObservedText.includes(token)),
      transitionObservedHasTendency: !transitionObservedText
        || /利空|利多|偏空|偏多|中性|风险约束|支撑|缓和|压制/.test(transitionObservedText)
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


def render_transition_contract(root, suppress_llm=False, unknown_render_state=False,
                               legacy_llm=False, raw_leak_llm=False,
                               noisy_meta=False):
    sample = {
        "schema": {"name": "signal_review_card", "version": "1.0.0"},
        "identity": {
            "card_id": "TRANSITION-CONTRACT-CARD",
            "short_id": "TCC",
            "symbol": "BTC",
            "strategy_version": "1.5.1",
            "confirmed_at": "2026-06-18T11:00:00+08:00",
        },
        "quality": {"overall": "OK"},
        "decision": {
            "lean": "LONG_BIAS",
            "support_label": "NO_TRADE_BLOCKED",
            "confidence": 64,
        },
        "decision_matrix": {
            "window": "CONFIRMED",
            "direction": "LONG_BIAS",
            "temporal_durability": "NEUTRAL",
            "audit_dissent": "PENDING_LLM",
            "model_trade_support": None,
            "execution_allowed": None,
            "context_warnings": ["SHOULD_NOT_RENDER_CONTEXT_WARNING"],
            "reason_codes": ["SHOULD_NOT_RENDER_REASON_CODE"],
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
            "core_skeleton": {
                "schema_version": "transition_core_skeleton@1.0.0",
                "timeline": {
                    "previous_card_id": "PREV-CARD",
                    "current_card_id": "TRANSITION-CONTRACT-CARD",
                    "previous_short_id": "PREV",
                    "current_short_id": "TCC",
                    "previous_ts_ms": 1781770200000,
                    "current_ts_ms": 1781772900000,
                    "elapsed_ms": 2700000,
                    "comparison_quality": "HIGH",
                },
                "domains": [
                    {
                        "domain": "TMV",
                        "previous": {"tmv_blend": 0.42, "tmvf_24h_final": 0.31},
                        "current": {"tmv_blend": 0.18, "tmvf_24h_final": 0.11},
                        "source_refs": ["factor_cross_section.tmvf"],
                    },
                    {
                        "domain": "MACRO",
                        "previous": {"macro_score": 0.26},
                        "current": {"macro_score": 0.45},
                        "source_refs": ["factor_cross_section.macro_pressure"],
                    },
                    {
                        "domain": "GAMMA",
                        "previous": {"net_gamma_notional_usd": 12400000},
                        "current": {"net_gamma_notional_usd": -7600000},
                        "source_refs": ["factor_cross_section.gamma_regime"],
                    },
                    {
                        "domain": "P_C_RATIO",
                        "previous": {"put_call_ratio": 0.92},
                        "current": {"put_call_ratio": 1.22},
                        "source_refs": ["factor_cross_section.gex_info"],
                    },
                    {
                        "domain": "CONFLICT",
                        "previous": {"ratio": 0.18, "level": "LOW"},
                        "current": {"ratio": 0.62, "level": "MATERIAL"},
                        "source_refs": ["conflict"],
                    },
                ],
            },
            "domain_change_summaries": [
                {
                    "domain": "MACRO",
                    "materiality": "CRITICAL",
                    "raw_change_count": 3,
                    "primary_fields": [
                        "factor_cross_section.macro_pressure.macro_score",
                        "factor_cross_section.macro_pressure.components.DXY.scoring_bps",
                        "factor_cross_section.macro_pressure.components.US10Y.scoring_bps",
                    ],
                    "source_refs": ["factor_cross_section.macro_pressure"],
                    "children": [],
                },
                {
                    "domain": "FUNDING",
                    "materiality": "HIGH",
                    "raw_change_count": 1,
                    "primary_fields": ["factor_cross_section.funding.last_rate"],
                    "source_refs": ["factor_cross_section.funding"],
                    "children": [],
                },
            ],
            "core_transition_display": [
                {
                    "domain": "FUNDING",
                    "title_cn": "Funding（期货资金费率）",
                    "value_key": "last_rate",
                    "previous_display": "0.00038%",
                    "current_display": "-0.001438%",
                    "delta_display": "-0.001818%",
                    "meaning_cn": "资金费率由轻微正值转为轻微负值，说明永续端多头付费压力已经消失，方向意义偏弱但能提示拥挤结构缓和。",
                    "grade_cn": "低",
                    "source_note": "原始 last_rate",
                },
                {
                    "domain": "GAMMA",
                    "title_cn": "Gamma（净 Gamma）",
                    "value_key": "net_gamma_metric",
                    "previous_display": "-1.5562",
                    "current_display": "-1.6217",
                    "delta_display": "-0.0655",
                    "meaning_cn": "旧卡兼容推导的 Gamma 指标小幅走弱，只能说明空间约束略加深，不能伪装成 USD 名义额。",
                    "grade_cn": "低",
                    "source_note": "旧卡兼容推导",
                },
                {
                    "domain": "P_C_RATIO",
                    "title_cn": "P/C（期权需求）",
                    "value_key": "put_call_ratio",
                    "previous_display": "2.29",
                    "current_display": "2.18",
                    "delta_display": "-0.11",
                    "meaning_cn": "期权保护需求从高位略回落，但仍处偏高区域，对方向判断只是缓和不是反转。",
                    "grade_cn": "低",
                    "source_note": "GEX put_call_ratio",
                },
            ],
            "raw_change_groups": [
                {
                    "domain": "MACRO",
                    "materiality": "CRITICAL",
                    "raw_change_count": 3,
                    "children": [
                        {
                            "domain": "MACRO",
                            "field": "factor_cross_section.macro_pressure.components.DXY.scoring_bps",
                            "previous": 17,
                            "current": 24,
                            "delta_abs": 7,
                            "role_before": "EXCLUDED",
                            "role_after": "EXCLUDED",
                            "materiality": "HIGH",
                            "meaning": "DXY_PRESSURE_RISE",
                            "source_ref": "factor_cross_section.macro_pressure",
                        }
                    ],
                },
            ],
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
            "transition_summary_cn": "维持NEUTRAL偏好，持续受MACRO_BLOCKING阻碍。",
            "trajectory_state": "DETERIORATING",
            "signal_continuity": "BLOCKED",
            "observed_changes": [
                {
                    "domain": "TMV",
                    "fact_cn": "TMV 从 0.42 降至 0.18，量价路径转弱。",
                    "impact_cn": "量价骨架明显走弱，需要把人工关注从延续支撑转向支撑失效核验。",
                    "tendency_cn": "偏空/支撑削弱",
                    "evidence_refs": ["/core_transition_display/0"],
                    "evidence_status": "SUFFICIENT",
                    "directional_role": "RISK_CONSTRAINT",
                    "magnitude_verdict": "changes_judgment",
                    "audit_attention_effect": "SHIFT_FOCUS",
                    "epistemic_status": "SUPPORTED_INFERENCE",
                    "materiality": "CRITICAL",
                },
                {
                    "domain": "FUNDING",
                    "fact_cn": "Funding 从 3.8e-06 转负至 -1.438e-05，发生正负符号翻转。",
                    "impact_cn": "资金费率由轻微正值转为轻微负值，说明永续端多头付费压力消失，方向意义偏弱。",
                    "tendency_cn": "中性/拥挤缓和",
                    "evidence_refs": ["/core_transition_display/2"],
                    "evidence_status": "SUFFICIENT",
                    "directional_role": "NEUTRAL_OR_EASING",
                    "magnitude_verdict": "background_only",
                    "audit_attention_effect": "WEAKEN_VIEW",
                    "epistemic_status": "SUPPORTED_INFERENCE",
                    "materiality": "HIGH",
                },
                {
                    "domain": "P_C_RATIO",
                    "fact_cn": "Put/Call 比例从 2.29 降至 2.18，发生正负符号翻转。",
                    "impact_cn": "保护需求从高位略回落，但绝对水平仍高，不足以单独改变中性审计结论。",
                    "tendency_cn": "中性/保护需求缓和",
                    "evidence_refs": ["/core_transition_display/5"],
                    "evidence_status": "SUFFICIENT",
                    "directional_role": "NEUTRAL_OR_EASING",
                    "magnitude_verdict": "background_only",
                    "audit_attention_effect": "BACKGROUND_ONLY",
                    "epistemic_status": "SUPPORTED_INFERENCE",
                    "materiality": "HIGH",
                }
            ],
            "cross_factor_interactions": ["宏观逆风（MACRO Headwind）持续存在，与资金费率（FUNDING）转负共同压制NEUTRAL偏好。"],
            "cross_factor_assessments": [
                {
                    "domains": ["TMV", "MACRO", "FUNDING"],
                    "relation": "CONSTRAINT_INTERACTION",
                    "assessment_cn": "量价路径转弱与宏观硬阻断同向构成约束，资金费率转负仅削弱拥挤解释，不能抵消主约束。",
                    "evidence_refs": ["/core_transition_display/0", "/core_transition_display/1", "/core_transition_display/2"],
                }
            ],
            "operator_focus": ["确认变化链而不是执行交易。"],
            "operator_checks": [
                {
                    "focus_cn": "核验宏观硬阻断是否继续存在。",
                    "why_cn": "它决定当前变化是短暂压力还是持续约束。",
                    "strengthens_if_cn": "若后续卡仍处于宏观硬阻断且 TMV 未恢复，约束解释增强。",
                    "weakens_if_cn": "若宏观压力回落且 TMV 恢复，约束解释减弱。",
                    "evidence_refs": ["/core_transition_display/0", "/core_transition_display/1"],
                }
            ],
            "invalid_if": ["如果宏观评分回落且脱离Headwind状态，MACRO_BLOCKING阻碍可能失效。"],
            "language_guard": {
                "no_trading_instruction": True,
                "no_external_data": True,
                "distinguishes_observation_from_causality": True,
            },
            "not_trading_advice": True,
            "policy_validation": {
                "passed": True,
                "raw_enum_leaks": [],
                "trading_instruction_terms": [],
                "unit_mislabel_terms": [],
                "materiality_boilerplate_terms": [],
                "invalid_evidence_refs": [],
            },
        },
    }
    if suppress_llm:
        review = sample["transition_llm_review"]
        review["transition_summary_cn"] = "SHOULD_NOT_RENDER_SUPPRESSED_SUMMARY 开仓"
        review["observed_changes"][0]["fact_cn"] = "SHOULD_NOT_RENDER_SUPPRESSED_CHANGE"
        review["cross_factor_assessments"][0]["assessment_cn"] = "SHOULD_NOT_RENDER_SUPPRESSED_CROSS_FACTOR"
        review["operator_checks"][0]["focus_cn"] = "SHOULD_NOT_RENDER_SUPPRESSED_OPERATOR"
        review["operator_focus"] = ["SHOULD_NOT_RENDER_SUPPRESSED_FOCUS"]
        review["invalid_if"] = ["SHOULD_NOT_RENDER_SUPPRESSED_INVALID_IF"]
        review["policy_validation"] = {
            "passed": False,
            "severity": "FATAL",
            "render_state": "SUPPRESS_LLM_TEXT",
            "issue_codes": ["trading_instruction"],
            "trading_instruction_terms": ["开仓"],
            "raw_enum_leaks": [],
            "unit_mislabel_terms": [],
            "materiality_boilerplate_terms": [],
            "invalid_evidence_refs": [],
        }
    if unknown_render_state:
        review = sample["transition_llm_review"]
        review["transition_summary_cn"] = "SHOULD_NOT_RENDER_UNKNOWN_RENDER_STATE"
        review["observed_changes"][0]["fact_cn"] = "SHOULD_NOT_RENDER_UNKNOWN_RENDER_STATE_CHANGE"
        review["policy_validation"] = {
            "passed": False,
            "severity": "ERROR",
            "render_state": "FUTURE_RENDER_STATE",
            "issue_codes": ["future_policy_state"],
        }
    if legacy_llm:
        review = sample["transition_llm_review"]
        review["schema_version"] = "signal_transition_llm_review@1.1.0"
        review["prompt_version"] = "gemini_signal_transition_review_prompt@1.1.0"
        review.pop("policy_validation", None)
    if raw_leak_llm:
        review = sample["transition_llm_review"]
        leaked = (
            "宏观 原始变化 6 项，主要字段：macro_pressure.components.US10Y.scoring_bps，"
            "来源：factor_cross_section.macro_pressure；核心前后值已入包。")
        review["transition_summary_cn"] = leaked
        review["observed_changes"][0]["fact_cn"] = leaked
        review["observed_changes"][0]["impact_cn"] = "source_ref 指向 factor_cross_section.macro_pressure。"
        review["cross_factor_assessments"][0]["assessment_cn"] = (
            "macro_pressure.components.DXY.scoring_bps 与 TMV 共同变化。")
        review["operator_checks"][0]["focus_cn"] = "核对 source_ref 与 primary_fields。"
        review["policy_validation"] = {
            "passed": False,
            "severity": "ERROR",
            "render_state": "DEGRADED_LLM_TEXT",
            "issue_codes": ["raw_field_path_leak"],
            "raw_field_path_terms": ["factor_cross_section", "macro_pressure.components"],
            "raw_enum_leaks": [],
            "trading_instruction_terms": [],
            "unit_mislabel_terms": [],
            "materiality_boilerplate_terms": [],
            "invalid_evidence_refs": [],
        }
    if noisy_meta:
        review = sample["transition_llm_review"]
        review["observed_changes"][0]["directional_role"] = "UNDETERMINED"
        review["observed_changes"][0]["magnitude_verdict"] = "indeterminate"
        review["observed_changes"][0]["audit_attention_effect"] = "UNDETERMINED"
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


def render_sample_card(root, sample):
    script = r"""
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const root = __ROOT__;
const card = __CARD__;
const app = fs.readFileSync(path.join(root, "app.js"), "utf8");
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
setTimeout(() => {
  const documentHtml = elements.documentView ? elements.documentView.innerHTML : "";
  const indexHtml = elements.indexList ? elements.indexList.innerHTML : "";
  const text = documentHtml.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");
  const indexText = indexHtml.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");
  process.stdout.write(JSON.stringify({ documentHtml, indexHtml, text, indexText }));
}, 20);
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


def durability_sample_card(with_durability=True):
    card = {
        "schema": {"name": "signal_review_card", "version": "1.0.0"},
        "identity": {
            "card_id": "DURABILITY-CONTRACT-CARD",
            "short_id": "DUR",
            "symbol": "BTC",
            "strategy_version": "1.5.2",
            "confirmed_at": "2026-06-24T10:00:00+08:00",
        },
        "market_context": {"price": 101234.5, "quote_currency": "USDT"},
        "quality": {"overall": "OK", "all_required_sources_ready": True},
        "decision": {
            "lean": "NEUTRAL",
            "support_label": "NO_TRADE_BLOCKED",
            "confidence": 64,
            "evidence_strength": "MEDIUM",
        },
        "conflict": {"ratio": 0.12, "level": "LOW"},
        "reasoning": {"evidence": []},
        "factor_cross_section": {
            "anchor": {"score": 72, "normalized_deviation": 0.56},
            "gamma_regime": {
                "regime": "TRANSITION",
                "net_gamma_notional_usd": 101000000,
                "confidence_multiplier": 0.98,
            },
            "gex_info": {
                "market_state": "POSITIVE_GAMMA",
                "net_gamma_notional_usd": 101000000,
            },
            "funding": {
                "last_rate": 0.00006,
                "last_funding_rate": 0.00006,
            },
        },
        "signal_window": {
            "session_context": {
                "display_label": "中性保守",
                "premise_durability": "NEUTRAL_CONSERVATIVE",
                "effective_zone": "NEUTRAL_CONSERVATIVE",
                "backtest_delta_pp": 0.09,
                "validation_basis": {
                    "data_range": "2023-04-17 -> 2026-04-16",
                    "headline_horizon_min": 60,
                    "sample_bars": 315363,
                    "research_grade": "MARKET_PRIOR_VALIDATED",
                },
            },
        },
    }
    if with_durability:
        card["signal_durability"] = {
            "schema_name": "SignalDurabilityLayer",
            "schema_version": "nrd.signal.durability_layer.v1",
            "audit_scope": "AUDIT_ONLY",
            "headline_score": 72,
            "headline_state": "ANCHOR_DURABLE",
            "comfort_window": {
                "tag": "NORMAL_WINDOW",
                "state": "NORMAL",
                "brief_token": "NW",
            },
            "temporal_session": {
                "state": "MEDIUM",
                "score": 0.61,
                "display_label": "中性保守",
                "premise_durability": "NEUTRAL_CONSERVATIVE",
                "backtest_delta_pp": 0.09,
                "validation_basis": {
                    "data_range": "2023-04-17 -> 2026-04-16",
                    "headline_horizon_min": 60,
                    "sample_bars": 315363,
                    "research_grade": "MARKET_PRIOR_VALIDATED",
                },
            },
            "session_context": {
                "state": "MEDIUM",
                "score": 0.61,
                "display_label": "中性保守",
                "premise_durability": "NEUTRAL_CONSERVATIVE",
                "backtest_delta_pp": 0.09,
            },
            "price_anchor_durability": {
                "schema_name": "SignalPriceAnchorDurability",
                "schema_version": "nrd.signal.price_anchor_durability.v1",
                "durability_state": "ANCHOR_DURABLE",
                "durability_score": 72,
                "state": "ANCHOR_DURABLE",
                "score": 72,
                "score_quality": "HIGH",
                "layer_scores": {
                    "anchor_native": {"score": 0.72, "state": "ANCHOR_DURABLE"},
                    "price_efficiency": {
                        "score": 0.65,
                        "state": "ANCHOR_WEAK",
                        "ppe": 1.0,
                        "method": "PROXY_OHLC",
                        "inside_anchor_band": True,
                        "band_distance_ratio": 0.56,
                        "interpretation": "PPE_FAVORABLE_EFFICIENT_NOT_AUTOPASS",
                    },
                    "options_gamma": {
                        "score": 0.80,
                        "state": "ANCHOR_DURABLE",
                        "regime": "TRANSITION",
                        "net_gamma_notional_usd": 0,
                    },
                    "perp_funding": {
                        "score": 0.70,
                        "last_rate": 0.00006,
                        "funding_rate": 0.00006,
                        "funding_aligns_with_signal": True,
                        "interpretation": "HEALTHY_CONFIRMATION",
                    },
                },
            },
            "layer_scores": {
                "anchor_native": {"score": 0.72, "state": "ANCHOR_DURABLE"},
                "price_efficiency": {
                    "score": 0.65,
                    "state": "ANCHOR_WEAK",
                    "ppe": 1.0,
                    "method": "PROXY_OHLC",
                    "inside_anchor_band": True,
                    "band_distance_ratio": 0.56,
                    "interpretation": "PPE_FAVORABLE_EFFICIENT_NOT_AUTOPASS",
                },
                "options_gamma": {
                    "score": 0.80,
                    "state": "ANCHOR_DURABLE",
                    "regime": "TRANSITION",
                    "net_gamma_notional_usd": 0,
                },
                "perp_funding": {
                    "score": 0.70,
                    "last_rate": 0.00006,
                    "funding_rate": 0.00006,
                    "funding_aligns_with_signal": True,
                    "interpretation": "HEALTHY_CONFIRMATION",
                },
            },
            "reason_codes": ["COMFORT_WINDOW_OK", "PRICE_ANCHOR_OK"],
            "data_gaps": ["NO_TRADE_GATE"],
            "confidence_policy": "DO_NOT_MULTIPLY_CONFIDENCE",
        }
    return card


def advisory_sample_card():
    card = durability_sample_card()
    card["identity"]["card_id"] = "ADVISORY-CONTRACT-CARD"
    card["identity"]["short_id"] = "ADV"
    card["decision"]["lean"] = "BULLISH_WITH_DISAGREEMENT"
    card["factor_cross_section"]["tmvf"] = {
        "tmv_blend": 0.42,
        "source_ref": "BINANCE_1H_KLINE",
    }
    card["llm_review"] = {
        "status": "OK",
        "schema": "signal_llm_review@1.4.0",
        "provider": "gemini",
        "model": "gemini-3.5-flash",
        "summary_cn": "LLM 复核完成，系统方向仍由信号层决定。",
        "integrated_trade_advisory": {
            "recommendation": "SELL_PUT_SPREAD_REVIEW",
            "final_conclusion_cn": "允许进入卖出 Put 价差的结构复核，但仍保持只读辅助。",
            "cross_loop_rationale_cn": "信号层方向、耐久度和盲审结构建议大体同向，适合放在人工结构复核清单首位。",
            "containment_assessment": {
                "state": "ESTABLISHED",
                "basis_cn": "中性接管成立，风险已被限定在结构复核而非方向重写。",
            },
            "premium_selling_fit": {
                "state": "FIT",
                "basis_cn": "卖方价差结构与当前波动和空间约束相匹配，仅供复核。",
            },
            "side_basis_cn": "偏多证据只能映射为 Put 侧结构复核，不能升级成交易许可。",
            "dominant_conflict_cn": "主要冲突来自宏观轻度逆风和分歧率仍未完全收敛。",
            "key_premises": [
                {
                    "premise_cn": "锚定耐久度仍在可复核区间。",
                    "evidence_refs": ["EV_DECISION"],
                },
                {
                    "premise_cn": "冲突没有扩大到推翻结构复核。",
                    "evidence_refs": ["EV_SIGNAL_DURABILITY"],
                },
            ],
            "invalid_if": [
                "宏观冲击升级为硬阻断。",
                "Gamma 空间结构转为空头放大。",
            ],
            "next_observation_cn": "继续观察冲突率是否回落以及锚定耐久度是否保持。",
            "session_advisory": {
                "liquidity_assessment": "CAUTION",
                "warning_level": "HIGH",
                "basis_cn": "美盘前陷阱窗口历史复合改写为 +5.31pp，需要高亮提醒。",
                "does_not_change_recommendation": True,
            },
            "source_alignment": "ALIGNED",
            "audit_only": True,
            "trade_authorization": False,
            "future_24h_bayesian_report": {
                "schema_version": "future_24h_bayesian_report@1.0.0",
                "horizon_hours": 24,
                "input_scope": "PACKET_FACTS_PLUS_MODEL_PRIOR_NO_LIVE_SEARCH",
                "live_external_data_used": False,
                "base_case": "UP",
                "posterior_weights_pct": {"up": 52, "down": 18, "range": 30},
                "report_cn": "未来24小时贝叶斯观察：基准情景偏上，主观情景权重为上涨52%、下跌18%、区间30%；关键观察位为卡内101500与主观推导的104800；重要反证是宏观压力继续增强，若价格跌破卡内中轴且方向证据转弱则本推断失效。",
                "key_levels": [
                    {
                        "role_cn": "卡内Gamma钉住观察位",
                        "price": 101500,
                        "basis_cn": "来自卡内 Gamma 截面。",
                        "source_type": "PACKET_OBSERVED",
                    },
                    {
                        "role_cn": "上方模型观察位",
                        "price": 104800,
                        "basis_cn": "模型估算观察位，由当前冲突率和波动形态推导。",
                        "source_type": "MODEL_ESTIMATED",
                    },
                ],
                "counter_evidence_cn": ["宏观压力继续增强。"],
                "invalid_if_cn": ["价格跌破卡内中轴且方向证据转弱。"],
                "policy_validation": {
                    "passed": True,
                    "posterior_weights_sum_100": True,
                },
            },
            "policy_validation": {"passed": True},
        },
    }
    return card


def advisory_section_text(rendered):
    text = rendered["text"]
    start = text.find("最高辅助交易决策")
    if start < 0:
        return ""
    end = text.find("Gamma / GEX", start)
    return text[start:end if end > start else len(text)]


def main():
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    project_memory = (ROOT / "PROJECT_MEMORY.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert_true("本地前端页面人工确认前不得推送" in project_memory,
                "PROJECT_MEMORY should record the frontend human-confirmation push gate")
    assert_true("重点清晰、逻辑贯通、关键内容全面" in project_memory,
                "PROJECT_MEMORY should record the audit-page clarity principle")
    assert_true("当前本地页面可推送" in agents,
                "AGENTS should require explicit local-page push confirmation")
    assert_true("function renderTransitionContext(doc)" in app,
                "frontend should render materialized transition_context")
    assert_true("${renderTransitionRawChanges(doc)}" not in app,
                "frontend should not render low-signal raw transition changes in the main page")
    assert_true("function renderIndexTransitionBadges(doc)" in app,
                "index should render transition badges from materialized data")
    assert_true("function renderTransitionLlmReview(doc)" in app,
                "frontend should render transition LLM sidecar reviews")
    assert_true("macro_shock" in app and "legacy_blocking_flags" in app,
                "frontend should keep native macro shock and legacy macro block fields traceable")
    assert_true("function renderSignalDurability(doc)" in app,
                "frontend should expose unified signal durability renderer")
    assert_true("${renderSignalDurability(doc)}" in app,
                "document render flow should call unified signal durability renderer")
    assert_true("function renderIntegratedTradeAdvisory(doc)" in app,
                "frontend should expose integrated trade advisory renderer")
    assert_true("${renderIntegratedTradeAdvisory(doc)}" in app,
                "document render flow should call integrated trade advisory renderer")
    assert_true("function future24hBayesianReport(doc" in app
                and "function renderFuture24hBayesianTrace(doc)" in app,
                "frontend should expose future_24h_bayesian_report helpers")
    assert_true("${renderFuture24hBayesianSummary(doc, advisory)}" in app,
                "integrated advisory should call future 24h Bayesian summary renderer")
    assert_true("${renderSignalSessionContext(doc)}" not in app,
                "standalone session-context renderer should be replaced in the main flow")
    metric_idx = app.find('class="metric-strip"')
    advisory_idx = app.find("${renderIntegratedTradeAdvisory(doc)}")
    gamma_idx = app.find("${renderGammaOverview(doc)}")
    session_idx = app.find("${renderSignalDurability(doc)}")
    transition_idx = app.find("${renderTransitionContext(doc)}")
    llm_idx = app.find("${renderLlmReview(doc)}")
    assert_true(metric_idx != -1 and advisory_idx != -1 and gamma_idx != -1
                and session_idx != -1 and transition_idx != -1 and llm_idx != -1,
                "document render flow should include metric, advisory, Gamma, durability, transition, and LLM sections")
    assert_true(metric_idx < advisory_idx < gamma_idx,
                "integrated trade advisory should render after the six top metrics and before Gamma/GEX")
    assert_true(session_idx < transition_idx < llm_idx,
                "transition context should render after durability context and before card LLM review")
    forecast_summary_idx = app.find("${renderFuture24hBayesianSummary(doc, advisory)}")
    advisory_grid_idx = app.find('class="integrated-advisory-grid"')
    assert_true(forecast_summary_idx != -1 and advisory_grid_idx != -1
                and forecast_summary_idx < advisory_grid_idx,
                "future 24h Bayesian report should render after advisory conclusion and before structure grid")
    for marker in (
            "comfort_window",
            "price_anchor_durability",
            "layer_scores",
            "reason_codes",
            "data_gaps",
            "AUDIT_ONLY",
            "DO_NOT_MULTIPLY_CONFIDENCE",
            "US_T2_CORE_COMFORT",
            "anchor_native",
            "price_efficiency",
            "options_gamma",
            "perp_funding",
    ):
        assert_true(marker in app,
                    "frontend should expose signal durability marker: " + marker)
    for marker in (
            "状态转移审计",
            "状态路径",
            "核心骨架",
            "领域变化摘要",
            "审计元数据",
            "比较质量",
            "状态转移原始字段变化",
            "LLM 变化链解释",
            "AUDIT_ONLY",
    ):
        assert_true(marker in app,
                    "frontend should expose transition audit label: " + marker)
    assert_true("delta_abs" in app and "comparison_quality" in app,
                "frontend should consume materialized deltas, not calculate them")
    assert_true('semanticCompact(ctx.comparison_quality' in app
                and 'semanticCompact(flags[0] || "AUDIT_ONLY")' in app,
                "sidebar transition badges should translate machine enums for readers")

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
    frontend_version = read_json(FRONTEND / "VERSION.json")
    assert_true("future_24h_bayesian_report" in frontend_version["frontend_contract"],
                "VERSION frontend_contract should document the optional future 24h report")

    durability_render = render_sample_card(FRONTEND, durability_sample_card())
    durability_text = durability_render["text"]
    durability_html = durability_render["documentHtml"]
    durability_index = durability_render["indexText"]
    assert_true("signal-durability" in durability_html,
                "native card should render unified signal durability section")
    for token in (
            "信号耐用性层",
            "合成耐用性",
            "舒适窗口",
            "时区耐用性",
            "72",
            "锚原生层",
            "PPE路径效率",
            "GEX / Gamma放大器",
            "Funding杠杆解释",
            "+0.09pp",
            "中性保守",
            "合成解释",
            "只读解释",
            "不改变置信",
    ):
        assert_true(token in durability_text,
                    "durability UI missing readable conclusion token: " + token)
    for raw_token in (
            "NORMAL_WINDOW",
            "ANCHOR_DURABLE",
            "anchor_native",
            "price_efficiency",
            "options_gamma",
            "perp_funding",
            "PPE_FAVORABLE_EFFICIENT_NOT_AUTOPASS",
            "COMFORT_WINDOW_OK",
            "NO_TRADE_GATE",
            "DO_NOT_MULTIPLY_CONFIDENCE",
            "headline_score",
            "comfort_window.tag",
            "NW",
    ):
        assert_true(raw_token not in durability_text,
                    "durability UI should not expose raw machine token: " + raw_token)
    assert_true("A. 锚原生层" not in durability_text
                and "B. PPE路径效率" not in durability_text,
                "durability layer titles should not include A/B prefixes")
    for token in (
            "结论",
            "最小验算",
            "评分依据",
            "系统锚分 72/100",
            "0.56 个锚带半宽",
            "路径效率较高",
            "仍留在锚带内",
            "正 Gamma",
            "101.0M USD",
            "抑制波动",
            "稳定器",
            "Funding 0.006%",
            "0.01%",
            "温和同向",
            "不拥挤",
    ):
        assert_true(token in durability_text,
                    "durability UI should show semantic validation token: " + token)
    for bad_token in (
            "净 Gamma 0",
            "锚耐用；Gamma",
            "PPE 1.00",
    ):
        assert_true(bad_token not in durability_text,
                    "durability UI should not show low-information formula text: " + bad_token)
    gamma_zero_card = json.loads(json.dumps(durability_sample_card()))
    gamma_zero_card["factor_cross_section"]["gamma_regime"]["regime"] = "POSITIVE_GAMMA_PINNING"
    gamma_zero_card["factor_cross_section"]["gamma_regime"]["net_gamma_notional_usd"] = 0
    gamma_zero_card["factor_cross_section"]["gex_info"]["market_state"] = "POSITIVE_GAMMA"
    gamma_zero_card["factor_cross_section"]["gex_info"]["net_gamma_notional_usd"] = 0
    gamma_zero_text = render_sample_card(FRONTEND, gamma_zero_card)["text"]
    assert_true("Gamma 状态待判" in gamma_zero_text
                and "本卡没有可直接确认的有效净 Gamma" in gamma_zero_text,
                "zero net Gamma without nonzero fallback should stay pending")
    assert_true("正 Gamma 稳定器" not in gamma_zero_text
                and "当前净 Gamma 约 0 USD" not in gamma_zero_text,
                "zero net Gamma should not be promoted to positive Gamma semantics")

    funding_boundary_card = json.loads(json.dumps(durability_sample_card()))
    boundary_funding = funding_boundary_card["signal_durability"]["price_anchor_durability"]["layer_scores"]["perp_funding"]
    boundary_funding["last_rate"] = 0.0001
    boundary_funding["funding_rate"] = 0.0001
    funding_boundary_card["signal_durability"]["layer_scores"]["perp_funding"]["last_rate"] = 0.0001
    funding_boundary_card["signal_durability"]["layer_scores"]["perp_funding"]["funding_rate"] = 0.0001
    funding_boundary_card["factor_cross_section"]["funding"]["last_rate"] = 0.0001
    funding_boundary_card["factor_cross_section"]["funding"]["last_funding_rate"] = 0.0001
    funding_boundary_text = render_sample_card(FRONTEND, funding_boundary_card)["text"]
    assert_true("Funding 0.01%" in funding_boundary_text
                and "不拥挤" in funding_boundary_text,
                "Funding exactly 0.0100% should stay baseline/mild and uncrowded")
    for bad_token in (
            "达到或高于 0.01% 拥挤阈值",
            "杠杆拥挤抬升",
            "已拥挤",
            "拥挤多头倾向",
    ):
        assert_true(bad_token not in funding_boundary_text,
                    "Funding equality boundary should not show crowding token: " + bad_token)

    gamma_proxy_card = json.loads(json.dumps(durability_sample_card()))
    proxy_gamma = gamma_proxy_card["signal_durability"]["price_anchor_durability"]["layer_scores"]["options_gamma"]
    proxy_gamma["net_gamma_notional_usd"] = 0.39
    proxy_gamma["net_gamma_source"] = "factor_cross_section.gamma_regime.proxy_metric"
    gamma_proxy_card["signal_durability"]["layer_scores"]["options_gamma"]["net_gamma_notional_usd"] = 0.39
    gamma_proxy_card["signal_durability"]["layer_scores"]["options_gamma"]["net_gamma_source"] = "factor_cross_section.gamma_regime.proxy_metric"
    gamma_proxy_card["factor_cross_section"]["gamma_regime"]["regime"] = "POSITIVE_GAMMA_PINNING"
    gamma_proxy_card["factor_cross_section"]["gamma_regime"]["net_gamma_notional_usd"] = 0.39
    gamma_proxy_card["factor_cross_section"]["gex_info"]["market_state"] = "POSITIVE_GAMMA"
    gamma_proxy_card["factor_cross_section"]["gex_info"]["net_gamma_notional_usd"] = 112000000
    gamma_proxy_card["factor_cross_section"]["gex_info"]["total_net_gex"] = 112000000
    gamma_proxy_text = render_sample_card(FRONTEND, gamma_proxy_card)["text"]
    assert_true("112.0M USD" in gamma_proxy_text
                and "正 Gamma 稳定器" in gamma_proxy_text,
                "real gex_info USD notional should outrank tiny Gamma proxy")
    assert_true("当前净 Gamma 约 0 USD" not in gamma_proxy_text,
                "tiny Gamma proxy should not be formatted as zero USD")

    outside_anchor_card = json.loads(json.dumps(durability_sample_card()))
    outside_anchor = outside_anchor_card["signal_durability"]["price_anchor_durability"]["layer_scores"]["anchor_native"]
    outside_anchor["score"] = 0.41
    outside_anchor["state"] = "ANCHOR_WEAK"
    outside_anchor["distance_ratio"] = 1.24
    outside_anchor["outside_anchor_band"] = True
    outside_anchor_card["factor_cross_section"]["anchor"]["normalized_deviation"] = 1.24
    outside_anchor_text = render_sample_card(FRONTEND, outside_anchor_card)["text"]
    assert_true("现价已离开锚带" in outside_anchor_text
                and "1.24 个锚带半宽" in outside_anchor_text
                and "破锚/弱锚边界" in outside_anchor_text,
                "anchor layer should describe outside-anchor validation when distance exceeds band")
    assert_true("现价仍在锚带内，偏离约 1.24" not in outside_anchor_text,
                "outside anchor should not be described as still inside the anchor band")

    missing_funding_card = json.loads(json.dumps(durability_sample_card()))
    missing_funding = missing_funding_card["signal_durability"]["price_anchor_durability"]["layer_scores"]["perp_funding"]
    missing_funding.pop("last_rate", None)
    missing_funding.pop("funding_rate", None)
    missing_funding_card["factor_cross_section"]["funding"].pop("last_rate", None)
    missing_funding_card["factor_cross_section"]["funding"].pop("last_funding_rate", None)
    missing_funding_text = render_sample_card(FRONTEND, missing_funding_card)["text"]
    assert_true("Funding 未提供" in missing_funding_text
                and "杠杆拥挤状态待判" in missing_funding_text,
                "missing Funding rate should stay pending")
    assert_true("结论：温和同向 Funding" not in missing_funding_text
                and "温和同向但不拥挤" not in missing_funding_text,
                "missing Funding rate should not be called warm aligned and uncrowded")
    assert_true("舒适窗 否" in durability_index
                and "72" in durability_index
                and "NW" not in durability_index,
                "sidebar mini-stats should show comfort-window conclusion, not raw NW")
    time_only_card = durability_sample_card()
    time_only_card["signal_durability"]["comfort_window"] = {
        "tag": "US_T2_TIME_ONLY",
        "state": "COMFORTABLE",
        "brief_token": "T2T",
    }
    time_only_index = render_sample_card(FRONTEND, time_only_card)["indexText"]
    assert_true("舒适窗 是" in time_only_index
                and "舒适窗 时间" not in time_only_index
                and "T2T" not in time_only_index
                and "US_T2_TIME_ONLY" not in time_only_index,
                "sidebar time-only comfort tag should collapse to yes/no conclusion")
    assert_true("[object Object]" not in durability_text
                and "[object Object]" not in durability_html,
                "durability UI should not leak object stringification")

    old_render = render_sample_card(
        FRONTEND, durability_sample_card(with_durability=False))
    old_text = old_render["text"]
    old_html = old_render["documentHtml"]
    old_index = old_render["indexText"]
    assert_true("signal-durability" in old_html,
                "old cards should still render a benign durability section")
    assert_true("旧卡兼容" in old_text and "未提供" in old_text,
                "old cards should explain missing durability as legacy compatible")
    assert_true("+0.09pp" in old_text
                and "中性保守" in old_text,
                "old cards should show front-end backfilled three-year temporal durability basis")
    assert_true("舒适窗 关闭" in old_index
                and "舒适窗 未提供" not in old_index,
                "old-card sidebar comfort status should use close/false wording")
    assert_true("[object Object]" not in old_text and "[object Object]" not in old_html,
                "old-card durability fallback should not stringify objects")

    advisory_render = render_sample_card(FRONTEND, advisory_sample_card())
    advisory_text = advisory_section_text(advisory_render)
    advisory_html = advisory_render["documentHtml"]
    assert_true(advisory_text, "integrated trade advisory should render for OK complete sidecar")
    assert_true(advisory_html.find('id="integrated-advisory"') != -1
                and advisory_html.find('id="gamma-overview"') != -1
                and advisory_html.find('id="integrated-advisory"') < advisory_html.find('id="gamma-overview"'),
                "integrated trade advisory should appear before Gamma/GEX in rendered HTML")
    forecast_summary_idx = advisory_html.find('class="future-24h-summary"')
    assert_true("未来 24 小时第一性推断" in advisory_html,
                "future 24h summary should have a visible reader-facing title")
    forecast_grid_idx = advisory_html.find('class="integrated-advisory-grid"')
    forecast_summary_html = advisory_html[
        forecast_summary_idx:forecast_grid_idx
        if forecast_summary_idx != -1 and forecast_grid_idx > forecast_summary_idx
        else forecast_summary_idx
    ]
    assert_true(advisory_html.find('class="integrated-advisory-head"') < forecast_summary_idx < forecast_grid_idx,
                "future 24h report should sit after the advisory head and before the structure grid")
    assert_true(forecast_summary_html.count("<p>") == 1
                and "<table" not in forecast_summary_html
                and "<ul" not in forecast_summary_html
                and "<dl" not in forecast_summary_html,
                "future 24h top report should be a single readable Chinese paragraph")
    for label in (
            "最高辅助交易决策",
            "允许进入卖出 Put 价差的结构复核",
            "复核卖出 Put 价差",
            "未来24小时贝叶斯观察",
            "中性接管",
            "卖方结构适配",
            "侧向依据",
            "主要冲突",
            "关键前提",
            "失效条件",
            "下一观察",
            "时段提醒",
            "+5.31pp",
            "只提醒，不改变信号方向或结构建议",
            "只读辅助，不是交易许可",
    ):
        assert_true(label in advisory_text,
                    "integrated advisory should show Chinese reader label/value: " + label)
    for trace_label in (
            "结构化权重",
            "点位来源",
            "验证信息",
            "模型估算",
            "卡内观测",
    ):
        assert_true(trace_label not in advisory_text,
                    "future 24h structured trace should stay out of the high advisory section: " + trace_label)
    for trace_label in (
            "未来24小时贝叶斯报告追溯",
            "结构化权重",
            "点位来源",
            "验证信息",
            "模型估算",
            "卡内观测",
            "上行情景",
            "卡内Gamma钉住观察位",
    ):
        assert_true(trace_label in advisory_render["text"],
                    "future 24h low trace should expose readable detail: " + trace_label)
    for raw_token in (
            "SELL_PUT_SPREAD_REVIEW",
            "ESTABLISHED",
            "FIT",
            "CAUTION",
            "HIGH",
            "ALIGNED",
            "recommendation",
            "audit_only",
            "trade_authorization",
            "source_alignment",
            "session_advisory",
            "policy_validation",
            "evidence_refs",
            "EV_DECISION",
            "EV_SIGNAL_DURABILITY",
    ):
        assert_true(raw_token not in advisory_text,
                    "integrated advisory and future 24h report should not expose raw enum/field code: " + raw_token)
    for raw_token in (
            "MODEL_ESTIMATED",
            "PACKET_OBSERVED",
            "EV_FORECAST_TMV",
    ):
        assert_true(raw_token not in advisory_render["text"],
                    "future 24h report should translate source kinds and hide evidence IDs: " + raw_token)
    assert_true("[object Object]" not in advisory_text and "[object Object]" not in advisory_html,
                "integrated advisory should not stringify objects")

    advisory_error_card = advisory_sample_card()
    advisory_error_card["llm_review"]["status"] = "ERROR"
    advisory_error_render = render_sample_card(FRONTEND, advisory_error_card)
    assert_true("最高辅助交易决策" not in advisory_error_render["text"],
                "ERROR llm_review should not render integrated advisory")
    assert_true("未来24小时贝叶斯观察" not in advisory_error_render["text"],
                "ERROR llm_review should not render future 24h report")
    assert_true('id="llm-review"' in advisory_error_render["documentHtml"],
                "ERROR llm_review should not remove the existing LLM section")

    advisory_failed_policy_card = advisory_sample_card()
    advisory_failed_policy_card["llm_review"]["integrated_trade_advisory"][
        "policy_validation"
    ]["passed"] = False
    advisory_failed_policy_render = render_sample_card(
        FRONTEND, advisory_failed_policy_card)
    assert_true('id="integrated-advisory"' not in
                advisory_failed_policy_render["documentHtml"],
                "failed local advisory policy must hide the integrated advisory")
    assert_true('id="llm-review"' in advisory_failed_policy_render["documentHtml"],
                "failed advisory policy must preserve the LLM audit section")

    advisory_missing_card = advisory_sample_card()
    advisory_missing_card["llm_review"]["integrated_trade_advisory"].pop("dominant_conflict_cn")
    advisory_missing_render = render_sample_card(FRONTEND, advisory_missing_card)
    assert_true("最高辅助交易决策" not in advisory_missing_render["text"],
                "missing advisory field should fail closed without rendering the block")
    assert_true('id="llm-review"' in advisory_missing_render["documentHtml"],
                "missing advisory field should not remove the existing LLM section")

    advisory_old_render = render_sample_card(FRONTEND, durability_sample_card())
    assert_true("最高辅助交易决策" not in advisory_old_render["text"],
                "old cards without integrated advisory should stay compatible")
    assert_true("未来24小时贝叶斯观察" not in advisory_old_render["text"]
                and "未来24小时贝叶斯报告追溯" not in advisory_old_render["text"],
                "old cards without future report should not render empty report blocks")
    assert_true('id="llm-review"' in advisory_old_render["documentHtml"],
                "old cards should still render the existing LLM section")

    incomplete_forecast_card = advisory_sample_card()
    incomplete_forecast_card["llm_review"]["integrated_trade_advisory"][
        "future_24h_bayesian_report"
    ].pop("policy_validation")
    incomplete_forecast_render = render_sample_card(FRONTEND, incomplete_forecast_card)
    assert_true("最高辅助交易决策" in incomplete_forecast_render["text"],
                "incomplete future report should not hide the parent advisory")
    assert_true("未来24小时贝叶斯观察" not in incomplete_forecast_render["text"]
                and "未来24小时贝叶斯报告追溯" not in incomplete_forecast_render["text"],
                "incomplete future report should fail closed without visible empty blocks")

    leaking_forecast_card = advisory_sample_card()
    leaking_forecast_card["llm_review"]["integrated_trade_advisory"][
        "future_24h_bayesian_report"
    ]["report_cn"] = "未来24小时 MODEL_ESTIMATED 依赖 EV_FORECAST_TMV。"
    leaking_forecast_render = render_sample_card(FRONTEND, leaking_forecast_card)
    assert_true("未来24小时 MODEL_ESTIMATED" not in leaking_forecast_render["text"]
                and "未来24小时贝叶斯报告追溯" not in leaking_forecast_render["text"],
                "future report with raw enum/evidence leakage should fail closed")

    rows = render_contract(FRONTEND)
    assert_true(rows, "render contract should cover cards")
    for row in rows:
        assert_true(not row["synthetic"], row["card_id"] + " should not be synthetic")
        assert_true(not row["objectObject"], row["card_id"] + " rendered [object Object]")
        assert_true(row["compactLedger"], row["card_id"] + " should render compact evidence ledger")
        assert_true(not row["oldWideTable"], row["card_id"] + " should not render old wide evidence table")
        assert_true(not row["redundantDecisionConclusion"],
                    row["card_id"] + " should not render the low-signal decision conclusion section")
        assert_true(not row["redundantDecisionMatrix"],
                    row["card_id"] + " should not render the low-signal decision matrix section")
        assert_true(not row["redundantContextWarnings"],
                    row["card_id"] + " should not render decision-matrix context warnings in the main page")
        assert_true(not row["redundantReasonCodes"],
                    row["card_id"] + " should not render decision-matrix reason codes in the main page")
        assert_true(row["gexRankSection"], row["card_id"] + " should keep GEX Rank percentile visible")
        assert_true(row["gammaOverviewSection"], row["card_id"] + " should keep Gamma/GEX highlights visible")
        assert_true(row["completeEvidenceLedger"], row["card_id"] + " should keep the complete evidence ledger visible")
        assert_true(row["factorCrossSection"], row["card_id"] + " should keep factor raw cross-section visible")
        assert_true(row["rawTraceJump"], row["card_id"] + " should keep raw trace jump navigation visible")
        assert_true(row["fundingSemanticContract"],
                    row["card_id"] + " should show the canonical funding semantic contract")
        assert_true(not row["legacyFundingRecompute"],
                    row["card_id"] + " should not recompute funding reflexivity in the frontend")
        assert_true(not row["fundingHumanMachineCodeLeak"],
                    row["card_id"] + " should localize funding machine codes in the human evidence card: "
                    + str(row["fundingMachineContexts"]))
        assert_true(not row["cvdHumanMachineCodeLeak"],
                    row["card_id"] + " should localize CVD machine codes in human evidence cards")
        assert_true(row["macroRawScore"], row["card_id"] + " should show raw macro score")
        assert_true(row["macroDirectionBackground"],
                    row["card_id"] + " should show MACRO direction background")
        assert_true(row["macroShockGate"],
                    row["card_id"] + " should show MACRO shock gate")
        assert_true(row["macroShockMissingOrState"],
                    row["card_id"] + " should not default missing macro shock to CLEAR/0")
        assert_true(row["llmSection"], row["card_id"] + " should always render the LLM review section")
        if not row["hasLlmReview"]:
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
        assert_true(not row["transitionLlmRawEnumLeaks"],
                    row["card_id"] + " transition LLM/core text leaked raw enum terms: "
                    + ", ".join(row["transitionLlmRawEnumLeaks"]))
        assert_true(not row["transitionObservedBoilerplate"],
                    row["card_id"] + " transition observed changes used vague materiality wording: "
                    + ", ".join(row["transitionObservedBoilerplate"]))
        assert_true(row["transitionObservedHasTendency"],
                    row["card_id"] + " transition observed changes should explain impact and tendency")

    transition_render = render_transition_contract(FRONTEND)
    full_transition_text = transition_render["text"]
    transition_html = transition_render["html"]
    core_start = transition_html.find("transition-core-summary")
    core_end = transition_html.find("transition-metadata", core_start)
    transition_core_html = (
        transition_html[core_start:core_end]
        if core_start != -1 and core_end > core_start
        else ""
    )
    assert_true('class="badge' not in transition_core_html,
                "core transition skeleton should not render materiality/grade badges")
    for label in (
            "决策结论",
            "封板决策矩阵",
            "Context warnings",
            "Reason codes",
            "SHOULD_NOT_RENDER_CONTEXT_WARNING",
            "SHOULD_NOT_RENDER_REASON_CODE",
    ):
        assert_true(label not in full_transition_text,
                    "decision and decision_matrix data should not render as low-signal main sections: " + label)
    start = full_transition_text.find("状态转移审计")
    end = full_transition_text.find("LLM 复核意见")
    transition_text = full_transition_text[start:end] if start != -1 and end != -1 else full_transition_text
    llm_pos = transition_text.find("LLM 变化链解释")
    raw_pos = full_transition_text.find("状态转移原始字段变化")
    edb_pos = full_transition_text.find("完整证据账本")
    assert_true(llm_pos != -1, "transition LLM explanation should use Chinese title")
    assert_true(raw_pos == -1,
                "low-signal raw transition changes should not render in the main page")
    assert_true(edb_pos != -1, "complete EDB ledger should remain visible")
    assert_true("状态转移原始字段变化" not in transition_text,
                "raw transition changes should not occupy the top transition board")
    metadata_pos = transition_text.find("审计元数据")
    assert_true(metadata_pos != -1,
                "machine audit fields should be available only inside audit metadata")
    primary_transition_text = transition_text[:metadata_pos]
    assert_true("previous_card_id" not in primary_transition_text
                and "current_card_id" not in primary_transition_text
                and "materiality_score" not in primary_transition_text
                and "llm_review_required" not in primary_transition_text,
                "machine audit fields should not be promoted in the top transition board")
    assert_true("TMV（量价路径）" in transition_text
                and "宏观（利率/美元/波动率）" in transition_text
                and "Gamma（净 Gamma）" in transition_text
                and "P/C（期权需求）" in transition_text,
                "top transition board should render the multi-domain semantic skeleton")
    assert_true("关键变化骨架 / Core transition" in transition_text,
                "top transition board should merge skeleton and domain summaries")
    assert_true("核心骨架" not in transition_text
                and "领域变化摘要" not in transition_text,
                "core skeleton and domain summaries should not render as separate top sections")
    assert_true("TMV（量价路径）" in transition_text
                and "Funding（期货资金费率）" in transition_text,
                "LLM observed changes should use bold Chinese semantic domain titles")
    assert_true('transition-observed-segment transition-observed-fact' in transition_html
                and 'transition-observed-segment transition-observed-impact' in transition_html
                and 'transition-observed-label">倾向' in transition_html,
                "LLM observed changes should render fact/impact/tendency as structured DOM segments")
    assert_true("；倾向：" not in transition_text and "； 倾向：" not in transition_text,
                "LLM observed changes should not prepend tendency with inline semicolon punctuation")
    assert_true("领域 (domain)" not in transition_text
                and "事实说明 (fact_cn)" not in transition_text
                and "材料性 (materiality)" not in transition_text,
                "LLM observed changes should not expose raw object field labels")
    assert_true("0.42 → 0.18" in transition_text
                and "2.29 → 2.18" in transition_text,
                "merged core transition should show key previous/current values")
    assert_true("0.00038% → -0.001438%" in transition_text,
                "core transition should use raw funding rate percent display")
    assert_true("-0.1359" not in transition_text,
                "core transition should not promote funding_norm as funding rate")
    assert_true("-0M" not in transition_text and "0M → 0M" not in transition_text,
                "core transition should not collapse Gamma values to zero million")
    assert_true("旧卡兼容推导" in transition_text,
                "historical Gamma metric should be labeled as compat-derived when not USD notional")
    assert_true("维持中性偏好，持续受宏观硬阻断" in transition_text,
                "transition LLM summary should localize raw decision/blocking enums")
    assert_true("状态: 正常 (OK)" not in primary_transition_text
                and "禁止交易指令: 有效 (VALID)" not in primary_transition_text
                and "轨迹状态 恶化 (DETERIORATING)" not in primary_transition_text
                and "连续性 已被阻断 (BLOCKED)" not in primary_transition_text,
                "transition LLM guard/status display should not append raw enum codes")
    assert_true("状态: 正常" in primary_transition_text
                and "禁止交易指令: 有效" in primary_transition_text
                and "轨迹状态 (trajectory_state) 恶化" in primary_transition_text
                and "连续性 (signal_continuity) 已被阻断" in primary_transition_text,
                "transition LLM guard/status display should remain Chinese-readable")
    assert_true("维持NEUTRAL" not in transition_text
                and "MACRO_BLOCKING" not in transition_text
                and "Headwind" not in transition_text,
                "transition main text should not leak raw enum wording")
    assert_true("正负符号翻转" not in transition_text,
                "P/C and other non-sign domains should not surface generic sign-flip wording")
    assert_true("source_ref" not in primary_transition_text
                and "factor_cross_section" not in primary_transition_text,
                "source/path details should stay out of the top transition board")
    raw_leak_render = render_transition_contract(FRONTEND, raw_leak_llm=True)
    raw_leak_text = raw_leak_render["text"]
    raw_leak_html = raw_leak_render["html"]
    assert_true("transition-llm is-degraded" not in raw_leak_html,
                "content-expression issues should not render as amber/red degraded")
    raw_start = raw_leak_text.find("LLM")
    raw_metadata = raw_leak_text.find("Core transition", raw_start)
    raw_primary = (
        raw_leak_text[raw_start:raw_metadata]
        if raw_start != -1 and raw_metadata > raw_start
        else raw_leak_text
    )
    for token in (
            "macro_pressure.components",
            "factor_cross_section",
            "source_ref",
            "primary_fields",
            "主要字段",
            "核心前后值已入包",
    ):
        assert_true(token not in raw_primary,
                    "raw field/path leakage should be masked in the transition reading flow: " + token)
    noisy_meta_render = render_transition_contract(FRONTEND, noisy_meta=True)
    noisy_meta_text = noisy_meta_render["text"]
    assert_true("方向作用: 未定" not in noisy_meta_text
                and "幅度判断: 不足判断" not in noisy_meta_text
                and "关注影响: 未定" not in noisy_meta_text,
                "uninformative transition meta chips should be hidden")
    for label in (
            "观察到的变化",
            "跨因子相互作用",
            "人工观察重点",
            "人工核验方案",
            "失效条件",
            "策略校验",
            "证据状态",
            "方向作用",
            "幅度判断",
            "关注影响",
            "轨迹状态",
            "连续性",
            "不含交易建议",
            "不使用外部数据",
            "区分观察与因果",
            "状态路径",
    ):
        assert_true(label in transition_text,
                    "transition UI should show Chinese semantic label: " + label)
    for label in (
            "最近 5 次轨迹",
            "24 小时基线",
            "同片段锚点",
            "上一值",
            "当前值",
            "变化量",
            "字段角色",
    ):
        assert_true(label not in full_transition_text,
                    "low-signal transition trace label should not render in the main page: " + label)
    for raw_label in (
            "Top material changes",
            "observed_changes",
            "cross_factor_interactions",
            "cross_factor_assessments",
            "operator_focus",
            "operator_checks",
            "policy_validation",
            "evidence_refs",
            "magnitude_verdict",
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

    suppressed_render = render_transition_contract(FRONTEND, suppress_llm=True)
    suppressed_text = suppressed_render["text"]
    suppressed_html = suppressed_render["html"]
    assert_true("transition-llm is-degraded is-hard-degraded" in suppressed_html,
                "suppressed/fatal transition LLM text should keep hard degraded styling")
    assert_true("LLM 变化链解释" in suppressed_text
                and "策略校验" in suppressed_text,
                "suppressed transition LLM card should retain audit status")
    assert_true("已降级隐藏" in suppressed_text,
                "suppressed transition LLM card should explain hidden model text")
    for label in (
            "SHOULD_NOT_RENDER_SUPPRESSED_SUMMARY",
            "SHOULD_NOT_RENDER_SUPPRESSED_CHANGE",
            "SHOULD_NOT_RENDER_SUPPRESSED_CROSS_FACTOR",
            "SHOULD_NOT_RENDER_SUPPRESSED_OPERATOR",
            "SHOULD_NOT_RENDER_SUPPRESSED_FOCUS",
            "SHOULD_NOT_RENDER_SUPPRESSED_INVALID_IF",
            "开仓",
            "观察到的变化",
            "人工核验方案",
    ):
        assert_true(label not in suppressed_text,
                    "suppressed transition LLM text should not render: " + label)

    unknown_render = render_transition_contract(FRONTEND, unknown_render_state=True)
    unknown_text = unknown_render["text"]
    assert_true("复核结果未通过当前客户端校验" in unknown_text,
                "unknown transition render_state should fail closed")
    for label in (
            "SHOULD_NOT_RENDER_UNKNOWN_RENDER_STATE",
            "SHOULD_NOT_RENDER_UNKNOWN_RENDER_STATE_CHANGE",
            "观察到的变化",
    ):
        assert_true(label not in unknown_text,
                    "unknown transition render_state should not render model text: " + label)

    legacy_render = render_transition_contract(FRONTEND, legacy_llm=True)
    legacy_text = legacy_render["text"]
    assert_true("未按当前策略验证" in legacy_text,
                "legacy transition LLM sidecar should be marked as not current-policy validated")
    assert_true("维持中性偏好，持续受宏观硬阻断" in legacy_text,
                "legacy transition LLM sidecar may still render readable audit text")

    print("signal_audit_frontend_render_contract: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_audit_frontend_render_contract: FAIL - " + str(exc))
        sys.exit(1)
