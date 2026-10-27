"""v3 fact layer without verdicts and the card's four rows from candidate_readings@0.1 (docs/opus/23)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "deploy" / "astra_workbench_v3"

# A minimal DOM for h() / s(): elements keep attributes, classes and text so a test can read them.
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
const classes = (n) => walk(n).map((x) => x.className || x.attrs.class || "").join(" ");
const fills = (n) => walk(n).map((x) => x.attrs.fill).filter(Boolean);
"""


def run(body, payload=None):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    script = (SHIM + 'import fs from "node:fs";\n'
              'const p = JSON.parse(fs.readFileSync(0, "utf8"));\n'
              + "".join(f'const {name} = await import("{(PAGE / f).as_uri()}");\n'
                        for name, f in (("F", "factors.js"), ("M", "model.js"), ("S", "session.js"), ("L", "locks.js")))
              + f"const out = await (async () => {{ {body} }})();\nconsole.log(JSON.stringify(out));")
    result = subprocess.run([node, "--input-type=module", "-e", script], input=json.dumps(payload or {}),
                            text=True, encoding="utf-8", capture_output=True, check=False, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


READINGS = {
    "schema": "candidate_readings@0.1", "at_ms": 1_792_494_000_000,
    "expected_move": {"iv_pct": 35.6, "iv_source": "deribit_mark_own_expiry", "iv_source_cn": "本期 Deribit 标记 ATM IV",
                      "hours": 16.0, "spot_usd": 79_700.0, "move_pct": 1.97, "move_usd": 1570.0, "short_strike_usd": 78_712.0,
                      "short_distance_pct": 1.24, "short_distance_usd": 988.0, "distance_in_moves": 0.63, "band": "narrow",
                      "band_cn": "窄", "breakeven_usd": 78_050.0, "breakeven_distance_pct": 0.82, "breakeven_in_moves": 0.5},
    "pricing": {"credit_btc": 0.002, "credit_usd": 160.0, "fee_btc": 0.00036, "fee_usd": 29.0, "fee_assumed": True,
                "fee_basis_cn": "公开 taker 费率（有上限）", "net_credit_btc": 0.00164, "net_credit_usd": 131.0,
                "mark_spread_btc": 0.0024, "mark_spread_usd": 192.0, "edge_usd": -61.0, "spread_cost_usd": -32.0,
                "fee_cost_usd": -29.0, "short_mark_iv": 38.0, "long_mark_iv": 47.0, "skew_vol_pts": 9.0, "skew_cost_usd": 18.0,
                "width_usd": 2000.0, "loss_at_protection_usd": 1943.0, "worst_to_net_ratio": 14.83,
                "tail_cn": "跌破保护腿后按 BTC 计赔付仍增加（币本位）"},
    "clock_price": {"iv_clock_adjusted": 41.2, "ref_cycle_start": 38.5, "ref_at_ms": 1_792_483_380_000,
                    "rv24_pct": 30.0, "rv24_clock_adjusted": 33.1, "basis_cn": "机制 B；当期读数未检验，只作描述",
                    "meaning_cn": "按剩余时段折算后的隐含波动"},
}
SIDE = {"readings": READINGS, "delta": -0.24, "breakevenState": "ok"}


def test_deck_rows_state_buffer_compensation_worst_and_clock_price():
    rows = run("return M.deckRows(p.m);", {"m": SIDE})
    by = {r["key"]: r for r in rows}
    assert [r["label"] for r in rows] == ["缓冲", "补偿", "最坏", "单位风险价格"]
    assert by["buffer"]["big"] == "0.63 倍预期波动" and by["buffer"]["chip"] == "窄" and by["buffer"]["chipCls"] == "warn"
    assert by["buffer"]["subs"][0]["text"] == ("短腿 1.24% · 988 美元 · Delta 0.24；盈亏平衡 0.82%（0.5 倍）；"
                                               "σ 来源：本期 Deribit 标记 ATM IV 35.6%")
    comp = by["compensation"]
    assert comp["big"] == "净信用 131 美元"
    # the identity: market gap = spread (recoverable in part) + fee (fixed); the skew is separate
    assert comp["subs"][0]["text"] == ("比市场估价 −61 美元 = 买卖价差 −32（挂单接近中间价可部分收回）+ 费用 −29"
                                       "（费用按公开上限假设）")
    assert comp["subs"][1]["text"] == "市场估价已含偏斜：保护腿 mark IV 比短腿高 9.0 点，约 18 美元（结构性，执行收不回，不在上面的差额里）"
    assert by["worst"]["big"] == "保护腿处亏 1,943 美元"
    assert by["worst"]["subs"][0]["text"] == "约为净信用的 14.8 倍；跌破保护腿后按 BTC 计赔付仍增加（币本位）"
    clock = by["clock"]
    assert clock["big"] == "时钟校正 IV 41.2%"
    assert clock["subs"][0]["text"] == "本周期 08:00 UTC 为 38.5%；近 24 小时实现（同口径）33.1%"
    assert clock["subs"][1] == {"text": "机制 B；当期读数未检验，只作描述", "muted": True}


def test_deck_rows_fall_back_and_never_flip_numbers_without_a_fee():
    assert run("return M.deckRows(p.m);", {"m": {"delta": -0.2}}) is None          # old cached view: old figures
    pricing = {"credit_usd": 160.0, "fee_btc": None, "fee_usd": None, "net_credit_usd": None, "edge_usd": None}
    rows = run("return M.deckRows(p.m);", {"m": {"readings": {"pricing": pricing}}})
    assert [r["key"] for r in rows] == ["compensation"]
    assert rows[0]["big"] == "费用未知" and rows[0]["subs"][0]["text"].startswith("费用未知，补偿不计算")
    standard = dict(READINGS["expected_move"], band="standard", band_cn="标准附近")
    buffer = run("return M.deckRows(p.m)[0];", {"m": {"readings": {"expected_move": standard}}})
    assert buffer["chipCls"] == "" and "Delta" not in buffer["subs"][0]["text"]


def test_side_model_and_map_take_sigma_and_its_source_from_the_readings():
    snap = {"contract": {"short_strike_usd": 78_712.0, "long_strike_usd": 76_712.0, "expiry_ms": 1_792_569_600_000,
                         "short_instrument": "BTC-27OCT26-78712-P"},
            "market": {"reference_price_usd": 79_700.0, "valuation_ts_ms": 1_792_512_000_000},
            "liability": {"breakeven_usd": [78_050.0]}, "economics": {}}
    gex = {"term_structure": [{"expiry": "27OCT26", "atm_iv": 37.4}]}
    view = {"market_context": {"current_price_usd": 79_700.0, "market_facts": {"space_context": {"gex_info": gex}}}}
    body = ("const m = M.sideModel(p.v, p.c); const mm = M.mapModel(p.v, { put: m });"
            "return { sigma: m.sigmaPct, be: m.beMovePct, src: mm.sigmaSource, map: mm.sigmaPct };")
    out = run(body, {"v": view, "c": {"side": "put", "snapshot": snap, "readings": READINGS}})
    assert out == {"sigma": 1.97, "be": 0.5, "src": "本期 Deribit 标记 ATM IV 35.6%", "map": 1.97}
    old = run(body, {"v": view, "c": {"side": "put", "snapshot": snap}})                 # no readings: as before
    assert round(old["sigma"], 4) == round(37.4 * (16 / 8760) ** 0.5, 4) and old["src"] == "27OCT26 ATM IV 37.4%"


def view_for_factors():
    sp = {"state": "favorable", "state_cn": "相对有利", "reason_cn": "从现价下跌到短腿 78,712，先遇到周度 正 Gamma 墙 79,000（缓冲）。",
          "short_strike_usd": 78_712.0, "distance_pct": 1.24, "distance_in_moves": 0.663,
          "gamma": {"fresh": True, "flip_usd": 80_035.0, "regime_at_short": "negative",
                    "events": [{"price": 79_000.0, "kind": "buffer", "label_cn": "周度 正 Gamma 墙 79,000"}]},
          "kpf": {"description_cn": "通往短腿的路上没有成交密集区（成交稀疏），短腿也不在密集区内。"}}
    call = {"state": "unfavorable", "reason_cn": "现价到短腿之间没有 Gamma 结构；短腿位于 Flip 80,035 上方的正 Gamma 区（对冲逆势，倾向钉住）。",
            "short_strike_usd": 81_000.0, "distance_pct": 1.6, "distance_in_moves": 0.9}   # an old record: no gamma block
    macro = {"macro_regime": "Mild Tailwind", "macro_score": -0.35, "data_status": "full_live",
             "component_summaries": [{"key": "DXY", "current_close": 99.1, "change_pct_3d": 0.6}]}
    mf = {"gamma_regime": {"regime": "POSITIVE_GAMMA_PINNING"}, "macro_pressure": macro,
          "volume_price": {"tmvf_24h": {"final_state": "bearish_bias", "data_ready": True, "tmv_final": -0.4},
                           "tmvf_48h": {"final_state": "strong_bearish_bias", "data_ready": True, "tmv_final": -0.7}},
          "m_die": {"score": 0.4, "direction": "DOWN", "components": {"path_efficiency": {"raw": {"efficiency": 0.2}}}},
          "space_context": {"gex_info": {"iv_percentile": 12, "dvol": 40, "atm_iv": 36.0}}}
    return {"market_context": {"market_facts": mf}, "space_gate": {"state": "x", "anchor": {"score": 55.0}},
            "candidates": [{"side": "put", "spatial_position": sp, "readings": {"expected_move": READINGS["expected_move"]}},
                           {"side": "call", "spatial_position": call}]}


def test_fact_layer_states_readings_without_verdicts_or_good_bad_colours():
    body = """
      const fs5 = F.coreFactors(p.v, 79700);
      const by = Object.fromEntries(fs5.map((f) => [f.key, f]));
      const pos = by.position;
      const vizOf = (f) => f.rows.filter((r) => r.viz).map((r) => fills(r.viz)).flat();
      const bg = F.backgroundItems(p.v, 79700);
      const opt = bg.find((x) => x.title === "期权情绪");
      return {
        posText: JSON.stringify({ h: pos.headline, l: pos.lines.map(({ name, way, band, moves, kpf }) => ({ name, way, band, moves, kpf })), n: pos.note }),
        posCls: pos.lines.map((l) => l.cls || "").join(""),
        gamma: by.space.rows.find((r) => r.label === "Gamma 体制").value,
        path: by.path.headline, pe: by.path.rows.find((r) => r.label === "路径效率").value, peFills: fills(by.path.rows.find((r) => r.label === "路径效率").viz),
        macro: by.macro.headline, macroNote: by.macro.note, macroFills: vizOf(by.macro),
        iv: opt.rows.find((r) => r.label === "IV 分位"),
        put: pos.lines.find((l) => l.side === "put"), call: pos.lines.find((l) => l.side === "call"),
      };"""
    out = run(body, {"v": view_for_factors()})
    for word in ("有利", "不利", "（缓冲）", "（加速）", "钉住"):
        assert word not in out["posText"], word
    assert out["posCls"] == ""
    assert out["put"]["moves"] == 0.63 and out["put"]["band"] == "窄"                  # readings win over the old 0.663
    assert out["put"]["way"] == "从现价下跌到短腿 78,712，路上先后是：周度 正 Gamma 墙 79,000。"
    assert out["call"]["way"] == "现价到短腿之间没有 Gamma 结构；短腿位于 Flip 80,035 上方的正 Gamma 区。"
    assert out["gamma"] == "正 Gamma（信号层口径，不区分做市商方向）"
    assert out["path"] == "TMV 24h 偏空 · 48h 强偏空（同源，不计票）"
    assert out["pe"] == "20%" and "var(--warn)" not in out["peFills"] and "var(--ok)" not in out["peFills"]
    assert out["macro"] == "综合得分 −0.35" and "顺风" not in out["macro"] and "绿色" not in out["macroNote"]
    assert not {"var(--ok)", "var(--liability)"} & set(out["macroFills"])
    assert out["iv"]["viz"] is None and out["iv"]["value"] == "12，DVOL 40"


def test_factor_and_lock_sources_carry_no_verdict_words():
    factors = (PAGE / "factors.js").read_text(encoding="utf-8")
    for word in ("有利", "不利", "顺风", "逆风", "钉住", "{ good: -1 }", "threshold: 0.35"):
        assert word not in factors, word
    locks = (PAGE / "locks.js").read_text(encoding="utf-8")
    assert "对本单有利" not in locks and "温和顺风" not in locks and "钉住" not in locks


def test_strip_shows_the_time_lamp_not_the_anchor():
    clock = {"phase": {"code": "pre_us", "label_cn": "美股开盘前"}, "hour_level": "normal",
             "expiry_ms": 1_792_569_600_000, "hours_to_expiry": 20.0, "remaining_time_share": 0.83,
             "remaining_variance_share": 0.72, "calendar_gaps": [],
             "windows": [{"kind": "us_open", "label_cn": "美股开盘", "at_ms": 1_792_501_560_000}]}
    now = 1_792_494_000_000
    lamp = run("return S.timeLamp(p.v, p.now);", {"v": {"session_clock": clock}, "now": now})
    assert lamp["text"] == "剩余风险 72% · 美股开盘 2.1 小时后" and lamp["level"] == "time"
    going = run("return S.timeLamp(p.v, p.now).text;", {"v": {"session_clock": clock}, "now": 1_792_501_600_000})
    assert going.endswith("· 美股开盘 进行中")
    assert run("return S.timeLamp(p.v, 0).level;", {"v": {}}) == "off"
    app = (PAGE / "app.js").read_text(encoding="utf-8")
    html = (PAGE / "index.html").read_text(encoding="utf-8")
    assert 'id="gate-state"' not in html and 'id="time-state"' in html
    assert "gate-state" not in app and "锚点 ${anchorScore" not in app and "timeLamp(v)" in app


def test_time_strip_marks_the_hour_of_each_high_variance_window():
    clock = {"expiry_ms": 1_792_569_600_000, "hours_to_expiry": 20.0, "now_hour_utc": 12, "hour_profile": {},
             "windows": [{"kind": "us_open", "label_cn": "美股开盘", "at_ms": 1_792_501_560_000},   
                         {"kind": "macro", "label_cn": "CPI", "at_ms": 1_792_499_400_000, "tentative": True}]}
    cells = run("return S.cycleCells(p.c, 0);", {"c": clock})
    marked = {c["hourUtc"]: [w["label"] for w in c["windows"]] for c in cells if c["windows"]}
    start = 1_792_569_600_000 - 24 * 3_600_000
    hour = lambda ms: (8 + (ms - start) // 3_600_000) % 24
    assert marked == {hour(1_792_501_560_000): ["美股开盘"], hour(1_792_499_400_000): ["CPI（日期待确认）"]}


def test_lock_now_line_reads_the_conditional_remaining_risk():
    untouched = {"applicable": True, "touched": False, "hours_left": 15.0,
                 "survival": {"hour_utc": 20, "later_touch_pct": 19, "beyond_pct": 9}, "basis_cn": "16 号 §3.3"}
    out = run("return L.pathNowModel(p.x);", {"x": untouched})
    assert out == {"text": "若到 20:00 UTC（北京 04:00）仍未触及：之后触及约 19%，到期越过约 9%", "muted": False, "basis": "16 号 §3.3"}
    touched = {"applicable": True, "touched": True, "touch_cn": "约一半到期越过短腿、约四分之一走到保护腿", "basis_cn": "b"}
    assert run("return L.pathNowModel(p.x).text;", {"x": touched}) == "已触及短腿：约一半到期越过短腿、约四分之一走到保护腿"
    off = run("return L.pathNowModel(p.x);", {"x": {"applicable": False, "why_cn": "只对 08:00 交割、剩余 ≤ 24.5 小时的合约给数字"}})
    assert off["muted"] is True and off["text"].startswith("只对 08:00 交割")
    late = run("return L.pathNowModel(p.x).text;", {"x": {"applicable": True, "touched": False, "survival": None,
                                                          "why_cn": "已过最后一个统计时点（02:00 UTC）"}})
    assert late == "尚未触及短腿；已过最后一个统计时点（02:00 UTC）"
    assert run("return L.pathNowModel(null);") is None


def thesis(i, state="缓冲充足", role=None, claim="c"):
    t = {"id": f"L{i}", "label_cn": f"依据{i}", "state_now": "HOLDS", "state_now_cn": state, "claim_cn": claim, "tone": "ok", "rule_cn": "r"}
    if role:
        t["role"] = role
    return t


def test_thesis_split_keeps_only_the_basis_in_table_and_headline():
    legacy = {"status": "OK", "theses": [thesis(i) for i in range(1, 7)],
              "projection_cn": {"headline_cn": "依据4 转为负 Gamma，旧标题", "where_cn": "w"}}
    legacy["theses"][3]["claim_cn"] = "锁定时已不利：负 Gamma"
    out = run("return L.splitTheses(p.l);", {"l": legacy})
    assert [t["id"] for t in out["basis"]] == ["L1", "L2", "L3"] and [t["id"] for t in out["background"]] == ["L4", "L5", "L6"]
    assert out["headline"] == "依据1：缓冲充足；依据2：缓冲充足；依据3：缓冲充足。"            # rebuilt, no background lane
    assert out["backgroundCn"][0] == "依据4：锁定时负 Gamma → 现在缓冲充足" and len(out["backgroundCn"]) == 3
    new = {"status": "OK", "theses": [thesis(i, role="basis" if i <= 3 else "background") for i in range(1, 7)],
           "projection_cn": {"headline_cn": "新标题", "background_cn": ["Gamma 体制：正 Gamma → 现在过渡（背景，不作依据）"]}}
    out = run("return L.splitTheses(p.l);", {"l": new})
    assert out["headline"] == "新标题" and out["backgroundCn"] == ["Gamma 体制：正 Gamma → 现在过渡（背景，不作依据）"]
    quiet = dict(new, projection_cn={"headline_cn": "新标题", "background_cn": []})
    assert run("return L.splitTheses(p.l).backgroundCn;", {"l": quiet}) == ["锁定以来背景读数没有变化"]
    rendered = run("""const box = L.ledgerView(p.l, null);
      const rows = walk(box).filter((x) => x.tagName === 'tr').length - 1;
      const sums = walk(box).filter((x) => x.tagName === 'summary').map((x) => x.textContent);
      const det = walk(box).find((x) => x.tagName === 'details' && x.className.includes('ledger-bg'));
      return { rows, sums, open: det ? det.attrs.open ?? null : 'none', head: walk(box).find((x) => x.className === 'ledger-head').textContent };""",
                   {"l": legacy})
    assert rendered["rows"] == 3 and rendered["sums"] == ["背景变化（不作依据）", "判定规则"]
    assert rendered["open"] is None and "旧标题" not in rendered["head"]
