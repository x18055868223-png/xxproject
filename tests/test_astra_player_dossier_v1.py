from pathlib import Path

import pytest

from tools import astra_player_dossier_v1 as dossier


ROOT = Path(__file__).resolve().parents[1] / 'docs/rulebook/examples'
SRC = ROOT / 'player_input_liability/source'
EXPIRY = 1794729600000
SLOT = EXPIRY - 18 * 60 * dossier.MINUTE
M = dossier.MINUTE


class Clock:
    def __init__(self, ms):
        self.ms = ms

    def __call__(self):
        return self.ms


def source(clock):
    cases = [dossier.player.read_json(next((SRC / 'cases/2026-11-14').glob(f'14_{s}_*.gz'))) for s in ('put', 'call')]
    cards = [r for r in dossier.rows(SRC / 'levels/2026-11-15.jsonl') if r['round'] == 'r2']
    cards = [next(r for r in cards if r['side'] == side) for side in ('put', 'call')]
    path = dossier.player.read_json(SRC / 'paths/2026-11-15.json.gz')
    hist = dossier.rows(ROOT / 'player_ard/generated/pin_history.jsonl')
    instruments = [dict(instrument_name=dossier.player.values(c, 'L1.contract')[leg], expiration_timestamp=EXPIRY,
                        settlement_currency='BTC', is_active=True) for c in cases for leg in ('short_instrument', 'long_instrument')]
    return dossier.seal_source(cases, cards, path, hist, instruments, received_at_ms=clock(), source='TEST FIXTURE')


def plan():
    return dict(dossier.read(ROOT / 'player_dossier/schedule.json'), effective_at_ms=SLOT, plan_id='TEST-PLAN')


EXCERPTS = dict(put='Put path', call='Call path', comparison='Both compared', put_counter='Put counter', call_counter='Call counter')


def raw(action='Accept / Call'):
    return ('**' + action + '。**\n' + '\n'.join(EXCERPTS.values())).encode()


@pytest.fixture
def active(tmp_path):
    clock = Clock(SLOT + M)
    d = dossier.Dossiers(tmp_path, clock)
    hand = d.tick(plan(), lambda *_: source(clock))[0]
    return d, clock, hand


def test_scheduled_expected_missed_cross_day_and_adhoc_denominator(active):
    d, clock, hand = active
    assert d.tick(plan(), lambda *_: pytest.fail('duplicate collection')) == [hand]
    other = d.expect(EXPIRY, 'STARTUP_AD_HOC', reference='separate')
    assert other.parent == hand.parent and other != hand
    clock.ms = SLOT + dossier.DAY * 2 + 30 * M
    due = d.tick(plan())
    assert len(due) == 3 and due[1] != hand
    assert dossier.read(next((due[1] / 'issuance').glob('*.json')))['state'] == 'MISSED'
    assert dossier.read(due[1] / 'expected.json')['expiry_ms'] == EXPIRY + dossier.DAY
    d.summary(EXPIRY)
    counts = dossier.read(d.path(EXPIRY) / 'manifest.json')['counts']
    assert counts == {'SCHEDULED': {'NO_RESPONSE': 1}, 'STARTUP_AD_HOC': {'EXPECTED': 1}}


def test_data_failure_retry_keeps_same_hand_and_eligibility(tmp_path):
    clock = Clock(SLOT + M)
    d = dossier.Dossiers(tmp_path, clock)
    def unavailable(*_):
        raise LookupError('no candidate')
    hand = d.tick(plan(), unavailable)[0]
    assert dossier.read(next((hand / 'issuance').glob('*.json')))['state'] == 'DATA_BLOCKED'
    assert d.tick(plan(), lambda *_: source(clock)) == [hand]
    actual_input = (hand / 'evidence' / 'input.md').read_text(encoding='utf-8')
    src = dossier.read(hand / 'evidence' / 'source.json')
    assert actual_input == dossier.player.build_input(
        *src['cases'], *src['cards'],
        index_path=src['index_path'], ard_history=src['history'], ard_band_snapshot=src.get('band_snapshot'),
    )
    archived = (ROOT / 'player_ard_closeout/generated/ai_input.md').read_text(encoding='utf-8')
    assert actual_input.split('## 本局牌面', 1)[1].split('5. **', 1)[0] == archived.split('## 本局牌面', 1)[1].split('5. **', 1)[0]
    assert '5. **现货量价与主动流。**' in actual_input and '不能仅因“增强未确认”淡化既成反证' in actual_input


