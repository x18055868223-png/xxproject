"""Install only the fixed MAP data namespace, with atomic release selection."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import sys

APP = Path('/opt/astra-map-data')
MODULES = [
    'astra_data_engine_v23.py', 'astra_data_projection_v23.py',
    'astra_underwriting_fact_context_v20.py', 'astra_underwriting_v20.py',
    'astra_commercial_math_v17.py', 'astra_map_sources_v24.py',
    'astra_map_projection_v24.py', 'astra_map_v24.py',
    'astra_map_presentation_v241.py', 'astra_map_data_server_v24.py',
]


def main(source: Path):
    manifest = json.loads((source / 'PACKAGE_MANIFEST.json').read_text())
    for name, expected in manifest['files'].items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('UNSAFE_PACKAGE_PATH')
        if hashlib.sha256((source / relative).read_bytes()).hexdigest() != expected:
            raise ValueError('PACKAGE_HASH_MISMATCH:' + name)
    release_id = hashlib.sha256(json.dumps(manifest['files'], sort_keys=True).encode()).hexdigest()[:20]
    releases = APP / 'releases'
    releases.mkdir(parents=True, exist_ok=True)
    target = releases / release_id
    target.mkdir(exist_ok=True)
    (target / 'tools').mkdir(exist_ok=True)
    for name in MODULES:
        shutil.copy2(source / 'tools' / name, target / 'tools' / name)
    shutil.copy2(source / 'demo/shared/data_product_registry_v23.json', target / 'tools/data_product_registry_v23.json')
    shutil.copy2(source / 'PACKAGE_MANIFEST.json', target / 'PACKAGE_MANIFEST.json')
    # Check the flat deployment imports before selecting the release. It has no
    # LLM credentials, UI files, option collector or execution permission.
    import subprocess
    subprocess.run([sys.executable, '-c', 'import astra_map_data_server_v24'], cwd=target/'tools', check=True)
    current = APP / 'current'
    old = current.resolve() if current.is_symlink() else None
    if old and old != target and old.parent == releases:
        temp = APP / 'previous.tmp'
        temp.unlink(missing_ok=True)
        temp.symlink_to(old)
        os.replace(temp, APP / 'previous')
    temp = APP / 'current.tmp'
    temp.unlink(missing_ok=True)
    temp.symlink_to(target)
    os.replace(temp, current)
    state = APP / 'state'
    state.mkdir(exist_ok=True)
    account = pwd.getpwnam('bitnami')
    os.chown(state, account.pw_uid, account.pw_gid)
    config = APP / 'config.json'
    if not config.exists():
        shutil.copy2(source / 'deploy/astra_map_data/config.example.json', config)
    for suffix in ('service', 'timer'):
        shutil.copy2(source / f'deploy/astra_map_data/astra-map-data.{suffix}', Path('/etc/systemd/system')/f'astra-map-data.{suffix}')
    # Only immutable releases created in this exact namespace can be pruned.
    keep = {target, old} | {(APP/'previous').resolve() if (APP/'previous').is_symlink() else None}
    for child in releases.iterdir():
        if child.is_dir() and not child.is_symlink() and child.resolve().parent == releases.resolve() and child not in keep:
            if len(child.name) == 20 and all(ch in '0123456789abcdef' for ch in child.name):
                shutil.rmtree(child)
    print(json.dumps({'namespace': str(APP), 'release': release_id, 'previous': str(old) if old else None}))


if __name__ == '__main__':
    main(Path(sys.argv[1]).resolve())
