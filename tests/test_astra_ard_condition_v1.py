"""ARD is a frozen assessment attachment and review evidence, never an exit gate."""
import json
import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import astra_player_condition_v1 as condition
import astra_anchor_relative_v1 as ard
from test_astra_anchor_relative_v1 import anchor,band,T,E,M


def facts(prices,history=None):
    return ard.analyze(history or [anchor()],dict(index_name='btc_usd',expiry_ms=E,
        points=[[T+i*M,p] for i,p in enumerate(prices,1)]),expiry_ms=E,
        cutoff_ms=T+len(prices)*M,window_ms=len(prices)*M,band_snapshot=band(2))


def freeze(tmp_path,entry_facts=None):
    entry_facts=entry_facts or facts([101,101])
    basis=dict(facts=entry_facts,responsibility_meaning='Pin100在Put95责任外，既有贴近仅作背景，不作阻断保证。',
               review_on=['Pin位置变化，或价格离开旧Pin邻域并向Put95收缩余量时复核；不自动退出。'])
    target=tmp_path/'frozen.json'
    condition.freeze_condition(target,contract=dict(side='put',short_strike_usd=95,long_strike_usd=85,
        short_instrument='P95',long_instrument='P85',expiry_ms=E),entry_at_ms=T+2*M,entry_index_usd=101,
        historical_facts=['入场前未进入Put95'],accepted_conditions=['原Pin仍只作责任外背景；不依赖其阻止下穿。'],
        invalidation='entry_not_returned',first_check_ms=T+3*M,check_interval_ms=M,ard_basis=basis)
    return target,basis


def test_freeze_reuses_existing_record_and_is_immutable(tmp_path):
    target,basis=freeze(tmp_path); original=target.read_bytes()
    basis['facts']['observations'][0]['pin']=999
    stored=json.loads(target.read_text(encoding='utf-8'))
    assert stored['ard_basis']['facts']['observations'][0]['pin']==100
    with pytest.raises(FileExistsError):freeze(tmp_path)
    assert target.read_bytes()==original


def test_migration_rechecks_old_anchor_without_new_revoke_rule(tmp_path):
    target,_=freeze(tmp_path);original=target.read_bytes()
    now=facts([101,101,98,97],[anchor(),anchor(T+3*M,98)])
    result=condition.compare_ard_basis(target,now,T+4*M)
    assert result['frozen_anchor']['pin']==100 and result['current_anchor']['pin']==98
    assert result['current_distance_to_frozen_pin_usd']==3  # not distance 1 to new Pin
    assert result['entry_responsibility_margin_usd']==6 and result['current_responsibility_margin_usd']==2
    assert result['pin_price_changed'] and not result['new_neighborhood_events']
    assert 'status' not in result and 'action' not in result
    path=dict(index_name='btc_usd',expiry_ms=E,points=[[T+3*M,98],[T+4*M,97]])
    assert condition.check_condition(target,path,T+4*M)['status']=='not_triggered'
    path['points'][-1][1]=94
    assert condition.check_condition(target,path,T+4*M)['status']=='triggered'
    assert target.read_bytes()==original


def test_old_return_is_not_new_and_new_departure_is_only_review_evidence(tmp_path):
    entry=facts([101,104,101]);entry['end_ms']=T+2*M  # inconsistent future sample
    with pytest.raises(ValueError):freeze(tmp_path,entry)
    target,_=freeze(tmp_path,facts([104,101]))
    now=facts([104,101,104,105])
    # 2-minute return is entry history; only the 3-minute leave is new.
    result=condition.compare_ard_basis(target,now,T+4*M)
    assert [(e['kind'],e['at_ms']) for e in result['new_neighborhood_events']]==[('leave',T+3*M)]


@pytest.mark.parametrize('change',['expiry','cutoff','off_clock','future_anchor'])
def test_followup_identity_time_and_check_clock_are_not_rechosen(tmp_path,change):
    target,_=freeze(tmp_path);now=facts([101,101,102]);check=T+3*M
    if change=='expiry':now['expiry_ms']+=M
    if change=='cutoff':now['end_ms']+=M
    if change=='off_clock':check+=1000
    if change=='future_anchor':now['observations'][-1]['ready']=check+1
    with pytest.raises(ValueError):condition.compare_ard_basis(target,now,check)


def test_missing_data_or_changed_measure_does_not_certify_behavior(tmp_path):
    target,_=freeze(tmp_path)
    now=facts([101,101,102]);now['band']=None
    result=condition.compare_ard_basis(target,now,T+3*M)
    assert result['new_neighborhood_events'] is None
    now['observations']=[];now['last']=None
    result=condition.compare_ard_basis(target,now,T+3*M)
    assert result['pin_price_changed'] is None and result['current_distance_to_frozen_pin_usd'] is None


def test_absent_ard_does_not_get_added_after_entry(tmp_path):
    target,_=freeze(tmp_path)
    record=json.loads(target.read_text(encoding='utf-8'));record.pop('ard_basis')
    target.write_text(json.dumps(record),encoding='utf-8')
    result=condition.compare_ard_basis(target,facts([101,101,102]),T+3*M)
    assert '不事后新增' in result['note']


@pytest.mark.parametrize('change',['expiry','future','invalid_pin','missing_history'])
def test_cannot_freeze_future_or_unrelated_ard(tmp_path,change):
    f=facts([101,101])
    if change=='expiry':f['expiry_ms']+=M
    if change=='future':f['end_ms']+=M
    if change=='invalid_pin':f['observations'][-1]['pin']=None
    if change=='missing_history':f['observations']=[]
    with pytest.raises(ValueError):freeze(tmp_path,f)


def test_real_hand_and_synthetic_continuation_stay_separate_and_reproducible(tmp_path):
    script=Path(__file__).resolve().parents[1]/'docs/rulebook/examples/player_ard_responsibility/reproduce.py'
    spec=importlib.util.spec_from_file_location('responsibility_example',script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    r=module.generate(tmp_path)
    assert r['fixed_reference']['anchor']['pin']==68000
    assert r['fixed_reference']['last']['relative']==pytest.approx(-1232.424333)
    assert r['last']['pin']==67000 and r['occupancy_pct'] is None
    body=(tmp_path/'ai_input.md').read_text(encoding='utf-8')
    assert 'SYNTHETIC' not in body and '61,300' not in body
    for n,expected in ((1,'not_triggered'),(2,'triggered')):
        result=json.loads((tmp_path/f'synthetic_hold/check_{n}.json').read_text(encoding='utf-8'))
        assert result['boundary']['status']==expected
        assert result['ard']['frozen_anchor']['pin']==100 and result['ard']['current_anchor']['pin']==98
        assert result['ard']['current_inside_frozen_neighborhood'] is False
    assert len(json.loads((tmp_path/'scenarios.json').read_text(encoding='utf-8')))==6
