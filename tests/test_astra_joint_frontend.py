import copy
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "deploy" / "signal_audit" / "frontend"
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from test_signal_comfort_frontend import render_cards
from test_signal_evidence_v22_frontend import card_v22

AS_OF_MS = 1781751600000


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def joint_summary(card_id="JOINT-0"):
    return {
        "schema_version": "astra_joint_display_summary@1.0.0",
        "status": "available",
        "identity": {
            "card_id": card_id,
            "symbol": "BTC",
            "confirmed_at": "2026-09-10T05:39:58+08:00",
            "strategy_name": "Astra 联合研究影子结果",
        },
        "as_of_ms": AS_OF_MS,
        "source_record_hash": "sha256:" + "a" * 64,
        "assessment_hash": "sha256:" + "b" * 64,
        "detail_projection_hash": "sha256:" + "c" * 64,
        "summary_cn": "Put 赔付风险较低：宽度折算期望赔付约 7.70%，另一侧约 13.33%。",
        "risk_comparison": {
            "relative_side": "put",
            "summary_cn": "Put 赔付风险较低：宽度折算期望赔付约 7.70%，另一侧约 13.33%。",
        },
        "quote_comparison_cn": "没有同期补偿；本卡只显示赔付风险排序，不判断实际卖出更优。",
        "put": {
            "status": "available",
            "probability_positive": 0.22,
            "expected_loss_normalized": 0.077,
            "expected_payout_btc": 0.00111,
            "quote_status": "not_collected",
        },
        "call": {
            "status": "available",
            "probability_positive": 0.31,
            "expected_loss_normalized": 0.1333,
            "expected_payout_btc": 0.00195,
            "quote_status": "not_collected",
        },
        "display_projection_hash": "sha256:" + "d" * 64,
    }


def joint_detail(card_id="JOINT-0"):
    summary = joint_summary(card_id)
    return {
        "schema_version": "astra_joint_display@1.0.0",
        "status": "available",
        "identity": summary["identity"],
        "summary_cn": summary["summary_cn"],
        "risk_comparison": summary["risk_comparison"],
        "quote_comparison_cn": summary["quote_comparison_cn"],
        "sides": {
            "put": {
                "status": "available",
                "label_cn": "Put 信用价差",
                "probability_positive": 0.22,
                "conditional_positive_loss": 0.35,
                "expected_loss_normalized": 0.077,
                "expected_payout_btc": 0.00111,
                "probability_cn": "赔付发生概率 22.0%",
                "expected_loss_cn": "期望赔付 0.001110 BTC，约为宽度折算的 7.70%",
                "reference_cn": "卖 77,000 / 买 75,000，宽度 2,000，入场价 79,000.00",
                "quote_cn": "没有同期补偿；只能比较赔付风险，不能说明该侧更值得卖。",
                "uncertainty_cn": "样本支持一般。",
                "scope_cn": "仅适用于本研究封存口径。",
            },
            "call": {
                "status": "available",
                "label_cn": "Call 信用价差",
                "probability_positive": 0.31,
                "conditional_positive_loss": 0.43,
                "expected_loss_normalized": 0.1333,
                "expected_payout_btc": 0.00195,
                "probability_cn": "赔付发生概率 31.0%",
                "expected_loss_cn": "期望赔付 0.001950 BTC，约为宽度折算的 13.33%",
                "reference_cn": "卖 81,000 / 买 83,000，宽度 2,000，入场价 79,000.00",
                "quote_cn": "没有同期补偿；只能比较赔付风险，不能说明该侧更值得卖。",
                "uncertainty_cn": "尾部样本偏少。",
                "scope_cn": "仅适用于本研究封存口径。",
            },
        },
        "scope_cn": "统计研究只用于影子验证；不改变 D-S 评级、原信号窗口或交易权限。",
        "provenance": {
            "as_of_ms": AS_OF_MS,
            "source_record_hash": "sha256:" + "a" * 64,
            "input_hash": "sha256:" + "e" * 64,
            "model_id": "gam-l2-two-part@research",
            "model_hash": "sha256:" + "f" * 64,
            "training_cutoff": "2021-12-31",
            "training_cutoff_ms": AS_OF_MS - 86400000,
        },
        "assessment_hash": "sha256:" + "b" * 64,
        "display_projection_hash": "sha256:" + "g" * 64,
    }


