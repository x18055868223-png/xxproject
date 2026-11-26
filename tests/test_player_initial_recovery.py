"""Interrupt the actual scheduled entry points. All network transports are fixtures."""
import json
import threading

import pytest

from test_astra_player_tracking_v13 import EXCERPTS, RULE, sample, serving, post
from test_astra_player_dossier_v1 import Clock, SLOT, plan, source
from tools import astra_player_dossier_v1 as dossier
from tools import astra_player_tracking_v13 as player
from tools import signal_llm_review


class PowerLoss(BaseException):
    pass


def model_reply(action):
    annotation = {**EXCERPTS, 'depends_on_ard': False}
    lines = [action, *EXCERPTS.values()]
    if action.startswith('Accept'):
        annotation['condition_plan'] = RULE
        lines.extend(RULE[k] for k in ('historical_facts', 'accepted_conditions', 'confirmation_excerpt'))
    text = '\n'.join(lines) + '\n<ANNOTATION_JSON>' + json.dumps(annotation, ensure_ascii=False) + '</ANNOTATION_JSON>'
    return {'choices': [{'message': {'content': text}, 'finish_reason': 'stop'}]}


@pytest.mark.parametrize('phase', ['before_receipt', 'after_receipt', 'after_attempt', 'late_receipt'])
def test_restart_from_each_persisted_boundary(tmp_path, monkeypatch, phase):
    clock = Clock(SLOT + dossier.MINUTE)
    d = dossier.Dossiers(tmp_path, clock)
    calls = []
    monkeypatch.setattr(dossier, 'collect', lambda *a, **kw: source(clock))
    def send(*args, **kwargs):
        calls.append(True)
        if phase == 'before_receipt':
            raise PowerLoss
        return model_reply('Reject')
    def cut(*args, **kwargs):
        raise PowerLoss
    with monkeypatch.context() as m:
        if phase in {'after_receipt', 'late_receipt'}:
            m.setattr(player.review, 'parse_initial_reply', cut)
        elif phase == 'after_attempt':
            original = d.select
            def stop_after_publish(hand):
                if dossier.attempts(hand):
                    raise PowerLoss
                return original(hand)
            m.setattr(d, 'select', stop_after_publish)
        with pytest.raises(PowerLoss):
            player.auto_deal(d, plan(), tmp_path, None, llm_config={'transport': send})
    h = next(tmp_path.glob('dossiers/*/hands/hand_*'))
    if phase == 'late_receipt':
        clock.ms += dossier.DAY  # recovery after expiry still records the late receipt
    restarted = dossier.Dossiers(tmp_path, clock)
    player.recover_initial(restarted, None)
    player.auto_deal(restarted, plan(), tmp_path, None, llm_config={'transport': send})
    assert len(calls) == 1 and len(dossier.attempts(h)) == 1
    attempt = dossier.read(dossier.attempts(h)[0])
    expected = {'before_receipt':'REQUEST_FAILED', 'late_receipt':'LATE'}.get(phase, 'VALID')
    assert attempt['status'] == expected
    assert (h / 'decision.json').exists() == (expected == 'VALID')
    assert not (h / 'tracking/link.json').exists()
    if phase == 'before_receipt':
        assert player.audit_record(h, clock(), {})['stage'] == 'DELIVERY_UNKNOWN'



def test_concurrent_dispatch_and_sync_cannot_steal_or_repeat_request(tmp_path, monkeypatch):
    clock = Clock(SLOT + dossier.MINUTE)
    d = dossier.Dossiers(tmp_path, clock)
    monkeypatch.setattr(dossier, 'collect', lambda *a, **kw: source(clock))
    started, release = threading.Event(), threading.Event()
    calls = []
    def send(*args, **kwargs):
        calls.append(1)
        started.set()
        assert release.wait(8)
        return model_reply('Reject')
    config = {'transport': send}
    result = []
    th = threading.Thread(target=lambda: result.extend(player.auto_deal(d, plan(), tmp_path, None, llm_config=config)))
    th.start()
    try:
        assert started.wait(8)
        assert player.recover_initial(d, None) == []
        assert player.auto_deal(d, plan(), tmp_path, None, llm_config=config) == [{'status':'AUTO_IN_FLIGHT'}]
        assert player.current_scheduled(tmp_path, clock())['stage'] == 'REQUEST_IN_PROGRESS'
        with dossier.writer(d.root):
            pass
    finally:
        release.set()
        th.join(10)
    assert result[0]['decision'] == 'Reject' and calls == [1]


def test_expired_damaged_hand_does_not_block_current_slot(tmp_path, monkeypatch):
    clock = Clock(SLOT + dossier.MINUTE)
    d = dossier.Dossiers(tmp_path, clock)
    policy = dict(plan(), effective_at_ms=SLOT-dossier.DAY)
    old = d.expect(SLOT-dossier.DAY+policy['expiry_offset_ms'],'SCHEDULED',plan=policy,slot=SLOT-dossier.DAY)
    dossier.write(old/'tracking/request.json',b'{}')
    dossier.write(old/'evidence/identity.json',b'{}')
    monkeypatch.setattr(dossier, 'collect', lambda *a, **kw: source(clock))
    result = player.auto_deal(d, policy, tmp_path, None, llm_config={'transport': lambda *a, **kw: model_reply('Reject')})
    assert [row['decision'] for row in result] == ['Reject']
    assert player.current_scheduled(tmp_path, clock())['hand_id'] != old.name
    assert (old/'tracking/request.json').read_bytes() == b'{}'


def test_accept_decision_restarts_and_links_once_through_native_service(tmp_path, monkeypatch):
    wb, d, old, now, expiry = sample(tmp_path)
    hand = d.expect(expiry, 'AD_HOC', reference='interrupt')
    d.deal(hand, dossier.read(old/'evidence/source.json'))
    calls = []
    def send(*a, **kw):
        calls.append(True)
        return model_reply('Accept / Call')
    cfg = player._llm_settings({'transport':send})
    with serving(wb) as base:
        service = post(base, wb)
        with monkeypatch.context() as m:
            def crash(*a, **kw):
                raise PowerLoss
            m.setattr(player, '_link_with_fresh_quote', crash)
            with pytest.raises(PowerLoss):
                player._deal_hand(d, hand, service, cfg)
        assert (hand/'decision.json').exists() and not (hand/'tracking/request.json').exists()
        player._deal_hand(d, hand, service, {}, recover_only=True)
        link = dossier.read(hand/'tracking/link.json')
        player._deal_hand(d, hand, service, {}, recover_only=True)
        assert dossier.read(hand/'tracking/link.json') == link and calls == [True]
        assert link['execution_allowed'] is False


def test_frozen_legacy_deadline_only_accepts_known_shorter_window(tmp_path):
    wb, d, hand, now, _ = sample(tmp_path)
    with serving(wb) as base:
        player.link(d, hand, post(base, wb), RULE)
    path = hand/'tracking/request.json'
    req = dossier.read(path)
    legacy = req['accepted_at_ms'] + 120_000
    for delta, valid in [(0, True), (1, False), (1_000_000, False)]:
        dossier.write(path, dossier.encode({**req,'deadline_ms':legacy+delta}),replace=True)
        if valid:
            proof, _ = player.request_proof(hand)
            assert proof['deadline_ms'] == legacy
        else:
            with pytest.raises(player.Blocked, match='identity_mismatch'):
                player.request_proof(hand)



