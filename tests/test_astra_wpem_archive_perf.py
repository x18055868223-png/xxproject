"""WPEM research archive: budget short-circuit, replay caching, window cap, blob storage."""
import copy
import gzip
import json

import pytest

from tools import astra_candidate_asof_v1 as packets
from tools import astra_underwriting_store_v20 as store
from tools import astra_underwriting_v20 as core
from tools import astra_wpem_workbench_v1 as adapter
from tests.test_astra_candidate_asof_v1 import snapshot_at
from tests.test_astra_underwriting_v20 import T

STEP = packets.DEFAULT_INTERVAL_MS


def on_disk(path):
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def count_builds(monkeypatch):
    calls = []
    original = packets.build_packet

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(packets, "build_packet", counted)
    return calls


def big_btc_map(size=1_000_000):
    rows = [{"i": i, "label": "x" * 40, "v": i * 0.5} for i in range(size // 70)]
    return {"map_id": "synthetic-map", "source_manifest": {"source_records": [{"content": {"values": {"rows": rows}}}]}}


def with_big_map(monkeypatch, btc_map):
    """Inject the same ~1 MB btc_map into every built packet (rehashed, storage-only test)."""
    original = packets.build_packet

    def build(*args, **kwargs):
        packet = original(*args, **kwargs)
        packet["snapshot"]["data_identity"]["btc_map"] = copy.deepcopy(btc_map)
        packet.pop("packet_hash")
        packet["packet_hash"] = core._hash(packet)
        return packet
    monkeypatch.setattr(packets, "build_packet", build)


# 1. Exhausted budget: no packet is built --------------------------------------------------

def test_exhausted_byte_budget_builds_no_packet(tmp_path, monkeypatch):
    calls = count_builds(monkeypatch)
    (tmp_path / "native_sources").mkdir()
    (tmp_path / "native_sources" / "x.json").write_bytes(b"x" * 4096)
    archive = packets.CandidateResearchArchive(tmp_path, max_bytes=4096)
    snapshot, raw = snapshot_at()
    for offset in range(3):
        later, raw = snapshot_at(T + offset * STEP)
        result = archive.sample(later, recorded_at_ms=T + offset * STEP, raw_payload=raw)
        assert result == {"status": "stopped", "reason": "research_byte_budget_exhausted"}
    assert calls == [] and archive.index_rows() == []
    assert archive.usage()["reason"] == "research_byte_budget_exhausted"


def test_budget_found_short_after_one_build_never_builds_again(tmp_path, monkeypatch):
    calls = count_builds(monkeypatch)
    archive = packets.CandidateResearchArchive(tmp_path, max_bytes=2000)
    for offset in range(4):
        snapshot, raw = snapshot_at(T + offset * STEP)
        assert archive.sample(snapshot, recorded_at_ms=T + offset * STEP)["status"] == "stopped"
    assert len(calls) == 1
    assert not list(tmp_path.glob("*.gz"))


def test_exhausted_sample_budget_builds_no_packet(tmp_path, monkeypatch):
    archive = packets.CandidateResearchArchive(tmp_path, max_samples=1)
    first, _ = snapshot_at()
    assert archive.sample(first, recorded_at_ms=T)["status"] == "recorded"
    calls = count_builds(monkeypatch)
    for offset in (1, 2):
        later, _ = snapshot_at(T + offset * STEP)
        assert archive.sample(later, recorded_at_ms=T + offset * STEP)["reason"] == "research_sample_budget_exhausted"
    assert calls == []


# 2. Replay cache --------------------------------------------------------------------------

def test_replay_cache_hit_returns_equal_packet_without_rereading(tmp_path, monkeypatch):
    archive = packets.CandidateResearchArchive(tmp_path)
    snapshot, raw = snapshot_at()
    archive.sample(snapshot, recorded_at_ms=T, raw_payload=raw)
    first = archive.replay(snapshot["snapshot_id"])

    def no_read(*args, **kwargs):
        raise AssertionError("packet file re-read")
    monkeypatch.setattr(archive, "_read_packet_file", no_read)
    monkeypatch.setattr(store, "read_jsonl", no_read)  # index read once, reused while unchanged
    second = archive.replay(snapshot["snapshot_id"])
    assert second == first and second is not first
    second["snapshot"]["contract"]["side"] = "mutated"  # a fresh object: the cache is not shared
    assert archive.replay(snapshot["snapshot_id"]) == first


def test_replay_memo_only_skips_identical_content(monkeypatch):
    snapshot, raw = snapshot_at()
    packet = packets.build_packet(snapshot, raw_payload=raw)
    full = []
    original = packets._replay_packet_full
    monkeypatch.setattr(packets, "_replay_packet_full", lambda p: full.append(1) or original(p))
    expected = packets.replay_packet(packet)
    assert packets.replay_packet(json.loads(json.dumps(packet))) == expected
    assert len(full) <= 1  # memo may already hold this content from another test
    tampered = copy.deepcopy(packet)
    tampered["valuation"]["economics"]["visible_credit_btc"] = 100
    with pytest.raises(ValueError, match="hash_mismatch"):
        packets.replay_packet(tampered)
    tampered["packet_hash"] = core._hash({k: v for k, v in tampered.items() if k != "packet_hash"})
    with pytest.raises(ValueError, match="valuation_mismatch"):
        packets.replay_packet(tampered)


def test_index_rows_reread_after_external_append(tmp_path):
    archive = packets.CandidateResearchArchive(tmp_path)
    snapshot, _ = snapshot_at()
    archive.sample(snapshot, recorded_at_ms=T)
    assert len(archive.index_rows()) == 1
    with archive.index_path.open("ab") as handle:
        handle.write(b'{"partial":')
    with pytest.raises(ValueError, match="not valid JSON"):
        archive.index_rows()


# 3. Window row cap --------------------------------------------------------------------------

def test_window_row_limit_default_and_clamp(monkeypatch):
    monkeypatch.delenv("ASTRA_WPEM_MAX_WINDOW_ROWS", raising=False)
    assert adapter.window_row_limit(STEP) == 24  # unchanged composition by default
    assert packets.window_rows_needed(STEP) == 13
    monkeypatch.setenv("ASTRA_WPEM_MAX_WINDOW_ROWS", "13")
    assert adapter.window_row_limit(STEP) == 13
    monkeypatch.setenv("ASTRA_WPEM_MAX_WINDOW_ROWS", "5")
    assert adapter.window_row_limit(STEP) == 13  # never below what the 60-minute window needs
    rows = [{"candidate_id": "c", "decision_asof_ms": i * STEP} for i in range(30)]
    assert len(adapter._same_object_rows(rows, {"candidate_id": "c"}, 29 * STEP)) == 13


@pytest.mark.parametrize("jitter", [0, 7_000])
def test_capped_rows_keep_15_and_60_minute_window_semantics(jitter):
    current, _ = snapshot_at(T + 20 * STEP + jitter)
    comparison = packets.build_packet(current)["window"]["comparability_hash"]
    history = []
    for i in range(20):
        snapshot, _ = snapshot_at(T + i * STEP + (jitter if i % 2 else 0))
        history.append({"candidate_id": snapshot["candidate_id"], "snapshot_id": snapshot["snapshot_id"],
                        "decision_asof_ms": snapshot["market"]["valuation_ts_ms"], "comparability_hash": comparison})
    full = packets.build_packet(current, history=history)["window"]
    capped = packets.build_packet(current, history=history[-packets.window_rows_needed(STEP):])["window"]
    assert capped == full
    assert [w["status"] for w in full["coverage"]] == ["covered", "covered"]


def test_compose_replays_at_most_the_cap(tmp_path, monkeypatch):
    archive = packets.CandidateResearchArchive(tmp_path)
    snaps = []
    for i in range(16):
        snapshot, raw = snapshot_at(T + i * STEP)
        assert archive.sample(snapshot, recorded_at_ms=T + i * STEP, raw_payload=raw)["status"] == "recorded"
        snaps.append((snapshot, raw))
    replayed = []
    original = archive.replay
    monkeypatch.setattr(archive, "replay", lambda sid: replayed.append(sid) or original(sid))
    current, raw = snaps[-1]
    monkeypatch.delenv("ASTRA_WPEM_MAX_WINDOW_ROWS", raising=False)
    full = adapter.compose(current, archive, tmp_path / "missing.jsonl", raw_payload=raw)
    assert len(replayed) == 16
    replayed.clear()
    monkeypatch.setenv("ASTRA_WPEM_MAX_WINDOW_ROWS", "13")
    capped = adapter.compose(current, archive, tmp_path / "missing.jsonl", raw_payload=raw)
    assert len(replayed) == 13
    assert capped["packet_hash"] == full["packet_hash"]
    windows = lambda package: package["same_candidate_window"]["coverage_summary"]["windows"]
    assert windows(capped) == windows(full)
    # Nothing to cut -> identical evidence, hash included.
    monkeypatch.setenv("ASTRA_WPEM_MAX_WINDOW_ROWS", "20")
    assert adapter.compose(current, archive, tmp_path / "missing.jsonl", raw_payload=raw) == full


def test_compose_reuses_exactly_matching_current_packet(tmp_path, monkeypatch):
    archive = packets.CandidateResearchArchive(tmp_path, max_samples=1)
    first, _ = snapshot_at()
    archive.sample(first, recorded_at_ms=T)
    snapshot, raw = snapshot_at(T + STEP)  # will not be archived (sample budget)
    prebuilt = packets.build_packet(snapshot, raw_payload=raw)
    other = packets.build_packet(snapshot)  # different input (no raw payload): must be rebuilt, not reused
    expected = adapter.compose(snapshot, archive, tmp_path / "missing.jsonl", raw_payload=raw)
    calls = count_builds(monkeypatch)
    reused = adapter.compose(snapshot, archive, tmp_path / "missing.jsonl", raw_payload=raw, current_packet=prebuilt)
    assert calls == [] and reused == expected
    assert adapter.compose(snapshot, archive, tmp_path / "missing.jsonl", raw_payload=raw,
                           current_packet=other) == expected
    assert len(calls) == 1


# 4. Blob dedupe / compression ---------------------------------------------------------------

def test_btc_map_blob_stored_once_and_read_back_byte_identical(tmp_path, monkeypatch):
    btc_map = big_btc_map()
    assert len(store._canonical(btc_map)) > 900_000
    with_big_map(monkeypatch, btc_map)
    archive = packets.CandidateResearchArchive(tmp_path)
    built, deltas = [], []
    original = packets.build_packet
    monkeypatch.setattr(packets, "build_packet", lambda *a, **k: built.append(original(*a, **k)) or built[-1])
    for i in range(5):
        before = archive.used_bytes()
        snapshot, raw = snapshot_at(T + i * STEP)
        assert archive.sample(snapshot, recorded_at_ms=T + i * STEP, raw_payload=raw)["status"] == "recorded"
        deltas.append(archive.used_bytes() - before)
    # Only the first sample pays for the map; later samples add a small envelope.
    map_blobs = [path for path in tmp_path.glob("blob-*.json.gz")
                 if b"\"label\":\"xxxx" in gzip.decompress(path.read_bytes())]
    assert len(map_blobs) == 1
    assert max(deltas[1:]) * 3 < deltas[0]
    assert on_disk(tmp_path) < len(store._canonical(btc_map))  # 5 maps stored in less than one raw map
    # Budget counts each blob once and matches a full rescan.
    assert archive.used_bytes() == on_disk(tmp_path)
    assert packets.CandidateResearchArchive(tmp_path).used_bytes() == on_disk(tmp_path)
    for packet, row in zip(built, archive.index_rows()):
        restored = archive._read_packet_file(row["packet_file"])
        assert store._canonical(restored) == store._canonical(packet)
        assert restored["packet_hash"] == row["packet_hash"] == core._hash(
            {k: v for k, v in restored.items() if k != "packet_hash"})


def test_encode_decode_roundtrip_and_reserved_key():
    snapshot, raw = snapshot_at()
    packet = packets.build_packet(snapshot, raw_payload=raw)
    envelope, blobs = packets.encode_stored_packet(packet, min_blob_bytes=512)
    assert blobs and len(envelope) < len(store._canonical(packet))
    restored = packets.decode_stored_packet(json.loads(envelope), lambda d: json.loads(blobs[d]))
    assert store._canonical(restored) == store._canonical(packet)
    odd = dict(packet, extra={packets.BLOB_REF_KEY: "not-a-ref"})
    envelope, blobs = packets.encode_stored_packet(odd, min_blob_bytes=512)
    assert blobs == {}
    assert packets.decode_stored_packet(json.loads(envelope), None) == odd


def test_blob_tampering_is_detected(tmp_path, monkeypatch):
    monkeypatch.setattr(packets, "BLOB_MIN_BYTES", 512)
    archive = packets.CandidateResearchArchive(tmp_path)
    snapshot, raw = snapshot_at()
    archive.sample(snapshot, recorded_at_ms=T, raw_payload=raw)
    blob = sorted(tmp_path.glob("blob-*.json.gz"))[0]
    blob.write_bytes(gzip.compress(gzip.decompress(blob.read_bytes()).replace(b"1", b"2", 1)))
    with pytest.raises(ValueError, match="research_blob_hash_mismatch"):
        packets.CandidateResearchArchive(tmp_path).replay(snapshot["snapshot_id"])


# 5. Legacy uncompressed archive ---------------------------------------------------------------

def test_legacy_uncompressed_packet_remains_readable(tmp_path):
    archive = packets.CandidateResearchArchive(tmp_path)
    snapshot, raw = snapshot_at()
    archive.sample(snapshot, recorded_at_ms=T, raw_payload=raw)
    row = archive.index_rows()[0]
    packet = archive._read_packet_file(row["packet_file"])
    # Rewrite as the pre-blob layout: <hash>.json canonical bytes and a .json index entry.
    for path in tmp_path.glob("*.gz"):
        path.unlink()
    (tmp_path / (row["packet_hash"] + ".json")).write_bytes((store._canonical(packet) + "\n").encode("utf-8"))
    legacy = {k: v for k, v in row.items() if k != "entry_hash"}
    legacy["packet_file"] = row["packet_hash"] + ".json"
    legacy["entry_hash"] = core._hash(legacy)
    archive.index_path.write_bytes((store._canonical(legacy) + "\n").encode("utf-8"))
    reopened = packets.CandidateResearchArchive(tmp_path)
    assert reopened.replay(snapshot["snapshot_id"]) == packet
    assert reopened.used_bytes() == on_disk(tmp_path)
    # New samples append in the new layout next to legacy ones.
    later, raw = snapshot_at(T + STEP)
    assert reopened.sample(later, recorded_at_ms=T + STEP, raw_payload=raw)["status"] == "recorded"
    assert reopened.index_rows()[-1]["packet_file"].endswith(".json.gz")
    assert reopened.replay(later["snapshot_id"])["window"]["coverage"][0]["sample_count"] == 2


def test_native_source_copy_is_written_within_running_budget(tmp_path):
    archive = packets.CandidateResearchArchive(tmp_path)
    payload = b'{"source":"bytes"}'
    digest = __import__("hashlib").sha256(payload).hexdigest()
    target = tmp_path / "native_sources" / (digest + ".json.gz")
    assert archive.write_within_budget(target, gzip.compress(payload, mtime=0))
    assert gzip.decompress(target.read_bytes()) == payload
    assert archive.used_bytes() == on_disk(tmp_path)
    small = packets.CandidateResearchArchive(tmp_path / "small", max_bytes=10)
    assert not small.write_within_budget(tmp_path / "small" / "native_sources" / "x.json.gz", b"x" * 64)
