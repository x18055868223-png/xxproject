"""Five-minute, read-only FMZ receiver. No exchange, LLM or trading calls."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import time

if __package__:
    from . import astra_data_engine_v23 as engine
    from . import astra_data_projection_v23 as projection
    from . import astra_data_retention_v23 as retention
    from . import astra_underwriting_fact_context_v20 as facts
else:
    import astra_data_engine_v23 as engine
    import astra_data_projection_v23 as projection
    import astra_data_retention_v23 as retention
    import astra_underwriting_fact_context_v20 as facts


def collect(fmz_log_dir: Path, state_dir: Path, *, now_ms: int | None = None) -> dict:
    started = time.perf_counter()
    now = int(time.time() * 1000) if now_ms is None else now_ms
    fmz_log_dir, state_dir = Path(fmz_log_dir), Path(state_dir)
    fmz_log_dir, state_dir = retention._validate_dirs(fmz_log_dir, state_dir, apply=True)
    data = engine.DataEngine(state_dir / "data_engine")
    source = fmz_log_dir / "snapshots.jsonl"
    fact = facts.latest_from_jsonl(source, now)
    projected = projection.qualify_fmz(data, fact, now)
    maintenance = retention.maintain(fmz_log_dir=fmz_log_dir, retained_dir=state_dir / "bridge",
                                     apply=True, now_ms=now, refresh_current=True)
    bridge = maintenance.get("current_refresh") or maintenance.get("refresh_current") or {}
    view = {"schema": "astra_data_server_observation@2.3.0", "received_at_ms": now,
            "producer_status": projected.get("status"), "producer_version": projected.get("strategy_version"),
            "snapshot_ts_ms": projected.get("snapshot_ts_ms"), "source_hash": projected.get("source_hash"),
            "source_gap_reasons": projected.get("gap_reasons") or [], "bridge": bridge,
            "producer_export_health": projected.get("export_health") or {},
            "maintenance": maintenance,
            "data_health": data.health(now), "external_http_requests": 0, "llm_http_calls": 0,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)}
    state_dir.mkdir(parents=True, exist_ok=True)
    target = state_dir / "current_data_health.json"
    tmp = target.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(view, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, target)
    return view


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fmz-log-dir", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    result = collect(args.fmz_log_dir, args.state_dir)
    print(json.dumps({key: value for key, value in result.items() if key != "data_health"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
