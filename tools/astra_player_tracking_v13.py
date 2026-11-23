"""Join the daily dossier to the workbench's existing shadow observations."""

from contextlib import ExitStack, contextmanager
from pathlib import Path
import argparse
import inspect
import json
import os
import re
import threading
import time
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
REVIEW_ID = r'rev_[0-9a-f]{24}'


class Blocked(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


_WRITER_STATE = threading.local()


@contextmanager
def _short_writer(d, timeout_s=60.0):
    """Acquire dossier writer lock only around brief disk mutations (never across LLM HTTP I/O)."""
    active = getattr(_WRITER_STATE, 'roots', None)
    if active is None:
        active = set()
        _WRITER_STATE.roots = active
    root_key = str(Path(d.root).resolve())
    if root_key in active:
        yield
        return
    active.add(root_key)
    try:
        with dossier.writer(d.root, timeout_s=timeout_s):
            yield
    finally:
        active.discard(root_key)


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
    contracts_map = {}
    for c_case, c_card in zip(source['cases'], source['cards']):
        c_side = c_card.get('side')
        if c_side in {'put', 'call'}:
            contracts_map[c_side] = dossier.player.bind(c_case, c_card, c_side)
    req_saved = optional(hand / 'responses' / dossier.key(attempt['attempt_id']) / 'request.json')
    req_pv = req_saved.get('prompt_version') if isinstance(req_saved, dict) else None
    att_pv = attempt.get('prompt_version')
    if req_pv is not None and att_pv is not None and req_pv != att_pv:
        raise Blocked('prompt_version_mismatch')
    pv = req_pv or att_pv
    p0_profile = getattr(dossier.player, 'INPUT_PROFILE_P0_STRICT', 'p0_strict@1')
    plan_pv = (expected.get('plan') or {}).get('prompt_version')
    if plan_pv is not None and pv != plan_pv:
        raise Blocked('prompt_version_mismatch')
    if meta.get('input_profile') == p0_profile and (not pv or pv == review.INITIAL_SYSTEM_PROMPT_VERSION):
        raise Blocked('prompt_version_mismatch')
    out = dict(origin=origin, contract=contract, contracts=contracts_map, source=source, decision=decision,
               raw=raw.decode('utf-8'), deadline_ms=min(deadline, decided + HANDOFF_MS))
    if pv is not None:
        out['prompt_version'] = pv
    if plan_pv is not None:
        out['plan_prompt_version'] = plan_pv
    if meta.get('input_profile') is not None:
        out['input_profile'] = meta['input_profile']
    return out


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


def _is_candidate_proof(proof):
    p0_profile = getattr(dossier.player, 'INPUT_PROFILE_P0_STRICT', 'p0_strict@1')
    pv = proof.get('prompt_version')
    ip = proof.get('input_profile')
    plan_pv = proof.get('plan_prompt_version')
    if ip == p0_profile and not pv:
        raise Blocked('missing_prompt_version')
    if pv is not None and pv not in review.INITIAL_SYSTEM_PROMPTS:
        raise Blocked('unknown_prompt_version')
    if plan_pv is not None and pv != plan_pv:
        raise Blocked('prompt_version_mismatch')
    if ip == p0_profile and pv == review.INITIAL_SYSTEM_PROMPT_VERSION:
        raise Blocked('prompt_version_mismatch')
    return (pv in review.CANDIDATE_STRICT_PROMPT_VERSIONS) or (
        ip == p0_profile and pv != review.INITIAL_SYSTEM_PROMPT_VERSION
    )


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
    is_candidate = _is_candidate_proof(proof)
    strict_mode = bool(is_candidate or proof.get('strict_contract'))
    is_p5_strict = proof.get('prompt_version') in getattr(review, 'P5_STRICT_PROMPT_VERSIONS', ())
    if strict_mode:
        if 'require_outside_at_t0' in plan:
            if plan.get('require_outside_at_t0') is not True:
                raise Blocked('condition_contract_validation_failed')
        elif is_p5_strict or proof.get('strict_contract'):
            raise Blocked('condition_contract_validation_failed')
        if plan['check_interval_ms'] != 300_000:
            raise Blocked('condition_contract_validation_failed')
        outside = True
    else:
        outside = plan.get('require_outside_at_t0', False)
        if not isinstance(outside, bool):
            raise Blocked('condition_plan_missing_or_unverifiable')
    out_plan = {**{k: plan[k] for k in (*required, 'check_interval_ms', 'invalidation', *(keys if depends else ()))},
                'require_outside_at_t0': outside}
    if strict_mode:
        decision_obj = dict((proof.get('decision') or {}).get('judgment') or {})
        contract_arg = proof.get('contracts') or proof.get('contract')
        check_res = review.validate_initial_reply_contract(
            {'raw_text': proof['raw'], 'decision': decision_obj, 'condition_plan': out_plan},
            contract_arg,
            strict_clock_5m=True,
            check_excerpt_completeness=True,
            require_outside_at_t0=True,
        )
        if check_res.get('status') != 'PASSED':
            raise Blocked('condition_contract_validation_failed')
    return out_plan


def condition_at_t0(proof, plan, snapshot, locked_at):
    plan = condition_plan(proof, plan)
    is_candidate = _is_candidate_proof(proof)
    strict_mode = bool(is_candidate or proof.get('strict_contract'))
    expiry = proof['contract']['expiry_ms']
    first = (locked_at // dossier.MINUTE + 1) * dossier.MINUTE
    invalidation = plan['invalidation']
    index = snapshot['market']['reference_price_usd']
    if strict_mode:
        if not dossier._is_strictly_outside(index, proof['contract']):
            raise Blocked('t0_outside_condition_no_longer_holds')
    elif plan['require_outside_at_t0'] and not dossier._is_strictly_outside(index, proof['contract']):
        raise Blocked('t0_outside_condition_no_longer_holds')
    frozen = dict(contract=contract_fields(proof['contract']), entry_at_ms=locked_at,
                  entry_index_usd=index,
                  historical_facts=plan['historical_facts'], accepted_conditions=plan['accepted_conditions'],
                  invalidation=invalidation, invalidation_meaning=dossier.condition.RULES[invalidation],
                  check_clock=dict(first_at_ms=first, interval_ms=plan['check_interval_ms']),
                  sampling=dossier.condition.SAMPLING, confirmation_excerpt=plan['confirmation_excerpt'])
    if strict_mode:
        frozen['require_outside_at_t0'] = True
        frozen['mechanical_contract_version'] = getattr(dossier.condition, 'MECHANICAL_CONTRACT_VERSION', 'p2_strict@1')
        if proof.get('prompt_version'):
            frozen['prompt_version'] = proof['prompt_version']
    if proof['decision']['judgment']['depends_on_ard']:
        s = proof['source']
        p0_profile = getattr(dossier.player, 'INPUT_PROFILE_P0_STRICT', 'p0_strict@1')
        if proof.get('input_profile') == p0_profile:
            facts = dossier.player.ard_facts_for_evidence(
                s['history'],
                s['index_path'],
                expiry_ms=expiry,
                cutoff_ms=min(c['read_at_ms'] for c in s['cards']),
                band_snapshot=s.get('band_snapshot'),
                input_profile=p0_profile,
            )
        else:
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
    # v1.3 originally froze a two-minute handoff. Replay the recorded limit
    # only when it exactly matches that policy; never extend an old request.
    legacy_deadline = min(proof['deadline_ms'], proof['origin']['decided_at_ms'] + 120_000)
    if request['deadline_ms'] == legacy_deadline:
        proof['deadline_ms'] = legacy_deadline
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
                and meta['settlement_state'] == 'settled'
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
            with opener.open(request, timeout=30) as response:
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


def _normalize_usage(raw_usage):
    if not isinstance(raw_usage, dict):
        return None
    out = {}
    for k in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens'):
        v = raw_usage.get(k)
        if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
            out[k] = v
    if not out:
        return None
    if 'total_tokens' not in out and 'prompt_tokens' in out and 'completion_tokens' in out:
        out['total_tokens'] = out['prompt_tokens'] + out['completion_tokens']
    return out


def _source_entry_context(hand, *, side=None, expiry_ms=None):
    """Read-only extraction of L3.expiry_walls expected_move_usd and entry_pin_snapshot from source.json."""
    src = optional(hand / 'evidence' / 'source.json')
    if not isinstance(src, dict):
        return {'expected_move_usd': None, 'entry_pin_snapshot': None}
    cases = src.get('cases') if isinstance(src.get('cases'), list) else []
    ordered_cases = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        c_side = (dossier.player.values(case, 'L1.contract') or {}).get('side')
        if side and c_side == side:
            ordered_cases.insert(0, case)
        else:
            ordered_cases.append(case)
    expected_move = None
    entry_pin = None
    history = src.get('history') if isinstance(src.get('history'), list) else []
    default_source = dossier.archive.expiry_structure.PIN_SOURCE
    default_method = dossier.archive.expiry_structure.PIN_METHOD

    for case in ordered_cases:
        try:
            ew_entry = dossier.player.entry(case, 'L3.expiry_walls')
        except Exception:
            continue
        if not isinstance(ew_entry, dict):
            continue
        ew = ew_entry.get('values') if isinstance(ew_entry.get('values'), dict) else {}
        ew_exp = ew.get('expiry_ms')
        if expiry_ms is not None and ew_exp is not None and ew_exp != expiry_ms:
            continue
        if expected_move is None:
            em_val = dossier.player.number(ew.get('expected_move_usd'))
            if em_val is not None and em_val > 0:
                expected_move = float(em_val)
        if entry_pin is None and not ew.get('stale'):
            walls = ew.get('walls') if isinstance(ew.get('walls'), dict) else {}
            pin_val = dossier.player.number(walls.get('pin'))
            pin_status = ew.get('pin_status')
            if pin_val is not None and pin_val > 0 and pin_status in (None, 'ok'):
                at_ms = ew.get('computed_at_ms') or ew_entry.get('asof_ms') or (case.get('build') or {}).get('built_at_ms')
                hist_match = next(
                    (
                        r for r in reversed(history)
                        if isinstance(r, dict)
                        and (expiry_ms is None or r.get('expiry_ms') == expiry_ms)
                        and r.get('pin_status') in (None, 'ok')
                        and (not isinstance(at_ms, int) or not isinstance(r.get('at_ms'), int) or r['at_ms'] <= at_ms)
                    ),
                    None,
                )
                pin_method = ew.get('pin_method') or (hist_match or {}).get('pin_method') or default_method
                pin_source = ew.get('pin_source') or (hist_match or {}).get('pin_source') or default_source
                entry_pin = {
                    'expiry_ms': expiry_ms if expiry_ms is not None else ew_exp,
                    'pin': float(pin_val),
                    'at_ms': at_ms if isinstance(at_ms, int) else None,
                    'ready_at_ms': ew_entry.get('asof_ms') if isinstance(ew_entry.get('asof_ms'), int) else at_ms,
                    'pin_method': pin_method,
                    'pin_source': pin_source,
                    'pin_status': pin_status or 'ok',
                    'source': 'shadow_dossier.evidence.source.L3.expiry_walls',
                }
        if expected_move is not None and entry_pin is not None:
            break
    return {'expected_move_usd': expected_move, 'entry_pin_snapshot': entry_pin}


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
    source_ctx = _source_entry_context(
        hand, side=proof['contract'].get('side'), expiry_ms=proof['contract'].get('expiry_ms')
    )
    ep_meta_path = episode / 'episode.json'
    ep_meta = optional(ep_meta_path)
    if isinstance(ep_meta, dict):
        changed = False
        if source_ctx.get('entry_pin_snapshot') and not ep_meta.get('entry_pin_snapshot'):
            ep_meta['entry_pin_snapshot'] = source_ctx['entry_pin_snapshot']
            changed = True
        if source_ctx.get('expected_move_usd') is not None and ep_meta.get('entry_expected_move_usd') is None:
            ep_meta['entry_expected_move_usd'] = source_ctx['expected_move_usd']
            changed = True
        if changed:
            dossier.write(ep_meta_path, dossier.encode(ep_meta), replace=True)
    link = dict(state=result['state'], origin=origin, t0=t0, recorded_at_ms=d.clock(),
                t0_sha256=dossier.sha(t0_files['point.json']), execution_allowed=False)
    dossier.write(root / 'link.json', dossier.encode(link))
    return link


_RETRYABLE_LOCK_ERRORS = {
    'tracking_quote_stale_or_unsynced', 'tracking_snapshot_rotated',
    'exact_current_contract_not_found', 'quote_held_not_current', 'lock_snapshot_not_retained',
}


def _link_blocked(root):
    blocked = optional(root / 'final_block.json') or optional(root / 'blocked.json')
    return blocked if blocked and blocked.get('reason') not in _RETRYABLE_LOCK_ERRORS else None


def _link_with_fresh_quote(d, hand, service, plan=None):
    root = hand / 'tracking'
    pending = optional(root / 'request.json')
    if not pending and plan and not (root / 'link.json').exists() and not _link_blocked(root):
        try:
            expired = d.clock() >= accepted(hand)['deadline_ms']
        except Blocked:
            expired = True
        if expired:
            return link(d, hand, service, plan)
    if pending and d.clock() >= pending['deadline_ms']:
        return link(d, hand, service, plan)
    if not (optional(root / 'link.json') or _link_blocked(root)):
        try:
            service('refresh_quote', dict(dossier_id=hand.parent.parent.name, hand_id=hand.name))
        except (Blocked, OSError, ValueError, KeyError, TypeError):
            pass
    res = link(d, hand, service, plan)
    if res.get('state') == 'LINK_UNVERIFIED' and not (optional(root / 'link.json') or _link_blocked(root)):
        time.sleep(0.5)
        try:
            service('refresh_quote', dict(dossier_id=hand.parent.parent.name, hand_id=hand.name))
        except (Blocked, OSError, ValueError, KeyError, TypeError):
            pass
        res = link(d, hand, service, plan)
    return res


def link(d, hand, service, plan=None):
    root = hand / 'tracking'
    saved = optional(root / 'link.json') or _link_blocked(root)
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
            with _short_writer(d):
                if exc.code == 'shadow_handoff_expired' and not (root / 'final_block.json').exists():
                    dossier.write(root / 'final_block.json', dossier.encode(dict(state='LOCK_BLOCKED', reason=exc.code, recorded_at_ms=d.clock())))
                d.note(hand, 'LOCK_BLOCKED', exc.code)
            return dict(state='LOCK_BLOCKED', reason=exc.code)
        request = dict(state='SHADOW_LOCK_REQUESTED', accepted_at_ms=proof['origin']['decided_at_ms'],
                       requested_at_ms=issued, deadline_ms=proof['deadline_ms'], origin=proof['origin'],
                       condition=frozen_plan)
        with _short_writer(d):
            if not (root / 'request.json').exists():
                dossier.write(root / 'request.json', dossier.encode(request))
                d.note(hand, 'AI_ACCEPTED', proof['origin']['decision_id'])
    proof, request = request_proof(hand)
    action = 'lock' if d.clock() < proof['deadline_ms'] else 'recover'
    try:
        result = service(action, dict(dossier_id=hand.parent.parent.name, hand_id=hand.name))
        reason = 'native_commit_unconfirmed'
    except Blocked as exc:
        if action == 'lock' and exc.code not in _RETRYABLE_LOCK_ERRORS:
            blocked = dict(state='LOCK_BLOCKED', reason=exc.code, recorded_at_ms=d.clock())
            with _short_writer(d):
                existing = optional(root / 'link.json') or _link_blocked(root)
                if existing:
                    return existing
                receipt = 'final_block.json' if (root / 'blocked.json').exists() else 'blocked.json'
                dossier.write(root / receipt, dossier.encode(blocked))
                d.note(hand, 'LOCK_BLOCKED', exc.code)
            return blocked
        reason = exc.code
    except (OSError, ValueError, KeyError, TypeError) as exc:
        reason = type(exc).__name__
    else:
        if result['state'] in {'SHADOW_LOCK_CREATED', 'ALREADY_LINKED'}:
            try:
                with _short_writer(d):
                    return optional(root / 'link.json') or save_link(d, hand, proof, request, result)
            except (OSError, ValueError, KeyError, TypeError, store.LockedCandidateError) as exc:
                reason = exc.code if isinstance(exc, Blocked) else type(exc).__name__
    with _short_writer(d):
        d.note(hand, 'LINK_UNVERIFIED', reason)
    return dict(state='LINK_UNVERIFIED', reason=reason)


def _merge_index_points(archive_root, expiry_ms, at, *, public_fn=None):
    by_ts = {}
    if archive_root is not None:
        root = Path(archive_root)
        for day_ms in (expiry_ms - dossier.DAY, expiry_ms):
            p = root / 'paths' / f'{dossier.utc(day_ms).date().isoformat()}.json.gz'
            if p.is_file():
                try:
                    import gzip
                    data = json.loads(gzip.decompress(p.read_bytes()))
                    if (data.get('schema') != 'index_path@0.1' or data.get('index_name') != 'btc_usd'
                            or data.get('expiry_ms') != day_ms):
                        continue
                    for t, v in data.get('points') or ():
                        if isinstance(t, int) and t <= min(at, expiry_ms) and dossier.player.number(v) and v > 0:
                            by_ts[t] = float(v)
                except Exception:
                    pass
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


def _episode_ard_state(frozen, episode, archive_root, index_path, tick, *, entry_pin_snapshot=None):
    """Build current ARD facts + strict same-measure comparison at a check_clock tick.

    Supports both `depends_on_ard=True` (`frozen['ard_basis']`) and read-only
    same-measure Pin comparison via `entry_pin_snapshot` when `depends_on_ard=False`.
    """
    if archive_root is None:
        return None
    expiry = frozen['contract']['expiry_ms']
    has_ard_basis = bool(frozen.get('ard_basis'))
    ep_meta = optional(episode / 'episode.json') if episode is not None else None
    ev_src = None
    if isinstance(ep_meta, dict) and ep_meta.get('hand_id') and episode is not None:
        hand_dir = episode.parent.parent / 'hands' / dossier.key(ep_meta['hand_id'])
        ev_src = optional(hand_dir / 'evidence' / 'source.json')
    else:
        hand_dir = None

    pin_snap = entry_pin_snapshot
    if not has_ard_basis:
        if not isinstance(pin_snap, dict) and isinstance(ep_meta, dict):
            pin_snap = ep_meta.get('entry_pin_snapshot')
        if not isinstance(pin_snap, dict) and hand_dir is not None:
            pin_snap = _source_entry_context(
                hand_dir, side=(frozen.get('contract') or {}).get('side'), expiry_ms=expiry
            ).get('entry_pin_snapshot')
        if not isinstance(pin_snap, dict):
            return None
        snap_exp = pin_snap.get('expiry_ms')
        snap_pin = dossier.player.number(pin_snap.get('pin'))
        snap_method = pin_snap.get('pin_method')
        snap_source = pin_snap.get('pin_source')
        if (snap_pin is None or snap_pin <= 0 or not snap_method or not snap_source
                or (snap_exp is not None and snap_exp != expiry)):
            return None

    struct_path = Path(archive_root) / 'structure' / f'{dossier.utc(expiry).date().isoformat()}.jsonl'
    if not struct_path.is_file():
        return None
    old_start = (frozen['ard_basis'].get('facts') or {}).get('start_ms') if has_ard_basis else None
    band_snap = ev_src.get('band_snapshot') if isinstance(ev_src, dict) else None
    if band_snap is None:
        if isinstance(old_start, int):
            band_cutoff = old_start + 4 * 3_600_000
        elif isinstance(frozen.get('entry_at_ms'), int):
            band_cutoff = frozen['entry_at_ms']
        else:
            band_cutoff = tick
        band_snap = dossier._resolve_band_snapshot(archive_root, band_cutoff)
    if isinstance(old_start, int) and tick > old_start:
        win_ms = tick - old_start
    elif isinstance(frozen.get('entry_at_ms'), int) and tick > frozen['entry_at_ms']:
        win_ms = max(4 * 3_600_000, tick - frozen['entry_at_ms'])
    else:
        win_ms = 4 * 3_600_000
    p0_profile = getattr(dossier.player, 'INPUT_PROFILE_P0_STRICT', 'p0_strict@1')
    hand_ident = optional(hand_dir / 'evidence' / 'identity.json') if hand_dir is not None else None
    strict_ard_asof = bool(
        (isinstance(hand_ident, dict) and hand_ident.get('input_profile') == p0_profile)
        or frozen.get('mechanical_contract_version') == getattr(dossier.condition, 'MECHANICAL_CONTRACT_VERSION', 'p2_strict@1')
    )
    history = [dossier._normalize_legacy_structure_row(r) for r in dossier.rows(struct_path)
               if r.get('expiry_ms') == expiry and isinstance(r.get('at_ms'), int) and r['at_ms'] <= tick]
    try:
        current = dossier.player.ard.analyze(history, index_path, expiry_ms=expiry, cutoff_ms=tick,
                                            band_snapshot=band_snap, window_ms=win_ms, strict_asof=strict_ard_asof)
        pin_status = next((r.get('pin_status') for r in reversed(history)
                           if r.get('available_at_ms') is not None and r['available_at_ms'] <= tick and r.get('pin_status')), None)
        if has_ard_basis:
            comparison = dossier.condition.compare_ard_basis(episode / 'condition.json', current, tick)
            comparison['same_measure'] = comparison.get('new_neighborhood_events') is not None
            if comparison.get('old_pin') is None and isinstance(comparison.get('frozen_anchor'), dict):
                comparison['old_pin'] = comparison['frozen_anchor'].get('pin')
            if comparison.get('new_pin') is None and isinstance(comparison.get('current_anchor'), dict):
                comparison['new_pin'] = comparison['current_anchor'].get('pin')
            if comparison.get('check_at_ms') is None:
                comparison['check_at_ms'] = tick
            if pin_status:
                comparison['pin_status'] = pin_status
            return dict(facts=current, comparison=comparison)

        latest = current['observations'][-1] if current.get('observations') else None
        price = current.get('last')
        old_pin = float(snap_pin)
        new_pin = float(latest['pin']) if (latest and latest.get('pin') is not None and pin_status in (None, 'ok')) else None
        comparable = bool(
            snap_method and snap_source and latest
            and (snap_method, snap_source) == (latest.get('method'), latest.get('source'))
        )
        same_measure = bool(
            comparable
            and current.get('method_comparable')
            and new_pin is not None
            and pin_status in (None, 'ok')
        )
        fresh = bool(price and price.get('t') == tick and latest and price.get('pin') == latest.get('pin'))
        pin_recent = bool(latest and latest.get('pin') is not None and isinstance(latest.get('at'), int)
                          and tick - latest['at'] <= dossier.archive.expiry_structure.GAP_MS)
        sample_avail = bool(fresh or (new_pin is not None and pin_recent and not price))
        pin_changed = (new_pin != old_pin) if (sample_avail and same_measure and new_pin is not None) else None
        band_half = ((current.get('band') or {}).get('half_width_usd')
                     if isinstance(current.get('band'), dict) else None)
        comparison = dict(
            mode='READ_ONLY_ENTRY_PIN',
            frozen_anchor=dict(
                pin=old_pin,
                at=pin_snap.get('at_ms'),
                ready=pin_snap.get('ready_at_ms') or pin_snap.get('at_ms'),
                method=snap_method,
                source=snap_source,
            ),
            current_anchor=latest,
            old_pin=old_pin,
            new_pin=new_pin,
            band_half=band_half,
            check_at_ms=tick,
            current_sample_available=sample_avail,
            pin_price_changed=pin_changed,
            same_measure=same_measure,
            pin_status=pin_status or ('ok' if new_pin is not None else 'unavailable'),
            source_method_comparable=comparable,
            new_neighborhood_events=None,
            note='入场未依赖ARD；以入场L3有效Pin作同口径只读对比，不事后新增ARD承担条件或改写原冻结规则。',
        )
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
                     ard=(ard_state['facts'] if frozen.get('ard_basis') and ard_state and ard_state['comparison'].get('same_measure') else None))
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
    default_timeout = 360 if ('pro' in str(model).lower() or 'reasoner' in str(model).lower()) else 300
    timeout_s = float(cfg.get('timeout_s') or os.environ.get('LLM_TIMEOUT_SEC') or default_timeout)
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


def _record_revoke_shadow_action(d, episode, frozen, quote, at, *, review_id):
    """Record SIMULATED_ORDER(BUY_BACK_SHORT) from real short_book asks, else REVOKE_RECORDED_NO_QUOTE."""
    c = frozen['contract']
    quote = quote or {
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
        ev_id = dossier.identity('sim_buyback', review_id)
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
    ev_id = dossier.identity('revoke_no_quote', review_id)
    return d.event(episode, ev_id, 'SHADOW_ACTION', body, observed=at, available=at)


def _is_verified_revoke(record):
    """Return True if a review decision or AI_REVOKE event represents a verified shadow revoke."""
    if not isinstance(record, dict):
        return False
    body = record.get('body') if isinstance(record.get('body'), dict) else record
    is_revoke = (record.get('kind') == 'AI_REVOKE') or (body.get('verdict') == 'REVOKE')
    if not is_revoke:
        return False
    if (body.get('protocol_status') == 'DISCRETIONARY_REVOKE_UNVERIFIED'
            or body.get('shadow_action') == 'REVOKE_UNVERIFIED_ATTRIBUTION'
            or body.get('discretionary_revoke_attribution') == 'UNVERIFIED'):
        return False
    return True


def _record_review_event(d, episode, decision):
    review_id = decision['review_id']
    event_id = dossier.identity('ai_rev', review_id)
    previous = optional(episode / 'events' / f'{event_id}.json')
    if previous is not None:
        return previous
    verdict = decision['verdict']
    kind = 'AI_HOLD' if verdict == 'HOLD' else 'AI_REVOKE' if verdict == 'REVOKE' else 'AI_HOLD_UNKNOWN'
    body = {k: decision.get(k) for k in (
        'review_id', 'verdict', 'ai_management_verdict', 'mechanical_condition_state',
        'protocol_status', 'shadow_action', 'trigger_categories', 'affected_conditions',
        'discretionary_mapped_condition_ids', 'discretionary_revoke_attribution',
        'condition_id_mapping_status', 'public_reply', 'model', 'reasoning_effort',
        'usage', 'input_sha256', 'raw_sha256')}
    if decision.get('review_channel') is not None:
        body['review_channel'] = decision['review_channel']
    if decision.get('new_qualified_data_present') is not None:
        body['new_qualified_data_present'] = decision['new_qualified_data_present']
    body['triggers'] = _compact_triggers(decision.get('triggers'), keys=('category', 'code', 'summary_cn', 'observed_at_ms'))
    decided = decision['decided_at_ms']
    return d.event(episode, event_id, kind, body, observed=decision['requested_at_ms'], available=decided, decided=decided)


def _resume_revoke_action(d, episode, frozen, decision):
    rev_id = dossier.key(decision['review_id'])
    for event_file in (episode / 'events').glob('*.json'):
        event = load(event_file)
        if (event['kind'] in {'SIMULATED_ORDER', 'SHADOW_ACTION'}
                and event['body'].get('review_id') == rev_id):
            return event
    rev_dir = episode / 'reviews' / rev_id
    if decision.get('requested_at_ms') is not None and decision.get('decided_at_ms') is not None:
        _record_review_event(d, episode, decision)
    saved = optional(rev_dir / 'revoke_quote.json') or {}
    at = saved.get('captured_at_ms') or decision['decided_at_ms']
    return _record_revoke_shadow_action(d, episode, frozen, saved.get('quote'), at, review_id=rev_id)


def _load_prior_reviews_and_context(episode):
    """Load prior valid hold reviews chronologically and build `last_review` incremental context."""
    items = []
    if episode is not None and (episode / 'reviews').is_dir():
        for folder in (episode / 'reviews').glob('rev_*'):
            dec = optional(folder / 'decision.json')
            if not isinstance(dec, dict) or dec.get('status') not in (None, 'VALID'):
                continue
            snap = optional(folder / 'review_snapshot.json')
            snap_dict = snap if isinstance(snap, dict) else {}
            merged = dict(snap_dict)
            merged.update(dec)
            for key in ('spot_usd', 'short_distance_pct', 'expected_move_usd', 'expected_move_ratio',
                        'flow_asof_ms', 'flow_direction', 'flow_cvd_4h', 'flow_cvd_12h',
                        'flow_momentum_4h_pct', 'flow_momentum_12h_pct', 'pin',
                        'evidence_asof_ms', 'cutoff_at_ms'):
                if merged.get(key) is None and snap_dict.get(key) is not None:
                    merged[key] = snap_dict[key]
            sort_ts = dec.get('decided_at_ms') or dec.get('requested_at_ms') or 0
            items.append((sort_ts, str(dec.get('review_id') or folder.name), merged, snap_dict))
    items.sort(key=lambda row: (row[0], row[1]))
    prior_reviews = [{**row[2], 'evidence_snapshot': row[3]} if row[3] else dict(row[2]) for row in items]
    if not items:
        return prior_reviews, None
    _, _, latest_dec, latest_snap = items[-1]
    last_review = dict(
        review_id=latest_dec.get('review_id'),
        reviewed_at_ms=latest_dec.get('decided_at_ms') or latest_dec.get('requested_at_ms'),
        evidence_asof_ms=latest_snap.get('evidence_asof_ms') or latest_snap.get('cutoff_at_ms'),
        review_count=len(prior_reviews),
        verdict=latest_dec.get('verdict'),
        public_reply=latest_dec.get('public_reply'),
        key_facts=list(latest_dec.get('key_facts') or []),
        spot_usd=latest_snap.get('spot_usd'),
        short_distance_pct=latest_snap.get('short_distance_pct'),
        expected_move_usd=latest_snap.get('expected_move_usd'),
        expected_move_ratio=latest_snap.get('expected_move_ratio'),
        flow_asof_ms=latest_snap.get('flow_asof_ms') if 'flow_window_basis' in latest_snap else None,
        flow_window_basis=latest_snap.get('flow_window_basis'),
        flow_direction=latest_snap.get('flow_direction'),
        flow_cvd_4h=latest_snap.get('flow_cvd_4h'),
        flow_cvd_12h=latest_snap.get('flow_cvd_12h'),
        flow_momentum_4h_pct=latest_snap.get('flow_momentum_4h_pct'),
        flow_momentum_12h_pct=latest_snap.get('flow_momentum_12h_pct'),
        pin=latest_snap.get('pin'),
        mechanical_condition_state=latest_snap.get('mechanical_condition_state') or latest_dec.get('mechanical_condition_state'),
    )
    return prior_reviews, last_review


def _normalize_skip_info(raw_skip, at):
    if isinstance(raw_skip, dict):
        reason = str(raw_skip.get('reason') or raw_skip.get('code') or '').strip().upper()
        if not reason or reason in {'NO_TRIGGER', 'NONE'}:
            return None
        out = dict(raw_skip)
        out['reason'] = reason
        if not out.get('summary_cn') and out.get('reason_cn'):
            out['summary_cn'] = out['reason_cn']
        if not isinstance(out.get('at_ms'), int):
            out['at_ms'] = at
        return out
    if isinstance(raw_skip, str):
        reason = raw_skip.strip().upper()
        if not reason or reason in {'NO_TRIGGER', 'NONE'}:
            return None
        return dict(reason=reason, at_ms=at)
    return None


_ROUTINE_IDLE_SKIP_CODES = frozenset({'NO_TRIGGER', 'NONE', 'COALESCED'})
_MEANINGFUL_SKIP_CODES = frozenset({
    'COOLDOWN', 'BELOW_MATERIALITY', 'DUPLICATE_EVENT', 'BUDGET_DEFERRED',
    'DATA_UNVERIFIED', 'NO_NEW_EVIDENCE', 'BACKOFF',
})


def _update_skip_and_telemetry(prior_state, next_state, triggers, action, at):
    """Preserve or accumulate meaningful skip counts, recent skips, and telemetry counters on trigger_state."""
    prior_pending = bool(isinstance(prior_state, dict) and prior_state.get('pending'))
    prior_revoked = bool(isinstance(prior_state, dict) and prior_state.get('revoked'))
    skip_info = _normalize_skip_info(next_state.get('last_skip'), at)
    prior_skip = _normalize_skip_info(prior_state.get('last_skip'), at) if isinstance(prior_state, dict) else None
    idle_or_debounce = bool(
        skip_info is not None
        and (
            skip_info['reason'] in _ROUTINE_IDLE_SKIP_CODES
            or (
                skip_info['reason'] == 'NO_NEW_EVIDENCE'
                and (not triggers or prior_revoked)
                and not prior_pending
            )
        )
    )
    if idle_or_debounce:
        skip_info = None
        if prior_skip is not None:
            next_state['last_skip'] = prior_state.get('last_skip')
        else:
            next_state.pop('last_skip', None)

    is_new_skip = bool(
        skip_info is not None
        and (
            prior_skip is None
            or skip_info.get('at_ms') == at
            or skip_info.get('reason') != prior_skip.get('reason')
            or skip_info.get('signature') != prior_skip.get('signature')
        )
    )
    raw_prior_counts = prior_state.get('skip_counts') if isinstance(prior_state, dict) else None
    prior_counts = ({str(k): int(v) for k, v in raw_prior_counts.items()
                     if str(k) not in {'NO_TRIGGER', 'NONE', 'COALESCED', ''} and isinstance(v, int) and v > 0}
                    if isinstance(raw_prior_counts, dict) else {})
    if idle_or_debounce:
        skip_counts = prior_counts
    elif isinstance(next_state.get('skip_counts'), dict):
        skip_counts = {str(k): int(v) for k, v in next_state['skip_counts'].items()
                       if str(k) not in {'NO_TRIGGER', 'NONE', 'COALESCED', ''} and isinstance(v, int) and v > 0}
    else:
        skip_counts = dict(prior_counts)
        if is_new_skip:
            r = skip_info['reason']
            skip_counts[r] = int(skip_counts.get(r) or 0) + 1
    next_state['skip_counts'] = skip_counts

    if isinstance(next_state.get('recent_skips'), list):
        recent = [s for s in next_state['recent_skips']
                  if isinstance(s, dict) and str(s.get('reason') or s.get('code') or '').upper() not in {'NO_TRIGGER', 'NONE', 'COALESCED', ''}]
    else:
        raw_recent = prior_state.get('recent_skips') if isinstance(prior_state, dict) else None
        recent = ([dict(s) for s in raw_recent
                   if isinstance(s, dict) and str(s.get('reason') or s.get('code') or '').upper() not in {'NO_TRIGGER', 'NONE', 'COALESCED', ''}]
                  if isinstance(raw_recent, list) else [])
        if is_new_skip:
            compact = {k: skip_info.get(k) for k in ('reason', 'category', 'code', 'signature', 'at_ms', 'summary_cn', 'detail')
                       if skip_info.get(k) is not None}
            recent.append(compact)
    next_state['recent_skips'] = recent[-20:]

    raw_tel = next_state.get('telemetry') if isinstance(next_state.get('telemetry'), dict) else (
        prior_state.get('telemetry') if isinstance(prior_state, dict) and isinstance(prior_state.get('telemetry'), dict) else {}
    )
    tel = dict(raw_tel)
    tel['eval_count'] = int(tel.get('eval_count') or 0) + 1
    tel['candidate_triggers_seen'] = int(tel.get('candidate_triggers_seen') or 0) + len(triggers or ())
    if triggers and (not action or not action.get('should_call')) and next_state.get('pending'):
        tel['coalesced_debounce_count'] = int(tel.get('coalesced_debounce_count') or 0) + 1
    elif action and action.get('should_call') and len(action.get('triggers') or ()) > 1:
        tel['coalesced_debounce_count'] = int(tel.get('coalesced_debounce_count') or 0) + (len(action['triggers']) - 1)
    next_state['telemetry'] = tel
    return skip_info if (is_new_skip and skip_info['reason'] in _MEANINGFUL_SKIP_CODES) else None


def evaluate_hold_management(d, hand, exported, *, archive_root=None, index_path=None, service=None, llm_config=None,
                           debounce_ms=review.DEFAULT_DEBOUNCE_MS, cooldown_ms=review.DEFAULT_COOLDOWN_MS,
                           soft_budget=2):
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
    ep_meta = optional(episode / 'episode.json') or {}
    source_ctx = _source_entry_context(
        hand, side=frozen['contract'].get('side'), expiry_ms=frozen['contract'].get('expiry_ms')
    )
    entry_pin_snapshot = ep_meta.get('entry_pin_snapshot') if isinstance(ep_meta, dict) else None
    if not isinstance(entry_pin_snapshot, dict):
        entry_pin_snapshot = source_ctx.get('entry_pin_snapshot')
    fallback_em = (
        ep_meta.get('entry_expected_move_usd')
        if isinstance(ep_meta, dict) and dossier.player.number(ep_meta.get('entry_expected_move_usd')) is not None
        else source_ctx.get('expected_move_usd')
    )

    ep_events = sorted((load(p) for p in (episode / 'events').glob('*.json')),
                       key=lambda e: (e['recorded_at_ms'], e['event_id']))
    t0_point = optional(root / 't0' / 'point.json')
    series_rows = []
    for row in exported.get('points') or ():
        metrics = row.get('metrics') if isinstance(row.get('metrics'), dict) else row
        if metrics.get('metrics_version') and 'flow_window_basis' not in metrics:
            metrics = {**metrics, 'flow_asof_ms': None}
            row = {**row, 'metrics': metrics} if 'metrics' in row else metrics
        series_rows.append(row)
    t0_metrics = (locked_compare.point_metrics(t0_point, t0_point) if t0_point
                  else (dict(series_rows[0]) if series_rows else {}))
    if t0_metrics.get('expected_move_usd') is None and fallback_em is not None:
        t0_metrics = {**t0_metrics, 'expected_move_usd': fallback_em}

    prior_reviews, last_review = _load_prior_reviews_and_context(episode)
    verified_prior_revoke = next((r for r in reversed(prior_reviews) if _is_verified_revoke(r)), None)
    if verified_prior_revoke is not None:
        with _short_writer(d):
            _resume_revoke_action(d, episode, frozen, verified_prior_revoke)
        return dict(action='ALREADY_REVOKED', review_id=verified_prior_revoke['review_id'])
    ep_index = index_path or _merge_index_points(archive_root, frozen['contract']['expiry_ms'], at)
    cc = frozen['check_clock']
    cap = min(at, frozen['contract']['expiry_ms'] - 1)
    ard_state = None
    if cap >= cc['first_at_ms']:
        ard_tick = cc['first_at_ms'] + ((cap - cc['first_at_ms']) // cc['interval_ms']) * cc['interval_ms']
        ard_state = _episode_ard_state(
            frozen, episode, archive_root, ep_index, ard_tick, entry_pin_snapshot=entry_pin_snapshot
        )
    ard_comparison = ard_state['comparison'] if ard_state else None
    triggers = _call_with_supported_kwargs(
        review.detect_hold_triggers,
        frozen_condition=frozen,
        side=frozen['contract']['side'],
        short_strike_usd=frozen['contract']['short_strike_usd'],
        entry_at_ms=frozen['entry_at_ms'],
        t0_metrics=t0_metrics,
        series_rows=series_rows,
        condition_events=ep_events,
        ard_comparison=ard_comparison,
        now_ms=at,
        last_review=last_review,
        entry_pin_snapshot=entry_pin_snapshot,
        expected_move_usd=t0_metrics.get('expected_move_usd'),
    )
    cfg = _llm_settings(llm_config)
    stale_inflight_ms = max(8 * dossier.MINUTE, int(cfg.get('timeout_s', 300) * 1000) + 60_000)
    state_path = episode / 'trigger_state.json'
    with _short_writer(d):
        prior_state = optional(state_path) or {}
        started = prior_state.get('inflight_started_at_ms')
        if prior_state.get('inflight') and isinstance(started, int) and prior_reviews:
            completed = prior_reviews[-1]
            if (completed.get('requested_at_ms') or 0) >= started and completed.get('decided_at_ms') is not None:
                _record_review_event(d, episode, completed)
                shadow_act = completed.get('shadow_action')
                proto_st = completed.get('protocol_status')
                rec_verified_revoke = (
                    (shadow_act in {'REVOKE_ALIGNED', 'REVOKE_DISCRETIONARY'})
                    or (shadow_act is None and proto_st != 'DISCRETIONARY_REVOKE_UNVERIFIED')
                )
                rec_verdict = completed['verdict'] if (rec_verified_revoke or completed['verdict'] != 'REVOKE') else None
                prior_state = review.complete_review_action(
                    prior_state, now_ms=completed['decided_at_ms'], success=True,
                    verdict=rec_verdict, cooldown_ms=cooldown_ms)
        next_state, action = _call_with_supported_kwargs(
            review.schedule_review_action,
            prior_state, triggers, now_ms=at, debounce_ms=debounce_ms,
            cooldown_ms=cooldown_ms, stale_inflight_ms=stale_inflight_ms,
            max_ordinary_reviews=soft_budget,
        )
        new_skip = _update_skip_and_telemetry(prior_state, next_state, triggers, action, at)
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
        if new_skip is not None and 'HOLD_REVIEW_SKIPPED' in getattr(dossier, 'EVENTS', set()):
            skip_sig = str(new_skip.get('signature') or new_skip.get('code') or new_skip['reason'])
            hour_bucket = at // 3_600_000
            skip_id = dossier.identity('rev_skip', hour_bucket, new_skip['reason'], skip_sig)
            if not (episode / 'events' / f'{dossier.key(skip_id)}.json').exists():
                skip_trigs = _compact_triggers(
                    triggers if isinstance(triggers, list) else [],
                    keys=('category', 'code', 'summary_cn', 'observed_at_ms'),
                )
                skip_cats = sorted({t.get('category') for t in skip_trigs if t.get('category')})
                skip_body = dict(
                    reason=new_skip['reason'],
                    signature=skip_sig,
                    trigger_categories=skip_cats,
                    triggers=skip_trigs,
                    summary_cn=new_skip.get('summary_cn') or new_skip.get('reason_cn') or new_skip.get('detail'),
                )
                skip_obs = new_skip.get('at_ms') if isinstance(new_skip.get('at_ms'), int) and new_skip['at_ms'] <= at else at
                d.event(episode, skip_id, 'HOLD_REVIEW_SKIPPED', skip_body, observed=skip_obs, available=at)
        if next_state.get('pending') and action is None:
            queued_cats = sorted({t.get('category') for t in next_state['pending'] if t.get('category')})
            q_body = dict(pending_since_ms=next_state.get('pending_since_ms'), trigger_categories=queued_cats,
                          triggers=[{k: t.get(k) for k in ('category', 'code', 'signature', 'observed_at_ms', 'summary_cn')}
                                    for t in next_state['pending']])
            q_id = dossier.identity('queued', next_state.get('pending_since_ms'), queued_cats)
            if not (episode / 'events' / f'{q_id}.json').exists():
                d.event(episode, q_id, 'HOLD_REVIEW_QUEUED', q_body, observed=at, available=at)
        if not action or not action.get('should_call'):
            out_idle = dict(triggers=triggers, action='QUEUED' if next_state.get('pending') else 'NO_TRIGGER',
                            pending=len(next_state.get('pending') or []))
            if new_skip is not None:
                out_idle['skip'] = new_skip
            return out_idle

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

        check_ev_list = [e for e in ep_events if str(e.get('kind', '')).startswith('CONDITION_')]
        cond_summary = _condition_check_summary(check_ev_list, asof_ms=at, check_clock=frozen['check_clock'],
                                                expiry_ms=frozen['contract']['expiry_ms'])
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

    has_m_trigger = bool(prog_triggered or any(isinstance(t, dict) and t.get('category') == 'A' for t in batch))
    has_c_trigger = bool(any(isinstance(t, dict) and t.get('category') in {'B', 'C', 'D'} for t in batch))
    if has_m_trigger and has_c_trigger:
        review_channel = 'MIXED'
    elif has_m_trigger:
        review_channel = 'MECHANICAL_M'
    elif has_c_trigger:
        review_channel = 'C_REVIEW_ONLY'
    else:
        review_channel = 'OBSERVE_ONLY'

    entry_ms = frozen['entry_at_ms']
    last_rev_ev_ms = (last_review or {}).get('evidence_asof_ms') or (last_review or {}).get('reviewed_at_ms')
    data_floor = max(entry_ms, last_rev_ev_ms) if isinstance(last_rev_ev_ms, int) else entry_ms
    has_new_tracker = any(
        isinstance(ts := review._row_ts(row), int)
        and data_floor < ts <= at
        and (
            dossier.player.number((row.get('metrics') if isinstance(row.get('metrics'), dict) else row).get('spot_usd')) is not None
            or dossier.player.number((row.get('metrics') if isinstance(row.get('metrics'), dict) else row).get('short_distance_pct')) is not None
        )
        for row in series_rows
    )
    chart_delay = (
        dossier.MINUTE
        if isinstance(ep_index, dict) and dossier.player._chart_semantics(ep_index) in dossier.player._CHART_DELAY_SEMANTICS
        else 0
    )
    has_new_index = any(
        isinstance(pt, (list, tuple)) and len(pt) == 2
        and isinstance(pt[0], int) and pt[0] > data_floor and pt[0] + chart_delay <= at
        and dossier.player.number(pt[1]) is not None and pt[1] > 0
        for pt in (ep_index.get('points') or () if isinstance(ep_index, dict) else ())
    )
    ard_new_obs = ard_comparison.get('newly_available_anchor_observations') if isinstance(ard_comparison, dict) else None
    ard_anchor_ready = (ard_comparison.get('current_anchor') or {}).get('ready') if isinstance(ard_comparison, dict) else None
    has_new_ard = bool(
        isinstance(ard_comparison, dict)
        and (
            (isinstance(ard_new_obs, list) and any(isinstance(r, dict) and isinstance(r.get('ready'), int) and data_floor < r['ready'] <= at for r in ard_new_obs))
            or (
                ard_comparison.get('same_measure') is True
                and ard_comparison.get('current_sample_available') is True
                and isinstance(ard_anchor_ready, int)
                and data_floor < ard_anchor_ready <= at
            )
        )
    )
    flow_floor_candidates = [t for t in (entry_ms, t0_metrics.get('flow_asof_ms'), (last_review or {}).get('flow_asof_ms')) if isinstance(t, int)]
    flow_floor = max(flow_floor_candidates) if flow_floor_candidates else entry_ms
    has_new_flow = bool(
        latest_m.get('flow_ready') is True
        and isinstance(latest_m.get('flow_asof_ms'), int)
        and flow_floor < latest_m['flow_asof_ms'] <= at
    )
    new_qualified_data_present = bool(has_new_tracker or has_new_index or has_new_ard or has_new_flow)

    authoritative_m = None
    if frozen.get('invalidation') in dossier.condition.RULES and hasattr(dossier.condition, 'canonical_confirmation_text'):
        try:
            authoritative_m = dossier.condition.canonical_confirmation_text(
                dict(frozen['contract']),
                frozen['invalidation'],
                (frozen.get('check_clock') or {}).get('interval_ms') or 300_000,
            )
        except ValueError:
            authoritative_m = None

    input_md = _call_with_supported_kwargs(
        review.build_hold_review_input,
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
            flow_direction=t0_metrics.get('flow_direction'),
            flow_asof_ms=t0_metrics.get('flow_asof_ms'),
            flow_cvd_4h=t0_metrics.get('flow_cvd_4h'),
            flow_cvd_12h=t0_metrics.get('flow_cvd_12h'),
            net_credit_after_fee_btc=((link_record.get('t0') or {}).get('economy') or {}).get('net_credit_after_entry_fee_btc'),
        ),
        post_entry_summary=dict(
            sample_count=len(series_rows),
            latest_ts_ms=review._row_ts(series_rows[-1]) if series_rows else None,
            latest_spot_usd=post_spots[-1] if post_spots else None,
            min_short_distance_pct=min(post_dists) if post_dists else None,
            latest_short_distance_pct=post_dists[-1] if post_dists else None,
            expected_move_usd=latest_m.get('expected_move_usd'),
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
        entry_pin_snapshot=entry_pin_snapshot,
        last_review_context=last_review,
        authoritative_m=authoritative_m,
        review_channel=review_channel,
        new_qualified_data_present=new_qualified_data_present,
    )
    req_ms = d.clock()
    input_bytes = input_md.encode('utf-8')
    packet_hash = dossier.sha(input_bytes)
    rev_id = dossier.identity('rev', req_ms, cats, packet_hash)
    hold_req_payload = review.build_hold_review_request_payload(
        input_md,
        model=cfg['model'],
        reasoning_effort=cfg.get('reasoning_effort'),
        endpoint=review._normalize_endpoint(cfg.get('endpoint')),
        packet_hash=packet_hash,
        requested_at_ms=req_ms,
        fidelity='EXACT',
    )
    call = episode / 'review_calls' / rev_id
    dossier.publish(call, {'input.md': input_bytes,
                           'request.json': dossier.encode(review.sanitize_model_request(hold_req_payload))})
    if 'AI_REVIEW_REQUESTED' in getattr(dossier, 'EVENTS', set()):
        with _short_writer(d):
            req_ev_id = dossier.identity('ai_req', rev_id)
            if not (episode / 'events' / f'{dossier.key(req_ev_id)}.json').exists():
                d.event(
                    episode, req_ev_id, 'AI_REVIEW_REQUESTED',
                    dict(review_id=rev_id, trigger_categories=cats, triggers=batch_triggers,
                         model=cfg['model'], reasoning_effort=cfg.get('reasoning_effort'),
                         input_sha256=packet_hash),
                    observed=req_ms, available=req_ms,
                )
    try:
        llm_res = _call_with_supported_kwargs(
            review.call_chat_llm,
            hold_req_payload['messages'], api_key=cfg['api_key'] or 'test-key', model=cfg['model'],
            endpoint=cfg.get('endpoint'), timeout_s=cfg.get('timeout_s', 300),
            retries=0,  # Hold review must never replay an ambiguous POST.
            reasoning_effort=cfg.get('reasoning_effort'),
            budget=cfg.get('budget'),
            budget_role='astra_player_hold_review',
            packet_hash=packet_hash,
            transport=cfg.get('transport'),
            on_response=lambda reply: _remember_reply(call, d, reply),
        )
        if llm_res.get('endpoint'):
            hold_req_payload['endpoint'] = llm_res['endpoint']
        raw_reply = llm_res['text']
        actual_model = llm_res.get('model') or cfg['model']
        effort = llm_res.get('reasoning_effort') or cfg.get('reasoning_effort')
        usage_dict = _normalize_usage(llm_res.get('usage'))
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
            else 'REVOKE_UNVERIFIED_ATTRIBUTION' if parsed['protocol_status'] == 'DISCRETIONARY_REVOKE_UNVERIFIED'
            else 'REVOKE_ALIGNED' if parsed['verdict'] == 'REVOKE'
            else 'HOLD_SHADOW_CONTINUE' if parsed['verdict'] == 'HOLD'
            else 'HOLD_UNKNOWN_OBSERVE'
        )
        verified_shadow_revoke = shadow_action in {'REVOKE_ALIGNED', 'REVOKE_DISCRETIONARY'}
        rev_decision = dict(
            review_id=rev_id,
            status='VALID',
            verdict=parsed['verdict'],
            ai_management_verdict=parsed['ai_management_verdict'],
            mechanical_condition_state=parsed['mechanical_condition_state'],
            protocol_status=parsed['protocol_status'],
            shadow_action=shadow_action,
            review_channel=review_channel,
            new_qualified_data_present=new_qualified_data_present,
            trigger_categories=cats,
            triggers=batch,
            reason=action['reason'],
            affected_conditions=parsed['affected_conditions'],
            mapped_condition_ids=parsed['mapped_condition_ids'],
            unmapped_condition_ids=parsed['unmapped_condition_ids'],
            discretionary_mapped_condition_ids=parsed.get('discretionary_mapped_condition_ids', []),
            discretionary_revoke_attribution=parsed.get('discretionary_revoke_attribution', 'NOT_APPLICABLE'),
            condition_id_mapping_status=parsed['condition_id_mapping_status'],
            public_reply=parsed['public_reply'],
            key_facts=parsed['key_facts'],
            notes=parsed.get('notes') or [],
            program_triggered=prog_triggered,
            model=actual_model,
            reasoning_effort=effort,
            usage=usage_dict,
            requested_at_ms=req_ms,
            decided_at_ms=fin_ms,
            input_sha256=packet_hash,
            raw_sha256=dossier.sha(raw_bytes),
        )
        attempt_rec = dict(
            review_id=rev_id, status='VALID', model=actual_model, reasoning_effort=effort,
            requested_at_ms=req_ms, decided_at_ms=fin_ms,
            usage=usage_dict, http_status=llm_res.get('http_status'),
        )
        snap_spot = latest_m.get('spot_usd') if dossier.player.number(latest_m.get('spot_usd')) is not None else (post_spots[-1] if post_spots else None)
        snap_dist = latest_m.get('short_distance_pct') if dossier.player.number(latest_m.get('short_distance_pct')) is not None else (post_dists[-1] if post_dists else None)
        snap_em = latest_m.get('expected_move_usd') if dossier.player.number(latest_m.get('expected_move_usd')) is not None else t0_metrics.get('expected_move_usd')
        short_k = dossier.player.number(frozen['contract'].get('short_strike_usd'))
        snap_em_ratio = (
            round(abs(float(snap_spot) - float(short_k)) / float(snap_em), 6)
            if (dossier.player.number(snap_spot) is not None and short_k is not None
                and dossier.player.number(snap_em) is not None and float(snap_em) > 0)
            else None
        )
        snap_pin = (ard_comparison or {}).get('new_pin')
        if snap_pin is None:
            snap_pin = (ard_comparison or {}).get('old_pin')
        review_snapshot = dict(
            review_id=rev_id,
            reviewed_at_ms=fin_ms,
            evidence_asof_ms=latest_m.get('asof_ms') or fin_ms,
            cutoff_at_ms=latest_m.get('asof_ms') or fin_ms,
            verdict=parsed['verdict'],
            spot_usd=snap_spot,
            short_distance_pct=snap_dist,
            expected_move_usd=snap_em,
            expected_move_ratio=snap_em_ratio,
            flow_asof_ms=latest_m.get('flow_asof_ms'),
            flow_window_basis=latest_m.get('flow_window_basis'),
            flow_direction=latest_m.get('flow_direction'),
            flow_cvd_4h=latest_m.get('flow_cvd_4h'),
            flow_cvd_12h=latest_m.get('flow_cvd_12h'),
            flow_momentum_4h_pct=latest_m.get('flow_momentum_4h_pct'),
            flow_momentum_12h_pct=latest_m.get('flow_momentum_12h_pct'),
            pin=snap_pin,
            mechanical_condition_state=mech_state,
            trigger_categories=cats,
        )
        revoke_quote = {}
        if verified_shadow_revoke:
            quote_pt = latest_pt
            if service is not None and d.clock() < frozen['contract']['expiry_ms']:
                try:
                    q_res = service('quote', dict(dossier_id=hand.parent.parent.name, hand_id=hand.name))
                    if isinstance(q_res, dict) and q_res.get('latest_point') is not None:
                        quote_pt = q_res['latest_point']
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            revoke_at = d.clock()
            quote = (locked_compare.short_buyback_quote(quote_pt)
                     if quote_pt and revoke_at < frozen['contract']['expiry_ms'] else None)
            revoke_quote['revoke_quote.json'] = dossier.encode(dict(captured_at_ms=revoke_at, quote=quote))
        with _short_writer(d):
            fresh_state = optional(state_path) or next_state
            dossier.publish(episode / 'reviews' / rev_id, {
                'input.md': input_bytes,
                'request.json': dossier.encode(review.sanitize_model_request(hold_req_payload)),
                'response.raw': raw_bytes,
                'attempt.json': dossier.encode(attempt_rec),
                'decision.json': dossier.encode(rev_decision),
                'review_snapshot.json': dossier.encode(review_snapshot),
                **revoke_quote,
            })
            _record_review_event(d, episode, rev_decision)
            effective_verdict = parsed['verdict'] if verified_shadow_revoke or parsed['verdict'] != 'REVOKE' else None
            done_state = review.complete_review_action(fresh_state, now_ms=fin_ms, success=True, verdict=effective_verdict, cooldown_ms=cooldown_ms)
            dossier.write(state_path, dossier.encode(done_state), replace=True)
        result_out = dict(
            triggers=triggers, action='REVIEWED', review_id=rev_id, verdict=parsed['verdict'],
            mechanical_condition_state=parsed['mechanical_condition_state'],
            protocol_status=parsed['protocol_status'], shadow_action=shadow_action,
            review_channel=review_channel,
            new_qualified_data_present=new_qualified_data_present,
            usage=usage_dict,
        )
    except Exception as exc:
        if (episode / 'reviews' / rev_id / 'decision.json').is_file():
            return dict(triggers=triggers, action='RECOVERY_PENDING', review_id=rev_id, error=type(exc).__name__)
        fin_ms = d.clock()
        backoff_ms = 30 * 60_000 if isinstance(exc, signal_llm_review.DailyBudgetExceeded) else review.RETRY_BACKOFF_MS
        with _short_writer(d):
            saved = optional(call / 'reply.json')
            status = 'PARSE_FAILED' if saved is not None and not (episode / 'reviews' / rev_id).exists() else 'FAILED'
            usage = _normalize_usage((saved or {}).get('usage'))
            fresh_state = optional(state_path) or next_state
            done_state = review.complete_review_action(
                fresh_state, now_ms=fin_ms, success=False, cooldown_ms=cooldown_ms, retry_backoff_ms=backoff_ms,
            )
            dossier.write(state_path, dossier.encode(done_state), replace=True)
            err_id = dossier.identity('rev_fail', fin_ms, cats, type(exc).__name__)
            d.event(episode, err_id, 'HOLD_REVIEW_FAILED',
                    dict(review_id=rev_id, error=type(exc).__name__, detail=str(exc)[:180], trigger_categories=cats,
                         triggers=batch_triggers, model=cfg['model'], reasoning_effort=cfg.get('reasoning_effort'),
                         status=status, usage=usage, input_sha256=packet_hash),
                    observed=req_ms, available=fin_ms)
        return dict(triggers=triggers, action='FAILED', review_id=rev_id, error=str(exc))

    if _is_verified_revoke(rev_decision):
        try:
            with _short_writer(d):
                _resume_revoke_action(d, episode, frozen, rev_decision)
        except Exception as exc:
            result_out['shadow_action_error'] = f'{type(exc).__name__}: {str(exc)[:180]}'
    return result_out


def sync(d, hand, service, *, index_path=None, check_due=False, archive_root=None,
         run_review=False, llm_config=None, debounce_ms=review.DEFAULT_DEBOUNCE_MS, cooldown_ms=review.DEFAULT_COOLDOWN_MS,
         soft_budget=2):
    """Copy the native tracker evidence; optionally run due fixed-clock checks and event-triggered hold reviews."""
    root = hand / 'tracking'
    link_record = load(root / 'link.json')
    episode = hand.parent.parent / 'episodes' / link_record['origin']['dossier_episode_id']
    prior_reviews, _last_review = _load_prior_reviews_and_context(episode)
    verified_prior_revoke = next((r for r in reversed(prior_reviews) if _is_verified_revoke(r)), None)
    if verified_prior_revoke is not None:
        with _short_writer(d):
            _resume_revoke_action(d, episode, load(episode / 'condition.json'), verified_prior_revoke)
    terminal = optional(root / 'terminal.json')
    if terminal is not None:
        meta, _ = terminal_trajectory(hand)
        if meta['settlement_state'] == 'settled':
            return terminal
        retry = optional(root / 'settlement_retry.json') or {}
        if d.clock() < retry.get('next_at_ms', 0):
            return terminal
        with _short_writer(d):
            dossier.write(root / 'settlement_retry.json', dossier.encode(dict(next_at_ms=d.clock() + 10 * 60_000)), replace=True)
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
    ep_index = index_path
    if ep_index is None and check_due and terminal is None:
        ep_index = _merge_index_points(archive_root, ep['contract']['expiry_ms'], at)
    with _short_writer(d):
        if archived and (terminal is None or settlement_state == 'settled'):
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
        checks, check_error = (None, None) if terminal else _run_due_checks(
            d, episode, at, index_path=ep_index, check_due=check_due, archive_root=archive_root)
    review_result = None
    if run_review and not archived:
        review_result = evaluate_hold_management(
            d, hand, exported, archive_root=archive_root, index_path=ep_index,
            service=service, llm_config=llm_config, debounce_ms=debounce_ms, cooldown_ms=cooldown_ms,
            soft_budget=soft_budget)
    if archived and (settlement_state == 'settled' or (terminal is None and settlement_state == 'delivery_price_unavailable')):
        with _short_writer(d):
            current = optional(root / 'terminal.json')
            if current is None or (settlement_state == 'settled' and terminal_trajectory(hand)[0]['settlement_state'] != 'settled'):
                dossier.write(root / 'terminal.json', dossier.encode(dict(export_id=archive_id, full_series=completed)), replace=current is not None)
    out = dict(state=ep['status'], event_id=event['event_id'], full_series=completed, settlement=settlement_state)
    if checks is not None:
        out['checks'] = checks
    if check_error is not None:
        out['check_error'] = check_error
    if review_result is not None:
        out['hold_review'] = review_result
    return out


def _recover_condition_plan(hand, decision):
    att_id = decision.get('attempt_id') if isinstance(decision, dict) else None
    if att_id:
        plan = optional(hand / 'responses' / dossier.key(att_id) / 'condition_plan.json')
        if plan is not None:
            return plan
    saved_plan = optional(hand / 'tracking' / 'condition_plan.json')
    if isinstance(saved_plan, dict) and saved_plan.get('invalidation'):
        return saved_plan
    if att_id:
        att = optional(hand / 'responses' / att_id / 'attempt.json') or {}
        ann = att.get('annotation') if isinstance(att.get('annotation'), dict) else {}
        if ann.get('invalidation') in dossier.condition.RULES and isinstance(ann.get('check_interval_ms'), int):
            return {k: ann[k] for k in ('invalidation', 'check_interval_ms', 'historical_facts',
                                        'accepted_conditions', 'confirmation_excerpt', 'require_outside_at_t0')
                    if k in ann}
    return None


def auto_deal(d, plan, archive_root, service, *, llm_config=None, clock=dossier.now_ms):
    """Run scheduled initial deal -> AI decision -> shadow tracking link when Accept."""
    with ExitStack() as lease:
        try:
            lease.enter_context(dossier.writer(d.root / '.auto-deal'))
        except (BlockingIOError, PermissionError):
            return [dict(status='AUTO_IN_FLIGHT')]
        return _auto_deal(d, plan, archive_root, service, llm_config=llm_config)


def _remember_reply(folder, d, reply):
    dossier.write(folder / 'reply.json', dossier.encode({**reply, 'decided_at_ms': d.clock()}))


def _hand_contracts_pair(hand, req=None):
    src = optional(hand / 'evidence' / 'source.json')
    if isinstance(src, dict) and isinstance(src.get('cases'), list) and isinstance(src.get('cards'), list):
        pair = {}
        for case, card in zip(src['cases'], src['cards']):
            side = card.get('side')
            if side in {'put', 'call'}:
                try:
                    pair[side] = contract_fields(dossier.player.bind(case, card, side))
                except (ValueError, KeyError, TypeError):
                    pass
        if pair:
            return pair
    if isinstance(req, dict):
        meta_pair = (req.get('metadata') or {}).get('contract_pair')
        if meta_pair:
            return meta_pair
    return None


def _record_initial_reply(d, hand, call, req, *, result=None, error=None):
    saved = optional(call / 'reply.json')
    contracts_pair = _hand_contracts_pair(hand, req)
    if result is None and saved is not None and not isinstance(error, review.ContractValidationError):
        try:
            req_for_parse = dict(req)
            had_exp_pv = 'expected_prompt_version' in req_for_parse
            if not had_exp_pv:
                exp_json = optional(hand / 'expected.json')
                plan_pv = ((exp_json.get('plan') or {}).get('prompt_version')) if isinstance(exp_json, dict) else None
                if plan_pv is not None:
                    req_for_parse['expected_prompt_version'] = plan_pv
            result = _call_with_supported_kwargs(
                review.parse_initial_reply,
                saved,
                req_for_parse,
                contracts=contracts_pair,
            )
            if not had_exp_pv and isinstance(result, dict) and isinstance(result.get('request_payload'), dict):
                result['request_payload'].pop('expected_prompt_version', None)
            error = None
        except (review.ContractValidationError, ValueError, KeyError, TypeError) as exc:
            error = exc
    reply = (saved if isinstance(saved, dict) else None) or result or {}
    payload = dict((result or {}).get('request_payload') or req)
    payload.update(requested_at_ms=req['requested_at_ms'], endpoint=reply.get('endpoint') or payload.get('endpoint'))
    is_contract_err = isinstance(error, review.ContractValidationError)
    is_timeout = isinstance(error, TimeoutError) or 'timed out' in str(error).lower() or isinstance(getattr(error, 'reason', None), TimeoutError)
    status = (
        'RECEIVED' if result is not None
        else 'PARSE_FAILED' if (saved is not None or is_contract_err)
        else 'TIMEOUT' if is_timeout
        else 'REQUEST_FAILED'
    )
    http_error = getattr(error, 'code', None) or getattr(error, 'status_code', None)
    delivery = ('RECEIVED' if saved is not None or result is not None or is_contract_err else
                'PROVIDER_REJECTED' if isinstance(http_error, int) and 400 <= http_error < 500 and http_error != 408
                else 'DELIVERY_UNKNOWN')
    raw = (result or {}).get('raw_bytes')
    detail = f'{type(error).__name__}: {str(error)[:180]}' if error else 'provider delivery unknown; no reply archived; automatic resend disabled' if result is None else None
    cv_report = (result or {}).get('contract_validation') or (error.contract_validation if is_contract_err else None)
    err_code = 'CONTRACT_VALIDATION_FAILED' if is_contract_err else None
    cfg_meta = dict(
        reasoning_effort=reply.get('reasoning_effort') or (req.get('parameters') or {}).get('reasoning_effort'),
        usage=reply.get('usage'),
        http_status=reply.get('http_status') or http_error,
        delivery_state=delivery,
    )
    if err_code is not None:
        cfg_meta['error_code'] = err_code
    full_raw_bytes = (
        (saved.get('text') or '').encode('utf-8') if isinstance(saved, dict)
        else (error.parsed_reply.get('raw_bytes') if is_contract_err and isinstance(error.parsed_reply, dict) else None) or raw
    )
    return _call_with_supported_kwargs(
        d.respond, hand, call.name, status, raw, (result or {}).get('annotation'),
        model=reply.get('model') or req['model'],
        configuration=cfg_meta,
        requested=req['requested_at_ms'], decided=reply.get('decided_at_ms') or d.clock(), channel='API_AUTO',
        error=detail, request_payload=review.sanitize_model_request(payload),
        full_raw=full_raw_bytes,
        condition_plan=(result or {}).get('condition_plan'),
        prompt_version=payload.get('prompt_version'),
        system_sha256=payload.get('system_sha256'),
        input_profile=payload.get('input_profile'),
        contract_validation=cv_report,
        error_code=err_code,
    )


def _auto_deal(d, plan, archive_root, service, *, llm_config=None):
    results = []
    now = d.clock()
    prompt_version = plan.get('prompt_version') or review.INITIAL_SYSTEM_PROMPT_VERSION
    if prompt_version not in review.INITIAL_SYSTEM_PROMPTS:
        raise ValueError(f'unsupported plan prompt_version: {prompt_version!r}')
    p0_profile = getattr(dossier.player, 'INPUT_PROFILE_P0_STRICT', 'p0_strict@1')
    legacy_profile = getattr(dossier.player, 'INPUT_PROFILE_LEGACY', 'legacy_v1')
    input_profile = plan.get('input_profile') or (
        p0_profile if prompt_version == review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION else legacy_profile
    )
    supported_profiles = getattr(dossier.player, 'SUPPORTED_INPUT_PROFILES', (legacy_profile, p0_profile))
    if input_profile not in supported_profiles:
        raise ValueError(f'unsupported plan input_profile: {input_profile!r}')
    for slot in reversed(dossier.schedule(plan, now)):
        expiry = slot + plan['expiry_offset_ms']
        hand = None
        try:
            # A closed historical slot cannot acquire a new position. A missing
            # expected record still counts as a missed scheduled opportunity.
            if expiry <= now:
                expected = d.path(expiry) / 'hands' / dossier.identity('hand', 'Deribit', 'BTC', expiry, 'SCHEDULED', slot)
                if (expected / 'expected.json').is_file():
                    continue
            with _short_writer(d):
                hand, = _call_with_supported_kwargs(d.tick, plan, slots=[slot], input_profile=input_profile)
            if expiry <= now:
                continue
            if not (hand / 'evidence').exists() and d.clock() < slot + plan['deal_grace_ms']:
                try:
                    source = dossier.collect(archive_root, expiry, slot, clock=d.clock,
                                             candidate_round=plan.get('candidate_round', 'r2'))
                    with _short_writer(d):
                        _call_with_supported_kwargs(d.deal, hand, source, input_profile=input_profile)
                except (OSError, LookupError, ValueError) as exc:
                    with _short_writer(d):
                        state = 'POLICY_BLOCKED' if isinstance(exc, dossier.player.HandNotDealt) else 'DATA_BLOCKED'
                        d.note(hand, state, str(exc))
            results.append(_deal_hand(
                d, hand, service, _llm_settings(llm_config),
                prompt_version=prompt_version, input_profile=input_profile,
            ))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            results.append(_hand_error(d, hand, exc, slot))
    return results


def _hand_error(d, hand, exc, slot=None):
    reason = getattr(exc, 'code', None) or type(exc).__name__
    if hand is not None:
        try:
            with _short_writer(d):
                d.note(hand, 'DEAL_BLOCKED', reason)
        except (OSError, ValueError):
            pass  # Preserve the failure in the scheduler journal if storage is unavailable.
    return dict(hand_id=hand.name if hand else None, scheduled_at_ms=slot, status='DEAL_BLOCKED', reason=reason)


def _safe_summary(d, expiry_ms):
    try:
        return d.summary(expiry_ms)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _deal_hand(d, hand, service, cfg, *, recover_only=False, prompt_version=None, input_profile=None):
    exp = load(hand / 'expected.json')
    link_todo = None
    llm_input = None
    with _short_writer(d):
        for request_file in sorted((hand / 'api_calls').glob('*/request.json')):
            call = request_file.parent
            if not (call / 'not_sent.json').is_file() and not (hand / 'responses' / call.name / 'attempt.json').is_file():
                _record_initial_reply(d, hand, call, load(request_file))
        if not (hand / 'decision.json').is_file():
            d.select(hand)
        now = d.clock()
        if (hand / 'decision.json').is_file():
            decision = load(hand / 'decision.json')
            action = decision['judgment']['action']
            if action == 'Accept' and now < exp['expiry_ms'] and not (hand / 'tracking' / 'link.json').is_file() and not _link_blocked(hand / 'tracking'):
                req = optional(hand / 'tracking' / 'request.json')
                c_plan = (req.get('condition') if isinstance(req, dict) else None) or _recover_condition_plan(hand, decision)
                if c_plan:
                    link_todo = c_plan
                else:
                    d.note(hand, 'LOCK_BLOCKED', 'condition_plan_missing')
            if link_todo is None:
                result = dict(hand_id=hand.name, status='ALREADY_DECIDED', decision=action, link=None)
        elif not (hand / 'evidence' / 'input.md').is_file():
            result = dict(hand_id=hand.name, status='NO_EVIDENCE')
        else:
            meta = d.verify_evidence(hand)
            deadline = dossier.Dossiers.reply_deadline(exp, meta)
            existing_auto = sorted(
                (load(p) for p in (hand / 'responses').glob('*/attempt.json')
                 if not p.parent.name.startswith('.') and load(p).get('channel') == 'API_AUTO'),
                key=lambda a: (int(a.get('ordinal') or 0), int(a.get('recorded_at_ms') or 0)),
            )
            notes = [load(p) for p in (hand / 'issuance').glob('*.json')]
            bjt_today = (now + 8 * 3_600_000) // dossier.DAY
            if deadline is not None and now >= deadline:
                result = dict(hand_id=hand.name, status='REPLY_WINDOW_CLOSED')
            elif existing_auto:
                result = dict(hand_id=hand.name, status='AUTO_ATTEMPTED', last_status=existing_auto[-1].get('status'))
            elif any(
                n.get('state') == 'DAILY_BUDGET_EXHAUSTED'
                and ((n.get('recorded_at_ms') or 0) + 8 * 3_600_000) // dossier.DAY == bjt_today
                for n in notes
            ):
                result = dict(hand_id=hand.name, status='DAILY_BUDGET_EXHAUSTED')
            elif recover_only:
                return dict(hand_id=hand.name, status='NOT_DISPATCHED')
            elif not cfg.get('api_key') and cfg.get('transport') is None:
                d.note(hand, 'LLM_NOT_CONFIGURED', 'missing_api_key')
                result = dict(hand_id=hand.name, status='LLM_NOT_CONFIGURED')
            else:
                _, input_bytes, source = evidence(hand)
                contracts_pair = [contract_fields(dossier.player.bind(case, card, card['side']))
                                  for case, card in zip(source['cases'], source['cards'])]
                llm_input = (meta, input_bytes, contracts_pair)
    if link_todo is not None:
        link_res = _link_with_fresh_quote(d, hand, service, link_todo)
        result = dict(hand_id=hand.name, status='ALREADY_DECIDED', decision='Accept', link=link_res)
        with _short_writer(d):
            _safe_summary(d, exp['expiry_ms'])
        return result
    if llm_input is None:
        with _short_writer(d):
            _safe_summary(d, exp['expiry_ms'])
        return result
    meta, input_bytes, contracts_pair = llm_input
    req_ms = d.clock()
    if deadline is not None and req_ms >= deadline:
        return dict(hand_id=hand.name, status='REPLY_WINDOW_CLOSED')
    p0_profile = getattr(dossier.player, 'INPUT_PROFILE_P0_STRICT', 'p0_strict@1')
    legacy_profile = getattr(dossier.player, 'INPUT_PROFILE_LEGACY', 'legacy_v1')
    plan_cfg = exp.get('plan') if isinstance(exp.get('plan'), dict) else {}
    eff_prompt_version = prompt_version or plan_cfg.get('prompt_version') or review.INITIAL_SYSTEM_PROMPT_VERSION
    eff_input_profile = input_profile or meta.get('input_profile') or plan_cfg.get('input_profile') or (
        p0_profile if eff_prompt_version == review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION else legacy_profile
    )
    req_input_profile = eff_input_profile if eff_input_profile != legacy_profile else None
    attempt_id = dossier.identity('auto', hand.name, req_ms, cfg['model'], len(existing_auto))
    req_payload = review.build_initial_request_payload(
        input_bytes.decode('utf-8'),
        contract_pair=contracts_pair,
        model=cfg['model'],
        prompt_version=eff_prompt_version,
        input_profile=req_input_profile,
        reasoning_effort=cfg.get('reasoning_effort'),
        endpoint=review._normalize_endpoint(cfg.get('endpoint')),
        packet_hash=meta['input_sha256'],
        requested_at_ms=req_ms,
        fidelity='EXACT',
    )
    call = hand / 'api_calls' / attempt_id
    dossier.write(call / 'request.json', dossier.encode(review.sanitize_model_request(req_payload)))
    dossier.write(call / 'dispatch.json', dossier.encode(dict(started_at_ms=d.clock(), timeout_s=cfg['timeout_s'])))
    try:
        ai_res = _call_with_supported_kwargs(
            review.decide_initial_hand,
            input_bytes.decode('utf-8'),
            contract_pair=contracts_pair,
            api_key=cfg['api_key'] or 'test-key',
            model=cfg['model'],
            prompt_version=eff_prompt_version,
            input_profile=req_input_profile,
            endpoint=cfg.get('endpoint'),
            timeout_s=cfg.get('timeout_s', 300),
            retries=0,  # An ambiguous POST must never be automatically replayed.
            reasoning_effort=cfg.get('reasoning_effort'),
            budget=cfg.get('budget'),
            packet_hash=meta['input_sha256'],
            transport=cfg.get('transport'),
            on_response=lambda reply: _remember_reply(call, d, reply),
        )
    except signal_llm_review.DailyBudgetExceeded as exc:
        with _short_writer(d):
            dossier.write(call / 'not_sent.json', dossier.encode(dict(reason='DAILY_BUDGET_EXHAUSTED')))
            d.note(hand, 'DAILY_BUDGET_EXHAUSTED', str(exc)[:180])
        result = dict(hand_id=hand.name, status='DAILY_BUDGET_EXHAUSTED', error=str(exc))
    except Exception as exc:
        with _short_writer(d):
            att = _record_initial_reply(d, hand, call, req_payload, error=exc)
        result = dict(hand_id=hand.name, status=att['status'], error=att.get('error'))
        if att['status'] == 'VALID':
            result['decision'] = att['judgment']['action']
            with _short_writer(d):
                d.note(hand, 'POST_RESPONSE_ERROR', type(exc).__name__)
            if result['decision'] == 'Accept':
                result['link'] = _link_with_fresh_quote(d, hand, service, _recover_condition_plan(hand, load(hand / 'decision.json')))
    else:
        with _short_writer(d):
            att = _record_initial_reply(d, hand, call, req_payload, result=ai_res)
            verdict = (att.get('judgment') or {}).get('action')
        link_res = None
        if att.get('status') == 'VALID' and verdict == 'Accept':
            link_res = _link_with_fresh_quote(d, hand, service, ai_res.get('condition_plan'))
        result = dict(hand_id=hand.name, status=att.get('status'), decision=verdict,
                            error=att.get('error'), link=link_res)
    with _short_writer(d):
        _safe_summary(d, exp['expiry_ms'])
    return result


def recover_initial(d, service):
    """Sync may finish an existing hand after the last dispatch timer. Never call the model."""
    out = []
    with ExitStack() as lease:
        try:
            lease.enter_context(dossier.writer(d.root / '.auto-deal'))
        except (BlockingIOError, PermissionError):
            return out
        cutoff = d.path(d.clock()).name
        for folder in sorted((d.root / 'dossiers').glob('Deribit_BTC_*'), reverse=True):
            if folder.name < cutoff and not any(
                not (p.parent / 'not_sent.json').is_file()
                for p in folder.glob('hands/*/api_calls/*/dispatch.json')
            ):
                continue  # Old sealed dossiers predate this recovery journal.
            for hand in (folder / 'hands').glob('hand_*'):
                try:
                    exp = load(hand / 'expected.json')
                    if exp['sample'] != 'SCHEDULED':
                        continue
                    if (hand / 'tracking/request.json').exists() or (hand / 'tracking/link.json').exists():
                        continue  # sync-open already recovers native lock requests
                    decision = optional(hand / 'decision.json')
                    if decision and decision['judgment']['action'] != 'Accept':
                        continue
                    calls = [p.parent for p in (hand / 'api_calls').glob('*/request.json')]
                    unfinished = any(not (c / 'not_sent.json').exists() and
                                     not (hand / 'responses' / c.name / 'attempt.json').exists() for c in calls)
                    saved_valid = any(load(p).get('status') == 'VALID' for p in dossier.attempts(hand))
                    if unfinished or (saved_valid and not decision) or (decision and d.clock() < exp['expiry_ms']):
                        if not _link_blocked(hand / 'tracking'):
                            out.append(_deal_hand(d, hand, service, {}, recover_only=True))
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    out.append(_hand_error(d, hand, exc))
    return out


def _compact_triggers(triggers, keys=('category', 'code', 'summary_cn')):
    if not isinstance(triggers, list):
        return []
    return [{k: t.get(k) for k in keys} for t in triggers if isinstance(t, dict)]


def _condition_check_summary(check_events, *, asof_ms=None, check_clock=None, expiry_ms=None):
    """Deduplicate grid checks per observed timestamp (preferring resolved checks over UNKNOWN)."""
    by_tick = {}
    for idx, ev in enumerate(check_events):
        kind = str(ev.get('kind', ''))
        if not kind.startswith('CONDITION_'):
            continue
        obs = ev.get('observed_at_ms')
        rec = ev.get('recorded_at_ms') or 0
        key = obs if isinstance(obs, int) else f'idx_{idx}_{ev.get("event_id")}'
        order_key = (obs if isinstance(obs, int) else rec, kind != 'CONDITION_UNKNOWN', rec, str(ev.get('event_id') or ''))
        prev = by_tick.get(key)
        if prev is None or order_key > prev[0]:
            by_tick[key] = (order_key, ev)
    ordered = [pair[1] for pair in sorted(by_tick.values(), key=lambda pair: pair[0])]
    triggered = sum(1 for e in ordered if e.get('kind') == 'CONDITION_TRIGGERED')
    not_triggered = sum(1 for e in ordered if e.get('kind') == 'CONDITION_NOT_TRIGGERED')
    unknown = sum(1 for e in ordered if e.get('kind') == 'CONDITION_UNKNOWN')
    latest = ordered[-1] if ordered else None
    first_at = (check_clock or {}).get('first_at_ms')
    interval = (check_clock or {}).get('interval_ms')
    not_due = not latest and isinstance(asof_ms, int) and isinstance(first_at, int) and asof_ms < first_at
    latest_at_ms = ((latest.get('observed_at_ms') or latest.get('recorded_at_ms')) if latest else None)
    due = None
    if isinstance(asof_ms, int) and isinstance(first_at, int) and isinstance(interval, int) and interval > 0:
        limit = min(asof_ms, expiry_ms - 1) if isinstance(expiry_ms, int) else asof_ms
        if limit >= first_at:
            due = first_at + (limit - first_at) // interval * interval
    mech_state = ('TRIGGERED' if triggered
                  else 'NOT_DUE' if not_due
                  else 'DATA_GAP' if (not latest or latest.get('kind') == 'CONDITION_UNKNOWN'
                                      or (due is not None and (latest_at_ms or 0) < due))
                  else 'INTACT')
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
                    usage=_normalize_usage(body.get('usage')),
                    protocol_status=body.get('protocol_status'),
                    shadow_action=body.get('shadow_action'),
                    summary=body.get('public_reply'))
    elif kind == 'AI_REVIEW_REQUESTED':
        item.update(review_id=body.get('review_id'),
                    trigger_categories=body.get('trigger_categories'),
                    triggers=_compact_triggers(body.get('triggers')),
                    model=body.get('model'),
                    reasoning_effort=body.get('reasoning_effort'),
                    summary='已发起 AI 持仓复核')
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
    elif kind == 'HOLD_REVIEW_SKIPPED':
        item.update(reason=body.get('reason'),
                    trigger_categories=body.get('trigger_categories'),
                    triggers=_compact_triggers(body.get('triggers')),
                    summary=body.get('summary_cn') or body.get('detail') or body.get('reason'))
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
            if dec.get('review_channel') is not None:
                rec['review_channel'] = dec['review_channel']
            if dec.get('new_qualified_data_present') is not None:
                rec['new_qualified_data_present'] = dec['new_qualified_data_present']
            att_dec = optional(folder / 'attempt.json')
            if rec.get('reasoning_effort') is None and isinstance(att_dec, dict) and att_dec.get('reasoning_effort') is not None:
                rec['reasoning_effort'] = att_dec.get('reasoning_effort')
            rec['usage'] = _normalize_usage(dec.get('usage')) or _normalize_usage((att_dec or {}).get('usage'))
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
            review_id = b.get('review_id') or ev.get('event_id')
            if review_id in seen_ids:
                continue
            rec = dict(
                review_id=review_id,
                status=b.get('status') or 'FAILED',
                verdict=None,
                trigger_categories=b.get('trigger_categories') or [],
                triggers=_compact_triggers(
                    b.get('triggers'), keys=('category', 'code', 'summary_cn', 'observed_at_ms')),
                model=b.get('model'),
                reasoning_effort=b.get('reasoning_effort'),
                requested_at_ms=ev.get('observed_at_ms') or ev.get('recorded_at_ms'),
                decided_at_ms=ev.get('recorded_at_ms'),
                error=b.get('detail') or b.get('error') or 'HOLD_REVIEW_FAILED',
                usage=b.get('usage'), input_sha256=b.get('input_sha256'),
            )
            if include_reply and ep is not None and re.fullmatch(REVIEW_ID, str(review_id)):
                call_dir = ep / 'review_calls' / review_id
                response = optional(call_dir / 'reply.json') or {}
                rec['raw_reply'] = response.get('text')
                if (call_dir / 'input.md').is_file():
                    rec['input_md'] = bounded(call_dir / 'input.md').decode('utf-8')
            reviews.append(rec)
    reviews.sort(key=lambda r: (r.get('decided_at_ms') or r.get('requested_at_ms') or 0, str(r.get('review_id') or '')), reverse=True)

    trig_state = optional(ep / 'trigger_state.json') if ep is not None else None
    trig_state = trig_state if isinstance(trig_state, dict) else {}
    skip_events = [e for e in sorted_events if e.get('kind') == 'HOLD_REVIEW_SKIPPED']
    by_reason = {}
    raw_counts = trig_state.get('skip_counts')
    if isinstance(raw_counts, dict):
        for k, v in raw_counts.items():
            rk = str(k).strip().upper()
            if rk and rk not in {'NO_TRIGGER', 'NONE'} and isinstance(v, int) and v > 0:
                by_reason[rk] = v
    ev_counts = {}
    ev_skips = []
    for ev in skip_events:
        b = ev.get('body') if isinstance(ev.get('body'), dict) else {}
        r = str(b.get('reason') or '').strip().upper()
        if not r or r in {'NO_TRIGGER', 'NONE'}:
            continue
        ev_counts[r] = ev_counts.get(r, 0) + 1
        ev_skips.append(dict(
            reason=r,
            signature=b.get('signature'),
            trigger_categories=b.get('trigger_categories') or [],
            at_ms=ev.get('observed_at_ms') or ev.get('recorded_at_ms'),
            recorded_at_ms=ev.get('recorded_at_ms'),
            summary_cn=b.get('summary_cn') or b.get('detail'),
        ))
    for r, cnt in ev_counts.items():
        if cnt > by_reason.get(r, 0):
            by_reason[r] = cnt

    state_recent = []
    if isinstance(trig_state.get('recent_skips'), list):
        for s in trig_state['recent_skips']:
            norm_s = _normalize_skip_info(s, s.get('at_ms') if isinstance(s, dict) else None)
            if norm_s is not None:
                state_recent.append(norm_s)
    state_last = _normalize_skip_info(trig_state.get('last_skip'), None)
    recent_skips = state_recent or ev_skips[-10:]
    if state_last is not None and not any(
        s.get('reason') == state_last.get('reason') and s.get('at_ms') == state_last.get('at_ms')
        for s in recent_skips
    ):
        recent_skips.append(state_last)
    recent_skips = sorted(recent_skips, key=lambda s: int(s.get('at_ms') or s.get('recorded_at_ms') or 0))[-10:]
    last_skip = None
    if ev_skips and state_last:
        ev_ts = int(ev_skips[-1].get('at_ms') or 0)
        st_ts = int(state_last.get('at_ms') or 0)
        last_skip = ev_skips[-1] if ev_ts >= st_ts else state_last
    else:
        last_skip = ev_skips[-1] if ev_skips else (state_last or (recent_skips[-1] if recent_skips else None))
    total_skips = sum(by_reason.values())
    hold_skip_summary = dict(
        total_skips=total_skips,
        by_reason=by_reason,
        last_skip=last_skip,
        recent_skips=recent_skips,
    )

    cond_summary = _condition_check_summary(
        check_events, asof_ms=max((e.get('recorded_at_ms') or 0 for e in sorted_events), default=None),
        check_clock=(frozen or {}).get('check_clock'), expiry_ms=((frozen or {}).get('contract') or {}).get('expiry_ms'))
    mech_state = cond_summary['mechanical_condition_state']
    latest_rev = reviews[0] if reviews else {}
    verified_rev_events = [e for e in rev_events if _is_verified_revoke(e)]
    protocol_status = latest_rev.get('protocol_status') or ('DISCRETIONARY_REVOKE' if (not trig_events and verified_rev_events) else 'ALIGNED')
    shadow_action = latest_rev.get('shadow_action') or ('REVOKE_DISCRETIONARY' if (not trig_events and verified_rev_events) else 'HOLD_SHADOW_CONTINUE' if latest_rev.get('verdict') == 'HOLD' else 'NONE')

    valid_reviews = [r for r in reviews if r.get('status') == 'VALID']
    failed_reviews = [r for r in reviews if r.get('status') == 'FAILED']
    queued_events = [e for e in sorted_events if e.get('kind') == 'HOLD_REVIEW_QUEUED']
    tel_raw = trig_state.get('telemetry') if isinstance(trig_state.get('telemetry'), dict) else {}
    coalesced_debounce_count = (
        int(tel_raw['coalesced_debounce_count'])
        if isinstance(tel_raw.get('coalesced_debounce_count'), int)
        else len(queued_events) + sum(max(0, len(r.get('triggers') or []) - 1) for r in valid_reviews)
    )
    candidate_trigger_count = (
        int(tel_raw['candidate_triggers_seen'])
        if isinstance(tel_raw.get('candidate_triggers_seen'), int)
        else max(len(valid_reviews) + len(failed_reviews) + total_skips,
                 len(queued_events) + sum(len(r.get('triggers') or []) for r in valid_reviews))
    )
    tok_totals = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
    has_tokens = False
    for r in valid_reviews:
        u = r.get('usage')
        if isinstance(u, dict) and any(isinstance(u.get(k), int) for k in tok_totals):
            has_tokens = True
            pt = u.get('prompt_tokens') if isinstance(u.get('prompt_tokens'), int) else 0
            ct = u.get('completion_tokens') if isinstance(u.get('completion_tokens'), int) else 0
            tt = u.get('total_tokens') if isinstance(u.get('total_tokens'), int) else (pt + ct)
            tok_totals['prompt_tokens'] += pt
            tok_totals['completion_tokens'] += ct
            tok_totals['total_tokens'] += tt
    management_telemetry = dict(
        grid_check_count=cond_summary['total'],
        candidate_trigger_count=candidate_trigger_count,
        coalesced_debounce_count=coalesced_debounce_count,
        skip_count=total_skips,
        skip_and_debounce_count=total_skips + coalesced_debounce_count,
        ai_review_count=len(valid_reviews),
        ai_failed_count=len(failed_reviews),
        ai_call_count=len(valid_reviews) + len(failed_reviews),
        usage=tok_totals if has_tokens else None,
    )

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
            ai_revoked_at_ms=(verified_rev_events[-1].get('observed_at_ms') or verified_rev_events[-1].get('recorded_at_ms')) if verified_rev_events else None,
            exit_status=exit_status,
            buyback_cost_btc=fill.get('cost_btc'),
            buyback_fee_btc=fill.get('fee_btc'),
            buyback_quote_at_ms=fill.get('quoted_at_ms'),
            retained_long_instrument=fill.get('long_instrument') or c.get('long_instrument'),
            long_retained=True,
        )
    elif no_quotes or verified_rev_events:
        nq = (no_quotes[-1].get('body') or {}) if no_quotes else {}
        shadow_mgmt = dict(
            mechanical_condition_state=mech_state,
            ai_management_verdict=latest_rev.get('verdict'),
            protocol_status=protocol_status,
            shadow_action=shadow_action,
            condition_triggered_at_ms=(trig_events[0].get('observed_at_ms') or trig_events[0].get('recorded_at_ms')) if trig_events else None,
            ai_revoked_at_ms=(verified_rev_events[-1].get('observed_at_ms') or verified_rev_events[-1].get('recorded_at_ms')) if verified_rev_events else None,
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
        hold_skip_summary=hold_skip_summary,
        management_telemetry=management_telemetry,
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
        hold_skip_summary=mgmt.get('hold_skip_summary'),
        management_telemetry=mgmt.get('management_telemetry'),
    )
    if latest:
        out['latest_verdict'] = latest.get('verdict') or latest.get('status')
        out['latest_trigger_categories'] = latest.get('trigger_categories') or []
        out['latest_reviewed_at_ms'] = latest.get('decided_at_ms') or latest.get('requested_at_ms')
        if include_reply:
            latest_view = {k: latest.get(k) for k in (
                'review_id', 'verdict', 'ai_management_verdict', 'mechanical_condition_state',
                'protocol_status', 'shadow_action', 'status', 'trigger_categories', 'triggers',
                'model', 'usage', 'requested_at_ms', 'decided_at_ms', 'public_reply', 'affected_conditions',
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
        'usage': _normalize_usage(cfg.get('usage')),
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
    if not detail and decision and decision.get('attempt_id'):
        attempt = optional(hand / 'responses' / dossier.key(decision['attempt_id']) / 'attempt.json')
    frozen = optional(hand / 'evidence' / 'identity.json')
    req = optional(root / 'request.json')
    receipt = optional(root / 'link.json')
    blocked = _link_blocked(root)
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
    lock_state = ('TRACKING' if tracker in (tracker_status or {}) else 'ARCHIVED' if terminal_meta else 'TRACKER_UNAVAILABLE'
                  if tracker else 'LOCK_BLOCKED' if blocked or any(n['state'] == 'LOCK_BLOCKED' for n in notes)
                  else 'LINK_UNVERIFIED' if any(n['state'] == 'LINK_UNVERIFIED' for n in notes)
                  else req['state'] if req else None)
    initial_usage = _normalize_usage(((attempt or {}).get('configuration') or {}).get('usage'))
    result = dict(dossier_id=exp['dossier_id'], hand_id=hand.name, sample=exp['sample'], state=state,
                  expiry_ms=exp['expiry_ms'], frozen_at_ms=(frozen or {}).get('hand_frozen_at_ms'),
                  scheduled_at_ms=exp['scheduled_at_ms'], side=(decision or {}).get('judgment', {}).get('side'),
                  model=(decision or {}).get('model') or (attempts[-1].get('model') if attempts else None),
                  usage=initial_usage,
                  tracker_episode_id=tracker if tracker in (tracker_status or {}) else None,
                  tracker_status=(tracker_status or {}).get(tracker),
                  tracking_state=lock_state)
    # The request is published before HTTP. Its existence alone cannot prove delivery.
    calls = [] if decision or attempts else [
        p.parent for p in (hand / 'api_calls').glob('*/request.json')
        if not (p.parent / 'not_sent.json').is_file()
    ]
    call = max(calls, key=lambda c: load(c / 'request.json')['requested_at_ms'], default=None)
    sent = optional(call / 'request.json') if call else None
    dispatch = optional(call / 'dispatch.json') if call else None
    reply = optional(call / 'reply.json') if call and not attempts else None
    last = attempts[-1] if attempts else {}
    note = notes[-1] if notes else {}
    updated = max((t for t in (exp.get('recorded_at_ms'), result['frozen_at_ms'],
                    (sent or {}).get('requested_at_ms'), (reply or {}).get('decided_at_ms'),
                    last.get('recorded_at_ms'), (decision or {}).get('recorded_at_ms'),
                    (req or {}).get('requested_at_ms'), (receipt or {}).get('recorded_at_ms'),
                    note.get('recorded_at_ms')) if isinstance(t, int)), default=None)
    stage = lock_state or state
    if receipt and tracker_status is None:
        stage = 'ARCHIVED' if terminal_meta else 'LINK_CONFIRMED'
    reason = (blocked or {}).get('reason') or last.get('error') or note.get('reason')
    if not receipt and note.get('state') == 'DEAL_BLOCKED' and note.get('recorded_at_ms') == updated:
        stage = 'DEAL_BLOCKED'
    elif not decision and frozen and not attempts and (
        state in {'AWAITING_RESPONSE', 'NO_RESPONSE'}
        or (state == 'DAILY_BUDGET_EXHAUSTED' and deadline and now >= deadline)
    ):
        if reply:
            stage = 'REPLY_SAVED'
        elif not sent:
            stage = 'DISPATCH_EXPIRED' if deadline and now >= deadline else 'AWAITING_DISPATCH'
        elif (deadline and now >= deadline) or (dispatch and now >= dispatch['started_at_ms'] + dispatch['timeout_s'] * 1000):
            stage = 'DELIVERY_UNKNOWN'
        else:
            stage = 'REQUEST_IN_PROGRESS' if dispatch else 'DELIVERY_UNKNOWN'
    elif not decision and last.get('status') in {'TIMEOUT', 'REQUEST_FAILED'} and last.get('channel') == 'API_AUTO':
        stage = (last.get('configuration') or {}).get('delivery_state') or 'DELIVERY_UNKNOWN'
    elif not decision and last.get('status') == 'VALID':
        stage = 'DECISION_PENDING'
    elif decision and stage == 'Accept':
        stage = 'HANDOFF_EXPIRED' if now >= min(deadline or exp['expiry_ms'], decision['decided_at_ms'] + HANDOFF_MS) else 'HANDOFF_PENDING'
    result.update(stage=stage, last_update_ms=updated, reply_deadline_ms=deadline, stage_reason=reason)
    if sent and not result['model']:
        result['model'] = sent.get('model')
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
                  usage=initial_usage or _normalize_usage(top_cfg.get('usage')),
                  lock_requested_at_ms=(req or {}).get('requested_at_ms'), receipt=receipt and receipt['t0'],
                  condition=frozen_cond, entry_basis=entry_basis,
                  events=[_event_projection(e) for e in recent], archive=terminal_meta,
                  attempts=[_attempt_projection(a) for a in attempts[-8:]],
                  notes=notes[-8:], lock_reason=(blocked or {}).get('reason') or note_lock_reason)
    if mgmt is not None:
        result['hold_reviews'] = mgmt['hold_reviews']
        result['shadow_management'] = mgmt['shadow_management']
        result['grid_check_summary'] = mgmt.get('grid_check_summary')
        result['hold_skip_summary'] = mgmt.get('hold_skip_summary')
        result['management_telemetry'] = mgmt.get('management_telemetry')
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


def current_scheduled(root, now):
    """Read the latest due formal hand without building the history or native tracker index."""
    folders = sorted((Path(root) / 'dossiers').glob('Deribit_BTC_*'), reverse=True)
    for folder in folders:
        candidates = []
        for path in (folder / 'hands').glob('hand_*/expected.json'):
            try:
                exp = load(path)
                if (isinstance(exp, dict) and exp.get('sample') == 'SCHEDULED'
                        and type(exp.get('scheduled_at_ms')) is int and exp['scheduled_at_ms'] <= now):
                    candidates.append((exp['scheduled_at_ms'], path.parent.name, path.parent))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        for _, _, hand in candidates:
            try:
                return audit_record(hand, now, None)
            except (OSError, ValueError, KeyError, TypeError):
                continue
    return None


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
        attempt = max((a for a in candidates if a.get('raw_sha256') or a.get('full_raw_sha256')),
                      key=lambda a: a['ordinal'], default=None)
        if attempt is not None:
            failed_raw = attempt.get('full_raw_sha256') is not None
            filename = 'response.full.raw' if failed_raw else 'response.raw'
            response = bounded(hand / 'responses' / dossier.key(attempt['attempt_id']) / filename)
            if dossier.sha(response) != attempt.get('full_raw_sha256' if failed_raw else 'raw_sha256'):
                raise Blocked('reply_identity_mismatch')
    return dict(input=input_bytes.decode('utf-8'), response=response.decode('utf-8') if response is not None else None,
                selected=decision is not None, response_status=(attempt or {}).get('status'))


def export_model_input(hand, *, review_id=None):
    """Strictly read-only, leakage-free export of exact or reconstructed model input."""
    hand = Path(hand)
    exp = load(hand / 'expected.json')
    if exp['hand_id'] != hand.name or exp['dossier_id'] != hand.parent.parent.name:
        raise Blocked('invalid_hand_identity')

    if review_id is None:
        meta, input_bytes, source = evidence(hand)
        _, selected_attempt, _ = selected(hand)
        attempt = selected_attempt
        if attempt is None:
            candidates = [
                load(p) for p in (hand / 'responses').glob('*/attempt.json')
                if not p.parent.name.startswith('.')
            ]
            attempt = max(
                candidates,
                key=lambda a: (int(a.get('ordinal') or 0), int(a.get('recorded_at_ms') or 0)),
                default=None,
            )
        precall_attempt = {}
        if isinstance(attempt, dict):
            cfg = attempt.get('configuration') if isinstance(attempt.get('configuration'), dict) else {}
            precall_attempt = {
                'attempt_id': attempt.get('attempt_id'),
                'model': attempt.get('model'),
                'channel': attempt.get('channel'),
                'requested_at_ms': attempt.get('requested_at_ms'),
                'reasoning_effort': cfg.get('reasoning_effort'),
            }
        precall_meta = {
            'dossier_id': exp['dossier_id'],
            'hand_id': hand.name,
            'sample': exp.get('sample'),
            'expiry_ms': exp.get('expiry_ms'),
            'frozen_at_ms': meta.get('hand_frozen_at_ms'),
            'input_sha256': meta.get('input_sha256'),
            'rules_sha256': meta.get('rules_sha256'),
            'generator': meta.get('generator'),
            'attempt_id': precall_attempt.get('attempt_id'),
            'channel': precall_attempt.get('channel'),
        }
        attempt_id = precall_attempt.get('attempt_id')
        req_path = (hand / 'responses' / dossier.key(attempt_id) / 'request.json') if attempt_id else None
        if req_path is not None and req_path.is_file():
            req_bytes = bounded(req_path)
            if isinstance(attempt, dict) and attempt.get('request_sha256') and dossier.sha(req_bytes) != attempt['request_sha256']:
                raise Blocked('request_identity_mismatch')
            req_payload = json.loads(req_bytes)
            if not isinstance(req_payload, dict):
                raise Blocked('request_payload_invalid')
            req_payload['fidelity'] = 'EXACT'
            req_payload['fidelity_reason'] = None
        else:
            contracts_pair = [
                contract_fields(dossier.player.bind(case, card, card['side']))
                for case, card in zip(source['cases'], source['cards'])
            ]
            reason = (
                '历史手牌未落盘原生 request.json，按冻结证据 evidence/input.md、候选合约对与固定初始系统提示词重建'
                if attempt_id else
                '手牌尚未产生实发请求快照 request.json，按冻结证据 evidence/input.md、候选合约对与固定初始系统提示词重建'
            )
            reconstructed_pv = (
                (attempt.get('prompt_version') if isinstance(attempt, dict) else None)
                or ((exp.get('plan') or {}).get('prompt_version') if isinstance(exp.get('plan'), dict) else None)
                or review.INITIAL_SYSTEM_PROMPT_VERSION
            )
            if reconstructed_pv not in review.INITIAL_SYSTEM_PROMPTS:
                reconstructed_pv = review.INITIAL_SYSTEM_PROMPT_VERSION
            req_payload = review.build_initial_request_payload(
                input_bytes.decode('utf-8'),
                contract_pair=contracts_pair,
                model=precall_attempt.get('model'),
                prompt_version=reconstructed_pv,
                input_profile=meta.get('input_profile'),
                reasoning_effort=precall_attempt.get('reasoning_effort'),
                endpoint=None,
                packet_hash=meta.get('input_sha256'),
                requested_at_ms=precall_attempt.get('requested_at_ms'),
                fidelity='RECONSTRUCTED',
                fidelity_reason=reason,
                metadata=precall_meta,
            )
        md_context = {
            **precall_meta,
            **({'requested_at_ms': precall_attempt['requested_at_ms']}
               if precall_attempt.get('requested_at_ms') is not None else {}),
        }
        filename_md = f'{hand.name}-model-input.md'
        filename_json = f'{hand.name}-request.json'
    else:
        rev_id = str(review_id)
        if not re.fullmatch(REVIEW_ID, rev_id):
            raise ValueError('invalid review id')
        link_rec = optional(hand / 'tracking' / 'link.json')
        ep_id = (link_rec.get('origin') or {}).get('dossier_episode_id') if isinstance(link_rec, dict) else None
        episode = (hand.parent.parent / 'episodes' / dossier.key(ep_id)) if ep_id else None
        if episode is not None and not episode.is_dir():
            episode = None
        if episode is None:
            episode = next(
                (p.parent for p in sorted((hand.parent.parent / 'episodes').glob('episode_*/episode.json'))
                 if load(p).get('hand_id') == hand.name),
                None,
            )
        if episode is None:
            raise FileNotFoundError(rev_id)
        rev_dir = episode / 'reviews' / dossier.key(rev_id)
        if not rev_dir.is_dir():
            rev_dir = episode / 'review_calls' / dossier.key(rev_id)
        if not rev_dir.is_dir():
            raise FileNotFoundError(str(rev_dir))
        rev_attempt = optional(rev_dir / 'attempt.json')
        precall_rev = {}
        if isinstance(rev_attempt, dict):
            precall_rev = {
                'review_id': rev_attempt.get('review_id') or rev_id,
                'model': rev_attempt.get('model'),
                'reasoning_effort': rev_attempt.get('reasoning_effort'),
                'requested_at_ms': rev_attempt.get('requested_at_ms'),
            }
        rev_meta = {
            'dossier_id': exp['dossier_id'],
            'hand_id': hand.name,
            'review_id': precall_rev.get('review_id') or rev_id,
            'sample': exp.get('sample'),
            'expiry_ms': exp.get('expiry_ms'),
        }
        rev_req_path = rev_dir / 'request.json'
        if rev_req_path.is_file():
            req_payload = json.loads(bounded(rev_req_path))
            if not isinstance(req_payload, dict):
                raise Blocked('request_payload_invalid')
            req_payload['fidelity'] = 'EXACT'
            req_payload['fidelity_reason'] = None
        else:
            rev_bytes = bounded(rev_dir / 'input.md')
            req_payload = review.build_hold_review_request_payload(
                rev_bytes.decode('utf-8'),
                model=precall_rev.get('model'),
                reasoning_effort=precall_rev.get('reasoning_effort'),
                endpoint=None,
                packet_hash=dossier.sha(rev_bytes),
                requested_at_ms=precall_rev.get('requested_at_ms'),
                fidelity='RECONSTRUCTED',
                fidelity_reason='历史持仓复核未落盘原生 request.json，按冻结复核牌面 reviews/<review_id>/input.md 与固定复核系统提示词重建',
                metadata=rev_meta,
            )
        md_context = {
            **rev_meta,
            **({'requested_at_ms': precall_rev['requested_at_ms']}
               if precall_rev.get('requested_at_ms') is not None else {}),
        }
        filename_md = f'{hand.name}-{rev_id}-model-input.md'
        filename_json = f'{hand.name}-{rev_id}-request.json'

    clean_req = review.sanitize_model_request(req_payload)
    full_model_input_md = review.format_model_input_markdown(clean_req, context=md_context)
    single_box_text = review.format_single_box_input(clean_req)
    return {
        'request': clean_req,
        'full_model_input_md': full_model_input_md,
        'single_box_text': single_box_text,
        'fidelity': clean_req['fidelity'],
        'fidelity_reason': clean_req.get('fidelity_reason'),
        'stage': clean_req['stage'],
        'filename_md': filename_md,
        'filename_json': filename_json,
    }


def _check_workbench_view_readiness(workbench_view, reasons, checks, *, now_ms=None, max_view_age_ms=120_000):
    checks['workbench_view_checked'] = True
    check_age = type(now_ms) is int and now_ms > 0 and type(max_view_age_ms) is int and max_view_age_ms > 0
    updated_at_ms = workbench_view.get('updated_at_ms')
    checks['workbench_view_updated_at_ms'] = updated_at_ms
    if check_age:
        if type(updated_at_ms) is not int or updated_at_ms <= 0:
            reasons.append('workbench_view_updated_at_invalid')
        elif updated_at_ms > now_ms:
            reasons.append('workbench_view_in_future')
        elif now_ms - updated_at_ms > max_view_age_ms:
            reasons.append('workbench_view_stale')

    discovery_status = workbench_view.get('discovery_status')
    if not isinstance(discovery_status, dict):
        reasons.append('discovery_status_missing')
        checks['discovery_capture_state'] = None
        checks['upstream_code'] = None
        checks['discovery_observed_at_ms'] = None
    else:
        cap_state = discovery_status.get('capture_state')
        up_code = discovery_status.get('upstream_code')
        obs_ms = discovery_status.get('observed_at_ms')
        checks['discovery_capture_state'] = cap_state
        checks['upstream_code'] = up_code
        checks['discovery_observed_at_ms'] = obs_ms
        if cap_state != 'COMPLETE':
            reasons.append(f"discovery_capture_not_complete:{cap_state if cap_state is not None else 'missing'}")
        if up_code is not None:
            reasons.append(f'upstream_code:{up_code}')
        if check_age:
            if type(obs_ms) is not int or obs_ms <= 0:
                reasons.append('discovery_observed_at_invalid')
            elif obs_ms > now_ms:
                reasons.append('discovery_observed_in_future')
            elif now_ms - obs_ms > max_view_age_ms:
                reasons.append('discovery_observed_stale')

    data_health = workbench_view.get('data_health')
    if not isinstance(data_health, dict):
        reasons.append('data_health_missing')
        checks['data_health_discovery_capture_state'] = None
        checks['data_health_discovery_status'] = None
    else:
        products = data_health.get('products')
        disc_prod = products.get('deribit.btc.discovery.current.v1') if isinstance(products, dict) else None
        if not isinstance(products, dict) or 'deribit.btc.discovery.current.v1' not in products or disc_prod is None:
            reasons.append('data_health_discovery_missing')
            checks['data_health_discovery_capture_state'] = None
            checks['data_health_discovery_status'] = None
        elif not isinstance(disc_prod, dict):
            reasons.append('data_health_discovery_invalid')
            checks['data_health_discovery_capture_state'] = None
            checks['data_health_discovery_status'] = None
        else:
            dh_cap = disc_prod.get('capture_state')
            dh_status = disc_prod.get('status')
            checks['data_health_discovery_capture_state'] = dh_cap
            checks['data_health_discovery_status'] = dh_status
            if dh_cap != 'COMPLETE':
                reasons.append(
                    f"data_health_discovery_not_complete:{dh_cap if dh_cap is not None else 'missing'}"
                )
            if dh_status != 'OK':
                reasons.append(
                    f"data_health_discovery_status:{dh_status if dh_status is not None else 'missing'}"
                )

    candidates = workbench_view.get('candidates')
    by_side_checks = {}
    live_lock_by_side = {'put': False, 'call': False}
    checks['candidates_by_side'] = by_side_checks
    checks['live_lock_eligible_by_side'] = live_lock_by_side
    if not isinstance(candidates, list):
        reasons.append('candidates_missing_or_invalid')
        return

    if any(not isinstance(it, dict) for it in candidates):
        reasons.append('candidate_item_invalid')

    for side in ('put', 'call'):
        matches = [it for it in candidates if isinstance(it, dict) and it.get('side') == side]
        if not matches:
            reasons.append(f'candidate_missing:{side}')
            by_side_checks[side] = {'present': False}
            continue
        if len(matches) > 1:
            reasons.append(f'candidate_duplicate:{side}')
        item = matches[0]
        st = item.get('status')
        snap = item.get('snapshot')
        formal_ok = item.get('formal_evidence_eligible') is True
        live_ok = item.get('live_lock_eligible') is True
        held = bool(item.get('held_capture'))
        hist_summary = bool(item.get('is_historical_summary'))
        lvc = item.get('last_valid_candidate')
        item_disc = item.get('discovery_status')

        live_lock_by_side[side] = live_ok
        side_info = {
            'present': True,
            'status': st,
            'has_snapshot': isinstance(snap, dict),
            'formal_evidence_eligible': formal_ok,
            'live_lock_eligible': live_ok,
            'held_capture': held,
            'is_historical_summary': hist_summary,
            'has_last_valid_candidate': lvc is not None,
        }
        by_side_checks[side] = side_info

        if st != 'snapshot':
            reasons.append(f"candidate_{side}_not_snapshot:{st if st is not None else 'missing'}")
        if not isinstance(snap, dict):
            reasons.append(f'candidate_{side}_snapshot_missing')
        if not formal_ok:
            reasons.append(f'candidate_{side}_not_formal_evidence_eligible')
        if held:
            reasons.append(f'candidate_{side}_held_capture')
        if hist_summary:
            reasons.append(f'candidate_{side}_historical_summary')
        if lvc is not None and (st != 'snapshot' or not formal_ok or not isinstance(snap, dict)):
            reasons.append(f'candidate_{side}_last_valid_candidate_summary')

        if item_disc is None:
            reasons.append(f'candidate_{side}_discovery_status_missing')
        elif not isinstance(item_disc, dict):
            reasons.append(f'candidate_{side}_discovery_status_invalid')
        else:
            dp = item_disc.get('delta_population')
            dp_complete = isinstance(dp, dict) and dp.get('complete') is True
            side_info['side_complete'] = item_disc.get('side_complete') is True
            side_info['capture_state'] = item_disc.get('capture_state')
            side_info['upstream_code'] = item_disc.get('upstream_code')
            side_info['delta_population_complete'] = dp_complete
            if item_disc.get('side_complete') is not True:
                reasons.append(f'candidate_{side}_not_complete')
            if item_disc.get('capture_state') != 'COMPLETE':
                c_st = item_disc.get('capture_state')
                reasons.append(
                    f"candidate_{side}_capture_not_complete:{c_st if c_st is not None else 'missing'}"
                )
            if item_disc.get('upstream_code') is not None:
                reasons.append(f"candidate_{side}_upstream_code:{item_disc.get('upstream_code')}")
            if not dp_complete:
                reasons.append(f'candidate_{side}_delta_population_incomplete')


def _check_source_bundle_readiness(source, now_ms, reasons, checks):
    checks['source_checked'] = True
    cards = source.get('cards')
    card_expiries = set()
    card_reads = {}
    card_sides = []
    if not isinstance(cards, list) or len(cards) != 2 or any(not isinstance(c, dict) for c in cards):
        reasons.append('source_cards_invalid')
    if isinstance(cards, list):
        for idx, card in enumerate(cards):
            if not isinstance(card, dict):
                continue
            side = card.get('side')
            card_sides.append(side)
            label = side if side in {'put', 'call'} else f'idx_{idx}'
            exp = card.get('expiry_ms')
            if type(exp) is not int or exp <= 0:
                reasons.append(f'source_card_{label}_invalid_expiry_ms')
            else:
                card_expiries.add(exp)
            read_at = card.get('read_at_ms')
            if type(read_at) is not int or read_at <= 0:
                reasons.append(f'source_card_{label}_invalid_read_at_ms')
            else:
                if side in {'put', 'call'} and side not in card_reads:
                    card_reads[side] = read_at
                if type(now_ms) is int and read_at > now_ms:
                    reasons.append(f'source_card_{label}_read_at_in_future')
            if card.get('backfilled'):
                reasons.append(f'source_card_{label}_backfilled')
            gaps = card.get('data_gaps')
            if gaps is not None and not isinstance(gaps, list):
                reasons.append(f'source_card_{label}_invalid_data_gaps')
            elif gaps:
                reasons.append(f'source_card_{label}_data_gaps')
            ckey = card.get('contract_key')
            if not isinstance(ckey, str) or not ckey.strip():
                reasons.append(f'source_card_{label}_missing_contract_key')
        if len(card_sides) != 2 or set(card_sides) != {'put', 'call'}:
            reasons.append('source_cards_sides_mismatch')
    if len(card_expiries) > 1:
        reasons.append('source_cards_expiry_mismatch')
    expiry_ms = next(iter(card_expiries)) if len(card_expiries) == 1 else None
    checks['source_expiry_ms'] = expiry_ms
    checks['source_card_sides'] = [s for s in card_sides if isinstance(s, str)]

    received_at_ms = source.get('received_at_ms')
    checks['source_received_at_ms'] = received_at_ms
    if type(received_at_ms) is not int or received_at_ms <= 0:
        reasons.append('source_received_at_invalid')
    else:
        if card_reads and max(card_reads.values()) > received_at_ms:
            reasons.append('source_received_before_card_read')
        if type(now_ms) is int and received_at_ms > now_ms:
            reasons.append('source_received_at_in_future')

    cases = source.get('cases')
    case_sides = []
    wanted_instruments = set()
    if not isinstance(cases, list) or len(cases) != 2 or any(not isinstance(cs, dict) for cs in cases):
        reasons.append('source_cases_invalid')
    if isinstance(cases, list):
        case_expiries = set()
        for idx, case in enumerate(cases):
            if not isinstance(case, dict):
                continue
            l1 = dossier.player.values(case, 'L1.contract')
            c_side = l1.get('side')
            case_sides.append(c_side)
            c_label = c_side if c_side in {'put', 'call'} else f'idx_{idx}'
            short_inst = l1.get('short_instrument')
            long_inst = l1.get('long_instrument')
            if (not isinstance(short_inst, str) or not short_inst.strip()
                    or not isinstance(long_inst, str) or not long_inst.strip()):
                reasons.append(f'source_case_{c_label}_missing_instruments')
            else:
                wanted_instruments.update((short_inst, long_inst))
            c_exp = l1.get('expiry_ms')
            if type(c_exp) is not int or c_exp <= 0:
                reasons.append(f'source_case_{c_label}_invalid_expiry_ms')
            else:
                case_expiries.add(c_exp)
                if expiry_ms is not None and c_exp != expiry_ms:
                    reasons.append(f'source_case_{c_label}_expiry_mismatch')
            read_at = card_reads.get(c_side)
            for leg_key in ('short', 'long'):
                book = dossier.player.values(case, f'L2.book.{leg_key}')
                idx_p = dossier.player.number(book.get('index_price')) if isinstance(book, dict) else None
                ts = book.get('timestamp') if isinstance(book, dict) else None
                if (not isinstance(book, dict) or not book or idx_p is None or idx_p <= 0
                        or type(ts) is not int or ts <= 0):
                    reasons.append(f'source_case_{c_label}_missing_l2_book_{leg_key}')
                elif type(read_at) is int and ts > read_at:
                    reasons.append(f'source_case_{c_label}_l2_book_{leg_key}_after_read_at')
            l2_quote = dossier.player.values(case, 'L2.quote')
            if not isinstance(l2_quote, dict) or not l2_quote:
                reasons.append(f'source_case_{c_label}_missing_l2_quote')
            else:
                if dossier.player.number(l2_quote.get('credit_btc')) is None:
                    reasons.append(f'source_case_{c_label}_invalid_l2_quote_credit')
                val_ts = l2_quote.get('valuation_ts_ms')
                if type(val_ts) is not int or val_ts <= 0:
                    reasons.append(f'source_case_{c_label}_invalid_l2_quote_valuation_ts')
                elif type(read_at) is int and val_ts > read_at:
                    reasons.append(f'source_case_{c_label}_l2_quote_valuation_after_read_at')
                rec_ts = l2_quote.get('received_ts_ms')
                if type(rec_ts) is not int or rec_ts <= 0:
                    reasons.append(f'source_case_{c_label}_invalid_l2_quote_received_ts')
                elif type(read_at) is int and rec_ts > read_at:
                    reasons.append(f'source_case_{c_label}_l2_quote_received_after_read_at')
                exch_times = l2_quote.get('exchange_times_ms')
                leg_rec = l2_quote.get('leg_received_ts_ms')
                if (not isinstance(exch_times, dict) or set(exch_times) != {'short', 'long'}
                        or not isinstance(leg_rec, dict) or set(leg_rec) != {'short', 'long'}):
                    reasons.append(f'source_case_{c_label}_invalid_l2_quote_clocks')
                else:
                    clocks_bad = False
                    if type(val_ts) is int and val_ts > 0 and type(rec_ts) is int and rec_ts > 0 and rec_ts > val_ts:
                        clocks_bad = True
                    for leg_key in ('short', 'long'):
                        et = exch_times.get(leg_key)
                        lt = leg_rec.get(leg_key)
                        if type(et) is not int or et <= 0 or type(lt) is not int or lt <= 0:
                            clocks_bad = True
                            break
                        if type(read_at) is int and (et > read_at or lt > read_at):
                            clocks_bad = True
                            break
                        if et > lt or (type(rec_ts) is int and rec_ts > 0 and lt > rec_ts):
                            clocks_bad = True
                            break
                    if clocks_bad:
                        reasons.append(f'source_case_{c_label}_invalid_l2_quote_clocks')
                gap_r = l2_quote.get('gap_reasons')
                q_gap_r = l2_quote.get('quote_gap_reasons')
                if (gap_r is not None and not isinstance(gap_r, list)) or (q_gap_r is not None and not isinstance(q_gap_r, list)):
                    reasons.append(f'source_case_{c_label}_l2_quote_gaps')
                elif gap_r or q_gap_r:
                    reasons.append(f'source_case_{c_label}_l2_quote_gaps')
        if len(case_sides) != 2 or set(case_sides) != {'put', 'call'}:
            reasons.append('source_cases_sides_mismatch')
        if len(case_expiries) > 1:
            reasons.append('source_cases_expiry_mismatch')
    checks['source_case_sides'] = [s for s in case_sides if isinstance(s, str)]

    instruments = source.get('instruments')
    proven = set()
    if not isinstance(instruments, list):
        reasons.append('source_instruments_missing')
    else:
        proven = {
            r.get('instrument_name')
            for r in instruments
            if isinstance(r, dict)
            and expiry_ms is not None
            and r.get('expiration_timestamp') == expiry_ms
            and r.get('settlement_currency') == 'BTC'
            and r.get('is_active') is True
            and isinstance(r.get('instrument_name'), str)
            and r.get('instrument_name')
        }
        if len(wanted_instruments) != 4 or not (wanted_instruments <= proven):
            reasons.append('source_instruments_incomplete_or_inactive')
    checks['source_instruments_proven'] = len(wanted_instruments & proven) if wanted_instruments else 0

    band_snapshot = source.get('band_snapshot')
    has_band = isinstance(band_snapshot, dict) and bool(band_snapshot)
    checks['source_has_band_snapshot'] = has_band
    if not has_band:
        reasons.append('source_band_snapshot_missing')
        checks['source_band_freezable'] = False
    elif card_reads:
        band_start = min(card_reads.values()) - 4 * dossier.player.ard.HOUR
        band_ok = dossier.player.ard.frozen_band(band_snapshot, band_start) is not None
        checks['source_band_freezable'] = band_ok
        if not band_ok:
            reasons.append('source_band_snapshot_unfreezable')
    else:
        checks['source_band_freezable'] = None

    index_path = source.get('index_path')
    points = index_path.get('points') if isinstance(index_path, dict) else None
    valid_points_shape = isinstance(points, list) and all(
        isinstance(pt, (list, tuple)) and len(pt) == 2 for pt in points
    )
    checks['source_index_points_count'] = len(points) if isinstance(points, list) else 0
    if not valid_points_shape or not points:
        reasons.append('source_index_path_points_missing')
    if (isinstance(index_path, dict) and expiry_ms is not None
            and index_path.get('expiry_ms') is not None and index_path.get('expiry_ms') != expiry_ms):
        reasons.append('source_index_path_expiry_mismatch')

    history = source.get('history')
    checks['source_ard_strict_observations'] = None
    checks['source_ard_strict_unclocked'] = None
    if not isinstance(history, list) or any(not isinstance(r, dict) for r in history):
        reasons.append('source_history_missing_or_invalid')
    elif expiry_ms is not None and card_reads and valid_points_shape:
        ard_strict = dossier.player.ard.analyze(
            history,
            index_path,
            expiry_ms=expiry_ms,
            cutoff_ms=min(card_reads.values()),
            band_snapshot=band_snapshot if isinstance(band_snapshot, dict) else None,
            strict_asof=True,
        )
        obs_rows = ard_strict.get('observations') or []
        checks['source_ard_strict_observations'] = len(obs_rows)
        checks['source_ard_strict_unclocked'] = ard_strict.get('unclocked_rows', 0)
        expiry_rows = [r for r in history if r.get('expiry_ms') == expiry_ms]
        if expiry_rows and len(obs_rows) == 0:
            reasons.append('source_history_no_native_clocked_rows')


def _check_source_workbench_consistency(source, workbench_view, reasons):
    cases = source.get('cases')
    candidates = workbench_view.get('candidates')
    if not isinstance(cases, list) or not isinstance(candidates, list):
        return
    cases_by_side = {}
    for case in cases:
        if not isinstance(case, dict):
            continue
        l1 = dossier.player.values(case, 'L1.contract')
        c_side = l1.get('side') if isinstance(l1, dict) else None
        if c_side in {'put', 'call'} and c_side not in cases_by_side:
            cases_by_side[c_side] = l1
    for side in ('put', 'call'):
        src_l1 = cases_by_side.get(side)
        cand = next((it for it in candidates if isinstance(it, dict) and it.get('side') == side), None)
        if not isinstance(src_l1, dict) or not isinstance(cand, dict):
            continue
        snap = cand.get('snapshot')
        if not isinstance(snap, dict):
            continue
        wb_contract = snap.get('contract')
        if not isinstance(wb_contract, dict):
            continue
        wb_exp = wb_contract.get('expiry_ms')
        src_exp = src_l1.get('expiry_ms')
        if wb_exp is not None and src_exp is not None and wb_exp != src_exp:
            reasons.append('source_workbench_expiry_mismatch')
        wb_short = wb_contract.get('short_instrument')
        wb_long = wb_contract.get('long_instrument')
        wb_side = wb_contract.get('side')
        if (
            (wb_short is not None and wb_short != src_l1.get('short_instrument'))
            or (wb_long is not None and wb_long != src_l1.get('long_instrument'))
            or (wb_side is not None and wb_side != side)
        ):
            reasons.append(f'source_workbench_contract_mismatch:{side}')


def evaluate_source_readiness(source=None, *, workbench_view=None, now_ms=None, max_view_age_ms=120_000):
    """Pure read-only readiness gate verifying upstream discovery/source completeness without network or disk I/O."""
    has_source = isinstance(source, dict)
    has_view = isinstance(workbench_view, dict)
    checks = {
        'workbench_view_checked': False,
        'source_checked': False,
        'live_lock_eligible_by_side': {},
        'accept_lock_ready': False,
    }
    if not has_source and not has_view:
        return {'status': 'UPSTREAM_BLOCKED', 'reasons': ['missing_input'], 'checks': checks}

    reasons = []
    if source is not None and not has_source:
        reasons.append('source_invalid_type')
    if workbench_view is not None and not has_view:
        reasons.append('workbench_view_invalid_type')
    if now_ms is not None and (type(now_ms) is not int or now_ms <= 0):
        reasons.append('invalid_now_ms')
    if type(max_view_age_ms) is not int or max_view_age_ms <= 0:
        reasons.append('invalid_max_view_age_ms')

    if has_view:
        _check_workbench_view_readiness(
            workbench_view, reasons, checks, now_ms=now_ms, max_view_age_ms=max_view_age_ms
        )
    if has_source:
        _check_source_bundle_readiness(source, now_ms, reasons, checks)
    if has_source and has_view:
        _check_source_workbench_consistency(source, workbench_view, reasons)

    deduped = list(dict.fromkeys(reasons))
    status = 'SOURCE_READY' if not deduped else 'UPSTREAM_BLOCKED'
    checks['accept_lock_ready'] = bool(
        status == 'SOURCE_READY'
        and has_view
        and all(checks['live_lock_eligible_by_side'].get(s) is True for s in ('put', 'call'))
    )
    return {'status': status, 'reasons': deduped, 'checks': checks}


def prewarm(plan, archive, service, *, clock=dossier.now_ms, public=dossier.public, dry_run=False):
    prompt_version = plan.get('prompt_version') or review.INITIAL_SYSTEM_PROMPT_VERSION
    if prompt_version not in review.INITIAL_SYSTEM_PROMPTS:
        raise ValueError(f'unsupported plan prompt_version: {prompt_version!r}')
    p0_profile = getattr(dossier.player, 'INPUT_PROFILE_P0_STRICT', 'p0_strict@1')
    legacy_profile = getattr(dossier.player, 'INPUT_PROFILE_LEGACY', 'legacy_v1')
    input_profile = plan.get('input_profile') or (
        p0_profile if prompt_version == review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION else legacy_profile
    )
    supported_profiles = getattr(dossier.player, 'SUPPORTED_INPUT_PROFILES', (legacy_profile, p0_profile))
    if input_profile not in supported_profiles:
        raise ValueError(f'unsupported plan input_profile: {input_profile!r}')
    mechanical_contract_version = (
        dossier.condition.MECHANICAL_CONTRACT_VERSION
        if prompt_version in review.CANDIDATE_STRICT_PROMPT_VERSIONS
        else None
    )
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
                  prompt_version=prompt_version,
                  input_profile=input_profile,
                  mechanical_contract_version=mechanical_contract_version,
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


_SYNC_OPEN_FAILED_STATES = {'LINK_UNVERIFIED', 'LOCK_BLOCKED', 'TRACKER_EXPORT_UNAVAILABLE'}
_SYNC_OPEN_FAILED_STATUSES = {
    'DEAL_BLOCKED', 'NO_EVIDENCE', 'LLM_NOT_CONFIGURED', 'DAILY_BUDGET_EXHAUSTED',
    'TIMEOUT', 'REQUEST_FAILED', 'PARSE_FAILED', 'LATE', 'REPLY_WINDOW_CLOSED', 'ERROR',
}


def classify_sync_open_batch(rows):
    """Classify sync-open batch outcomes into ALL_OK / DEGRADED / FAILED without hiding per-hand errors."""
    items = [r for r in (rows or []) if isinstance(r, dict)]
    failed = []
    for row in items:
        is_fail = (
            row.get('state') in _SYNC_OPEN_FAILED_STATES
            or row.get('status') in _SYNC_OPEN_FAILED_STATUSES
            or row.get('action') == 'RECOVERY_PENDING'
            or bool(row.get('error'))
        )
        if is_fail:
            failed.append(dict(
                hand_id=row.get('hand_id'),
                dossier_id=row.get('dossier_id'),
                phase=row.get('phase'),
                state=row.get('state') or row.get('status') or row.get('action'),
                reason=row.get('reason') or row.get('error'),
            ))
    total = len(items)
    failed_count = len(failed)
    ok_count = total - failed_count
    if failed_count == 0:
        batch_status = 'ALL_OK'
    elif ok_count > 0:
        batch_status = 'DEGRADED'
    else:
        batch_status = 'FAILED'
    return dict(
        batch_status=batch_status,
        total=total,
        ok_count=ok_count,
        failed_count=failed_count,
        failed_hands=failed,
    )


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
            if args.command == 'auto-deal':
                out = auto_deal(d, dossier.read(args.plan), args.archive, service)
            elif args.command == 'sync-open':
                out = recover_initial(d, service)
                for request_file in sorted((d.root / 'dossiers').glob('*/hands/hand_*/tracking/request.json')):
                    hand = request_file.parent.parent
                    try:
                        if (request_file.parent / 'link.json').exists() or _link_blocked(request_file.parent):
                            continue
                        expiry = load(hand / 'expected.json')['expiry_ms']
                        if d.clock() < expiry:
                            out.append(_link_with_fresh_quote(d, hand, service))
                            with _short_writer(d):
                                d.summary(expiry)
                    except (OSError, ValueError, KeyError, TypeError) as exc:
                        out.append(dict(
                            state='LINK_UNVERIFIED',
                            hand_id=hand.name,
                            dossier_id=hand.parent.parent.name,
                            phase='request',
                            error=type(exc).__name__,
                            reason=getattr(exc, 'code', type(exc).__name__),
                        ))
                for link_file in sorted((d.root / 'dossiers').glob('*/hands/hand_*/tracking/link.json')):
                    hand = link_file.parent.parent
                    try:
                        terminal = optional(link_file.parent / 'terminal.json')
                        if terminal:
                            meta = load(link_file.parent / 'exports' / dossier.key(terminal['export_id']) / 'manifest.json')
                            if meta.get('settlement_state') == 'settled':
                                continue
                        out.append(sync(d, hand, service, check_due=args.check_due,
                                        archive_root=args.archive, run_review=args.review))
                        with _short_writer(d):
                            d.summary(load(hand / 'expected.json')['expiry_ms'])
                    except (OSError, ValueError, KeyError, TypeError) as exc:
                        try:
                            with _short_writer(d):
                                d.note(hand, 'TRACKER_EXPORT_UNAVAILABLE', getattr(exc, 'code', type(exc).__name__))
                                d.summary(load(hand / 'expected.json')['expiry_ms'])
                        except Exception:
                            pass
                        out.append(dict(
                            state='TRACKER_EXPORT_UNAVAILABLE',
                            hand_id=hand.name,
                            dossier_id=hand.parent.parent.name,
                            phase='sync',
                            error=type(exc).__name__,
                            reason=getattr(exc, 'code', type(exc).__name__),
                        ))
            else:
                hand = hand_path(args.root, args.dossier, args.hand)
                out = (_link_with_fresh_quote(d, hand, service, dossier.read(args.condition) if args.condition else None)
                       if args.command == 'link'
                       else sync(d, hand, service, check_due=args.check_due,
                                 archive_root=args.archive, run_review=args.review))
                with _short_writer(d):
                    d.summary(load(hand / 'expected.json')['expiry_ms'])
        print(dossier.encode(out).decode('utf-8'), end='')
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f'{getattr(exc, "code", type(exc).__name__)}\n')
    if args.command == 'sync-open' and isinstance(out, list):
        batch = classify_sync_open_batch(out)
        if batch['batch_status'] != 'ALL_OK':
            import sys
            sys.stderr.write(json.dumps(batch, ensure_ascii=False) + '\n')
            parser.exit(2)
    if args.command == 'auto-deal' and any(row.get('status') in {
        'DEAL_BLOCKED', 'NO_EVIDENCE', 'LLM_NOT_CONFIGURED', 'DAILY_BUDGET_EXHAUSTED',
        'TIMEOUT', 'REQUEST_FAILED', 'PARSE_FAILED', 'LATE', 'REPLY_WINDOW_CLOSED',
    } or (row.get('status') == 'AUTO_ATTEMPTED' and row.get('last_status') != 'VALID')
      or (row.get('link') or {}).get('state') in {'LOCK_BLOCKED', 'LINK_UNVERIFIED'} for row in out):
        parser.exit(2)
    if isinstance(out, dict) and out.get('state') in {'LOCK_BLOCKED', 'LINK_UNVERIFIED', 'NO_TRACKING_REQUEST'}:
        parser.exit(2)


if __name__ == '__main__':
    main()
