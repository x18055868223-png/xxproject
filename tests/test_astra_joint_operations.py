import json
from datetime import datetime, timezone
import astra_joint_operations as operations
import astra_joint_shadow as shadow
from test_astra_joint_shadow import ms, observation, contracts, artifact
from astra_joint_dataset import canonical_hash


def seed(folder):
    at = ms(2026, 9, 14, 23)
    obs = observation(at, entry_ms=at + 1, identity='due')
    assessment = shadow.make_assessment(obs, 80000, contracts(at + 1), artifact(), canonical_hash(obs))
    shadow.Ledger(folder).record_decision('due', at, {'observation': obs, 'assessment': assessment})
    return assessment['sides']['put']['reference']['expiry_ms']


def test_no_early_request_and_official_settlement_is_idempotent(tmp_path):
    expiry = seed(tmp_path); calls = []
    day = datetime.fromtimestamp(expiry/1000, timezone.utc).strftime('%Y-%m-%d')
    def transport(url, params):
        calls.append((url, params))
        payload = {'result': {'data': [{'date': day, 'delivery_price': 80500}]}}
        return payload, {'sha256': 'official-fixture'}, json.dumps(payload).encode()
    assert operations.settle_due(tmp_path, clock=lambda: expiry-1, transport=transport)['http_attempts'] == 0
    assert not calls
    assert operations.settle_due(tmp_path, clock=lambda: expiry+1, transport=transport)['settled'] == 1
    assert operations.settle_due(tmp_path, clock=lambda: expiry+2, transport=transport)['http_attempts'] == 0
    assert len(calls) == 1
    out = json.loads((tmp_path/'outcomes.jsonl').read_text(encoding='utf-8'))
    assert out['source_provenance']['kind'] == 'official_deribit_delivery'
    assert not out['sides']['put']['strict_net_result_ready']  # No historical quotes invented.


def test_missing_delivery_remains_pending_and_shared_minute_attempt(tmp_path):
    expiry = seed(tmp_path); calls = []
    def transport(url, params):
        calls.append(1)
        return {'result': {'data': []}}, {}, b'{}'
    one = operations.settle_due(tmp_path, clock=lambda: expiry+1, transport=transport)
    two = operations.settle_due(tmp_path, clock=lambda: expiry+2, transport=transport)
    assert one['missing_official_delivery'] == 1
    assert two['settled'] == 0 and len(calls) == 1
    assert not (tmp_path/'outcomes.jsonl').exists()
