import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def test_round4_all_axis_uses_same_cutoff_peer_contract_bounds():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to execute the actual frontend axis")
    script = r"""
const fs = require("fs");
const vm = require("vm");
const app = fs.readFileSync("deploy/signal_audit/frontend/app.js", "utf8");
const fakeDocument = {
  readyState: "loading",
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: () => {},
};
const sandbox = {
  console,
  document: fakeDocument,
  window: {
    document: fakeDocument,
    location: { hostname: "127.0.0.1", protocol: "http:" },
    innerWidth: 1440,
    setInterval: () => 0,
    clearInterval: () => {},
  },
  setTimeout: () => 0,
  clearTimeout: () => {},
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(app, sandbox, { filename: "app.js" });

const api = sandbox.window.__astraMapAxisV241;
const testApi = sandbox.window.__astraWorkbenchTest;
if (!api || !testApi) throw new Error("frontend test hooks missing");

const ref = (side, role, price) => ({
  reference_id: `contract_${side}_${role}`,
  family: "CONTRACT_LIABILITY",
  role,
  raw_price: price,
  display_price_usd: price,
  coordinate_qualification: "COMPARABLE",
  eligible_for_display: true,
  expiry_scope: 1790323200000,
});
const contractMap = (side, short, long, be, cutoff) => {
  const map = {
    current_price_usd: 100000,
    price_basis: "DERIBIT_BTC_USD_INDEX",
    display_reference_ids: ["near"],
    presentation: {
      mode: "FIXTURE",
      candidate_identity: { reference_time_ms: cutoff, current_price_usd: 100000, reference_source: "DERIBIT_BTC_USD_INDEX" },
      price_axis: {
        current: { price_usd: 100000, source: "DERIBIT_BTC_USD_INDEX", time_ms: cutoff },
        references: [
          { reference_id: "near", family: "TRADED_ACCEPTANCE", role: "OBSERVED_ZONE",
            display_low_usd: 99000, display_high_usd: 101000, eligible_for_display: true,
            selected_for_display: true, coordinate_qualification: "COMPARABLE", label_cn: "近端接受区" },
        ],
      },
    },
    reference_pool: [
      { reference_id: "near", family: "TRADED_ACCEPTANCE", role: "OBSERVED_ZONE",
        display_low_usd: 99000, display_high_usd: 101000, eligible_for_display: true,
        selected_for_display: true, coordinate_qualification: "COMPARABLE", label_cn: "近端接受区" },
      ref(side, "SHORT_LEG", short),
      ref(side, "PROTECTIVE_LEG", long),
      ref(side, "NEAR_BREAKEVEN", be),
    ],
    policy_link: {
      candidate_id: side,
      candidate_version: `${side}@fixture`,
      contract: { side, short_strike_usd: short, long_strike_usd: long, expiry_ms: 1790323200000 },
      region_liability_rows: [],
      region_liability_full_rows: [],
    },
  };
  if (cutoff !== undefined) map.cutoff_at_ms = cutoff;
  return map;
};
const option = (key, side, map) => ({ key, side, hasMap: true, candidate: { btc_map: map } });
const byRole = (group, role) => group.refs.find((item) => item.role === role);
const assertOnAxis = (marker, name, expectedX) => {
  if (!marker) throw new Error(`${name} missing`);
  if (marker.edge || marker.offscreen) throw new Error(`${name} was clamped/offscreen: ${JSON.stringify(marker)}`);
  if (Math.abs(marker.x1 - marker.rawX1) > 1e-6) throw new Error(`${name} raw/display x diverged`);
  if (Math.abs(marker.x1 - expectedX) > 1e-6) throw new Error(`${name} x=${marker.x1}, expected ${expectedX}`);
};
const contractBandWidths = (svg) =>
  [...svg.matchAll(/<rect class="map-contract-band[^"]*" x="[^"]+" y="[^"]+" width="([0-9.]+)"/g)].map((match) => Number(match[1]));

const putMap = contractMap("put", 97000, 95000, 96729.15, 1000);
const callMap = contractMap("call", 103000, 105000, 103247.79, 1000);

testApi.state.selectedMapCandidateKey = "put";
testApi.state.mapAxisMode = "all";
let model = api.buildMapAxisModel({
  map: putMap,
  candidate: { btc_map: putMap },
  label: "Put 保单",
  source: "candidate",
  options: [option("put", "Put", putMap), option("call", "Call", callMap)],
});
const putGroup = model.contracts.find((item) => item.side === "Put");
const callGroup = model.contracts.find((item) => item.side === "Call");
if (!putGroup || !callGroup || !callGroup.sameCutoff) {
  throw new Error(`all mode should include both same-cutoff contracts: ${JSON.stringify(model.contracts.map((item) => ({side: item.side, sameCutoff: item.sameCutoff})))}`);
}
assertOnAxis(byRole(callGroup, "PROTECTIVE_LEG"), "Put-selected Call protective 105000", model.toX(105000, 126, 900));
assertOnAxis(byRole(callGroup, "SHORT_LEG"), "Put-selected Call short 103000", model.toX(103000, 126, 900));
assertOnAxis(byRole(putGroup, "PROTECTIVE_LEG"), "Put protective 95000", model.toX(95000, 126, 900));
const putWidth = Math.abs(byRole(putGroup, "SHORT_LEG").x1 - byRole(putGroup, "PROTECTIVE_LEG").x1);
const callWidth = Math.abs(byRole(callGroup, "SHORT_LEG").x1 - byRole(callGroup, "PROTECTIVE_LEG").x1);
if (Math.abs(putWidth - callWidth) > 1e-6) {
  throw new Error(`equal 2000 USD legs should have equal desktop width: put=${putWidth}, call=${callWidth}`);
}
const desktopSvg = api.renderMapAxisSvg(model, false);
if (!desktopSvg.includes("保护腿 105,000")) throw new Error("desktop SVG should label the 105000 protective leg");
const desktopWidths = contractBandWidths(desktopSvg);
if (desktopWidths.length !== 2 || desktopWidths.some((width) => Math.abs(width - putWidth) > 0.01)) {
  throw new Error(`desktop SVG contract band widths should match model geometry: ${desktopWidths.join(",")} vs ${putWidth}`);
}
const compactSvg = api.renderMapAxisSvg(model, true);
const compactPutWidth = Math.abs(model.toX(97000, 24, 350) - model.toX(95000, 24, 350));
const compactCall105X = model.toX(105000, 24, 350);
if (!compactSvg.includes(`cx="${compactCall105X.toFixed(2)}"`)) {
  throw new Error(`compact SVG should render Call protective at raw x ${compactCall105X.toFixed(2)}`);
}
const compactWidths = contractBandWidths(compactSvg);
if (compactWidths.length !== 2 || compactWidths.some((width) => Math.abs(width - compactPutWidth) > 0.01)) {
  throw new Error(`compact SVG contract band widths should match remapped geometry: ${compactWidths.join(",")} vs ${compactPutWidth}`);
}

testApi.state.selectedMapCandidateKey = "call";
testApi.state.mapAxisMode = "all";
model = api.buildMapAxisModel({
  map: callMap,
  candidate: { btc_map: callMap },
  label: "Call 保单",
  source: "candidate",
  options: [option("put", "Put", putMap), option("call", "Call", callMap)],
});
const callSelected = model.contracts.find((item) => item.side === "Call");
assertOnAxis(byRole(callSelected, "PROTECTIVE_LEG"), "Call-selected Call protective 105000", model.toX(105000, 126, 900));

testApi.state.selectedMapCandidateKey = "put";
testApi.state.mapAxisMode = "focus";
model = api.buildMapAxisModel({
  map: putMap,
  candidate: { btc_map: putMap },
  label: "Put 保单",
  source: "candidate",
  options: [option("put", "Put", putMap), option("call", "Call", callMap)],
});
if (model.contracts.some((item) => item.side === "Call")
    || !model.offAxisContractGroups.some((item) => item.side === "Call" && item.sameCutoff === true)) {
  throw new Error("focus mode should keep the far same-cutoff peer below the axis");
}

testApi.state.mapAxisMode = "all";
const callDifferentCutoff = contractMap("call", 103000, 105000, 103247.79, 4000);
model = api.buildMapAxisModel({
  map: putMap,
  candidate: { btc_map: putMap },
  label: "Put 保单",
  source: "candidate",
  options: [option("put", "Put", putMap), option("call", "Call", callDifferentCutoff)],
});
if (model.contracts.some((item) => item.side === "Call")
    || !model.offAxisContractGroups.some((item) => item.side === "Call" && item.sameCutoff === false)
    || model.domain.max > 103000) {
  throw new Error(`different-cutoff peer must not expand all-domain: ${JSON.stringify({domain: model.domain, contracts: model.contracts, offAxis: model.offAxisContractGroups})}`);
}

const putMissingCutoff = contractMap("put", 97000, 95000, 96729.15, undefined);
const callMissingCutoff = contractMap("call", 103000, 105000, 103247.79, undefined);
model = api.buildMapAxisModel({
  map: putMissingCutoff,
  candidate: { btc_map: putMissingCutoff },
  label: "Put 保单",
  source: "candidate",
  options: [option("put", "Put", putMissingCutoff), option("call", "Call", callMissingCutoff)],
});
if (model.contracts.some((item) => item.side === "Call")
    || !model.offAxisContractGroups.some((item) => item.side === "Call" && item.sameCutoff === false)) {
  throw new Error("both-missing cutoff must not be treated as same-version peer");
}

const quoteClockOnly = (map, quoteTime) => {
  map.presentation.reference_time_ms = quoteTime;
  map.presentation.candidate_identity.reference_time_ms = quoteTime;
  map.presentation.price_axis.current.time_ms = quoteTime;
  map.updated_at_ms = quoteTime;
  return map;
};
const putQuoteOnlyCutoff = quoteClockOnly(contractMap("put", 97000, 95000, 96729.15, undefined), 7000);
const callQuoteOnlyCutoff = quoteClockOnly(contractMap("call", 103000, 105000, 103247.79, undefined), 7000);
model = api.buildMapAxisModel({
  map: putQuoteOnlyCutoff,
  candidate: { btc_map: putQuoteOnlyCutoff },
  label: "Put 保单",
  source: "candidate",
  options: [option("put", "Put", putQuoteOnlyCutoff), option("call", "Call", callQuoteOnlyCutoff)],
});
if (model.contracts.some((item) => item.side === "Call")
    || !model.offAxisContractGroups.some((item) => item.side === "Call" && item.sameCutoff === false)
    || model.domain.max > 103000) {
  throw new Error("matching quote/update clocks without explicit MAP cutoff must not create same-version all-axis geometry");
}

const callSingleMissingCutoff = quoteClockOnly(contractMap("call", 103000, 105000, 103247.79, undefined), 1000);
model = api.buildMapAxisModel({
  map: putMap,
  candidate: { btc_map: putMap },
  label: "Put 保单",
  source: "candidate",
  options: [option("put", "Put", putMap), option("call", "Call", callSingleMissingCutoff)],
});
if (model.contracts.some((item) => item.side === "Call")
    || !model.offAxisContractGroups.some((item) => item.side === "Call" && item.sameCutoff === false)
    || model.domain.max > 103000) {
  throw new Error("single missing explicit MAP cutoff must not inherit same quote clock for all-axis geometry");
}

model = api.buildMapAxisModel({
  map: putMap,
  candidate: null,
  label: "全局 MAP",
  source: "global",
  options: [option("put", "Put", putMap), option("call", "Call", callMap)],
});
if (model.contracts.length || model.offAxisContractGroups.length) {
  throw new Error("unselected global MAP must not borrow candidate contracts in all mode");
}

console.log(JSON.stringify({ allDomain: model.domain, desktopWidths, compactWidths }));
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert_true(result.returncode == 0, result.stdout + result.stderr)
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert_true(len(payload["desktopWidths"]) == 2, "desktop should render both same-cutoff contract bands")
    assert_true(len(payload["compactWidths"]) == 2, "compact should render both same-cutoff contract bands")
