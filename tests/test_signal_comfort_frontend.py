import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "deploy" / "signal_audit" / "frontend"
AS_OF_MS = 1781751600000
FINAL_CANARY_CARD_NAMES = [
    "ASTRA-COMFORT-FINAL-REPLAY-20260906T064403+0800-BTC-nr_1788647373791_DOWN-9201.json",
    "20260907T115950+0800-BTC-nr_1788749787332_DOWN-e206.json",
]


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def side_rating(
    grade,
    *,
    model_grade=None,
    basis="结构和压力与该侧信用价差复核相容。",
    counter="主要反证已列入等待观察。",
    next_observation="继续观察价格是否保持在关键空间内。",
    unresolved=None,
    caps=None,
    refs=None,
    counter_refs=None,
    s_basis="比 A 多出的依据来自量价主干的非重复确认。",
    upgrade_refs=None,
):
    return {
        "status": "RATED" if grade else "UNRATED",
        "model_grade": model_grade or grade,
        "final_grade": grade,
        "basis_cn": basis,
        "counter_evidence_cn": counter,
        "next_observation_cn": next_observation,
        "unresolved_conditions_cn": list(unresolved or []),
        "cap_reasons_cn": list(caps or []),
        "evidence_refs": list(refs or ["EV_TMV"]),
        "counter_evidence_refs": list(counter_refs or ["EV_FLOW_CONFIRM"]),
        "s_upgrade_basis_cn": s_basis if grade == "S" else "",
        "s_upgrade_evidence_refs": list(upgrade_refs or ["EV_TMV"]) if grade == "S" else [],
    }


def comfort_ratings(
    *,
    put_grade="B",
    call_grade="S",
    headline_grade="S",
    focus_side="call_credit",
    action="优先准入：Call 侧先进入人工候选价差复核；仍需确认两腿、报价和退出条件。",
    put=None,
    call=None,
):
    return {
        "schema": "signal_comfort_ratings@1.0.0",
        "rating_scope": "signal_side_admission",
        "candidate_quote_economics": "not_evaluated",
        "as_of_ms": AS_OF_MS,
        "headline": {
            "final_grade": headline_grade,
            "focus_side": focus_side,
            "action_cn": action,
        },
        "put_credit": put or side_rating(
            put_grade,
            basis="下行侵入压力存在但仍有结构冲突，先作为启动关注。",
            counter="主动流仍可能反向扩大下行压力。",
            caps=["等待关键反证缓解。"] if put_grade == "B" else [],
            refs=["EV_GGR_SPATIAL", "EV_TMV"],
            counter_refs=["EV_FLOW_CONFIRM"],
        ),
        "call_credit": call or side_rating(
            call_grade,
            basis="上行侵入压力受限，结构与反向压力共同支持该侧复核。",
            counter="宏观冲击仍可能改变上方压力。",
            next_observation="观察价格是否继续低于上方空间约束，并确认主动流未重新转强。",
            refs=["EV_GGR_SPATIAL", "EV_FUNDING"],
            counter_refs=["EV_MACRO"],
            upgrade_refs=["EV_TMV"],
        ),
    }


def comfort_summary(comfort):
    return json.loads(json.dumps(comfort, ensure_ascii=False))


def rating_input(ref, group):
    return {"source_ref": ref, "source_group": group, "status": "OK", "usable": True}


def rating_support(ref, text):
    return {"source_ref": ref, "basis_cn": text}


def rating_opposition(ref, text):
    return {"source_ref": ref, "basis_cn": text}


def native_signal_rating():
    return {
        "schema": "signal_rating@1.0.0",
        "rating_scope": "side_environment_v1",
        "candidate_quote_economics": "not_evaluated",
        "as_of_ms": AS_OF_MS,
        "context": {
            "nr_state": "NR_REPAIR_CONFIRMED",
            "source_refs": ["EV_GGR_SPATIAL", "EV_TMV", "EV_FLOW_CONFIRM", "EV_FUNDING", "EV_MACRO"],
        },
        "claims": {
            "structure": {
                "status": "SUPPORTED",
                "summary_cn": "期权空间结构支持分侧信用价差复核。",
                "required_inputs": [rating_input("EV_GGR_SPATIAL", "GGR")],
                "support": [rating_support("EV_GGR_SPATIAL", "正 Gamma 与上方空间约束可作为结构背景。")],
                "opposition": [],
                "unknowns": [],
            },
            "put_pressure": {
                "status": "CONFLICTED",
                "summary_cn": "Put 侧既有空间支持，也有主动流反证。",
                "required_inputs": [rating_input("EV_TMV", "TMV"), rating_input("EV_FLOW_CONFIRM", "FLOW")],
                "support": [rating_support("EV_TMV", "量价主干仍给出下行压力背景。")],
                "opposition": [rating_opposition("EV_FLOW_CONFIRM", "主动流反证尚未解除。")],
                "unknowns": [],
            },
            "call_pressure": {
                "status": "SUPPORTED",
                "summary_cn": "Call 侧上行侵入压力受限，支持信号层复核。",
                "required_inputs": [rating_input("EV_GGR_SPATIAL", "GGR"), rating_input("EV_FUNDING", "FUNDING")],
                "support": [
                    rating_support("EV_GGR_SPATIAL", "上方空间约束仍可读。"),
                    rating_support("EV_FUNDING", "资金费率没有形成上行拥挤反证。"),
                ],
                "opposition": [],
                "unknowns": [],
            },
        },
    }