def run_joint_http_reader(path_override=None):
    script = r'''
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const root = __ROOT__;
const app = fs.readFileSync(path.join(root, "app.js"), "utf8");
const elements = {};
const buttons = new Map();
const calls = [];
const retryButtons = [];
const manifest = { cards: Array.from({ length: 20 }, (_, index) => ({
  summary: __SUMMARY_FUNC__(`JOINT-${index}`),
  path: __PATH_OVERRIDE__ || `joint-shadow/JOINT-${index}.json`
})) };
function detail(index) { return __DETAIL_FUNC__(`JOINT-${index}`); }
function element(id) {
  if (!elements[id]) {
    elements[id] = {
      id, value: "", innerHTML: "", textContent: "", dataset: {}, hidden: false,
      classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
      addEventListener() {}, setAttribute() {},
      insertAdjacentHTML(_where, html) { this.innerHTML += html; },
      focus() {}
    };
  }
  return elements[id];
}
const document = {
  body: { appendChild() {} },
  head: { appendChild() { throw new Error("joint mode must not load fallback.js"); } },
  createElement() { return { href: "", download: "", click() {}, remove() {}, addEventListener() {} }; },
  getElementById(id) { if (id === "signal-data") return { textContent: "[]" }; return element(id); },
  querySelector(selector) { return element(selector.startsWith("#") ? selector.slice(1) : selector); },
  querySelectorAll(selector) {
    if (selector === ".index-item") {
      const ids = [...element("indexList").innerHTML.matchAll(/data-card-id="([^"]+)"/g)].map((match) => match[1]);
      return ids.map((id) => {
        const button = { dataset: { cardId: id }, addEventListener(type, handler) { if (type === "click") this.click = handler; } };
        buttons.set(id, button);
        return button;
      });
    }
    if (selector === ".card-retry") {
      retryButtons.length = 0;
      const html = element("documentView").innerHTML || "";
      const matches = html.match(/<button\b[^>]*class="[^"]*\bcard-retry\b[^"]*"[^>]*>/g) || [];
      matches.forEach((tag) => {
        const button = { dataset: {}, addEventListener(type, handler) { if (type === "click") this.click = handler; } };
        tag.replace(/data-([a-z0-9-]+)="([^"]*)"/gi, (_m, key, value) => {
          button.dataset[String(key).replace(/-([a-z])/g, (_match, ch) => ch.toUpperCase())] = value;
          return "";
        });
        retryButtons.push(button);
      });
      return retryButtons;
    }
    return [];
  }
};
async function fetch(url) {
  calls.push(url);
  if (url === "joint-shadow/manifest.json") return { ok: true, status: 200, async json() { return manifest; } };
  if (url === "signal_cards/index.json") throw new Error("signal manifest should not be fetched in joint mode");
  const match = /JOINT-(\d+)\.json$/.exec(url);
  if (!match) return { ok: false, status: 404, async json() { return {}; } };
  return { ok: true, status: 200, async json() { return detail(Number(match[1])); } };
}
const context = {
  window: { location: { protocol: "http:", search: "?research=joint", hash: "" }, SIGNAL_AUDIT_CARD_TIMEOUT_MS: 1000 },
  document, console, Intl, Map, Promise, setTimeout, clearTimeout, fetch, decodeURIComponent
};
vm.createContext(context);
vm.runInContext(app, context);
setTimeout(() => {
  const text = element("documentView").innerHTML.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
    process.stdout.write(JSON.stringify({
    calls,
    detailCalls: calls.filter((item) => item.endsWith(".json") && !item.endsWith("manifest.json")).length,
    listButtons: (element("indexList").innerHTML.match(/class="index-item/g) || []).length,
    gradeHidden: element("gradeFilterGroup").hidden,
    indexText: element("indexList").innerHTML.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim(),
    text,
    html: element("documentView").innerHTML
  }));
}, 100);
'''
    script = script.replace("__ROOT__", json.dumps(str(FRONTEND)))
    script = script.replace("__PATH_OVERRIDE__", json.dumps(path_override))
    script = script.replace("__SUMMARY_FUNC__", "((id) => (" + json.dumps(joint_summary("__ID__")).replace('"__ID__"', 'id') + "))")
    script = script.replace("__DETAIL_FUNC__", "((id) => (" + json.dumps(joint_detail("__ID__")).replace('"__ID__"', 'id') + "))")
    result = subprocess.run(["node", "-e", script], text=True, capture_output=True, encoding="utf-8", errors="replace", timeout=20)
    assert_true(result.returncode == 0, result.stderr or result.stdout)
    return json.loads(result.stdout)


