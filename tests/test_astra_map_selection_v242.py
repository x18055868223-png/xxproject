import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "deploy" / "signal_audit" / "frontend"


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def test_v242_map_default_selection_tracks_watch_region_and_user_events():
    node = shutil.which("node")
    if not node:
        return
    script = r"""
const fs = require("fs");
const vm = require("vm");
const app = fs.readFileSync("deploy/signal_audit/frontend/app.js", "utf8");

class FakeElement {
  constructor(owner, attrs = {}) {
    this.owner = owner;
    this.attrs = attrs;
    this.listeners = {};
    this.value = attrs.value || "";
    this.dataset = {};
    for (const [key, value] of Object.entries(attrs)) {
      if (key.startsWith("data-")) {
        const name = key.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
        this.dataset[name] = value;
      }
    }
    this.classList = { add() {}, remove() {}, toggle() {} };
  }
  getAttribute(name) {
    return this.attrs[name] || "";
  }
  addEventListener(type, fn) {
    this.listeners[type] = fn;
  }
  dispatch(type) {
    const fn = this.listeners[type];
    if (fn) fn({ target: this, key: type, preventDefault() {} });
  }
  querySelectorAll(selector) {
    return this.owner.queryWithin(selector);
  }
  set innerHTML(value) {
    this._innerHTML = String(value || "");
    this.owner.sync(this._innerHTML);
  }
  get innerHTML() {
    return this._innerHTML || "";
  }
}

class FakeDocument {
  constructor() {
    this.readyState = "loading";
    this.root = new FakeElement(this);
    this.select = null;
    this.regionButtons = [];
    this.regionSelects = [];
    this.axisButtons = [];
  }
  addEventListener() {}
  querySelector(selector) {
    if (selector === "#btcMap") return this.root;
    if (selector === "#mapCandidateSelect") return this.select;
    return null;
  }
  querySelectorAll() {
    return [];
  }
  queryWithin(selector) {
    if (selector === "[data-map-region-id]") return this.regionButtons;
    if (selector === "[data-map-region-select]") return this.regionSelects;
    if (selector === "[data-map-axis-mode]") return this.axisButtons;
    return [];
  }
  sync(html) {
    this.regionButtons = [...html.matchAll(/data-map-region-id="([^"]+)"/g)]
      .map((match) => new FakeElement(this, { "data-map-region-id": match[1] }));
    this.regionSelects = [...html.matchAll(/data-map-region-select/g)]
      .map(() => new FakeElement(this, { "data-map-region-select": "" }));
    this.axisButtons = [...html.matchAll(/data-map-axis-mode="([^"]+)"/g)]
      .map((match) => new FakeElement(this, { "data-map-axis-mode": match[1] }));
    this.select = html.includes('id="mapCandidateSelect"') ? new FakeElement(this, { id: "mapCandidateSelect" }) : null;
  }
}

let fetchCalls = 0;
const fakeDocument = new FakeDocument();
const sandbox = {
  console,
  document: fakeDocument,
  window: {
    location: { hostname: "127.0.0.1", protocol: "http:" },
    document: fakeDocument,
    setInterval: () => 0,
    clearInterval() {},
    innerWidth: 1440,
  },
  fetch: () => {
    fetchCalls += 1;
    throw new Error("network must not be used by MAP selection rendering");
  },
  setTimeout: () => 0,
  clearTimeout() {},
};
sandbox.window.fetch = sandbox.fetch;
vm.createContext(sandbox);
vm.runInContext(app, sandbox, { filename: "app.js" });

const testApi = sandbox.window.__astraWorkbenchTest;
if (!testApi || typeof testApi.renderAll !== "function") {
  throw new Error("workbench render test hook missing");
}

const region = (id, label, low, high, extra = {}) => ({
  region_id: id,
  region_label_cn: label,
  price_range_cn: `${low}-${high}`,
  coordinate_qualification: extra.coordinate_qualification || "COMPARABLE",
  relationship_cn: extra.relationship_cn || "同版责任",
  payout_range_btc_cn: extra.payout_range_btc_cn || "0 - 0.01 BTC",
  static_net_result_range_btc_cn: extra.static_net_result_range_btc_cn || "-0.01 - 0.01 BTC",
  terminal_scenario_basis: extra.terminal_scenario_basis || "official_settlement_inside_contract_comparable_coordinate",
  highlighted: extra.highlighted === true,
});

const reference = (id, label, low, high, extra = {}) => ({
  reference_id: id,
  label_cn: label,
  family: extra.family || "TRADED_ACCEPTANCE",
  role: extra.role || "OBSERVED_ZONE",
  display_low_usd: low,
  display_high_usd: high,
  price_basis: "DERIBIT_BTC_USD_INDEX",
  coordinate_qualification: extra.coordinate_qualification || "COMPARABLE",
  eligible_for_display: true,
});

function mapFor(side, version, watchId, rows, extra = {}) {
  const lower = side.toLowerCase();
  const short = side === "Put" ? 97000 : 103000;
  const long = side === "Put" ? 95000 : 105000;
  const refs = [
    ...rows.map((row, index) => reference(row.region_id, row.region_label_cn, 96000 + index * 900, 96500 + index * 900,
      { coordinate_qualification: row.coordinate_qualification || "COMPARABLE" })),
    reference(`${lower}-${version}-short`, `${side} 卖出腿`, short, short, { family: "CONTRACT_LIABILITY", role: "SHORT_LEG" }),
    reference(`${lower}-${version}-long`, `${side} 保护腿`, long, long, { family: "CONTRACT_LIABILITY", role: "PROTECTIVE_LEG" }),
    reference(`${lower}-${version}-be`, `${side} 近端BE`, side === "Put" ? 96680 : 103240, side === "Put" ? 96680 : 103240,
      { family: "CONTRACT_LIABILITY", role: "NEAR_BREAKEVEN" }),
  ];
  return {
    cutoff_at_ms: extra.cutoff || (version === "v2" ? 2000 : 1000),
    current_price_usd: 100000,
    price_basis: "DERIBIT_BTC_USD_INDEX",
    display_reference_ids: rows.map((row) => row.region_id),
    reference_pool: refs,
    presentation: {
      mode: "FIXTURE",
      candidate_identity: {
        reference_time_ms: extra.cutoff || (version === "v2" ? 2000 : 1000),
        current_price_usd: 100000,
        reference_source: "DERIBIT_BTC_USD_INDEX",
        short_strike_usd: short,
        long_strike_usd: long,
        quantity_btc: 1,
        expiry_ms: 86401000,
      },
      price_axis: { current: { price_usd: 100000, source: "DERIBIT_BTC_USD_INDEX", time_ms: 1000 } },
      decision_brief: {
        pushes: [{ title_cn: `${side} 背景`, text_cn: "测试背景", source_state_cn: "结构有效" }],
        conflicts: [{ kind_cn: "测试冲突", text_cn: "测试冲突保留" }],
        policy_position: { summary_cn: `${side} 本单位置`, checkpoints: [] },
        watch: watchId ? {
          qualified: true,
          primary_region_id: watchId,
          title_cn: `${side} 优先观察`,
          reason_cn: "靠近责任起点",
          check_cn: "观察是否进入",
          change_cn: "连续进入改变判断",
        } : extra.watch || {},
      },
    },
    background: {},
    policy_link: {
      candidate_id: `${lower}-${version}`,
      candidate_version: `${lower}@${version}`,
      contract: { side, short_strike_usd: short, long_strike_usd: long, expiry_ms: 86401000 },
      region_liability_rows: rows.slice(0, 2),
      region_liability_full_rows: rows,
    },
  };
}

function candidate(side, map) {
  return { side, btc_map: map, snapshot: { side, candidate_id: `${side.toLowerCase()}-${map.policy_link.candidate_version}`, snapshot_id: `snapshot-${map.policy_link.candidate_version}` } };
}

const putMap = mapFor("Put", "v1", "put-watch", [
  region("put-first", "Put 第一责任区", 94000, 95000, { highlighted: true }),
  region("put-watch", "Put 推荐责任区", 95000, 96000),
]);
const callMap = mapFor("Call", "v1", "call-watch", [
  region("call-first", "Call 第一责任区", 103000, 104000, { highlighted: true }),
  region("call-watch", "Call 推荐责任区", 104000, 105000),
  region("call-nominal", "Call 名义观察区", 105000, 106000, { coordinate_qualification: "NOMINAL_ONLY" }),
]);

testApi.state.apiOnline = true;
testApi.state.loading = false;
testApi.state.workbench = { view: { btc_map: { presentation: {}, background: {} } }, candidates: [candidate("Put", putMap), candidate("Call", callMap)] };
testApi.state.selectedMapCandidateKey = "";
testApi.state.selectedMapRegionId = "";
testApi.renderAll();
if (!fakeDocument.root.innerHTML.includes("先选择 Put 或 Call 保单") || testApi.state.selectedMapRegionId !== "") {
  throw new Error("unselected MAP should not borrow any policy region");
}

fakeDocument.select.value = "call";
fakeDocument.select.dispatch("change");
if (testApi.state.selectedMapCandidateKey !== "call") throw new Error("candidate select change was not applied");
if (testApi.state.selectedMapRegionId !== "call-watch") {
  throw new Error(`Call default should use decision_brief.watch.primary_region_id, got ${testApi.state.selectedMapRegionId}`);
}
let policyPanelStart = fakeDocument.root.innerHTML.indexOf('aria-labelledby="mapPolicyTitle"');
let selectedPolicyHtml = fakeDocument.root.innerHTML.slice(policyPanelStart, fakeDocument.root.innerHTML.indexOf("<summary>展开保单责任审计表"));
if (!selectedPolicyHtml.includes("Call 推荐责任区") || selectedPolicyHtml.includes("<strong>Call 第一责任区</strong>")) {
  throw new Error("Call selected region card did not follow the watch recommendation");
}
if (!/<g class="[^"]*is-selected[^"]*"[^>]+data-map-region-id="call-watch"/.test(fakeDocument.root.innerHTML)) {
  throw new Error("Call watch marker should be selected on the first render after candidate change");
}

const nominalButton = fakeDocument.regionButtons.find((item) => item.getAttribute("data-map-region-id") === "call-nominal");
if (!nominalButton) throw new Error("nominal region button missing from rendered event flow");
nominalButton.dispatch("click");
if (testApi.state.selectedMapRegionId !== "call-nominal" || !fakeDocument.root.innerHTML.includes("Call 名义观察区")) {
  throw new Error("explicit user region click should override the default watch region");
}
const sameMapContextKey = testApi.state.selectedMapRegionContextKey;
testApi.renderAll();
if (testApi.state.selectedMapRegionId !== "call-nominal" || testApi.state.selectedMapRegionContextKey !== sameMapContextKey) {
  throw new Error("explicit user selection should remain inside the same candidate/MAP context");
}

fakeDocument.select.value = "put";
fakeDocument.select.dispatch("change");
if (testApi.state.selectedMapRegionId !== "put-watch") {
  throw new Error(`Put default should use decision_brief.watch.primary_region_id, got ${testApi.state.selectedMapRegionId}`);
}
policyPanelStart = fakeDocument.root.innerHTML.indexOf('aria-labelledby="mapPolicyTitle"');
selectedPolicyHtml = fakeDocument.root.innerHTML.slice(policyPanelStart, fakeDocument.root.innerHTML.indexOf("<summary>展开保单责任审计表"));
if (!selectedPolicyHtml.includes("Put 推荐责任区") || selectedPolicyHtml.includes("<strong>Put 第一责任区</strong>")) {
  throw new Error("Put selected region card did not follow the watch recommendation");
}

const callNoWatch = mapFor("Call", "v1", "", [
  region("call-no-watch-first", "Call 无推荐第一责任区", 101000, 102000, { highlighted: true }),
  region("call-no-watch-second", "Call 无推荐第二责任区", 102000, 103000),
]);
testApi.state.workbench = { view: { btc_map: { presentation: {}, background: {} } }, candidates: [candidate("Call", callNoWatch)] };
testApi.state.selectedMapCandidateKey = "call";
testApi.state.selectedMapRegionId = "";
testApi.renderAll();
if (testApi.state.selectedMapRegionId !== "") {
  throw new Error(`missing watch recommendation must not select an arbitrary row, got ${testApi.state.selectedMapRegionId}`);
}
if (!fakeDocument.root.innerHTML.includes("未返回有效优先观察区域")) {
  throw new Error("missing watch recommendation should be explicit instead of showing a fake priority");
}

const callNew = mapFor("Call", "v2", "call-new-watch", [
  region("call-new-first", "Call 新版第一责任区", 106000, 107000),
  region("call-new-watch", "Call 新版推荐责任区", 107000, 108000),
]);
testApi.state.workbench = { view: { btc_map: { presentation: {}, background: {} } }, candidates: [candidate("Call", callNew)] };
testApi.state.selectedMapCandidateKey = "call";
testApi.state.selectedMapRegionId = "call-old-watch";
testApi.renderAll();
if (testApi.state.selectedMapRegionId !== "call-new-watch" || !fakeDocument.root.innerHTML.includes("Call 新版推荐责任区")) {
  throw new Error(`stale region should be cleared and replaced by same-version watch region, got ${testApi.state.selectedMapRegionId}`);
}

const callSharedA = mapFor("Call", "sameA", "shared-old-watch", [
  region("shared-old-watch", "Call A 旧推荐区", 108000, 109000),
  region("shared-stable", "Call A 手选共用区", 109000, 110000),
]);
const callSharedB = mapFor("Call", "sameB", "shared-new-watch", [
  region("shared-old-watch", "Call B 同ID旧区", 108000, 109000),
  region("shared-new-watch", "Call B 新推荐区", 110000, 111000),
  region("shared-stable", "Call B 共用区", 111000, 112000),
]);
testApi.state.workbench = { view: { btc_map: { presentation: {}, background: {} } }, candidates: [candidate("Call", callSharedA)] };
testApi.state.selectedMapCandidateKey = "call";
testApi.state.selectedMapRegionId = "";
testApi.state.selectedMapRegionContextKey = "";
testApi.renderAll();
const sharedButton = fakeDocument.regionButtons.find((item) => item.getAttribute("data-map-region-id") === "shared-stable");
if (!sharedButton) throw new Error("shared user-selectable region missing");
sharedButton.dispatch("click");
if (testApi.state.selectedMapRegionId !== "shared-stable") {
  throw new Error("same-version explicit shared region was not selected");
}
testApi.state.workbench = { view: { btc_map: { presentation: {}, background: {} } }, candidates: [candidate("Call", callSharedB)] };
testApi.state.selectedMapCandidateKey = "call";
testApi.renderAll();
policyPanelStart = fakeDocument.root.innerHTML.indexOf('aria-labelledby="mapPolicyTitle"');
selectedPolicyHtml = fakeDocument.root.innerHTML.slice(policyPanelStart, fakeDocument.root.innerHTML.indexOf("<summary>展开保单责任审计表"));
if (testApi.state.selectedMapRegionId !== "shared-new-watch" || !selectedPolicyHtml.includes("Call B 新推荐区") || selectedPolicyHtml.includes("Call B 共用区")) {
  throw new Error(`same-side new snapshot should clear an old explicit region even when the old region_id still exists, got ${testApi.state.selectedMapRegionId}`);
}

fakeDocument.select.value = "";
fakeDocument.select.dispatch("change");
if (testApi.state.selectedMapRegionId !== "" || testApi.state.selectedMapRegionContextKey !== "" || !fakeDocument.root.innerHTML.includes("全局 MAP 不套用上一张保单的金额解释")) {
  throw new Error("switching back to unselected should clear policy-region state");
}
if (fetchCalls !== 0) throw new Error(`MAP render/event selection should not touch network, got ${fetchCalls} calls`);
console.log("v242-map-selection: ok");
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert_true(result.returncode == 0, result.stdout + result.stderr)


def test_v242_map_source_clock_rows_and_manual_review_reason_labels():
    node = shutil.which("node")
    if not node:
        return
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
    location: { hostname: "127.0.0.1", protocol: "http:" },
    document: fakeDocument,
    setInterval: () => 0,
    clearInterval() {},
    innerWidth: 1440,
  },
  setTimeout: () => 0,
  clearTimeout() {},
};
vm.createContext(sandbox);
vm.runInContext(app, sandbox, { filename: "app.js" });
const mapApi = sandbox.window.__astraMapAxisV241;
const workbenchApi = sandbox.window.__astraWorkbenchTest;
if (!mapApi || typeof mapApi.renderMapBackground !== "function" || !workbenchApi || typeof workbenchApi.label !== "function") {
  throw new Error("MAP/source-clock test hooks missing");
}
const map = {
  cutoff_at_ms: 1893456000000,
  presentation: {
    mode: "FIXTURE",
    background_rows: [{
      key: "allocation",
      title_cn: "配置",
      fact_state: "partial",
      cutoff_at_ms: 1893456000000,
      source_observation_end_ms: 1893456000000,
      source_explanation: {
        source_cn: "聚合来源",
        market_cn: "BTC 背景",
        measure_cn: "多产品源钟",
        window_cn: "混合窗口",
        qualification_cn: "逐产品判断",
      },
      source_clock_rows: [
        {
          product_id: "btc.etf.flow.daily.v1",
          source_cn: "Farside",
          observation_start_ms: 1000,
          observation_end_ms: 2000,
          first_seen_at_ms: 3000,
          usage_status: "current",
          eligible: true,
          reasons: ["ok"],
        },
        {
          product_id: "macro.usd.broad.close.v1",
          source_cn: "FRED",
          observation_start_ms: 0,
          observation_end_ms: 0,
          first_seen_at_ms: 0,
          usage_status: "missing_or_invalid",
          eligible: false,
          reasons: [
            "source_clock_missing",
            "map_cutoff_missing",
            "source_manifest_cutoff_missing",
            "source_manifest_cutoff_mismatch",
            "observation_after_first_seen",
            "observation_after_cutoff",
            "first_seen_after_cutoff",
            "published_after_cutoff",
            "qualified_after_cutoff",
            "observation_start_invalid",
            "observation_start_after_end",
            "observation_start_after_cutoff",
          ],
        },
      ],
      primary_metrics: [{ label: "测试指标", value: 1, unit: "USD" }],
    }],
  },
};
const html = mapApi.renderMapBackground(map);
for (const text of ["分产品源钟", "Farside", "现货 BTC ETF 日净流量", "FRED", "广义美元 DTWEXBGS", "用途合格", "用途不合格", "缺失或无效", "缺少观察起点", "缺少观察截止", "缺少首次可得"]) {
  if (!html.includes(text)) throw new Error(`source-clock rows should include ${text}`);
}
if (html.includes("<span>FRED / macro.usd.broad.close.v1</span>") || html.includes("<span>macro.usd.broad.close.v1</span>")) {
  throw new Error("source-clock main line should prefer Chinese product text over raw product_id");
}
const macroAt = html.indexOf("FRED");
const macroLine = html.slice(macroAt, html.indexOf("</li>", macroAt));
if (macroLine.includes("2030")) {
  throw new Error("missing product clocks must not be backfilled from the row-level cutoff or source observation time");
}
if (macroLine.includes("1970") || macroLine.includes("08:00:00")) {
  throw new Error("zero product clocks must render as missing text, not the Unix epoch");
}
if (macroLine.includes("缺原生 SpaceGate")) {
  throw new Error("source-clock usage status should not reuse SpaceGate-specific labels");
}
for (const text of [
  "源钟缺少观察截止或首次可得",
  "MAP截止缺失",
  "来源清单截止缺失",
  "来源清单截止与MAP截止不一致",
  "观察截止晚于首次可得",
  "观察截止晚于MAP截止",
  "首次可得晚于MAP截止",
  "发布时间晚于MAP截止",
  "资格判定晚于MAP截止",
  "观察起点非正",
  "观察起点晚于观察截止",
  "观察起点晚于MAP截止",
]) {
  if (!macroLine.includes(text)) throw new Error(`source-clock reason should be localized: ${text}`);
}
for (const [code, text] of [
  ["manual_review_cooldown_active", "手动评审冷却中"],
  ["manual_review_inflight", "同版手动评审正在推理"],
  ["manual_review_budget_exhausted", "手动评审额度已用尽"],
]) {
  const got = workbenchApi.label(code);
  if (got !== text) throw new Error(`${code} should render as ${text}, got ${got}`);
}
console.log("v242-source-clock-labels: ok");
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert_true(result.returncode == 0, result.stdout + result.stderr)


def main():
    test_v242_map_default_selection_tracks_watch_region_and_user_events()
    test_v242_map_source_clock_rows_and_manual_review_reason_labels()
    print("astra_map_selection_v242: PASS")


if __name__ == "__main__":
    main()
