import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "deploy" / "signal_audit" / "frontend"


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def test_v241_axis_model_when_node_is_available():
    node = shutil.which("node")
    if not node:
        return
    script = r"""
const fs = require("fs");
const vm = require("vm");
const app = fs.readFileSync("deploy/signal_audit/frontend/app.js", "utf8");
const fakeDocument = {
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: () => {},
};
const sandbox = {
  console,
  document: fakeDocument,
  window: {
    location: { hostname: "127.0.0.1", protocol: "http:" },
    setInterval: () => 0,
    clearInterval: () => {},
  },
  setTimeout: () => 0,
  clearTimeout: () => {},
};
sandbox.window.document = fakeDocument;
vm.createContext(sandbox);
vm.runInContext(app, sandbox, { filename: "app.js" });
const api = sandbox.window.__astraMapAxisV241;
if (!api || typeof api.buildMapAxisModel !== "function") {
  throw new Error("axis test hook missing");
}
const contract = (side, short, long, be, cutoff) => ({
  cutoff_at_ms: cutoff,
  current_price_usd: 100000,
  price_basis: "DERIBIT_BTC_USD_INDEX",
  display_reference_ids: ["near", "far", "tiny", "unknown", "global", "cp"],
  presentation: {
    mode: "FIXTURE",
    mode_cn: "合成测试",
    candidate_identity: { reference_time_ms: cutoff, current_price_usd: 100000, reference_source: "DERIBIT_BTC_USD_INDEX" },
    price_axis: {
      current: { price_usd: 100000, source: "DERIBIT_BTC_USD_INDEX", time_ms: cutoff },
    },
  },
  background: {},
  reference_pool: [
    { reference_id: "near", family: "TRADED_ACCEPTANCE", role: "OBSERVED_ZONE",
      display_low_usd: 99000, display_high_usd: 101000, eligible_for_display: true,
      coordinate_qualification: "COMPARABLE", label_cn: "近端接受区" },
    { reference_id: "far", family: "TRADED_ACCEPTANCE", role: "OBSERVED_ZONE",
      display_low_usd: 88000, display_high_usd: 89000, eligible_for_display: true,
      coordinate_qualification: "COMPARABLE", label_cn: "远端接受区" },
    { reference_id: "tiny", family: "OPTION_STRUCTURE", role: "EFFECTIVE_FLIP",
      display_low_usd: 99999.8, display_high_usd: 100000.2, eligible_for_display: true,
      coordinate_qualification: "COMPARABLE", label_cn: "极窄结构区" },
    { reference_id: "unknown", family: "OPTION_STRUCTURE", role: "UNKNOWN",
      display_price_usd: 100500, eligible_for_display: true,
      coordinate_qualification: "UNKNOWN", label_cn: "未知资格参考" },
    { reference_id: "global", family: "OPTION_STRUCTURE", role: "GLOBAL_ONLY",
      display_price_usd: 101500, eligible_for_display: true,
      coordinate_qualification: "GLOBAL_ONLY", label_cn: "全局期限参考" },
    { reference_id: "cp", family: "ONCHAIN_COST", role: "CP_STH",
      display_price_usd: 98000, eligible_for_display: true,
      coordinate_qualification: "NOMINAL_ONLY", label_cn: "链上成本 CP_STH" },
    { reference_id: `contract_${side}_short`, family: "CONTRACT_LIABILITY", role: "SHORT_LEG",
      raw_price: short, display_price_usd: short, coordinate_qualification: "COMPARABLE",
      eligible_for_display: true, expiry_scope: 1790323200000 },
    { reference_id: `contract_${side}_long`, family: "CONTRACT_LIABILITY", role: "PROTECTIVE_LEG",
      raw_price: long, display_price_usd: long, coordinate_qualification: "COMPARABLE",
      eligible_for_display: true, expiry_scope: 1790323200000 },
    { reference_id: `contract_${side}_be`, family: "CONTRACT_LIABILITY", role: "NEAR_BREAKEVEN",
      raw_price: be, display_price_usd: be, coordinate_qualification: "COMPARABLE",
      eligible_for_display: true, expiry_scope: 1790323200000 },
  ],
  policy_link: {
    candidate_id: side,
    candidate_version: `${side}@demo`,
    contract: { side, short_strike_usd: short, long_strike_usd: long, expiry_ms: 1790323200000 },
    region_liability_rows: [],
    region_liability_full_rows: [],
  },
});
const putMap = contract("put", 97000, 95000, 96729.15, 1000);
putMap.presentation.price_axis.references = putMap.reference_pool.map(ref => ({...ref, selected_for_display: true}));
putMap.presentation.price_axis.references.push({reference_id: "not-selected-zone", family: "TRADED_ACCEPTANCE", role: "KPF",
  raw_low: 102000, raw_high: 102100, eligible_for_display: true, selected_for_display: false, coordinate_qualification: "COMPARABLE"});
const callMap = contract("call", 103000, 105000, 103247.79, 4000);
const callSameCutoffMap = contract("call", 103000, 105000, 103247.79, 1000);
const model = api.buildMapAxisModel({
  map: putMap,
  candidate: { btc_map: putMap },
  label: "Put 保单",
  source: "candidate",
  options: [
    { key: "put", side: "Put", hasMap: true, candidate: { btc_map: putMap } },
    { key: "call", side: "Call", hasMap: true, candidate: { btc_map: callMap } },
  ],
});
const x97000 = model.toX(97000, 126, 900);
const globalModel = api.buildMapAxisModel({map: putMap, candidate: null, source: "global", options: [
  {key: "put", side: "Put", candidate: {btc_map: putMap}},
  {key: "call", side: "Call", candidate: {btc_map: callMap}},
]});
if (globalModel.contracts.length || globalModel.offAxisContractGroups.length) {
  throw new Error("unselected global MAP must not borrow one policy's contract geometry");
}
if (model.markers.some(item => item.id.startsWith("contract_") || item.id === "not-selected-zone")
    || !model.markers.some(item => item.id === "near")) {
  throw new Error("presentation must retain core display selection and separate contract roles");
}
const x98000 = model.toX(98000, 126, 900);
const x99000 = model.toX(99000, 126, 900);
if (Math.abs((x98000 - x97000) - (x99000 - x98000)) > 1e-6) {
  throw new Error("axis is not linear for equal price gaps");
}
if (!(model.domain.min > 90000 && model.domain.min < 97000)) {
  throw new Error(`focus domain was squeezed by far reference: ${JSON.stringify(model.domain)}`);
}
const nominal = model.markers.find((item) => item.id === "cp");
if (nominal) {
  throw new Error("nominal CP_STH should not be placed on the price axis");
}
const nominalList = model.nominalReferences.find((item) => item.id === "cp");
if (!nominalList || nominalList.x1 !== undefined || nominalList.x2 !== undefined || nominalList.comparable) {
  throw new Error("nominal CP_STH should stay off-axis without toX coordinates");
}
if (model.markers.some((item) => item.id === "unknown" || item.id === "global")) {
  throw new Error("UNKNOWN/GLOBAL_ONLY references must not enter comparable axis markers");
}
const far = model.markers.find((item) => item.id === "far");
if (!far || far.edge !== "left") {
  throw new Error("far comparable reference should retain edge full-value indicator");
}
const tiny = model.markers.find((item) => item.id === "tiny");
if (!tiny) {
  throw new Error("tiny comparable zone missing");
}
const tinyWidth = Math.abs(tiny.x2 - tiny.x1);
const svg = api.renderMapAxisSvg(model);
const tinyRect = new RegExp(`<rect x="${Math.min(tiny.x1, tiny.x2).toFixed(2)}" y="[^"]+" width="${tinyWidth.toFixed(2)}"`).test(svg);
if (!tinyRect) {
  throw new Error("tiny observed zone visible rect should keep true geometric width");
}
const putBe = model.contracts.find((item) => item.side === "Put").refs.find((item) => item.role === "NEAR_BREAKEVEN");
if (!putBe || Math.abs(putBe.x1 - model.toX(96729.15, 126, 900)) > 1e-6) {
  throw new Error("near BE x coordinate changed during label layout");
}
const compactSvg = api.renderMapAxisSvg(model, true);
if (!compactSvg.includes('viewBox="0 0 400') || compactSvg.includes('viewBox="0 0 1060')) {
  throw new Error("compact mobile SVG should use a readable 400px viewBox instead of scaled desktop geometry");
}
for (const text of ["选择区域", "map-region-select", "Put 合同", "卖出腿", "保护腿", "近端BE"]) {
  if (!compactSvg.includes(text)) throw new Error(`compact mobile axis should include ${text}`);
}
for (const [price, role] of [[97000, "short"], [95000, "long"], [96729.15, "be"]]) {
  const expected = model.toX(price, 24, 350);
  if (expected < 24 || expected > 374) throw new Error(`compact ${role} expected x escaped domain: ${expected}`);
  if (!compactSvg.includes(`cx="${expected.toFixed(2)}"`)) {
    throw new Error(`compact ${role} contract point should be remapped to readable mobile x ${expected.toFixed(2)}`);
  }
}
const labelYs = [...compactSvg.matchAll(/<text x="[^"]+" y="([0-9.]+)" text-anchor="(?:start|middle|end)">(?:卖出腿|保护腿|近端BE) /g)].map((match) => Number(match[1]));
if (labelYs.length < 3) throw new Error("compact contract labels should remain visible in the SVG");
const exercisedAnchors = new Set();
for (const map of [putMap, callSameCutoffMap]) {
  const layoutModel = api.buildMapAxisModel({map, candidate: {btc_map: map}, source: "candidate", options: []});
  const layoutSvg = api.renderMapAxisSvg(layoutModel, true);
  const labels = [...layoutSvg.matchAll(/<text x="([0-9.]+)" y="[^"]+" text-anchor="(start|middle|end)">(卖出腿|保护腿|近端BE) /g)];
  if (labels.length !== 3) throw new Error("each compact policy must retain exactly three contract labels");
  const prices = {"卖出腿": map.policy_link.contract.short_strike_usd,
    "保护腿": map.policy_link.contract.long_strike_usd,
    "近端BE": map.reference_pool.find(ref => ref.role === "NEAR_BREAKEVEN").raw_price};
  for (const [, x, anchor, role] of labels) {
    const priceX = layoutModel.toX(prices[role], 24, 350);
    if (Math.abs(Number(x) - priceX) > 0.0051) throw new Error(`${role} text moved away from its price coordinate`);
    const expectedAnchor = priceX < 100 ? "start" : priceX > 300 ? "end" : "middle";
    if (anchor !== expectedAnchor) throw new Error(`${role} uses ${anchor} at x=${x}, expected ${expectedAnchor}`);
    exercisedAnchors.add(anchor);
  }
}
if (exercisedAnchors.size !== 3) throw new Error("compact label fixtures must exercise left, middle and right positions");
const uniqueYs = [...new Set(labelYs.map((value) => value.toFixed(2)))].map(Number).sort((a, b) => a - b);
if (uniqueYs.length < 3 || uniqueYs.some((value, index) => index > 0 && value - uniqueYs[index - 1] < 21.5)) {
  throw new Error(`compact contract labels should use readable lane spacing, got ${uniqueYs.join(",")}`);
}
if (model.contracts.length !== 1) {
  throw new Error(`expected active and same-cutoff contract tracks only, got ${model.contracts.length}`);
}
if (!model.offAxisContractGroups.some((item) => item.sameCutoff === false && item.side === "Call")) {
  throw new Error("opposite side with different freeze time should be listed off-axis");
}
const sameCutoffFar = api.buildMapAxisModel({
  map: putMap,
  candidate: { btc_map: putMap },
  label: "Put 保单",
  source: "candidate",
  options: [
    { key: "put", side: "Put", hasMap: true, candidate: { btc_map: putMap } },
    { key: "call", side: "Call", hasMap: true, candidate: { btc_map: callSameCutoffMap } },
  ],
});
if (sameCutoffFar.contracts.some((item) => item.side === "Call")) {
  throw new Error("far same-cutoff opposite contract should move off-axis in focus mode instead of clamping");
}
if (!sameCutoffFar.offAxisContractGroups.some((item) => item.sameCutoff === true && item.side === "Call")) {
  throw new Error("same-cutoff far opposite contract should remain readable off-axis");
}
console.log(JSON.stringify({
  domain: model.domain,
  contracts: model.contracts.map((item) => ({ side: item.side, sameCutoff: item.sameCutoff })),
  offAxisContracts: model.offAxisContractGroups.map((item) => ({ side: item.side, sameCutoff: item.sameCutoff })),
  nominalIds: model.nominalReferences.map((item) => item.id),
}));
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
    assert_true("cp" in payload["nominalIds"], "nominal reference should be reported off-axis")
    assert_true(any(item["side"] == "Call" and item["sameCutoff"] is False for item in payload["offAxisContracts"]),
                "opposite-side contract should move off-axis with cutoff-difference label")


def test_v241_contract_documentation_strings():
    version = json.loads((FRONTEND / "VERSION.json").read_text(encoding="utf-8"))
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    index = (FRONTEND / "index.html").read_text(encoding="utf-8")
    for text in (
        "map.presentation is preferred",
        "linear numeric BTC USD SVG price axis",
        "Selected-region responsibility reads policy_link.region_liability_full_rows",
    ):
        assert_true(text in json.dumps(version, ensure_ascii=False), f"VERSION should document {text}")
    for text in (
        "数值价格轴",
        "本轮四问摘要",
        "presentation.decision_brief",
        "尚未生成背景推动摘要",
        "presentation.candidate_identity",
        "冻结时点剩余",
        "触区不是立即盈亏",
        "对侧冻结时点不同",
        "map-region-select",
        "safeNumber(window.innerWidth)",
        "Math.abs(other.x1 - marker.x1) < 140",
        "lane * 22",
        "contractRowHeight = 154",
    ):
        assert_true(text in app + index, f"v2.4.1 UI wording should include {text}")


def test_v241_real_presentation_labels_and_placeholder_events_when_node_is_available():
    node = shutil.which("node")
    if not node:
        return
    script = r"""
