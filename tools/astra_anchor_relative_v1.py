"""Pin-relative observed dynamics. No score, decision, interpolation or orders.

Reads the existing per-expiry structure archive. Price samples use the last Pin
already available then, subject to the archive's existing 40-minute gap limit.
A supplied native FMZ band is frozen BEFORE the window; it is a reused distance
scale centred on Pin, not the original Flip score or evidence of attraction.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math

try:
    from .astra_expiry_structure_v1 import GAP_MS, PIN_METHOD, PIN_SOURCE
except ImportError:
    from astra_expiry_structure_v1 import GAP_MS, PIN_METHOD, PIN_SOURCE

MINUTE = 60_000
HOUR = 3_600_000


def num(x):
    return float(x) if not isinstance(x, bool) and isinstance(x, (int, float)) and math.isfinite(x) else None


def clock(x):
    return type(x) is int and x > 0


def frozen_band(snapshot, start):
    """Only explicit native band_half, known before start and still FRESH then.

    The existing FMZ stale age is 40 minutes. No reverse inference from a score,
    normalized deviation or today's price; no fallback band for a missing value.
    """
    if not isinstance(snapshot, dict):
        return None
    a = (snapshot.get('factor_snapshot') or {}).get('anchor') or {}
    available = snapshot.get('source_available_at_ms')
    observed = snapshot.get('snapshot_exported_ms')
    gex_at = a.get('gex_source_ts_ms')
    half = num(a.get('band_half'))
    if (not all(clock(t) for t in (available, observed, gex_at))
            or not gex_at <= observed <= available <= start
            or start - gex_at >= 40*MINUTE or start-observed >= 40*MINUTE
            or half is None or half <= 0 or a.get('ready') is not True
            or a.get('freshness') != 'FRESH'):
        return None
    return dict(half_width_usd=half, band_clamped=a.get('band_clamped'), frozen_at_ms=start, source_at_ms=observed,
                available_at_ms=available, source='factor_snapshot.anchor.band_half')


def analyze(history, index_path, *, expiry_ms, cutoff_ms, band_snapshot=None, window_ms=4*HOUR, strict_asof=False):
    """Internal factual result; the player consumes render(), not a new protocol."""
    start = cutoff_ms-window_ms
    band = frozen_band(band_snapshot, start)
    rows = []
    unclocked = 0
    legacy_backfill = False
    for r in history or []:
        if r.get('schema') != 'structure_snapshot@0.1' or r.get('expiry_ms') != expiry_ms:
            continue
        at, ready = r.get('at_ms'), r.get('available_at_ms')
        if not clock(at) or at > cutoff_ms:
            continue
        if 'available_at_ms' not in r or ready is None or (strict_asof and r.get('compat_backfill_applied') is True):
            if strict_asof:
                unclocked += 1
                continue
            ready = at
            legacy_backfill = True
        if not clock(ready) or ready < at:
            unclocked += 1
            continue
        if ready > cutoff_ms:
            continue
        pin = num(((r.get('profile') or {}).get('walls') or {}).get('pin'))
        status = r.get('pin_status') if 'pin_status' in r and r.get('pin_status') is not None else ('ok' if pin is not None and pin > 0 else 'unavailable')
        valid = pin is not None and pin > 0 and status == 'ok'
        method = r['pin_method'] if 'pin_method' in r else PIN_METHOD
        source = r['pin_source'] if 'pin_source' in r else PIN_SOURCE
        rows.append(dict(at=at, ready=ready, pin=pin if valid else None,
                         method=method, source=source))
    rows.sort(key=lambda r: (r['ready'], r['at']))
    # Conflicting simultaneous snapshots are unusable, not an arbitrary last winner.
    unique = []
    for r in rows:
        if unique and r['ready'] == unique[-1]['ready']:
            if (r['pin'],r['method'],r['source'],r['at']) != (unique[-1]['pin'],unique[-1]['method'],unique[-1]['source'],unique[-1]['at']):
                unique[-1] = {**r, 'pin':None}
        else:
            unique.append(r)
    # Availability order must not let a late old observation roll either the
    # displayed migration history or the price reference back to an older Pin.
    rows = []
    for r in unique:
        if not rows or r['at'] >= rows[-1]['at']:
            rows.append(r)
    prior = [r for r in rows if r['ready'] <= start]
    observations = prior[-1:]+[r for r in rows if r['ready'] > start]
    observations = [r for r in observations if r['at'] >= start-GAP_MS]
    changes = [dict(from_pin=a['pin'], to_pin=b['pin'], observed_at_ms=b['at'], confirmed_at_ms=b['ready'],
                    comparable=bool(a['method'] and a['source'] and (a['method'],a['source'])==(b['method'],b['source'])
                               and b['at']-a['at']<=GAP_MS))
               for a,b in zip(observations,observations[1:])
               if a['pin'] is not None and b['pin'] is not None and a['pin']!=b['pin']]
    prices = {}
    if isinstance(index_path,dict) and index_path.get('index_name')=='btc_usd' and index_path.get('expiry_ms')==expiry_ms:
        for t,p in index_path.get('points') or []:
            if clock(t) and start<=t<=cutoff_ms and t%MINUTE==0 and num(p) is not None and p>0:
                prices[t] = p if t not in prices or prices[t]==p else None
    grid = list(range((start//MINUTE+1)*MINUTE,cutoff_ms//MINUTE*MINUTE+1,MINUTE))
    samples=[]; events=[]; previous=None; cursor=0; known=None; segment=0
    for t in grid:
        rebased=False
        while cursor<len(rows) and rows[cursor]['ready']<=t:
            incoming=rows[cursor];cursor+=1
            if known is not None and incoming['at']<known['at']:
                continue  # a late older receipt cannot roll the known anchor back
            rebased=rebased or (known is not None and
                (incoming['pin'],incoming['method'],incoming['source'])!=(known['pin'],known['method'],known['source']))
            known=incoming
        price=prices.get(t)
        if known is None or known['pin'] is None or t-known['at']>GAP_MS or price is None:
            previous=None
            continue
        identity=(known['pin'],known['method'],known['source'])
        joined=not rebased and previous is not None and previous['t']==t-MINUTE and previous['identity']==identity
        if not joined: segment+=1
        d=price-known['pin']
        inside=abs(d)<=band['half_width_usd'] if band else None
        item=dict(t=t,price=price,pin=known['pin'],relative=d,distance=abs(d),inside=inside,segment=segment,identity=identity)
        if joined and band and known['method'] and known['source'] and inside!=previous['inside']:
            events.append(dict(kind='return' if inside else 'leave',at_ms=t,segment=segment,pin=known['pin']))
        samples.append(item);previous=item
    first,last=(samples[0],samples[-1]) if samples else (None,None)
    full=bool(grid) and len(samples)==len(grid)
    method_known=bool(observations) and all(r['method'] and r['source'] for r in observations)
    method_same=method_known and len({(r['method'],r['source']) for r in observations})==1
    same_anchor=bool(samples) and not changes and len({s['pin'] for s in samples})==1
    current_segment=[s for s in samples if last and s['segment']==last['segment']]
    current_events=[e for e in events if last and e['segment']==last['segment']]
    relation='coverage_or_method_limited'
    if full and method_same:
        if same_anchor:
            returns=sum(e['kind']=='return' for e in current_events)
            if returns:
                relation=('departed_again' if current_events[-1]['kind']=='leave' else
                          'repeated_returns' if returns>1 else 'departed_then_returned')
            elif last['distance']>first['distance'] and all(b['distance']>=a['distance'] for a,b in zip(samples,samples[1:])):
                relation='sampled_distance_increasing'
            else:
                relation='endpoint_only'
        elif (last['price']-first['price'])*(last['pin']-first['pin'])>0:
            relation='same_direction_endpoint_changes'
        else:
            relation='anchor_changed'
    # Keep a window-start reference separate from the historical, moving Pin.
    # Only a Pin already known at start may be frozen; later receipts cannot seed it.
    reference = prior[-1] if prior and prior[-1]['pin'] is not None and start-prior[-1]['at']<=GAP_MS else None
    fixed = None
    if reference:
        fixed_prices = [(t,prices[t]) for t in grid if prices.get(t) is not None]
        fixed = dict(anchor=dict(reference), frozen_at_ms=start, valid_samples=len(fixed_prices),
                     first=None, last=None)
        if fixed_prices:
            fixed['first'],fixed['last'] = [dict(t=t,price=p,relative=p-reference['pin'])
                                            for t,p in (fixed_prices[0],fixed_prices[-1])]
    return dict(expiry_ms=expiry_ms,index_name='btc_usd',start_ms=start,end_ms=cutoff_ms,
                fixed_reference=fixed,band=band,observations=observations,changes=changes,
                unclocked_rows=unclocked,expected_samples=len(grid),valid_samples=len(samples),full=full,
                method_comparable=method_same,first=first,last=last,
                occupancy_pct=100*sum(s['inside'] for s in samples)/len(samples) if samples and band and method_same else None,
                events=events,current_events=current_events,relation=relation,
                current_segment_start=current_segment[0] if current_segment else None,
                price_displacement_usd=last['price']-first['price'] if samples else None,
                pin_displacement_usd=last['pin']-first['pin'] if samples else None,
                relative_displacement_usd=last['relative']-first['relative'] if samples else None,
                **({'legacy_clock_backfill': True} if legacy_backfill else {}))


def render(facts, *, current_pin=None, disclose_backfill=False):
    def f(x): return f'{x:,.1f}'.removesuffix('.0')
    def when(t): return datetime.fromtimestamp(t/1000,timezone.utc).strftime('%m-%d %H:%M:%S UTC')
    head=f"**锚相对动态ARD（{when(facts['start_ms'])}—{when(facts['end_ms'])}）。** "
    obs=[r for r in facts['observations'] if r['pin'] is not None]
    if not obs:
        return head+'缺可按当时可用时间对齐的本期Pin历史，迁移未确认；不拿当前Pin回看历史，也不据此弃权。'
    changes=facts['changes']
    if changes:
        last=changes[-1]
        text=f"本期Pin输出位置由{f(obs[0]['pin'])}变为{f(obs[-1]['pin'])} USD。最近一次输出变化{f(last['from_pin'])}→{f(last['to_pin'])}，截面时钟{when(last['observed_at_ms'])}，至{when(last['confirmed_at_ms'])}已可用。"
        if not facts['method_comparable'] or not all(c['comparable'] for c in changes):
            text+='历史方法身份、来源或时间覆盖不足，不能确认是同口径结构迁移。'
    elif len({r['pin'] for r in obs})==1:
        text=f"可用截面中的本期Pin均为{f(obs[-1]['pin'])}；只表示所见截面未变，不代表每次刷新都未迁移。"
    else:
        text=f"已有截面见Pin {f(obs[0]['pin'])}及{f(obs[-1]['pin'])}；断点隔开，迁移时序不完整。"
    text+=f"按最近已知锚对齐指数分钟价，有效{facts['valid_samples']}/{facts['expected_samples']}个分钟样本。覆盖数不是独立结构更新次数，也不证明吸附、定价完成或因果。超过40分钟的旧锚、缺价或缺口不补齐。"
    first,last=facts['first'],facts['last']
    if first and last:
        text+=f"覆盖端点：价格位移{facts['price_displacement_usd']:+,.1f}、Pin位移{facts['pin_displacement_usd']:+,.1f}，价格减Pin由{first['relative']:+,.1f}变为{last['relative']:+,.1f} USD。"
        segment_start=facts['current_segment_start']
        if segment_start and segment_start['t']!=first['t']:
            text+=f"最近同价已知Pin片段自{when(segment_start['t'])}起，价格减Pin由{segment_start['relative']:+,.1f}变为{last['relative']:+,.1f} USD。"
        text+=f"末个有效分钟{when(last['t'])}距当时Pin {f(last['distance'])} USD。"
    fixed=facts.get('fixed_reference')
    if changes and fixed and fixed['first']:
        text+=f"另以窗初已知Pin {f(fixed['anchor']['pin'])}固定参照，价格减该锚由{fixed['first']['relative']:+,.1f}变为{fixed['last']['relative']:+,.1f} USD（有效{fixed['valid_samples']}/{facts['expected_samples']}个分钟）；参照不随Pin迁移。"
    if facts['band']:
        text+=f"半宽固定为窗前已知的{f(facts['band']['half_width_usd'])} USD，中心取历史逐点已知Pin；有效采样停留率{facts['occupancy_pct']:.1f}%。" if facts['occupancy_pct'] is not None else '已有固定半宽，但覆盖或口径不可比，不报整窗停留率。'
        if facts['band'].get('band_clamped'):text+='原半宽曾按源算法上限裁剪，此处原值冻结。'
        recent=facts['current_events']
        for kind,label in [('leave','离开'),('return','返回')]:
            events=[e for e in recent if e['kind']==kind]
            if events:text+=f"当前可比片段最近{label}邻域：{when(events[-1]['at_ms'])}。"
        if not recent:text+='当前可比片段没有可确认的邻域离返事件。'
        elif recent[-1]['kind']=='leave':text+='最近离开后，本可比片段采样未见返回。'
        if last:text+=f"末个有效采样在邻域{'内' if last['inside'] else '外'}。"
    else:
        text+='缺窗前可冻结的原半宽，不计算停留率或邻域离返。'
    relations={
        'repeated_returns':'同一已知Pin片段观察到反复离开后返回，不证明吸附。',
        'departed_then_returned':'同一已知Pin片段观察到偏离后返回，不保证仍在围绕。',
        'departed_again':'曾返回，但最近又离开，不能把旧回收当作仍在围绕。',
        'sampled_distance_increasing':'可比分钟采样中的相对距离持续扩大，不预测随后方向。',
        'same_direction_endpoint_changes':'价格与锚端点同向变化，不能证明Pin驱动价格。',
        'anchor_changed':'锚本身有变化，不能按固定锚解释整窗迁离。',
        'endpoint_only':'仅作端点比较，不据此声称持续迁离或吸附。',
        'coverage_or_method_limited':'覆盖或历史口径凭据不足，不判定整窗持续迁离、回归或共同迁移。'}
    text+=relations[facts['relation']]
    if current_pin is not None and obs[-1]['pin']!=current_pin:
        text+='历史末次Pin与正文当前Pin不同，不能合成同一当前关系。'
    if disclose_backfill and facts.get('legacy_clock_backfill'):
        text+='（含历史回填可用时钟，非原生可得收据）'
    return head+text
