import hashlib
import json
from pathlib import Path
import tarfile

from deploy.astra_map_data.build_server_package import build


def test_small_reproducible_package_has_only_declared_data_surface(tmp_path):
    root=Path(__file__).resolve().parents[1]
    first=build(root,tmp_path/'first.tar.gz')
    second=build(root,tmp_path/'second.tar.gz')
    assert first['sha256']==second['sha256']
    assert first['bytes']<1024*1024
    with tarfile.open(first['package'],'r:gz') as archive:
        names=archive.getnames()
        assert not any('frontend' in name or 'neutral_regulation_demo_fmz' in name or '.artifacts' in name for name in names)
        assert not any(name.endswith('.env') or name.endswith('.zip') or name.endswith('.csv') for name in names)
        manifest=json.loads(archive.extractfile('PACKAGE_MANIFEST.json').read())
        for name,digest in manifest['files'].items():
            assert hashlib.sha256(archive.extractfile(name).read()).hexdigest()==digest
        assert len(names)==len(manifest['files'])+1
        assert 'tools/astra_map_data_server_v24.py' in names
        assert 'tools/astra_kpf_light_v1.py' in names
