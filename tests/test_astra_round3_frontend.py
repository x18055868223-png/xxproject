import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "deploy" / "signal_audit" / "frontend"
APP_JS = FRONTEND / "app.js"

sys.path.append(str(Path(__file__).resolve().parent))
from astra_workbench_frontend_harness import render_workbench  # noqa: E402


WORKBENCH_IDS = [
    "apiStatus",
    "previewBanner",
    "scenarioControl",
    "currentConclusion",
    "marketContext",
    "gateBadges",
    "spaceGate",
    "btcMap",
    "candidateArea",
    "manualReviewState",
    "dataHealth",
    "dailySnapshots",
    "transitions",
    "gapArea",
    "historyArea",
]


def _btc_map(map_id: str | None = "map-1", candidate_id: str = "candidate-put") -> dict:
    body = {
        "schema": "btc_map@1",
        "cutoff_at_ms": 1_790_323_200_000,
        "current_price_usd": 100000,
        "price_basis": "DERIBIT_BTC_USD_INDEX",
        "presentation": {
            "map_id": map_id,
            "mode": "SYNTHETIC",
            "candidate_identity": {
                "candidate_id": candidate_id,
                "reference_time_ms": 1_790_323_200_000,
                "short_strike_usd": 97000,
                "long_strike_usd": 95000,
                "expiry_ms": 1_790_409_600_000,
                "quantity_btc": 0.1,
                "current_price_usd": 100000,
                "net_credit_after_entry_fee_btc": 0.00021,
                "m_ref_btc": 0.00003,
                "near_breakeven_usd": 96796.73,
            },
            "decision_brief": {
                "summary": {"headline_cn": "模拟 MAP 摘要"},
                "drivers": {"headline_cn": "方向和区域仅作审计"},
                "location": {"headline_cn": "本单位于冻结价格轴"},
                "watch": {"headline_cn": "优先观察 KPF 区域", "primary_region_id": "kpf-zone"},
            },
        },
        "policy_link": {
            "candidate_id": candidate_id,
            "candidate_version": "policy-v1",
            "region_liability_rows": [
                {
                    "region_id": "kpf-zone",
                    "region_label_cn": "历史成交接受区 KPF",
                    "price_range_cn": "99,650 — 100,350 USD",
                    "relationship_cn": "当前价仍在责任区外侧",
                    "terminal_payout_range": "0 — 0.001 BTC",
                    "static_net_range": "-0.001 — 0.00021 BTC",
                }
            ],
            "region_liability_full_rows": [
                {
                    "region_id": "kpf-zone",
                    "region_label_cn": "历史成交接受区 KPF",
                    "price_range_cn": "99,650 — 100,350 USD",
                    "relationship_cn": "当前价仍在责任区外侧",
                    "terminal_payout_range": "0 — 0.001 BTC",
                    "static_net_range": "-0.001 — 0.00021 BTC",
                    "terminal_scenario_basis": "synthetic_fixture_not_current_source",
                    "limitations": ["synthetic_fixture_not_current_source"],
                }
            ],
        },
        "reference_pool": [
            {
                "reference_id": "kpf-zone",
                "family": "TRADED_ACCEPTANCE",
                "role": "OBSERVED_ZONE",
                "label_cn": "历史成交接受区 KPF",
                "display_low_usd": 99650,
                "display_high_usd": 100350,
                "coordinate_qualification": "COMPARABLE",
                "price_basis": "DERIBIT_BTC_USD_INDEX",
                "eligible_for_display": True,
                "selected_for_display": True,
                "note_cn": "模拟区域",
            },
            {
                "reference_id": "short-leg",
                "family": "CONTRACT_LIABILITY",
                "role": "SHORT_LEG",
                "display_price_usd": 97000,
                "coordinate_qualification": "COMPARABLE",
            },
            {
                "reference_id": "long-leg",
                "family": "CONTRACT_LIABILITY",
                "role": "PROTECTIVE_LEG",
                "display_price_usd": 95000,
                "coordinate_qualification": "COMPARABLE",
            },
            {
                "reference_id": "near-be",
                "family": "CONTRACT_LIABILITY",
                "role": "NEAR_BREAKEVEN",
                "display_price_usd": 96796.73,
                "coordinate_qualification": "COMPARABLE",
            },
        ],
        "response_events": [
            {
                "reference_id": "kpf-zone",
                "summary_cn": "模拟区域样本",
                "price": 100010,
                "observed_at_ms": 1_790_323_200_000,
                "coverage": "PRICE_SAMPLE",
            }
        ],
        "background": {},
    }
    if map_id is not None:
        body["map_id"] = map_id
    return body


