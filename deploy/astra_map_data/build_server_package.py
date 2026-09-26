"""Build the explicit, standard-library-only data deployment surface."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

MODULES = [
    'astra_data_engine_v23.py', 'astra_data_projection_v23.py',
    'astra_underwriting_fact_context_v20.py', 'astra_underwriting_v20.py',
    'astra_commercial_math_v17.py', 'astra_map_sources_v24.py',
    'astra_map_projection_v24.py', 'astra_map_v24.py',
    'astra_map_presentation_v241.py', 'astra_map_data_server_v24.py', 'astra_kpf_light_v1.py',
]
DEPLOY_FILES = [
    'deploy/astra_map_data/config.example.json', 'deploy/astra_map_data/astra-map-data.service',
    'deploy/astra_map_data/astra-map-data.timer', 'deploy/astra_map_data/install_server_data.sh',
    'deploy/astra_map_data/install_map_data.py', 'deploy/astra_map_data/build_server_package.py',
    'deploy/astra_map_data/README.md', 'deploy/kpf_light/README.md',
    'deploy/kpf_light/source_manifest.json', 'deploy/kpf_light/astra-kpf-light.env.example',
    'deploy/kpf_light/install_kpf_light.sh', 'deploy/kpf_light/uninstall_kpf_light.sh',
    'deploy/kpf_light/rollback_kpf_light.sh',
    'deploy/kpf_light/systemd/astra-kpf-light.service', 'deploy/kpf_light/systemd/astra-kpf-light.timer',
]


def build(root: Path, output: Path, source_commit: str | None = None) -> dict:
    paths = [root/'tools'/name for name in MODULES]
    paths += [root/'demo/shared/data_product_registry_v23.json']
    paths += [root/name for name in DEPLOY_FILES]
    vendor_manifest=json.loads((root/'deploy/kpf_light/source_manifest.json').read_text(encoding='utf-8-sig'))
    for entry in vendor_manifest['files']:
        relative=Path(entry['path'])
        if relative.is_absolute() or '..' in relative.parts or not relative.as_posix().startswith('deploy/kpf_light/vendor/kpf/'):
            raise ValueError('INVALID_VENDOR_PACKAGE_PATH')
        paths.append(root/relative)
    if any(path.is_symlink() for path in paths):
        raise ValueError('SYMLINK_PACKAGE_SOURCE_REJECTED')
    paths = sorted(set(paths))
    content = {}
    for path in paths:
        name=path.relative_to(root).as_posix()
        raw=path.read_bytes()
        # Match Git's LF deployment surface. Vendored algorithms are retained
        # byte-for-byte because their original source hashes are the contract.
        content[name]=raw if name.startswith('deploy/kpf_light/vendor/') else raw.replace(b'\r\n',b'\n')
    if source_commit is not None and (len(source_commit)!=40 or any(char not in '0123456789abcdef' for char in source_commit)):
        raise ValueError('INVALID_SOURCE_COMMIT')
    manifest = {'schema': 'astra_server_data_package@1', 'source_commit':source_commit,
                'files': {name: hashlib.sha256(raw).hexdigest() for name,raw in content.items()},
                'no_frontend': True, 'no_fmz_program': True, 'no_llm_credentials': True}
    content['PACKAGE_MANIFEST.json'] = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2)+'\n').encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('wb') as stream, gzip.GzipFile(filename='', mode='wb', fileobj=stream, mtime=0) as zipped, tarfile.open(fileobj=zipped, mode='w') as archive:
        for name, raw in sorted(content.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(raw), (0o755 if name.endswith('.sh') else 0o644), 0
            archive.addfile(info, io.BytesIO(raw))
    return {'package': str(output), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'bytes': output.stat().st_size, 'file_count': len(content)}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-commit', help='Verified xxproject work-branch commit for a release package')
    args=parser.parse_args()
    print(json.dumps(build(args.root, args.output, args.source_commit), ensure_ascii=False))
