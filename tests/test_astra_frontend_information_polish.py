import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from astra_workbench_frontend_harness import render_workbench  # noqa: E402
from test_astra_wpem_frontend import _candidate, _main_candidate_view, _workbench  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "deploy" / "signal_audit" / "frontend" / "app.js"


def _wpem_polish_candidate():
    candidate = _candidate("put")
    summary = candidate["wpem_summary"]
    summary["path_cn"] = "服务端路径摘要：价格仍在卖出腿外侧。"
    summary["compensation_cn"] = "服务端补偿摘要：可见信用下降但仍为正。"
    evidence = candidate["wpem_evidence"]
    evidence["risk_status"] = {
        "risk_reference_provided": False,
        "mu_M_null_reason": "REFERENCE_ANALYSIS_MISSING",
    }
    ldp = evidence["same_candidate_window"]["LevelDeltaPath"]
    ldp["level"].update({
        "reference_price_usd": 100000,
        "signed_distance_to_short_pct": -3.25,
    })
    ldp["delta"].update({
        "status": "STRICT_SAME_CANDIDATE_DELTA",
        "changes": {
            "reference_price_usd_delta": 2500,
            "visible_credit_btc_delta": -0.00035,
        },
    })
    candidate["snapshot"]["economics"]["mu_ref_btc"] = None
    candidate["snapshot"]["economics"]["reference_margin_btc"] = None
    return candidate


def _map_for_candidate(candidate):
    snapshot = candidate["snapshot"]
    candidate_id = candidate["candidate_id"]
    snapshot_id = snapshot["snapshot_id"]
    return {
        "map_id": "map-info-polish",
        "data_identity": "CURRENT",
        "cutoff_at_ms": 1790323200000,
        "current_price_usd": 100000,
        "policy_link": {
            "candidate_id": candidate_id,
            "snapshot_id": snapshot_id,
            "candidate_version": "same-candidate",
            "contract": {"side": "put", "expiry_ms": 1790400000000},
            "region_liability_full_rows": [
                {
                    "region_id": "acceptance-zone",
                    "region_label_cn": "接受区",
                    "price_range_cn": "96,000-98,000 USD",
                    "relationship_cn": "before_payout",
                    "terminal_payout_range": {"min_btc": 0, "max_btc": 0},
                    "static_net_range": {"min_btc": 0.00038, "max_btc": 0.00039},
                    "terminal_scenario_basis": "official_settlement_inside_contract_comparable_coordinate",
                    "coordinate_qualification": "COMPARABLE",
                }
            ],
        },
        "presentation": {
            "candidate_identity": {
                "candidate_id": candidate_id,
                "snapshot_id": snapshot_id,
                "short_strike_usd": 97000,
                "long_strike_usd": 95000,
                "near_breakeven_usd": 96600,
                "expiry_ms": 1790400000000,
                "reference_time_ms": 1790323200000,
                "current_price_usd": 100000,
                "quantity_btc": 0.1,
                "net_credit_after_entry_fee_btc": 0.00039,
            },
            "decision_brief": {
                "pushes": [{"title_cn": "背景", "text_cn": "服务端摘要保留。"}],
                "conflicts": [{"title_cn": "缺口", "text_cn": "FMZ 当前事实仍需核验。"}],
                "policy_position": {"summary_cn": "本单位置来自同版 MAP。"},
                "watch": {"qualified": False, "reason_cn": "没有合格观察区。"},
            },
        },
        "background": {
            "allocation": {"fact_state": "usable", "summary_cn": "配置背景可用。"},
        },
        "reference_pool": [
            {
                "reference_id": "acceptance-zone",
                "family": "TRADED_ACCEPTANCE",
                "role": "OBSERVED_ZONE",
                "display_low_usd": 96000,
                "display_high_usd": 98000,
                "coordinate_qualification": "COMPARABLE",
                "price_basis": "DERIBIT_BTC_USD_INDEX",
            },
            {"reference_id": "short-leg", "family": "CONTRACT_LIABILITY", "role": "SHORT_LEG", "display_price_usd": 97000, "expiry_scope": 1790400000000},
            {"reference_id": "near-be", "family": "CONTRACT_LIABILITY", "role": "NEAR_BREAKEVEN", "display_price_usd": 96600, "expiry_scope": 1790400000000},
            {"reference_id": "long-leg", "family": "CONTRACT_LIABILITY", "role": "PROTECTIVE_LEG", "display_price_usd": 95000, "expiry_scope": 1790400000000},
        ],
    }


