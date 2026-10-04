import json
from tools import astra_data_server_v23 as server


def test_empty_bridge_is_missing_and_no_request_or_fake_live(tmp_path):
    logs, state = tmp_path / "fmz", tmp_path / "state"
    logs.mkdir()
    (logs / "snapshots.jsonl").write_bytes(b"")
    result = server.collect(logs, state, now_ms=1790390000000)
    assert result["producer_status"] == "missing"
    assert result["bridge"]["status"] == "source_empty"
    assert result["external_http_requests"] == result["llm_http_calls"] == 0
    assert json.loads((state / "current_data_health.json").read_text(encoding="utf-8"))["source_hash"] is None
    assert not (state / "bridge" / "latest.json").exists()