def test_formal_timer_to_native_tracking_and_fixed_check(tmp_path, monkeypatch):
    import test_astra_player_tracking_v13 as fixtures
    import test_astra_workbench_v20 as market
    slot = market.EXPIRY - 16 * 3_600_000
    monkeypatch.setattr(market, 'T', slot + dossier.MINUTE)
    monkeypatch.setitem(RULE, 'check_interval_ms', 5*dossier.MINUTE)
    monkeypatch.setitem(RULE, 'confirmation_excerpt', 'UTC 五分钟固定时钟确认。')

    def market_at_deal(path):
        now = [market.T]
        wb, snapshots = market.make_workbench(path, clock=lambda: now[0], api_key=None)
        market.write_fmz(snapshots, market.native_gate(ts=now[0]-1000), now=now[0])
        view = wb.refresh(now_ms=now[0], record_daily=False)
        return wb, snapshots, now, view['candidates'][0]['snapshot']

    monkeypatch.setattr(fixtures, 'setup', market_at_deal)
    wb, d, other, now, expiry = fixtures.sample(tmp_path)
    policy = dict(plan(), effective_at_ms=slot, candidate_round='r16', expiry_offset_ms=16*3_600_000)
    packet = dossier.read(other/'evidence/source.json')
    calls = []
    def collect(*args, **kwargs):
        with dossier.writer(d.root):
            pass  # Slow market reads do not monopolize the dossier writer.
        return packet
    def transport(*args, **kwargs):
        calls.append(True)
        return model_reply('Accept / Call')
    monkeypatch.setattr(dossier, 'collect', collect)
    with serving(wb) as base:
        service = post(base, wb)
        row, = player.auto_deal(d, policy, tmp_path, service, llm_config={'transport':transport})
        assert row['decision'] == 'Accept' and row['link']['state'] == 'SHADOW_LOCK_CREATED'
        hand = other.parent/row['hand_id']
        assert dossier.read(hand/'expected.json')['sample'] == 'SCHEDULED'
        ep = hand.parent.parent/'episodes'/row['link']['origin']['dossier_episode_id']
        frozen = dossier.read(ep/'condition.json')
        assert frozen['check_clock']['interval_ms'] == 5 * dossier.MINUTE
        tick = frozen['check_clock']['first_at_ms']
        now[0] = tick + dossier.MINUTE
        facts = dict(expiry_ms=expiry,index_name='btc_usd',points=[[tick,103_100]])
        player.sync(d,hand,service,index_path=facts,check_due=True)
        assert any(dossier.read(p)['kind'].startswith('CONDITION_') for p in (ep/'events').glob('*.json'))
        assert calls == [True]


def test_transport_typeerror_and_ledger_failure_cannot_reissue(tmp_path, monkeypatch):
    clock = Clock(SLOT + dossier.MINUTE)
    d = dossier.Dossiers(tmp_path, clock)
    monkeypatch.setattr(dossier, 'collect', lambda *a, **kw: source(clock))
    class Ledger:
        def reserve(self, **kw): return {'reservation_id':'fixture'}
        def complete(self, *a, **kw): raise OSError('mock accounting storage unavailable')
    def send(*a, **kw):
        return model_reply('Reject')
    row, = player.auto_deal(d,plan(),tmp_path,None,llm_config={'transport':send,'budget':Ledger()})
    assert row['decision'] == 'Reject'
    calls = []
    def uncertain(*a,**kw):
        calls.append(1)
        raise TypeError("unexpected keyword after transport")
    with pytest.raises(TypeError):
        player.review._invoke_transport(uncertain,'k','m',{},1,'http://localhost')
    assert calls == [1]


def test_llm_api_error_400_records_provider_rejected(tmp_path, monkeypatch):
    clock = Clock(SLOT + dossier.MINUTE)
    d = dossier.Dossiers(tmp_path, clock)
    monkeypatch.setattr(dossier, 'collect', lambda *a, **kw: source(clock))

    def bad_request(*a, **kw):
        raise signal_llm_review.LlmApiError(400, 'bad request')

    row, = player.auto_deal(d, plan(), tmp_path, None, llm_config={'transport': bad_request})
    hand = next(tmp_path.glob('dossiers/*/hands/hand_*'))
    attempt = dossier.read(dossier.attempts(hand)[0])
    assert row['status'] == 'REQUEST_FAILED'
    assert attempt['status'] == 'REQUEST_FAILED'
    assert attempt['configuration']['http_status'] == 400
    assert attempt['configuration']['delivery_state'] == 'PROVIDER_REJECTED'
    assert player.audit_record(hand, clock(), {})['stage'] == 'PROVIDER_REJECTED'


def test_corrupt_sibling_adhoc_hand_does_not_block_scheduled_auto_deal(tmp_path, monkeypatch):
    clock = Clock(SLOT + dossier.MINUTE)
    d = dossier.Dossiers(tmp_path, clock)
    policy = plan()
    expiry = SLOT + policy['expiry_offset_ms']
    corrupt = d.expect(expiry, 'AD_HOC', reference='corrupt-sibling')
    dossier.write(corrupt / 'expected.json', b'{not-valid-json', replace=True)
    monkeypatch.setattr(dossier, 'collect', lambda *a, **kw: source(clock))
    row, = player.auto_deal(d, policy, tmp_path, None, llm_config={'transport': lambda *a, **kw: model_reply('Reject')})
    assert row['status'] == 'VALID'
    assert row['decision'] == 'Reject'


def test_daily_budget_exceeded_after_reply_deadline_reports_dispatch_expired(tmp_path, monkeypatch):
    clock = Clock(SLOT + dossier.MINUTE)
    d = dossier.Dossiers(tmp_path / 'dossier', clock)
    monkeypatch.setattr(dossier, 'collect', lambda *a, **kw: source(clock))
    zero_budget = signal_llm_review.DailyHttpBudget(tmp_path / 'zero_budget.json', limit=0)
    policy = plan()
    row, = player.auto_deal(
        d, policy, tmp_path, None,
        llm_config={'budget': zero_budget, 'transport': lambda *a, **kw: model_reply('Reject')},
    )
    assert row['status'] == 'DAILY_BUDGET_EXHAUSTED'
    hand = next((tmp_path / 'dossier' / 'dossiers').glob('Deribit_BTC_*/hands/hand_*'))
    call_dir = next((hand / 'api_calls').glob('*'))
    assert (call_dir / 'dispatch.json').is_file()
    assert (call_dir / 'not_sent.json').is_file()
    assert player.audit_record(hand, clock(), {})['stage'] == 'DAILY_BUDGET_EXHAUSTED'

    clock.ms = SLOT + dossier.MINUTE + policy['reply_grace_ms'] + 1_000
    assert player.audit_record(hand, clock(), {})['stage'] == 'DISPATCH_EXPIRED'
    d.tick(policy, slots=[SLOT])
    assert player.audit_record(hand, clock(), {})['stage'] == 'DISPATCH_EXPIRED'
    clock.ms += dossier.DAY
    assert player.recover_initial(d, None) == []
    assert player.audit_record(hand, clock(), {})['stage'] == 'DISPATCH_EXPIRED'


def test_current_scheduled_picks_max_scheduled_at_and_skips_corrupt_dossier(tmp_path):
    hour = 60 * dossier.MINUTE
    clock = Clock(SLOT + 3 * hour)
    d = dossier.Dossiers(tmp_path, clock)
    policy = plan()
    expiry = SLOT + policy['expiry_offset_ms']
    d.expect(expiry, 'SCHEDULED', plan=policy, slot=SLOT)
    policy_r16 = dict(policy, plan_id='TEST-PLAN-R16', candidate_round='r16',
                      effective_at_ms=SLOT + 2 * hour, expiry_offset_ms=16 * hour)
    latest = d.expect(expiry, 'SCHEDULED', plan=policy_r16, slot=SLOT + 2 * hour)
    dossier.write(
        d.path(expiry) / 'hands' / 'hand_ffffffffffffffffffffffff' / 'expected.json',
        dossier.encode(dict(
            dossier_id=d.path(expiry).name,
            hand_id='hand_ffffffffffffffffffffffff',
            sample='SCHEDULED',
            scheduled_at_ms=SLOT + 4 * hour,
            expiry_ms=expiry,
        )),
    )

    corrupt_dossier = tmp_path / 'dossiers' / 'Deribit_BTC_20990101T080000Z' / 'hands'
    dossier.write(corrupt_dossier / 'hand_000000000000000000000001' / 'expected.json', b'{bad-json')
    dossier.write(
        corrupt_dossier / 'hand_000000000000000000000002' / 'expected.json',
        dossier.encode(dict(
            dossier_id='Deribit_BTC_20990101T080000Z',
            hand_id='hand_wrong_name',
            sample='SCHEDULED',
            scheduled_at_ms=SLOT + 3 * hour,
            expiry_ms=expiry,
        )),
    )

    current = player.current_scheduled(tmp_path, clock())
    assert current is not None
    assert current['hand_id'] == latest.name
    assert current['scheduled_at_ms'] == SLOT + 2 * hour


