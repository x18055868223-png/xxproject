"""Derive bounded current facts from frozen logs; raw files remain retained."""
import gzip,json,hashlib,tarfile
from pathlib import Path
ROOT=Path('/home/bitnami/astra-joint-research-20260914')
FACTORS={'anchor','gamma_regime','gex_info','m_die','flow','macro_pressure','skew','neutral_repair_signal'}
DECISION={'ts_ms','symbol','demo_version','decision','side','market_state','data_quality','reject_type','reject_reasons'}
def main():
    out=ROOT/'slim';out.mkdir(exist_ok=True);reports=[]
    for p in sorted(ROOT.glob('*.facts.jsonl.gz')):
        target=out/p.name
        if not target.exists():
            with gzip.open(p,'rt',encoding='utf-8') as src,gzip.open(target,'wt',encoding='utf-8',compresslevel=6) as dst:
                for line in src:
                    row=json.loads(line)
                    row['factor_snapshot']={k:v for k,v in row.get('factor_snapshot',{}).items() if k in FACTORS}
                    row['decision']={k:v for k,v in row.get('decision',{}).items() if k in DECISION}
                    row['projection_schema']='astra_joint_frozen_fact_projection@1.0.0'
                    dst.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')
        h=hashlib.sha256(target.read_bytes()).hexdigest()
        reports.append({'file':target.name,'sha256':h,'bytes':target.stat().st_size,'raw_preserved_in':str(ROOT/'raw')})
    (out/'manifest.json').write_text(json.dumps(reports,indent=2))
    with tarfile.open(ROOT/'slim_bundle.tar','w') as archive:
        for p in sorted(out.iterdir()):archive.add(p,arcname=p.name)
        for p in [ROOT/'inventory.json',ROOT/'scan_summary.json']:archive.add(p,arcname=p.name)
    print(json.dumps({'bytes':(ROOT/'slim_bundle.tar').stat().st_size,'files':len(reports)}),flush=True)
if __name__=='__main__':main()
