"""Predeclared exploratory edge diagnostics on immutable historical artifacts."""
from __future__ import annotations
import argparse,json,math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import astra_control_study as c
from astra_second_report import read_csv,write_csv

SIDES=c.SIDES
RULES={
 'original_EDB':'direction','TMVF_direction':'tmv_direction','active_flow_direction':'flow_direction',
 'past_4h_price_momentum':'history_4h_sign','past_4h_price_reversal':'history_4h_sign',
 'Flip':'flip_position_sign','reverse_Flip':'flip_position_sign',
 'effective_Anchor':'anchor_position_sign','past_4h_midline':'price_midline_sign'}

def direction(x):
    if isinstance(x,(float,int)) and not isinstance(x,bool):return 1 if x>0 else -1 if x<0 else 0
    x=str(x).lower().replace('-','_')
    if x in ('bullish','neutral_to_bullish'):return 1
    if x in ('bearish','neutral_to_bearish'):return -1
    return 0

def choice(f,rule):
    if rule=='TMVF_direction' and f['tmv_data_ready'] is not True:return None
    if rule=='active_flow_direction' and f['flow_data_ready'] is not True:return None
    s=direction(f.get(RULES[rule]))
    if rule in ('past_4h_price_reversal','reverse_Flip'):s=-s
    return 'put_credit' if s>0 else 'call_credit' if s<0 else None

def extended(weighted):
    m=c.metrics(weighted)
    if not weighted:return m
    denom=sum(w*r['actual_width']/r['entry_price'] for r,w in weighted)
    payout=sum(w*r['payout_btc'] for r,w in weighted)
    m['aggregate_required_q']=payout/denom
    m['unweighted_mean_roi']=sum(w*r['net_pnl_btc']/r['margin_btc'] for r,w in weighted)/m['n']
    losses=[(r,w) for r,w in weighted if r['net_pnl_btc'] < -1e-12]
    wins=[(r,w) for r,w in weighted if r['net_pnl_btc'] > 1e-12]
    m['avg_win_roi']=sum(w*r['net_pnl_btc']/r['margin_btc'] for r,w in wins)/sum(w for r,w in wins) if wins else None
    m['avg_loss_roi']=-sum(w*r['net_pnl_btc']/r['margin_btc'] for r,w in losses)/sum(w for r,w in losses) if losses else None
    m['worst_five_payout_to_credit']=sum(r['payout_btc']*w for r,w in sorted(weighted,key=lambda rw:rw[0]['payout_btc']*rw[1],reverse=True)[:5])/sum(r['net_credit_btc']*w for r,w in weighted)
    return m

def group_pairs(rows):
    by=defaultdict(dict)
    for r in rows:
        assert r['side'] not in by[r['card_id']]
        by[r['card_id']][r['side']]=r
    assert all(set(p)==set(SIDES) for p in by.values())
    return by

def week(r):
    d=datetime.fromisoformat(r['delivery_date']).date().isocalendar()
    return f'{d.year}-W{d.week:02d}'

def comparisons(by,facts,rule,eligible=None,bootstrap=True):
    ids=[i for i in by if (eligible is None or i in eligible) and choice(facts[i],rule)]
    selected=[by[i][choice(facts[i],rule)] for i in ids]
    out={'rule':rule,'eligible_universe':len(by) if eligible is None else len(eligible),'selected_n':len(ids),
         'unselected_n':(len(by) if eligible is None else len(eligible))-len(ids),'selected':extended([(r,1) for r in selected]),'baselines':{}}
    for name in (*SIDES,'random_side_expectation'):
        pairs=[]
        for i,r in zip(ids,selected):
            controls=[(by[i][s],.5) for s in SIDES] if name=='random_side_expectation' else [(by[i][name],1)]
            pairs.append((r,controls))
        d=c.pair_metrics(pairs)
        d['control']=extended([v for r,vs in pairs for v in vs])
        if bootstrap:
            d['day_interval']=c.bootstrap(pairs,2000,20260914)
            d['week_interval']=c.bootstrap(pairs,2000,20260914,True)
            omitted=[]
            for w in sorted({week(r) for r,_ in pairs}):
                p=[(r,p) for r,p in pairs if week(r)!=w]
                if p:omitted.append({'omitted_week':w,**c.pair_metrics(p)})
            d['leave_week_out_roi_delta_range']=[min(x['roi_delta_pp'] for x in omitted),max(x['roi_delta_pp'] for x in omitted)] if omitted else None
        out['baselines'][name]=d
    out['selected_ids']=ids
    return out

