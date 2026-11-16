"""Reproduce A/B from the same frozen hand. No network, model call or outcomes.

Legacy structure rows have only the collection tick clock. A successful journal
report from the SAME service run proves availability no later than that receipt.
This is an explicit conservative compatibility projection, not a native timestamp.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
sys.path.insert(0,str(ROOT/'tools'))
import astra_player_input_v1 as player
import astra_anchor_relative_v1 as ard
import astra_expiry_structure_v1 as structure


def legacy_history(rows,runs):
    out=[]
    for row in rows:
        if row.get('available_at_ms') is not None:
            out.append(dict(row));continue
        matches=[r for r in runs if r['service_started_ms']<=row['at_ms']<=r['receipt_ms']
                 and r['structure_records']>0 and r.get('invocation')
                 and not any(e.get('step')=='structure' for e in r['errors'])]
        if len(matches)!=1:
            out.append(dict(row));continue  # no guessed receipt
        run=matches[0]
        out.append({**row,'available_at_ms':run['receipt_ms'],
                    'pin_source':structure.PIN_SOURCE,'pin_method':None,
                    'compat_backfill_applied':True,'availability_basis':'systemd_success_receipt_upper_bound',
                    'source_record_sha256':hashlib.sha256(json.dumps(row,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
                    'receipt_invocation':run['invocation']})
    return out


def generate(out):
    src=HERE.parent/'player_input_liability/source'
    cases=[player.read_json(next((src/'cases/2026-11-14').glob(f'*_{s}_*.json.gz'))) for s in ('put','call')]
    cards=[json.loads(l) for l in (src/'levels/2026-11-15.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
    cards=[next(c for c in cards if c['side']==s and c['round']=='r2') for s in ('put','call')]
    path=player.read_json(src/'paths/2026-11-15.json.gz')
    rows=[json.loads(l) for l in (HERE/'source/structure/2026-11-15.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
    runs=json.loads((HERE/'source/collection_runs.json').read_text(encoding='utf-8'))
    history=legacy_history(rows,runs)
    a=player.build_input(*cases,*cards,index_path=path,include_ard=False)
    old=(HERE.parent/'player_first_order_guard/ai_input.md').read_text(encoding='utf-8')
    assert a==old,'A must remain byte-for-byte equivalent to the previous frozen body'
    b=player.build_input(*cases,*cards,index_path=path,ard_history=history)
    start=b.index('\n\n**锚相对动态ARD');end=b.index('\n\n3. **',start)
    assert (b[:start]+b[end:]).replace(player.ARD_READING,'')==a
    facts=ard.analyze(history,path,expiry_ms=cards[0]['expiry_ms'],cutoff_ms=min(c['read_at_ms'] for c in cards))
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    values={'A_input.md':a,'B_input.md':b,'ard_excerpt.md':b[start+2:end]+'\n',
            'ard_facts.json':json.dumps(facts,ensure_ascii=False,indent=2)+'\n',
            'pin_history.jsonl':'\n'.join(json.dumps(r,ensure_ascii=False,sort_keys=True) for r in history)+'\n'}
    for name,text in values.items():
        with (out/name).open('x',encoding='utf-8',newline='\n') as f:f.write(text)
    return facts


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True)
    facts=generate(p.parse_args().out)
    print(json.dumps({k:facts[k] for k in ('valid_samples','expected_samples','occupancy_pct','relation')},ensure_ascii=False))
