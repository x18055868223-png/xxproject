"""Decision Path dp-2026-10-27.1 on the page: V items, P reservation box, the conclusion gate flag,
the lock conclusion's limit price, and older sessions unchanged."""
import copy
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_astra_workbench_v3_decision_narrative import SESSION as OLD_SESSION, run  # noqa: E402

RES = {"reservation_btc": 0.01231, "reservation_usd": 1046.4, "mark_spread_btc": 0.01171, "fee_btc": 0.0006,
       "visible_credit_btc": 0.0105, "visible_credit_usd": 892.5, "bid_gap_btc": 0.00181, "bid_gap_usd": 153.9,
       "mid_credit_btc": 0.0128, "mid_credit_usd": 1088.0, "mid_gap_btc": -0.00049, "mid_gap_usd": -41.7,
       "long_bid_zero": False, "long_spread_to_mid": 0.2, "long_book_wide": False, "reservation_reliable": True}


def item(code, kind, title):
    return {"code": code, "kind": kind, "title_cn": title, "evidence_cn": f"短腿距现价 0.80%，约 0.18 倍（{code}）",
            "why_cn": f"{title}：正常波动就会触及", "clears_cn": "改为更远的短腿后，这一项才解除", "basis": "未验证，只作否决",
            "src": "D2"}


V_START = {"verdict": {"code": "veto", "cn": "不适合做：否决 1 项", "tone": "bad"},
           "items": [item("narrow", "veto", "缓冲窄"), item("mid_short", "caution", "中间价也不够市场估价")],
           "unknown_cn": ["近端倒挂（数据不足）"],
           "lines": [{"k": "缓冲窄", "v": "短腿距现价 0.80%。…", "tone": "bad", "src": "D2"},
                     {"k": "中间价也不够市场估价", "v": "…", "tone": "warn", "src": "P"},
                     {"k": "检查项", "v": "资料 可读；近端倒挂 无法判断；缓冲 窄", "src": "V"}]}
P_START = {"verdict": {"code": "mid_covers", "cn": "挂中间价可覆盖市场估价", "tone": ""}, "reservation": RES,
           "reservation_btc": RES["reservation_btc"], "lines": [{"k": "保留价", "v": "0.0123 BTC", "src": "P"}]}
NEW = {
    "verification_id": "v27", "status": "open", "side": "put", "path_version": "dp-2026-10-27.1", "started_at_ms": 1_792_494_000_000,
    "contract": {"short_instrument": "BTC-28OCT26-78712-P", "short_strike_usd": 78712, "long_strike_usd": 76712},
    "steps": [{"id": "V", "title": "否决", "question": "有没有不适合做的理由？", "veto_summary": True},
              {"id": "R", "title": "风险", "question": "要扛多少、什么时候来？"},
              {"id": "P", "title": "价格", "question": "什么价格才值得做？", "premium": True, "conclusion_gate": True},
              {"id": "PL", "title": "预案", "question": "被触及时你会怎么做？"},
              {"id": "AU", "title": "审计", "question": "模型认同吗？", "on_demand": True, "markable": True}],
    "opened": {"V": 1}, "marks": {}, "notes": {},
    "readings": {"start": {"at_ms": 1_792_494_000_000, "steps": {
        "V": V_START,
        "R": {"verdict": {"code": "narrow", "cn": "缓冲窄 · 事件 1 个", "tone": "warn"}, "lines": [{"k": "合约", "v": "卖 put", "src": "D1"}]},
        "P": P_START,
        "PL": {"verdict": {"code": "plan", "cn": "预案登记", "tone": ""}, "lines": [{"k": "检查点", "v": "08:00", "src": "H"}]},
        "AU": {"verdict": None, "lines": [], "note": "按需触发"}}}},
}


def new(**over):
    s = copy.deepcopy(NEW)
    s.update(over)
    return s


def at_p(**over):
    return new(opened={"V": 1, "R": 2, "P": 3}, **over)


