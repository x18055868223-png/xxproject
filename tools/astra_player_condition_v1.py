"""Freeze one shadow player's boundary condition and check it at agreed UTC ticks.

No orders, model calls, entry policy or automatic Hold. This narrow helper supports
only explicit new-entry rules; it does not interpret arbitrary prose conditions.
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

try:
    from .astra_player_input_v1 import number, responsibility_trace, responsibility_zone
except ImportError:
    from astra_player_input_v1 import number, responsibility_trace, responsibility_zone

MINUTE = 60_000
RULES = {
    'new_entry': '入场后出现严格进入责任区的采样；即使随后已返回，在固定检查点仍记为触发。',
    'entry_not_returned': '入场后出现严格进入责任区的采样，且至固定检查点没有后续严格返回责任外的采样。',
}
SAMPLING = 'btc_usd UTC一分钟采样；严格越过短腿才进入，严格回到外侧才返回；触及不进入也不返回，不推断采样间路径。'
CONTRACT_FIELDS = ('venue', 'underlying', 'settlement_currency', 'quantity_btc', 'side',
                   'short_instrument', 'long_instrument', 'short_strike_usd', 'long_strike_usd', 'expiry_ms')


def validate(record):
    c = record['contract']
    short, long, price = [number(x) for x in (c['short_strike_usd'], c['long_strike_usd'], record['entry_index_usd'])]
    if (c['side'] not in ('put', 'call') or any(x is None or x <= 0 for x in (short, long, price))
            or (short <= long if c['side'] == 'put' else short >= long)
            or not c.get('short_instrument') or not c.get('long_instrument')):
        raise ValueError('invalid frozen responsibility')
    clock = record['check_clock']
    entry, first, interval, expiry = (record['entry_at_ms'], clock['first_at_ms'], clock['interval_ms'], c['expiry_ms'])
    if (any(type(x) is not int for x in (entry, first, interval, expiry))
            or not 0 < entry < first < expiry or first % MINUTE or interval < MINUTE or interval % MINUTE):
        raise ValueError('explicit UTC minute-aligned clock after entry and before expiry required')
    if (record['invalidation'] not in RULES or record['sampling'] != SAMPLING
            or record['invalidation_meaning'] != RULES[record['invalidation']]
            or not record['historical_facts'] or not record['accepted_conditions']):
        raise ValueError('explicit supported rule, sampling, history and ongoing conditions required')
    basis = record.get('ard_basis')
    if basis is not None:
        facts = basis['facts']
        if (facts['expiry_ms'] != c['expiry_ms'] or facts['index_name'] != 'btc_usd'
                or not 0 < facts['start_ms'] < facts['end_ms'] <= entry
                or not facts['observations'] or not facts['last']
                or not basis['responsibility_meaning'] or not basis['review_on']):
            raise ValueError('ARD basis needs same-expiry pre-entry facts and explicit responsibility meaning/review conditions')
        if any(not r['at'] <= r['ready'] <= facts['end_ms'] for r in facts['observations']):
            raise ValueError('future or unclocked ARD cannot be frozen as entry evidence')
        anchor = facts['observations'][-1]
        if (number(anchor['pin']) is None or anchor['pin'] <= 0
                or facts['last']['pin'] != anchor['pin'] or facts['last']['t'] > facts['end_ms']):
            raise ValueError('ARD entry reference must be an observed usable Pin, not a later or invalid value')


def freeze_condition(path, *, contract, entry_at_ms, entry_index_usd, historical_facts,
                     accepted_conditions, invalidation, first_check_ms, check_interval_ms,
                     ard_basis=None):
    """Write a new assessment artifact only; never update an existing frozen hand."""
    # Freeze the responsibility, not an archive experiment's old exit_policy.
    record = dict(contract={k: contract[k] for k in CONTRACT_FIELDS if k in contract},
                  entry_at_ms=entry_at_ms, entry_index_usd=entry_index_usd,
                  historical_facts=historical_facts, accepted_conditions=accepted_conditions,
                  invalidation=invalidation, invalidation_meaning=RULES.get(invalidation),
                  check_clock=dict(first_at_ms=first_check_ms, interval_ms=check_interval_ms),
                  sampling=SAMPLING)
    if ard_basis is not None:
        # Assessment attachment only, never written back into market observations.
        record['ard_basis'] = deepcopy(ard_basis)
    validate(record)
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return record


def compare_ard_basis(frozen_path, current_facts, check_at_ms):
    """Read-only WPEM handoff: compare evidence, never alter the frozen exit rule.

    The caller supplies analyze() facts from the same expiry at an agreed tick.
    Event comparison needs the original start, half-width and source/method.
    Different rolling windows still permit explicit position comparison, not a
    change in residence rate. check_condition remains the separate boundary check.
    """
    record = json.loads(Path(frozen_path).read_text(encoding='utf-8'))
    validate(record)
    if 'ard_basis' not in record:
        return {'note': '入场未依赖ARD；不事后新增ARD承担条件。'}
    c, tick = record['contract'], record['check_clock']
    if (type(check_at_ms) is not int or not tick['first_at_ms'] <= check_at_ms < c['expiry_ms']
            or (check_at_ms-tick['first_at_ms']) % tick['interval_ms']):
        raise ValueError('ARD comparison must use the original check clock')
    old = record['ard_basis']['facts']
    if (current_facts['expiry_ms'] != c['expiry_ms'] or current_facts['index_name'] != 'btc_usd'
            or current_facts['end_ms'] != check_at_ms):
        raise ValueError('same expiry/index and exact check cutoff required')
    if any(not r['at'] <= r['ready'] <= check_at_ms for r in current_facts['observations']):
        raise ValueError('future ARD observation at check')
    entry = record['entry_at_ms']
    original = old['observations'][-1]
    latest = current_facts['observations'][-1] if current_facts['observations'] else None
    price = current_facts['last']
    comparable = bool(original['method'] and original['source'] and latest
                      and (original['method'],original['source']) == (latest['method'],latest['source']))
    same_measure = (comparable and old['method_comparable'] and current_facts['method_comparable']
                    and current_facts['start_ms'] == old['start_ms']
                    and old['band'] is not None and current_facts['band'] == old['band'])
    fresh = bool(price and price['t'] == check_at_ms and latest
                 and price['pin'] == latest['pin'])
    sign = 1 if c['side'] == 'put' else -1
    return dict(
        frozen_anchor=deepcopy(original), current_anchor=deepcopy(latest),
        current_sample_available=fresh,
        pin_price_changed=(latest['pin'] != original['pin']) if fresh else None,
        source_method_comparable=comparable,
        newly_available_anchor_observations=[deepcopy(r) for r in current_facts['observations'] if r['ready'] > entry],
        new_neighborhood_events=[deepcopy(e) for e in current_facts['events']
                                 if entry < e['at_ms'] <= check_at_ms and e['pin'] == original['pin']] if same_measure else None,
        entry_distance_to_frozen_pin_usd=abs(record['entry_index_usd']-original['pin']),
        current_distance_to_frozen_pin_usd=abs(price['price']-original['pin']) if fresh else None,
        current_inside_frozen_neighborhood=(abs(price['price']-original['pin']) <= old['band']['half_width_usd'])
                                          if fresh and old['band'] else None,
        entry_responsibility_margin_usd=sign*(record['entry_index_usd']-c['short_strike_usd']),
        current_responsibility_margin_usd=sign*(price['price']-c['short_strike_usd']) if fresh else None,
        current_zone=responsibility_zone(price['price'],c) if fresh else None,
        review_on=deepcopy(record['ard_basis']['review_on']),
        note='以入场Pin作固定参照；新增指入场后才可得，观测早于入场的不冒充新迁移。不同窗/半宽/口径不比较停留率或离返；复核事实不自动触发Revoke，破坏仍由原冻结规则判断。')


def check_condition(frozen_path, index_path, check_at_ms):
    """Read the frozen rule; return its factual status, never a trading action.

    Missing/off-grid samples are uncheckable. Old pre-entry paths and future data
    are ignored. A not-triggered boundary rule does not certify other conditions.
    """
    def result(status, reason, **facts):
        return dict(status=status, check_at_ms=check_at_ms, reason=reason, **facts)

    try:
        record = json.loads(Path(frozen_path).read_text(encoding='utf-8'))
        validate(record)
    except (OSError, ValueError, KeyError, TypeError):
        return result('uncheckable', '原冻结条件或检查/确认口径缺失，不能判触发或未触发。')
    c, clock = record['contract'], record['check_clock']
    if type(check_at_ms) is not int or check_at_ms >= c['expiry_ms']:
        return result('uncheckable', '仅检查有效期限内的约定UTC时刻。')
    if check_at_ms < clock['first_at_ms'] or (check_at_ms-clock['first_at_ms']) % clock['interval_ms']:
        return result('not_due', '不是入场时冻结的检查点，不作触发判断。')
    if not isinstance(index_path, dict) or not isinstance(index_path.get('points'), list):
        return result('uncheckable', '未提供可检查的指数采样序列。')
    if index_path.get('index_name') != 'btc_usd' or index_path.get('expiry_ms') != c['expiry_ms']:
        return result('uncheckable', '路径指数或到期身份与冻结责任不同。')
    start = record['entry_at_ms']
    first_sample = (start // MINUTE + 1) * MINUTE
    points = []
    for row in index_path.get('points', []):
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            return result('uncheckable', '采样格式不完整。')
        t, p = row
        if type(t) is not int:
            return result('uncheckable', '采样时间不可靠。')
        if start < t <= check_at_ms:
            if number(p) is None or p <= 0:
                return result('uncheckable', '入场后指数采样缺失。')
            points.append((t, p))
    points.sort()
    if [t for t, _ in points] != list(range(first_sample, check_at_ms+1, MINUTE)):
        return result('uncheckable', '入场后至检查点缺分钟、时间错位或重复采样；不能补造进入/返回。')
    episodes = responsibility_trace([(start, record['entry_index_usd']), *points], c['side'], c['short_strike_usd'])['episodes']
    new = [e for e in episodes if e['entered_ms'] is not None and e['entered_ms'] > start]
    triggered = bool(new and (record['invalidation'] == 'new_entry' or new[-1]['returned_ms'] is None))
    return result('triggered' if triggered else 'not_triggered',
                  RULES[record['invalidation']] if triggered else '这条冻结边界规则未触发；不自动等同于全部承担条件成立或Hold。',
                  current_zone=responsibility_zone(points[-1][1], c),
                  latest_new_entry_ms=new[-1]['entered_ms'] if new else None,
                  return_after_latest_entry_ms=new[-1]['returned_ms'] if new else None)
