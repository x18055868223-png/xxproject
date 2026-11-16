"""Offline reproduction of this revision; old frozen examples remain untouched."""
import argparse
import json
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
sys.path.insert(0,str(ROOT/'tools'))
import astra_anchor_relative_v1 as ard
import astra_player_input_v1 as player
import astra_player_condition_v1 as condition


def scenario_fixtures():
    """Explicit synthetic geometry; no synthetic number enters the real hand."""
    start=1794650400000;expiry=1794729600000;minute=60000
    def anchor(t,pin):
        return dict(schema='structure_snapshot@0.1',expiry_ms=expiry,at_ms=t,available_at_ms=t,
                    pin_source='synthetic fixture',pin_method='fixture-v1',pin_status='ok',profile={'walls':{'pin':pin}})
    width=dict(snapshot_exported_ms=start-minute,source_available_at_ms=start-minute,
               factor_snapshot={'anchor':dict(band_half=2,ready=True,freshness='FRESH',gex_source_ts_ms=start-minute)})
    cases=[
        ('稳定Pin反复返回',[100,104,100,96,100],[(0,100)],110,None),
        ('稳定Pin采样持续迁离',[101,102,103,104,105],[(0,100)],110,2),
        ('Pin与价格共同上移',[101,102,111,112,121],[(0,100),(3,110),(5,120)],130,None),
        ('Pin与Call短腿同位',[109,111,109,111,109],[(0,110)],110,None),
        ('Pin历史缺失',[100,101,100,99,100],[],110,None),
        ('历史回归与当前同向净卖须并读',[100,104,100,99,98],[(0,100)],110,-2),
    ]
    result=[]
    for name,prices,pins,call,flow in cases:
        history=[anchor(start+i*minute,p) for i,p in pins]
        path=dict(index_name='btc_usd',expiry_ms=expiry,points=[[start+(i+1)*minute,p] for i,p in enumerate(prices)])
        facts=ard.analyze(history,path,expiry_ms=expiry,cutoff_ms=start+5*minute,window_ms=5*minute,band_snapshot=width)
        result.append(dict(name=name,synthetic=True,put_legs=[95,85],call_legs=[call,call+10],
                           prices=prices,spot_flow_btc_same_5min=flow,ard=facts,text=ard.render(facts)))
    return result


def generate(out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    src=HERE.parent/'player_input_liability/source'
    cases=[player.read_json(next((src/'cases/2026-11-14').glob(f'*_{s}_*.json.gz'))) for s in ('put','call')]
    cards=[json.loads(l) for l in (src/'levels/2026-11-15.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
    cards=[next(c for c in cards if c['side']==s and c['round']=='r2') for s in ('put','call')]
    path=player.read_json(src/'paths/2026-11-15.json.gz')
    # Exact legacy projection frozen last round: same-run receipts, method unknown.
    history=[json.loads(l) for l in (HERE.parent/'player_ard/generated/pin_history.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
    a=player.build_input(*cases,*cards,index_path=path,include_ard=False)
    assert a==(HERE.parent/'player_first_order_guard/ai_input.md').read_text(encoding='utf-8')
    body=player.build_input(*cases,*cards,index_path=path,ard_history=history)
    facts=ard.analyze(history,path,expiry_ms=cards[0]['expiry_ms'],cutoff_ms=min(c['read_at_ms'] for c in cards))
    fixtures=scenario_fixtures()
    documents={'ai_input.md':body,'ard_facts.json':json.dumps(facts,ensure_ascii=False,indent=2)+'\n',
               'rule_delta.md':player.ARD_READING.strip()+'\n',
               'scenarios.json':json.dumps(fixtures,ensure_ascii=False,indent=2)+'\n',
               'scenarios.md':'# 合成情景：仅验收责任解释，不是实盘数据\n\n'+'\n\n'.join(
                   f"## {i}. {f['name']}\n\nPut {f['put_legs']}；Call {f['call_legs']}；同窗分钟价格{f['prices']}；"
                   f"同窗现货净主动流{f['spot_flow_btc_same_5min']} BTC（None表示未提供）。\n\n{f['text']}"
                   for i,f in enumerate(fixtures,1))+'\n'}
    for name,text in documents.items():
        with (out/name).open('x',encoding='utf-8',newline='\n') as stream:stream.write(text)
    # A separate, explicitly synthetic continuation proves the frozen interface.
    # Its numbers never enter ai_input.md or the real player reply.
    demo=out/'synthetic_hold';demo.mkdir(exist_ok=False)
    example=fixtures[0];before=example['ard'];start=before['start_ms'];entry=before['end_ms'];minute=60000
    frozen=demo/'condition.json'
    condition.freeze_condition(frozen,contract=dict(side='put',short_strike_usd=95,long_strike_usd=85,
        short_instrument='SYNTHETIC-P95',long_instrument='SYNTHETIC-P85',expiry_ms=before['expiry_ms']),
        entry_at_ms=entry,entry_index_usd=100,historical_facts=['合成：Pin100外出后两次返回，Put95未进入。'],
        accepted_conditions=['已观察的局部返回位于Put95责任外；不假定将来必然返回。'],
        invalidation='entry_not_returned',first_check_ms=entry+minute,check_interval_ms=minute,
        ard_basis=dict(facts=before,responsibility_meaning='依赖已观测的责任外局部返回作为有限背景，非阻断保证。',
                       review_on=['Pin改变、价格离开原100±2邻域并向95收缩余量时复核；仅原短腿破坏规则可机械判定。']))
    history=[dict(schema='structure_snapshot@0.1',expiry_ms=before['expiry_ms'],at_ms=t,available_at_ms=t,
                  pin_source='synthetic fixture',pin_method='fixture-v1',pin_status='ok',profile={'walls':{'pin':p}})
             for t,p in [(start,100),(entry+minute,98)]]
    width=dict(snapshot_exported_ms=start-minute,source_available_at_ms=start-minute,
               factor_snapshot={'anchor':dict(band_half=2,ready=True,freshness='FRESH',gex_source_ts_ms=start-minute)})
    prices=example['prices']+[97,94]
    path=dict(index_name='btc_usd',expiry_ms=before['expiry_ms'],points=[[start+(i+1)*minute,p] for i,p in enumerate(prices)])
    for i in (1,2):
        tick=entry+i*minute
        current=ard.analyze(history,path,expiry_ms=before['expiry_ms'],cutoff_ms=tick,window_ms=tick-start,band_snapshot=width)
        result=dict(synthetic=True,ard=condition.compare_ard_basis(frozen,current,tick),
                    boundary=condition.check_condition(frozen,path,tick))
        with (demo/f'check_{i}.json').open('x',encoding='utf-8') as stream:
            json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    return facts


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',required=True)
    f=generate(parser.parse_args().out)
    print(json.dumps(dict(valid_samples=f['valid_samples'],fixed_pin=f['fixed_reference']['anchor']['pin'],
                         fixed_end_relative=f['fixed_reference']['last']['relative']),ensure_ascii=False))