def integrated_advisory(comfort, *, recommendation="SELL_CALL_SPREAD_REVIEW"):
    return {
        "recommendation": recommendation,
        "final_conclusion_cn": "Call 侧可进入信号层人工复核，但候选两腿和净补偿尚未评估。",
        "cross_loop_rationale_cn": "结构、量价主干和资金费率合在一起支持分侧阅读，反证保留在下一观察。",
        "containment_assessment": {"state": "ESTABLISHED", "basis_cn": "当前边界允许人工复核，不改变源端执行权限。"},
        "premium_selling_fit": {"state": "CONDITIONAL", "basis_cn": "只评价信号层环境，具体权利金补偿稍后确认。"},
        "side_basis_cn": "源端侧别与本卡 Call 信用价差评级一致。",
        "dominant_conflict_cn": "主动流与宏观背景仍是主要反证来源。",
        "key_premises": ["价格仍在上方空间约束内。", "必需数据源保持可用。"],
        "invalid_if": ["价格突破上方空间约束。", "源端转入等待或阻断。"],
        "next_observation_cn": "等待下一轮主动流和宏观背景确认。",
        "session_advisory": {
            "liquidity_assessment": "CAUTION",
            "warning_level": "INFO",
            "basis_cn": "本卡仅用于人工准备，不授权下单。",
            "does_not_change_recommendation": True,
        },
        "source_alignment": "ALIGNED",
        "audit_only": True,
        "trade_authorization": False,
        "policy_validation": {"passed": True},
        "side_comfort_ratings": comfort,
    }


def base_card(
    card_id="COMFORT-AS",
    *,
    comfort=None,
    llm_status="OK",
    support_label="TRADE_SUPPORT_REVIEW",
    side_hint="CALL_CREDIT",
    recommendation="SELL_CALL_SPREAD_REVIEW",
    include_native=True,
    include_advisory=True,
    include_summary=True,
):
    content = {
        "summary_cn": "LLM 深入分析保留为只读审计，综合评级以本卡行动结论为准。",
        "main_supporting_factors": ["期权空间结构与量价主干共同提供可读约束。"],
        "main_risks_or_conflicts": ["宏观背景和主动买卖流仍需继续观察。"],
        "operator_focus": ["先看综合等级、侧别和下一观察条件。"],
        "invalid_if": ["若价格突破对应空间约束，本轮复核失效。"],
    }
    if comfort is not None and include_advisory:
        content["integrated_trade_advisory"] = integrated_advisory(comfort, recommendation=recommendation)
    elif comfort is not None:
        content["integrated_trade_advisory"] = {"side_comfort_ratings": comfort}
    card = {
        "schema": {"name": "signal_review_card", "version": "1.0.0", "status": "FINAL"},
        "identity": {
            "card_id": card_id,
            "short_id": card_id[-4:],
            "symbol": "BTC",
            "strategy_name": "Astra 舒适度测试",
            "strategy_version": "1.6.0",
            "confirmed_at": "2026-06-18T11:00:00+08:00",
        },
        "market_context": {"price": 100000, "quote_currency": "USDT"},
        "quality": {
            "overall": "OK",
            "all_required_sources_ready": True,
            "sources": {
                "tmvf": {"status": "OK", "age_ms": 15000},
                "micro_flow": {"status": "OK", "age_ms": 30000},
                "funding": {"status": "OK", "age_ms": 45000},
            },
            "missing_fields": [],
            "degraded_sources": [],
        },
        "decision": {
            "lean": "BEARISH_LEAN",
            "side_hint": side_hint,
            "support_label": support_label,
            "confidence": 73,
        },
        "decision_matrix": {
            "support_label": support_label,
            "side_hint": side_hint,
            "execution_allowed": False,
        },
        "blocking": {
            "has_block": False,
            "hard_veto": None,
            "soft_gates": [],
            "unblock_conditions": [{"condition_cn": "等待候选价差报价进入复核。", "threshold": 0.0}],
        },
        "factor_cross_section": {
            "gamma_regime": {
                "regime": "POSITIVE_GAMMA",
                "net_gamma_notional_usd": 12500000,
                "pin_strike": 100500,
                "distance_to_pin_pct": 0.0024,
                "call_wall": 103000,
                "put_wall": 97000,
                "observed_at": "2026-06-18T10:59:30+08:00",
            },
            "gex_info": {
                "market_state": "POSITIVE_GAMMA_PINNING",
                "net_gamma_notional_usd": 12500000,
                "distance_to_pin_pct": 0.0024,
                "call_wall": 103000,
                "put_wall": 97000,
            },
            "tmvf": {
                "direction": "Bullish",
                "tmv_blend": 0.12,
                "tmvf_24h_final": "上方空间仍受约束",
                "age_ms": 20000,
            },
            "micro_flow": {
                "combined_vote": -0.21,
                "agreement": "MIXED_UNCLEAR",
                "absorption_state": "SELL_CONFIRMS_DOWN",
            },
            "macro_pressure": {
                "macro_score": 0.08,
                "macro_regime": "NEUTRAL",
                "macro_shock": {"state": "CLEAR", "direction_confirmed": False},
            },
            "funding": {
                "last_rate": 0.00003,
                "canonical_funding_semantics": {
                    "canonical_text_cn": "资金费率温和，不构成独立方向。",
                    "crowding_state": "NOT_CROWDED",
                    "fee_bias_cn": "轻微多头付费",
                    "edb_participation": "NON_VOTING",
                },
            },
        },
        "reasoning": {
            "summary_cn": "证据摘要以中文保留关键因素，内部计算细节进入完整下载资料。",
            "evidence": [
                {
                    "key": "GGR_SPATIAL",
                    "gloss_cn": "Gamma 空间结构",
                    "participation_status": "ACTIVE",
                    "source_ref": "factor_cross_section.gamma_regime",
                    "raw_values": {"regime": "POSITIVE_GAMMA", "net_gamma_notional_usd": 12500000},
                },
                {
                    "key": "TMV",
                    "gloss_cn": "量价主干",
                    "participation_status": "ACTIVE",
                    "source_ref": "factor_cross_section.tmvf",
                    "raw_values": {"direction": "Bullish", "tmv_blend": 0.12},
                },
                {
                    "key": "FLOW_CONFIRM",
                    "gloss_cn": "主动流确认",
                    "participation_status": "ACTIVE",
                    "exclusion_reason": "CVD_STRENGTH_NOT_ACTIVE",
                    "source_ref": "factor_cross_section.micro_flow",
                    "raw_values": {"combined_vote": -0.21, "agreement": "MIXED_UNCLEAR"},
                },
                {
                    "key": "FUNDING",
                    "gloss_cn": "资金费率",
                    "participation_status": "NON_VOTING",
                    "source_ref": "factor_cross_section.funding",
                    "raw_values": {"last_rate": 0.00003},
                },
                {
                    "key": "MACRO",
                    "gloss_cn": "宏观背景",
                    "participation_status": "NON_VOTING",
                    "source_ref": "factor_cross_section.macro_pressure",
                    "raw_values": {"macro_score": 0.08, "macro_regime": "NEUTRAL"},
                },
            ],
        },
        "conflict": {"explanation_cn": "结构支持与主动流压力存在局部分歧，需要分侧阅读。"},
        "llm_review": {"status": llm_status, "content": content},
        "display_layers": {"headline": "Astra 舒适度测试卡"},
    }
    if include_native:
        card["signal_rating"] = native_signal_rating()
    if comfort is not None and include_summary:
        card["signal_comfort_summary"] = comfort_summary(comfort)
    return card