const fs = require("fs");
const vm = require("vm");
const app = fs.readFileSync("deploy/signal_audit/frontend/app.js", "utf8");
const fakeDocument = {
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: () => {},
};
const sandbox = {
  console,
  document: fakeDocument,
  window: {
    location: { hostname: "127.0.0.1", protocol: "http:" },
    setInterval: () => 0,
    clearInterval: () => {},
  },
  setTimeout: () => 0,
  clearTimeout: () => {},
};
sandbox.window.document = fakeDocument;
vm.createContext(sandbox);
vm.runInContext(app, sandbox, { filename: "app.js" });
const api = sandbox.window.__astraMapAxisV241;
if (!api || typeof api.renderMapBackground !== "function" || typeof api.renderMapFactSummary !== "function") {
  throw new Error("v2.4.1 background test hook missing");
}
const map = {
  presentation: {
    mode: "FIXTURE",
    decision_brief: {
      pushes: [
        { title_cn: "配置资金", text_cn: "ETF标准净流量折合BTC为背景需求，量价温和偏多。", basis_cn: "源价格折算，非实买BTC" },
        { title_cn: "库存", text_cn: "短期持有者资本化价格仍在现价下方。" },
        { title_cn: "融资", text_cn: "币安 U本位持仓量与资金费仅作背景。" },
        { title_cn: "外部压力", text_cn: "美元与利率背景未出现阻断性冲击。" },
      ],
      conflicts: [{ kind_cn: "方向与合同责任", text_cn: "温和偏多对卖Call不利，不是自动选边。" }],
      policy_position: {
        summary_cn: "Put 保单位于现价下方，先看卖出腿与保护腿之间。",
        checkpoints: [{ label_cn: "卖出腿", price_usd: 97000, distance_usd: -3000, distance_pct: -3 }],
      },
      watch: { qualified: true, primary_region_id: "near", title_cn: "优先观察卖出腿附近", reason_cn: "靠近赔付开始", check_cn: "看是否进入区域", change_cn: "连续进入会改变审查重点" },
    },
    background_rows: [
      {
        key: "inventory",
        title_cn: "库存",
        fact_state: "usable",
        cutoff_at_ms: 2000,
        source_observation_end_ms: 1000,
        source_explanation: { source_cn: "Bitview", market_cn: "BTC链上", measure_cn: "短期持有者资本化价格", window_cn: "日线", qualification_cn: "当前用于背景" },
        primary_metrics: [
          { label: "CP_STH", value: 73552.76, unit: "USD" },
          { label: "同源价距CP_STH", value: 12.345, unit: "%" },
          { label: "亏损份额", value: 0.29, unit: "%" },
          { label: "亏损金额前", value: 120, unit: "US$m" },
          { label: "亏损金额后", value: 95, unit: "US$m" },
          { label: "前期份额", value: 0.18, unit: "%" },
          { label: "抛压金额", value: null, unit: null, display_value: "未提供" },
        ],
      },
      {
        key: "financing",
        title_cn: "融资",
        fact_state: "partial",
        source_explanation: { source_cn: "Binance", market_cn: "BTCUSDT U本位永续", measure_cn: "原生持仓量与已结算资金费率", window_cn: "24h", qualification_cn: "背景事实" },
        primary_metrics: [
          { label: "币安 BTCUSDT USD-M 已结算Funding", value: 0.00012, unit: "rate_decimal", window_cn: "24h观察到的结算" },
          { label: "币安 BTCUSDT USD-M 原生OI变化", value: -1.4, unit: "%", window_cn: "24h" },
          { label: "币安 BTCUSDT USD-M 前段Funding", value: 0.0001, unit: "rate_decimal", window_cn: "前一24h" },
          { label: "空单位", value: 3, unit: null },
        ],
      },
    ],
  },
};
const html = api.renderMapBackground(map);
for (const text of ["合成·结构有效", "局部缺口", "已结算资金费率", "24h观察到的结算", "前期份额", "亏损金额前", "亏损金额后", "计算截止", "来源最晚观察", "来源 / 市场", "口径", "时窗", "资格", "短期持有者资本化价格", "BTCUSDT U本位永续"]) {
  if (!html.includes(text)) throw new Error(`background should include ${text}`);
}
const fullBriefHtml = api.renderMapFactSummary({ map, candidate: { side: "Call", btc_map: map }, label: "Call 保单" });
for (const text of ["配置资金", "库存", "融资", "外部压力", "对卖 Call：上行或偏多背景会增加触及卖出腿的压力"]) {
  if (!fullBriefHtml.includes(text)) throw new Error(`brief should include ${text}`);
}
if (fullBriefHtml.includes("源价格折算，非实买BTC")) {
  throw new Error("long source-method basis should stay out of the four-question main rows");
}
if (html.includes("指标 1") || html.includes("undefined")) {
  throw new Error("real primary_metrics labels and null units should not fall back to 指标1/undefined");
}
if (!html.includes("币安 BTCUSDT U本位持仓量变化（OI）")
    || !html.includes("币安 BTCUSDT U本位前一窗口资金费率")
    || html.includes("USD M") || html.includes("USD-M")) {
  throw new Error("actual producer financing labels must use Chinese U-margined terminology");
}
map.presentation.decision_brief.pushes[0].basis_cn = "净流量以美元百万计；BTC列是同版合格冻结价下的名义折合，不是实际BTC申赎数量。";
map.presentation.decision_brief.pushes[0].source_state_cn = "局部缺口；美元背景过期。";
const briefWithoutRepeatedMethods = api.renderMapFactSummary({ map, candidate: { side: "Call", btc_map: map }, label: "Call 保单" });
if (briefWithoutRepeatedMethods.includes("净流量以美元百万计")
    || !briefWithoutRepeatedMethods.includes("美元背景过期")) {
  throw new Error("brief must keep explicit qualification gaps without duplicating full source methodology");
}
const manyGaps = { ...map, presentation: { ...map.presentation, decision_brief: {
  ...map.presentation.decision_brief,
  conflicts: [
    { kind_cn: "时窗限制", text_cn: "ETF窗口对照限制" },
    { kind_cn: "数据缺口", text_cn: "GEX空间结构当前不可用" },
    { kind_cn: "数据缺口", text_cn: "KPF没有合格区域" },
    { kind_cn: "数据缺口", text_cn: "FMZ原生连续事实缺失" },
  ],
} } };
const groupedGaps = api.renderMapFactSummary({ map: manyGaps, candidate: null, label: "未选保单" });
const foldedAt = groupedGaps.indexOf("其他分歧与限制（1项）");
if (foldedAt < 0 || !groupedGaps.includes("ETF窗口对照限制")
    || ["GEX空间结构当前不可用", "KPF没有合格区域", "FMZ原生连续事实缺失"]
      .some(text => groupedGaps.indexOf(text) < 0 || groupedGaps.indexOf(text) > foldedAt)) {
  throw new Error("critical gaps must stay visible and other limitations must remain in counted details");
}
const translated = api.referenceDisplayTitle({ label_cn: "OPTION_STRUCTURE PUT_REFERENCE", family: "OPTION_STRUCTURE", role: "PUT_REFERENCE" });
if (!translated.includes("期权结构") || !translated.includes("Put 结构参考")) {
  throw new Error(`machine reference label was not translated: ${translated}`);
}
const kpf = api.referenceDisplayTitle({ label_cn: "TRADED_ACCEPTANCE KPF", family: "TRADED_ACCEPTANCE", role: "KPF" });
if (!kpf.includes("历史接受区") || !kpf.includes("KPF")) {
  throw new Error(`KPF reference label was not translated: ${kpf}`);
}
const noPath = api.responseFactsForRegion({
  presentation: {
    response_by_region: [
      { region_id: "r1", summary_cn: "尚无可验证区域响应", events: [{ event_cn: "尚无可验证区域响应" }] },
    ],
  },
}, { region_id: "r1" });
if (noPath.length !== 0) {
  throw new Error("placeholder response rows should not count as verifiable events");
}
const withPath = api.responseFactsForRegion({
  presentation: {
    response_by_region: [
      { region_id: "r2", events: [{ event_cn: "进入区间后回落", time_ms: 1000, method: "PRICE_SAMPLE" }] },
    ],
  },
}, { region_id: "r2" });
if (withPath.length !== 1) {
  throw new Error("timed response event should remain visible");
}
const emptyAxis = api.renderMapAxisSvg({
  domain: { min: 94000, max: 106000, width: 12000 },
  currentPrice: 100000,
  current: {},
  markers: [],
  contracts: [],
  toX: (price, left = 126, width = 900) => left + (price - 94000) / 12000 * width,
});
if (app.includes('"有可验证路径"') || !app.includes('"有可核对响应记录"')) {
  throw new Error("one verifiable position record must not claim a proven path");
}
if (!emptyAxis.includes("市场参照缺口") || emptyAxis.includes("本轨暂无合格可比参照")) {
  throw new Error("empty market tracks should collapse into one compact gap row");
}
const briefHtml = api.renderMapFactSummary({
  map,
  candidate: { side: "Call", snapshot: { side: "Call" } },
  label: "Call 保单",
});
for (const text of ["本轮四问摘要", "供需与背景推动", "主要矛盾和缺口", "本单所在位置", "优先观察及原因", "ETF标准净流量", "对卖 Call", "定位该区域"]) {
  if (!briefHtml.includes(text)) throw new Error(`decision brief should include ${text}`);
}
const missingBrief = api.renderMapFactSummary({ map: { presentation: {} }, candidate: null, label: "未选保单" });
if (!missingBrief.includes("尚未生成四问摘要") || !missingBrief.includes("未选保单")
    || !missingBrief.includes("不借入合成合同")) {
  throw new Error("missing decision brief should stay explicit and unselected policy should not borrow contract facts");
}
const policyMap = {
  cutoff_at_ms: 1000,
  presentation: {
    candidate_identity: {
      short_strike_usd: 97000,
      long_strike_usd: 95000,
      quantity_btc: 1,
      expiry_ms: 86_401_000,
      current_price_usd: 100000,
      net_credit_after_entry_fee_btc: 0.0123,
      M_ref_btc: 0.0045,
      near_breakeven_usd: 96729.15,
      reference_time_ms: 1000,
    },
    price_axis: { current: { price_usd: 100000, source: "DERIBIT_BTC_USD_INDEX", time_ms: 1000 } },
  },
};
const selectedIdentity = api.renderMapPolicyIdentity({
  map: policyMap,
  candidate: { side: "Put", snapshot: { side: "Put" }, review: { status: "OK" } },
  label: "Put 保单",
});
for (const text of ["Put 保单身份", "卖Put Ks", "买Put Kl", "冻结时点剩余", "净信用（入场费后）", "M_ref / 缺口", "近端 BE", "最近评审"]) {
  if (!selectedIdentity.includes(text)) throw new Error(`selected policy identity should include ${text}`);
}
if (selectedIdentity.includes("sha256") || selectedIdentity.includes("candidate_version") || selectedIdentity.includes("presentation.candidate_identity")) {
  throw new Error("policy identity strip should not expose technical hashes or versions");
}
const missingMRef = api.renderMapPolicyIdentity({
  map: { presentation: { candidate_identity: { short_strike_usd: 97000, long_strike_usd: 95000 } } },
  candidate: { side: "Put", snapshot: { side: "Put" } },
  label: "Put 保单",
});
if (!missingMRef.includes("M_ref 缺口")) {
  throw new Error("missing M_ref should render as a Chinese gap");
}
const unselected = api.renderMapPolicyIdentity({
  map: policyMap,
  candidate: null,
  label: "未选保单",
});
if (!unselected.includes("未选保单") || !unselected.includes("先选择 Put / Call") || unselected.includes("净信用（入场费后）")) {
  throw new Error("unselected MAP identity should only show current price and selection prompt");
}
console.log("labels-placeholder-events: ok");
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
    test_v241_axis_model_when_node_is_available()
    test_v241_contract_documentation_strings()
    test_v241_real_presentation_labels_and_placeholder_events_when_node_is_available()
    print("astra_map_ui_v241: PASS")


if __name__ == "__main__":
    main()
