"""Verify frozen sources without silently replacing a research revision."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
from astra_joint_sources import request, digest, save


def verify_sources(root):
    root = Path(root)
    manifest = json.loads((root / 'raw/binance/manifest.json').read_text('utf-8'))
    def check(meta):
        path = root / 'raw/binance' / meta['feed'] / meta['frequency'] / ('BTCUSDT-1m-' + meta['period'] + '.zip')
        result = {'feed': meta['feed'], 'period': meta['period'], 'frozen_sha256': meta['sha256']}
        result['local_ok'] = digest(path) == meta['sha256']
        try:
            current = request(meta['url'] + '.CHECKSUM').decode().split()[0].lower()
            result.update(current_official_sha256=current, same_revision=current == meta['sha256'])
        except Exception as exc:
            result.update(same_revision=None, error_type=type(exc).__name__)
        return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(check, manifest['results']))
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'results': results,
              'local_invalid': sum(not r['local_ok'] for r in results),
              'official_changed': sum(r['same_revision'] is False for r in results),
              'official_unavailable': sum(r['same_revision'] is None for r in results),
              'policy': 'Frozen input remains immutable; a revised official archive requires a separate research revision.'}
    target = root / 'validation' / ('official-source-check-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '.json')
    save(target, report, True)
    return {k: v for k, v in report.items() if k != 'results'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    print(json.dumps(verify_sources(parser.parse_args().root)), flush=True)
