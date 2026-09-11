"""Reader uses availability, while displaying unknown market observation honestly."""
import copy
from test_signal_evidence_v22_frontend import card_v22, refresh_reader_hashes
from test_signal_evidence_v21_frontend import render_cards, assert_no_machine_leak, html_section


def card_with_times():
    card = card_v22()
    card['llm_review']['prompt_version'] = 'signal_llm_review_prompt@2.2.1'
    card['llm_review']['evidence_context'] = {'schema': 'signal_evidence_packet@2.1.1'}
    adv = card['llm_review']['content']['integrated_trade_advisory']
    asof = card['signal_evidence_summary']['as_of_ms']
    for fact in adv['market_facts']:
        fact.update(available_at_ms=asof-1000, observed_at_ms=None)
        fact['provenance'] = dict(observed_at_ms=None, generated_at_ms=asof-3000,
                                  fetched_at_ms=asof-1000, recorded_at_ms=asof,
                                  time_errors=[], time_basis='upstream_generated')
    refresh_reader_hashes(card)
    return card


def test_unknown_observation_remains_readable_and_price_bias_consistent():
    card = card_with_times()
    before = copy.deepcopy(card)
    rendered = render_cards([card])
    assert card == before
    assert '真实观测未知' in rendered['documentText']
    assert '上游结果时间' in rendered['documentText']
    assert '最近抓取' in rendered['documentText']
    assert '卡片记录' in rendered['documentText']
    assert '偏空' in html_section(rendered['documentHtml'], 'signal-comfort')
    assert_no_machine_leak(rendered, context='v2.2.1 source clocks')


def test_future_source_clock_does_not_pass_price_bias_check():
    card = card_with_times()
    adv = card['llm_review']['content']['integrated_trade_advisory']
    ref = adv['price_bias']['evidence_refs'][0]
    next(f for f in adv['market_facts'] if f['id']==ref)['provenance']['generated_at_ms'] = card['signal_evidence_summary']['as_of_ms']+1
    refresh_reader_hashes(card)
    rendered = render_cards([card])
    assert '倾向未提供' in rendered['documentText']
    assert 'LLM 独立价格倾向：偏空' not in html_section(rendered['documentHtml'], 'signal-comfort')


def test_unspecified_time_basis_is_not_called_market_observation():
    card = card_with_times()
    adv = card['llm_review']['content']['integrated_trade_advisory']
    for fact in adv['market_facts']:
        fact['observed_at_ms'] = fact['available_at_ms']
        fact.pop('provenance', None)
    refresh_reader_hashes(card)
    rendered = render_cards([card])
    assert '记录时点：' in rendered['documentText']
    assert '市场观测：' not in rendered['documentText']
