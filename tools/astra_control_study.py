"""Frozen-data clock controls for Astra; no network, models or production writes."""
from __future__ import annotations
import argparse, bisect, csv, hashlib, json, math, random
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
import astra_light_study_analysis as base
import astra_second_study as second
from astra_second_report import read_csv, write_csv

BJT=timezone(timedelta(hours=8))
MINUTE=60000
HOUR=3600000
SIDES=('put_credit','call_credit')
SESSION_NAMES={'美盘后':'美股收盘后 04:00—08:00','亚盘':'亚盘 08:00—15:00',
 '欧盘':'欧盘 15:00—20:00','美盘前核心窗':'美盘前核心窗 20:00—21:30','美盘中':'美股交易时段 21:30—04:00'}

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def load(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def save(p,value):Path(p).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def iso(ms):return datetime.fromtimestamp(ms/1000,BJT).isoformat()
def valid_dte(h):return 8 < h <= 24
def tenor_bin(h):
    if not valid_dte(h):raise ValueError('outside ordinary tenor')
    return min(7,max(0,math.ceil((h-8)/2-1e-10)-1))

def grid_times(start,end,offset_minutes):
    step=30*MINUTE; offset=offset_minutes*MINUTE
    current=math.ceil((start-offset)/step)*step+offset
    while current<=end:
        if valid_dte((base._next_delivery_ms(current)-current)/HOUR):yield current
        current+=step

def near_distance_ms(ms,events):
    at=bisect.bisect_left(events,ms)
    return min(abs(ms-events[x]) for x in (at-1,at) if 0<=x<len(events))

def decorate(r,cfg,sessions):
    r=dict(r)
    r['entry_ms']=int(r['entry_ms']);r['expiry_ms']=int(r['expiry_ms'])
    r['margin_btc']=cfg['margin_lambda']*r['actual_width']/r['entry_price']
    r['tenor_bin']=tenor_bin(r['dte_hours'])
    r['dte_match_bin']=f"({8+2*r['tenor_bin']},{10+2*r['tenor_bin']}]"
    r['session']=SESSION_NAMES[second._session_bjt(r['entry_ms'],sessions)]
    r['short_distance_pct']=abs(r['short_strike']/r['entry_price']-1)*100
    r['delivery_date']=datetime.fromtimestamp(r['expiry_ms']/1000,BJT).date().isoformat()
    r['entry_date']=datetime.fromtimestamp(r['entry_ms']/1000,BJT).date().isoformat()
    r['entry_beijing']=iso(r['entry_ms']);r['expiry_beijing']=iso(r['expiry_ms'])
    return r

def sums(weighted):
    out=[0.0]*8
    for r,w in weighted:
        p=r['net_pnl_btc']; m=r['margin_btc']
        out[0]+=w;out[1]+=w*(p>1e-12);out[2]+=w*(p < -1e-12)
        out[3]+=w*p;out[4]+=w*m;out[5]+=w*m*r['dte_hours']/24
        out[6]+=w*r['net_credit_btc'];out[7]+=w*r['payout_btc']
    return out

def metrics(weighted):
    a=sums(weighted); n,wins,losses,pnl,margin,days,credit,payout=a
    if not n:return {'n':0,'unique_entries':0,'delivery_dates':0,'win_rate':None,'intrusion_rate':None,
       'capital_roi':None,'apr':None,'payoff_ratio':None,'avg_loss_btc':None,'total_payout_to_credit':None,
       'dte_mean':None,'short_distance_pct_mean':None,'pnl_btc':None,'margin_btc':None}
    pos=sum(w*max(r['net_pnl_btc'],0) for r,w in weighted)
    neg=sum(w*max(-r['net_pnl_btc'],0) for r,w in weighted)
    avgloss=neg/losses if losses else None
    return {'n':n,'unique_entries':len({r['entry_ms'] for r,w in weighted}),
      'delivery_dates':len({r['delivery_date'] for r,w in weighted}),
      'wins':wins,'losses':losses,'ties':max(0.0,n-wins-losses),'win_rate':wins/n,'intrusion_rate':losses/n,
      'capital_roi':pnl/margin,'apr':365*pnl/days,'pnl_btc':pnl,'margin_btc':margin,
      'payoff_ratio':(pos/wins)/avgloss if wins and avgloss else None,'avg_loss_btc':avgloss,
      'total_payout_to_credit':payout/credit if credit else None,
      'worst_roi':min(r['net_pnl_btc']/r['margin_btc'] for r,w in weighted),
      'dte_mean':sum(w*r['dte_hours'] for r,w in weighted)/n,
      'short_distance_pct_mean':sum(w*r.get('short_distance_pct',0) for r,w in weighted)/n}

def key(r):return (r['delivery_date'],r['tenor_bin'],r['side'],r['target_width'],r['credit_fraction'])
def pool_index(rows):
    result=defaultdict(list)
    for r in rows:result[key(r)].append(r)
    return result

def pair_group(signals, pools):
    pairs=[]; missing=[]
    for r in signals:
        controls=pools.get(key(r),[])
        if not controls:missing.append(r['card_id']);continue
        pairs.append((r,[(c,1/len(controls)) for c in controls]))
    return pairs,missing

def pair_metrics(pairs):
    signal=metrics([(r,1) for r,c in pairs])
    control=metrics([item for r,c in pairs for item in c])
    return {'signal':signal,'control':control,
      'win_delta_pp':100*(signal['win_rate']-control['win_rate']) if pairs else None,
      'roi_delta_pp':100*(signal['capital_roi']-control['capital_roi']) if pairs else None,
      'dte_delta_hours':signal['dte_mean']-control['dte_mean'] if pairs else None}

def quantile(a,p):
    a=sorted(a);at=(len(a)-1)*p;lo=int(at);hi=min(lo+1,len(a)-1)
    return a[lo]*(hi-at)+a[hi]*(at-lo) if hi!=lo else a[lo]

def bootstrap(pairs,draws,seed,by_week=False):
    blocks=defaultdict(lambda:[[0.0]*8,[0.0]*8])
    for r,controls in pairs:
        day=datetime.fromisoformat(r['delivery_date']).date()
        cluster=f'{day.isocalendar().year}-W{day.isocalendar().week:02d}' if by_week else r['delivery_date']
        for target,values in zip(blocks[cluster],(sums([(r,1)]),sums(controls))):
            for i,v in enumerate(values):target[i]+=v
    values=list(blocks.values());n=len(values)
    if n<2:return {'clusters':n,'win_delta_pp_interval':None,'roi_delta_pp_interval':None}
    rng=random.Random(seed);wd=[];rd=[]
    for _ in range(draws):
        ss=[0.0]*8;cc=[0.0]*8
        for _ in range(n):
            a,b=values[rng.randrange(n)]
            for i in range(8):ss[i]+=a[i];cc[i]+=b[i]
        wd.append(100*(ss[1]/ss[0]-cc[1]/cc[0]));rd.append(100*(ss[3]/ss[4]-cc[3]/cc[4]))
    return {'clusters':n,'draws':draws,'win_delta_pp_interval':[quantile(wd,.025),quantile(wd,.975)],
      'roi_delta_pp_interval':[quantile(rd,.025),quantile(rd,.975)]}

def grouping(signals):
    groups={'全部信号':signals}
    for session in SESSION_NAMES.values():groups[session]=[r for r in signals if r['session']==session]
    for d,label in [('BULLISH','原偏多'),('BEARISH','原偏空'),('NEUTRAL','原中性')]:
        groups[label]=[r for r in signals if r['direction']==d]
    groups['既有离线B级']=[r for r in signals if r.get('offline_grade')=='B']
    return groups

def compare(signals,controls,cfg,variant):
    pools=pool_index(controls); comparisons=[]; ledger=[]; daily=[]
    for label,group in grouping(signals).items():
        for side in SIDES:
            side_rows=[r for r in group if r['side']==side]
            if not side_rows:continue
            pairs,missing=pair_group(side_rows,pools)
            result={'variant':variant,'group':label,'side':side,'candidate_signals':len(side_rows),
                    'missing_controls':len(missing),'unmatched_card_ids':missing,**pair_metrics(pairs)}
            if label=='全部信号':
                result['by_delivery_day']=bootstrap(pairs,cfg['bootstrap']['draws'],cfg['bootstrap']['seed'])
                result['by_delivery_week']=bootstrap(pairs,cfg['bootstrap']['draws'],cfg['bootstrap']['seed'],True)
                for s,pool in pairs:
                    cm=metrics(pool)
                    daily.append({'variant':variant,'card_id':s['card_id'],'delivery_date':s['delivery_date'],
                        'side':side,'signal_win':s['net_pnl_btc']>1e-12,'control_expected_win':cm['win_rate'],
                        'signal_roi':s['net_pnl_btc']/s['margin_btc'],'control_capital_roi':cm['capital_roi'],
                        'signal_dte':s['dte_hours'],'control_mean_dte':cm['dte_mean']})
                    for c,w in pool:
                        ledger.append({'variant':variant,'signal_card_id':s['card_id'],'signal_entry_ms':s['entry_ms'],
                          'control_id':c['card_id'],'control_entry_ms':c['entry_ms'],'side':side,'weight':w,
                          'delivery_date':s['delivery_date'],'tenor_bin':s['tenor_bin'],
                          'signal_pnl_btc':s['net_pnl_btc'],'control_pnl_btc':c['net_pnl_btc']})
            comparisons.append(result)
    return comparisons,ledger,daily

def main_run(config_path,output_dir,first_dir,second_dir):
    cfg=load(config_path);prior=load(second_dir/'study_config.json')
    root=first_dir.parents[1]
    for relative,digest in cfg['source_hashes'].items():
        if sha(root/relative)!=digest:raise RuntimeError('Frozen source changed: '+relative)
    output_dir.mkdir(parents=True,exist_ok=True)
    samples=[json.loads(x) for x in (first_dir/'standard_signal_samples.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
    events=sorted(int(s['event_time_ms']) for s in samples)
    candles=base._index_klines(second._read_csv(first_dir/'market/klines.csv'))
    instruments=base._index_instruments(second._load_study_instruments(first_dir))
    deliveries=base._index_deliveries(load(first_dir/'market/delivery_prices.json'))
    signals=[decorate(r,cfg,prior['sessions_bjt']) for r in read_csv(second_dir/'report/ordinary_joined.csv')]
    source_signal_ids={r['card_id'] for r in signals}
    if len(source_signal_ids)!=114:raise RuntimeError('signal sample count')
    rows=[]; entry_ledger=[];exclusions=[]
    for offset in (cfg['main_grid_offset_minutes'],cfg['offset_sensitivity_minutes']):
        for entry in grid_times(cfg['entry_start_ms'],cfg['entry_end_ms'],offset):
            exp=base._next_delivery_ms(entry); price=second._open_price(candles,entry)
            delivery=deliveries.get(second._utc_date(exp))
            eid=f'CLOCK-{entry}-O{offset}'
            item={'card_id':eid,'entry_ms':entry,'entry_beijing':iso(entry),'expiry_ms':exp,'expiry_beijing':iso(exp),
                  'dte_hours':(exp-entry)/HOUR,'grid_offset_minutes':offset,'entry_price':price}
            item['nearest_event_minutes']=near_distance_ms(entry,events)/MINUTE
            item['near_event_30m']=item['nearest_event_minutes']<=30
            if price is None or delivery is None or exp>prior['original_cutoff_ms']:
                item['status']='missing_data_or_not_matured';exclusions.append(item);entry_ledger.append(item);continue
            sample={'card_id':eid,'direction':'NEUTRAL','version':'independent_clock'}
            trades,issues=second._scenario_rows_for_entry(sample=sample,entry_ms=entry,entry_price=price,
              expiry_ms=exp,delivery_price=delivery,instruments_by_expiry=instruments,config=prior,cohort='independent_clock')
            item['status']='complete' if len(trades)==18 else 'partial';item['trade_rows']=len(trades)
            entry_ledger.append(item)
            for problem in issues:exclusions.append({**item,**problem})
            for trade in trades:
                trade=decorate(trade,cfg,prior['sessions_bjt'])
                # NEUTRAL above only invokes the existing two-side payoff builder.
                # Clock entries have no signal direction or weekend tenor identity.
                trade['direction']='NOT_APPLICABLE'
                trade['relation']='fixed_put' if trade['side']=='put_credit' else 'fixed_call'
                trade['dte_bucket']=trade['dte_match_bin']
                trade['grid_offset_minutes']=offset;trade['nearest_event_minutes']=item['nearest_event_minutes']
                trade['near_event_30m']=item['near_event_30m'];rows.append(trade)
    is_main=lambda r:r['target_width']==2000 and r['credit_fraction']==.1
    sig_main=[r for r in signals if is_main(r)]
    controls_main=[r for r in rows if is_main(r)]
    variants={'主时钟':[r for r in controls_main if r['grid_offset_minutes']==0],
              '偏移15分钟':[r for r in controls_main if r['grid_offset_minutes']==15],
              '剔除事件前后30分钟':[r for r in controls_main if r['grid_offset_minutes']==0 and not r['near_event_30m']]}
    comparisons=[];links=[];paired=[]
    for label,controls in variants.items():
        c,l,p=compare(sig_main,controls,cfg,label);comparisons+=c;links+=l;paired+=p
    raw=[]
    for label,cohort in [('信号时点',sig_main),*variants.items()]:
        for side in SIDES:raw.append({'group':label,'side':side,**metrics([(r,1) for r in cohort if r['side']==side])})
    sensitivity=[]
    for width in cfg['target_width_usd']:
        for q in cfg['net_credit_fractions']:
            sub=[r for r in signals if r['target_width']==width and r['credit_fraction']==q]
            pool=pool_index([r for r in rows if r['grid_offset_minutes']==0 and r['target_width']==width and r['credit_fraction']==q])
            for side in SIDES:
                group=[r for r in sub if r['side']==side];p,miss=pair_group(group,pool)
                sensitivity.append({'target_width':width,'credit_fraction':q,'side':side,'matched_signals':len(p),
                  'missing_controls':len(miss),**pair_metrics(p)})
    directional=[r for r in sig_main if r['direction']!='NEUTRAL']
    dir_diagnostic=[]
    for label,pick in [('按原方向',[(r,1) for r in directional if (r['direction']=='BULLISH')==(r['side']=='put_credit')]),
                       ('同信号固定Put',[(r,1) for r in directional if r['side']=='put_credit']),
                       ('同信号固定Call',[(r,1) for r in directional if r['side']=='call_credit']),
                       ('两侧等权诊断',[(r,.5) for r in directional])]:
        dir_diagnostic.append({'group':label,**metrics(pick)})
    weekend=[]
    weekend_source=[r for r in read_csv(second_dir/'analysis/weekend_results.csv') if is_main(r) and r['is_matured']]
    # Weekend entries were already every Saturday; ignoring the source card does not change either fixed side.
    for r in weekend_source:
        r['margin_btc']=cfg['margin_lambda']*r['actual_width']/r['entry_price'];r['entry_ms']=int(r['entry_ms'])
        r['short_distance_pct']=abs(r['short_strike']/r['entry_price']-1)*100
    for side in SIDES:
        group=[r for r in weekend_source if r['side']==side]
        weekend.append({'group':'每周六固定入场，不读信号','side':side,**metrics([(r,1) for r in group])})
    wd=[r for r in weekend_source if r['direction']!='NEUTRAL']
    for label,group in [('周末按来源方向',[r for r in wd if (r['direction']=='BULLISH')==(r['side']=='put_credit')]),
                       ('同五轮固定Put',[r for r in wd if r['side']=='put_credit']),
                       ('同五轮固定Call',[r for r in wd if r['side']=='call_credit'])]:
        weekend.append({'group':label,'side':'direction_comparison',**metrics([(r,1) for r in group])})
    checks={'signal_cards':114,'signal_main_rows':len(sig_main),'control_grid_entries':dict(Counter(r['grid_offset_minutes'] for r in entry_ledger)),
       'control_trade_rows':len(rows),'source_hashes_verified':len(cfg['source_hashes']),
       'all_control_dte_valid':all(valid_dte(r['dte_hours']) for r in rows),'all_control_in_range':all(cfg['entry_start_ms']<=r['entry_ms']<=cfg['entry_end_ms'] for r in rows),
       'main_matched_by_side':{r['side']:r['signal']['n'] for r in comparisons if r['variant']=='主时钟' and r['group']=='全部信号'},
       'missing_price_or_contract_records':len(exclusions),'no_network_calls':True}
    if not checks['all_control_dte_valid'] or not checks['all_control_in_range']:raise RuntimeError('range check')
    report={'schema':'astra_signal_control_result@1.0.0','generated_at_utc':datetime.now(timezone.utc).isoformat(),
       'config_sha256':sha(config_path),'config':cfg,'acceptance':checks,'raw':raw,'matched':comparisons,
       'sensitivity':sensitivity,'direction_diagnostic':dir_diagnostic,'weekend':weekend,
       'signal_ids':sorted(source_signal_ids),'disclaimer':'observational matched contrast; not randomized causal attribution or historical quoted returns'}
    write_csv(output_dir/'control_entries.csv',entry_ledger);write_csv(output_dir/'control_all_scenarios.csv',rows)
    write_csv(output_dir/'control_main.csv',controls_main);write_csv(output_dir/'signal_main.csv',sig_main)
    write_csv(output_dir/'control_exclusions.csv',exclusions or [{'status':'none'}])
    write_csv(output_dir/'matched_links.csv',links);write_csv(output_dir/'paired_main.csv',paired);write_csv(output_dir/'raw_summary.csv',raw)
    flat=[]
    for r in comparisons:
        f={k:v for k,v in r.items() if not isinstance(v,(dict,list))}
        for name in ('signal','control'):
            f.update({name+'_'+k:v for k,v in r[name].items()})
        flat.append(f)
    write_csv(output_dir/'matched_summary.csv',flat)
    save(output_dir/'report.json',report);save(output_dir/'acceptance.json',checks)
    return checks

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--first-study',type=Path,required=True)
    parser.add_argument('--second-study',type=Path,required=True);args=parser.parse_args()
    print(json.dumps(main_run(args.config,args.output,args.first_study,args.second_study),ensure_ascii=False))
