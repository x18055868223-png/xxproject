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


def main():
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

    print("signal_audit_frontend_render_contract: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_audit_frontend_render_contract: FAIL - " + str(exc))
        sys.exit(1)