def factor(f,r,name):
    if name=='conflict':return f['conflict_level'] or 'unknown'
    if name=='conflict_binary':
        return 'low_NONE_MILD' if f['conflict_level'] in ('NONE','MILD') else 'high_MATERIAL_SEVERE' if f['conflict_level'] in ('MATERIAL','SEVERE') else 'unknown'
    if name=='offline_grade':return r.get('offline_grade') or 'unrated'
    if name=='grade_B_vs_DC':return 'B' if r.get('offline_grade')=='B' else 'D_C' if r.get('offline_grade') in ('D','C') else r.get('offline_grade') or 'unrated'
    if name=='net_GEX_sign':
        v=f.get('board_net_gex_usd')
        return 'unknown' if not f['gex_record_usable'] or v is None else 'positive' if v>0 else 'negative' if v<0 else 'zero'
    if name=='wall_position':
        p=f['asof_price'];wall=f.get('put_wall' if r['side']=='put_credit' else 'call_wall')
        if not f['gex_record_usable'] or wall is None or p is None:return 'unknown'
        if (r['side']=='put_credit' and p<=wall) or (r['side']=='call_credit' and p>=wall):return 'price_already_beyond_wall'
        if r['short_strike']==wall:return 'short_at_wall'
        outside=r['short_strike']<wall if r['side']=='put_credit' else r['short_strike']>wall
        return 'short_beyond_wall' if outside else 'short_inside_wall'
    if name=='anchor_band_short':
        if not f['anchor_band_usable']:return 'unknown'
        edge=f['anchor_axis']+(-1 if r['side']=='put_credit' else 1)*f['anchor_half_width']
        outside=r['short_strike']<=edge if r['side']=='put_credit' else r['short_strike']>=edge
        return 'short_beyond_band' if outside else 'short_inside_band'
    if name=='TMV_CVD_relation':
        if not f['tmv_data_ready'] or not f['flow_data_ready']:return 'unknown'
        t=direction(f['tmv_direction']);v=direction(f['flow_direction']);side=1 if r['side']=='put_credit' else -1
        if not t or not v:return 'neutral_or_inactive'
        if t!=v:return 'opposed'
        return 'both_favorable' if t==side else 'both_adverse'
    raise ValueError(name)

