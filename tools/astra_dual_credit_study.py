"""Pair frozen credit spreads into protected two-sided trades; no network calls."""
from __future__ import annotations
import argparse, math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import astra_control_study as c
from astra_second_report import read_csv, write_csv

PAIR_KEYS=('card_id','entry_ms','expiry_ms','target_width','credit_fraction')
COMMON=('card_id','entry_ms','expiry_ms','entry_price','delivery_price','dte_hours','target_width','credit_fraction')
EPS=1e-12

def close(a,b):
    if a is None or b is None:return a is b
    if isinstance(a,(float,int)) and isinstance(b,(float,int)):
        return math.isclose(a,b,rel_tol=1e-11,abs_tol=EPS)
    return a==b

def combine_pair(put,call,margin_lambda):
    if put['side']!='put_credit' or call['side']!='call_credit':raise ValueError('side mismatch')
    for field in COMMON:
        if not close(put.get(field),call.get(field)):raise ValueError('identity mismatch: '+field)
    mature=put.get('is_matured',True)
    if mature!=call.get('is_matured',True):raise ValueError('maturity mismatch')
    p0=put['entry_price']; st=put.get('delivery_price')
    if not math.isfinite(p0) or p0<=0 or margin_lambda<=0:raise ValueError('invalid price or margin')
    if not put['long_strike']<put['short_strike']<p0<call['short_strike']<call['long_strike']:
        raise ValueError('legs not ordered and strictly OTM')
    if mature and (st is None or not math.isfinite(st) or st<=0):raise ValueError('invalid settlement')
    out={k:put.get(k) for k in ('card_id','entry_ms','expiry_ms','entry_price','delivery_price','dte_hours',
        'target_width','credit_fraction','entry_beijing','expiry_beijing','delivery_date','entry_date',
        'direction','session','tenor_bin','dte_match_bin','grid_offset_minutes','near_event_30m',
        'nearest_event_minutes','source_card_age_hours','source_card_age_le_24h','observation_date_bjt')}
    out.update(side='dual_credit',is_matured=mature,quantity_per_side_btc=1,
        margin_basis='sum_of_two_single_case_calibrated_side_margins_not_exchange_PM',
        put_source_grade=put.get('offline_grade'),call_source_grade=call.get('offline_grade'))
    for prefix,r in (('put',put),('call',call)):
        width=abs(r['short_strike']-r['long_strike'])
        if not close(width,r['actual_width']):raise ValueError('actual width mismatch')
        credit=r['credit_fraction']*width/p0
        if not close(credit,r['net_credit_btc']):raise ValueError('credit mismatch')
        if mature:
            gross=(max(r['short_strike']-st,0)-max(r['long_strike']-st,0)) if prefix=='put' else (max(st-r['short_strike'],0)-max(st-r['long_strike'],0))
            payout=gross/st
            if not close(payout,r['payout_btc']) or not close(credit-payout,r['net_pnl_btc']):
                raise ValueError('inverse payoff mismatch')
        else:payout=None
        for k in ('short_instrument','long_instrument','short_strike','long_strike','actual_width'):
            out[prefix+'_'+k]=r.get(k)
        out[prefix+'_net_credit_btc']=credit
        out[prefix+'_payout_btc']=payout
        out[prefix+'_net_pnl_btc']=credit-payout if mature else None
        out[prefix+'_margin_btc']=margin_lambda*width/p0
    out['net_credit_btc']=out['put_net_credit_btc']+out['call_net_credit_btc']
    out['margin_btc']=out['put_margin_btc']+out['call_margin_btc']
    out['payout_btc']=out['put_payout_btc']+out['call_payout_btc'] if mature else None
    out['net_pnl_btc']=out['net_credit_btc']-out['payout_btc'] if mature else None
    out['result']='not_matured' if not mature else ('win' if out['net_pnl_btc']>EPS else 'loss' if out['net_pnl_btc'] < -EPS else 'breakeven')
    out['holding_return_on_margin']=out['net_pnl_btc']/out['margin_btc'] if mature else None
    out['combined_breakeven_intruded']=out['result']=='loss' if mature else None
    out['both_short_legs_otm_at_expiry']=(put['short_strike']<=st<=call['short_strike']) if mature else None
    out['one_side_loss_rescued']=mature and out['result']=='win' and min(out['put_net_pnl_btc'],out['call_net_pnl_btc']) < -EPS
    out['both_sides_win']=mature and min(out['put_net_pnl_btc'],out['call_net_pnl_btc'])>EPS
    if mature and out['put_payout_btc']>EPS and out['call_payout_btc']>EPS:raise ValueError('both tails pay at one expiry')
    return out

