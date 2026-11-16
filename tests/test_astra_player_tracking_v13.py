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
        assert first['state'] == 'LOCK_BLOCKED' and first['reason'] == reason
        now[0] += M
        wb.refresh(now_ms=now[0], record_daily=False)
        assert tracking.link(d, hand, lambda *_: pytest.fail('retry would resample'), RULE) == first
        assert service('lock', dict(dossier_id=hand.parent.parent.name, hand_id=hand.name))['state'] == 'LINK_UNVERIFIED'
        assert dossier.read(hand / 'decision.json')['judgment']['action'] == 'Accept'
        assert [r['lock_actor'] for r in wb.locked_candidates.list()] == (['USER'] if has_manual else [])


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
        price = 104_000
        now[0] = expiry + 3 * M
        wb.delivery_price_fetch = lambda _: dict(delivery_price_usd=price, source='Deribit fixture')
        wb._settle_expired_locks(now[0])
        assert tracking.sync(d, hand, service)['settlement'] == 'settled'
        seal = tracking.load(hand / 'tracking' / 'terminal.json')
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

        # Manual active lock on the Put contract still blocks even a formal SCHEDULED hand.
        put_ep = next(ep for ep in wb.locked_candidates.list() if ep['lock_actor'] == 'USER')
        put_point = wb.locked_candidates.read_point(put_ep['t0_baseline_point_id'])
        put_sched_origin = dict(base_origin, plan_id='daily-deribit-btc-r2@1', hand_id='hand_put_sched_01',
                                dossier_episode_id='episode_put_sched_01', request_key='shadow_' + 'c' * 24)
        with pytest.raises(tracking.store.LockedCandidateError) as manual_exc:
            wb.locked_candidates.lock(put_point, request_key=put_sched_origin['request_key'],
                                      now_ms=now[0], shadow_origin=put_sched_origin)
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
        monkeypatch.setattr(archive.Deribit, 'index_path', lambda _self: points)

        synced = tracking.sync(d, hand, service, check_due=True)
        assert [c['observed_at_ms'] for c in synced['checks']] == [first_tick, first_tick + 5 * M]
        assert [c['kind'] for c in synced['checks']] == ['CONDITION_NOT_TRIGGERED', 'CONDITION_TRIGGERED']

        # Re-running at a later clock skips already checked ticks and records no duplicates.
        now[0] += M
        wb.refresh(now_ms=now[0], record_daily=False)
        resynced = tracking.sync(d, hand, service, check_due=True)
        assert resynced['checks'] == []
        assert len(list((ep_dir / 'events').glob('check_*.json'))) == 2


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

    # Attempt 2: TimeoutError -> TIMEOUT
    clk.ms += 1_000
    r2 = tracking.auto_deal(
        d_run, plan(), tmp_path, lambda *_: {},
        llm_config={'api_key': 'k', 'model': 'm-test', 'reasoning_effort': 'high', 'retries': 0, 'transport': timeout_transport},
    )
    assert r2[0]['status'] == 'TIMEOUT'
    assert len(tracking.audit_record(hand, clk(), {}, detail=True)['attempts']) == 2

    # Attempt 3 allowed (< 3), hits DailyBudgetExceeded -> notes DAILY_BUDGET_EXHAUSTED, skips attempt 4 immediately
    clk.ms += 1_000
    zero_budget = signal_llm_review.DailyHttpBudget(tmp_path / 'zero_budget.json', limit=0)
    r3 = tracking.auto_deal(
        d_run, plan(), tmp_path, lambda *_: {},
        llm_config={'api_key': 'k', 'model': 'm-test', 'retries': 0, 'budget': zero_budget, 'transport': timeout_transport},
    )
    assert r3[0]['status'] == 'DAILY_BUDGET_EXHAUSTED'
    assert calls[0] == 2  # budget checked before transport

    clk.ms += 1_000
    r4 = tracking.auto_deal(
        d_run, plan(), tmp_path, lambda *_: {},
        llm_config={'api_key': 'k', 'model': 'm-test', 'retries': 0, 'transport': timeout_transport},
    )
    assert r4[0]['status'] == 'DAILY_BUDGET_EXHAUSTED'
    assert calls[0] == 2

    # Fresh hand with 0 attempts hitting DailyBudgetExceeded surfaces DAILY_BUDGET_EXHAUSTED on audit_record
    d_bud = dossier.Dossiers(tmp_path / 'budget_zero', clock=clk)
    r_bud = tracking.auto_deal(
        d_bud, plan(), tmp_path, lambda *_: {},
        llm_config={'api_key': 'k', 'model': 'm-test', 'retries': 0, 'budget': zero_budget, 'transport': timeout_transport},
    )
    assert r_bud[0]['status'] == 'DAILY_BUDGET_EXHAUSTED'
    bud_hand = next((tmp_path / 'budget_zero' / 'dossiers').glob('Deribit_BTC_*/hands/hand_*'))
    assert tracking.audit_record(bud_hand, clk(), {})['state'] == 'DAILY_BUDGET_EXHAUSTED'

    # Hand with 3 failed attempts stops at AUTO_ATTEMPTED on 4th call
    d_max = dossier.Dossiers(tmp_path / 'max_attempts', clock=clk)
    for _ in range(3):
        clk.ms += 1_000
        tracking.auto_deal(
            d_max, plan(), tmp_path, lambda *_: {},
            llm_config={'api_key': 'k', 'model': 'm-test', 'retries': 0, 'transport': timeout_transport},
        )
    before_calls = calls[0]
    r_stop = tracking.auto_deal(
        d_max, plan(), tmp_path, lambda *_: {},
        llm_config={'api_key': 'k', 'model': 'm-test', 'retries': 0, 'transport': timeout_transport},
    )
    assert r_stop[0]['status'] == 'AUTO_ATTEMPTED' and r_stop[0]['last_status'] == 'TIMEOUT'
    assert calls[0] == before_calls


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
        assert seen_revoke_pts == [exported['latest_point']]
        st_saved = dossier.read(ep_dir / 'trigger_state.json')
        assert st_saved['revoked'] is True and st_saved['inflight'] is False

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
    assert receipt['state'] == 'LOCK_BLOCKED'
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