def run(root,out):
    cfg=c.load(root/'.artifacts/astra-control-study-20260913/study_config.json')
    old=c.load(root/'.artifacts/astra-second-study-20260913/study_config.json')
    protocol=c.load(out/'protocol.json')
    for p,h in protocol['source_hashes'].items():assert c.sha(root/p)==h,p
    seal=c.load(out/'features_seal.json')
    for p,h in seal['hashes'].items():assert c.sha(out/Path(p.replace('\\','/')))==h,p
    facts={f['card_id']:f for f in read_csv(out/'features/signal_facts.csv')}
    rows=[c.decorate(r,cfg,old['sessions_bjt']) for r in read_csv(root/'.artifacts/astra-second-study-20260913/report/ordinary_joined.csv')]
    main=[r for r in rows if r['target_width']==2000 and r['credit_fraction']==.1]
    by=group_pairs(main);assert len(by)==114
    full=c.load(root/'.artifacts/astra-control-study-20260913/results/report.json')
    result={'schema':'astra_edge_results@1.0.0','protocol_sha':c.sha(out/'protocol.json'),
      'facts_seal_sha':c.sha(out/'features_seal.json'),'sample_role':protocol['sample_role'],
      'mapping':'Bullish/neutral-to-bullish=>Put; Bearish/neutral-to-bearish=>Call; neutral/unclear=>no choice. active_flow_direction denotes readable aggressive-flow direction, including EDB non-voting observations; not active voting.',
      'side_rules':[comparisons(by,facts,r) for r in RULES]}
    common=set(i for i in by if all(choice(facts[i],r) for r in ('Flip','effective_Anchor','past_4h_midline')))
    result['axis_identical_intersection']=[comparisons(by,facts,r,common) for r in ('Flip','reverse_Flip','effective_Anchor','past_4h_midline')]
    dates=sorted({r['delivery_date'] for r in main});start=datetime.fromisoformat(min(dates));end=datetime.fromisoformat(max(dates))
    # Three equal chronological calendar durations, not equal winning-trade groups.
    block=lambda r:min(2,int(3*(datetime.fromisoformat(r['delivery_date'])-start).total_seconds()/((end-start).total_seconds()+86400)))+1
    result['stability_boundaries']={'start':min(dates),'end':max(dates),'three_blocks':'equal calendar durations'}
    stability=[]
    for rule in RULES:
        for axis,fn in [('month',lambda r:r['delivery_date'][:7]),('third',block),('session',lambda r:r['session'])]:
            for label in sorted({fn(p['put_credit']) for p in by.values()},key=str):
                ids={i for i,p in by.items() if fn(p['put_credit'])==label}
                stability.append({'axis':axis,'label':label,**comparisons(by,facts,rule,ids,False)})
    result['stability']=stability
    tables=[]
    for name in ('conflict','conflict_binary','offline_grade','grade_B_vs_DC','net_GEX_sign','wall_position','anchor_band_short','TMV_CVD_relation'):
        for side in SIDES:
            whole=[r for r in main if r['side']==side]
            levels=sorted({factor(facts[r['card_id']],r,name) for r in whole})
            if name=='offline_grade':levels=list(dict.fromkeys([*levels,'A','S']))
            if name=='wall_position':levels=list(dict.fromkeys([*levels,'short_beyond_wall']))
            for label in levels:
                chosen=[r for r in whole if factor(facts[r['card_id']],r,name)==label]
                other=[r for r in whole if factor(facts[r['card_id']],r,name)!=label]
                tables.append({'factor':name,'side':side,'label':label,'coverage':len(chosen)/len(whole),
                  'selected':extended([(r,1) for r in chosen]),'unselected':extended([(r,1) for r in other]),
                  'months':{mo:extended([(r,1) for r in chosen if r['delivery_date'][:7]==mo]) for mo in sorted({r['delivery_date'][:7] for r in main})}})
    result['factor_groups']=tables
    sensitivity=[]
    for width in (1500,2000,2500):
        for q in (.05,.1,.2):
            sub=group_pairs([r for r in rows if r['target_width']==width and r['credit_fraction']==q])
            for rule in RULES:sensitivity.append({'target_width':width,'credit_fraction':q,**comparisons(sub,facts,rule,bootstrap=False)})
    result['sensitivity']=sensitivity
    ledger=[]
    for r in main:
        f=facts[r['card_id']]
        ledger.append({**r,**{f'choice_{rule}':choice(f,rule) for rule in RULES},
           **{f'factor_{n}':factor(f,r,n) for n in ('conflict_binary','net_GEX_sign','wall_position','anchor_band_short','TMV_CVD_relation')}})
    target=out/'analysis';target.mkdir(exist_ok=True)
    write_csv(target/'main_ledger.csv',ledger)
    write_csv(target/'factor_summary.csv',[{k:v for k,v in r.items() if not isinstance(v,(dict,list))}|{'selected_'+k:v for k,v in r['selected'].items()} for r in tables])
    c.save(target/'report.json',result)
    print(json.dumps({'n':len(by),'common_axis_n':len(common),'rules':[{'rule':r['rule'],'n':r['selected_n'],'win':r['selected']['win_rate'],'roi':r['selected']['capital_roi'],'required_q':r['selected'].get('aggregate_required_q'),'delta_vs_random_pp':r['baselines']['random_side_expectation']['roi_delta_pp'],'week_interval':r['baselines']['random_side_expectation']['week_interval']} for r in result['side_rules']]},ensure_ascii=False,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--workspace',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.workspace,a.output)