def test_current_delta_null_does_not_borrow_a_legacy_top_level_value():
    candidate = _wpem_polish_candidate()
    delta = candidate["wpem_evidence"]["same_candidate_window"]["LevelDeltaPath"]["delta"]
    delta["changes"] = {"reference_price_usd_delta": None, "visible_credit_btc_delta": None}
    delta["reference_price_usd_delta"] = 99999
    delta["visible_credit_btc_delta"] = 123
    html = render_workbench(_workbench([candidate]))["areas"]["candidateArea"]
    readout = html.split('class="wpem-readout"', 1)[1].split("</dl>", 1)[0]
    assert "参考价变化" not in readout
    assert "可见信用变化" not in readout
    assert "99,999" not in readout
    assert "未返回严格同候选增量字段" in readout


def test_missing_changes_does_not_promote_legacy_top_level_delta_values():
    candidate = _wpem_polish_candidate()
    delta = candidate["wpem_evidence"]["same_candidate_window"]["LevelDeltaPath"]["delta"]
    del delta["changes"]
    delta["reference_price_usd_delta"] = 99999
    delta["visible_credit_btc_delta"] = 123
    html = render_workbench(_workbench([candidate]))["areas"]["candidateArea"]
    readout = html.split('class="wpem-readout"', 1)[1].split("</dl>", 1)[0]
    assert "参考价变化" not in readout
    assert "可见信用变化" not in readout
    assert "99,999" not in readout
    assert "未返回严格同候选增量字段" in readout


def test_candidate_core_keeps_useful_numbers_and_moves_technical_labels_down():
    candidate = _wpem_polish_candidate()
    html = render_workbench(_workbench([candidate]))["areas"]["candidateArea"]
    main_html = _main_candidate_view(html)

    assert 'class="metric-grid candidate-compensation"' in main_html
    assert "权利金差 c" not in main_html
    assert "建仓费用 f_entry" not in main_html
    assert "建仓净信用 c-f_entry" not in main_html
    assert "权利金差" in main_html
    assert "建仓费用" in main_html
    assert "建仓净信用" in main_html
    assert "建仓费用 f_entry" in html
    assert "持有到期总费用 f_total" in html

    assert "风险参考未提供，μ/M 未知：参考分析缺失。" in main_html
    assert "冻结赔付 μ_ref" not in main_html
    assert "参考余量 M_ref" not in main_html
    assert "冻结 μ_ref" in html
    assert "未提供" in html


def test_wpem_readout_uses_existing_level_delta_path_and_keeps_raw_audit_nested():
    candidate = _wpem_polish_candidate()
    html = render_workbench(_workbench([candidate]))["areas"]["candidateArea"]

    assert "wpem-readout" in html
    assert "<dl>" in html
    assert "参考价 100,000 USD" in html
    assert "距卖出腿 -3.25%" in html
    assert "参考价变化 +2,500 USD" in html
    assert "可见信用变化 -0.00035 BTC" in html
    assert "服务端路径摘要：价格仍在卖出腿外侧。" in html
    assert "服务端补偿摘要：可见信用下降但仍为正。" in html
    assert "15m 模拟窗口覆盖" in html
    assert "15m 模拟窗口覆盖（本地模拟窗口）" not in html
    assert "1h 窗口不足（本地模拟窗口）" in html

    assert "15m / 1h 覆盖诊断" in html
    assert "完整来源与协议记录" in html
    assert html.index("15m / 1h 覆盖诊断") < html.index("完整来源与协议记录")
    assert html.index("完整来源与协议记录") < html.index("WPEM 截面、Level / Delta / Path")
    assert "candidate-old-call-peer" in html
    assert "record-alpha" in html