def pair_rows(rows,margin_lambda):
    groups=defaultdict(list);output=[];issues=[]
    for r in rows:groups[tuple(r.get(k) for k in PAIR_KEYS)].append(r)
    for key,group in sorted(groups.items()):
        try:
            if len(group)!=2 or {r['side'] for r in group}!={'put_credit','call_credit'}:
                raise ValueError('missing or duplicate side')
            sides={r['side']:r for r in group}
            output.append(combine_pair(sides['put_credit'],sides['call_credit'],margin_lambda))
        except ValueError as exc:issues.append({**dict(zip(PAIR_KEYS,key)),'reason':str(exc),'source_rows':len(group)})
    return output,issues

def measure(rows):
    valid=[r for r in rows if r.get('is_matured',True) and r.get('net_pnl_btc') is not None]
    m=c.metrics([(r,1) for r in valid]); m.pop('short_distance_pct_mean',None)
    m['pending']=len(rows)-len(valid)
    if valid and valid[0]['side']=='dual_credit':
        m['both_sides_win']=sum(r['both_sides_win'] for r in valid)
        m['one_side_loss_rescued']=sum(r['one_side_loss_rescued'] for r in valid)
        m['both_short_legs_otm_at_expiry']=sum(r['both_short_legs_otm_at_expiry'] for r in valid)
        m['mean_total_short_gap_pct']=sum((r['call_short_strike']-r['put_short_strike'])/r['entry_price']*100 for r in valid)/len(valid)
    return m

