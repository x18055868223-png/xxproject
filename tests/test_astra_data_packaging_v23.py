"""Verify the actual flat package imports without a repository beside it."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_data_only_flat_import_and_real_registry(tmp_path):
    modules = ('astra_data_server_v23', 'astra_data_engine_v23', 'astra_data_projection_v23',
               'astra_data_retention_v23', 'astra_underwriting_fact_context_v20',
               'astra_underwriting_v20', 'astra_commercial_math_v17')
    for name in modules:
        shutil.copyfile(ROOT / 'tools' / (name + '.py'), tmp_path / (name + '.py'))
    shutil.copyfile(ROOT / 'demo/shared/data_product_registry_v23.json',
                    tmp_path / 'data_product_registry_v23.json')
    script = ('import astra_data_server_v23; from astra_data_engine_v23 import DataEngine; '
              'e=DataEngine("state"); import json; '
              'print(json.dumps({"products":len(e.health(1790390000000)["products"])}))')
    result = subprocess.run([sys.executable, '-I', '-c',
                             'import sys; sys.path.insert(0,sys.argv[1]); ' + script, str(tmp_path)],
                            cwd=tmp_path, capture_output=True, text=True, check=True)
    registry = json.loads((ROOT / 'demo/shared/data_product_registry_v23.json').read_text(encoding='utf-8'))
    assert json.loads(result.stdout)['products'] == len(registry['products'])


def test_data_maintenance_unit_targets_only_its_receiver():
    service = (ROOT / 'deploy/signal_audit/data-engine-maintenance.service').read_text(encoding='utf-8')
    timer = (ROOT / 'deploy/signal_audit/data-engine-maintenance.timer').read_text(encoding='utf-8')
    assert 'User=bitnami' in service
    assert '/tools/astra_data_server_v23.py' in service
    assert 'Unit=astra-data-engine-maintenance.service' in timer
    assert 'OnUnitActiveSec=300' in timer
