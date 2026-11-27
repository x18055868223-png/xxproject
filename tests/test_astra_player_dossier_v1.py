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
    # D02: unverified boundary chart bucket (14:00:00 -> 66,767.6) is excluded in favor of eligible 13:59:00 -> 66,739.4
    assert '合格图表末点66,739.4（标签2026-11-14 13:59:00 UTC）' in actual_input
    assert '双as-of核验' in actual_input
    assert '指数66,767.6' not in actual_input
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


def test_note_multi_tick_attempts_diagnostics_and_window_close_preservation(tmp_path):
    clock = Clock(SLOT + 2 * M)
    d = dossier.Dossiers(tmp_path, clock)

    def blocked(*_):
        raise LookupError('call candidate missing')

    # Tick 1 at +2m -> DATA_BLOCKED created with attempt_count=1
    hand = d.tick(plan(), blocked)[0]
    note_files = list((hand / 'issuance').glob('*.json'))
    assert len(note_files) == 1
    n1 = dossier.read(note_files[0])
    assert n1['state'] == 'DATA_BLOCKED'
    assert n1['reason'] == 'call candidate missing'
    assert n1['recorded_at_ms'] == SLOT + 2 * M
    assert n1['first_recorded_at_ms'] == SLOT + 2 * M
    assert n1['attempt_count'] == 1
    assert n1['attempt_times_ms'] == [SLOT + 2 * M]

    # Direct multi-tick note() updates diagnostics and caps attempt_times_ms at 16
    for i in range(2, 20):
        clock.ms = SLOT + i * M
        d.note(hand, 'DATA_BLOCKED', 'call candidate missing', diagnostics={'last_step': i, 'capture_state': 'PARTIAL'})
    note_files = list((hand / 'issuance').glob('*.json'))
    assert len(note_files) == 1
    n_many = dossier.read(note_files[0])
    assert n_many['first_recorded_at_ms'] == SLOT + 2 * M
    assert n_many['recorded_at_ms'] == SLOT + 19 * M
    assert n_many['attempt_count'] == 19
    assert len(n_many['attempt_times_ms']) == 16
    assert n_many['attempt_times_ms'][-1] == SLOT + 19 * M
    assert n_many['diagnostics'] == {'last_step': 19, 'capture_state': 'PARTIAL'}

    # At +32m (>= deal_grace_ms=20m in schedule.json): tick() preserves DATA_BLOCKED and adds window_closed_at_ms
    grace = plan()['deal_grace_ms']
    clock.ms = SLOT + grace + 2 * M
    d.tick(plan(), blocked)
    note_files = list((hand / 'issuance').glob('*.json'))
    assert len(note_files) == 1
    n_closed = dossier.read(note_files[0])
    assert n_closed['state'] == 'DATA_BLOCKED'
    assert n_closed['window_closed_at_ms'] == SLOT + grace + 2 * M
    assert n_closed['attempt_count'] == 19

    # Subsequent tick past grace does NOT overwrite window_closed_at_ms
    clock.ms = SLOT + grace + 10 * M
    d.tick(plan(), blocked)
    assert dossier.read(note_files[0])['window_closed_at_ms'] == SLOT + grace + 2 * M
    d.summary(EXPIRY)
    assert dossier.read(d.path(EXPIRY) / 'manifest.json')['counts'] == {'SCHEDULED': {'DATA_BLOCKED': 1}}

    # Contrast: next day's slot with ZERO prior notes still records MISSED after grace closes
    clock.ms = SLOT + dossier.DAY + grace + 2 * M
    due = d.tick(plan())
    missed_notes = [dossier.read(p) for p in (due[1] / 'issuance').glob('*.json')]
    assert len(missed_notes) == 1 and missed_notes[0]['state'] == 'MISSED'


