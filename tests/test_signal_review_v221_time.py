"""New availability semantics and old immutable review/budget contracts."""
import copy
import json
from pathlib import Path
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import signal_review_v2 as review
import signal_review_v2_runtime as runtime
from test_signal_review_v22 import packet, card, payload_v22 as _payload, side_v22, role, AS_OF_MS


def payload_v22():
    side = side_v22('B', [role('F_STRUCTURE'), role('F_COUNTER', 'counters_fit')], primary_counter_ref='F_COUNTER')
    return _payload(side, side)


def current_packet():
    result = packet()
    result['schema'] = 'signal_evidence_packet@2.1.1'
    for fact in result['facts']:
        fact['available_at_ms'] = AS_OF_MS - 500
        fact['observed_at_ms'] = None
        fact['provenance'] = dict(observed_at_ms=None, generated_at_ms=AS_OF_MS - 2000,
                                  fetched_at_ms=AS_OF_MS - 500, recorded_at_ms=AS_OF_MS,
                                  time_basis='upstream_generated', time_errors=[])
    return result


def test_generated_only_and_fetch_only_are_limited_usable_facts():
    p = current_packet()
    for generated in (AS_OF_MS - 2000, None):
        for fact in p['facts']:
            fact['provenance']['generated_at_ms'] = generated
        built = review.build_review(card(), payload_v22(), p)
        assert built['status'] == 'OK'
        assert built['prompt_version'] == 'signal_llm_review_prompt@2.2.1'
        assert built['schema_version'] == 'signal_llm_review@2.2.0'
        review.validate_persisted_review(built)
        assert all(f['observed_at_ms'] is None for f in built['integrated_trade_advisory']['market_facts'])


def test_any_known_future_time_and_invalid_available_is_rejected():
    for location, key, value in [
        ('fact', 'available_at_ms', None), ('fact', 'available_at_ms', True),
        ('fact', 'available_at_ms', AS_OF_MS + 1), ('fact', 'observed_at_ms', AS_OF_MS + 1),
        ('provenance', 'generated_at_ms', AS_OF_MS + 1),
        ('provenance', 'fetched_at_ms', AS_OF_MS + 1),
        ('provenance', 'observed_at_ms', 'bad'),
        ('provenance', 'time_errors', ['invalid_generated_at']),
    ]:
        p = current_packet()
        fact = p['facts'][0]
        (fact if location == 'fact' else fact['provenance'])[key] = value
        indexed = review._fact_index(p)[fact['id']]
        assert not review._fact_is_usable(indexed, as_of_ms=AS_OF_MS)[0], (location, key, value)
    p = current_packet()
    del p['facts'][0]['available_at_ms']
    assert not review._fact_is_usable(review._fact_index(p)['F_STRUCTURE'], as_of_ms=AS_OF_MS)[0]


def test_old_prompt_packet_and_review_hash_stay_on_original_contract():
    p = packet()
    p['schema'] = 'signal_evidence_packet@2.1.0'
    built = review.build_review(card(), payload_v22(), p, prompt_version='signal_llm_review_prompt@2.2.0')
    original = copy.deepcopy(built)
    review.validate_persisted_review(built)
    assert built == original
    assert built['evidence_context']['schema'] == p['schema']
    assert 'available_at_ms' not in built['integrated_trade_advisory']['market_facts'][0]
    assert review.supported_review_protocol(built) == '2.2'


def test_prompt_upgrade_freezes_finished_and_unfinished_v220_allowances():
    p = packet()
    p['schema'] = 'signal_evidence_packet@2.1.0'
    old_prompt = 'signal_llm_review_prompt@2.2.0'
    old_review = review.build_review(card(), payload_v22(), p, prompt_version=old_prompt)
    record = {'card_id': card()['identity']['card_id'], 'llm_review': old_review}
    for settled in (True, False):
        with tempfile.TemporaryDirectory() as directory:
            states = Path(directory)
            state = dict(packet=p, prompt=old_prompt, attempts=[{'number': 1, 'status': 'RESERVED'}],
                         settled=settled, record=record)
            path = states / (runtime._state_key(record['card_id'], old_prompt, runtime.MODE, review.DEFAULT_MODEL) + '.json')
            original = json.dumps(state)
            path.write_text(original, encoding='utf-8')
            assert runtime._state_path(states, record['card_id'], review.DEFAULT_MODEL) == path
            def no_http(*args, **kwargs):
                raise AssertionError('version change must not issue HTTP')
            result, called = runtime._run_card(card(), current_packet(), path, None, review.DEFAULT_MODEL,
                                              240, 'unused', None, no_http, None)
            assert not called and path.read_text(encoding='utf-8') == original
            if settled:
                assert result == record
            else:
                assert result['llm_review']['retry_budget']['used'] == 1


def test_prompt_explains_unknown_observation_without_redefining_ratings():
    request = review.build_request(current_packet(), review.DEFAULT_MODEL)
    content = request['messages'][1]['content']
    assert '真实观测时间未知但卡时已可得' in content
    assert '不因此统一限级' in content
    assert 'available_at_ms' in content