@pytest.mark.parametrize('action', ['Accept / Call', 'Reject', 'Unknown'])
def test_attempts_raw_duplicate_first_valid_and_restart(active, action):
    d, clock, hand = active
    raw_input = (hand / 'evidence' / 'input.md').read_bytes()
    assert d.respond(hand, 'timeout', 'TIMEOUT')['status'] == 'TIMEOUT'
    assert d.respond(hand, 'parser-error', 'RECEIVED', raw(action), {})['status'] == 'PARSE_FAILED'
    fix = d.reparse(hand, 'parser-error', EXCERPTS, 'manual link correction')
    assert dossier.read(fix)['judgment']['action'] == action.split()[0]
    assert d.select(hand) is None
    dec = d.respond(hand, 'one', 'RECEIVED', raw(action), EXCERPTS)
    assert dec['status'] == 'VALID'
    restarted = dossier.Dossiers(d.root, clock)
    assert restarted.respond(hand, 'one', 'RECEIVED', raw(action), EXCERPTS) == dec
    assert restarted.respond(hand, 'two', 'RECEIVED', raw(action), EXCERPTS)['status'] == 'DUPLICATE'
    alternative = 'Reject' if action != 'Reject' else 'Unknown'
    assert restarted.respond(hand, 'three', 'RECEIVED', raw(alternative), EXCERPTS)['status'] == 'VALID'
    assert restarted.select(hand)['attempt_id'] == 'one'
    assert (hand / 'evidence' / 'input.md').read_bytes() == raw_input
    assert (hand / 'responses/one/response.raw').read_bytes() == raw(action)


def test_late_and_unverified_and_hash_fail(active):
    d, clock, hand = active
    clock.ms += 21 * M
    assert d.respond(hand, 'late', 'RECEIVED', raw(), EXCERPTS)['status'] == 'LATE'
    assert d.select(hand) is None
    clock.ms = EXPIRY + M
    out = d.settle(EXPIRY, None)
    assert out['classification'] == 'UNVERIFIED'
    d.save_outcome(out, {'error': 'NO_OFFICIAL'})
    d.summary(EXPIRY)
    assert dossier.read(d.path(EXPIRY) / 'manifest.json')['status'] == 'UNVERIFIED'
    (hand / 'evidence/input.md').write_bytes(b'modified')
    with pytest.raises(ValueError, match='hash mismatch'):
        d.verify_evidence(hand)


def fixture_cashflow(root):
    clock = Clock(SLOT + M)
    d = dossier.Dossiers(root, clock)
    hand = d.expect(EXPIRY, 'FIXTURE', reference='synthetic-management')
    s = source(clock)
    d.deal(hand, s)
    d.respond(hand, 'fixture-answer', 'RECEIVED', raw(), EXCERPTS, channel='FIXTURE')
    c = dossier.player.values(s['cases'][1], 'L1.contract')
    price = s['cards'][1]['index_usd']
    condition_file = root / 'condition-fixture.json'
    frozen = dossier.condition.freeze_condition(condition_file, contract=c, entry_at_ms=clock(), entry_index_usd=price,
        historical_facts=['fixture'], accepted_conditions=['fixture'], invalidation='entry_not_returned',
        first_check_ms=clock()+M, check_interval_ms=M)
    episode = d.episode(hand, frozen)
    assert d.episode(hand, frozen) == episode
    entry = clock()
    clock.ms += 2*M
    p = dict(index_name='btc_usd', expiry_ms=EXPIRY, points=[[entry-M, 68000], [entry+M, 68000]])
    assert d.check(episode, p, entry+2*M)['kind'] == 'CONDITION_UNKNOWN'
    assert d.check(episode, p, entry+M)['kind'] == 'CONDITION_TRIGGERED'
    assert d.check(episode, p, entry+M)['kind'] == 'CONDITION_TRIGGERED'
    d.event(episode, 'late-old', 'OBSERVATION', {'note': 'fixture'}, observed=entry-M, available=entry+M)
    assert dossier.read(episode/'events/late-old.json')['scope'] == 'PRE_ENTRY_OBSERVATION'
    d.event(episode, 'ard-review', 'REVIEW_REQUIRED', {'note': 'fixture ARD change only'})
    d.event(episode, 'ai-revoke', 'AI_REVOKE', {'public_reply': 'Fixture Revoke recommendation'})
    d.event(episode, 'shadow-action', 'SHADOW_ACTION', {'action': 'BUY_BACK_SHORT'})
    d.event(episode, 'simulated-short', 'SIMULATED_ORDER', dict(action='BUY_BACK_SHORT', instrument=c['short_instrument'],
        quantity_btc=1.0, cost_btc=0.012, fee_btc=0.0004))
    with pytest.raises(ValueError):
        d.event(episode, 'order', 'ORDER_REQUESTED', {})
    clock.ms = EXPIRY + M
    out = d.settle(EXPIRY, 70000.0, fixture=True, management=dict(episode_id=episode.name, entry_credit_btc=0.003,
        entry_fee_btc=0.0004, long_delivery_fee_btc=0.0001, hold_delivery_fee_btc=0.0002))
    dest = d.save_outcome(out, {'kind': 'FIXTURE'})
    assert d.save_outcome(out, {'kind': 'FIXTURE'}) == dest
    d.summary(EXPIRY)
    return out, episode


