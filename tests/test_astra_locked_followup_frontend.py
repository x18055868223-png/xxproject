import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _sample_candidate():
    return {
        "candidate_id": "put-lock",
        "evidence_url": "/api/workbench/candidate-evidence/snap-lock-1",
        "side": "put",
        "status": "snapshot",
        "snapshot": {
            "snapshot_id": "snap-lock-1",
            "contract": {
                "side": "put",
                "short_instrument": "BTC-30OCT26-97000-P",
                "long_instrument": "BTC-30OCT26-95000-P",
                "quantity_btc": 1,
                "expiry_ms": 1793337600000,
            },
            "economics": {
                "premium_spread_btc": 0.0016,
                "theoretical_two_leg_fee_btc": 0.00032,
                "net_credit_after_entry_fee_btc": 0.00128,
            },
            "market": {"reference_price_usd": 98200},
        },
        "review_gate": {"enabled": True},
        "observation_review": {"enabled": True},
    }


def _sample_comparison():
    return {
        "schema": "locked_candidate_followup@1.0.0",
        "episode": {
            "episode_id": "episode-put-1",
            "candidate_id": "put-lock",
            "snapshot_id": "snap-lock-1",
            "side": "put",
            "status": "PAUSED",
            "created_at_ms": 1790900020000,
            "baseline_decision_asof_ms": 1790900000000,
            "latest_decision_asof_ms": 1790903600000,
            "point_count": 2,
            "last_gap_reason": "中间采样间隔超过计划窗口",
            "sampling": {"collector_error": "最近一轮行情源暂不可用"},
            "provider_response": {"secret": "SHOULD_NOT_EXPORT"},
            "provider_request": {"prompt": "SHOULD_NOT_EXPORT"},
            "request_body": {"prompt": "SHOULD_NOT_EXPORT"},
            "request_payload": {"prompt": "SHOULD_NOT_EXPORT"},
        },
        "fixed_contract": {
            "side": "put",
            "short_instrument": "BTC-30OCT26-97000-P",
            "long_instrument": "BTC-30OCT26-95000-P",
            "quantity_text": "1 BTC",
        },
        "baseline": {
            "point_id": "t0",
            "snapshot_id": "snap-lock-1",
            "asof_ms": 1790900000000,
            "recorded_at_ms": 1790900010000,
            "public_candidate": {"candidate_id": "put-lock", "snapshot_id": "snap-lock-1"},
        },
        "latest": {
            "point_id": "t1",
            "snapshot_id": "snap-lock-2",
            "asof_ms": 1790903600000,
            "recorded_at_ms": 1790903610000,
            "public_candidate": {"candidate_id": "put-lock", "snapshot_id": "snap-lock-2"},
        },
        "overview": {
            "short_distance": {"text_cn": "距离卖出腿 1.2%"},
            "baseline_breakeven_usd": [96600, 95200],
            "breakeven_distances": [{"t1": {"text_cn": "最新距锁定参考 BE 1.8%"}}],
            "remaining_to_expiry_ms": 7200000,
            "close": {
                "debit_btc": 0.00042,
                "close_fee_btc": 0.00008,
                "net_debit_after_close_fee_btc": 0.00050,
                "reference_result_btc": -0.00050,
            },
            "reference_result": {"text_cn": "参考估计，非实际 PnL"},
        },
        "rows": [
            {
                "key": "short_distance",
                "group": "responsibility",
                "label_cn": "短腿距离",
                "t0": {"text_cn": "锁定时在卖出腿外侧", "asof_ms": 1790900000000},
                "t1": {"text_cn": "最新更靠近卖出腿", "asof_ms": 1790903600000},
                "change_cn": "距离缩小",
                "meaning_cn": "仅说明责任位置更近，不推赔付概率",
                "status": "OBSERVED",
                "raw": {
                    "token": "SHOULD_NOT_EXPORT",
                    "source_bindings": {"hidden": True},
                    "fact_context": {
                        "market_facts": {
                            "edb_reasoning": "确定性公开事实解释应保留",
                            "reasoning_content": "SHOULD_NOT_EXPORT",
                        }
                    },
                    "public_packet": {"quote": "公开两端点原件"},
                },
            },
            {
                "key": "path",
                "group": "process",
                "label_cn": "过程覆盖",
                "t0": {"text_cn": "锁定点", "asof_ms": 1790900000000},
                "t1": {},
                "change_cn": "中间采样有缺口",
                "meaning_cn": "两端点可读，但不能宣称完整 Path",
                "status": "GAP",
            },
        ],
        "process": {
            "summary_cn": "锁定以来有两次公开样本，中间有间隙。",
            "observed_count": 2,
            "first_ms": 1790900000000,
            "last_ms": 1790903600000,
            "max_gap_ms": 3600000,
            "index_range": [0, 2],
            "observations": [{"text_cn": "最近一次仍能读取同合同。"}],
            "gaps": [{"reason_cn": "中间 1 个采样窗口未覆盖。"}],
            "other_events": [{"summary_cn": "停止跟踪不是平仓。"}],
        },
        "explanations": {
            "baseline": {
                "current_questions": {
                    "q1": {"summary_cn": "Q1 锁定时相对适配一般。"},
                    "q2": {"summary_cn": "Q2 补偿不足以覆盖新增风险。"},
                },
                "wpem_summary": {"summary_cn": "程序口径保留反证。"},
                "accepted_review": {
                    "summary_cn": "锁定时 accepted review 可读。",
                    "decision_asof_ms": 1790900000000,
                    "reasoning": "SHOULD_NOT_EXPORT",
                },
            },
            "latest": {
                "current_questions": {
                    "q1_summary_cn": "Q1 最新点更靠近责任边界。",
                    "q2_summary_cn": "Q2 仍需显式评审。",
                },
                "accepted_review": {
                    "summary_cn": "最近 accepted review 可读。",
                    "reviewed_at_ms": 1790903600000,
                    "provider_reasoning": "SHOULD_NOT_EXPORT",
                },
            },
        },
        "limitations_cn": ["t0/t1 两端点不是完整 Path", "实际成交未录入"],
    }


