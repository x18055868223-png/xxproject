import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import astra_player_condition_v1 as condition

M=60_000
T=1_800_000_000_000


def freeze(tmp_path, side='put', rule='entry_not_returned', entry_price=101, interval=2*M):
    contract=dict(side=side,short_strike_usd=100,long_strike_usd=90 if side=='put' else 110,
                  short_instrument=f'BTC-short-{side}',long_instrument=f'BTC-long-{side}',expiry_ms=T+60*M)
    target=tmp_path/'condition.json'
    condition.freeze_condition(target,contract=contract,entry_at_ms=T,entry_index_usd=entry_price,
                               historical_facts=['之前曾入区并返回，不当作新增'],accepted_conditions=[condition.RULES[rule]],
                               invalidation=rule,first_check_ms=T+2*M,check_interval_ms=interval)
    return target


def path(prices,old=None):
    return dict(index_name='btc_usd',expiry_ms=T+60*M,
                points=(old or [])+[[T+i*M,p] for i,p in enumerate(prices,1)])


@pytest.mark.parametrize('side,outer,inner',[('put',101,99),('call',99,101)])
def test_touch_entry_return_fixed_clock_and_no_mutation(tmp_path,side,outer,inner):
    target=freeze(tmp_path,side=side,entry_price=outer)
    original=target.read_bytes()
    assert condition.check_condition(target,path([100,100]),T+2*M)['status']=='not_triggered'
    assert condition.check_condition(target,path([inner,inner]),T+M)['status']=='not_due'
    assert condition.check_condition(target,path([inner,inner]),T+2*M)['status']=='triggered'
    assert condition.check_condition(target,path([inner,100]),T+2*M)['status']=='triggered'  # touch isn't outside
    returned=condition.check_condition(target,path([inner,outer]),T+2*M)
    assert returned['status']=='not_triggered' and returned['return_after_latest_entry_ms']==T+2*M
    assert '不自动等同' in returned['reason']
    assert target.read_bytes()==original
    with pytest.raises(FileExistsError):freeze(tmp_path,side=side,entry_price=outer)


def test_any_new_entry_rule_differs_from_unreturned_rule(tmp_path):
    target=freeze(tmp_path,rule='new_entry')
    result=condition.check_condition(target,path([99,101]),T+2*M)
    assert result['status']=='triggered' and result['return_after_latest_entry_ms']==T+2*M


def test_past_entry_and_future_low_never_count_as_new(tmp_path):
    target=freeze(tmp_path)
    data=path([101,101,50],old=[[T-3*M,99],[T-2*M,101]])
    before=copy.deepcopy(data)
    result=condition.check_condition(target,data,T+2*M)
    assert result['status']=='not_triggered' and result['latest_new_entry_ms'] is None
    assert data==before


def test_already_inside_at_entry_not_a_new_crossing(tmp_path):
    target=freeze(tmp_path,entry_price=99)
    assert condition.check_condition(target,path([99,99]),T+2*M)['status']=='not_triggered'
    result=condition.check_condition(target,path([101,99]),T+2*M)
    assert result['status']=='triggered' and result['latest_new_entry_ms']==T+2*M


@pytest.mark.parametrize('change',['missing','duplicate','offgrid','wrong_index','wrong_expiry','bad_price','absent_path','absent_points'])
def test_missing_or_incompatible_coverage_is_not_invented(tmp_path,change):
    target=freeze(tmp_path);data=path([99,101])
    if change=='missing':data['points'].pop(0)
    if change=='duplicate':data['points'].append(data['points'][0])
    if change=='offgrid':data['points'][0][0]+=1000
    if change=='wrong_index':data['index_name']='last_trade'
    if change=='wrong_expiry':data['expiry_ms']+=M
    if change=='bad_price':data['points'][0][1]=None
    if change=='absent_path':data=None
    if change=='absent_points':data.pop('points')
    assert condition.check_condition(target,data,T+2*M)['status']=='uncheckable'


def test_missing_original_clock_or_confirmation_never_guessed(tmp_path):
    target=freeze(tmp_path)
    for field in ('check_clock','sampling'):
        record=json.loads(target.read_text(encoding='utf-8'))
        record.pop(field,None)
        target.write_text(json.dumps(record),encoding='utf-8')
        assert condition.check_condition(target,path([99,99]),T+2*M)['status']=='uncheckable'


def test_check_after_expiry_does_not_continue_preexpiry_rule(tmp_path):
    target=freeze(tmp_path)
    assert condition.check_condition(target,path([99,99]),T+60*M)['status']=='uncheckable'


def test_initial_partial_minute_seed_is_supported(tmp_path):
    target=tmp_path/'partial.json'
    condition.freeze_condition(target,contract=dict(side='call',short_strike_usd=100,long_strike_usd=110,
        short_instrument='BTC-C100',long_instrument='BTC-C110',expiry_ms=T+60*M),
        entry_at_ms=T+27000,entry_index_usd=99,historical_facts=['初始责任外'],
        accepted_conditions=['检查点有新进入未返回则撤销'],invalidation='entry_not_returned',
        first_check_ms=T+M,check_interval_ms=M)
    assert condition.check_condition(target,path([101]),T+M)['status']=='triggered'
