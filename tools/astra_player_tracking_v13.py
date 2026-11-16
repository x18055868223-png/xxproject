"""Join the daily dossier to the workbench's existing shadow observations."""

from pathlib import Path
import argparse
import inspect
import json
import os
import re
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

try:
    from . import astra_locked_candidate_compare_v1 as locked_compare
    from . import astra_locked_candidate_store_v1 as store
    from . import astra_player_dossier_v1 as dossier
    from . import astra_player_review_v1 as review
    from . import astra_underwriting_v20 as underwriting
    from . import signal_llm_review
except ImportError:
    import astra_locked_candidate_compare_v1 as locked_compare
    import astra_locked_candidate_store_v1 as store
    import astra_player_dossier_v1 as dossier
    import astra_player_review_v1 as review
    import astra_underwriting_v20 as underwriting
    import signal_llm_review


AUDIT_PATH = '/api/workbench/player-audit'
SERVICE_PATH = '/api/workbench/shadow-tracking'
# The first request has a 15-minute handoff allowance, within the original hand
# deadline. The quote itself must pass the collector's existing book-age limits.
HANDOFF_MS = 15 * 60_000
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
    if (not isinstance(plan, dict) or any(not dossier.excerpt_in_text(plan.get(k), proof['raw']) for k in required)
            or type(plan.get('check_interval_ms')) is not int
            or not 0 < plan['check_interval_ms'] <= dossier.DAY
            or plan['check_interval_ms'] % dossier.MINUTE
            or plan.get('invalidation') not in dossier.condition.RULES):
        raise Blocked('condition_plan_missing_or_unverifiable')
    depends = proof['decision']['judgment']['depends_on_ard']
    keys = ('ard_responsibility_meaning', 'ard_review_on')
    if depends and any(not dossier.excerpt_in_text(plan.get(k), proof['raw']) for k in keys):
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


def _merge_index_points(archive_root, expiry_ms, at, *, exported_points=None, public_fn=None):
    by_ts = {}
    if archive_root is not None:
        root = Path(archive_root)
        for day_ms in (expiry_ms - dossier.DAY, expiry_ms):
            p = root / 'paths' / f'{dossier.utc(day_ms).date().isoformat()}.json.gz'
            if p.is_file():
                try:
                    import gzip
                    data = json.loads(gzip.decompress(p.read_bytes()))
                    for t, v in data.get('points') or ():
                        if isinstance(t, int) and t <= min(at, expiry_ms) and dossier.player.number(v) and v > 0:
                            by_ts[t] = float(v)
                except Exception:
                    pass
    for row in exported_points or ():
        if not isinstance(row, dict):
            continue
        m = row.get('metrics') if isinstance(row.get('metrics'), dict) else row
        ts = row.get('recorded_at_ms') or m.get('asof_ms')
        spot = m.get('spot_usd')
        if isinstance(ts, int) and dossier.player.number(spot) and spot > 0:
            minute_ts = ts - (ts % dossier.MINUTE)
            if minute_ts <= min(at, expiry_ms):
                by_ts.setdefault(minute_ts, float(spot))
    try:
        points = public_fn() if public_fn is not None else dossier.archive.Deribit().index_path()
        for item in points or ():
            if isinstance(item, (list, tuple)) and len(item) == 2:
                t, v = item
                if isinstance(t, int) and t <= min(at, expiry_ms) and dossier.player.number(v) and v > 0:
                    by_ts[t] = float(v)
    except Exception:
        pass
    return dict(index_name='btc_usd', expiry_ms=expiry_ms, points=[[t, by_ts[t]] for t in sorted(by_ts)])


def _episode_ard_state(frozen, episode, archive_root, index_path, tick):
    """Build current ARD facts + strict same-measure comparison at a check_clock tick."""
    if not frozen.get('ard_basis') or archive_root is None:
        return None
    expiry = frozen['contract']['expiry_ms']
    struct_path = Path(archive_root) / 'structure' / f'{dossier.utc(expiry).date().isoformat()}.jsonl'
    if not struct_path.is_file():
        return None
    old_start = (frozen['ard_basis'].get('facts') or {}).get('start_ms')
    band_snap = None
    ep_meta = optional(episode / 'episode.json')
    if ep_meta and ep_meta.get('hand_id'):
        ev_src = optional(episode.parent.parent / 'hands' / dossier.key(ep_meta['hand_id']) / 'evidence' / 'source.json')
        if isinstance(ev_src, dict):
            band_snap = ev_src.get('band_snapshot')
    if band_snap is None:
        band_cutoff = (old_start + 4 * 3_600_000) if isinstance(old_start, int) else tick
        band_snap = dossier._resolve_band_snapshot(archive_root, band_cutoff)
    win_ms = (tick - old_start) if isinstance(old_start, int) and tick > old_start else 4 * 3_600_000
    history = [dossier._normalize_legacy_structure_row(r) for r in dossier.rows(struct_path)
               if r.get('expiry_ms') == expiry and isinstance(r.get('at_ms'), int) and r['at_ms'] <= tick]
    try:
        current = dossier.player.ard.analyze(history, index_path, expiry_ms=expiry, cutoff_ms=tick,
                                            band_snapshot=band_snap, window_ms=win_ms)
        comparison = dossier.condition.compare_ard_basis(episode / 'condition.json', current, tick)
        comparison['same_measure'] = comparison.get('new_neighborhood_events') is not None
        pin_status = next((r.get('pin_status') for r in reversed(history)
                           if r.get('available_at_ms') is not None and r['available_at_ms'] <= tick and r.get('pin_status')), None)
        if pin_status:
            comparison['pin_status'] = pin_status
        return dict(facts=current, comparison=comparison)
    except Exception:
        return None


def _run_due_checks(d, episode, at, *, index_path=None, check_due=False, archive_root=None):
    """Run due grid condition checks idempotently without duplicating existing ticks."""
    if index_path is None and not check_due:
        return None, None
    frozen = optional(episode / 'condition.json')
    if frozen is None:
        return [], None
    if index_path is None:
        try:
            expiry = frozen['contract']['expiry_ms']
            index_path = _merge_index_points(archive_root, expiry, at)
            if not index_path['points']:
                return [], 'index_path_unavailable'
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return [], type(exc).__name__
    clock_cfg = frozen['check_clock']
    limit = min(at, frozen['contract']['expiry_ms'] - 1)
    done_ticks = {
        ev.get('observed_at_ms')
        for p in (episode / 'events').glob('check_*.json')
        if (ev := optional(p)) and ev.get('kind') in {'CONDITION_TRIGGERED', 'CONDITION_NOT_TRIGGERED'}
    }
    checked_events = []
    ard_state = None
    for tick in range(clock_cfg['first_at_ms'], limit + 1, clock_cfg['interval_ms']):
        if tick in done_ticks:
            continue
        ard_state = _episode_ard_state(frozen, episode, archive_root, index_path, tick)
        ev = d.check(episode, index_path, tick, available=at,
                     ard=(ard_state['facts'] if ard_state and ard_state['comparison'].get('same_measure') else None))
        checked_events.append(dict(event_id=ev['event_id'], kind=ev['kind'], observed_at_ms=ev['observed_at_ms']))
    return checked_events, None