def test_malformed_reply_json_missing_text_records_parse_failed(tmp_path):
    clock = Clock(SLOT + dossier.MINUTE)
    d = dossier.Dossiers(tmp_path, clock)
    hand, = d.tick(plan(), slots=[SLOT])
    d.deal(hand, source(clock))
    req = player.review.build_initial_request_payload(
        (hand / 'evidence' / 'input.md').read_text(encoding='utf-8'),
        contract_pair=[],
        model='m-test',
        requested_at_ms=clock(),
    )
    call = hand / 'api_calls' / 'auto_malformed_reply'
    dossier.write(call / 'request.json', dossier.encode(player.review.sanitize_model_request(req)))
    dossier.write(call / 'reply.json', dossier.encode({'model': 'm-test', 'usage': {'total_tokens': 5}}))

    out = player.recover_initial(d, None)
    assert out == [{'hand_id': hand.name, 'status': 'AUTO_ATTEMPTED', 'last_status': 'PARSE_FAILED'}]
    att = dossier.read(dossier.attempts(hand)[0])
    assert att['status'] == 'PARSE_FAILED'
    assert player.audit_record(hand, clock(), {})['state'] == 'PARSE_FAILED'


def test_legacy_120s_request_with_retryable_blocked_after_expiry_is_skipped_cleanly(tmp_path):
    clock = Clock(SLOT + dossier.MINUTE)
    d = dossier.Dossiers(tmp_path, clock)
    policy = plan()
    expiry = SLOT + policy['expiry_offset_ms']
    old = d.expect(expiry, 'SCHEDULED', plan=policy, slot=SLOT)
    d.deal(old, source(clock))
    clock.ms += 1_000
    raw = (
        '**Accept / Call**\n'
        + '\n'.join(EXCERPTS.values())
        + '\n'
        + '\n'.join(RULE[k] for k in ('historical_facts', 'accepted_conditions', 'confirmation_excerpt'))
    ).encode('utf-8')
    d.respond(
        old, 'legacy-reply', 'RECEIVED', raw, {**EXCERPTS, 'depends_on_ard': False},
        model='m-legacy', requested=clock() - 500, decided=clock(),
        configuration={'reasoning_effort': 'low'},
    )
    d.select(old)
    proof = player.accepted(old)
    frozen_plan = player.condition_plan(proof, RULE)
    proof['origin']['condition_sha256'] = dossier.sha(dossier.encode(frozen_plan))
    legacy_deadline = min(proof['deadline_ms'], proof['origin']['decided_at_ms'] + 120_000)
    dossier.write(
        old / 'tracking' / 'request.json',
        dossier.encode(dict(
            state='SHADOW_LOCK_REQUESTED',
            accepted_at_ms=proof['origin']['decided_at_ms'],
            requested_at_ms=clock(),
            deadline_ms=legacy_deadline,
            origin=proof['origin'],
            condition=frozen_plan,
        )),
    )
    dossier.write(
        old / 'tracking' / 'blocked.json',
        dossier.encode(dict(
            state='LOCK_BLOCKED',
            reason='tracking_quote_stale_or_unsynced',
            recorded_at_ms=clock(),
        )),
    )
    before = {p.relative_to(old): p.read_bytes() for p in sorted(old.rglob('*')) if p.is_file()}

    clock.ms = expiry + dossier.MINUTE
    svc_calls, llm_calls = [], []
    def fail_service(*a, **kw):
        svc_calls.append((a, kw))
        raise AssertionError('service transport must not be called for expired legacy hand')
    def fail_llm(*a, **kw):
        llm_calls.append((a, kw))
        raise AssertionError('LLM transport must not be called for expired legacy hand')

    assert player.recover_initial(d, fail_service) == []
    assert player.auto_deal(d, policy, tmp_path, fail_service, llm_config={'transport': fail_llm}) == []
    after = {p.relative_to(old): p.read_bytes() for p in sorted(old.rglob('*')) if p.is_file()}
    assert after == before
    assert svc_calls == []
    assert llm_calls == []


