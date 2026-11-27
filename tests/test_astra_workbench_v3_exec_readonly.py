"""Workbench v3 live execution band: read-only observer, honest states, simulated fixture kept out of deploy/."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FRONT = ROOT / "deploy" / "astra_workbench_v3"
EXEC = (FRONT / "execution.js").as_uri()
FIXTURE = ROOT / "tests" / "fixtures" / "workbench_v3_exec_states.json"


def run(expr, payload):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    script = (f'import * as E from "{EXEC}";\n'
              'import fs from "node:fs";\n'
              'const p = JSON.parse(fs.readFileSync(0, "utf8"));\n'
              f'console.log(JSON.stringify({expr}));')
    result = subprocess.run([node, "--input-type=module", "-e", script], input=json.dumps(payload),
                            text=True, encoding="utf-8", capture_output=True, check=False, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def states():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["states"]


def model(status, **opts):
    return run("(() => { const m = E.execModel(p.s, p.o); return {level: m.level, stage: m.stage, strip: m.stripText,"
               " action: m.action, banners: m.banners, track: m.track, last: m.last, mode: m.modeInfo}; })()",
               {"s": status, "o": opts})


def test_page_has_no_execution_write_surface():
    exec_src = (FRONT / "execution.js").read_text(encoding="utf-8")
    api_src = (FRONT / "api.js").read_text(encoding="utf-8")
    app_src = (FRONT / "app.js").read_text(encoding="utf-8")
    html = (FRONT / "index.html").read_text(encoding="utf-8")
    for token in ("fetch(", "method:", "idempotency", "confirm_code", "/approve", "halt-entry", "exit-all",
                  "disable-writes", "capital-policy", "password", "execPost", "execLogin", "<input", '"input"'):
        assert token not in exec_src, token
    assert re.findall(r"exec\w*:\s*\(", api_src) == ["execLive: ("]
    assert "/execution/session" not in api_src and "/execution/${" not in api_src
    for token in ("execPost", "execLogin", "execLogout", "execSession", "runExecAction", "pending_approval"):
        assert token not in app_src, token
    for token in ("开仓批准", "风控台", "手动风控", "批准"):
        assert token not in html, token
    # the only buttons in the band switch simulated scenarios
    assert exec_src.count('type: "button"') == 1 and "onPickFixture" in exec_src


def test_simulated_fixture_is_local_only_and_labelled():
    assert not (FRONT / "fixtures" / "exec_states.json").exists()
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert raw["simulated"] is True and "模拟" in raw["note"]
    assert {"idle", "entry", "tp", "revoke", "unknown", "cash", "takeover", "stale", "expiry", "settled"} <= set(raw["states"])
    app_src = (FRONT / "app.js").read_text(encoding="utf-8")
    assert "raw.simulated !== true" in app_src  # a non-simulated file is never shown as a scenario
    m = model(raw["states"]["tp"], fixture=True)
    assert m["strip"].startswith("模拟") and m["mode"]["tone"] == "sim"


def test_lifecycle_reads_and_levels():
    s = states()
    expect = {
        "idle": ("idle", "off"), "entry": ("entry", "ok"), "tp": ("hold", "ok"), "revoke": ("exit", "warn"),
        "residual": ("residual", "ok"), "unknown": ("entry", "bad"), "cash": ("exit", "bad"),
        "takeover": ("takeover", "bad"), "stale": ("hold", "bad"), "expiry": ("expired", "ok"),
        "expiry_stage2": ("expired_stage2", "warn"), "settled": ("idle", "off"),
    }
    for key, (stage, level) in expect.items():
        m = model(s[key], fixture=True)
        assert (m["stage"]["key"], m["level"]) == (stage, level), key
    assert "结果未知" in model(s["unknown"], fixture=True)["strip"]
    assert model(s["settled"], fixture=True)["last"]["terminal_outcome"] == "EXPIRY_SETTLED"


def test_stop_unknown_and_expiry_never_read_as_settled():
    s = states()
    for key in ("takeover", "unknown", "expiry", "stale", "cash"):
        m = model(s[key], fixture=True)
        assert m["stage"]["key"] != "settled" and m["track"][-1]["done"] is False, key
    # phase alone cannot claim settlement; only cashflow_settled === true does
    ep = dict(s["tp"]["active_episode"], phase="CASHFLOW_SETTLED", cashflow_settled=None)
    assert model(dict(s["tp"], active_episode=ep), fixture=True)["stage"]["key"] != "settled"
    ep = dict(s["tp"]["active_episode"], cashflow_settled=True, terminal_outcome="EARLY_FLAT_SETTLED",
              short_pos_btc="0", prot_pos_btc="0", short_liability_cleared=True, inventory_cleared=True)
    assert model(dict(s["tp"], active_episode=ep), fixture=True)["stage"]["text"] == "提前平仓结清"
    # hours past expiry without settlement is flagged (even when OTM inventory_cleared=true)
    late = dict(s["expiry"], generated_at_ms=s["expiry"]["generated_at_ms"] + 3 * 3_600_000)
    m_late = model(late, fixture=True)
    assert m_late["stage"]["key"] == "expired" and m_late["level"] == "warn"
    assert m_late["stage"]["text"] == "已到期 · 交割久未落账"


def test_stage2_expiry_unknown_pending_alerts_tp_terminal_and_idle_45s():
    s = states()
    # 1. ITM post-expiry Stage-2 future must never read as cleared/settled
    m_s2 = model(s["expiry_stage2"], fixture=True)
    assert m_s2["stage"]["key"] == "expired_stage2" and m_s2["level"] == "warn"
    assert "交割期货" in m_s2["stage"]["text"]
    for bad_tok in ("两腿已清", "仓位已归零", "结清"):
        assert bad_tok not in m_s2["stage"]["text"] and bad_tok not in m_s2["action"]["text"]
    rendered_s2 = render({"s": s["expiry_stage2"]}, "(() => { const b = document.createElement('div'); E.renderExecution(b, {status: p.s}, {fixture: true}); return b.textContent; })()")
    assert "交割期货未结" in rendered_s2 and "两腿已清" not in rendered_s2 and "仓位已归零" not in rendered_s2

    # 2. Mid-entry EP SUBMIT_UNKNOWN + exit_latched=true (short=0, prot=0, short_liability_cleared=true)
    ep_unk = dict(
        s["entry"]["active_episode"],
        exit_latched=True, exit_latch_reason="AI_REVOKE", exit_started_at_ms=1795615150000,
        short_pos_btc="0", prot_pos_btc="0", short_liability_cleared=True, inventory_cleared=False,
        open_or_unknown_commands=[{
            "cmd_id": "cmd_ep_unk", "purpose": "EP", "status": "SUBMIT_UNKNOWN",
            "instrument_name": "BTC-26NOV26-57000-P", "direction": "buy", "amount": "0.1", "filled_amount": "0", "limit_price": "0.0015",
        }],
    )
    m_unk = model(dict(s["entry"], active_episode=ep_unk), fixture=True)
    assert m_unk["stage"]["key"] == "unknown_pending" and m_unk["level"] == "bad"
    assert m_unk["track"][2]["done"] is False and m_unk["track"][3]["done"] is False
    for bad_tok in ("两腿已清", "仓位已归零"):
        assert bad_tok not in m_unk["stage"]["text"] and bad_tok not in m_unk["action"]["text"]

    # 3. EXIT_PRICE_BELOW_EXCHANGE_MIN_BAND & QUOTE_UNAVAILABLE_AT_REVOKE alerts
    ep_band = dict(s["revoke"]["active_episode"], alerts=["EXIT_PRICE_BELOW_EXCHANGE_MIN_BAND", "QUOTE_UNAVAILABLE_AT_REVOKE"])
    m_band = model(dict(s["revoke"], active_episode=ep_band), fixture=True)
    titles = [b["title"] for b in m_band["banners"]]
    assert "退出限价低于价格带" in titles and "退出时缺有效报价" in titles
    assert any(b["tone"] == "warn" and b["title"] == "退出限价低于价格带" for b in m_band["banners"])

    # 4. Terminal tp.order_status with TP_ACTIVE must not show 止盈单挂单中
    ep_tp_term = dict(s["tp"]["active_episode"], tp=dict(s["tp"]["active_episode"]["tp"], substate="TP_ACTIVE", order_status="CANCELLED"))
    m_tp_term = model(dict(s["tp"], active_episode=ep_tp_term), fixture=True)
    assert "止盈单挂单中" not in m_tp_term["action"]["text"] and "止盈单未在挂" in m_tp_term["action"]["text"]

    # 5. Idle 45s freshness: 25s stays fresh (off), 50s flags 执行状态过期
    idle_25 = dict(s["idle"], stale_after_ms=45000, status_age_ms=25000, stale=False, generated_at_ms=0)
    m_25 = model(idle_25, fixture=False, nowMs=100_000, fetchedAtMs=100_000)
    assert m_25["level"] == "off" and not any("过期" in b["title"] for b in m_25["banners"])
    idle_50 = dict(s["idle"], stale_after_ms=45000, status_age_ms=50000, stale=True, generated_at_ms=0)
    m_50 = model(idle_50, fixture=False, nowMs=100_000, fetchedAtMs=100_000)
    assert any(b["title"] == "执行状态过期" for b in m_50["banners"])

    # 6. Trader-facing subtraction: shadow 0/0 budget hidden, entry_ep_only 0 credit hidden, valuation_tier translated
    rendered_idle = render({"s": s["idle"]}, "(() => { const b = document.createElement('div'); E.renderExecution(b, {status: p.s}, {fixture: true}); return b.textContent; })()")
    assert "风险预算" not in rendered_idle
    rendered_entry = render({"s": s["entry"]}, "(() => { const b = document.createElement('div'); E.renderExecution(b, {status: p.s}, {fixture: true}); return b.textContent; })()")
    assert "配对净信用" not in rendered_entry
    rendered_tp = render({"s": s["tp"]}, "(() => { const b = document.createElement('div'); E.renderExecution(b, {status: p.s}, {fixture: true}); return b.textContent; })()")
    assert "仅标记价参考" in rendered_tp


def test_missing_reads_unknown_not_zero_and_stale_is_flagged():
    assert run("[E.fmtDec(null), E.fmtDec(''), E.fmtDec('0.00010'), E.fmtDec('-0.1', 'BTC')]", {}) == \
        ["未知", "未知", "0.0001", "−0.1 BTC"]
    s = states()["tp"]
    real = dict(s, generated_at_ms=1_000, facts_age_ms=None, status_age_ms=None)
    m = model(real, fixture=False, nowMs=60_000)
    assert m["level"] == "bad" and any(b["title"] == "执行状态过期" for b in m["banners"])
    assert model(dict(s, mode="LIVE"))["mode"]["tone"] == "real"
    assert model(dict(s, mode="SHADOW_ONLY"))["mode"]["tone"] == "shadow"
    assert model(dict(s, mode="SOMETHING_NEW"))["mode"]["tone"] == "unknown"


def test_strip_hides_when_not_connected_and_reports_read_errors():
    assert run("E.execStripModel(null, {status: 404})", {})["hidden"] is True
    out = run("E.execStripModel(null, {status: 502})", {})
    assert out["hidden"] is False and out["level"] == "bad"
    assert run("E.execNeedsFastPoll(p.s)", {"s": states()["tp"]}) is True
    assert run("E.execNeedsFastPoll(p.s)", {"s": states()["settled"]}) is False


def test_legacy_approval_fields_are_ignored():
    legacy = dict(states()["idle"], pending_approval_candidates=[{"hand_id": "h", "confirm_code": "CONF-1"}])
    m = model(legacy, fixture=False, nowMs=legacy["generated_at_ms"])
    assert "批准" not in json.dumps(m, ensure_ascii=False) and "CONF-1" not in json.dumps(m)


# A minimal DOM, enough for format.h / clear and the band renderer.
FAKE_DOM = r"""
class Node { constructor() { this.childNodes = []; this.parentNode = null; } get firstChild() { return this.childNodes[0] || null; }
  append(...xs) { for (const x of xs) { const n = x instanceof Node ? x : new Text(String(x)); n.parentNode = this; this.childNodes.push(n); } }
  removeChild(n) { this.childNodes = this.childNodes.filter((c) => c !== n); return n; }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(""); } }
