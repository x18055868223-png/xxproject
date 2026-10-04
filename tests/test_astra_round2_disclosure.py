import json
import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "deploy" / "signal_audit" / "frontend" / "app.js"


def _candidate(candidate_id: str, snapshot_id: str) -> dict:
    return {
        "candidate_id": candidate_id,
        "side": "put",
        "review_gate": {"enabled": False, "reasons": ["manual_review_service_not_configured"]},
        "snapshot": {
            "snapshot_id": snapshot_id,
            "data_identity": {"kind": "synthetic_fixture"},
            "contract": {
                "side": "put",
                "short_instrument": "BTC-26SEP26-97000-P",
                "long_instrument": "BTC-26SEP26-95000-P",
                "quantity_btc": 0.1,
                "width_usd": 2000,
                "short_strike_usd": 97000,
                "long_strike_usd": 95000,
                "expiry_ms": 1_790_409_600_000,
            },
            "market": {
                "valuation_ts_ms": 1_790_323_200_000,
                "reference_price_usd": 100000,
                "quote": {"credit_btc": 0.0004},
            },
            "fee": {"amount_btc": 0.0000375, "delivery_fee_btc": 0.0000075},
            "economics": {
                "premium_spread_btc": 0.0004,
                "theoretical_two_leg_fee_btc": 0.00003,
                "net_credit_after_entry_fee_btc": 0.00037,
                "net_credit_after_fee_btc": 0.0003625,
                "mu_ref_btc": 0.00025,
                "reference_margin_btc": 0.0001125,
                "valuation_spot_usd": 100000,
            },
            "risk": {"status": "supported"},
            "liability": {
                "starts_at_usd": 97000,
                "protection_at_usd": 95000,
                "breakeven_usd": [96649.64509],
                "btc_contract_cap": None,
                "payout_table": [
                    {"settlement_usd": 100000, "payout_btc": 0.0},
                    {"settlement_usd": 95000, "payout_btc": 0.00210526},
                ],
            },
        },
    }


def test_candidate_details_disclosure_survives_only_same_candidate_snapshot() -> None:
    app = APP_JS
    node = shutil.which("node")
    assert node, "node is not available"
    payload = {
        "same": {"preview": {"kind": "synthetic_fixture"}, "candidates": [_candidate("candidate-a", "snapshot-1")]},
        "new_version": {"preview": {"kind": "synthetic_fixture"}, "candidates": [_candidate("candidate-a", "snapshot-2")]},
        "other_candidate": {"preview": {"kind": "synthetic_fixture"}, "candidates": [_candidate("candidate-b", "snapshot-1")]},
    }
    script = r"""
const fs = require("fs");
const vm = require("vm");
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
const app = fs.readFileSync(process.env.APP_JS, "utf8");

function parseAttrs(attrs) {
  const out = {};
  const re = /([A-Za-z0-9_-]+)="([^"]*)"/g;
  let match;
  while ((match = re.exec(attrs))) out[match[1]] = match[2];
  return out;
}

function parseDetails(html) {
  const details = [];
  const re = /<details\b([^>]*)>/g;
  let match;
  while ((match = re.exec(html))) {
    const attrs = parseAttrs(match[1]);
    if (!attrs["data-disclosure-key"]) continue;
    details.push({
      open: /\sopen(?:\s|>|$)/.test(match[1]),
      dataset: { disclosureKey: attrs["data-disclosure-key"] },
      addEventListener() {},
    });
  }
  return details;
}

function makeElement(id) {
  let html = "";
  let details = [];
  return {
    id,
    hidden: false,
    className: "",
    textContent: "",
    value: "",
    dataset: {},
    style: {},
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener() {},
    setAttribute(name, value) { this[name] = value; },
    getAttribute(name) { return this[name] || null; },
    focus() {},
    close() {},
    showModal() {},
    scrollIntoView() {},
    set innerHTML(value) {
      html = String(value);
      details = parseDetails(html);
    },
    get innerHTML() { return html; },
    querySelector() { return null; },
    querySelectorAll(selector) {
      if (selector === "details[open][data-disclosure-key]") {
        return details.filter((item) => item.open && item.dataset.disclosureKey);
      }
      if (selector === "details[data-disclosure-key]") {
        return details.filter((item) => item.dataset.disclosureKey);
      }
      return [];
    },
  };
}

const elements = { candidateArea: makeElement("candidateArea") };
const document = {
  readyState: "loading",
  querySelector(selector) {
    if (selector === "#candidateArea") return elements.candidateArea;
    return null;
  },
  querySelectorAll() { return []; },
  addEventListener() {},
};
const sandbox = {
  console,
  document,
  window: {
    document,
    location: { hostname: "127.0.0.1", protocol: "http:" },
    setInterval() { throw new Error("unexpected interval"); },
    clearInterval() {},
    crypto: { randomUUID() { return "test"; } },
  },
  fetch() { throw new Error("fetch disabled"); },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(app, sandbox, { filename: "app.js" });
const api = sandbox.window.__astraWorkbenchTest;
if (!api || typeof api.renderCandidates !== "function") throw new Error("renderCandidates missing");

function render(workbench) {
  api.state.workbench = workbench;
  api.renderCandidates();
  return elements.candidateArea.querySelectorAll("details[data-disclosure-key]");
}

let details = render(payload.same);
if (details.length !== 1) throw new Error("candidate details missing");
if (details[0].dataset.disclosureKey !== "candidate-a::snapshot-1::candidate-details") {
  throw new Error(`unexpected key ${details[0].dataset.disclosureKey}`);
}
if (!/卖出腿/.test(elements.candidateArea.innerHTML) || !/净费用/.test(elements.candidateArea.innerHTML) || !/支付表/.test(elements.candidateArea.innerHTML)) {
  throw new Error("nested contract fee payout details missing");
}
details[0].open = true;
details = render(payload.same);
const sameOpen = details[0].open === true;
details[0].open = false;
details = render(payload.same);
const sameClosed = details[0].open === false;
details[0].open = true;
details = render(payload.new_version);
const newVersionClosed = details[0].open === false;
details = render(payload.other_candidate);
const otherCandidateClosed = details[0].open === false;
console.log(JSON.stringify({ sameOpen, sameClosed, newVersionClosed, otherCandidateClosed }));
"""
    result = subprocess.run(
        [node, "-e", script],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        env={**os.environ, "APP_JS": str(app)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(result.stdout)
    assert observed == {
        "sameOpen": True,
        "sameClosed": True,
        "newVersionClosed": True,
        "otherCandidateClosed": True,
    }
