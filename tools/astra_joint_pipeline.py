"""Explicit stages for research replay, labels, selection, and sealed testing."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime,timezone
import csv
import json
from pathlib import Path
import time
from astra_joint_contract import PROTOCOL
from astra_joint_sources import digest,save
from astra_joint_dataset import CandidateWriter

def replay_history(root):
    from astra_joint_events import StreamingReplay
    from astra_joint_data import read_binance_klines
    root=Path(root)
    if (root/'decisions/candidate_seal.json').exists():raise ValueError('replay already sealed')
    manifest=json.loads((root/'raw/binance/manifest.json').read_text('utf-8'))
    if manifest['errors']:raise ValueError('resolve archive errors or register explicit missing sources first')
    archives=sorted((r for r in manifest['results'] if r['feed']=='um'),key=lambda r:r['period'])
    folder=root/'events';folder.mkdir(exist_ok=True)
    events=(folder/'event_ledger.jsonl').open('w',encoding='utf-8')
    observations=(folder/'observations.jsonl').open('w',encoding='utf-8')
    writer=CandidateWriter(root);counts=Counter();start=time.monotonic()
    def event(item):
        events.write(json.dumps(item,ensure_ascii=False,separators=(',',':'))+'\n');counts['events']+=1
    def observation(item):
        observations.write(json.dumps(item,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')
        counts[item['observation_kind']]+=1;writer.accept(item)
    runner=StreamingReplay(on_event=event,on_observation=observation,on_clock=observation)
    for meta in archives:
        path=root/'raw/binance/um'/meta['frequency']/f"BTCUSDT-1m-{meta['period']}.zip"
        if digest(path)!=meta['sha256']:raise ValueError('source changed during replay')
        for bar in read_binance_klines(path):runner.push(bar)
        counts['minutes']+=meta['rows'];events.flush();observations.flush()
        print(json.dumps({'period':meta['period'],'minutes':counts['minutes'],
                          'observations':sum(counts[k] for k in ('shock','cooldown','plus15','plus30','clock')),
                          'seconds':round(time.monotonic()-start,2)}),flush=True)
    runner.finish();events.close();observations.close();candidate_seal=writer.close()
    result={'counts':dict(counts),'seconds':round(time.monotonic()-start,2),
            'event_sha256':digest(folder/'event_ledger.jsonl'),
            'observation_sha256':digest(folder/'observations.jsonl'),'candidate_counts':candidate_seal['counts'],
            'protocol_sha256':digest(root/'protocol.json')}
    save(folder/'replay_seal.json',result,True)
    return result

def joined_rows(root,years,primary_only=True):
    from astra_joint_contract import FEATURE_GROUPS
    root=Path(root);result=[]
    numeric=set(FEATURE_GROUPS['joint'])|{'as_of_ms','entry_ms','expiry_ms','target_width','actual_width',
        'entry_price','short_strike','long_strike','short_creation_ms','long_creation_ms'}
    seal=json.loads((root/'decisions/candidate_seal.json').read_text('utf-8'))
    for year in years:
        source=root/'decisions'/f'candidates-{year}.csv'
        if digest(source)!=seal['files'][source.name]:raise ValueError('candidate seal broken')
        with (root/'outcomes'/f'outcomes-{year}.csv').open(encoding='utf-8',newline='') as stream:
            labels={r['row_id']:r for r in csv.DictReader(stream)}
        with source.open(encoding='utf-8',newline='') as stream:
            for row in csv.DictReader(stream):
                if primary_only and float(row['target_width'])!=2000:continue
                label=labels.get(row['row_id'])
                if not label or label['status']!='settled':continue
                for key in numeric:row[key]=float(row[key]) if row.get(key) not in ('',None) else None
                row.update(payout_btc=float(label['payout_btc']),loss_normalized=float(label['loss_normalized']))
                result.append(row)
    return result

def normalize_parquet(root):
    from astra_joint_data import read_binance_klines
    import pandas as pd
    root=Path(root);out=root/'facts';out.mkdir(exist_ok=True);meta=[]
    manifest=json.loads((root/'raw/binance/manifest.json').read_text('utf-8'))
    for item in sorted(manifest['results'],key=lambda x:(x['feed'],x['period'])):
        src=root/'raw/binance'/item['feed']/item['frequency']/f"BTCUSDT-1m-{item['period']}.zip"
        dest=out/item['feed']/(item['period']+'.parquet');dest.parent.mkdir(exist_ok=True)
        if not dest.exists():
            rows=read_binance_klines(src)
            frame=pd.DataFrame(rows)
            frame['source_sha256']=item['sha256'];frame['market']=item['feed']
            frame['available_at_ms']=frame['close_time_ms']+1
            frame.to_parquet(dest,index=False,compression='zstd')
        else:
            recorded=pd.read_parquet(dest,columns=['source_sha256'])['source_sha256'].unique().tolist()
            if recorded != [item['sha256']]:
                raise ValueError('existing fact partition belongs to a different raw revision: '+str(dest))
        meta.append({'path':str(dest.relative_to(root)),'sha256':digest(dest),'source_sha256':item['sha256'],'rows':item['rows']})
    save(out/'manifest.json',meta,True)
    return {'partitions':len(meta)}

def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['replay','parquet']);p.add_argument('--root',type=Path,required=True)
    a=p.parse_args();result=replay_history(a.root) if a.stage=='replay' else normalize_parquet(a.root)
    print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
