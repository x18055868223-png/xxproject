"""Partitioned point-in-time candidates; outcomes are a separate explicit step."""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
from datetime import datetime,timezone,timedelta
import csv
import hashlib
import json
from pathlib import Path
import time
from astra_joint_contract import PROTOCOL, FEATURE_GROUPS, SCHEMA_VERSION
from astra_joint_sources import digest,save

MINUTE=60000
FEATURES=FEATURE_GROUPS['joint']
FIELDS=['row_id','observation_id','event_family','episode_id','active_episode_id','in_episode','observation_kind',
        'as_of_ms','entry_ms','expiry_ms','delivery_date','split','side','target_width','actual_width',
        'short_strike','long_strike','short_name','long_name','short_creation_ms','long_creation_ms',
        'entry_price','price_source','option_source_sha256','source_observation_hash',*FEATURES]

def canonical_hash(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def expiry_ms(entry):
    date=datetime.fromtimestamp(entry/1000,timezone.utc)
    expiry=date.replace(hour=8,minute=0,second=0,microsecond=0)
    if expiry.timestamp()*1000<=entry:expiry+=timedelta(days=1)
    return int(expiry.timestamp()*1000)

def split_for_expiry(expiry):
    year=datetime.fromtimestamp(expiry/1000,timezone.utc).year
    return 'train' if year in (2020,2021) else 'selection' if year==2022 else 'locked_test' if year==2023 else 'development'

def select_legs(contracts,side,price,target,entry):
    kind='put' if side=='put' else 'call'
    pool=[x for x in contracts if x.get('option_type')==kind and x.get('creation_timestamp',10**20)<=entry]
    shorts=[x for x in pool if x['strike']<price] if side=='put' else [x for x in pool if x['strike']>price]
    if not shorts:return None
    short=(max if side=='put' else min)(shorts,key=lambda x:x['strike'])
    longs=[x for x in pool if x['strike']<short['strike']] if side=='put' else [x for x in pool if x['strike']>short['strike']]
    if not longs:return None
    long=min(longs,key=lambda x:(abs(abs(x['strike']-short['strike'])-target),abs(x['strike']-short['strike']),x['instrument_name']))
    return short,long

class SpotOpenCache:
    def __init__(self,root):self.root=Path(root);self.month=None;self.values={}
    def get(self,stamp):
        from astra_joint_data import read_binance_klines
        date=datetime.fromtimestamp(stamp/1000,timezone.utc);month=date.strftime('%Y-%m')
        if month!=self.month:
            folder=self.root/'raw/binance/spot'
            monthly=folder/'monthly'/f'BTCUSDT-1m-{month}.zip'
            paths=[monthly] if monthly.exists() else sorted((folder/'daily').glob(f'BTCUSDT-1m-{month}-*.zip'))
            self.values={}
            for path in paths:
                for r in read_binance_klines(path):self.values[r['open_time_ms']]=r['open']
            self.month=month
        return self.values.get(stamp)

class CandidateWriter:
    def __init__(self,root):
        self.root=Path(root);self.folder=self.root/'decisions';self.folder.mkdir(exist_ok=True)
        self.source=self.root/'raw/deribit/instruments.json';self.source_hash=digest(self.source)
        self.contracts=defaultdict(list)
        for c in json.loads(self.source.read_text('utf-8')):self.contracts[c['expiration_timestamp']].append(c)
        self.spot=SpotOpenCache(root);self.files={};self.writers={};self.counts=Counter()
        self.gaps=(self.folder/'candidate_gaps.jsonl').open('w',encoding='utf-8')
    def accept(self,observation):
        entry=int(observation['entry_ms']);expiry=expiry_ms(entry);hours=(expiry-entry)/3600000
        self.counts['observations']+=1
        if not 8<hours<=24:
            self.counts['dte_excluded']+=1;return
        price=self.spot.get(entry)
        if not price:
            self._gap(observation,'entry_price_unavailable');return
        contracts=self.contracts.get(expiry,[])
        if not contracts:
            self._gap(observation,'actual_expiry_contracts_unavailable');return
        year=datetime.fromtimestamp(expiry/1000,timezone.utc).year
        if year not in self.files:
            f=(self.folder/f'candidates-{year}.csv').open('w',encoding='utf-8',newline='');self.files[year]=f
            self.writers[year]=csv.DictWriter(f,fieldnames=FIELDS);self.writers[year].writeheader()
        for side in ('put','call'):
            for target in PROTOCOL['widths']:
                selected=select_legs(contracts,side,price,target,entry)
                if selected is None:
                    self._gap(observation,'actual_legs_unavailable',side=side,target_width=target);continue
                short,long=selected;width=abs(short['strike']-long['strike'])
                features={key:observation.get(key) for key in FEATURES}
                features.update(dte_hours=hours,short_distance_fraction=abs(short['strike']-price)/price,
                                width_fraction=width/price,side_sign=-1 if side=='put' else 1,
                                adverse_move_since_shock=observation.get('down_move_since_shock' if side=='put' else 'up_move_since_shock'),
                                favorable_move_since_shock=observation.get('up_move_since_shock' if side=='put' else 'down_move_since_shock'))
                obs_id=observation['observation_id']
                row={**features,'row_id':canonical_hash([obs_id,side,target]),'observation_id':obs_id,
                     **{k:observation.get(k) for k in ('event_family','episode_id','active_episode_id','in_episode','observation_kind','as_of_ms')},
                     'entry_ms':entry,'expiry_ms':expiry,'delivery_date':datetime.fromtimestamp(expiry/1000,timezone.utc).strftime('%Y-%m-%d'),
                     'split':split_for_expiry(expiry),'side':side,'target_width':target,'actual_width':width,
                     'short_strike':short['strike'],'long_strike':long['strike'],
                     'short_name':short['instrument_name'],'long_name':long['instrument_name'],
                     'short_creation_ms':short['creation_timestamp'],'long_creation_ms':long['creation_timestamp'],
                     'entry_price':price,'price_source':'Binance BTCUSDT spot next minute open',
                     'option_source_sha256':self.source_hash,'source_observation_hash':canonical_hash(observation)}
                self.writers[year].writerow(row);self.counts[f'rows_{year}']+=1
    def _gap(self,observation,reason,**extra):
        self.gaps.write(json.dumps({'observation_id':observation['observation_id'],'reason':reason,**extra})+'\n');self.counts[reason]+=1
    def close(self):
        for f in self.files.values():f.close()
        self.gaps.close()
        seal={'schema':SCHEMA_VERSION,'sealed_at':datetime.now(timezone.utc).isoformat(),
              'outcomes_joined':False,'counts':dict(self.counts),
              'source_protocol_sha256':digest(self.root/'protocol.json'),
              'files':{p.name:digest(p) for p in sorted(self.folder.glob('*.csv'))}}
        save(self.folder/'candidate_seal.json',seal,True)
        return seal

def payout(side,short,long,settlement):
    if settlement<=0:raise ValueError('invalid settlement')
    if side=='put':return (max(short-settlement,0)-max(long-settlement,0))/settlement
    if side=='call':return (max(settlement-short,0)-max(settlement-long,0))/settlement
    raise ValueError('invalid side')

def build_outcomes(root,years):
    root=Path(root);seal=json.loads((root/'decisions/candidate_seal.json').read_text('utf-8'))
    if 2023 in years and not (root/'models/selection_seal.json').exists():
        raise ValueError('2023 outcomes remain sealed until model selection')
    prices={x['date']:x['delivery_price'] for x in json.loads((root/'raw/deribit/delivery_prices.json').read_text('utf-8'))}
    folder=root/'outcomes';folder.mkdir(exist_ok=True);summary={}
    for year in years:
        src=root/'decisions'/f'candidates-{year}.csv'
        if digest(src)!=seal['files'][src.name]:raise ValueError('candidate identity changed')
        dest=folder/f'outcomes-{year}.csv'
        if dest.exists():raise ValueError('outcomes already exist; use recorded result rather than overwrite')
        count=missing=0
        with src.open(encoding='utf-8',newline='') as stream,dest.open('w',encoding='utf-8',newline='') as out:
            writer=csv.DictWriter(out,fieldnames=['row_id','settlement_price','payout_btc','loss_normalized','status'])
            writer.writeheader()
            for row in csv.DictReader(stream):
                S=prices.get(row['delivery_date']);payment=None;loss=None
                if S:
                    payment=payout(row['side'],float(row['short_strike']),float(row['long_strike']),S)
                    loss=payment/(float(row['actual_width'])/float(row['entry_price']))
                else:missing+=1
                writer.writerow({'row_id':row['row_id'],'settlement_price':S,'payout_btc':payment,
                                 'loss_normalized':loss,'status':'settled' if S else 'missing_official_delivery'})
                count+=1
        summary[str(year)]={'rows':count,'missing':missing,'sha256':digest(dest)}
    save(folder/('outcome_seal-'+'-'.join(map(str,years))+'.json'),summary,True)
    return summary

def main():
    p=argparse.ArgumentParser();p.add_argument('job',choices=['outcomes']);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--years',type=int,nargs='+',required=True);a=p.parse_args()
    print(json.dumps(build_outcomes(a.root,a.years)),flush=True)

if __name__=='__main__':main()