def _run_node(script: str, payload: dict):
    node = shutil.which("node")
    if not node:
        raise AssertionError("node is not available")
    result = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


NODE_BOOT = r"""
const fs = require("fs");
const vm = require("vm");
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
const app = fs.readFileSync("deploy/signal_audit/frontend/app.js", "utf8");

function makeElement(id) {
  return {
    id,
    hidden: false,
    className: "",
    innerHTML: "",
    textContent: "",
    value: "",
    dataset: {},
    style: {},
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener() {},
    click() {},
    setAttribute(name, value) { this[name] = value; },
    getAttribute(name) { return this[name] || null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    focus() {},
    close() {},
    showModal() {},
  };
}
const ids = ["apiStatus","previewBanner","scenarioControl","currentConclusion","marketContext",
  "gateBadges","spaceGate","btcMap","candidateArea","lockedCandidatesArea","manualReviewState",
  "dataHealth","dailySnapshots","transitions","gapArea","historyArea","researchLibrary","auditLibrary",
  "tokenDialog","tokenInput","tokenDialogTitle","tokenDialogNote","tokenSubmitButton"];
const elements = {};
for (const id of ids) elements[id] = makeElement(id);
const document = {
  readyState: "loading",
  createElement(tag) { return makeElement(tag); },
  getElementById(id) { return elements[id] || null; },
  querySelector(selector) {
    if (selector.startsWith("#")) return elements[selector.slice(1)] || null;
    return null;
  },
  querySelectorAll() { return []; },
  addEventListener() {},
};
const calls = [];
const sandbox = {
  console,
  document,
  window: {
    document,
    location: { hostname: "127.0.0.1", protocol: "http:", hash: payload.hash || "" },
    innerWidth: 1440,
    scrollY: 0,
    setInterval() { return 0; },
    clearInterval() {},
    setTimeout(fn) { fn(); return 0; },
    clearTimeout() {},
    scrollTo() {},
    crypto: { randomUUID() { return "test-idempotency"; } },
    sessionStorage: { getItem() { return ""; }, setItem() {} },
  },
  URL: { createObjectURL() { return "blob:test"; }, revokeObjectURL() {} },
  Blob: function(parts, options) { sandbox.lastBlob = { parts, options }; return sandbox.lastBlob; },
  setTimeout(fn) { fn(); return 0; },
  clearTimeout() {},
  fetch(url, options = {}) {
    calls.push({ url: String(url), method: options.method || "GET", body: options.body || "", headers: options.headers || {}, credentials: options.credentials || "" });
    if (String(url) === "/api/workbench/session") {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ authenticated: true, expires_at_ms: 1790910000000 }) });
    }
    if (String(url) === "/api/workbench/locked-candidates" && options.method === "POST") {
      if (payload.lockPostError) {
        return Promise.resolve({ ok: false, status: 409, statusText: "Conflict", json: () => Promise.resolve(payload.lockPostError) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ created: true, episode: payload.episode, llm_call_count: 0 }) });
    }
    if (String(url) === "/api/workbench/locked-candidates") {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ schema: "locked_candidate_summaries@1", episodes: [payload.episode], llm_call_count: 0 }) });
    }
    if (String(url).startsWith("/api/workbench/candidate-evidence/")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({
        schema: "candidate_evidence@1",
        _public_point: {
          snapshot_id: "snap-lock-1",
          asof_ms: 1790900000000,
          recorded_at_ms: 1790900010000,
          public_candidate: { ...payload.candidate, snapshot: { ...payload.candidate.snapshot, full_snapshot_marker: "FULL_SNAPSHOT" } },
          public_packet: { retained: "FULL_PACKET" },
        },
      }) });
    }
    if (String(url).startsWith("/api/workbench/locked-candidates/") && String(url).includes("/evidence?")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ schema: "locked_candidate_evidence@1", comparison: payload.fullComparison || payload.comparison, retained_snapshots: [{ snapshot_id: "snap-lock-1", public_packet: { quote: "full evidence" } }] }) });
    }
    if (String(url).startsWith("/api/workbench/locked-candidates/")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(payload.comparison) });
    }
    if (String(url).startsWith("/api/workbench?")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ schema: "workbench", candidates: [payload.candidate], manual_review_state: {}, locked_candidates: { episodes: [payload.episode] }, updated_at_ms: 1790903600000 }) });
    }
    if (String(url) === "/api/workbench/review") {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ status: "OK" }) });
    }
    return Promise.resolve({ ok: false, status: 404, statusText: "missing", json: () => Promise.resolve({}) });
  },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(app, sandbox, { filename: "app.js" });
const api = sandbox.window.__astraWorkbenchTest;
api.state.workbench = { schema: "workbench", candidates: [payload.candidate], manual_review_state: {}, updated_at_ms: 1790900000000 };
api.state.apiOnline = true;
api.setInMemoryToken("server-token");
"""


