"""Frontend reads routed facts, preserving facets and snapshot identity."""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent))
from astra_workbench_frontend_harness import render_workbench
from test_astra_wpem_frontend import _candidate, _workbench


def fixture():
    c=_candidate(); s=c['snapshot']
    def unit(identifier, reason, relation):
        return {'evidence_id':identifier,'source_asset':identifier,'reason_cn':reason,
                'loop_relations':[{'loop':'NEUTRAL_STABILIZING_LOOP','relation':relation}],
                'system_role':'FEEDBACK','dependency_group':'OPTION_SPACE','q1_role':'SUPPORT','q2_role':'NONE'}
    c['wpem_evidence']['evidence_routing']={
        'schema':'evidence_routing@1.0.0','routing_hash':'routing-fixture-hash',
        'binding':{'candidate_id':s['candidate_id'],'snapshot_id':s['snapshot_id']},
        'units':[unit('anchor','ANCHOR_SUPPORT_SOURCE','SUPPORT'),unit('gamma','GAMMA_COUNTER_SOURCE','CONTRADICT')],
        'dependency_blocks':[{'block_id':'block.option_space','evidence_ids':['anchor','gamma'],'representative_id':'gamma'}],
        'problem_map':[{'domain':'FEEDBACK_STABILITY','label_cn':'反馈稳定性','summary_cn':'BACKEND_ONLY_SUMMARY','limitations':[]}],
        'loop_digest':{'neutral_stabilizing_loop':{'support_blocks':['block.option_space'],'counter_blocks':['block.option_space'],'limitations':[]}}}
    return c


def test_loop_facets_use_explicit_routed_relations_not_single_group_representative():
    c=fixture(); html=render_workbench(_workbench([c]))['areas']['candidateArea']
    summary=html.split('<details class="routing-details"',1)[0]
    assert 'BACKEND_ONLY_SUMMARY' in summary
    neutral=summary.split('<h5>中性稳定回路</h5>',1)[1].split('<h5>空头回路</h5>',1)[0]
    assert '<p>ANCHOR_SUPPORT_SOURCE</p>' in neutral
    assert '<strong>反证</strong> GAMMA_COUNTER_SOURCE' in neutral
    assert 'data-routing-hash="routing-fixture-hash"' in html
    assert '来源与窗口' in html


def test_routed_observations_are_escaped_and_wrong_snapshot_does_not_leak_routes():
    c=fixture(); c['current_questions']['q1']['routing_observations_cn']=['<script>ROUTED_TEXT</script>']
    html=render_workbench(_workbench([c]))['areas']['candidateArea']
    assert '&lt;script&gt;ROUTED_TEXT&lt;/script&gt;' in html
    assert '<script>ROUTED_TEXT' not in html
    c['wpem_evidence']['evidence_routing']['binding']['snapshot_id']='previous-snapshot'
    html=render_workbench(_workbench([c]))['areas']['candidateArea']
    assert 'BACKEND_ONLY_SUMMARY' not in html
    assert 'Q1 ·' in html and 'Q2 ·' in html


def test_questions_precede_folded_research_without_removing_source_facets():
    html=render_workbench(_workbench([fixture()]))['areas']['candidateArea']
    opening='<details class="evidence-routing"'
    assert opening in html and '<details class="evidence-routing" open' not in html
    assert html.index('Q1 ·') < html.index(opening)
    assert html.index('Q2 ·') < html.index(opening)
    assert '展开本单证据关系 · 量价、结构与路径' in html
    assert 'ANCHOR_SUPPORT_SOURCE' in html and 'GAMMA_COUNTER_SOURCE' in html
    assert '来源不同不等于独立确认' in html


def test_context_readings_remain_reachable_with_original_clock_and_escaped_text():
    c=fixture()
    c['current_questions']['research_context']={'items':[{'label_cn':'Funding',
        'text_cn':'原资金费率 0.0000528；<原标签>', 'source_observed_ms':None,'asof_ms':1791194371908}],
        'independence_note_cn':'共享来源不重复增信'}
    html=render_workbench(_workbench([c]))['areas']['candidateArea']
    assert '原资金费率 0.0000528' in html and '&lt;原标签&gt;' in html
    assert '来源时点' in html and '资料时点' in html
    assert html.index('Q2 ·') < html.index('<details class="ia-research candidate-research-library"') < html.index('<div class="consumer-research"')
    assert '共享来源不重复增信' in html
    assert '<details class="consumer-research" open' not in html


def test_short_fragment_is_not_followed_by_contradictory_cross_section_only_text():
    c=fixture()
    c['wpem_evidence']['same_candidate_window']['status']='INSUFFICIENT'
    c['wpem_summary']['path_cn']='连续窗口尚不足，当前归因只使用截面事实。'
    c['current_questions']['q1']['window_attribution_cn']='已观察3点/5.82分钟；不足以概括完整路径。'
    html=render_workbench(_workbench([c]))['areas']['candidateArea']
    assert '已观察3点/5.82分钟' in html
    assert '当前归因只使用截面事实' not in html


def test_context_display_uses_complete_readings_instead_of_clipped_catalog_sentence():
    c=fixture()
    rows=[{'key':key,'current_close':value,'reference_close':value-1,
           'scoring_value':-4,'scoring_unit':'bps','current_ts_ms':1791194371908}
          for key,value in [('VOLQ',21.2),('DXY',102.127),('US10Y',5.277)]]
    c['current_questions']['research_context']={'items':[{'source_asset':'macro_pressure',
        'label_cn':'宏观','text_cn':'OLD_CLIPPED_TEXT','readings':{'component_summaries':rows}}]}
    html=render_workbench(_workbench([c]))['areas']['candidateArea']
    assert 'US10Y 5.277' in html and '原变化 -4 bps' in html
    assert 'OLD_CLIPPED_TEXT' not in html
    assert '原读数与来源' in html and '&quot;current_ts_ms&quot;' in html


def test_unknown_direction_enum_keeps_original_producer_label_readable():
    c=fixture()
    c['current_questions']['research_context']={'items':[{'source_asset':'active_flow_cvd_unqualified',
        'label_cn':'主动流','text_cn':'old text','readings':{'combined':{'direction':'neutral_to_bearish'},
        'fast_4h':{'data_ready':True,'cvd_sum':-469.011,'cvd_unit':'BTC','momentum_return_pct':-0.232},
        'slow_12h':{'data_ready':False}}}]}
    html=render_workbench(_workbench([c]))['areas']['candidateArea']
    assert '原方向 neutral_to_bearish' in html and 'CVD -469.011 BTC' in html
    assert '12h 原就绪 否' in html
