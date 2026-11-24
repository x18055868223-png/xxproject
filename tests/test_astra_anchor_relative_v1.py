"""ARD facts: causal anchor/price alignment, frozen width, gaps and no voting."""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import astra_anchor_relative_v1 as ard
import astra_player_input_v1 as player
import astra_expiry_structure_v1 as structure
import astra_research_archive_v1 as archive

M=60_000
T=1_800_000_000_000
E=T+24*3_600_000


def anchor(at=T-M,pin=100,ready=None,method='native-v1'):
    return dict(schema='structure_snapshot@0.1',expiry_ms=E,at_ms=at,
                available_at_ms=at if ready is None else ready,
                pin_source=structure.PIN_SOURCE,pin_method=method,pin_status='ok',
                profile={'walls':{'pin':pin}})


def band(half=10,at=T-M):
    return dict(snapshot_exported_ms=at,source_available_at_ms=at,
                factor_snapshot={'anchor':dict(band_half=half,ready=True,band_clamped=False,
                                               freshness='FRESH',gex_source_ts_ms=at)})


def compute(prices,history=None,half=10):
    points=[[T+i*M,p] for i,p in enumerate(prices,1) if p is not None]
    path=dict(index_name='btc_usd',expiry_ms=E,points=points)
    return ard.analyze(history if history is not None else [anchor()],path,expiry_ms=E,
                       cutoff_ms=T+len(prices)*M,window_ms=len(prices)*M,
                       band_snapshot=band(half) if half else None)


def test_stable_anchor_repeated_departure_and_return():
    r=compute([100,112,100,88,100])
    assert r['full'] and r['occupancy_pct']==60 and r['relation']=='repeated_returns'
    assert [(x['kind'],x['at_ms']) for x in r['current_events']]==[
        ('leave',T+2*M),('return',T+3*M),('leave',T+4*M),('return',T+5*M)]


def test_stable_anchor_sampled_distance_keeps_increasing():
    r=compute([101,102,103,104,105],half=2)
    assert r['relation']=='sampled_distance_increasing' and r['occupancy_pct']==40
    assert r['current_events']==[dict(kind='leave',at_ms=T+3*M,segment=1,pin=100)]
    assert '不预测随后方向' in ard.render(r)


def test_old_return_does_not_hide_latest_unreturned_departure():
    r=compute([100,112,100,112,113])
    assert r['relation']=='departed_again'
    text=ard.render(r)
    assert '最近离开后，本可比片段采样未见返回' in text and '末个有效采样在邻域外' in text


def test_joint_migration_is_observed_displacement_not_causality():
    r=compute([101,102,111,112,121],[anchor(),anchor(T+3*M,110),anchor(T+5*M,120)],half=2)
    assert r['relation']=='same_direction_endpoint_changes'
    assert r['price_displacement_usd']==20 and r['pin_displacement_usd']==20
    assert r['relative_displacement_usd']==0 and not r['events']
    assert '不能证明Pin驱动价格' in ard.render(r)
    fixed=r['fixed_reference']
    assert fixed['anchor']['pin']==100 and fixed['frozen_at_ms']==T
    assert fixed['first']['relative']==1 and fixed['last']['relative']==21
    assert '参照不随Pin迁移' in ard.render(r)


def test_window_start_reference_never_uses_a_later_receipt():
    r=compute([101,102,103],[anchor(T,100,ready=T+M)])
    assert r['valid_samples']==3 and r['fixed_reference'] is None
    assert '窗初已知Pin' not in ard.render(r)


def test_late_older_observation_cannot_rewrite_displayed_migration():
    history=[anchor(),anchor(T+2*M,110),anchor(T+M,90,ready=T+3*M)]
    r=compute([101,111,112],history)
    assert r['last']['pin']==110 and r['observations'][-1]['pin']==110
    assert len(r['changes'])==1 and r['changes'][0]['to_pin']==110