CTX = "mkctx(p.s, { onConclude: (c, x) => posts.push({ conclude: c, ...(x || {}) }), onCancel() {}, onAskLock() {}, onOpenLock() {} })"


def test_v_items_are_listed_prominently_in_both_views_with_the_checks_line():
    body = """const out = {};
      for (const mode of ['narrative', 'table']) {
        localStorage.setItem('astra.dp.view', mode);
        const box = new N('div'); D.renderDecision(box, mkctx(p.s));
        const v = step(box, 'V');
        out[mode] = { titles: texts(v, 'dp-veto-title'), items: byCls(v, 'dp-veto-item').map((x) => x.className),
          ev: texts(v, 'dp-veto-ev'), clears: texts(v, 'dp-veto-clears'), checks: texts(v, 'dp-veto-checks'),
          table: byCls(v, 'dp-lines').length, chip: texts(v, 'chip') };
      }
      return out;"""
    out = run(body, {"s": new()})
    for mode in ("narrative", "table"):
        o = out[mode]
        assert o["titles"] == ["缓冲窄", "中间价也不够市场估价"]
        assert o["items"] == ["dp-veto-item bad", "dp-veto-item warn"]
        assert o["ev"][0].startswith("短腿距现价 0.80%") and o["clears"][0].startswith("解除条件：")
        assert o["checks"] == ["检查项：资料 可读；近端倒挂 无法判断；缓冲 窄", "无法判断：近端倒挂（数据不足）"]
        assert o["table"] == 0                                  # the items replace the line table
        assert "不适合做：否决 1 项" in o["chip"]


def test_v_without_items_is_the_plain_sentence_and_a_failed_v_keeps_its_lines():
    s = new()
    s["readings"]["start"]["steps"]["V"] = {"verdict": {"code": "no_veto", "cn": "未见否决项（这不是做的理由）", "tone": ""},
                                            "items": [], "unknown_cn": [], "lines": [{"k": "检查项", "v": "资料 可读"}]}
    out = run("const box = new N('div'); D.renderDecision(box, mkctx(p.s)); const v = step(box, 'V');"
              "return { none: texts(v, 'dp-veto-none'), items: byCls(v, 'dp-veto-item').length, checks: texts(v, 'dp-veto-checks') };",
              {"s": s})
    assert out == {"none": ["未见否决项（这不是做的理由）"], "items": 0, "checks": ["检查项：资料 可读"]}
    s["readings"]["start"]["steps"]["V"] = {"verdict": {"code": "error", "cn": "读数失败", "tone": "warn"}, "items": [],
                                            "lines": [{"k": "说明", "v": "这一步读数失败"}]}
    out = run("localStorage.setItem('astra.dp.view', 'table'); const box = new N('div'); D.renderDecision(box, mkctx(p.s));"
              "const v = step(box, 'V'); return { veto: byCls(v, 'dp-veto').length, table: texts(v, 'dp-lines') };", {"s": s})
    assert out["veto"] == 0 and "这一步读数失败" in out["table"][0]


def test_p_reservation_box_and_the_conclusion_opens_at_the_gate_step():
    body = """const box = new N('div'); D.renderDecision(box, mkctx(p.s));
      const pStep = step(box, 'P');
      return { box: texts(pStep, 'dp-reserve-main'), rows: byCls(pStep, 'dp-reserve-row').map((x) => [x.className, x.textContent]),
               conclude: byCls(box, 'dp-conclude').length, gate: D.gateStepId(p.s) };"""
    out = run(body, {"s": at_p()})
    assert out["gate"] == "P" and out["conclude"] == 1
    assert out["box"] == ["保留价 0.0123 BTC（约 1,046 美元）"]
    assert out["rows"][0] == ["dp-reserve-row warn", "按买价可得 0.0105 BTC（差 约 154 美元）"]
    assert out["rows"][1] == ["dp-reserve-row", "两腿中间价 0.0128 BTC（不低于保留价，多 约 42 美元）"]
    # before P is open there is no conclusion; R open is not enough
    before = run("const box = new N('div'); D.renderDecision(box, mkctx(p.s)); return byCls(box, 'dp-conclude').length;",
                 {"s": new(opened={"V": 1, "R": 2})})
    assert before == 0
    unreliable = at_p()
    unreliable["readings"]["start"]["steps"]["P"]["reservation"] = {**RES, "mid_credit_btc": None, "mid_gap_btc": None,
                                                                    "reservation_reliable": False}
    out = run(body, {"s": unreliable})
    assert out["rows"][1][1] == "两腿中间价 不可算（缺对侧盘口）"
    assert out["rows"][2] == ["dp-reserve-row warn", "保护腿盘口空或过宽，标记价可能失真，保留价仅供参考。"]


