"""Same-server MAP collector and local consumer; no HTTP server or LLM calls."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any

if __package__:
    from . import astra_data_engine_v23 as data
    from . import astra_data_projection_v23 as data_projection
    from . import astra_underwriting_fact_context_v20 as facts
    from . import astra_map_projection_v24 as projection
    from . import astra_map_sources_v24 as sources
else:
    import astra_data_engine_v23 as data
    import astra_data_projection_v23 as data_projection
    import astra_underwriting_fact_context_v20 as facts
    import astra_map_projection_v24 as projection
    import astra_map_sources_v24 as sources

SCHEMA = "astra_map_data_server@2.4.2"
INDEX = "deribit.btc.index.map_context.v1"
FAST = [sources.OI_NATIVE, sources.FUNDING_SETTLED]
SLOW = [
    [sources.ETF_FLOW, sources.MACRO_USD_BROAD, sources.MACRO_US10Y_NOMINAL, sources.MACRO_US10Y_REAL, sources.BORROW_RATE],
    [sources.BRK_COST],
    [sources.BRK_PNL],
]
REQUIRED = {
    "背景底价": [INDEX],
    "配置资金": [sources.ETF_FLOW],
    "库存与兑现": [sources.BRK_COST, sources.BRK_PNL],
    "杠杆融资": [sources.OI_NATIVE, sources.FUNDING_SETTLED, sources.BORROW_RATE],
    "外部条件": [sources.MACRO_USD_BROAD, sources.MACRO_US10Y_NOMINAL, sources.MACRO_US10Y_REAL],
    "空间背景": [projection.KPF_PRODUCT, projection.GLOBAL_PRODUCT],
    "持续事实": [data_projection.BRIDGE, data_projection.GATE],
}
MAX_STATE_BYTES = 8 * 1024 * 1024


def write_atomic(path: Path, value: dict) -> None:
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    if len(raw) > MAX_STATE_BYTES:
        raise ValueError("MAP_STATE_TOO_LARGE")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def read_current(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        raw = handle.read(MAX_STATE_BYTES + 1)
    if len(raw) > MAX_STATE_BYTES:
        raise ValueError("MAP_PRIOR_STATE_TOO_LARGE")
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise ValueError("MAP_PRIOR_STATE_INVALID")
    return value


def completeness(engine: Any, now_ms: int, records: list[dict]) -> dict:
    """Qualification for the actual product/scope, not catalog registration."""
    by_product = {row.get("identity", {}).get("product_id"): row for row in records}
    groups = {}
    for label, products in REQUIRED.items():
        rows = []
        for product in products:
            row = by_product.get(product)
            usage = "current_fact" if product == data_projection.BRIDGE else "space_gate" if product in {data_projection.GATE, projection.GLOBAL_PRODUCT} else "background"
            if row is None:
                try:
                    row = engine.read(product, now_ms=now_ms, usage=usage)
                except data.DataEngineError:
                    row = {}
            clocks = row.get("time") or {}
            decision = row.get("usage_decision") or {}
            qualified = bool(decision.get("can_use"))
            if product == projection.KPF_PRODUCT:
                values = row.get("content", {}).get("values") or {}
                qualified = qualified and values.get("valid") is True and any(
                    item.get("qualification_complete") for item in values.get("zones") or [])
            rows.append({"product_id": product, "can_use": qualified,
                         "status": decision.get("status") or "MISSING", "usage": usage,
                         "observation_end_ms": clocks.get("observation_end_ms"),
                         "first_seen_at_ms": clocks.get("first_seen_at_ms"),
                         "reason_codes": decision.get("reason_codes") or row.get("quality", {}).get("reason_codes") or []})
        groups[label] = {"complete": all(row["can_use"] for row in rows), "products": rows}
    return {"complete": all(group["complete"] for group in groups.values()), "groups": groups,
            "meaning": "满足当前用途与时效的产品；不代表经济有效或承保许可"}


def collect(config: dict, *, now_ms: int | None = None, transport=None) -> dict:
    started = time.perf_counter()
    began = int(time.time() * 1000) if now_ms is None else now_ms
    state_dir = Path(config["state_dir"])
    state_dir.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(state_dir).free < int(config.get("min_free_bytes", 6 * 1024**3)):
        raise ValueError("MAP_DISK_FREE_GUARD")
    current = state_dir / "current_map.json"
    previous = read_current(current)
    data_engine_dir = Path(config.get("data_engine_dir") or state_dir / "data_engine")
    engine = data.DataEngine(data_engine_dir)
    fmz = facts.latest_from_jsonl(Path(config["snapshots_jsonl"]), began)
    fmz = data_projection.qualify_fmz(engine, fmz, began)
    tick = int(previous.get("next_slow_group", 0)) % len(SLOW)
    selected = FAST + SLOW[tick]
    source_cfg = dict(config.get("source_config") or {})
    source_cfg["products"] = selected
    source_cfg["shared_http_budget_per_collect"] = 24
    captured = sources.collect_sources(engine, began, transport=transport, config=source_cfg) if config.get("sources_enabled", True) else []
    price_record = None
    if config.get("sources_enabled", True) and config.get("index_enabled", True):
        try:
            def load_index():
                body = sources._get_json("https://www.deribit.com/api/v2/public/ticker", {"instrument_name": "BTC-PERPETUAL"}, transport=transport)
                result = body.get("result") or {}
                price = projection._number(result.get("index_price"))
                stamp = result.get("timestamp")
                if price is None or price <= 0 or not isinstance(stamp, int) or isinstance(stamp, bool):
                    raise ValueError("MAP_INDEX_PRICE_OR_CLOCK_MISSING")
                retrieved = int(time.time()*1000) if now_ms is None else began
                return {"values": {"price_usd": price, "price_basis": "DERIBIT_BTC_USD_INDEX",
                                   "source": "deribit_public_ticker_index", "timestamp": stamp},
                        "observation_end_ms": stamp, "retrieved_at_ms": retrieved,
                        "source_revision": str(stamp), "data_state": "OK", "source_ref": "Deribit public/ticker BTC-PERPETUAL"}
            price_record = engine.fetch(INDEX, load_index, now_ms=max(began,int(time.time()*1000) if now_ms is None else began))
            captured.append(price_record)
        except (data.DataEngineError, ValueError, OSError):
            pass
    # The freeze cutoff follows completed actual retrieval; starting the network
    # request does not make a subsequently obtained record available earlier.
    now = max([began, int(time.time() * 1000) if now_ms is None else began] +
              [int(row.get("time", {}).get("first_seen_at_ms") or began) for row in captured])
    kpf_cfg = {"kpf_manifest_path": config["kpf_manifest_path"]}
    kpf = projection.collect_kpf_artifacts(engine, now, kpf_cfg)
    read_cfg = {key: value for key, value in source_cfg.items() if key != "products"}
    read_cfg.update(kpf_cfg)
    records = projection.read_cached_sources(engine, now, read_cfg)
    price_record = engine.read(INDEX, now_ms=now, usage="background")
    records.append(price_record)
    price_values = price_record.get("content", {}).get("values") or {}
    current_market = {"can_use": price_record.get("usage_decision", {}).get("can_use") is True,
                      "price_usd": price_values.get("price_usd"), "price_basis": price_values.get("price_basis"),
                      "source_product": INDEX, "source_record_id": price_record.get("record_id"),
                      "observed_at_ms": price_record.get("time", {}).get("observation_end_ms")}
    mapping = projection.build_projection(engine, now_ms=now, fmz_fact=fmz, source_records=records,
                                           config=read_cfg, prior_state=previous.get("projection_state"), current_market=current_market)
    producer_records = data_projection.fmz_records(engine, fmz, now)
    all_records = records + producer_records
    frozen = engine.freeze(all_records, cutoff_at_ms=now)
    view = {"schema": SCHEMA, "generated_at_ms": now, "next_slow_group": (tick + 1) % len(SLOW),
            "collected_products": selected, "fmz": {key: fmz.get(key) for key in
                ("status", "strategy_version", "snapshot_ts_ms", "source_hash", "gap_reasons")},
            "kpf": {"gaps": kpf.get("gaps"), "artifact_hashes": kpf.get("artifact_hashes")},
            "completeness": completeness(engine, now, all_records), "freeze_manifest": frozen,
            "current_market": current_market,
            "btc_map": mapping["btc_map"], "map_gaps": mapping["gaps"],
            "projection_state": projection.persistable_state(mapping["btc_map"], previous.get("projection_state")),
            "llm_http_calls": 0, "trading_calls": 0, "elapsed_ms": round((time.perf_counter()-started)*1000, 3)}
    if previous:
        write_atomic(state_dir / "previous_map.json", previous)
    write_atomic(current, view)
    return view


@contextmanager
def process_lock(state_dir: Path):
    import fcntl
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "collector.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("MAP_COLLECTOR_ALREADY_RUNNING") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    try:
        with process_lock(Path(config["state_dir"])):
            view = collect(config)
        print(json.dumps({key: view[key] for key in ("schema", "generated_at_ms", "fmz", "kpf", "completeness", "elapsed_ms", "llm_http_calls", "trading_calls")}, ensure_ascii=False))
        return 0
    except (ValueError, OSError) as exc:
        # Keep the last completed snapshot and its source clock. Emit a bounded
        # failure receipt; never re-clock a stale successful MAP after failure.
        write_atomic(Path(config["state_dir"]) / "last_failure.json", {
            "schema": "astra_map_collection_failure@1", "failed_at_ms": int(time.time()*1000),
            "reason": str(exc)[:240], "llm_http_calls": 0, "trading_calls": 0})
        print(json.dumps({"status": "failed", "reason": str(exc)[:240]}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
