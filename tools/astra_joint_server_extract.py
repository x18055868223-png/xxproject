"""Run explicitly on the research host: freeze existing logs, scan, compact facts.

Writes only /home/bitnami/astra-joint-research-20260914; no service/config edits.
Original raw bytes remain in the frozen directory and are identified by hash.
"""
from pathlib import Path
import datetime
import gzip
import hashlib
import json
import shutil

SOURCE=Path('/home/bitnami/fmz2/logs/storage/668422/demo/logs')
OUTPUT=Path('/home/bitnami/astra-joint-research-20260914')

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for data in iter(lambda:f.read(1048576),b''):h.update(data)
    return h.hexdigest()

def write(path,value):
    data=json.dumps(value,ensure_ascii=False,indent=2).encode()
    if path.exists() and path.read_bytes()!=data:raise ValueError('frozen metadata already exists')
    path.write_bytes(data)

def compact(value,depth=0):
    if isinstance(value,list):
        if len(value)>64:return {'research_omitted_list_length':len(value),'raw_available':True}
        return [compact(x,depth+1) for x in value]
    if isinstance(value,dict):
        return {k:compact(v,depth+1) for k,v in value.items()
                if k not in {'contract_audit','raw_response','percentile_grid','historical_percentiles'}}
    return value

def main():
    if OUTPUT.parent!=Path('/home/bitnami'):raise ValueError('unexpected target')
    OUTPUT.mkdir(exist_ok=True); raw=OUTPUT/'raw';raw.mkdir(exist_ok=True)
    inventory=OUTPUT/'inventory.json'
    if inventory.exists():items=json.loads(inventory.read_text())['files']
    else:
        items=[{'name':p.name,'bytes':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns}
               for p in sorted(SOURCE.iterdir()) if p.name.startswith(('snapshots.jsonl','decisions.jsonl')) or p.name=='signal_review.jsonl']
        write(inventory,{'frozen_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'files':items})
    reports=[]
    for item in items:
        target=raw/item['name'];receipt=target.with_name(target.name+'.receipt.json')
        if not receipt.exists():
            temp=target.with_suffix(target.suffix+'.partial');remaining=item['bytes']
            with (SOURCE/item['name']).open('rb') as src,temp.open('wb') as dst:
                while remaining:
                    b=src.read(min(remaining,1048576))
                    if not b:raise ValueError('source rotated/truncated before freeze')
                    dst.write(b);remaining-=len(b)
            temp.replace(target)
            write(receipt,{**item,'source':str(SOURCE/item['name']),'frozen_source':str(target),'sha256':sha(target)})
        identity=json.loads(receipt.read_text());report_path=OUTPUT/(item['name']+'.scan.json')
        if report_path.exists():reports.append(json.loads(report_path.read_text()));continue
        if sha(target)!=identity['sha256']:raise ValueError('frozen raw identity changed')
        report={**identity,'lines':0,'bad_lines':0,'missing_time':0,'first_ms':None,'last_ms':None,
                'gaps_over_5min':0,'backward_intervals':0,'keys':{},'interval_counts':{},'versions':{}}
        compact_path=OUTPUT/(item['name']+'.facts.jsonl.gz');opener=gzip.open if target.suffix=='.gz' else open
        previous=None
        with opener(target,'rt',encoding='utf-8') as stream,gzip.open(compact_path,'wt',encoding='utf-8',compresslevel=6) as out:
            for line_number,line in enumerate(stream,1):
                report['lines']+=1
                try:obj=json.loads(line)
                except ValueError:report['bad_lines']+=1;continue
                for k in obj:report['keys'][k]=report['keys'].get(k,0)+1
                decision=obj.get('decision') if isinstance(obj.get('decision'),dict) else obj
                stamp=decision.get('ts_ms') or obj.get('ts_ms')
                version=decision.get('demo_version') or (obj.get('identity') or {}).get('strategy_version')
                report['versions'][str(version)]=report['versions'].get(str(version),0)+1
                if isinstance(stamp,(float,int)):
                    if report['first_ms'] is None:report['first_ms']=stamp
                    report['last_ms']=stamp
                    if previous is not None:
                        delta=stamp-previous;report['gaps_over_5min']+=int(delta>300000);report['backward_intervals']+=int(delta<0)
                        bucket=str(round(delta/60000,1));report['interval_counts'][bucket]=report['interval_counts'].get(bucket,0)+1
                    previous=stamp
                else:report['missing_time']+=1
                fact=compact(obj.get('factor_snapshot') or decision.get('factor_snapshot') or {})
                keep={k:compact(v) for k,v in decision.items() if k not in {'factor_snapshot','contract_audit','module_states','runtime_facts'}}
                result={'source_file':target.name,'source_sha256':identity['sha256'],'line':line_number,
                        'ts_ms':stamp,'version':version,'decision':keep,'factor_snapshot':fact,
                        'omission_note':'large arrays remain in immutable source, not reconstructed'}
                if item['name']=='signal_review.jsonl':result['original_card']=obj
                out.write(json.dumps(result,ensure_ascii=False,separators=(',',':'))+'\n')
        report['compact_file']=str(compact_path);report['compact_sha256']=sha(compact_path)
        write(report_path,report);reports.append(report)
        print(json.dumps({'file':target.name,'lines':report['lines'],'bad':report['bad_lines'],'compact_bytes':compact_path.stat().st_size}),flush=True)
    write(OUTPUT/'scan_summary.json',reports)
    import tarfile
    with tarfile.open(OUTPUT/'compact_bundle.tar','w') as archive:
        for p in sorted(OUTPUT.iterdir()):
            if p.is_file() and (p.name.endswith('.json') or p.name.endswith('.facts.jsonl.gz')):archive.add(p,arcname=p.name)
    print(json.dumps({'complete':True,'files':len(reports),'bundle_bytes':(OUTPUT/'compact_bundle.tar').stat().st_size}),flush=True)

if __name__=='__main__':main()