def test_short_buyback_long_survives_and_native_cash(tmp_path):
    out, episode = fixture_cashflow(tmp_path)
    e = out['episodes'][0]
    assert e['long_remaining_before_expiry_btc'] == 1.0
    assert e['long_settlement_credit_btc'] == pytest.approx(1000/70000)
    assert e['original_spread_payout_btc'] == pytest.approx(2000/70000)
    assert e['managed_net_btc'] == pytest.approx(0.003-0.0004-0.012-0.0004+1000/70000-0.0001)
    assert len(list((episode/'events').glob('check*'))) == 2
    assert out['actual_execution_net_btc'] is None


def test_writer_excludes_other_writer_and_releases(tmp_path):
    with dossier.writer(tmp_path):
        with pytest.raises(OSError):
            with dossier.writer(tmp_path):
                pytest.fail('second writer acquired lock')
    with dossier.writer(tmp_path):
        pass


def test_shipped_plan_freezes_before_due(tmp_path):
    configured = dossier.read(ROOT / 'player_dossier/schedule.json')
    clock = Clock(configured['effective_at_ms'] - M)
    d = dossier.Dossiers(tmp_path, clock)
    assert d.tick(configured) == []
    assert dossier.read(tmp_path / 'plans' / f"{configured['plan_id']}.json") == configured
    clock.ms += M
    assert len(d.tick(configured)) == 1


def test_official_result_is_native_and_idempotent(active, monkeypatch):
    d, clock, hand = active
    clock.ms = EXPIRY + M
    calls = []
    def public(method, **params):
        calls.append((method, params))
        return {'data': [{'date': '2026-11-15', 'delivery_price': 63993.3}]}
    monkeypatch.setattr(dossier, 'public', public)
    monkeypatch.setattr(dossier.archive.Deribit, 'index_path', lambda _: [])
    first = d.official(EXPIRY)
    assert d.official(EXPIRY) == first and len(calls) == 1
    assert list(d.settle_due()) == []
    result = dossier.read(first / 'outcome.json')
    assert result['classification'] == 'OFFICIAL' and result['delivery'] == 63993.3
    assert result['hands'][0]['lower_payout_side'] == 'call'
    assert result['actual_execution_net_btc'] is None


def test_respond_without_response_and_record_deadline(active):
    d, clock, hand = active
    assert 'AWAITING_RESPONSE' in d.summary(EXPIRY).read_text(encoding='utf-8')
    clock.ms += 21*M
    assert d.tick(plan()) == [hand]
    assert 'NO_RESPONSE' in d.summary(EXPIRY).read_text(encoding='utf-8')
    assert any(dossier.read(p)['state'] == 'REPLY_WINDOW_CLOSED' for p in (hand/'issuance').glob('*.json'))


def test_delivery_price_does_not_certify_a_missing_native_pair(tmp_path):
    clock = Clock(SLOT)
    d = dossier.Dossiers(tmp_path, clock)
    assert d.tick(plan())
    clock.ms = EXPIRY + M
    result = d.settle(EXPIRY, 63993.3)
    assert result['classification'] == 'UNVERIFIED'
    assert result['verification_gap'] and result['hands'] == []