def test_lock_candidate_posts_locked_endpoint_not_review():
    script = NODE_BOOT + r"""
(async () => {
  const pending = api.beginLockCandidate("put-lock");
  api.state.workbench.candidates[0].snapshot.snapshot_id = "snap-late";
  await pending;
  console.log(JSON.stringify({ calls, lockedHtml: elements.lockedCandidatesArea.innerHTML, candidateHtml: elements.candidateArea.innerHTML }));
})();
"""
    out = _run_node(script, {"candidate": _sample_candidate(), "episode": _sample_comparison()["episode"], "comparison": _sample_comparison()})
    assert any(call["url"] == "/api/workbench/locked-candidates" and call["method"] == "POST" for call in out["calls"])
    assert not any(call["url"] == "/api/workbench/review" for call in out["calls"])
    first_post = next(call for call in out["calls"] if call["method"] == "POST")
    body = json.loads(first_post["body"])
    assert body["candidate_id"] == "put-lock"
    assert body["snapshot_id"] == "snap-lock-1"
    assert body["idempotency_key"] == "test-idempotency"
    assert body["new_reference"] is False
    assert "Authorization" not in first_post["headers"]
    assert first_post["credentials"] == "same-origin"
    assert 'class="locked-detail"' in out["lockedHtml"]
    assert 'data-locked-export="episode-put-1"' in out["lockedHtml"]
    assert "已暂停" in out["lockedHtml"]


