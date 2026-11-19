"""Render a complete player hand from native cases and level cards; no model or orders.

CLI reads the workbench/archive's existing JSON (or gzip) and level JSONL.
Known lifetime events prevent issuance. Missing auxiliary data do not select an action.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

try:
    from .astra_structural_board_v1 import entry, number, usable
    from .astra_level_behaviour_v1 import levels_for
    from . import astra_anchor_relative_v1 as ard
except ImportError:
    from astra_structural_board_v1 import entry, number, usable
    from astra_level_behaviour_v1 import levels_for
    import astra_anchor_relative_v1 as ard

HOUR = 3_600_000
MINUTE = 60_000
DAY = 24 * HOUR
_CHART_DELAY_SEMANTICS = {'minute_open', 'unverified_minute_label'}
_CHART_DIRECT_SEMANTICS = {'instant', 'minute_close'}
_SPOT_END_CLOCK_KEYS = ('end_ts_ms', 'complete_ts_ms', 'window_end_ms', 'end_ms')
ARD_READING = ('\n\nARD责任判读：责任边界→入区前路径→短腿穿越与回收→ARD→量价反证→两侧择优→条件冻结。'
               '读取可得的贴近、离返、迁移事实，结合Pin相对两侧短腿的位置、当前余量与剩余期限，'
               '分别说明它支持、削弱或限制哪项承担条件；若关系延续或破坏，哪侧责任更不利。'
               '这是交割前条件风险取舍，不能以“不预测涨跌”回避择优，也不能只复述ARD。'
               'Pin同短腿不多一层缓冲；停留率不是吸附概率，共同变化不证明Pin驱动价格。'
               '它与穿越、原空间评分可能同源，不重复计票；与同窗量价有冲突须明说，不能跳过量价反证。'
               '没有独立增量可写“未增加可辨识的责任信息”；缺可靠ARD沿一阶路径正常判断，不自动Reject或Unknown。'
               '不新增Accept/Reject/Revoke门槛。若Accept实际依赖ARD，冻结同expiry的Pin身份/位置、原事实及窗口/半宽口径、'
               '责任相关性与具体复核变化；有效破坏另按原检查钟冻结。续判区分旧事实与新增，不换锚维护原理由；'
               'ARD变化只要求复核，满足已冻结破坏标准才构成Revoke依据。')


class HandNotDealt(ValueError):
    """An observed external event falls within the hand's remaining life."""


def fmt(value):
    n = number(value)
    return f"{n:,.1f}".removesuffix(".0") if n is not None else "未提供"


