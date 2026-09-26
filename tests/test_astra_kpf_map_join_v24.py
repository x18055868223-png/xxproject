from datetime import timedelta
from decimal import Decimal
import hashlib
import json

from tests.test_astra_kpf_light_v1 import _write_day, _ms
from tools import astra_kpf_light_v1 as light
from tools import astra_map_projection_v24 as projection
from tools.astra_data_engine_v23 import DataEngine


def publish(tmp_path):
    now=light.now_ms()
    end=light.ms_to_utc(now).date()-timedelta(days=1)
    for offset in range(90):
        day=end-timedelta(days=89-offset)
        _write_day(tmp_path,day,[(offset*3+1,'90450','10',_ms(day,1)),
                                 (offset*3+2,'90500','180',_ms(day,2)),
                                 (offset*3+3,'90550','10',_ms(day,3))])
    output=tmp_path/'published'
    result=light.run_oneshot(data_root=tmp_path,output_root=output,as_of_ms=now,
                             current_price=Decimal('89500'),fetch_quote=False,
                             min_free_bytes=0,storage_budget_bytes=10*1024**3)
    assert result['ok']
    return output, max(now,light.now_ms())+1000


def test_real_producer_contract_is_consumed_without_geometry_or_basis_loss(tmp_path):
    output,now=publish(tmp_path)
    engine=DataEngine(tmp_path/'engine')
    captured=projection.collect_kpf_artifacts(engine,now,{'kpf_manifest_path':str(output/'current.json')})
    assert captured['gaps']==[]
    values=captured['record']['content']['values']
    assert values['valid'] is True and values['zones']
    assert all(zone['raw_low']==zone['original_zone']['native_producer_zone']['raw']['basin_low'] for zone in values['zones'])
    assert all(zone['quote_currency']=='USDT' for zone in values['zones'])
    usable=[zone for zone in values['zones'] if zone['qualification_complete']]
    assert usable and all(zone['coordinate_qualification']=='NOMINAL_ONLY' for zone in usable)
    manifest=engine.freeze([engine.read(projection.KPF_PRODUCT,now_ms=now,usage='background')],cutoff_at_ms=now)
    frozen=manifest['source_records'][0]['content']['values']
    assert frozen['artifact_hashes']==values['artifact_hashes']


def test_native_audit_failure_cannot_be_overridden_by_zone_can_use(tmp_path):
    output,now=publish(tmp_path)
    pointer=json.loads((output/'current.json').read_text())
    audit_ref=pointer['artifacts']['audit']
    path=output/audit_ref['path']
    audit=json.loads(path.read_text())
    audit['self_audit_pass']=False
    path.write_text(json.dumps(audit),encoding='utf-8')
    audit_ref['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    (output/'current.json').write_text(json.dumps(pointer),encoding='utf-8')
    captured=projection.collect_kpf_artifacts(DataEngine(tmp_path/'engine'),now,{'kpf_manifest_path':str(output/'current.json')})
    values=captured['record']['content']['values']
    assert values['valid'] is False
    assert captured['gaps']==['kpf_native_audit_or_source_clock_unqualified']
    assert all(not zone['usage_decision']['can_use'] for zone in values['zones'])