def test_hash_route_restores_locked_episode_detail():
    script = NODE_BOOT + r"""
api.state.lockedEpisodes = [payload.episode];
api.state.lockedComparisons[payload.episode.episode_id] = payload.comparison;
api.renderAll();
console.log(JSON.stringify({ html: elements.lockedCandidatesArea.innerHTML }));
"""
    out = _run_node(script, {
        "hash": "#lockedEpisode-episode-put-1",
        "candidate": _sample_candidate(),
        "episode": _sample_comparison()["episode"],
        "comparison": _sample_comparison(),
    })
    html = out["html"]
    assert "本单跟踪 · Locked Candidate" in html
    assert "BTC-30OCT26-97000-P" in html
    assert "前后对照表" in html
    assert "比较口径与限制" in html
    assert "<summary>依据</summary>" in html
    assert "锁定时 accepted review 可读" in html


def test_locked_routes_keep_home_compact_and_detail_secondary():
    script = NODE_BOOT + r"""
api.state.lockedEpisodes = [payload.episode];
api.state.lockedComparisons[payload.episode.episode_id] = payload.comparison;
api.renderAll();
const compact = elements.lockedCandidatesArea.innerHTML;
sandbox.window.location.hash = "#lockedCandidates";
api.renderAll();
const list = elements.lockedCandidatesArea.innerHTML;
sandbox.window.location.hash = "#lockedEpisode-episode-put-1";
api.renderAll();
const detail = elements.lockedCandidatesArea.innerHTML;
console.log(JSON.stringify({ compact, list, detail }));
"""
    out = _run_node(script, {
        "candidate": _sample_candidate(),
        "episode": _sample_comparison()["episode"],
        "comparison": _sample_comparison(),
    })
    assert "张已锁定保单" in out["compact"]
    assert "前后对照表" not in out["compact"]
    assert "locked-summary-card" in out["list"]
    assert "前后对照表" not in out["list"]
    assert "前后对照表" in out["detail"]
    assert "返回跟踪列表" in out["detail"]


def test_locked_export_is_fixed_pair_offline_and_public_scrubbed():
    script = NODE_BOOT + r"""
const html = api.lockedExportHtml(payload.comparison);
console.log(JSON.stringify({ html }));
"""
    out = _run_node(script, {"candidate": _sample_candidate(), "episode": _sample_comparison()["episode"], "comparison": _sample_comparison()})
    html = out["html"]
    assert "BTC-30OCT26-97000-P" in html
    assert "BTC-30OCT26-95000-P" in html
    assert "96,600 USD" in html
    assert "95,200 USD" in html
    assert "参考平仓费用 0.00008 BTC" in html
    assert "参考结果 -0.0005 BTC" in html
    assert "参考估计，非实际 PnL" in html
    assert "[object Object]" not in html
    assert "完整公开 JSON" in html
    assert "<details><summary>完整公开 JSON</summary>" in html
    assert "<script" not in html.lower()
    assert "fetch(" not in html
    assert "setInterval" not in html
    assert "确定性公开事实解释应保留" in html
    assert "public_packet" in html
    for forbidden in ("SHOULD_NOT_EXPORT", "provider_response", "source_bindings", "reasoning_content", "provider_reasoning", "server-token", "private_packet"):
        assert forbidden not in html


def test_locked_export_fetches_exact_pair_evidence_without_bearer():
    script = NODE_BOOT + r"""
(async () => {
  api.state.lockedComparisons[payload.episode.episode_id] = payload.comparison;
  await api.downloadLockedExport(payload.episode.episode_id);
  console.log(JSON.stringify({ calls, html: sandbox.lastBlob.parts[0], message: api.state.lockedMessage }));
})();
"""
    out = _run_node(script, {"candidate": _sample_candidate(), "episode": _sample_comparison()["episode"], "comparison": _sample_comparison()})
    evidence_gets = [call for call in out["calls"] if "/evidence?baseline_point_id=t0&latest_point_id=t1" in call["url"]]
    assert evidence_gets
    assert evidence_gets[0]["method"] == "GET"
    assert "Authorization" not in evidence_gets[0]["headers"]
    assert "full evidence" in out["html"]
    assert "已生成离线 HTML" in out["message"]