EDGE = {"required_edge_bid": 0.18, "required_edge_mid": 0.0, "required_edge_note_cn": None,
        "max_loss_btc": 0.0112, "max_loss_usd": 952.0}
# the round block is read against the page's clock (Date.now): the fixtures sit around the real now
NOW_MS = int(time.time() * 1000)
WINDOW_AT = NOW_MS - 10 * 60_000                       # a round window opened 10 minutes ago
NEXT_AT = WINDOW_AT + 12 * 3_600_000


def bjt(ms, ref):
    """decision.js until(): HH:MM in Beijing time, with MM/DD when the day is not the reference's."""
    a, b = (datetime.fromtimestamp(x / 1000, timezone(timedelta(hours=8))) for x in (ms, ref))
    return a.strftime("%H:%M") if a.date() == b.date() else a.strftime("%m/%d %H:%M")


def round_at(current_at, nxt, current=1, **over):
    return {"rounds_utc": [2, 14], "current": current, "current_at_ms": current_at,
            "next": {"round": 2, "at_ms": nxt, "same_expiry": True, "hours_left_at": 18.0},
            "pass_cost": {"premium_btc": 0.0012, "premium_usd": 102.0, "variance_share": 0.42},
            "note_cn": "两轮按 UTC 2 点与 14 点", **over}


ROUND = round_at(WINDOW_AT, NEXT_AT)
P_ROWS = """const box = new N('div'); D.renderDecision(box, mkctx(p.s)); const pStep = step(box, 'P');
  return { rows: byCls(pStep, 'dp-reserve-row').map((x) => x.textContent), round: byCls(pStep, 'dp-round').map((x) => x.textContent) };"""


def p_with(res_over=None, round_=None, at=None):
    s = at_p()
    p = s["readings"]["start"]["steps"]["P"]
    p["reservation"] = {**RES, **(res_over or {})}
    if round_ is not None:
        p["round"] = round_
    if at is not None:
        s["readings"]["start"]["at_ms"] = at
    return s


def test_p_box_shows_required_edge_max_loss_and_the_rounds():
    out = run(P_ROWS, {"s": p_with(EDGE, ROUND, at=WINDOW_AT + 60_000)})
    rows = out["rows"]
    assert rows[2] == "所需优势：按买价 18%，按中间价 0（不低于保留价）　真实预期赔付要比市场估价低这么多，这一局才不亏（已含费用）"
    assert rows[3] == "计分单位（按现价折算的最大损失）约 0.0112 BTC（约 952 美元）"
    assert rows[4:] == ["现在是第 1 轮出手时窗",
                        f"下一轮：第 2 轮，北京时间 {bjt(NEXT_AT, WINDOW_AT + 60_000)}，届时剩余约 18 小时",
                        "等到第二轮前，这段时间约占剩余风险的 42%；保费按平方根粗算约减少 0.0012 BTC（约 102 美元）（偏低，价外价差衰减更快）"]
    assert out["round"] and out["round"][0].endswith("两轮按 UTC 2 点与 14 点")
    # outside a window, next round on the next expiry; no pass cost shown
    later = NOW_MS + 30 * 3_600_000
    other = round_at(None, later, current=None, pass_cost=None,
                     next={"round": 1, "at_ms": later, "same_expiry": False, "hours_left_at": None})
    rows = run(P_ROWS, {"s": p_with(EDGE, other, at=NOW_MS)})["rows"]
    assert rows[4:] == [f"下一轮：第 1 轮，北京时间 {bjt(later, NOW_MS)}（届时为下一期合约）"]
    assert not any("现在是第" in r or "等到第二轮" in r for r in rows)