def _call_with_supported_kwargs(fn, *args, **kwargs):
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return fn(*args, **kwargs)
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return fn(*args, **kwargs)
    return fn(*args, **{k: v for k, v in kwargs.items() if k in params})


def _llm_settings(llm_config=None):
    cfg = dict(llm_config or {})
    api_key = cfg.get('api_key') or os.environ.get('ASTRA_WORKBENCH_LLM_API_KEY') or os.environ.get('LLM_API_KEY')
    model = cfg.get('model') or os.environ.get('ASTRA_PLAYER_MODEL') or os.environ.get('ASTRA_WORKBENCH_LLM_MODEL') or os.environ.get('LLM_MODEL') or 'deepseek-flash'
    endpoint = cfg.get('endpoint') or os.environ.get('LLM_BASE_URL') or os.environ.get('ASTRA_WORKBENCH_LLM_ENDPOINT')
    timeout_s = float(cfg.get('timeout_s') or os.environ.get('LLM_TIMEOUT_SEC') or 300)
    effort = cfg.get('reasoning_effort') or os.environ.get('ASTRA_PLAYER_REASONING_EFFORT') or os.environ.get('LLM_RECON_EFFORT')
    env_retries = os.environ.get('ASTRA_PLAYER_LLM_RETRIES')
    if 'retries' in cfg and cfg['retries'] is not None:
        retries = int(cfg['retries'])
    elif env_retries is not None and env_retries.strip():
        retries = int(env_retries)
    else:
        retries = 0 if timeout_s >= 300 else 1
    budget = cfg.get('budget')
    if budget is None and cfg.get('transport') is None:
        ledger_path = os.environ.get('ASTRA_PLAYER_USAGE_LEDGER') or os.environ.get('ASTRA_WORKBENCH_USAGE_LEDGER')
        if ledger_path:
            daily_limit = int(
                os.environ.get('ASTRA_PLAYER_DAILY_HTTP_LIMIT')
                or os.environ.get('ASTRA_WORKBENCH_DAILY_HTTP_LIMIT')
                or os.environ.get('LLM_DAILY_HTTP_CAP')
                or signal_llm_review.DEFAULT_DAILY_HTTP_LIMIT
            )
            budget = signal_llm_review.DailyHttpBudget(ledger_path, limit=daily_limit)
    return dict(api_key=api_key, model=model, endpoint=endpoint, timeout_s=timeout_s,
                reasoning_effort=effort, retries=retries, budget=budget, transport=cfg.get('transport'))


def _record_revoke_shadow_action(d, episode, frozen, latest_point, at, *, review_id=None):
    """Record SIMULATED_ORDER(BUY_BACK_SHORT) from real short_book asks, else REVOKE_RECORDED_NO_QUOTE."""
    c = frozen['contract']
    quote = locked_compare.short_buyback_quote(latest_point) if latest_point else {
        'short_buyback_ask_btc': None, 'short_buyback_fee_btc': None,
        'short_buyback_total_btc': None, 'short_quote_asof_ms': None,
        'short_buyback_gaps': ['latest_point_unavailable'],
    }
    gaps = list(quote.get('short_buyback_gaps') or [])
    q_ts = quote.get('short_quote_asof_ms')
    if q_ts is None or not (0 <= at - q_ts <= underwriting.MAX_BOOK_AGE_MS):
        if 'short_quote_stale_at_revoke' not in gaps:
            gaps.append('short_quote_stale_at_revoke')
    ask_btc = quote.get('short_buyback_ask_btc')
    fee_btc = quote.get('short_buyback_fee_btc') or 0.0
    if ask_btc is not None and not gaps:
        body = dict(
            action='BUY_BACK_SHORT',
            instrument=c['short_instrument'],
            long_instrument=c['long_instrument'],
            long_retained=True,
            quantity_btc=c['quantity_btc'],
            cost_btc=ask_btc,
            fee_btc=fee_btc,
            total_cost_btc=quote.get('short_buyback_total_btc') or (ask_btc + fee_btc),
            quoted_at_ms=quote.get('short_quote_asof_ms') or at,
            review_id=review_id,
            simulated=True,
            execution_allowed=False,
            assumption='ask_depth_fill_short_only_hold_long_to_expiry',
        )
        ev_id = dossier.identity('sim_buyback', review_id, body)
        return d.event(episode, ev_id, 'SIMULATED_ORDER', body, observed=quote.get('short_quote_asof_ms') or at, available=at)
    body = dict(
        action='REVOKE_RECORDED_NO_QUOTE',
        revoked_at_ms=at,
        short_instrument=c['short_instrument'],
        long_instrument=c['long_instrument'],
        long_retained=True,
        quote_gap_reasons=gaps or ['short_buyback_quote_unavailable'],
        review_id=review_id,
        simulated=True,
        execution_allowed=False,
    )
    ev_id = dossier.identity('revoke_no_quote', review_id, body)
    return d.event(episode, ev_id, 'SHADOW_ACTION', body, observed=at, available=at)


