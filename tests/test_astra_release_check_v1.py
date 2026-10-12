"""The release check passes only when deliverable, manifest and server agree."""
import hashlib
import json

from tools import astra_release_check_v1 as rc


def write(tmp_path, version="2.0.6", manifest_version="2.0.6", manifest_hash=None):
    deliverable = tmp_path / "fmz.py"
    deliverable.write_bytes(b'CONFIG = {\r\n    "demo_version": "%s",\n}\n' % version.encode())
    digest = hashlib.sha256(deliverable.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"components": [{"component": "fmz_producer", "candidate_version": manifest_version,
                                                    "source_hash": manifest_hash or digest}]}), encoding="utf-8")
    return deliverable, manifest


def test_all_layers_agree(tmp_path):
    deliverable, manifest = write(tmp_path)
    root = tmp_path / "root.py"
    root.write_bytes(deliverable.read_bytes())
    out = rc.check(deliverable=deliverable, manifest=manifest, view={"market_context": {"strategy_version": "2.0.6"}},
                   root_deliverable=root)
    assert out["ok"] and out["version"] == "2.0.6" and len(out["checks"]) == 5


def test_each_mismatch_fails(tmp_path):
    deliverable, manifest = write(tmp_path, manifest_version="2.0.5")
    assert not rc.check(deliverable=deliverable, manifest=manifest)["ok"]
    deliverable, manifest = write(tmp_path, manifest_hash="0" * 64)
    assert not rc.check(deliverable=deliverable, manifest=manifest)["ok"]
    deliverable, manifest = write(tmp_path)
    assert not rc.check(deliverable=deliverable, manifest=manifest,
                        view={"market_context": {"strategy_version": "2.0.5"}})["ok"]
    stale_root = tmp_path / "stale.py"
    stale_root.write_bytes(b"old")
    assert not rc.check(deliverable=deliverable, manifest=manifest, root_deliverable=stale_root)["ok"]


def test_repository_state_is_coherent_now():
    out = rc.check()
    assert out["ok"], out["checks"]
