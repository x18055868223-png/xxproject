import copy
from test_signal_evidence_v221_frontend import card_with_times
from test_signal_evidence_v22_frontend import refresh_reader_hashes
from test_signal_evidence_v21_frontend import projection_hash, render_cards, html_section, assert_no_machine_leak


def section_card():
    card = card_with_times()
    facts = card['llm_review']['content']['integrated_trade_advisory']['market_facts']
    asof = card['signal_evidence_summary']['as_of_ms']
    for key, value, label in (
        ('structure.gamma.call_wall', 65432.1, '上方 Call 墙'),
        ('structure.gamma.put_wall', 64321.2, '下方 Put 墙'),
        ('structure.gamma.pin_strike', 64765.3, 'Pin 或最大 Gamma 行权价'),
    ):
        existing = next((f for f in facts if f['id'] == key), None)
        new = dict(id=key, topic='structure_location', label_cn=label, value=value,
                   unit='USDT', source_refs=['factor_cross_section.gex_info'], source_group='OPTIONS_STRUCTURE',
                   usable=True, observed_at_ms=None, available_at_ms=asof,
                   window='当前截面', summary_cn=label, limitations_cn=[])
        if existing: existing.update(new)
        else: facts.append(new)
    refresh_reader_hashes(card)
    return card


def test_spatial_points_live_in_dynamics_and_keep_full_source_values():
    rendered = render_cards([section_card()])
    spatial = html_section(rendered['documentHtml'], 'signal-spatial-dynamics')
    for value in ('65,432.1', '64,321.2', '64,765.3'):
        assert value in spatial
    assert 'spatial-levels' in spatial
    source = html_section(rendered['documentHtml'], 'market-options-structure')
    assert 'spatial-source-value' in source
    assert_no_machine_leak(rendered, context='section placement')


def test_valid_local_change_projection_is_separate_from_decision_and_tamper_isolated():
    card = section_card()
    baseline = render_cards([card])
    summary = card['signal_evidence_summary']
    projection = dict(schema_version='signal_change_projection@1.1.0',
                      source_record_hash=summary['source_record_hash'], assessment_hash=summary['assessment_hash'],
                      as_of_ms=summary['as_of_ms'], facts=[], reasons_cn=[], rows=[dict(
                          key='net_gamma', label_cn='净 Gamma 名义规模', previous=210610000,
                          current=306820000, delta=96210000, unit='USD', usable=True,
                          summary_cn='净名义值增加；不直接证明约束增强。', gap_cn='', source_refs=[])])
    projection['projection_hash'] = projection_hash(projection)
    card['local_change_projection'] = projection
    rendered = render_cards([card])
    changes = html_section(rendered['documentHtml'], 'signal-key-changes')
    assert '$210.61M' in changes and '$306.82M' in changes
    assert '未进入当时模型评审' in changes
    assert html_section(rendered['documentHtml'], 'signal-comfort') == html_section(baseline['documentHtml'], 'signal-comfort')
    bad = copy.deepcopy(card)
    bad['local_change_projection']['rows'][0]['current'] = 999999999
    assert '$1B' not in html_section(render_cards([bad])['documentHtml'], 'signal-key-changes')
    assert_no_machine_leak(rendered, context='local change display')
