"""Descriptive width and non-event controls; no fitting or model selection."""
from __future__ import annotations
import argparse
from collections import defaultdict
import json
from pathlib import Path
from statistics import fmean

from astra_joint_pipeline import joined_rows
from astra_joint_sources import save, digest


def by_day(values):
    days = defaultdict(list)
    for day, value in values:
        days[day].append(value)
    return {day: fmean(items) for day, items in days.items()}


def summarize(rows):
    def mean(key):
        return fmean(by_day((r['delivery_date'], key(r)) for r in rows).values()) if rows else None
    return {'side_rows': len(rows), 'observations': len({r['observation_id'] for r in rows}),
            'delivery_dates': len({r['delivery_date'] for r in rows}),
            'mean_dte_hours': mean(lambda r: r['dte_hours']),
            'mean_actual_width': mean(lambda r: r['actual_width']),
            'mean_payout_btc': mean(lambda r: r['payout_btc']),
            'mean_loss_normalized': mean(lambda r: r['loss_normalized']),
            'credit_scenarios': {str(c): {
                'net_win_rate': mean(lambda r: float(c * r['actual_width'] / r['entry_price'] - r['payout_btc'] > 1e-12)),
                'mean_net_btc': mean(lambda r: c * r['actual_width'] / r['entry_price'] - r['payout_btc'])
            } for c in (.05, .10, .20)}}


def is_non_event_clock(row):
    return row['observation_kind'] == 'clock' and str(row.get('in_episode', '')).lower() == 'false'


def compare(rows):
    groups = defaultdict(list)
    controls = defaultdict(list)
    for row in rows:
        state = 'clock_outside_episode' if is_non_event_clock(row) else row['observation_kind']
        groups[(int(row['target_width']), state, row['side'])].append(row)
        if is_non_event_clock(row) and row['target_width'] == 2000:
            controls[(row['delivery_date'], row['side'], row['actual_width'])].append(row)
    matched, missing = [], defaultdict(int)
    for row in rows:
        if row['target_width'] != 2000 or row['observation_kind'] == 'clock':
            continue
        choices = controls.get((row['delivery_date'], row['side'], row['actual_width']), [])
        if not choices:
            missing[row['observation_kind']] += 1
            continue
        control = min(choices, key=lambda r: (abs(r['dte_hours'] - row['dte_hours']), r['as_of_ms']))
        matched.append({'event_row_id': row['row_id'], 'control_row_id': control['row_id'],
                        'delivery_date': row['delivery_date'], 'kind': row['observation_kind'], 'side': row['side'],
                        'dte_gap_hours': row['dte_hours'] - control['dte_hours'],
                        'short_strike_gap': row['short_strike'] - control['short_strike'],
                        'payout_saving_btc': control['payout_btc'] - row['payout_btc']})
    matched_groups = defaultdict(list)
    for row in matched:
        matched_groups[(row['kind'], row['side'])].append(row)
    return {'schema': 'astra_joint_descriptive_benchmarks@1.0.0',
            'scope': 'Supplementary description, not a new confirmatory test. Outcomes were already opened. No fitting, threshold search or tuning.',
            'weighting': 'Each delivery date equal; repeated rows and reused controls are not independent.',
            'control_rule': 'Outside-episode clock only; same delivery date, side and actual width; nearest DTE, tie earlier clock. No outcome used in matching. Residual DTE and strike gaps remain.',
            'quote_boundary': 'All credit scenarios are assumptions, not historical executable credit. No net-edge acceptance.',
            'width_and_phase': [{'target_width': k[0], 'kind': k[1], 'side': k[2], **summarize(v)} for k, v in sorted(groups.items())],
            'matched_control': [{'kind': k[0], 'side': k[1], 'pairs': len(v),
                                 'distinct_controls': len({r['control_row_id'] for r in v}),
                                 'delivery_dates': len({r['delivery_date'] for r in v}),
                                 'mean_abs_dte_gap_hours': fmean(by_day((r['delivery_date'], abs(r['dte_gap_hours'])) for r in v).values()),
                                 'mean_abs_short_strike_gap': fmean(by_day((r['delivery_date'], abs(r['short_strike_gap'])) for r in v).values()),
                                 'mean_payout_saving_btc': fmean(by_day((r['delivery_date'], r['payout_saving_btc']) for r in v).values())}
                                for k, v in sorted(matched_groups.items())],
            'unmatched_by_phase': dict(missing), 'matched_rows': matched}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    locked = args.root / 'models/locked_test_report.json'
    if not locked.exists():
        raise ValueError('Requires previously opened and sealed historical test report')
    result = compare(joined_rows(args.root, [2023], primary_only=False))
    result['locked_report_sha256'] = digest(locked)
    result['candidate_seal_sha256'] = digest(args.root / 'decisions/candidate_seal.json')
    save(args.root / 'models/descriptive_benchmarks.json', result, immutable=True)
    print(json.dumps({'groups': len(result['width_and_phase']), 'matched_rows': len(result['matched_rows']),
                      'unmatched': result['unmatched_by_phase']}))


if __name__ == '__main__':
    main()
