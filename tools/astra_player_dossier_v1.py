"""Local append-only dossiers for the natural-language shadow player."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from urllib.parse import urlencode
from urllib.request import urlopen

try:
    from . import astra_commercial_math_v17 as cash, astra_level_behaviour_v1 as levels
    from . import astra_player_condition_v1 as condition, astra_player_input_v1 as player
    from . import astra_research_archive_v1 as archive
except ImportError:
    import astra_commercial_math_v17 as cash
    import astra_level_behaviour_v1 as levels
    import astra_player_condition_v1 as condition
    import astra_player_input_v1 as player
    import astra_research_archive_v1 as archive


DAY, MINUTE = 86_400_000, 60_000
RESEARCH = 'AI Player v1 / ARD closeout 2026-11-15'
PARSER = 'public-excerpts@1'
EVENTS = {'OBSERVATION', 'REVIEW_REQUIRED', 'CONDITION_TRIGGERED', 'CONDITION_NOT_TRIGGERED',
          'CONDITION_UNKNOWN', 'AI_HOLD', 'AI_REVOKE', 'AI_HOLD_UNKNOWN',
          'HOLD_REVIEW_FAILED', 'HOLD_REVIEW_QUEUED', 'HOLD_REVIEW_SKIPPED',
          'SHADOW_ACTION', 'SIMULATED_ORDER', 'CORRECTION'}
MANUAL_EVENTS = {kind for kind in EVENTS if not kind.startswith('CONDITION_')}
KINDS = {'SCHEDULED', 'STARTUP_AD_HOC', 'AD_HOC', 'HISTORICAL_REPLAY', 'FIXTURE'}


def now_ms():
    return time.time_ns() // 1_000_000


def utc(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def identity(prefix, *values):
    return prefix + '_' + sha(encode(values))[:24]


def key(value):
    if not re.fullmatch(r'[a-zA-Z0-9_@-]{1,90}', value):
        raise ValueError('invalid record key')
    return value


def read(path):
    return json.loads(path.read_bytes())


def rows(path):
    return [json.loads(line) for line in path.read_bytes().splitlines() if line.strip()] if path.exists() else []


def attempts(hand):
    # A killed writer can leave an unpublished stage beneath responses/.
    records = (p for p in (hand / 'responses').glob('*/attempt.json') if not p.parent.name.startswith('.'))
    return sorted(records, key=lambda p: read(p)['ordinal'])


def publish(folder, files):
    """Atomically publish a complete evidence bundle under the local writer lock."""
    folder.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.stage-', dir=folder.parent))
    try:
        for name, data in files.items():
            with (stage / name).open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        stage.rename(folder)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def write(path, data, *, replace=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not replace:
        if path.read_bytes() != data:
            raise ValueError(f'immutable object conflict: {path.name}')
        return
    fd, temp = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


@contextmanager
def writer(root, *, timeout_s: float = 0.0):
    root.mkdir(parents=True, exist_ok=True)
    if (root / 'chain.jsonl').exists() or (root / '_state' / 'state.json').exists():
        raise ValueError('choose a separate local dossier directory')
    with (root / '.writer.lock').open('a+b') as lock:
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt

                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)
        yield  # closing the descriptor also releases the lock after process failure


def public(method, **params):
    if method not in {'get_instruments', 'get_index_chart_data', 'get_delivery_prices'}:
        raise ValueError('unsupported market data endpoint')
    with urlopen(archive.PUBLIC + 'public/' + method + '?' + urlencode(params), timeout=15) as response:
        result = json.load(response)
    return result['result']


DAILY_SCHEDULES = {
    'r2': (14 * 60 * MINUTE, 18 * 60 * MINUTE),
    'r16': (16 * 60 * MINUTE, 16 * 60 * MINUTE),
}


def schedule(plan, at):
    if plan['research'] != RESEARCH or plan['mode'] != 'SHADOW_ONLY':
        raise ValueError('invalid shadow plan')
    first = plan['effective_at_ms']
    expected = DAILY_SCHEDULES.get(plan.get('candidate_round'))
    if (expected is None or first % DAY != expected[0]
            or plan['expiry_offset_ms'] != expected[1]
            or not 0 < plan['deal_grace_ms'] <= 40 * MINUTE
            or not 0 < plan['reply_grace_ms'] <= DAY):
        raise ValueError('invalid daily r2 schedule')
    return range(first, at + 1, DAY)


def seal_source(cases, cards, index_path, history, instruments=None, *, received_at_ms=None,
                source='local archive', band_snapshot=None):
    out = dict(cases=cases, cards=cards, index_path=index_path, history=history, instruments=instruments,
               received_at_ms=received_at_ms, source=source)
    if band_snapshot is not None:
        out['band_snapshot'] = band_snapshot
    return out


def _normalize_legacy_structure_row(r):
    if r.get('schema') == 'structure_snapshot@0.1' and ('available_at_ms' not in r or r.get('available_at_ms') is None):
        at = r.get('at_ms')
        if player.ard.clock(at):
            pin = player.ard.num(((r.get('profile') or {}).get('walls') or {}).get('pin'))
            return {
                **r,
                'available_at_ms': at,
                'pin_source': r.get('pin_source') or archive.expiry_structure.PIN_SOURCE,
                'pin_method': r.get('pin_method') or archive.expiry_structure.PIN_METHOD,
                'pin_status': r.get('pin_status') or ('ok' if pin is not None and pin > 0 else 'unavailable'),
                'compat_backfill_applied': True,
                'backfill_source': 'structure_snapshot.at_ms',
            }
    return r


def collect_live_structure_rows(candidates, expiry, max_read_ms):
    """Build native structure_snapshot@0.1 rows from usable L3.expiry_walls without backfilling gaps."""
    out = []
    seen = set()
    for case in candidates or ():
        row = player.entry(case, 'L3.expiry_walls')
        asof = row.get('asof_ms')
        if not player.ard.clock(asof) or not player.usable(row, max_read_ms, archive.expiry_structure.GAP_MS):
            continue
        wall = row.get('values') or {}
        if wall.get('expiry_ms') != expiry or wall.get('stale') is True:
            continue
        pin = player.number((wall.get('walls') or {}).get('pin'))
        if pin is None or pin <= 0:
            continue
        pin_method = row.get('pin_method') or wall.get('pin_method') or archive.expiry_structure.PIN_METHOD
        pin_source = row.get('pin_source') or wall.get('pin_source') or archive.expiry_structure.PIN_SOURCE
        pin_status = row.get('pin_status') or wall.get('pin_status') or 'ok'
        dedup_key = (asof, pin, pin_method, pin_source)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        profile = {'walls': dict(wall.get('walls') or {'pin': pin})}
        for k in ('top', 'totals'):
            if isinstance(wall.get(k), dict):
                profile[k] = wall[k]
        snap = {
            'schema': archive.expiry_structure.SNAPSHOT_SCHEMA,
            'expiry_ms': expiry,
            'at_ms': asof,
            'available_at_ms': asof,
            'pin_source': pin_source,
            'pin_method': pin_method,
            'pin_status': pin_status,
            'source_layer': 'L3.expiry_walls',
            'profile': profile,
        }
        for k in ('atm_iv_pct', 'expected_move_usd', 'forward_usd'):
            val = player.number(wall.get(k))
            if val is not None:
                snap[k] = val
        out.append(snap)
    out.sort(key=lambda r: r['at_ms'])
    return out


def _resolve_band_snapshot(archive_root, cutoff_ms, band_snapshot=None):
    if band_snapshot is not None:
        return band_snapshot
    start = cutoff_ms - 4 * 60 * MINUTE
    root = Path(archive_root)
    direct = root / 'band_snapshot.json'
    if direct.is_file():
        try:
            cand = read(direct)
            if player.ard.frozen_band(cand, start) is not None:
                return cand
        except Exception:
            pass
    band_days = []
    for ts in (start, cutoff_ms):
        day_str = utc(ts).date().isoformat()
        if day_str not in band_days:
            band_days.append(day_str)
    archived_paths = [root / 'band_snapshots' / f'{day_str}.jsonl' for day_str in band_days]
    fmz_env = os.environ.get('ASTRA_WORKBENCH_SNAPSHOTS_JSONL')
    extra_paths = ([Path(fmz_env)] if fmz_env else []) + [root.parent / 'native-fmz.jsonl']
    for candidate_path in archived_paths + extra_paths:
        if candidate_path.is_file():
            try:
                for row in reversed(rows(candidate_path)):
                    if player.ard.frozen_band(row, start) is not None:
                        return row
            except Exception:
                pass
    return None


def collect(archive_root, expiry, slot, *, startup=False, clock=now_ms, band_snapshot=None, candidate_round='r2'):
    """Use the existing archive's native cases. Public reads fill the current path and expiry proof."""
    day = utc(expiry).date().isoformat()
    case_dir = archive_root / 'cases' / utc(slot).date().isoformat()
    candidates = []
    for side in ('put', 'call'):
        matches = list(case_dir.glob(f'{utc(slot):%H}_{side}_*.json.gz'))
        if len(matches) != 1:
            raise LookupError(f'需同一个采集小时的唯一 {side} 原生案卷，现有 {len(matches)} 份')
        candidates.append(player.read_json(matches[0]))
    reads = [case['build']['built_at_ms'] for case in candidates]
    contracts = [player.values(case, 'L1.contract') for case in candidates]
    if any(c['expiry_ms'] != expiry for c in contracts):
        raise LookupError('当前引擎候选到期与目标不符')
    if startup and clock() - min(reads) > 15 * MINUTE:
        raise LookupError('当前小时案卷距启动过久，等下一次原生采集')
    instruments = public('get_instruments', currency='BTC', kind='option', expired='false')
    wanted = {c[f'{leg}_instrument'] for c in contracts for leg in ('short', 'long')}
    instrument_proof = [r for r in instruments if r.get('instrument_name') in wanted]
    if {r['instrument_name'] for r in instrument_proof if r.get('expiration_timestamp') == expiry
            and r.get('settlement_currency') == 'BTC' and r.get('is_active')} != wanted:
        raise LookupError('Deribit 未核实四条原生腿和目标到期')
    points = archive.Deribit().index_path()
    received = clock()
    history = [_normalize_legacy_structure_row(r) for r in rows(archive_root / 'structure' / f'{day}.jsonl')
               if r.get('expiry_ms') == expiry and r['at_ms'] <= max(reads)]
    covered_at = {r.get('at_ms') for r in history if isinstance(r.get('at_ms'), int)}
    for live_row in collect_live_structure_rows(candidates, expiry, max(reads)):
        if live_row['at_ms'] not in covered_at:
            history.append(live_row)
            covered_at.add(live_row['at_ms'])
    history.sort(key=lambda r: (r.get('available_at_ms') if isinstance(r.get('available_at_ms'), int) else r.get('at_ms') or 0,
                                r.get('at_ms') or 0))
    known = [r for r in history if r.get('available_at_ms') is not None and r['available_at_ms'] <= max(reads)]
    cards = []
    existing = rows(archive_root / 'levels' / f'{day}.jsonl')
    for case, c, tick in zip(candidates, contracts, reads):
        native = [r for r in existing if r.get('schema') == levels.CARD_SCHEMA and r.get('round') == candidate_round
                  and r.get('case_id') == case['case_id'] and r.get('backfilled') is False]
        if len(native) == 1:
            cards.append(native[0])
            continue
        late_scheduled = not startup and clock() >= slot + 15 * MINUTE
        if not startup and not late_scheduled:
            raise LookupError(f"等待 {c['side']} 原生 {candidate_round} 位置卡")
        cut = [p for p in points if p[0] <= tick]
        if not cut or tick - cut[-1][0] > levels.PATH_EDGE_MS:
            raise LookupError('候选读取时的指数路径未覆盖')
        snaps = [r for r in known if r['available_at_ms'] <= tick]
        gap = 'startup_card_from_native_case' if startup else 'scheduled_card_from_native_case'
        cards.append(levels.build_card(expiry_ms=expiry, rnd=candidate_round, side=c['side'], read_at_ms=tick,
                                     walls=player.values(case, 'L3.expiry_walls'), points=cut, case=case,
                                     contract=c, snapshots=snaps, extra_gaps=[gap],
                                     timestamp_semantics='unverified_minute_label'))
    path = dict(schema='index_path@0.1', index_name='btc_usd', expiry_ms=expiry, fetched_at_ms=received,
                timestamp_semantics='unverified_minute_label',
                points=[p for p in points if p[0] <= max(reads)])
    resolved_band = _resolve_band_snapshot(archive_root, min(reads), band_snapshot=band_snapshot)
    return seal_source(candidates, cards, path, history, instrument_proof, received_at_ms=received,
                       source='native research archive + Deribit public read', band_snapshot=resolved_band)


