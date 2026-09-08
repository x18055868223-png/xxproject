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
const retryButtons = [];
const downloadClicks = [];
const comfort = {
  schema: "signal_comfort_ratings@1.0.0",
  rating_scope: "signal_side_admission",
  candidate_quote_economics: "not_evaluated",
  as_of_ms: 1781751600000,
  headline: {
    final_grade: "B",
    focus_side: "call_credit",
    action_cn: "启动关注：Call 侧进入人工观察，等待关键反证解除。"
  },
  put_credit: {
    status: "RATED",
    model_grade: "C",
    final_grade: "C",
    basis_cn: "Put 侧暂按普通观察处理。",
    counter_evidence_cn: "下行压力尚未充分解除。",
    next_observation_cn: "继续观察下方空间。",
    evidence_refs: [],
    counter_evidence_refs: [],
    unresolved_conditions_cn: [],
    cap_reasons_cn: [],
    s_upgrade_basis_cn: "",
    s_upgrade_evidence_refs: []
  },
  call_credit: {
    status: "RATED",
    model_grade: "B",
    final_grade: "B",
    basis_cn: "Call 侧有可说明机会，但仍停在启动关注。",
    counter_evidence_cn: "宏观背景仍需观察。",
    next_observation_cn: "等待下一轮主动流确认。",
    evidence_refs: [],
    counter_evidence_refs: [],
    unresolved_conditions_cn: ["等待主动流确认。"],
    cap_reasons_cn: [],
    s_upgrade_basis_cn: "",
    s_upgrade_evidence_refs: []
  }
};

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
  path: `signal_cards/CARD-${index}.json`,
  signal_comfort_summary: index === 1 ? comfort : undefined
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
    display_layers: { headline: `CARD-${index} 摘要` },
    llm_review: index === 1
      ? { status: "OK", content: { integrated_trade_advisory: { side_comfort_ratings: comfort } } }
      : undefined
  };
}

const document = {
  body: { appendChild() {} },
  head: { appendChild() { throw new Error("HTTP mode must not load fallback.js"); } },
  createElement(tag) {
    if (tag === "a") {
      return {
        href: "",
        download: "",
        click() { downloadClicks.push({ href: this.href, download: this.download }); },
        remove() {}
      };
    }
    return { addEventListener() {}, remove() {}, set src(_value) {} };
  },
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
    if (selector === ".card-retry") {
      retryButtons.length = 0;
      const html = element("documentView").innerHTML || "";
      const matches = html.match(/<button\b[^>]*class="[^"]*\bcard-retry\b[^"]*"[^>]*>/g) || [];
      matches.forEach((tag, index) => {
        const button = {
          dataset: {},
          addEventListener(type, handler) {
            if (type === "click") this.click = handler;
          }
        };
        tag.replace(/data-([a-z0-9-]+)="([^"]*)"/gi, (_m, key, value) => {
          const name = String(key).replace(/-([a-z])/g, (_match, ch) => ch.toUpperCase());
          button.dataset[name] = value;
          return "";
        });
        retryButtons.push(button);
      });
      return retryButtons;
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
    return { ok: false, status: 404, async json() { return {}; } };
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
      errorVisible: element("documentView").innerHTML.includes("单卡资料加载失败"),
      friendlyReason: element("documentView").innerHTML.includes("这张卡的资料暂时不可用，请稍后重试。"),
      titleVisible: element("documentView").innerHTML.includes("本卡资料暂不可用"),
      statusHidden: !element("documentView").innerHTML.includes("404") && !element("documentView").innerHTML.includes("HTTP"),
      listStillVisible: (element("indexList").innerHTML.match(/class="index-item/g) || []).length
    };
    const cardOne = buttons.get("CARD-1");
    if (!cardOne || typeof cardOne.click !== "function") throw new Error("missing CARD-1 button");
    const beforeCachedClick = calls.filter((item) => item.endsWith("CARD-1.json")).length;
    cardOne.click();
    setTimeout(() => {
      const afterCachedClick = calls.filter((item) => item.endsWith("CARD-1.json")).length;
      const cardOneText = element("documentView").innerHTML.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
      const downloadButton = retryButtons.find((button) => button.dataset.downloadCardId === "CARD-1");
      if (!downloadButton || typeof downloadButton.click !== "function") throw new Error("missing CARD-1 download button");
      downloadButton.click();
      process.stdout.write(JSON.stringify({ initial, failed, beforeCachedClick, afterCachedClick, cardOneText, downloadClicks }));
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
                and data["failed"]["errorVisible"]
                and data["failed"]["friendlyReason"]
                and data["failed"]["titleVisible"]
                and data["failed"]["statusHidden"],
                "selected prefetch 404 should get one bounded retry and reader-safe error UI")
    assert_true(data["failed"]["listStillVisible"] == 15,
                "one bad card must not clear or block the manifest list")
    assert_true(data["beforeCachedClick"] == 1
                and data["afterCachedClick"] == 1,
                "clicking a prefetched card should reuse the in-memory cache")
    assert_true("B级｜Call 信用价差" in data["cardOneText"]
                and "暂未完成有效评级" not in data["cardOneText"],
                "HTTP detail cache should preserve the published manifest comfort summary")
    assert_true(data["downloadClicks"] and data["downloadClicks"][0]["href"] == "signal_cards/CARD-1.json"
                and data["downloadClicks"][0]["download"] == "CARD-1.json",
                "HTTP download should use the same-origin static full-card JSON path")
    print("signal_audit_frontend_lazy_load: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_audit_frontend_lazy_load: FAIL - " + str(exc))
        sys.exit(1)