def evaluate_hold_management(d, hand, exported, *, archive_root=None, index_path=None, service=None, llm_config=None,
                           debounce_ms=review.DEFAULT_DEBOUNCE_MS, cooldown_ms=review.DEFAULT_COOLDOWN_MS):
    """Detect A/B/C/D triggers, debounce/coalesce calls, run incremental HOLD/REVOKE/UNKNOWN review & shadow exit."""
    root = hand / 'tracking'
    link_record = load(root / 'link.json')
    origin = link_record['origin']
    episode = hand.parent.parent / 'episodes' / origin['dossier_episode_id']
    frozen = optional(episode / 'condition.json')
    decision = optional(hand / 'decision.json')
    if frozen is None or decision is None:
        return None
    at = d.clock()
    ep_events = sorted((load(p) for p in (episode / 'events').glob('*.json')),
                       key=lambda e: (e['recorded_at_ms'], e['event_id']))
    t0_point = optional(root / 't0' / 'point.json')
    series_rows = list(exported.get('points') or [])
    t0_metrics = (locked_compare.point_metrics(t0_point, t0_point) if t0_point
                  else (series_rows[0] if series_rows else {}))
    ep_index = index_path or _merge_index_points(archive_root, frozen['contract']['expiry_ms'], at, exported_points=series_rows)
    cc = frozen['check_clock']
    cap = min(at, frozen['contract']['expiry_ms'] - 1)
    ard_state = None
    if cap >= cc['first_at_ms']:
        ard_tick = cc['first_at_ms'] + ((cap - cc['first_at_ms']) // cc['interval_ms']) * cc['interval_ms']
        ard_state = _episode_ard_state(frozen, episode, archive_root, ep_index, ard_tick)
    ard_comparison = ard_state['comparison'] if ard_state else None
    triggers = review.detect_hold_triggers(
        frozen_condition=frozen,
        side=frozen['contract']['side'],
        short_strike_usd=frozen['contract']['short_strike_usd'],
        entry_at_ms=frozen['entry_at_ms'],
        t0_metrics=t0_metrics,
        series_rows=series_rows,
        condition_events=ep_events,
        ard_comparison=ard_comparison,
        now_ms=at,
    )
    cfg = _llm_settings(llm_config)
    stale_inflight_ms = max(20 * 60_000, int(cfg.get('timeout_s', 300) * 2500))
    state_path = episode / 'trigger_state.json'
    prior_state = optional(state_path) or {}
    next_state, action = _call_with_supported_kwargs(
        review.schedule_review_action,
        prior_state, triggers, now_ms=at, debounce_ms=debounce_ms,
        cooldown_ms=cooldown_ms, stale_inflight_ms=stale_inflight_ms,
    )
    dossier.write(state_path, dossier.encode(next_state), replace=True)
    if next_state.get('stale_inflight_recovered_at_ms') == at:
        stale_trigs = next_state.get('pending') or (action.get('triggers') if action else None) or []
        stale_cats = sorted({t.get('category') for t in stale_trigs if isinstance(t, dict) and t.get('category')})
        stale_id = dossier.identity('rev_fail', at, 'inflight_stale_recovered')
        if not (episode / 'events' / f'{stale_id}.json').exists():
            d.event(
                episode, stale_id, 'HOLD_REVIEW_FAILED',
                dict(error='inflight_stale_recovered', trigger_categories=stale_cats,
                     model=cfg.get('model'), reasoning_effort=cfg.get('reasoning_effort')),
                observed=at, available=at,
            )
    if next_state.get('pending') and action is None:
        queued_cats = sorted({t.get('category') for t in next_state['pending'] if t.get('category')})
        q_body = dict(pending_since_ms=next_state.get('pending_since_ms'), trigger_categories=queued_cats,
                      triggers=[{k: t.get(k) for k in ('category', 'code', 'signature', 'observed_at_ms', 'summary_cn')}
                                for t in next_state['pending']])
        q_id = dossier.identity('queued', next_state.get('pending_since_ms'), queued_cats)
        if not (episode / 'events' / f'{q_id}.json').exists():
            d.event(episode, q_id, 'HOLD_REVIEW_QUEUED', q_body, observed=at, available=at)
    if not action or not action.get('should_call'):
        return dict(triggers=triggers, action='QUEUED' if next_state.get('pending') else 'NO_TRIGGER',
                    pending=len(next_state.get('pending') or []))

    batch = action['triggers']
    batch_triggers = _compact_triggers(batch, keys=('category', 'code', 'summary_cn', 'observed_at_ms'))
    cats = sorted({t.get('category') for t in batch if t.get('category')})
    if not cfg.get('api_key') and cfg.get('transport') is None:
        done_state = review.complete_review_action(
            next_state, now_ms=at, success=False, cooldown_ms=cooldown_ms, retry_backoff_ms=30 * 60_000,
        )
        dossier.write(state_path, dossier.encode(done_state), replace=True)
        err_id = dossier.identity('rev_fail', at, cats, 'llm_not_configured')
        d.event(episode, err_id, 'HOLD_REVIEW_FAILED',
                dict(error='llm_not_configured', trigger_categories=cats, triggers=batch_triggers,
                     model=cfg.get('model'), reasoning_effort=cfg.get('reasoning_effort')),
                observed=at, available=at)
        return dict(triggers=triggers, action='FAILED', error='llm_not_configured')

    prior_reviews = []
    for rfile in sorted((episode / 'reviews').glob('rev_*/decision.json')):
        try:
            prior_reviews.append(load(rfile))
        except Exception:
            pass
    check_ev_list = [e for e in ep_events if str(e.get('kind', '')).startswith('CONDITION_')]
    cond_summary = _condition_check_summary(check_ev_list)
    prog_triggered = cond_summary['triggered'] > 0
    mech_state = cond_summary['mechanical_condition_state']
    latest_chk_ms = cond_summary['latest_at_ms']
    basis_path = episode / 'entry_basis.json'
    entry_basis = optional(basis_path)
    if entry_basis is None:
        entry_basis = review.build_entry_decision_basis(
            hand_id=hand.name,
            episode_id=episode.name,
            initial_decision=decision,
            frozen_condition=frozen,
            raw_sha256=decision.get('raw_sha256'),
        )
        dossier.write(basis_path, dossier.encode(entry_basis))

    post_spots = [(row.get('metrics') or row).get('spot_usd') for row in series_rows if isinstance(row, dict)]
    post_spots = [s for s in post_spots if dossier.player.number(s)]
    post_dists = [(row.get('metrics') or row).get('short_distance_pct') for row in series_rows if isinstance(row, dict)]
    post_dists = [x for x in post_dists if dossier.player.number(x)]
    latest_m = ((series_rows[-1].get('metrics') if isinstance(series_rows[-1].get('metrics'), dict) else series_rows[-1])
                if series_rows else {})
    latest_pt = exported.get('latest_point')
    quote_info = locked_compare.short_buyback_quote(latest_pt) if latest_pt else {'short_buyback_gaps': ['latest_point_unavailable']}
    input_md = review.build_hold_review_input(
        contract=frozen['contract'],
        entry_at_ms=frozen['entry_at_ms'],
        initial_decision=decision,
        frozen_condition=frozen,
        triggers=batch,
        t0_summary=dict(
            locked_at=frozen['entry_at_ms'],
            entry_index_usd=frozen.get('entry_index_usd'),
            short_distance_pct=t0_metrics.get('short_distance_pct'),
            expected_move_usd=t0_metrics.get('expected_move_usd'),
            net_credit_after_fee_btc=((link_record.get('t0') or {}).get('economy') or {}).get('net_credit_after_entry_fee_btc'),
        ),
        post_entry_summary=dict(
            sample_count=len(series_rows),
            latest_spot_usd=post_spots[-1] if post_spots else None,
            min_short_distance_pct=min(post_dists) if post_dists else None,
            latest_short_distance_pct=post_dists[-1] if post_dists else None,
        ),
        condition_summary=dict(
            program_triggered=prog_triggered,
            mechanical_condition_state=mech_state,
            checks_count=len(check_ev_list),
            latest_check_at_ms=latest_chk_ms,
        ),
        ard_summary=ard_comparison or '未开启或无可比同口径 ARD 数据',
        flow_summary=dict(
            flow_ready=latest_m.get('flow_ready'),
            flow_asof_ms=latest_m.get('flow_asof_ms'),
            flow_direction=latest_m.get('flow_direction'),
            flow_fast_4h=latest_m.get('flow_fast_4h'),
            flow_slow_12h=latest_m.get('flow_slow_12h'),
            flow_cvd_4h=latest_m.get('flow_cvd_4h'),
            flow_cvd_12h=latest_m.get('flow_cvd_12h'),
            flow_momentum_4h_pct=latest_m.get('flow_momentum_4h_pct'),
            flow_momentum_12h_pct=latest_m.get('flow_momentum_12h_pct'),
        ),
        quote_summary=quote_info,
        prior_reviews=prior_reviews,
        entry_basis=entry_basis,
    )
    req_ms = d.clock()
    input_bytes = input_md.encode('utf-8')
    packet_hash = dossier.sha(input_bytes)
    rev_id = dossier.identity('rev', req_ms, cats, packet_hash)
    try:
        messages = [
            {'role': 'system', 'content': '你是中性回路影子持仓复核 AI。只可基于增量牌面输出 HOLD、REVOKE 或 UNKNOWN，严禁改选 Put/Call，严禁推翻已触发的 CONDITION_TRIGGERED。'},
            {'role': 'user', 'content': input_md},
        ]
        llm_res = _call_with_supported_kwargs(
            review.call_chat_llm,
            messages, api_key=cfg['api_key'] or 'test-key', model=cfg['model'],
            endpoint=cfg.get('endpoint'), timeout_s=cfg.get('timeout_s', 300),
            retries=cfg.get('retries'),
            reasoning_effort=cfg.get('reasoning_effort'),
            budget=cfg.get('budget'),
            budget_role='astra_player_hold_review',
            packet_hash=packet_hash,
            transport=cfg.get('transport'),
        )
        raw_reply = llm_res['text']
        actual_model = llm_res.get('model') or cfg['model']
        effort = llm_res.get('reasoning_effort') or cfg.get('reasoning_effort')
        parsed = review.parse_hold_review_response(
            raw_reply,
            frozen_condition=frozen,
            program_triggered=prog_triggered,
            mechanical_state=mech_state,
            valid_condition_ids=entry_basis.get('valid_condition_ids'),
        )
        fin_ms = d.clock()
        raw_bytes = raw_reply.encode('utf-8')
        shadow_action = (
            'FROZEN_RULE_TRIGGERED_CONFLICT' if parsed['protocol_status'] == 'CONFLICT_WITH_FROZEN_INVALIDATION'
            else 'REVOKE_DISCRETIONARY' if parsed['protocol_status'] == 'DISCRETIONARY_REVOKE'
            else 'REVOKE_ALIGNED' if parsed['verdict'] == 'REVOKE'
            else 'HOLD_SHADOW_CONTINUE' if parsed['verdict'] == 'HOLD'
            else 'HOLD_UNKNOWN_OBSERVE'
        )
        rev_decision = dict(
            review_id=rev_id,
            status='VALID',
            verdict=parsed['verdict'],
            ai_management_verdict=parsed['ai_management_verdict'],
            mechanical_condition_state=parsed['mechanical_condition_state'],
            protocol_status=parsed['protocol_status'],
            shadow_action=shadow_action,
            trigger_categories=cats,
            triggers=batch,
            reason=action['reason'],
            affected_conditions=parsed['affected_conditions'],
            mapped_condition_ids=parsed['mapped_condition_ids'],
            unmapped_condition_ids=parsed['unmapped_condition_ids'],
            condition_id_mapping_status=parsed['condition_id_mapping_status'],
            public_reply=parsed['public_reply'],
            key_facts=parsed['key_facts'],
            notes=parsed.get('notes') or [],
            program_triggered=prog_triggered,
            model=actual_model,
            reasoning_effort=effort,
            requested_at_ms=req_ms,
            decided_at_ms=fin_ms,
            input_sha256=packet_hash,
            raw_sha256=dossier.sha(raw_bytes),
        )
        attempt_rec = dict(
            review_id=rev_id, status='VALID', model=actual_model, reasoning_effort=effort,
            requested_at_ms=req_ms, decided_at_ms=fin_ms,
            usage=llm_res.get('usage'), http_status=llm_res.get('http_status'),
        )
        dossier.publish(episode / 'reviews' / rev_id, {
            'input.md': input_bytes,
            'response.raw': raw_bytes,
            'attempt.json': dossier.encode(attempt_rec),
            'decision.json': dossier.encode(rev_decision),
        })
        ev_kind = 'AI_HOLD' if parsed['verdict'] == 'HOLD' else 'AI_REVOKE' if parsed['verdict'] == 'REVOKE' else 'AI_HOLD_UNKNOWN'
        ev_body = {k: rev_decision[k] for k in (
            'review_id', 'verdict', 'ai_management_verdict', 'mechanical_condition_state',
            'protocol_status', 'shadow_action', 'trigger_categories', 'affected_conditions',
            'condition_id_mapping_status', 'public_reply', 'model', 'reasoning_effort',
            'input_sha256', 'raw_sha256')}
        ev_body['triggers'] = batch_triggers
        d.event(episode, dossier.identity('ai_rev', rev_id), ev_kind, ev_body, observed=req_ms, available=fin_ms, decided=fin_ms)
        done_state = review.complete_review_action(next_state, now_ms=fin_ms, success=True, verdict=parsed['verdict'], cooldown_ms=cooldown_ms)
        dossier.write(state_path, dossier.encode(done_state), replace=True)
        result_out = dict(
            triggers=triggers, action='REVIEWED', review_id=rev_id, verdict=parsed['verdict'],
            mechanical_condition_state=parsed['mechanical_condition_state'],
            protocol_status=parsed['protocol_status'], shadow_action=shadow_action,
        )
    except Exception as exc:
        fin_ms = d.clock()
        backoff_ms = 30 * 60_000 if isinstance(exc, signal_llm_review.DailyBudgetExceeded) else review.RETRY_BACKOFF_MS
        done_state = review.complete_review_action(
            next_state, now_ms=fin_ms, success=False, cooldown_ms=cooldown_ms, retry_backoff_ms=backoff_ms,
        )
        dossier.write(state_path, dossier.encode(done_state), replace=True)
        err_id = dossier.identity('rev_fail', fin_ms, cats, type(exc).__name__)
        d.event(episode, err_id, 'HOLD_REVIEW_FAILED',
                dict(review_id=rev_id, error=type(exc).__name__, detail=str(exc)[:180], trigger_categories=cats,
                     triggers=batch_triggers, model=cfg['model'], reasoning_effort=cfg.get('reasoning_effort')),
                observed=req_ms, available=fin_ms)
        return dict(triggers=triggers, action='FAILED', review_id=rev_id, error=str(exc))

    if result_out['verdict'] == 'REVOKE':
        quote_pt = latest_pt
        if service is not None:
            try:
                q_res = service('quote', dict(dossier_id=hand.parent.parent.name, hand_id=hand.name))
            except Exception:
                q_res = None
            if isinstance(q_res, dict) and q_res.get('latest_point') is not None:
                quote_pt = q_res['latest_point']
        try:
            revoke_at = d.clock()
            _record_revoke_shadow_action(d, episode, frozen, quote_pt, revoke_at, review_id=rev_id)
        except Exception as exc:
            result_out['shadow_action_error'] = f'{type(exc).__name__}: {str(exc)[:180]}'
    return result_out


def sync(d, hand, service, *, index_path=None, check_due=False, archive_root=None,
         run_review=False, llm_config=None, debounce_ms=review.DEFAULT_DEBOUNCE_MS, cooldown_ms=review.DEFAULT_COOLDOWN_MS):
    """Copy the native tracker evidence; optionally run due fixed-clock checks and event-triggered hold reviews."""
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
    ep_index = index_path
    if ep_index is None and check_due:
        ep_index = _merge_index_points(archive_root, ep['contract']['expiry_ms'], at, exported_points=exported['points'])
    checks, check_error = _run_due_checks(d, episode, at, index_path=ep_index, check_due=check_due, archive_root=archive_root)
    review_result = None
    if run_review and not archived:
        review_result = evaluate_hold_management(
            d, hand, exported, archive_root=archive_root, index_path=ep_index,
            service=service, llm_config=llm_config, debounce_ms=debounce_ms, cooldown_ms=cooldown_ms)
    if archived and completed and settlement_state in {'settled', 'delivery_price_unavailable'}:
        dossier.write(root / 'terminal.json', dossier.encode(dict(export_id=archive_id)))
    out = dict(state=ep['status'], event_id=event['event_id'], full_series=completed, settlement=settlement_state)
    if checks is not None:
        out['checks'] = checks
    if check_error is not None:
        out['check_error'] = check_error
    if review_result is not None:
        out['hold_review'] = review_result
    return out


def auto_deal(d, plan, archive_root, service, *, llm_config=None, clock=dossier.now_ms):
    """Run scheduled initial deal -> AI decision -> shadow tracking link when Accept."""
    cand_round = plan.get('candidate_round', 'r2')
    hands = d.tick(plan, source_for=lambda expiry, slot: dossier.collect(
        archive_root, expiry, slot, clock=d.clock, candidate_round=cand_round))
    cfg = _llm_settings(llm_config)
    results = []
    for hand in hands:
        exp = load(hand / 'expected.json')
        now = d.clock()
        if (hand / 'decision.json').is_file():
            decision = load(hand / 'decision.json')
            action = decision['judgment']['action']
            link_res = None
            if action == 'Accept' and not (hand / 'tracking' / 'link.json').is_file() and not (hand / 'tracking' / 'blocked.json').is_file():
                req = optional(hand / 'tracking' / 'request.json')
                if req and req.get('condition'):
                    link_res = link(d, hand, service, req['condition'])
            results.append(dict(hand_id=hand.name, status='ALREADY_DECIDED', decision=action, link=link_res))
            continue
        if not (hand / 'evidence' / 'input.md').is_file():
            results.append(dict(hand_id=hand.name, status='NO_EVIDENCE'))
            continue
        meta = d.verify_evidence(hand)
        deadline = dossier.Dossiers.reply_deadline(exp, meta)
        if deadline is not None and now >= deadline:
            results.append(dict(hand_id=hand.name, status='REPLY_WINDOW_CLOSED'))
            continue
        existing_auto = [load(p) for p in (hand / 'responses').glob('*/attempt.json')
                         if not p.parent.name.startswith('.') and load(p).get('channel') == 'API_AUTO']
        if any(a.get('status') in {'VALID', 'RECEIVED'} for a in existing_auto) or len(existing_auto) >= 3:
            results.append(dict(hand_id=hand.name, status='AUTO_ATTEMPTED', last_status=existing_auto[-1].get('status')))
            continue
        notes = [load(p) for p in (hand / 'issuance').glob('*.json')]
        bjt_today = (now + 8 * 3_600_000) // dossier.DAY
        if any(
            n.get('state') == 'DAILY_BUDGET_EXHAUSTED'
            and ((n.get('recorded_at_ms') or 0) + 8 * 3_600_000) // dossier.DAY == bjt_today
            for n in notes
        ):
            results.append(dict(hand_id=hand.name, status='DAILY_BUDGET_EXHAUSTED'))
            continue
        if not cfg.get('api_key') and cfg.get('transport') is None:
            d.note(hand, 'LLM_NOT_CONFIGURED', 'missing_api_key')
            results.append(dict(hand_id=hand.name, status='LLM_NOT_CONFIGURED'))
            continue
        _, input_bytes, source = evidence(hand)
        contracts_pair = [contract_fields(dossier.player.bind(case, card, card['side']))
                          for case, card in zip(source['cases'], source['cards'])]
        req_ms = d.clock()
        attempt_id = dossier.identity('auto', req_ms, cfg['model'])
        try:
            ai_res = _call_with_supported_kwargs(
                review.decide_initial_hand,
                input_bytes.decode('utf-8'),
                contract_pair=contracts_pair,
                api_key=cfg['api_key'] or 'test-key',
                model=cfg['model'],
                endpoint=cfg.get('endpoint'),
                timeout_s=cfg.get('timeout_s', 300),
                retries=cfg.get('retries'),
                reasoning_effort=cfg.get('reasoning_effort'),
                budget=cfg.get('budget'),
                packet_hash=meta['input_sha256'],
                transport=cfg.get('transport'),
            )
            dec_ms = d.clock()
            att = d.respond(
                hand, attempt_id, 'RECEIVED', ai_res['raw_bytes'], ai_res['annotation'],
                model=ai_res.get('model') or cfg['model'],
                configuration=dict(reasoning_effort=ai_res.get('reasoning_effort') or cfg.get('reasoning_effort'),
                                   usage=ai_res.get('usage'), http_status=ai_res.get('http_status')),
                requested=req_ms, decided=dec_ms, channel='API_AUTO',
            )
            verdict = (att.get('judgment') or {}).get('action')
            link_res = None
            if att.get('status') == 'VALID' and verdict == 'Accept':
                link_res = link(d, hand, service, ai_res.get('condition_plan'))
            results.append(dict(hand_id=hand.name, status=att.get('status'), decision=verdict,
                                error=att.get('error'), link=link_res))
        except signal_llm_review.DailyBudgetExceeded as exc:
            d.note(hand, 'DAILY_BUDGET_EXHAUSTED', str(exc)[:180])
            results.append(dict(hand_id=hand.name, status='DAILY_BUDGET_EXHAUSTED', error=str(exc)))
        except Exception as exc:
            dec_ms = d.clock()
            is_timeout = (
                isinstance(exc, TimeoutError)
                or 'timed out' in str(exc).lower()
                or isinstance(getattr(exc, 'reason', None), TimeoutError)
            )
            fail_status = 'TIMEOUT' if is_timeout else 'REQUEST_FAILED'
            err_text = f"{type(exc).__name__}: {str(exc)[:180]}"
            try:
                _call_with_supported_kwargs(
                    d.respond,
                    hand, attempt_id, fail_status, None, None, model=cfg['model'],
                    configuration=dict(error=type(exc).__name__, detail=str(exc)[:180],
                                       reasoning_effort=cfg.get('reasoning_effort')),
                    requested=req_ms, decided=dec_ms, channel='API_AUTO',
                    error=err_text,
                )
            except Exception:
                pass
            results.append(dict(hand_id=hand.name, status=fail_status, error=err_text))
        d.summary(exp['expiry_ms'])
    return results


def _compact_triggers(triggers, keys=('category', 'code', 'summary_cn')):
    if not isinstance(triggers, list):
        return []
    return [{k: t.get(k) for k in keys} for t in triggers if isinstance(t, dict)]


def _condition_check_summary(check_events):
    """Deduplicate grid checks per observed timestamp (preferring resolved checks over UNKNOWN)."""
    by_tick = {}
    for idx, ev in enumerate(check_events):
        kind = str(ev.get('kind', ''))
        if not kind.startswith('CONDITION_'):
            continue
        obs = ev.get('observed_at_ms')
        rec = ev.get('recorded_at_ms') or 0
        key = obs if isinstance(obs, int) else f'idx_{idx}_{ev.get("event_id")}'
        order_key = (obs if isinstance(obs, int) else rec, rec, 1 if kind != 'CONDITION_UNKNOWN' else 0, str(ev.get('event_id') or ''))
        prev = by_tick.get(key)
        if prev is None or order_key > prev[0]:
            by_tick[key] = (order_key, ev)
    ordered = [pair[1] for pair in sorted(by_tick.values(), key=lambda pair: pair[0])]
    triggered = sum(1 for e in ordered if e.get('kind') == 'CONDITION_TRIGGERED')
    not_triggered = sum(1 for e in ordered if e.get('kind') == 'CONDITION_NOT_TRIGGERED')
    unknown = sum(1 for e in ordered if e.get('kind') == 'CONDITION_UNKNOWN')
    latest = ordered[-1] if ordered else None
    mech_state = ('TRIGGERED' if triggered
                  else 'DATA_GAP' if (latest and latest.get('kind') == 'CONDITION_UNKNOWN')
                  else 'INTACT')
    latest_at_ms = ((latest.get('observed_at_ms') or latest.get('recorded_at_ms')) if latest else None)
    return dict(
        total=len(ordered), triggered=triggered, not_triggered=not_triggered,
        unknown=unknown, latest_at_ms=latest_at_ms, mechanical_condition_state=mech_state,
    )


def _event_projection(ev):
    body = ev.get('body') if isinstance(ev.get('body'), dict) else {}
    kind = ev['kind']
    item = dict(event_id=ev.get('event_id'), kind=kind,
                observed_at_ms=ev.get('observed_at_ms'), recorded_at_ms=ev.get('recorded_at_ms'))
    if kind in {'AI_HOLD', 'AI_REVOKE', 'AI_HOLD_UNKNOWN'}:
        item.update(review_id=body.get('review_id'), verdict=body.get('verdict'),
                    trigger_categories=body.get('trigger_categories'),
                    triggers=_compact_triggers(body.get('triggers')),
                    model=body.get('model'),
                    reasoning_effort=body.get('reasoning_effort'),
                    protocol_status=body.get('protocol_status'),
                    shadow_action=body.get('shadow_action'),
                    summary=body.get('public_reply'))
    elif kind == 'HOLD_REVIEW_FAILED':
        item.update(review_id=body.get('review_id'), verdict='FAILED',
                    trigger_categories=body.get('trigger_categories'),
                    triggers=_compact_triggers(body.get('triggers')),
                    model=body.get('model'),
                    reasoning_effort=body.get('reasoning_effort'),
                    summary=body.get('detail') or body.get('error'))
    elif kind == 'HOLD_REVIEW_QUEUED':
        item.update(trigger_categories=body.get('trigger_categories'),
                    triggers=_compact_triggers(body.get('triggers')),
                    summary='等待去抖合并窗口完成')
    elif kind == 'SIMULATED_ORDER':
        item.update(summary=f"买回 {body.get('instrument')} · 成本 {body.get('cost_btc')} BTC")
    elif kind == 'SHADOW_ACTION':
        item.update(summary=f"{body.get('action')} · {', '.join(body.get('quote_gap_reasons') or [])}")
    elif kind == 'CONDITION_TRIGGERED':
        res = body.get('result') if isinstance(body.get('result'), dict) else {}
        item.update(summary=res.get('rule') or '原冻结条件已触发')
    return item


def _episode_management_data(ep, frozen, sorted_events, *, contract=None, include_reply=True):
    check_events = [e for e in sorted_events if str(e.get('kind', '')).startswith('CONDITION_')]
    trig_events = [e for e in sorted_events if e.get('kind') == 'CONDITION_TRIGGERED']
    rev_events = [e for e in sorted_events if e.get('kind') == 'AI_REVOKE']
    buybacks = [e for e in sorted_events if e.get('kind') == 'SIMULATED_ORDER'
                and (e.get('body') or {}).get('action') == 'BUY_BACK_SHORT']
    no_quotes = [e for e in sorted_events if e.get('kind') == 'SHADOW_ACTION'
                 and (e.get('body') or {}).get('action') == 'REVOKE_RECORDED_NO_QUOTE']

    reviews = []
    seen_ids = set()
    if ep is not None and (ep / 'reviews').is_dir():
        for folder in sorted((ep / 'reviews').glob('rev_*')):
            dec = optional(folder / 'decision.json')
            if not dec:
                continue
            rev_id = dec.get('review_id') or folder.name
            seen_ids.add(rev_id)
            rec = {k: dec.get(k) for k in (
                'review_id', 'status', 'verdict', 'ai_management_verdict', 'mechanical_condition_state',
                'protocol_status', 'shadow_action', 'trigger_categories', 'affected_conditions',
                'mapped_condition_ids', 'unmapped_condition_ids', 'condition_id_mapping_status',
                'model', 'reasoning_effort', 'requested_at_ms', 'decided_at_ms', 'input_sha256', 'raw_sha256')}
            if rec.get('reasoning_effort') is None:
                att_dec = optional(folder / 'attempt.json')
                if isinstance(att_dec, dict) and att_dec.get('reasoning_effort') is not None:
                    rec['reasoning_effort'] = att_dec.get('reasoning_effort')
            rec['triggers'] = _compact_triggers(
                dec.get('triggers'), keys=('category', 'code', 'summary_cn', 'observed_at_ms'))
            if include_reply:
                rec['public_reply'] = dec.get('public_reply')
                in_path, raw_path = folder / 'input.md', folder / 'response.raw'
                rec['input_md'] = bounded(in_path).decode('utf-8') if in_path.is_file() else None
                rec['raw_reply'] = bounded(raw_path).decode('utf-8') if raw_path.is_file() else None
            reviews.append(rec)
    for ev in sorted_events:
        if ev.get('kind') == 'HOLD_REVIEW_FAILED':
            b = ev.get('body') or {}
            reviews.append(dict(
                review_id=b.get('review_id') or ev.get('event_id'),
                status='FAILED',
                verdict=None,
                trigger_categories=b.get('trigger_categories') or [],
                triggers=_compact_triggers(
                    b.get('triggers'), keys=('category', 'code', 'summary_cn', 'observed_at_ms')),
                model=b.get('model'),
                reasoning_effort=b.get('reasoning_effort'),
                requested_at_ms=ev.get('observed_at_ms') or ev.get('recorded_at_ms'),
                decided_at_ms=ev.get('recorded_at_ms'),
                error=b.get('detail') or b.get('error') or 'HOLD_REVIEW_FAILED',
            ))
    reviews.sort(key=lambda r: (r.get('decided_at_ms') or r.get('requested_at_ms') or 0, str(r.get('review_id') or '')), reverse=True)

    cond_summary = _condition_check_summary(check_events)
    mech_state = cond_summary['mechanical_condition_state']
    latest_rev = reviews[0] if reviews else {}
    protocol_status = latest_rev.get('protocol_status') or ('DISCRETIONARY_REVOKE' if (not trig_events and rev_events) else 'ALIGNED')
    shadow_action = latest_rev.get('shadow_action') or ('REVOKE_DISCRETIONARY' if (not trig_events and rev_events) else 'HOLD_SHADOW_CONTINUE' if latest_rev.get('verdict') == 'HOLD' else 'NONE')

    c = (frozen or {}).get('contract') or contract or {}
    if buybacks:
        fill = buybacks[-1].get('body') or {}
        exit_status = 'SIMULATED_BUY_BACK_SHORT'
        shadow_mgmt = dict(
            mechanical_condition_state=mech_state,
            ai_management_verdict=latest_rev.get('verdict'),
            protocol_status=protocol_status,
            shadow_action=shadow_action,
            condition_triggered_at_ms=(trig_events[0].get('observed_at_ms') or trig_events[0].get('recorded_at_ms')) if trig_events else None,
            ai_revoked_at_ms=(rev_events[-1].get('observed_at_ms') or rev_events[-1].get('recorded_at_ms')) if rev_events else None,
            exit_status=exit_status,
            buyback_cost_btc=fill.get('cost_btc'),
            buyback_fee_btc=fill.get('fee_btc'),
            buyback_quote_at_ms=fill.get('quoted_at_ms'),
            retained_long_instrument=fill.get('long_instrument') or c.get('long_instrument'),
            long_retained=True,
        )
    elif no_quotes or rev_events:
        nq = (no_quotes[-1].get('body') or {}) if no_quotes else {}
        shadow_mgmt = dict(
            mechanical_condition_state=mech_state,
            ai_management_verdict=latest_rev.get('verdict'),
            protocol_status=protocol_status,
            shadow_action=shadow_action,
            condition_triggered_at_ms=(trig_events[0].get('observed_at_ms') or trig_events[0].get('recorded_at_ms')) if trig_events else None,
            ai_revoked_at_ms=(rev_events[-1].get('observed_at_ms') or rev_events[-1].get('recorded_at_ms')) if rev_events else None,
            exit_status='REVOKE_NO_QUOTE',
            quote_gap_reasons=nq.get('quote_gap_reasons') or ['short_buyback_quote_unavailable'],
            retained_long_instrument=nq.get('long_instrument') or c.get('long_instrument'),
            long_retained=True,
        )
    else:
        shadow_mgmt = dict(
            mechanical_condition_state=mech_state,
            ai_management_verdict=latest_rev.get('verdict'),
            protocol_status=protocol_status,
            shadow_action=shadow_action,
            condition_triggered_at_ms=(trig_events[0].get('observed_at_ms') or trig_events[0].get('recorded_at_ms')) if trig_events else None,
            ai_revoked_at_ms=None,
            exit_status='NONE',
            retained_long_instrument=c.get('long_instrument'),
            long_retained=True,
        )
    return dict(
        hold_reviews=reviews,
        shadow_management=shadow_mgmt,
        check_count=cond_summary['total'],
        grid_check_summary=cond_summary,
    )


def episode_management_summary(root, dossier_id, hand_id, dossier_episode_id=None, *, include_reply=False):
    """Lightweight read-only projection of dossier condition checks, AI hold reviews & shadow exit for locks view."""
    if not dossier_id or not hand_id:
        return None
    hand = hand_path(root, dossier_id, hand_id)
    link_rec = optional(hand / 'tracking' / 'link.json')
    ep_id = dossier_episode_id or ((link_rec or {}).get('origin') or {}).get('dossier_episode_id')
    if not ep_id:
        return None
    ep = hand.parent.parent / 'episodes' / dossier.key(ep_id)
    if not ep.is_dir():
        return None
    frozen = optional(ep / 'condition.json')
    sorted_events = sorted((load(p) for p in (ep / 'events').glob('*.json')),
                           key=lambda e: (e['recorded_at_ms'], e['event_id']))
    mgmt = _episode_management_data(ep, frozen, sorted_events, include_reply=include_reply)
    latest = mgmt['hold_reviews'][0] if mgmt['hold_reviews'] else None
    out = dict(
        frozen_check_count=mgmt['check_count'],
        mechanical_condition_state=mgmt['shadow_management']['mechanical_condition_state'],
        protocol_status=mgmt['shadow_management']['protocol_status'],
        shadow_action=mgmt['shadow_management']['shadow_action'],
        condition_triggered=mgmt['shadow_management']['condition_triggered_at_ms'] is not None,
        condition_triggered_at_ms=mgmt['shadow_management']['condition_triggered_at_ms'],
        ai_revoked_at_ms=mgmt['shadow_management']['ai_revoked_at_ms'],
        exit_status=mgmt['shadow_management']['exit_status'],
        shadow_management=mgmt['shadow_management'],
        review_count=len(mgmt['hold_reviews']),
    )
    if latest:
        out['latest_verdict'] = latest.get('verdict') or latest.get('status')
        out['latest_trigger_categories'] = latest.get('trigger_categories') or []
        out['latest_reviewed_at_ms'] = latest.get('decided_at_ms') or latest.get('requested_at_ms')
        if include_reply:
            latest_view = {k: latest.get(k) for k in (
                'review_id', 'verdict', 'ai_management_verdict', 'mechanical_condition_state',
                'protocol_status', 'shadow_action', 'status', 'trigger_categories', 'triggers',
                'model', 'requested_at_ms', 'decided_at_ms', 'public_reply', 'affected_conditions',
                'unmapped_condition_ids', 'condition_id_mapping_status', 'error')}
            if latest.get('reasoning_effort') is not None:
                latest_view['reasoning_effort'] = latest.get('reasoning_effort')
            out['latest_review'] = latest_view
    return out


def _attempt_projection(attempt_rec):
    cfg = attempt_rec.get('configuration') if isinstance(attempt_rec.get('configuration'), dict) else {}
    err = attempt_rec.get('error')
    if not err and cfg.get('error'):
        err = f"{cfg['error']}: {cfg['detail']}" if cfg.get('detail') else cfg['error']
    return {
        'status': attempt_rec.get('status'),
        'model': attempt_rec.get('model'),
        'reasoning_effort': cfg.get('reasoning_effort') or attempt_rec.get('reasoning_effort'),
        'requested_at_ms': attempt_rec.get('requested_at_ms'),
        'decided_at_ms': attempt_rec.get('decided_at_ms'),
        'recorded_at_ms': attempt_rec.get('recorded_at_ms'),
        'error': err,
    }


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
             notes[-1]['state'] if notes and notes[-1].get('state') in {
                 'LLM_NOT_CONFIGURED', 'DAILY_BUDGET_EXHAUSTED', 'POLICY_BLOCKED'} else
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
    sorted_events = sorted((load(p) for p in (ep / 'events').glob('*.json')),
                           key=lambda e: (e['recorded_at_ms'], e['event_id'])) if ep else []
    recent = sorted_events[-64:]
    frozen_cond = optional(ep / 'condition.json') if ep else None
    mgmt = _episode_management_data(ep, frozen_cond, sorted_events, include_reply=True) if ep else None
    entry_basis = optional(ep / 'entry_basis.json') if ep else None
    if entry_basis is None and decision and frozen_cond:
        entry_basis = review.build_entry_decision_basis(
            hand_id=hand.name,
            episode_id=ep.name if ep else None,
            initial_decision=decision,
            frozen_condition=frozen_cond,
            raw_sha256=decision.get('raw_sha256'),
        )
    top_cfg = ((attempt or (attempts[-1] if attempts else None) or {}).get('configuration') or {})
    note_lock_reason = next(
        (n.get('reason') for n in reversed(notes)
         if n.get('state') in {'LOCK_BLOCKED', 'LINK_UNVERIFIED'} and n.get('reason')),
        None,
    )
    result.update(decision=decision, requested_at_ms=(attempt or {}).get('requested_at_ms'),
                  reasoning_effort=top_cfg.get('reasoning_effort'),
                  lock_requested_at_ms=(req or {}).get('requested_at_ms'), receipt=receipt and receipt['t0'],
                  condition=frozen_cond, entry_basis=entry_basis,
                  events=[_event_projection(e) for e in recent], archive=terminal_meta,
                  attempts=[_attempt_projection(a) for a in attempts[-8:]],
                  notes=notes[-8:], lock_reason=(blocked or {}).get('reason') or note_lock_reason)
    if mgmt is not None:
        result['hold_reviews'] = mgmt['hold_reviews']
        result['shadow_management'] = mgmt['shadow_management']
        result['grid_check_summary'] = mgmt.get('grid_check_summary')
    outcomes = [(path, load(path)) for path in (hand.parent.parent / 'outcomes').glob('outcome_*/outcome.json')]
    if outcomes:
        out = max(outcomes, key=lambda pair: pair[1]['recorded_at_ms'])[1]
        result['outcome'] = {k: out.get(k) for k in ('classification', 'delivery', 'verification_gap', 'recorded_at_ms')}
        grade = next((h for h in out['hands'] if h['hand_id'] == hand.name), None)
        if grade:
            result['outcome']['liability_btc'] = {
                side: row['delivery_payout_btc'] for side, row in grade['sides'].items()}
            for extra in ('t0_net_credit_after_entry_fee_btc', 'hold_to_expiry_shadow_net_btc', 'managed_cashflow'):
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
    slot_offset = plan['effective_at_ms'] % dossier.DAY
    slot = now - now % dossier.DAY + slot_offset
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
    cfg = _llm_settings()
    report = dict(state='PREWARM_DRY_RUN' if dry_run else 'PREWARM', plan_id=plan['plan_id'],
                  candidate_round=plan.get('candidate_round', 'r2'),
                  checked_at_ms=now, scheduled_at_ms=slot,
                  target_expiry_ms=expiry,
                  response_channel='API_AUTO' if cfg.get('api_key') else 'MANUAL_IMPORT',
                  llm_configured=bool(cfg.get('api_key')),
                  llm_model=cfg.get('model'),
                  archive_readable=(archive / 'cases').is_dir() and (archive / 'levels').is_dir(),
                  structure_readable=(archive / 'structure').is_dir(),
                  band_snapshots_readable=((archive / 'band_snapshots').is_dir()
                                           or bool(os.environ.get('ASTRA_WORKBENCH_SNAPSHOTS_JSONL'))),
                  execution_allowed=False)
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
    parser.add_argument('--url', default='http://127.0.0.1:8895', help='private loopback workbench listener')
    sub = parser.add_subparsers(dest='command', required=True)
    warm = sub.add_parser('prewarm')
    warm.add_argument('--plan', type=Path, required=True)
    warm.add_argument('--archive', type=Path, required=True)
    warm.add_argument('--dry-run', action='store_true')
    deal = sub.add_parser('auto-deal')
    deal.add_argument('--root', type=Path, required=True)
    deal.add_argument('--plan', type=Path, required=True)
    deal.add_argument('--archive', type=Path, required=True)
    for name in ('link', 'sync'):
        command = sub.add_parser(name)
        command.add_argument('--root', type=Path, required=True)
        command.add_argument('--dossier', required=True)
        command.add_argument('--hand', required=True)
        if name == 'link':
            command.add_argument('--condition', type=Path)
        else:
            command.add_argument('--check-due', action='store_true')
            command.add_argument('--review', action='store_true')
            command.add_argument('--archive', type=Path)
    follow = sub.add_parser('sync-open')
    follow.add_argument('--root', type=Path, required=True)
    follow.add_argument('--check-due', action='store_true')
    follow.add_argument('--review', action='store_true')
    follow.add_argument('--archive', type=Path)
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
            with dossier.writer(d.root, timeout_s=60.0):
                if args.command == 'auto-deal':
                    out = auto_deal(d, dossier.read(args.plan), args.archive, service)
                elif args.command == 'sync-open':
                    out = []
                    for link_file in (d.root / 'dossiers').glob('*/hands/hand_*/tracking/link.json'):
                        if (link_file.parent / 'terminal.json').exists():
                            continue
                        hand = link_file.parent.parent
                        try:
                            out.append(sync(d, hand, service, check_due=args.check_due,
                                            archive_root=args.archive, run_review=args.review))
                        except (OSError, ValueError, KeyError, TypeError) as exc:
                            d.note(hand, 'TRACKER_EXPORT_UNAVAILABLE', getattr(exc, 'code', type(exc).__name__))
                            out.append(dict(state='TRACKER_EXPORT_UNAVAILABLE', hand_id=hand.name))
                        d.summary(load(hand / 'expected.json')['expiry_ms'])
                else:
                    hand = hand_path(args.root, args.dossier, args.hand)
                    out = (link(d, hand, service, dossier.read(args.condition) if args.condition else None)
                           if args.command == 'link'
                           else sync(d, hand, service, check_due=args.check_due,
                                     archive_root=args.archive, run_review=args.review))
                    d.summary(load(hand / 'expected.json')['expiry_ms'])
        print(dossier.encode(out).decode('utf-8'), end='')
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f'{getattr(exc, "code", type(exc).__name__)}\n')
    if isinstance(out, dict) and out.get('state') in {'LOCK_BLOCKED', 'LINK_UNVERIFIED', 'NO_TRACKING_REQUEST'}:
        parser.exit(2)


if __name__ == '__main__':
    main()
