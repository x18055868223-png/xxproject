from html.parser import HTMLParser

from tests.astra_workbench_frontend_harness import render_workbench
from tests.test_astra_preview_repairs_r1 import _simulated_result
from tools import astra_workbench_preview_v20 as preview

NOW = 1_790_323_200_000


class ReceiptVisibility(HTMLParser):
    def __init__(self):
        super().__init__()
        self.disclosures = []
        self.current_q1_visible = False
        self.history_notice_visible = False

    def handle_starttag(self, tag, attrs):
        if tag == 'details':
            self.disclosures.append('open' in dict(attrs))

    def handle_endtag(self, tag):
        if tag == 'details':
            self.disclosures.pop()

    def handle_data(self, data):
        visible = all(self.disclosures)
        if 'Q1 ·' in data and visible:
            self.current_q1_visible = True
        if '历史回执 · 当前候选尚无同版结论' in data and visible:
            self.history_notice_visible = True


def test_old_negative_receipt_is_history_not_visible_current_conclusion():
    old = preview.build_preview(NOW, scenario_id='W4')
    receipt = _simulated_result(old, 'W4')
    current = preview.build_preview(NOW, scenario_id='W1', manual_review_state={'last_result': receipt})
    area = render_workbench(current)['areas']['manualReviewState']
    parser = ReceiptVisibility()
    parser.feed(area)
    assert parser.history_notice_visible
    assert not parser.current_q1_visible
    assert '参考补偿不足' in area  # Original failure is retained inside history.
    assert current['candidates'][0]['snapshot']['economics']['reference_margin_btc'] > 0


def test_same_version_questions_and_tail_boundary_remain_visible():
    current = preview.build_preview(NOW, scenario_id='W2')
    receipt = _simulated_result(current, 'W2')
    current = preview.build_preview(NOW, scenario_id='W2', manual_review_state={'last_result': receipt})
    areas = render_workbench(current)['areas']
    parser = ReceiptVisibility()
    parser.feed(areas['manualReviewState'])
    assert parser.current_q1_visible
    assert not parser.history_notice_visible
    assert '风险与μ' in areas['manualReviewState'] or '风险覆盖' in areas['manualReviewState']
    assert '合同责任边界' in areas['manualReviewState']
    for internal in ('fact_context.', 'flow.direction', 'QUANTIFIED'):
        assert internal not in areas['manualReviewState']
    # Tail responsibility is present before the candidate's closed details.
    brief = areas['candidateArea'].split('<details class="candidate-details"', 1)[0]
    assert '没有固定 BTC 上限' in brief
    assert '执行许可关闭' in brief
