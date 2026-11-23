"""Freeze one shadow player's boundary condition and check it at agreed UTC ticks.

No orders, model calls, entry policy or automatic Hold. This narrow helper supports
only explicit new-entry rules; it does not interpret arbitrary prose conditions.
Fixed check clock starts at the next full UTC minute after entry and steps every
5 minutes by default ((entry_at_ms // 60_000 + 1) * 60_000 + k * interval_ms).
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

try:
    from .astra_player_input_v1 import number, responsibility_trace, responsibility_zone
except ImportError:
    from astra_player_input_v1 import number, responsibility_trace, responsibility_zone

MECHANICAL_CONTRACT_VERSION = "p2_strict@1"
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
    if record.get('require_outside_at_t0') is True:
        if classify_price_state(price, c)['state'] != 'OUTSIDE':
            raise ValueError('entry_index_usd must be strictly outside short strike when require_outside_at_t0 is True')
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
                     ard_basis=None, require_outside_at_t0=False):
    """Write a new assessment artifact only; never update an existing frozen hand."""
    # Freeze the responsibility, not an archive experiment's old exit_policy.
    record = dict(contract={k: contract[k] for k in CONTRACT_FIELDS if k in contract},
                  entry_at_ms=entry_at_ms, entry_index_usd=entry_index_usd,
                  historical_facts=historical_facts, accepted_conditions=accepted_conditions,
                  invalidation=invalidation, invalidation_meaning=RULES.get(invalidation),
                  check_clock=dict(first_at_ms=first_check_ms, interval_ms=check_interval_ms),
                  sampling=SAMPLING)
    if require_outside_at_t0:
        record['require_outside_at_t0'] = True
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


def _contract_legs(contract):
    if not isinstance(contract, dict):
        raise ValueError('valid contract mapping required')
    side = str(contract.get('side') or '').strip().lower()
    short, long = number(contract.get('short_strike_usd')), number(contract.get('long_strike_usd'))
    if (side not in ('put', 'call') or short is None or long is None or short <= 0 or long <= 0
            or (short <= long if side == 'put' else short >= long)):
        raise ValueError('valid contract side, short_strike_usd and long_strike_usd required')
    return side, float(short), float(long)


def _fmt_usd(value):
    return f'{value:,.1f}'.removesuffix('.0')


def _strike_pattern(value):
    if float(value).is_integer():
        whole = int(round(value))
        s = f'{whole:,}'
        if ',' in s:
            grouped = r'[,，\s]?'.join(re.escape(part) for part in s.split(','))
            return rf'(?<!\d){grouped}(?:\.0+)?(?!\d)'
        return rf'(?<!\d){whole}(?:\.0+)?(?!\d)'
    return rf'(?<!\d){re.escape(str(value))}(?!\d)'


def geometric_spread_band(contract):
    """Return (low, high) payout band between short and long strikes (geometry only)."""
    _, short, long = _contract_legs(contract)
    return tuple(sorted((short, long)))


def classify_price_state(price, contract):
    """Classify a single spot/index level under both spread geometry and unilateral entry rules."""
    idx = number(price)
    if idx is None or idx <= 0:
        raise ValueError('positive finite price required')
    side, short, long = _contract_legs(contract)
    direction = -1 if side == 'put' else 1
    lo, hi = geometric_spread_band(contract)
    if direction * (idx - short) < 0:
        state = 'OUTSIDE'
    elif idx == short:
        state = 'SHORT_TOUCH'
    elif idx == long:
        state = 'PROTECTION_TOUCH'
    elif direction * (idx - long) > 0:
        state = 'BEYOND_PROTECTION'
    else:
        state = 'BETWEEN_LEGS'
    return {
        'state': state,
        'index_usd': float(idx),
        'zone': responsibility_zone(idx, contract),
        'geometric_interval_usd': (lo, hi),
        'in_geometric_band': lo < idx < hi,
        'on_adverse_side_of_short': direction * (idx - short) > 0,
        'strictly_outside_short': direction * (idx - short) < 0,
        'at_short_touch': idx == short,
        'at_protection_touch': idx == long,
        'beyond_protection': direction * (idx - long) > 0,
    }


def mechanical_semantics_spec(contract, invalidation='entry_not_returned', check_interval_ms=300_000):
    """Single authoritative semantic contract generated from condition_v1 + input_v1 rules."""
    side, short, long = _contract_legs(contract)
    if invalidation not in RULES:
        raise ValueError(f'unsupported invalidation rule: {invalidation!r}')
    if isinstance(check_interval_ms, bool) or not isinstance(check_interval_ms, int) or check_interval_ms < MINUTE or check_interval_ms % MINUTE:
        raise ValueError('check_interval_ms must be a positive minute-aligned integer')
    lo, hi = geometric_spread_band(contract)
    adverse_cmp = f'< {_fmt_usd(short)}' if side == 'put' else f'> {_fmt_usd(short)}'
    outside_cmp = f'> {_fmt_usd(short)}' if side == 'put' else f'< {_fmt_usd(short)}'
    direction = -1 if side == 'put' else 1
    step = min(100.0, short / 10.0)
    five_pos = {
        'outside': classify_price_state(short - direction * step, contract),
        'short_touch': classify_price_state(short, contract),
        'between_legs': classify_price_state((short + long) / 2.0, contract),
        'protection_touch': classify_price_state(long, contract),
        'beyond_protection': classify_price_state(long + direction * step, contract),
    }
    mins = check_interval_ms // MINUTE
    return {
        'contract_version': MECHANICAL_CONTRACT_VERSION,
        'side': side,
        'short_strike_usd': short,
        'long_strike_usd': long,
        'geometric_spread_band': {'low_usd': lo, 'high_usd': hi, 'note': '仅描述两腿之间赔付宽度，不用于限缩机械触发状态。'},
        'unilateral_adverse_side': {
            'condition': f'price {adverse_cmp}',
            'outside_condition': f'price {outside_cmp}',
            'includes_between_legs': True,
            'includes_protection_touch': True,
            'includes_beyond_protection': True,
            'short_touch_enters': False,
            'short_touch_returns_outside': False,
        },
        'five_position_reference': five_pos,
        'event_rules': {'invalidation': invalidation, 'meaning': RULES[invalidation]},
        'sampling': SAMPLING,
        'clock': {
            'interval_ms': check_interval_ms,
            'first_sample_rule': (
                f'首次检查为入场后下一UTC整分钟，之后每{mins}分钟（{check_interval_ms}ms）检查一次'
                f'（(entry_at_ms // 60_000 + 1) * 60_000 起按 interval_ms 步进，默认 300_000）'
            ),
        },
    }


def canonical_confirmation_text(contract, invalidation, check_interval_ms=300_000):
    """Deterministically render authoritative M confirmation wording bound to contract and rule."""
    spec = mechanical_semantics_spec(contract, invalidation=invalidation, check_interval_ms=check_interval_ms)
    side = spec['side']
    short_s = _fmt_usd(spec['short_strike_usd'])
    long_s = _fmt_usd(spec['long_strike_usd'])
    op_adv = '严格低于' if side == 'put' else '严格高于'
    op_out = '严格高于' if side == 'put' else '严格低于'
    beyond_dir = f'低于 {long_s}' if side == 'put' else f'高于 {long_s}'
    lo_s, hi_s = _fmt_usd(spec['geometric_spread_band']['low_usd']), _fmt_usd(spec['geometric_spread_band']['high_usd'])
    mins = check_interval_ms // MINUTE
    return (
        f'破坏确认口径：采用程序机械规则 {invalidation}（{RULES[invalidation]}）。'
        f'按卖出短腿 {short_s} USD 作单侧严格入区门槛：合格 UTC 一分钟采样{op_adv} {short_s} USD 即计为严格进入'
        f'（涵盖 {lo_s}–{hi_s} USD 两腿之间、恰好触及保护腿 {long_s} USD 以及{beyond_dir} USD 的保护腿外侧）；'
        f'恰好触及 {short_s} USD 短腿不算新进入也不算严格返回，必须{op_out} {short_s} USD 才算严格返回责任外。'
        f'首次检查为入场后下一UTC整分钟，之后每{mins}分钟（{check_interval_ms}ms）检查一次；缺分钟或时间错位判为 uncheckable，不补造路径。'
    )


def validate_confirmation_prose(text, contract, *, expected_invalidation=None, expected_interval_ms=None):
    """Deterministically detect contradictions between M confirmation prose and mechanical truth."""
    side, short, long = _contract_legs(contract)
    if not isinstance(text, str) or not text.strip():
        return {'valid': False, 'violations': ['confirmation_empty']}
    short_re = _strike_pattern(short)
    long_re = _strike_pattern(long)
    sep_re = r'\s*(?:至|到|[-–—～~])\s*'
    bracket_interval_re = rf'[\[\【]\s*(?:{long_re}\s*[,，]\s*{short_re}|{short_re}\s*[,，]\s*{long_re})\s*[\]\】]'
    interval_re = rf'(?:{long_re}{sep_re}{short_re}|{short_re}{sep_re}{long_re}|{bracket_interval_re})'
    neg_prefix = r'(?:严禁|禁止|不得|不能|不可|不要|不该|不采用|不使用|不选择|不按|不适用|并非|不是|非|绝非|不应|错误地将)'
    non_strict_neg = r'(?:严禁|禁止|不得|不能|不可|不要|不该|并非|不是|绝非|不应|不算|不计为?|不记为?|不视作?|不构成|不触发|未算|未计|未构成|既不算|也不算|均不算|都不算|既不构成|也不构成|均不构成|都不构成)'

    violations = []
    clauses = [c.strip() for c in re.split(r'[。；;\n]+', text) if c.strip()]

    prot_touch_re = re.compile(
        rf'(?:(?:保护腿\s*)?{long_re}|保护腿)[^，;；。\n（）()]{{0,18}}(?:边界)?[^，;；。\n（）()]{{0,18}}'
        rf'(?:均不算|都不算|不算|不计入|不计|不视作)[^，;；。\n（）()]*(?:严格)?(?:入区|进入|破坏|触发)'
    )
    beyond_cmp = r'(?:低于|小于|<|跌破|越过)' if side == 'put' else r'(?:高于|大于|>|突破|越过)'
    beyond_excl_re = re.compile(
        rf'(?:{beyond_cmp}\s*(?:保护腿\s*)?{long_re}|(?:越过|超过|超出|跌破|突破)\s*保护腿(?:外侧|之外|以外)?)'
        rf'[^，;；。\n（）()]*(?:均不算|都不算|不算|不计入|不计|不触发|不构成|不纳入)'
    )
    two_sided_ineq_re = (
        rf'(?:低于|小于|<)[^，。；\n]*{short_re}\s*(?:且|并|同时)\s*(?:高于|大于|>)[^，。；\n]*{long_re}'
        if side == 'put'
        else rf'(?:高于|大于|>)[^，。；\n]*{short_re}\s*(?:且|并|同时)\s*(?:低于|小于|<)[^，。；\n]*{long_re}'
    )
    finite_entry_re = re.compile(
        rf'(?<!涵盖)(?<!包括)(?<!包含)(?:进入|入|落入|跌入|涨入|介于|限于|局限(?:在|于))[^，。；\n（）()]{{0,18}}{interval_re}'
        rf'|{interval_re}[^，。；\n（）()]{{0,12}}(?:责任区间?|亏损区间?|内)\s*(?:才|即|方|始)?\s*(?:计为|算|触发|破坏|构成)'
        rf'|{two_sided_ineq_re}'
    )
    cmp_short = (
        r'(?:严格\s*)?(?:低于|小于|<|跌破|越过|穿过|下破|跌穿)'
        if side == 'put'
        else r'(?:严格\s*)?(?:高于|大于|>|突破|越过|穿过|上破|涨穿)'
    )
    # Strip parenthetical inclusive examples when verifying whether the core clause uses a unilateral short threshold.
    has_unilateral_short = False
    sub_split_re = re.compile(r'(?:[，;；（）()]|,(?!\d))+')
    for cl in clauses:
        for seg in (s.strip() for s in sub_split_re.split(cl) if s.strip()):
            if re.search(neg_prefix, seg):
                continue
            seg_core = re.sub(r'（(?:[^（）]*?(?:包括|涵盖|含|包含|例如|如)[^（）]*)）|\((?:[^()]*?(?:including|包括|涵盖|含|包含)[^()]*)?\)', '', seg)
            if re.search(rf'{cmp_short}\s*(?:Put|Call)?\s*(?:卖出)?(?:短腿\s*)?{short_re}', seg_core, re.IGNORECASE) and not re.search(
                rf'(?:并|且|而)\s*(?:进入|在|高于|大于|>|低于|小于|<)[^。；\n]*{long_re}', seg_core
            ):
                has_unilateral_short = True
                break
        if has_unilateral_short:
            break

    has_explicit_protection_inclusive = bool(re.search(
        rf'(?:含|包括|涵盖|覆盖|包含)[^。；\n]*(?:(?:触及|恰好|越过)\s*(?:或\s*(?:触及|越过)\s*)?(?:保护腿\s*)?(?:{long_re}|保护腿)|保护腿)[^。；\n]*(?:边界|外侧|以外|之外|及其外侧|{beyond_cmp}\s*(?:保护腿\s*)?{long_re}|更深)?'
        rf'|(?:触及|越过|恰好)(?:[/／]|或|(?:或)?(?:触及|越过))?\s*(?:保护腿\s*{long_re}?|{long_re}[^。；\n]*保护腿)[^。；\n]*(?:以及|及|与|、|/|／|或)[^。；\n]*(?:外侧|以外|之外|保护腿|{beyond_cmp})[^。；\n]*(?:计入|记为|计为|触发|算作|构成|进入|视为)',
        text,
    ))
    for cl in clauses:
        for seg in (s.strip() for s in sub_split_re.split(cl) if s.strip()):
            pos = cl.find(seg)
            prefix_ctx = cl[max(0, pos - 24):pos] if pos >= 0 else ''
            if re.search(rf'{neg_prefix}', seg) or re.search(rf'{neg_prefix}[^。；\n]{{0,24}}$', prefix_ctx):
                continue
            if prot_touch_re.search(seg) and not re.search(r'(?:并非不算|并非不计)', seg):
                violations.append('excludes_protection_touch')
                break
        if 'excludes_protection_touch' in violations:
            break

    for cl in clauses:
        for seg in (s.strip() for s in sub_split_re.split(cl) if s.strip()):
            pos = cl.find(seg)
            prefix_ctx = cl[max(0, pos - 20):pos] if pos >= 0 else ''
            if re.search(rf'{neg_prefix}', seg) or re.search(rf'{neg_prefix}[^。；\n]{{0,20}}$', prefix_ctx):
                continue
            if beyond_excl_re.search(seg) and not re.search(r'(?:并非不算|并非不计)', seg):
                violations.append('excludes_beyond_protection')
                break
        if 'excludes_beyond_protection' in violations:
            break

    has_two_sided_ineq = False
    has_finite_restriction = False
    bracket_entry_re = re.compile(
        rf'(?<!涵盖)(?<!包括)(?<!包含)(?:进入|入|落入|跌入|涨入|介于|限于|局限(?:在|于))[^，。；\n（）()]{{0,18}}{bracket_interval_re}'
        rf'|{bracket_interval_re}[^，。；\n（）()]{{0,12}}(?:责任区间?|亏损区间?|内)?\s*(?:才|即|方|始)?\s*(?:计为|算|触发|破坏|构成)'
    )
    for cl in clauses:
        if re.search(rf'{neg_prefix}[^。；\n]{{0,24}}{interval_re}', cl):
            continue
        if re.search(two_sided_ineq_re, cl) and not re.search(rf'{neg_prefix}', cl):
            has_two_sided_ineq = True
        if bracket_entry_re.search(cl) and not re.search(rf'{neg_prefix}', cl):
            has_finite_restriction = True
            break
        for seg in (s.strip() for s in sub_split_re.split(cl) if s.strip()):
            if re.match(r'^(?:包括|涵盖|含|包含)\b', seg):
                continue
            if finite_entry_re.search(seg):
                has_finite_restriction = True
                break
        if has_finite_restriction:
            break
    if (has_two_sided_ineq or (has_finite_restriction and not (has_unilateral_short and has_explicit_protection_inclusive))) and 'excludes_beyond_protection' not in violations:
        violations.append('restricts_entry_to_finite_interval')

    # Non-strict return / entry boundaries (short-strike touch is neither entry nor return).
    if side == 'call':
        non_strict_ret_dir_re = re.compile(
            rf'(?:回到|回落(?:到|至)?|返回(?:到|至)?|跌回(?:到|至)?|跌至|降至|收(?:于|在))[^，。；\n]{{0,18}}(?:(?:Call)?\s*(?:卖出)?短腿\s*)?(?:{short_re}|短腿)[^，。；\n]{{0,8}}(?:及以下|或以下|以下含|含该位)'
            rf'|(?:{short_re}|短腿)[^，。；\n]{{0,8}}(?:及以下|或以下)[^，。；\n]{{0,14}}(?:返回|回到|脱离|解除|责任外)'
            rf'|(?:<=|≤|小于等于|低于或等于|不超过)\s*(?:(?:Call)?\s*(?:卖出)?短腿\s*)?(?:{short_re}|短腿)[^，。；\n]{{0,18}}(?:返回|回到|脱离|责任外)'
            rf'|(?:返回|回到|脱离|责任外)[^，。；\n]{{0,18}}(?:<=|≤|小于等于|低于或等于|不超过)\s*(?:(?:Call)?\s*(?:卖出)?短腿\s*)?(?:{short_re}|短腿)',
            re.IGNORECASE,
        )
        non_strict_ent_dir_re = re.compile(
            rf'(?:>=|≥|大于等于|高于或等于|达到或高于|触及或高于)\s*(?:(?:Call)?\s*(?:卖出)?短腿\s*)?(?:{short_re}|短腿)'
            rf'|(?:突破|涨至|升至|触及|高于|到达)?\s*(?:(?:Call)?\s*(?:卖出)?短腿\s*)?(?:{short_re}|短腿)[^，。；\n]{{0,8}}(?:及以上|或以上)[^，。；\n]{{0,14}}(?:进入|入区|触发|破坏|计为|记为|算)'
            rf'|(?:进入|入区|触发|破坏)[^，。；\n]{{0,16}}(?:(?:Call)?\s*(?:卖出)?短腿\s*)?(?:{short_re}|短腿)[^，。；\n]{{0,8}}(?:及以上|或以上)',
            re.IGNORECASE,
        )
    else:
        non_strict_ret_dir_re = re.compile(
            rf'(?:站上|站回|回到|回升(?:到|至)?|返回(?:到|至)?|涨回(?:到|至)?|升至|收(?:于|在))[^，。；\n]{{0,18}}(?:(?:Put)?\s*(?:卖出)?短腿\s*)?(?:{short_re}|短腿)[^，。；\n]{{0,8}}(?:及以上|或以上|以上含|含该位)'
            rf'|(?:{short_re}|短腿)[^，。；\n]{{0,8}}(?:及以上|或以上)[^，。；\n]{{0,14}}(?:返回|回到|脱离|解除|责任外)'
            rf'|(?:>=|≥|大于等于|高于或等于|不低于)\s*(?:(?:Put)?\s*(?:卖出)?短腿\s*)?(?:{short_re}|短腿)[^，。；\n]{{0,18}}(?:返回|回到|脱离|责任外)'
            rf'|(?:返回|回到|脱离|责任外)[^，。；\n]{{0,18}}(?:>=|≥|大于等于|高于或等于|不低于)\s*(?:(?:Put)?\s*(?:卖出)?短腿\s*)?(?:{short_re}|短腿)',
            re.IGNORECASE,
        )
        non_strict_ent_dir_re = re.compile(
            rf'(?:<=|≤|小于等于|低于或等于|达到或低于|触及或低于)\s*(?:(?:Put)?\s*(?:卖出)?短腿\s*)?(?:{short_re}|短腿)'
            rf'|(?:跌破|跌至|降至|触及|低于|到达)?\s*(?:(?:Put)?\s*(?:卖出)?短腿\s*)?(?:{short_re}|短腿)[^，。；\n]{{0,8}}(?:及以下|或以下)[^，。；\n]{{0,14}}(?:进入|入区|触发|破坏|计为|记为|算)'
            rf'|(?:进入|入区|触发|破坏)[^，。；\n]{{0,16}}(?:(?:Put)?\s*(?:卖出)?短腿\s*)?(?:{short_re}|短腿)[^，。；\n]{{0,8}}(?:及以下|或以下)',
            re.IGNORECASE,
        )

    touch_ret_re = re.compile(
        rf'(?:触及|碰到|到达|恰好(?:触及|等于|到达)?)\s*(?:(?:Put|Call)?\s*(?:卖出)?短腿(?:\s*{short_re})?|{short_re}(?:\s*USD)?(?:\s*短腿)?|该位|边界)?[^，。；\n]{{0,12}}(?:即|就|便|也|可|方)?(?:算作?|计为|记为|视为|视作|构成|即)\s*(?:严格)?(?:返回|回到责任外)',
        re.IGNORECASE,
    )
    touch_ent_re = re.compile(
        rf'(?:触及|碰到|到达|恰好(?:触及|等于|到达)?)\s*(?:(?:Put|Call)?\s*(?:卖出)?短腿(?:\s*{short_re})?|{short_re}(?:\s*USD)?(?:\s*短腿)?|该位|边界)?[^，。；\n]{{0,12}}(?:即|就|便|也|可|方)?(?:算作?|计为|记为|视为|视作|构成|即)\s*(?:严格)?(?:新进入|进入|入区|触发|破坏)',
        re.IGNORECASE,
    )

    for cl in clauses:
        for seg in (s.strip() for s in sub_split_re.split(cl) if s.strip()):
            pos = cl.find(seg)
            prefix_ctx = cl[max(0, pos - 24):pos] if pos >= 0 else ''
            if re.search(non_strict_neg, seg) or re.search(rf'{neg_prefix}[^。；\n]{{0,24}}$', prefix_ctx):
                continue
            if 'non_strict_return_boundary' not in violations and (non_strict_ret_dir_re.search(seg) or touch_ret_re.search(seg)):
                violations.append('non_strict_return_boundary')
            if 'non_strict_entry_boundary' not in violations and not re.search(rf'保护腿|{long_re}', seg):
                if non_strict_ent_dir_re.search(seg) or touch_ent_re.search(seg):
                    violations.append('non_strict_entry_boundary')

    if expected_invalidation in RULES:
        other = 'entry_not_returned' if expected_invalidation == 'new_entry' else 'new_entry'
        rule_pos_re = re.compile(rf'(?<![不非勿未])(?:采用|选择|规则为?|按|使用|适用)[^，。；\n]{{0,12}}\b{other}\b')
        for cl in clauses:
            for seg in (s.strip() for s in sub_split_re.split(cl) if s.strip()):
                if re.search(rf'{neg_prefix}|两选一|不同于|而非\s*{other}', seg):
                    continue
                if rule_pos_re.search(seg):
                    violations.append('invalidation_rule_mismatch')
                    break
            if 'invalidation_rule_mismatch' in violations:
                break
            if expected_invalidation == 'new_entry' and (
                re.search(r'(?:且|并)?(?:至|截至)?(?:固定)?检查点(?:仍)?(?:未|没有)(?:后续)?(?:严格)?返回(?:责任外)?[^，。；\n]{0,6}(?:才|方|始)?(?:触发|破坏|计为触发|记为触发)', cl)
                or re.search(r'(?:若|如果)[^，。；\n]{0,12}(?:检查点前?)?(?:已|已经)(?:严格)?返回(?:责任外)?[^，。；\n]{0,8}(?:则)?(?:不触发|不算触发|不计为触发|不构成破坏)', cl)
            ):
                violations.append('invalidation_rule_mismatch')
                break
            if expected_invalidation == 'entry_not_returned' and (
                re.search(r'即使(?:随后|检查点前)?(?:已|已经)?返回[^。；\n]{0,16}(?:仍|依然|依旧|均)?(?:记为|计为|算作?|视为)?触发', cl)
                or re.search(r'(?:无论|不管)(?:随后)?是否(?:已)?返回[^。；\n]{0,12}(?:均|都|仍)?(?:记为|计为|算作?|视为)?触发', cl)
            ):
                violations.append('invalidation_rule_mismatch')
                break

    clock_neg = r'(?:严禁|禁止|不得|不要|不写|不作|不按|不以|不采用|不使用|不可|避免|区别于|并非|不是|非|绝非|而非|而不是|勿)'
    invented_clock_re = re.compile(
        r'不设(?:额外)?检查时钟|无需(?:额外)?检查时钟|没有(?:固定)?检查时钟'
        r'|(?:[:：]00\s*[/、,，-]\s*[:：]05|00分\s*[/、,，-]\s*05分)'
        r'|自然\s*5\s*分钟整点|固定整\s*5\s*分钟(?:自然)?网格|整点\s*5\s*分钟网格'
    )
    for cl in clauses:
        for seg in (s.strip() for s in sub_split_re.split(cl) if s.strip()):
            pos = cl.find(seg)
            seg_ctx = cl[max(0, pos - 36):pos] if pos >= 0 else ''
            for m in invented_clock_re.finditer(seg):
                local_ctx = seg[:m.start()]
                if re.search(clock_neg, local_ctx) or re.search(rf'{clock_neg}[^。；\n]{{0,36}}$', seg_ctx):
                    continue
                violations.append('clock_interval_mismatch')
                break
            if 'clock_interval_mismatch' in violations:
                break
        if 'clock_interval_mismatch' in violations:
            break

    if expected_interval_ms is not None and 'clock_interval_mismatch' not in violations:
        m_ms = re.findall(r'check_interval_ms\s*(?:=|为|:|：)\s*(\d+)', text)
        if m_ms and any(int(val) != expected_interval_ms for val in m_ms):
            violations.append('clock_interval_mismatch')
        if 'clock_interval_mismatch' not in violations:
            sampling_ctx_re = r'(?:采样|指数|btc_usd|K线|记录|数据|行情)'
            for cl in clauses:
                for m in re.finditer(r'(?:检查|步进|间隔|每\s*\d+\s*分钟)[^。；\n]{0,12}[（(]\s*(\d+)\s*ms\s*[)）]', cl, re.IGNORECASE):
                    prefix_ctx = cl[max(0, m.start() - 36):m.start()]
                    if re.search(clock_neg, prefix_ctx):
                        continue
                    if int(m.group(1)) != expected_interval_ms:
                        violations.append('clock_interval_mismatch')
                        break
                if 'clock_interval_mismatch' in violations:
                    break
                for seg in (s.strip() for s in sub_split_re.split(cl) if s.strip()):
                    pos = cl.find(seg)
                    prefix_ctx = cl[max(0, pos - 36):pos] if pos >= 0 else ''
                    if re.search(clock_neg, seg) or re.search(rf'{clock_neg}[^。；\n]{{0,36}}$', prefix_ctx):
                        continue
                    if expected_interval_ms != 3_600_000 and re.search(r'每小时|每\s*60\s*分钟|60\s*分钟\s*(?:检查|复核|间隔|时钟|一次|步进)', seg):
                        violations.append('clock_interval_mismatch')
                        break
                    if expected_interval_ms != MINUTE:
                        for zm in re.finditer(r'逐分钟', seg):
                            near = seg[max(0, zm.start() - 12):zm.end() + 12]
                            if not re.search(sampling_ctx_re, near, re.IGNORECASE):
                                violations.append('clock_interval_mismatch')
                                break
                    if 'clock_interval_mismatch' in violations:
                        break
                    for m in re.finditer(r'(?:每|按)\s*(\d+)\s*分钟', seg, re.IGNORECASE):
                        n_min = int(m.group(1))
                        near = seg[max(0, m.start() - 12):m.end() + 14]
                        if n_min == 1 and re.search(sampling_ctx_re, near, re.IGNORECASE) and not re.search(r'(?:检查|判定|复核|时钟|触发)', near):
                            continue
                        if n_min * MINUTE != expected_interval_ms:
                            violations.append('clock_interval_mismatch')
                            break
                    if 'clock_interval_mismatch' in violations:
                        break
                    for m in re.finditer(r'(?<!\d)(\d+)\s*分钟\s*(?:检查|复核|间隔|时钟|网格|一次|步进)', seg):
                        n_min = int(m.group(1))
                        near = seg[max(0, m.start() - 12):m.end() + 12]
                        if n_min == 1 and re.search(sampling_ctx_re, near, re.IGNORECASE) and not re.search(r'(?:检查|判定|复核|时钟|触发)', near):
                            continue
                        if n_min * MINUTE != expected_interval_ms:
                            violations.append('clock_interval_mismatch')
                            break
                    if 'clock_interval_mismatch' in violations:
                        break
                if 'clock_interval_mismatch' in violations:
                    break

    return {'valid': not violations, 'violations': list(dict.fromkeys(violations))}
