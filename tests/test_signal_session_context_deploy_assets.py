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


def assert_asset_root(root):
    assert_true((root / "app.js").exists(), "missing app.js in " + str(root))
    assert_true((root / "signal_cards" / "index.json").exists(),
                "missing signal_cards/index.json in " + str(root))
    cards = cards_with_session_context(root)
    assert_true(cards, "static signal_cards should include session_context in " + str(root))
    codes = {ctx.get("rationale_code") for _path, _card, ctx in cards}
    for expected in ("PRE_US_TRAPDOOR", "US_DEEP_POST_CATALYST",
                     "ASIA_MORNING", "ASIA_AFTERNOON_LULL",
                     "POST_US_DEADZONE"):
        assert_true(expected in codes, "static cards should cover " + expected)
    for _path, card, ctx in cards:
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
    for root in (DEPLOY_FRONTEND, DIST_FRONTEND):
        assert_asset_root(root)
        assert_render(root)
    assert_true((DEPLOY_FRONTEND / "app.js").read_text(encoding="utf-8")
                == (DIST_FRONTEND / "app.js").read_text(encoding="utf-8"),
                "deploy and dist app.js should match")
    print("signal_session_context_deploy_assets: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_session_context_deploy_assets: FAIL - " + str(exc))
        sys.exit(1)