def test_fixed_reference_keeps_actual_price_coverage_without_filling_gaps():
    r=compute([101,None,121],[anchor(),anchor(T+3*M,120)],half=2)
    assert r['fixed_reference']['valid_samples']==2
    assert r['fixed_reference']['last']['relative']==21 and r['last']['relative']==1
    assert r['expiry_ms']==E and r['index_name']=='btc_usd'


def test_anchor_jumping_to_price_is_not_price_return():
    r=compute([100,130,130],[anchor(),anchor(T+3*M,130)])
    assert [x['kind'] for x in r['events']]==['leave']
    assert not r['current_events'] and r['occupancy_pct']==pytest.approx(200/3)


def test_missing_anchor_does_not_mean_stable_and_never_backcasts_current_pin():
    r=compute([101,102],[])
    assert not r['valid_samples'] and r['occupancy_pct'] is None
    text=ard.render(r,current_pin=100)
    assert '迁移未确认' in text and '均为' not in text


def test_late_availability_and_future_rows_do_not_leak():
    original=[anchor()]
    expected=compute([101,102],original)
    late=anchor(T+M,300,ready=T+3*M)
    future=anchor(T+3*M,999)
    assert compute([101,102],original+[late,future])==expected
    r=compute([101,102,103],[anchor(),anchor(T+M,300,ready=T+2*M)])
    assert r['first']['pin']==100 and r['last']['pin']==300


def test_source_tick_time_alone_is_not_availability():
    bad=anchor(ready=T-2*M)
    r_bad=compute([101,102],[bad])
    assert r_bad['valid_samples']==0 and r_bad['unclocked_rows']==1
    legacy=anchor();legacy.pop('available_at_ms');legacy.pop('pin_source');legacy.pop('pin_method')
    r_leg=compute([101,102],[legacy])
    assert r_leg['valid_samples']==2 and r_leg['legacy_clock_backfill'] is True


@pytest.mark.parametrize('field',['expiry','index'])
def test_other_expiry_or_price_series_cannot_be_joined(field):
    a=anchor();path=dict(index_name='btc_usd',expiry_ms=E,points=[[T+M,101],[T+2*M,102]])
    if field=='expiry':a['expiry_ms']+=M
    else:path['index_name']='BTC-PERPETUAL'
    r=ard.analyze([a],path,expiry_ms=E,cutoff_ms=T+2*M,window_ms=2*M,band_snapshot=band())
    assert r['valid_samples']==0


def test_missing_price_resets_return_sequence_and_denominator():
    r=compute([100,130,None,100,100])
    assert r['valid_samples']==4 and r['expected_samples']==5 and not r['full']
    assert r['occupancy_pct']==75 and not any(x['kind']=='return' for x in r['events'])
    assert r['relation']=='coverage_or_method_limited'


def test_long_anchor_gap_is_not_filled_as_continuous_residence():
    r=compute([100]*50,[anchor(T,100)])
    assert r['valid_samples']==40 and r['occupancy_pct']==100 and not r['full']
    assert '40/50' in ard.render(r) and '持续扩大' not in ard.render(r)


def test_invalid_pin_snapshot_breaks_even_if_price_did_not_move():
    invalid=anchor(T+2*M);invalid['pin_status']='unavailable'
    r=compute([130,130,100,100],[anchor(),invalid,anchor(T+3*M)])
    assert r['valid_samples']==3 and not r['events']


def test_method_change_does_not_get_aggregated_into_comparable_rate():
    r=compute([130,100,100],[anchor(),anchor(T+2*M,100,method='changed-v2')])
    assert r['occupancy_pct'] is None and not r['method_comparable'] and not r['events']


@pytest.mark.parametrize('change',['future','missing','stale','old','late_receipt'])
def test_width_must_be_explicit_and_known_before_window(change):
    b=band();a=b['factor_snapshot']['anchor']
    if change=='future':b=band(at=T+M)
    if change=='missing':a.pop('band_half');a['normalized_deviation']=.1
    if change=='stale':a['freshness']='STALE'
    if change=='old':b=band(at=T-41*M)
    if change=='late_receipt':b['source_available_at_ms']=T+M
    assert ard.frozen_band(b,T) is None


