"""Join the daily dossier to the workbench's existing shadow observations."""

from pathlib import Path
import argparse
import json
import os
import re
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

try:
    from . import astra_player_dossier_v1 as dossier
    from . import astra_underwriting_v20 as underwriting
    from . import astra_locked_candidate_store_v1 as store
except ImportError:
    import astra_player_dossier_v1 as dossier
    import astra_underwriting_v20 as underwriting
    import astra_locked_candidate_store_v1 as store


AUDIT_PATH = '/api/workbench/player-audit'
SERVICE_PATH = '/api/workbench/shadow-tracking'
# The first request has a two-minute handoff allowance, within the original hand
# deadline. The quote itself must pass the collector's existing book-age limits.
HANDOFF_MS = 120_000
MAX_BYTES = 8_000_000
CONTRACT_FIELDS = dossier.condition.CONTRACT_FIELDS
DECISION_FIELDS = ('attempt_id', 'raw_sha256', 'parser', 'judgment', 'configuration_id',
                   'decided_at_ms', 'recorded_at_ms', 'channel', 'model')
DOSSIER_ID = r'Deribit_BTC_\d{8}T\d{6}Z'
HAND_ID = r'hand_[0-9a-f]{24}'
TRACKER_ID = r'lce_[0-9a-f]{24}'


