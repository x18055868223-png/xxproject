"""R1-R8: consumer metadata counterexamples over a legal synthetic candidate.

Window mutations here are isolated routing unit tests. Full native/synthetic
window qualification is exercised separately through the composer fixtures.
"""
from copy import deepcopy
import sys
from pathlib import Path
import pytest
sys.path.append(str(Path(__file__).resolve().parent))
from test_astra_underwriting_v20 import sample
from tools import astra_underwriting_v20 as core,astra_underwriting_fact_context_v20 as facts
from tools import astra_wpem_review_evidence_v1 as evidence,astra_evidence_routing_v1 as route


def fixture(direction='bearish'):
    raw=sample('put');now=raw['market']['valuation_ts_ms']
    source=facts._synthetic_preview_snapshot(now)
    flow=source['factor_snapshot']['flow']['micro_flow']
    for row in flow.values(): row['direction']=direction
    raw['fmz_fact_context']=facts.from_evaluation_snapshot(source,now,source_hash=core._hash(source))
    s=core.evaluate(raw);e=evidence.build_review_evidence(s,include_routing=False)
    return s,e


def asset(r,name):return next(u for u in r['units'] if u['source_asset']==name)
def support_blocks(r,loop):return r['loop_digest'][loop]['support_blocks']


def test_r1_anchor_gamma_walls_are_one_option_space_block():
    s,e=fixture();r=route.build_routing(s,e)
    option=[b for b in r['dependency_blocks'] if b['dependency_group']=='OPTION_SPACE']
    assert len(option)==1
    assert len(option[0]['evidence_ids'])>=3
    assert support_blocks(r,'neutral_stabilizing_loop')==[option[0]['block_id']]


def test_r2_safe_location_is_state_and_bearish_flow_is_candidate_counter():
    s,e=fixture();r=route.build_routing(s,e)
    assert asset(r,'responsibility_distance')['system_role']=='STATE'
    flow=asset(r,'active_flow_cvd')
    assert flow['q1_role']=='COUNTER' and flow['system_role']=='FEEDBACK'
    assert all(rel['loop']=='OBSERVATION_ONLY' for rel in asset(r,'responsibility_distance')['loop_relations'])


def test_r3_neutral_and_bearish_feedback_coexist_without_winner_or_global_counter():
    s,e=fixture();r=route.build_routing(s,e)
    assert support_blocks(r,'neutral_stabilizing_loop') and support_blocks(r,'bearish_loop')
    assert not support_blocks(r,'bullish_loop')
    assert 'block.flow' not in r['loop_digest']['bullish_loop']['counter_blocks']
    assert r['loop_digest']['cross_loop_conflicts']
    forbidden={'winner','loop_score','vote_count','confidence_pct','probability'}
    def check(v):
        if isinstance(v,dict):
            assert forbidden.isdisjoint(v)
            for x in v.values():check(x)
        elif isinstance(v,list):
            for x in v:check(x)
    check(r)


def test_r4_favorable_path_and_thinner_credit_remain_different_question_roles():
    s,e=fixture(); w=e['same_candidate_window']
    w['qualification']['status']='SYNTHETIC_WINDOW_COVERED'
    w['LevelDeltaPath']['path'].update(status='PATH_OBSERVED',classification='single_direction_favorable_relief')
    w['LevelDeltaPath']['delta'].update(status='STRICT_SAME_CANDIDATE_DELTA',changes={'visible_credit_btc_delta':-0.0001})
    r=route.build_routing(s,e)
    assert asset(r,'wpem_window_path')['q1_role']=='PRIMARY'
    assert asset(r,'wpem_delta')['q2_role']=='REASSESS'  # a change is not auto-classified as worse
    for name in ('fee','visible_credit','risk_reference'):
        assert asset(r,name)['q1_role'] not in {'PRIMARY','SUPPORT'}


def test_r5_macro_risk_off_is_cross_loop_disturbance_not_bearish_vote():
    s,e=fixture();s['fact_context']['market_facts']['macro_pressure'].update(data_status='OK',macro_regime='RiskOff')
    r=route.build_routing(s,e);u=asset(r,'macro_pressure')
    assert u['system_role']=='DISTURBANCE'
    assert all(x['loop'] in {'CROSS_LOOP','OBSERVATION_ONLY'} for x in u['loop_relations'])
    assert 'block.macro_risk' not in support_blocks(r,'bearish_loop')


def test_r6_unavailable_risk_is_not_market_counter_or_zero_mu():
    s,e=fixture();s['risk']['status']='BLOCKED';s['economics']['mu_ref_btc']=None
    before=deepcopy(s);r=route.build_routing(s,e)
    assert s==before and s['economics']['mu_ref_btc'] is None
    assert asset(r,'risk_reference')['q2_role']=='LIMITATION'
    assert all(x['loop']=='OBSERVATION_ONLY' for x in asset(r,'risk_reference')['loop_relations'])
    for key in ('bullish_loop','neutral_stabilizing_loop','bearish_loop'):
        assert 'block.risk_reference' not in r['loop_digest'][key]['support_blocks']+r['loop_digest'][key]['counter_blocks']


def test_r7_same_endpoint_different_path_changes_path_route_not_compensation():
    s,e=fixture();a=deepcopy(e);b=deepcopy(e)
    for doc,kind in ((a,'single_direction_favorable_relief'),(b,'repeated_cross_zone')):
        doc['same_candidate_window']['LevelDeltaPath']['path'].update(status='PATH_OBSERVED',classification=kind)
    x,y=route.build_routing(s,a),route.build_routing(s,b)
    assert asset(x,'wpem_window_path')['reason_cn']!=asset(y,'wpem_window_path')['reason_cn']
    assert {k:v for k,v in asset(x,'visible_credit').items() if k!='wpem_binding'} == {k:v for k,v in asset(y,'visible_credit').items() if k!='wpem_binding'}


def test_r8_candidate_rotation_rebinds_all_units_and_keeps_break():
    s,e=fixture();old=route.build_routing(s,e);s2=deepcopy(s);e2=deepcopy(e)
    s2['candidate_id']='rotated-candidate';s2['snapshot_id']='rotated-snapshot'
    e2.update(candidate_id=s2['candidate_id'],snapshot_id=s2['snapshot_id'])
    e2['same_candidate_window']['source_method_breaks']['candidate_rotation_observed']=True
    e2['same_candidate_window']['LevelDeltaPath']['path'].update(status='NO_PATH_DUE_TO_WINDOW_GAP')
    new=route.build_routing(s2,e2)
    assert set(u['evidence_id'] for u in old['units']).isdisjoint(u['evidence_id'] for u in new['units'])
    assert all(u['binding']['candidate_id']==s2['candidate_id'] for u in new['units'])
    assert asset(new,'wpem_candidate_rotation')['q1_role']=='REASSESS'


@pytest.mark.parametrize('state',['STALE','CONFLICT','UNKNOWN'])
def test_bad_option_quality_cannot_support_neutral_loop(state):
    s,e=fixture();f=s['fact_context']['market_facts']
    f['anchor']['gex_freshness']=state
    f['gamma_regime']['data_state']=state
    f['space_gate']['net_gamma'].update(valid=False,quality=state,freshness=state)
    f['space_gate']['state']='STRUCTURE_BLOCKED'
    r=route.build_routing(s,e)
    assert not support_blocks(r,'neutral_stabilizing_loop')
