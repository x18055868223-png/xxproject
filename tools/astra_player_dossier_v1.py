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
          'SHADOW_ACTION', 'SHADOW_EXITED', 'SIMULATED_ORDER', 'CORRECTION'}
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


def _normalize_fallback_cases(fallback_cases):
    if fallback_cases is None:
        return {}
    by_side = {}
    if isinstance(fallback_cases, dict) or hasattr(fallback_cases, 'keys'):
        for side in ('put', 'call'):
            cand = fallback_cases.get(side)
            if isinstance(cand, dict) or hasattr(cand, 'keys'):
                by_side[side] = dict(cand)
        return by_side
    if isinstance(fallback_cases, (list, tuple)):
        for item in fallback_cases:
            if not (isinstance(item, dict) or hasattr(item, 'keys')):
                continue
            cand = dict(item)
            side = (player.values(cand, 'L1.contract') or {}).get('side')
            if side in ('put', 'call'):
                if side in by_side:
                    raise LookupError(f'重复的 {side} 回退案卷')
                by_side[side] = cand
        return by_side
    raise LookupError('无效的回退案卷格式')


def collect(archive_root, expiry, slot, *, startup=False, clock=now_ms, band_snapshot=None, candidate_round='r2',
            fallback_cases=None, provenance_override=None):
    """Use the existing archive's native cases. Public reads fill the current path and expiry proof."""
    archive_root = Path(archive_root)
    day = utc(expiry).date().isoformat()
    case_dir = archive_root / 'cases' / utc(slot).date().isoformat()
    fallback_by_side = _normalize_fallback_cases(fallback_cases)
    candidates = []
    candidate_is_fallback = []
    used_fallback = False
    for side in ('put', 'call'):
        matches = list(case_dir.glob(f'{utc(slot):%H}_{side}_*.json.gz')) if case_dir.is_dir() else []
        if len(matches) > 1:
            raise LookupError(f'需同一个采集小时的唯一 {side} 原生案卷，现有 {len(matches)} 份')
        if len(matches) == 1:
            candidates.append(player.read_json(matches[0]))
            candidate_is_fallback.append(False)
        elif side in fallback_by_side:
            fb_case = fallback_by_side[side]
            cid = fb_case.get('case_id')
            if not isinstance(cid, str) or not cid.startswith('cf_'):
                raise LookupError(f'无效的 {side} 回退案卷身份')
            fb_build = fb_case.get('build') if isinstance(fb_case.get('build'), dict) else {}
            if fb_build.get('content_sha256') is not None:
                if fb_build.get('content_sha256') != archive._canonical_case_content_sha256(fb_case):
                    raise LookupError(f'{side} 回退案卷内容哈希校验失败')
            candidates.append(fb_case)
            candidate_is_fallback.append(True)
            used_fallback = True
        else:
            raise LookupError(f'需同一个采集小时的唯一 {side} 原生案卷，现有 0 份')
    reads = [(case.get('build') or {}).get('built_at_ms') for case in candidates]
    if any(type(t) is not int for t in reads):
        raise LookupError('案卷构建时间无效')
    contracts = [player.values(case, 'L1.contract') for case in candidates]
    if [c.get('side') for c in contracts] != ['put', 'call']:
        raise LookupError('案卷方向与目标槽位不符')
    if any(c['expiry_ms'] != expiry for c in contracts):
        raise LookupError('当前引擎候选到期与目标不符')
    at_check = clock()
    if startup:
        if at_check - min(reads) > 15 * MINUTE:
            raise LookupError('当前小时案卷距启动过久，等下一次原生采集')
    elif any(not (slot <= t <= at_check) for t in reads):
        raise LookupError('当前小时案卷构建时间超出采集槽位窗口')
    if abs(reads[0] - reads[1]) > 60_000:
        raise LookupError('双侧案卷构建时间差超过 60 秒')
    val_reads = []
    for side_idx, (case, built_ms, is_fb) in enumerate(zip(candidates, reads, candidate_is_fallback)):
        s_name = 'put' if side_idx == 0 else 'call'
        quote = player.values(case, 'L2.quote')
        if not isinstance(quote, dict):
            raise LookupError(f'{s_name} 案卷缺少 L2.quote 报价')
        q_credit = player.number(quote.get('credit_btc'))
        if q_credit is None or q_credit <= 0:
            raise LookupError(f'{s_name} 案卷缺少有效正权利金净信用')
        if (quote.get('gap_reasons') or []) or (quote.get('quote_gap_reasons') or []):
            raise LookupError(f'{s_name} 案卷报价存在盘口缺口')
        v_ts = quote.get('valuation_ts_ms')
        if type(v_ts) is not int:
            v_ts = case.get('asof_ms')
        if type(v_ts) is not int or not (0 <= built_ms - v_ts <= 180_000):
            raise LookupError(f'{s_name} 案卷报价估值时间缺失或超出 180 秒保鲜期')
        if not startup and v_ts < (slot - 180_000):
            raise LookupError(f'{s_name} 案卷报价估值时间早于采集槽位允许窗口')
        if is_fb and (at_check - v_ts) > 180_000:
            raise LookupError(f'{s_name} 回退案卷报价已超过 180 秒保鲜期')
        val_reads.append(v_ts)
    if abs(val_reads[0] - val_reads[1]) > 60_000:
        raise LookupError('双侧案卷报价估值时间差超过 60 秒')
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
    allow_backfill_card = fallback_cases is not None or provenance_override is not None
    for case, c, tick in zip(candidates, contracts, reads):
        native = [r for r in existing if r.get('schema') == levels.CARD_SCHEMA and r.get('round') == candidate_round
                  and r.get('case_id') == case['case_id'] and r.get('backfilled') is False]
        if len(native) == 1:
            cards.append(native[0])
            continue
        late_scheduled = not startup and clock() >= slot + 15 * MINUTE
        if not startup and not late_scheduled and not allow_backfill_card:
            raise LookupError(f"等待 {c['side']} 原生 {candidate_round} 位置卡")
        cut = [p for p in points if p[0] <= tick]
        if not cut or tick - cut[-1][0] > levels.PATH_EDGE_MS:
            raise LookupError('候选读取时的指数路径未覆盖')
        snaps = [r for r in known if r['available_at_ms'] <= tick]
        gap = (
            'startup_card_from_native_case'
            if startup
            else 'targeted_backfill_card_from_native_case'
            if allow_backfill_card
            else 'scheduled_card_from_native_case'
        )
        cards.append(levels.build_card(expiry_ms=expiry, rnd=candidate_round, side=c['side'], read_at_ms=tick,
                                     walls=player.values(case, 'L3.expiry_walls'), points=cut, case=case,
                                     contract=c, snapshots=snaps, extra_gaps=[gap],
                                     timestamp_semantics='unverified_minute_label'))
    path = dict(schema='index_path@0.1', index_name='btc_usd', expiry_ms=expiry, fetched_at_ms=received,
                timestamp_semantics='unverified_minute_label',
                points=[p for p in points if p[0] <= max(reads)])
    resolved_band = _resolve_band_snapshot(archive_root, min(reads), band_snapshot=band_snapshot)
    source_label = provenance_override or (
        'targeted workbench verified native case + Deribit public read'
        if used_fallback
        else 'native research archive + Deribit public read'
    )
    return seal_source(candidates, cards, path, history, instrument_proof, received_at_ms=received,
                       source=source_label, band_snapshot=resolved_band)


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

    def note(self, hand, state, reason, *, diagnostics=None):
        now = self.clock()
        name = identity('note', state, reason) + '.json'
        out = hand / 'issuance' / name
        if out.exists():
            prior = read(out)
            prior_rec = prior.get('recorded_at_ms')
            first_at = prior.get('first_recorded_at_ms', prior_rec if type(prior_rec) is int else now)
            prior_times = prior.get('attempt_times_ms')
            if not isinstance(prior_times, list) or not prior_times:
                prior_times = [prior_rec] if type(prior_rec) is int else []
            payload = dict(
                state=state,
                reason=reason,
                recorded_at_ms=now,
                first_recorded_at_ms=first_at,
                attempt_count=int(prior.get('attempt_count') or 1) + 1,
                attempt_times_ms=[*prior_times, now][-16:],
            )
            if 'window_closed_at_ms' in prior:
                payload['window_closed_at_ms'] = prior['window_closed_at_ms']
            if diagnostics is not None:
                prior_diag = prior.get('diagnostics')
                if isinstance(prior_diag, dict) and isinstance(diagnostics, dict):
                    payload['diagnostics'] = {**prior_diag, **diagnostics}
                else:
                    payload['diagnostics'] = diagnostics
            elif 'diagnostics' in prior:
                payload['diagnostics'] = prior['diagnostics']
            write(out, encode(payload), replace=True)
        else:
            payload = dict(
                state=state,
                reason=reason,
                recorded_at_ms=now,
                first_recorded_at_ms=now,
                attempt_count=1,
                attempt_times_ms=[now],
                **({'diagnostics': diagnostics} if diagnostics is not None else {}),
            )
            write(out, encode(payload))

    def deal(self, hand, source, *, input_profile=None):
        target = hand / 'evidence'
        if target.exists():
            self.verify_evidence(hand)
            return target / 'input.md'
        legacy_profile = getattr(player, 'INPUT_PROFILE_LEGACY', 'legacy_v1')
        supported_profiles = getattr(player, 'SUPPORTED_INPUT_PROFILES', (legacy_profile, 'p0_strict@1'))
        effective_profile = legacy_profile if input_profile is None else input_profile
        if effective_profile not in supported_profiles:
            raise ValueError(f'unsupported input_profile: {effective_profile!r}')
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
        if effective_profile != legacy_profile:
            text = player.build_input(*source['cases'], *source['cards'], index_path=source['index_path'],
                                      ard_history=source['history'], ard_band_snapshot=source.get('band_snapshot'),
                                      input_profile=effective_profile)
        else:
            text = player.build_input(*source['cases'], *source['cards'], index_path=source['index_path'],
                                      ard_history=source['history'], ard_band_snapshot=source.get('band_snapshot'))
        raw, inputs = text.replace('\n', os.linesep).encode('utf-8'), encode(source)
        files = [Path(module.__file__) for module in (player, player.ard, condition, levels)]
        code = {p.name: sha(p.read_bytes()) for p in files}
        if '## 怎么读' in text and '## 本局牌面' in text and '## 读完后另写' in text:
            rules = text.split('## 怎么读', 1)[1].split('## 本局牌面', 1)[0] + text.split('## 读完后另写', 1)[1]
        else:
            rules = text.split('## 本局牌面', 1)[0]
        meta = dict(research=RESEARCH, source_sha256=sha(inputs), input_sha256=sha(raw),
                    rules_sha256=sha(rules.encode()), generator=code, generated_at_ms=at,
                    source_observed_at_ms=reads, source_bundle_available_at_ms=source['received_at_ms'],
                    hand_frozen_at_ms=self.clock())
        if effective_profile != legacy_profile:
            meta['input_profile'] = effective_profile
            meta['candidate_mode'] = True
        publish(target, {'input.md': raw, 'source.json': inputs, 'identity.json': encode(meta)})
        return target / 'input.md'

    def verify_evidence(self, hand):
        folder = hand / 'evidence'
        meta = read(folder / 'identity.json')
        if any(sha((folder / f).read_bytes()) != meta[k] for f, k in
               (('input.md', 'input_sha256'), ('source.json', 'source_sha256'))):
            raise ValueError('frozen input or source hash mismatch')
        return meta

    def tick(self, plan, source_for=None, *, slots=None, input_profile=None):
        at, output = self.clock(), []
        auto_walk = slots is None
        slots = schedule(plan, at) if auto_walk else slots
        legacy_profile = getattr(player, 'INPUT_PROFILE_LEGACY', 'legacy_v1')
        effective_profile = input_profile if input_profile is not None else (plan.get('input_profile') or legacy_profile)
        write(self.root / 'plans' / (key(plan['plan_id']) + '.json'), encode(plan))
        for slot in slots:
            expiry = slot + plan['expiry_offset_ms']
            if auto_walk and at >= expiry:
                hand_target = (
                    self.path(expiry)
                    / 'hands'
                    / identity('hand', 'Deribit', 'BTC', expiry, 'SCHEDULED', slot)
                    / 'expected.json'
                )
                if hand_target.is_file() and (read(hand_target).get('plan') or {}).get('plan_id') != plan['plan_id']:
                    continue
            hand = self.expect(expiry, 'SCHEDULED', plan=plan, slot=slot)
            if (hand / 'evidence').exists():
                meta = self.verify_evidence(hand)
                if at >= self.reply_deadline(read(hand / 'expected.json'), meta) and not self.select(hand):
                    self.note(hand, 'REPLY_WINDOW_CLOSED', '截止前没有有效回复，保留所有尝试')
            elif at >= slot + plan['deal_grace_ms']:
                notes = sorted((hand / 'issuance').glob('*.json'), key=lambda p: read(p)['recorded_at_ms'])
                if not notes:
                    self.note(hand, 'MISSED', '计划发牌窗口已结束')
                else:
                    latest = read(notes[-1])
                    if 'window_closed_at_ms' not in latest:
                        write(notes[-1], encode(dict(latest, window_closed_at_ms=at)), replace=True)
            elif source_for:
                try:
                    self.deal(hand, source_for(expiry, slot), input_profile=effective_profile)
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
                full_raw=None, condition_plan=None, prompt_version=None, system_sha256=None,
                input_profile=None, contract_validation=None, error_code=None):
        meta, at = self.verify_evidence(hand), self.clock()
        exp = read(hand / 'expected.json')
        folder = hand / 'responses' / key(attempt)
        payload_hash = sha(raw) if raw is not None else None
        request_bytes = encode(request_payload) if request_payload is not None else None
        request_hash = sha(request_bytes) if request_bytes is not None else None
        legacy_profile = getattr(player, 'INPUT_PROFILE_LEGACY', 'legacy_v1')
        p0_profile = getattr(player, 'INPUT_PROFILE_P0_STRICT', 'p0_strict@1')
        plan_pv = (exp.get('plan') or {}).get('prompt_version')
        req_pv = request_payload.get('prompt_version') if isinstance(request_payload, dict) else None
        caller_req_pv_mismatch = prompt_version is not None and req_pv is not None and prompt_version != req_pv
        eff_prompt_version = prompt_version if prompt_version is not None else req_pv
        eff_system_sha256 = system_sha256 if system_sha256 is not None else (
            request_payload.get('system_sha256') if isinstance(request_payload, dict) else None
        )
        eff_input_profile = input_profile if input_profile is not None else (
            (request_payload.get('input_profile') if isinstance(request_payload, dict) else None)
            or meta.get('input_profile')
        )
        eff_cv = dict(contract_validation) if isinstance(contract_validation, dict) else None
        eff_error_code = error_code
        is_non_legacy = (
            (eff_prompt_version is not None and eff_prompt_version != 'player_initial_system@1.0')
            or (eff_input_profile is not None and eff_input_profile != legacy_profile)
            or eff_cv is not None
            or system_sha256 is not None
            or eff_error_code is not None
        )
        declared = dict(raw_sha256=payload_hash, annotation=annotation, input_status=status, model=model,
                        configuration=configuration, channel=channel, requested_at_ms=requested, decided_at_ms=decided)
        if request_hash is not None:
            declared['request_sha256'] = request_hash
        if full_raw is not None:
            declared['full_raw_sha256'] = sha(full_raw)
        if eff_prompt_version is not None and (is_non_legacy or plan_pv is not None):
            declared['prompt_version'] = eff_prompt_version
        if is_non_legacy:
            if eff_system_sha256 is not None:
                declared['system_sha256'] = eff_system_sha256
            if eff_input_profile is not None:
                declared['input_profile'] = eff_input_profile
            if eff_cv is not None:
                declared['contract_validation_status'] = eff_cv.get('status')
            if eff_error_code is not None:
                declared['error_code'] = eff_error_code
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
            # Fail-closed candidate contract gate even on direct programmatic respond() calls.
            if status == 'VALID':
                if eff_error_code == 'CONTRACT_VALIDATION_FAILED':
                    status = 'PARSE_FAILED'
                    record_error = (
                        str(error)[:240]
                        if isinstance(error, str) and error.strip()
                        else 'contract_validation_failed'
                    )
                    is_non_legacy = True
                elif caller_req_pv_mismatch:
                    status, record_error = 'PARSE_FAILED', f'caller_req_pv_mismatch:{prompt_version}!={req_pv}'[:240]
                    eff_error_code = eff_error_code or 'CONTRACT_VALIDATION_FAILED'
                    is_non_legacy = True
                elif meta.get('input_profile') == p0_profile and not eff_prompt_version:
                    status, record_error = 'PARSE_FAILED', 'missing_prompt_version'
                    eff_error_code = eff_error_code or 'CONTRACT_VALIDATION_FAILED'
                    is_non_legacy = True
                elif plan_pv is not None and eff_prompt_version != plan_pv:
                    status, record_error = 'PARSE_FAILED', f'prompt_version_plan_mismatch:{eff_prompt_version}!={plan_pv}'[:240]
                    eff_error_code = eff_error_code or 'CONTRACT_VALIDATION_FAILED'
                    is_non_legacy = True
                elif meta.get('input_profile') == p0_profile and eff_prompt_version == 'player_initial_system@1.0':
                    status, record_error = 'PARSE_FAILED', 'p0_strict_forbids_legacy_v10_prompt'
                    eff_error_code = eff_error_code or 'CONTRACT_VALIDATION_FAILED'
                    is_non_legacy = True
                else:
                    need_review_mod = (
                        meta.get('input_profile') == p0_profile
                        or eff_prompt_version is not None
                        or eff_cv is not None
                    )
                    if need_review_mod:
                        if __package__:
                            from . import astra_player_review_v1 as review_mod
                        else:
                            import astra_player_review_v1 as review_mod
                        if eff_prompt_version is not None and eff_prompt_version not in review_mod.INITIAL_SYSTEM_PROMPTS:
                            status, record_error = 'PARSE_FAILED', f'unknown_prompt_version:{eff_prompt_version}'
                            eff_error_code = eff_error_code or 'CONTRACT_VALIDATION_FAILED'
                            is_non_legacy = True
                        elif eff_prompt_version in review_mod.CANDIDATE_STRICT_PROMPT_VERSIONS:
                            is_non_legacy = True
                            if eff_cv is not None and eff_cv.get('status') != 'PASSED':
                                errs = ', '.join(((eff_cv.get('layer1_deterministic') or {}).get('blocking_errors') or []))
                                status = 'PARSE_FAILED'
                                record_error = f"contract_validation_failed ({eff_cv.get('status')}): {errs}"[:240]
                                eff_error_code = eff_error_code or 'CONTRACT_VALIDATION_FAILED'
                            elif judgment['action'] == 'Accept' and not isinstance(condition_plan, dict):
                                status, record_error = 'PARSE_FAILED', 'candidate Accept requires condition_plan'
                                eff_error_code = 'CONTRACT_VALIDATION_FAILED'
                            else:
                                src = read(hand / 'evidence' / 'source.json')
                                contracts_pair = {
                                    c['side']: player.bind(cs, c, c['side'])
                                    for cs, c in zip(src['cases'], src['cards'])
                                    if c.get('side') in {'put', 'call'}
                                }
                                plan_to_check = condition_plan
                                if (isinstance(condition_plan, dict)
                                        and eff_prompt_version not in getattr(review_mod, 'P5_STRICT_PROMPT_VERSIONS', ())
                                        and 'require_outside_at_t0' not in condition_plan):
                                    plan_to_check = dict(condition_plan, require_outside_at_t0=True)
                                try:
                                    eff_cv = review_mod.validate_initial_reply_contract(
                                        {'raw_text': raw.decode('utf-8'), 'decision': judgment, 'condition_plan': plan_to_check},
                                        contracts_pair,
                                        strict_clock_5m=True,
                                        check_excerpt_completeness=True,
                                        require_outside_at_t0=True,
                                    )
                                except (ValueError, TypeError, KeyError) as exc:
                                    eff_cv = {
                                        'status': 'CONTRACT_FAILED',
                                        'layer1_deterministic': {'valid': False, 'blocking_errors': [str(exc)], 'excerpt_checks': {}},
                                    }
                                    status, record_error = 'PARSE_FAILED', f'contract_validation_failed: {exc}'[:240]
                                    eff_error_code = 'CONTRACT_VALIDATION_FAILED'
                                else:
                                    declared['contract_validation_status'] = eff_cv.get('status')
                                    if eff_cv.get('status') != 'PASSED':
                                        errs = ', '.join((eff_cv.get('layer1_deterministic') or {}).get('blocking_errors') or [])
                                        status, record_error = 'PARSE_FAILED', f"contract_validation_failed ({eff_cv.get('status')}): {errs}"[:240]
                                        eff_error_code = 'CONTRACT_VALIDATION_FAILED'
                        elif eff_cv is not None and eff_cv.get('status') != 'PASSED':
                            status, record_error = 'PARSE_FAILED', record_error or 'contract_validation_failed'
                            eff_error_code = eff_error_code or 'CONTRACT_VALIDATION_FAILED'
                            is_non_legacy = True
        elif status not in {'TIMEOUT', 'NO_RESPONSE', 'REQUEST_FAILED', 'PARSE_FAILED'} or raw is not None:
            raise ValueError('invalid response status or raw payload')
        elif isinstance(error, str) and error.strip():
            record_error = str(error)[:240]
        if status == 'LATE' and record_error is None and isinstance(error, str) and error.strip():
            record_error = str(error)[:240]
        if payload_hash is not None and any(r['status'] == 'VALID' and r['raw_sha256'] == payload_hash for r in siblings):
            status = 'DUPLICATE'
        if is_non_legacy:
            config_id = identity('config', RESEARCH, exp['plan'] and exp['plan']['plan_id'],
                                 meta['generator'], meta['rules_sha256'], model, configuration, channel,
                                 eff_prompt_version, eff_system_sha256, eff_input_profile)
        else:
            config_id = identity('config', RESEARCH, exp['plan'] and exp['plan']['plan_id'],
                                 meta['generator'], meta['rules_sha256'], model, configuration, channel)
        record = dict(declared, status=status, attempt_id=attempt, ordinal=len(siblings) + 1,
                      recorded_at_ms=at, input_sha256=meta['input_sha256'], parser=PARSER,
                      configuration_id=config_id, judgment=judgment, error=record_error, hand_id=hand.name)
        if eff_prompt_version is not None and (is_non_legacy or plan_pv is not None) and 'prompt_version' not in record:
            record['prompt_version'] = eff_prompt_version
        if is_non_legacy:
            if eff_error_code is not None:
                record['error_code'] = eff_error_code
            if eff_cv is not None:
                record['contract_validation_status'] = eff_cv.get('status')
                record['contract_validation'] = eff_cv
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
        meta = self.verify_evidence(hand)
        source = read(hand / 'evidence' / 'source.json')
        side = decision['judgment']['side']
        case, card = next((x, c) for x, c in zip(source['cases'], source['cards']) if c['side'] == side)
        contract = player.bind(case, card, side)
        if any(frozen['contract'].get(k) != contract.get(k) for k in condition.CONTRACT_FIELDS):
            raise ValueError('frozen legs differ from evidence')
        exp = read(hand / 'expected.json')
        plan_pv = (exp.get('plan') or {}).get('prompt_version')
        p0_profile = getattr(player, 'INPUT_PROFILE_P0_STRICT', 'p0_strict@1')
        att_dir = hand / 'responses' / key(decision['attempt_id'])
        att_rec = read(att_dir / 'attempt.json') if (att_dir / 'attempt.json').is_file() else {}
        req_pv = read(att_dir / 'request.json').get('prompt_version') if (att_dir / 'request.json').is_file() else None
        att_pv = att_rec.get('prompt_version') or req_pv
        if __package__:
            from . import astra_player_review_v1 as review_mod
        else:
            import astra_player_review_v1 as review_mod
        if req_pv is not None and att_rec.get('prompt_version') is not None and req_pv != att_rec.get('prompt_version'):
            raise ValueError('selected attempt prompt_version differs from request.json')
        if meta.get('input_profile') == p0_profile and (not att_pv or att_pv == 'player_initial_system@1.0'):
            raise ValueError('candidate p0_strict@1 hand requires non-legacy prompt_version before freeze')
        if plan_pv is not None and att_pv != plan_pv:
            raise ValueError(f'selected attempt prompt_version {att_pv!r} does not match plan prompt_version {plan_pv!r}')
        if att_pv is not None and att_pv not in review_mod.INITIAL_SYSTEM_PROMPTS:
            raise ValueError(f'unknown prompt_version on selected attempt: {att_pv!r}')
        is_candidate_prompt = att_pv in review_mod.CANDIDATE_STRICT_PROMPT_VERSIONS
        is_p5_strict = att_pv in getattr(review_mod, 'P5_STRICT_PROMPT_VERSIONS', ())
        require_candidate_gate = (
            is_candidate_prompt
            or frozen.get('require_outside_at_t0') is True
            or bool(frozen.get('mechanical_contract_version'))
        )
        if require_candidate_gate:
            if is_p5_strict and frozen.get('require_outside_at_t0') is not True:
                raise ValueError('P5 candidate episode requires explicit require_outside_at_t0=True')
            if is_candidate_prompt and frozen.get('require_outside_at_t0') is False:
                raise ValueError('candidate episode cannot disable require_outside_at_t0')
            if not _is_strictly_outside(frozen['entry_index_usd'], frozen['contract']):
                raise ValueError('candidate episode requires entry_index_usd strictly OUTSIDE short strike at t0')
            expected_first = (frozen['entry_at_ms'] // MINUTE + 1) * MINUTE
            if frozen['check_clock']['first_at_ms'] != expected_first:
                raise ValueError('candidate episode check_clock.first_at_ms must be next UTC full minute after entry_at_ms')
            if frozen['check_clock']['interval_ms'] != 300_000:
                raise ValueError('candidate episode check_clock.interval_ms must be 300000')
            if tracking is None and is_candidate_prompt:
                raw_path = att_dir / 'response.raw'
                if not raw_path.is_file():
                    raise ValueError('candidate manual freeze requires response.raw')
                contracts_pair = {
                    c['side']: player.bind(cs, c, c['side'])
                    for cs, c in zip(source['cases'], source['cards'])
                    if c.get('side') in {'put', 'call'}
                }
                manual_plan = {
                    'invalidation': frozen['invalidation'],
                    'check_interval_ms': frozen['check_clock']['interval_ms'],
                    'historical_facts': frozen['historical_facts'],
                    'accepted_conditions': frozen['accepted_conditions'],
                    'confirmation_excerpt': frozen.get('confirmation_excerpt') or (
                        read(att_dir / 'condition_plan.json').get('confirmation_excerpt')
                        if (att_dir / 'condition_plan.json').is_file() else ''
                    ),
                }
                if 'require_outside_at_t0' in frozen:
                    manual_plan['require_outside_at_t0'] = frozen['require_outside_at_t0']
                elif not is_p5_strict:
                    manual_plan['require_outside_at_t0'] = True
                if frozen.get('ard_basis') and isinstance(frozen['ard_basis'], dict):
                    manual_plan['ard_responsibility_meaning'] = frozen['ard_basis'].get('responsibility_meaning', '')
                    manual_plan['ard_review_on'] = frozen['ard_basis'].get('review_on', '')
                cv = review_mod.validate_initial_reply_contract(
                    {'raw_text': raw_path.read_text(encoding='utf-8'), 'decision': decision['judgment'], 'condition_plan': manual_plan},
                    contracts_pair,
                    strict_clock_5m=True,
                    check_excerpt_completeness=True,
                    require_outside_at_t0=True,
                )
                if cv.get('status') != 'PASSED':
                    errs = ', '.join((cv.get('layer1_deterministic') or {}).get('blocking_errors') or [])
                    raise ValueError(f"candidate manual freeze contract validation failed: {errs}")
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
        if basis:
            cutoff_ms = min(card['read_at_ms'] for card in source['cards'])
            if meta.get('input_profile') == p0_profile:
                expected_facts = player.ard_facts_for_evidence(
                    source['history'],
                    source['index_path'],
                    expiry_ms=exp['expiry_ms'],
                    cutoff_ms=cutoff_ms,
                    band_snapshot=source.get('band_snapshot'),
                    input_profile=p0_profile,
                )
            else:
                ard_index_path = source['index_path']
                if isinstance(ard_index_path, dict) and player._chart_semantics(ard_index_path) in player._CHART_DELAY_SEMANTICS:
                    ard_pts = player.chart_points_audit(ard_index_path, exp['expiry_ms'], cutoff_ms)['eligible']
                    ard_index_path = dict(ard_index_path, points=[[t, p] for t, p in ard_pts])
                expected_facts = player.ard.analyze(
                    source['history'],
                    ard_index_path,
                    expiry_ms=exp['expiry_ms'],
                    cutoff_ms=cutoff_ms,
                    band_snapshot=source.get('band_snapshot'),
                )
            if encode(basis['facts']) != encode(expected_facts):
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
            decided_at_ms = decision.get('recorded_at_ms') if isinstance(decision, dict) else None
            t0_credit, shadow_net, managed_shadow = None, None, None
            tracking_dir = hand / 'tracking'
            link_path = tracking_dir / 'link.json'
            final_block = read(tracking_dir / 'final_block.json') if (tracking_dir / 'final_block.json').is_file() else None
            blocked_rec = read(tracking_dir / 'blocked.json') if (tracking_dir / 'blocked.json').is_file() else None
            pending_rec = read(tracking_dir / 'pending_lock.json') if (tracking_dir / 'pending_lock.json').is_file() else None
            lock_mode = None
            delayed_lock = False
            lock_reason = None
            locked_at_ms = None
            offset_from_decision_ms = None
            if chosen is None:
                lock_status = 'NOT_APPLICABLE'
                management_status = 'NOT_APPLICABLE'
            elif link_path.is_file():
                link_rec = read(link_path)
                locked_at_ms = (link_rec.get('t0') or {}).get('locked_at_ms')
                if isinstance(locked_at_ms, int) and isinstance(decided_at_ms, int):
                    offset_from_decision_ms = locked_at_ms - decided_at_ms
                lock_mode = link_rec.get('lock_mode') or (
                    'DELAYED_LOCK' if (link_rec.get('delayed_lock') or link_rec.get('retry_history')) else 'IMMEDIATE_LOCK'
                )
                delayed_lock = bool(lock_mode == 'DELAYED_LOCK' or link_rec.get('delayed_lock'))
                lock_status = 'DELAYED_LOCK' if delayed_lock else 'IMMEDIATE_LOCK'
                if pending_rec:
                    lock_reason = pending_rec.get('last_failure_reason') or pending_rec.get('last_reason')
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
                management_status = managed_shadow['mode'] if managed_shadow is not None else 'HOLD_TO_EXPIRY'
            else:
                pending_reason = (pending_rec or {}).get('last_failure_reason') or (pending_rec or {}).get('last_reason')
                terminal_block = final_block or blocked_rec
                if terminal_block is not None:
                    lock_status = 'LOCK_BLOCKED'
                    lock_reason = terminal_block.get('reason') or pending_reason
                else:
                    lock_status = 'UNLINKED'
                    lock_reason = pending_reason
                management_status = 'NOT_APPLICABLE'
            hand_row = dict(hand_id=hand.name, sample=exp['sample'], decision=decision and decision['judgment']['action'],
                            chosen_side=chosen, sides=pair, lower_payout_side=better,
                            t0_net_credit_after_entry_fee_btc=t0_credit,
                            hold_to_expiry_shadow_net_btc=shadow_net,
                            lock_status=lock_status, lock_mode=lock_mode, delayed_lock=delayed_lock,
                            lock_reason=lock_reason, decided_at_ms=decided_at_ms, locked_at_ms=locked_at_ms,
                            offset_from_decision_ms=offset_from_decision_ms, management_status=management_status)
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
                has_dealt_evidence = any(
                    (folder / 'hands' / h['hand_id'] / 'evidence' / 'identity.json').is_file()
                    for h in hands
                    if h.get('sample') != 'FIXTURE' and isinstance(h.get('hand_id'), str)
                )
                existing_outcomes = [read(p) for p in (folder / 'outcomes').glob('outcome_*/outcome.json')]
                already_settled = (
                    any(o['classification'] == 'OFFICIAL' for o in existing_outcomes)
                    or (not has_dealt_evidence and bool(existing_outcomes))
                )
                if self.clock() >= expiry and not already_settled:
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
        lines, counts, lock_counts = [], {}, {}
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
            if decision and decision['judgment']['action'] == 'Accept':
                if link:
                    lkey = 'DELAYED_LOCK' if (link.get('delayed_lock') or link.get('lock_mode') == 'DELAYED_LOCK') else 'IMMEDIATE_LOCK'
                elif (tracking / 'final_block.json').is_file() or (tracking / 'blocked.json').is_file():
                    lkey = 'LOCK_BLOCKED'
                elif (tracking / 'pending_lock.json').is_file():
                    lkey = 'PENDING_LOCK'
                else:
                    lkey = 'UNLINKED'
                lock_counts[lkey] = lock_counts.get(lkey, 0) + 1
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
            if (tracking / 'pending_lock.json').is_file():
                lines.append(f"  - [延迟锁定试锁状态]({prefix}/tracking/pending_lock.json)")
            if (tracking / 'final_block.json').is_file():
                lines.append(f"  - [锁定终局终止回执]({prefix}/tracking/final_block.json)")
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
        write(folder / 'manifest.json', encode(dict(dossier_id=folder.name, expiry_ms=expiry, status=status,
                                                    counts=counts, lock_counts=lock_counts)), replace=True)
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
    exit_events = [e for e in events if e.get('kind') == 'SHADOW_EXITED']
    if not revokes and not flow and not no_quotes and not exit_events:
        return None
    c = frozen['contract']
    q = c['quantity_btc']

    def _with_shadow_exit(result):
        if not exit_events:
            return result
        sx_ev = exit_events[-1]
        sx = sx_ev.get('body') or {}
        result.update(
            shadow_exit_event_id=sx_ev.get('event_id'),
            shadow_exit_at_ms=sx.get('shadow_exit_at_ms'),
            valuation_tier=sx.get('valuation_tier'),
            frozen_exit_pnl_btc=sx.get('exit_pnl_btc'),
            indicative_exit_pnl_btc=sx.get('indicative_exit_pnl_btc'),
            shadow_exit_snapshot_sha256=sx.get('snapshot_sha256'),
            counterfactual_hold_to_expiry_net_btc=hold_shadow_net,
        )
        return result

    if flow and settlement is not None and player.number(t0_credit) is not None:
        fill = flow[-1]['body']
        if fill.get('instrument') != c['short_instrument'] or fill.get('quantity_btc') != q:
            return _with_shadow_exit(dict(
                mode='UNPRICED_REVOKE', short_buyback_cost_btc=None, managed_net_btc=None,
                long_instrument=c['long_instrument'], unpriced_reason='short_fill_contract_mismatch',
            ))
        buyback = fill.get('cost_btc')
        buyback_fee = fill.get('fee_btc', 0.0)
        if any(type(x) not in (int, float) or not 0 <= x < float('inf') for x in (buyback, buyback_fee)):
            return _with_shadow_exit(dict(
                mode='UNPRICED_REVOKE', short_buyback_cost_btc=None, managed_net_btc=None,
                long_instrument=c['long_instrument'], unpriced_reason='invalid_buyback_quote',
            ))
        long_payout = cash.inverse_leg_payout(c['side'], c['long_strike_usd'], settlement, q)
        long_fee = fill.get('long_delivery_fee_btc', 0.0)
        managed = round(t0_credit - buyback - buyback_fee + long_payout - long_fee, 8)
        economy = ((link_rec or {}).get('t0') or {}).get('economy') or {}
        return _with_shadow_exit(dict(
            mode='SHADOW_MANAGED', simulated_short_fill_event=flow[-1]['event_id'],
            short_remaining=0, long_instrument=c['long_instrument'],
            long_remaining_before_expiry_btc=q, long_settlement_credit_btc=long_payout,
            long_remaining_after_expiry=0,
            entry_credit_btc=economy.get('visible_credit_btc'),
            t0_net_credit_after_entry_fee_btc=t0_credit,
            fees_btc=dict(entry=economy.get('theoretical_two_leg_fee_btc'), buyback=buyback_fee, delivery=long_fee),
            short_buyback_cost_btc=buyback, quoted_at_ms=fill.get('quoted_at_ms'),
            managed_net_btc=managed, original_hold_net_btc=hold_shadow_net,
            delta_btc=round(managed - hold_shadow_net, 8) if hold_shadow_net is not None else None,
        ))
    if flow and settlement is None:
        return _with_shadow_exit(dict(
            mode='UNPRICED_REVOKE', short_buyback_cost_btc=None, managed_net_btc=None,
            long_instrument=c['long_instrument'], long_remaining_before_expiry_btc=q,
            revoked_at_ms=revokes[-1].get('observed_at_ms') or revokes[-1].get('recorded_at_ms') if revokes else None,
            unpriced_reason='settlement_price_missing',
        ))
    sx_body = (exit_events[-1].get('body') or {}) if exit_events else {}
    reason = (
        ((no_quotes[-1].get('body') or {}).get('quote_gap_reasons') if no_quotes else None)
        or sx_body.get('quote_gap_reasons')
        or ['no_reliable_short_buyback_quote']
    )
    revoked_at = (
        (revokes[-1].get('observed_at_ms') or revokes[-1].get('recorded_at_ms'))
        if revokes else sx_body.get('shadow_exit_at_ms')
    )
    return _with_shadow_exit(dict(
        mode='UNPRICED_REVOKE', short_buyback_cost_btc=None, managed_net_btc=None,
        long_instrument=c['long_instrument'], long_remaining_before_expiry_btc=q,
        revoked_at_ms=revoked_at,
        unpriced_reason=reason[0] if isinstance(reason, list) and reason else reason,
    ))


def model_identity(model):
    return isinstance(model, str) and bool(model.strip()) and len(model) <= 120 and model.isprintable()


def _is_strictly_outside(price, contract):
    if hasattr(condition, 'classify_price_state'):
        state = condition.classify_price_state(price, contract)
        if isinstance(state, str):
            return state == 'OUTSIDE'
        if isinstance(state, dict):
            return state.get('state') == 'OUTSIDE' and state.get('strictly_outside_short', True) is True
    return player.responsibility_zone(price, contract) == '在责任区外'


_PUNCT_TRANS = str.maketrans('，。：；（）【】“”‘’　', ',.:;()[]""\'\' ')
_TERMINAL_END_RE = re.compile(r'[。！？.!?][”’"\'）)\]】」』]*$')
_SHORT_LABEL_PREFIX_RE = re.compile(r'^(?!\d{1,2}:\d{2})(?:[^：:\n（(]{1,24}(?:[（(][^）)\n]{1,24}[）)])?)[：:]\s*')


def _norm_excerpt(value):
    cleaned = re.sub(r'(?m)^\s*(?:[-*•]|\d+[.)、]|[一二三四五六七八九十]+[、.．])\s+', '', value)
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


_SECTION_TAG_OPT = r'(?:\s*[（(\[][^）)\]\n]{1,24}[\])）])?'
_SECTION_LABELS = (
    ('put', rf'(?:Put|PUT)\s*(?:侧)?(?:责任)?(?:路径)?(?:判断|分析|评估)?{_SECTION_TAG_OPT}'),
    ('call', rf'(?:Call|CALL)\s*(?:侧)?(?:责任)?(?:路径)?(?:判断|分析|评估)?{_SECTION_TAG_OPT}'),
    ('put_counter', rf'(?:Put|PUT)\s*(?:侧)?(?:最强)?反证{_SECTION_TAG_OPT}'),
    ('call_counter', rf'(?:Call|CALL)\s*(?:侧)?(?:最强)?反证{_SECTION_TAG_OPT}'),
    ('comparison', rf'(?:两侧|双侧)?(?:对比|比较)(?:与)?(?:择优|结论)?{_SECTION_TAG_OPT}'),
    ('ard_responsibility_meaning', rf'ARD\s*责任(?:相关性|含义|意义){_SECTION_TAG_OPT}'),
    ('ard_review_on', rf'ARD\s*(?:复核(?:变化|条件|触发)|触发复核){_SECTION_TAG_OPT}'),
    ('ard', rf'ARD\s*(?:责任)?(?:判读|解读|说明){_SECTION_TAG_OPT}'),
    ('historical_facts', rf'(?:冻结)?历史事实(?:冻结)?{_SECTION_TAG_OPT}'),
    ('accepted_conditions', rf'(?:(?:持续)?承担条件|持续条件){_SECTION_TAG_OPT}'),
    ('confirmation_excerpt', rf'(?:(?:机械)?(?:有效)?(?:破坏)?(?:确认)?(?:口径|标准)|破坏口径|有效破坏标准){_SECTION_TAG_OPT}'),
)


def extract_labeled_sections(text):
    """Deterministically extract full paragraph text per stable Chinese S/C/M section label."""
    if not isinstance(text, str) or not text.strip():
        return {}
    sections = {}
    current_key = None
    current_pat = None
    current_lines = []
    heading_pending = None

    def _flush():
        nonlocal current_key, current_pat, current_lines
        if current_key and current_lines and current_key not in sections:
            raw_full = '\n'.join(current_lines).strip()
            cleaned = re.sub(r'^[\s\-*•#>]+|\*\*', '', raw_full).strip()
            cleaned = cleaned.replace('`', '')
            if current_pat is not None:
                m_pref = re.match(rf'^(?:{current_pat})\s*[：:]\s*', cleaned, re.IGNORECASE)
                body = cleaned[m_pref.end():].strip() if m_pref else raw_full
            else:
                body = raw_full
            sections[current_key] = {'raw_full': raw_full, 'full': raw_full, 'body': body}
        current_key = None
        current_pat = None
        current_lines = []

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            if current_key is not None:
                _flush()
            continue
        if '<ANNOTATION_JSON>' in stripped:
            _flush()
            break
        cleaned_line = re.sub(r'^(?:[-*•]|\d+[.)、]|[一二三四五六七八九十]+[、.．]|#{1,4})\s*', '', stripped).replace('**', '').replace('`', '').strip()
        matched_inline = None
        matched_inline_pat = None
        matched_heading = None
        for key_name, pat in _SECTION_LABELS:
            if re.match(rf'^(?:{pat})\s*[：:]\s*\S', cleaned_line, re.IGNORECASE):
                matched_inline = key_name
                matched_inline_pat = pat
                break
            if re.match(rf'^(?:{pat})\s*[：:]?\s*$', cleaned_line, re.IGNORECASE):
                matched_heading = key_name
                break
        if matched_inline is not None:
            _flush()
            heading_pending = None
            current_key = matched_inline
            current_pat = matched_inline_pat
            current_lines = [stripped]
        elif matched_heading is not None:
            _flush()
            heading_pending = matched_heading
        elif heading_pending is not None:
            current_key = heading_pending
            current_pat = None
            heading_pending = None
            current_lines = [stripped]
        elif current_key is not None:
            is_list_item = bool(re.match(r'^(?:[-*•]|\d+[.)、]|[（(][一二三四五六七八九十\d]+[）)])\s*', stripped))
            if re.match(r'^#{1,4}\s+\S', stripped) or (not is_list_item and _SHORT_LABEL_PREFIX_RE.match(cleaned_line)):
                _flush()
            else:
                current_lines.append(stripped)
    _flush()
    return sections


def inspect_excerpt_integrity(excerpt, text, *, expected_section=None):
    """Classify verbatim excerpt completeness: complete paragraph vs sentence truncation vs mid-word cut.

    Covers full section if explicitly labeled, and supports natural prose paragraphs without requiring
    rigid colon-prefixed headers.
    """
    if not isinstance(excerpt, str) or not excerpt.strip():
        return {'status': 'EMPTY', 'complete': False, 'matched': False, 'section': expected_section}
    if not excerpt_in_text(excerpt, text):
        return {'status': 'NOT_IN_TEXT', 'complete': False, 'matched': False, 'section': expected_section}

    ex_stripped = excerpt.strip()
    ends_terminal = bool(_TERMINAL_END_RE.search(ex_stripped))
    norm_ex = _norm_excerpt(ex_stripped)
    sections = extract_labeled_sections(text)
    owner = None
    matched_section = expected_section
    from_labeled_section = False
    ard_subfields = {'ard_responsibility_meaning', 'ard_review_on'}
    ard_allowed = {'ard', *ard_subfields}

    def _wrong_section(sec_key, *, matched=True):
        return {
            'status': 'WRONG_SECTION',
            'complete': False,
            'matched': matched,
            'section': sec_key,
            'expected_section': expected_section,
            'coverage_ratio': 0.0,
        }

    def _check_multiline(raw_body):
        # Support intentional multi-paragraph verbatim excerpts (e.g., 4-item C with blank lines inside)
        # when bounded cleanly on both sides by paragraph/heading boundaries and ending with terminal punctuation.
        exact_idx = raw_body.find(ex_stripped)
        if exact_idx >= 0 and ends_terminal and ('\n' in ex_stripped):
            before = raw_body[:exact_idx]
            after = raw_body[exact_idx + len(ex_stripped):]
            clean_before = (exact_idx == 0) or before.endswith('\n') or bool(re.search(r'[：:]\s*$', before))
            clean_after = (not after.strip()) or bool(re.match(r'^\s*\n', after))
            if clean_before and clean_after:
                return {'status': 'COMPLETE_PARAGRAPH', 'complete': True, 'matched': True, 'section': matched_section, 'coverage_ratio': 1.0}
        elif ends_terminal and ('\n' in ex_stripped):
            raw_lines = raw_body.splitlines()
            non_empty = [(idx, ln.strip()) for idx, ln in enumerate(raw_lines) if ln.strip()]
            for pos_i, (line_idx, ln_text) in enumerate(non_empty):
                cleaned_li = re.sub(r'^(?:[-*•]|\d+[.)、])\s*', '', ln_text).replace('**', '').replace('`', '').strip()
                m_pref = _SHORT_LABEL_PREFIX_RE.match(cleaned_li)
                candidates = [ln_text]
                if m_pref and cleaned_li[m_pref.end():].strip():
                    candidates.append(cleaned_li[m_pref.end():].strip())
                prev_ln = raw_lines[line_idx - 1].strip() if line_idx > 0 else ''
                clean_start = (
                    line_idx == 0
                    or not prev_ln
                    or bool(_TERMINAL_END_RE.search(prev_ln))
                    or bool(re.search(r'[：:]\s*$', prev_ln))
                    or bool(re.match(r'^(?:#{1,4}\s+\S|\*\*[^*]+\*\*\s*$)', prev_ln))
                    or bool(re.match(r'^(?:#{1,4}\s+|[-*•]\s*|\d+[.)、]\s*|[（(][一二三四五六七八九十\d]+[）)]\s*)', ln_text))
                )
                for start_idx_cand, start_part in enumerate(candidates):
                    if not (clean_start or start_idx_cand == 1):
                        continue
                    norm_start = _norm_excerpt(start_part)
                    if not norm_start or not norm_ex.startswith(norm_start):
                        continue
                    parts = [start_part]
                    for pos_j in range(pos_i + 1, len(non_empty)):
                        end_line_idx, next_ln = non_empty[pos_j]
                        parts.append(next_ln)
                        cand_norm = _norm_excerpt('\n'.join(parts))
                        if cand_norm == norm_ex:
                            clean_end = (
                                end_line_idx == len(raw_lines) - 1
                                or not raw_lines[end_line_idx + 1].strip()
                                or bool(_TERMINAL_END_RE.search(next_ln))
                            )
                            if clean_end:
                                return {'status': 'COMPLETE_PARAGRAPH', 'complete': True, 'matched': True, 'section': matched_section, 'coverage_ratio': 1.0}
                            break
                        if len(cand_norm) >= len(norm_ex) or not norm_ex.startswith(cand_norm):
                            break
        return None

    def _match_in_section(sec):
        return norm_ex in _norm_excerpt(sec['full']) or norm_ex in _norm_excerpt(sec['body'])

    raw_body = text.split('<ANNOTATION_JSON>')[0]
    if expected_section in ard_subfields:
        allowed_keys = tuple(dict.fromkeys((expected_section, 'ard', 'ard_responsibility_meaning', 'ard_review_on')))
        for sec_key in allowed_keys:
            sec = sections.get(sec_key)
            if sec is not None and _match_in_section(sec):
                owner = sec
                matched_section = sec_key
                from_labeled_section = True
                break
        if owner is None:
            for sec_key, sec in sections.items():
                if sec_key not in ard_allowed and _match_in_section(sec):
                    return _wrong_section(sec_key)
            if expected_section in sections:
                sec = sections[expected_section]
                if ends_terminal and ('\n' in ex_stripped) and (
                    norm_ex.startswith(_norm_excerpt(sec['full'])) or norm_ex.startswith(_norm_excerpt(sec['body']))
                ):
                    for sec_key, other_sec in sections.items():
                        if sec_key not in ard_allowed:
                            ob = _norm_excerpt(other_sec['body'])
                            if ob and ob in norm_ex:
                                return _wrong_section(sec_key)
                    multi = _check_multiline(raw_body)
                    if multi is not None:
                        return multi
                owner = sec
                from_labeled_section = True
    elif expected_section:
        if expected_section in sections:
            sec = sections[expected_section]
            if _match_in_section(sec):
                owner = sec
                from_labeled_section = True
            else:
                for sec_key, other_sec in sections.items():
                    if sec_key != expected_section and _match_in_section(other_sec):
                        return _wrong_section(sec_key)
                if ends_terminal and ('\n' in ex_stripped) and (
                    norm_ex.startswith(_norm_excerpt(sec['full'])) or norm_ex.startswith(_norm_excerpt(sec['body']))
                ):
                    for sec_key, other_sec in sections.items():
                        if sec_key != expected_section:
                            ob = _norm_excerpt(other_sec['body'])
                            if ob and ob in norm_ex:
                                return _wrong_section(sec_key)
                    multi = _check_multiline(raw_body)
                    if multi is not None:
                        return multi
                owner = sec
                from_labeled_section = True
        else:
            for sec_key, other_sec in sections.items():
                if _match_in_section(other_sec):
                    return _wrong_section(sec_key)
    else:
        for sec_key, sec in sections.items():
            if _match_in_section(sec):
                owner = sec
                matched_section = sec_key
                from_labeled_section = True
                break

    if owner is None:
        multi = _check_multiline(raw_body)
        if multi is not None:
            return multi
        for block in re.split(r'\n\s*\n', raw_body):
            b = block.strip()
            if not b or norm_ex not in _norm_excerpt(b):
                continue
            lines = [ln.strip() for ln in b.splitlines() if ln.strip()]
            # Support single-newline-separated natural prose paragraphs inside a block.
            if len(lines) > 1:
                # Strip a leading markdown heading line if present.
                if re.match(r'^(?:#{1,4}\s+\S|\*\*[^*]+\*\*\s*$)', lines[0]):
                    body_no_head = '\n'.join(lines[1:]).strip()
                    if body_no_head and norm_ex == _norm_excerpt(body_no_head):
                        owner = {'raw_full': b, 'full': body_no_head, 'body': body_no_head}
                        break
                matched_line = next(
                    (ln for ln in lines if norm_ex in _norm_excerpt(ln)),
                    None,
                )
                if matched_line is not None:
                    cleaned_ln = re.sub(r'^(?:[-*•]|\d+[.)、])\s*', '', matched_line).replace('**', '').replace('`', '').strip()
                    m_pref = _SHORT_LABEL_PREFIX_RE.match(cleaned_ln)
                    body_only = cleaned_ln[m_pref.end():].strip() if m_pref else matched_line
                    owner = {'raw_full': matched_line, 'full': matched_line, 'body': body_only}
                    break
            cleaned_b = re.sub(r'^(?:[-*•]|\d+[.)、])\s*', '', b).replace('**', '').replace('`', '').strip()
            m_pref = _SHORT_LABEL_PREFIX_RE.match(cleaned_b)
            body_only = cleaned_b[m_pref.end():].strip() if m_pref else b
            owner = {'raw_full': b, 'full': b, 'body': body_only}
            break
        if owner is None:
            owner = {'raw_full': raw_body.strip(), 'full': raw_body.strip(), 'body': raw_body.strip()}

    norm_raw_full = _norm_excerpt(owner.get('raw_full', owner['full']))
    norm_full = _norm_excerpt(owner['full'])
    norm_body = _norm_excerpt(owner['body'])
    if norm_ex in (norm_raw_full, norm_full, norm_body):
        if not ends_terminal and not from_labeled_section:
            trailing_connector = bool(re.search(r'[,，、:：;；(\[（\-—–]$', ex_stripped))
            open_paren_mismatch = (
                ex_stripped.count('(') > ex_stripped.count(')')
                or ex_stripped.count('（') > ex_stripped.count('）')
                or ex_stripped.count('[') > ex_stripped.count(']')
                or ex_stripped.count('【') > ex_stripped.count('】')
                or ex_stripped.count('「') > ex_stripped.count('」')
                or ex_stripped.count('“') > ex_stripped.count('”')
                or ex_stripped.count('‘') > ex_stripped.count('’')
                or ex_stripped.count('"') % 2 == 1
            )
            if len(norm_ex) < 16 or trailing_connector or open_paren_mismatch:
                return {
                    'status': 'MID_SENTENCE_TRUNCATION',
                    'complete': False,
                    'matched': True,
                    'section': matched_section,
                    'coverage_ratio': 1.0,
                    'missing_trailing_chars': 0,
                }
        return {'status': 'COMPLETE_PARAGRAPH', 'complete': True, 'matched': True, 'section': matched_section, 'coverage_ratio': 1.0}

    idx = norm_full.find(norm_ex)
    if idx < 0:
        idx_body = norm_body.find(norm_ex)
        if idx_body < 0:
            return _wrong_section(None, matched=False)
        trailing = norm_body[idx_body + len(norm_ex):]
        leading = norm_body[:idx_body]
        den = max(1, len(norm_body))
    else:
        trailing = norm_full[idx + len(norm_ex):]
        leading = norm_full[:idx]
        den = max(1, len(norm_full))
    # ARD subfields (meaning + review_on) may be two complete sentences within one ARD paragraph.
    if expected_section in {'ard_responsibility_meaning', 'ard_review_on'} and ends_terminal:
        clean_start = (not leading) or leading.endswith((':', '：', '。', '！', '？', '.', '!', '?', ';', '；'))
        if clean_start:
            return {
                'status': 'COMPLETE_SENTENCE',
                'complete': True,
                'matched': True,
                'section': matched_section,
                'coverage_ratio': round(len(norm_ex) / den, 4),
            }
    status = 'PARTIAL_SENTENCE_SUBSTRING' if (ends_terminal and not leading) else 'MID_SENTENCE_TRUNCATION'
    if not ends_terminal and trailing:
        status = 'MID_SENTENCE_TRUNCATION'
    elif leading and not leading.endswith((':', '：')) and ':' not in leading:
        status = 'MID_SENTENCE_TRUNCATION'
    return {
        'status': status,
        'complete': False,
        'matched': True,
        'section': matched_section,
        'coverage_ratio': round(len(norm_ex) / den, 4),
        'missing_trailing_chars': len(trailing),
    }


def build_annotation_from_sections(text, *, action, depends_on_ard, invalidation=None,
                                   check_interval_ms=300_000, require_outside_at_t0=False):
    """Deterministically populate S/C/M excerpts directly from labeled prose sections (Option B helper)."""
    sections = extract_labeled_sections(text)
    required = ['put', 'call', 'comparison', 'put_counter', 'call_counter']
    missing = [k for k in required if k not in sections]
    if missing:
        raise ValueError(f'missing labeled sections for deterministic extraction: {", ".join(missing)}')
    out = {k: sections[k]['full'] for k in required}
    out['depends_on_ard'] = bool(depends_on_ard)
    if action == 'Accept':
        plan_keys = ['historical_facts', 'accepted_conditions', 'confirmation_excerpt']
        missing_plan = [k for k in plan_keys if k not in sections]
        if missing_plan:
            raise ValueError(f'missing labeled condition sections: {", ".join(missing_plan)}')
        out['condition_plan'] = {
            'invalidation': invalidation,
            'check_interval_ms': check_interval_ms,
            'require_outside_at_t0': bool(require_outside_at_t0),
            **{k: sections[k]['full'] for k in plan_keys},
        }
    return out


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
    response.add_argument('--condition-plan', type=Path)
    response.add_argument('--configuration', type=Path)
    response.add_argument('--model')
    response.add_argument('--prompt-version')
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
            meta = d.verify_evidence(hand)
            exp = read(hand / 'expected.json')
            plan_pv = (exp.get('plan') or {}).get('prompt_version')
            p0_profile = getattr(player, 'INPUT_PROFILE_P0_STRICT', 'p0_strict@1')
            hand_profile = meta.get('input_profile')
            pv = args.prompt_version
            raw_bytes = args.raw.read_bytes() if args.raw else None
            ann_obj = read(args.annotation) if args.annotation else None
            c_plan_obj = read(args.condition_plan) if args.condition_plan else None
            cfg_obj = read(args.configuration) if args.configuration else None
            sys_sha = None
            cv_report = None
            full_raw_bytes = None
            def _extract_cli_annotation_parts():
                fail_raw, fail_ann, fail_cplan = raw_bytes, ann_obj, c_plan_obj
                if fail_ann is None and raw_bytes is not None:
                    m_tag = re.search(
                        r'<ANNOTATION_JSON>\s*([\s\S]*?)\s*</ANNOTATION_JSON>',
                        raw_bytes.decode('utf-8', errors='replace'),
                    )
                    if m_tag:
                        try:
                            tag_obj = json.loads(m_tag.group(1))
                        except ValueError:
                            tag_obj = None
                        if isinstance(tag_obj, dict):
                            fail_ann = tag_obj
                            fail_raw = (
                                raw_bytes.decode('utf-8', errors='replace')[:m_tag.start()].rstrip() + '\n'
                            ).encode('utf-8')
                            if fail_cplan is None and isinstance(tag_obj.get('condition_plan'), dict):
                                fail_cplan = tag_obj['condition_plan']
                return fail_raw, fail_ann, fail_cplan

            needs_prompt_gate = (
                (hand_profile == p0_profile)
                or (plan_pv is not None)
                or (pv is not None and pv != 'player_initial_system@1.0')
            )
            if needs_prompt_gate:
                if __package__:
                    from . import astra_player_review_v1 as review_mod
                else:
                    import astra_player_review_v1 as review_mod
                gate_err = None
                if not pv or pv not in review_mod.INITIAL_SYSTEM_PROMPTS:
                    gate_err = f'candidate response requires a known --prompt-version (got {pv!r})'
                elif hand_profile == p0_profile and pv == 'player_initial_system@1.0':
                    gate_err = 'p0_strict@1 hand forbids legacy player_initial_system@1.0 --prompt-version'
                elif plan_pv is not None and pv != plan_pv:
                    gate_err = f'--prompt-version {pv!r} does not match plan prompt_version {plan_pv!r}'
                if gate_err is not None:
                    if args.status == 'RECEIVED' and raw_bytes is not None:
                        fail_sys_sha = (
                            sha(review_mod.INITIAL_SYSTEM_PROMPTS[pv].encode('utf-8'))
                            if pv in review_mod.INITIAL_SYSTEM_PROMPTS else None
                        )
                        fail_raw, fail_ann, fail_cplan = _extract_cli_annotation_parts()
                        try:
                            d.respond(
                                hand, args.attempt, 'RECEIVED', fail_raw, fail_ann,
                                model=args.model, configuration=cfg_obj,
                                requested=args.requested_ms, decided=args.decided_ms, channel=args.channel,
                                error=gate_err, full_raw=raw_bytes, condition_plan=fail_cplan,
                                prompt_version=pv, system_sha256=fail_sys_sha, input_profile=hand_profile,
                                error_code='CONTRACT_VALIDATION_FAILED',
                            )
                        except (OSError, ValueError, KeyError, TypeError):
                            pass
                    parser.exit(2, f'{gate_err}\n')
                sys_sha = sha(review_mod.INITIAL_SYSTEM_PROMPTS[pv].encode('utf-8'))
                if args.status == 'RECEIVED' and raw_bytes is not None and pv in review_mod.CANDIDATE_STRICT_PROMPT_VERSIONS:
                    src = read(hand / 'evidence' / 'source.json')
                    contracts_pair = {
                        c['side']: player.bind(cs, c, c['side'])
                        for cs, c in zip(src['cases'], src['cards'])
                        if c.get('side') in {'put', 'call'}
                    }
                    raw_text = raw_bytes.decode('utf-8')
                    if '<ANNOTATION_JSON>' in raw_text or re.search(r'```json\s*[\s\S]*?```', raw_text, re.IGNORECASE):
                        llm_text = raw_text
                    elif isinstance(ann_obj, dict):
                        merged_ann = dict(ann_obj)
                        if c_plan_obj is not None and 'condition_plan' not in merged_ann:
                            merged_ann['condition_plan'] = c_plan_obj
                        llm_text = raw_text.rstrip() + '\n\n<ANNOTATION_JSON>\n' + json.dumps(merged_ann, ensure_ascii=False) + '\n</ANNOTATION_JSON>'
                    else:
                        msg = 'candidate response requires <ANNOTATION_JSON> in --raw or --annotation JSON'
                        try:
                            d.respond(
                                hand, args.attempt, 'RECEIVED', raw_bytes, ann_obj,
                                model=args.model, configuration=cfg_obj,
                                requested=args.requested_ms, decided=args.decided_ms, channel=args.channel,
                                error=msg, full_raw=raw_bytes, condition_plan=c_plan_obj,
                                prompt_version=pv, system_sha256=sys_sha, input_profile=hand_profile,
                                error_code='CONTRACT_VALIDATION_FAILED',
                            )
                        except (OSError, ValueError, KeyError, TypeError):
                            pass
                        parser.exit(2, msg + '\n')
                    req_stub = review_mod.build_initial_request_payload(
                        (hand / 'evidence' / 'input.md').read_text(encoding='utf-8'),
                        contract_pair=contracts_pair,
                        model=args.model or 'manual',
                        prompt_version=pv,
                        input_profile=hand_profile,
                        fidelity='RECONSTRUCTED',
                    )
                    try:
                        parsed_cli = review_mod.parse_initial_reply(
                            {'text': llm_text, 'model': args.model or 'manual'},
                            req_stub,
                            contracts=contracts_pair,
                        )
                    except review_mod.ContractValidationError as exc:
                        parsed_fail = exc.parsed_reply if isinstance(exc.parsed_reply, dict) else {}
                        try:
                            d.respond(
                                hand, args.attempt, 'RECEIVED',
                                parsed_fail.get('raw_bytes') if parsed_fail.get('raw_bytes') is not None else raw_bytes,
                                parsed_fail.get('annotation') if parsed_fail.get('annotation') is not None else ann_obj,
                                model=args.model, configuration=cfg_obj,
                                requested=args.requested_ms, decided=args.decided_ms, channel=args.channel,
                                error=str(exc), full_raw=raw_bytes,
                                condition_plan=parsed_fail.get('condition_plan') if parsed_fail.get('condition_plan') is not None else c_plan_obj,
                                prompt_version=pv, system_sha256=sys_sha, input_profile=hand_profile,
                                contract_validation=exc.contract_validation,
                                error_code='CONTRACT_VALIDATION_FAILED',
                            )
                        except (OSError, ValueError, KeyError, TypeError):
                            pass
                        parser.exit(2, f'contract validation failed: {exc}\n')
                    except (ValueError, KeyError, TypeError) as exc:
                        fail_raw, fail_ann, fail_cplan = _extract_cli_annotation_parts()
                        try:
                            d.respond(
                                hand, args.attempt, 'RECEIVED', fail_raw, fail_ann,
                                model=args.model, configuration=cfg_obj,
                                requested=args.requested_ms, decided=args.decided_ms, channel=args.channel,
                                error=str(exc), full_raw=raw_bytes, condition_plan=fail_cplan,
                                prompt_version=pv, system_sha256=sys_sha, input_profile=hand_profile,
                                error_code='CONTRACT_VALIDATION_FAILED',
                            )
                        except (OSError, ValueError, KeyError, TypeError):
                            pass
                        parser.exit(2, f'contract validation failed: {exc}\n')
                    full_raw_bytes = raw_bytes
                    raw_bytes = parsed_cli['raw_bytes']
                    ann_obj = parsed_cli['annotation']
                    c_plan_obj = parsed_cli['condition_plan']
                    cv_report = parsed_cli.get('contract_validation')
            result = d.respond(hand, args.attempt, args.status, raw_bytes,
                               ann_obj, model=args.model,
                               configuration=cfg_obj,
                               requested=args.requested_ms, decided=args.decided_ms, channel=args.channel,
                               error=args.error, full_raw=full_raw_bytes, condition_plan=c_plan_obj,
                               prompt_version=pv, system_sha256=sys_sha, input_profile=hand_profile,
                               contract_validation=cv_report)
            result = {k: result[k] for k in ('hand_id', 'attempt_id', 'status', 'error')}
        elif cmd == 'reparse':
            result = d.reparse(hand, args.attempt, read(args.annotation), args.reason)
        elif cmd == 'freeze':
            try:
                result = d.episode(hand, read(args.condition), player.read_json(args.entry_path) if args.entry_path else None)
            except ValueError as exc:
                parser.exit(2, f'{exc}\n')
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