def test_p_box_says_when_its_round_reading_is_out_of_date():
    # the reading was taken in round 1's window, which closed two hours ago: no "now", no pass cost
    closed = round_at(NOW_MS - 3 * 3_600_000, NOW_MS + 9 * 3_600_000)
    rows = run(P_ROWS, {"s": p_with(EDGE, closed, at=NOW_MS - 3 * 3_600_000 + 60_000)})["rows"]
    assert "读数时处在第 1 轮出手时窗（该时窗已结束）" in rows
    assert not any(r.startswith("现在是第") or "平方根" in r for r in rows)
    # the next round itself has passed: the block says it is out of date
    stale = round_at(NOW_MS - 14 * 3_600_000, NOW_MS - 2 * 3_600_000)
    rows = run(P_ROWS, {"s": p_with(EDGE, stale, at=NOW_MS - 14 * 3_600_000)})["rows"]
    assert rows[4] == "以下轮次按读数时刻计算，已过时；刷新“现在”的读数后再看。"


def test_p_box_degrades_without_edge_loss_or_round():
    out = run(P_ROWS, {"s": p_with({"required_edge_bid": None, "required_edge_mid": None,
                                    "required_edge_note_cn": "标记价差不大于 0", "max_loss_btc": None, "max_loss_usd": None})})
    assert out["rows"][2] == "所需优势：不可算（标记价差不大于 0）"
    assert not any(r.startswith("计分单位") for r in out["rows"]) and out["round"] == []
    out = run(P_ROWS, {"s": p_with({"required_edge_bid": None, "required_edge_mid": 0.075})})
    assert out["rows"][2].startswith("所需优势：按买价 不可算，按中间价 7.5%")
    # old readings with none of the new fields: the box is as before
    out = run(P_ROWS, {"s": at_p()})
    assert len(out["rows"]) == 2 and out["round"] == []


def test_box_keys_hide_the_new_p_lines_from_the_table():
    s = p_with(EDGE, ROUND)
    s["readings"]["start"]["steps"]["P"]["lines"] += [{"k": "所需优势", "v": "按买价 18%"}, {"k": "最大损失", "v": "0.0112 BTC"},
                                                      {"k": "轮次", "v": "第 1 轮"}, {"k": "其他", "v": "保留"}]
    out = run("localStorage.setItem('astra.dp.view', 'table'); const box = new N('div'); D.renderDecision(box, mkctx(p.s));"
              "return texts(step(box, 'P'), 'dp-lines');", {"s": s})
    assert "其他" in out[0] and not any(k in out[0] for k in ("所需优势", "最大损失", "轮次"))
    src = (Path(__file__).resolve().parents[1] / "deploy" / "astra_workbench_v3" / "decision.js").read_text(encoding="utf-8")
    assert all(f'"{k}"' in src.split("const BOX_KEYS")[1].split("\n")[0] for k in ("所需优势", "最大损失", "轮次"))
    assert "概率" not in src


def test_gate_falls_back_to_q2_for_older_sessions():
    out = run("return [D.gateStepId(p.a), D.gateStepId(p.b), D.gateStepId({ steps: [] })];",
              {"a": OLD_SESSION, "b": NEW})
    assert out == ["Q2", "P", None]