def test_call_chat_llm_malformed_http_status_completes_budget_without_reserved_leak(tmp_path):
    budget = signal_llm_review.DailyHttpBudget(tmp_path / 'budget.json', limit=5)

    def malformed_status_transport(*a, **kw):
        return {
            '_http_status': 'not-an-int',
            'model': 'deepseek-chat',
            'choices': [{'finish_reason': 'stop', 'message': {'content': 'ok'}}],
            'usage': {'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5},
        }

    res = player.review.call_chat_llm(
        [{'role': 'user', 'content': 'ping'}],
        api_key='k',
        model='deepseek-chat',
        transport=malformed_status_transport,
        budget=budget,
        on_response=lambda reply: None,
    )
    assert res['http_status'] == 200
    snap = budget.snapshot()
    assert snap['http_calls_used'] == 1
    assert snap['events'][-1]['status_category'] == 'HTTP_OK'
    assert not any(ev.get('status_category') == 'RESERVED' for ev in snap['events'])


def test_hand_5249_timeout_saves_partial_keeps_delivery_unknown_and_never_replays(tmp_path, monkeypatch):
    clock = Clock(SLOT + dossier.MINUTE)
    d = dossier.Dossiers(tmp_path, clock)
    monkeypatch.setattr(dossier, 'collect', lambda *a, **kw: source(clock))
    monkeypatch.setenv('LLM_TIMEOUT_SEC', '360')
    monkeypatch.delenv('ASTRA_PLAYER_TIMEOUT_SEC', raising=False)

    calls = []
    seen_timeouts = []

    def streaming_timeout_transport(api_key, model, body, timeout_s, endpoint=None):
        del api_key, body, endpoint
        calls.append(model)
        seen_timeouts.append(timeout_s)
        partial = {
            'text': '## 初步分析\nPut 短腿暂在外侧，继续推导...',
            'model': model,
            'chunks': 3,
            'usage': {'prompt_tokens': 1200, 'completion_tokens': 420, 'total_tokens': 1620},
        }
        err = TimeoutError('read timed out after 600s')
        err.partial_response = partial
        raise err

    r1, = player.auto_deal(
        d, plan(), tmp_path, None,
        llm_config={
            'api_key': 'k', 'model': 'deepseek-reasoner', 'reasoning_effort': 'high',
            'transport': streaming_timeout_transport,
        },
    )
    assert r1['status'] == 'TIMEOUT'
    # Generic LLM_TIMEOUT_SEC=360 is raised to >=600s floor for initial reasoner underwriting
    assert seen_timeouts == [600.0]

    hand = next(tmp_path.glob('dossiers/*/hands/hand_*'))
    call_dir = next((hand / 'api_calls').glob('*'))
    assert (call_dir / 'partial_reply.json').is_file()
    assert not (call_dir / 'reply.json').exists()
    assert not (hand / 'decision.json').exists()
    assert dossier.read(call_dir / 'dispatch.json')['timeout_s'] == 600.0

    att = dossier.read(dossier.attempts(hand)[0])
    assert att['status'] == 'TIMEOUT'
    assert att['configuration']['delivery_state'] == 'DELIVERY_UNKNOWN'
    assert att['configuration']['partial_chunks'] == 3
    assert att['configuration']['partial_bytes'] > 0
    assert att['configuration']['partial_usage']['completion_tokens'] == 420
    assert not (hand / 'responses' / att['attempt_id'] / 'response.raw').exists()

    aud = player.audit_record(hand, clock(), {}, detail=True)
    assert aud['state'] == 'TIMEOUT'
    assert aud['stage'] == 'DELIVERY_UNKNOWN'
    assert aud['attempts'][0]['partial_chunks'] == 3

    # Next recover_initial and auto_deal ticks MUST NOT auto-replay DELIVERY_UNKNOWN timeout
    clock.ms += 5 * dossier.MINUTE
    assert player.recover_initial(d, None) == []
    r2, = player.auto_deal(
        d, plan(), tmp_path, None,
        llm_config={'api_key': 'k', 'model': 'deepseek-reasoner'},
    )
    assert r2 == {'hand_id': hand.name, 'status': 'AUTO_ATTEMPTED', 'last_status': 'TIMEOUT'}
    assert len(calls) == 1
    assert len(dossier.attempts(hand)) == 1


def test_hand_d62e_length_parse_failed_bounded_retry_succeeds_and_preserves_ordinal1(tmp_path, monkeypatch):
    clock = Clock(SLOT + dossier.MINUTE)
    d = dossier.Dossiers(tmp_path, clock)
    monkeypatch.setattr(dossier, 'collect', lambda *a, **kw: source(clock))
    monkeypatch.delenv('ASTRA_PLAYER_INITIAL_MAX_ATTEMPTS', raising=False)

    calls = []

    def transport(*args, **kwargs):
        del args, kwargs
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            return {
                'model': 'deepseek-reasoner',
                'choices': [{'message': {'content': '## Reject\n正文中途被截断，缺注解块'}, 'finish_reason': 'length'}],
                'usage': {'prompt_tokens': 500, 'completion_tokens': 24576, 'total_tokens': 25076},
            }
        return model_reply('Reject')

    r1, = player.auto_deal(d, plan(), tmp_path, None, llm_config={'api_key': 'k', 'model': 'deepseek-reasoner', 'transport': transport})
    assert r1['status'] == 'PARSE_FAILED'
    hand = next(tmp_path.glob('dossiers/*/hands/hand_*'))
    att1_before = dossier.read(dossier.attempts(hand)[0])
    assert att1_before['ordinal'] == 1
    assert att1_before['status'] == 'PARSE_FAILED'
    assert att1_before['configuration']['delivery_state'] == 'RECEIVED'
    call1_bytes = (hand / 'api_calls' / att1_before['attempt_id'] / 'reply.json').read_bytes()

    # recover_initial / recover_only=True must NEVER call the LLM even for PARSE_FAILED
    clock.ms += 2 * dossier.MINUTE
    assert player.recover_initial(d, None) == []
    rec_only = player._deal_hand(d, hand, None, {}, recover_only=True)
    assert rec_only == {'hand_id': hand.name, 'status': 'AUTO_ATTEMPTED', 'last_status': 'PARSE_FAILED'}
    assert calls == [1]

    # Next auto_deal tick inside reply_deadline retries once (max_attempts=2) and succeeds
    clock.ms += 3 * dossier.MINUTE
    r2, = player.auto_deal(d, plan(), tmp_path, None, llm_config={'api_key': 'k', 'model': 'deepseek-reasoner', 'transport': transport})
    assert r2['status'] == 'VALID' and r2['decision'] == 'Reject'
    assert calls == [1, 2]
    attempts = dossier.attempts(hand)
    assert [dossier.read(p)['ordinal'] for p in attempts] == [1, 2]
    assert dossier.read(attempts[0]) == att1_before
    assert (hand / 'api_calls' / att1_before['attempt_id'] / 'reply.json').read_bytes() == call1_bytes
    assert dossier.read(attempts[1])['status'] == 'VALID'
    assert dossier.read(hand / 'decision.json')['attempt_id'] == dossier.read(attempts[1])['attempt_id']

    # Bounded at max_initial_attempts=2 when both attempts fail PARSE_FAILED
    d_cap = dossier.Dossiers(tmp_path / 'bounded_cap', clock)
    cap_calls = []

    def always_truncated(*args, **kwargs):
        del args, kwargs
        cap_calls.append(1)
        return {'choices': [{'message': {'content': '截断输出无JSON'}, 'finish_reason': 'length'}], 'usage': {'total_tokens': 99}}

    assert player.auto_deal(d_cap, plan(), tmp_path, None, llm_config={'api_key': 'k', 'transport': always_truncated})[0]['status'] == 'PARSE_FAILED'
    clock.ms += dossier.MINUTE
    assert player.auto_deal(d_cap, plan(), tmp_path, None, llm_config={'api_key': 'k', 'transport': always_truncated})[0]['status'] == 'PARSE_FAILED'
    clock.ms += dossier.MINUTE
    r3, = player.auto_deal(d_cap, plan(), tmp_path, None, llm_config={'api_key': 'k', 'transport': always_truncated})
    assert r3['status'] == 'AUTO_ATTEMPTED' and r3['last_status'] == 'PARSE_FAILED'
    assert len(cap_calls) == 2

    # Window < 30s remaining must NOT retry PARSE_FAILED
    d_win = dossier.Dossiers(tmp_path / 'window_cap', clock)
    win_hand, = d_win.tick(plan(), slots=[SLOT])
    d_win.deal(win_hand, source(clock))
    win_calls = []
    def trunc_once(*a, **kw):
        win_calls.append(1)
        return {'choices': [{'message': {'content': '截断'}, 'finish_reason': 'length'}]}
    assert player._deal_hand(d_win, win_hand, None, player._llm_settings({'api_key': 'k', 'transport': trunc_once}))['status'] == 'PARSE_FAILED'
    win_meta = d_win.verify_evidence(win_hand)
    win_deadline = dossier.Dossiers.reply_deadline(dossier.read(win_hand / 'expected.json'), win_meta)
    clock.ms = win_deadline - 10_000
    assert player._deal_hand(d_win, win_hand, None, player._llm_settings({'api_key': 'k', 'transport': trunc_once}))['status'] == 'AUTO_ATTEMPTED'
    assert win_calls == [1]

    # DAILY_BUDGET_EXHAUSTED today must NOT retry PARSE_FAILED
    d_win.note(win_hand, 'DAILY_BUDGET_EXHAUSTED', 'cap reached')
    clock.ms = win_deadline - 120_000
    assert player._deal_hand(d_win, win_hand, None, player._llm_settings({'api_key': 'k', 'transport': trunc_once}))['status'] == 'DAILY_BUDGET_EXHAUSTED'
    assert win_calls == [1]


def test_hand_af89_sibling_lock_mismatch_and_condition_plan_missing_isolate_current_slot(tmp_path, monkeypatch):
    import sys
    clock = Clock(SLOT + dossier.MINUTE)
    d = dossier.Dossiers(tmp_path, clock)
    policy = dict(plan(), effective_at_ms=SLOT)
    old_hand = d.expect(SLOT + policy['expiry_offset_ms'], 'AD_HOC', reference='af89-sibling')
    d.deal(old_hand, source(clock))
    req_t = clock()
    clock.ms += 100
    raw = (
        '**Accept / Call**\n' + '\n'.join(EXCERPTS.values()) + '\n'
        + '\n'.join(RULE[k] for k in ('historical_facts', 'accepted_conditions', 'confirmation_excerpt'))
    ).encode('utf-8')
    d.respond(old_hand, 'old-valid', 'RECEIVED', raw, {**EXCERPTS, 'depends_on_ard': False},
              model='m-old', requested=req_t, decided=clock(), configuration={'reasoning_effort': 'low'})
    d.select(old_hand)
    proof = player.accepted(old_hand)
    c_plan = player.condition_plan(proof, RULE)
    proof['origin']['condition_sha256'] = dossier.sha(dossier.encode(c_plan))
    bad_origin = {**proof['origin'], 'hand_id': 'hand_000000000000000000000000'}
    dossier.write(
        old_hand / 'tracking' / 'request.json',
        dossier.encode(dict(
            state='SHADOW_LOCK_REQUESTED',
            accepted_at_ms=proof['origin']['decided_at_ms'],
            requested_at_ms=clock(),
            deadline_ms=proof['deadline_ms'],
            origin=bad_origin,
            condition=c_plan,
        )),
    )

    # link() seals blocked.json instead of raising uncaught lock_request_identity_mismatch
    res_bad = player.link(d, old_hand, lambda *a, **kw: pytest.fail('service must not be called on identity mismatch'))
    assert res_bad == {'state': 'LOCK_BLOCKED', 'reason': 'lock_request_identity_mismatch'}
    assert dossier.read(old_hand / 'tracking' / 'blocked.json')['reason'] == 'lock_request_identity_mismatch'
    assert player._link_blocked(old_hand / 'tracking')['reason'] == 'lock_request_identity_mismatch'

    # Sibling current slot still deals cleanly in auto_deal
    monkeypatch.setattr(dossier, 'collect', lambda *a, **kw: source(clock))
    out = player.auto_deal(d, policy, tmp_path, None, llm_config={'transport': lambda *a, **kw: model_reply('Reject')})
    assert any(r.get('decision') == 'Reject' and r.get('status') == 'VALID' for r in out)

    # condition_plan_missing seals blocked.json and causes main() auto-deal to exit 2 visibly
    d_miss = dossier.Dossiers(tmp_path / 'miss_plan', clock)
    miss_hand, = d_miss.tick(plan(), slots=[SLOT])
    d_miss.deal(miss_hand, source(clock))
    req_miss = clock()
    clock.ms += 50
    miss_raw = ('**Accept / Call**\n' + '\n'.join(EXCERPTS.values())).encode('utf-8')
    d_miss.respond(miss_hand, 'miss-plan', 'RECEIVED', miss_raw, {**EXCERPTS, 'depends_on_ard': False},
                   model='m-miss', requested=req_miss, decided=clock(), configuration={'reasoning_effort': 'low'})
    d_miss.select(miss_hand)
    res_miss = player._deal_hand(d_miss, miss_hand, None, {})
    assert res_miss['status'] == 'ALREADY_DECIDED'
    assert res_miss['link'] == {'state': 'LOCK_BLOCKED', 'reason': 'condition_plan_missing'}
    assert dossier.read(miss_hand / 'tracking' / 'blocked.json')['terminal'] is True
    assert dossier.read(miss_hand / 'tracking' / 'blocked.json')['reason'] == 'condition_plan_missing'

    d_cli = dossier.Dossiers(tmp_path / 'miss_plan_cli', clock)
    cli_hand, = d_cli.tick(plan(), slots=[SLOT])
    d_cli.deal(cli_hand, source(clock))
    req_cli = clock()
    clock.ms += 50
    d_cli.respond(cli_hand, 'miss-cli', 'RECEIVED', miss_raw, {**EXCERPTS, 'depends_on_ard': False},
                  model='m-miss', requested=req_cli, decided=clock(), configuration={'reasoning_effort': 'low'})
    d_cli.select(cli_hand)
    plan_file = tmp_path / 'plan.json'
    plan_file.write_text(json.dumps(plan()), encoding='utf-8')
    orig_dossiers = dossier.Dossiers

    class ClockedDossiers(orig_dossiers):
        def __init__(self, root, *_a, **_kw):
            super().__init__(root, clock)

    monkeypatch.setattr(player.dossier, 'Dossiers', ClockedDossiers)
    monkeypatch.setattr(
        sys, 'argv',
        ['astra_player_tracking_v13.py', 'auto-deal', '--root', str(d_cli.root),
         '--plan', str(plan_file), '--archive', str(tmp_path)],
    )
    with pytest.raises(SystemExit) as exit_exc:
        player.main()
    assert exit_exc.value.code == 2

    # sync-open isolates a request.json lock_request_identity_mismatch hand and continues syncing sibling open hands
    d_sync = orig_dossiers(tmp_path / 'sync_iso', clock)
    bad_sync = d_sync.expect(SLOT + policy['expiry_offset_ms'], 'AD_HOC', reference='bad-sync-req')
    d_sync.deal(bad_sync, source(clock))
    req_sync = clock()
    clock.ms += 50
    d_sync.respond(bad_sync, 'bad-sync', 'RECEIVED', raw, {**EXCERPTS, 'depends_on_ard': False},
                   model='m-old', requested=req_sync, decided=clock(), configuration={'reasoning_effort': 'low'})
    d_sync.select(bad_sync)
    sync_proof = player.accepted(bad_sync)
    sync_plan = player.condition_plan(sync_proof, RULE)
    sync_proof['origin']['condition_sha256'] = dossier.sha(dossier.encode(sync_plan))
    dossier.write(
        bad_sync / 'tracking' / 'request.json',
        dossier.encode(dict(
            state='SHADOW_LOCK_REQUESTED',
            accepted_at_ms=sync_proof['origin']['decided_at_ms'],
            requested_at_ms=clock(),
            deadline_ms=sync_proof['deadline_ms'],
            origin={**sync_proof['origin'], 'hand_id': 'hand_000000000000000000000000'},
            condition=sync_plan,
        )),
    )
    good_sync = d_sync.expect(SLOT + policy['expiry_offset_ms'], 'AD_HOC', reference='good-sync-link')
    dossier.write(good_sync / 'tracking' / 'link.json', dossier.encode({'state': 'SHADOW_LOCK_CREATED'}))
    synced_hands = []
    monkeypatch.setattr(player, 'recover_initial', lambda *_a, **_kw: [])
    monkeypatch.setattr(player, 'sync', lambda _d, h, _s, **_kw: synced_hands.append(h.name) or {'state': 'active', 'hand_id': h.name})
    monkeypatch.setattr(orig_dossiers, 'summary', lambda *_a, **_kw: tmp_path / 's.md')
    monkeypatch.setattr(sys, 'argv', ['astra_player_tracking_v13.py', 'sync-open', '--root', str(d_sync.root)])
    with pytest.raises(SystemExit) as sync_exit:
        player.main()
    assert sync_exit.value.code == 2
    assert synced_hands == [good_sync.name]
    assert dossier.read(bad_sync / 'tracking' / 'blocked.json')['reason'] == 'lock_request_identity_mismatch'

    # Subsequent sync-open skips the sealed blocked hand and succeeds cleanly (exit 0)
    synced_hands.clear()
    player.main()
    assert synced_hands == [good_sync.name]


def test_clean_eof_sse_truncation_records_delivery_unknown_and_never_retries(tmp_path, monkeypatch):
    import http.client
    clock = Clock(SLOT + dossier.MINUTE)
    d = dossier.Dossiers(tmp_path, clock)
    monkeypatch.setattr(dossier, 'collect', lambda *a, **kw: source(clock))
    calls = []

    def eof_transport(*args, **kwargs):
        calls.append(True)
        exc = http.client.IncompleteRead(b'## Accept / Call')
        exc.partial_response = {
            'http_status': 200,
            'model': 'deepseek-v4-pro',
            'choices': [{'index': 0, 'finish_reason': None, 'message': {'content': '## Accept / Call\n</ANNOTATION_JSON>'}}],
            'chunk_count': 2,
            'bytes_received': 128,
            'usage': {'completion_tokens': 12},
        }
        raise exc

    row, = player.auto_deal(
        d, plan(), tmp_path, None,
        llm_config={'transport': eof_transport, 'model': 'deepseek-v4-pro'},
    )
    assert row['status'] == 'REQUEST_FAILED'
    hand = next(tmp_path.glob('dossiers/*/hands/hand_*'))
    attempt = dossier.read(dossier.attempts(hand)[0])
    assert attempt['status'] == 'REQUEST_FAILED'
    assert attempt['configuration']['delivery_state'] == 'DELIVERY_UNKNOWN'
    assert not (hand / 'responses' / attempt['attempt_id'] / 'response.raw').exists()
    partial = dossier.read(hand / 'api_calls' / attempt['attempt_id'] / 'partial_reply.json')
    assert partial['bytes_received'] == 128

    # Next scheduled tick NEVER replays IncompleteRead / DELIVERY_UNKNOWN
    row2, = player.auto_deal(
        d, plan(), tmp_path, None,
        llm_config={'transport': eof_transport, 'model': 'deepseek-v4-pro'},
    )
    assert calls == [True]
    assert row2 == {'hand_id': hand.name, 'status': 'AUTO_ATTEMPTED', 'last_status': 'REQUEST_FAILED'}

    # classify_sync_open_batch detects nested LOCK_BLOCKED link failure on recover_initial rows
    batch = player.classify_sync_open_batch([
        {'hand_id': hand.name, 'status': 'ALREADY_DECIDED', 'decision': 'Accept',
         'link': {'state': 'LOCK_BLOCKED', 'reason': 'condition_plan_missing'}},
    ])
    assert batch['batch_status'] == 'FAILED' and batch['failed_count'] == 1
    assert batch['failed_hands'][0]['state'] == 'LOCK_BLOCKED'
    assert batch['failed_hands'][0]['reason'] == 'condition_plan_missing'


def _strict_p5_reply_payload(action_header='## Accept / Call', *, short_strike=103_000, long_strike=105_000, require_outside_at_t0=True):
    put_txt = 'Put路径判断：下方短腿存在一定距离，但历史存在快速下探动作，需对比上侧缓冲。'
    call_txt = 'Call路径判断：上方短腿与现价之间保留充足波动余量，历史冲高均在阻力前折返。'
    put_cnt = 'Put最强反证：4h现货主动净卖尚未消除，下侧急挫风险仍在。'
    call_cnt = 'Call最强反证：隐含波动率仍有可能受短线脉冲抬升。'
    comp_txt = '两侧对比择优：综合比较两侧空间结构与已知反证后，择优判断当前条件。'
    hist_txt = '冻结历史事实：读取截止前指数始终位于Call卖出短腿下方，未出现越过短腿采样。'
    acc_txt = (
        '承担条件：正面承担短线波动抬升的不利事实，但上方余量尚未耗尽；'
        '现货跨源时钟存在OBSERVED_UNALIGNED不确定性；'
        '若盘中短腿外侧剩余余量实质收缩且4h现货主动流同向转强，则触发独立于机械M的实质重审。'
    )
    is_call = 'Call' in action_header
    cmp_word = '严格高于' if is_call else '严格低于'
    conf_txt = (
        f'破坏确认口径：采用 entry_not_returned，首次检查为入场后下一UTC整分钟，之后每5分钟（check_interval_ms=300000）执行一次，'
        f'btc_usd 分钟采样{cmp_word}卖出短腿{short_strike:,}即记为进入（包含保护腿{long_strike:,}边界及其外侧）且至检查点仍未严格回到短腿外侧才触发，'
        f'触及短腿不算进入也不算返回。'
    )
    paragraphs = [action_header, put_txt, call_txt, put_cnt, call_cnt, comp_txt]
    ann = {
        'put': put_txt,
        'call': call_txt,
        'put_counter': put_cnt,
        'call_counter': call_cnt,
        'comparison': comp_txt,
        'depends_on_ard': False,
    }
    if action_header.startswith('## Accept'):
        paragraphs.extend([hist_txt, acc_txt, conf_txt])
        ann['condition_plan'] = {
            'invalidation': 'entry_not_returned',
            'check_interval_ms': 300_000,
            'require_outside_at_t0': require_outside_at_t0,
            'historical_facts': hist_txt,
            'accepted_conditions': acc_txt,
            'confirmation_excerpt': conf_txt,
        }
    text = '\n\n'.join(paragraphs) + '\n\n<ANNOTATION_JSON>\n' + json.dumps(ann, ensure_ascii=False) + '\n</ANNOTATION_JSON>'
    return {'choices': [{'message': {'content': text}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 120, 'completion_tokens': 80, 'total_tokens': 200}}


def test_provider_429_and_503_retry_vs_400_401_403_and_mid_sse_delivery_unknown(tmp_path, monkeypatch):
    clock = Clock(SLOT + dossier.MINUTE)
    monkeypatch.setattr(dossier, 'collect', lambda *a, **kw: source(clock))

    # 1) Pre-generation 429 with Retry-After=15s -> PROVIDER_REJECTED, respects retry_after_s, retries once and succeeds
    d_429 = dossier.Dossiers(tmp_path / 'r_429', clock)
    calls_429 = []

    def transport_429(*_a, **_kw):
        calls_429.append(clock())
        if len(calls_429) == 1:
            err = signal_llm_review.LlmApiError(429, 'rate limit exceeded')
            err.retry_after = 15.0
            raise err
        return model_reply('Reject')

    r1, = player.auto_deal(d_429, plan(), tmp_path, None, llm_config={'api_key': 'k', 'model': 'deepseek-reasoner', 'transport': transport_429})
    assert r1['status'] == 'REQUEST_FAILED'
    h_429 = next((tmp_path / 'r_429' / 'dossiers').glob('Deribit_BTC_*/hands/hand_*'))
    att_429 = dossier.read(dossier.attempts(h_429)[0])
    assert att_429['configuration']['http_status'] == 429
    assert att_429['configuration']['delivery_state'] == 'PROVIDER_REJECTED'
    assert att_429['configuration']['retry_after_s'] == 15.0

    # Before Retry-After (only +5s elapsed) -> does NOT retry yet
    clock.ms += 5_000
    r_wait, = player.auto_deal(d_429, plan(), tmp_path, None, llm_config={'api_key': 'k', 'model': 'deepseek-reasoner', 'transport': transport_429})
    assert r_wait == {'hand_id': h_429.name, 'status': 'AUTO_ATTEMPTED', 'last_status': 'REQUEST_FAILED'}
    assert len(calls_429) == 1

    # After Retry-After (+15s total) -> retries once and succeeds
    clock.ms += 10_000
    r2, = player.auto_deal(d_429, plan(), tmp_path, None, llm_config={'api_key': 'k', 'model': 'deepseek-reasoner', 'transport': transport_429})
    assert r2['status'] == 'VALID' and r2['decision'] == 'Reject'
    assert len(calls_429) == 2

    # 2) Pre-generation 503 (header-level, no partial bytes) -> PROVIDER_UNAVAILABLE, retries once on next tick
    d_503 = dossier.Dossiers(tmp_path / 'r_503', clock)
    calls_503 = []

    def transport_503(*_a, **_kw):
        calls_503.append(1)
        if len(calls_503) == 1:
            raise signal_llm_review.LlmApiError(503, 'upstream overloaded')
        return model_reply('Reject')

    res_503_1, = player.auto_deal(d_503, plan(), tmp_path, None, llm_config={'api_key': 'k', 'model': 'deepseek-reasoner', 'transport': transport_503})
    assert res_503_1['status'] == 'REQUEST_FAILED'
    h_503 = next((tmp_path / 'r_503' / 'dossiers').glob('Deribit_BTC_*/hands/hand_*'))
    att_503 = dossier.read(dossier.attempts(h_503)[0])
    assert att_503['configuration']['delivery_state'] == 'PROVIDER_UNAVAILABLE'
    assert player.audit_record(h_503, clock(), {})['stage'] == 'PROVIDER_UNAVAILABLE'
    clock.ms += 5_000
    res_503_2, = player.auto_deal(d_503, plan(), tmp_path, None, llm_config={'api_key': 'k', 'model': 'deepseek-reasoner', 'transport': transport_503})
    assert res_503_2['status'] == 'VALID' and len(calls_503) == 2

    # 3) Non-retryable 400 / 401 / 403 -> PROVIDER_REJECTED, NEVER retries
    for code in (400, 401, 403):
        d_4xx = dossier.Dossiers(tmp_path / f'r_{code}', clock)
        c_4xx = []

        def t_4xx(*_a, _code=code, _c=c_4xx, **_kw):
            _c.append(1)
            raise signal_llm_review.LlmApiError(_code, f'client error {_code}')

        player.auto_deal(d_4xx, plan(), tmp_path, None, llm_config={'api_key': 'k', 'transport': t_4xx})
        h_4xx = next((tmp_path / f'r_{code}' / 'dossiers').glob('Deribit_BTC_*/hands/hand_*'))
        assert dossier.read(dossier.attempts(h_4xx)[0])['configuration']['delivery_state'] == 'PROVIDER_REJECTED'
        r_again, = player.auto_deal(d_4xx, plan(), tmp_path, None, llm_config={'api_key': 'k', 'transport': t_4xx})
        assert r_again == {'hand_id': h_4xx.name, 'status': 'AUTO_ATTEMPTED', 'last_status': 'REQUEST_FAILED'}
        assert len(c_4xx) == 1

    # 4) Ambiguous 502 / 504 & mid-SSE 503 (partial_bytes > 0) -> DELIVERY_UNKNOWN, NEVER retries
    for label, status_code, with_partial in [('502', 502, False), ('504', 504, False), ('mid_sse_503', 503, True)]:
        d_amb = dossier.Dossiers(tmp_path / f'r_{label}', clock)
        c_amb = []

        def t_amb(*_a, _sc=status_code, _wp=with_partial, _c=c_amb, **_kw):
            _c.append(1)
            err = signal_llm_review.LlmApiError(_sc, f'err {_sc}')
            if _wp:
                err.partial_response = {'text': '## Accept / Call\n半截流式内容', 'chunks': 2, 'bytes_received': 64}
            raise err

        player.auto_deal(d_amb, plan(), tmp_path, None, llm_config={'api_key': 'k', 'transport': t_amb})
        h_amb = next((tmp_path / f'r_{label}' / 'dossiers').glob('Deribit_BTC_*/hands/hand_*'))
        att_amb = dossier.read(dossier.attempts(h_amb)[0])
        assert att_amb['configuration']['delivery_state'] == 'DELIVERY_UNKNOWN'
        if with_partial:
            assert att_amb['configuration']['partial_bytes'] > 0
        r_amb2, = player.auto_deal(d_amb, plan(), tmp_path, None, llm_config={'api_key': 'k', 'transport': t_amb})
        assert r_amb2 == {'hand_id': h_amb.name, 'status': 'AUTO_ATTEMPTED', 'last_status': 'REQUEST_FAILED'}
        assert len(c_amb) == 1


def test_opt_in_standby_model_fast_failover_parse_failover_denylist_and_p0_p2_provenance(tmp_path, monkeypatch):
    for key in (
        'ASTRA_PLAYER_FALLBACK_ENABLED', 'ASTRA_PLAYER_FALLBACK_MODEL',
        'ASTRA_PLAYER_FALLBACK_ENDPOINT', 'ASTRA_PLAYER_FALLBACK_API_KEY',
    ):
        monkeypatch.delenv(key, raising=False)

    # Default OFF
    default_cfg = player._llm_settings({'api_key': 'k', 'model': 'deepseek-reasoner'})
    assert default_cfg['fallback_enabled'] is False
    assert default_cfg['fallback_model'] is None

    # 1) Fast inline failover on immediate 503 (fallback_enabled=False vs True) + shared DailyHttpBudget
    clock = Clock(SLOT + dossier.MINUTE)
    monkeypatch.setattr(dossier, 'collect', lambda *a, **kw: source(clock))

    d_off = dossier.Dossiers(tmp_path / 'fb_off', clock)
    seen_off = []

    def primary_503_then_ok(api_key, model, body, timeout_s, endpoint=None):
        del api_key, body, timeout_s, endpoint
        seen_off.append(model)
        if len(seen_off) == 1:
            raise signal_llm_review.LlmApiError(503, 'primary busy')
        return model_reply('Reject')

    # With fallback_enabled=False: single auto_deal call does NOT inline failover
    r_off1, = player.auto_deal(
        d_off, plan(), tmp_path, None,
        llm_config={'api_key': 'k', 'model': 'deepseek-reasoner', 'fallback_enabled': False, 'fallback_model': 'deepseek-chat', 'transport': primary_503_then_ok},
    )
    assert r_off1['status'] == 'REQUEST_FAILED'
    assert seen_off == ['deepseek-reasoner']

    # With fallback_enabled=True: single auto_deal call immediately fails over inline to standby model!
    d_fast = dossier.Dossiers(tmp_path / 'fb_fast', clock)
    shared_budget = signal_llm_review.DailyHttpBudget(tmp_path / 'shared_fb_budget.json', limit=3)
    seen_fast = []

    def fast_failover_transport(api_key, model, body, timeout_s, endpoint=None):
        seen_fast.append((api_key, model, endpoint, timeout_s))
        if model == 'deepseek-reasoner':
            err = signal_llm_review.LlmApiError(429, 'primary rate limited')
            err.retry_after = 0.0
            raise err
        assert model == 'deepseek-chat'
        return model_reply('Reject')

    r_fast, = player.auto_deal(
        d_fast, plan(), tmp_path, None,
        llm_config={
            'api_key': 'k-prim',
            'model': 'deepseek-reasoner',
            'endpoint': 'https://primary.example/v1',
            'fallback_enabled': True,
            'fallback_model': 'deepseek-chat',
            'fallback_endpoint': 'https://standby.example/v1',
            'fallback_api_key': 'k-standby',
            'fallback_reasoning_effort': 'medium',
            'budget': shared_budget,
            'transport': fast_failover_transport,
        },
    )
    assert r_fast['status'] == 'VALID' and r_fast['decision'] == 'Reject'
    assert [m for _, m, _, _ in seen_fast] == ['deepseek-reasoner', 'deepseek-chat']
    assert seen_fast[1][0] == 'k-standby'
    assert seen_fast[1][2] == 'https://standby.example/v1/chat/completions'

    h_fast = next((tmp_path / 'fb_fast' / 'dossiers').glob('Deribit_BTC_*/hands/hand_*'))
    atts_fast = [dossier.read(p) for p in dossier.attempts(h_fast)]
    assert len(atts_fast) == 2
    assert atts_fast[0]['model'] == 'deepseek-reasoner' and atts_fast[0]['status'] == 'REQUEST_FAILED'
    assert atts_fast[1]['model'] == 'deepseek-chat' and atts_fast[1]['status'] == 'VALID'
    assert atts_fast[1]['configuration']['is_fallback'] is True
    assert atts_fast[1]['configuration']['fallback_from_model'] == 'deepseek-reasoner'
    assert atts_fast[1]['configuration']['fallback_from_attempt_id'] == atts_fast[0]['attempt_id']
    assert atts_fast[1]['configuration']['fallback_reason'] == 'HTTP_429'

    aud_fast = player.audit_record(h_fast, clock(), {}, detail=True)
    assert aud_fast['model'] == 'deepseek-chat'
    assert aud_fast['is_fallback'] is True
    assert aud_fast['fallback_from_model'] == 'deepseek-reasoner'
    assert aud_fast['fallback_reason'] == 'HTTP_429'
    assert aud_fast['attempts'][1]['is_fallback'] is True
    assert aud_fast['attempts'][1]['fallback_from_attempt_id'] == atts_fast[0]['attempt_id']

    snap = shared_budget.snapshot()
    assert snap['http_calls_used'] == 2
    assert [ev['role'] for ev in snap['events']] == ['astra_player_initial', 'astra_player_initial_fallback']

    # 2) Next-tick failover on mechanical PARSE_FAILED under strict p0_strict@1 + p2_strict@1 + Accept shadow link (execution_allowed=False)
    wb, d_p5, seed_hand, now_ref, expiry = sample(tmp_path / 'p5_standby')
    p5_src = dossier.read(seed_hand / 'evidence' / 'source.json')
    p5_hand = d_p5.expect(expiry, 'AD_HOC', reference='p5-standby-accept')
    d_p5.deal(p5_hand, p5_src, input_profile='p0_strict@1')
    p5_calls = []

    def p5_transport(api_key, model, body, timeout_s, endpoint=None):
        del api_key, timeout_s, endpoint
        p5_calls.append((model, body['messages'][0]['content'], body['messages'][1]['content']))
        if len(p5_calls) == 1:
            return {'model': model, 'choices': [{'message': {'content': '## Accept / Call\n截断缺JSON'}, 'finish_reason': 'length'}]}
        return _strict_p5_reply_payload('## Accept / Call', short_strike=103_000, long_strike=105_000, require_outside_at_t0=True)

    p5_cfg = player._llm_settings({
        'api_key': 'k',
        'model': 'deepseek-reasoner',
        'fallback_enabled': True,
        'fallback_model': 'deepseek-chat',
        'transport': p5_transport,
    })
    with serving(wb) as base:
        svc = post(base, wb)
        r_p5_1 = player._deal_hand(
            d_p5, p5_hand, svc, p5_cfg,
            prompt_version=player.review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
            input_profile='p0_strict@1',
        )
        assert r_p5_1['status'] == 'PARSE_FAILED'
        assert len(p5_calls) == 1
        now_ref[0] += 10_000
        wb.refresh(now_ms=now_ref[0], record_daily=False)
        r_p5_2 = player._deal_hand(
            d_p5, p5_hand, svc, p5_cfg,
            prompt_version=player.review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
            input_profile='p0_strict@1',
        )
        assert r_p5_2['status'] == 'VALID' and r_p5_2['decision'] == 'Accept'
        assert r_p5_2['link']['state'] == 'SHADOW_LOCK_CREATED'
        assert r_p5_2['link']['execution_allowed'] is False
        assert [m for m, _, _ in p5_calls] == ['deepseek-reasoner', 'deepseek-chat']
        # Identical p0_strict@1 system prompt and user evidence across primary & standby!
        assert p5_calls[0][1] == p5_calls[1][1]
        assert p5_calls[0][2] == p5_calls[1][2]
        p5_att2 = dossier.read(dossier.attempts(p5_hand)[1])
        assert p5_att2['prompt_version'] == player.review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION
        assert p5_att2['input_profile'] == 'p0_strict@1'
        assert p5_att2['contract_validation_status'] == 'PASSED'
        assert p5_att2['configuration']['fallback_reason'] == 'PARSE_FAILED'

    # 3) Denylist: standby NEVER triggers on CONTRACT_VALIDATION_FAILED, DELIVERY_UNKNOWN, business Reject, DATA_BLOCKED, or exhausted shared budget
    # (a) CONTRACT_VALIDATION_FAILED (require_outside_at_t0=False under p2_strict@1)
    h_cv = d_p5.expect(expiry, 'AD_HOC', reference='p5-cv-deny')
    d_p5.deal(h_cv, p5_src, input_profile='p0_strict@1')
    cv_calls = []

    def cv_transport(_k, model, *_a, **_kw):
        cv_calls.append(model)
        return _strict_p5_reply_payload('## Accept / Call', require_outside_at_t0=False)

    cv_cfg = player._llm_settings({'api_key': 'k', 'model': 'deepseek-reasoner', 'fallback_enabled': True, 'fallback_model': 'deepseek-chat', 'transport': cv_transport})
    assert player._deal_hand(d_p5, h_cv, None, cv_cfg, prompt_version=player.review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION, input_profile='p0_strict@1')['status'] == 'PARSE_FAILED'
    assert player._deal_hand(d_p5, h_cv, None, cv_cfg, prompt_version=player.review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION, input_profile='p0_strict@1')['status'] == 'AUTO_ATTEMPTED'
    assert cv_calls == ['deepseek-reasoner']

    # (b) DELIVERY_UNKNOWN (TimeoutError)
    h_du = d_p5.expect(expiry, 'AD_HOC', reference='p5-du-deny')
    d_p5.deal(h_du, p5_src, input_profile='p0_strict@1')
    du_calls = []

    def du_transport(_k, model, *_a, **_kw):
        du_calls.append(model)
        raise TimeoutError('read timed out')

    du_cfg = player._llm_settings({'api_key': 'k', 'model': 'deepseek-reasoner', 'fallback_enabled': True, 'fallback_model': 'deepseek-chat', 'transport': du_transport})
    assert player._deal_hand(d_p5, h_du, None, du_cfg)['status'] == 'TIMEOUT'
    assert player._deal_hand(d_p5, h_du, None, du_cfg)['status'] == 'AUTO_ATTEMPTED'
    assert du_calls == ['deepseek-reasoner']

    # (c) Business Reject & DATA_BLOCKED & exhausted shared budget
    h_rej = d_p5.expect(expiry, 'AD_HOC', reference='p5-rej-deny')
    d_p5.deal(h_rej, p5_src, input_profile='p0_strict@1')
    rej_calls = []
    rej_cfg = player._llm_settings({
        'api_key': 'k', 'model': 'deepseek-reasoner', 'fallback_enabled': True, 'fallback_model': 'deepseek-chat',
        'transport': lambda _k, model, *_a, **_kw: rej_calls.append(model) or _strict_p5_reply_payload('## Reject'),
    })
    assert player._deal_hand(d_p5, h_rej, None, rej_cfg, prompt_version=player.review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION, input_profile='p0_strict@1')['decision'] == 'Reject'
    assert player._deal_hand(d_p5, h_rej, None, rej_cfg, prompt_version=player.review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION, input_profile='p0_strict@1')['status'] == 'ALREADY_DECIDED'
    assert rej_calls == ['deepseek-reasoner']

    h_blocked = d_p5.expect(expiry, 'AD_HOC', reference='p5-data-blocked')
    d_p5.note(h_blocked, 'DATA_BLOCKED', 'missing call case')
    assert player._deal_hand(d_p5, h_blocked, None, rej_cfg)['status'] == 'NO_EVIDENCE'
    assert rej_calls == ['deepseek-reasoner']

    cap_1 = signal_llm_review.DailyHttpBudget(tmp_path / 'cap_1_budget.json', limit=1)
    h_cap = d_p5.expect(expiry, 'AD_HOC', reference='p5-budget-cap')
    d_p5.deal(h_cap, p5_src, input_profile='p0_strict@1')
    cap_seen = []

    def cap_transport(_k, model, *_a, **_kw):
        cap_seen.append(model)
        raise signal_llm_review.LlmApiError(503, 'busy')

    cap_cfg = player._llm_settings({
        'api_key': 'k', 'model': 'deepseek-reasoner', 'fallback_enabled': True, 'fallback_model': 'deepseek-chat',
        'budget': cap_1, 'transport': cap_transport,
    })
    res_cap = player._deal_hand(d_p5, h_cap, None, cap_cfg, prompt_version=player.review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION, input_profile='p0_strict@1')
    # Attempt #1 consumed the 1 daily call; fast inline standby hits DailyBudgetExceeded and records not_sent.json!
    assert res_cap['status'] == 'DAILY_BUDGET_EXHAUSTED'
    assert cap_seen == ['deepseek-reasoner']


def test_service_budget_deadline_and_45s_reply_window_guards(tmp_path, monkeypatch):
    import time as _time
    clock = Clock(SLOT + dossier.MINUTE)
    d = dossier.Dossiers(tmp_path, clock)
    monkeypatch.setattr(dossier, 'collect', lambda *a, **kw: source(clock))

    # 1) < 45s remaining service wall-clock budget defers without starting HTTP call
    h_svc, = d.tick(plan(), slots=[SLOT])
    d.deal(h_svc, source(clock))
    http_calls = []
    cfg_short_svc = player._llm_settings({
        'api_key': 'k',
        'model': 'deepseek-reasoner',
        'service_deadline_mono': _time.monotonic() + 30.0,
        'transport': lambda *a, **kw: http_calls.append(1) or model_reply('Reject'),
    })
    r_def = player._deal_hand(d, h_svc, None, cfg_short_svc)
    assert r_def == {'hand_id': h_svc.name, 'status': 'SERVICE_BUDGET_DEFERRED'}
    assert http_calls == []
    assert player.audit_record(h_svc, clock(), {})['state'] == 'SERVICE_BUDGET_DEFERRED'

    # 2) 95s remaining service budget caps call_timeout_s <= 75.0s (20s safety margin before systemd SIGKILL)
    h_cap = d.expect(SLOT + plan()['expiry_offset_ms'], 'AD_HOC', reference='svc-cap-95s')
    d.deal(h_cap, source(clock))
    seen_t = []
    cfg_95 = player._llm_settings({
        'api_key': 'k',
        'model': 'deepseek-reasoner',
        'service_deadline_mono': _time.monotonic() + 95.0,
        'transport': lambda _k, _m, _b, timeout_s, **_kw: seen_t.append(timeout_s) or model_reply('Reject'),
    })
    r_ok = player._deal_hand(d, h_cap, None, cfg_95)
    assert r_ok['status'] == 'VALID'
    assert len(seen_t) == 1 and 70.0 <= seen_t[0] <= 75.0

    # 3) < 45s remaining before reply_deadline refuses Attempt #1 so call_timeout_s >= 30s never overruns into LATE
    h_late = d.expect(SLOT + plan()['expiry_offset_ms'], 'AD_HOC', reference='reply-win-30s')
    d.deal(h_late, source(clock))
    meta_late = d.verify_evidence(h_late)
    dl_late = dossier.Dossiers.reply_deadline(dossier.read(h_late / 'expected.json'), meta_late)
    clock.ms = dl_late - 30_000  # only 30s left (< 45s required)
    late_calls = []
    r_win = player._deal_hand(
        d, h_late, None,
        player._llm_settings({'api_key': 'k', 'transport': lambda *a, **kw: late_calls.append(1) or model_reply('Reject')}),
    )
    assert r_win == {'hand_id': h_late.name, 'status': 'REPLY_WINDOW_CLOSED'}
    assert late_calls == []
