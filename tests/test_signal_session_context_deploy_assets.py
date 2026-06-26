import json
import pathlib
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEPLOY_FRONTEND = ROOT / "deploy" / "signal_audit" / "frontend"
DIST_FRONTEND = ROOT / "dist" / "signal-audit-deploy" / "frontend"


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def card_files(root):
    return sorted(path for path in (root / "signal_cards").glob("*.json")
                  if path.name != "index.json")


def cards_with_session_context(root):
    cards = []
    for path in card_files(root):
        card = read_json(path)
        ctx = (((card.get("signal_window") or {}).get("session_context"))
               if isinstance(card, dict) else None)
        if isinstance(ctx, dict) and ctx:
            cards.append((path, card, ctx))
    return cards


def event_time(card):
    identity = card.get("identity") or {}
    value = identity.get("confirmed_time_ms")
    return value if isinstance(value, (int, float)) else 0


def assert_asset_root(root):
    assert_true((root / "app.js").exists(), "missing app.js in " + str(root))
    assert_true((root / "signal_cards" / "index.json").exists(),
                "missing signal_cards/index.json in " + str(root))
    cards = cards_with_session_context(root)
    assert_true(cards, "static signal_cards should include session_context in " + str(root))
    ordered_cards = sorted((card for _path, card, _ctx in cards), key=event_time)
    transition_cards = [
        card for card in ordered_cards
        if isinstance(card.get("transition_context"), dict)
        and card["transition_context"].get("transition_id")
    ]
    assert_true(len(ordered_cards) >= 5,
                "deploy fixture should include at least five representative cards")
    assert_true(len(transition_cards) >= len(ordered_cards) - 1,
                "all cards after the first event should include materialized transition_context")
    latest_card = ordered_cards[-1]
    latest_transition = latest_card.get("transition_context") or {}
    assert_true(latest_transition.get("audit_scope") == "AUDIT_ONLY",
                "latest deploy fixture transition should stay audit-only")
    assert_true(latest_transition.get("current_card_id")
                == (latest_card.get("identity") or {}).get("card_id"),
                "latest transition should align with current card identity")
    for key in ("raw_change_groups", "recent_5_trajectory",
                "baseline_24h", "episode_anchor"):
        assert_true(latest_transition.get(key),
                    "latest transition JSON should keep " + key
                    + " for non-visual trace reuse")
    assert_true((root / "signal_cards" / "trajectory" / "BTC.json").exists(),
                "deploy fixture should publish BTC transition trajectory JSON")
    codes = {ctx.get("rationale_code") for _path, _card, ctx in cards}
    for expected in ("PRE_US_TRAPDOOR", "US_DEEP_POST_CATALYST",
                     "ASIA_MORNING", "ASIA_AFTERNOON_LULL",
                     "POST_US_DEADZONE"):
        assert_true(expected in codes, "static cards should cover " + expected)
    for _path, card, ctx in cards:
        identity = card.get("identity") or {}
        assert_true(identity.get("strategy_version") == "1.5.1",
                    "deploy fixture should match current FMZ producer version")
        macro = ((card.get("factor_cross_section") or {}).get("macro_pressure")
                 or {})
        macro_shock = macro.get("macro_shock") or {}
        assert_true(macro_shock.get("state") in ("CLEAR", "WATCH", "BLOCK", "UNKNOWN"),
                    "deploy fixture should include producer-native macro shock state")
        assert_true(macro_shock.get("block") in (True, False),
                    "deploy fixture should include producer-native macro shock block")
        anchor = ((card.get("provenance") or {}).get("transition_audit_source")
                  or {})
        assert_true(anchor.get("schema_name") == "SignalTransitionProducerAnchor",
                    "deploy fixture should include native transition producer anchor")
        assert_true(anchor.get("schema_version") == "1.0.0",
                    "transition producer anchor version")
        assert_true(anchor.get("audit_scope") == "AUDIT_ONLY",
                    "transition producer anchor should stay audit-only")
        assert_true(anchor.get("event_time_ms") == identity.get("confirmed_time_ms"),
                    "transition producer anchor should use confirmed event time")
        assert_true(anchor.get("event_time_basis") == "identity.confirmed_time_ms",
                    "transition producer anchor should declare event time basis")
        assert_true(anchor.get("transition_computation_owner")
                    == "MATERIALIZER_DERIVED",
                    "producer should delegate transition computation to materializer")
        assert_true(ctx.get("schema_name") == "SignalSessionPremiseDurabilityContext",
                    "session_context should use full premise durability schema")
        assert_true(ctx.get("compat_backfill_applied") is not True,
                    "deploy fixture should represent native producer output, not materializer backfill")
        for key in ("clock_window", "adjustment_direction", "evidence_level",
                    "backtest_delta_pp", "validation_basis"):
            assert_true(ctx.get(key) not in (None, ""),
                        "session_context missing " + key)
        assert_true(isinstance(ctx.get("validation_basis"), dict),
                    "validation_basis should be structured")
        assert_true(ctx["validation_basis"].get("source_document")
                    == "结论档案_各时段信号耐久度_2023-2026_v1",
                    "validation_basis should cite source document")
        assert_true(ctx.get("confidence_policy") == "DO_NOT_MULTIPLY_CONFIDENCE",
                    "session_context must not multiply confidence")
        assert_true(ctx.get("affects_confidence") is False,
                    "session_context should not affect confidence")
        assert_true(ctx.get("affects_blocking") is False,
                    "session_context should not affect blocking")
        assert_true(ctx.get("affects_trade_allowed") is False,
                    "session_context should not affect trading permission")
        assert_true(card["decision_matrix"]["temporal_durability"]
                    == ctx["premise_durability"],
                    "matrix should mirror session premise durability")
    fallback = (root / "signal_cards" / "fallback.js").read_text(encoding="utf-8")
    assert_true('"session_context"' in fallback,
                "fallback.js should include session_context for file mode")