LIMIT = """const ctx = { ...%s, confirm: 'lock' };
  const box = new N('div'); D.renderDecision(box, ctx);
  const inputs = walk(box).filter((x) => x.tagName === 'INPUT');
  const price = inputs.find((x) => x.dataset.dpLimit === 'price'), minutes = inputs.find((x) => x.dataset.dpLimit === 'minutes');
  const warn = one(box, 'dp-limit-warn'), err = byCls(box, 'dp-error').find((x) => x.attrs.role === 'alert');
  const go = walk(box).find((x) => x.tagName === 'BUTTON' && x.textContent === '确认锁定');
  const type = (el, v) => { el.value = v; el.fire('input', el); };
  const state = () => ({ warn: warn.hidden ? '' : warn.textContent, err: err.hidden ? '' : err.textContent, disabled: !!go.disabled });
""" % CTX


def test_limit_inputs_prefill_round_up_and_warn_below_the_reservation():
    body = LIMIT + """const out = { price: price.value, minutes: minutes.value, start: state() };
      type(price, '0.0120'); out.below = state();
      type(price, '0.5'); out.bad = state();
      type(price, '0.0124'); type(minutes, '0'); out.badMin = state();
      return out;"""
    out = run(body, {"s": at_p()})
    assert out["price"] == "0.0124" and out["minutes"] == "60"            # 0.01231 rounded up, never below
    assert out["start"] == {"warn": "", "err": "", "disabled": False}
    assert out["below"]["warn"] == "低于保留价：即使成交，净收也低于市场估价" and out["below"]["disabled"] is False
    assert out["bad"]["err"] == "挂单价需在 0.00001 到 0.1 BTC 之间" and out["bad"]["disabled"] is True
    assert out["badMin"]["err"] == "有效时长需为 1–1440 分钟的整数"
    assert run("return [D.ceil4(0.0123), D.ceil4(0.012300000000000002), D.ceil4(0.01230001), D.ceil4(null)];", {}) \
        == [0.0123, 0.0123, 0.0124, None]


def test_limit_payload_only_with_a_valid_price_and_cleared_price_locks_without_one():
    body = LIMIT + """go.fire('click'); const first = posts.slice();
      posts.length = 0; type(price, ''); go.fire('click'); const cleared = posts.slice();
      posts.length = 0; type(price, 'abc'); go.fire('click'); const invalid = posts.slice();
      return { first, cleared, invalid };"""
    out = run(body, {"s": at_p(verification_id="v27b")})
    assert out["first"] == [{"conclude": "lock", "limit": {"credit_btc": 0.0124, "valid_minutes": 60}}]
    assert out["cleared"] == [{"conclude": "lock"}]
    assert out["invalid"] == []
    # unknown reservation: empty price, the user may still lock
    s = at_p(verification_id="v27c")
    s["readings"]["start"]["steps"]["P"]["reservation"] = {**RES, "reservation_btc": None}
    s["readings"]["start"]["steps"]["P"]["reservation_btc"] = None
    out = run(LIMIT + "return { price: price.value, ...state() };", {"s": s})
    assert out == {"price": "", "warn": "", "err": "", "disabled": False}


def test_server_error_is_shown_next_to_the_limit_inputs():
    out = run(LIMIT.replace("confirm: 'lock' }", "confirm: 'lock', error: '结论未记录：挂单价或有效时长不合法' }")
              + "return { ...state(), top: box.childNodes[0].className };", {"s": at_p(verification_id="v27d")})
    assert out["err"] == "结论未记录：挂单价或有效时长不合法" and out["top"] != "dp-error"


def test_closed_view_shows_the_limit_with_its_reservation():
    s = at_p(status="closed", conclusion={"choice": "lock", "at_ms": 1_792_494_000_000, "episode_id": "e1",
                                          "limit": {"credit_btc": 0.0124, "valid_until_ms": 1_792_497_600_000,
                                                    "reservation_btc": 0.01231, "set_at_ms": 1_792_494_000_000}})
    out = run("const box = new N('div'); D.renderDecision(box, " + CTX + "); return texts(box, 'dp-limit-line');", {"s": s})
    assert out == ["挂单价 0.0124 BTC，有效至 20:00（已过期）（保留价 0.0123 BTC）"]