class Blocked(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def bounded(path, limit=MAX_BYTES):
    # Dossier/storage paths are owned by the service user. Do not follow links
    # from archive attachments or accept client-specified filesystem paths.
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise Blocked('evidence_path_invalid')
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise Blocked('evidence_too_large')
    return data


def load(path):
    return json.loads(bounded(path))


def optional(path):
    try:
        return load(path)
    except FileNotFoundError:
        return None


def hand_path(root, dossier_id, hand_id):
    if not re.fullmatch(DOSSIER_ID, str(dossier_id)) or not re.fullmatch(HAND_ID, str(hand_id)):
        raise Blocked('invalid_hand_identity')
    return Path(root) / 'dossiers' / dossier_id / 'hands' / hand_id


def selected(hand):
    """Verify original bytes, first valid choice and case association without writing."""
    saved = optional(hand / 'decision.json')
    if saved is None:
        return None, None, None
    attempt_id = dossier.key(saved['attempt_id'])
    original = hand / 'responses' / attempt_id
    attempts = [load(p) for p in original.parent.glob('*/attempt.json') if not p.parent.name.startswith('.')]
    first = min((x for x in attempts if x['status'] == 'VALID'), key=lambda x: x['ordinal'], default=None)
    raw = bounded(original / 'response.raw')
    if (not first or first['hand_id'] != hand.name or first['raw_sha256'] != dossier.sha(raw)
            or saved != {**{k: first[k] for k in DECISION_FIELDS},
                         'response': f'responses/{attempt_id}/response.raw'}
            or dossier.parse(raw, first['annotation']) != saved['judgment']):
        raise Blocked('reply_identity_mismatch')
    return saved, first, raw


def evidence(hand):
    bundle = hand / 'evidence'
    identity = load(bundle / 'identity.json')
    raw, source_bytes = bounded(bundle / 'input.md'), bounded(bundle / 'source.json')
    if (identity['research'] != dossier.RESEARCH or identity['input_sha256'] != dossier.sha(raw)
            or identity['source_sha256'] != dossier.sha(source_bytes)):
        raise Blocked('hand_identity_mismatch')
    return identity, raw, json.loads(source_bytes)


def contract_fields(contract):
    return {key: contract.get(key) for key in CONTRACT_FIELDS}


def accepted(hand):
    expected = load(hand / 'expected.json')
    decision, attempt, raw = selected(hand)
    if not decision or decision['judgment']['action'] != 'Accept':
        raise Blocked('no_valid_accept')
    if expected['sample'] not in {'SCHEDULED', 'STARTUP_AD_HOC', 'AD_HOC'}:
        raise Blocked('non_forward_hand')
    meta, _, source = evidence(hand)
    model = attempt['model']
    frozen, requested, decided, recorded = (meta['hand_frozen_at_ms'], attempt['requested_at_ms'],
                                             attempt['decided_at_ms'], attempt['recorded_at_ms'])
    deadline = dossier.Dossiers.reply_deadline(expected, meta)
    if (not dossier.model_identity(model)
            or any(type(t) is not int for t in (frozen, requested, decided, recorded, deadline))
            or not frozen <= requested <= decided <= recorded < deadline
            or not isinstance(attempt['channel'], str) or not attempt['channel'].strip()
            or meta['input_sha256'] != attempt['input_sha256']):
        raise Blocked('missing_or_invalid_reply_provenance')
    expiry = expected['expiry_ms']
    side = decision['judgment']['side']
    case, card = next((case, card) for case, card in zip(source['cases'], source['cards']) if card['side'] == side)
    contract = dossier.player.bind(case, card, side)
    if (expected['hand_id'] != hand.name or expected['dossier_id'] != hand.parent.parent.name
            or contract['expiry_ms'] != expiry):
        raise Blocked('hand_contract_mismatch')
    quote = dossier.player.values(case, 'L2.quote')
    exact = contract_fields(contract)
    decision_sha = dossier.sha(dossier.encode(decision))
    plan = (expected.get('plan') or {}).get('plan_id')
    request_key = dossier.identity('shadow', plan, expected['scheduled_at_ms'], hand.name,
                                   decision_sha, exact)
    origin = dict(dossier_id=expected['dossier_id'], hand_id=hand.name,
                  dossier_episode_id=dossier.identity('episode', hand.name, decision['attempt_id'], contract),
                  decision_id=decision['attempt_id'], decision_sha256=decision_sha,
                  raw_sha256=decision['raw_sha256'], input_sha256=meta['input_sha256'],
                  configuration_id=decision['configuration_id'], model=model, research=dossier.RESEARCH,
                  plan_id=plan, scheduled_at_ms=expected['scheduled_at_ms'],
                  hand_frozen_at_ms=frozen, counter_excerpt=decision['judgment'][side + '_counter'][:140],
                  decided_at_ms=decided, decision_recorded_at_ms=recorded,
                  decision_asof_ms=card['read_at_ms'], decision_index_usd=card['index_usd'],
                  decision_quote=quote_reference(quote), request_key=request_key)
    return dict(origin=origin, contract=contract, source=source, decision=decision,
                raw=raw.decode('utf-8'), deadline_ms=min(deadline, decided + HANDOFF_MS))


def quote_reference(quote):
    if not isinstance(quote, dict):
        return {}
    return {k: quote[k] for k in ('kind', 'credit_btc', 'valuation_ts_ms', 'exchange_times_ms',
                                  'received_ts_ms', 'leg_received_ts_ms', 'source_book_hash') if k in quote}


def confirm_quote(snapshot, now):
    market = snapshot['market']
    quote = market.get('quote') or {}
    asof = market.get('valuation_ts_ms')
    exchanges = quote.get('exchange_times_ms') or {}
    arrivals = quote.get('leg_received_ts_ms') or {}
    if (quote.get('kind') != 'sequential_leg_book_estimate' or not quote.get('source_book_hash')
            or quote.get('gap_reasons') or quote.get('quote_gap_reasons')
            or quote.get('executable_or_filled') is not False or type(asof) is not int
            or quote.get('valuation_ts_ms') != asof or not 0 <= now - asof <= underwriting.MAX_BOOK_AGE_MS
            or type(quote.get('received_ts_ms')) is not int or not 0 < quote['received_ts_ms'] <= asof
            or set(exchanges) != {'short', 'long'} or set(arrivals) != {'short', 'long'}
            or any(type(exchanges[k]) is not int or type(arrivals[k]) is not int
                   or not 0 < exchanges[k] <= arrivals[k] <= quote['received_ts_ms']
                   or now - exchanges[k] > underwriting.MAX_BOOK_AGE_MS for k in ('short', 'long'))
            or abs(exchanges['short'] - exchanges['long']) > underwriting.MAX_BOOK_SKEW_MS):
        raise Blocked('tracking_quote_stale_or_unsynced')
    return quote_reference(quote)


def condition_plan(proof, plan):
    required = ('historical_facts', 'accepted_conditions', 'confirmation_excerpt')
    if (not isinstance(plan, dict) or any(not isinstance(plan.get(k), str) or not plan[k].strip()
            or plan[k] not in proof['raw'] for k in required)
            or type(plan.get('check_interval_ms')) is not int
            or not 0 < plan['check_interval_ms'] <= dossier.DAY
            or plan['check_interval_ms'] % dossier.MINUTE
            or plan.get('invalidation') not in dossier.condition.RULES):
        raise Blocked('condition_plan_missing_or_unverifiable')
    depends = proof['decision']['judgment']['depends_on_ard']
    keys = ('ard_responsibility_meaning', 'ard_review_on')
    if depends and any(not isinstance(plan.get(k), str) or not plan[k].strip() or plan[k] not in proof['raw'] for k in keys):
        raise Blocked('ard_condition_excerpt_missing')
    outside = plan.get('require_outside_at_t0', False)
    if not isinstance(outside, bool):
        raise Blocked('condition_plan_missing_or_unverifiable')
    return {**{k: plan[k] for k in (*required, 'check_interval_ms', 'invalidation', *(keys if depends else ()))},
            'require_outside_at_t0': outside}


def condition_at_t0(proof, plan, snapshot, locked_at):
    plan = condition_plan(proof, plan)
    expiry = proof['contract']['expiry_ms']
    first = (locked_at // dossier.MINUTE + 1) * dossier.MINUTE
    invalidation = plan['invalidation']
    index = snapshot['market']['reference_price_usd']
    if plan['require_outside_at_t0'] and dossier.player.responsibility_zone(index, proof['contract']) != '在责任区外':
        raise Blocked('t0_outside_condition_no_longer_holds')
    frozen = dict(contract=contract_fields(proof['contract']), entry_at_ms=locked_at,
                  entry_index_usd=index,
                  historical_facts=plan['historical_facts'], accepted_conditions=plan['accepted_conditions'],
                  invalidation=invalidation, invalidation_meaning=dossier.condition.RULES[invalidation],
                  check_clock=dict(first_at_ms=first, interval_ms=plan['check_interval_ms']),
                  sampling=dossier.condition.SAMPLING, confirmation_excerpt=plan['confirmation_excerpt'])
    if proof['decision']['judgment']['depends_on_ard']:
        s = proof['source']
        facts = dossier.player.ard.analyze(s['history'], s['index_path'], expiry_ms=expiry,
            cutoff_ms=min(c['read_at_ms'] for c in s['cards']), band_snapshot=s.get('band_snapshot'))
        frozen['ard_basis'] = dict(facts=facts, responsibility_meaning=plan['ard_responsibility_meaning'],
                                   review_on=plan['ard_review_on'])
    try:
        dossier.condition.validate(frozen)
    except (ValueError, TypeError, KeyError) as exc:
        raise Blocked('condition_uncheckable_at_t0') from exc
    return frozen


def request_proof(hand):
    proof = accepted(hand)
    request = load(hand / 'tracking' / 'request.json')
    proof['origin']['condition_sha256'] = dossier.sha(dossier.encode(condition_plan(proof, request['condition'])))
    if (request['origin'] != proof['origin']
            or request['deadline_ms'] != proof['deadline_ms']
            or not proof['origin']['decision_recorded_at_ms'] <= request['requested_at_ms'] < proof['deadline_ms']):
        raise Blocked('lock_request_identity_mismatch')
    return proof, request


def tracker_t0(origin, episode, point):
    checked = store.validate_point(point)
    snap = checked['snapshot']
    if (episode.get('ai_shadow') != origin or episode['t0_baseline_point_id'] != checked['point_id']
            or episode['baseline_snapshot_id'] != checked['snapshot_id']
            or episode['candidate_id'] != checked['candidate_id']
            or contract_fields(episode['contract']) != contract_fields(checked['contract'])):
        raise Blocked('tracker_t0_identity_mismatch')
    return dict(tracker_episode_id=episode['episode_id'], candidate_id=checked['candidate_id'],
                snapshot_id=checked['snapshot_id'], point_id=checked['point_id'],
                tracking_asof_ms=checked['decision_asof_ms'], tracking_point_recorded_at_ms=checked['recorded_at_ms'],
                locked_at_ms=episode['created_at_ms'], index_usd=snap['market']['reference_price_usd'],
                quote=quote_reference(snap['market'].get('quote')), economy=snap.get('economics'),
                offset_from_decision_ms=episode['created_at_ms'] - origin['decided_at_ms'],
                index_change_usd=round(snap['market']['reference_price_usd'] - origin['decision_index_usd'], 8))


def terminal_trajectory(hand):
    root = hand / 'tracking'
    version = load(root / 'terminal.json')['export_id']
    folder = root / 'exports' / dossier.key(version)
    meta = load(folder / 'manifest.json')
    data = bounded(folder / 'trajectory.json')
    if (meta['trajectory_sha256'] != dossier.sha(data)
            or meta['t0_sha256'] != dossier.sha(bounded(root / 't0' / 'point.json'))):
        raise Blocked('terminal_evidence_hash_mismatch')
    return meta, data


def ensure_terminal_archive(root, episode):
    """Native cleanup may proceed only after the dossier's sealed full export."""
    try:
        origin = episode['ai_shadow']
        hand = hand_path(root, origin['dossier_id'], origin['hand_id'])
        meta, _ = terminal_trajectory(hand)
        return (meta['tracker_episode_id'] == episode['episode_id']
                and meta['t0_point_id'] == episode['t0_baseline_point_id']
                and meta['last_point_id'] == episode['latest_point_id']
                and meta['status'] == episode['status']
                and meta['stopped_at_ms'] == episode['stopped_at_ms']
                and meta['settlement_state'] in {'settled', 'delivery_price_unavailable'}
                and meta['complete_native_series'] is True)
    except (OSError, ValueError, KeyError, TypeError):
        return False


class LocalOnly(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        # Never forward the shadow-service credential even to a configured proxy.
        return None


def local_service(base, token):
    url = urlsplit(base)
    if (url.scheme != 'http' or url.hostname not in {'127.0.0.1', '::1'} or url.username or url.password
            or url.path not in {'', '/'} or url.query or url.fragment or not token):
        raise Blocked('shadow_service_requires_loopback_credential')
    opener = build_opener(ProxyHandler({}), LocalOnly)

    def post(action, body):
        request = Request(base.rstrip('/') + SERVICE_PATH + '/' + action, data=dossier.encode(body),
                          headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        try:
            with opener.open(request, timeout=12) as response:
                encoded = response.read(MAX_BYTES + 1)
        except HTTPError as exc:
            # 5xx/timeouts are ambiguous after a mutation. Only a confirmed
            # gate or validation failure is safe to archive as definitive.
            if not 400 <= exc.code < 500 or exc.code in {408, 429}:
                raise
            with exc:
                error = json.loads(exc.read(4096))
            raise Blocked(error.get('code', 'shadow_service_denied')) from exc
        if len(encoded) > MAX_BYTES:
            raise OSError('shadow response size limit')
        return json.loads(encoded)

    return post


def save_link(d, hand, proof, request, result):
    origin = proof['origin']
    if result.get('execution_allowed') is not False:
        raise Blocked('tracking_authority_mismatch')
    point, ep = result['point'], result['episode']
    t0 = tracker_t0(origin, ep, point)
    snapshot = point['candidate']['snapshot']
    if (t0 != result['t0'] or t0['locked_at_ms'] > d.clock()
            or not request['requested_at_ms'] <= t0['locked_at_ms'] < proof['deadline_ms']
            or contract_fields(snapshot['contract']) != contract_fields(proof['contract'])):
        raise Blocked('tracking_receipt_mismatch')
    confirm_quote(snapshot, t0['locked_at_ms'])
    frozen = condition_at_t0(proof, request['condition'], snapshot, t0['locked_at_ms'])
    root = hand / 'tracking'
    t0_files = {'point.json': dossier.encode(point), 'identity.json': dossier.encode(t0)}
    if (root / 't0').exists():
        if any(bounded(root / 't0' / name) != data for name, data in t0_files.items()):
            raise Blocked('tracking_t0_archive_mismatch')
    else:
        dossier.publish(root / 't0', t0_files)
    episode = d.episode(hand, frozen, tracking=(origin, ep, point))
    if episode.name != origin['dossier_episode_id']:
        raise Blocked('dossier_episode_identity_mismatch')
    link = dict(state=result['state'], origin=origin, t0=t0, recorded_at_ms=d.clock(),
                t0_sha256=dossier.sha(t0_files['point.json']), execution_allowed=False)
    dossier.write(root / 'link.json', dossier.encode(link))
    return link


def link(d, hand, service, plan=None):
    root = hand / 'tracking'
    saved = optional(root / 'link.json') or optional(root / 'blocked.json')
    if saved:
        return saved
    request = optional(root / 'request.json')
    if request is None:
        try:
            proof = accepted(hand)
            issued = d.clock()
            if issued >= proof['deadline_ms']:
                raise Blocked('shadow_handoff_expired')
            frozen_plan = condition_plan(proof, plan)
            proof['origin']['condition_sha256'] = dossier.sha(dossier.encode(frozen_plan))
            if (hand.parent.parent / 'episodes' / proof['origin']['dossier_episode_id']).exists():
                raise Blocked('existing_unlinked_dossier_episode')
        except Blocked as exc:
            if exc.code in {'no_valid_accept', 'non_forward_hand'}:
                return dict(state='NO_TRACKING_REQUEST', reason=exc.code)
            # Failure before any transport attempt remains visible in the dossier.
            d.note(hand, 'LOCK_BLOCKED', exc.code)
            return dict(state='LOCK_BLOCKED', reason=exc.code)
        request = dict(state='SHADOW_LOCK_REQUESTED', accepted_at_ms=proof['origin']['decided_at_ms'],
                       requested_at_ms=issued, deadline_ms=proof['deadline_ms'], origin=proof['origin'],
                       condition=frozen_plan)
        dossier.write(root / 'request.json', dossier.encode(request))
        d.note(hand, 'AI_ACCEPTED', proof['origin']['decision_id'])
        action = 'lock'
    else:
        proof, request = request_proof(hand)
        action = 'recover'
    try:
        result = service(action, dict(dossier_id=hand.parent.parent.name, hand_id=hand.name))
        reason = 'native_commit_unconfirmed'
    except Blocked as exc:
        if action == 'lock':
            blocked = dict(state='LOCK_BLOCKED', reason=exc.code, recorded_at_ms=d.clock())
            dossier.write(root / 'blocked.json', dossier.encode(blocked))
            d.note(hand, 'LOCK_BLOCKED', exc.code)
            return blocked
        reason = exc.code
    except (OSError, ValueError, KeyError, TypeError) as exc:
        reason = type(exc).__name__
    else:
        if result['state'] in {'SHADOW_LOCK_CREATED', 'ALREADY_LINKED'}:
            try:
                return save_link(d, hand, proof, request, result)
            except (OSError, ValueError, KeyError, TypeError, store.LockedCandidateError) as exc:
                reason = exc.code if isinstance(exc, Blocked) else type(exc).__name__
    d.note(hand, 'LINK_UNVERIFIED', reason)
    return dict(state='LINK_UNVERIFIED', reason=reason)


def _run_due_checks(d, episode, at, *, index_path=None, check_due=False):
    """Run due grid condition checks idempotently without duplicating existing ticks."""
    if index_path is None and not check_due:
        return None, None
    frozen = optional(episode / 'condition.json')
    if frozen is None:
        return [], None
    if index_path is None:
        try:
            points = dossier.archive.Deribit().index_path()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return [], type(exc).__name__
        expiry = frozen['contract']['expiry_ms']
        index_path = dict(
            index_name='btc_usd',
            expiry_ms=expiry,
            points=[p for p in points if isinstance(p, (list, tuple)) and len(p) == 2 and p[0] <= min(at, expiry)],
        )
    clock_cfg = frozen['check_clock']
    limit = min(at, frozen['contract']['expiry_ms'] - 1)
    done_ticks = {
        ev.get('observed_at_ms')
        for p in (episode / 'events').glob('check_*.json')
        if (ev := optional(p)) and ev.get('kind') in {'CONDITION_TRIGGERED', 'CONDITION_NOT_TRIGGERED'}
    }
    checked_events = []
    for tick in range(clock_cfg['first_at_ms'], limit + 1, clock_cfg['interval_ms']):
        if tick in done_ticks:
            continue
        ev = d.check(episode, index_path, tick, available=at)
        checked_events.append(dict(event_id=ev['event_id'], kind=ev['kind'], observed_at_ms=ev['observed_at_ms']))
    return checked_events, None


def sync(d, hand, service, *, index_path=None, check_due=False):
    """Copy the native tracker evidence; optionally run due fixed-clock checks."""
    root = hand / 'tracking'
    link_record = load(root / 'link.json')
    if (terminal := optional(root / 'terminal.json')) is not None:
        return terminal
    exported = service('export', dict(dossier_id=hand.parent.parent.name, hand_id=hand.name))
    ep, point = exported['episode'], exported['point']
    if tracker_t0(link_record['origin'], ep, point) != link_record['t0'] or exported['t0'] != link_record['t0']:
        raise Blocked('tracking_export_identity_mismatch')
    point_bytes = dossier.encode(point)
    if dossier.sha(point_bytes) != link_record['t0_sha256'] or point_bytes != bounded(root / 't0' / 'point.json'):
        raise Blocked('t0_hash_mismatch')
    settlement = exported.get('settlement')
    completed = (not exported['thinned'] and not exported['unreadable_points']
                 and len(exported['points']) == ep['point_count'])
    body = dict(episode=ep, points=exported['points'], gaps=exported['gaps'],
                thinned=exported['thinned'], unreadable_points=exported['unreadable_points'],
                valuation_basis='MARK_TO_MARKET_REFERENCE', terminal_valuation=settlement,
                terminal_basis='HOLD_TO_EXPIRY_SHADOW' if (settlement or {}).get('status') == 'settled' else None,
                actual_execution=None)
    archive_id = dossier.identity('track', body)
    at = d.clock()
    archived = ep['status'] in {'stopped', 'expired'}
    settlement_state = (settlement or {}).get('status', 'pending')
    if archived:
        data = dossier.encode(body)
        seal = dict(export_id=archive_id, tracker_episode_id=ep['episode_id'],
                    t0_point_id=ep['t0_baseline_point_id'], t0_sha256=link_record['t0_sha256'],
                    last_point_id=ep['latest_point_id'], trajectory_sha256=dossier.sha(data),
                    status=ep['status'], stopped_at_ms=ep['stopped_at_ms'], recorded_at_ms=at,
                    complete_native_series=completed, settlement_state=settlement_state)
        folder = root / 'exports' / archive_id
        if not folder.exists():
            dossier.publish(folder, {'manifest.json': dossier.encode(seal), 'trajectory.json': data})
    last = exported['points'][-1] if exported['points'] else None
    observations = dict(tracker_episode_id=ep['episode_id'], tracker_status=ep['status'],
                        t0=link_record['t0']['point_id'], last_point=last, gaps=ep['gap_count'],
                        gap_storage_truncated=ep['gap_storage_truncated'], last_gap=ep['last_gap'],
                        complete_native_series=completed, settlement=settlement,
                        export_id=archive_id if archived else None, meaning='TRACKER_OBSERVATION')
    episode = hand.parent.parent / 'episodes' / link_record['origin']['dossier_episode_id']
    event_id = dossier.identity('observation', observations)
    previous = optional(episode / 'events' / (event_id + '.json'))
    event = previous or d.event(episode, event_id, 'OBSERVATION', observations,
        observed=max((t for t in (ep['latest_recorded_at_ms'], ep['stopped_at_ms'],
                                  (ep['last_gap'] or {}).get('recorded_at_ms'),
                                  (settlement or {}).get('settled_at_ms')) if t is not None), default=None),
        available=at)
    checks, check_error = _run_due_checks(d, episode, at, index_path=index_path, check_due=check_due)
    if archived and completed and settlement_state in {'settled', 'delivery_price_unavailable'}:
        dossier.write(root / 'terminal.json', dossier.encode(dict(export_id=archive_id)))
    out = dict(state=ep['status'], event_id=event['event_id'], full_series=completed, settlement=settlement_state)
    if checks is not None:
        out['checks'] = checks
    if check_error is not None:
        out['check_error'] = check_error
    return out


def audit_record(hand, now, tracker_status, *, detail=False):
    exp = load(hand / 'expected.json')
    if exp['hand_id'] != hand.name or exp['dossier_id'] != hand.parent.parent.name:
        raise Blocked('invalid_hand_identity')
    root = hand / 'tracking'
    decision, attempt, _ = selected(hand) if detail else (optional(hand / 'decision.json'), None, None)
    frozen = optional(hand / 'evidence' / 'identity.json')
    req = optional(root / 'request.json')
    receipt = optional(root / 'link.json')
    blocked = optional(root / 'blocked.json')
    notes = sorted((load(p) for p in (hand / 'issuance').glob('*.json')), key=lambda p: p['recorded_at_ms'])
    deadline = dossier.Dossiers.reply_deadline(exp, frozen) if frozen else None
    attempts = sorted((load(p) for p in (hand / 'responses').glob('*/attempt.json') if not p.parent.name.startswith('.')),
                      key=lambda p: p['ordinal'])
    state = (decision['judgment']['action'] if decision else attempts[-1]['status'] if attempts else
             'AWAITING_RESPONSE' if frozen and deadline is not None and now < deadline else 'NO_RESPONSE' if frozen
             else notes[-1]['state'] if notes else 'EXPECTED')
    tracker = receipt['t0']['tracker_episode_id'] if receipt else None
    terminal = optional(root / 'terminal.json') if receipt else None
    terminal_meta = optional(root / 'exports' / dossier.key(terminal['export_id']) / 'manifest.json') if terminal else None
    if terminal_meta and detail:
        try:
            terminal_meta, _ = terminal_trajectory(hand)
        except (OSError, ValueError, KeyError, TypeError):
            terminal_meta = None
    lock_state = ('TRACKING' if tracker in tracker_status else 'ARCHIVED' if terminal_meta else 'TRACKER_UNAVAILABLE'
                  if tracker else 'LOCK_BLOCKED' if blocked or any(n['state'] == 'LOCK_BLOCKED' for n in notes)
                  else 'LINK_UNVERIFIED' if any(n['state'] == 'LINK_UNVERIFIED' for n in notes)
                  else req['state'] if req else None)
    result = dict(dossier_id=exp['dossier_id'], hand_id=hand.name, sample=exp['sample'], state=state,
                  expiry_ms=exp['expiry_ms'], frozen_at_ms=(frozen or {}).get('hand_frozen_at_ms'),
                  scheduled_at_ms=exp['scheduled_at_ms'], side=(decision or {}).get('judgment', {}).get('side'),
                  model=(decision or {}).get('model') or (attempts[-1].get('model') if attempts else None),
                  tracker_episode_id=tracker if tracker in tracker_status else None,
                  tracker_status=tracker_status.get(tracker),
                  tracking_state=lock_state)
    if not detail:
        return result
    if frozen:
        meta, raw, source = evidence(hand)
        board = raw.decode('utf-8').split('## 本局牌面', 1)[1].split('## 读完后另写', 1)[0].strip()
        proof = []
        for case, card in zip(source['cases'], source['cards']):
            side = card['side']
            proof.append(dict(side=side, contract=contract_fields(dossier.player.bind(case, card, side)),
                              case_id=case['case_id'], read_at_ms=card['read_at_ms'], index_usd=card['index_usd'],
                              quote=quote_reference(dossier.player.values(case, 'L2.quote'))))
        path = source['index_path']
        result.update(identity=meta, board=board, contracts=proof,
                      ard_line=next((line for line in board.splitlines() if line.startswith('**锚相对动态ARD')), None),
                      source=dict(name=source.get('source'), index_name=path.get('index_name'),
                                  fetched_at_ms=path.get('fetched_at_ms'), points=len(path.get('points', [])),
                                  pin_records=len(source['history'])))
    ep = hand.parent.parent / 'episodes' / receipt['origin']['dossier_episode_id'] if receipt else None
    if ep is None and decision and frozen:
        ep = next((p.parent for p in (hand.parent.parent / 'episodes').glob('episode_*/episode.json')
                   if load(p).get('hand_id') == hand.name), None)
    events = (load(p) for p in (ep / 'events').glob('*.json')) if ep else ()
    recent = sorted(events, key=lambda e: (e['recorded_at_ms'], e['event_id']))[-8:]
    result.update(decision=decision, requested_at_ms=(attempt or {}).get('requested_at_ms'),
                  reasoning_effort=((attempt or {}).get('configuration') or {}).get('reasoning_effort'),
                  lock_requested_at_ms=(req or {}).get('requested_at_ms'), receipt=receipt and receipt['t0'],
                  condition=optional(ep / 'condition.json') if ep else None,
                  events=[dict(kind=e['kind'], observed_at_ms=e['observed_at_ms'], recorded_at_ms=e['recorded_at_ms'])
                          for e in recent], archive=terminal_meta,
                  attempts=[{k: a.get(k) for k in ('status', 'model', 'requested_at_ms', 'decided_at_ms',
                                                   'recorded_at_ms', 'error')} for a in attempts[-8:]],
                  notes=notes[-8:], lock_reason=(blocked or {}).get('reason'))
    outcomes = [(path, load(path)) for path in (hand.parent.parent / 'outcomes').glob('outcome_*/outcome.json')]
    if outcomes:
        out = max(outcomes, key=lambda pair: pair[1]['recorded_at_ms'])[1]
        result['outcome'] = {k: out.get(k) for k in ('classification', 'delivery', 'verification_gap', 'recorded_at_ms')}
        grade = next((h for h in out['hands'] if h['hand_id'] == hand.name), None)
        if grade:
            result['outcome']['liability_btc'] = {
                side: row['delivery_payout_btc'] for side, row in grade['sides'].items()}
            for extra in ('t0_net_credit_after_entry_fee_btc', 'hold_to_expiry_shadow_net_btc'):
                if extra in grade:
                    result['outcome'][extra] = grade[extra]
    return result


def audit_index(root, now, active_ids):
    folders = sorted((Path(root) / 'dossiers').glob('Deribit_BTC_*'), reverse=True)[:40]
    cards = []
    for folder in folders:
        hands = sorted((folder / 'hands').glob('hand_*'),
                       key=lambda path: (path / 'expected.json').stat().st_mtime if (path / 'expected.json').is_file() else 0,
                       reverse=True)
        for hand in hands[:40 - len(cards)]:
            try:
                cards.append(audit_record(hand, now, active_ids))
            except (ValueError, OSError, KeyError, TypeError):
                cards.append(dict(dossier_id=folder.name, hand_id=hand.name, state='EVIDENCE_UNAVAILABLE'))
        if len(cards) == 40:
            break
    cards.sort(key=lambda c: (c.get('frozen_at_ms') or c.get('scheduled_at_ms') or 0, c['hand_id']), reverse=True)
    return {'hands': cards[:40]}


def audit_originals(hand):
    _, input_bytes, _ = evidence(hand)
    decision, attempt, response = selected(hand)
    if attempt is None:
        candidates = [load(p) for p in (hand / 'responses').glob('*/attempt.json') if not p.parent.name.startswith('.')]
        attempt = max((a for a in candidates if a.get('raw_sha256')), key=lambda a: a['ordinal'], default=None)
        if attempt is not None:
            response = bounded(hand / 'responses' / dossier.key(attempt['attempt_id']) / 'response.raw')
            if dossier.sha(response) != attempt['raw_sha256']:
                raise Blocked('reply_identity_mismatch')
    return dict(input=input_bytes.decode('utf-8'), response=response.decode('utf-8') if response is not None else None,
                selected=decision is not None, response_status=(attempt or {}).get('status'))


def prewarm(plan, archive, service, *, clock=dossier.now_ms, public=dossier.public, dry_run=False):
    now = clock()
    slot = now - now % dossier.DAY + 14 * 60 * dossier.MINUTE
    if not dry_run:
        if not slot - 10 * dossier.MINUTE <= now < slot or slot not in dossier.schedule(plan, slot):
            raise Blocked('outside_plan_prewarm_window')
    else:
        if now >= slot:
            slot += dossier.DAY
        if slot < plan['effective_at_ms']:
            slot = plan['effective_at_ms']
        dossier.schedule(plan, slot)
    expiry = slot + plan['expiry_offset_ms']
    report = dict(state='PREWARM_DRY_RUN' if dry_run else 'PREWARM', plan_id=plan['plan_id'],
                  checked_at_ms=now, scheduled_at_ms=slot,
                  target_expiry_ms=expiry, response_channel='MANUAL_IMPORT',
                  archive_readable=(archive / 'cases').is_dir() and (archive / 'levels').is_dir())
    if dry_run:
        report['dry_run'] = True
    try:
        instruments = public('get_instruments', currency='BTC', kind='option', expired='false')
        report['native_expiry_present'] = any(i.get('expiration_timestamp') == expiry
            and i.get('settlement_currency') == 'BTC' and i.get('is_active') for i in instruments)
    except (OSError, ValueError, KeyError, TypeError):
        report['native_expiry_present'] = None
    try:
        report['tracking'] = service('readiness', {})
    except (OSError, ValueError, KeyError, TypeError):
        report['tracking'] = {'state': 'UNAVAILABLE'}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8787', help='private loopback workbench listener')
    sub = parser.add_subparsers(dest='command', required=True)
    warm = sub.add_parser('prewarm')
    warm.add_argument('--plan', type=Path, required=True)
    warm.add_argument('--archive', type=Path, required=True)
    warm.add_argument('--dry-run', action='store_true')
    for name in ('link', 'sync'):
        command = sub.add_parser(name)
        command.add_argument('--root', type=Path, required=True)
        command.add_argument('--dossier', required=True)
        command.add_argument('--hand', required=True)
        if name == 'link':
            command.add_argument('--condition', type=Path)
        else:
            command.add_argument('--check-due', action='store_true')
    follow = sub.add_parser('sync-open')
    follow.add_argument('--root', type=Path, required=True)
    follow.add_argument('--check-due', action='store_true')
    args = parser.parse_args()
    try:
        try:
            service = local_service(args.url, os.environ.get('ASTRA_AI_SHADOW_LOCK_TOKEN'))
        except Blocked as error:
            def service(_action, _body, blocked=error):
                raise blocked
        if args.command == 'prewarm':
            out = prewarm(dossier.read(args.plan), args.archive, service, dry_run=args.dry_run)
        else:
            d = dossier.Dossiers(args.root)
            with dossier.writer(d.root):
                if args.command == 'sync-open':
                    out = []
                    for link_file in (d.root / 'dossiers').glob('*/hands/hand_*/tracking/link.json'):
                        if (link_file.parent / 'terminal.json').exists():
                            continue
                        hand = link_file.parent.parent
                        try:
                            out.append(sync(d, hand, service, check_due=args.check_due))
                        except (OSError, ValueError, KeyError, TypeError) as exc:
                            d.note(hand, 'TRACKER_EXPORT_UNAVAILABLE', getattr(exc, 'code', type(exc).__name__))
                            out.append(dict(state='TRACKER_EXPORT_UNAVAILABLE', hand_id=hand.name))
                        d.summary(load(hand / 'expected.json')['expiry_ms'])
                else:
                    hand = hand_path(args.root, args.dossier, args.hand)
                    out = (link(d, hand, service, dossier.read(args.condition) if args.condition else None)
                           if args.command == 'link' else sync(d, hand, service, check_due=args.check_due))
                    d.summary(load(hand / 'expected.json')['expiry_ms'])
        print(dossier.encode(out).decode('utf-8'), end='')
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f'{getattr(exc, "code", type(exc).__name__)}\n')
    if isinstance(out, dict) and out.get('state') in {'LOCK_BLOCKED', 'LINK_UNVERIFIED', 'NO_TRACKING_REQUEST'}:
        parser.exit(2)


if __name__ == '__main__':
    main()
