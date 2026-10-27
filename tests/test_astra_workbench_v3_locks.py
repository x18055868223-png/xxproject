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
