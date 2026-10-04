"""Local, immutable presentation of one server MAP capture. No external transport."""

from __future__ import annotations

import argparse
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse

from .astra_map_presentation_v241 import build_presentation
from .astra_map_v24 import _hash

FRONTEND = Path(__file__).resolve().parents[1] / "deploy" / "signal_audit" / "frontend"


def build_readonly_view(capture: dict) -> dict:
    mapping = deepcopy(capture["btc_map"])
    cutoff = mapping["cutoff_at_ms"]
    original_id = mapping.pop("map_id", None)
    mapping["map_gaps"] = list(dict.fromkeys(list(mapping.get("map_gaps") or [])
                                            + list(capture.get("map_gaps") or [])
                                            + list((capture.get("fmz") or {}).get("gap_reasons") or [])))
    mapping["presentation"] = build_presentation(mapping, presentation_mode="REPLAY")
    mapping["map_id"] = _hash(mapping)
    groups = (capture.get("completeness") or {}).get("groups") or {}
    records = (mapping.get("source_manifest") or {}).get("source_records") or []
    products = {}
    for group in groups.values():
        for product in group.get("products") or []:
            pid = product["product_id"]
            record = next((r for r in records if (r.get("identity") or {}).get("product_id") == pid), None)
            decision = deepcopy(product)
            decision.pop("product_id", None)
            entries = []
            if record is not None:
                qualification = record.get("usage_qualification") or record.get("usage_decision") or {}
                decision.update({k: v for k, v in qualification.items() if k not in decision})
                entries = [{"usage_decision": decision,
                            "usage_decisions": {product.get("usage", "background"): decision},
                            "time": deepcopy(record.get("time") or {}),
                            "record_id": record.get("record_id")}]
            products[pid] = {"status": product.get("status", "MISSING"), "current": entries,
                             "usage_decision": decision}
    # FMZ facts and current policy quotes are absent in this capture. The
    # independent background index never fills either authority.
    missing = list(dict.fromkeys(["historical_replay_not_current", "fmz_nonfuture_snapshot_missing",
                                 "producer_native_space_gate_missing"] + list(capture.get("map_gaps") or [])))
    return {
        "schema": "astra_workbench@2.0.0", "updated_at_ms": cutoff,
        "presentation_mode": "REPLAY", "llm_call_count": 0,
        "preview": {"kind": "historical_replay", "mode": "REPLAY",
                    "label": "服务器数据引擎截面 · 冻结回放",
                    "llm_http_calls": 0, "trading_permission": False,
                    "source_map_id": original_id},
        "btc_map": mapping,
        "market_context": {"status": "missing", "source_ts_ms": cutoff,
                           "current_price_usd": None, "gap_reasons": missing},
        "space_gate": {"status": "STRUCTURE_BLOCKED", "updated_at_ms": cutoff,
                       "source": "missing_producer_native_space_gate", "blocking_reasons": missing},
        "candidates": [], "gap_reasons": missing,
        "manual_review_state": {"inflight": None, "last_result": None},
        "data_health": {"generated_at_ms": cutoff, "historical_replay": True,
                        "products": products},
        "daily_snapshots": [], "state_transitions": [],
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/workbench":
            return self.send_json(200, self.server.view)
        if path == "/api/workbench/review-state":
            return self.send_json(200, {"manual_review_state": self.server.view["manual_review_state"]})
        target = (FRONTEND / (unquote(path).lstrip("/") or "index.html")).resolve()
        if not target.is_relative_to(FRONTEND.resolve()) or not target.is_file():
            return self.send_json(404, {"status": "not_found"})
        body = target.read_bytes()
        mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") else ""))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.send_json(403, {"status": "read_only_frozen_view"})

    def send_json(self, status, value):
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8901)
    args = parser.parse_args()
    capture = json.loads(args.capture.read_text(encoding="utf-8-sig"))
    if capture.get("llm_http_calls") != 0 or capture.get("trading_calls") != 0:
        raise ValueError("Capture must belong to the read-only data chain")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.view = build_readonly_view(capture)
    server.serve_forever()


if __name__ == "__main__":
    main()
