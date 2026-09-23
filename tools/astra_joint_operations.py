"""Bounded, read-only market operations for the separately approved shadow run."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from astra_joint_shadow import Ledger, _fetch_once, public_get, now_ms, settle_decision


def settle_due(folder, *, clock=now_ms, transport=public_get):
    ledger = Ledger(folder); at = int(clock()); pending = []
    for (identifier, payload) in ledger.db.execute(
            'SELECT d.id,d.payload FROM decisions d LEFT JOIN outcomes o ON d.id=o.id WHERE o.id IS NULL'):
        row = json.loads(payload)
        expiries = {int(s['reference']['expiry_ms']) for s in row['assessment'].get('sides', {}).values() if s.get('reference')}
        if len(expiries) == 1 and next(iter(expiries)) <= at:
            pending.append((identifier, next(iter(expiries))))
    from astra_joint_v11_forward import _connection, settle as settle_natural
    natural_db = _connection(folder)
    natural_pending = []
    for (payload,) in natural_db.execute("SELECT c.payload FROM choices c JOIN quotes q ON q.id=c.id LEFT JOIN outcomes o ON c.id=o.id WHERE o.id IS NULL AND q.status='complete' AND q.payload IS NOT NULL"):
        c = json.loads(payload)
        expiries = [s['reference']['expiry_ms'] for s in c.get('sides', {}).values() if s.get('reference')]
        if expiries and max(expiries) <= at:
            natural_pending.append(c['card_id'])
    natural_db.close()
    if not pending and not natural_pending:
        return {'http_attempts': 0, 'settled': 0}
    # The last 100 delivery dates cover this fixed 90-day protocol. A long
    # outage remains an explicit gap; it does not trigger unbounded pagination.
    attempted, payload, meta, error = _fetch_once(
        ledger, Path(folder)/'raw', 'deribit_delivery_prices', at//60000,
        'https://www.deribit.com/api/v2/public/get_delivery_prices',
        {'index_name':'btc_usd','count':100,'offset':0}, transport, at)
    if not payload or payload.get('error'):
        return {'http_attempts':int(attempted), 'settled':0, 'error':error or 'official_delivery_unavailable'}
    prices = {r['date']:r['delivery_price'] for r in payload.get('result',{}).get('data',[])}
    settled = 0; missing = 0
    for identifier, expiry in pending:
        day = datetime.fromtimestamp(expiry/1000, timezone.utc).strftime('%Y-%m-%d')
        price = prices.get(day)
        if not isinstance(price,(int,float)) or price <= 0:
            missing += 1; continue
        settle_decision(folder, identifier, price, recorded_at_ms=at,
                        source_provenance={'kind':'official_deribit_delivery', 'date':day, 'response':meta})
        settled += 1
    natural_settled = settle_natural(folder, prices, at,
        {'kind':'official_deribit_delivery', 'response':meta}) if natural_pending else 0
    return {'http_attempts':int(attempted), 'settled':settled, 'natural_settled':natural_settled,
        'missing_official_delivery':missing + len(natural_pending) - natural_settled}


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--folder',type=Path,required=True)
    print(json.dumps(settle_due(parser.parse_args().folder)),flush=True)
