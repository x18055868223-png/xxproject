"""Observation RC: bounded reads, exact evidence and local failure boundaries."""
from copy import deepcopy
import json
import pytest
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from tools import astra_workbench_reading_v1 as reading
from tools import astra_locked_candidate_compare_v1 as compare
from tools import astra_workbench_v20 as workbench
from test_astra_locked_followup import setup, lock


def test_compressed_inputs_are_bounded_and_not_mutable_aliases():
    cache = reading.CompressedPoints(max_items=3, max_bytes=4096)
    for index in range(30):
        cache[str(index)] = {"source": "large same input" * 1000, "index": index}
    assert len(cache) == 3 and cache.stored_bytes < 4096
    value = cache["29"]
    value["index"] = -1
    assert cache["29"]["index"] == 29
    assert all(isinstance(value, bytes) for value in cache.entries.values())


def test_reading_projection_retains_facts_clocks_and_nulls_without_source_bodies():
    source = {"candidates": [{"snapshot": {"snapshot_id": "frozen", "economics": {"mu_ref_btc": None},
        "data_identity": {"source_records": [{"record_id": "r", "identity": {"product_id": "p"},
        "content": {"large": "raw"}, "time": {"observed_at_ms": 123}, "usage_qualification": {"can_use": False}}]}}}],
        "daily_snapshots": [], "btc_map_projection": {"large": "raw"}}
    original = deepcopy(source)
    result = reading.workbench_reading(source)
    assert source == original
    row = result["candidates"][0]["snapshot"]["data_identity"]["source_records"][0]
    assert row["time"] == {"observed_at_ms": 123} and not row["usage_qualification"]["can_use"]
    assert row["content_deferred"] and "content" not in row
    assert result["candidates"][0]["snapshot"]["economics"]["mu_ref_btc"] is None


@pytest.mark.parametrize("receipt", [
    {"status": "ERROR", "snapshot_id": "same"},
    {"status": "rejected", "snapshot_id": "same"},
    {"status": "attached", "reviewed_snapshot_id": "different"},
    {"status": "OK", "snapshot_id": "same", "review": {"status": "ERROR"}},
])
def test_non_current_receipts_do_not_inflate_full_proof(tmp_path, monkeypatch, receipt):
    wb, _, _, _ = setup(tmp_path)
    wb._wpem_display_packages["same"] = {"evidence_hash": "small-descriptor"}
    monkeypatch.setattr(wb._wpem_packages, "get", lambda *a: pytest.fail("unnecessary full proof"))
    assert not workbench._receipt_needs_full_proof("same", receipt)
    assert wb._display_wpem_package("same", {"last_result": receipt}, [], receipt) == {"evidence_hash": "small-descriptor"}


def test_same_snapshot_accepted_receipt_still_requires_full_proof(tmp_path):
    wb, _, _, _ = setup(tmp_path)
    wb._wpem_packages["same"] = {"composition_inputs": ["exact retained inputs"]}
    receipt = {"status": "OK", "reviewed_snapshot_id": "same"}
    assert workbench._receipt_needs_full_proof("same", receipt)
    assert wb._display_wpem_package("same", {"last_result": receipt}, [], receipt)["composition_inputs"]


def test_thin_and_exact_evidence_have_same_rows_and_selected_identities(tmp_path):
    wb, _, _, snapshot = setup(tmp_path)
    episode = lock(wb, snapshot)["episode"]
    full = wb.locked_candidate_detail(episode["episode_id"])
    thin = wb.locked_candidate_detail(episode["episode_id"], include_raw=False)
    assert thin["baseline"]["point_id"] == full["baseline"]["point_id"]
    assert thin["latest"]["snapshot_id"] == full["latest"]["snapshot_id"]
    for left, right in zip(thin["rows"], full["rows"]):
        for key in ("key", "t0", "t1", "change_cn", "meaning_cn", "status"):
            assert left[key] == right[key]
    assert "public_packet" not in thin["baseline"]
    assert len(json.dumps(thin)) < len(json.dumps(full))
    exact = wb.locked_candidate_detail(episode["episode_id"], baseline_point_id=thin["baseline"]["point_id"],
                                      latest_point_id=thin["latest"]["point_id"])
    assert exact["baseline"] == full["baseline"]


def test_30_read_rounds_use_cached_projection_without_replaying_or_writing(tmp_path, monkeypatch):
    wb, _, clock, snapshot = setup(tmp_path)
    episode = lock(wb, snapshot)["episode"]
    first = wb.locked_candidate_detail(episode["episode_id"], include_raw=False)
    before = wb.locked_candidates.index_path.read_bytes()
    monkeypatch.setattr(wb.locked_candidates, "detail", lambda *a, **kw: pytest.fail("repeated full point read"))
    monkeypatch.setattr(wb, "_build_btc_map_projection", lambda *a, **kw: pytest.fail("GET rebuilds full MAP"))
    monkeypatch.setattr(wb, "quote_capture", lambda *a, **kw: pytest.fail("GET collects quotes"))
    for _ in range(30):
        clock[0] += 1000
        detail = wb.locked_candidate_detail(episode["episode_id"], include_raw=False)
        assert detail["baseline"] == first["baseline"]
        assert detail["latest"] == first["latest"]
        assert wb.cached_view(reading_only=True)["llm_call_count"] == 0
    assert wb.locked_candidates.index_path.read_bytes() == before
    assert len(wb._locked_detail_cache) == 1


