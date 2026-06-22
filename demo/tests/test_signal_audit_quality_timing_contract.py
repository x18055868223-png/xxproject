import importlib.util
import json
import pathlib
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]
SIGNAL_FILE = (
    ROOT / "demo" / "\u6700\u65b0\u4ea4\u4ed8\u7269" /
    "neutral_regulation_demo_fmz.py"
)
DEPLOY_FRONTEND = ROOT / "deploy" / "signal_audit" / "frontend"
DIST_FRONTEND = ROOT / "dist" / "signal-audit-deploy" / "frontend"


def load_signal_module():
    spec = importlib.util.spec_from_file_location("nrd_signal", SIGNAL_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(
            "{}: expected {!r}, got {!r}".format(message, expected, actual)
        )


def read(path):
    return path.read_text(encoding="utf-8")


def assert_distance_render(root):
    card = {
        "schema": {
            "name": "signal_review_card",
            "version": "1.0.0",
            "status": "FINAL",
        },
        "identity": {
            "card_id": "DISTANCE-PIN-UNIT-TEST",
            "short_id": "DPIN",
            "confirmed_at": "2026-06-21T16:52:09+08:00",
            "symbol": "BTC",
            "strategy_name": "中性回路信号层",
            "strategy_version": "1.3.0",
        },
        "market_context": {
            "price": 63694.07,
            "quote_currency": "USDT",
        },
        "decision": {
            "lean": "NEUTRAL",
            "support_label": "WAIT_CONFIRMATION",
            "evidence_strength": 69,
            "confidence": 48,
            "confidence_calibration": "UNCALIBRATED",
            "final_conclusion_cn": "distance render test",
        },
        "quality": {
            "overall": "OK",
            "all_required_sources_ready": True,
            "sources": {},
        },
        "factor_cross_section": {
            "gamma_regime": {
                "regime": "TRANSITION",
                "confidence_multiplier": 0.98,
                "distance_to_pin_pct": -0.304691,
                "pin_pull_direction": "DOWN",
                "pin_strike": 63500,
            },
            "gex_info": {},
        },
        "reasoning": {},
        "conflict": {},
        "blocking": {},
        "provenance": {},
        "delivery": {},
        "integrity": {},
    }
    script = r"""
const fs = require("fs");
const vm = require("vm");
const app = fs.readFileSync(__APP_PATH__, "utf8");
const card = __CARD_JSON__;
const elements = {};
function element(id) {
  if (!elements[id]) {
    elements[id] = {
      id,
      value: "",
      innerHTML: "",
      textContent: "",
      dataset: {},
      addEventListener() {},
      insertAdjacentHTML(_where, html) { this.innerHTML += html; }
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
  window: { location: { protocol: "file:" } },
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
  const html = elements.documentView.innerHTML;
  if (!html.includes("-0.3%")) {
    throw new Error("pin distance should render near -0.3%, got: " + html.slice(0, 2000));
  }
  if (html.includes("-30.47%") || html.includes("-30.46%")) {
    throw new Error("pin distance was scaled by 100: " + html.slice(0, 2000));
  }
}, 0);
"""
    script = script.replace("__APP_PATH__", json.dumps(str(root / "app.js")))
    script = script.replace("__CARD_JSON__", json.dumps(card, ensure_ascii=False))
    result = subprocess.run(["node", "-e", script],
                            text=True, capture_output=True,
                            encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise AssertionError(
            "frontend should render distance_to_pin_pct as percent points: "
            + (result.stderr or result.stdout))


def assert_evidence_ledger_render(root):
    raw_values = {
        "nested_object": {
            "a0": 0, "a1": 1, "a2": 2, "a3": 3, "a4": 4,
            "a5": 5, "a6": 6, "a7": 7, "a8": 8,
        },
        "nested_array": [
            {"name": "arr0", "ready": True},
            {"name": "arr1", "ready": True},
            {"name": "arr2", "ready": True},
            {"name": "arr3", "ready": True},
            {"name": "arr4", "ready": True},
            {"name": "arr5", "ready": True},
            {"name": "arr6", "ready": False},
        ],
    }
    for idx in range(12):
        raw_values["field_{:02d}".format(idx)] = idx
    raw_values["observed_at"] = "2026-06-22T13:43:59+08:00"
    raw_values["source_ref"] = "LEDGER_RENDER_TEST"
    card = {
        "schema": {
            "name": "signal_review_card",
            "version": "1.0.0",
            "status": "FINAL",
        },
        "identity": {
            "card_id": "LEDGER-RENDER-TEST",
            "short_id": "LEDGER",
            "confirmed_at": "2026-06-22T13:44:00+08:00",
            "symbol": "BTC",
            "strategy_name": "中性回路信号层",
            "strategy_version": "1.4.0",
        },
        "market_context": {"price": 64000, "quote_currency": "USDT"},
        "decision": {
            "lean": "NEUTRAL",
            "support_label": "WAIT_CONFIRMATION",
            "evidence_strength": 69,
            "confidence": 48,
            "final_conclusion_cn": "ledger render test",
        },
        "quality": {"overall": "OK", "all_required_sources_ready": True, "sources": {}},
        "factor_cross_section": {
            "gamma_regime": {},
            "gex_info": {},
            "macro_pressure": {
                "macro_regime": "Mild Headwind",
                "score": 0.23,
                "data_confidence": 1,
                "source_ref": "MACRO_TEST",
            },
        },
        "reasoning": {
            "engine": "EDB",
            "engine_version": "0.5",
            "score": {"method": "test", "weighted_vote_sum": 0.1,
                      "effective_weight_sum": 1, "final": 0.1},
            "agreement": {"value": 1},
            "coverage": {"value": 1},
            "confidence_decomposition": {"confidence_final": 48},
            "evidence": [{
                "key": "FUNDING",
                "gloss_cn": "主动流确认",
                "participation_status": "NON_VOTING",
                "vote": 0,
                "configured_weight": 0.25,
                "reliability": 1,
                "information": 1,
                "effective_weight": 0,
                "weighted_contribution": 0,
                "absolute_share_pct": 0,
                "lean": "NEUTRAL",
                "auxiliary_role": "FUTURES_FUNDING_CROWDING",
                "auxiliary_lean": "BULLISH",
                "source_ref": {"source_path": "factor_cross_section.funding", "source_rank": 3},
                "raw_values": {
                    **raw_values,
                    "last_rate": 0.000061,
                    "effect": "mild_long_bias",
                    "source_ref": "BINANCE_FUNDING_RATE",
                },
            }],
        },
        "conflict": {},
        "blocking": {},
        "provenance": {},
        "delivery": {},
        "integrity": {},
    }
    template = json.loads(json.dumps(card["reasoning"]["evidence"][0], ensure_ascii=False))
    card["reasoning"]["evidence"] = []
    for rate, effect in [
        (0.000061, "mild_long_bias"),
        (0.00012, "crowded_long_bias"),
        (-0.000061, "mild_short_bias"),
        (-0.00012, "crowded_short_bias"),
    ]:
        row = json.loads(json.dumps(template, ensure_ascii=False))
        row["raw_values"]["last_rate"] = rate
        row["raw_values"]["effect"] = effect
        row["auxiliary_lean"] = "BEARISH" if rate > 0 else "BULLISH"
        card["reasoning"]["evidence"].append(row)
    card["reasoning"]["evidence"].append({
        "key": "MACRO",
        "gloss_cn": "宏观",
        "participation_status": "ACTIVE",
        "vote": -0.5,
        "configured_weight": 0.16,
        "reliability": 1,
        "information": 1,
        "effective_weight": 0.16,
        "weighted_contribution": -0.08,
        "absolute_share_pct": 15,
        "lean": "BEARISH",
        "auxiliary_role": "MACRO_CONTEXT",
        "source_ref": "factor_cross_section.macro_pressure",
        "detail": {"macro_regime": "Mild Headwind"},
    })
    script = r"""
const fs = require("fs");
const vm = require("vm");
const app = fs.readFileSync(__APP_PATH__, "utf8");
const card = __CARD_JSON__;
const elements = {};
function element(id) {
  if (!elements[id]) {
    elements[id] = {
      id,
      value: "",
      innerHTML: "",
      textContent: "",
      dataset: {},
      addEventListener() {},
      insertAdjacentHTML(_where, html) { this.innerHTML += html; }
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
  window: { location: { protocol: "file:" } },
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
  const html = elements.documentView.innerHTML;
  if (!html.includes("class=\"evidence-ledger\"")) {
    throw new Error("ledger layout missing: " + html.slice(0, 2000));
  }
  if (html.includes("class=\"evidence-table\"")) {
    throw new Error("old evidence table should not render: " + html.slice(0, 2000));
  }
  if (html.includes("[object Object]")) {
    throw new Error("nested raw values should not render as object strings: " + html.slice(0, 2000));
  }
  const ledgerStart = html.indexOf("class=\"evidence-ledger\"");
  const ledgerEnd = html.indexOf("Confidence decomposition", ledgerStart);
  const ledgerHtml = html.slice(ledgerStart, ledgerEnd > ledgerStart ? ledgerEnd : undefined);
  for (const forbidden of ["nested_object", "nested_array", "a8", "arr6", "field_11", "Raw values"]) {
    if (ledgerHtml.includes(forbidden)) {
      throw new Error("EDB ledger should not expand raw noise field " + forbidden + ": " + ledgerHtml.slice(0, 3000));
    }
  }
  for (const rawTrace of ["evidence_raw_values.FUNDING", "nested_object", "a8", "arr6", "field_11"]) {
    if (!html.includes(rawTrace)) {
      throw new Error("full data section should preserve raw trace " + rawTrace + ": " + html.slice(0, 3000));
    }
  }
  for (const required of [
    "FUNDING", "费率端倾向", "BTCUSDT 永续资金费率 0.0061%",
    "温和多头倾向", "0.012%", "拥挤多头倾向",
    "-0.0061%", "温和空头倾向", "-0.012%", "拥挤空头倾向",
    "0.01%", "反身性辅助倾向为偏空", "反身性辅助倾向为偏多",
    "宏观背景 Mild Headwind，分数 0.23", "FUTURES_FUNDING_CROWDING",
    "source_path", "source_rank"
  ]) {
    if (!ledgerHtml.includes(required)) {
      throw new Error("EDB ledger summary missing " + required + ": " + ledgerHtml.slice(0, 3000));
    }
  }
}, 0);
"""
    script = script.replace("__APP_PATH__", json.dumps(str(root / "app.js")))
    script = script.replace("__CARD_JSON__", json.dumps(card, ensure_ascii=False))
    result = subprocess.run(["node", "-e", script],
                            text=True, capture_output=True,
                            encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise AssertionError(
            "frontend should render compact evidence ledger: "
            + (result.stderr or result.stdout))


def main():
    mod = load_signal_module()
    config = dict(mod.CONFIG)
    card = mod.build_sample_review_card(config)
    cross = card["factor_cross_section"]

    cross["neutral_repair"]["age_ms"] = 15_000
    cross["tmvf"]["age_ms"] = 45_000
    cross["micro_flow"]["age_ms"] = 90_000
    cross["macro_pressure"]["data_age_ms"] = 180_000
    cross["gamma_regime"]["age_ms"] = 240_000
    cross["gamma_regime"].pop("distance_to_pin_pct", None)
    cross["gamma_regime"]["pin"] = {
        "pin_strike": 62_000,
        "distance_to_pin_pct": -1.75,
        "pin_pull_direction": "DOWN",
    }
    cross["skew"]["age_ms"] = 300_000
    cross["gex_info"] = {
        "availability": "ready",
        "data_status": "LKGV_CACHE",
        "market_state": "negative_gamma",
        "age_ms": 600_000,
        "fetched_at": "2026-06-19T14:47:21+00:00",
        "source_ref": "GEX_MONITOR_API",
        "fetch_error": None,
        "reasons": ["GEX_INFO_LKGV_FALLBACK"],
        "net_gamma_notional_usd": -112_000_000,
    }

    record = mod.build_audit_record(card, config)
    sources = record["quality"]["sources"]

    assert_equal(sources["price"]["age_ms"], 0, "price age should be explicit")
    for name, expected_age in (
            ("neutral_repair", 15_000),
            ("tmvf", 45_000),
            ("micro_flow", 90_000),
            ("macro_pressure", 180_000),
            ("gamma_regime", 240_000),
            ("skew", 300_000),
            ("gex_info", 600_000)):
        assert_equal(sources[name]["age_ms"], expected_age,
                     name + " quality age")
        assert_true(sources[name]["observed_at"],
                    name + " quality observed_at should be displayable")
        assert_true(sources[name]["source_ref"],
                    name + " source_ref should stay available")

    assert_equal(sources["gex_info"]["observed_at"],
                 "2026-06-19T14:47:21+00:00",
                 "gex observed_at should prefer fetched_at")
    assert_equal(sources["gex_info"]["status"], "LKGV_CACHE",
                 "gex cache state should stay visible")
    assert_equal(sources["gex_info"]["reason"], "GEX_INFO_LKGV_FALLBACK",
                 "gex cache reason should use reasons list")
    assert_true("factor_cross_section.gex_info" not in
                record["quality"]["optional_missing_sources"],
                "gex with ready cached body should not be treated as missing")
    assert_equal(record["factor_cross_section"]["gamma_regime"][
                 "distance_to_pin_pct"], -1.75,
                 "gamma nested pin distance should be promoted")
    assert_equal(record["factor_cross_section"]["gamma_regime"][
                 "pin_pull_direction"], "DOWN",
                 "gamma nested pin direction should be promoted")

    for root in (DEPLOY_FRONTEND, DIST_FRONTEND):
        app = read(root / "app.js")
        assert_true("function qualitySourceView(doc, key, source)" in app,
                    "app.js should derive quality timing from nearby factor data")
        assert_true("function qualityReasonText(source)" in app,
                    "app.js should not mark OK sources as missing reason")
        assert_true("factor_cross_section.macro_pressure" in app,
                    "app.js should know macro quality fallback path")
        assert_true("qualitySourceView(doc, key, source)" in app,
                    "renderQuality should use the derived source view")
        assert_true("function pinDistanceText(doc, gamma, gex)" in app,
                    "app.js should derive missing distance_to_pin_pct for old cards")
        assert_true("normalizeDistancePct" not in app,
                    "app.js should not rescale distance_to_pin_pct; it is already percent points")
        assert_true("Math.abs(numeric) <= 1 ? numeric * 100 : numeric" not in app,
                    "app.js should not use <=1 percent heuristics for pin distance")
        assert_true("const explicitPct = safeNumber(explicit);" in app,
                    "app.js should parse explicit distance_to_pin_pct without unit conversion")
        assert_true("pctPoint(explicitPct)" in app,
                    "app.js should render explicit pin distance as percent points")
        assert_true("const diffPct = ((pin - price) / price) * 100;" in app,
                    "app.js should derive missing pin distance with signal-layer sign convention")
        assert_true("const direction = diffPct > 0 ? \"UP\" : (diffPct < 0 ? \"DOWN\" : \"FLAT\");" in app,
                    "app.js should derive pin direction with signal-layer labels")
        assert_true("function rawValueText(value, depth = 0)" in app,
                    "app.js should format nested raw values in evidence ledger")
        assert_true("class=\"evidence-ledger\"" in app,
                    "app.js should render evidence as responsive ledger cards")
        assert_true("class=\"evidence-table\"" not in app,
                    "app.js should not render the old wide evidence table")
        assert_true(".slice(0, 10)" not in app,
                    "app.js should not silently truncate raw evidence fields")
        assert_true("function nullSemantics(path, scope = \"\")" in app,
                    "app.js should classify benign null fields by path")
        assert_true("缓存可用，主体数据完整" in app,
                    "app.js should explain LKGV cache as usable body data")
        assert_true("无错误" in app and "无警告" in app,
                    "app.js should not mark no-error/no-warning nulls as missing")
        assert_distance_render(root)
        assert_evidence_ledger_render(root)

    signal_source = read(SIGNAL_FILE)
    assert_true("signal_runtime_facts = self._runtime_facts()" in signal_source,
                "real signal recording should pass runtime facts into audit card")
    assert_true(
        "self.signal_events.maybe_record(\n"
        "            neutral_repair_signal,\n"
        "            factor_snapshot,\n"
        "            signal_runtime_facts)" in signal_source,
        "real signal maybe_record call should use full runtime facts")

    print("signal_audit_quality_timing_contract: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_audit_quality_timing_contract: FAIL - " + str(exc))
        sys.exit(1)
