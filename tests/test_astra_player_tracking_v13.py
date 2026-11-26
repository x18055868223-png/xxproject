"""Local integration fixtures. All markets, replies and delivery prices here are simulated."""

from contextlib import contextmanager
from argparse import Namespace
from http.server import ThreadingHTTPServer
from pathlib import Path
import json
import threading
import urllib.error
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tools import astra_player_dossier_v1 as dossier
from tools import astra_player_review_v1 as review
from tools import astra_player_tracking_v13 as tracking
from tools import astra_research_archive_v1 as archive
from tools import astra_workbench_v20 as workbench
from tools import signal_llm_review
from test_astra_locked_followup import lock, setup
from test_astra_player_dossier_v1 import Clock, SLOT, plan, source as fixture_source


M = dossier.MINUTE
EXCERPTS = dict(put='Put：现价在外，先核查 98,000 及 97,000 的短腿路径。',
                call='Call：现价在外，先核查 102,000 及 103,000 的短腿路径。',
                put_counter='Put 最强反证：98,000 已多次穿越，不能当作固定缓冲。',
                call_counter='Call 最强反证：102,000 可再测试，剩余期限内仍存在入区路径。',
                comparison='两侧分别核查，按到期前入区路径和相对余量择边。')
RULE = dict(historical_facts='历史事实：两个短腿在前窗采样均未被进入。',
            accepted_conditions='继续观察的前提是先在短腿责任外锁定，进入后在检查钟前严格返回。',
            confirmation_excerpt='UTC 下一个整分钟首检，随后每分钟检查。严格进入后仍未严格回到责任外时触发。缺分钟待核查。',
            invalidation='entry_not_returned', check_interval_ms=M, require_outside_at_t0=True)


def sample(tmp_path, action='Accept / Call', *, manual=False, status='RECEIVED'):
    wb, _, now, first = setup(tmp_path / 'tracker')
    wb.config.shadow_experiment = True
    wb.config.shadow_dossier_root = tmp_path / 'dossier'
    wb.config.shadow_service_token = 'fixture-service-token'
    wb.config.frontend_dir = Path(__file__).resolve().parents[1] / 'deploy/astra_workbench_v3'
    d = dossier.Dossiers(wb.config.shadow_dossier_root, clock=lambda: now[0])
    expiry = first['contract']['expiry_ms']
    cases, cards, native = [], [], []
    points = [[t, 100_000] for t in range(now[0] - dossier.DAY, now[0] + 1, M)]
    for c in wb._current_view['candidates']:
        s = c['snapshot']
        case = wb.case_file(s['snapshot_id'])
        read = case['build']['built_at_ms']
        walls = dict(expiry_ms=expiry, walls={'pin': 99_000},
                     top={'oi_put': [{'strike': 98_000}], 'oi_call': [{'strike': 102_000}]})
        dossier.player.entry(case, 'L3.expiry_walls').update(values=walls, asof_ms=read, quality='ok')
        contract = dossier.player.values(case, 'L1.contract')
        cards.append(dossier.levels.build_card(expiry_ms=expiry, rnd='r2', side=contract['side'],
                     read_at_ms=read, walls=walls, points=points, case=case, contract=contract, snapshots=[]))
        cases.append(case)
        native.extend(dict(instrument_name=contract[f'{leg}_instrument'], expiration_timestamp=expiry,
                           settlement_currency='BTC', is_active=True) for leg in ('short', 'long'))
    path = dict(schema='index_path@0.1', index_name='btc_usd', expiry_ms=expiry, points=points,
                fetched_at_ms=now[0])
    hand = d.expect(expiry, 'AD_HOC', reference='TEST v1.3')
    d.deal(hand, dossier.seal_source(cases, cards, path, [], native, received_at_ms=now[0], source='TEST_FROZEN'))
    requested = now[0]
    now[0] += 100
    raw = ('**' + action + '**\n' + '\n'.join(EXCERPTS.values()) + '\n' +
           '\n'.join(RULE[k] for k in ('historical_facts', 'accepted_conditions', 'confirmation_excerpt'))
           + '\n<svg id="injection-probe"></svg>').encode()
    d.respond(hand, 'fixture-reply', status, raw if status == 'RECEIVED' else None, EXCERPTS,
              model='fixture-model-1.3', requested=requested, decided=now[0],
              configuration={'reasoning_effort': 'fixture'})
    if manual:
        lock(wb, first)
    now[0] += 1_000
    wb.refresh(now_ms=now[0], record_daily=False)
    return wb, d, hand, now, expiry


@contextmanager
def serving(wb):
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), workbench.make_handler(wb))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{httpd.server_port}'
    finally:
        httpd.shutdown()
        thread.join()
        httpd.server_close()


def get(base, route, token=None):
    headers = {'Authorization': f'Bearer {token}'} if token else {}
    with urlopen(Request(base + route, headers=headers), timeout=8) as resp:
        return json.load(resp)


def case_ref(hand):
    return hand.parent.parent.name + '/' + hand.name


def post(base, wb):
    return tracking.local_service(base, wb.config.shadow_service_token)


@pytest.mark.parametrize('side', ['Put', 'Call'])
def test_native_pair_two_clocks_reader_and_read_only_proofs(tmp_path, side):
    wb, d, hand, now, expiry = sample(tmp_path, f'Accept / {side}', manual=(side == 'Call'))
    critical = hand / 'evidence' / 'input.md'
    original_bytes = critical.read_bytes()
    with serving(wb) as base:
        receipt = tracking.link(d, hand, post(base, wb), RULE)
        assert receipt['state'] == 'SHADOW_LOCK_CREATED'
        assert tracking.link(d, hand, post(base, wb), RULE) == receipt
        t0 = receipt['t0']
        ep = t0['tracker_episode_id']
        expected = tracking.accepted(hand)
        assert t0['locked_at_ms'] >= expected['origin']['decided_at_ms'] > expected['origin']['decision_asof_ms']
        assert t0['tracking_asof_ms'] > expected['origin']['decision_asof_ms']
        assert t0['quote']['exchange_times_ms']['short'] <= t0['locked_at_ms']
        rows = get(base, '/api/workbench/locked-candidates')['episodes']
        got = next(r for r in rows if r['episode_id'] == ep)
        assert got['lock_actor'] == 'AI_SHADOW' and got['ai_shadow']['model'] == 'fixture-model-1.3'
        assert 'counter_excerpt' not in got['ai_shadow']
        assert tracking.contract_fields(got['contract']) == tracking.contract_fields(expected['contract'])
        if side == 'Call':
            assert next(r for r in rows if r['episode_id'] != ep)['lock_actor'] == 'USER'
        path = f'/api/workbench/locked-candidates/{ep}'
        read = get(base, path)['episode']
        assert read['episode_id'] == ep and 'counter_excerpt' not in read['ai_shadow']
        assert get(base, path, 'secret')['episode']['ai_shadow']['counter_excerpt']
        now[0] += 2 * M
        wb.refresh(now_ms=now[0], record_daily=False)
        assert len(get(base, path + '/series')['points']) == 2
        audit = tracking.AUDIT_PATH + '/' + case_ref(hand)
        for route in (tracking.AUDIT_PATH, audit, audit + '/originals'):
            with pytest.raises(HTTPError) as exc:
                get(base, route)
            assert exc.value.code == 401
        projected = get(base, audit, 'secret')
        assert projected['tracker_episode_id'] == ep and projected['tracking_state'] == 'TRACKING'
        assert projected['decision']['judgment']['side'] == side.lower()
        assert projected['condition']['confirmation_excerpt'] == RULE['confirmation_excerpt']
        assert get(base, audit + '/originals', 'secret')['input'].encode() == original_bytes
        with pytest.raises(tracking.Blocked, match='credential_required'):
            tracking.local_service(base, 'secret')('readiness', {})
        assert hand.name in d.summary(expiry).read_text(encoding='utf-8')
        assert critical.read_bytes() == original_bytes


@pytest.mark.parametrize('action,status,reason', [
    ('Reject', 'RECEIVED', 'no_valid_accept'), ('Unknown', 'RECEIVED', 'no_valid_accept'),
    ('ambiguous output', 'RECEIVED', 'no_valid_accept'), ('Accept / Call', 'TIMEOUT', 'no_valid_accept'),
    ('Accept / Call', 'RECEIVED', 'shadow_handoff_expired'),
])
def test_other_decisions_dont_start_tracking(tmp_path, action, status, reason):
    wb, d, hand, now, _ = sample(tmp_path, action, status=status)
    if reason == 'shadow_handoff_expired':
        now[0] += tracking.HANDOFF_MS
    receipt = tracking.link(d, hand, lambda *_: pytest.fail('lock request sent'), RULE)
    assert receipt['reason'] == reason and not wb.locked_candidates.list()
    if action in {'Reject', 'Unknown'}:
        assert tracking.audit_record(hand, now[0], {})['state'] == action


@pytest.mark.parametrize('gate,reason', [
    ('stale', 'tracking_quote_stale_or_unsynced'),
    ('missing', 'exact_current_contract_not_found'),
    ('held', 'quote_held_not_current'),
    ('manual', 'active_tracking_exists'),
    ('capacity', 'active_budget_exhausted'),
])
def test_real_gate_keeps_accept_without_substituting_contract(tmp_path, gate, reason):
    action = 'Accept / Put' if gate == 'manual' else 'Accept / Call'
    wb, d, hand, now, _ = sample(tmp_path, action, manual=gate in {'manual', 'capacity'})
    has_manual = gate in {'manual', 'capacity'}
    if gate == 'stale':
        now[0] += 6_000
    if gate == 'missing':
        wb._current_view['candidates'] = [c for c in wb._current_view['candidates'] if c['side'] != 'call']
    if gate == 'held':
        wb._held_snapshot_ids = {c['snapshot']['snapshot_id'] for c in wb._current_view['candidates']}
    if gate == 'capacity':
        wb.locked_candidates.max_active = 1
    with serving(wb) as base:
        service = post(base, wb)
        first = tracking.link(d, hand, service, RULE)
        assert first['reason'] == reason
        assert first['state'] == ('LOCK_BLOCKED' if has_manual else 'LINK_UNVERIFIED')
        now[0] += M
        wb.refresh(now_ms=now[0], record_daily=False)
        if has_manual:
            assert tracking.link(d, hand, lambda *_: pytest.fail('permanent block should not retry'), RULE) == first
            assert service('lock', dict(dossier_id=hand.parent.parent.name, hand_id=hand.name))['state'] == 'LINK_UNVERIFIED'
        else:
            receipt = tracking._link_with_fresh_quote(d, hand, service)
            assert receipt['state'] == 'SHADOW_LOCK_CREATED'
            assert tracking.contract_fields(wb.locked_candidates.list()[0]['contract']) == tracking.contract_fields(tracking.accepted(hand)['contract'])
        assert dossier.read(hand / 'decision.json')['judgment']['action'] == 'Accept'
        assert [r['lock_actor'] for r in wb.locked_candidates.list()] == (['USER'] if has_manual else ['AI_SHADOW'])


def test_half_commit_is_recovered_after_restart_without_a_fresh_quote(tmp_path):
    wb, d, hand, now, _ = sample(tmp_path)
    with serving(wb) as base:
        service = post(base, wb)
        def lose_reply(action, payload):
            assert service(action, payload)['state'] == 'SHADOW_LOCK_CREATED'
            raise TimeoutError('native commit succeeded; HTTP response lost')
        assert tracking.link(d, hand, lose_reply, RULE)['state'] == 'LINK_UNVERIFIED'
    now[0] += 25 * M
    restarted = workbench.AstraWorkbench(wb.config, clock_ms=lambda: now[0])
    assert not restarted._lockable_points
    with serving(restarted) as base:
        recovered = tracking.link(d, hand, post(base, restarted))
        assert recovered['state'] == 'ALREADY_LINKED'
        assert len(restarted.locked_candidates.list()) == 1
        assert len(list((hand.parent.parent / 'episodes').glob('episode_*'))) == 1


