"""Native producer integration, event issuance and absence of opinion leakage."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from test_astra_structural_board_v1 import inputs, CUT, E, M, H

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import astra_player_input_v1 as player
import astra_level_behaviour_v1 as level_engine


def add(case, ref, data):
    layer=ref.split('.')[0]
    rows=case['layers'].setdefault(layer, [])
    case['refs'][ref]={'layer':layer,'index':len(rows)}
    rows.append({'ref':ref,'quality':'ok','asof_ms':CUT-M,'values':data})


@pytest.fixture
def hand():
    put,pc=inputs(side='put');call,cc=inputs(side='call')
    for case in (put,call):
        player.entry(case,'L3.expiry_walls')['values']['atm_iv_pct']=45.0
        add(case,'L7.events',{'from_ms':CUT-M,'expiry_ms':E,'events':[]})
        add(case,'L5.volume_price',{'tmvf_24h':{'final_state':'LEAK_BULLISH','tmv_core':0.123456789},
                                  'micro_flow':{}})
    return put,call,pc,cc


def test_native_pair_complete_text_without_opinions_or_mutation(hand):
    old=copy.deepcopy(hand);text=player.build_input(*hand)
    assert hand==old
    assert all('## '+s in text for s in ['这是什么局','怎么读','规则','本局牌面','读完后另写','若已经持有'])
    assert '卖76,000、买74,000' in text and '卖84,000、买86,000' in text
    assert '**本期Pin**' in text and '2,000 USD' in text and '倍尺度' in text
    assert '首次上穿后1小时' in text
    for forbidden in ['TMV','LEAK_BULLISH','0.123456789','ETF','Schema','sha256','Vega','Theta']:
        assert forbidden not in text


@pytest.mark.parametrize('change',['missing_iv','wrong_expiry','missing_move'])
def test_scale_never_falls_back_to_realized_or_unbound_move(hand,change):
    for case in hand[:2]:
        w=player.entry(case,'L3.expiry_walls')['values']
        if change=='missing_iv':w.pop('atm_iv_pct')
        if change=='missing_move':w.pop('expected_move_usd')
        if change=='wrong_expiry':
            # Wrong-expiry volatility is not a scale, while structure stays native.
            w.pop('expected_move_usd')
            add(case,'L4.expiry_vol',{'expected_move_usd':777,'expiry_ms':E+H})
        w['realized_vol']=999
    text=player.build_input(*hand)
    assert '剩余预期波动尺度：未提供' in text and '倍尺度' not in text
    assert '999' not in text and '777' not in text


@pytest.mark.parametrize('at',[CUT,CUT+H,E])
def test_known_lifetime_event_prevents_dealing_not_reject(hand,at):
    player.values(hand[0],'L7.events')['events']=[{'at_ms':at,'kind':'CPI'}]
    with pytest.raises(player.HandNotDealt):player.build_input(*hand)


def test_past_later_or_unobserved_events_do_not_veto(hand):
    player.values(hand[0],'L7.events')['events']=[{'at_ms':CUT-1},{'at_ms':E+1}]
    assert player.build_input(*hand)
    advisory_text = player.build_input(*hand,events={'from_ms':CUT,'events':[{'at_ms':CUT+H,'kind':'CPI','name_cn':'CPI 公布','blocking':False}]})
    assert '到期前已知常规日程（供牌手审查）：CPI 公布' in advisory_text
    assert player.build_input(*hand,events={'from_ms':CUT+1,'events':[{'at_ms':CUT+H}]})
    with pytest.raises(player.HandNotDealt):
        player.build_input(*hand,events={'from_ms':CUT,'events':[{'at_ms':CUT+H,'kind':'known_interference'}]})


def test_card_mismatch_and_pair_time_mismatch_rejected(hand):
    hand[2]['contract_key']='wrong'
    with pytest.raises(ValueError,match='identity'):player.build_input(*hand)


def test_future_path_cut_and_whole_window_extrema(hand):
    path={'index_name':'btc_usd','expiry_ms':E,
          'points':[[t,80600] for t in range(CUT-24*H,CUT+1,M)]+[[CUT+M,999999]]}
    text=player.build_input(*hand,index_path=path)
    assert text.count('窗口最接近采样为'+player.stamp(CUT))==2
    assert text.count('指数80,600，距短腿')==2
    assert '999,999' not in text
    assert '近5分钟指数从80,600到80,600' in text


def test_partial_first_hour_and_zero_cross_touch_are_not_fabricated():
    path={'crossings':1,'first_cross_ms':CUT-10*M,'first_cross_dir':'down',
          'beyond_60m_usd':12,'back_within_60m':False}
    assert '不足1小时' in player.path_text(path,100,90,CUT,None)
    assert '没碰到' not in player.path_text({'crossings':0,'closest_usd':0},100,90,CUT,None)


def test_gap_flow_is_omitted_and_distant_kpf_not_rendered(hand):
    for card in hand[2:]:
        for lv in card['levels']:
            for f in lv['flow'].values():f['gap']=True
    add(hand[0],'L3.map.references',{'reference_pool':[
        {'role':'KPF_BASIN','raw_low':lo,'raw_high':hi,'observation_end_ms':CUT-M,
         'quality':'OK','coordinate_qualification':qualification,'usage_decision':{'can_use':True}}
        for lo,hi,qualification in [(79000,80000,'COMPARABLE'),(90000,91000,'COMPARABLE'),
                                     (79501,79502,'NOMINAL_ONLY')]]})
    text=player.build_input(*hand)
    assert '成交接受区79,000–80,000' in text and '90,000' not in text
    assert '79,501' not in text
    assert '普通Put净主动' not in text


def test_inside_liability_is_not_a_reverse_entry_buffer(hand):
    hand[2]['index_usd']=75000
    text=player.build_input(*hand)
    assert '现价已到达或进入短腿责任' in text
    put_section=text.split('3. **Put')[1].split('4. **Call')[0]
    assert '**78,000**' not in put_section


def test_cli_writes_complete_hand_and_never_writes_event_blocked_hand(hand,tmp_path):
    p,c,pc,cc=hand
    for name,data in [('put',p),('call',c)]:
        (tmp_path/(name+'.json')).write_text(json.dumps(data),encoding='utf-8')
    levels=tmp_path/'levels.jsonl';levels.write_text('\n'.join(json.dumps(x) for x in [pc,cc]),encoding='utf-8')
    out=tmp_path/'hand.md'
    cmd=[sys.executable,player.__file__,'--put-case',str(tmp_path/'put.json'),'--call-case',str(tmp_path/'call.json'),
         '--levels',str(levels),'--out',str(out)]
    result=subprocess.run(cmd,capture_output=True)
    assert result.returncode==0,result.stderr
    assert '## 规则' in out.read_text(encoding='utf-8')
    events=tmp_path/'events.json';events.write_text(json.dumps({'from_ms':CUT,'events':[{'at_ms':CUT+H}]}))
    blocked=tmp_path/'blocked.md'
    cmd[-1]=str(blocked)
    result=subprocess.run(cmd+['--events',str(events)],capture_output=True)
    assert result.returncode==2 and not blocked.exists()


def test_archive_entry_selects_native_candidates_without_manual_paths(hand,tmp_path):
    p,c,pc,cc=hand
    case_dir=tmp_path/'cases'/'2026-11-11';case_dir.mkdir(parents=True)
    level_dir=tmp_path/'levels';level_dir.mkdir()
    for side,case in [('put',p),('call',c)]:
        (case_dir/f"14_{side}_{case['case_id']}.json").write_text(json.dumps(case))
    (level_dir/'2026-11-12.jsonl').write_text('\n'.join(json.dumps(x) for x in [pc,cc]))
    out=tmp_path/'native.md'
    cmd=[sys.executable,player.__file__,'--archive',str(tmp_path),'--expiry-day','2026-11-12',
         '--round','r2','--out',str(out)]
    result=subprocess.run(cmd,capture_output=True)
    assert result.returncode==0,result.stderr
    assert '卖76,000、买74,000' in out.read_text(encoding='utf-8')


def rebuild(hand, points):
    """Keep cases/legs fixed; feed each synthetic observation through the real producer."""
    cards=[]
    for case in hand[:2]:
        c=player.values(case,'L1.contract');w=player.values(case,'L3.expiry_walls')
        cards.append(level_engine.build_card(expiry_ms=E,rnd='r2',side=c['side'],read_at_ms=CUT,
                     walls=w,points=points,case=case,contract=c))
    return (*hand[:2],*cards), {'index_name':'btc_usd','expiry_ms':E,'points':points}


def section(text,side):
    return text.split('3. **Put' if side=='put' else '4. **Call')[1].split('4. **Call' if side=='put' else '5. **')[0]


def test_zero_stations_short_pin_and_only_nearest_interior(hand):
    for case in hand[:2]:
        w=player.values(case,'L3.expiry_walls')
        w['top']={'oi_put':[{'strike':76000},{'strike':75000},{'strike':74500}],
                  'gamma_put':[{'strike':76000}]}
        w['walls']={'pin':76000}
    hand,path=rebuild(hand,[[t,80000] for t in range(CUT-24*H,CUT+1,M)])
    body=player.build_input(*hand,index_path=path)
    text=section(body,'put')
    assert '入区前没有已列主要位置' in text
    assert text.count('**短腿76,000**')==1 and text.count('上述Pin')==1
    assert body.count('**本期Pin**')==1 and '**本期Pin** 76,000' in body
    assert '不另算一站' in text and '本窗口采样未见进入责任区' in text
    assert '责任区内下一处75,000' in text and '74,500' not in text
    assert '不是入区前缓冲' in text


def test_multiple_stations_are_unique_and_short_not_counted_again(hand):
    w=player.values(hand[0],'L3.expiry_walls')
    w['top']['gamma_put']=[{'strike':79000}]
    w['walls']['pin']=79000
    hand,path=rebuild(hand,[[t,80600] for t in range(CUT-24*H,CUT+1,M)])
    text=section(player.build_input(*hand,index_path=path),'put')
    assert text.count('- **79,000**')==1 and text.count('- **78,000**')==1
    assert text.count('- **短腿76,000**')==1 and text.count('上述Pin')==1
    assert text.index('- **79,000**') < text.index('- **78,000**') < text.index('- **短腿76,000**')


@pytest.mark.parametrize('side,outside,inside',[('put',80000,75500),('call',80000,84500)])
def test_entered_then_returned_is_never_reported_as_never_entered(hand,side,outside,inside):
    points=[[t,inside if CUT-3*H<=t<CUT-H else outside] for t in range(CUT-24*H,CUT+1,M)]
    hand,path=rebuild(hand,points)
    text=section(player.build_input(*hand,index_path=path),side)
    short=text.split('- **短腿')[1]
    assert '曾在短腿责任内，其后有回到责任外的记录' in short
    assert '读取时指数在责任区外' in short and '未见进入责任区' not in short
    assert '最近一次进入的采样确认时间'+player.stamp(CUT-3*H) in short
    assert '最近一次返回责任外的采样确认时间'+player.stamp(CUT-H) in short
    assert '深入责任500 USD' in short


def test_entry_episode_depth_is_not_first_hour_depth(hand):
    points=[[t,80000 if t<CUT-3*H or t>=CUT-M else 75900 if t<=CUT-2*H else 75500]
            for t in range(CUT-24*H,CUT+1,M)]
    hand,path=rebuild(hand,points)
    text=section(player.build_input(*hand,index_path=path),'put').split('- **短腿')[1]
    assert '指数75,500，深入责任500 USD' in text
    assert '其下方100 USD' not in text and '首次下穿' not in text


def test_history_gap_or_missing_short_path_does_not_invent_never_entered(hand):
    points=[[CUT-24*H+M,80000],[CUT-2*M,80000],[CUT,80000]]
    hand,path=rebuild(hand,points)
    text=section(player.build_input(*hand,index_path=path),'put').split('- **短腿')[1]
    assert '24小时覆盖不足' in text and '不能据此认定全窗从未进入' in text
    assert '最近一次进入' not in text
    for card in hand[2:]:
        for level in card['levels']:level['before_24h']=None
    text=section(player.build_input(*hand),'put').split('- **短腿')[1]
    assert '短腿历史覆盖不足' in text and '读取时指数在责任区外' in text


def test_native_card_alone_preserves_known_entry_and_recovery(hand):
    points=[[t,75500 if CUT-3*H<=t<CUT-H else 80000] for t in range(CUT-24*H,CUT+1,M)]
    hand,_=rebuild(hand,points)
    text=section(player.build_input(*hand),'put').split('- **短腿')[1]
    assert '窗口曾在责任内；读取时已回到责任外' in text
    assert '未附指数序列，不补写最近进入/返回时刻或完整入区深度' in text


@pytest.mark.parametrize('side,k,inside',[('put',76000,75000),('call',84000,85000)])
def test_touching_short_is_not_entering_and_future_is_excluded(hand,side,k,inside):
    points=[[t,k if t==CUT-M else 80000] for t in range(CUT-24*H,CUT+1,M)]
    hand,path=rebuild(hand,points)
    path['points'].append([CUT+M,inside])
    text=section(player.build_input(*hand,index_path=path),side).split('- **短腿')[1]
    assert '本窗口采样未见进入责任区' in text and '最近一次进入' not in text
    assert '首次上穿' not in text and '首次下穿' not in text


def test_holding_same_liability_new_entry_can_be_compared_with_frozen_condition(hand):
    # Synthetic frozen condition: remain outside; a NEW observed entry after entry_at breaks it.
    entry_at=CUT-H
    points=[[t,75500 if CUT-12*H<=t<CUT-11*H or t>=CUT-10*M else 80000]
            for t in range(CUT-24*H,CUT+1,M)]
    hand,path=rebuild(hand,points)
    frozen_contract=copy.deepcopy(player.values(hand[0],'L1.contract'))
    text=player.build_input(*hand,index_path=path)
    short=section(text,'put').split('- **短腿')[1]
    assert '在责任区内（短腿与保护腿之间）' in text
    assert '最近一次进入的采样确认时间'+player.stamp(CUT-10*M) in short
    assert CUT-10*M > entry_at and CUT-12*H < entry_at
    assert player.values(hand[0],'L1.contract')==frozen_contract
    assert '旧窗口事实不算新增' in text and '原冻结标准' in text and 'Hold/Revoke' in text
    # Generator describes facts, never emits a holding judgement into the board.
    assert 'Revoke' not in section(text,'put')


@pytest.mark.parametrize('side,index,zone',[('put',73500,'已越过保护腿'),('call',86500,'已越过保护腿'),
    ('put',76000,'正处短腿边界'),('call',86000,'正处保护腿边界')])
def test_zone_boundaries_are_explicit(hand,side,index,zone):
    c=player.values(hand[0 if side=='put' else 1],'L1.contract')
    assert player.responsibility_zone(index,c)==zone


def test_flip_inside_call_is_not_a_pre_entry_obstacle(hand):
    add(hand[0],'L3.space.anchor',{'anchor':{'effective_flip_point':85000,'freshness':'FRESH'}})
    text=player.build_input(*hand)
    assert '对Call，轴在短腿与保护腿之间，不是入区前缓冲' in text


@pytest.mark.parametrize('pin,relation',[(75000,'在责任区内，距短腿1,000 USD'),
    (73000,'在保护腿之外，距短腿3,000 USD'),(76000,'与短腿76,000同位'),
    (74000,'与保护腿74,000同位'),(79000,'位于入区前路径'),(85000,'不在当前入区前路径')])
def test_pin_always_located_in_shared_background_even_without_entry_station(hand,pin,relation):
    for case in hand[:2]:
        w=player.values(case,'L3.expiry_walls');w['top']={};w['walls']={'pin':pin}
        # A different signal-layer Pin must never override this-expiry Pin.
        add(case,'L3.space.anchor',{'gamma_regime':{'pin_strike':99999}})
    body=player.build_input(*hand)
    background=body.split('2. **')[1].split('3. **')[0]
    assert '**本期Pin** '+player.fmt(pin) in background and relation in background
    assert 'Put：' in background and 'Call：' in background and '99,999' not in body
    assert body.count('**本期Pin**')==1


def test_pin_change_between_serial_readings_is_not_silently_overwritten(hand):
    player.values(hand[0],'L3.expiry_walls')['walls']['pin']=76000
    player.values(hand[1],'L3.expiry_walls')['walls']['pin']=84000
    body=player.build_input(*hand)
    assert 'Put读取的**本期Pin** 76,000' in body and 'Call读取的**本期Pin** 84,000' in body


def test_player_prompt_requires_both_sides_comparison_before_choice(hand):
    body=player.build_input(*hand)
    assert '两侧须按同一标准审完' in body and '双方都可建立时必须择优' in body
    assert '不能在先读一侧勉强可接受后停止' in body
    assert '到本次交割前对该责任有利与不利的具体路径' in body
    assert '最强反证' in body and '不编造优势或因平局弃权' in body
    assert '不新增独立Accept/Reject或退出触发' in body
    assert '期权Put净主动卖不等于标的下行净流' in body
    assert '不能确认最近5分钟同向运动' in body


def test_zero_spot_flow_does_not_create_a_buy_direction(hand):
    player.values(hand[0],'L5.volume_price')['micro_flow']={'fast_4h':{
        'data_ready':True,'cvd_unit':'BTC','cvd_sum':0.,'momentum_return_pct':0.}}
    text=player.build_input(*hand)
    assert '现货成交柱净主动成交持平' in text
    assert '净主动买0' not in text and '净主动卖0' not in text


@pytest.mark.parametrize('side,outer,inner',[('put',101,98),('call',99,102)])
def test_trace_touch_is_neither_entry_nor_return(side,outer,inner):
    trace=player.responsibility_trace([(M,outer),(2*M,100),(3*M,inner),(4*M,100),(5*M,outer)],side,100)
    assert trace['touches']==[2*M,4*M]
    assert trace['episodes']==[dict(entered_ms=3*M,observed_from_ms=3*M,peak_ms=3*M,
                                    peak_price=inner,depth_usd=2,returned_ms=5*M)]


def test_call_latest_entry_depth_does_not_use_initial_episode_or_down_cross(hand):
    points=[[t,87000 if t<CUT-20*H else 84500 if CUT-3*H<=t<CUT-H else 80000]
            for t in range(CUT-24*H,CUT+1,M)]
    hand,path=rebuild(hand,points)
    short=section(player.build_input(*hand,index_path=path),'call').split('- **短腿')[1]
    assert '该段进入时刻未覆盖' in short and '深入责任500 USD' in short
    assert '深入责任3,000' not in short and '首次下穿' not in short
    assert '最近一次进入的采样确认时间'+player.stamp(CUT-3*H) in short
    assert '最近一次返回责任外的采样确认时间'+player.stamp(CUT-H) in short
    native=section(player.build_input(*hand),'call').split('- **短腿')[1]
    assert '首次有记录的穿越为返回责任外' in native
    assert '入区深度' not in native.replace('完整入区深度','')


def test_nearest_approach_identity_and_endpoint_change_not_recovery_claim(hand):
    points=[[t,76500 if t in (CUT-10*H,CUT-2*H) else 80000] for t in range(CUT-24*H,CUT+1,M)]
    hand,path=rebuild(hand,points)
    short=section(player.build_input(*hand,index_path=path),'put').split('- **短腿')[1]
    assert '窗口最接近采样为'+player.stamp(CUT-2*H) in short
    assert '距短腿500 USD' in short and '扩大3,500 USD' in short
    assert '不表示此后单调远离或压力解除' in short
    path['points'].pop(-10)  # an interior gap hides possible later approach/entry
    incomplete=section(player.build_input(*hand,index_path=path),'put').split('- **短腿')[1]
    assert '24小时覆盖不足' in incomplete and '窗口最接近采样为' not in incomplete


def test_newer_reading_does_not_invent_recovery_time_after_last_path_sample(hand):
    points=[[t,84500 if t>=CUT-H else 80000] for t in range(CUT-24*H,CUT+1,M)]
    hand,path=rebuild(hand,points)
    hand[3]['index_usd']=83900
    short=section(player.build_input(*hand,index_path=path),'call').split('- **短腿')[1]
    assert '截至路径末点未见返回责任外' in short
    assert '末点至读取过程未覆盖' in short and '读取时指数在责任区外' in short
    assert '截至读取未返回' not in short


def test_guard_keeps_accept_and_distinguishes_candidate_from_exclusion(hand):
    body=player.build_input(*hand)
    assert '至少一侧责任路径条件可接受时，必须Accept并选侧' in body
    assert '只有Put和Call分别有足以排除其承担条件的具体不利事实，才Reject' in body
    assert '两侧都有具体不利事实才Reject' not in body
    assert '关键反证必须审查，但不自动排除该侧' in body
    assert '没有可接受侧且关键资料实质阻塞判断时，才Unknown' in body
    assert '关键反证候选不自动排除' in body and '充分排除后不能靠历史未进入' in body
    assert 'Put的下行/净卖与Call的上行/净买' in body
    assert '不等长窗口不能直接比净流绝对额' in body
    assert '小幅反弹、Pin或同位多个属性不能自动抵消' in body
    assert '不能偷改原规则' in body and 'UTC检查锚点及间隔' in body


def test_frozen_real_hand_time_identity_and_counterevidence_without_chosen_side():
    root=Path(__file__).resolve().parents[1]/'docs/rulebook/examples/player_input_liability/source'
    if not root.exists():pytest.skip('local frozen research archive is not distributed with code')
    cases=[player.read_json(next((root/'cases/2026-11-14').glob(f'*_{side}_*.json.gz'))) for side in ('put','call')]
    cards=[json.loads(x) for x in (root/'levels/2026-11-15.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
    cards=[next(c for c in cards if c['side']==side and c['round']=='r2') for side in ('put','call')]
    path=player.read_json(root/'paths/2026-11-15.json.gz')
    path['points'].append([max(c['read_at_ms'] for c in cards)+M,61300])
    body=player.build_input(*cases,*cards,index_path=path)
    put=section(body,'put');call=section(body,'call')
    assert '2026-11-14 04:14:00 UTC' in put and '距短腿507.7 USD' in put and '扩大231.7 USD' in put
    assert '2026-11-14 09:03:00 UTC' in call and '2026-11-14 12:59:00 UTC' in call
    assert '深入责任398.9 USD' in call and '深入责任2,469.7' not in call
    assert '61,300' not in body and '**本期Pin** 67,000' in body
    assert '净主动卖390.7 BTC' in body and '净主动卖1,186.1 BTC' in body
    assert '前8小时不重叠窗口' not in body and '未作12h减4h不重叠相减' in body
    assert '双as-of核验' in body and '合格图表末点66,739.4' in body
    assert '须明确作为当前已成立的反证' in body and '尚不能确认压力缓和或增强' not in body
    assert '69,469.7，已越过保护腿，该段进入时刻未覆盖' in call and '读取时指数在责任区外' in call
    assert '须纳入该侧最强反证审查' in body and '旧越界不单独构成Reject' in body
    assert 'Binance 现货量柱首末收盘价' in body and 'Deribit BTC/USD 指数分钟价' in body
    assert '2026-11-14 13:58:28 UTC' in body and '该时钟不等于首末柱时间' in body
    for legacy in ('judgement', 'lower_risk', '按对手价成交', '承保线'):
        assert legacy not in body


@pytest.mark.parametrize('incomplete', [False, True])
def test_historical_protection_touch_remains_visible(hand, incomplete):
    points = [[t, 86000 if t == CUT-H else 80000] for t in range(CUT-player.DAY, CUT+1, M)]
    if incomplete:
        points.pop(-10)
    hand, path = rebuild(hand, points)
    call = section(player.build_input(*hand, index_path=path), 'call')
    expected = '86,000，正处保护腿边界' if incomplete else '曾恰好触及保护腿'
    assert expected in call


def test_derive_non_overlapping_spot_windows_and_boundary(hand):
    case = copy.deepcopy(hand[0])
    add(case, 'L5.volume_price', {
        'spot_cvd_4h': 767.6,
        'spot_cvd_12h': 396.4,
        'spot_price_change_pct_4h': 1.43,
        'spot_price_change_pct_12h': 0.31,
        'micro_flow': {
            'fast_4h': {'data_ready': True, 'cvd_unit': 'BTC', 'cvd_sum': 767.6, 'momentum_return_pct': 1.43,
                        'source': 'binance', 'symbol': 'BTCUSDT', 'end_ts_ms': CUT},
            'slow_12h': {'data_ready': True, 'cvd_unit': 'BTC', 'cvd_sum': 396.4, 'momentum_return_pct': 0.31,
                         'source': 'binance', 'symbol': 'BTCUSDT', 'end_ts_ms': CUT},
        },
    })
    derived = player.derive_non_overlapping_spot_windows(player.entry(case, 'L5.volume_price'), cutoff=CUT)
    assert derived['comparable'] is True
    assert derived['previous_8h']['cvd_sum'] == pytest.approx(-371.2, abs=1e-6)
    expected_ret = ((1.0 + 0.0031) / (1.0 + 0.0143) - 1.0) * 100.0
    assert derived['previous_8h']['return_pct'] == pytest.approx(expected_ret, abs=1e-4)
    assert '由卖转买' in derived['direction_transition']['summary']

    # Missing completion clocks on both windows must block previous_8h derivation (D01)
    missing_clock = copy.deepcopy(hand[0])
    add(missing_clock, 'L5.volume_price', {
        'micro_flow': {
            'fast_4h': {'data_ready': True, 'cvd_unit': 'BTC', 'cvd_sum': 767.6, 'momentum_return_pct': 1.43, 'source': 'binance', 'symbol': 'BTCUSDT'},
            'slow_12h': {'data_ready': True, 'cvd_unit': 'BTC', 'cvd_sum': 396.4, 'momentum_return_pct': 0.31, 'source': 'binance', 'symbol': 'BTCUSDT'},
        },
    })
    no_clk = player.derive_non_overlapping_spot_windows(player.entry(missing_clock, 'L5.volume_price'), cutoff=CUT)
    assert no_clk['comparable'] is False and 'previous_8h' not in no_clk

    # Source mismatch blocks previous_8h derivation rather than guessing
    mismatch = copy.deepcopy(hand[0])
    add(mismatch, 'L5.volume_price', {
        'micro_flow': {
            'fast_4h': {'data_ready': True, 'cvd_unit': 'BTC', 'cvd_sum': 10.0, 'momentum_return_pct': 0.5, 'source': 'binance', 'end_ts_ms': CUT},
            'slow_12h': {'data_ready': True, 'cvd_unit': 'BTC', 'cvd_sum': 20.0, 'momentum_return_pct': 1.0, 'source': 'okx', 'end_ts_ms': CUT},
        },
    })
    bad = player.derive_non_overlapping_spot_windows(player.entry(mismatch, 'L5.volume_price'), cutoff=CUT)
    assert bad['comparable'] is False and 'previous_8h' not in bad