def _candidate(snapshot_id: str = "snapshot-1", map_id: str | None = "map-1") -> dict:
    candidate_id = "candidate-put"
    mapping = _btc_map(map_id, candidate_id)
    return {
        "candidate_id": candidate_id,
        "side": "put",
        "review_gate": {"enabled": True},
        "btc_map": mapping,
        "review": {
            "status": "SIMULATED",
            "reviewed_snapshot_id": snapshot_id,
            "response_protocol": "research_review@2.4.6",
        },
        "snapshot": {
            "snapshot_id": snapshot_id,
            "candidate_id": candidate_id,
            "side": "put",
            "data_identity": {"kind": "synthetic_fixture", "btc_map": mapping},
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
            "market": {"valuation_ts_ms": 1_790_323_200_000, "reference_price_usd": 100000},
            "fee": {"amount_btc": 0.00002},
            "economics": {
                "premium_spread_btc": 0.00023,
                "net_credit_after_entry_fee_btc": 0.00021,
                "mu_ref_btc": 0.00018,
                "m_ref_btc": 0.00003,
                "reference_margin_btc": 0.00003,
            },
            "risk": {"status": "supported"},
            "liability": {
                "starts_at_usd": 97000,
                "protection_at_usd": 95000,
                "breakeven_usd": [96796.73],
                "btc_contract_cap": None,
            },
        },
    }


def _workbench(snapshot_id: str = "snapshot-1", map_id: str | None = "map-1", global_map_id: str | None = "global-map-1") -> dict:
    return {
        "preview": {"kind": "synthetic_fixture", "label": "W1 本地模拟"},
        "updated_at_ms": 1_790_323_200_000,
        "space_gate": {"state": "STRUCTURE_ELIGIBLE", "source": "synthetic_fixture"},
        "candidates": [_candidate(snapshot_id, map_id)],
        "btc_map": _btc_map(global_map_id, "global"),
        "manual_review_state": {
            "last_result": {
                "status": "SIMULATED",
                "candidate_id": "candidate-put",
                "snapshot_id": snapshot_id,
                "map_id": map_id,
                "side": "put",
                "review": {
                    "status": "SIMULATED",
                    "response_protocol": "research_review@2.4.6",
                    "reviewed_snapshot_id": snapshot_id,
                    "candidate_id": "candidate-put",
                    "applicability": "CONTESTED",
                    "selected_claim_ids": ["claim.risk.overall_mu_reference"],
                    "selected_counter_ids": ["counter.risk.coverage_unknown"],
                    "selected_gap_ids": ["gap.map.response_missing"],
                    "selected_reassess_condition_ids": ["reassess.map.response_change"],
                    "summary_cn": "模拟联合结论",
                    "q1": {
                        "title_cn": "适配",
                        "summary_cn": "量价暂时偏离 Put 不利方向。",
                        "competing_explanation_cn": "上行持续性未知。",
                    },
                    "q2": {
                        "title_cn": "补偿",
                        "summary_cn": "保留关注，但不生成交易许可。",
                        "exchange_reason_cn": "净信用覆盖给定整体参考后仍有小额余量；最强反证是上行持续性未知，不能把当前偏离责任区当成终审。",
                        "coverage_cn": "整体 μ 有值不等于单项软风险已覆盖；方向风险覆盖未知。",
                        "liability_cn": "币本位 Put 深尾没有固定 BTC 最大赔付。",
                        "waiting_cost_cn": "等待可能损失当前保费与剩余期限。",
                        "strongest_counter": {"claim_cn": "上行持续性未知，不能把当前偏离责任区当成终审。"},
                        "observable_changes_cn": [
                            "已有 MAP 区域出现新的进入或离开记录",
                            "价格相对卖出腿或 BE 明显变化",
                            "第三条不应显示",
                        ],
                        "reassess_if_cn": ["同版报价、费用或风险参考更新后再看。"],
                    },
                },
            }
        },
    }


