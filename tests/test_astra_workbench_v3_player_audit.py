"""v3 player-audit UI: model-input copy and download controls on frozen hands and valid hold reviews."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
API_JS = (ROOT / "deploy" / "astra_workbench_v3" / "api.js").as_uri()
PLAYER_AUDIT_JS = (ROOT / "deploy" / "astra_workbench_v3" / "player-audit.js").as_uri()

SHIM = r"""
class T { constructor(t) { this.text = String(t); } get textContent() { return this.text; } }
class CList {
  constructor(n) { this.n = n; }
  _parts() { return String(this.n.className || "").split(/\s+/).filter(Boolean); }
  add(...cs) { const s = new Set(this._parts()); cs.forEach((c) => s.add(c)); this.n.className = [...s].join(" "); }
  remove(...cs) { const r = new Set(cs); this.n.className = this._parts().filter((c) => !r.has(c)).join(" "); }
  contains(c) { return this._parts().includes(c); }
}
class N {
  constructor(tag) {
    this.tagName = tag; this.childNodes = []; this.attrs = {}; this.className = "";
    this.dataset = {}; this.style = {}; this.listeners = {}; this.classList = new CList(this);
    this.isConnected = true; this.value = "";
  }
  append(...cs) { for (const c of cs) if (c != null) this.childNodes.push(c); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(ev, fn) { (this.listeners[ev] ||= []).push(fn); }
  remove() {}
  focus() {}
  select() {}
  setSelectionRange() {}
  querySelectorAll() { return []; }
  set textContent(v) { this.childNodes = [new T(v)]; }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(""); }
}
globalThis.Node = N;
globalThis.document = {
  body: new N("body"),
  createElement: (t) => new N(t),
  createElementNS: (_, t) => new N(t),
  createTextNode: (t) => new T(t),
  execCommand: () => false,
};
"""


def _run_node(body: str, payload: dict) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    script = (
        SHIM
        + 'import fs from "node:fs";\n'
        + 'const p = JSON.parse(fs.readFileSync(0, "utf8"));\n'
        + f'const {{ api }} = await import("{API_JS}");\n'
        + f'const {{ renderPlayerAudit }} = await import("{PLAYER_AUDIT_JS}");\n'
        + f"const out = await (async () => {{ {body} }})();\n"
        + "console.log(JSON.stringify(out));\n"
    )
    res = subprocess.run(
        [node, "--input-type=module", "-e", script],
        input=json.dumps(payload),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        cwd=ROOT,
    )
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_player_audit_model_input_urls_and_copy_download_controls():
    body = r"""
    let copied = null;
    Object.defineProperty(globalThis, "navigator", {
      value: { clipboard: { writeText: async (txt) => { copied = txt; } } },
      configurable: true,
    });
    const calls = [];
    const box = document.createElement("div");
    const walk = (n, out = []) => {
      if (n && n.attrs) {
        out.push(n);
        for (const c of n.childNodes) walk(c, out);
      }
      return out;
    };
    renderPlayerAudit(box, {
      cards: [p.detail],
      ref: `${p.detail.dossier_id}/${p.detail.hand_id}`,
      detail: p.detail,
      expanded: new Set(),
    }, {
      authenticated: true,
      available: true,
      onSelect: () => {},
      onTracker: () => {},
      onOriginals: () => {},
      onLogin: () => {},
      onRefresh: () => {},
      modelInputUrl: api.playerModelInputUrl,
      onModelInput: async (d, h, r) => {
        calls.push({ d, h, r });
        return {
          single_box_text: `BOX:${d}:${h}:${r || "initial"}`,
          fidelity: "RECONSTRUCTED",
          fidelity_reason: "historical",
        };
      },
    });
    const nodes = walk(box);
    const links = nodes.filter((n) => n.tagName === "a" && (n.className || "").includes("pa-export-link"))
      .map((n) => ({ text: n.textContent, href: n.attrs.href, download: n.attrs.download }));
    const btns = nodes.filter((n) => n.tagName === "button" && (n.className || "").includes("pa-export-btn"));
    for (const fn of btns[0].listeners.click || []) await fn();
    const firstCopy = { label: btns[0].textContent, copied };
    for (const fn of btns[1].listeners.click || []) await fn();
    const secondCopy = { label: btns[1].textContent, copied };

    const emptyBox = document.createElement("div");
    renderPlayerAudit(emptyBox, {
      cards: [p.expectedOnly],
      ref: `${p.expectedOnly.dossier_id}/${p.expectedOnly.hand_id}`,
      detail: p.expectedOnly,
      expanded: new Set(),
    }, {
      authenticated: true,
      available: true,
      onSelect: () => {},
      onTracker: () => {},
      onOriginals: () => {},
      onLogin: () => {},
      onRefresh: () => {},
      modelInputUrl: api.playerModelInputUrl,
      onModelInput: async () => ({ single_box_text: "none" }),
    });
    const emptyLinks = walk(emptyBox).filter((n) => (n.className || "").includes("pa-export-bar"));
    return { links, btnCount: btns.length, calls, firstCopy, secondCopy, emptyExportBars: emptyLinks.length };
    """
    payload = {
        "detail": {
            "dossier_id": "Deribit_BTC_20261117T080000Z",
            "hand_id": "hand_4e0efb992e4e459cca46d497",
            "sample": "AD_HOC",
            "state": "Accept",
            "side": "call",
            "expiry_ms": 1794902400000,
            "frozen_at_ms": 1794850171699,
            "model": "deepseek-v4-pro",
            "board": "1. **责任。** 示例牌面",
            "decision": {"decided_at_ms": 1794850442999, "judgment": {
                "action": "Accept", "side": "call", "depends_on_ard": False,
                "comparison": "cmp", "put": "put", "put_counter": "pc", "call": "call", "call_counter": "cc",
            }},
            "hold_reviews": [
                {"review_id": "rev_1e61af69aea5679ea554232e", "status": "VALID", "verdict": "HOLD", "decided_at_ms": 1794867328657, "public_reply": "ok"},
                {"review_id": "rev_000000000000000000000001", "status": "FAILED", "error": "TIMEOUT", "decided_at_ms": 1794867000000},
            ],
        },
        "expectedOnly": {
            "dossier_id": "Deribit_BTC_20261117T080000Z",
            "hand_id": "hand_111111111111111111111111",
            "sample": "SCHEDULED",
            "state": "EXPECTED",
            "expiry_ms": 1794902400000,
        },
    }
    out = _run_node(body, payload)
    assert out["btnCount"] == 2
    assert out["emptyExportBars"] == 0
    assert out["calls"] == [
        {"d": "Deribit_BTC_20261117T080000Z", "h": "hand_4e0efb992e4e459cca46d497", "r": None},
        {"d": "Deribit_BTC_20261117T080000Z", "h": "hand_4e0efb992e4e459cca46d497", "r": "rev_1e61af69aea5679ea554232e"},
    ]
    assert out["firstCopy"] == {
        "label": "已复制（重建）✓",
        "copied": "BOX:Deribit_BTC_20261117T080000Z:hand_4e0efb992e4e459cca46d497:initial",
    }
    assert out["secondCopy"] == {
        "label": "已复制（重建）✓",
        "copied": "BOX:Deribit_BTC_20261117T080000Z:hand_4e0efb992e4e459cca46d497:rev_1e61af69aea5679ea554232e",
    }
    assert [x["href"] for x in out["links"]] == [
        "/api/workbench/player-audit/Deribit_BTC_20261117T080000Z/hand_4e0efb992e4e459cca46d497/model-input.md",
        "/api/workbench/player-audit/Deribit_BTC_20261117T080000Z/hand_4e0efb992e4e459cca46d497/request.json",
        "/api/workbench/player-audit/Deribit_BTC_20261117T080000Z/hand_4e0efb992e4e459cca46d497/model-input.md?review_id=rev_1e61af69aea5679ea554232e",
        "/api/workbench/player-audit/Deribit_BTC_20261117T080000Z/hand_4e0efb992e4e459cca46d497/request.json?review_id=rev_1e61af69aea5679ea554232e",
    ]


def test_current_hand_banner_labels_and_error_handling():
    current_hand_js = (ROOT / "deploy" / "astra_workbench_v3" / "current-hand.js").as_uri()
    body = f"""
    const box = document.createElement("div");
    box.id = "current-hand";
    box.hidden = true;
    document.getElementById = (id) => (id === "current-hand" ? box : null);
    let mode = "ok";
    globalThis.fetch = async () => {{
      if (mode === "404_disabled") {{
        return {{
          ok: false, status: 404, headers: {{ get: () => null }},
          text: async () => JSON.stringify({{ code: "shadow_dossiers_disabled" }}),
        }};
      }}
      if (mode === "bad_json") {{
        return {{
          ok: true, status: 200, headers: {{ get: () => null }},
          text: async () => "<html>proxy</html>",
        }};
      }}
      if (mode === "403") {{
        return {{
          ok: false, status: 403, headers: {{ get: () => null }},
          text: async () => JSON.stringify({{ code: "insecure_transport" }}),
        }};
      }}
      return {{
        ok: true, status: 200, headers: {{ get: () => null }},
        text: async () => JSON.stringify({{ hand: {{ state: "Reject", expiry_ms: 1794902400000 }} }}),
      }};
    }};
    const mod = await import("{current_hand_js}");
    const labelsCheck = {{
      accept: mod.LABELS.Accept,
      reject: mod.LABELS.Reject,
      unknown: mod.LABELS.Unknown,
      missingStage: mod.stageText({{}}),
    }};
    mod.setSession(true);
    await new Promise((r) => setTimeout(r, 20));
    const rejectText = box.textContent;
    const rejectHidden = box.hidden;

    mode = "404_disabled";
    await mod.loadCurrent();
    const disabledHidden = box.hidden;
    const disabledText = box.textContent;

    mode = "bad_json";
    await mod.loadCurrent();
    const badJsonText = box.textContent;
    const badJsonHidden = box.hidden;
    const badJsonState = box.dataset.state;

    mode = "403";
    await mod.loadCurrent();
    const err403Text = box.textContent;

    return {{ labelsCheck, rejectText, rejectHidden, disabledHidden, disabledText, badJsonText, badJsonHidden, badJsonState, err403Text }};
    """
    out = _run_node(body, {})
    assert out["labelsCheck"]["accept"] == "已接受 · Accept"
    assert out["labelsCheck"]["reject"] == "已拒绝 · Reject"
    assert out["labelsCheck"]["unknown"] == "暂不判定 · Unknown"
    assert out["labelsCheck"]["missingStage"] == "状态未知"
    assert out["rejectHidden"] is False
    assert "已拒绝 · Reject" in out["rejectText"]
    assert out["disabledHidden"] is True and out["disabledText"] == ""
    assert out["badJsonHidden"] is False and "响应格式异常" in out["badJsonText"] and out["badJsonState"] == "error"
    assert "HTTP 403" in out["err403Text"]


def test_current_hand_shadow_exited_label():
    current_hand_js = (ROOT / "deploy" / "astra_workbench_v3" / "current-hand.js").as_uri()
    out = _run_node(
        f"""
        const mod = await import("{current_hand_js}");
        return {{
          label: mod.LABELS.SHADOW_EXITED,
          stage: mod.stageText({{ stage: "SHADOW_EXITED" }}),
        }};
        """,
        {},
    )
    assert out["label"] == "影子已退出（终态 · 已停止主动追踪）"
    assert out["stage"] == "影子已退出（终态 · 已停止主动追踪）"


def _base_audit_detail(*, valuation_tier="UNPRICED", exit_terminal=True):
    t0 = 1_795_400_000_000
    events = [
        {"event_id": "ev_not_1", "kind": "CONDITION_NOT_TRIGGERED", "observed_at_ms": t0 + 60_000},
        {"event_id": "ev_obs_1", "kind": "OBSERVATION", "observed_at_ms": t0 + 120_000},
    ]
    grid_history = [
        {"event_id": "ev_not_1", "kind": "CONDITION_NOT_TRIGGERED", "observed_at_ms": t0 + 60_000, "recorded_at_ms": t0 + 60_010, "post_revoke": False},
    ]
    for i in range(177):
        ts = t0 + (i + 2) * 300_000
        post_rev = i >= 172
        ev = {
            "event_id": f"ev_trig_{i}",
            "kind": "CONDITION_TRIGGERED",
            "observed_at_ms": ts,
            "recorded_at_ms": ts + 100,
            "summary": "entry_not_returned",
            "post_revoke": post_rev,
        }
        events.append(ev)
        grid_history.append({
            "event_id": ev["event_id"],
            "kind": "CONDITION_TRIGGERED",
            "observed_at_ms": ts,
            "recorded_at_ms": ts + 100,
            "post_revoke": post_rev,
        })
    revoke_ts = t0 + 174 * 300_000
    events.extend([
        {"event_id": "ev_hold", "kind": "AI_HOLD", "observed_at_ms": t0 + 300_000, "verdict": "HOLD", "summary": "继续持有"},
        {"event_id": "ev_fail", "kind": "HOLD_REVIEW_FAILED", "observed_at_ms": t0 + 600_000, "verdict": "FAILED", "summary": "TIMEOUT"},
        {"event_id": "ev_rev", "kind": "AI_REVOKE", "observed_at_ms": revoke_ts, "verdict": "REVOKE", "summary": "建议撤销"},
        {"event_id": "ev_sx", "kind": "SHADOW_EXITED", "observed_at_ms": revoke_ts + 500, "summary": valuation_tier},
    ])
    sx = {
        "schema": "shadow_exit_snapshot@1",
        "exit_state": "SHADOW_EXITED",
        "terminal": True,
        "active_monitoring_stopped": True,
        "exit_reason": "AI_REVOKE",
        "review_id": "rev_111111111111111111111111",
        "shadow_exit_at_ms": revoke_ts + 500,
        "valuation_tier": valuation_tier,
        "exit_pnl_btc": -0.0021 if valuation_tier == "EXECUTABLE_TWO_LEG_QUOTE" else None,
        "exit_pnl_usd": -210.0 if valuation_tier == "EXECUTABLE_TWO_LEG_QUOTE" else None,
        "indicative_exit_pnl_btc": -0.0019 if valuation_tier == "INDICATIVE_MARK_ONLY" else None,
        "spot_usd": 100_000.0,
        "exit_quote": {
            "short_buyback_ask_btc": 0.0080,
            "long_sale_bid_btc": 0.0015,
            "short_buy_btc": 0.0080,
            "long_sale_btc": 0.0015,
            "close_debit_btc": 0.0065,
            "close_fee_btc": 0.0002,
        },
        "quote_gap_reasons": [] if valuation_tier == "EXECUTABLE_TWO_LEG_QUOTE" else ["short_quote_stale_at_revoke"],
        "last_indicative_reference": (
            {
                "point_id": "p_pre",
                "asof_ms": revoke_ts - 60_000,
                "age_at_revoke_ms": 60_000,
                "spot_usd": 101_200.0,
                "float_pnl_btc": -0.0014,
                "basis": "pre_revoke_asof_series_reference_not_exit_fill",
            }
            if valuation_tier == "UNPRICED" else None
        ),
    }
    return {
        "dossier_id": "Deribit_BTC_20261124T080000Z",
        "hand_id": "hand_222222222222222222222222",
        "sample": "SCHEDULED",
        "state": "Accept",
        "side": "call",
        "expiry_ms": t0 + 16 * 3_600_000,
        "frozen_at_ms": t0,
        "model": "deepseek-v4-pro",
        "board": "1. **责任。** 示例",
        "decision": {"decided_at_ms": t0 + 10_000, "judgment": {
            "action": "Accept", "side": "call", "depends_on_ard": False,
            "comparison": "c", "put": "p", "put_counter": "pc", "call": "cc", "call_counter": "ccnt",
        }},
        "condition": {
            "historical_facts": "hist",
            "accepted_conditions": "acc",
            "invalidation": "entry_not_returned",
            "invalidation_meaning": "meaning",
            "check_clock": {"first_at_ms": t0 + 60_000, "interval_ms": 300_000},
        },
        "events": events,
        "grid_check_history": grid_history,
        "post_revoke_grid_check_count": 5,
        "grid_check_summary": {"total": 178, "not_triggered": 1, "triggered": 177, "unknown": 0, "latest_at_ms": t0 + 178 * 300_000},
        "hold_reviews": [
            {"review_id": "rev_111111111111111111111111", "status": "VALID", "verdict": "REVOKE", "decided_at_ms": revoke_ts, "public_reply": "建议撤销"},
        ],
        "shadow_management": {
            "mechanical_condition_state": "TRIGGERED",
            "condition_triggered_at_ms": t0 + 2 * 300_000,
            "ai_revoked_at_ms": revoke_ts,
            "protocol_status": "ALIGNED",
            "shadow_action": "REVOKE_ALIGNED",
            "exit_terminal": exit_terminal,
            "exit_state": "SHADOW_EXITED" if exit_terminal else "ACTIVE",
            "active_monitoring_stopped": exit_terminal,
            "valuation_tier": valuation_tier if exit_terminal else None,
            "shadow_exit": sx if exit_terminal else None,
            "exit_status": "REVOKE_NO_QUOTE",
        },
        "shadow_exit": sx if exit_terminal else None,
        "outcome": {
            "classification": "OFFICIAL",
            "delivery": 105_000.0,
            "liability_btc": {"put": 0.0, "call": 0.019048},
            "t0_net_credit_after_entry_fee_btc": 0.0046,
            "hold_to_expiry_shadow_net_btc": -0.0222,
        },
    }


def _render_audit_payload(detail):
    return _run_node(
        """
        const box = document.createElement("div");
        const walk = (n, out = []) => {
          if (n && n.attrs) {
            out.push(n);
            for (const c of n.childNodes) walk(c, out);
          }
          return out;
        };
        renderPlayerAudit(box, {
          cards: [p.detail],
          ref: `${p.detail.dossier_id}/${p.detail.hand_id}`,
          detail: p.detail,
          expanded: new Set(),
        }, {
          authenticated: true,
          available: true,
          onSelect: () => {},
          onTracker: () => {},
          onOriginals: () => {},
          onLogin: () => {},
          onRefresh: () => {},
          modelInputUrl: api.playerModelInputUrl,
        });
        const nodes = walk(box);
        const byClass = (cls) => nodes.find((n) => (n.className || "").split(/\\s+/).includes(cls));
        const condCard = byClass("pa-cond-card");
        const evTbl = byClass("pa-events-table");
        const evRows = evTbl ? walk(evTbl).filter((n) => n.tagName === "tr").slice(1).map((r) => r.textContent) : [];
        const auditDet = byClass("pa-grid-audit");
        const auditRows = auditDet ? walk(auditDet).filter((n) => n.tagName === "tr").slice(1).map((r) => r.textContent) : [];
        const holdCard = byClass("pa-hold-card");
        const holdChips = holdCard ? walk(holdCard).filter((n) => (n.className || "").includes("chip")).map((n) => n.textContent) : [];
        const outCard = byClass("pa-outcome-card");
        const outPairs = outCard
          ? walk(outCard).filter((n) => n.tagName === "dt" || n.tagName === "dd").map((n) => n.textContent)
          : [];
        return {
          condText: condCard ? condCard.textContent : "",
          evRows,
          auditSummary: auditDet ? auditDet.childNodes[0].textContent : null,
          auditCount: auditRows.length,
          auditLegacyRows: auditRows.filter((t) => t.includes("撤销后历史遗留")).length,
          holdText: holdCard ? holdCard.textContent : "",
          holdChips,
          outText: outCard ? outCard.textContent : "",
          outPairs,
        };
        """,
        {"detail": detail},
    )


def test_player_audit_shadow_exit_collapsed_grid_and_counterfactual_separation():
    out = _render_audit_payload(_base_audit_detail(valuation_tier="UNPRICED"))
    assert "AI 撤销 → 影子已退出 → 已停止主动追踪" in out["condText"]
    assert "含修复前遗留撤销后检查 5 次，已默认折叠" in out["condText"]
    assert "(已折叠 175 条重复触发检查)" in out["condText"]

    trig_rows = [r for r in out["evRows"] if "网格检查：已触发破坏条件" in r]
    assert len(trig_rows) == 2
    assert any("AI 建议撤销 · REVOKE" in r for r in out["evRows"])
    assert any("影子已退出（终态 · 已停止主动追踪）" in r for r in out["evRows"])
    assert any("AI 续持 · HOLD" in r for r in out["evRows"])
    assert any("持仓复核调用失败" in r for r in out["evRows"])

    assert out["auditSummary"] == "展开完整网格检查审计轨迹（共 178 次）"
    assert out["auditCount"] == 178
    assert out["auditLegacyRows"] == 5

    assert "影子已退出 · 已停止主动追踪" in out["holdChips"]
    assert "缺价不可核实 UNPRICED" in out["holdChips"]
    assert "影子退出，退出价/收益暂不可核实（UNPRICED）" in out["holdText"]
    assert "撤销前最后有效指示值（非退出成交价）：" in out["holdText"]
    assert "101,200.0 USD" in out["holdText"] and "−0.001400 BTC" in out["holdText"]

    assert "冻结影子退出净结果（UNPRICED）" in out["outPairs"]
    assert "反事实对照：若持有至到期影子净结果" in out["outPairs"]
    assert "持有到期影子净结果" not in out["outPairs"]  # must use counterfactual label instead


def test_player_audit_shadow_exit_executable_indicative_and_legacy_fallback():
    exec_out = _render_audit_payload(_base_audit_detail(valuation_tier="EXECUTABLE_TWO_LEG_QUOTE"))
    assert "双腿报价估值" in exec_out["holdChips"]
    assert "影子模拟双腿退出估值，非实盘成交" in exec_out["holdText"]
    assert "冻结影子退出净结果（EXECUTABLE）" in exec_out["outPairs"]
    assert "反事实对照：若持有至到期影子净结果" in exec_out["outPairs"]

    ind_out = _render_audit_payload(_base_audit_detail(valuation_tier="INDICATIVE_MARK_ONLY"))
    assert "仅指示性估值" in ind_out["holdChips"]
    assert "影子退出（指示性标记估值 · 非可执行报价）" in ind_out["holdText"]
    assert "冻结影子退出净结果（INDICATIVE）" in ind_out["outPairs"]

    legacy = _base_audit_detail(valuation_tier="UNPRICED", exit_terminal=False)
    legacy["shadow_management"]["exit_status"] = "SIMULATED_BUY_BACK_SHORT"
    legacy["shadow_management"]["buyback_cost_btc"] = 0.008
    legacy["shadow_management"]["buyback_fee_btc"] = 0.0002
    legacy_out = _render_audit_payload(legacy)
    assert "影子退出：模拟买回短腿" in legacy_out["holdChips"]
    assert "影子已退出 · 已停止主动追踪" not in legacy_out["holdChips"]
    assert "持有到期影子净结果" in legacy_out["outPairs"]
    assert "反事实对照：若持有至到期影子净结果" not in legacy_out["outPairs"]

    # Backend tick_ms grid_check_history + indicative_marks + debit_btc compatibility
    tick_detail = _base_audit_detail(valuation_tier="INDICATIVE_MARK_ONLY")
    t0 = tick_detail["frozen_at_ms"]
    tick_detail["grid_check_history"] = [
        {"kind": "CONDITION_TRIGGERED", "tick_ms": t0 + 300_000, "post_revoke": False},
        {"kind": "CONDITION_TRIGGERED", "tick_ms": t0 + 900_000, "post_revoke": True},
    ]
    tick_detail["shadow_management"]["shadow_exit"]["indicative_marks"] = {
        "short_mark_btc": 0.0081, "long_mark_btc": 0.0019, "asof_ms": t0 + 840_000,
    }
    tick_detail["shadow_exit"] = tick_detail["shadow_management"]["shadow_exit"]
    tick_out = _render_audit_payload(tick_detail)
    assert tick_out["auditSummary"] == "展开完整网格检查审计轨迹（共 2 次）"
    assert tick_out["auditLegacyRows"] == 1
    assert "短腿标记 0.008100 BTC / 保护腿标记 0.001900 BTC" in tick_out["holdText"]
    assert "UTC" in tick_out["condText"]