def test_observation_capture_uses_candidate_evidence_url_when_present():
    script = NODE_BOOT + r"""
(async () => {
  const capture = await api.buildObservationCapture(payload.candidate, "rendered text");
  console.log(JSON.stringify({ calls, capture }));
})();
"""
    out = _run_node(script, {"candidate": _sample_candidate(), "episode": _sample_comparison()["episode"], "comparison": _sample_comparison()})
    evidence_gets = [call for call in out["calls"] if call["url"] == "/api/workbench/candidate-evidence/snap-lock-1"]
    assert evidence_gets
    assert "Authorization" not in evidence_gets[0]["headers"]
    assert out["capture"]["public_view"]["candidates"][0]["snapshot"]["full_snapshot_marker"] == "FULL_SNAPSHOT"
    assert out["capture"]["retained_candidate_evidence"]["public_packet"]["retained"] == "FULL_PACKET"


def test_locked_followup_copy_keeps_three_clocks_no_trade_and_no_path_claim():
    script = NODE_BOOT + r"""
const html = api.lockedExportHtml(payload.comparison);
console.log(JSON.stringify({ html }));
"""
    out = _run_node(script, {"candidate": _sample_candidate(), "episode": _sample_comparison()["episode"], "comparison": _sample_comparison()})
    html = out["html"]
    assert "t0 资料时点" in html
    assert "资料存档时间" in html
    assert "锁定时间" in html
    assert "最近资料时点" in html
    assert "首个资料源时点" in html
    assert "采样缺口" in html
    assert "中间 1 个采样窗口未覆盖" in html
    assert "实际成交" in html and "未录入" in html
    assert "不宣称完整 Path" in html or "不是完整 Path" in html
    assert "停止跟踪也不是平仓" in html
    assert "不等于成交、开仓批准或账户持仓" in html


def test_tiny_boundary_crossing_preserves_sign_in_overview_and_rows():
    comparison = _sample_comparison()
    comparison["overview"]["short_distance"] = {"unit": "pct", "value": -0.000001}
    comparison["rows"][0].update(status="changed", t0={"unit": "pct", "value": 0.000001},
                                  t1={"unit": "pct", "value": -0.000001})
    comparison["rows"][0]["meaning_cn"] = "原文参考 0.123456% 与 0.00000001 BTC 保持原样"
    script = NODE_BOOT + 'console.log(JSON.stringify({html:api.lockedExportHtml(payload.comparison)}));'
    out = _run_node(script, {"candidate": _sample_candidate(), "episode": comparison["episode"], "comparison": comparison})
    visible = out["html"].split('<summary>完整公开 JSON</summary>')[0]
    assert visible.count("负值（绝对值小于 0.001%）") >= 2
    assert "正值（绝对值小于 0.001%）" in visible
    assert "微降（小于 0.001 个百分点）" in visible
    assert "0.123456% 与 0.00000001 BTC" in visible


def test_large_remote_evidence_has_bounded_dom_and_complete_download():
    comparison = _sample_comparison()
    comparison["large_source"] = "x" * 1000000
    script = NODE_BOOT + r'''
(async () => {
  let markup = "";
  const details = {open:true, dataset:{remoteJsonUrl:"/api/workbench/locked-candidates/episode-put-1/evidence?baseline_point_id=t0&latest_point_id=t1"},
    querySelector(){return null;}, insertAdjacentHTML(_, html){markup += html;}};
  await api.hydrateRawFold(details);
  console.log(JSON.stringify({markup, hydrated:details.dataset.rawHydrated}));
})();
'''
    out = _run_node(script, {"candidate": _sample_candidate(), "episode": comparison["episode"], "comparison": comparison})
    assert out["hydrated"] == "true"
    assert len(out["markup"]) < 100000
    assert "预览不是完整 JSON" in out["markup"]
    assert "下载完整 JSON" in out["markup"]
    assert "baseline_point_id=t0&amp;latest_point_id=t1" in out["markup"]


