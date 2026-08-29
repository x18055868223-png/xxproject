import json
import pathlib
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "deploy" / "signal_audit" / "frontend"


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    script = r'''
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const root = __ROOT__;
const app = fs.readFileSync(path.join(root, "app.js"), "utf8");
const indexHtml = fs.readFileSync(path.join(root, "index.html"), "utf8");
const elements = {};
const buttons = new Map();
const calls = [];

function element(id) {
  if (!elements[id]) {
    elements[id] = {
      id, value: "", innerHTML: "", textContent: "", dataset: {},
      classList: { add() {}, remove() {}, toggle() {} },
      addEventListener() {},
      insertAdjacentHTML(_where, html) { this.innerHTML += html; },
      focus() {}
    };
  }
  return elements[id];
}

const manifest = { cards: Array.from({ length: 20 }, (_, index) => ({
  card_id: `CARD-${index}`,
  confirmed_at: `2026-08-${String(28 - index).padStart(2, "0")}T12:00:00+08:00`,
  symbol: "BTC",
  quality: "OK",
  path: `signal_cards/CARD-${index}.json`
})) };

function detail(index) {
  return {
    schema: { name: "signal_review_card", version: "1.0.0", status: "FINAL" },
    identity: {
      card_id: `CARD-${index}`, short_id: String(index), symbol: "BTC",
      confirmed_at: manifest.cards[index].confirmed_at, strategy_name: "lazy-test"
    },
    market_context: { price: 100000, quote_currency: "USDT" },
    quality: { overall: "OK", all_required_sources_ready: true },
    decision: {
      lean: "NEUTRAL", support_label: "WAIT_CONFIRMATION", confidence: 50,
      evidence_strength: 50
    },
    decision_matrix: { audit_dissent: "PENDING_LLM" },
    reasoning: { evidence: [] },
    display_layers: { headline: `CARD-${index} 摘要` }
  };
}

const document = {
  head: { appendChild() { throw new Error("HTTP mode must not load fallback.js"); } },
  getElementById(id) {
    if (id === "signal-data") return { textContent: "[]" };
    return element(id);
  },
  querySelector(selector) {
    return element(selector.startsWith("#") ? selector.slice(1) : selector);
  },
  querySelectorAll(selector) {
    if (selector === ".index-item") {
      const ids = [...element("indexList").innerHTML.matchAll(/data-card-id="([^"]+)"/g)]
        .map((match) => match[1]);
      return ids.map((id) => {
        const button = { dataset: { cardId: id }, addEventListener(type, handler) {
          if (type === "click") this.click = handler;
        }};
        buttons.set(id, button);
        return button;
      });
    }
    return [];
  }
};

async function fetch(url) {
  calls.push(url);
  if (url === "signal_cards/index.json") {
    return { ok: true, status: 200, async json() { return manifest; } };
  }
  const match = /CARD-(\d+)\.json$/.exec(url);
  if (!match) return { ok: false, status: 404, async json() { return {}; } };
  const index = Number(match[1]);
  if (index === 2) {
    return { ok: true, status: 200, async json() { throw new SyntaxError("bad json"); } };
  }
  return { ok: true, status: 200, async json() { return detail(index); } };
}

const context = {
  window: { location: { protocol: "http:" }, SIGNAL_AUDIT_CARD_TIMEOUT_MS: 1000 },
  document, console, Intl, Map, Promise, setTimeout, clearTimeout, fetch
};
vm.createContext(context);
vm.runInContext(app, context);

setTimeout(() => {
  const initial = {
    manifestCalls: calls.filter((item) => item === "signal_cards/index.json").length,
    detailCalls: calls.filter((item) => item.endsWith(".json") && !item.endsWith("index.json")).length,
    listButtons: (element("indexList").innerHTML.match(/class="index-item/g) || []).length,
    fallbackStatic: indexHtml.includes("signal_cards/fallback.js")
  };
  const cardTwo = buttons.get("CARD-2");
  if (!cardTwo || typeof cardTwo.click !== "function") throw new Error("missing CARD-2 button");
  cardTwo.click();
  setTimeout(() => {
    const failed = {
      cardTwoCalls: calls.filter((item) => item.endsWith("CARD-2.json")).length,
      errorVisible: element("documentView").innerHTML.includes("单卡 JSON 加载失败"),
      listStillVisible: (element("indexList").innerHTML.match(/class="index-item/g) || []).length
    };
    const cardOne = buttons.get("CARD-1");
    if (!cardOne || typeof cardOne.click !== "function") throw new Error("missing CARD-1 button");
    const beforeCachedClick = calls.filter((item) => item.endsWith("CARD-1.json")).length;
    cardOne.click();
    setTimeout(() => {
      const afterCachedClick = calls.filter((item) => item.endsWith("CARD-1.json")).length;
      process.stdout.write(JSON.stringify({ initial, failed, beforeCachedClick, afterCachedClick }));
    }, 30);
  }, 40);
}, 80);
'''.replace("__ROOT__", json.dumps(str(FRONTEND)))
    result = subprocess.run(
        ["node", "-e", script], text=True, capture_output=True,
        encoding="utf-8", errors="replace", timeout=20,
    )
    assert_true(result.returncode == 0, result.stderr or result.stdout)
    data = json.loads(result.stdout)
    assert_true(data["initial"]["manifestCalls"] == 1,
                "HTTP startup should fetch exactly one manifest")
    assert_true(data["initial"]["detailCalls"] == 3,
                "startup should load one selected card plus two bounded prefetch cards")
    assert_true(data["initial"]["listButtons"] == 15,
                "old manifest format should still render only the newest 15 summaries")
    assert_true(data["initial"]["fallbackStatic"] is False,
                "HTTP index must not eagerly download fallback.js")
    assert_true(data["failed"]["cardTwoCalls"] == 2
                and data["failed"]["errorVisible"],
                "selected prefetch failure should get one bounded retry and local error UI")
    assert_true(data["failed"]["listStillVisible"] == 15,
                "one bad card must not clear or block the manifest list")
    assert_true(data["beforeCachedClick"] == 1
                and data["afterCachedClick"] == 1,
                "clicking a prefetched card should reuse the in-memory cache")
    print("signal_audit_frontend_lazy_load: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_audit_frontend_lazy_load: FAIL - " + str(exc))
        sys.exit(1)
