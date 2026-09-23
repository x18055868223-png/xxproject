"""Outcome-free facts for edge identification. Only closed historical bars enter features."""
from __future__ import annotations
import argparse,bisect,json,math
from collections import Counter
from pathlib import Path
import astra_control_study as prior
from astra_second_report import read_csv,write_csv

MINUTE=60000
def finite(x):
    if x is None or isinstance(x,bool):return None
    try:v=float(x);return v if math.isfinite(v) else None
    except (ValueError,TypeError):return None
def sign(x):return None if x is None else 1 if x>0 else -1 if x<0 else 0

class HistoricalBars:
    def __init__(self,rows):
        self.rows=sorted(rows,key=lambda r:r['open_time_ms'])
        self.ends=[int(r['close_time_ms']) for r in self.rows]
        self.opens={int(r['open_time_ms']):float(r['open']) for r in self.rows}
    def window(self,asof_ms,n=240):
        i=bisect.bisect_right(self.ends,asof_ms)
        if i<n:return {'history_4h_status':'insufficient','history_last_close_ms':self.ends[i-1] if i else None}
        rs=self.rows[i-n:i]
        if any(int(b['open_time_ms'])-int(a['open_time_ms'])!=MINUTE for a,b in zip(rs,rs[1:])):
            return {'history_4h_status':'gap','history_last_close_ms':self.ends[i-1]}
        if asof_ms-self.ends[i-1]>MINUTE:return {'history_4h_status':'stale','history_last_close_ms':self.ends[i-1]}
        p0=float(rs[0]['open']);last=float(rs[-1]['close']);high=max(float(r['high']) for r in rs);low=min(float(r['low']) for r in rs)
        values=[p0]+[float(r['close']) for r in rs]
        log_rets=[math.log(b/a) for a,b in zip(values,values[1:])]
        variation=sum(abs(b-a) for a,b in zip(values,values[1:]))
        return {'history_4h_status':'available','history_first_open_ms':int(rs[0]['open_time_ms']),
            'history_last_close_ms':self.ends[i-1],'history_4h_n':n,
            'history_4h_return':last/p0-1,'history_4h_rv':math.sqrt(sum(x*x for x in log_rets)),
            'history_4h_range_fraction':high/low-1,'history_4h_midpoint':(high+low)/2,
            'history_4h_efficiency':abs(last-p0)/variation if variation else 0.0}

