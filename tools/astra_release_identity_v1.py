"""Read the one candidate manifest without collecting, calling or deploying."""
from pathlib import Path
import hashlib
import json

MANIFEST_PATH = Path(__file__).resolve().parents[1]/'deploy'/'signal_audit'/'release_candidate_manifest.json'
INSTALLED_MANIFEST_PATH = Path(__file__).resolve().parent/'release_candidate_manifest.json'


def release_identity():
    path=MANIFEST_PATH if MANIFEST_PATH.is_file() else INSTALLED_MANIFEST_PATH
    if not path.is_file():
        return {'release_id':'WPEM-RC-20261005-01','status':'MANIFEST_PREPARING','manifest_hash':None,'llm_protocol':'bounded_claim_ids@2.5.0'}
    raw=path.read_bytes()
    value=json.loads(raw)
    return {key:value.get(key) for key in ('schema','release_id','status','runtime_status','native_feed_status','risk_reference_status','llm_protocol','llm_model','components')} | {'manifest_hash':hashlib.sha256(raw).hexdigest()}