def test_joint_shadow_manifest_is_lazy_and_separate():
    data = run_joint_http_reader()
    assert_true(data["calls"].count("joint-shadow/manifest.json") == 1, "joint mode should fetch its own manifest once")
    assert_true("signal_cards/index.json" not in data["calls"], "joint mode must not fetch production card manifest")
    assert_true(data["detailCalls"] == 3, "joint mode should load selected detail plus two bounded prefetches")
    assert_true(data["listButtons"] == 15, "joint mode should keep the same initial list limit")
    assert_true(data["gradeHidden"] is True, "D-S grade filter should be hidden for standalone statistical shadow records")
    text = data["text"]
    assert_true("BTC 联合研究影子评估" in text, "standalone joint detail should use research header")
    assert_true("赔付发生概率 22.0%" in text and "期望赔付 0.001110 BTC" in text, "side scalar details should render")
    assert_true("训练截止： 2021-12-31 UTC" in text or "训练截止：2021-12-31 UTC" in text, "date-only training cutoff should render as UTC date")
    assert_true("2021/12/31 08:00:00" not in text, "date-only training cutoff must not invent a Beijing time")
    assert_true("Put 赔付概率 22%" in data["indexText"] and "Call 赔付概率 31%" in data["indexText"], "index should label payout probability explicitly")
    assert_true("Put 赔付 22%" not in data["indexText"] and "Call 赔付 31%" not in data["indexText"], "index should not use ambiguous payout wording")
    assert_true("没有同期补偿" in text and "不能说明该侧更值得卖" in text, "quote boundary should remain explicit")
    assert_true("不触发 LLM、通知或交易权限" in text, "production boundary should be visible")
    for forbidden in ("source_record_hash", "assessment_hash", "input_hash", "model_hash", "sha256:"):
        assert_true(forbidden not in text and forbidden not in data["html"], f"machine field leaked: {forbidden}")


def test_unknown_training_cutoff_does_not_become_epoch(monkeypatch):
    original = joint_detail

    def without_cutoff(identity):
        card = original(identity)
        card["provenance"]["training_cutoff"] = None
        card["provenance"]["training_cutoff_ms"] = None
        return card

    monkeypatch.setitem(globals(), "joint_detail", without_cutoff)
    rendered = run_joint_http_reader()
    assert_true("1970-01-01" not in rendered["text"], "unknown cutoff must not be converted into epoch")


def test_existing_signal_card_only_shows_joint_summary_when_projection_exists():
    base = card_v22()
    rendered = render_cards([base])
    assert_true("统计研究影子" not in rendered["documentText"], "missing joint projection should not create placeholders")

    enriched = card_v22()
    enriched["joint_research_summary"] = joint_summary("INLINE-JOINT")
    rendered = render_cards([enriched])
    assert_true("本卡建议" in rendered["documentText"], "normal v2 card should keep highest decision section")
    assert_true("统计研究影子" in rendered["documentText"], "joint summary should embed inside highest decision")
    assert_true("Put B级" in rendered["indexText"] and "Call B级" in rendered["indexText"], "D-S list stats should remain unchanged for production card")
    assert_true("不改变 D-S 评级、原信号窗口或交易权限" in rendered["documentText"], "joint boundary should be explicit")
    for forbidden in ("source_record_hash", "assessment_hash", "input_hash", "model_hash", "sha256:"):
        assert_true(forbidden not in rendered["documentText"] and forbidden not in rendered["documentHtml"], f"inline machine field leaked: {forbidden}")