def test_reading_detail_defers_row_raw_to_the_evidence_endpoint(tmp_path):
    wb, _, _, snapshot = setup(tmp_path)
    episode = lock(wb, snapshot)["episode"]
    reading = wb.locked_candidate_detail(episode["episode_id"], include_raw=False)
    assert reading["rows"] and all("raw" not in row and row["raw_deferred"] for row in reading["rows"])
    assert all("t0" in row and "label_cn" in row for row in reading["rows"])
    exact = wb.locked_candidate_detail(episode["episode_id"], include_raw=True,
                                       baseline_point_id=reading["baseline"]["point_id"],
                                       latest_point_id=reading["latest"]["point_id"])
    assert any("raw" in row for row in exact["rows"])


def test_cache_does_not_hide_a_changed_point_file(tmp_path):
    wb, _, _, snapshot = setup(tmp_path)
    episode = lock(wb, snapshot)["episode"]
    wb.locked_candidate_detail(episode["episode_id"], include_raw=False)
    wb.locked_candidates._point_path(episode["t0_baseline_point_id"]).write_bytes(b"broken")
    with pytest.raises(workbench.WorkbenchError):
        wb.locked_candidate_detail(episode["episode_id"], include_raw=False)


@pytest.mark.parametrize("delta,direction", [(0.0000000000001, "微增"), (-0.0000000000001, "微降")])
def test_tiny_percent_change_is_not_unchanged(delta, direction):
    meta = dict(semantic_key="d", method="m", product="p", source="s", window="w", unit="pct")
    a = {"value": 0.0, "unit": "pct", "asof_ms": 1, "meta": meta}
    b = {**a, "value": delta, "asof_ms": 2}
    text, state = compare._change_cn(a, b)
    assert state == "changed" and direction in text
    assert "-0" not in compare._format_value(delta, "pct")


def test_http_private_session_exact_evidence_and_no_cross_site_writes(tmp_path):
    wb, _, clock, snapshot = setup(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), workbench.make_handler(wb))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    def call(path, *, method="GET", payload=None, headers=None):
        request = Request(origin + path, data=json.dumps(payload).encode() if payload is not None else None,
                          method=method, headers=headers or {})
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, dict(response.headers), json.load(response)
    try:
        status, headers, body = call("/api/workbench/session", method="POST", headers={"Authorization": "Bearer secret", "Origin": origin})
        assert status == 200 and body["authenticated"] and "secret" not in json.dumps(body)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        assert "HttpOnly" in headers["Set-Cookie"] and "SameSite=Strict" in headers["Set-Cookie"]
        assert call("/api/workbench/session", headers={"Cookie": cookie})[2]["authenticated"]
        # The public proxy gates reads on this check: a live session passes, anything else is 401 with no body.
        def check(headers):
            try:
                with urlopen(Request(origin + "/api/workbench/session/check", headers=headers), timeout=5) as response:
                    return response.status, response.read()
            except HTTPError as error:
                return error.code, error.read()
        assert check({"Cookie": cookie}) == (204, b"")
        assert check({}) == (401, b"")
        assert check({"Cookie": "astra_workbench_session=forged"}) == (401, b"")
        payload = {"candidate_id": snapshot["candidate_id"], "snapshot_id": snapshot["snapshot_id"], "idempotency_key": "session-lock"}
        assert call("/api/workbench/locked-candidates", method="POST", payload=payload,
                    headers={"Cookie": cookie, "Origin": "https://different.example"})[0] == 403
        assert wb.locked_candidates.list() == []
        status, _, result = call("/api/workbench/locked-candidates", method="POST", payload=payload,
                                 headers={"Cookie": cookie, "Origin": origin})
        assert status == 200 and result["created"]
        episode_id = result["episode"]["episode_id"]
        status, _, thin = call("/api/workbench/locked-candidates/" + episode_id)
        assert status == 200 and "public_candidate" not in thin["baseline"]
        url = f"/api/workbench/locked-candidates/{episode_id}/evidence"
        assert call(url)[0] == 400
        exact = call(url + f'?baseline_point_id={thin["baseline"]["point_id"]}&latest_point_id={thin["latest"]["point_id"]}')[2]
        assert exact["baseline"]["snapshot_id"] == snapshot["snapshot_id"]
        assert exact["baseline"]["public_packet"]["packet_hash"]
        status, _, _ = call("/api/workbench/session", method="DELETE", headers={"Cookie": cookie, "Origin": origin})
        assert status == 200
        assert not call("/api/workbench/session", headers={"Cookie": cookie})[2]["authenticated"]
        assert check({"Cookie": cookie})[0] == 401
        assert call("/api/workbench/locked-candidates", method="POST", payload=payload,
                    headers={"Cookie": cookie, "Origin": origin})[0] == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
