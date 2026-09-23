"""Immutable-source preparation for the isolated Astra joint research.

No trading API, production writes, or model calls. Existing archives are copied,
verified, and never edited. A changed remote archive is a new revision.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timezone, timedelta
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
import zipfile

from astra_joint_contract import PROTOCOL

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()

def save(path, data, immutable=False):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
    if immutable and path.exists():
        if path.read_bytes() != encoded:
            raise ValueError('immutable artifact already exists: '+str(path))
        return
    temp = path.with_suffix(path.suffix+'.partial')
    temp.write_bytes(encoded); temp.replace(path)

def freeze(root, output):
    root, output = Path(root), Path(output)
    work = Path(__file__).resolve().parents[1]
    protocol_path = output/'protocol.json'
    if protocol_path.exists():
        if json.loads(protocol_path.read_text('utf-8')) != PROTOCOL:
            raise ValueError('protocol differs from sealed research')
        return json.loads((output/'protocol_seal.json').read_text('utf-8'))
    files = {}
    for folder in ('tools','tests','deploy/signal_audit/frontend','demo/最新交付物','docs/astra'):
        for path in (work/folder).rglob('*'):
            if path.is_file() and '__pycache__' not in str(path) and path.suffix in ('.py','.js','.css','.html','.md'):
                files[path.relative_to(work).as_posix()] = digest(path)
    state = {'head': subprocess.check_output(['git','-C',str(work),'rev-parse','HEAD'],text=True).strip(),
             'status': subprocess.check_output(['git','-C',str(work),'status','--porcelain'],text=True),
             'files': files, 'scope': 'pre-implementation assets plus newly created joint contract/source preparer',
             'fmz_sha256': files['demo/最新交付物/neutral_regulation_demo_fmz.py']}
    save(output/'implementation_baseline.json',state,True)
    save(protocol_path,PROTOCOL,True)
    usage = {'2020_2021': 'training, no prior strategy result found in archived study scopes',
             '2022': 'selection, no prior strategy result found in archived study scopes',
             '2023': 'locked historical test; source inventory alone is not a result review',
             '2024_2026': 'previously studied; development/drift only',
             'evidence': ['docs/astra/30_局部定式长期验证规格.md',
                          '.artifacts/astra-local-validation-20260914/validation_protocol.md'],
             'qualification': 'audit of repository research scopes; not a claim about all human exposure to these markets'}
    save(output/'research_use_audit.json',usage,True)
    seal = {'frozen_at_utc':datetime.now(timezone.utc).isoformat(),
            'protocol_sha256':digest(protocol_path),'baseline_sha256':digest(output/'implementation_baseline.json'),
            'usage_sha256':digest(output/'research_use_audit.json'),
            'test_unopened': True, 'outcomes_computed': False}
    save(output/'protocol_seal.json',seal,True)
    return seal

def request(url):
    req = urllib.request.Request(url,headers={'User-Agent':'Astra-joint-local-research/1.0'})
    with urllib.request.urlopen(req,timeout=60) as response:
        return response.read()

def periods():
    for year in range(2020,2027):
        for month in range(1,13):
            if (year,month)>(2026,8): break
            yield 'monthly',f'{year}-{month:02d}'
    for day in range(1,14):
        yield 'daily',f'2026-09-{day:02d}'

def fetch_archive(output, reuse, feed, frequency, period):
    folder=Path(output)/'raw/binance'/feed/frequency
    folder.mkdir(parents=True,exist_ok=True)
    name=f'BTCUSDT-1m-{period}.zip'; target=folder/name
    receipt=folder/(name+'.receipt.json')
    if receipt.exists() and target.exists():
        meta=json.loads(receipt.read_text('utf-8'))
        if digest(target)!=meta['sha256']: raise ValueError('archive changed: '+str(target))
        return meta
    original=Path(reuse)/'market/binance'/feed/name
    old_receipt=original.with_name(original.name+'.receipt.json')
    prefix='spot' if feed=='spot' else 'futures/um'
    url=f'https://data.binance.vision/data/{prefix}/{frequency}/klines/BTCUSDT/1m/{name}'
    reused=False
    if frequency=='monthly' and original.exists() and old_receipt.exists():
        meta=json.loads(old_receipt.read_text('utf-8'))
        expected=meta['official_checksum']
        if digest(original)!=expected: raise ValueError('original archive checksum mismatch')
        shutil.copyfile(original,target); reused=True
    else:
        expected=request(url+'.CHECKSUM').decode().split()[0].lower()
        raw=request(url)
        actual=hashlib.sha256(raw).hexdigest()
        if actual!=expected: raise ValueError('official checksum mismatch')
        if target.exists() and digest(target)!=actual:
            revision=folder/'revisions'/digest(target)/name
            revision.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(target,revision)
        temp=target.with_suffix('.zip.partial');temp.write_bytes(raw);temp.replace(target)
    count=0; first=last=None; gaps=0; units=set()
    with zipfile.ZipFile(target) as archive:
        if len(archive.namelist())!=1: raise ValueError('unexpected zip members')
        with archive.open(archive.namelist()[0]) as stream:
            for row in csv.reader(io.TextIOWrapper(stream,encoding='utf-8-sig')):
                if not row or not row[0].isdigit():continue
                raw_time=int(row[0]); micro=raw_time>10**14
                stamp=raw_time//1000 if micro else raw_time
                units.add('microseconds' if micro else 'milliseconds')
                if len(row)<11:raise ValueError('missing API columns')
                if not 0<=float(row[9])<=float(row[5])+1e-5:raise ValueError('invalid active volume')
                if last is not None and stamp-last!=60000:gaps+=1
                if first is None:first=stamp
                last=stamp;count+=1
    meta={'feed':feed,'frequency':frequency,'period':period,'url':url,'sha256':digest(target),
          'official_checksum':expected,'rows':count,'first_open_ms':first,'last_open_ms':last,
          'noncontiguous_intervals':gaps,'timestamp_units':sorted(units),'reused':reused,
          'bytes':target.stat().st_size,'fetched_or_reused_at':datetime.now(timezone.utc).isoformat(),
          'observation_identity':'API closed minute, separately identified spot and UM futures'}
    (folder/(name+'.CHECKSUM')).write_text(expected+'  '+name+'\n',encoding='utf-8')
    save(receipt,meta,True)
    return meta

def binance(output,reuse):
    jobs=[(feed,f,p) for feed in ('spot','um') for f,p in periods()]
    results=[];errors=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending={pool.submit(fetch_archive,output,reuse,*job):job for job in jobs}
        for task in as_completed(pending):
            try:
                meta=task.result();results.append(meta)
                print(json.dumps({k:meta[k] for k in ('feed','period','rows','reused')}),flush=True)
            except Exception as exc:
                item={'job':pending[task],'error':str(exc)};errors.append(item)
                print(json.dumps(item),flush=True)
    save(Path(output)/'raw/binance/manifest.json',{'results':results,'errors':errors,'expected':len(jobs)})
    return {'completed':len(results),'errors':len(errors)}

def deribit(root,output,reuse):
    folder=Path(output)/'raw/deribit';folder.mkdir(parents=True,exist_ok=True)
    original=Path(root)/'.artifacts/astra-light-study-20260913/market/cache/deribit/2256b638c21d48b5_get_instruments.json'
    source=folder/'instruments_response.json'
    if not source.exists():shutil.copyfile(original,source)
    if digest(source)!='d46cce0dd7a608b22636787e78a707bf3789152775eacb38349f665c0148e8f6':
        raise ValueError('instrument source identity changed')
    all_rows=json.loads(source.read_text('utf-8'))['result']
    start=int(datetime(2020,1,1,tzinfo=timezone.utc).timestamp()*1000)
    end=int(datetime(2026,9,14,tzinfo=timezone.utc).timestamp()*1000)
    keep=[r for r in all_rows if start<=r.get('expiration_timestamp',0)<end
          and r.get('kind')=='option' and r.get('settlement_currency')=='BTC']
    save(folder/'instruments.json',keep,True)
    save(folder/'instruments.receipt.json',{'source_sha256':digest(source),'rows':len(keep),
         'expiry_dates':len({r['expiration_timestamp'] for r in keep}),
         'constraint':'creation_timestamp <= entry time; archive listing not proof of liquidity'},True)
    values={};sources=[];offset=0
    while True:
        params={'index_name':'btc_usd','count':100,'offset':offset}
        url='https://www.deribit.com/api/v2/public/get_delivery_prices?'+urllib.parse.urlencode(sorted(params.items()))
        key=hashlib.sha256(url.encode()).hexdigest()[:24]+'_get_delivery_prices'
        path=folder/(key+'.json');receipt=folder/(key+'.receipt.json')
        old=Path(reuse)/'market/deribit/raw'/(key+'.json')
        if not path.exists():
            if old.exists():
                shutil.copyfile(old,path)
                shutil.copyfile(old.with_name(key+'.receipt.json'),receipt)
            else:
                path.write_bytes(request(url))
                save(receipt,{'url':url,'sha256':digest(path),'fetched_at_utc':datetime.now(timezone.utc).isoformat()},True)
        meta=json.loads(receipt.read_text('utf-8'))
        if digest(path)!=meta['sha256']:raise ValueError('delivery cache changed')
        data=json.loads(path.read_text('utf-8'))
        if data.get('error'):raise ValueError(data['error'])
        rows=data['result']['data'];sources.append(meta)
        if not rows:break
        for row in rows:
            if row['date'] in values and values[row['date']]!=row['delivery_price']:
                raise ValueError('conflicting delivery')
            values[row['date']]=row['delivery_price']
        print(json.dumps({'delivery_offset':offset,'oldest':min(x['date'] for x in rows)}),flush=True)
        if min(x['date'] for x in rows)<='2020-01-01':break
        offset+=len(rows);time.sleep(.25)
    result=[{'date':d,'delivery_price':v} for d,v in sorted(values.items()) if '2020-01-01'<=d<='2026-09-13']
    save(folder/'delivery_prices.json',result,True)
    save(folder/'delivery_prices.receipt.json',{'sources':sources,'rows':len(result),'role':'outcomes only'},True)
    return {'instruments':len(keep),'delivery_days':len(result)}

def main():
    p=argparse.ArgumentParser();p.add_argument('job',choices=['freeze','binance','deribit'])
    p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--reuse',type=Path,required=True);a=p.parse_args()
    result=freeze(a.root,a.output) if a.job=='freeze' else binance(a.output,a.reuse) if a.job=='binance' else deribit(a.root,a.output,a.reuse)
    print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