def test_load_workbench_refreshes_active_locked_detail_without_changing_route():
    fresh = _sample_comparison()
    fresh["latest"]["asof_ms"] = 1790907200000
    fresh["rows"][0]["t1"]["text_cn"] = "轮询后最新点已更新"
    script = NODE_BOOT + r"""
(async () => {
  api.state.lockedEpisodes = [payload.episode];
  api.state.lockedComparisons[payload.episode.episode_id] = { ...payload.comparison, rows: [] };
  await api.loadWorkbench();
  api.renderAll();
  console.log(JSON.stringify({ calls, html: elements.lockedCandidatesArea.innerHTML, hash: sandbox.window.location.hash }));
})();
"""
    out = _run_node(script, {
        "hash": "#lockedEpisode-episode-put-1",
        "candidate": _sample_candidate(),
        "episode": fresh["episode"],
        "comparison": fresh,
    })
    detail_gets = [call for call in out["calls"] if call["url"] == "/api/workbench/locked-candidates/episode-put-1" and call["method"] == "GET"]
    assert detail_gets
    assert "轮询后最新点已更新" in out["html"]
    assert out["hash"] == "#lockedEpisode-episode-put-1"


def test_locked_error_preserves_backend_code_context_and_unknown_state():
    script = NODE_BOOT + r"""
(async () => {
  await api.beginLockCandidate("put-lock");
  console.log(JSON.stringify({ calls, error: api.state.lockedError, message: api.state.lockedMessage, html: elements.lockedCandidatesArea.innerHTML }));
})();
"""
    out = _run_node(script, {
        "candidate": _sample_candidate(),
        "episode": _sample_comparison()["episode"],
        "comparison": _sample_comparison(),
        "lockPostError": {"code": "active_budget_exhausted", "message_cn": "请停止旧跟踪后再锁定"},
    })
    assert any(call["url"] == "/api/workbench/locked-candidates" and call["method"] == "POST" for call in out["calls"])
    assert "活跃跟踪数量已达上限" in out["error"]
    assert "请停止旧跟踪后再锁定" in out["error"]
    assert "状态不明" in out["message"]


def test_locked_reading_scope_survives_live_candidate_rotation_and_isolates_episode():
    script = NODE_BOOT + r"""
api.state.lockedEpisodes = [payload.episode];
api.state.lockedComparisons[payload.episode.episode_id] = payload.comparison;
api.renderAll();
const initial = api.state.disclosureScope;
api.state.workbench.candidates[0].snapshot.snapshot_id = "rotated-snapshot";
api.renderAll();
const refreshed = api.state.disclosureScope;
sandbox.window.location.hash = "#lockedEpisode-different-episode";
api.renderAll();
console.log(JSON.stringify({initial,refreshed,switched:api.state.disclosureScope}));
"""
    out = _run_node(script, {"hash":"#lockedEpisode-episode-put-1", "candidate":_sample_candidate(),
        "episode":_sample_comparison()["episode"],"comparison":_sample_comparison()})
    assert out["initial"] == out["refreshed"] == "locked::episode-put-1"
    assert out["switched"] != out["initial"]


def test_same_episode_rerender_preserves_table_reading_position_only_for_that_episode():
    script = NODE_BOOT + r"""
api.state.lockedEpisodes = [payload.episode];
api.state.lockedComparisons[payload.episode.episode_id] = payload.comparison;
let table = {scrollTop: 530, scrollLeft: 167};
let oldId = "lockedEpisode-episode-put-1";
const target = elements.lockedCandidatesArea;
target.querySelector = (selector) => selector === ".locked-detail"
  ? {id: oldId, querySelector() {return table;}} : selector === ".locked-table-wrap" ? table : null;
Object.defineProperty(target, "innerHTML", {set(value) {
  table = {scrollTop: 0, scrollLeft: 0};
  oldId = (value.match(/id="(lockedEpisode-[^"]+)"/) || [])[1];
}});
api.renderLockedCandidates();
const same = {...table};
sandbox.window.location.hash = "#lockedEpisode-different-episode";
api.renderLockedCandidates();
console.log(JSON.stringify({same,switched:table}));
"""
    out = _run_node(script, {"hash":"#lockedEpisode-episode-put-1", "candidate":_sample_candidate(),
        "episode":_sample_comparison()["episode"],"comparison":_sample_comparison()})
    assert out["same"] == {"scrollTop":530,"scrollLeft":167}
    assert out["switched"] == {"scrollTop":0,"scrollLeft":0}