def test_manual_stop_no_revoke_and_archive_survives_retention(tmp_path, monkeypatch):
    wb, d, hand, now, expiry = sample(tmp_path)
    with serving(wb) as base:
        service = post(base, wb)
        receipt = tracking.link(d, hand, service, RULE)
        t0 = receipt['t0']
        ep = t0['tracker_episode_id']
        target = hand.parent.parent / 'episodes' / receipt['origin']['dossier_episode_id']
        original = (target / 'condition.json').read_bytes()
        tick = dossier.read(target / 'condition.json')['check_clock']['first_at_ms']
        now[0] = tick
        identity = dict(index_name='btc_usd', expiry_ms=expiry)
        assert d.check(target, dict(identity, points=[]), tick)['kind'] == 'CONDITION_UNKNOWN'
        assert d.check(target, dict(identity, points=[[tick, 103_100]]), tick)['kind'] == 'CONDITION_TRIGGERED'
        wb.locked_candidates.record_gap(ep, now_ms=now[0], reason='fixture_feed_gap')
        now[0] += 2 * M
        wb.refresh(now_ms=now[0], record_daily=False)
        wb.stop_locked_candidate(ep, authorization='Bearer secret')
        assert tracking.sync(d, hand, service)['state'] == 'stopped'
        assert not wb.locked_candidates.compact(now[0] + 1, retention={'drop_after_ms': 0})['dropped_episodes']
        wb.delivery_price_fetch = lambda _: None
        now[0] = expiry + 49 * 3_600_000
        wb._settle_expired_locks(now[0])
        assert tracking.sync(d, hand, service)['settlement'] == 'delivery_price_unavailable'
        unavailable = tracking.load(hand / 'tracking' / 'terminal.json')
        price = 104_000
        now[0] += 3_600_000
        wb.delivery_price_fetch = lambda _: dict(delivery_price_usd=price, source='Deribit fixture')
        wb._settle_expired_locks(now[0])
        assert tracking.sync(d, hand, service)['settlement'] == 'settled'
        seal = tracking.load(hand / 'tracking' / 'terminal.json')
        assert seal['export_id'] != unavailable['export_id']
        assert (hand / 'tracking' / 'exports' / unavailable['export_id'] / 'trajectory.json').is_file()
        trajectory = tracking.load(hand / 'tracking' / 'exports' / seal['export_id'] / 'trajectory.json')
        assert len(trajectory['points']) == 2 and trajectory['gaps'][0]['reason'] == 'fixture_feed_gap'
        monkeypatch.setattr(dossier, 'public', lambda *_args, **_kw:
                            {'data': [{'date': dossier.utc(expiry).date().isoformat(), 'delivery_price': price}]})
        outcome = dossier.read(d.official(expiry, dict(identity, points=[])) / 'outcome.json')
        assert outcome['hands'][0]['sides']['call']['delivery_payout_btc'] == trajectory['terminal_valuation']['payout_btc']
        assert outcome['actual_execution_net_btc'] is None and (target / 'condition.json').read_bytes() == original
        now[0] += 61 * dossier.DAY
        assert wb.compact_locked(now[0])['dropped_episodes'] == [ep]
        view = get(base, tracking.AUDIT_PATH + '/' + case_ref(hand), 'secret')
        assert view['tracking_state'] == 'ARCHIVED' and view['tracker_episode_id'] is None
        assert view['archive']['settlement_state'] == 'settled'
        archived_path = tracking.AUDIT_PATH + '/' + case_ref(hand) + '/trajectory'
        assert get(base, archived_path, 'secret')['terminal_basis'] == 'HOLD_TO_EXPIRY_SHADOW'
        with pytest.raises(HTTPError) as unauthorized:
            get(base, archived_path)
        assert unauthorized.value.code == 401
        assert all(e['kind'] not in {'AI_REVOKE', 'SHADOW_ACTION'} for e in view['events'])


def test_first_valid_import_requires_v13_provenance(tmp_path):
    wb, d, hand, now, expiry = sample(tmp_path, status='TIMEOUT')
    strict = dossier.Dossiers(d.root, clock=lambda: now[0], require_reply_provenance=True)
    raw = ('**Accept / Call**\n' + '\n'.join(EXCERPTS.values())).encode()
    bad = strict.respond(hand, 'missing-identity', 'RECEIVED', raw, EXCERPTS)
    assert bad['status'] == 'PROVENANCE_MISSING' and strict.select(hand) is None
    good = strict.respond(hand, 'with-identity', 'RECEIVED', raw, EXCERPTS,
                          model='fixture-model-1.3', requested=now[0], decided=now[0])
    assert good['status'] == 'VALID' and strict.select(hand)['attempt_id'] == 'with-identity'
    now[0] += 20 * M
    late = strict.respond(hand, 'late', 'RECEIVED', raw.replace(b'Call**', b'Call**\n'), EXCERPTS,
                          model='fixture-model-1.3', requested=now[0], decided=now[0])
    assert late['status'] == 'LATE'
    assert strict.select(hand)['attempt_id'] == 'with-identity'


def test_prewarm_has_no_writes_and_disables_only_legacy_scheduler(tmp_path):
    wb, d, hand, now, _ = sample(tmp_path)
    cfg = plan()
    slot = cfg['effective_at_ms']
    now[0] = slot - 5 * M
    prior = sorted(d.root.rglob('*'))
    warm = tracking.prewarm(cfg, tmp_path, lambda _action, _body: {'enabled': True},
                           clock=d.clock, public=lambda *_a, **_k: [])
    assert warm['state'] == 'PREWARM' and not warm['native_expiry_present']
    assert sorted(d.root.rglob('*')) == prior
    now[0] = slot
    with pytest.raises(tracking.Blocked, match='outside_plan_prewarm_window'):
        tracking.prewarm(cfg, tmp_path, lambda *_: {}, clock=d.clock)
    assert wb._maybe_scheduled_review(slot, wait=True) is None
    assert wb.scheduled_review_status(slot)['paused_for_shadow_experiment']
    with pytest.raises(workbench.WorkbenchError, match='formal shadow experiment'):
        wb.review_candidate('unused', 'unused', authorization=None, _scheduled=True)
    assert dossier.read(hand / 'decision.json')['model'] == 'fixture-model-1.3'


def test_archive_experiment_keeps_inquiry_and_gates_legacy_player(tmp_path, monkeypatch):
    monkeypatch.setenv('LLM_API_KEY', 'fixture')
    monkeypatch.setenv('ASTRA_PLAYER_ENABLED', '1')
    monkeypatch.setenv('ASTRA_RESEARCH_INQUIRY_ENABLED', '1')
    monkeypatch.setattr(archive, '_http_json', lambda *_: {})
    seen = []
    def tick(_archive, **callbacks):
        seen.append((callable(callbacks['run_player']), callable(callbacks['run_inquiry'])))
        return {}
    monkeypatch.setattr(archive, 'tick', tick)
    args = Namespace(workbench='http://127.0.0.1:8787', inbox=tmp_path, receipts=tmp_path, workbench_dir=tmp_path)
    monkeypatch.delenv('ASTRA_AI_SHADOW_EXPERIMENT', raising=False)
    archive._tick_main(args, archive.Archive(tmp_path))
    monkeypatch.setenv('ASTRA_AI_SHADOW_EXPERIMENT', '1')
    archive._tick_main(args, archive.Archive(tmp_path))
    assert seen == [(True, True), (False, True)]


def test_scheduled_shadow_coexists_with_adhoc_but_respects_manual_and_duplicate_locks(tmp_path):
    wb, d, adhoc_hand, now, expiry = sample(tmp_path, 'Accept / Call', manual=True)
    source = dossier.read(adhoc_hand / 'evidence' / 'source.json')
    raw_bytes = (adhoc_hand / 'responses' / 'fixture-reply' / 'response.raw').read_bytes()

    with serving(wb) as base:
        service = post(base, wb)
        adhoc_receipt = tracking.link(d, adhoc_hand, service, RULE)
        assert adhoc_receipt['state'] == 'SHADOW_LOCK_CREATED'
        adhoc_ep = adhoc_receipt['t0']['tracker_episode_id']
        call_point = wb.locked_candidates.read_point(adhoc_receipt['t0']['point_id'])
        base_origin = dict(adhoc_receipt['origin'])

        # Another ad-hoc lock on the same contract is still blocked.
        second_adhoc = d.expect(expiry, 'STARTUP_AD_HOC', reference='second-adhoc')
        d.deal(second_adhoc, source)
        d.respond(second_adhoc, 'reply-2', 'RECEIVED', raw_bytes, EXCERPTS,
                  model='fixture-model-1.3', requested=now[0], decided=now[0])
        blocked_adhoc = tracking.link(d, second_adhoc, service, RULE)
        assert blocked_adhoc['state'] == 'LOCK_BLOCKED' and blocked_adhoc['reason'] == 'active_tracking_exists'

        # Formal SCHEDULED lock (plan_id != None, different hand_id) coexists with ad-hoc on same contract.
        sched_origin = dict(base_origin, plan_id='daily-deribit-btc-r2@1', hand_id='hand_scheduled_01',
                            dossier_episode_id='episode_scheduled_01', request_key='shadow_' + 'a' * 24)
        sched_res = wb.locked_candidates.lock(call_point, request_key=sched_origin['request_key'],
                                              now_ms=now[0], shadow_origin=sched_origin)
        assert sched_res['created'] is True
        sched_ep = sched_res['episode']['episode_id']
        assert sched_ep != adhoc_ep
        episodes = get(base, '/api/workbench/locked-candidates')['episodes']
        assert {adhoc_ep, sched_ep} <= {row['episode_id'] for row in episodes}

        # Second SCHEDULED hand on the same contract is blocked once a scheduled episode is active.
        dup_sched_origin = dict(base_origin, plan_id='OTHER-PLAN', hand_id='hand_scheduled_02',
                                dossier_episode_id='episode_scheduled_02', request_key='shadow_' + 'b' * 24)
        with pytest.raises(tracking.store.LockedCandidateError) as dup_exc:
            wb.locked_candidates.lock(call_point, request_key=dup_sched_origin['request_key'],
                                      now_ms=now[0], shadow_origin=dup_sched_origin)
        assert dup_exc.value.code == 'active_tracking_exists'

        # Manual active lock on the Put contract coexists with a formal SCHEDULED hand without blocking it,
        # while a second SCHEDULED hand on Put is still blocked and ad-hoc remains blocked by manual.
        put_ep = next(ep for ep in wb.locked_candidates.list() if ep['lock_actor'] == 'USER')
        put_point = wb.locked_candidates.read_point(put_ep['t0_baseline_point_id'])
        put_sched_origin = dict(base_origin, plan_id='daily-deribit-btc-r2@1', hand_id='hand_put_sched_01',
                                dossier_episode_id='episode_put_sched_01', request_key='shadow_' + 'c' * 24)
        put_sched_res = wb.locked_candidates.lock(put_point, request_key=put_sched_origin['request_key'],
                                                  now_ms=now[0], shadow_origin=put_sched_origin)
        assert put_sched_res['created'] is True
        assert put_sched_res['episode']['episode_id'] != put_ep['episode_id']
        assert put_sched_res['episode']['lock_actor'] == 'AI_SHADOW'

        dup_put_sched = dict(base_origin, plan_id='OTHER-PLAN', hand_id='hand_put_sched_02',
                             dossier_episode_id='episode_put_sched_02', request_key='shadow_' + 'd' * 24)
        with pytest.raises(tracking.store.LockedCandidateError) as manual_exc:
            wb.locked_candidates.lock(put_point, request_key=dup_put_sched['request_key'],
                                      now_ms=now[0], shadow_origin=dup_put_sched)
        assert manual_exc.value.code == 'active_tracking_exists'