def stamp(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def side_of(index, level):
    return "上方" if index > level else "下方" if index < level else "该位"


def distance(value, scale):
    return f"{fmt(value)} USD" + (f"（约{value / scale:.2f}倍尺度）" if scale else "")


def responsibility_zone(index, contract):
    short, long = contract['short_strike_usd'], contract['long_strike_usd']
    direction = -1 if contract['side'] == 'put' else 1
    if index == short:
        return '正处短腿边界'
    if index == long:
        return '正处保护腿边界'
    if direction * (index - short) < 0:
        return '在责任区外'
    return '已越过保护腿' if direction * (index - long) > 0 else '在责任区内（短腿与保护腿之间）'


def values(case, ref):
    return entry(case, ref).get("values") or {}


def bind(case, card, side):
    c = values(case, "L1.contract")
    cutoff = (case.get("build") or {}).get("built_at_ms")
    if case.get("schema") != "case_file@0.1" or card.get("schema") != "level_card@0.1":
        raise ValueError("native case_file and level_card required")
    if (not case.get("case_id") or card.get("case_id") != case["case_id"]
            or card.get("read_at_ms") != cutoff or card.get("side") != side or c.get("side") != side
            or card.get("expiry_ms") != c.get("expiry_ms")
            or card.get("contract_key") != f"{c.get('short_instrument')}|{c.get('long_instrument')}"):
        raise ValueError("case/card identity, legs, side, expiry or reading mismatch")
    short, long, index = (number(c.get("short_strike_usd")), number(c.get("long_strike_usd")),
                          number(card.get("index_usd")))
    if (any(x is None or x <= 0 for x in (short, long, index, number(c.get("quantity_btc"))))
            or not isinstance(cutoff, int) or c.get("expiry_ms", 0) <= cutoff
            or (short <= long if side == "put" else short >= long)
            or c.get("underlying") != "BTC" or c.get("settlement_currency") != "BTC"
            or c.get("venue") != "Deribit"):
        raise ValueError("invalid responsibility or index")
    if not usable(entry(case, "L1.contract"), cutoff, 5 * 60_000):
        raise ValueError("contract is not usable at the reading")
    return c


def check_events(case, cutoff, expiry, additional):
    event_values = values(case, "L7.events")
    batches = [event_values] if isinstance(event_values, dict) else []
    if additional:
        batches.append(additional)
    advisories = []
    for batch in batches:
        # Existing event_calendar contract_events format, also usable by an operator
        # supplying a known external interference. A future observation is not evidence.
        known = number(batch.get("from_ms"))
        if known is None or known > cutoff:
            continue
        for event in batch.get("events") or []:
            at = number(event.get("at_ms"))
            if at is not None and cutoff <= at <= expiry:
                if event.get("blocking") is False:
                    label = event.get("name_cn") or event.get("kind") or "外源日程"
                    advisories.append(f"{label}（{stamp(at)}）")
                    continue
                raise HandNotDealt(f"不发牌：已知外源事件位于剩余寿命内（{stamp(at)}）。")
    return advisories


def expiry_scale(case, cutoff, expiry):
    row = entry(case, "L3.expiry_walls")
    wall = row.get("values") or {}
    move, iv = number(wall.get("expected_move_usd")), number(wall.get("atm_iv_pct"))
    return (move if usable(row, cutoff, 5 * 60_000) and wall.get("expiry_ms") == expiry
            and not wall.get("stale") and move is not None and move > 0
            and iv is not None and iv > 0 else None)


def role_text(roles):
    labels = []
    oi = [side.title() for side in ('put', 'call') if any(r.startswith('oi_' + side + '#') for r in roles)]
    if oi:
        labels.append('/'.join(oi) + ' OI集中位')
    if any(r.startswith('gamma_') for r in roles):
        labels.append('Gamma派生位')
    if "pin" in roles:
        labels.append("上述Pin")
    return "、".join(dict.fromkeys(labels))


def path_text(path, index, k, cutoff, scale):
    now = f"读取时在{side_of(index, k)}。"
    if not isinstance(path, dict) or number(path.get("crossings")) is None:
        return "该站路径未提供；" + now
    crossings = path["crossings"]
    if crossings == 0:
        closest = number(path.get("closest_usd"))
        if closest is None:
            return "未见穿过，最近距离未提供；" + now
        # A sampled touch without a side change must not be called 'not touched'.
        if closest == 0:
            return "到达过该位，未穿过；" + now
        return "没碰到；最接近时仍隔" + distance(closest, scale) + "。" + now
    text = "反复穿过。" if crossings >= 5 else "穿过数次。" if crossings > 1 else "穿过一次。"
    first, beyond = number(path.get("first_cross_ms")), number(path.get("beyond_60m_usd"))
    direction = path.get("first_cross_dir")
    if first is not None and first > cutoff:
        raise ValueError("future crossing in frozen path")
    if first is not None and beyond is not None and direction in ("up", "down"):
        horizon = "1小时" if cutoff - first >= HOUR else "截至读取的不足1小时"
        target = "上方" if direction == "up" else "下方"
        origin = "下方" if direction == "up" else "上方"
        text += f"首次{'上' if direction == 'up' else '下'}穿后{horizon}最深到其{target}{fmt(beyond)} USD"
        returned = path.get("back_within_60m")
        if returned is True:
            text += f"，该段内回到{origin}"
        elif returned is False:
            text += f"，该段内未回到{origin}"
        text += "。"
    return text + now


def flow_text(level):
    for window, hours in (("6h", 6), ("24h", 24)):
        flow = (level.get("flow") or {}).get(window) or {}
        nums = [number(flow.get(k)) for k in ("c_buy", "c_sell", "p_buy", "p_sell")]
        if flow.get("gap") is not False or flow.get("truncated") is not False or any(n is None or n < 0 for n in nums):
            continue
        parts = []
        for kind, buy, sell in (("Call", *nums[:2]), ("Put", *nums[2:])):
            if buy == sell == 0:
                continue
            net = buy - sell
            parts.append(f"普通{kind}净主动{'买' if net > 0 else '卖' if net < 0 else '持平'}"
                         + (f"{fmt(abs(net))} BTC" if net else ""))
        return f"近{hours}小时" + "、".join(parts) + "；仅与本站路径并读。" if parts else ""
    return ""


def major_positions(case, card, contract):
    cutoff = card['read_at_ms']
    row = entry(case, "L3.expiry_walls")
    wall = row.get("values") or {}
    if (not usable(row, cutoff, 5 * 60_000) or wall.get("expiry_ms") != contract["expiry_ms"]
            or wall.get("stale")):
        raise ValueError("current expiry structure required to enumerate the entry path")
    return levels_for(wall, None, None)


def pin_relation(pin, index, contract):
    """One expiry Pin relative to this reading and its liability; never a vote."""
    short, long = contract['short_strike_usd'], contract['long_strike_usd']
    direction = -1 if contract['side'] == 'put' else 1
    spot = '与现价同位' if pin == index else f'在现价{side_of(pin, index)}{fmt(abs(pin-index))} USD'
    if pin == short:
        boundary = f'与短腿{fmt(short)}同位，不另算一站'
    elif pin == long:
        boundary = f'与保护腿{fmt(long)}同位，不是入区前缓冲'
    elif min(short, long) < pin < max(short, long):
        boundary = f'在责任区内，距短腿{fmt(abs(pin-short))} USD，不是入区前缓冲'
    elif direction * (pin-long) > 0:
        boundary = f'在保护腿之外，距短腿{fmt(abs(pin-short))} USD，不是入区前缓冲'
    else:
        on_path = direction*(index-short)<0 and min(index,short)<pin<max(index,short)
        boundary = (f'在短腿的责任外侧{fmt(abs(pin-short))} USD；'
                    + ('位于入区前路径' if on_path else '不在当前入区前路径'))
    return spot + '，' + boundary


def pin_context(cases, cards, contracts):
    # Read the actual expiry profile, never gamma_regime.pin_strike or the all-tenor magnet.
    rows = {s: entry(cases[s], 'L3.expiry_walls') for s in cases}
    pins = {s: number(((r.get('values') or {}).get('walls') or {}).get('pin')) for s,r in rows.items()}
    valid = {s: p for s,p in pins.items() if p is not None and p > 0}
    if not valid:
        return ''
    same = len(valid)==2 and len(set(valid.values()))==1
    if same:
        clocks = {rows[s].get('asof_ms') for s in valid}
        source = ('结构源' + stamp(next(iter(clocks))) if len(clocks)==1 else
                  '；'.join(s.title()+'结构源'+stamp(rows[s]['asof_ms']) for s in valid))
        text = f"**本期Pin** {fmt(next(iter(valid.values())))}（{source}）。"
        return text + ' '.join(s.title()+'：'+pin_relation(p,cards[s]['index_usd'],contracts[s])+'。' for s,p in valid.items())
    # Serial readings can straddle a profile update: retain each source instead of choosing Put's Pin.
    return ' '.join(f"{s.title()}读取的**本期Pin** {fmt(p)}（结构源{stamp(rows[s]['asof_ms'])}）："
                    + pin_relation(p,cards[s]['index_usd'],contracts[s])+'。' for s,p in valid.items())


def stations(case, card, contract):
    index, short, cutoff = card["index_usd"], contract["short_strike_usd"], card["read_at_ms"]
    major = major_positions(case, card, contract)
    outside = index > short if contract["side"] == "put" else index < short
    if not outside:
        return {}, []  # once inside, the path back to short is not an entry buffer
    lo, hi = sorted((index, short))
    roles = {k: v for k, v in major.items() if lo < k < hi}
    zones = []
    map_row = entry(case, "L3.map.references")
    if map_row.get("quality") == "ok":
        for z in (map_row.get("values") or {}).get("reference_pool") or []:
            if (z.get("role") != "KPF_BASIN" or z.get("quality") != "OK"
                    or z.get("coordinate_qualification") != "COMPARABLE"
                    or (z.get("usage_decision") or {}).get("can_use") is not True):
                continue
            a, b, at = number(z.get("raw_low")), number(z.get("raw_high")), number(z.get("observation_end_ms"))
            if (a is not None and b is not None and a <= b and at is not None and at <= cutoff
                    and max(a, lo) < min(b, hi)):
                zones.append((max(a, lo), min(b, hi)))
    return roles, sorted(set(zones), reverse=contract["side"] == "put")


def _chart_semantics(index_path):
    if not isinstance(index_path, Mapping):
        return 'minute_close'
    sem = index_path.get('timestamp_semantics')
    if sem is not None:
        if sem not in _CHART_DELAY_SEMANTICS and sem not in _CHART_DIRECT_SEMANTICS:
            raise ValueError(f'unsupported timestamp_semantics: {sem}')
        return sem
    if index_path.get('schema') == 'index_path@0.1' or index_path.get('fetched_at_ms') is not None:
        return 'unverified_minute_label'
    return 'minute_close'


def chart_points_audit(index_path, expiry, cutoff):
    """Separate eligible chart points from unverified boundary minute buckets at cutoff."""
    if not index_path or index_path.get('expiry_ms') != expiry or index_path.get('index_name') != 'btc_usd':
        return {
            'semantics': None,
            'fetched_at_ms': None,
            'eligible': [],
            'excluded_boundary': [],
            'latest_eligible': None,
        }
    semantics = _chart_semantics(index_path)
    delay = MINUTE if semantics in _CHART_DELAY_SEMANTICS else 0
    eligible = []
    excluded_boundary = []
    for t, p in index_path.get('points', []):
        if number(t) is None or number(p) is None or p <= 0:
            continue
        if not (cutoff - DAY < t <= cutoff):
            continue
        if t + delay <= cutoff:
            eligible.append((t, p))
        else:
            excluded_boundary.append((t, p))
    eligible.sort(key=lambda x: x[0])
    excluded_boundary.sort(key=lambda x: x[0])
    return {
        'semantics': semantics,
        'fetched_at_ms': index_path.get('fetched_at_ms') if type(index_path.get('fetched_at_ms')) is int else None,
        'eligible': eligible,
        'excluded_boundary': excluded_boundary,
        'latest_eligible': eligible[-1] if eligible else None,
    }


def extract_l2_index_observations(cases, cards):
    """Extract independent, clock-verified Deribit L2 orderbook index observations per side."""
    observations = {}
    for side, case in cases.items():
        card = cards[side]
        read_at = card.get('read_at_ms')
        short_vals = values(case, 'L2.book.short')
        long_vals = values(case, 'L2.book.long')
        quote_vals = values(case, 'L2.quote')
        price = number(short_vals.get('index_price'))
        obs_ms = short_vals.get('timestamp')
        acq_ms = quote_vals.get('received_ts_ms')
        if (price is None or price <= 0 or type(obs_ms) is not int or type(acq_ms) is not int
                or type(read_at) is not int):
            continue
        long_price = number(long_vals.get('index_price'))
        long_ts = long_vals.get('timestamp')
        if long_price is not None and abs(long_price - price) > 1e-6:
            continue
        latest_obs = max(obs_ms, long_ts) if type(long_ts) is int else obs_ms
        if not (latest_obs <= acq_ms <= read_at):
            continue
        observations[side] = {
            'side': side,
            'price_usd': price,
            'observed_ms': obs_ms,
            'acquired_ms': acq_ms,
            'read_at_ms': read_at,
            'source': 'Deribit L2.book.short.values.index_price',
        }
    return observations


def window_points(index_path, expiry, cutoff):
    return chart_points_audit(index_path, expiry, cutoff)['eligible']


def continuous(points, start, end):
    # The existing btc_usd chart is sampled every minute; missing minutes are gaps,
    # not evidence that an entry, recovery or closer approach did not happen.
    return (len(points)>=2 and 0<=points[0][0]-start<=60_000 and 0<=end-points[-1][0]<=60_000
            and all(0 < b[0]-a[0] <= 60_000 for a,b in zip(points,points[1:])))


def responsibility_trace(points, side, short):
    """Strict inside episodes; a touch neither enters nor returns outside.

    Caller supplies a continuous, time-cut series. An episode already open at the
    left edge has no known entry time. No full-window extreme stands in for it.
    """
    direction=-1 if side=='put' else 1
    episodes=[];active=None;previous=None;touches=[]
    for t,p in points:
        depth=direction*(p-short)
        state=1 if depth>0 else -1 if depth<0 else 0
        if state==0:
            touches.append(t)
        if state==1:
            if active is None:
                active={'entered_ms':t if previous is not None and previous<=0 else None,
                        'observed_from_ms':t,'peak_ms':t,'peak_price':p,'depth_usd':depth,'returned_ms':None}
            if depth>=active['depth_usd']:
                active.update(peak_ms=t,peak_price=p,depth_usd=depth)
        elif state==-1 and active is not None:
            active['returned_ms']=t;episodes.append(active);active=None
        if state or previous is None:
            previous=state
    if active is not None:
        episodes.append(active)
    return {'episodes':episodes,'touches':touches}


def short_path_text(level, points, contract, index, cutoff, scale, *, report_protection=False, chart_end_ms=None):
    """Responsibility-relative entry/recovery, or a timed closest approach."""
    k = contract['short_strike_usd']
    direction = -1 if contract['side'] == 'put' else 1
    now = f"读取时指数{responsibility_zone(index, contract)}、在短腿{side_of(index, k)}。"
    if not points:
        # The native card has a first crossing, not a last entry/return or full extrema.
        path = level.get('before_24h')
        if not isinstance(path, dict):
            return '短腿历史覆盖不足，无法确认此前进入或返回。' + now
        crossings, closest = number(path.get('crossings')), number(path.get('closest_usd'))
        entry_dir = 'down' if direction == -1 else 'up'
        proven_inside = (crossings is not None and crossings > 0 and
                         ((closest is not None and closest > 0) or
                          (path.get('first_cross_dir') == entry_dir and (number(path.get('beyond_60m_usd')) or 0) > 0)))
        if proven_inside:
            summary = '原生24小时卡记录窗口曾在责任内；' + ('读取时已回到责任外。' if direction*(index-k)<0 else '读取时未回到责任外。')
        elif crossings == 0:
            summary = '原生24小时卡未见进入责任区。' if direction*(index-k)<0 else '原生24小时卡未记录穿越，不能据此说未进入。'
        else:
            summary = '原生卡记录边界穿越；未附采样序列，不将触边直接记为深入责任。'
        first=number(path.get('first_cross_ms'))
        if first is not None and first>cutoff:
            raise ValueError('future crossing in frozen path')
        if first is not None and closest is not None and closest>0 and path.get('first_cross_dir') in ('up','down'):
            event='进入责任区' if path.get('first_cross_dir')==entry_dir else '返回责任外'
            summary+=f'原生卡首次有记录的穿越为{event}，采样时间{stamp(first)}。'
            depth=number(path.get('beyond_60m_usd'))
            if event=='进入责任区' and depth is not None and depth>0:
                summary+=f'其后至多1小时内记录最大入区深度{fmt(depth)} USD；不代表完整入区过程。'
        if crossings==0 and closest is not None:
            summary+=f'原生卡最接近距离{fmt(closest)} USD，时刻未提供，无法比较其后余量变化。'
        return summary+now+'未附指数序列，不补写最近进入/返回时刻或完整入区深度。'
    end_ms = cutoff if chart_end_ms is None else chart_end_ms
    complete = continuous(points, cutoff - DAY, end_ms)
    inside = [(t, p) for t, p in points if direction * (p - k) > 0]
    protection = contract['long_strike_usd']
    extreme = max(inside, key=lambda x: direction * x[1], default=None) if report_protection else None
    if not complete:
        text=('已覆盖采样曾在责任内。' if inside else '已覆盖片段未见进入，不能据此认定全窗从未进入。')
        if extreme and direction * (extreme[1] - protection) >= 0:
            text += f'已覆盖采样{stamp(extreme[0])}指数{fmt(extreme[1])}，{responsibility_zone(extreme[1], contract)}。'
        return (text+f'24小时覆盖不足：可见采样{stamp(points[0][0])}至{stamp(points[-1][0])}；'
                +'无法可靠比较最近接近后的余量，或确认全窗最近进入/返回与完整入区过程。'+now)
    trace=responsibility_trace(points,contract['side'],k)
    episodes=trace['episodes']
    if not episodes:
        # Latest sample wins a tied minimum: do not hide a recent retest behind an old one.
        at,price=min(points,key=lambda x:(abs(x[1]-k),-x[0]))
        near=abs(price-k);current=abs(index-k);change=current-near
        text='本窗口采样未见进入责任区。'
        if trace['touches']:
            text+='曾恰好触及短腿，未记为进入。'
        text+=f'窗口最接近采样为{stamp(at)}，指数{fmt(price)}，距短腿{fmt(near)} USD。'
        if index!=points[-1][1]:
            text+='读取指数与路径末点不一致，无法比较其后余量。'
        else:
            movement='扩大' if change>0 else '收缩' if change<0 else '不变'
            text+=f'至读取距离{fmt(current)} USD，较该采样{movement}'
            text+=(f'{fmt(abs(change))} USD。' if change else '。')
            text+='这是端点比较，不表示此后单调远离或压力解除。'
        return text+now
    text='窗口采样曾在短腿责任内'
    text+= '，其后有回到责任外的记录。' if any(e['returned_ms'] for e in episodes) else '，未见返回责任外。'
    last=episodes[-1]
    if episodes[0]['entered_ms'] is None:
        first_t,first_p=points[0]
        text+=f'窗口首个采样{stamp(first_t)}指数{fmt(first_p)}，{responsibility_zone(first_p,contract)}，该段进入时刻未覆盖。'
    if last['entered_ms'] is not None:
        text+=f"最近一次进入的采样确认时间{stamp(last['entered_ms'])}。"
    returns=[e['returned_ms'] for e in episodes if e['returned_ms'] is not None]
    if returns:
        text+=f'最近一次返回责任外的采样确认时间{stamp(returns[-1])}'
        text+= '（早于最近一次再进入）。' if last['entered_ms'] and returns[-1]<last['entered_ms'] else '。'
    scope='该次进入后' if last['entered_ms'] is not None else '已覆盖的责任内片段'
    text+=f"{scope}最深采样{stamp(last['peak_ms'])}，指数{fmt(last['peak_price'])}，深入责任{fmt(last['depth_usd'])} USD；"
    text+= '该次已返回责任外。' if last['returned_ms'] is not None else '截至路径末点未见返回责任外。'
    width=abs(k-contract['long_strike_usd'])
    if (any(e['depth_usd']>width for e in episodes[:-1]) and last['depth_usd']<=width
            and direction*(points[0][1]-k)<=width):
        text+='窗口较早片段另有保护腿外采样，不能混作最近一次进入的深度。'
    elif last['depth_usd']>width:
        text+='该段有越过保护腿的采样。'
    elif extreme and extreme[1] == protection and points[0][1] != protection:
        text += f'窗口采样{stamp(extreme[0])}指数{fmt(protection)}，曾恰好触及保护腿。'
    if index!=points[-1][1]:
        text+='路径末点与读取指数不同，上述时序仅截至'+stamp(points[-1][0])+'；末点至读取过程未覆盖。'
    return text+now


def path_section(case, card, contract, scale, roles, zones, index_path, *, report_protection=False, boundary_replaced=None):
    index, cutoff, side = card["index_usd"], card["read_at_ms"], contract["side"]
    rows = {r["k"]: r for r in card.get("levels") or []}
    intro = "读取前24小时的已发生路径"
    if contract["expiry_ms"] - cutoff < DAY:
        intro += "，长于剩余寿命，不是交割前预测"
    lines = [intro + "。"]
    audit = chart_points_audit(index_path, contract['expiry_ms'], cutoff)
    points = audit['eligible']
    delay = MINUTE if audit['semantics'] in _CHART_DELAY_SEMANTICS else 0
    chart_end_ms = cutoff - delay
    if not roles:
        outside = index > contract["short_strike_usd"] if side == "put" else index < contract["short_strike_usd"]
        if boundary_replaced and outside:
            lines.append("合格图表末点在短腿责任外，入区前没有已列主要位置，直接审查短腿；不虚构缓冲，不自动Reject。")
        else:
            lines.append("入区前没有已列主要位置，直接审查短腿；不虚构缓冲，不自动Reject。" if outside
                         else "现价已到达或进入短腿责任，不再将现价与短腿间的位置称为入区前缓冲。")
    attached = set()
    for k in sorted(roles, reverse=side == "put"):
        level = rows.get(k, {})
        oi = level.get("oi") or {}
        attrs = role_text(roles[k])
        for z in zones:
            if z[0] <= k <= z[1]:
                attrs += f"、成交接受区{fmt(z[0])}–{fmt(z[1])}（入区前部分）"
                attached.add(z)
        path = ("指数序列覆盖不足，仅限原生卡已记录片段；" if points and not continuous(points, cutoff-DAY, chart_end_ms) else '')
        lines.append(f"- **{fmt(k)}**：{attrs}；Call/Put OI {fmt(oi.get('c'))}/{fmt(oi.get('p'))} BTC。"
                     + path + path_text(level.get("before_24h"), index, k, cutoff, scale) + flow_text(level))
    for z in zones:
        if z not in attached:
            lines.append(f"- **{fmt(z[0])}–{fmt(z[1])}**：入区前成交接受区；该区边界的穿过路径未提供，不借用邻站反馈。")
    major = major_positions(case, card, contract)
    short, long = contract['short_strike_usd'], contract['long_strike_usd']
    level = rows.get(short, {})
    attrs = role_text(major.get(short, []))
    oi = level.get('oi') or {}
    lines.append(f"- **短腿{fmt(short)}**" + (f"：与{attrs}同位，仅一处，不另算一站" if attrs else '：责任起点')
                 + f"；Call/Put OI {fmt(oi.get('c'))}/{fmt(oi.get('p'))} BTC。"
                 + short_path_text(level, points, contract, index, cutoff, scale,
                                   report_protection=report_protection, chart_end_ms=chart_end_ms) + flow_text(level))
    interior = [k for k in major if min(short, long) < k < max(short, long)]
    if interior:
        k = min(interior, key=lambda k: abs(k-short))
        oi = (rows.get(k, {}).get('oi') or {})
        lines.append(f"- **责任区内下一处{fmt(k)}**：{role_text(major[k])}；Call/Put OI {fmt(oi.get('c'))}/{fmt(oi.get('p'))} BTC。"
                     + '从短腿向保护腿方向最近的已列主要位置，不是入区前缓冲。')
    if len(points) >= 2:
        first = next(((t, p) for t, p in reversed(points) if t <= chart_end_ms - 5 * 60_000), None)
        if first and chart_end_ms - first[0] <= 10 * 60_000 and chart_end_ms - points[-1][0] <= 60_000:
            lines.append(f"近5分钟指数从{fmt(first[1])}到{fmt(points[-1][1])} USD；仅辅助定位，不代替上述责任路径。")
    return "\n".join(lines)


def _spot_dir(value, pos_label, neg_label, flat_label='持平'):
    if value > 0:
        return pos_label
    if value < 0:
        return neg_label
    return flat_label


def _extract_spot_end_clock(window):
    """Return (end_ms, error_reason) for a spot window; missing or conflicting completion clock fails."""
    found = {}
    for key in _SPOT_END_CLOCK_KEYS:
        if key not in window or window[key] is None:
            continue
        val = window[key]
        if type(val) is not int or val <= 0:
            return None, f'窗口完成时钟({key})无效'
        found[key] = val
    if not found:
        return None, 'missing'
    unique = set(found.values())
    if len(unique) > 1:
        return None, '单窗存在互相冲突的完成时钟'
    return next(iter(unique)), None


def derive_non_overlapping_spot_windows(row, *, cutoff=None):
    """Verify same-source/same-cutoff/completed/no-gap spot windows and derive the non-overlapping [-12h, -4h] slice."""
    if not isinstance(row, Mapping):
        return {'comparable': False, 'reason': '未提供现货量价数据'}
    if cutoff is not None and not usable(row, cutoff, HOUR):
        return {'comparable': False, 'reason': '量价行不在可用时钟或质量未就绪'}
    vals = row.get('values')
    micro = vals.get('micro_flow') if isinstance(vals, Mapping) else None
    if not isinstance(vals, Mapping) or not isinstance(micro, Mapping):
        return {'comparable': False, 'reason': '缺少micro_flow现货窗口'}
    fast = micro.get('fast_4h')
    slow = micro.get('slow_12h')
    if not isinstance(fast, Mapping) or not isinstance(slow, Mapping):
        return {'comparable': False, 'reason': '缺少4h或12h现货窗口，未作12h减4h相减'}

    bad_flags = ('gap', 'truncated', 'cvd_degraded', 'backfill_mixed', 'window_backfill')
    for scope in (row, vals, micro, fast, slow):
        for flag in bad_flags:
            if scope.get(flag) is True:
                return {'comparable': False, 'reason': f'存在缺口/降级/回填标记({flag})，未作12h减4h相减'}

    parsed = {}
    end_clocks = {}
    for name, w in (('fast_4h', fast), ('slow_12h', slow)):
        if w.get('data_ready') is not True or w.get('cvd_unit') != 'BTC':
            return {'comparable': False, 'reason': f'{name}未就绪或单位非BTC，未作12h减4h相减'}
        if w.get('bar_complete') is False:
            return {'comparable': False, 'reason': f'{name}存在未完成柱(bar_complete=false)，未作12h减4h相减'}
        cvd = number(w.get('cvd_sum'))
        ret = number(w.get('momentum_return_pct'))
        if ret is None:
            ret = number(w.get('price_return_pct'))
        if cvd is None or ret is None:
            return {'comparable': False, 'reason': f'{name}净主动量或涨跌幅缺失，未作12h减4h相减'}
        parsed[name] = (cvd, ret)
        end_ms, err = _extract_spot_end_clock(w)
        if err and err != 'missing':
            return {'comparable': False, 'reason': f'{name}{err}，未作12h减4h相减'}
        end_clocks[name] = end_ms
        avail = w.get('available_at_ms')
        if cutoff is not None and type(avail) is int and avail > cutoff:
            return {'comparable': False, 'reason': f'{name}可得时钟晚于截止，未作12h减4h相减'}

    fast_end, slow_end = end_clocks['fast_4h'], end_clocks['slow_12h']
    if fast_end is None and slow_end is None:
        return {'comparable': False, 'reason': '两窗均缺少完成时钟，无法证明同截止，未作12h减4h相减'}
    if fast_end is None or slow_end is None:
        return {'comparable': False, 'reason': '单侧窗口缺少完成时钟，无法证明同截止，未作12h减4h相减'}
    if fast_end != slow_end:
        return {'comparable': False, 'reason': '两窗完成时钟不一致，未作12h减4h相减'}
    if cutoff is not None and fast_end > cutoff:
        return {'comparable': False, 'reason': '窗口完成时钟晚于截止，未作12h减4h相减'}

    for key in _SPOT_END_CLOCK_KEYS:
        fv, sv = fast.get(key), slow.get(key)
        if fv is not None and sv is not None and fv != sv:
            return {'comparable': False, 'reason': f'两窗截止时间({key})不一致，未作12h减4h相减'}

    for key in ('source', 'bar_source', 'symbol', 'price_source'):
        fv, sv = fast.get(key), slow.get(key)
        if (fv is not None) != (sv is not None) or (fv is not None and fv != sv):
            return {'comparable': False, 'reason': f'两窗来源口径({key})不一致，未作12h减4h相减'}

    for name, w in (('fast_4h', fast), ('slow_12h', slow)):
        if 'coverage_frac' in w and w['coverage_frac'] is not None:
            cf = number(w['coverage_frac'])
            if cf is None or cf < 0.85:
                return {'comparable': False, 'reason': f'{name}覆盖率不足0.85，未作12h减4h相减'}

    fast_start = fast.get('window_start_ms') if type(fast.get('window_start_ms')) is int else None
    slow_start = slow.get('window_start_ms') if type(slow.get('window_start_ms')) is int else None
    if fast.get('window_start_ms') is not None and fast_start is None:
        return {'comparable': False, 'reason': 'fast_4h起始时钟无效，未作12h减4h相减'}
    if slow.get('window_start_ms') is not None and slow_start is None:
        return {'comparable': False, 'reason': 'slow_12h起始时钟无效，未作12h减4h相减'}
    if fast_start is not None and fast_start >= fast_end:
        return {'comparable': False, 'reason': 'fast_4h起止时钟非法，未作12h减4h相减'}
    if slow_start is not None and slow_start >= slow_end:
        return {'comparable': False, 'reason': 'slow_12h起止时钟非法，未作12h减4h相减'}
    if fast_start is not None and slow_start is not None and not (slow_start < fast_start):
        return {'comparable': False, 'reason': '两窗起始时钟不满足嵌套关系，未作12h减4h相减'}

    fast_hours = 4.0
    if 'coverage_hours' in fast and fast['coverage_hours'] is not None:
        fh = number(fast['coverage_hours'])
        if fh is None or not (3.0 <= fh <= 4.5):
            return {'comparable': False, 'reason': 'fast_4h覆盖时长超出[3.0, 4.5]h，未作12h减4h相减'}
        fast_hours = fh
    elif fast_start is not None:
        fh = (fast_end - fast_start) / HOUR
        if not (3.0 <= fh <= 4.5):
            return {'comparable': False, 'reason': 'fast_4h起止时长超出[3.0, 4.5]h，未作12h减4h相减'}
        fast_hours = fh

    slow_hours = 12.0
    if 'coverage_hours' in slow and slow['coverage_hours'] is not None:
        sh = number(slow['coverage_hours'])
        if sh is None or not (10.0 <= sh <= 12.5):
            return {'comparable': False, 'reason': 'slow_12h覆盖时长超出[10.0, 12.5]h，未作12h减4h相减'}
        slow_hours = sh
    elif slow_start is not None:
        sh = (slow_end - slow_start) / HOUR
        if not (10.0 <= sh <= 12.5):
            return {'comparable': False, 'reason': 'slow_12h起止时长超出[10.0, 12.5]h，未作12h减4h相减'}
        slow_hours = sh

    if slow_hours - fast_hours < 4.0:
        return {'comparable': False, 'reason': '12h与4h覆盖时长差不足4.0h，未作12h减4h相减'}

    if fast.get('bar_count') is not None and slow.get('bar_count') is not None:
        fb, sb = number(fast['bar_count']), number(slow['bar_count'])
        if fb is None or sb is None or not (sb > fb > 0):
            return {'comparable': False, 'reason': 'bar_count不满足12h大于4h且为正，未作12h减4h相减'}

    fast_cvd, fast_ret = parsed['fast_4h']
    slow_cvd, slow_ret = parsed['slow_12h']
    prev_hours = round(slow_hours - fast_hours, 4)
    prev_cvd = slow_cvd - fast_cvd
    prev_rate = prev_cvd / prev_hours
    fast_rate = fast_cvd / fast_hours
    slow_rate = slow_cvd / slow_hours

    f_open, f_close = number(fast.get('price_open')), number(fast.get('price_close'))
    s_open, s_close = number(slow.get('price_open')), number(slow.get('price_close'))
    if (f_open is not None and f_open > 0 and f_close is not None and f_close > 0
            and s_open is not None and s_open > 0 and s_close is not None and s_close > 0
            and f_close == s_close):
        prev_ret = (f_open / s_open - 1.0) * 100.0
        return_method = 'endpoints'
    else:
        denom = 1.0 + fast_ret / 100.0
        if denom <= 0:
            return {'comparable': False, 'reason': '近4小时收益分母非正，无法推导前8小时价格变化'}
        prev_ret = ((1.0 + slow_ret / 100.0) / denom - 1.0) * 100.0
        return_method = 'compound'

    prev_flow_dir = _spot_dir(prev_cvd, 'buy', 'sell', 'flat')
    fast_flow_dir = _spot_dir(fast_cvd, 'buy', 'sell', 'flat')
    prev_price_dir = _spot_dir(prev_ret, 'up', 'down', 'flat')
    fast_price_dir = _spot_dir(fast_ret, 'up', 'down', 'flat')

    if prev_flow_dir == fast_flow_dir and prev_flow_dir != 'flat':
        flow_cn = '买' if fast_flow_dir == 'buy' else '卖'
        if prev_price_dir == fast_price_dir and prev_price_dir != 'flat':
            price_cn = '上行' if fast_price_dir == 'up' else '下行'
            summary = f'延续净主动{flow_cn}与{price_cn}（前后两段均价流同向）'
        else:
            summary = (f'延续净主动{flow_cn}（价格由{_spot_dir(prev_ret, "涨", "跌", "平")}'
                       f'转{_spot_dir(fast_ret, "涨", "跌", "平")}）')
    elif prev_flow_dir != fast_flow_dir:
        summary = (f'净主动流由{_spot_dir(prev_cvd, "买", "卖", "持平")}'
                   f'转{_spot_dir(fast_cvd, "买", "卖", "持平")}')
    else:
        summary = '两段净主动流均持平'

    return {
        'comparable': True,
        'window_end_ms': fast_end,
        'fast': {
            'hours': fast_hours,
            'cvd_sum': fast_cvd,
            'cvd_rate_btc_per_h': fast_rate,
            'return_pct': fast_ret,
        },
        'previous_8h': {
            'hours': prev_hours,
            'cvd_sum': prev_cvd,
            'cvd_rate_btc_per_h': prev_rate,
            'return_pct': prev_ret,
            'return_method': return_method,
        },
        'slow': {
            'hours': slow_hours,
            'cvd_sum': slow_cvd,
            'cvd_rate_btc_per_h': slow_rate,
            'return_pct': slow_ret,
        },
        'direction_transition': {
            'flow': f'{prev_flow_dir}_to_{fast_flow_dir}',
            'price': f'{prev_price_dir}_to_{fast_price_dir}',
            'flow_continued': prev_flow_dir == fast_flow_dir and prev_flow_dir != 'flat',
            'flow_switched': prev_flow_dir != fast_flow_dir,
            'price_continued': prev_price_dir == fast_price_dir and prev_price_dir != 'flat',
            'price_switched': prev_price_dir != fast_price_dir,
            'summary': summary,
        },
    }


def _format_dual_asof_notes(boundary_replaced, chart_audits, l2_obs, contracts):
    parts = []
    if boundary_replaced:
        fetched = next((a['fetched_at_ms'] for a in chart_audits.values() if a.get('fetched_at_ms') is not None), None)
        fetched_txt = f"（图表取得时钟{stamp(fetched)}）" if fetched is not None else ''
        labels = sorted({excl[0] for excl, _ in boundary_replaced.values()})
        elig = sorted({latest[0] for _, latest in boundary_replaced.values()})
        parts.append(
            f"双as-of核验：Deribit btc_usd 图表分钟标签区间语义未证实，排除边界桶"
            f"{'、'.join(stamp(t) for t in labels)}，图表路径仅截至合格分钟标签"
            f"{'、'.join(stamp(t) for t in elig)}{fetched_txt}；不以未完结图表桶充当读取时当前指数或断言读取时区内外。"
        )
    if l2_obs and boundary_replaced:
        obs_items = []
        for s in ('put', 'call'):
            obs = l2_obs.get(s)
            if not obs:
                continue
            price = obs['price_usd']
            rel_parts = []
            for side_name, c in contracts.items():
                zone = responsibility_zone(price, c)
                dist = abs(price - c['short_strike_usd'])
                rel_parts.append(f"{side_name.title()}短腿{fmt(c['short_strike_usd'])}{zone}（距{fmt(dist)} USD）")
            obs_items.append(
                f"{s.title()}原生盘口{stamp(obs['observed_ms'])}（取得{stamp(obs['acquired_ms'])}）"
                f"见指数{price:,.2f} USD（{'；'.join(rel_parts)}）"
            )
        if obs_items:
            parts.append(
                "Deribit原生盘口独立观测：" + "；".join(obs_items)
                + "。盘口观测与图表分钟标签分源记录、跨源先后不可证实，不拼成单一时间线。"
            )
    return " ".join(parts)


def build_input(put_case, call_case, put_card, call_card, *, events=None, index_path=None,
                ard_history=None, ard_band_snapshot=None, include_ard=True):
    """Return player-facing Markdown. Raises HandNotDealt before rendering on known events."""
    cases, raw_cards = {"put": put_case, "call": call_case}, {"put": put_card, "call": call_card}
    contracts = {s: bind(cases[s], raw_cards[s], s) for s in cases}
    pc, cc = contracts["put"], contracts["call"]
    if pc["expiry_ms"] != cc["expiry_ms"] or raw_cards['put'].get('round') != raw_cards['call'].get('round'):
        raise ValueError("pair must share expiry and reading round")
    # Native archive freezes the two candidates serially, normally seconds apart.
    if abs(raw_cards['put']['read_at_ms'] - raw_cards['call']['read_at_ms']) > 60_000:
        raise ValueError("pair readings more than one minute apart")
    chart_audits = {s: chart_points_audit(index_path, pc['expiry_ms'], raw_cards[s]['read_at_ms']) for s in cases}
    cards = {}
    boundary_replaced = {}
    for s in cases:
        raw_card = raw_cards[s]
        audit = chart_audits[s]
        excluded = audit['excluded_boundary']
        latest = audit['latest_eligible']
        if excluded and latest is not None and any(abs(p - raw_card['index_usd']) <= 1e-6 for _, p in excluded):
            cards[s] = dict(raw_card, index_usd=latest[1])
            boundary_replaced[s] = (excluded[-1], latest)
        else:
            cards[s] = raw_card
    l2_obs = extract_l2_index_observations(cases, raw_cards)
    advisory_events = []
    for s in cases:
        for note in check_events(cases[s], cards[s]["read_at_ms"], contracts[s]["expiry_ms"], events):
            if note not in advisory_events:
                advisory_events.append(note)
    scales = {s: expiry_scale(cases[s], cards[s]["read_at_ms"], contracts[s]["expiry_ms"]) for s in cases}
    groups = {s: stations(cases[s], cards[s], contracts[s]) for s in cases}
    responsibility = []
    for s, c in contracts.items():
        card, scale = cards[s], scales[s]
        route = " → ".join(fmt(k) for k in sorted(groups[s][0], reverse=s == "put")) or "无已列主要位置"
        lo, hi = sorted((c['short_strike_usd'], c['long_strike_usd']))
        if s in boundary_replaced:
            _, latest = boundary_replaced[s]
            idx_label = f"合格图表末点{fmt(card['index_usd'])}（标签{stamp(latest[0])}）"
        else:
            idx_label = f"指数{fmt(card['index_usd'])}"
        responsibility.append(f"{s.title()}：{idx_label}，{responsibility_zone(card['index_usd'], c)}；卖{fmt(c['short_strike_usd'])}、买{fmt(c['long_strike_usd'])}，"
                              f"责任区{fmt(lo)}–{fmt(hi)} USD，数量{fmt(c['quantity_btc'])} BTC。"
                              f"入区前主结构由近到远：{route}。短腿距离{distance(abs(card['index_usd']-c['short_strike_usd']), scale)}。")
    dual_asof_note = _format_dual_asof_notes(boundary_replaced, chart_audits, l2_obs, contracts)
    if dual_asof_note:
        responsibility.append(dual_asof_note)
    if scales['put'] == scales['call']:
        scale_text = f"本期到期隐含波动给出的剩余预期波动尺度：{fmt(scales['put'])}" + (" USD。" if scales['put'] else "。")
    else:
        scale_text = "本期到期隐含波动剩余尺度：" + "；".join(f"{s.title()} {fmt(scales[s])}" + (" USD" if scales[s] else "") for s in cases) + "。"
    if not all(scales.values()):
        scale_text += "未提供尺度的一侧不按美元距离判断松紧，继续判断路径。"
    if advisory_events:
        scale_text += f"到期前已知常规日程（供牌手审查）：{'、'.join(advisory_events)}。"
    cutoff = put_card['read_at_ms']
    g = entry(put_case, 'L3.gex.structure')
    net = number((g.get('values') or {}).get('total_net_gex')) if usable(g, cutoff, HOUR) else None
    if net is not None:
        scale_text += f"市场净Gamma为{'负' if net < 0 else '正' if net > 0 else '零'}，净GEX约{net / 1e8:.1f}亿美元量级；不证明本侧义务方向。"
    anchor = entry(put_case, 'L3.space.anchor')
    av = (anchor.get('values') or {}).get('anchor') or {}
    flip = number(av.get('effective_flip_point')) if usable(anchor, cutoff, HOUR) and av.get('freshness') == 'FRESH' else None
    if flip is not None:
        put_ref_label = "合格图表末点" if 'put' in boundary_replaced else "现价"
        scale_text += f"翻转参考{fmt(flip)}，{put_ref_label}在其{side_of(cards['put']['index_usd'],flip)}。"
        for s,c in contracts.items():
            idx, short, long = cards[s]['index_usd'], c['short_strike_usd'], c['long_strike_usd']
            outside = idx > short if s == 'put' else idx < short
            if flip == short:
                relation='与短腿同位，不另算一站'
            elif min(short,long)<flip<max(short,long):
                relation='在短腿与保护腿之间，不是入区前缓冲'
            elif (flip<=long if s=='put' else flip>=long):
                relation='在保护腿边界或其后，不是入区前缓冲'
            elif outside and min(idx,short)<flip<max(idx,short):
                relation='在入区前路径，仅作定位，不视为障碍'
            elif (flip<=short if s=='put' else flip>=short):
                relation='在短腿之后或边界上，不是入区前缓冲'
            else:
                relation='在该侧入区前行走方向之外，不是必经位置'
            scale_text += f"对{s.title()}，轴{relation}。"
    pin_text = pin_context(cases, cards, contracts)
    ard_text = ''
    if include_ard:
        min_read = min(c['read_at_ms'] for c in cards.values())
        ard_index_path = index_path
        if isinstance(index_path, Mapping) and _chart_semantics(index_path) in _CHART_DELAY_SEMANTICS:
            ard_pts = chart_points_audit(index_path, pc['expiry_ms'], min_read)['eligible']
            ard_index_path = dict(index_path, points=[[t, p] for t, p in ard_pts])
        ard_facts = ard.analyze(ard_history, ard_index_path, expiry_ms=pc['expiry_ms'],
                                cutoff_ms=min_read,
                                band_snapshot=ard_band_snapshot)
        current_pin = (values(put_case,'L3.expiry_walls').get('walls') or {}).get('pin')
        ard_text = '\n\n'+ard.render(ard_facts,current_pin=current_pin)
    sections = [path_section(cases[s],cards[s],contracts[s],scales[s],*groups[s],index_path,
                             report_protection=include_ard,
                             boundary_replaced=boundary_replaced.get(s)) for s in cases]
    spot = []
    row = entry(put_case, 'L5.volume_price')
    micro = (row.get('values') or {}).get('micro_flow') or {}
    derived = derive_non_overlapping_spot_windows(row, cutoff=cutoff)
    if usable(row, cutoff, HOUR):
        for key, label in [('fast_4h', '近4小时'), ('slow_12h', '近12小时')]:
            f = micro.get(key) or {}
            cvd = number(f.get('cvd_sum'))
            ret = number(f.get('momentum_return_pct'))
            if ret is None:
                ret = number(f.get('price_return_pct'))
            if f.get('data_ready') is True and f.get('cvd_unit') == 'BTC' and cvd is not None and ret is not None:
                same = '同向' if cvd * ret > 0 else '不一致' if cvd * ret < 0 else '无明确同向'
                flow = '净主动成交持平' if cvd == 0 else f"净主动{'买' if cvd > 0 else '卖'}{fmt(abs(cvd))} BTC"
                rate_text = ''
                if derived.get('comparable'):
                    rate_val = derived['fast' if key == 'fast_4h' else 'slow']['cvd_rate_btc_per_h']
                    rate_text = f"（{rate_val:+.1f} BTC/h）"
                spot.append(f"{label}现货成交柱{flow}{rate_text}、价格变化{ret:+.2f}%，价流{same}")
        if derived.get('comparable'):
            prev = derived['previous_8h']
            prev_cvd, prev_ret, prev_rate = prev['cvd_sum'], prev['return_pct'], prev['cvd_rate_btc_per_h']
            prev_same = '同向' if prev_cvd * prev_ret > 0 else '不一致' if prev_cvd * prev_ret < 0 else '无明确同向'
            prev_flow = '净主动成交持平' if prev_cvd == 0 else f"净主动{'买' if prev_cvd > 0 else '卖'}{fmt(abs(prev_cvd))} BTC"
            ret_basis = '两端价差' if prev['return_method'] == 'endpoints' else '复合'
            spot.append(
                f"前8小时不重叠窗口（相对读牌[-12h, -4h]，约{prev['hours']:.1f}h，同源推导）"
                f"{prev_flow}（{prev_rate:+.1f} BTC/h）、{ret_basis}价格变化{prev_ret:+.2f}%，价流{prev_same}；"
                f"与近4小时对照：{derived['direction_transition']['summary']}"
            )
        elif spot:
            spot.append(f"未作12h减4h不重叠相减（{derived['reason']}）")
    if spot:
        spot_text = (
            '；'.join(spot)
            + '。区分三点：近4小时若存在可核验的不利价流同向压力，须明确作为当前已成立的反证（不因“增强未确认”或近5分钟小幅回落自动抵消）；'
            + '可比不重叠前8h→近4h反映历史阶段切换或延续；最近更短窗口是否继续增强才需要更高频新窗确认。'
        )
        if include_ard:
            spot_text += (f"现货涨跌取 FMZ 的 Binance 现货量柱首末收盘价，窗口止于末根完成柱。"
                          f"案卷量价事实时钟为{stamp(row['asof_ms'])}，该时钟不等于首末柱时间。"
                          "ARD 另用 Deribit BTC/USD 指数分钟价，两者的价格来源和窗口口径不同。")
    else:
        spot_text = '现货成交限定未提供；继续依据位置与路径判断。'
    # The without-ARD mode is a replay of the frozen first-order baseline.
    decision_policy = (
        '至少一侧责任路径条件可接受时，必须Accept并选侧。只有Put和Call分别有足以排除其承担条件的具体不利事实，才Reject。'
        '关键反证必须审查，但不自动排除该侧。没有可接受侧且关键资料实质阻塞判断时，才Unknown。'
        if include_ard else '至少一侧可接受就必须Accept并选侧；两侧都有具体不利事实才Reject；没有可接受侧且关键资料实质阻塞判断才Unknown。'
    )
    protection_review = (
        '\n历史窗内曾达到或越过保护腿，即使现已回到责任外，也须纳入该侧最强反证审查。区分窗首已越界、'
        '窗内确认的进入、最近进入与回收、当前所在侧。最近较浅进入不能替代完整历史，旧越界不单独构成Reject。'
        if include_ard else ''
    )
    rejection_output = 'Reject分别写明两侧排除承担条件的具体依据' if include_ard else 'Reject分别写两侧反证'
    hours=(pc['expiry_ms']-max(c['read_at_ms'] for c in cards.values()))/HOUR
    return f"""# AI牌手完整输入

## 这是什么局

Deribit BTC末日轮信用垂直价差，BTC结算，到期{stamp(pc['expiry_ms'])}，剩余约{hours:.2f}小时。使用引擎真实候选，不外移短腿。判断这次交割前接不接短腿责任，不预测价格。
Put读取于{stamp(put_card['read_at_ms'])}，Call读取于{stamp(call_card['read_at_ms'])}；这是冻结截面，不冒称当前行情。已检查所提供的寿命内事件记录；没有附带明确干扰标记，不自行弃权。

## 怎么读

最小推理闭环：先确定短腿与保护腿责任边界，再检查现价到短腿之间的必经位置；核对这些位置及短腿本身的历史穿越、回收和当前所在侧，最后形成承担条件。没有中间站时直接审查短腿，不得虚构缓冲。同价属性合为一处，不当作多票；责任区内位置不算入区前缓冲。资金流、Gamma、最近价格变化只作限定，不能跳过这条链。只用本期到期隐含波动尺度读距离；缺尺度或成交、义务方向未知，都不单独构成弃权理由。{ARD_READING if include_ard else ''}

## 规则

现象≠结论。两侧须按同一标准审完，展示顺序不代表优先级，不能在先读一侧勉强可接受后停止。{decision_policy}
双方都可建立时必须择优：围绕本次交割前的责任路径，比较入区前结构、测试与回收、当前所在侧，以及在剩余期限和本期尺度下哪侧更少依赖尚未出现的阻断或回收。指出具体相对优势及最强反证；不能用“这侧也能接受”代替比较，也不能给两侧设置不同的宽严标准。距离、OI、穿越次数不单独决定选侧；确无可证实优势时如实说明，仍作有条件的Accept选择，不编造优势或因平局弃权。接受只表示路径条件可接受，不表示已授权交易。{protection_review}
关键反证须正面审查：价格净路径向该侧短腿靠近、余量收缩，且同窗现货主动流同向时，检查较近窗口是否有可验证缓和、责任是否真正回收。Put的下行/净卖与Call的上行/净买分别是不利组合，反向流不能照搬；若另有已观测的归一化距离紧缩、重复进入或仍留区内，进一步判断是否与本侧承担条件不相容。反复穿过的Gamma位或同位属性，不得仅因地理上介于现价与短腿之间就被赋予“可靠阻挡力”；若因距离或余量更大而选择承受不利现货流的一侧，必须正面解释为何该责任仍可承担，不能仅因“增强未确认”淡化既成反证。关键反证候选不自动排除，但充分排除后不能靠历史未进入、另一侧不完美或维持Accept比例强行承保。一侧被排除而另一侧可接受就选另一侧，两侧被排除才Reject；未进入也不等于当前路径有利。

## 本局牌面

1. **责任。** {' '.join(responsibility)} 已在责任内或行走方向之外的位置不作为入区前缓冲。

2. **空间背景：尺度、体制、翻转轴、Pin。** {scale_text} {pin_text}{ard_text}

3. **Put责任路径，入区前由近到远。** {sections[0]}

4. **Call责任路径，入区前由近到远。** {sections[1]}

5. **现货量价与主动流。** {spot_text}

路径窗为读取前24小时，指数按一分钟采样；时刻是采样确认，不是精确触价，缺分钟不补齐。短腿深度绑定具体入区片段，站点的首小时偏离不代替入区深度；未进入侧的最近接近与读取距离仅作端点比较。每站成交注明自身窗口，仅普通单腿，跨窗并读不证明穿过瞬间因果。

## 读完后另写

先用两侧各一小段给出可核查的责任路径判断，包含到本次交割前对该责任有利与不利的具体路径；再给Accept、Reject或Unknown及选侧，说明相对另一侧胜在哪里、最强反证是什么，最后冻结承担条件与有效破坏标准。理由须能沿“责任边界→入区前位置→穿越与当前所在侧→承担条件”追溯；无中间站直接审短腿。不要只复述事实、用“保持责任外”循环解释选择，或用过去24小时替未来剩余寿命作预测；不能为了支持预选侧才倒定承担条件。条件与破坏标准是独立评估，不回填现象。
零站与历史未进入不构成已证实的结构缓冲；反复穿过的Gamma位或同位属性，不得仅因地理上介于现价与短腿之间就被赋予“可靠阻挡力”。若价格沿通向该侧责任的方向接近已列Pin，且同窗现货主动净流与该接近同向，把剩余路径余量收缩列为承担条件的复核提醒；不把Pin视为吸附保证，也不新增独立Accept/Reject或退出触发；若入场后出现新增重大反证或安全余量实质消耗，将由事中事件门独立审查原承担条件是否仍成立（并不必等到机械越界才退出）。期权Put净主动卖不等于标的下行净流，4h/12h净流不能确认最近5分钟同向运动。
压力变化只用同源、同口径、可核验的相邻不重叠窗口比较；不等长窗口不能直接比净流绝对额。若存在可核验的近4小时不利价流同向压力，须作为当前已成立反证正面处理；选择承受不利现货流的一侧时，必须正面解释为何该责任仍可承担，不以“增强未确认”或近5分钟小幅回落淡化。资料不足就写“目前没有确认压力已经缓和”，不改写成“已证明增强”。小幅反弹、Pin或同位多个属性不能自动抵消较长窗口反证。后续新低只能进后续截面，不能倒灌旧牌面，也不自动成为承保机会。
{rejection_output}；Unknown仅写真正阻塞项。不用后来的盈亏改写牌面。

## 若已经持有

入场分别冻结：具体责任与入场时间、当时已发生的路径事实、持续承担条件、有效破坏标准、检查时钟和采样确认口径。“过去24小时未进入”是历史，不是将来条件。破坏规则须写清UTC检查锚点及间隔、采用哪个指数采样、触及/严格进入/返回的边界和确认方式；“进入且仍未返回”只能在预先固定的时点判断，不能让每次续判另选时间。一次触及、一次进入、连续采样留区内是不同事实；检查间或分钟内的连续状态不凭空补造。
持仓时答Hold/Revoke，锁定同一责任与原冻结标准，旧窗口事实不算新增。缺原规则、确认口径或必要覆盖时，不能宣称已触发或未触发；未触发某条规则也不自动证明全部承担条件成立。只按新事实与冻结规则续判，不因时间、浮亏、Delta或义务未知单独退出。新严重反证若未被原规则覆盖，另记复盘，不能偷改原规则；独立硬风控仍可按自身既有权限保护。到期前新尺度只重读距离，不改破坏口径。
"""


def read_json(path):
    p=Path(path)
    data=gzip.decompress(p.read_bytes()) if p.suffix=='.gz' else p.read_bytes()
    return json.loads(data.decode('utf-8-sig'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',help='existing research_archive root; selects the actual native pair')
    parser.add_argument('--expiry-day',help='UTC expiry date, with --archive')
    parser.add_argument('--round',choices=['r1','r2'],help='native round, with --archive')
    parser.add_argument('--put-case')
    parser.add_argument('--call-case')
    parser.add_argument('--levels',help='native level_card JSONL')
    parser.add_argument('--events',help='existing contract_events object for additional known interference')
    parser.add_argument('--index-path',help='optional native index_path@0.1; cut to each reading')
    parser.add_argument('--ard-history',help='existing per-expiry structure JSONL; needs actual availability clocks')
    parser.add_argument('--ard-band-snapshot',help='optional native FMZ snapshot with explicit band_half known before ARD window')
    parser.add_argument('--without-ard',action='store_true',help='historical replay only: reproduce the unchanged first-order baseline (A)')
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    if args.archive:
        if not args.expiry_day or not args.round or any([args.put_case,args.call_case,args.levels]):
            parser.error('--archive needs --expiry-day and --round; do not mix explicit files')
        # Parse/reformat the date, rather than treating it as an arbitrary path component.
        day=datetime.strptime(args.expiry_day,'%Y-%m-%d').strftime('%Y-%m-%d')
        root=Path(args.archive)
        if not args.ard_history and (root/'structure'/f'{day}.jsonl').exists():
            args.ard_history=str(root/'structure'/f'{day}.jsonl')
        rows=[json.loads(l) for l in (root/'levels'/f'{day}.jsonl').read_text(encoding='utf-8-sig').splitlines() if l.strip()]
        cases,cards=[],[]
        for side in ('put','call'):
            matched=[r for r in rows if r.get('schema')=='level_card@0.1' and r.get('side')==side
                     and r.get('round')==args.round and r.get('case_id')]
            if len(matched)!=1:
                parser.error('exactly one native case-bound card per side/round is required')
            card=matched[0]
            read_day=datetime.fromtimestamp(card['read_at_ms']/1000,timezone.utc).strftime('%Y-%m-%d')
            files=list((root/'cases'/read_day).glob(f"*_{card['case_id']}.json*"))
            if len(files)!=1:
                parser.error('exactly one archived case for the card is required')
            cards.append(card);cases.append(read_json(files[0]))
        if not args.index_path and (root/'paths'/f'{day}.json.gz').exists():
            args.index_path=str(root/'paths'/f'{day}.json.gz')
    else:
        if not all([args.put_case,args.call_case,args.levels]):
            parser.error('supply --archive or --put-case --call-case --levels')
        cases=[read_json(args.put_case),read_json(args.call_case)]
        rows=[json.loads(line) for line in Path(args.levels).read_text(encoding='utf-8-sig').splitlines() if line.strip()]
        cards=[]
        for case in cases:
            matched=[r for r in rows if r.get('schema')=='level_card@0.1' and r.get('case_id')==case['case_id']]
            if len(matched)!=1:
                parser.error('exactly one native level card per case is required')
            cards.append(matched[0])
    try:
        text=build_input(*cases,*cards,events=read_json(args.events) if args.events else None,
                         index_path=read_json(args.index_path) if args.index_path else None,
                         ard_history=[json.loads(line) for line in Path(args.ard_history).read_text(encoding='utf-8-sig').splitlines() if line.strip()] if args.ard_history else None,
                         ard_band_snapshot=read_json(args.ard_band_snapshot) if args.ard_band_snapshot else None,
                         include_ard=not args.without_ard)
    except HandNotDealt as exc:
        parser.exit(2,str(exc)+'\n')
    with Path(args.out).open('x',encoding='utf-8') as stream:
        stream.write(text)


if __name__=='__main__':
    main()