def test_recovery_ignores_stage_and_rebuilds_selected_decision(active):
    d, clock, hand = active
    staged = hand / 'responses/.stage-interrupted/attempt.json'
    dossier.write(staged, b'{"ordinal": 0, "status": "VALID"}\n')
    assert d.select(hand) is None
    assert d.respond(hand, 'published', 'RECEIVED', raw(), EXCERPTS)['ordinal'] == 1
    selected = (hand / 'decision.json').read_bytes()
    (hand / 'decision.json').unlink()  # simulate interruption after publishing the attempt
    assert dossier.Dossiers(d.root, clock).select(hand)['attempt_id'] == 'published'
    assert (hand / 'decision.json').read_bytes() == selected


def test_parse_prioritizes_standalone_decision_heading_over_body_prefixes():
    body_with_prefixes = (
        '**Accept / Call**\n'
        + '\n'.join(EXCERPTS.values())
        + '\nUnknown 仅用于关键缺口，不阻塞判断。\n'
        + 'Reject 仅在双侧均不利时出现。\n'
        + 'Accept 条件如下：现价位于责任外。'
    ).encode('utf-8')
    parsed = dossier.parse(body_with_prefixes, EXCERPTS)
    assert parsed['action'] == 'Accept' and parsed['side'] == 'call'

    conflicting_headings = ('**Accept / Call**\n**Reject**\n' + '\n'.join(EXCERPTS.values())).encode('utf-8')
    with pytest.raises(ValueError, match='unambiguous public decision heading'):
        dossier.parse(conflicting_headings, EXCERPTS)

    prefix_fallback = ('Accept / Call：满足条件。\n' + '\n'.join(EXCERPTS.values())).encode('utf-8')
    assert dossier.parse(prefix_fallback, EXCERPTS)['side'] == 'call'

    conflicting_prefixes = ('Accept 条件。\nReject 理由。\n' + '\n'.join(EXCERPTS.values())).encode('utf-8')
    with pytest.raises(ValueError, match='unambiguous public decision heading'):
        dossier.parse(conflicting_prefixes, EXCERPTS)


def test_collect_scheduled_derives_missing_r2_card_in_last_five_minutes(tmp_path, monkeypatch):
    import shutil
    archive_root = tmp_path / 'archive'
    shutil.copytree(SRC / 'cases', archive_root / 'cases')
    cases = [dossier.player.read_json(next((SRC / 'cases/2026-11-14').glob(f'14_{s}_*.gz'))) for s in ('put', 'call')]
    instruments = [dict(instrument_name=dossier.player.values(c, 'L1.contract')[leg], expiration_timestamp=EXPIRY,
                        settlement_currency='BTC', is_active=True) for c in cases for leg in ('short_instrument', 'long_instrument')]
    path = dossier.player.read_json(SRC / 'paths/2026-11-15.json.gz')
    monkeypatch.setattr(dossier, 'public', lambda *_a, **_k: instruments)
    monkeypatch.setattr(dossier.archive.Deribit, 'index_path', lambda _self: path['points'])

    with pytest.raises(LookupError, match='原生 r2 位置卡'):
        dossier.collect(archive_root, EXPIRY, SLOT, startup=False, clock=Clock(SLOT + 10 * M))

    derived = dossier.collect(archive_root, EXPIRY, SLOT, startup=False, clock=Clock(SLOT + 15 * M))
    assert len(derived['cards']) == 2
    assert all('scheduled_card_from_native_case' in card['data_gaps'] for card in derived['cards'])


def test_settle_leaves_t0_credit_none_without_tracking_link(active, monkeypatch):
    d, clock, hand = active
    d.respond(hand, 'accepted', 'RECEIVED', raw(), EXCERPTS)
    clock.ms = EXPIRY + M
    monkeypatch.setattr(dossier, 'public', lambda *_a, **_k: {'data': [{'date': '2026-11-15', 'delivery_price': 64000.0}]})
    monkeypatch.setattr(dossier.archive.Deribit, 'index_path', lambda _self: [])
    result = dossier.read(d.official(EXPIRY) / 'outcome.json')
    assert result['hands'][0]['t0_net_credit_after_entry_fee_btc'] is None
    assert result['hands'][0]['hold_to_expiry_shadow_net_btc'] is None
