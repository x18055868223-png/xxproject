import json
import sys

from test_signal_comfort_frontend import (
    FRONTEND,
    assert_no_machine_leak,
    assert_true,
    render_cards,
)
from test_signal_evidence_frontend import evidence_card


def main():
    rendered = render_cards([evidence_card()])
    text = rendered["documentText"]
    html = rendered["documentHtml"]
    ordered = [
        'id="signal-comfort"',
        'id="signal-spatial-dynamics"',
        'id="signal-key-changes"',
        'id="signal-llm-review"',
        'id="signal-next-conditions"',
        'id="market-evidence"',
    ]
    positions = [html.find(label) for label in ordered]
    assert_true(all(pos >= 0 for pos in positions),
                "reader should render every required top-level section")
    assert_true(positions == sorted(positions),
                "v2 reader sections should follow the contracted reading order")
    assert_true("本卡行动结论" in text and "关键变化骨架" in text
                and "中文市场事实" in text and "下一观察条件" in text,
                "report should retain the action, verified changes, evidence and observation chapters")
    assert_true("最高辅助交易决策" in text and "空间约束动力学" in text
                and "LLM 独立复核意见" in text,
                "v2 report should retain the four approved audit modules")
    assert_true("Put 侧证据等级 A" in text and "Call 信用价差" in text,
                "v2 action and both side ratings should be visible")
    for token in ("方向:", "当前限制:", "数据质量", "系统边界与阻断", "既有深入分析", "LLM 深入分析", "证据账本摘要", "冲突解释"):
        assert_true(token not in text, "v2 reader should not render old header, metrics, or deep sections: " + token)
    assert_true("完整审计资料" in text and "下载完整审计资料" in text,
                "audit data should be available through a plain download entry")
    assert_true("raw-trace" not in html and "field-path" not in html,
                "ordinary page should not keep hidden raw trace DOM")
    assert_no_machine_leak(rendered, context="render contract")

    index_html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert_true('id="gradeFilter"' in index_html
                and "B及以上" in index_html
                and "A/S" in index_html,
                "sidebar should expose B+ and A/S filters")
    cache_token = json.loads((FRONTEND / "VERSION.json").read_text(encoding="utf-8"))["frontend_cache_token"]
    assert_true(bool(cache_token) and f'app.js?v={cache_token}"' in index_html,
                "index and version metadata should use the same reader cache token")
    print("signal_audit_frontend_render_contract: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_audit_frontend_render_contract: FAIL - " + str(exc))
        sys.exit(1)