def fixture_valid_as():
    return base_card("COMFORT-AS", comfort=comfort_ratings())


def fixture_tie():
    comfort = comfort_ratings(
        put_grade="A",
        call_grade="A",
        headline_grade="A",
        focus_side="tie",
        action="信号层准入：两侧同为 A 级，当前没有单一优先侧。",
    )
    return base_card(
        "COMFORT-TIE",
        comfort=comfort,
        side_hint="BOTH_CREDIT",
        recommendation="NEUTRAL_SINGLE_SIDE_REVIEW",
    )


def fixture_unrated():
    comfort = comfort_ratings(
        put_grade=None,
        call_grade=None,
        headline_grade=None,
        focus_side="none",
        action="",
        put=side_rating(None, basis="暂未完成有效评级。", counter="必要来源缺失。"),
        call=side_rating(None, basis="暂未完成有效评级。", counter="必要来源缺失。"),
    )
    return base_card("COMFORT-UNRATED", comfort=comfort)


def fixture_old_card():
    return base_card("COMFORT-OLD", comfort=None, include_native=False)


def fixture_error_card():
    return base_card("COMFORT-ERROR", comfort=None, llm_status="ERROR", include_native=False)


def load_final_canary_cards():
    canary_dir = os.environ.get("ASTRA_COMFORT_FINAL_CANARY_DIR", "")
    assert_true(canary_dir, "set ASTRA_COMFORT_FINAL_CANARY_DIR to run root final canary checks")
    final_canary_dir = pathlib.Path(canary_dir)
    paths = [final_canary_dir / name for name in FINAL_CANARY_CARD_NAMES]
    missing = [str(path) for path in paths if not path.exists()]
    assert_true(not missing, "root final canary cards should exist: " + ", ".join(missing))
    return [json.loads(path.read_text(encoding="utf-8")) for path in paths]


