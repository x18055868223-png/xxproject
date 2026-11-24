"""v3 lock list: the latest five by default; tracking and open contracts are always shown."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOCKS = (ROOT / "deploy" / "astra_workbench_v3" / "locks.js").as_uri()


def visible(episodes, **opts):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    script = (f'import {{ visibleLocks }} from "{LOCKS}";\n'
              'import fs from "node:fs";\n'
              'const p = JSON.parse(fs.readFileSync(0, "utf8"));\n'
              'const out = visibleLocks(p.episodes, p.opts);\n'
              'console.log(JSON.stringify({ ids: out.rows.map((e) => e.episode_id), hidden: out.hidden }));')
    result = subprocess.run([node, "--input-type=module", "-e", script], input=json.dumps({"episodes": episodes, "opts": opts}),
                            text=True, encoding="utf-8", capture_output=True, check=False, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def eps(n, active=()):
    return [{"episode_id": f"e{i}", "created_at_ms": i, "status": "active" if i in active else "expired"} for i in range(n)]


def test_at_most_the_latest_five_by_default():
    out = visible(eps(8))
    assert out == {"ids": ["e7", "e6", "e5", "e4", "e3"], "hidden": 3}
    assert visible(eps(4))["hidden"] == 0


def test_tracking_and_open_contracts_stay_visible_and_show_all_lists_everything():
    out = visible(eps(8, active=(1,)), selected="e0")
    assert out["ids"] == ["e7", "e6", "e5", "e4", "e3", "e1", "e0"] and out["hidden"] == 1
    assert visible(eps(8), showAll=True) == {"ids": [f"e{i}" for i in range(7, -1, -1)], "hidden": 0}


# ── lock detail: recomputed lock-time values, this expiry's structure, verification commitments ──
SHIM = r"""
class T { constructor(t) { this.text = String(t); } get textContent() { return this.text; } }
class N {
  constructor(tag) { this.tagName = tag; this.childNodes = []; this.attrs = {}; this.className = ""; this.dataset = {}; this.style = {}; }
  append(...cs) { for (const c of cs) this.childNodes.push(c); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  addEventListener() {}
  set textContent(v) { this.childNodes = [new T(v)]; }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(""); }
}
globalThis.Node = N;
globalThis.document = { createElement: (t) => new N(t), createElementNS: (_, t) => new N(t), createTextNode: (t) => new T(t) };
const walk = (n, out = []) => { if (n && n.attrs) { out.push(n); for (const c of n.childNodes) walk(c, out); } return out; };
const sections = (n) => walk(n).filter((x) => x.tagName === "h5").map((x) => x.textContent);
"""


def run(body, payload=None):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    script = (SHIM + 'import fs from "node:fs";\n'
              'const p = JSON.parse(fs.readFileSync(0, "utf8"));\n'
              f'const L = await import("{LOCKS}");\n'
              f"const out = await (async () => {{ {body} }})();\nconsole.log(JSON.stringify(out));")
    result = subprocess.run([node, "--input-type=module", "-e", script], input=json.dumps(payload or {}),
                            text=True, encoding="utf-8", capture_output=True, check=False, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


T0 = 1_793_070_000_000
COMMIT = {"verification_id": "dpv_1", "path_version": "2",
          "limit": {"credit_btc": 0.002, "valid_until_ms": T0 + 30 * 60_000, "reservation_btc": 0.0015, "set_at_ms": T0 - 60_000},
          "plan": {"choice": "hold", "label_cn": "持有到期，不补救"}}


def pts(*credits, start=T0):
    return [{"asof_ms": start + i * 60_000, "visible_credit_btc": c} for i, c in enumerate(credits)]


def test_commitment_fill_reached_open_and_missed_from_one_minute_samples():
    fill = lambda points, now: run("return L.commitmentFill(p.c, p.pts, { t0: p.t0, now: p.now });",
                                   {"c": COMMIT, "pts": points, "t0": T0, "now": now})
    # first sample in the window at or above the limit; one before the lock does not count
    reached = fill([{"asof_ms": T0 - 60_000, "visible_credit_btc": 0.003}, *pts(0.0018, 0.0021, 0.0025)], T0 + 3_600_000)
    assert reached == {"state": "reached", "at": T0 + 60_000, "best": 0.0021}
    assert fill(pts(0.0018, 0.0019), T0 + 5 * 60_000)["state"] == "open"
    # a sample after the validity is ignored
    missed = fill([*pts(0.0018, 0.0019), {"asof_ms": T0 + 31 * 60_000, "visible_credit_btc": 0.003}], T0 + 3_600_000)
    assert missed["state"] == "missed" and missed["best"] == 0.0019
    assert run("return L.commitmentFill({ plan: { label_cn: 'x' } }, [], {});") is None
    # a verification that joined an older lock: samples before its own limit do not count
    late = {**COMMIT, "limit": {**COMMIT["limit"], "set_at_ms": T0 + 90_000, "valid_until_ms": T0 + 31 * 60_000}}
    out = run("return L.commitmentFill(p.c, p.pts, { t0: p.t0, now: p.now });",
              {"c": late, "pts": pts(0.0030, 0.0018, 0.0019), "t0": T0, "now": T0 + 3_600_000})
    assert out["state"] == "missed" and out["best"] == 0.0019
    # the window closes at expiry, so a limit valid past it is missed, not still open
    out = run("return L.commitmentFill(p.c, p.pts, { t0: p.t0, expiry: p.exp, now: p.now });",
              {"c": COMMIT, "pts": pts(0.0018), "t0": T0, "exp": T0 + 5 * 60_000, "now": T0 + 10 * 60_000})
    assert out["state"] == "missed"


def test_commitment_view_states_limit_fill_and_plan():
    text = lambda points, now: run("return L.commitmentView(p.c, { points: p.pts }, { t0: p.t0, now: p.now }).textContent;",
                                   {"c": COMMIT, "pts": points, "t0": T0, "now": now})
    hit = text(pts(0.0018, 0.0021), T0 + 3_600_000)
    assert hit.startswith("挂单价 0.002 BTC（保留价 0.0015 BTC，有效至 ")
    assert "按采样，市场买价在 " in hit and "达到挂单价（按买价可成交）" in hit
    assert hit.endswith("被触及时：持有到期，不补救")
    assert "有效期内市场买价最高 0.0019 BTC，未达到挂单价（被动成交无法确认）" in text(pts(0.0018, 0.0019), T0 + 3_600_000)
    assert "有效期内，尚未达到（目前最高 0.0019 BTC）" in text(pts(0.0018, 0.0019), T0 + 5 * 60_000)
    assert run("return L.commitmentView(null, null, {});") is None
    failed = run("return L.commitmentView(p.c, null, { error: '超时' }).textContent;", {"c": COMMIT})
    assert "采样读取失败：超时" in failed and "正在读取" not in failed
    thin = run("return L.commitmentView(p.c, { points: p.pts, thinned: true }, { t0: p.t0, now: p.now }).textContent;",
               {"c": COMMIT, "pts": pts(0.0018, 0.0019), "t0": T0, "now": T0 + 3_600_000})
    assert "采样已抽稀" in thin


LEVEL_ROWS = [
    {"key": "levels.pin", "group": "levels", "label_cn": "本期 Pin", "status": "changed",
     "t0": {"value": 98000.0, "unit": "USD"}, "t1": {"value": 99000.0, "unit": "USD"},
     "to_short_pct": {"t0": 1.0, "t1": 2.04}, "stale": {"t0": False, "t1": False}},
    {"key": "levels.flip", "group": "levels", "label_cn": "Gamma 翻转位", "status": "not_recorded_at_lock",
     "t0": None, "t1": {"value": 97500.0, "unit": "USD"}, "to_short_pct": {"t0": None, "t1": 0.5}, "stale": {"t0": False, "t1": True}},
]


def test_levels_view_lists_lock_time_latest_change_and_distance_to_short():
    rows = run("const v = L.levelsView(p.rows); return walk(v).filter((x) => x.tagName === 'tr').slice(1).map((r) => r.childNodes.map((c) => c.textContent));",
               {"rows": LEVEL_ROWS})
    assert rows[0] == ["本期 Pin", "98,000", "99,000", "+1,000", "+2.04%（锁定时 +1.00%）"]
    assert rows[1] == ["Gamma 翻转位", "未记录", "97,500", "锁定时未记录", "+0.50%"]
    note = run("return L.levelsView(p.rows).textContent;", {"rows": LEVEL_ROWS})
    assert "部分读数采集时数据偏旧" in note


def test_lock_detail_places_levels_after_changes_marks_backfill_and_not_generated():
    detail = {
        "episode": {"episode_id": "lce_1", "status": "active", "created_at_ms": T0},
        "fixed_contract": {"side": "put", "short_strike_usd": 96000, "long_strike_usd": 94000, "width_usd": 2000, "quantity_btc": 1},
        "baseline": {"asof_ms": T0}, "latest": {"asof_ms": T0 + 60_000},
        "overview": {"reference_result": {}},
        "rows": [
            {"key": "context.active_flow", "group": "context", "status": "changed",
             "t0": {"value": {"combined": {"direction": "bearish", "data_ready": True}}, "compat_backfill_applied": True,
                    "backfill_source": "snapshot.fact_context.market_facts"},
             "t1": {"value": {"combined": {"direction": "bullish", "data_ready": True}}}},
            {"key": "opinion.q1.direct_refs", "group": "opinion", "status": "not_recorded_at_lock", "t0": None,
             "t1": {"value": {"summary_cn": "Q1"}}},
            *LEVEL_ROWS,
        ],
        "process": {}, "verification_commitments": COMMIT,
    }
    out = run("const v = L.lockDetail(p.d, { series: { points: p.pts } }); return { secs: sections(v), text: v.textContent };",
              {"d": detail, "pts": pts(0.0018, 0.0021)})
    secs = out["secs"]
    assert secs.index("本期结构（锁定时 vs 最新）") == secs.index("之后的变化（明细）") + 1
    assert secs.index("核验时的承诺") == secs.index("锁定时") + 1
    assert "主动卖方占优（由锁定时市场事实重算）" in out["text"]
    assert "锁定时未生成" in out["text"] and "锁定后才出现" not in out["text"]


# ── market background: reading · key numbers, the change of the headline numbers ──
TMV = lambda blend, h24, h48, state: {"tmv_state": state, "tmv_blend": blend, "tmvf_24h": {"tmv_final": h24, "core": {"trend_strength_pct": 0.166}},
                                      "tmvf_48h": {"tmv_final": h48}}
FLOW = lambda score, cvd12, cvd4, d: {"combined": {"direction": d, "data_ready": True, "score": score},
                                      "slow_12h": {"cvd_sum": cvd12, "momentum_return_pct": -1.013}, "fast_4h": {"cvd_sum": cvd4, "direction": "neutral"}}
SPACE = lambda score, gamma, flip, regime: {"space_gate": {"state": "STRUCTURE_BLOCKED"}, "gamma_regime": {"regime": regime},
                                            "space_gate_anchor": {"score": score}, "space_gate_net_gamma": {"value": gamma, "unit": "USD"},
                                            "space_context_anchor": {"effective_flip_point": flip},
                                            "space_context_gamma": {"distance_to_flip_pct": 0.118}}
BF = {"compat_backfill_applied": True, "backfill_source": "snapshot.fact_context.market_facts"}


def ctx(row, extra=None):
    return run("return L.contextRow(p.r, p.x || {});", {"r": row, "x": extra})


def test_context_cells_carry_numbers_and_the_change_column_the_headline_delta():
    tmv = ctx({"key": "context.tmv_price_path", "status": "changed",
               "t0": {"value": TMV(-0.5545, -0.567, -0.546, "strong_bearish_bias"), **BF},
               "t1": {"value": TMV(0.5216, 0.464, 0.560, "strong_bullish_bias")}})
    assert tmv["t0"] == "强偏空 · 综合 −0.55（24h −0.57 / 48h −0.55），24h 趋势强度 0.17%"
    assert tmv["t1"].startswith("强偏多 · 综合 +0.52（24h +0.46 / 48h +0.56）")
    assert tmv["status"] == "综合 +1.08" and tmv["backfilled"] and tmv["moved"]
    flow = ctx({"key": "context.active_flow", "status": "changed", "t0": {"value": FLOW(-0.399, 1089.9, 199.8, "bearish")},
                "t1": {"value": FLOW(0.476, 1739.2, 41.9, "bullish")}})
    assert flow["t0"] == "主动卖方占优 · 综合 −0.40，12h CVD +1,090 BTC（价格 −1.01%），4h CVD +200 BTC（均衡）"
    assert flow["status"] == "综合 +0.88；12h CVD +649 BTC"
    funding = ctx({"key": "context.funding", "status": "changed",
                   "t0": {"value": {"last_rate": 0.0001, "semantics": {"crowding_state": "NOT_CROWDED"},
                                    "tmvf_24h": {"funding_cum": 0.00027945}, "tmvf_48h": {"funding_state": "crowded_long"}}},
                   "t1": {"value": {"last_rate": 0.00005008, "semantics": {"crowding_state": "NOT_CROWDED"}}}})
    assert funding["t0"] == "费率未拥挤 · 最新 +0.0100%，24h 累计 +0.0279%，48h 多头拥挤"
    assert funding["t1"] == "费率未拥挤 · 最新 +0.0050%" and funding["status"] == "最新费率 −0.50 bp"
    skew = ctx({"key": "context.skew", "status": "changed", "t0": {"value": {"rr_blend": -0.0324, "rr_z": -0.32, "delta_rr": -0.00084}},
                "t1": {"value": {"rr_blend": 0.0114}}})
    assert skew["t0"] == "看跌期权更贵 · RR −0.032，z −0.32，较基线 −0.0008" and skew["status"] == "RR +0.044"
    macro = ctx({"key": "context.macro", "status": "changed",
                 "t0": {"value": {"macro_score": -0.172, "component_summaries": [
                     {"key": "VOLQ", "scoring_value": -6.71, "scoring_unit": "pct"}, {"key": "US10Y", "scoring_value": -0.2, "scoring_unit": "bps"},
                     {"key": "weird_code", "scoring_value": 1, "scoring_unit": "pct"}]}},
                 "t1": {"value": {"macro_score": -0.132}}})
    assert macro["t0"] == "得分 −0.17 · 纳指波动率 −6.71%，美债 10 年 −0.20 bp" and macro["status"] == "得分 +0.04"


def test_space_row_uses_the_structure_flip_and_arrows_the_anchor_score():
    row = {"key": "context.option_space", "status": "changed",
           "t0": {"value": SPACE(48.86, 9974865.24, 79818.1, "TRANSITION"), **BF},
           "t1": {"value": SPACE(2.68, 109375932.35, 79783.4, "POSITIVE_GAMMA_PINNING")}}
    out = ctx(row, {"t0": {"flip": 79819.26, "spot": 79592.22}, "t1": {"flip": 79783.38, "spot": 80915.47}})
    assert out["t0"] == "FMZ 空间门关闭（仅参考），过渡 · 锚定分 48.9，净 Gamma +9.97M 美元，翻转位 79,819（现价在下方 0.28%）"
    assert out["t1"].endswith("净 Gamma +109M 美元，翻转位 79,783（现价在上方 1.42%）")
    assert out["status"] == "锚定分 48.9 → 2.7；净 Gamma +99.4M 美元"
    # without the structure flip: the anchor's flip is labelled, its unsigned distance gets no direction
    alone = ctx(row)
    assert "翻转位（锚点口径） 79,818（相距 0.12%）" in alone["t0"] and "上方" not in alone["t0"]


def test_equal_headline_numbers_read_unchanged_and_missing_fields_never_print_raw():
    same = ctx({"key": "context.tmv_price_path", "status": "changed",
                "t0": {"value": TMV(-0.551, None, None, "bearish_bias"), **BF}, "t1": {"value": TMV(-0.549, None, None, "strong_bearish_bias")}})
    assert same["status"] == "主读数未变" and not same["moved"]
    assert same["t0"] == "偏空 · 综合 −0.55，24h 趋势强度 0.17%"
    sparse = ctx({"key": "context.option_space", "status": "changed", "t0": {"value": {"space_gate": {"state": "SOMETHING_NEW"}}},
                  "t1": {"value": {"gamma_regime": {"regime": "TRANSITION"}}}})
    assert sparse["t0"] == "—" and sparse["t1"] == "过渡" and sparse["status"] == "有变化"
    missing = ctx({"key": "context.funding", "status": "latest_missing", "t0": {"value": {"last_rate": 0.0001}}, "t1": None})
    assert missing["t1"] == "未记录" and missing["status"] == "最新缺失"
    q = ctx({"key": "opinion.q1.direct_refs", "status": "not_recorded_at_lock", "t0": None, "t1": {"value": {"summary_cn": "Q1 摘要"}}})
    assert q["t0"] == "—" and q["t1"] == "Q1 摘要" and q["status"] == "锁定时未生成"


def test_trace_rows_and_a_never_computed_risk_reference_are_not_shown():
    shown = lambda r: run("return L.ctxShown(p.r);", {"r": r})
    assert not shown({"key": "context.routing_relations", "display": "trace"})
    assert not shown({"key": "risk.status", "t0": {"value": "missing"}, "t1": {"value": "missing"}})
    assert not shown({"key": "risk.status", "t0": None, "t1": {"value": "missing"}})
    assert shown({"key": "risk.status", "t0": {"value": "missing"}, "t1": {"value": "supported"}})
    assert shown({"key": "context.macro", "t0": None, "t1": {"value": {}}})


def test_levels_view_words_why_a_lock_time_level_is_absent_and_marks_recomputed_values():
    rows = [
        {"key": "levels.pin", "group": "levels", "label_cn": "本期 Pin", "status": "not_recorded_at_lock",
         "t0_missing_reason": "expiry_profile_not_saved", "t0": None, "t1": {"value": 80500.0}, "to_short_pct": {"t1": 1.9}},
        {"key": "levels.max_pain_this", "group": "levels", "label_cn": "本期最大痛点", "status": "not_recorded_at_lock",
         "t0_missing_reason": "source_not_covering_expiry", "t0": {"value": None, **BF}, "t1": {"value": 80000.0}, "to_short_pct": {}},
        {"key": "levels.flip", "group": "levels", "label_cn": "Gamma 翻转位", "status": "changed",
         "t0": {"value": 79819.26, **BF}, "t1": {"value": 79783.38}, "to_short_pct": {"t0": 1.04, "t1": 0.99}},
    ]
    out = run("const v = L.levelsView(p.rows); return { rows: walk(v).filter((x) => x.tagName === 'tr').slice(1).map((r) => r.childNodes.map((c) => c.textContent)), text: v.textContent };",
              {"rows": rows})
    assert out["rows"][0][:4] == ["本期 Pin", "未保存", "80,500", "锁定时未保存本期期权分布，无法重算"]
    assert out["rows"][1][:4] == ["本期最大痛点", "未覆盖", "80,000", "当时数据源未覆盖本到期日"]
    assert out["rows"][2] == ["Gamma 翻转位", "79,819（重算）", "79,783", "−36", "+0.99%（锁定时 +1.04%）"]
    assert "标“重算”的锁定时读数" in out["text"]


# ── P5 shadow exit terminal view, valuation tiers, counterfactual isolation ──
def test_shadow_exit_list_stop_reason_chips_and_outcome_tiers():
    episodes = [
        {
            "episode_id": "lce_active",
            "status": "active",
            "created_at_ms": T0 + 10_000,
            "contract": {"side": "put", "short_strike_usd": 95000, "long_strike_usd": 93000, "expiry_ms": T0 + 86_400_000},
            "ai_shadow": {"model": "deepseek-v4-pro"},
            "ai_dossier_management": {"latest_verdict": "HOLD"},
            "outcome": {"verdict": "profit", "basis": "floating_now", "flat_band_usd": 10},
        },
        {
            "episode_id": "lce_exec",
            "status": "stopped",
            "stop_reason": "shadow_revoked_terminal",
            "created_at_ms": T0 + 9_000,
            "contract": {"side": "call", "short_strike_usd": 102000, "long_strike_usd": 104000, "expiry_ms": T0 + 86_400_000},
            "ai_shadow": {"model": "deepseek-v4-pro"},
            "ai_dossier_management": {
                "latest_verdict": "REVOKE",
                "exit_terminal": True,
                "exit_state": "SHADOW_EXITED",
                "shadow_management": {"exit_terminal": True, "exit_state": "SHADOW_EXITED"},
            },
            "outcome": {"verdict": "loss", "basis": "shadow_exit_frozen", "frozen_exit_pnl_btc": -0.0021, "flat_band_usd": 10},
        },
        {
            "episode_id": "lce_ind",
            "status": "stopped",
            "stop_reason": "shadow_revoked_terminal",
            "created_at_ms": T0 + 8_000,
            "contract": {"side": "call", "short_strike_usd": 102000, "long_strike_usd": 104000, "expiry_ms": T0 + 86_400_000},
            "ai_shadow": {"model": "deepseek-v4-pro"},
            "ai_dossier_management": {"latest_verdict": "REVOKE", "exit_terminal": True, "exit_state": "SHADOW_EXITED"},
            "outcome": {"verdict": None, "basis": "shadow_exit_indicative_only", "flat_band_usd": 10},
        },
        {
            "episode_id": "lce_unp",
            "status": "stopped",
            "stop_reason": "shadow_revoked_terminal",
            "created_at_ms": T0 + 7_000,
            "contract": {"side": "put", "short_strike_usd": 95000, "long_strike_usd": 93000, "expiry_ms": T0 + 86_400_000},
            "ai_shadow": {"model": "deepseek-v4-pro"},
            "ai_dossier_management": {"latest_verdict": "REVOKE", "exit_terminal": True, "exit_state": "SHADOW_EXITED"},
            "outcome": {"verdict": None, "basis": "shadow_exit_unpriced", "flat_band_usd": 10},
        },
    ]
    out = run(
        """
        const tbl = L.lockList(p.episodes, { selected: null, onOpen: () => {}, renderDetail: () => document.createElement('div') });
        const rows = walk(tbl).filter((x) => x.tagName === 'tr' && (x.className || '').includes('lock-row'));
        return rows.map((r) => {
          const tds = r.childNodes;
          const chips = walk(tds[2]).filter((x) => (x.className || '').includes('chip')).map((x) => x.textContent);
          const outcomeSpan = walk(tds[3]).find((x) => x.attrs && x.attrs.title);
          return {
            id: r.attrs.id,
            statusText: tds[2].textContent,
            chips,
            outcomeText: tds[3].textContent,
            outcomeTitle: outcomeSpan ? outcomeSpan.attrs.title : null,
          };
        });
        """,
        {"episodes": episodes},
    )
    by_id = {r["id"]: r for r in out}
    assert "追踪中" in by_id["lock-lce_active"]["statusText"] and "影子已退出" not in by_id["lock-lce_active"]["chips"]
    assert by_id["lock-lce_active"]["outcomeText"] == "盈利 浮动"

    exec_row = by_id["lock-lce_exec"]
    assert "AI 撤销 · 影子已退出（已停止主动追踪）" in exec_row["statusText"]
    assert "AI · REVOKE" in exec_row["chips"] and "影子已退出" in exec_row["chips"]
    assert exec_row["outcomeText"] == "亏损 影子退出"
    assert "按撤销时冻结双腿影子报价" in exec_row["outcomeTitle"]

    ind_row = by_id["lock-lce_ind"]
    assert ind_row["outcomeText"] == "指示估值"
    assert "指示估值 · 非实盘" in ind_row["outcomeTitle"]

    unp_row = by_id["lock-lce_unp"]
    assert unp_row["outcomeText"] == "价不可核实"
    assert "退出价不可核实" in unp_row["outcomeTitle"]


def test_shadow_exit_lock_detail_three_tiers_counterfactual_and_no_relock():
    future_expiry = 4_000_000_000_000
    base_detail = {
        "episode": {
            "episode_id": "lce_sx1",
            "status": "stopped",
            "stop_reason": "shadow_revoked_terminal",
            "created_at_ms": T0,
            "latest_snapshot_id": "snap_latest",
            "latest_decision_asof_ms": T0 + 120_000,
            "ai_shadow": {"dossier_id": "Deribit_BTC_20261124T080000Z", "hand_id": "hand_aaaaaaaaaaaaaaaaaaaaaaaa", "model": "deepseek-v4-pro"},
        },
        "fixed_contract": {
            "side": "call",
            "short_strike_usd": 102000,
            "long_strike_usd": 104000,
            "width_usd": 2000,
            "quantity_btc": 1,
            "expiry_ms": future_expiry,
            "long_instrument": "BTC-24NOV26-104000-C",
        },
        "baseline": {"asof_ms": T0},
        "latest": {"asof_ms": T0 + 600_000},
        "overview": {
            "remaining_to_expiry_ms": 3_600_000,
            "reference_result": {"entry_credit_btc": 0.005, "entry_fee_btc": 0.0004, "entry_after_fee_btc": 0.0046},
            "path_now": {"applicable": True, "touched": True, "touch_cn": "已触及短腿"},
        },
        "settlement": {
            "status": "settled",
            "delivery": {"delivery_price_usd": 105000, "date": "2026-11-24"},
            "payout_btc": 0.019048,
            "entry_after_fee_btc": 0.0046,
            "settlement_result_btc": -0.014448,
            "settlement_result_usd": -1517.0,
        },
        "rows": [],
        "process": {},
    }
    series = {
        "points": [{"asof_ms": T0 + 600_000, "spot_usd": 105000, "float_pnl_btc": -0.0222, "max_profit_btc": 0.005, "remaining_credit_btc": 0.0272}]
    }

    def render_tier(sx, extra_episode=None, extra_mgmt=None):
        detail = json.loads(json.dumps(base_detail))
        if extra_episode:
            detail["episode"].update(extra_episode)
        detail["ai_dossier_management"] = {
            "frozen_check_count": 5,
            "condition_triggered": True,
            "condition_triggered_at_ms": T0 + 300_000,
            "latest_verdict": "REVOKE",
            "exit_terminal": True,
            "exit_state": "SHADOW_EXITED",
            "shadow_management": {
                "exit_terminal": True,
                "exit_state": "SHADOW_EXITED",
                "active_monitoring_stopped": True,
                "valuation_tier": sx["valuation_tier"],
                "shadow_exit": sx,
                **(extra_mgmt or {}),
            },
        }
        return run(
            """
            const v = L.lockDetail(p.d, { session: true, series: p.series, onRelock: () => {} });
            const n = walk(v);
            const cf = n.find((x) => x.tagName === 'details' && (x.className || '').includes('ld-counterfactual'));
            const btns = n.filter((x) => x.tagName === 'button').map((x) => x.textContent);
            const results = n.filter((x) => (x.className || '').includes('ld-result')).map((x) => x.textContent);
            return { secs: sections(v), text: v.textContent, cfSummary: cf ? cf.childNodes[0].textContent : null, cfText: cf ? cf.textContent : null, btns, results };
            """,
            {"d": detail, "series": series},
        )

    sx_exec = {
        "valuation_tier": "EXECUTABLE_TWO_LEG_QUOTE",
        "shadow_exit_at_ms": T0 + 350_000,
        "exit_pnl_btc": -0.0018,
        "exit_pnl_usd": -184.5,
        "spot_usd": 102500.0,
        "entry": {"entry_after_fee_btc": 0.0046},
        "exit_quote": {
            "short_buyback_ask_btc": 0.0082,
            "long_sale_bid_btc": 0.0022,
            "short_buy_btc": 0.0082,
            "long_sale_btc": 0.0022,
            "close_debit_btc": 0.0060,
            "close_fee_btc": 0.0004,
            "leg_skew_ms": 420,
            "valuation_ts_ms": T0 + 349_000,
            "age_at_revoke_ms": 1000,
        },
        "quote_gap_reasons": [],
    }
    out_exec = render_tier(sx_exec)
    assert out_exec["secs"][:2] == ["AI 持仓复核与影子管理摘要", "影子退出（终态）"]
    assert "现在" not in out_exec["secs"]
    assert "AI 撤销 → 影子已退出（终态）→ 已停止主动追踪" in out_exec["text"]
    assert "影子模拟双腿退出估值（非实盘成交）" in out_exec["results"][0]
    assert "−0.0018 BTC" in out_exec["results"][0]
    assert "185 美元" in out_exec["results"][0]
    assert "两腿时差 420 ms" in out_exec["results"][0]
    assert "0.006 BTC" in out_exec["results"][0] and "0.0004 BTC" in out_exec["results"][0]
    assert out_exec["cfSummary"] == "反事实对照：若未撤销持有至到期（仅供研究，不计入本局影子退出盈亏）"
    assert "以下为撤销后假设继续持有至到期的行情与结算对照，不影响上述已冻结的影子退出状态或盈亏。" in out_exec["cfText"]
    assert "到期结算" in out_exec["cfText"] and "锁定以来浮动盈亏" in out_exec["cfText"]
    assert "以最近一点重新追踪" not in out_exec["btns"]

    sx_ind = {
        "valuation_tier": "INDICATIVE_MARK_ONLY",
        "shadow_exit_at_ms": T0 + 350_000,
        "exit_pnl_btc": None,
        "indicative_exit_pnl_btc": -0.00195,
        "indicative_exit_pnl_usd": -200.0,
        "spot_usd": 102500.0,
        "exit_quote": {"short_mark_btc": 0.0081, "long_mark_btc": 0.0019, "mark_asof_ms": T0 + 349_000},
        "quote_gap_reasons": ["long_depth_insufficient"],
    }
    out_ind = render_tier(sx_ind)
    assert "影子退出（指示性标记估值 · 非可执行报价）" in out_ind["results"][0]
    assert "−0.00195 BTC" in out_ind["results"][0]
    assert "保护腿盘口深度不足" in out_ind["results"][0]
    assert "影子模拟双腿退出估值（非实盘成交）" not in out_ind["results"][0]

    sx_unp_with_ref = {
        "valuation_tier": "UNPRICED",
        "shadow_exit_at_ms": T0 + 350_000,
        "exit_pnl_btc": None,
        "indicative_exit_pnl_btc": None,
        "quote_gap_reasons": ["short_quote_stale_at_revoke", "long_book_missing"],
        "last_indicative_reference": {
            "asof_ms": T0 + 240_000,
            "spot_usd": 102100.0,
            "float_pnl_btc": -0.0012,
            "basis": "pre_revoke_asof_series_reference_not_exit_fill",
        },
    }
    out_unp = render_tier(sx_unp_with_ref)
    assert "影子退出，退出价/收益暂不可核实（UNPRICED）" in out_unp["results"][0]
    assert "撤销时短腿报价已过期" in out_unp["results"][0]
    assert "BTC-24NOV26-104000-C" in out_unp["results"][0]
    assert "撤销前最后有效指示值（非退出成交价）：" in out_unp["text"]
    assert "102,100 USD" in out_unp["text"] and "−0.0012 BTC" in out_unp["text"]

    sx_unp_no_ref = {**sx_unp_with_ref, "last_indicative_reference": None}
    out_unp_noref = render_tier(sx_unp_no_ref)
    assert "撤销前最后有效指示值（非退出成交价）：" not in out_unp_noref["text"]

    # Manual non-revoked stopped episode still keeps relock button and top-level "现在" section
    manual_stopped = {
        **base_detail,
        "episode": {"episode_id": "lce_manual", "status": "stopped", "stop_reason": "observation_stopped_not_position_close",
                    "created_at_ms": T0, "latest_snapshot_id": "snap_latest", "latest_decision_asof_ms": T0 + 120_000},
    }
    out_manual = run(
        """
        const v = L.lockDetail(p.d, { session: true, series: p.series, onRelock: () => {} });
        return { secs: sections(v), btns: walk(v).filter((x) => x.tagName === 'button').map((x) => x.textContent) };
        """,
        {"d": manual_stopped, "series": series},
    )
    assert "现在" in out_manual["secs"] and "影子退出（终态）" not in out_manual["secs"]
    assert "以最近一点重新追踪" in out_manual["btns"]

    # Backend canonical schema fields: net_credit_after_entry_fee_btc, exchange_times_ms, debit_btc, indicative_marks
    sx_worker_exec = {
        "valuation_tier": "EXECUTABLE_TWO_LEG_QUOTE",
        "shadow_exit_at_ms": T0 + 350_000,
        "exit_pnl_btc": -0.0018,
        "spot_usd": 102500.0,
        "entry": {"net_credit_after_entry_fee_btc": 0.0046},
        "exit_quote": {
            "short_buy_btc": 0.0082,
            "long_sale_btc": 0.0022,
            "debit_btc": 0.0060,
            "close_fee_btc": 0.0004,
            "exchange_times_ms": {"short": T0 + 348_200, "long": T0 + 348_750},
            "valuation_ts_ms": T0 + 348_750,
        },
        "historical_compat": True,
    }
    out_w_exec = render_tier(sx_worker_exec)
    assert "入场扣费后信用 0.0046 BTC" in out_w_exec["results"][0]
    assert "两腿时差 550 ms" in out_w_exec["results"][0]
    assert "双腿平仓借记 0.006 BTC" in out_w_exec["results"][0]
    assert "历史兼容收敛补建终态" in out_w_exec["results"][0]

    sx_worker_ind = {
        "valuation_tier": "INDICATIVE_MARK_ONLY",
        "shadow_exit_at_ms": T0 + 350_000,
        "indicative_exit_pnl_btc": -0.00195,
        "spot_usd": 102500.0,
        "indicative_marks": {"short_mark_btc": 0.0081, "long_mark_btc": 0.0019, "asof_ms": T0 + 349_000},
        "quote_gap_reasons": ["long_depth_insufficient"],
    }
    out_w_ind = render_tier(sx_worker_ind)
    assert "短腿标记价 0.0081 BTC，保护腿标记价 0.0019 BTC" in out_w_ind["results"][0]