def test_an_old_session_renders_and_locks_as_before():
    body = """const box = new N('div'); D.renderDecision(box, { ...%s, confirm: 'lock' });
      const inputs = walk(box).filter((x) => x.tagName === 'INPUT' && x.dataset.dpLimit);
      const go = walk(box).find((x) => x.tagName === 'BUTTON' && x.textContent === '确认锁定');
      go.fire('click');
      return { inputs: inputs.length, reserve: byCls(box, 'dp-reserve').length, veto: byCls(box, 'dp-veto').length,
               posts, conclude: byCls(box, 'dp-conclude').length,
               confirm: texts(box, 'confirm') };""" % CTX
    out = run(body, {"s": OLD_SESSION})
    assert out["inputs"] == 0 and out["reserve"] == 0 and out["veto"] == 0 and out["conclude"] == 1
    assert out["posts"] == [{"conclude": "lock"}]
    assert out["confirm"] == ["以现在的报价为锁定起点，每分钟采样同一合同直到到期；锁定记录附带本次核验。页面不下单。"]


def test_v_narrative_keeps_the_items_and_adds_only_its_question():
    s = new()
    s["readings"]["start"]["narrative"] = {"schema": "dp_narrative@0.1", "steps": {"V": {
        "lead": "不适合做：否决 1 项。", "sentences": [{"text": "缓冲窄：短腿距现价 0.80%。", "fn": [], "tone": "bad"}], "footnotes": [],
        "ask": {"question": "这些否决项，你怎么处理？",
                "options": [{"key": "stop", "label": "按否决，不做", "lean": "toward_decline", "needs_note": False},
                            {"key": "look", "label": "仍想看价格（写下理由）", "lean": "unchanged", "needs_note": True}]},
        "evidence": [], "covered": [0, 1, 2]}}}
    body = """localStorage.setItem('astra.dp.view', 'narrative');
      const box = new N('div'); D.renderDecision(box, mkctx(p.s)); const v = step(box, 'V');
      const look = walk(v).find((x) => x.dataset.opt === 'look'); look.fire('click');
      const v2 = step(box, 'V');
      return { titles: texts(v2, 'dp-veto-title'), para: byCls(v2, 'dp-para').length, q: texts(v2, 'dp-ask-q'),
               opts: texts(v2, 'dp-opt'), posts, editor: walk(v2).some((x) => x.tagName === 'TEXTAREA') };"""
    out = run(body, {"s": s})
    assert out["titles"] == ["缓冲窄", "中间价也不够市场估价"] and out["para"] == 0
    assert out["q"] == ["这些否决项，你怎么处理？"] and out["opts"] == ["按否决，不做", "仍想看价格（写下理由）"]
    assert out["posts"] == [] and out["editor"] is True          # needs a note: held until the reason is saved


def test_inquirer_text_names_the_new_steps_without_touching_pnl():
    import json, shutil, subprocess
    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node is not available")
    uri = (Path(__file__).resolve().parents[1] / "deploy" / "astra_workbench_v3" / "inquiry_terms.js").as_uri()
    script = (f'import {{ cnText }} from "{uri}";\n'
              'console.log(JSON.stringify(["PL 选了持有到期，P 步低于保留价", "P&L 为负，R:R 不佳"].map((t) => cnText(t, {}))));')
    res = subprocess.run([node, "--input-type=module", "-e", script], text=True, encoding="utf-8", capture_output=True,
                         stdin=subprocess.DEVNULL, check=False)
    assert res.returncode == 0, res.stderr
    a, b = json.loads(res.stdout)
    assert a.startswith("PL 预案 选了持有到期，P 价格步") and b.startswith("P&L") and "R 风险" not in b
