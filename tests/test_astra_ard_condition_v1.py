"""ARD is a frozen assessment attachment and review evidence, never an exit gate."""
import json
import importlib.util
import re
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
    if not script.exists():pytest.skip('local frozen-example reproduction is not distributed with code')
    spec=importlib.util.spec_from_file_location('responsibility_example',script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    src=module.HERE.parent/'player_input_liability/source'
    cases=[module.player.read_json(next((src/'cases/2026-11-14').glob(f'*_{s}_*.json.gz'))) for s in ('put','call')]
    cards=[json.loads(l) for l in (src/'levels/2026-11-15.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
    cards=[next(c for c in cards if c['side']==s and c['round']=='r2') for s in ('put','call')]
    path=module.player.read_json(src/'paths/2026-11-15.json.gz')
    history=[json.loads(l) for l in (module.HERE.parent/'player_ard/generated/pin_history.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
    r=module.ard.analyze(history,path,expiry_ms=cards[0]['expiry_ms'],cutoff_ms=min(c['read_at_ms'] for c in cards))
    assert r['fixed_reference']['anchor']['pin']==68000
    assert r['fixed_reference']['last']['relative']==pytest.approx(-1232.424333)
    assert r['last']['pin']==67000 and r['occupancy_pct'] is None
    first_order=module.player.build_input(*cases,*cards,index_path=path,include_ard=False)
    orig_first=(module.HERE.parent/'player_first_order_guard/ai_input.md').read_text(encoding='utf-8')
    reconciled_orig=(
        orig_first.split('## 本局牌面',1)[1].split('5. **',1)[0]
        .replace('指数66,767.6，','合格图表末点66,739.4（标签2026-11-14 13:59:00 UTC），')
        .replace('短腿距离767.6 USD（约0.61倍尺度）。','短腿距离739.4 USD（约0.58倍尺度）。')
        .replace(
            '短腿距离232.4 USD（约0.18倍尺度）。 已在责任内',
            '短腿距离260.6 USD（约0.21倍尺度）。 双as-of核验：Deribit btc_usd 图表分钟标签区间语义未证实，'
            '排除边界桶2026-11-14 14:00:00 UTC，图表路径仅截至合格分钟标签2026-11-14 13:59:00 UTC；'
            '不以未完结图表桶充当读取时当前指数或断言读取时区内外。 已在责任内',
        )
        .replace('翻转参考69,482.9，现价在其下方','翻转参考69,482.9，合格图表末点在其下方')
        .replace('在现价上方232.4 USD','在合格图表末点上方260.6 USD')
        .replace('\n入区前没有已列主要位置，直接审查短腿','\n合格图表末点在短腿责任外，入区前没有已列主要位置，直接审查短腿')
        .replace('至读取距离767.6 USD，较该采样扩大259.9 USD','至读取距离739.4 USD，较该采样扩大231.7 USD')
        .replace('近5分钟指数从66,759到66,767.6 USD','近5分钟指数从66,730.4到66,739.4 USD')
    )
    first_order_board=first_order.split('## 本局牌面',1)[1].split('5. **',1)[0]
    assert first_order_board==reconciled_orig
    body=module.player.build_input(*cases,*cards,index_path=path,ard_history=history)
    body_board=body.split('## 本局牌面',1)[1].split('5. **',1)[0]
    assert re.sub(r'\n\n\*\*锚相对动态ARD（.*?(?=\n\n3\. \*\*)','',body_board,count=1,flags=re.S)==first_order_board
    assert 'SYNTHETIC' not in body and '61,300' not in body
    fixtures=module.scenario_fixtures()
    assert len(fixtures)==6
    example=fixtures[0];before=example['ard'];start=before['start_ms'];entry=before['end_ms'];minute=60000
    frozen=tmp_path/'condition.json'
    module.condition.freeze_condition(frozen,contract=dict(side='put',short_strike_usd=95,long_strike_usd=85,
        short_instrument='SYNTHETIC-P95',long_instrument='SYNTHETIC-P85',expiry_ms=before['expiry_ms']),
        entry_at_ms=entry,entry_index_usd=100,historical_facts=['合成：Pin100外出后两次返回，Put95未进入。'],
        accepted_conditions=['已观察的局部返回位于Put95责任外；不假定将来必然返回。'],
        invalidation='entry_not_returned',first_check_ms=entry+minute,check_interval_ms=minute,
        ard_basis=dict(facts=before,responsibility_meaning='依赖已观测的责任外局部返回作为有限背景，非阻断保证。',
                       review_on=['Pin改变、价格离开原100±2邻域并向95收缩余量时复核；仅原短腿破坏规则可机械判定。']))
    synth_history=[dict(schema='structure_snapshot@0.1',expiry_ms=before['expiry_ms'],at_ms=t,available_at_ms=t,
        pin_source='synthetic fixture',pin_method='fixture-v1',pin_status='ok',profile={'walls':{'pin':p}})
        for t,p in [(start,100),(entry+minute,98)]]
    width=dict(snapshot_exported_ms=start-minute,source_available_at_ms=start-minute,
        factor_snapshot={'anchor':dict(band_half=2,ready=True,freshness='FRESH',gex_source_ts_ms=start-minute)})
    prices=example['prices']+[97,94]
    synth_path=dict(index_name='btc_usd',expiry_ms=before['expiry_ms'],points=[[start+(i+1)*minute,p] for i,p in enumerate(prices)])
    for i,expected in ((1,'not_triggered'),(2,'triggered')):
        tick=entry+i*minute
        current=module.ard.analyze(synth_history,synth_path,expiry_ms=before['expiry_ms'],cutoff_ms=tick,window_ms=tick-start,band_snapshot=width)
        ard_cmp=module.condition.compare_ard_basis(frozen,current,tick)
        boundary=module.condition.check_condition(frozen,synth_path,tick)
        assert boundary['status']==expected
        assert ard_cmp['frozen_anchor']['pin']==100 and ard_cmp['current_anchor']['pin']==98
        assert ard_cmp['current_inside_frozen_neighborhood'] is False