def test_map_axis_is_first_and_full_price_readout_does_not_change_projection():
    candidate = _candidate("put", with_wpem=False)
    candidate["btc_map"] = _map_for_candidate(candidate)
    view = _workbench([candidate])
    view["__test_state"] = {"selectedMapCandidateKey": "put", "mapAxisMode": "all"}
    html = render_workbench(view)["areas"]["btcMap"]

    assert html.index("数值价格轴") < html.index("本轮四问摘要")
    assert html.index("本轮四问摘要") < html.index("区域响应与保单责任关联")
    assert html.index("区域响应与保单责任关联") < html.index("四行背景")
    assert "map-policy-context" in html
    assert "查看保单身份与冻结补偿" in html
    assert "map-axis-readout" in html
    assert "卖出腿 97,000 USD" in html
    assert "近端盈亏平衡 96,600 USD" in html
    assert "保护腿 95,000 USD" in html

    node = shutil.which("node")
    if not node:
        return
    script = r"""
const fs = require("fs");
const vm = require("vm");
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
const app = fs.readFileSync("deploy/signal_audit/frontend/app.js", "utf8");
const document = { readyState: "loading", querySelector() { return null; }, querySelectorAll() { return []; }, addEventListener() {} };
const sandbox = {
  console,
  document,
  window: { document, location: { hostname: "127.0.0.1", protocol: "http:" }, innerWidth: 1440, setInterval() {}, clearInterval() {} },
  setTimeout() {},
  clearTimeout() {},
  fetch() { throw new Error("fetch disabled"); },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(app, sandbox, { filename: "app.js" });
sandbox.window.__astraWorkbenchTest.state.selectedMapCandidateKey = "put";
sandbox.window.__astraWorkbenchTest.state.mapAxisMode = "all";
const context = {
  map: payload.map,
  candidate: payload.candidate,
  label: "Put 保单",
  source: "candidate",
  options: [{ key: "put", side: "Put", candidate: payload.candidate, hasMap: true }],
};
const model = sandbox.window.__astraMapAxisV241.buildMapAxisModel(context);
const left = 126;
const width = 900;
const pxA = model.toX(97000, left, width) - model.toX(96000, left, width);
const pxB = model.toX(96000, left, width) - model.toX(95000, left, width);
console.log(JSON.stringify({ diff: Math.abs(pxA - pxB), contractCount: model.contracts[0].refs.length }));
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        input=json.dumps({"map": candidate["btc_map"], "candidate": candidate}, ensure_ascii=False),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        env=os.environ.copy(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    checked = json.loads(result.stdout)
    assert checked["diff"] < 1e-9
    assert checked["contractCount"] == 3


def test_manual_review_operation_state_moves_after_receipt_but_stays_visible_when_busy():
    candidate = _candidate("put")
    view = _workbench([candidate])
    view["manual_review_state"] = {
        "cooldown_remaining_ms": 600000,
        "daily_http_budget": {"http_calls_used": 3, "http_calls_limit": 4, "date_bjt": "2026-10-05"},
        "inflight": {"request_id": "hidden"},
        "last_result": {},
    }
    view["__test_state"] = {
        "selectedMapCandidateKey": "put",
        "reviewMessage": "正在推理；已恢复服务端运行中评审，自动等候回执。",
    }
    html = render_workbench(view)["areas"]["manualReviewState"]

    assert html.index("当前查看 · Put 保单") < html.index("评审运行状态与调用限制")
    assert '<details class="review-operation-details" open>' in html
    assert "正在推理；已恢复服务端运行中评审，自动等候回执。" in html
    assert "全局冷却" in html
    assert "共享 HTTP 预算" in html
    assert "hidden" not in html


def test_stale_and_research_boundaries_remain_frozen_not_current():
    stale_candidate = _candidate("put")
    stale_candidate["review_validity"] = {"status": "STALE", "reason_cn": "snapshot changed"}
    stale_candidate["review"] = {
        "status": "OK",
        "reviewed_snapshot_id": stale_candidate["snapshot"]["snapshot_id"],
        "summary_cn": "旧同版正文不应提升为当前。",
        "q1": {"summary_cn": "旧 Q1"},
        "q2": {"summary_cn": "旧 Q2"},
    }
    stale_html = render_workbench(_workbench([stale_candidate]))["areas"]["candidateArea"]
    assert "当前综合解释" not in stale_html
    assert "旧评审已过期，仅折叠保留" in stale_html
    assert "snapshot changed" in stale_html

    research_candidate = _candidate("put")
    research_result = {
        "status": "RESEARCH_OK",
        "production_attach_allowed": False,
        "candidate_id": research_candidate["candidate_id"],
        "side": "put",
        "review": {
            "status": "RESEARCH_OK",
            "production_attach_allowed": False,
            "summary_cn": "研究正文语义通过。",
            "q1": {"summary_cn": "研究 Q1 正文"},
            "q2": {"summary_cn": "研究 Q2 正文"},
        },
    }
    view = _workbench([research_candidate])
    view["manual_review_state"] = {"last_result": research_result}
    manual_html = render_workbench(view)["areas"]["manualReviewState"]
    assert "研究复核完成 · 未生产附着" in manual_html
    assert "当前综合解释" not in manual_html


def test_stale_old_receipt_does_not_hide_current_candidate_wpem_readout():
    candidate = _wpem_polish_candidate()
    candidate["review_validity"] = {"status": "STALE", "reason_cn": "saved research receipt is not current"}
    candidate["review"] = {
        "status": "OK",
        "reviewed_snapshot_id": "old-snapshot",
        "summary_cn": "旧总结只属于旧输入。",
        "q1": {"summary_cn": "旧 Q1"},
        "q2": {"summary_cn": "旧 Q2"},
    }
    html = render_workbench(_workbench([candidate]))["areas"]["candidateArea"]
    stale_lead = html.split('<details class="wpem-stale-review"', 1)[0]

    assert "当前综合解释" not in html
    assert "旧评审已过期，仅折叠保留" in html
    assert "旧 Q1" not in stale_lead
    assert "旧 Q1" in html.split('<details class="wpem-stale-review"', 1)[1]
    assert "wpem-readout" in stale_lead
    assert "参考价 100,000 USD" in stale_lead
    assert "参考价变化 +2,500 USD" in stale_lead
    assert "可见信用变化 -0.00035 BTC" in stale_lead
    assert "15m 模拟窗口覆盖" in stale_lead
    assert "15m 模拟窗口覆盖（本地模拟窗口）" not in stale_lead


def test_current_review_readout_dedup_uses_rendered_review_questions():
    candidate = _wpem_polish_candidate()
    candidate["wpem_summary"]["path_cn"] = "CURRENT review Q1 already says this."
    candidate["wpem_summary"]["compensation_cn"] = "CURRENT review Q2 already says this."
    candidate["review_validity"] = {"status": "CURRENT", "checked_at_ms": 1790323200000}
    candidate["review"] = {
        "status": "OK",
        "reviewed_snapshot_id": candidate["snapshot"]["snapshot_id"],
        "q1": {"summary_cn": "CURRENT review Q1 already says this."},
        "q2": {"summary_cn": "CURRENT review Q2 already says this."},
    }
    html = render_workbench(_workbench([candidate]))["areas"]["candidateArea"]
    start = html.index('<div class="wpem-readout"')
    readout_area = html[start:html.index('<details class="wpem-audit"', start)]

    assert "当前综合解释" in html
    assert "CURRENT review Q1 already says this." in html
    assert "CURRENT review Q2 already says this." in html
    assert "CURRENT review Q1 already says this." not in readout_area
    assert "CURRENT review Q2 already says this." not in readout_area
    assert "参考价变化 +2,500 USD" in readout_area
    assert "可见信用变化 -0.00035 BTC" in readout_area
