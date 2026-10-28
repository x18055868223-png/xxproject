import json
import os
import threading

import pytest

from tools import astra_data_engine_v23 as de
from tools.astra_data_engine_v23 import DataEngine, DataEngineError


T = 1_790_323_200_000
MU = "risk.astra.frozen_mu.reference.v1"
INDEX = "deribit.btc.index.v1"


def _engine(tmp_path):
    return DataEngine(tmp_path / "state")


def _ingest(e, n, scope=None):
    return e.ingest(MU, {"mu_btc": n, "model_hash": "a" * 64}, observation_end_ms=T + n,
                    retrieved_at_ms=T + n, scope=scope or {"candidate_id": "c"})


def _read(e, scope=None):
    return e.read(MU, now_ms=T + 10_000, usage="underwriting_accounting", scope=scope or {"candidate_id": "c"})


@pytest.fixture
def count_parses(monkeypatch):
    calls = {"n": 0}
    real = json.loads

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(de.json, "loads", counting)
    return calls


def test_cache_hit_avoids_reparsing_the_state_file(tmp_path, count_parses):
    e = _engine(tmp_path)
    _ingest(e, 1)
    count_parses["n"] = 0
    for _ in range(20):
        assert _read(e)["content"]["values"]["mu_btc"] == 1
    e.health(T + 10_000)
    _ingest(e, 2)  # mutation after our own save also reuses the cache
    assert _read(e)["content"]["values"]["mu_btc"] == 2
    assert count_parses["n"] == 0


def test_external_replace_and_in_place_rewrite_are_detected(tmp_path, count_parses):
    e = _engine(tmp_path)
    _ingest(e, 1)
    path = e.state_path
    state = json.loads(path.read_text(encoding="utf-8"))
    key = next(iter(state["current"]))
    # Another process replacing the file atomically (new inode).
    state["current"][key]["content"]["values"]["mu_btc"] = 7
    tmp = path.with_name("other.tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, path)
    count_parses["n"] = 0
    assert _read(e)["content"]["values"]["mu_btc"] == 7
    assert count_parses["n"] == 1
    # In-place rewrite with a different size keeps the inode but changes size/mtime.
    state["current"][key]["content"]["values"]["mu_btc"] = 12345
    path.write_text(json.dumps(state, indent=1), encoding="utf-8")
    assert _read(e)["content"]["values"]["mu_btc"] == 12345
    # A second engine (e.g. another component in this process) sees the same data.
    assert _read(_engine(tmp_path))["content"]["values"]["mu_btc"] == 12345


def test_deleted_or_corrupt_state_falls_back_to_empty(tmp_path):
    e = _engine(tmp_path)
    _ingest(e, 1)
    e.state_path.write_text("{not json", encoding="utf-8")
    assert "record_id" not in _read(e)
    e.state_path.unlink()
    assert "record_id" not in _read(e)


def test_returned_records_do_not_alias_the_cache(tmp_path):
    e = _engine(tmp_path)
    _ingest(e, 1)
    first = _read(e)
    first["content"]["values"]["mu_btc"] = 999
    first["time"]["observation_end_ms"] = 0
    health = e.health(T + 10_000)
    health["recent_events"].clear()
    assert _read(e)["content"]["values"]["mu_btc"] == 1
    assert e.health(T + 10_000)["recent_events"]


def test_failed_atomic_save_keeps_disk_and_cache_unchanged(tmp_path, monkeypatch):
    e = _engine(tmp_path)
    _ingest(e, 1)
    before = e.state_path.read_bytes()

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(de.os, "replace", boom)
    with pytest.raises(OSError):
        _ingest(e, 2)
    monkeypatch.undo()
    assert e.state_path.read_bytes() == before
    assert _read(e)["content"]["values"]["mu_btc"] == 1
    assert not list(e.state_path.parent.glob("*.lock"))


def test_rejected_multi_reserve_does_not_leak_into_cached_state(tmp_path):
    e = _engine(tmp_path)
    limit = e._product_budget_limit(e._product(INDEX))
    e.reserve_request(INDEX, now_ms=T, count=limit - 1)
    with pytest.raises(DataEngineError):
        e.reserve_request(INDEX, now_ms=T, count=3)  # all-or-none
    e.reserve_request(INDEX, now_ms=T, count=1)  # exactly one slot was left
    with pytest.raises(DataEngineError):
        e.reserve_request(INDEX, now_ms=T, count=1)


def test_cache_miss_fetch_saves_twice_and_marks_success(tmp_path, monkeypatch):
    e = _engine(tmp_path)
    writes = {"n": 0}
    real = de._write_json_atomic

    def counting(*args, **kwargs):
        writes["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(de, "_write_json_atomic", counting)
    rec = e.fetch(INDEX, lambda: {"values": {"index_price": 100_000.0}, "observation_end_ms": T,
                                  "retrieved_at_ms": T + 5}, now_ms=T, scope={"index": "btc_usd"})
    assert rec["fetch_decision"]["status"] == "FETCHED"
    assert writes["n"] == 2  # budget reservation + record with cleared fetch error
    status = json.loads(e.state_path.read_text(encoding="utf-8"))["fetch_status"]
    assert status[de._state_key(INDEX, {"index": "btc_usd"})] == {
        "attempts": 0, "next_retry_at_ms": None, "last_success_at_ms": T + 5}
    hit = e.fetch(INDEX, lambda: pytest.fail("loader called on hit"), now_ms=T + 1000, scope={"index": "btc_usd"})
    assert hit["fetch_decision"]["status"] == "CACHE_HIT"
    assert writes["n"] == 2


def test_concurrent_threads_and_engines_lose_no_updates(tmp_path):
    engines = [_engine(tmp_path) for _ in range(3)]
    errors = []

    def worker(idx):
        try:
            e = engines[idx % len(engines)]
            for n in range(10):
                _ingest(e, n + 1, scope={"candidate_id": f"t{idx}-{n}"})
                _read(e, scope={"candidate_id": f"t{idx}-{n}"})
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    on_disk = json.loads(engines[0].state_path.read_text(encoding="utf-8"))
    assert len([k for k in on_disk["current"] if k.startswith(MU + "|")]) == 60
    de._STATE_CACHE.clear()  # a fresh process sees the same thing
    assert _read(_engine(tmp_path), scope={"candidate_id": "t5-9"})["content"]["values"]["mu_btc"] == 10