def test_joint_manifest_rejects_unexpected_detail_paths_before_fetch():
    for path in (
        "https://example.invalid/JOINT-0.json", "//example.invalid/JOINT-0.json",
        "joint-shadow/../../JOINT-0.json", "joint-shadow/%2e%2e/JOINT-0.json",
        "joint-shadow/%252e%252e/JOINT-0.json", "joint-shadow/%2fJOINT-0.json",
        "joint-shadow/\\JOINT-0.json", "private/JOINT-0.json",
    ):
        data = run_joint_http_reader(path)
        assert_true(data["calls"] == ["joint-shadow/manifest.json"], f"unexpected detail request for {path}")


def test_late_statistics_do_not_claim_participation():
    card = card_v22()
    card["joint_research_summary"] = joint_summary("LATE")
    for included in (False, None, True):
        card["joint_research_review_included"] = included
        text = render_cards([card])["documentText"]
        assert ("本卡评审已使用这份冻结统计" in text) == (included is True)
        assert ("补充统计，未确认参与本卡评审" in text) == (included is not True)


def joint_reader_card():
    from test_signal_evidence_frontend import assessment_hash, html_section
    from test_signal_evidence_v21_frontend import projection_hash
    card = card_v22()
    review = card['llm_review']
    advisory = review['content']['integrated_trade_advisory']
    summary = card['signal_evidence_summary']
    for obj in (review, advisory):
        obj.update(schema_version='signal_llm_review@2.3.0', prompt_version='signal_llm_review_prompt@2.3.0')
    summary.update(schema='signal_evidence_summary@2.3.0', review_schema_version='signal_llm_review@2.3.0', display_projection_version='2.3.0')
    joint = dict(status='ASSESSED', statistical_preference='call_credit', recommendation='watch',
                 mechanism_verdict='contradicts', summary_cn='联合复核建议暂缓。',
                 applicability_gaps_cn=[], strengthen_if_cn=['价格回到约束区。'], weaken_if_cn=['上行压力继续传导。'],
                 evidence_roles=[dict(ref='pressure.tmv.direction', role='counters_applicability', claim_cn='上行压力挑战本卡统计适用。')])
    old = dict(status='ASSESSED', summary_cn='旧建议准备Call。', tradeoffs_cn=['旧条件展望仍建议准备Call。'],
               evidence_refs=[], validation_reasons_cn=[], outlooks=[dict(horizon_hours=4, scenario_cn='未来四小时Call侧仍可准备。', watch_cn='观察上方压力。', evidence_refs=[])])
    for obj in (advisory, summary):
        obj.update(joint_review=copy.deepcopy(joint), advisory_guidance=copy.deepcopy(old))
    advisory['validation']['assessment_hash'] = assessment_hash(advisory)
    summary['assessment_hash'] = advisory['validation']['assessment_hash']
    summary['display_projection_hash'] = projection_hash(summary)
    card['joint_research_summary'] = joint_summary('LATE-V23')
    return card


def test_joint_advice_is_the_only_highest_conclusion():
    from test_signal_evidence_frontend import html_section
    card = joint_reader_card()
    rendered = render_cards([card])
    top = html_section(rendered['documentHtml'], 'signal-comfort')
    assert '联合复核建议暂缓' in top and '上行压力挑战本卡统计适用' in top
    assert '价格回到约束区' in top and '上行压力继续传导' in top
    assert '旧条件展望仍建议准备Call' not in top and '未来四小时Call侧仍可准备' not in top
    assert '补充统计，未确认参与本卡评审' in top
    assert '当前统计排序尚未证明优于简单几何基线' in top
    assert '自然卡净效果仍待验证' in top
    assert '校验未通过' not in rendered['documentText']