def main_run(workspace,output):
    cfg=c.load(output/'study_config.json')
    for path,digest in cfg['source_hashes'].items():
        if c.sha(workspace/path)!=digest:raise RuntimeError('frozen source changed: '+path)
    old=c.load(workspace/'.artifacts/astra-control-study-20260913/study_config.json')
    prior=c.load(workspace/'.artifacts/astra-second-study-20260913/study_config.json')
    controls=read_csv(workspace/'.artifacts/astra-control-study-20260913/results/control_all_scenarios.csv')
    signals=[c.decorate(r,old,prior['sessions_bjt']) for r in read_csv(workspace/'.artifacts/astra-second-study-20260913/report/ordinary_joined.csv')]
    weekends=read_csv(workspace/'.artifacts/astra-second-study-20260913/analysis/weekend_results.csv')
    for r in weekends:r['margin_btc']=cfg['margin_lambda']*r['actual_width']/r['entry_price']
    dc,ic=pair_rows(controls,cfg['margin_lambda']); ds,iss=pair_rows(signals,cfg['margin_lambda']);dw,iw=pair_rows(weekends,cfg['margin_lambda'])
    if ic or iss or iw:raise RuntimeError('pairing defects: '+str((ic+iss+iw)[:3]))
    main=lambda r:r['target_width']==2000 and r['credit_fraction']==.1
    sm=[r for r in ds if main(r)];wm=[r for r in dw if main(r)]
    cm=[r for r in dc if main(r) and r['grid_offset_minutes']==0]
    variants={'主时钟':cm,'偏移15分钟':[r for r in dc if main(r) and r['grid_offset_minutes']==15],
              '剔除事件前后30分钟':[r for r in cm if not r['near_event_30m']]}
    summary=[]
    for cohort,one,two in [('完整时钟',[r for r in controls if main(r) and r['grid_offset_minutes']==0],cm),
                           ('信号时点',[r for r in signals if main(r)],sm),('周末轮',[r for r in weekends if main(r)],wm)]:
        for side in ('put_credit','call_credit'):
            summary.append({'cohort':cohort,'strategy':side,**measure([r for r in one if r['side']==side])})
        summary.append({'cohort':cohort,'strategy':'dual_credit',**measure(two)})
    matches=[];links=[]
    for label,cc in variants.items():
        pairs,missing=c.pair_group(sm,c.pool_index(cc))
        result={'variant':label,'missing_ids':missing,**c.pair_metrics(pairs)}
        result['by_delivery_day']=c.bootstrap(pairs,2000,20260913)
        result['by_delivery_week']=c.bootstrap(pairs,2000,20260913,True)
        matches.append(result)
        for s,pool in pairs:
            for r,w in pool:links.append({'variant':label,'signal_id':s['card_id'],'control_id':r['card_id'],'weight':w,'delivery_date':s['delivery_date'],'tenor_bin':s['tenor_bin']})
    sensitivity=[]
    for width in (1500,2000,2500):
        for q in (.05,.1,.2):
            sub=lambda rows:[r for r in rows if r['target_width']==width and r['credit_fraction']==q]
            cc=[r for r in sub(dc) if r['grid_offset_minutes']==0];ss=sub(ds)
            pairs,missing=c.pair_group(ss,c.pool_index(cc))
            sensitivity.append({'target_width':width,'credit_fraction':q,'full_clock':measure(cc),'signal':measure(ss),
                                'weekend':measure(sub(dw)),'matched':c.pair_metrics(pairs),'missing_ids':missing})
    session=[]
    for label in c.SESSION_NAMES.values():
        ss=[r for r in sm if r['session']==label];cc=[r for r in cm if r['session']==label]
        session.append({'session':label,'comparison_basis':'raw_clock_time_groups_not_date_matched',
                        'signal':measure(ss),'full_clock':measure(cc)})
    neutral=[r for r in sm if r['direction']=='NEUTRAL']
    directional=[r for r in sm if r['direction']!='NEUTRAL']
    direction=[]
    for name,rows in [('中性48张',neutral),('有方向66张',directional)]:
        direction.append({'group':name,'strategy':'dual_credit',**measure(rows)})
    chosen=[r for r in signals if main(r) and r['direction']!='NEUTRAL' and ((r['direction']=='BULLISH')==(r['side']=='put_credit'))]
    direction.append({'group':'有方向66张','strategy':'original_direction',**measure(chosen)})
    output_results=output/'results';output_results.mkdir(exist_ok=True)
    write_csv(output_results/'dual_control_all_scenarios.csv',dc)
    write_csv(output_results/'dual_signal_all_scenarios.csv',ds)
    write_csv(output_results/'dual_weekend_all_scenarios.csv',dw)
    write_csv(output_results/'dual_main.csv',[dict(r,cohort=name) for name,rows in [('full_clock',cm),('signal',sm),('weekend',wm)] for r in rows])
    write_csv(output_results/'matched_links.csv',links);write_csv(output_results/'summary.csv',summary)
    write_csv(output_results/'pairing_issues.csv',ic+iss+iw or [{'status':'none'}])
    acceptance={'control_combinations':len(dc),'signal_combinations':len(ds),'weekend_combinations':len(dw),
        'main_clock_entries':len(cm),'main_signal_entries':len(sm),'weekend_entries':len(wm),'weekend_matured':sum(r['is_matured'] for r in wm),
        'pairing_issues':len(ic+iss+iw),'source_hashes_verified':len(cfg['source_hashes']),
        'no_network_or_llm_calls':True,'all_ordinary_dte_valid':all(c.valid_dte(r['dte_hours']) for r in dc+ds),
        'main_match_signal_count':matches[0]['signal']['n'],'main_match_unique_controls':matches[0]['control']['unique_entries']}
    assert (len(dc),len(ds),len(dw),len(cm),len(sm))==(48762,1026,117,2709,114)
    assert acceptance['weekend_matured']==12 and acceptance['all_ordinary_dte_valid']
    report={'schema':'astra_dual_credit_study@1.0.0','generated_at_utc':datetime.now(timezone.utc).isoformat(),
        'config_sha256':c.sha(output/'study_config.json'),'acceptance':acceptance,'summary':summary,
        'matched':matches,'sensitivity':sensitivity,'sessions':session,'direction':direction,
        'top_losses':sorted(cm,key=lambda r:r['holding_return_on_margin'])[:10],
        'weekend_main':wm}
    c.save(output_results/'report.json',report);c.save(output_results/'acceptance.json',acceptance)
    print(__import__('json').dumps({'acceptance':acceptance,'summary':summary,'matched':matches},ensure_ascii=False,indent=2))

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--workspace',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args();main_run(a.workspace,a.output)