def _run_node(script: str, payload: dict) -> dict:
    node = shutil.which("node")
    assert node, "node is not available"
    result = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        env={**os.environ, "ASTRA_WORKBENCH_IDS": json.dumps(WORKBENCH_IDS)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def _node_harness(extra_js: str) -> str:
    return r"""
const fs = require("fs");
const vm = require("vm");
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
const app = fs.readFileSync("deploy/signal_audit/frontend/app.js", "utf8");
const ids = JSON.parse(process.env.ASTRA_WORKBENCH_IDS);

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
    details.push({
      open: /\sopen(?:\s|>|$)/.test(match[1]),
      className: attrs.class || "",
      dataset: { mapDisclosureKey: attrs["data-map-disclosure-key"] || "" },
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
      if (selector === "details[open][data-map-disclosure-key]") {
        return details.filter((item) => item.open && item.dataset.mapDisclosureKey);
      }
      if (selector === "details[data-map-disclosure-key]") {
        return details.filter((item) => item.dataset.mapDisclosureKey);
      }
      if (selector === "details") return details;
      return [];
    },
  };
}

const elements = {};
for (const id of ids) elements[id] = makeElement(id);
for (const id of ["refreshButton", "reloadHistoryButton", "historySearch", "forgetTokenButton",
  "cancelTokenButton", "tokenForm", "tokenInput", "tokenDialog"]) {
  elements[id] = makeElement(id);
}

const document = {
  readyState: "loading",
  querySelector(selector) {
    if (selector.startsWith("#")) return elements[selector.slice(1)] || null;
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
    innerWidth: 390,
    setInterval() { throw new Error("unexpected interval"); },
    clearInterval() {},
    crypto: { randomUUID() { return "test-review-id"; } },
  },
  setTimeout() { throw new Error("unexpected timeout"); },
  clearTimeout() {},
  fetch() { throw new Error("fetch disabled"); },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(app, sandbox, { filename: "app.js" });
const api = sandbox.window.__astraWorkbenchTest;
if (!api || typeof api.renderAll !== "function") throw new Error("workbench test API missing");
""" + extra_js


def test_map_disclosures_keep_same_precise_identity_and_reset_on_changes() -> None:
    script = _node_harness(
        r"""
function render(workbench, selectedKey = "put") {
  api.state.workbench = workbench;
  api.state.apiOnline = true;
  api.state.selectedMapCandidateKey = selectedKey;
  api.state.lastLoadedAt = workbench.updated_at_ms || 0;
  api.renderAll();
  return elements.btcMap.querySelectorAll("details[data-map-disclosure-key]");
}

let details = render(payload.same);
const axis = details.find((item) => item.className.includes("map-axis-audit"));
const policy = details.find((item) => item.className.includes("map-policy-audit"));
const response = details.find((item) => item.className.includes("map-response-audit"));
if (!axis || !policy || !response) throw new Error("map audit disclosure keys missing");
axis.open = true;
details = render(payload.same);
const sameAxisOpen = details.find((item) => item.className.includes("map-axis-audit")).open === true;
details.find((item) => item.className.includes("map-axis-audit")).open = true;
details = render(payload.newMap);
const newMapClosed = details.find((item) => item.className.includes("map-axis-audit")).open === false;
details = render(payload.same);
details.find((item) => item.className.includes("map-policy-audit")).open = true;
details = render(payload.newSnapshot);
const newSnapshotClosed = details.find((item) => item.className.includes("map-policy-audit")).open === false;

details = render(payload.same, "");
const globalAxis = details.find((item) => item.className.includes("map-axis-audit"));
if (!globalAxis) throw new Error("global axis disclosure key missing");
globalAxis.open = true;
details = render(payload.same, "");
const sameGlobalOpen = details.find((item) => item.className.includes("map-axis-audit")).open === true;
details.find((item) => item.className.includes("map-axis-audit")).open = true;
details = render(payload.newGlobal, "");
const newGlobalClosed = details.find((item) => item.className.includes("map-axis-audit")).open === false;

console.log(JSON.stringify({ sameAxisOpen, newMapClosed, newSnapshotClosed, sameGlobalOpen, newGlobalClosed }));
"""
    )
    observed = _run_node(
        script,
        {
            "same": _workbench("snapshot-1", "map-1", "global-map-1"),
            "newMap": _workbench("snapshot-1", "map-2", "global-map-1"),
            "newSnapshot": _workbench("snapshot-2", "map-1", "global-map-1"),
            "newGlobal": _workbench("snapshot-1", "map-1", "global-map-2"),
        },
    )
    assert observed == {
        "sameAxisOpen": True,
        "newMapClosed": True,
        "newSnapshotClosed": True,
        "sameGlobalOpen": True,
        "newGlobalClosed": True,
    }


def test_missing_precise_map_identity_has_no_map_disclosure_keys() -> None:
    script = _node_harness(
        r"""
api.state.workbench = payload;
api.state.apiOnline = true;
api.state.selectedMapCandidateKey = "put";
api.renderAll();
console.log(JSON.stringify({
  hasDisclosureKey: elements.btcMap.innerHTML.includes("data-map-disclosure-key"),
  hasAxis: elements.btcMap.innerHTML.includes("展开价格轴审计表"),
}));
"""
    )
    observed = _run_node(script, _workbench("snapshot-1", None, "global-map-1"))
    assert observed == {"hasDisclosureKey": False, "hasAxis": True}


def test_q2_round3_order_and_observable_changes_render_from_fields() -> None:
    html = render_workbench(_workbench())["areas"]["manualReviewState"]
    order = [
        html.index("保留关注，但不生成交易许可。"),
        html.index("当前交换与最强反证"),
        html.index("已有 MAP 区域出现新的进入或离开记录"),
        html.index("等待可能损失当前保费与剩余期限。"),
        html.index("展开责任与重审条件"),
    ]
    assert "方向风险覆盖未知" in html
    assert "价格相对卖出腿或 BE 明显变化" in html
    assert "第三条不应显示" not in html
    assert "有限目录来源" in html
    assert "claim.risk.overall_mu_reference" in html
    assert "反证：上行持续性未知" not in html
    assert order == sorted(order)


def test_preview_identity_is_short_with_full_boundary_reachable() -> None:
    html = render_workbench(_workbench())["areas"]["previewBanner"]
    assert "本地模拟 · 零真实 LLM · 执行关闭" in html
    assert "查看完整边界" in html
    assert "不可作为当前 FMZ 事实" in html