def render_cards(cards, *, grade_filter=""):
    cards_path = None
    script = r"""
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const root = __ROOT__;
const cards = JSON.parse(fs.readFileSync(__CARDS_PATH__, "utf8"));
const gradeFilter = __GRADE_FILTER__;
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
      handlers: {},
      classList: { add() {}, remove() {}, toggle() {} },
      addEventListener(type, handler) { this.handlers[type] = handler; },
      insertAdjacentHTML(_where, html) { this.innerHTML += html; },
      focus() {},
      scrollIntoView() {}
    };
  }
  return elements[id];
}

const document = {
  body: { appendChild() {} },
  head: { appendChild() {} },
  documentElement: { classList: { add() {}, remove() {}, toggle() {} } },
  createElement(tag) {
    return {
      tagName: tag,
      href: "",
      download: "",
      remove() {},
      click() {},
      addEventListener() {},
      set src(_value) {},
    };
  },
  getElementById(id) {
    if (id === "signal-data") return { textContent: JSON.stringify(cards) };
    return element(id);
  },
  querySelector(selector) {
    return element(selector.startsWith("#") ? selector.slice(1) : selector);
  },
  querySelectorAll() { return []; }
};

const context = {
  window: {
    location: { protocol: "file:" },
    SIGNAL_CARD_FIXTURES: cards,
    URL: { createObjectURL() { return "blob:mock"; }, revokeObjectURL() {} },
    matchMedia() { return { matches: false, addEventListener() {}, removeEventListener() {} }; }
  },
  document,
  console,
  Intl,
  Map,
  Set,
  Promise,
  Blob,
  setTimeout,
  clearTimeout,
  fetch: () => Promise.reject(new Error("unexpected fetch"))
};

vm.createContext(context);
vm.runInContext(app, context);

setTimeout(() => {
  if (gradeFilter) {
    const filter = elements.gradeFilter;
    filter.value = gradeFilter;
    filter.handlers.change({ target: filter });
  }
  setTimeout(() => {
    const documentHtml = elements.documentView ? elements.documentView.innerHTML : "";
    const indexHtml = elements.indexList ? elements.indexList.innerHTML : "";
    const documentText = documentHtml.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
    const indexText = indexHtml.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
    const filterState = {
      directionHidden: !!(elements.directionFilterGroup && elements.directionFilterGroup.hidden),
      actionHidden: !!(elements.actionFilterGroup && elements.actionFilterGroup.hidden),
      qualityHidden: !!(elements.qualityFilterGroup && elements.qualityFilterGroup.hidden),
      directionValue: elements.directionFilter ? elements.directionFilter.value : "",
      actionValue: elements.actionFilter ? elements.actionFilter.value : "",
      qualityValue: elements.qualityFilter ? elements.qualityFilter.value : "",
      directionOptions: elements.directionFilter ? elements.directionFilter.innerHTML : "",
      actionOptions: elements.actionFilter ? elements.actionFilter.innerHTML : "",
      qualityOptions: elements.qualityFilter ? elements.qualityFilter.innerHTML : ""
    };
    process.stdout.write(JSON.stringify({ documentHtml, indexHtml, documentText, indexText, filterState }));
  }, 20);
}, 40);
"""
    script = script.replace("__ROOT__", json.dumps(str(FRONTEND)))
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
        json.dump(cards, handle, ensure_ascii=False)
        cards_path = pathlib.Path(handle.name)
    script = script.replace("__CARDS_PATH__", json.dumps(str(cards_path)))
    script = script.replace("__GRADE_FILTER__", json.dumps(grade_filter))
    try:
        result = subprocess.run(
            ["node", "-e", script],
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    finally:
        if cards_path:
            cards_path.unlink(missing_ok=True)
    assert_true(result.returncode == 0, result.stderr or result.stdout)
    return json.loads(result.stdout)


def render_and_download_card(cards, *, card_id=None):
    cards_path = None
    script = r"""
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const root = __ROOT__;
const cards = JSON.parse(fs.readFileSync(__CARDS_PATH__, "utf8"));
const targetCardId = __CARD_ID__;
const app = fs.readFileSync(path.join(root, "app.js"), "utf8");
const elements = {};
const downloads = { blobs: [], clicked: [], revoked: [], scheduled: [] };
let lastButtons = [];

function element(id) {
  if (!elements[id]) {
    elements[id] = {
      id,
      value: "",
      innerHTML: "",
      textContent: "",
      dataset: {},
      handlers: {},
      classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
      setAttribute() {},
      querySelector() { return null; },
      addEventListener(type, handler) { this.handlers[type] = handler; },
      insertAdjacentHTML(_where, html) { this.innerHTML += html; },
      focus() {},
      scrollIntoView() {}
    };
  }
  return elements[id];
}

function camelDatasetName(name) {
  return String(name || "").replace(/-([a-z])/g, (_m, ch) => ch.toUpperCase());
}

function parseButtons(selector) {
  if (selector !== ".card-retry") return [];
  const html = element("documentView").innerHTML || "";
  const matches = html.match(/<button\b[^>]*class="[^"]*\bcard-retry\b[^"]*"[^>]*>/g) || [];
  lastButtons = matches.map((tag) => {
    const button = element(`button-${lastButtons.length + Math.random()}`);
    button.dataset = {};
    tag.replace(/data-([a-z0-9-]+)="([^"]*)"/gi, (_m, key, value) => {
      button.dataset[camelDatasetName(key)] = value;
      return "";
    });
    return button;
  });
  return lastButtons;
}

const document = {
  body: { appendChild() {} },
  head: { appendChild() {} },
  documentElement: { classList: { add() {}, remove() {}, toggle() {} } },
  createElement(tag) {
    if (tag === "a") {
      return {
        tagName: tag,
        href: "",
        download: "",
        remove() {},
        click() { downloads.clicked.push({ href: this.href, download: this.download }); },
        addEventListener() {},
      };
    }
    return { tagName: tag, addEventListener() {}, remove() {}, set src(_value) {} };
  },
  getElementById(id) {
    if (id === "signal-data") return { textContent: JSON.stringify(cards) };
    return element(id);
  },
  querySelector(selector) {
    return element(selector.startsWith("#") ? selector.slice(1) : selector);
  },
  querySelectorAll(selector) {
    return parseButtons(selector);
  }
};

function controlledSetTimeout(fn, ms) {
  downloads.scheduled.push(ms);
  if (Number(ms) >= 1000) return { delayed: true, ms };
  return setTimeout(fn, ms);
}

const context = {
  window: {
    location: { protocol: "file:" },
    SIGNAL_CARD_FIXTURES: cards,
    URL: {
      createObjectURL(blob) { downloads.blobs.push(blob); return `blob:mock-${downloads.blobs.length}`; },
      revokeObjectURL(url) { downloads.revoked.push(url); }
    },
    matchMedia() { return { matches: false, addEventListener() {}, removeEventListener() {} }; }
  },
  document,
  console,
  Intl,
  Map,
  Set,
  Promise,
  Blob,
  setTimeout: controlledSetTimeout,
  clearTimeout,
  fetch: () => Promise.reject(new Error("unexpected fetch"))
};

vm.createContext(context);
vm.runInContext(app, context);

setTimeout(() => {
  const button = lastButtons.find((item) => item.dataset.downloadCardId === targetCardId)
    || lastButtons.find((item) => item.dataset.downloadCardId);
  if (!button || !button.handlers.click) {
    process.stdout.write(JSON.stringify({ error: "download button not wired", buttonCount: lastButtons.length }));
    return;
  }
  button.handlers.click({ target: button });
  const blob = downloads.blobs[0];
  if (!blob || typeof blob.text !== "function") {
    process.stdout.write(JSON.stringify({ error: "blob not captured", clicked: downloads.clicked }));
    return;
  }
  blob.text().then((blobText) => {
    process.stdout.write(JSON.stringify({
      clicked: downloads.clicked,
      revoked: downloads.revoked,
      scheduled: downloads.scheduled,
      blobText,
    }));
  }).catch((error) => {
    process.stdout.write(JSON.stringify({ error: error.message }));
  });
}, 60);
"""
    script = script.replace("__ROOT__", json.dumps(str(FRONTEND)))
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
        json.dump(cards, handle, ensure_ascii=False)
        cards_path = pathlib.Path(handle.name)
    script = script.replace("__CARDS_PATH__", json.dumps(str(cards_path)))
    script = script.replace("__CARD_ID__", json.dumps(card_id or cards[0]["identity"]["card_id"]))
    try:
        result = subprocess.run(
            ["node", "-e", script],
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    finally:
        if cards_path:
            cards_path.unlink(missing_ok=True)
    assert_true(result.returncode == 0, result.stderr or result.stdout)
    payload = json.loads(result.stdout)
    assert_true("error" not in payload, payload.get("error", "download failed"))
    return payload


def assert_no_machine_leak(rendered, *, context):
    text = " ".join([rendered.get("documentText", ""), rendered.get("indexText", "")])
    html = " ".join([rendered.get("documentHtml", ""), rendered.get("indexHtml", "")])
    for token in (
        "factor_cross_section",
        "source_ref",
        "signal_rating@",
        "signal_comfort_ratings@",
        "model_grade",
        "final_grade",
        "candidate_quote_economics",
        "WAIT_CONFIRMATION",
        "NO_TRADE_BLOCKED",
        "TRADE_SUPPORT",
        "confidence",
        "durability",
        "weighted",
        "weight",
        "source_path",
        "schema_name",
        "CLEAR",
        "Bullish",
        "BULLISH",
        "BULLISH_STRONG",
        "TRANSITION",
        "bullish_bias",
        "gamma_regime",
        "gex_info",
        "net_gamma_notional",
        "net_gex_sign",
        "EDB",
        "对应来源",
        "完整机器字段",
        "机器细节",
        "内部冲突比例",
        "综合舒适度来自",
        "本地校验",
        "单卡 JSON",
        "索引摘要",
        "空间调制",
        "SEVERE",
        "计票",
        "门控",
        "源端链路",
        "页面或 LLM",
        "[object Object]",
    ):
        assert_true(token not in text and token not in html,
                    context + " should not leak machine token: " + token)


def test_comfort_reader_valid_as():
    rendered = render_cards([fixture_valid_as()])
    text = rendered["documentText"]
    html = rendered["documentHtml"]
    assert_true("S级｜Call 信用价差" in text, "top action should use validated headline final grade and side")
    assert_true("Put 信用价差" in text and "Call 信用价差" in text, "reader should compare both sides")
    assert_true("S级额外依据" in text, "S grade should show extra basis beyond A")
    assert_true("候选两腿、报价、费用、净补偿与退出条件仍在候选交易层确认" in text,
                "signal admission must not approve a specific spread")
    assert_true("中文市场证据" in text and "期权空间结构" in text and "主动买卖流" in text,
                "market facts should remain available in Chinese")
    assert_true("12.5M USD" in text, "net Gamma notional should include USD unit")
    assert_true("无冲击阻断" in text and "偏多" in text, "mixed-case market states should be translated")
    assert_true("下载完整审计资料" in text, "full audit data should be available through a plain download entry")
    assert_true(text.find("本卡行动结论") < text.find("市场价格") < text.find("中文市场证据"),
                "action conclusion should appear before metrics and market evidence")
    assert_true("#market-options-structure" in html and "#market-active-flow" in html,
                "source links should point to Chinese market fact anchors")
    assert_no_machine_leak(rendered, context="valid A/S card")



def test_download_button_writes_full_source_card_blob():
    card = fixture_valid_as()
    payload = render_and_download_card([card], card_id=card["identity"]["card_id"])
    assert_true(payload["clicked"] and payload["clicked"][0]["download"] == f"{card['identity']['card_id']}.json",
                "download click should use the selected card filename")
    assert_true(payload["clicked"][0]["href"].startswith("blob:mock-"),
                "download should use a blob URL")
    assert_true(payload["revoked"] == [],
                "blob URL must not be revoked synchronously before the browser starts the download")
    assert_true(any(int(ms) >= 1000 for ms in payload["scheduled"]),
                "blob URL should be scheduled for delayed cleanup")
    downloaded = json.loads(payload["blobText"])
    assert_true(downloaded["identity"]["card_id"] == card["identity"]["card_id"],
                "downloaded blob should contain the full selected source card")
    assert_true("llm_review" in downloaded and "integrated_trade_advisory" in downloaded["llm_review"]["content"],
                "downloaded blob should preserve LLM review content")
    assert_true("signal_rating" in downloaded and "call_pressure" in downloaded["signal_rating"]["claims"],
                "downloaded blob should preserve native producer rating")
    assert_true("factor_cross_section" in downloaded and "gamma_regime" in downloaded["factor_cross_section"],
                "downloaded blob should preserve market source facts")


def test_comfort_headline_is_not_recomputed_from_sides():
    bad = comfort_ratings(put_grade="B", call_grade="S", headline_grade="B", focus_side="put_credit")
    rendered = render_cards([base_card("COMFORT-MISMATCH", comfort=bad)])
    text = rendered["documentText"]
    assert_true("暂未完成有效评级" in text, "mismatched headline should invalidate the card rating")
    assert_true("S级｜Call 信用价差" not in text, "frontend must not recompute headline from the better side")
    assert_true("中文市场证据" in text, "invalid rating should still keep market facts")
    assert_no_machine_leak(rendered, context="mismatched comfort card")


def test_detail_cannot_upgrade_unrated_manifest_summary():
    card = fixture_valid_as()
    card["signal_comfort_summary"] = {
        "headline": {"final_grade": None, "focus_side": "none", "action_cn": ""},
        "put_credit": {"final_grade": None},
        "call_credit": {"final_grade": None},
    }
    rendered = render_cards([card])
    text = rendered["documentText"]
    assert_true("暂未完成有效评级" in text,
                "detail rating should be invalid when manifest summary did not provide a usable final grade")
    assert_true("S级｜Call 信用价差" not in text,
                "detail must not upgrade over an unrated list summary")
    assert_true("中文市场证据" in text,
                "summary/detail mismatch should preserve market facts")


def test_full_comfort_requires_published_summary():
    card = fixture_valid_as()
    card.pop("signal_comfort_summary")
    rendered = render_cards([card])
    text = rendered["documentText"]
    assert_true("暂未完成有效评级" in text, "new D-S detail grade must require the published summary")
    assert_true("S级｜Call 信用价差" not in text, "full comfort must not upgrade without published summary")
    assert_true("中文市场证据" in text, "invalid rating should still keep market facts")


def test_wait_source_boundary_cannot_show_admission():
    card = base_card(
        "COMFORT-WAIT-A",
        comfort=comfort_ratings(call_grade="A", headline_grade="A"),
        support_label="WAIT_CONFIRMATION",
    )
    rendered = render_cards([card])
    text = rendered["documentText"]
    assert_true("暂未完成有效评级" in text, "WAIT plus LLM A/S must be rejected in the reader")
    assert_true("A级｜Call 信用价差" not in text and "S级｜Call 信用价差" not in text,
                "source WAIT must not imply admission")
    assert_true("中文市场证据" in text, "WAIT rejection should not hide market facts")


def test_summary_missing_with_full_advisory_cannot_show_admission():
    card = base_card("COMFORT-NO-SUMMARY", comfort=comfort_ratings(), include_summary=False)
    rendered = render_cards([card])
    text = rendered["documentText"]
    assert_true("暂未完成有效评级" in text, "detail comfort must not be accepted when the published summary is absent")
    assert_true("S级｜Call 信用价差" not in text, "full advisory must not display admission without published summary")


def test_frontend_does_not_rescore_published_grade_from_local_side_quality_or_native_status():
    card = fixture_valid_as()
    card["quality"]["all_required_sources_ready"] = False
    card["quality"]["missing_fields"] = ["SHOULD_STAY_IN_DOWNLOAD_ONLY"]
    card["decision"]["side_hint"] = "PUT_CREDIT"
    card["decision_matrix"]["side_hint"] = "PUT_CREDIT"
    card["llm_review"]["content"]["integrated_trade_advisory"]["recommendation"] = "SELL_PUT_SPREAD_REVIEW"
    card["signal_rating"]["claims"]["call_pressure"]["status"] = "OPPOSED"
    rendered = render_cards([card])
    text = rendered["documentText"]
    assert_true("S级｜Call 信用价差" in text,
                "frontend should follow the published validated summary instead of rebuilding side/quality/native gates")
    assert_true("暂未完成有效评级" not in text,
                "local frontend-only gates must not invalidate a published matching comfort summary")
    assert_no_machine_leak(rendered, context="published comfort no frontend rescoring")


def test_comfort_refs_must_resolve_and_not_use_future():
    bad_call = side_rating(
        "A",
        refs=["future_24h_bayesian_report"],
        counter_refs=["EV_MACRO"],
        basis="错误引用未来报告，不能通过本卡评级。",
    )
    card = base_card(
        "COMFORT-FUTURE-REF",
        comfort=comfort_ratings(call_grade="A", headline_grade="A", call=bad_call),
    )
    rendered = render_cards([card])
    text = rendered["documentText"]
    assert_true("暂未完成有效评级" in text, "future or unresolved refs must invalidate A/S detail grade")
    assert_true("A级｜Call 信用价差" not in text, "future refs must not be usable for current admission")


def test_net_gamma_prefers_gex_usd_over_gamma_proxy():
    card = fixture_valid_as()
    card["factor_cross_section"]["gamma_regime"]["net_gamma_notional_usd"] = -0.095645
    card["factor_cross_section"]["gamma_regime"]["net_gamma_notional"] = -0.095645
    card["factor_cross_section"]["gex_info"]["net_gamma_notional_usd"] = 256115816.82
    rendered = render_cards([card])
    text = rendered["documentText"]
    assert_true("256.1M USD" in text, "market facts should prefer GEX USD net Gamma notional")
    assert_true("-0 USD" not in text, "small GGR proxy must not be formatted as USD notional")
    assert_no_machine_leak(rendered, context="net gamma priority")


def test_capped_b_does_not_read_as_current_admission():
    call = side_rating(
        "B",
        model_grade="A",
        basis="结构符合信号层准入，但源端等待一项确认。",
        caps=["源端等待确认，最高只到 B。"],
        refs=["EV_GGR_SPATIAL", "EV_FUNDING"],
        counter_refs=["EV_MACRO"],
    )
    comfort = comfort_ratings(
        put_grade="C",
        call_grade="B",
        headline_grade="B",
        focus_side="call_credit",
        action="信号层准入：Call 侧可进入人工交易准备。",
        call=call,
    )
    rendered = render_cards([base_card("COMFORT-CAP-B", comfort=comfort)])
    text = rendered["documentText"]
    assert_true("B级｜Call 信用价差" in text, "capped card should keep final B grade")
    assert_true("只启动关注" in text and "局部支持依据" in text,
                "admission language under final B must be framed as local support only")
    assert_true("可以启动人工交易准备" not in text,
                "capped B must not keep current-admission action wording")
    assert_no_machine_leak(rendered, context="capped B wording")


def test_reader_sanitizes_quality_note_durability_score_boolean_and_old_coefficients():
    card = fixture_valid_as()
    content = card["llm_review"]["content"]
    content["data_quality_note"] = "confidence 0.1234567890123456 未校准；true/false 仅为机器布尔。"
    content["summary_cn"] = "总耐用性评分为69，价格锚、flip 与时效事实仍需保留。"
    content["integrated_trade_advisory"]["future_24h_bayesian_report"] = {
        "schema_version": "future_24h_bayesian_report@1.0.0",
        "horizon_hours": 24,
        "input_scope": "PACKET_FACTS_PLUS_MODEL_PRIOR_NO_LIVE_SEARCH",
        "live_external_data_used": False,
        "base_case": "RANGE",
        "report_cn": "基准情景为窄幅震荡，分布为区间40%，上行35%，下行25%。距上方看涨墙约2.81%，距下方看跌墙约0.33%。",
        "posterior_weights_pct": {"up": 35, "down": 25, "range": 40},
        "key_levels": [],
        "policy_validation": {"passed": True, "audit_only": True},
    }
    card["conflict"]["explanation_cn"] = "期权偏斜与主方向相悖，分歧等级 SEVERE"
    card["factor_cross_section"]["tmvf"]["window_conflict"] = False
    for evidence in card["reasoning"]["evidence"]:
        if evidence.get("key") == "GGR_SPATIAL":
            evidence["raw_values"]["confidence_multiplier"] = 0.98
    rendered = render_cards([card])
    combined = " ".join([
        rendered["documentText"],
        rendered["indexText"],
        rendered["documentHtml"],
        rendered["indexHtml"],
    ])
    assert_true("旧置信" in combined and "0.1235" in combined,
                "data quality note should be readable and long decimals should be compact")
    for token in ("confidence", "true", "false", "总耐用性评分", "空间调制", "SEVERE", "分布为区间", "上行35%", "下行25%", "模型主观情景权重"):
        assert_true(token not in combined, "reader should not leak canary token: " + token)
    assert_true("严重分歧" in combined, "SEVERE conflict level should be localized")
    assert_true("2.81%" in combined and "0.33%" in combined,
                "market observation percentages should remain visible after scenario allocation is removed")
    assert_true("基准情景为窄幅震荡，多情景细节为" not in combined,
                "future 24h summary should not leave an empty scenario-allocation sentence")
    assert_true("窗口冲突 否" in rendered["documentText"],
                "boolean window conflict should render as Chinese")
    assert_true("价格锚" in rendered["documentText"] and "flip" in rendered["documentText"] and "时效" in rendered["documentText"],
                "old total score removal should preserve useful anchor/flip/timeliness facts")


def test_unrated_side_keeps_basis_as_observation_and_filters_candidate_economics_gap():
    put = side_rating(
        None,
        basis="模型保留观察：结构来源不足，不能形成等级。",
        counter="候选经济性未评估 not_evaluated。",
        unresolved=["候选经济性未评估 not_evaluated", "实际可执行性未评估，周末薄流动性需要复核。", "价格锚还需要复核。"],
        refs=[],
        counter_refs=[],
    )
    call = side_rating(
        "B",
        basis="Call 侧只启动关注。",
        counter="宏观背景仍需观察。",
        unresolved=["未评估任何候选结构的经济性与风险，不构成评级依据。"],
        refs=["EV_GGR_SPATIAL"],
        counter_refs=["EV_MACRO"],
    )
    comfort = comfort_ratings(
        put_grade=None,
        call_grade="B",
        headline_grade="B",
        focus_side="call_credit",
        action="启动关注：Call 侧进入人工观察。",
        put=put,
        call=call,
    )
    rendered = render_cards([base_card("COMFORT-UNRATED-SIDE", comfort=comfort)])
    text = rendered["documentText"]
    assert_true("模型尚未给出有效等级，以下为保留观察" in text,
                "UNRATED side basis should be framed as retained observation")
    assert_true("支持理由：模型保留观察" not in text,
                "UNRATED side must not label model basis as support")
    assert_true("候选经济性未评估" not in text and "not_evaluated" not in text
                and "未评估任何候选结构的经济性与风险" not in text
                and "实际可执行性未评估" not in text,
                "candidate economics unresolved item should not appear as why-grade-stopped")
    assert_true("主要反对 尚无可用反对说明" in text or "尚无可用反对说明" in text,
                "candidate quote boundary should not be presented as counter-evidence")
    assert_true("执行层仍需复核薄流动性与成交风险" in text,
                "thin-liquidity execution risk should remain visible without becoming a quote gate")
    assert_true("具体报价与补偿在交易准备时确认，不是信号评级的必要输入" in text,
                "reader should state candidate economics belongs to later trade preparation")
    assert_true("Put 信用价差：价格锚还需要复核" in text,
                "real unresolved evidence gap should remain visible")
    assert_no_machine_leak(rendered, context="unrated side observation")

def test_anchor_source_link_opens_readable_anchor_facts():
    card = fixture_valid_as()
    card["factor_cross_section"]["anchor"] = {
        "effective_flip_point": 79775.28489,
        "band_half": 280.40723,
        "freshness": "FRESH",
        "ready": True,
        "score": 91,
        "gravity_score": 0.99,
    }
    card["llm_review"]["content"]["integrated_trade_advisory"]["side_comfort_ratings"]["call_credit"]["evidence_refs"] = ["EV_ANCHOR"]
    card["signal_comfort_summary"]["call_credit"]["evidence_refs"] = ["EV_ANCHOR"]
    rendered = render_cards([card])
    combined = rendered["documentText"] + " " + rendered["documentHtml"]
    assert_true("价格锚有效翻转点" in combined and "79,775.28" in combined,
                "options structure card should expose the anchor effective flip")
    assert_true("锚带半宽" in combined and "280.41" in combined,
                "options structure card should expose the anchor band half")
    assert_true("锚新鲜度" in combined and "新鲜" in combined and "锚可用" in combined and "是" in combined,
                "options structure card should expose anchor freshness and readiness")
    assert_true('href="#market-options-structure"' in rendered["documentHtml"] and ">价格锚</a>" in rendered["documentHtml"],
                "EV_ANCHOR source link should be named price anchor and jump to the structure card")
    assert_true("gravity_score" not in combined and "锚原生分" not in combined,
                "anchor source card should not expose score/gravity internals")
    assert_no_machine_leak(rendered, context="anchor facts")

def test_root_final_canary_cards_have_no_reader_text_leaks():
    for card in load_final_canary_cards():
        rendered = render_cards([card])
        combined = " ".join([
            rendered["documentText"],
            rendered["indexText"],
            rendered["documentHtml"],
            rendered["indexHtml"],
        ])
        for token in (
            "confidence",
            "true",
            "false",
            "总耐用性评分",
            "空间调制",
            "SEVERE",
            "分布为区间",
            "上行35%",
            "下行25%",
            "模型主观情景权重",
            "完整机器字段",
            "机器细节",
            "单卡 JSON",
            "索引摘要",
            "404",
        ):
            assert_true(token not in combined,
                        card["identity"]["card_id"] + " should not leak canary token: " + token)
        assert_true(not re.search(r"总耐用.{0,12}\d", combined),
                    card["identity"]["card_id"] + " should not expose old durability total score")
        assert_true(not re.search(r"-?\d+\.\d{9,}", combined),
                    card["identity"]["card_id"] + " should not expose long raw decimal scalars")
        assert_true(not re.search(r"来源质量\s*(?:1|0\.8)\b", combined),
                    card["identity"]["card_id"] + " should not expose internal source quality scalar")
        if "9201" in card["identity"]["card_id"]:
            assert_true("2.81%" in combined and "0.33%" in combined,
                        "9201 should retain market observation percentages while hiding scenario weights")


def test_index_and_mobile_shell_are_reader_safe():
    card = fixture_valid_as()
    card["decision"]["lean"] = "BULLISH_STRONG"
    card["identity"]["event_type"] = "TRANSITION"
    card["identity"]["card_id"] = "ASTRA-COMFORT-FINAL-REPLAY-20260906T064403+0800-BTC-nr_1788647373791_DOWN-9201"
    card["identity"]["short_id"] = "ACF-9201"
    rendered = render_cards([card])
    assert_true("强偏多" in rendered["indexText"] and "状态转移" in rendered["indexText"],
                "index should translate machine enums")
    assert_true("#ACF-9201" not in rendered["indexText"] and "#9201" not in rendered["indexText"],
                "visible index text should not show short ids")
    assert_no_machine_leak(rendered, context="index reader text")
    shell = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert_true("mobileIndexToggle" in shell and "切换信号/筛选" in shell,
                "mobile shell should expose a collapsed index toggle")
    assert_true("sidebar.is-open" in shell and "max-width: 980px" in shell,
                "mobile CSS should default to collapsed sidebar and preserve desktop sidebar")
    assert_true(".metric-strip" in shell and "repeat(2, minmax(0, 1fr))" in shell,
                "mobile metrics should remain compact two-column blocks")


def test_legacy_comfort_cards_remain_readable_but_do_not_enter_v2_grade_filters():
    cards = [
        fixture_valid_as(),
        fixture_tie(),
        base_card("COMFORT-C", comfort=comfort_ratings(
            put_grade="C",
            call_grade="C",
            headline_grade="C",
            focus_side="tie",
            action="普通观察：本轮没有主动跟踪价值。",
        )),
        fixture_old_card(),
    ]
    all_rendered = render_cards(cards)
    attention = render_cards(cards, grade_filter="attention")
    admission = render_cards(cards, grade_filter="admission")
    assert_true("C级" in all_rendered["indexText"] and "历史版本未综合评级" in all_rendered["indexText"],
                "all index should keep C and old cards visible")
    assert_true(
        all(card_id not in attention["indexHtml"] for card_id in ("COMFORT-AS", "COMFORT-TIE", "COMFORT-C", "COMFORT-OLD")),
        "B+ filter should exclude legacy comfort cards from the v2 evidence filter",
    )
    assert_true(
        all(card_id not in admission["indexHtml"] for card_id in ("COMFORT-AS", "COMFORT-TIE", "COMFORT-C", "COMFORT-OLD")),
        "A/S filter should exclude legacy comfort cards from the v2 evidence filter",
    )


def test_old_unrated_and_error_cards_do_not_become_d():
    for card in (fixture_unrated(), fixture_old_card(), fixture_error_card()):
        rendered = render_cards([card])
        text = rendered["documentText"]
        assert_true("D级" not in text, "unrated, old, or error cards must not be shown as D")
        assert_true("历史版本未综合评级" in text or "暂未完成有效评级" in text,
                    "unrated, old, or error cards should explain unavailable rating")
        if card["identity"]["card_id"] == "COMFORT-ERROR":
            assert_true("暂未完成有效评级" in rendered["indexText"] and "历史版本未综合评级" not in text,
                        "review ERROR without comfort should not be treated as a historical card")
        assert_true("评级时点 待加载" not in text,
                    "old or unrated cards should not imply the rating is still loading")
        assert_no_machine_leak(rendered, context=card["identity"]["card_id"])


def main():
    test_comfort_reader_valid_as()
    test_download_button_writes_full_source_card_blob()
    test_comfort_headline_is_not_recomputed_from_sides()
    test_detail_cannot_upgrade_unrated_manifest_summary()
    test_full_comfort_requires_published_summary()
    test_wait_source_boundary_cannot_show_admission()
    test_summary_missing_with_full_advisory_cannot_show_admission()
    test_frontend_does_not_rescore_published_grade_from_local_side_quality_or_native_status()
    test_comfort_refs_must_resolve_and_not_use_future()
    test_net_gamma_prefers_gex_usd_over_gamma_proxy()
    test_capped_b_does_not_read_as_current_admission()
    test_reader_sanitizes_quality_note_durability_score_boolean_and_old_coefficients()
    test_unrated_side_keeps_basis_as_observation_and_filters_candidate_economics_gap()
    test_anchor_source_link_opens_readable_anchor_facts()
    if os.environ.get("ASTRA_COMFORT_FINAL_CANARY_DIR"):
        test_root_final_canary_cards_have_no_reader_text_leaks()
    test_index_and_mobile_shell_are_reader_safe()
    test_legacy_comfort_cards_remain_readable_but_do_not_enter_v2_grade_filters()
    test_old_unrated_and_error_cards_do_not_become_d()
    print("signal_comfort_frontend: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_comfort_frontend: FAIL - " + str(exc))
        sys.exit(1)