def assert_render(root):
    script = r"""
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const root = __ROOT__;
const app = fs.readFileSync(path.join(root, "app.js"), "utf8");
const elements = {};
function element(id) {
  if (!elements[id]) {
    elements[id] = {
      id, value: "", innerHTML: "", textContent: "", dataset: {},
      addEventListener() {},
      insertAdjacentHTML(_where, html) { this.innerHTML += html; }
    };
  }
  return elements[id];
}
const document = {
  getElementById(id) {
    if (id === "signal-data") return { textContent: "[]" };
    return element(id);
  },
  querySelector(selector) {
    return element(selector.startsWith("#") ? selector.slice(1) : selector);
  },
  querySelectorAll() { return []; }
};
async function fetchLocal(rel) {
  return {
    ok: true,
    status: 200,
    async json() {
      return JSON.parse(fs.readFileSync(path.join(root, rel), "utf8"));
    }
  };
}
const context = {
  window: { location: { protocol: "http:" }, SIGNAL_CARD_FIXTURES: [] },
  document, console, Intl, setTimeout, clearTimeout, fetch: fetchLocal
};
vm.createContext(context);
vm.runInContext(app, context);
setTimeout(() => {
  const html = elements.documentView.innerHTML;
  const required = [
    "信号时区置信度 / 前提耐久度",
    "本层结论",
    "三年验证依据",
    "结论红线"
  ];
  const missing = required.filter((item) => !html.includes(item));
  if (missing.length) {
    throw new Error("session context render missing: " + missing.join(","));
  }
}, 0);
"""
    script = script.replace("__ROOT__", json.dumps(str(root)))
    result = subprocess.run(["node", "-e", script], text=True,
                            capture_output=True, encoding="utf-8",
                            errors="replace")
    assert_true(result.returncode == 0,
                "frontend should render session_context: "
                + (result.stderr or result.stdout))


def main():
    assert_asset_root(DEPLOY_FRONTEND)
    assert_render(DEPLOY_FRONTEND)
    if DIST_FRONTEND.exists():
        assert_asset_root(DIST_FRONTEND)
        assert_render(DIST_FRONTEND)
        assert_true((DEPLOY_FRONTEND / "app.js").read_text(encoding="utf-8")
                    == (DIST_FRONTEND / "app.js").read_text(encoding="utf-8"),
                    "deploy and dist app.js should match")
        assert_true((DEPLOY_FRONTEND / "index.html").read_text(encoding="utf-8")
                    == (DIST_FRONTEND / "index.html").read_text(encoding="utf-8"),
                    "deploy and dist index.html should match")
    else:
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        assert_true("dist/" in gitignore,
                    "dist may be absent only because it is ignored package output")
    print("signal_session_context_deploy_assets: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_session_context_deploy_assets: FAIL - " + str(exc))
        sys.exit(1)
