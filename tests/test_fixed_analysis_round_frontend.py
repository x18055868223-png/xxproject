import json
import pathlib
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "deploy" / "signal_audit" / "frontend"


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def sample_card(fixed=False):
    card = {
        "schema": {"name": "signal_review_card", "version": "1.0.0"},
        "identity": {
            "card_id": "FIXED-FRONTEND-CARD" if fixed else "NORMAL-FRONTEND-CARD",
            "short_id": "FIX" if fixed else "NRM",
            "symbol": "BTC",
            "strategy_name": "中性回路信号层",
            "strategy_version": "1.5.7",
            "confirmed_at": "2026-07-24T23:00:15+08:00",
        },
        "market_context": {"price": 65500.0, "quote_currency": "USDT"},
        "quality": {"overall": "OK", "all_required_sources_ready": True},
        "decision": {
            "lean": "NEUTRAL",
            "support_label": "WAIT_CONFIRMATION",
            "confidence": 39,
            "evidence_strength": 49,
        },
        "conflict": {"ratio": 0.342, "level": "MILD"},
        "reasoning": {"evidence": []},
        "factor_cross_section": {
            "tmvf": {"tmv_blend": -0.46, "direction": "Bearish"},
            "funding": {
                "canonical_funding_semantics": {
                    "canonical_text_cn": "资金费率处于温和区间，不构成拥挤。",
                    "raw_funding_rate_pct": -0.0004,
                    "crowding_threshold_pct": 0.01,
                    "crowding_state": "NOT_CROWDED",
                    "reflexivity_importance": "NOISE",
                    "edb_participation": "NON_VOTING",
                },
            },
        },
        "signal_durability": {
            "audit_scope": "AUDIT_ONLY",
            "headline_score": 73,
            "headline_state": "STABLE",
            "comfort_window": {"state": "NORMAL"},
        },
    }
    if fixed:
        card["identity"]["tags"] = ["FIXED_ROUND_ANALYSIS"]
        card["analysis_round"] = {
            "schema_name": "SignalFixedAnalysisRound",
            "schema_version": "1.0.0",
            "label_cn": "固定轮次分析",
            "trigger_clock": "Asia/Shanghai 23:00",
            "scheduled_time_utc8": "2026-07-24T23:00:00+08:00",
            "snapshot_collected_time_utc8": "2026-07-24T23:00:15+08:00",
            "ny_time": "2026-07-24T11:00:15-04:00",
            "dst_mode": "EDT",
            "bypassed_gate": "DIE_ANCHOR_TRIGGER_ONLY",
            "trigger_policy": "BYPASS_ANCHOR_DIE_FOR_FIXED_ANALYSIS",
            "does_not_override_producer_decision": True,
            "audit_only": True,
        }
        card["transition_context"] = {
            "audit_scope": "AUDIT_ONLY",
            "transition_id": "fixed-transition",
            "previous_card_id": "PREV-CARD",
            "current_card_id": "FIXED-FRONTEND-CARD",
            "elapsed_ms": 86400000,
            "comparison_quality": "HIGH",
            "cross_domain_flags": ["FIXED_ROUND_ANALYSIS"],
            "decision_transition": {
                "lean_before": "BEARISH_WEAK",
                "lean_after": "NEUTRAL",
                "confidence_before": 41,
                "confidence_after": 39,
            },
            "core_skeleton": {
                "timeline": {
                    "previous_short_id": "PRV",
                    "current_short_id": "FIX",
                    "previous_ts_ms": 1784886000000,
                    "current_ts_ms": 1784972400000,
                }
            },
        }
    return card


def render_card(card):
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
  const documentText = documentHtml.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");
  const indexText = indexHtml.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");
  process.stdout.write(JSON.stringify({ documentHtml, indexHtml, documentText, indexText }));
}, 20);
"""
    script = (
        script
        .replace("__ROOT__", json.dumps(str(FRONTEND)))
        .replace("__CARD__", json.dumps(card, ensure_ascii=False))
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
    fixed = render_card(sample_card(fixed=True))
    normal = render_card(sample_card(fixed=False))

    assert_true("固定轮次分析" in fixed["documentText"],
                "detail header should show fixed-round Chinese badge")
    assert_true("固定轮次" in fixed["indexText"],
                "sidebar index should show short fixed-round label")
    assert_true("固定时间截面差分" in fixed["documentText"],
                "transition reading flow should identify fixed-time snapshot diff")
    assert_true("北京时间 23:00" in fixed["documentText"],
                "fixed-round transition note should mention the 23:00 BJT snapshot")

    for raw in (
            "FIXED_ROUND_ANALYSIS",
            "DIE_ANCHOR_TRIGGER_ONLY",
            "BYPASS_ANCHOR_DIE_FOR_FIXED_ANALYSIS",
            "trigger_policy",
    ):
        assert_true(raw not in fixed["documentText"],
                    "fixed-round machine field/code should not enter the main reading text: " + raw)

    assert_true(fixed["documentText"].count("最高辅助交易决策") <= 1,
                "fixed-round tag must not create a second integrated advisory block")
    assert_true("固定轮次分析" not in normal["documentText"],
                "old/normal cards without analysis_round or tags should not show fixed detail badge")
    assert_true("固定轮次" not in normal["indexText"],
                "old/normal cards without analysis_round or tags should not show fixed index label")

    index = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert_true(".fixed-round-badge" in index,
                "fixed-round badge should have mobile-safe wrapping style")

    print("fixed_analysis_round_frontend: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("fixed_analysis_round_frontend: FAIL - " + str(exc))
        sys.exit(1)