class Text extends Node { constructor(t) { super(); this.t = t; } get textContent() { return this.t; } }
class El extends Node { constructor(tag) { super(); this.tagName = tag.toUpperCase(); this.attrs = {}; this.dataset = {}; this.className = "";
    const self = this; this.classList = { toggle(c, on) { const s = new Set(self.className.split(" ").filter(Boolean)); on ? s.add(c) : s.delete(c); self.className = [...s].join(" "); } }; }
  set textContent(v) { this.childNodes = [new Text(String(v))]; } get textContent() { return super.textContent; }
  setAttribute(k, v) { this.attrs[k] = String(v); } addEventListener() {}
  all() { return [this, ...this.childNodes.flatMap((c) => (c instanceof El ? c.all() : []))]; } }
globalThis.Node = Node;
globalThis.document = { createElement: (t) => new El(t), createTextNode: (t) => new Text(t) };
"""


def render(payload, expr):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    script = (FAKE_DOM + f'const E = await import("{EXEC}");\n'
              'import("node:fs").then(async ({default: fs}) => {\n'
              'const p = JSON.parse(fs.readFileSync(0, "utf8"));\n'
              f'console.log(JSON.stringify({expr})); }});')
    result = subprocess.run([node, "--input-type=module", "-e", script], input=json.dumps(payload),
                            text=True, encoding="utf-8", capture_output=True, check=False, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_every_scenario_renders_the_four_rows_without_placeholders():
    out = render({"s": states()}, "Object.fromEntries(Object.entries(p.s).map(([k, s]) => { const box = document.createElement('div');"
                 " E.renderExecution(box, {status: s, fixtureKeys: [k]}, {fixture: true});"
                 " const els = box.all(); const pre = els.find((e) => e.tagName === 'PRE');"
                 " return [k, {text: box.textContent.replace(pre ? pre.textContent : '', ''),"
                 " rows: els.filter((e) => e.className.split(' ').includes('exec-row')).length,"
                 " buttons: els.filter((e) => e.tagName === 'BUTTON').map((b) => b.className),"
                 " inputs: els.filter((e) => ['INPUT', 'FORM', 'TEXTAREA', 'SELECT'].includes(e.tagName)).length}]; }))")
    for key, r in out.items():
        assert "undefined" not in r["text"] and "NaN" not in r["text"] and "null" not in r["text"], key
        assert r["inputs"] == 0 and all(c.startswith("chip") for c in r["buttons"]), key
        assert r["rows"] == (0 if key in ("idle", "settled") else 4), key
        assert "模拟数据" in r["text"], key
    assert "上一局" in out["settled"]["text"] and "到期交割结清" in out["settled"]["text"]
    assert "结果未知" in out["unknown"]["text"] and "已停写" in out["takeover"]["text"]


def test_freshness_trusts_server_age_and_only_flags_long_client_gaps():
    s = states()
    idle = dict(s["idle"], status_age_ms=900, facts_age_ms=200_000, stale=False, generated_at_ms=0)
    m = model(idle, fixture=False, nowMs=100_000, fetchedAtMs=80_000)   # read 20 s ago, idle facts not counted
    assert m["level"] == "off" and not any("过期" in b["title"] for b in m["banners"])
    m = model(idle, fixture=False, nowMs=200_000, fetchedAtMs=80_000)   # no re-read for 2 minutes
    assert any(b["title"] == "执行状态过期" for b in m["banners"])
    active = dict(s["tp"], stale=True, status_age_ms=900, facts_age_ms=20_000)
    assert model(active, fixture=False, nowMs=1_000, fetchedAtMs=1_000)["level"] == "bad"


def test_unknown_tp_and_flat_residual_read_honestly():
    s = states()
    ep = dict(s["tp"]["active_episode"], tp=dict(s["tp"]["active_episode"]["tp"], order_status="SUBMIT_UNKNOWN"))
    assert model(dict(s["tp"], active_episode=ep), fixture=True)["action"]["text"] == "止盈单结果未知，核验中"
    ep = dict(s["residual"]["active_episode"], prot_pos_btc="0", residual={"status": "NONE"})
    m = model(dict(s["residual"], active_episode=ep), fixture=True)
    assert m["stage"]["text"] == "两腿已清 · 待库存确认" and "买盘" not in m["action"]["text"]
    assert 'api.post("/execution' not in (FRONT / "app.js").read_text(encoding="utf-8")


def test_real_coordinator_public_status_contract_renders_honestly(tmp_path: Path):
    import sys
    for p in (str(ROOT), str(ROOT / "tests"), str(ROOT / "tools")):
        if p not in sys.path:
            sys.path.insert(0, p)
    from decimal import Decimal as D
    import astra_exec_contracts_v1 as contracts
    import astra_exec_store_v1 as store
    import astra_execution_coordinator_v1 as coord_mod
    from tests import test_astra_exec_p2_integration as p2

    # 1. entry_ep_only from REAL ExecutionCoordinator: EP filled, ES blocked (bid below floor), entry_closed=False
    venue = p2._setup_venue()
    venue.set_book("BTC-25NOV26-62000-P", bid="0.0005", bid_size="5.0", ask="0.0006", ask_size="5.0", mark_price="0.0006")
    st_path = tmp_path / "public_status.json"
    st = store.ExecutionStore(tmp_path / "exec.sqlite3")
    dos_root = tmp_path / "dos"
    dos_root.mkdir()
    try:
        p2._build_p5_dossier(dos_root, decided_at_ms=venue.server_time_ms - 5_000)
        adp = p2.SimExchangeAdapter(venue)
        coord = coord_mod.ExecutionCoordinator(
            mode="TESTNET", env_tag="tn", subaccount_id="sub-tn-1",
            store=st, adapter=adp, status_path=st_path, dossier_root=dos_root,
            config=contracts.ExecConfig(initial_qty_btc=D("0.1"), max_entry_slice_btc=D("0.1")),
        )
        coord.startup_init(now_ms=venue.server_time_ms)
        st_idle = coord.handle_capital_policy(
            {"entry_armed": True, "budget_cap_btc": "0.20", "max_qty_btc": "0.1", "operator": "cfg"},
            now_ms=venue.server_time_ms, run_tick_after=False,
        )["status"]
        m_idle = model(st_idle, fixture=False, nowMs=st_idle["generated_at_ms"])
        assert m_idle["stage"]["key"] == "idle"

        st_ep = coord.run_tick(now_ms=venue.server_time_ms)
        m_ep = model(st_ep, fixture=False, nowMs=st_ep["generated_at_ms"])
        assert m_ep["stage"]["key"] == "entry"
        assert "库存已清" not in m_ep["stage"]["text"]
        assert "短腿已清" not in m_ep["stage"]["text"]
        assert m_ep["track"][2]["done"] is False and m_ep["track"][3]["done"] is False

        # Restore short bid -> reaches TP_MANAGED, verify real producer budget & TP & decided_at_ms render cleanly
        venue.advance_time(50)
        venue.set_book("BTC-25NOV26-62000-P", bid="0.0044", bid_size="5.0", ask="0.0046", ask_size="5.0", mark_price="0.0045")
        p2._refresh_books(venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P")
        st_tp = p2._drive(coord, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=3, dt_ms=50)
        m_tp = model(st_tp, fixture=False, nowMs=st_tp["generated_at_ms"])
        assert m_tp["stage"]["key"] == "hold"
        rendered_tp = render({"s": st_tp, "now": st_tp["generated_at_ms"]}, "(() => { const b = document.createElement('div'); E.renderExecution(b, {status: p.s}, {nowMs: p.now}); return b.textContent; })()")
        assert "风险预算" in rendered_tp and "入场已启用" in rendered_tp and "undefined" not in rendered_tp

        # 2. Historical cleared 10009 alert with revoke.cash_wall_10009=False must NOT show stale cash-wall danger banner
        cleared_10009 = json.loads(json.dumps(st_tp))
        cleared_10009["active_episode"]["exit_latched"] = True
        cleared_10009["active_episode"]["exit_latch_reason"] = "AI_REVOKE"
        cleared_10009["active_episode"]["alerts"] = ["RX_MARGIN_OR_CASH_EXHAUSTED_SPREAD_HEDGED_MANUAL_DECISION"]
        cleared_10009["active_episode"]["revoke"]["cash_wall_10009"] = False
        m_clr = model(cleared_10009, fixture=False, nowMs=cleared_10009["generated_at_ms"])
        assert not any("现金不足" in b["title"] for b in m_clr["banners"])
        assert "现金不足" not in m_clr["action"]["text"]

        # 3. Real producer settled history + scientific notation shadow_counterfactual (-5e-7) renders cleanly
        tp_cmd = next(c for c in st.get_commands(st_tp["active_episode"]["episode_id"]) if c.purpose == "TP")
        venue.fill_resting_order(tp_cmd.label, D("0.1"), price=D("0.0002"), liquidity="M")
        st_set = p2._drive(coord, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=4, dt_ms=500)
        m_set = model(st_set, fixture=False, nowMs=st_set["generated_at_ms"])
        assert m_set["stage"]["key"] == "idle" and m_set["last"] is not None
        rendered_set = render({"s": st_set, "now": st_set["generated_at_ms"]}, "(() => { const b = document.createElement('div'); E.renderExecution(b, {status: p.s}, {nowMs: p.now}); return b.textContent; })()")
        assert "已实现" in rendered_set and "提前平仓结清" in rendered_set

        sci_ep = json.loads(json.dumps(st_tp))
        sci_ep["active_episode"]["shadow_counterfactual"] = {"shadow_exit_pnl_btc": -5e-7, "valuation_tier": "TIER_1"}
        rendered_sci = render({"s": sci_ep, "now": sci_ep["generated_at_ms"]}, "(() => { const b = document.createElement('div'); E.renderExecution(b, {status: p.s}, {nowMs: p.now}); return b.textContent; })()")
        assert "−0.0000005 BTC" in rendered_sci and "未知" not in rendered_sci.split("影子退出盈亏")[1][:30]
    finally:
        st.close()