class Dossiers:
    """Every public mutation runs under writer(root) in the CLI."""

    def __init__(self, root, clock=now_ms, *, require_reply_provenance=False):
        self.root, self.clock = Path(root), clock
        self.require_reply_provenance = require_reply_provenance

    def path(self, expiry):
        return self.root / 'dossiers' / f'Deribit_BTC_{utc(expiry):%Y%m%dT%H%M%SZ}'

    def expect(self, expiry, kind, *, plan=None, slot=None, reference=None):
        if kind not in KINDS or type(expiry) is not int or expiry <= 0:
            raise ValueError('invalid hand identity')
        if plan is not None:
            write(self.root / 'plans' / (key(plan['plan_id']) + '.json'), encode(plan))
        if kind == 'SCHEDULED':
            if (plan is None or slot not in schedule(plan, self.clock()) or slot + plan['expiry_offset_ms'] != expiry):
                raise ValueError('not a due slot of this plan')
            reference = slot
        elif reference is None:
            raise ValueError('non-scheduled observations require a stable operator reference')
        folder = self.path(expiry)
        hand_id = identity('hand', 'Deribit', 'BTC', expiry, kind, reference)
        target = folder / 'hands' / hand_id / 'expected.json'
        expected = dict(hand_id=hand_id, dossier_id=folder.name, expiry_ms=expiry, sample=kind, plan=plan,
                        scheduled_at_ms=slot, reference=reference)
        if target.exists():
            old = read(target)
            if {k: old[k] for k in expected} != expected:
                raise ValueError('existing schedule identity has different configuration')
        else:
            write(target, encode(dict(expected, recorded_at_ms=self.clock())))
        return target.parent

    def note(self, hand, state, reason):
        name = identity('note', state, reason) + '.json'
        out = hand / 'issuance' / name
        if not out.exists():
            write(out, encode(dict(state=state, reason=reason, recorded_at_ms=self.clock())))

    def deal(self, hand, source):
        target = hand / 'evidence'
        if target.exists():
            self.verify_evidence(hand)
            return target / 'input.md'
        exp = read(hand / 'expected.json')
        at = self.clock()
        expiry = exp['expiry_ms']
        reads = [c['read_at_ms'] for c in source['cards']]
        replay = exp['sample'] in {'HISTORICAL_REPLAY', 'FIXTURE'}
        if any(t > at for t in reads) or any(c['expiry_ms'] != expiry for c in source['cards']):
            raise ValueError('candidate cutoff or expiry mismatch')
        if not replay:
            if at >= expiry or not source.get('instruments') or not max(reads) <= source['received_at_ms'] <= at:
                raise ValueError('missing current native instrument or availability evidence')
            if exp['sample'] == 'SCHEDULED':
                slot, grace = exp['scheduled_at_ms'], exp['plan']['deal_grace_ms']
                if not slot <= min(reads) <= max(reads) <= at < slot + grace:
                    raise ValueError('outside planned capture window')
            elif at - min(reads) > 15 * MINUTE:
                raise ValueError('startup capture older than 15 minutes')
            proof = {r['instrument_name'] for r in source['instruments'] if r.get('expiration_timestamp') == expiry
                     and r.get('settlement_currency') == 'BTC' and r.get('is_active')}
            wanted = {player.values(c, 'L1.contract')[leg] for c in source['cases']
                      for leg in ('short_instrument', 'long_instrument')}
            if not wanted <= proof:
                raise ValueError('missing native leg/expiry proof')
        text = player.build_input(*source['cases'], *source['cards'], index_path=source['index_path'],
                                  ard_history=source['history'], ard_band_snapshot=source.get('band_snapshot'))
        raw, inputs = text.replace('\n', os.linesep).encode('utf-8'), encode(source)
        files = [Path(module.__file__) for module in (player, player.ard, condition, levels)]
        code = {p.name: sha(p.read_bytes()) for p in files}
        rules = text.split('## 怎么读', 1)[1].split('## 本局牌面', 1)[0] + text.split('## 读完后另写', 1)[1]
        meta = dict(research=RESEARCH, source_sha256=sha(inputs), input_sha256=sha(raw),
                    rules_sha256=sha(rules.encode()), generator=code, generated_at_ms=at,
                    source_observed_at_ms=reads, source_bundle_available_at_ms=source['received_at_ms'],
                    hand_frozen_at_ms=self.clock())
        publish(target, {'input.md': raw, 'source.json': inputs, 'identity.json': encode(meta)})
        return target / 'input.md'

    def verify_evidence(self, hand):
        folder = hand / 'evidence'
        meta = read(folder / 'identity.json')
        if any(sha((folder / f).read_bytes()) != meta[k] for f, k in
               (('input.md', 'input_sha256'), ('source.json', 'source_sha256'))):
            raise ValueError('frozen input or source hash mismatch')
        return meta

    def tick(self, plan, source_for=None, *, slots=None):
        at, output = self.clock(), []
        slots = schedule(plan, at) if slots is None else slots
        write(self.root / 'plans' / (key(plan['plan_id']) + '.json'), encode(plan))
        for slot in slots:
            expiry = slot + plan['expiry_offset_ms']
            hand = self.expect(expiry, 'SCHEDULED', plan=plan, slot=slot)
            if (hand / 'evidence').exists():
                meta = self.verify_evidence(hand)
                if at >= self.reply_deadline(read(hand / 'expected.json'), meta) and not self.select(hand):
                    self.note(hand, 'REPLY_WINDOW_CLOSED', '截止前没有有效回复，保留所有尝试')
            elif at >= slot + plan['deal_grace_ms']:
                self.note(hand, 'MISSED', '计划发牌窗口已结束')
            elif source_for:
                try:
                    self.deal(hand, source_for(expiry, slot))
                except (OSError, LookupError, ValueError, player.HandNotDealt, KeyError) as error:
                    self.note(hand, 'POLICY_BLOCKED' if isinstance(error, player.HandNotDealt) else 'DATA_BLOCKED', str(error))
            output.append(hand)
        return output

    @staticmethod
    def reply_deadline(exp, meta):
        if exp['sample'] in {'HISTORICAL_REPLAY', 'FIXTURE'}:
            return None
        grace = exp['plan']['reply_grace_ms'] if exp['sample'] == 'SCHEDULED' else 20 * MINUTE
        return min(exp['expiry_ms'], meta['hand_frozen_at_ms'] + grace)

    def select(self, hand):
        for path in attempts(hand):
            rec = read(path)
            if rec['status'] != 'VALID':
                continue
            if sha((path.parent / 'response.raw').read_bytes()) != rec['raw_sha256']:
                raise ValueError('response hash mismatch')
            selected = {k: rec[k] for k in ('attempt_id', 'raw_sha256', 'parser', 'judgment', 'configuration_id',
                                          'decided_at_ms', 'recorded_at_ms', 'channel', 'model')}
            selected['response'] = str(path.parent.relative_to(hand).as_posix()) + '/response.raw'
            write(hand / 'decision.json', encode(selected))
            return selected
        return None

    def reparse(self, hand, attempt, annotation, reason):
        self.verify_evidence(hand)
        target = hand / 'responses' / key(attempt)
        original = read(target / 'attempt.json')
        raw = (target / 'response.raw').read_bytes()
        if not reason or sha(raw) != original['raw_sha256']:
            raise ValueError('reparse needs original bytes and a reason')
        judgment, error = None, None
        try:
            judgment = parse(raw, annotation)
        except (ValueError, UnicodeError, TypeError) as exc:
            error = str(exc)
        data = dict(prior_attempt=attempt, parser=PARSER, raw_sha256=original['raw_sha256'], reason=reason,
                    judgment=judgment, error=error, annotation=annotation, changes_selected_decision=False)
        path = target / 'derivations' / (identity('parse', data) + '.json')
        if not path.exists():
            write(path, encode(dict(data, recorded_at_ms=self.clock())))
        return path

    def respond(self, hand, attempt, status, raw=None, annotation=None, *, model=None, configuration=None,
                requested=None, decided=None, channel='MANUAL_IMPORT', error=None, request_payload=None,
                full_raw=None, condition_plan=None):
        meta, at = self.verify_evidence(hand), self.clock()
        exp = read(hand / 'expected.json')
        folder = hand / 'responses' / key(attempt)
        payload_hash = sha(raw) if raw is not None else None
        request_bytes = encode(request_payload) if request_payload is not None else None
        request_hash = sha(request_bytes) if request_bytes is not None else None
        declared = dict(raw_sha256=payload_hash, annotation=annotation, input_status=status, model=model,
                        configuration=configuration, channel=channel, requested_at_ms=requested, decided_at_ms=decided)
        if request_hash is not None:
            declared['request_sha256'] = request_hash
        if full_raw is not None:
            declared['full_raw_sha256'] = sha(full_raw)
        if folder.exists():
            old = read(folder / 'attempt.json')
            if any(old.get(k) != v for k, v in declared.items()):
                raise ValueError('attempt identity reused with different reply or metadata')
            self.select(hand)
            return old
        known = [t for t in (requested, decided) if t is not None]
        if any(type(t) is not int or not meta['hand_frozen_at_ms'] <= t <= at for t in known) or known != sorted(known):
            raise ValueError('invalid response clocks')
        siblings = [read(p) for p in attempts(hand)]
        judgment, record_error = None, None
        if status == 'RECEIVED' and raw is not None:
            try:
                judgment = parse(raw, annotation)
            except (UnicodeError, ValueError, TypeError) as exc:
                status, record_error = 'PARSE_FAILED', str(exc)
            else:
                deadline = self.reply_deadline(exp, meta)
                status = 'LATE' if deadline is not None and at >= deadline else 'VALID'
                if (status == 'VALID' and deadline is not None and self.require_reply_provenance
                        and not (requested is not None and decided is not None and model_identity(model))):
                    status, record_error = 'PROVENANCE_MISSING', 'actual model, request and completion clocks required'
        elif status not in {'TIMEOUT', 'NO_RESPONSE', 'REQUEST_FAILED', 'PARSE_FAILED'} or raw is not None:
            raise ValueError('invalid response status or raw payload')
        elif isinstance(error, str) and error.strip():
            record_error = str(error)[:240]
        if payload_hash is not None and any(r['status'] == 'VALID' and r['raw_sha256'] == payload_hash for r in siblings):
            status = 'DUPLICATE'
        config_id = identity('config', RESEARCH, exp['plan'] and exp['plan']['plan_id'],
                             meta['generator'], meta['rules_sha256'], model, configuration, channel)
        record = dict(declared, status=status, attempt_id=attempt, ordinal=len(siblings) + 1,
                      recorded_at_ms=at, input_sha256=meta['input_sha256'], parser=PARSER,
                      configuration_id=config_id, judgment=judgment, error=record_error, hand_id=hand.name)
        publish(folder, {
            'attempt.json': encode(record),
            **({'response.raw': raw} if raw is not None else {}),
            **({'response.full.raw': full_raw} if full_raw is not None else {}),
            **({'condition_plan.json': encode(condition_plan)} if condition_plan is not None else {}),
            **({'request.json': request_bytes} if request_bytes is not None else {}),
        })
        self.select(hand)
        return record

    def episode(self, hand, frozen, entry_path=None, *, tracking=None):
        decision = self.select(hand)
        if not decision or decision['judgment']['action'] != 'Accept':
            raise ValueError('only a selected valid Accept can freeze a shadow episode')
        condition.validate(frozen)
        source = read(hand / 'evidence' / 'source.json')
        side = decision['judgment']['side']
        case, card = next((x, c) for x, c in zip(source['cases'], source['cards']) if c['side'] == side)
        contract = player.bind(case, card, side)
        if any(frozen['contract'].get(k) != contract.get(k) for k in condition.CONTRACT_FIELDS):
            raise ValueError('frozen legs differ from evidence')
        exp = read(hand / 'expected.json')
        if tracking is not None:
            if __package__:
                from . import astra_player_tracking_v13 as tracking_core
            else:
                import astra_player_tracking_v13 as tracking_core
            proof, request = tracking_core.request_proof(hand)
            origin, tracker, point = tracking
            t0 = tracking_core.tracker_t0(origin, tracker, point)
            if (entry_path is not None or origin != proof['origin'] or
                    not request['requested_at_ms'] <= t0['locked_at_ms'] < proof['deadline_ms'] or
                    frozen != tracking_core.condition_at_t0(proof, request['condition'],
                                                           point['candidate']['snapshot'], t0['locked_at_ms'])):
                raise ValueError('shadow tracking t0 does not match frozen authority')
            entry_path = dict(source='native workbench tracker t0', **t0)
        elif exp['sample'] not in {'HISTORICAL_REPLAY', 'FIXTURE'}:
            at = frozen['entry_at_ms']
            if (not decision['recorded_at_ms'] <= at <= self.clock() or not entry_path
                    or entry_path.get('expiry_ms') != exp['expiry_ms'] or entry_path.get('index_name') != 'btc_usd'
                    or not at <= entry_path.get('fetched_at_ms', 0) <= self.clock()
                    or [at, frozen['entry_index_usd']] not in entry_path.get('points', [])):
                raise ValueError('live shadow entry needs a post-decision native index sample and its availability clock')
        basis = frozen.get('ard_basis')
        if bool(basis) != decision['judgment'].get('depends_on_ard', False):
            raise ValueError('ARD attachment must reflect the actual selected answer')
        if basis and encode(basis['facts']) != encode(player.ard.analyze(
                source['history'], source['index_path'], expiry_ms=exp['expiry_ms'],
                cutoff_ms=min(card['read_at_ms'] for card in source['cards']), band_snapshot=source.get('band_snapshot'))):
            raise ValueError('ARD basis differs from frozen evidence')
        episode = identity('episode', hand.name, decision['attempt_id'], contract)
        target = hand.parent.parent / 'episodes' / episode
        if target.exists():
            if read(target / 'condition.json') != frozen or read(target / 'entry_source.json') != entry_path:
                raise ValueError('existing episode has different frozen condition')
            return target
        publish(target, {'episode.json': encode(dict(episode_id=episode, hand_id=hand.name, side=side,
                        recorded_at_ms=self.clock(), mode='SHADOW_DECISION', decision=decision['attempt_id'])),
                         'condition.json': encode(frozen), 'entry_source.json': encode(entry_path)})
        return target

    def event(self, episode, event_id, kind, body, *, observed=None, available=None, decided=None):
        frozen = read(episode / 'condition.json')
        at, entry = self.clock(), frozen['entry_at_ms']
        if kind not in EVENTS or any(type(t) is not int or t > at for t in (observed, available, decided) if t is not None):
            raise ValueError('invalid shadow event or time')
        if observed is not None and available is not None and observed > available:
            raise ValueError('invalid availability order')
        if kind in {'AI_HOLD', 'AI_REVOKE'} and not body.get('public_reply'):
            raise ValueError('save actual public reply for an AI recommendation')
        if kind == 'CORRECTION' and (not body.get('reason') or not
                (episode / 'events' / (key(body['supersedes']) + '.json')).is_file()):
            raise ValueError('a correction needs its prior event and reason')
        p = episode / 'events' / (key(event_id) + '.json')
        data = dict(event_id=event_id, kind=kind, body=body, observed_at_ms=observed,
                    available_at_ms=available, decided_at_ms=decided,
                    scope='PRE_ENTRY_OBSERVATION' if observed is not None and observed <= entry else 'AFTER_ENTRY_OR_UNCLOCKED')
        if p.exists():
            old = read(p)
            if any(old[k] != v for k, v in data.items()):
                raise ValueError('cannot overwrite event, append a correction')
            return old
        data.update(recorded_at_ms=at, episode_id=episode.name, mode='SHADOW_ONLY')
        write(p, encode(data))
        return data

    def check(self, episode, path, tick, *, available=None, ard=None):
        if tick > self.clock() or (available is not None and available < tick):
            raise ValueError('future or invalid check cutoff')
        frozen = episode / 'condition.json'
        # Save only samples used by the checker. Post-check ticks are never part of this event.
        entry = read(frozen)['entry_at_ms']
        supplied = path.get('points')
        if isinstance(supplied, list) and all(isinstance(p, (list, tuple)) and len(p) == 2 and type(p[0]) is int for p in supplied):
            supplied = [p for p in supplied if entry < p[0] <= tick]
        used = dict(path, points=supplied)
        result = condition.check_condition(frozen, used, tick)
        kind = {'triggered': 'CONDITION_TRIGGERED', 'not_triggered': 'CONDITION_NOT_TRIGGERED'}.get(
            result['status'], 'CONDITION_UNKNOWN')
        if ard is not None:
            compare = condition.compare_ard_basis(frozen, ard, tick)
            if compare.get('pin_price_changed') or compare.get('new_neighborhood_events'):
                self.event(episode, identity('review', tick, ard), 'REVIEW_REQUIRED', {'comparison': compare, 'facts': ard},
                           observed=tick, available=available)
        return self.event(episode, identity('check', tick, used, available), kind,
                          {'result': result, 'path_used': used}, observed=tick, available=available)

    def settle(self, expiry, delivery, path=None, *, fixture=False, management=None, supersedes=None, reason=None):
        folder = self.path(expiry)
        if not folder.is_dir() or self.clock() < expiry:
            raise ValueError('no expiry dossier due for settlement')
        previous = folder / 'outcomes'
        if supersedes and (not reason or not (previous / key(supersedes) / 'outcome.json').exists()):
            raise ValueError('outcome revision needs an existing version and reason')
        if path and (path.get('index_name') != 'btc_usd' or path.get('expiry_ms') != expiry):
            raise ValueError('post-hand path identity differs from dossier')
        if delivery is not None:
            if type(delivery) not in (int, float) or not 0 < delivery < float('inf'):
                raise ValueError('delivery must be a positive finite price')
        elif management:
            raise ValueError('management accounting requires a settlement price')
        hands, episodes = [], []
        for hand in sorted((folder / 'hands').glob('hand_*')):
            exp = read(hand / 'expected.json')
            if fixture != (exp['sample'] == 'FIXTURE') or not (hand / 'evidence').is_dir():
                continue
            self.verify_evidence(hand)
            decision = self.select(hand)
            source = read(hand / 'evidence' / 'source.json')
            pair = {}
            for case, card in zip(source['cases'], source['cards']):
                c = player.values(case, 'L1.contract')
                side = c['side']
                start = card['read_at_ms']
                observed = sorted((t, p) for t, p in (path or {}).get('points', [])
                                  if start < t <= expiry and player.number(p) is not None and p > 0)
                summary = None
                if observed:
                    series = [(start, card['index_usd']), *observed] if observed[0][0] - start <= MINUTE else observed
                    trace = player.responsibility_trace(series, side, c['short_strike_usd'])
                    full = [t for t, _ in observed] == list(range((start // MINUTE + 1) * MINUTE, expiry + 1, MINUTE))
                    summary = dict(complete=full, samples=len(observed), first_ms=observed[0][0], last_ms=observed[-1][0],
                                   touches=trace['touches'], entries_and_returns=trace['episodes'],
                                   max_depth_usd=max((x['depth_usd'] for x in trace['episodes']), default=0))
                payout = cash.inverse_spread_payout(side, c['short_strike_usd'], c['long_strike_usd'], delivery,
                                                   c['quantity_btc']) if delivery else None
                pair[side] = dict(contract=c, initial_margin_usd=abs(card['index_usd'] - c['short_strike_usd']),
                                  remaining_ms=expiry - start, path=summary, delivery_payout_btc=payout,
                                  quote=player.entry(case, 'L2.quote'), economics_estimate=player.entry(case, 'L1.economics'),
                                  executable_net_return_btc=None)
            put, call = (pair[x]['delivery_payout_btc'] for x in ('put', 'call'))
            better = None if put is None else ('tie' if put == call else 'put' if put < call else 'call')
            chosen = decision['judgment']['side'] if decision else None
            t0_credit, shadow_net, managed_shadow = None, None, None
            link_path = hand / 'tracking' / 'link.json'
            if chosen and link_path.is_file():
                link_rec = read(link_path)
                raw_credit = ((link_rec.get('t0') or {}).get('economy') or {}).get('net_credit_after_entry_fee_btc')
                if player.number(raw_credit) is not None:
                    t0_credit = raw_credit
                    if pair[chosen]['delivery_payout_btc'] is not None:
                        shadow_net = round(t0_credit - pair[chosen]['delivery_payout_btc'], 8)
                ep_id = (link_rec.get('origin') or {}).get('dossier_episode_id')
                ep_dir = (folder / 'episodes' / key(ep_id)) if ep_id else None
                if not fixture and ep_dir is not None and (ep_dir / 'condition.json').is_file():
                    ep_events = sorted((read(p) for p in (ep_dir / 'events').glob('*.json')),
                                       key=lambda e: (e['recorded_at_ms'], e['event_id']))
                    managed_shadow = _shadow_managed_cashflow(
                        read(ep_dir / 'condition.json'), delivery, ep_events, link_rec,
                        t0_credit=t0_credit, hold_shadow_net=shadow_net)
                    if managed_shadow is not None:
                        episodes.append(dict(episode_id=ep_id, hand_id=hand.name, **managed_shadow))
            hand_row = dict(hand_id=hand.name, sample=exp['sample'], decision=decision and decision['judgment']['action'],
                            chosen_side=chosen, sides=pair, lower_payout_side=better,
                            t0_net_credit_after_entry_fee_btc=t0_credit,
                            hold_to_expiry_shadow_net_btc=shadow_net)
            if managed_shadow is not None:
                hand_row['managed_cashflow'] = managed_shadow
            hands.append(hand_row)
        if management is not None:
            if not fixture:
                raise ValueError('simulated cashflow fixture is isolated from official outcomes')
            target = folder / 'episodes' / key(management['episode_id'])
            frozen = read(target / 'condition.json')
            actions = [read(p) for p in (target / 'events').glob('*.json')]
            episodes.append(managed_cashflow(frozen, delivery, actions, management))
        body = dict(version='dossier-outcome@1', expiry_ms=expiry, delivery=delivery, quote_currency='BTC',
                    classification='FIXTURE' if fixture else 'OFFICIAL' if delivery and hands else 'UNVERIFIED',
                    verification_gap='missing eligible native candidate evidence' if delivery and not hands else None,
                    hands=hands, episodes=episodes, actual_execution_net_btc=None, supersedes=supersedes, reason=reason)
        return body

    def save_outcome(self, body, source, path=None):
        expiry = body['expiry_ms']
        # Different evidence is a new immutable version, never a rewrite.
        handle = identity('outcome', body, source, path)
        target = self.path(expiry) / 'outcomes' / handle
        if not target.exists():
            publish(target, {'outcome.json': encode(dict(body, outcome_id=handle, recorded_at_ms=self.clock())),
                             'settlement_source.json': encode(source), 'path_source.json': encode(path)})
        return target

    def official(self, expiry, path=None, *, supersedes=None, reason=None):
        if self.clock() < expiry or expiry % DAY != 8 * 60 * MINUTE:
            raise ValueError('official daily BTC index result requires an elapsed native 08:00 UTC expiry')
        previous = sorted((self.path(expiry) / 'outcomes').glob('outcome_*/outcome.json'), key=lambda p: read(p)['recorded_at_ms'])
        verified = [p for p in previous if read(p)['classification'] == 'OFFICIAL']
        if verified and supersedes is None:
            if path is not None:
                raise ValueError('a changed path after official settlement requires a prior outcome and reason')
            return verified[-1].parent
        day = utc(expiry).date().isoformat()
        params = dict(index_name='btc_usd', start_timestamp=expiry, end_timestamp=expiry, count=10)
        source = dict(endpoint='public/get_delivery_prices', params=params)
        try:
            source['response'] = public('get_delivery_prices', **params)
            matches = [r['delivery_price'] for r in source['response']['data'] if r.get('date') == day]
        except (OSError, KeyError, ValueError) as error:
            source['error'] = type(error).__name__
            matches = []
        price = matches[0] if len(matches) == 1 else None
        if price is not None:
            source['available_to_dossier_at_ms'] = self.clock()
        if path is None and price is not None:
            try:
                path = dict(index_name='btc_usd', expiry_ms=expiry, points=[p for p in archive.Deribit().index_path() if p[0] <= expiry])
            except (OSError, ValueError, KeyError):
                source['path_gap'] = 'public minute path unavailable'
        if not price and verified:
            raise ValueError('official price unavailable, existing verified outcome retained')
        return self.save_outcome(self.settle(expiry, price, path, supersedes=supersedes, reason=reason), source, path)

    def settle_due(self, *, errors=None):
        for folder in sorted((self.root / 'dossiers').glob('Deribit_BTC_*')):
            try:
                hands = [read(p) for p in (folder / 'hands').glob('*/expected.json')]
                if not hands or all(h['sample'] == 'FIXTURE' for h in hands):
                    continue
                expiry = hands[0]['expiry_ms']
                if self.clock() >= expiry and not any(read(p)['classification'] == 'OFFICIAL' for p in
                                                      (folder / 'outcomes').glob('outcome_*/outcome.json')):
                    self.official(expiry)
                    yield self.summary(expiry)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                import sys
                if isinstance(errors, list):
                    errors.append(dict(dossier_id=folder.name, error=type(exc).__name__, detail=str(exc)))
                sys.stderr.write(f'settle_due skipped {folder.name}: {type(exc).__name__}: {exc}\n')
                continue

    def summary(self, expiry):
        folder = self.path(expiry)
        hands = sorted((folder / 'hands').glob('hand_*'))
        if not hands:
            raise ValueError('no dossier for expiry')
        outcomes = sorted((folder / 'outcomes').glob('outcome_*/outcome.json'), key=lambda p: read(p)['recorded_at_ms'])
        latest = read(outcomes[-1]) if outcomes else None
        status = ('OPEN' if self.clock() < expiry else 'SETTLEMENT_PENDING' if not latest else
                  'CLOSED' if latest['classification'] in {'OFFICIAL', 'FIXTURE'} else 'UNVERIFIED')
        front = [f'# {folder.name}', '', f'到期 {utc(expiry).isoformat()} · {status}', '']
        lines, counts = [], {}
        for hand in hands:
            exp = read(hand / 'expected.json')
            decision = self.select(hand)
            prefix = f'hands/{hand.name}'
            replies = attempts(hand)
            notes = sorted((hand / 'issuance').glob('*.json'), key=lambda p: read(p)['recorded_at_ms'])
            unanswered = None
            if (hand / 'evidence').exists():
                meta = self.verify_evidence(hand)
                deadline = self.reply_deadline(exp, meta)
                unanswered = 'AWAITING_RESPONSE' if deadline is not None and self.clock() < deadline else 'NO_RESPONSE'
            last_note = read(notes[-1])['state'] if notes else None
            if last_note in {'DEAL_BLOCKED', 'LLM_NOT_CONFIGURED', 'DAILY_BUDGET_EXHAUSTED'}:
                unanswered = last_note
            state = (decision['judgment']['action'] if decision else read(replies[-1])['status'] if replies else
                     unanswered or last_note or 'EXPECTED')
            sample = exp['sample']
            counts.setdefault(sample, {})[state] = counts.setdefault(sample, {}).get(state, 0) + 1
            lines.append(f'- {sample} · {state} · [计划身份]({prefix}/expected.json)')
            if exp['plan']:
                lines[-1] += f" · {utc(exp['scheduled_at_ms']).isoformat()} · {exp['plan']['plan_id']}"
            if (hand / 'evidence').exists():
                self.verify_evidence(hand)
                lines[-1] += f' · [原始牌面]({prefix}/evidence/input.md) · [身份与来源]({prefix}/evidence/identity.json)'
            if decision:
                lines[-1] += f" · [正式判断]({prefix}/decision.json) · [原始回复]({prefix}/{decision['response']})"
            tracking = hand / 'tracking'
            link = read(tracking / 'link.json') if (tracking / 'link.json').is_file() else None
            if link:
                lines[-1] += f" · [AI 推理审计](/#player-hand/{folder.name}/{hand.name})"
                terminal = tracking / 'terminal.json'
                if terminal.is_file():
                    export = key(read(terminal)['export_id'])
                    archive = f"{prefix}/tracking/exports/{export}"
                    lines[-1] += f" · [追踪归档身份]({archive}/manifest.json) · [完整轨迹、缺口与结算]({archive}/trajectory.json)"
                else:
                    lines[-1] += f" · [打开锁定追踪](/#lock-{link['t0']['tracker_episode_id']})"
                lines[-1] += f" · [锁定回执]({prefix}/tracking/link.json)"
            if (tracking / 'request.json').is_file():
                lines.append(f"  - [影子锁定申请]({prefix}/tracking/request.json)")
            if (tracking / 'blocked.json').is_file():
                lines.append(f"  - [锁定阻塞回执]({prefix}/tracking/blocked.json)")
            for item in replies + notes:
                rec = read(item)
                lines.append(f"  - {rec.get('status', rec.get('state'))} · [记录]({item.relative_to(folder).as_posix()})")
        front.extend(f'- {sample} 共 {sum(states.values())} 局，' + '，'.join(f'{k} {v}' for k, v in states.items())
                     for sample, states in counts.items())
        front.extend(['', '## 发牌与回复', '', *lines, '', '## 责任与事中事件', ''])
        for episode in sorted((folder / 'episodes').glob('episode_*')):
            base = episode.relative_to(folder).as_posix()
            front.append(f'- [{episode.name} 冻结条件]({base}/condition.json)')
            for event in sorted((episode / 'events').glob('*.json'), key=lambda p: read(p)['recorded_at_ms']):
                front.append(f"  - [{read(event)['kind']}]({event.relative_to(folder).as_posix()})")
        front.extend(['', '## 到期结果', ''])
        for outcome in outcomes:
            rec = read(outcome)
            front.append(f"- [{rec['classification']} · {rec['delivery']}]({outcome.relative_to(folder).as_posix()})")
        if not outcomes:
            front.append('尚无机械判卷。')
        for review in sorted((folder / 'reviews').glob('*.json')):
            front.append(f'- [事后复盘]({review.relative_to(folder).as_posix()})')
        write(folder / 'summary.md', ('\n'.join(front) + '\n').encode(), replace=True)
        write(folder / 'manifest.json', encode(dict(dossier_id=folder.name, expiry_ms=expiry, status=status, counts=counts)), replace=True)
        return folder / 'summary.md'


def managed_cashflow(frozen, settlement, events, record):
    """One deterministic full buyback of the short leg, with the same long still held."""
    c = frozen['contract']
    flow = [e for e in events if e['kind'] == 'SIMULATED_ORDER' and e['body'].get('action') == 'BUY_BACK_SHORT']
    if len(flow) != 1:
        raise ValueError('fixture requires a single simulated short buyback')
    fill = flow[0]['body']
    q = c['quantity_btc']
    if fill['instrument'] != c['short_instrument'] or fill['quantity_btc'] != q:
        raise ValueError('simulated short fill must match the frozen leg and quantity')
    long = cash.inverse_leg_payout(c['side'], c['long_strike_usd'], settlement, q)
    hold = cash.inverse_spread_payout(c['side'], c['short_strike_usd'], c['long_strike_usd'], settlement, q)
    amounts = [record[k] for k in ('entry_credit_btc', 'entry_fee_btc', 'long_delivery_fee_btc', 'hold_delivery_fee_btc')]
    amounts += [fill[k] for k in ('cost_btc', 'fee_btc')]
    if any(type(x) not in (int, float) or not 0 <= x < float('inf') for x in amounts):
        raise ValueError('missing or invalid BTC cash inputs')
    credit, entry_fee, long_fee, hold_fee, buyback, buyback_fee = amounts
    managed = credit - entry_fee - buyback - buyback_fee + long - long_fee
    baseline = credit - entry_fee - hold - hold_fee
    return dict(mode='FIXTURE', simulated_short_fill_event=flow[0]['event_id'], short_remaining=0,
                long_instrument=c['long_instrument'], long_remaining_before_expiry_btc=q,
                long_settlement_credit_btc=long, long_remaining_after_expiry=0, entry_credit_btc=credit,
                fees_btc=dict(entry=entry_fee, buyback=buyback_fee, delivery=long_fee, hold_delivery=hold_fee),
                short_buyback_cost_btc=buyback, original_spread_payout_btc=hold,
                managed_net_btc=managed, original_hold_net_btc=baseline, delta_btc=managed - baseline)


def _is_verified_revoke_event(event):
    if not isinstance(event, dict) or event.get('kind') != 'AI_REVOKE':
        return False
    body = event.get('body') if isinstance(event.get('body'), dict) else {}
    return (body.get('protocol_status') != 'DISCRETIONARY_REVOKE_UNVERIFIED'
            and body.get('shadow_action') != 'REVOKE_UNVERIFIED_ATTRIBUTION'
            and body.get('discretionary_revoke_attribution') != 'UNVERIFIED')


def _shadow_managed_cashflow(frozen, settlement, events, link_rec, *, t0_credit=None, hold_shadow_net=None):
    """Non-fixture shadow accounting for verified AI_REVOKE: short buyback at ask (preserving long) or unpriced revoke."""
    revokes = [e for e in events if _is_verified_revoke_event(e)]
    flow = [e for e in events if e.get('kind') == 'SIMULATED_ORDER' and (e.get('body') or {}).get('action') == 'BUY_BACK_SHORT']
    no_quotes = [e for e in events if e.get('kind') == 'SHADOW_ACTION' and (e.get('body') or {}).get('action') == 'REVOKE_RECORDED_NO_QUOTE']
    if not revokes and not flow and not no_quotes:
        return None
    c = frozen['contract']
    q = c['quantity_btc']
    if flow and settlement is not None and player.number(t0_credit) is not None:
        fill = flow[-1]['body']
        if fill.get('instrument') != c['short_instrument'] or fill.get('quantity_btc') != q:
            return dict(mode='UNPRICED_REVOKE', short_buyback_cost_btc=None, managed_net_btc=None,
                        long_instrument=c['long_instrument'], unpriced_reason='short_fill_contract_mismatch')
        buyback = fill.get('cost_btc')
        buyback_fee = fill.get('fee_btc', 0.0)
        if any(type(x) not in (int, float) or not 0 <= x < float('inf') for x in (buyback, buyback_fee)):
            return dict(mode='UNPRICED_REVOKE', short_buyback_cost_btc=None, managed_net_btc=None,
                        long_instrument=c['long_instrument'], unpriced_reason='invalid_buyback_quote')
        long_payout = cash.inverse_leg_payout(c['side'], c['long_strike_usd'], settlement, q)
        long_fee = fill.get('long_delivery_fee_btc', 0.0)
        managed = round(t0_credit - buyback - buyback_fee + long_payout - long_fee, 8)
        economy = ((link_rec or {}).get('t0') or {}).get('economy') or {}
        return dict(mode='SHADOW_MANAGED', simulated_short_fill_event=flow[-1]['event_id'],
                    short_remaining=0, long_instrument=c['long_instrument'],
                    long_remaining_before_expiry_btc=q, long_settlement_credit_btc=long_payout,
                    long_remaining_after_expiry=0,
                    entry_credit_btc=economy.get('visible_credit_btc'),
                    t0_net_credit_after_entry_fee_btc=t0_credit,
                    fees_btc=dict(entry=economy.get('theoretical_two_leg_fee_btc'), buyback=buyback_fee, delivery=long_fee),
                    short_buyback_cost_btc=buyback, quoted_at_ms=fill.get('quoted_at_ms'),
                    managed_net_btc=managed, original_hold_net_btc=hold_shadow_net,
                    delta_btc=round(managed - hold_shadow_net, 8) if hold_shadow_net is not None else None)
    if flow and settlement is None:
        return dict(mode='UNPRICED_REVOKE', short_buyback_cost_btc=None, managed_net_btc=None,
                    long_instrument=c['long_instrument'], long_remaining_before_expiry_btc=q,
                    revoked_at_ms=revokes[-1].get('observed_at_ms') or revokes[-1].get('recorded_at_ms') if revokes else None,
                    unpriced_reason='settlement_price_missing')
    reason = ((no_quotes[-1].get('body') or {}).get('quote_gap_reasons') if no_quotes else None) or ['no_reliable_short_buyback_quote']
    return dict(mode='UNPRICED_REVOKE', short_buyback_cost_btc=None, managed_net_btc=None,
                long_instrument=c['long_instrument'], long_remaining_before_expiry_btc=q,
                revoked_at_ms=revokes[-1].get('observed_at_ms') or revokes[-1].get('recorded_at_ms') if revokes else None,
                unpriced_reason=reason[0] if isinstance(reason, list) and reason else reason)


def model_identity(model):
    return isinstance(model, str) and bool(model.strip()) and len(model) <= 120 and model.isprintable()


_PUNCT_TRANS = str.maketrans('，。：；（）【】“”‘’　', ',.:;()[]""\'\' ')


def _norm_excerpt(value):
    cleaned = re.sub(r'(?m)^\s*(?:[-*•]|\d+[.)])\s+', '', value)
    cleaned = cleaned.replace('**', '').replace('`', '')
    collapsed = re.sub(r'\s+', ' ', cleaned.translate(_PUNCT_TRANS)).strip()
    cjk_joined = re.sub(r'(?<=[^\x00-\x7f])\s+|\s+(?=[^\x00-\x7f])', '', collapsed)
    return re.sub(r'\s*([,.:;()\[\]"/])\s*', r'\1', cjk_joined)


def excerpt_in_text(excerpt, text):
    if not isinstance(excerpt, str) or not excerpt.strip() or not isinstance(text, str):
        return False
    if excerpt in text:
        return True
    norm_ex = _norm_excerpt(excerpt)
    return bool(norm_ex) and norm_ex in _norm_excerpt(text)


def parse(raw, annotation):
    text = raw.decode('utf-8')
    headings, fallback = [], []
    for line in text.splitlines():
        body = line.strip().lstrip('#* ').rstrip('* ').strip()
        if match := re.match(r'^(?:(?:最终|当前)?(?:结论|决策|判断)\s*[：:]\s*)?(Accept|Reject|Unknown)(?:\s*[/／]\s*(Put|Call))?\s*[。.:：]?$', body, re.I):
            headings.append((match[1].title(), match[2].lower() if match[2] else None))
        elif match := re.match(r'^(?:(?:最终|当前)?(?:结论|决策|判断)\s*[：:]\s*)?(Accept|Reject|Unknown)(?:\s*[/／]\s*(Put|Call))?(?=$|[。：:.\s])', body, re.I):
            fallback.append((match[1].title(), match[2].lower() if match[2] else None))
    approved = headings or fallback
    if len(set(approved)) != 1:
        raise ValueError('require one unambiguous public decision heading')
    action, side = approved[0]
    if (action == 'Accept') != bool(side):
        raise ValueError('only Accept needs an explicit Put/Call')
    if not isinstance(annotation, dict):
        raise ValueError('provide verbatim public excerpts for both sides and comparison')
    keys = ('put', 'call', 'comparison', 'put_counter', 'call_counter')
    if any(not excerpt_in_text(annotation.get(k), text) for k in keys):
        raise ValueError('both sides, strongest counterevidence and comparison must point to verbatim excerpts')
    if type(annotation.get('depends_on_ard', False)) is not bool:
        raise ValueError('ARD dependence must be an explicit boolean')
    return dict(action=action, side=side, **{k: annotation[k] for k in keys},
                depends_on_ard=annotation.get('depends_on_ard', False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    def command(name, hand=False, episode=False):
        p = sub.add_parser(name)
        p.add_argument('--expiry-ms', type=int, required=True)
        if hand:
            p.add_argument('--hand', required=True)
        if episode:
            p.add_argument('--episode', required=True)
        return p
    tick = sub.add_parser('tick')
    tick.add_argument('--plan', type=Path, required=True)
    tick.add_argument('--archive', type=Path)
    tick.add_argument('--settle-due', action='store_true')
    sub.add_parser('settle-due')
    startup = command('startup')
    startup.add_argument('--archive', type=Path, required=True)
    startup.add_argument('--reference', required=True)
    replay = command('historical-replay')
    replay.add_argument('--source', type=Path, required=True)
    replay.add_argument('--reference', required=True)
    response = command('response', hand=True)
    response.add_argument('--attempt', required=True)
    response.add_argument('--status', choices=['RECEIVED', 'TIMEOUT', 'NO_RESPONSE', 'REQUEST_FAILED'], default='RECEIVED')
    response.add_argument('--raw', type=Path)
    response.add_argument('--annotation', type=Path)
    response.add_argument('--configuration', type=Path)
    response.add_argument('--model')
    response.add_argument('--requested-ms', type=int)
    response.add_argument('--decided-ms', type=int)
    response.add_argument('--channel', default='MANUAL_IMPORT')
    response.add_argument('--error')
    reparse = command('reparse', hand=True)
    reparse.add_argument('--attempt', required=True)
    reparse.add_argument('--annotation', type=Path, required=True)
    reparse.add_argument('--reason', required=True)
    freeze = command('freeze', hand=True)
    freeze.add_argument('--condition', type=Path, required=True)
    freeze.add_argument('--entry-path', type=Path)
    event = command('event', episode=True)
    event.add_argument('--event-id', required=True)
    event.add_argument('--kind', choices=sorted(MANUAL_EVENTS), required=True)
    event.add_argument('--body', type=Path, required=True)
    event.add_argument('--observed-ms', type=int)
    event.add_argument('--available-ms', type=int)
    event.add_argument('--decided-ms', type=int)
    check = command('check', episode=True)
    check.add_argument('--path', type=Path, required=True)
    check.add_argument('--check-ms', type=int, required=True)
    check.add_argument('--available-ms', type=int)
    check.add_argument('--ard', type=Path)
    settle = command('settle')
    settle.add_argument('--path', type=Path)
    settle.add_argument('--supersedes')
    settle.add_argument('--reason')
    review = command('review')
    review.add_argument('--body', type=Path, required=True)
    review.add_argument('--outcome', required=True)
    review.add_argument('--supersedes')
    review.add_argument('--reason')
    command('summary')
    args = parser.parse_args()
    d = Dossiers(args.root, require_reply_provenance=os.environ.get('ASTRA_AI_SHADOW_EXPERIMENT') == '1')
    with writer(d.root, timeout_s=30.0):
        if args.command == 'tick':
            plan_doc = read(args.plan)
            rnd = plan_doc.get('candidate_round', 'r2')
            hands = d.tick(plan_doc, (lambda expiry, slot: collect(args.archive, expiry, slot, candidate_round=rnd))
                           if args.archive else None)
            graded = list(d.settle_due()) if args.settle_due else []
            print(json.dumps([str(p) for p in [*(d.summary(read(h / 'expected.json')['expiry_ms']) for h in hands), *graded]], ensure_ascii=False))
            return
        if args.command == 'settle-due':
            errors = []
            settled = [str(p) for p in d.settle_due(errors=errors)]
            print(json.dumps(settled, ensure_ascii=False))
            if errors:
                import sys
                status = 'DEGRADED' if settled else 'FAILED'
                sys.stderr.write(json.dumps(
                    dict(batch_status=status, settled_count=len(settled), failed_count=len(errors), errors=errors),
                    ensure_ascii=False,
                ) + '\n')
                parser.exit(2)
            return
        expiry, cmd = args.expiry_ms, args.command
        folder = d.path(expiry)
        hand = folder / 'hands' / key(args.hand) if hasattr(args, 'hand') else None
        episode = folder / 'episodes' / key(args.episode) if hasattr(args, 'episode') else None
        if cmd in {'startup', 'historical-replay'}:
            start = cmd == 'startup'
            hand = d.expect(expiry, 'STARTUP_AD_HOC' if start else 'HISTORICAL_REPLAY', reference=args.reference)
            try:
                if (hand / 'evidence').exists():
                    d.verify_evidence(hand)
                    result = hand / 'evidence' / 'input.md'
                else:
                    result = d.deal(hand, collect(args.archive, expiry, now_ms(), startup=True) if start else read(args.source))
            except (OSError, LookupError, ValueError, player.HandNotDealt, KeyError) as exc:
                d.note(hand, 'DATA_BLOCKED', str(exc))
                print(d.summary(expiry))
                parser.exit(2, f'{exc}\n')
        elif cmd == 'response':
            result = d.respond(hand, args.attempt, args.status, args.raw.read_bytes() if args.raw else None,
                               read(args.annotation) if args.annotation else None, model=args.model,
                               configuration=read(args.configuration) if args.configuration else None,
                               requested=args.requested_ms, decided=args.decided_ms, channel=args.channel,
                               error=args.error)
            result = {k: result[k] for k in ('hand_id', 'attempt_id', 'status', 'error')}
        elif cmd == 'reparse':
            result = d.reparse(hand, args.attempt, read(args.annotation), args.reason)
        elif cmd == 'freeze':
            result = d.episode(hand, read(args.condition), player.read_json(args.entry_path) if args.entry_path else None)
        elif cmd == 'event':
            result = d.event(episode, args.event_id, args.kind, read(args.body), observed=args.observed_ms,
                             available=args.available_ms, decided=args.decided_ms)['kind']
        elif cmd == 'check':
            result = d.check(episode, player.read_json(args.path), args.check_ms, available=args.available_ms,
                             ard=read(args.ard) if args.ard else None)['kind']
        elif cmd == 'settle':
            result = d.official(expiry, player.read_json(args.path) if args.path else None,
                                supersedes=args.supersedes, reason=args.reason)
        elif cmd == 'review':
            if not (folder / 'outcomes' / key(args.outcome) / 'outcome.json').is_file():
                raise ValueError('review requires a reference outcome')
            if args.supersedes and (not args.reason or not (folder / 'reviews' / (key(args.supersedes) + '.json')).exists()):
                raise ValueError('review revision requires an existing review and a reason')
            record = dict(outcome_id=args.outcome, review=read(args.body), supersedes=args.supersedes, reason=args.reason)
            result = folder / 'reviews' / (identity('review', record) + '.json')
            if not result.exists():
                write(result, encode(dict(record, recorded_at_ms=d.clock())))
        else:
            print(d.summary(expiry))
            return
        print(json.dumps(dict(result=str(result) if isinstance(result, Path) else result,
                              summary=str(d.summary(expiry))), ensure_ascii=False))


if __name__ == '__main__':
    main()