def features_for_signal(s,raw,bars):
    asof=int(s['event_time_ms']);entry=(asof//MINUTE+1)*MINUTE
    factors=raw.get('factor_cross_section',{});anchor=factors.get('anchor',{});conflict=raw.get('conflict',{})
    tmv=factors.get('tmvf',{});gex=factors.get('gex_info',{})
    common=s.get('raw_common_facts',{})
    tmv24=tmv.get('tmvf_24h',{}).get('data_ready')
    tmv48=tmv.get('tmvf_48h',{}).get('data_ready')
    ggr_pin=finite(common.get('gamma_regime',{}).get('pin_strike'))
    board=common.get('gex_info',{})
    flow_votes=[e for e in raw.get('reasoning',{}).get('evidence',[]) if str(e.get('key','')).upper().startswith(('CVD','FLOW'))]
    magnet=finite(board.get('magnet_price'))
    magnet_source='magnet_price'
    if magnet is None:
        magnet=finite(board.get('magnet_level'));magnet_source='magnet_level'
    d={k:s.get(k) for k in ('card_id','episode_id','version','direction','card_price','anchor_axis','anchor_half_width',
        'anchor_band_clamped','anchor_normalized_deviation','anchor_freshness','gamma_regime','board_net_gex_usd',
        'board_gex_stale','call_wall','put_wall','gamma_flip','pin_strike','tmv_direction','tmv_blend','flow_direction',
        'flow_data_ready','flow_4h_cvd_norm','flow_12h_cvd_norm','funding_raw_rate','macro_raw_score',
        'source_record_sha256','source_line')}
    d.update(asof_ms=asof,entry_ms=entry,asof_price=finite(s.get('card_price')),
        entry_price=bars.opens.get(entry),conflict_level=conflict.get('level'),conflict_ratio=finite(conflict.get('ratio')),
        original_confidence=raw.get('decision',{}).get('confidence'),
        anchor_ready=anchor.get('ready'),tmv_24h_data_ready=tmv24,tmv_48h_data_ready=tmv48,
        anchor_std_usd=finite(anchor.get('std_usd')),
        flow_edb_active=any(e.get('participation_status')=='ACTIVE' and (finite(e.get('effective_weight')) or 0)>0 for e in flow_votes),
        tmv_data_ready=(tmv24 is True and tmv48 is True),ggr_pin_strike=ggr_pin,
        board_magnet_price=magnet,board_magnet_source=magnet_source if magnet is not None else None,
        original_bias=raw.get('decision',{}).get('directional_bias'),
        gamma_source_time_semantics_present=bool(gex.get('gex_time_semantics')))
    d.update(bars.window(asof))
    d['history_4h_sign']=sign(d.get('history_4h_return'))
    d['anchor_usable']=bool(d['anchor_ready'] is True and not d['anchor_band_clamped'] and finite(d['anchor_axis']) and finite(d['anchor_half_width']) and d['anchor_half_width']>0 and d['anchor_freshness']=='FRESH')
    d['anchor_band_usable']=bool(d['anchor_usable'] and d['anchor_std_usd'] is not None and d['anchor_std_usd']>0)
    d['anchor_band_source']='observed_std_derived' if d['anchor_std_usd'] is not None and d['anchor_std_usd']>0 else 'default_or_unknown_std'
    d['gex_record_usable']=d['board_gex_stale'] is False
    d['flip_position_sign']=sign(d['asof_price']-d['gamma_flip']) if d['gex_record_usable'] and d['asof_price'] and d['gamma_flip'] else None
    d['anchor_position_sign']=sign(d['asof_price']-d['anchor_axis']) if d['anchor_usable'] and d['asof_price'] else None
    d['price_midline_sign']=sign(d['asof_price']-d['history_4h_midpoint']) if d.get('history_4h_midpoint') and d['asof_price'] else None
    d['price_provenance']='card contemporaneous price; past bars last closed <= card time; entry price only from next-minute open'
    return d

def run(root,out):
    light=root/'.artifacts/astra-light-study-20260913'
    samples=[json.loads(x) for x in (light/'standard_signal_samples.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
    raw={}
    for line in (light/'raw_signal_review.jsonl').read_text(encoding='utf-8').splitlines():
        try:r=json.loads(line);key=r['identity']['card_id']
        except (ValueError,KeyError,TypeError):continue
        if key in raw and raw[key]!=r:raise ValueError('conflicting original records')
        raw[key]=r
    assert len(samples)==176 and len({s['card_id'] for s in samples})==176
    bars=HistoricalBars(read_csv(light/'market/klines.csv'))
    rows=[features_for_signal(s,raw[s['card_id']],bars) for s in samples]
    clocks=[]
    for r in read_csv(root/'.artifacts/astra-control-study-20260913/results/control_entries.csv'):
        if r['grid_offset_minutes']!=0:continue
        ms=int(r['entry_ms']);h=bars.window(ms)
        clocks.append({'card_id':r['card_id'],'entry_ms':ms,'asof_ms':ms,'asof_price':r['entry_price'],
            'history_4h_sign':sign(h.get('history_4h_return')),
            'price_midline_sign':sign(r['entry_price']-h['history_4h_midpoint']) if h.get('history_4h_midpoint') else None,**h})
    assert len(clocks)==2709
    assert all(r.get('history_last_close_ms',0)<=r['asof_ms'] for r in rows+clocks)
    target=out/'features';target.mkdir(parents=True,exist_ok=True)
    write_csv(target/'signal_facts.csv',rows);write_csv(target/'clock_facts.csv',clocks)
    prior.save(target/'coverage.json',{'signal_n':len(rows),'clock_n':len(clocks),'outcomes_joined':False,
        'past_bar_time_check':True,'anchor_usable':sum(r['anchor_usable'] for r in rows),
        'conflict_levels':dict(Counter(r['conflict_level'] for r in rows)),
        'signal_history_status':dict(Counter(r['history_4h_status'] for r in rows)),
        'clock_history_status':dict(Counter(r['history_4h_status'] for r in clocks)),
        'columns':{k:sum(r.get(k) is not None for r in rows) for k in rows[0]}})
    print(json.dumps(prior.load(target/'coverage.json'),ensure_ascii=False))

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--workspace',required=True,type=Path);ap.add_argument('--output',required=True,type=Path)
    a=ap.parse_args();run(a.workspace,a.output)