def test_observed_capped_width_is_reused_without_a_new_veto():
    b=band();b['factor_snapshot']['anchor']['band_clamped']=True
    frozen=ard.frozen_band(b,T)
    assert frozen['half_width_usd']==10 and frozen['band_clamped'] is True


def test_no_width_still_outputs_anchor_and_relative_migration():
    r=compute([101,102,103],half=None)
    assert r['last']['relative']==3 and r['occupancy_pct'] is None
    assert '不计算停留率或邻域离返' in ard.render(r)


def test_anchor_out_and_back_between_minutes_breaks_the_price_event():
    r=compute([130,100],[anchor(),anchor(T+M+1000,200),anchor(T+M+2000,100)])
    assert not r['events']


def test_conflicting_same_time_snapshots_do_not_pick_a_winner():
    r=compute([100,100],[anchor(),anchor(pin=200)])
    assert not r['valid_samples']


def test_old_mode_keeps_first_order_path_and_new_mode_uses_closeout_rules():
    from test_astra_structural_board_v1 import inputs
    put,pc=inputs(side='put');call,cc=inputs(side='call')
    for case in (put,call):player.values(case,'L3.expiry_walls')['atm_iv_pct']=45.
    a=player.build_input(put,call,pc,cc,include_ard=False)
    b=player.build_input(put,call,pc,cc)
    ard_start=b.index('\n\n**锚相对动态ARD')
    ard_end=b.index('\n\n3. **',ard_start)
    assert b[ard_end:b.index('\n\n5. **')]==a[a.index('\n\n3. **'):a.index('\n\n5. **')]
    assert 'ARD责任判读' not in a and '两侧都有具体不利事实才Reject' in a
    assert 'Pin位于' not in b[ard_start:ard_end]  # responsibility relation stays in existing Pin context
    assert '不新增Accept/Reject/Revoke门槛' in b and '同源，不重复计票' in b
    assert '分别说明它支持、削弱或限制哪项承担条件' in b
    assert '与同窗量价有冲突须明说' in b and '未增加可辨识的责任信息' in b
    assert '不换锚维护原理由' in b and '不自动Reject或Unknown' in b


@pytest.mark.parametrize('pin,relation',[(84000,'与短腿84,000同位'),(90000,'在保护腿之外')])
def test_ard_keeps_real_responsibility_relation_for_same_strike_or_far_pin(pin,relation):
    from test_astra_structural_board_v1 import inputs,CUT,E as expiry,H
    put,pc=inputs(side='put');call,cc=inputs(side='call')
    for c in (put,call):
        player.values(c,'L3.expiry_walls')['walls']['pin']=pin
        player.values(c,'L3.expiry_walls')['atm_iv_pct']=45.
    rows=[{**anchor(t,pin),'expiry_ms':expiry} for t in range(CUT-4*H,CUT+1,30*M)]
    path={'index_name':'btc_usd','expiry_ms':expiry,'points':[[t,80600] for t in range(CUT-4*H,CUT+1,M)]}
    body=player.build_input(put,call,pc,cc,index_path=path,ard_history=rows)
    assert relation in body and '**锚相对动态ARD' in body
    facts=body.split('**锚相对动态ARD')[1].split('\n\n3. **')[0]
    assert 'Accept' not in facts and 'Reject' not in facts and '安全' not in facts


def test_native_producer_persists_clock_source_and_method_before_any_lock(tmp_path,monkeypatch):
    from test_astra_expiry_structure_v1 import FakeDeribit,NOW
    store=archive.Archive(tmp_path);state=store.load_state()
    monkeypatch.setattr(archive.time,'time',lambda:(NOW+5000)/1000)
    written,_=archive.collect_structure(store,state,archive.Deribit(FakeDeribit()),NOW)
    assert written==2 and 'contracts' in state and not state['contracts']
    rows=[json.loads(line) for p in (tmp_path/'structure').glob('*.jsonl') for line in p.read_text().splitlines()]
    assert all(r['at_ms']==NOW and r['available_at_ms']==NOW+5000 for r in rows)
    assert all(r['pin_source']==structure.PIN_SOURCE and r['pin_method']==structure.PIN_METHOD for r in rows)
    assert all(r['pin_status'] in ('ok','unavailable') for r in rows)
    assert store.verify()['ok']