def test_sync_check_due_backfills_grid_checks_idempotently(tmp_path, monkeypatch):
    wb, d, hand, now, expiry = sample(tmp_path)
    five_min_rule = dict(RULE, check_interval_ms=5 * M)
    with serving(wb) as base:
        service = post(base, wb)
        receipt = tracking.link(d, hand, service, five_min_rule)
        ep_dir = hand.parent.parent / 'episodes' / receipt['origin']['dossier_episode_id']
        frozen = dossier.read(ep_dir / 'condition.json')
        first_tick = frozen['check_clock']['first_at_ms']
        entry_ms = frozen['entry_at_ms']
        now[0] = first_tick + 6 * M
        wb.refresh(now_ms=now[0], record_daily=False)
        points = [[t, 103_500 if t >= first_tick + 5 * M else 100_000]
                  for t in range((entry_ms // M + 1) * M, now[0] + 1, M)]
        monkeypatch.setattr(archive.Deribit, 'index_path', lambda _self: [r for r in points if r[0] != first_tick])
        missing = tracking.sync(d, hand, service, check_due=True)
        assert [c['kind'] for c in missing['checks']] == ['CONDITION_UNKNOWN', 'CONDITION_UNKNOWN']
        monkeypatch.setattr(archive.Deribit, 'index_path', lambda _self: points)

        synced = tracking.sync(d, hand, service, check_due=True)
        assert [c['observed_at_ms'] for c in synced['checks']] == [first_tick, first_tick + 5 * M]
        assert [c['kind'] for c in synced['checks']] == ['CONDITION_NOT_TRIGGERED', 'CONDITION_TRIGGERED']

        # Re-running at a later clock skips already checked ticks and records no duplicates.
        now[0] += M
        wb.refresh(now_ms=now[0], record_daily=False)
        resynced = tracking.sync(d, hand, service, check_due=True)
        assert resynced['checks'] == []
        assert len(list((ep_dir / 'events').glob('check_*.json'))) == 4


def test_settle_merges_tracking_t0_net_credit_and_shadow_net(tmp_path, monkeypatch):
    wb, d, hand, now, expiry = sample(tmp_path)
    with serving(wb) as base:
        service = post(base, wb)
        receipt = tracking.link(d, hand, service, RULE)
        expected_credit = receipt['t0']['economy']['net_credit_after_entry_fee_btc']
        assert expected_credit is not None
        now[0] = expiry + 2 * M
        price = 104_000.0
        monkeypatch.setattr(dossier, 'public', lambda *_a, **_k:
                            {'data': [{'date': dossier.utc(expiry).date().isoformat(), 'delivery_price': price}]})
        monkeypatch.setattr(dossier.archive.Deribit, 'index_path', lambda _self: [])
        outcome = dossier.read(d.official(expiry) / 'outcome.json')
        row = outcome['hands'][0]
        assert row['t0_net_credit_after_entry_fee_btc'] == expected_credit
        payout = row['sides']['call']['delivery_payout_btc']
        assert row['hold_to_expiry_shadow_net_btc'] == round(expected_credit - payout, 8)


def test_prewarm_dry_run_checks_outside_window_without_writes(tmp_path):
    cfg = plan()
    slot = cfg['effective_at_ms']
    outside_before = slot - 7 * 60 * M
    prior = sorted(tmp_path.rglob('*'))
    warm = tracking.prewarm(cfg, tmp_path, lambda _action, _body: {'enabled': True},
                            clock=lambda: outside_before, public=lambda *_a, **_k: [], dry_run=True)
    assert warm['state'] == 'PREWARM_DRY_RUN' and warm['dry_run'] is True
    assert warm['scheduled_at_ms'] == slot
    assert sorted(tmp_path.rglob('*')) == prior

    outside_after = slot + 5 * M
    warm_next = tracking.prewarm(cfg, tmp_path, lambda _action, _body: {'enabled': True},
                                 clock=lambda: outside_after, public=lambda *_a, **_k: [], dry_run=True)
    assert warm_next['state'] == 'PREWARM_DRY_RUN'
    assert warm_next['scheduled_at_ms'] == slot + dossier.DAY
    with pytest.raises(tracking.Blocked, match='outside_plan_prewarm_window'):
        tracking.prewarm(cfg, tmp_path, lambda *_: {}, clock=lambda: outside_before)


def test_lock_expiry_levels_refresh_and_shadow_dossier_backfill(tmp_path):
    wb, d, hand, now, expiry = sample(tmp_path)
    with serving(wb) as base:
        service = post(base, wb)
        receipt = tracking.link(d, hand, service, RULE)
        ep = receipt['t0']['tracker_episode_id']
        detail = get(base, f'/api/workbench/locked-candidates/{ep}', 'secret')
        pin_row = next(r for r in detail['rows'] if r['key'] == 'levels.pin')
        assert pin_row['t0']['value'] is not None
        assert pin_row['t0']['compat_backfill_applied'] is True
        assert pin_row['t0']['backfill_source'] == 'shadow_dossier.evidence.source.L3.expiry_walls'


def test_legacy_structure_clock_backfill_and_band_snapshot_in_seal_source():
    raw_row = {
        'schema': 'structure_snapshot@0.1',
        'expiry_ms': 1_794_902_400_000,
        'at_ms': 1_794_800_000_000,
        'profile': {'walls': {'pin': 64000.0}},
    }
    norm = dossier._normalize_legacy_structure_row(raw_row)
    assert norm['available_at_ms'] == raw_row['at_ms']
    assert norm['pin_source'] == dossier.archive.expiry_structure.PIN_SOURCE
    assert norm['pin_method'] == dossier.archive.expiry_structure.PIN_METHOD
    assert norm['pin_status'] == 'ok'
    assert norm['compat_backfill_applied'] is True
    sealed = dossier.seal_source([], [], {}, [norm], band_snapshot={'factor_snapshot': {'anchor': {'band_half': 500.0}}})
    assert sealed['band_snapshot']['factor_snapshot']['anchor']['band_half'] == 500.0


def test_llm_settings_retries_and_shared_budget_resolution(tmp_path, monkeypatch):
    for key in (
        'ASTRA_PLAYER_LLM_RETRIES', 'ASTRA_PLAYER_USAGE_LEDGER', 'ASTRA_WORKBENCH_USAGE_LEDGER',
        'ASTRA_PLAYER_DAILY_HTTP_LIMIT', 'ASTRA_WORKBENCH_DAILY_HTTP_LIMIT',
        'LLM_DAILY_HTTP_CAP', 'LLM_TIMEOUT_SEC',
    ):
        monkeypatch.delenv(key, raising=False)

    assert tracking._llm_settings({'timeout_s': 300})['retries'] == 0
    assert tracking._llm_settings({'timeout_s': 120})['retries'] == 1

    monkeypatch.setenv('ASTRA_PLAYER_LLM_RETRIES', '2')
    assert tracking._llm_settings({'timeout_s': 300})['retries'] == 2
    assert tracking._llm_settings({'timeout_s': 120, 'retries': 0})['retries'] == 0

    wb_ledger = tmp_path / 'wb_ledger.json'
    player_ledger = tmp_path / 'player_ledger.json'
    monkeypatch.setenv('ASTRA_WORKBENCH_USAGE_LEDGER', str(wb_ledger))
    monkeypatch.setenv('LLM_DAILY_HTTP_CAP', '11')
    cfg_wb = tracking._llm_settings()
    assert isinstance(cfg_wb['budget'], signal_llm_review.DailyHttpBudget)
    assert cfg_wb['budget'].state_path == wb_ledger and cfg_wb['budget'].limit == 11

    monkeypatch.setenv('ASTRA_PLAYER_USAGE_LEDGER', str(player_ledger))
    monkeypatch.setenv('ASTRA_WORKBENCH_DAILY_HTTP_LIMIT', '7')
    monkeypatch.setenv('ASTRA_PLAYER_DAILY_HTTP_LIMIT', '4')
    cfg_player = tracking._llm_settings()
    assert cfg_player['budget'].state_path == player_ledger and cfg_player['budget'].limit == 4

    # Transport suppresses env-auto budget construction, while explicit budget is preserved.
    assert tracking._llm_settings({'transport': lambda *_: {}})['budget'] is None
    explicit = signal_llm_review.DailyHttpBudget(tmp_path / 'explicit.json', limit=9)
    assert tracking._llm_settings({'transport': lambda *_: {}, 'budget': explicit})['budget'] is explicit


def test_auto_deal_budget_exhausted_timeout_attempts_and_audit_states(tmp_path, monkeypatch):
    for key in ('LLM_API_KEY', 'ASTRA_WORKBENCH_LLM_API_KEY', 'ASTRA_PLAYER_USAGE_LEDGER', 'ASTRA_WORKBENCH_USAGE_LEDGER'):
        monkeypatch.delenv(key, raising=False)

    clk = Clock(SLOT + M)
    monkeypatch.setattr(dossier, 'collect', lambda *_a, **_kw: fixture_source(clk))

    # 1. LLM_NOT_CONFIGURED surfaces instead of AWAITING_RESPONSE when attempts is empty
    d_unconf = dossier.Dossiers(tmp_path / 'unconfigured', clock=clk)
    res_unconf = tracking.auto_deal(d_unconf, plan(), tmp_path, lambda *_: {}, llm_config={'api_key': ''})
    assert res_unconf[0]['status'] == 'LLM_NOT_CONFIGURED'
    unconf_hand = next((tmp_path / 'unconfigured' / 'dossiers').glob('Deribit_BTC_*/hands/hand_*'))
    assert tracking.audit_record(unconf_hand, clk(), {})['state'] == 'LLM_NOT_CONFIGURED'

    # 2. POLICY_BLOCKED note on undealt hand surfaces cleanly
    d_pol = dossier.Dossiers(tmp_path / 'policy', clock=clk)
    monkeypatch.setattr(dossier, 'collect', lambda *_a, **_kw: (_ for _ in ()).throw(dossier.player.HandNotDealt('blocked_by_policy')))
    tracking.auto_deal(d_pol, plan(), tmp_path, lambda *_: {}, llm_config={'api_key': 'k'})
    pol_hand = next((tmp_path / 'policy' / 'dossiers').glob('Deribit_BTC_*/hands/hand_*'))
    assert tracking.audit_record(pol_hand, clk(), {})['state'] == 'POLICY_BLOCKED'

    # 3. TIMEOUT classification + error provenance + up to 3 attempts + DailyBudgetExceeded lock-out
    monkeypatch.setattr(dossier, 'collect', lambda *_a, **_kw: fixture_source(clk))
    d_run = dossier.Dossiers(tmp_path / 'runtime', clock=clk)
    calls = [0]

    def timeout_transport(*_a, **_kw):
        calls[0] += 1
        if calls[0] == 1:
            raise urllib.error.URLError(TimeoutError('connect timed out'))
        raise TimeoutError('read timed out')

    # Attempt 1: URLError(TimeoutError) -> TIMEOUT with reasoning_effort and error saved
    r1 = tracking.auto_deal(
        d_run, plan(), tmp_path, lambda *_: {},
        llm_config={'api_key': 'k', 'model': 'm-test', 'reasoning_effort': 'high', 'retries': 0, 'transport': timeout_transport},
    )
    assert r1[0]['status'] == 'TIMEOUT'
    assert 'URLError' in r1[0]['error']
    hand = next((tmp_path / 'runtime' / 'dossiers').glob('Deribit_BTC_*/hands/hand_*'))
    aud = tracking.audit_record(hand, clk(), {}, detail=True)
    assert aud['state'] == 'TIMEOUT'
    assert len(aud['attempts']) == 1
    assert aud['attempts'][0]['status'] == 'TIMEOUT'
    assert aud['attempts'][0]['reasoning_effort'] == 'high'
    assert 'URLError' in aud['attempts'][0]['error']

    # A transport timeout cannot tell whether the provider received the POST.
    clk.ms += 1_000
    r2 = tracking.auto_deal(d_run, plan(), tmp_path, lambda *_: {},
        llm_config={'api_key': 'k', 'retries': 4, 'transport': timeout_transport})
    assert r2[0]['status'] == 'AUTO_ATTEMPTED' and calls == [1]
    assert len(tracking.audit_record(hand, clk(), {}, detail=True)['attempts']) == 1
    zero_budget = signal_llm_review.DailyHttpBudget(tmp_path / 'zero_budget.json', limit=0)

    # Fresh hand with 0 attempts hitting DailyBudgetExceeded surfaces DAILY_BUDGET_EXHAUSTED on audit_record
    d_bud = dossier.Dossiers(tmp_path / 'budget_zero', clock=clk)
    r_bud = tracking.auto_deal(
        d_bud, plan(), tmp_path, lambda *_: {},
        llm_config={'api_key': 'k', 'model': 'm-test', 'retries': 0, 'budget': zero_budget, 'transport': timeout_transport},
    )
    assert r_bud[0]['status'] == 'DAILY_BUDGET_EXHAUSTED'
    bud_hand = next((tmp_path / 'budget_zero' / 'dossiers').glob('Deribit_BTC_*/hands/hand_*'))
    assert tracking.audit_record(bud_hand, clk(), {})['state'] == 'DAILY_BUDGET_EXHAUSTED'

    again = tracking.auto_deal(d_bud, plan(), tmp_path, lambda *_: {},
        llm_config={'api_key': 'k', 'transport': timeout_transport})
    assert again[0]['status'] == 'DAILY_BUDGET_EXHAUSTED' and calls == [1]


def test_shadow_quote_endpoint_lazy_entry_basis_and_ard_band_window(tmp_path, monkeypatch):
    wb, d, hand, now, expiry = sample(tmp_path, 'Accept / Call')
    with serving(wb) as base:
        svc = post(base, wb)
        receipt = tracking.link(d, hand, svc, RULE)
        ep = receipt['t0']['tracker_episode_id']
        ep_dir = hand.parent.parent / 'episodes' / receipt['origin']['dossier_episode_id']

        # Before any hold review runs: audit_record lazily builds entry_basis without writing & populates shadow_management
        assert not (ep_dir / 'entry_basis.json').exists()
        aud = tracking.audit_record(hand, now[0], {ep: 'active'}, detail=True)
        assert not (ep_dir / 'entry_basis.json').exists()
        assert aud['entry_basis']['schema'] == 'entry_decision_basis@1'
        assert aud['entry_basis']['M1']['invalidation'] == RULE['invalidation']
        assert aud['hold_reviews'] == []
        assert aud['shadow_management']['exit_status'] == 'NONE'

        # Live shadow quote endpoint captures a fresh point and validates payload/token
        now[0] += 30_000
        q_res = svc('quote', dict(dossier_id=hand.parent.parent.name, hand_id=hand.name))
        assert q_res['state'] == 'QUOTE_CAPTURED'
        assert q_res['execution_allowed'] is False
        assert q_res['latest_point']['candidate']['snapshot']['market']['valuation_ts_ms'] == now[0]

        with pytest.raises(tracking.Blocked, match='invalid_shadow_request'):
            svc('quote', dict(dossier_id=hand.parent.parent.name))
        with pytest.raises(tracking.Blocked, match='credential_required'):
            tracking.local_service(base, 'wrong-token')('quote', dict(dossier_id=hand.parent.parent.name, hand_id=hand.name))

        # Capture failure returns QUOTE_UNAVAILABLE without crashing HTTP handler
        monkeypatch.setattr(wb, 'quote_capture', lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError('book_down')))
        q_fail = svc('quote', dict(dossier_id=hand.parent.parent.name, hand_id=hand.name))
        assert q_fail['state'] == 'QUOTE_UNAVAILABLE'
        assert q_fail['latest_point'] is None
        assert q_fail['error'] == 'WorkbenchError'
        assert q_fail['execution_allowed'] is False

    # _episode_ard_state searches band_snapshot at old_start + 4h rather than tick when source.json omits band_snapshot
    arch_root = tmp_path / 'ard_arch'
    struct_dir = arch_root / 'structure'
    struct_dir.mkdir(parents=True)
    exp_day = dossier.utc(expiry).date().isoformat()
    (struct_dir / f'{exp_day}.jsonl').write_text(json.dumps({
        'schema': 'structure_snapshot@0.1', 'expiry_ms': expiry, 'at_ms': now[0] - 10 * M,
        'available_at_ms': now[0] - 9 * M, 'pin_source': 'deribit_gex', 'pin_method': 'gamma_peak',
        'pin_status': 'ok', 'profile': {'walls': {'pin': 100_000.0}},
    }) + '\n', encoding='utf-8')
    old_start = now[0] - 8 * 3_600_000
    tick = now[0]
    seen_cutoffs = []
    monkeypatch.setattr(dossier, '_resolve_band_snapshot', lambda _root, cutoff: seen_cutoffs.append(cutoff) or None)
    frozen_ard = {
        'contract': receipt['origin'] and dossier.read(ep_dir / 'condition.json')['contract'],
        'entry_at_ms': dossier.read(ep_dir / 'condition.json')['entry_at_ms'],
        'ard_basis': {'facts': {'start_ms': old_start, 'anchor': {'pin': 100_000.0}, 'counts': {}}},
    }
    tracking._episode_ard_state(frozen_ard, ep_dir, arch_root, {'index_name': 'btc_usd', 'expiry_ms': expiry, 'points': [[tick, 100_000.0]]}, tick)
    assert seen_cutoffs == [old_start + 4 * 3_600_000]


def test_reviewer_guards_unauth_redaction_deduped_checks_and_revoke_isolation(tmp_path, monkeypatch):
    # 1) _condition_check_summary orders by observed_at_ms & deduplicates healed CONDITION_UNKNOWN
    checks = [
        {'event_id': 'ev_z', 'kind': 'CONDITION_NOT_TRIGGERED', 'observed_at_ms': SLOT + 10 * M, 'recorded_at_ms': SLOT + 11 * M},
        {'event_id': 'ev_a', 'kind': 'CONDITION_UNKNOWN', 'observed_at_ms': SLOT + 5 * M, 'recorded_at_ms': SLOT + 11 * M},
        {'event_id': 'ev_b', 'kind': 'CONDITION_NOT_TRIGGERED', 'observed_at_ms': SLOT + 5 * M, 'recorded_at_ms': SLOT + 11 * M},
    ]
    summary = tracking._condition_check_summary(checks)
    assert summary['total'] == 2
    assert summary['unknown'] == 0
    assert summary['not_triggered'] == 2
    assert summary['mechanical_condition_state'] == 'INTACT'
    assert summary['latest_at_ms'] == SLOT + 10 * M
    clock = {'first_at_ms': SLOT + 5 * M, 'interval_ms': 5 * M}
    assert tracking._condition_check_summary([], asof_ms=SLOT, check_clock=clock)['mechanical_condition_state'] == 'NOT_DUE'
    assert tracking._condition_check_summary([], asof_ms=SLOT + 5 * M, check_clock=clock)['mechanical_condition_state'] == 'NOT_DUE'
    assert tracking._condition_check_summary([], asof_ms=SLOT + 5 * M + 2_000, check_clock=clock)['mechanical_condition_state'] == 'NOT_DUE'
    assert tracking._condition_check_summary([], asof_ms=SLOT + 6 * M, check_clock=clock)['mechanical_condition_state'] == 'DATA_GAP'
    assert tracking._condition_check_summary(checks, asof_ms=SLOT + 15 * M, check_clock=clock)['mechanical_condition_state'] == 'INTACT'
    assert tracking._condition_check_summary(checks, asof_ms=SLOT + 16 * M, check_clock=clock)['mechanical_condition_state'] == 'DATA_GAP'

    # 2) Unauthenticated locked-candidates detail redacts private AI public_reply & hold_reviews
    wb, d, hand, now, _ = sample(tmp_path)
    with serving(wb) as base:
        svc = post(base, wb)
        receipt = tracking.link(d, hand, svc, RULE)
        lce_id = receipt['t0']['tracker_episode_id']
        ep_dir = hand.parent.parent / 'episodes' / receipt['origin']['dossier_episode_id']
        now[0] += 5 * M
        exported = svc('export', dict(dossier_id=hand.parent.parent.name, hand_id=hand.name))
        # Seed continuous <10m points + CONDITION_TRIGGERED to bypass debounce (Priority A)
        exported['points'] = [
            {'metrics': {'asof_ms': now[0] - 2 * M, 'spot_usd': 102_000.0, 'short_distance_pct': 0.98, 'flow_ready': False}},
            {'metrics': {'asof_ms': now[0] - M, 'spot_usd': 103_200.0, 'short_distance_pct': -0.19, 'flow_ready': False}},
        ]
        d.event(ep_dir, dossier.identity('chk', now[0] - M), 'CONDITION_TRIGGERED',
                dict(result={'rule': 'entry_not_returned'}), observed=now[0] - M, available=now[0])
        seen_revoke_pts = []
        def boom_revoke(_d, _ep, _fr, q_pt, _at, **_kw):
            seen_revoke_pts.append(q_pt)
            raise RuntimeError('quote_io_boom')
        write_revoke = tracking._record_revoke_shadow_action
        monkeypatch.setattr(tracking, '_record_revoke_shadow_action', boom_revoke)
        res = tracking.evaluate_hold_management(
            d, hand, exported, service=lambda action, _b: {'state': 'QUOTE_UNAVAILABLE', 'latest_point': None},
            llm_config={
                'api_key': 'k', 'model': 'm', 'reasoning_effort': 'high',
                'transport': lambda *a, **k: {'choices': [{'finish_reason': 'stop', 'message': {
                    'content': "## REVOKE\n\n突破短腿边界。\n<HOLD_REVIEW_JSON>{\"verdict\":\"REVOKE\",\"affected_conditions\":[\"M1\"],\"public_reply\":\"PRIVATE_REPLY_SECRET\",\"key_facts\":[\"M1\"]}</HOLD_REVIEW_JSON>"
                }}]},
            },
        )
        assert res['action'] == 'REVIEWED' and res['verdict'] == 'REVOKE'
        assert 'shadow_action_error' in res
        # QUOTE_UNAVAILABLE preserved exported['latest_point'] fallback instead of overwriting with None
        assert seen_revoke_pts == [tracking.locked_compare.short_buyback_quote(exported['latest_point'])]
        st_saved = dossier.read(ep_dir / 'trigger_state.json')
        assert st_saved['revoked'] is True and st_saved['inflight'] is False

        monkeypatch.setattr(tracking, '_record_revoke_shadow_action', write_revoke)
        for _ in range(2):
            tracking.sync(d, hand, svc, run_review=False)
        actions = [tracking.load(p) for p in (ep_dir / 'events').glob('*.json')
                   if tracking.load(p)['kind'] in {'SIMULATED_ORDER', 'SHADOW_ACTION'}]
        assert len(actions) == 1 and actions[0]['body']['review_id'] == res['review_id']

        unauth_raw = get(base, f'/api/workbench/locked-candidates/{lce_id}')
        latest_unauth = unauth_raw['ai_dossier_management']['latest_review']
        assert 'public_reply' not in latest_unauth
        assert latest_unauth['verdict'] == 'REVOKE'
        assert 'hold_reviews' not in unauth_raw['ai_dossier_management']

        auth_raw = get(base, f'/api/workbench/locked-candidates/{lce_id}', 'secret')
        assert auth_raw['ai_dossier_management']['latest_review']['public_reply'] == 'PRIVATE_REPLY_SECRET'


def test_auto_deal_refreshes_stale_cached_quote_before_link(tmp_path):
    wb, d, hand, now, _ = sample(tmp_path, 'Accept / Call')
    # Advance clock by 20s so cached _current_view quote (>5s) would fail confirm_quote without refresh_quote
    now[0] += 20_000
    with serving(wb) as base:
        receipt = tracking._link_with_fresh_quote(d, hand, post(base, wb), RULE)
    assert receipt['state'] == 'SHADOW_LOCK_CREATED'
    assert now[0] - receipt['t0']['quote']['valuation_ts_ms'] <= 5_000


def test_refresh_quote_failure_still_blocks_lock_without_relaxing_age(tmp_path):
    wb, d, hand, now, _ = sample(tmp_path, 'Accept / Call')
    now[0] += 20_000
    wb.quote_capture = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('exchange_down'))
    with serving(wb) as base:
        receipt = tracking._link_with_fresh_quote(d, hand, post(base, wb), RULE)
    assert receipt['state'] == 'LINK_UNVERIFIED'
    assert receipt['reason'] == 'tracking_quote_stale_or_unsynced'


def test_writer_lock_released_during_hold_review_llm_call(tmp_path):
    wb, d, hand, now, expiry = sample(tmp_path, 'Accept / Call')
    with serving(wb) as base:
        service = post(base, wb)
        tracking.link(d, hand, service, RULE)
        exported = service('export', dict(dossier_id=hand.parent.parent.name, hand_id=hand.name))
        ep_dir = hand.parent.parent / 'episodes' / tracking.accepted(hand)['origin']['dossier_episode_id']
        exported['points'] = [
            {'metrics': {'asof_ms': now[0] - 2 * M, 'spot_usd': 101_000.0, 'short_distance_pct': 0.98, 'flow_ready': False}},
            {'metrics': {'asof_ms': now[0] - M, 'spot_usd': 102_500.0, 'short_distance_pct': -0.49, 'flow_ready': False}},
        ]
        d.event(ep_dir, dossier.identity('chk', now[0] - M), 'CONDITION_TRIGGERED',
                dict(result={'rule': 'entry_not_returned'}), observed=now[0] - M, available=now[0])
        lock_acquired_during_llm = []

        def concurrent_writer_transport(*_a, **_k):
            # Another process / writer must NOT hit BlockingIOError while LLM call is in flight!
            with dossier.writer(d.root, timeout_s=0.2):
                lock_acquired_during_llm.append(True)
            return {'choices': [{'finish_reason': 'stop', 'message': {
                'content': "## REVOKE\n\n确认触发。\n<HOLD_REVIEW_JSON>{\"verdict\":\"REVOKE\",\"affected_conditions\":[\"M1\"],\"public_reply\":\"确认撤销\",\"key_facts\":[\"M1\"]}</HOLD_REVIEW_JSON>"
            }}]}

        res = tracking.evaluate_hold_management(
            d, hand, exported, service=lambda *_: {'state': 'QUOTE_UNAVAILABLE', 'latest_point': None},
            llm_config={'api_key': 'k', 'model': 'm', 'transport': concurrent_writer_transport},
        )
        assert lock_acquired_during_llm == [True]
        assert res['action'] == 'REVIEWED' and res['verdict'] == 'REVOKE'


def test_v11_unified_entry_gate_and_skip_telemetry(tmp_path):
    wb, d, hand, now, _ = sample(tmp_path, 'Accept / Call')
    wb.config.shadow_experiment = True
    wb.config.server_token = 'tok'
    wb.config.api_key = 'k'
    with serving(wb) as base:
        service = post(base, wb)
        link_rec = tracking.link(d, hand, service, RULE)
        lce_id = link_rec['t0']['tracker_episode_id']
        state = wb.hold_review_state(lce_id)
        assert 'ai_shadow_hold_review_unified' in state['reasons'] and state['enabled'] is False
        with pytest.raises(workbench.WorkbenchError, match='统一由事件门驱动'):
            wb.hold_review_locked(lce_id, 'idem_12345678', authorization='Bearer tok')

        # Idle tick with 0 triggers must NOT spam HOLD_REVIEW_SKIPPED
        exported = service('export', dict(dossier_id=hand.parent.parent.name, hand_id=hand.name))
        exported['points'] = [{'metrics': {'asof_ms': now[0] - M, 'spot_usd': 100_000.0, 'short_distance_pct': 2.0, 'flow_ready': False}}]
        idle_res = tracking.evaluate_hold_management(d, hand, exported, llm_config={'api_key': 'k'})
        assert idle_res['action'] == 'NO_TRIGGER' and 'skip' not in idle_res

        # Cooldown-blocked B trigger emits HOLD_REVIEW_SKIPPED + skip summary
        ep_dir = hand.parent.parent / 'episodes' / tracking.accepted(hand)['origin']['dossier_episode_id']
        now[0] += 10 * M
        dossier.write(ep_dir / 'trigger_state.json', dossier.encode({'cooldown_until_ms': now[0] + 600_000}), replace=True)
        exported['points'] = [
            {'metrics': {'asof_ms': now[0] - 2 * M, 'spot_usd': 101_000.0, 'short_distance_pct': 0.98, 'flow_ready': False}},
            {'metrics': {'asof_ms': now[0] - M, 'spot_usd': 101_500.0, 'short_distance_pct': 0.48, 'flow_ready': False}},
        ]
        skip_res = tracking.evaluate_hold_management(d, hand, exported, llm_config={'api_key': 'k'})
        assert skip_res['action'] == 'QUEUED' and skip_res['skip']['reason'] == 'COOLDOWN'
        rec = tracking.audit_record(hand, now[0], {lce_id: 'active'}, detail=True)
        assert rec['hold_skip_summary']['total_skips'] >= 1
        assert rec['hold_skip_summary']['by_reason'].get('COOLDOWN', 0) >= 1
        assert rec['management_telemetry']['skip_count'] >= 1


def _raw_get(base, route, token=None):
    headers = {'Authorization': f'Bearer {token}'} if token else {}
    with urlopen(Request(base + route, headers=headers), timeout=8) as resp:
        return resp.status, dict(resp.headers.items()), resp.read()


def test_initial_request_is_already_frozen_and_parse_failure_is_recoverable(tmp_path, monkeypatch):
    clk = Clock(SLOT + M)
    d = dossier.Dossiers(tmp_path / 'runtime', clock=clk)
    monkeypatch.setattr(dossier, 'collect', lambda *_a, **_kw: fixture_source(clk))
    raw = '  模型实际回复，缺决策头。\n'
    usage = {'prompt_tokens': 20, 'completion_tokens': 11, 'total_tokens': 31, 'prompt_cache_hit_tokens': 8}

    def transport(*a, **kw):
        call = next(d.root.glob('dossiers/*/hands/*/api_calls/*/request.json'))
        assert dossier.read(call)['stage'] == 'initial'
        nested = tracking.auto_deal(d, plan(), tmp_path, lambda *_: {})
        assert nested == [{'status': 'AUTO_IN_FLIGHT'}]
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': raw}}], 'usage': usage}

    args = (d, plan(), tmp_path, lambda *_: {})
    config = {'api_key': 'synthetic', 'model': 'fixture-model', 'retries': 0, 'transport': transport}
    result = tracking.auto_deal(*args, llm_config=config)
    assert result[0]['status'] == 'PARSE_FAILED'
    hand = next(d.root.glob('dossiers/*/hands/hand_*'))
    assert tracking.audit_originals(hand)['response'] == raw
    attempt = next(hand.glob('responses/*/attempt.json'))
    assert dossier.read(attempt)['configuration']['usage']['total_tokens'] == 31
    assert tracking.export_model_input(hand)['request']['fidelity'] == 'EXACT'


def test_auto_deal_and_hold_review_persist_exact_request_json_and_export(tmp_path, monkeypatch):
    clk = Clock(SLOT + M)
    monkeypatch.setattr(dossier, 'collect', lambda *_a, **_kw: fixture_source(clk))

    # 1) auto_deal success saves EXACT request.json & request_sha256, including normalized endpoint
    d_ok = dossier.Dossiers(tmp_path / 'auto_ok', clock=clk)
    sent_initial = []
    reject_reply = (
        '## Reject\n\n'
        'Put路径：97000 距现价偏近。\n'
        'Call路径：103000 上方波动尚在。\n'
        '对比结论：两侧均不承接。\n'
        'Put反证：存在回踩穿破路径。\n'
        'Call反证：上方仍存冲高路径。\n'
        '<ANNOTATION_JSON>'
        + json.dumps({
            'put': 'Put路径：97000 距现价偏近。',
            'call': 'Call路径：103000 上方波动尚在。',
            'comparison': '两侧均不承接。',
            'put_counter': 'Put反证：存在回踩穿破路径。',
            'call_counter': 'Call反证：上方仍存冲高路径。',
            'depends_on_ard': False,
        }, ensure_ascii=False)
        + '</ANNOTATION_JSON>'
    )

    def ok_transport(api_key, model, body, timeout, endpoint=None):
        sent_initial.append((endpoint, json.loads(json.dumps(body))))
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': reject_reply}}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30}}

    res_ok = tracking.auto_deal(
        d_ok, plan(), tmp_path, lambda *_: {},
        llm_config={'api_key': 'secret-key-01', 'model': 'deepseek-reasoner',
                    'endpoint': 'https://example.invalid/v1', 'reasoning_effort': 'high',
                    'retries': 0, 'transport': ok_transport},
    )
    assert res_ok[0]['status'] == 'VALID' and res_ok[0]['decision'] == 'Reject'
    ok_hand = next((tmp_path / 'auto_ok' / 'dossiers').glob('Deribit_BTC_*/hands/hand_*'))
    ok_attempt = tracking.load(next((ok_hand / 'responses').glob('*/attempt.json')))
    ok_req_file = ok_hand / 'responses' / ok_attempt['attempt_id'] / 'request.json'
    assert ok_req_file.is_file()
    assert ok_attempt['request_sha256'] == dossier.sha(ok_req_file.read_bytes())
    saved_req = tracking.load(ok_req_file)
    assert saved_req['fidelity'] == 'EXACT'
    assert saved_req['stage'] == 'initial'
    assert saved_req['endpoint'] == 'https://example.invalid/v1/chat/completions'
    assert saved_req['messages'] == sent_initial[0][1]['messages']
    assert 'secret-key-01' not in ok_req_file.read_text(encoding='utf-8')

    exported_ok = tracking.export_model_input(ok_hand)
    assert exported_ok['fidelity'] == 'EXACT'
    assert exported_ok['fidelity_reason'] is None
    assert exported_ok['stage'] == 'initial'
    assert exported_ok['request'] == saved_req
    assert '来源保真度：EXACT\n' in exported_ok['full_model_input_md']
    assert exported_ok['single_box_text'].index('【SYSTEM】\n' + review.INITIAL_SYSTEM_PROMPT) == 0
    assert '【USER】\n候选合约对：' in exported_ok['single_box_text']

    # 2) auto_deal timeout/failure branch also persists EXACT outgoing request.json
    d_fail = dossier.Dossiers(tmp_path / 'auto_fail', clock=clk)
    res_fail = tracking.auto_deal(
        d_fail, plan(), tmp_path, lambda *_: {},
        llm_config={'api_key': 'secret-key-02', 'model': 'deepseek-chat',
                    'endpoint': 'https://fail.invalid/v1', 'retries': 0,
                    'transport': lambda *a, **k: (_ for _ in ()).throw(TimeoutError('read timed out'))},
    )
    assert res_fail[0]['status'] == 'TIMEOUT'
    fail_hand = next((tmp_path / 'auto_fail' / 'dossiers').glob('Deribit_BTC_*/hands/hand_*'))
    fail_attempt = tracking.load(next((fail_hand / 'responses').glob('*/attempt.json')))
    fail_req_file = fail_hand / 'responses' / fail_attempt['attempt_id'] / 'request.json'
    assert fail_req_file.is_file()
    assert fail_attempt['request_sha256'] == dossier.sha(fail_req_file.read_bytes())
    exported_fail = tracking.export_model_input(fail_hand)
    assert exported_fail['fidelity'] == 'EXACT'
    assert exported_fail['request']['endpoint'] == 'https://fail.invalid/v1/chat/completions'

    # 3) evaluate_hold_management persists EXACT reviews/<rev_id>/request.json atomically
    wb, d, hand, now, _ = sample(tmp_path, 'Accept / Call')
    with serving(wb) as base:
        svc = post(base, wb)
        receipt = tracking.link(d, hand, svc, RULE)
        ep_dir = hand.parent.parent / 'episodes' / receipt['origin']['dossier_episode_id']
        now[0] += 5 * M
        exported = svc('export', dict(dossier_id=hand.parent.parent.name, hand_id=hand.name))
        exported['points'] = [
            {'metrics': {'asof_ms': now[0] - 2 * M, 'spot_usd': 102_000.0, 'short_distance_pct': 0.98, 'flow_ready': False}},
            {'metrics': {'asof_ms': now[0] - M, 'spot_usd': 103_200.0, 'short_distance_pct': -0.19, 'flow_ready': False}},
        ]
        d.event(ep_dir, dossier.identity('chk', now[0] - M), 'CONDITION_TRIGGERED',
                dict(result={'rule': 'entry_not_returned'}), observed=now[0] - M, available=now[0])
        sent_hold = []

        def hold_transport(api_key, model, body, timeout, endpoint=None):
            sent_hold.append((endpoint, json.loads(json.dumps(body))))
            return {'choices': [{'finish_reason': 'stop', 'message': {
                'content': '## REVOKE\n\n已突破短腿。\n<HOLD_REVIEW_JSON>{"verdict":"REVOKE","affected_conditions":["M1"],"public_reply":"确认撤销","key_facts":["M1"]}</HOLD_REVIEW_JSON>'
            }}]}

        hold_res = tracking.evaluate_hold_management(
            d, hand, exported, service=lambda *_a: {'state': 'QUOTE_UNAVAILABLE', 'latest_point': None},
            llm_config={'api_key': 'hold-secret', 'model': 'deepseek-reasoner',
                        'endpoint': 'https://hold.invalid/v1', 'reasoning_effort': 'medium',
                        'transport': hold_transport},
        )
        assert hold_res['action'] == 'REVIEWED'
        rev_id = hold_res['review_id']
        hold_req_file = ep_dir / 'reviews' / rev_id / 'request.json'
        assert hold_req_file.is_file()
        saved_hold_req = tracking.load(hold_req_file)
        assert saved_hold_req['fidelity'] == 'EXACT'
        assert saved_hold_req['stage'] == 'hold_review'
        assert saved_hold_req['endpoint'] == 'https://hold.invalid/v1/chat/completions'
        assert saved_hold_req['messages'] == sent_hold[0][1]['messages']
        assert 'hold-secret' not in hold_req_file.read_text(encoding='utf-8')

        exported_hold = tracking.export_model_input(hand, review_id=rev_id)
        assert exported_hold['fidelity'] == 'EXACT'
        assert exported_hold['stage'] == 'hold_review'
        assert exported_hold['request'] == saved_hold_req
        assert rev_id in exported_hold['full_model_input_md']


def test_historical_scheduled_and_adhoc_export_reconstructed_without_leakage_or_llm(tmp_path, monkeypatch):
    def forbid_llm(*_a, **_kw):
        raise AssertionError('export_model_input must never invoke LLM')

    monkeypatch.setattr(review, 'call_chat_llm', forbid_llm)
    monkeypatch.setattr(review, 'decide_initial_hand', forbid_llm)

    # 1) Historical SCHEDULED hand without request.json (with decision, condition, outcome)
    clk = Clock(SLOT + M)
    d_sched = dossier.Dossiers(tmp_path / 'hist_sched', clock=clk)
    sched_hand = d_sched.tick(plan(), lambda *_a: fixture_source(clk))[0]
    src = tracking.load(sched_hand / 'evidence' / 'source.json')
    raw_accept = (
        '**Accept / Call**\n' + '\n'.join(EXCERPTS.values()) + '\n'
        + '\n'.join(RULE[k] for k in ('historical_facts', 'accepted_conditions', 'confirmation_excerpt'))
        + '\nCANARY_POST_DECISION_REPLY'
    ).encode('utf-8')
    req_t = clk()
    clk.ms += 100
    d_sched.respond(
        sched_hand, 'hist-sched-1', 'RECEIVED', raw_accept, EXCERPTS,
        model='hist-model-sched', requested=req_t, decided=clk(),
        configuration={'reasoning_effort': 'high', 'usage': {'total_tokens': 999}},
    )
    assert not (sched_hand / 'responses' / 'hist-sched-1' / 'request.json').exists()
    dossier_dir = sched_hand.parent.parent
    dossier.write(dossier_dir / 'outcomes' / 'outcome_hist' / 'outcome.json', dossier.encode({
        'classification': 'CANARY_OUTCOME_CLASS',
        'delivery': {'delivery_price_usd': 101_000.0},
        'verification_gap': None,
        'recorded_at_ms': clk() + 10_000,
        'hands': [{'hand_id': sched_hand.name, 'sides': {'call': {'delivery_payout_btc': 0.01}}}],
    }))

    exp_sched = tracking.export_model_input(sched_hand)
    assert exp_sched['fidelity'] == 'RECONSTRUCTED'
    assert exp_sched['fidelity_reason']
    assert exp_sched['stage'] == 'initial'
    req_s = exp_sched['request']
    assert req_s['messages'][0]['content'] == review.INITIAL_SYSTEM_PROMPT
    user_s = req_s['messages'][1]['content']
    assert '候选合约对：' in user_s
    assert (sched_hand / 'evidence' / 'input.md').read_bytes().decode('utf-8') in user_s
    assert req_s['metadata'] == {
        'dossier_id': dossier_dir.name,
        'hand_id': sched_hand.name,
        'sample': 'SCHEDULED',
        'expiry_ms': tracking.load(sched_hand / 'expected.json')['expiry_ms'],
        'frozen_at_ms': tracking.load(sched_hand / 'evidence' / 'identity.json')['hand_frozen_at_ms'],
        'input_sha256': tracking.load(sched_hand / 'evidence' / 'identity.json')['input_sha256'],
        'rules_sha256': tracking.load(sched_hand / 'evidence' / 'identity.json')['rules_sha256'],
        'generator': tracking.load(sched_hand / 'evidence' / 'identity.json')['generator'],
        'attempt_id': 'hist-sched-1',
        'channel': 'MANUAL_IMPORT',
    }
    for forbidden_key in ('judgment', 'annotation', 'verdict', 'raw_sha256', 'usage', 'decided_at_ms',
                          'condition', 'outcome', 'locked_series'):
        assert forbidden_key not in req_s
        assert forbidden_key not in req_s['metadata']
    for leak in ('CANARY_POST_DECISION_REPLY', 'CANARY_OUTCOME_CLASS', 'Accept / Call', 'side=call', 'HOLD', 'REVOKE', 'outcome', 'condition'):
        assert leak not in user_s
    serialized_sched = json.dumps(exp_sched, ensure_ascii=False)
    for canary in ('CANARY_POST_DECISION_REPLY', 'CANARY_OUTCOME_CLASS', 'side=call', 'delivery_payout_btc', 'locked_series'):
        assert canary not in serialized_sched

    # 2) Historical AD_HOC hand + legacy hold review without request.json
    wb, d_ah, ah_hand, now, expiry = sample(tmp_path / 'hist_adhoc', 'Accept / Call')
    assert not (ah_hand / 'responses' / 'fixture-reply' / 'request.json').exists()
    with serving(wb) as base:
        svc = post(base, wb)
        receipt = tracking.link(d_ah, ah_hand, svc, RULE)
        ep_dir = ah_hand.parent.parent / 'episodes' / receipt['origin']['dossier_episode_id']
        rev_id = 'rev_' + 'a1b2c3d4' * 3
        legacy_rev_input = '# 持仓增量复核牌面\n\n## 1. 原承诺\n- 锁定责任：CALL 短腿 102000\n'
        rev_bytes = legacy_rev_input.encode('utf-8')
        dossier.publish(ep_dir / 'reviews' / rev_id, {
            'input.md': rev_bytes,
            'response.raw': b'CANARY_HOLD_RAW_REPLY',
            'attempt.json': dossier.encode({
                'review_id': rev_id,
                'status': 'VALID',
                'verdict': 'REVOKE',
                'model': 'legacy-hold-model',
                'reasoning_effort': 'low',
                'requested_at_ms': now[0] + 60_000,
                'decided_at_ms': now[0] + 65_000,
                'usage': {'total_tokens': 321},
            }),
            'decision.json': dossier.encode({
                'review_id': rev_id,
                'verdict': 'REVOKE',
                'public_reply': 'CANARY_PRIVATE_HOLD_REPLY',
                'shadow_action': 'REVOKE_ALIGNED',
            }),
            'review_snapshot.json': dossier.encode({'review_id': rev_id, 'verdict': 'REVOKE'}),
        })

    exp_ah_init = tracking.export_model_input(ah_hand)
    assert exp_ah_init['fidelity'] == 'RECONSTRUCTED'
    assert exp_ah_init['stage'] == 'initial'
    assert exp_ah_init['request']['metadata']['sample'] == 'AD_HOC'
    assert 'side=call' not in json.dumps(exp_ah_init, ensure_ascii=False)
    assert 'Accept / Call' not in exp_ah_init['request']['messages'][1]['content']
    assert RULE['confirmation_excerpt'] not in json.dumps(exp_ah_init, ensure_ascii=False)
    assert EXCERPTS['comparison'] not in json.dumps(exp_ah_init, ensure_ascii=False)

    exp_ah_hold = tracking.export_model_input(ah_hand, review_id=rev_id)
    assert exp_ah_hold['fidelity'] == 'RECONSTRUCTED'
    assert exp_ah_hold['fidelity_reason']
    assert exp_ah_hold['stage'] == 'hold_review'
    assert exp_ah_hold['request']['model'] == 'legacy-hold-model'
    assert exp_ah_hold['request']['parameters']['reasoning_effort'] == 'low'
    assert exp_ah_hold['request']['packet_hash'] == dossier.sha(rev_bytes)
    assert exp_ah_hold['request']['messages'] == [
        {'role': 'system', 'content': review.HOLD_REVIEW_SYSTEM_PROMPT},
        {'role': 'user', 'content': legacy_rev_input},
    ]
    serialized_hold = json.dumps(exp_ah_hold, ensure_ascii=False)
    for canary in ('CANARY_HOLD_RAW_REPLY', 'CANARY_PRIVATE_HOLD_REPLY', 'REVOKE_ALIGNED', 'total_tokens', '321'):
        assert canary not in serialized_hold
    assert 'Accept' not in exp_ah_hold['request']['messages'][1]['content']
    assert 'REVOKE' not in exp_ah_hold['request']['messages'][1]['content']
    assert 'HOLD' not in exp_ah_hold['request']['messages'][1]['content']


def test_workbench_model_input_http_endpoints_and_guards(tmp_path, monkeypatch):
    wb, d, hand, now, _ = sample(tmp_path, 'Accept / Call')
    with serving(wb) as base:
        svc = post(base, wb)
        receipt = tracking.link(d, hand, svc, RULE)
        ep_dir = hand.parent.parent / 'episodes' / receipt['origin']['dossier_episode_id']
        rev_id = 'rev_' + '12345678' * 3
        hold_input = '# 持仓增量复核牌面\n\n## 1. 原承诺\n- 锁定责任：CALL 短腿 102000\n'
        hold_req = review.build_hold_review_request_payload(
            hold_input, model='m-http', reasoning_effort='high',
            endpoint='https://example.invalid/v1', packet_hash=dossier.sha(hold_input.encode('utf-8')),
            requested_at_ms=now[0] + 1000, fidelity='EXACT',
        )
        dossier.publish(ep_dir / 'reviews' / rev_id, {
            'input.md': hold_input.encode('utf-8'),
            'request.json': dossier.encode(hold_req),
            'response.raw': b'ok',
            'attempt.json': dossier.encode({'review_id': rev_id, 'status': 'VALID', 'model': 'm-http', 'requested_at_ms': now[0] + 1000}),
            'decision.json': dossier.encode({'review_id': rev_id, 'verdict': 'HOLD', 'public_reply': 'ok'}),
            'review_snapshot.json': dossier.encode({'review_id': rev_id, 'verdict': 'HOLD'}),
        })

        audit_base = tracking.AUDIT_PATH + '/' + case_ref(hand)
        # 1) Unauthorized (401)
        for suffix in ('/model-input', '/model-input.md', '/request.json', f'/model-input?review_id={rev_id}'):
            with pytest.raises(HTTPError) as exc:
                _raw_get(base, audit_base + suffix)
            assert exc.value.code == 401

        # 2) Initial hand 200 endpoints & headers
        expected_init = tracking.export_model_input(hand)
        st_j, hdr_j, raw_j = _raw_get(base, audit_base + '/model-input', 'secret')
        assert st_j == 200
        assert hdr_j.get('Cache-Control') == 'no-store'
        assert json.loads(raw_j) == expected_init

        st_md, hdr_md, raw_md = _raw_get(base, audit_base + '/model-input.md', 'secret')
        assert st_md == 200
        assert hdr_md.get('Content-Type', '').startswith('text/markdown')
        assert hdr_md.get('Cache-Control') == 'no-store'
        assert hdr_md.get('X-Content-Type-Options') == 'nosniff'
        assert hdr_md.get('Content-Disposition') == f'attachment; filename="{expected_init["filename_md"]}"'
        assert raw_md.decode('utf-8') == expected_init['full_model_input_md']

        st_rq, hdr_rq, raw_rq = _raw_get(base, audit_base + '/request.json', 'secret')
        assert st_rq == 200
        assert hdr_rq.get('Content-Type', '').startswith('application/json')
        assert hdr_rq.get('Cache-Control') == 'no-store'
        assert hdr_rq.get('X-Content-Type-Options') == 'nosniff'
        assert hdr_rq.get('Content-Disposition') == f'attachment; filename="{expected_init["filename_json"]}"'
        assert json.loads(raw_rq) == expected_init['request']

        # 3) Hold review ?review_id=... 200 endpoints
        expected_hold = tracking.export_model_input(hand, review_id=rev_id)
        st_hj, _, raw_hj = _raw_get(base, audit_base + f'/model-input?review_id={rev_id}', 'secret')
        assert st_hj == 200 and json.loads(raw_hj) == expected_hold

        st_hmd, hdr_hmd, raw_hmd = _raw_get(base, audit_base + f'/model-input.md?review_id={rev_id}', 'secret')
        assert st_hmd == 200
        assert hdr_hmd.get('Content-Disposition') == f'attachment; filename="{expected_hold["filename_md"]}"'
        assert raw_hmd.decode('utf-8') == expected_hold['full_model_input_md']

        st_hrq, hdr_hrq, raw_hrq = _raw_get(base, audit_base + f'/request.json?review_id={rev_id}', 'secret')
        assert st_hrq == 200
        assert hdr_hrq.get('Content-Disposition') == f'attachment; filename="{expected_hold["filename_json"]}"'
        assert json.loads(raw_hrq) == expected_hold['request']

        # 4) Bad / missing review_id -> 409 / 404
        with pytest.raises(HTTPError) as bad_rev:
            _raw_get(base, audit_base + '/model-input?review_id=not_a_review', 'secret')
        assert bad_rev.value.code == 409

        with pytest.raises(HTTPError) as missing_rev:
            _raw_get(base, audit_base + '/model-input?review_id=rev_' + '0' * 24, 'secret')
        assert missing_rev.value.code == 404

        # 5) Size limit -> 413
        monkeypatch.setattr(tracking, 'MAX_BYTES', 64)
        with pytest.raises(HTTPError) as too_big:
            _raw_get(base, audit_base + '/model-input.md', 'secret')
        assert too_big.value.code == 413


def test_player_audit_current_preserves_403_and_401_status_codes(tmp_path):
    wb, _, _, _, _ = sample(tmp_path, 'Accept / Call')
    wb.config.server_token = 'secret-token'
    wb.config.public_origin = 'https://audit.example.com'
    with serving(wb) as base:
        req_403 = Request(base + tracking.AUDIT_PATH + '/current', headers={'Host': 'evil.example.com', 'Authorization': 'Bearer secret-token'})
        with pytest.raises(HTTPError) as err_403:
            urlopen(req_403, timeout=8)
        assert err_403.value.code == 403
        assert json.loads(err_403.value.read().decode('utf-8'))['code'] == 'host_not_allowed'

        req_401 = Request(base + tracking.AUDIT_PATH + '/current', headers={'Host': 'audit.example.com'})
        with pytest.raises(HTTPError) as err_401:
            urlopen(req_401, timeout=8)
        assert err_401.value.code == 401
        assert json.loads(err_401.value.read().decode('utf-8'))['code'] == 'unauthorized'


def test_shadow_export_handles_large_payload_above_legacy_8mb_cap(tmp_path, monkeypatch):
    wb, d, hand, _, _ = sample(tmp_path, 'Accept / Call')
    assert tracking.MAX_BYTES >= 32_000_000
    with serving(wb) as base:
        svc = post(base, wb)
        receipt = tracking.link(d, hand, svc, RULE)
        ep_id = receipt['t0']['tracker_episode_id']
        ep = wb.locked_candidates.detail(ep_id)['episode']
        t0_point = wb.locked_candidates.read_point(ep['t0_baseline_point_id'])
        # Inflate exported point + series payload past the legacy 8_000_000 byte limit (~9.5 MB compact)
        filler = 'x' * 5_000_000
        large_point = {**t0_point, '_padding': filler}
        real_read = wb.locked_candidates.read_point
        monkeypatch.setattr(
            wb.locked_candidates, 'read_point',
            lambda pid: large_point if pid == ep['t0_baseline_point_id'] else real_read(pid),
        )
        big_points = [{'point_id': f'p{i}', 'pad': 'y' * 14_000} for i in range(250)]
        real_series = wb.locked_candidate_series
        monkeypatch.setattr(
            wb, 'locked_candidate_series',
            lambda eid, **_kw: {**real_series(eid), 'points': big_points},
        )
        exported = svc('export', {'dossier_id': hand.parent.parent.name, 'hand_id': hand.name})
        compact_size = len(
            (json.dumps(exported, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
        )
        assert compact_size > 8_000_000
        assert exported['point']['_padding'] == filler
        assert len(exported['points']) == 250

        # Still enforces MAX_BYTES cap with explicit 413 shadow_evidence_size_limit when exceeded
        monkeypatch.setattr(tracking, 'MAX_BYTES', compact_size - 1)
        with pytest.raises(tracking.Blocked, match='shadow_evidence_size_limit'):
            svc('export', {'dossier_id': hand.parent.parent.name, 'hand_id': hand.name})


def test_refresh_quote_preserves_capture_error_instead_of_contract_mismatch(tmp_path):
    wb, d, hand, _, _ = sample(tmp_path, 'Accept / Call')
    wb._capture_candidate = lambda *_a, **_kw: {
        'side': 'call',
        'status': 'error',
        'snapshot': None,
        'error': 'DERIBIT_HTTP_429: deribit shared cooldown active',
    }
    res = wb.shadow_tracking('refresh_quote', {'dossier_id': hand.parent.parent.name, 'hand_id': hand.name})
    assert res['state'] == 'QUOTE_UNAVAILABLE'
    assert 'DERIBIT_HTTP_429' in res['error']
    assert res['error'] != 'contract_mismatch'


def test_llm_timeout_precedence_initial_vs_hold_and_dynamic_deadline_cap(tmp_path, monkeypatch):
    for key in ('ASTRA_PLAYER_TIMEOUT_SEC', 'ASTRA_PLAYER_HOLD_TIMEOUT_SEC', 'LLM_TIMEOUT_SEC'):
        monkeypatch.delenv(key, raising=False)

    assert tracking._llm_settings({'model': 'deepseek-reasoner'})['initial_timeout_s'] == 600.0
    assert tracking._llm_settings({'model': 'deepseek-chat'})['initial_timeout_s'] == 300.0

    monkeypatch.setenv('LLM_TIMEOUT_SEC', '360')
    s_generic = tracking._llm_settings({'model': 'deepseek-reasoner'})
    assert s_generic['timeout_s'] == 360.0
    assert s_generic['timeout_explicit'] is False
    assert s_generic['initial_timeout_s'] == 600.0
    assert s_generic['hold_timeout_s'] == 360.0

    monkeypatch.setenv('ASTRA_PLAYER_TIMEOUT_SEC', '520')
    s_player = tracking._llm_settings({'model': 'deepseek-reasoner'})
    assert s_player['timeout_s'] == 520.0
    assert s_player['timeout_explicit'] is True
    assert s_player['initial_timeout_s'] == 520.0
    assert s_player['hold_timeout_s'] == 360.0  # hold bounded <= 360 unless explicit/hold env

    monkeypatch.setenv('ASTRA_PLAYER_HOLD_TIMEOUT_SEC', '410')
    assert tracking._llm_settings({'model': 'deepseek-reasoner'})['hold_timeout_s'] == 410.0

    # Dynamic reply_deadline cap in _deal_hand (with 15s headroom and 30s floor)
    clk = Clock(SLOT + M)
    d = dossier.Dossiers(tmp_path, clock=clk)
    monkeypatch.setattr(dossier, 'collect', lambda *_a, **_kw: fixture_source(clk))
    p = plan()
    p['reply_grace_ms'] = 120_000  # 120s window
    hand, = d.tick(p, slots=[SLOT])
    d.deal(hand, fixture_source(clk))
    clk.ms += 20_000  # 100s left -> capped to 85.0s
    seen_timeout = []

    def cap_transport(api_key, model, body, timeout_s, endpoint=None):
        del api_key, model, body, endpoint
        seen_timeout.append(timeout_s)
        return {'choices': [{'finish_reason': 'stop', 'message': {
            'content': '## Reject\n\nPut：风险高。\nCall：风险高。\n对比：不承接。\nPut反证：近。\nCall反证：近。\n<ANNOTATION_JSON>{"put":"Put：风险高。","call":"Call：风险高。","comparison":"对比：不承接。","put_counter":"Put反证：近。","call_counter":"Call反证：近。","depends_on_ard":false}</ANNOTATION_JSON>'
        }}]}

    res = tracking._deal_hand(d, hand, None, tracking._llm_settings({'model': 'deepseek-reasoner', 'transport': cap_transport}))
    assert res['status'] == 'VALID'
    assert seen_timeout == [85.0]


def test_hold_orphan_reply_recovery_before_stale_inflight_and_cas_protection(tmp_path):
    wb, d, hand, now, _ = sample(tmp_path, 'Accept / Call')
    with serving(wb) as base:
        svc = post(base, wb)
        receipt = tracking.link(d, hand, svc, RULE)
        ep_dir = hand.parent.parent / 'episodes' / receipt['origin']['dossier_episode_id']
        now[0] += 5 * M
        exported = svc('export', dict(dossier_id=hand.parent.parent.name, hand_id=hand.name))
        exported['points'] = [
            {'metrics': {'asof_ms': now[0] - 2 * M, 'spot_usd': 102_000.0, 'short_distance_pct': 0.98, 'flow_ready': False}},
            {'metrics': {'asof_ms': now[0] - M, 'spot_usd': 103_200.0, 'short_distance_pct': -0.19, 'flow_ready': False}},
        ]
        d.event(ep_dir, dossier.identity('chk', now[0] - M), 'CONDITION_TRIGGERED',
                dict(result={'rule': 'entry_not_returned'}), observed=now[0] - M, available=now[0])

        # 1) Orphaned review_calls/*/reply.json recovered BEFORE stale_inflight re-dispatch
        started_ms = now[0] - 30 * M
        rev_id = 'rev_' + '11223344' * 3
        batch = [{'category': 'A', 'code': 'CONDITION_TRIGGERED', 'signature': 'CONDITION_TRIGGERED',
                  'severity': 3, 'observed_at_ms': now[0] - M, 'summary_cn': 'triggered'}]
        dossier.write(ep_dir / 'trigger_state.json', dossier.encode({
            'revoked': False, 'inflight': True, 'inflight_started_at_ms': started_ms,
            'inflight_batch': batch, 'pending': [], 'dispatched_signatures': {},
        }), replace=True)
        call_dir = ep_dir / 'review_calls' / rev_id
        hold_req = review.build_hold_review_request_payload(
            'orphan input', model='m-orphan', requested_at_ms=started_ms + 5,
        )
        dossier.publish(call_dir, {
            'input.md': b'orphan input',
            'request.json': dossier.encode(hold_req),
            'reply.json': dossier.encode({
                'text': '## HOLD\n\n继续持有。\n<HOLD_REVIEW_JSON>{"verdict":"HOLD","affected_conditions":[],"public_reply":"继续观察","key_facts":["k"]}</HOLD_REVIEW_JSON>',
                'model': 'm-orphan',
                'usage': {'total_tokens': 42},
                'decided_at_ms': started_ms + 2_000,
            }),
        })
        res_rec = tracking.evaluate_hold_management(
            d, hand, exported,
            llm_config={'api_key': 'k', 'model': 'm-orphan',
                        'transport': lambda *a, **k: pytest.fail('orphan reply should prevent duplicate LLM call')},
        )
        assert res_rec.get('recovered_review_id') == rev_id
        assert (ep_dir / 'reviews' / rev_id / 'decision.json').is_file()
        assert dossier.read(ep_dir / 'trigger_state.json')['inflight'] is False
        assert not any(
            (dossier.read(p).get('body') or {}).get('error') == 'inflight_stale_recovered'
            for p in (ep_dir / 'events').glob('*.json')
        )

        # 2) RECOVERY_PENDING cleanup when decision.json already published with decided_at_ms >= started
        rev_id2 = 'rev_' + '55667788' * 3
        started2 = now[0]
        dossier.publish(ep_dir / 'reviews' / rev_id2, {
            'input.md': b'rp',
            'request.json': dossier.encode({'stage': 'hold_review', 'messages': []}),
            'response.raw': b'ok',
            'attempt.json': dossier.encode({'review_id': rev_id2, 'status': 'VALID'}),
            'decision.json': dossier.encode({
                'review_id': rev_id2, 'status': 'VALID', 'verdict': 'HOLD',
                'requested_at_ms': started2 + 5, 'decided_at_ms': started2 + 500,
                'public_reply': 'ok', 'trigger_categories': ['A'], 'triggers': batch,
            }),
            'review_snapshot.json': dossier.encode({'review_id': rev_id2, 'verdict': 'HOLD'}),
        })
        dossier.write(ep_dir / 'trigger_state.json', dossier.encode({
            'revoked': False, 'inflight': True, 'inflight_started_at_ms': started2,
            'inflight_batch': batch, 'pending': [],
            'dispatched_signatures': {'A:CONDITION_TRIGGERED': {'severity': 3, 'at_ms': started2}},
            'cooldown_until_ms': now[0] + 600_000,
        }), replace=True)
        now[0] += M
        tracking.evaluate_hold_management(
            d, hand, exported,
            llm_config={'api_key': 'k', 'transport': lambda *a, **k: pytest.fail('RECOVERY_PENDING must not recall')},
        )
        assert dossier.read(ep_dir / 'trigger_state.json')['inflight'] is False

        # 3) CAS ownership: stale worker finishing late must NOT overwrite newer inflight trigger_state.json
        dossier.write(ep_dir / 'trigger_state.json', dossier.encode({
            'revoked': False, 'inflight': False, 'pending': [], 'dispatched_signatures': {},
        }), replace=True)
        now[0] += 35 * M
        exported['points'].append({'metrics': {'asof_ms': now[0] - M, 'spot_usd': 103_400.0, 'short_distance_pct': -0.39, 'flow_ready': False}})
        d.event(ep_dir, dossier.identity('chk', now[0] - M), 'CONDITION_TRIGGERED',
                dict(result={'rule': 'entry_not_returned'}), observed=now[0] - M, available=now[0])
        newer_started = now[0] + 5_000

        def hijacked_transport(*_a, **_k):
            cur = dossier.read(ep_dir / 'trigger_state.json')
            dossier.write(ep_dir / 'trigger_state.json', dossier.encode({
                **cur, 'inflight': True, 'inflight_started_at_ms': newer_started,
            }), replace=True)
            raise TimeoutError('stale worker timed out after takeover')

        res_cas = tracking.evaluate_hold_management(
            d, hand, exported,
            llm_config={'api_key': 'k', 'model': 'm', 'transport': hijacked_transport},
        )
        assert res_cas['action'] == 'FAILED'
        final_st = dossier.read(ep_dir / 'trigger_state.json')
        assert final_st['inflight'] is True
        assert final_st['inflight_started_at_ms'] == newer_started

        # 4) CAS ownership when stale worker SUCCEEDS late after takeover -> publishes review but preserves newer inflight
        dossier.write(ep_dir / 'trigger_state.json', dossier.encode({
            'revoked': False, 'inflight': False, 'pending': [], 'dispatched_signatures': {},
        }), replace=True)
        now[0] += 35 * M
        exported['points'].append({'metrics': {'asof_ms': now[0] - M, 'spot_usd': 103_450.0, 'short_distance_pct': -0.44, 'flow_ready': False}})
        d.event(ep_dir, dossier.identity('chk', now[0] - M), 'CONDITION_TRIGGERED',
                dict(result={'rule': 'entry_not_returned'}), observed=now[0] - M, available=now[0])
        takeover_started = now[0] + 9_000

        def hijacked_success_transport(*_a, **_k):
            cur = dossier.read(ep_dir / 'trigger_state.json')
            dossier.write(ep_dir / 'trigger_state.json', dossier.encode({
                **cur, 'inflight': True, 'inflight_started_at_ms': takeover_started,
            }), replace=True)
            return {'choices': [{'finish_reason': 'stop', 'message': {
                'content': '## HOLD\n\n继续观察。\n<HOLD_REVIEW_JSON>{"verdict":"HOLD","affected_conditions":[],"public_reply":"继续观察","key_facts":["k"]}</HOLD_REVIEW_JSON>'
            }}]}

        res_cas_ok = tracking.evaluate_hold_management(
            d, hand, exported,
            llm_config={'api_key': 'k', 'model': 'm', 'transport': hijacked_success_transport},
        )
        assert res_cas_ok['action'] == 'REVIEWED'
        assert dossier.read(ep_dir / 'trigger_state.json')['inflight_started_at_ms'] == takeover_started
        now[0] += M
        res_cas_next = tracking.evaluate_hold_management(
            d, hand, exported,
            llm_config={'api_key': 'k', 'model': 'm', 'transport': lambda *a, **k: pytest.fail('takeover inflight must stay active')},
        )
        assert res_cas_next['action'] == 'NO_TRIGGER'
        assert dossier.read(ep_dir / 'trigger_state.json')['inflight'] is True
        assert dossier.read(ep_dir / 'trigger_state.json')['inflight_started_at_ms'] == takeover_started

        # 5) Malformed orphaned reply.json marks PARSE_FAILED and suppresses stale_inflight re-dispatch
        wb2, d2, hand2, now2, _ = sample(tmp_path / 'mal_orphan', 'Accept / Call')
        with serving(wb2) as base2:
            svc2 = post(base2, wb2)
            rec2 = tracking.link(d2, hand2, svc2, RULE)
            ep2 = hand2.parent.parent / 'episodes' / rec2['origin']['dossier_episode_id']
            now2[0] += 5 * M
            exp2 = svc2('export', dict(dossier_id=hand2.parent.parent.name, hand_id=hand2.name))
            exp2['points'] = [
                {'metrics': {'asof_ms': now2[0] - 2 * M, 'spot_usd': 101_000.0, 'short_distance_pct': 0.98, 'flow_ready': False}},
                {'metrics': {'asof_ms': now2[0] - M, 'spot_usd': 101_400.0, 'short_distance_pct': 0.58, 'flow_ready': False}},
            ]
            mal_rev_id = 'rev_' + '99aabbcc' * 3
            mal_started = now2[0]
            now2[0] = mal_started + 30 * M
            b_batch = [{'category': 'B', 'code': 'BUFFER_EROSION', 'signature': 'BUFFER_EROSION',
                        'severity': 1, 'observed_at_ms': mal_started - M, 'summary_cn': 'erosion'}]
            dossier.write(ep2 / 'trigger_state.json', dossier.encode({
                'revoked': False, 'inflight': True, 'inflight_started_at_ms': mal_started,
                'inflight_batch': b_batch, 'pending': [], 'dispatched_signatures': {},
            }), replace=True)
            mal_call_dir = ep2 / 'review_calls' / mal_rev_id
            dossier.publish(mal_call_dir, {
                'input.md': b'orphan malformed input',
                'request.json': dossier.encode(review.build_hold_review_request_payload(
                    'orphan malformed input', model='m-orphan', requested_at_ms=mal_started + 5,
                )),
                'reply.json': dossier.encode({
                    'text': 'truncated without tag',
                    'model': 'm-orphan',
                    'decided_at_ms': mal_started + 1_000,
                }),
            })
            res_mal = tracking.evaluate_hold_management(
                d2, hand2, exp2,
                llm_config={'api_key': 'k', 'model': 'm-orphan',
                            'transport': lambda *a, **k: pytest.fail('malformed orphan must enter backoff without immediate re-call')},
            )
            assert res_mal['action'] == 'QUEUED'
            assert (mal_call_dir / 'parse_failed.json').is_file()
            mal_st = dossier.read(ep2 / 'trigger_state.json')
            assert mal_st['inflight'] is False and mal_st['backoff_until_ms'] is not None
            assert any(
                (dossier.read(p).get('body') or {}).get('status') == 'PARSE_FAILED'
                for p in (ep2 / 'events').glob('*.json')
            )
            assert not any(
                (dossier.read(p).get('body') or {}).get('error') == 'inflight_stale_recovered'
                for p in (ep2 / 'events').glob('*.json')
            )


def test_pre_request_existing_episode_and_sync_open_request_isolation(tmp_path, monkeypatch):
    wb, d, hand, now, _ = sample(tmp_path, 'Accept / Call')
    proof = tracking.accepted(hand)
    ep_dir = hand.parent.parent / 'episodes' / proof['origin']['dossier_episode_id']
    ep_dir.mkdir(parents=True, exist_ok=True)
    res_block = tracking.link(d, hand, lambda *a, **k: pytest.fail('must not call service'), RULE)
    assert res_block == {'state': 'LOCK_BLOCKED', 'reason': 'existing_unlinked_dossier_episode'}
    assert dossier.read(hand / 'tracking' / 'blocked.json')['reason'] == 'existing_unlinked_dossier_episode'
    assert dossier.read(hand / 'tracking' / 'blocked.json')['terminal'] is True
    assert tracking._link_blocked(hand / 'tracking')['reason'] == 'existing_unlinked_dossier_episode'


def test_audit_index_returns_more_than_40_hands(tmp_path):
    clk = Clock(SLOT + 45 * dossier.DAY + M)
    d = dossier.Dossiers(tmp_path, clock=clk)
    p = plan()
    for i in range(45):
        slot_i = SLOT + i * dossier.DAY
        d.expect(slot_i + p['expiry_offset_ms'], 'SCHEDULED', plan=p, slot=slot_i)
    idx = tracking.audit_index(tmp_path, clk(), {})
    assert len(idx['hands']) == 45


def test_hold_orphan_ignores_superseded_dispatch_and_recovers_non_auto_revoke(tmp_path):
    # 1. Stale superseded orphan (requested_at_ms=T1 < T2) arriving late (decided_at_ms>=T2) must NOT clear T2 inflight
    wb, d, hand, now, _ = sample(tmp_path / 'stale_orphan', 'Accept / Call')
    with serving(wb) as base:
        svc = post(base, wb)
        rec = tracking.link(d, hand, svc, RULE)
        ep = hand.parent.parent / 'episodes' / rec['origin']['dossier_episode_id']
        now[0] += 5 * M
        exported = svc('export', dict(dossier_id=hand.parent.parent.name, hand_id=hand.name))
        exported['points'] = [
            {'metrics': {'asof_ms': now[0] - 2 * M, 'spot_usd': 100_100.0, 'short_distance_pct': 2.8, 'flow_ready': False}},
            {'metrics': {'asof_ms': now[0] - M, 'spot_usd': 100_200.0, 'short_distance_pct': 2.7, 'flow_ready': False}},
        ]
        t1 = now[0]
        t2 = t1 + 10 * M
        now[0] = t2 + M  # within stale_inflight_ms of t2
        stale_rev_id = 'rev_' + '11aa22bb' * 3
        dossier.publish(ep / 'review_calls' / stale_rev_id, {
            'input.md': b'stale input',
            'request.json': dossier.encode(review.build_hold_review_request_payload(
                'stale input', model='m-stale', requested_at_ms=t1,
            )),
            'reply.json': dossier.encode({
                'text': '## HOLD\n\n继续持有。\n<HOLD_REVIEW_JSON>{"verdict":"HOLD","affected_conditions":[],"public_reply":"ok","key_facts":["k"]}</HOLD_REVIEW_JSON>',
                'model': 'm-stale',
                'decided_at_ms': t2 + 1_000,
            }),
        })
        dossier.write(ep / 'trigger_state.json', dossier.encode({
            'revoked': False,
            'inflight': True,
            'inflight_started_at_ms': t2,
            'inflight_batch': [],
            'pending': [],
            'dispatched_signatures': {},
        }), replace=True)
        res_keep = tracking.evaluate_hold_management(
            d, hand, exported,
            llm_config={'api_key': 'k', 'model': 'm-stale', 'transport': lambda *a, **k: pytest.fail('must remain coalesced')},
        )
        assert res_keep['action'] == 'NO_TRIGGER'
        assert not (ep / 'reviews' / stale_rev_id).exists()
        assert dossier.read(ep / 'trigger_state.json')['inflight'] is True
        assert dossier.read(ep / 'trigger_state.json')['inflight_started_at_ms'] == t2

    # 2. Orphan verified REVOKE when _can_auto_shadow_exit=False (execution_allowed=True) returns ALREADY_REVOKED
    wb2, d2, hand2, now2, _ = sample(tmp_path / 'exec_orphan', 'Accept / Call')
    with serving(wb2) as base2:
        svc2 = post(base2, wb2)
        rec2 = tracking.link(d2, hand2, svc2, RULE)
        link_path2 = hand2 / 'tracking' / 'link.json'
        dossier.write(link_path2, dossier.encode({**rec2, 'execution_allowed': True}), replace=True)
        ep2 = hand2.parent.parent / 'episodes' / rec2['origin']['dossier_episode_id']
        now2[0] += 5 * M
        exported2 = svc2('export', dict(dossier_id=hand2.parent.parent.name, hand_id=hand2.name))
        exported2['points'] = [
            {'metrics': {'asof_ms': now2[0] - 2 * M, 'spot_usd': 103_200.0, 'short_distance_pct': -0.19, 'flow_ready': False}},
            {'metrics': {'asof_ms': now2[0] - M, 'spot_usd': 103_400.0, 'short_distance_pct': -0.39, 'flow_ready': False}},
        ]
        d2.event(ep2, dossier.identity('chk', now2[0] - M), 'CONDITION_TRIGGERED',
                 dict(result={'rule': 'entry_not_returned'}), observed=now2[0] - M, available=now2[0])
        started_ex = now2[0]
        now2[0] = started_ex + 30 * M
        ex_rev_id = 'rev_' + '33cc44dd' * 3
        dossier.write(ep2 / 'trigger_state.json', dossier.encode({
            'revoked': False,
            'inflight': True,
            'inflight_started_at_ms': started_ex,
            'inflight_batch': [{'category': 'A', 'code': 'CONDITION_TRIGGERED', 'signature': f'CONDITION_TRIGGERED:{started_ex - M}', 'severity': 3, 'observed_at_ms': started_ex - M}],
            'pending': [],
            'dispatched_signatures': {},
        }), replace=True)
        dossier.publish(ep2 / 'review_calls' / ex_rev_id, {
            'input.md': b'revoke input',
            'request.json': dossier.encode(review.build_hold_review_request_payload(
                'revoke input', model='m-exec', requested_at_ms=started_ex + 5,
            )),
            'reply.json': dossier.encode({
                'text': '## REVOKE\n\n边界已触发，撤销。\n<HOLD_REVIEW_JSON>{"verdict":"REVOKE","program_triggered":true,"affected_conditions":["M1"],"public_reply":"撤销","key_facts":["trigger"]}</HOLD_REVIEW_JSON>',
                'model': 'm-exec',
                'decided_at_ms': started_ex + 1_000,
            }),
        })
        res_ex = tracking.evaluate_hold_management(
            d2, hand2, exported2,
            llm_config={'api_key': 'k', 'model': 'm-exec', 'transport': lambda *a, **k: pytest.fail('orphan REVOKE must recover cleanly')},
        )
        assert res_ex == {
            'action': 'ALREADY_REVOKED',
            'recovered': True,
            'review_id': ex_rev_id,
            'exit_intent_only': True,
        }
        assert not (hand2 / 'tracking' / 'shadow_exit.json').exists()

    # 3. Orphan verified REVOKE when real_execution_context is active preserves live_execution_active=True and does NOT stop native tracker
    wb3, d3, hand3, now3, _ = sample(tmp_path / 'live_orphan', 'Accept / Call')
    stop_calls: list[dict] = []
    with serving(wb3) as base3:
        svc3 = post(base3, wb3)
        rec3 = tracking.link(d3, hand3, svc3, RULE)
        ep3 = hand3.parent.parent / 'episodes' / rec3['origin']['dossier_episode_id']
        now3[0] += 5 * M
        exported3 = svc3('export', dict(dossier_id=hand3.parent.parent.name, hand_id=hand3.name))
        exported3['points'] = [
            {'metrics': {'asof_ms': now3[0] - 2 * M, 'spot_usd': 103_200.0, 'short_distance_pct': -0.19, 'flow_ready': False}},
            {'metrics': {'asof_ms': now3[0] - M, 'spot_usd': 103_400.0, 'short_distance_pct': -0.39, 'flow_ready': False}},
        ]
        d3.event(ep3, dossier.identity('chk', now3[0] - M), 'CONDITION_TRIGGERED',
                 dict(result={'rule': 'entry_not_returned'}), observed=now3[0] - M, available=now3[0])
        started_lv = now3[0]
        now3[0] = started_lv + 30 * M
        lv_rev_id = 'rev_' + '55ee66ff' * 3
        dossier.write(ep3 / 'trigger_state.json', dossier.encode({
            'revoked': False,
            'inflight': True,
            'inflight_started_at_ms': started_lv,
            'inflight_batch': [{'category': 'A', 'code': 'CONDITION_TRIGGERED', 'signature': f'CONDITION_TRIGGERED:{started_lv - M}', 'severity': 3, 'observed_at_ms': started_lv - M}],
            'pending': [],
            'dispatched_signatures': {},
        }), replace=True)
        dossier.publish(ep3 / 'review_calls' / lv_rev_id, {
            'input.md': b'revoke input',
            'request.json': dossier.encode(review.build_hold_review_request_payload(
                'revoke input', model='m-live', requested_at_ms=started_lv + 5,
            )),
            'reply.json': dossier.encode({
                'text': '## REVOKE\n\n触发实盘撤退。\n<HOLD_REVIEW_JSON>{"verdict":"REVOKE","program_triggered":true,"affected_conditions":["M1"],"public_reply":"撤销","key_facts":["trigger"]}</HOLD_REVIEW_JSON>',
                'model': 'm-live',
                'decided_at_ms': started_lv + 1_000,
            }),
        })

        def _spy_svc(action: str, body: dict) -> dict:
            if action == 'stop':
                stop_calls.append(body)
            return svc3(action, body)

        res_lv = tracking.evaluate_hold_management(
            d3, hand3, exported3, service=_spy_svc,
            llm_config={'api_key': 'k', 'model': 'm-live', 'transport': lambda *a, **k: pytest.fail('orphan REVOKE must recover cleanly')},
            real_execution_context={'mode': 'TESTNET', 'hand_id': hand3.name, 'episode_id': 'ep_live_1'},
        )
        assert res_lv == {
            'action': 'ALREADY_REVOKED',
            'recovered': True,
            'review_id': lv_rev_id,
            'shadow_exited': True,
            'live_execution_active': True,
        }
        assert stop_calls == []
        sx_lv = dossier.read(hand3 / 'tracking' / 'shadow_exit.json')
        assert sx_lv['tracker_stopped'] is False

