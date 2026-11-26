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