def test_per_card_highlights_replace_fixed_closure_without_rewriting_opinion():
    card = joint_reader_card()
    original = copy.deepcopy(card)
    rendered = render_cards([card])
    text = rendered['documentText']
    assert text.index('本轮重要内容汇总') < text.index('联合复核建议暂缓')
    assert 'R17 · 2026/09/22' not in text and '风险工具有用，业务未成立' not in text
    assert '联合研究评估' in text and '卡时研究意见' in text
    assert card == original
    assert '风险工具有用，业务未成立' not in render_cards([card_v22()])['documentText']
    shadow = run_joint_http_reader()['text']
    assert '风险工具有用，业务未成立' not in shadow and 'Put 赔付风险较低' in shadow


def test_tail_percentage_stays_withdrawn_even_for_stale_available_status():
    from test_signal_evidence_frontend import html_section
    card = joint_reader_card()
    for status in ('available', 'research_only', 'insufficient'):
        variant = copy.deepcopy(card)
        for side in ('put', 'call'):
            variant['joint_research_summary'][side].update(
                tail_probability_status=status, tail_probability=0.876543)
        original = copy.deepcopy(variant)
        top = html_section(render_cards([variant])['documentHtml'], 'signal-comfort')
        highlights = html_section(render_cards([variant])['documentHtml'], 'audit-highlights')
        assert '87.7%' not in top and '87.65%' not in top
        assert '87.7%' not in highlights and '87.65%' not in highlights
        assert top.count('<dt>保护腿突破概率</dt><dd>暂不采用</dd>') == 2
        assert '22%' in top and '31%' in top
        assert variant == original


def test_unqualified_joint_recommendation_does_not_escape_in_full_or_summary_view():
    from test_signal_evidence_frontend import assessment_hash, html_section
    from test_signal_evidence_v21_frontend import projection_hash
    card = joint_reader_card()
    advisory = card['llm_review']['content']['integrated_trade_advisory']
    summary = card['signal_evidence_summary']
    for obj in (advisory, summary):
        obj['joint_review'].update(recommendation='put_credit', summary_cn='不应采用的Put推荐。')
    advisory['side_evidence_ratings']['put_credit'].update(status='UNRATED', grade=None)
    summary['put_credit'].update(status='UNRATED', grade=None)
    advisory['validation']['assessment_hash'] = assessment_hash(advisory)
    summary['assessment_hash'] = advisory['validation']['assessment_hash']
    summary['display_projection_hash'] = projection_hash(summary)
    for summary_only in (False, True):
        variant = copy.deepcopy(card)
        if summary_only:
            variant.pop('llm_review')
        rendered = render_cards([variant])
        top = html_section(rendered['documentHtml'], 'signal-comfort')
        assert '联合建议暂不采用' in top
        assert '不应采用的Put推荐' not in top
        assert '旧建议准备Call' not in top and '旧条件展望' not in top
        assert '上行压力挑战本卡统计适用' not in top
        assert '当前统计排序尚未证明优于简单几何基线' in top
        assert 'Call 信用价差' in top
        if not summary_only:
            highlights = html_section(rendered['documentHtml'], 'audit-highlights')
            assert '联合建议暂不采用' in highlights and '推荐侧证据未通过本地语义核验' in highlights
            assert '不应采用的Put推荐' not in highlights


def main():
    test_joint_shadow_manifest_is_lazy_and_separate()
    test_existing_signal_card_only_shows_joint_summary_when_projection_exists()
    test_joint_manifest_rejects_unexpected_detail_paths_before_fetch()
    test_late_statistics_do_not_claim_participation()
    test_joint_advice_is_the_only_highest_conclusion()
    test_per_card_highlights_replace_fixed_closure_without_rewriting_opinion()
    test_tail_percentage_stays_withdrawn_even_for_stale_available_status()
    test_unqualified_joint_recommendation_does_not_escape_in_full_or_summary_view()
    print("astra_joint_frontend: PASS (8 standalone checks; use pytest for fixture-based checks)")


if __name__ == "__main__":
    main()
