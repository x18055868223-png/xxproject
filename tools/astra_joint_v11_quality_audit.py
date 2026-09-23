"""Read-only semantic audit of the exact frozen Binance archive files."""
from pathlib import Path
import argparse,csv,hashlib,io,json,math,zipfile
from collections import Counter
from astra_joint_data import normalize_timestamp_ms

def issues(row):
    if len(row)<11:return ['missing_columns']
    result=[]
    try:
        values=[float(row[i]) for i in range(11)]
        if not all(math.isfinite(x) for x in values):return ['non_finite']
        opened,closed=normalize_timestamp_ms(row[0]),normalize_timestamp_ms(row[6])
        o,h,l,c,v,q,n,tb,tq=[values[i] for i in (1,2,3,4,5,7,8,9,10)]
        if opened%60000 or closed-opened!=59999:result.append('minute_time_shape')
        if min(o,h,l,c)<=0 or not l<=o<=h or not l<=c<=h:result.append('ohlc_containment')
        if min(v,q,tb,tq)<0:result.append('negative_volume')
        if n<0 or n!=int(n):result.append('trade_count')
        if tb>v+max(1e-9,abs(v)*1e-10):result.append('taker_base_exceeds_total')
        if tq>q+max(1e-7,abs(q)*1e-10):result.append('taker_quote_exceeds_total')
        if v==0 and (q!=0 or tb!=0 or tq!=0):result.append('zero_volume_inconsistent')
    except (ValueError,TypeError,OverflowError):return ['parse_error']
    return result

def run(manifest_path,output):
    manifest_path=Path(manifest_path);output=Path(output)
    if output.exists():raise ValueError('semantic audit already exists; preserve its identity')
    manifest=json.loads(manifest_path.read_text('utf-8'));output.mkdir(parents=True)
    totals=Counter();sources=[]
    with (output/'anomalies.jsonl').open('w',encoding='utf-8') as errors:
        for source in manifest['sources']:
            path=Path(source['source_file']);counts=Counter();digest=hashlib.sha256(path.read_bytes()).hexdigest()
            if digest!=source['sha256']:raise ValueError('archive hash changed: '+str(path))
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    if not name.endswith('.csv'):continue
                    with archive.open(name) as stream:
                        for line,row in enumerate(csv.reader(io.TextIOWrapper(stream,encoding='utf-8-sig')),1):
                            if row and row[0] in ('open_time','open_time_ms'):continue
                            counts['raw_rows']+=1;found=issues(row)
                            if found:
                                counts['anomalous_rows']+=1;counts.update(found)
                                errors.write(json.dumps(dict(source=str(path),member=name,line=line,issues=found,row=row),ensure_ascii=False)+'\n')
            totals.update(counts);sources.append(dict(path=str(path),sha256=digest,counts=dict(counts)))
            print(json.dumps(dict(file=path.name,rows=counts['raw_rows'],anomalous=counts['anomalous_rows'])),flush=True)
    report=dict(schema='astra_v11_source_semantic_audit@1.0.0',source_manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                counts=dict(totals),sources=sources,scope='read-only audit; original data and failures are unchanged')
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--manifest',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    print(json.dumps(run(a.manifest,a.output)['counts']))