def test_collect_fallback_cases_provenance_early_card_and_strict_guards(tmp_path, monkeypatch):
    import json
    import shutil
    from tools import astra_case_file_v1 as cf

    cases = [dossier.player.read_json(next((SRC / 'cases/2026-11-14').glob(f'14_{s}_*.gz'))) for s in ('put', 'call')]
    # Seal canonical content_sha256 on both fixture cases
    sealed_cases = {}
    for s, c in zip(('put', 'call'), cases):
        body = {k: v for k, v in c.items() if k != 'build'}
        sha = dossier.archive._canonical_case_content_sha256(body)
        sealed_cases[s] = {**body, 'build': {**c['build'], 'content_sha256': sha}}

    instruments = [
        dict(
            instrument_name=dossier.player.values(c, 'L1.contract')[leg],
            expiration_timestamp=EXPIRY,
            settlement_currency='BTC',
            is_active=True,
        )
        for c in cases
        for leg in ('short_instrument', 'long_instrument')
    ]
    path = dossier.player.read_json(SRC / 'paths/2026-11-15.json.gz')
    monkeypatch.setattr(dossier, 'public', lambda *_a, **_k: instruments)
    monkeypatch.setattr(dossier.archive.Deribit, 'index_path', lambda _self: path['points'])

    # 1. Empty archive + fallback_cases at SLOT + 2m (< 15m, no levels/*.jsonl yet) builds targeted_backfill cards
    empty_arch = tmp_path / 'empty_arch'
    src_fb = dossier.collect(
        empty_arch,
        EXPIRY,
        SLOT,
        startup=False,
        clock=Clock(SLOT + 2 * M),
        fallback_cases=sealed_cases,
    )
    assert src_fb['source'] == 'targeted workbench verified native case + Deribit public read'
    assert len(src_fb['cards']) == 2
    assert all('targeted_backfill_card_from_native_case' in card['data_gaps'] for card in src_fb['cards'])

    # Sequence form + provenance_override when 1 side is on disk and 1 side is from fallback
    half_arch = tmp_path / 'half_arch'
    put_gz = next((SRC / 'cases/2026-11-14').glob('14_put_*.gz'))
    dest_dir = half_arch / 'cases/2026-11-14'
    dest_dir.mkdir(parents=True)
    shutil.copy2(put_gz, dest_dir / put_gz.name)
    src_half = dossier.collect(
        half_arch,
        EXPIRY,
        SLOT,
        startup=False,
        clock=Clock(SLOT + 2 * M),
        fallback_cases=[sealed_cases['put'], sealed_cases['call']],
        provenance_override='targeted backfill persisted + Deribit public read',
    )
    assert src_half['source'] == 'targeted backfill persisted + Deribit public read'

    # Fails closed when tampered content_sha256
    bad_fb = json.loads(json.dumps(sealed_cases))
    bad_fb['call']['contract_cn'] = 'tampered'
    with pytest.raises(LookupError, match='哈希校验失败'):
        dossier.collect(empty_arch, EXPIRY, SLOT, clock=Clock(SLOT + 2 * M), fallback_cases=bad_fb)

    # Fails closed when pair skew > 60s
    skew_fb = json.loads(json.dumps(sealed_cases))
    skew_fb['call']['build']['built_at_ms'] = skew_fb['put']['build']['built_at_ms'] + 65_000
    with pytest.raises(LookupError, match='60 秒'):
        dossier.collect(empty_arch, EXPIRY, SLOT, clock=Clock(SLOT + 2 * M), fallback_cases=skew_fb)

    # Fails closed when index_path does not cover tick within PATH_EDGE_MS
    monkeypatch.setattr(dossier.archive.Deribit, 'index_path', lambda _self: [[SLOT - 30 * M, 66500.0]])
    with pytest.raises(LookupError, match='指数路径未覆盖'):
        dossier.collect(empty_arch, EXPIRY, SLOT, clock=Clock(SLOT + 2 * M), fallback_cases=sealed_cases)
    assert cf.SCHEMA == 'case_file@0.1'


def test_settle_due_deduplicates_unverified_zero_evidence_dossier_while_retrying_dealt_dossier(tmp_path, monkeypatch):
    # 1. Zero-evidence DATA_BLOCKED dossier settles UNVERIFIED once and never duplicates outcome_*
    clock = Clock(SLOT + 2 * M)
    d_blocked = dossier.Dossiers(tmp_path / 'blocked_root', clock)
    d_blocked.tick(plan(), lambda *_: (_ for _ in ()).throw(LookupError('no cases')))
    delivery_calls = []

    def fake_delivery(method, **params):
        delivery_calls.append((method, clock()))
        return {'data': [{'date': '2026-11-15', 'delivery_price': 64100.0}]}

    monkeypatch.setattr(dossier, 'public', fake_delivery)
    monkeypatch.setattr(dossier.archive.Deribit, 'index_path', lambda _self: [])

    clock.ms = EXPIRY + 6 * M
    settled_1 = list(d_blocked.settle_due())
    assert len(settled_1) == 1 and len(delivery_calls) == 1
    out_dirs = list((d_blocked.path(EXPIRY) / 'outcomes').glob('outcome_*/outcome.json'))
    assert len(out_dirs) == 1 and dossier.read(out_dirs[0])['classification'] == 'UNVERIFIED'

    clock.ms = EXPIRY + 16 * M
    assert list(d_blocked.settle_due()) == []
    clock.ms = EXPIRY + 31 * M
    assert list(d_blocked.settle_due()) == []
    assert len(delivery_calls) == 1
    assert len(list((d_blocked.path(EXPIRY) / 'outcomes').glob('outcome_*/outcome.json'))) == 1

    # 2. Dealt dossier whose first settlement was UNVERIFIED (missing delivery price) STILL retries until OFFICIAL
    clock_dealt = Clock(SLOT + M)
    d_dealt = dossier.Dossiers(tmp_path / 'dealt_root', clock_dealt)
    d_dealt.tick(plan(), lambda *_: source(clock_dealt))
    published = {'ready': False}

    def delayed_delivery(method, **params):
        return {'data': [{'date': '2026-11-15', 'delivery_price': 64100.0}] if published['ready'] else []}

    monkeypatch.setattr(dossier, 'public', delayed_delivery)
    clock_dealt.ms = EXPIRY + 6 * M
    assert len(list(d_dealt.settle_due())) == 1
    assert [dossier.read(p)['classification'] for p in (d_dealt.path(EXPIRY) / 'outcomes').glob('outcome_*/outcome.json')] == ['UNVERIFIED']

    published['ready'] = True
    clock_dealt.ms = EXPIRY + 16 * M
    assert len(list(d_dealt.settle_due())) == 1
    classes = {dossier.read(p)['classification'] for p in (d_dealt.path(EXPIRY) / 'outcomes').glob('outcome_*/outcome.json')}
    assert 'OFFICIAL' in classes
    clock_dealt.ms = EXPIRY + 31 * M
    assert list(d_dealt.settle_due()) == []