def test_legacy_compatibility_requires_same_successful_run_and_never_forges_method():
    script=Path(__file__).resolve().parents[1]/'docs/rulebook/examples/player_ard/reproduce.py'
    if not script.exists():pytest.skip('local frozen-example reproduction is not distributed with code')
    spec=importlib.util.spec_from_file_location('ard_example',script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    row=anchor();row.pop('available_at_ms');row.pop('pin_method')
    run=dict(service_started_ms=T-2*M,receipt_ms=T+M,invocation='observed-run',structure_records=2,errors=[])
    projected=module.legacy_history([row],[run])[0]
    assert projected['available_at_ms']==T+M and projected['pin_method'] is None
    assert projected['compat_backfill_applied'] is True and 'available_at_ms' not in row
    assert 'available_at_ms' not in module.legacy_history([row],[{**run,'errors':[{'step':'structure'}]}])[0]
    assert 'available_at_ms' not in module.legacy_history([row],[{**run,'service_started_ms':T}])[0]
    r=ard.analyze([projected],{'index_name':'btc_usd','expiry_ms':E,'points':[[T,100]]},
                   expiry_ms=E,cutoff_ms=T,window_ms=M)
    assert r['valid_samples']==0  # old observation with a later receipt is not yet known


def test_real_ard_replays_identical_A_without_hindsight(tmp_path):
    script=Path(__file__).resolve().parents[1]/'docs/rulebook/examples/player_ard_closeout/reproduce.py'
    if not script.exists():pytest.skip('local frozen-example reproduction is not distributed with code')
    spec=importlib.util.spec_from_file_location('ard_example',script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    history_path=module.HERE.parent/'player_ard/generated/pin_history.jsonl'
    history=[json.loads(line) for line in history_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    cases=[module.player.read_json(next((module.SOURCE/'cases/2026-11-14').glob(f'*_{side}_*.json.gz'))) for side in ('put','call')]
    cards=[json.loads(line) for line in (module.SOURCE/'levels/2026-11-15.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    cards=[next(c for c in cards if c['side']==side and c['round']=='r2') for side in ('put','call')]
    path=module.player.read_json(module.SOURCE/'paths/2026-11-15.json.gz')
    r=ard.analyze(history,path,expiry_ms=cards[0]['expiry_ms'],cutoff_ms=min(c['read_at_ms'] for c in cards))
    prev_facts=json.loads((module.HERE.parent/'player_ard_responsibility/generated/ard_facts.json').read_text(encoding='utf-8'))
    assert json.loads(json.dumps(r))==prev_facts
    assert r['valid_samples']==r['expected_samples']==240
    assert r['observations'][0]['pin']==68000 and r['observations'][-1]['pin']==67000
    assert r['changes'][-1]['observed_at_ms']==1794652225446
    assert r['pin_displacement_usd']==-1000 and r['price_displacement_usd']==pytest.approx(-568.255)
    assert r['band'] is None and r['occupancy_pct'] is None and not r['method_comparable']
    first_order=module.player.build_input(*cases,*cards,index_path=path,include_ard=False)
    orig_first=(module.HERE.parent/'player_first_order_guard/ai_input.md').read_text(encoding='utf-8')
    # Reconcile pre-5a43b21d (163ff9b3) historical frozen wording with 5a43b21d/6311071c dual-as-of chart-endpoint baseline.
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
    (tmp_path/'ai_input.md').write_text(body,encoding='utf-8')
    assert '61,300' not in body
    assert 'Pin输出位置由68,000变为67,000' in body
    assert '不能确认是同口径结构迁移' in body
    # Dual-as-of chart_points_audit excludes the unverified 14:00:00 UTC boundary minute -> 239/240 qualified minutes
    assert '有效239/240个分钟样本' in body and '覆盖数不是独立结构更新次数' in body
    assert '窗初已知Pin 68,000固定参照' in body
