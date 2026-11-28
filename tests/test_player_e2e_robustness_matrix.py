"""End-to-end stage-by-stage robustness acceptance matrix for AI Player deal, inference, lock, and audit."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import gzip
import json
import threading
import time

import pytest

from tools import astra_player_dossier_v1 as dossier
from tools import astra_player_review_v1 as review
from tools import astra_player_tracking_v13 as tracking
from tools import astra_research_archive_v1 as archive
from tools import astra_underwriting_discovery_v20 as discovery
from tools import astra_workbench_v20 as workbench
from tools import signal_llm_review
from test_astra_player_dossier_v1 import Clock, SLOT, plan, source as fixture_source
from test_astra_player_tracking_v13 import EXCERPTS, M, RULE, post, serving
from test_astra_workbench_v20 import make_workbench, native_gate, write_fmz
from test_deribit_discovery_reliability import _http_error, _nov26_27nov26_chain, _opt


HOUR = 3_600_000


def _nov26_book_payload(name: str, ts_ms: int, delta: float, spot: float = 60_476.0) -> dict:
    is_short_candidate = name.endswith(("-60000-P", "-59000-P", "-60500-C", "-61000-C", "-62000-C"))
    bid = 0.013 if is_short_candidate else 0.003
    ask = 0.015 if is_short_candidate else 0.004
    return {
        "instrument_name": name,
        "timestamp": ts_ms,
        "state": "open",
        "underlying_price": spot,
        "index_price": spot,
        "mark_price": round((bid + ask) / 2.0, 4),
        "mark_iv": 51.5,
        "greeks": {"delta": delta, "gamma": 0.00012, "vega": 9.5, "theta": -185.0, "rho": -0.25},
        "bids": [[bid, 3.0]],
        "asks": [[ask, 3.0]],
    }


def _strict_p5_reply_text(
    action_header: str = "## Accept / Put",
    *,
    short_strike: int = 60_000,
    long_strike: int = 58_000,
    require_outside_at_t0: bool = True,
) -> str:
    put_txt = "Put路径判断：下方短腿60,000与现价保留缓冲，前窗回撤在短腿外侧企稳。"
    call_txt = "Call路径判断：上方短腿61,000距现价偏近，反抽脉冲易先触探上侧。"
    put_cnt = "Put最强反证：现货主动净卖若再度放大，仍可能快速压缩下侧余量。"
    call_cnt = "Call最强反证：上侧短腿绝对距离更窄，夜间反弹路径更容易越界。"
    comp_txt = "两侧对比择优：综合两侧短腿距离、历史回撤路径与反证，当前择优结论明确。"
    is_call = "Call" in action_header
    side_label = "Call" if is_call else "Put"
    outside_word = "下方" if is_call else "上方"
    cmp_word = "严格高于" if is_call else "严格低于"
    hist_txt = f"冻结历史事实：读取截止前指数始终位于{side_label}卖出短腿{outside_word}，未出现越过短腿采样。"
    acc_txt = (
        "承担条件：正面承担短线波动抬升的不利事实，但外侧余量尚未耗尽；"
        "现货跨源时钟存在OBSERVED_UNALIGNED不确定性；"
        "若盘中短腿外侧剩余余量实质收缩且4h现货主动流同向转强，则触发独立于机械M的实质重审。"
    )
    conf_txt = (
        f"破坏确认口径：采用 entry_not_returned，首次检查为入场后下一UTC整分钟，之后每5分钟（check_interval_ms=300000）执行一次，"
        f"btc_usd 分钟采样{cmp_word}卖出短腿{short_strike:,}即记为进入（包含保护腿{long_strike:,}边界及其外侧）且至检查点仍未严格回到短腿外侧才触发，"
        f"触及短腿不算进入也不算返回。"
    )
    paragraphs = [action_header, put_txt, call_txt, put_cnt, call_cnt, comp_txt]
    ann: dict = {
        "put": put_txt,
        "call": call_txt,
        "put_counter": put_cnt,
        "call_counter": call_cnt,
        "comparison": comp_txt,
        "depends_on_ard": False,
    }
    if action_header.startswith("## Accept"):
        paragraphs.extend([hist_txt, acc_txt, conf_txt])
        ann["condition_plan"] = {
            "invalidation": "entry_not_returned",
            "check_interval_ms": 300_000,
            "require_outside_at_t0": require_outside_at_t0,
            "historical_facts": hist_txt,
            "accepted_conditions": acc_txt,
            "confirmation_excerpt": conf_txt,
        }
    return "\n\n".join(paragraphs) + "\n\n<ANNOTATION_JSON>\n" + json.dumps(ann, ensure_ascii=False) + "\n</ANNOTATION_JSON>"


def _strict_p5_single_side_reply_text(
    action_header: str = "## Accept / Call",
    *,
    available_side: str = "call",
    short_strike: int = 61_000,
    long_strike: int = 62_000,
    require_outside_at_t0: bool = True,
) -> str:
    if available_side == "call":
        side_txt = f"Call路径判断：上方短腿{short_strike:,}、保护腿{long_strike:,}与现价保留缓冲，仅评估Call侧。"
        cnt_txt = f"Call最强反证：现货主动净买若持续放大，上侧短腿{short_strike:,}可能承压。"
        side_key, cnt_key = "call", "call_counter"
    else:
        side_txt = f"Put路径判断：下方短腿{short_strike:,}、保护腿{long_strike:,}与现价保留缓冲，仅评估Put侧。"
        cnt_txt = f"Put最强反证：现货主动净卖若持续放大，下侧短腿{short_strike:,}可能承压。"
        side_key, cnt_key = "put", "put_counter"
    comp_txt = f"对比与结论：本轮仅{available_side.title()}侧存在合格候选，围绕该侧完成独立判断。"
    cmp_word = "严格高于" if available_side == "call" else "严格低于"
    outside_word = "下方" if available_side == "call" else "上方"
    hist_txt = f"冻结历史事实：读取截止前指数始终位于{available_side.title()}卖出短腿{short_strike:,}{outside_word}。"
    acc_txt = "承担条件：外侧余量尚存，若盘中短腿外侧余量实质收缩且现货主动流同向转强，触发独立复核。"
    conf_txt = (
        f"破坏确认口径：采用 entry_not_returned，首次检查为入场后下一UTC整分钟，之后每5分钟（check_interval_ms=300000）执行一次，"
        f"btc_usd 分钟采样{cmp_word}卖出短腿{short_strike:,}即记为进入（包含保护腿{long_strike:,}边界及其外侧）且至检查点仍未严格回到短腿外侧才触发，"
        f"触及短腿不算进入也不算返回。"
    )
    paragraphs = [action_header, side_txt, cnt_txt, comp_txt]
    ann: dict = {
        side_key: side_txt,
        cnt_key: cnt_txt,
        "comparison": comp_txt,
        "depends_on_ard": False,
    }
    if action_header.startswith("## Accept"):
        paragraphs.extend([hist_txt, acc_txt, conf_txt])
        ann["condition_plan"] = {
            "invalidation": "entry_not_returned",
            "check_interval_ms": 300_000,
            "require_outside_at_t0": require_outside_at_t0,
            "historical_facts": hist_txt,
            "accepted_conditions": acc_txt,
            "confirmation_excerpt": conf_txt,
        }
    return "\n\n".join(paragraphs) + "\n\n<ANNOTATION_JSON>\n" + json.dumps(ann, ensure_ascii=False) + "\n</ANNOTATION_JSON>"


def _extract_pair_strikes_from_user_content(content: str, side: str) -> tuple[int, int]:
    import re
    m = re.search(rf"-\s*{side.upper()}:\s*short=[^\n]*?\(([\d.]+)\),\s*long=[^\n]*?\(([\d.]+)\)", content, re.I)
    assert m is not None, f"missing {side} contract line in user content"
    return int(float(m.group(1))), int(float(m.group(2)))


@contextmanager
def _sse_mock_server(script_fn):
    """Local HTTP server speaking real OpenAI/DeepSeek SSE (`text/event-stream`) and error codes."""
    calls: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            return

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or "0")
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
            idx = len(calls)
            calls.append({"path": self.path, "headers": dict(self.headers), "body": body})
            spec = script_fn(idx, body)
            status = int(spec.get("status", 200))
            if status != 200:
                payload = json.dumps(spec.get("error_body") or {"error": {"message": f"http_{status}"}}).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                if spec.get("retry_after") is not None:
                    self.send_header("Retry-After", str(spec["retry_after"]))
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                self.wfile.flush()
                return

            chunks: list[bytes] = []
            model_name = spec.get("model") or body.get("model") or "deepseek-reasoner"
            if spec.get("reasoning"):
                ev_r = {
                    "id": f"chatcmpl-{idx}",
                    "model": model_name,
                    "choices": [{"index": 0, "delta": {"reasoning_content": spec["reasoning"]}, "finish_reason": None}],
                }
                chunks.append(f"data: {json.dumps(ev_r, ensure_ascii=False)}\n\n".encode("utf-8"))
            text = spec.get("text") or ""
            mid = max(1, len(text) // 2)
            parts = [text[:mid], text[mid:]] if text else [""]
            finish_reason = spec.get("finish_reason", "stop")
            for p_i, piece in enumerate(parts):
                is_last = p_i == len(parts) - 1
                ev = {
                    "id": f"chatcmpl-{idx}",
                    "model": model_name,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": piece},
                        "finish_reason": finish_reason if (is_last and not spec.get("drop_before_done")) else None,
                    }],
                }
                if is_last and not spec.get("drop_before_done"):
                    ev["usage"] = spec.get("usage") or {"prompt_tokens": 900, "completion_tokens": 220, "total_tokens": 1120}
                chunks.append(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8"))
                if spec.get("drop_before_done") and p_i == 0:
                    break
            if not spec.get("drop_before_done"):
                chunks.append(b"data: [DONE]\n\n")

            if spec.get("drop_before_done"):
                # Send chunked transfer-encoding without terminating 0-chunk -> triggers IncompleteRead!
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for c in chunks:
                    self.wfile.write(f"{len(c):X}\r\n".encode("ascii") + c + b"\r\n")
                    self.wfile.flush()
                self.close_connection = True
                return

            full_sse = b"".join(chunks)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(full_sse)))
            self.end_headers()
            self.wfile.write(full_sse)
            self.wfile.flush()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/v1", calls
    finally:
        httpd.shutdown()
        thread.join()
        httpd.server_close()


def _setup_nov26_workbench_and_archive(
    tmp_path: Path,
    monkeypatch,
    *,
    clock: list[int],
    put_mode: dict | None = None,
):
    slot_ms, expiry_ms, spot, rows, call_deltas = _nov26_27nov26_chain()
    put_deltas = {60_000: -0.34, 59_000: -0.16, 58_000: -0.06, 57_000: -0.02, 56_000: -0.008, 55_000: -0.002}
    meta_by_name = {r["instrument_name"]: dict(r) for r in rows}
    put_cfg = put_mode or {}
    wire_calls: list[tuple[int, str, str]] = []

    wb, snaps = make_workbench(tmp_path / "wb_state", clock=lambda: clock[0])
    dossier_root = tmp_path / "dossiers"
    wb.config = replace(
        wb.config,
        shadow_dossier_root=dossier_root,
        shadow_service_token="shadow-secret",
        shadow_experiment=True,
        frontend_dir=Path(__file__).resolve().parents[1] / "deploy/astra_workbench_v3",
    )
    wb.discovery_capture = wb._default_discovery_capture
    wb.quote_capture = wb._default_quote_capture

    def fake_disc_get(_base, path, params, _timeout):
        receipt = {"data_engine_receipt": {"retrieved_at_ms": clock[0]}}
        if path == "/public/get_index_price":
            return {"result": {"index_price": spot}, **receipt}
        if path == "/public/get_instruments":
            return {"result": [dict(r) for r in rows], **receipt}
        if path == "/public/get_order_book":
            name = params["instrument_name"]
            wire_calls.append((clock[0], "disc", name))
            strike = int(name.split("-")[2])
            if name.endswith("-P"):
                if put_cfg.get("429"):
                    raise discovery.DeribitPublicError.from_http(
                        _http_error(429, b'{"error":{"code":10028,"message":"too_many_requests"}}', headers={"Retry-After": "10"}),
                        "/public/get_order_book",
                    )
                if put_cfg.get("above_max"):
                    return {"result": _nov26_book_payload(name, clock[0], -0.65, spot), **receipt}
                if put_cfg.get("missing_delta") and strike == 60_000:
                    payload = _nov26_book_payload(name, clock[0], -0.34, spot)
                    payload["greeks"] = {"delta": None}
                    return {"result": payload, **receipt}
                return {"result": _nov26_book_payload(name, clock[0], put_deltas[strike], spot), **receipt}
            if put_cfg.get("call_above_max"):
                return {"result": _nov26_book_payload(name, clock[0], 0.65, spot), **receipt}
            assert strike <= 70_000, f"far-OTM Call strike {name} was not pruned"
            return {"result": _nov26_book_payload(name, clock[0], call_deltas[strike], spot), **receipt}
        raise AssertionError(path)

    def fake_quote_get(path, params):
        name = params["instrument_name"]
        wire_calls.append((clock[0], "quote", name))
        if path == "public/get_instrument":
            return dict(meta_by_name[name])
        if path == "public/get_order_book":
            strike = int(name.split("-")[2])
            d_val = put_deltas.get(strike, -0.01) if name.endswith("-P") else call_deltas.get(strike, 0.0)
            return _nov26_book_payload(name, clock[0], d_val, spot)
        raise AssertionError(path)

    monkeypatch.setattr(discovery, "_public_get", fake_disc_get)
    monkeypatch.setattr(workbench.public_quote, "public_transport", fake_quote_get)
    monkeypatch.setattr(
        dossier,
        "public",
        lambda method, **_kw: [dict(r) for r in rows]
        if method == "get_instruments"
        else {"data": [{"date": "2026-11-27", "delivery_price": 60_800.0}]},
    )
    monkeypatch.setattr(
        dossier.archive.Deribit,
        "index_path",
        lambda _self: [[t, spot] for t in range(slot_ms - dossier.DAY, max(clock[0], expiry_ms) + M, M)],
    )

    def sync_market_aux():
        write_fmz(snaps, native_gate(ts=clock[0] - 1_000), now=clock[0])
        wb._expiry_gamma = {
            "schema": "astra_expiry_gamma_v1@1.0.0",
            "status": "ok",
            "computed_at_ms": clock[0] - 2_000,
            "available_at_ms": clock[0] - 1_500,
            "expiries": {
                "27NOV26": {
                    "expiry": "27NOV26",
                    "expiry_ms": expiry_ms,
                    "forward_usd": spot,
                    "atm_iv_pct": 51.5,
                    "expected_move_usd": 1_250.0,
                    "pin_status": "ok",
                    "walls": {
                        "pin": 60_500.0,
                        "gamma_call": 62_000.0,
                        "gamma_put": 59_000.0,
                        "oi_call": 63_000.0,
                        "oi_put": 58_000.0,
                    },
                    "top": {"oi_put": [{"strike": 58_000.0}], "oi_call": [{"strike": 63_000.0}]},
                }
            },
            "realized_vol": {
                "instrument": "BTC-PERPETUAL",
                "symbol": "BTC-PERPETUAL",
                "bar": "1h close-to-close",
                "source": "deribit_public/get_tradingview_chart_data",
                "status": "ok",
                "rv_24h_pct": 47.2,
                "last_bar_end_ms": (clock[0] // HOUR) * HOUR,
                "rv_computed_at_ms": clock[0] - 2_000,
                "rv_available_at_ms": clock[0] - 1_500,
                "cached_fallback": False,
            },
        }

    sync_market_aux()

    arch_root = tmp_path / "research_archive"
    arch = archive.Archive(arch_root)
    exp_day = dossier.utc(expiry_ms).date().isoformat()
    for sub in ("cases", "levels", "structure", "band_snapshots"):
        (arch_root / sub).mkdir(parents=True, exist_ok=True)
    band_ts = slot_ms - 4 * HOUR
    fmz_jsonl = tmp_path / "fmz_band.jsonl"
    fmz_jsonl.write_text(
        json.dumps({
            "snapshot_exported_ms": band_ts - 600_000,
            "source_available_at_ms": band_ts - 540_000,
            "snapshot_exported_utc8": "2026-11-26 20:00:00",
            "factor_snapshot": {
                "anchor": {"ready": True, "freshness": "FRESH", "band_half": 220.0, "gex_source_ts_ms": band_ts - 650_000},
                "flow": {"direction": "neutral", "cvd_sum": 8.5},
            },
        }) + "\n",
        encoding="utf-8",
    )
    archive.archive_band_snapshots(arch, fmz_jsonl, now_ms=slot_ms - 5 * M)
    arch.append_line(
        f"structure/{exp_day}.jsonl",
        {
            "schema": "structure_snapshot@0.1",
            "expiry_ms": expiry_ms,
            "at_ms": slot_ms - 10 * M,
            "available_at_ms": slot_ms - 9 * M,
            "pin_source": "deribit_gex",
            "pin_method": "gamma_peak",
            "pin_status": "ok",
            "expected_move_usd": 1_250.0,
            "profile": {
                "walls": {"pin": 60_500.0},
                "top": {"oi_put": [{"strike": 58_000.0}], "oi_call": [{"strike": 63_000.0}]},
            },
        },
        at_ms=slot_ms - 9 * M,
        kind="structure",
    )
    plan_r16 = {
        "plan_id": "daily_r16@3",
        "research": dossier.RESEARCH,
        "mode": "SHADOW_ONLY",
        "candidate_round": "r16",
        "input_profile": "p0_strict@1",
        "prompt_version": review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
        "mechanical_contract_version": "p2_strict@1",
        "effective_at_ms": slot_ms,
        "expiry_offset_ms": 16 * HOUR,
        "deal_grace_ms": 30 * M,
        "reply_grace_ms": 30 * M,
    }
    return wb, arch_root, dossier_root, plan_r16, slot_ms, expiry_ms, wire_calls, sync_market_aux


# ---------------------------------------------------------------------------
# 1. Source Readiness, Prewarm Alert & Audit Originals Unit/Integration Checks
# ---------------------------------------------------------------------------

def test_evaluate_source_readiness_pair_skew_target_expiry_p0_strict_and_single_side():
    now_ms = SLOT + 5 * M
    target_exp = SLOT + 2 * HOUR
    base_view = {
        "updated_at_ms": now_ms,
        "discovery_status": {"capture_state": "COMPLETE", "upstream_code": None, "observed_at_ms": now_ms},
        "data_health": {"products": {"deribit.btc.discovery.current.v1": {"status": "OK", "capture_state": "COMPLETE"}}},
        "expiry_gamma": {
            "schema": "astra_expiry_gamma_v1@1.0.0",
            "status": "ok",
            "computed_at_ms": now_ms - 5_000,
            "by_expiry": {"14NOV26": {"expiry": "14NOV26", "expiry_ms": target_exp, "stale": False, "walls": {"pin": 66_000.0}}},
            "realized_vol": {"rv_24h_pct": 44.2},
        },
        "candidates": [
            {
                "side": "put",
                "status": "snapshot",
                "snapshot": {"snapshot_id": "sp", "contract": {"side": "put", "expiry_ms": target_exp}, "market": {"valuation_ts_ms": now_ms - 2_000}},
                "formal_evidence_eligible": True,
                "live_lock_eligible": True,
                "discovery_status": {"capture_state": "COMPLETE", "side_complete": True, "upstream_code": None, "delta_population": {"complete": True, "verified_eligible": 2}},
            },
            {
                "side": "call",
                "status": "snapshot",
                "snapshot": {"snapshot_id": "sc", "contract": {"side": "call", "expiry_ms": target_exp}, "market": {"valuation_ts_ms": now_ms - 1_000}},
                "formal_evidence_eligible": True,
                "live_lock_eligible": True,
                "discovery_status": {"capture_state": "COMPLETE", "side_complete": True, "upstream_code": None, "delta_population": {"complete": True, "verified_eligible": 2}},
            },
        ],
    }

    ok = tracking.evaluate_source_readiness(workbench_view=base_view, now_ms=now_ms, target_expiry_ms=target_exp, slot_ms=SLOT, input_profile="p0_strict@1")
    assert ok["status"] == "SOURCE_READY" and ok["reasons"] == []

    skew_view = json.loads(json.dumps(base_view))
    skew_view["candidates"][0]["snapshot"]["market"]["valuation_ts_ms"] = now_ms - 95_000
    r_skew = tracking.evaluate_source_readiness(workbench_view=skew_view, now_ms=now_ms, target_expiry_ms=target_exp)
    assert r_skew["status"] == "UPSTREAM_BLOCKED" and "PAIR_SKEW_EXCEEDED" in r_skew["reasons"]

    exp_view = json.loads(json.dumps(base_view))
    exp_view["candidates"][1]["snapshot"]["contract"]["expiry_ms"] = target_exp + dossier.DAY
    r_exp = tracking.evaluate_source_readiness(workbench_view=exp_view, now_ms=now_ms, target_expiry_ms=target_exp)
    assert r_exp["status"] == "UPSTREAM_BLOCKED"
    assert any(r.startswith("candidate_call_expiry_mismatch:") for r in r_exp["reasons"])

    no_rv_view = json.loads(json.dumps(base_view))
    no_rv_view["expiry_gamma"]["realized_vol"]["rv_24h_pct"] = None
    r_rv = tracking.evaluate_source_readiness(workbench_view=no_rv_view, now_ms=now_ms, target_expiry_ms=target_exp, input_profile="p0_strict@1")
    assert r_rv["status"] == "UPSTREAM_BLOCKED" and "expiry_gamma_rv_24h_missing" in r_rv["reasons"]

    no_wall_view = json.loads(json.dumps(base_view))
    no_wall_view["expiry_gamma"]["by_expiry"] = {}
    r_wall = tracking.evaluate_source_readiness(workbench_view=no_wall_view, now_ms=now_ms, target_expiry_ms=target_exp, input_profile="p0_strict@1")
    assert r_wall["status"] == "UPSTREAM_BLOCKED" and "expiry_gamma_walls_missing_for_target_expiry" in r_wall["reasons"]

    single_side_view = json.loads(json.dumps(base_view))
    single_side_view["candidates"][1] = {
        "side": "call",
        "status": "gap",
        "snapshot": None,
        "formal_evidence_eligible": False,
        "live_lock_eligible": False,
        "last_valid_candidate": {"side": "call", "is_historical_summary": True},
        "discovery_status": {
            "capture_state": "COMPLETE",
            "side_complete": True,
            "upstream_code": None,
            "delta_population": {"complete": True, "total_qualified": 8, "verified_eligible": 0, "verified_above_max": 8},
        },
    }
    r_single = tracking.evaluate_source_readiness(workbench_view=single_side_view, now_ms=now_ms, target_expiry_ms=target_exp, input_profile="p0_strict@1")
    assert r_single["status"] == "MARKET_SINGLE_SIDE_ONLY"
    assert r_single["checks"]["accept_lock_ready"] is True
    assert "candidate_call_no_verified_eligible_strike" in r_single["reasons"]

    inc_view = json.loads(json.dumps(single_side_view))
    inc_view["candidates"][1]["discovery_status"]["delta_population"]["complete"] = False
    assert tracking.evaluate_source_readiness(workbench_view=inc_view, now_ms=now_ms)["status"] == "UPSTREAM_BLOCKED"


def test_prewarm_business_readiness_stderr_alert_and_audit_originals_hardening(tmp_path, monkeypatch, capsys):
    cfg = plan()
    slot = cfg["effective_at_ms"]
    expiry = slot + cfg["expiry_offset_ms"]
    arch_dir = tmp_path / "archive"
    for sub in ("cases", "levels", "structure", "band_snapshots"):
        (arch_dir / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LLM_API_KEY", "k-test")

    def svc_blocked(action, body):
        if action == "prepare_deal":
            assert body["allow_refresh"] is True
            assert body["target_expiry_ms"] == expiry and body["slot_ms"] == slot
            return {
                "readiness_status": "UPSTREAM_BLOCKED",
                "ready_for_deal": False,
                "blockers": ["deribit_cooldown_active"],
                "sides": {"put": {"ready": True}, "call": {"ready": False}},
                "deribit_cooldown_remaining_ms": 25_000,
                "refresh_triggered": False,
            }
        return {"enabled": True}

    capsys.readouterr()
    rep_warn = tracking.prewarm(
        cfg, arch_dir, svc_blocked,
        clock=lambda: slot - 5 * M,
        public=lambda *_a, **_kw: [{"expiration_timestamp": expiry, "settlement_currency": "BTC", "is_active": True}],
    )
    captured = capsys.readouterr()
    assert rep_warn["state"] == "PREWARM"
    assert rep_warn["business_readiness"]["ready_for_deal"] is False
    assert rep_warn["business_readiness"]["alert_level"] == "WARN"
    assert captured.out == ""
    assert captured.err.startswith("PREWARM_ALERT ")
    alert_obj = json.loads(captured.err[len("PREWARM_ALERT "):].strip())
    assert alert_obj["status"] == "UPSTREAM_BLOCKED" and alert_obj["alert_level"] == "WARN"

    clk = Clock(SLOT + M)
    d = dossier.Dossiers(tmp_path / "d_audit", clock=clk)
    undealt, = d.tick(plan(), slots=[SLOT])
    d.note(undealt, "DATA_BLOCKED", "需同一个采集小时的唯一 put 原生案卷，现有 0 份")
    orig_undealt = tracking.audit_originals(undealt)
    assert orig_undealt["evidence_available"] is False
    assert orig_undealt["input"] is None
    assert orig_undealt["response"] is None
    assert orig_undealt["selected"] is False
    assert orig_undealt["issuance_notes"][0]["state"] == "DATA_BLOCKED"
    with pytest.raises(tracking.Blocked, match="evidence_not_dealt"):
        tracking.export_model_input(undealt)

    h_to = d.expect(SLOT + plan()["expiry_offset_ms"], "AD_HOC", reference="hand-5249-timeout")
    d.deal(h_to, fixture_source(clk))

    def timeout_with_cot(*_a, **_kw):
        err = TimeoutError("read timed out after 600s")
        err.partial_response = {
            "http_status": 200,
            "model": "deepseek-reasoner",
            "text": "## 初步比较\nPut 与 Call 尚在权衡...",
            "reasoning_content": "SECRET_COT_MUST_NEVER_LEAK",
            "chunk_count": 5,
            "bytes_received": 256,
            "usage": {"prompt_tokens": 1000, "completion_tokens": 350, "total_tokens": 1350},
        }
        raise err

    res_to = tracking._deal_hand(d, h_to, None, tracking._llm_settings({"api_key": "k", "model": "deepseek-reasoner", "transport": timeout_with_cot}))
    assert res_to["status"] == "TIMEOUT"
    orig_to = tracking.audit_originals(h_to)
    assert orig_to["evidence_available"] is True
    assert orig_to["response_status"] == "TIMEOUT"
    assert orig_to["partial_response"]["content"] == "## 初步比较\nPut 与 Call 尚在权衡..."
    assert "reasoning_content" not in orig_to["partial_response"]
    assert "SECRET_COT_MUST_NEVER_LEAK" not in json.dumps(orig_to, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 2. Nov 26 `hand_66a598b4790172fa3fd56276` Full Real-Pipeline Stage-by-Stage E2E
# ---------------------------------------------------------------------------

def test_nov26_hand_66a598b4_full_stage_by_stage_e2e_recovery_lock_audit_and_hold_review(tmp_path, monkeypatch, capsys):
    slot_0 = int(datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc).timestamp() * 1000)
    clock = [slot_0 - 8 * M + 4_166]  # 15:52:04.166Z (exact live prewarm timestamp)
    wb, arch_root, dossier_root, plan_r16, slot_ms, expiry_ms, wire_calls, sync_market_aux = _setup_nov26_workbench_and_archive(
        tmp_path, monkeypatch, clock=clock,
    )
    monkeypatch.setenv("LLM_API_KEY", "k-e2e")
    accept_text = _strict_p5_reply_text("## Accept / Put", short_strike=60_000, long_strike=58_000, require_outside_at_t0=True)

    with serving(wb) as wb_base, _sse_mock_server(
        lambda _idx, _body: {"status": 200, "reasoning": "核查两侧行权价与余量...", "text": accept_text, "finish_reason": "stop"}
    ) as (llm_endpoint, llm_calls):
        service = post(wb_base, wb)

        # Stage A: 15:52:04Z Prewarm — refreshes 46-Call-strike chain cleanly (<=12 Call wire books) and verifies business readiness
        capsys.readouterr()
        rep_prewarm = tracking.prewarm(
            plan_r16,
            arch_root,
            service,
            clock=lambda: clock[0],
            public=dossier.public,
        )
        cap_pre = capsys.readouterr()
        assert rep_prewarm["state"] == "PREWARM"
        assert rep_prewarm["business_readiness"]["ready_for_deal"] is True
        assert rep_prewarm["business_readiness"]["alert_level"] == "NONE"
        assert cap_pre.err == ""
        call_disc_wires = [n for _, kind, n in wire_calls if kind == "disc" and n.endswith("-C")]
        assert len(call_disc_wires) == 11 and len(call_disc_wires) <= 12

        # Stage B: 16:02:00Z Auto-Deal — Research Archive has 0 cases for hour 16 (`cases/2026-11-26/16_*` empty!)
        clock[0] = slot_ms + 2 * M  # 16:02:00Z
        sync_market_aux()
        case_dir_16 = arch_root / "cases" / "2026-11-26"
        assert not list(case_dir_16.glob("16_*"))

        d = dossier.Dossiers(dossier_root, clock=lambda: clock[0], require_reply_provenance=True)
        llm_cfg = {
            "api_key": "k-e2e",
            "model": "deepseek-reasoner",
            "endpoint": llm_endpoint,
            "prompt_version": review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
            "input_profile": "p0_strict@1",
        }
        rows_1602 = tracking.auto_deal(
            d, plan_r16, arch_root, service, llm_config=llm_cfg,
        )
        assert len(rows_1602) == 1
        row0 = rows_1602[0]
        # Exact deterministic hand_id of the real 2026-11-26 16:00Z incident!
        assert row0["hand_id"] == "hand_66a598b4790172fa3fd56276"
        assert row0["status"] == "VALID"
        assert row0["decision"] == "Accept"
        assert row0["link"]["state"] == "SHADOW_LOCK_CREATED"
        assert row0["link"]["execution_allowed"] is False

        # Verify native cases backfilled into Research Archive with intact SHA-256 chain!
        put_files = list(case_dir_16.glob("16_put_cf_*.json.gz"))
        call_files = list(case_dir_16.glob("16_call_cf_*.json.gz"))
        assert len(put_files) == 1 and len(call_files) == 1
        assert archive.Archive(arch_root).verify()["ok"] is True

        # Verify frozen evidence, SSE response, contract validation, shadow lock, and audit surfaces
        hand = d.path(expiry_ms) / "hands" / "hand_66a598b4790172fa3fd56276"
        assert (hand / "evidence" / "input.md").is_file()
        meta = d.verify_evidence(hand)
        assert meta["input_profile"] == "p0_strict@1"
        src_doc = dossier.read(hand / "evidence" / "source.json")
        assert src_doc["source"] == "targeted workbench verified native case + Deribit public read"
        assert len(llm_calls) == 1
        assert llm_calls[0]["body"]["stream"] is True

        orig = tracking.audit_originals(hand)
        assert orig["evidence_available"] is True
        assert orig["selected"] is True
        assert orig["response_status"] == "VALID"
        assert "60,000" in orig["input"] and "61,000" in orig["input"]
        aud = tracking.audit_record(hand, clock[0], {}, detail=True)
        assert aud["decision"]["judgment"]["action"] == "Accept"
        assert aud["decision"]["judgment"]["side"] == "put"
        assert aud["source"]["name"] == "targeted workbench verified native case + Deribit public read"

        # Stage C: 16:07:05Z Multi-Tick Idempotency — zero new LLM calls, zero duplicate archive files
        clock[0] = slot_ms + 7 * M + 5_000
        rows_1607 = tracking.auto_deal(
            d, plan_r16, arch_root, service, llm_config=llm_cfg,
        )
        assert rows_1607[0]["hand_id"] == "hand_66a598b4790172fa3fd56276"
        assert rows_1607[0]["status"] == "ALREADY_DECIDED"
        assert rows_1607[0]["decision"] == "Accept"
        assert len(llm_calls) == 1
        assert len(list(case_dir_16.glob("16_put_cf_*.json.gz"))) == 1
        assert archive.Archive(arch_root).verify()["ok"] is True

        # Stage D: 16:10:00Z 5m Grid Check & Hold Sync — verifies downstream tracking episode continuity
        clock[0] = slot_ms + 10 * M
        sync_market_aux()
        wb.refresh(now_ms=clock[0], record_daily=False)
        sync_res = tracking.sync(d, hand, service, check_due=True, archive_root=arch_root)
        assert sync_res["state"] == "active"
        assert sync_res["checks"] and all(c["kind"] == "CONDITION_NOT_TRIGGERED" for c in sync_res["checks"])
        ep_dirs = list((d.path(expiry_ms) / "episodes").glob("episode_*"))
        assert len(ep_dirs) == 1
        ev_kinds = [dossier.read(p)["kind"] for p in (ep_dirs[0] / "events").glob("*.json")]
        assert any(k in {"CONDITION_NOT_TRIGGERED", "CONDITION_TRIGGERED", "CONDITION_UNKNOWN"} for k in ev_kinds)


# ---------------------------------------------------------------------------
# 3. Data-Path Bounded Counterexamples, Qualification Strictness & Race Matrix
# ---------------------------------------------------------------------------

def test_data_path_counterexample_matrix_fail_closed_and_race_safety(tmp_path, monkeypatch, capsys):
    # Counterexample A1: Confirmed Put-absent (`above_max` -> verified_eligible=0, MARKET_SINGLE_SIDE_ONLY) now proceeds on Call via formal path!
    slot_0 = int(datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc).timestamp() * 1000)
    clock = [slot_0 - 8 * M]
    wb_ss, arch_ss, dos_ss, plan_r16, slot_ms, expiry_ms, _, sync_aux_ss = _setup_nov26_workbench_and_archive(
        tmp_path / "single_side_call", monkeypatch, clock=clock, put_mode={"above_max": True},
    )
    monkeypatch.setenv("LLM_API_KEY", "k-single")

    def call_accept_script(_idx, body):
        short_k, long_k = _extract_pair_strikes_from_user_content(body["messages"][1]["content"], "call")
        return {
            "status": 200,
            "reasoning": "仅Call侧合格，逐项核查Call路径与最强反证。",
            "text": _strict_p5_single_side_reply_text(
                "## Accept / Call", available_side="call", short_strike=short_k, long_strike=long_k, require_outside_at_t0=True
            ),
            "finish_reason": "stop",
        }

    with serving(wb_ss) as base_ss, _sse_mock_server(call_accept_script) as (llm_ep_ss, llm_calls_ss):
        svc_ss = post(base_ss, wb_ss)

        # 15:52Z Prewarm returns READY_FOR_DEAL with empty blockers & no stderr alert
        capsys.readouterr()
        rep_pre = tracking.prewarm(plan_r16, arch_ss, svc_ss, clock=lambda: clock[0], public=dossier.public)
        cap = capsys.readouterr()
        assert rep_pre["state"] == "PREWARM"
        assert rep_pre["business_readiness"]["ready_for_deal"] is True
        assert rep_pre["business_readiness"]["status"] == "READY_FOR_DEAL"
        assert rep_pre["business_readiness"]["blockers"] == []
        assert rep_pre["business_readiness"]["alert_level"] == "NONE"
        assert cap.err == ""

        # 16:02Z Scheduled auto_deal -> deals Call-only hand, backfills 16_call_cf_*.json.gz only, creates Call shadow lock
        clock[0] = slot_ms + 2 * M
        sync_aux_ss()
        d_ss = dossier.Dossiers(dos_ss, clock=lambda: clock[0], require_reply_provenance=True)
        llm_cfg_ss = {
            "api_key": "k-single",
            "model": "deepseek-reasoner",
            "endpoint": llm_ep_ss,
            "prompt_version": review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
            "input_profile": "p0_strict@1",
        }
        rows_ss = tracking.auto_deal(d_ss, plan_r16, arch_ss, svc_ss, llm_config=llm_cfg_ss)
        assert len(rows_ss) == 1
        assert rows_ss[0]["status"] == "VALID"
        assert rows_ss[0]["decision"] == "Accept"
        assert rows_ss[0]["link"]["state"] == "SHADOW_LOCK_CREATED"
        assert rows_ss[0]["link"]["execution_allowed"] is False

        case_dir_16 = arch_ss / "cases" / "2026-11-26"
        assert len(list(case_dir_16.glob("16_put_cf_*.json.gz"))) == 0
        assert len(list(case_dir_16.glob("16_call_cf_*.json.gz"))) == 1
        assert archive.Archive(arch_ss).verify()["ok"] is True

        hand_ss = d_ss.path(expiry_ms) / "hands" / rows_ss[0]["hand_id"]
        src_ss = dossier.read(hand_ss / "evidence" / "source.json")
        assert len(src_ss["cases"]) == 1 and "put" in src_ss["absent_sides"]
        orig_ss = tracking.audit_originals(hand_ss)
        assert "禁止 Accept/Put" in orig_ss["input"] and "无合格价差候选" in orig_ss["input"]
        aud_ss = tracking.audit_record(hand_ss, clock[0], {}, detail=True)
        assert aud_ss["decision"]["judgment"]["action"] == "Accept"
        assert aud_ss["decision"]["judgment"]["side"] == "call"

        # 16:07Z repeat tick -> idempotent ALREADY_DECIDED, 1 LLM call, 1 Call archive case
        clock[0] = slot_ms + 7 * M
        rows_ss2 = tracking.auto_deal(d_ss, plan_r16, arch_ss, svc_ss, llm_config=llm_cfg_ss)
        assert rows_ss2[0]["status"] == "ALREADY_DECIDED"
        assert len(llm_calls_ss) == 1
        assert len(list(case_dir_16.glob("16_call_cf_*.json.gz"))) == 1

        # Downstream 16:10Z tracking sync + SHADOW_ONLY ExecutionCoordinator scan + post-expiry settle_due
        clock[0] = slot_ms + 10 * M
        sync_aux_ss()
        wb_ss.refresh(now_ms=clock[0], record_daily=False)
        sync_ss = tracking.sync(d_ss, hand_ss, svc_ss, check_due=True, archive_root=arch_ss)
        assert sync_ss["state"] == "active"

        from tools import astra_exec_store_v1 as exec_store, astra_execution_coordinator_v1 as exec_coord
        coord_dir = tmp_path / "coord_shadow_ss"
        coord_dir.mkdir(parents=True, exist_ok=True)
        coord = exec_coord.ExecutionCoordinator(
            mode="SHADOW_ONLY",
            env_tag="shadow",
            subaccount_id="shadow",
            store=exec_store.ExecutionStore(coord_dir / "exec.sqlite3"),
            adapter=None,
            status_path=coord_dir / "status.json",
            dossier_root=dos_ss,
        )
        coord.startup_init(now_ms=clock[0])
        coord_tick = coord.run_tick(now_ms=clock[0])
        assert coord_tick["mode"] == "SHADOW_ONLY" and coord_tick["writes_enabled"] is False

        clock[0] = expiry_ms + 5 * M
        settled_ss = list(d_ss.settle_due())
        assert len(settled_ss) == 1
        assert list(d_ss.settle_due()) == []

    # Counterexample A2: BOTH Put and Call confirmed market-absent (`above_max` on both -> MARKET_NO_ELIGIBLE_CANDIDATES) across 6 ticks + settle_due
    clock_both = [slot_0 - 8 * M]
    wb_miss, arch_miss, dos_miss, plan_both, slot_ms_b, expiry_ms_b, _, sync_aux_miss = _setup_nov26_workbench_and_archive(
        tmp_path / "miss_both", monkeypatch, clock=clock_both, put_mode={"above_max": True, "call_above_max": True},
    )
    monkeypatch.setenv("LLM_API_KEY", "k-never")

    with serving(wb_miss) as base_miss:
        svc_miss = post(base_miss, wb_miss)

        capsys.readouterr()
        rep_pre_b = tracking.prewarm(plan_both, arch_miss, svc_miss, clock=lambda: clock_both[0], public=dossier.public)
        cap_b = capsys.readouterr()
        assert rep_pre_b["state"] == "PREWARM"
        assert rep_pre_b["business_readiness"]["ready_for_deal"] is False
        assert rep_pre_b["business_readiness"]["status"] == "MARKET_NO_ELIGIBLE_CANDIDATES"
        assert cap_b.err.startswith("PREWARM_ALERT ")

        d_miss = dossier.Dossiers(dos_miss, clock=lambda: clock_both[0])
        never_llm = {"api_key": "k-never", "transport": lambda *_a, **_kw: pytest.fail("LLM must never run when both sides absent")}

        for attempt_idx, minute_offset in enumerate((2, 7, 12, 17, 22, 27), start=1):
            clock_both[0] = slot_ms_b + minute_offset * M
            sync_aux_miss()
            res, = tracking.auto_deal(d_miss, plan_both, arch_miss, svc_miss, llm_config=never_llm)
            assert res["status"] == "NO_EVIDENCE"
            assert res["block_state"] == "DATA_BLOCKED"
            assert res["attempts"] == attempt_idx
            assert res["window_closed"] is False

        hand_miss = d_miss.path(expiry_ms_b) / "hands" / "hand_66a598b4790172fa3fd56276"
        assert not (hand_miss / "evidence").exists()
        notes = [dossier.read(p) for p in (hand_miss / "issuance").glob("*.json")]
        assert len(notes) == 1
        assert notes[0]["state"] == "DATA_BLOCKED"
        assert notes[0]["attempt_count"] == 6
        assert notes[0]["diagnostics"]["readiness_status"] == "MARKET_NO_ELIGIBLE_CANDIDATES"
        assert "Put(" in notes[0]["reason"] and "Call(" in notes[0]["reason"]

        clock_both[0] = slot_ms_b + 32 * M
        res_close, = tracking.auto_deal(d_miss, plan_both, arch_miss, svc_miss, llm_config=never_llm)
        assert res_close["block_state"] == "DATA_BLOCKED" and res_close["window_closed"] is True
        notes_after = [dossier.read(p) for p in (hand_miss / "issuance").glob("*.json")]
        assert len(notes_after) == 1 and notes_after[0]["window_closed_at_ms"] == slot_ms_b + 32 * M

        clock_both[0] = expiry_ms_b + 5 * M
        assert len(list(d_miss.settle_due())) == 1
        assert list(d_miss.settle_due()) == []

    # Counterexample B: Stale Snapshot / Missing L4 RV24 / Incomplete HTTP 200 (`delta=None`) NEVER leak `cases`
    clock_b = [slot_0 + 2 * M]
    wb_b, arch_b, dos_b, plan_b, _, exp_b, _, sync_aux_b = _setup_nov26_workbench_and_archive(
        tmp_path / "stale_guard", monkeypatch, clock=clock_b,
    )
    with serving(wb_b) as base_b:
        svc_b = post(base_b, wb_b)
        # Seed fresh view at 16:02Z
        r_ok = svc_b("prepare_deal", {"target_expiry_ms": exp_b, "slot_ms": slot_0, "allow_refresh": True, "input_profile": "p0_strict@1"})
        assert r_ok["ready_for_deal"] is True and "cases" in r_ok

        # Advance 185s (inside 300s discovery TTL, OUTSIDE 180s QUOTE_HOLD_MS!) -> STALE_OR_PRE_SLOT_VIEW and NO `cases` leaked!
        clock_b[0] += 185_000
        r_185s = svc_b("prepare_deal", {"target_expiry_ms": exp_b, "slot_ms": slot_0, "allow_refresh": False, "input_profile": "p0_strict@1"})
        assert r_185s["ready_for_deal"] is False
        assert r_185s["readiness_status"] == "STALE_OR_PRE_SLOT_VIEW"
        assert "cases" not in r_185s

        # Advance 10m (16:12Z > 180s quote TTL) while Deribit cooldown blocks refresh -> STALE_OR_PRE_SLOT_VIEW / DERIBIT_429_COOLDOWN must NOT attach stale `cases`!
        clock_b[0] = slot_0 + 12 * M
        wb_b.data_engine.set_provider_cooldown("deribit", until_ms=clock_b[0] + 15_000, reason="DERIBIT_HTTP_429", now_ms=clock_b[0])
        r_stale_no_ref = svc_b("prepare_deal", {"target_expiry_ms": exp_b, "slot_ms": slot_0, "allow_refresh": False, "input_profile": "p0_strict@1"})
        assert r_stale_no_ref["ready_for_deal"] is False
        assert r_stale_no_ref["readiness_status"] == "STALE_OR_PRE_SLOT_VIEW"
        assert "cases" not in r_stale_no_ref

        # Missing L4 rv_24h_pct OR cached_fallback=True OR 6m-old L3 walls (< 20m STALE_MS, > 5m deal cutoff) -> L3_OR_L4_INCOMPLETE and NO `cases` leaked!
        clock_b[0] += 16_000  # advance past 15s Deribit cooldown
        sync_aux_b()
        wb_b._expiry_gamma["realized_vol"]["rv_24h_pct"] = None
        wb_b.refresh(now_ms=clock_b[0], record_daily=False)
        r_no_rv = svc_b("prepare_deal", {"target_expiry_ms": exp_b, "slot_ms": slot_0, "allow_refresh": False, "input_profile": "p0_strict@1"})
        assert r_no_rv["ready_for_deal"] is False
        assert r_no_rv["readiness_status"] == "L3_OR_L4_INCOMPLETE"
        assert "cases" not in r_no_rv

        sync_aux_b()
        wb_b._expiry_gamma["realized_vol"]["cached_fallback"] = True
        wb_b.refresh(now_ms=clock_b[0], record_daily=False)
        r_fb_rv = svc_b("prepare_deal", {"target_expiry_ms": exp_b, "slot_ms": slot_0, "allow_refresh": False, "input_profile": "p0_strict@1"})
        assert r_fb_rv["ready_for_deal"] is False
        assert r_fb_rv["readiness_status"] == "L3_OR_L4_INCOMPLETE"
        assert "cases" not in r_fb_rv

        sync_aux_b()
        wb_b._expiry_gamma["computed_at_ms"] = clock_b[0] - 6 * M  # 6m old (> 5m major_positions limit, < 20m STALE_MS)
        wb_b.refresh(now_ms=clock_b[0], record_daily=False)
        r_6m_l3 = svc_b("prepare_deal", {"target_expiry_ms": exp_b, "slot_ms": slot_0, "allow_refresh": False, "input_profile": "p0_strict@1"})
        assert r_6m_l3["ready_for_deal"] is False
        assert r_6m_l3["readiness_status"] == "L3_OR_L4_INCOMPLETE"
        assert "cases" not in r_6m_l3

    # Counterexample C: Tampered `content_sha256` on backfill case fails closed in `_auto_deal` (`dealt_cases = None`)
    clock_c = [slot_0 + 3 * M]
    wb_c, arch_c, dos_c, plan_c, _, exp_c, _, _ = _setup_nov26_workbench_and_archive(
        tmp_path / "tamper_guard", monkeypatch, clock=clock_c,
    )
    wb_c.refresh(now_ms=clock_c[0], record_daily=False)
    prep_valid = wb_c.shadow_tracking("prepare_deal", {"target_expiry_ms": exp_c, "slot_ms": slot_0, "allow_refresh": False, "input_profile": "p0_strict@1"})
    assert prep_valid["ready_for_deal"] is True
    tampered_prep = json.loads(json.dumps(prep_valid))
    tampered_prep["cases"]["put"]["build"]["content_sha256"] = "0" * 64
    d_c = dossier.Dossiers(dos_c, clock=lambda: clock_c[0])
    res_tamper, = tracking.auto_deal(
        d_c, plan_c, arch_c,
        lambda action, _b: tampered_prep if action == "prepare_deal" else {},
        llm_config={"api_key": "k", "transport": lambda *_a, **_kw: pytest.fail("must fail closed on bad hash")},
    )
    assert res_tamper["status"] == "NO_EVIDENCE"
    assert res_tamper["block_state"] == "DATA_BLOCKED"
    h_c = d_c.path(exp_c) / "hands" / "hand_66a598b4790172fa3fd56276"
    note_c = dossier.read(next((h_c / "issuance").glob("*.json")))
    assert note_c["diagnostics"]["backfill_persisted"]["validation_failed"] is True

    # Scenario D: Concurrent `tick.lock` held by Research Archive -> `backfill_slot_cases` returns `lock_busy=True` without corrupting chain, and verified in-memory `fallback_cases` still deals cleanly!
    clock_d = [slot_0 + 4 * M]
    wb_d, arch_d, dos_d, plan_d, _, exp_d, _, _ = _setup_nov26_workbench_and_archive(
        tmp_path / "lock_race", monkeypatch, clock=clock_d,
    )
    orig_bf = archive.backfill_slot_cases
    monkeypatch.setattr(
        dossier.archive,
        "backfill_slot_cases",
        lambda *a, **kw: orig_bf(*a, **{**kw, "lock_timeout_s": 0.05, "lock_poll_s": 0.02}),
    )
    reject_p5 = _strict_p5_reply_text("## Reject")
    with serving(wb_d) as base_d, archive._file_lock(arch_d / "_state" / "tick.lock"):
        svc_d = post(base_d, wb_d)
        d_d = dossier.Dossiers(dos_d, clock=lambda: clock_d[0])
        res_race, = tracking.auto_deal(
            d_d, plan_d, arch_d, svc_d,
            llm_config={
                "api_key": "k",
                "model": "deepseek-reasoner",
                "transport": lambda *_a, **_kw: {"choices": [{"finish_reason": "stop", "message": {"content": reject_p5}}]},
            },
        )
        assert res_race["status"] == "VALID" and res_race["decision"] == "Reject"
    assert archive.Archive(arch_d).verify()["ok"] is True


# ---------------------------------------------------------------------------
# 4. Model-Path Real-SSE Matrix: Truncation, Parse Retry, 429/400/502, Crash Recovery & Budget Guards
# ---------------------------------------------------------------------------

def test_model_path_real_sse_truncation_parse_retry_crash_recovery_and_budget_guards(tmp_path, monkeypatch):
    slot_0 = int(datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc).timestamp() * 1000)
    clock = [slot_0 + 2 * M]
    wb, arch_root, dossier_root, plan_r16, _, expiry_ms, _, sync_aux = _setup_nov26_workbench_and_archive(
        tmp_path / "model_matrix", monkeypatch, clock=clock,
    )
    wb.refresh(now_ms=clock[0], record_daily=False)
    prep = wb.shadow_tracking("prepare_deal", {"target_expiry_ms": expiry_ms, "slot_ms": slot_0, "allow_refresh": False, "input_profile": "p0_strict@1"})
    archive.backfill_slot_cases(arch_root, prep["cases"], slot_ms=slot_0, target_expiry_ms=expiry_ms, now_ms=clock[0])
    base_source = dossier.collect(
        arch_root, expiry_ms, slot_0, clock=lambda: clock[0], candidate_round="r16",
        fallback_cases=prep["cases"],
        provenance_override="targeted workbench verified native case + Deribit public read",
    )

    d = dossier.Dossiers(dossier_root, clock=lambda: clock[0], require_reply_provenance=True)
    accept_put = _strict_p5_reply_text("## Accept / Put", short_strike=60_000, long_strike=58_000)

    # 1) Real SSE IncompleteRead (dropped connection mid-stream) -> DELIVERY_UNKNOWN + partial_reply.json + ZERO replay
    h_inc = d.expect(expiry_ms, "AD_HOC", reference="sse-incomplete")
    d.deal(h_inc, base_source, input_profile="p0_strict@1")
    with _sse_mock_server(
        lambda _i, _b: {"status": 200, "reasoning": "SECRET_COT_STREAM", "text": "## Accept / Put\n半截输出", "drop_before_done": True}
    ) as (ep_inc, calls_inc):
        cfg_inc = tracking._llm_settings({"api_key": "k", "model": "deepseek-reasoner", "endpoint": ep_inc})
        r_inc1 = tracking._deal_hand(d, h_inc, None, cfg_inc, prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION, input_profile="p0_strict@1")
        assert r_inc1["status"] == "REQUEST_FAILED"
        r_inc2 = tracking._deal_hand(d, h_inc, None, cfg_inc, prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION, input_profile="p0_strict@1")
        assert r_inc2 == {"hand_id": h_inc.name, "status": "AUTO_ATTEMPTED", "last_status": "REQUEST_FAILED"}
        assert len(calls_inc) == 1
        att_inc = dossier.read(dossier.attempts(h_inc)[0])
        assert att_inc["configuration"]["partial_bytes"] > 0
        orig_inc = tracking.audit_originals(h_inc)
        assert orig_inc["response_status"] == "REQUEST_FAILED"
        assert orig_inc["partial_response"]["bytes_received"] > 0
        assert "SECRET_COT_STREAM" not in json.dumps(orig_inc, ensure_ascii=False)

    # 1b) Strict candidate Accept structural precheck violations (`invalidation="bad"`, `check_interval_ms=90000`, missing `condition_plan`, missing `accepted_conditions`) -> CONTRACT_VALIDATION_FAILED, ZERO retry & ZERO standby failover!
    for bad_label, mutator in (
        ("missing_plan", lambda ann: ann.pop("condition_plan", None)),
        ("bad_invalidation", lambda ann: ann["condition_plan"].__setitem__("invalidation", "bad_rule")),
        ("non_minute_interval", lambda ann: ann["condition_plan"].__setitem__("check_interval_ms", 90_000)),
        ("omitted_t0_outside", lambda ann: ann["condition_plan"].pop("require_outside_at_t0", None)),
    ):
        h_bad_cv = d.expect(expiry_ms, "AD_HOC", reference=f"sse-cv-{bad_label}")
        d.deal(h_bad_cv, base_source, input_profile="p0_strict@1")
        pub_part, json_part = accept_put.split("<ANNOTATION_JSON>")
        bad_ann = json.loads(json_part.split("</ANNOTATION_JSON>")[0].strip())
        mutator(bad_ann)
        bad_text = pub_part + "<ANNOTATION_JSON>\n" + json.dumps(bad_ann, ensure_ascii=False) + "\n</ANNOTATION_JSON>"
        seen_cv_models: list[str] = []

        def bad_cv_transport(_k, model, *_a, **_kw):
            seen_cv_models.append(model)
            return {"choices": [{"finish_reason": "stop", "message": {"content": bad_text}}]}

        cfg_bad_cv = tracking._llm_settings({
            "api_key": "k",
            "model": "deepseek-reasoner",
            "fallback_enabled": True,
            "fallback_model": "deepseek-chat",
            "transport": bad_cv_transport,
        })
        r_cv1 = tracking._deal_hand(d, h_bad_cv, None, cfg_bad_cv, prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION, input_profile="p0_strict@1")
        assert r_cv1["status"] == "PARSE_FAILED"
        att_cv = dossier.read(dossier.attempts(h_bad_cv)[0])
        assert att_cv["error_code"] == "CONTRACT_VALIDATION_FAILED"
        assert att_cv["contract_validation_status"] == "CONTRACT_FAILED"
        r_cv2 = tracking._deal_hand(d, h_bad_cv, None, cfg_bad_cv, prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION, input_profile="p0_strict@1")
        assert r_cv2 == {"hand_id": h_bad_cv.name, "status": "AUTO_ATTEMPTED", "last_status": "PARSE_FAILED"}
        assert seen_cv_models == ["deepseek-reasoner"]

    # Non-verbatim `put` JSON pointer alongside unique valid `Put` prose resolves deterministically on Attempt 1
    h_para = d.expect(expiry_ms, "AD_HOC", reference="sse-cv-paraphrased-put-resolved")
    d.deal(h_para, base_source, input_profile="p0_strict@1")
    pub_p, json_p = accept_put.split("<ANNOTATION_JSON>")
    ann_p = json.loads(json_p.split("</ANNOTATION_JSON>")[0].strip())
    ann_p["put"] = "非原文摘录改写句。"
    para_text = pub_p + "<ANNOTATION_JSON>\n" + json.dumps(ann_p, ensure_ascii=False) + "\n</ANNOTATION_JSON>"
    r_para = tracking._deal_hand(
        d,
        h_para,
        None,
        tracking._llm_settings({
            "api_key": "k",
            "model": "deepseek-reasoner",
            "transport": lambda *_a, **_kw: {"choices": [{"finish_reason": "stop", "message": {"content": para_text}}]},
        }),
        prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
        input_profile="p0_strict@1",
    )
    assert r_para["status"] == "VALID" and r_para["decision"] == "Accept"

    # 2) Real SSE `finish_reason="length"` truncation (hand_d62e pattern) -> PARSE_FAILED -> next-tick bounded retry -> VALID + SHADOW_LOCK_CREATED
    h_len = d.expect(expiry_ms, "AD_HOC", reference="sse-length-retry")
    d.deal(h_len, base_source, input_profile="p0_strict@1")
    with serving(wb) as wb_base, _sse_mock_server(
        lambda idx, _b: (
            {"status": 200, "text": "## Accept / Put\n输出达到上限被截断缺少JSON", "finish_reason": "length"}
            if idx == 0
            else {"status": 200, "text": accept_put, "finish_reason": "stop"}
        )
    ) as (ep_len, calls_len):
        svc = post(wb_base, wb)
        cfg_len = tracking._llm_settings({"api_key": "k", "model": "deepseek-reasoner", "endpoint": ep_len})
        r_len1 = tracking._deal_hand(d, h_len, svc, cfg_len, prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION, input_profile="p0_strict@1")
        assert r_len1["status"] == "PARSE_FAILED"
        clock[0] += 5_000
        sync_aux()
        wb.refresh(now_ms=clock[0], record_daily=False)
        r_len2 = tracking._deal_hand(d, h_len, svc, cfg_len, prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION, input_profile="p0_strict@1")
        assert r_len2["status"] == "VALID" and r_len2["decision"] == "Accept"
        assert r_len2["link"]["state"] == "SHADOW_LOCK_CREATED"
        assert len(calls_len) == 2
        atts_len = [dossier.read(p) for p in dossier.attempts(h_len)]
        assert [a["status"] for a in atts_len] == ["PARSE_FAILED", "VALID"]

    # 3) Real HTTP 429 (`Retry-After: 0`) retries once & succeeds vs HTTP 502 (`DELIVERY_UNKNOWN`) never retries
    h_429 = d.expect(expiry_ms, "AD_HOC", reference="sse-429-retry")
    d.deal(h_429, base_source, input_profile="p0_strict@1")
    reject_put = _strict_p5_reply_text("## Reject")
    with _sse_mock_server(
        lambda idx, _b: (
            {"status": 429, "retry_after": "0", "error_body": {"error": {"message": "rate_limit"}}}
            if idx == 0
            else {"status": 200, "text": reject_put, "finish_reason": "stop"}
        )
    ) as (ep_429, calls_429):
        cfg_429 = tracking._llm_settings({"api_key": "k", "model": "deepseek-reasoner", "endpoint": ep_429})
        r_429_1 = tracking._deal_hand(d, h_429, None, cfg_429, prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION, input_profile="p0_strict@1")
        assert r_429_1["status"] == "REQUEST_FAILED"
        clock[0] += 2_000
        r_429_2 = tracking._deal_hand(d, h_429, None, cfg_429, prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION, input_profile="p0_strict@1")
        assert r_429_2["status"] == "VALID" and r_429_2["decision"] == "Reject"
        assert len(calls_429) == 2

    h_502 = d.expect(expiry_ms, "AD_HOC", reference="sse-502-noretry")
    d.deal(h_502, base_source, input_profile="p0_strict@1")
    with _sse_mock_server(lambda _i, _b: {"status": 502}) as (ep_502, calls_502):
        cfg_502 = tracking._llm_settings({"api_key": "k", "model": "deepseek-reasoner", "endpoint": ep_502})
        assert tracking._deal_hand(d, h_502, None, cfg_502, prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION, input_profile="p0_strict@1")["status"] == "REQUEST_FAILED"
        assert tracking._deal_hand(d, h_502, None, cfg_502, prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION, input_profile="p0_strict@1")["status"] == "AUTO_ATTEMPTED"
        assert len(calls_502) == 1

    # 4) Service budget (<45s) and reply_deadline (<45s) guards skip cleanly before dispatching HTTP
    h_guard = d.expect(expiry_ms, "SCHEDULED", plan=plan_r16, slot=slot_0)
    d.deal(h_guard, base_source, input_profile="p0_strict@1")
    cfg_never = tracking._llm_settings({"api_key": "k", "transport": lambda *_a, **_kw: pytest.fail("must skip before HTTP")})
    r_svc_skip = tracking._deal_hand(
        d, h_guard, None, cfg_never,
        prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
        input_profile="p0_strict@1",
        service_deadline_mono=time.monotonic() + 30.0,
    )
    assert r_svc_skip["status"] == "SERVICE_BUDGET_DEFERRED"

    # Advance clock to 20s before reply_deadline (`hand_frozen_at_ms + 30m - 20s` -> `< 45s` remaining)
    frozen_ms = d.verify_evidence(h_guard)["hand_frozen_at_ms"]
    clock[0] = frozen_ms + plan_r16["reply_grace_ms"] - 20_000
    r_dl_skip = tracking._deal_hand(
        d, h_guard, None, cfg_never,
        prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
        input_profile="p0_strict@1",
    )
    assert r_dl_skip["status"] == "REPLY_WINDOW_CLOSED"

    # 5) Process crash recovery: `reply.json` written to disk before crash -> `recover_initial` completes VALID + shadow lock with ZERO HTTP calls
    clock[0] = slot_0 + 3 * M
    sync_aux()
    wb.refresh(now_ms=clock[0], record_daily=False)
    crash_dossier_root = tmp_path / "crash_dossiers"
    wb.config = replace(wb.config, shadow_dossier_root=crash_dossier_root)
    d_crash = dossier.Dossiers(crash_dossier_root, clock=lambda: clock[0], require_reply_provenance=True)
    h_crash = d_crash.expect(expiry_ms, "SCHEDULED", plan=plan_r16, slot=slot_0)
    d_crash.deal(h_crash, base_source, input_profile="p0_strict@1")
    orig_respond = dossier.Dossiers.respond
    crash_flag = {"crash_once": True}

    def crash_before_respond(self_d, *args, **kwargs):
        if crash_flag["crash_once"]:
            crash_flag["crash_once"] = False
            raise OSError("simulated power loss after reply.json persisted")
        return orig_respond(self_d, *args, **kwargs)

    with serving(wb) as wb_base, _sse_mock_server(
        lambda _i, _b: {"status": 200, "text": accept_put, "finish_reason": "stop"}
    ) as (ep_crash, calls_crash):
        svc = post(wb_base, wb)
        cfg_crash = tracking._llm_settings({"api_key": "k", "model": "deepseek-reasoner", "endpoint": ep_crash})
        monkeypatch.setattr(dossier.Dossiers, "respond", crash_before_respond)
        with pytest.raises(OSError, match="simulated power loss"):
            tracking._deal_hand(
                d_crash, h_crash, svc, cfg_crash,
                prompt_version=review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
                input_profile="p0_strict@1",
            )
        assert len(calls_crash) == 1
        assert list((h_crash / "api_calls").glob("*/reply.json"))
        assert not (h_crash / "decision.json").exists()

        monkeypatch.setattr(dossier.Dossiers, "respond", orig_respond)
        recovered = tracking.recover_initial(d_crash, svc)
        rec_row = next(r for r in recovered if r["hand_id"] == h_crash.name)
        assert rec_row["status"] == "ALREADY_DECIDED" and rec_row["decision"] == "Accept"
        assert rec_row["link"]["state"] == "SHADOW_LOCK_CREATED"
        assert len(calls_crash) == 1


def test_single_side_contract_reject_illegal_accept_and_transient_recovery(tmp_path, monkeypatch):
    slot_0 = int(datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc).timestamp() * 1000)

    # 1. Single-side Reject on Put-absent / Call-only hand succeeds VALID cleanly with zero shadow lock
    clk_rej = [slot_0 + 2 * M]
    wb_r, arch_r, dos_r, plan_r, _, exp_r, _, sync_r = _setup_nov26_workbench_and_archive(
        tmp_path / "ss_reject", monkeypatch, clock=clk_rej, put_mode={"above_max": True},
    )
    rej_text = _strict_p5_single_side_reply_text("## Reject", available_side="call", short_strike=61_000, long_strike=62_000)
    with serving(wb_r) as base_r, _sse_mock_server(lambda _i, _b: {"status": 200, "text": rej_text, "finish_reason": "stop"}) as (ep_r, calls_r):
        svc_r = post(base_r, wb_r)
        d_r = dossier.Dossiers(dos_r, clock=lambda: clk_rej[0], require_reply_provenance=True)
        res_r, = tracking.auto_deal(
            d_r, plan_r, arch_r, svc_r,
            llm_config={"api_key": "k-r", "model": "deepseek-reasoner", "endpoint": ep_r,
                        "prompt_version": review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION, "input_profile": "p0_strict@1"},
        )
        assert res_r["status"] == "VALID" and res_r["decision"] == "Reject"
        assert len(calls_r) == 1
        hand_r = d_r.path(exp_r) / "hands" / res_r["hand_id"]
        assert not (hand_r / "tracking" / "link.json").exists()

    # 2. Single-side illegal Accept/Put (absent side!) is hard-blocked (`PARSE_FAILED`), zero decision, zero shadow lock
    clk_bad = [slot_0 + 2 * M]
    wb_b, arch_b, dos_b, plan_b, _, exp_b, _, sync_b = _setup_nov26_workbench_and_archive(
        tmp_path / "ss_illegal", monkeypatch, clock=clk_bad, put_mode={"above_max": True},
    )
    illegal_put_accept = _strict_p5_reply_text("## Accept / Put", short_strike=60_000, long_strike=58_000, require_outside_at_t0=True)
    with serving(wb_b) as base_b, _sse_mock_server(lambda _i, _b: {"status": 200, "text": illegal_put_accept, "finish_reason": "stop"}) as (ep_b, calls_b):
        svc_b = post(base_b, wb_b)
        d_b = dossier.Dossiers(dos_b, clock=lambda: clk_bad[0], require_reply_provenance=True)
        res_b, = tracking.auto_deal(
            d_b, plan_b, arch_b, svc_b,
            llm_config={"api_key": "k-b", "model": "deepseek-reasoner", "endpoint": ep_b,
                        "prompt_version": review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION, "input_profile": "p0_strict@1"},
        )
        assert res_b["status"] == "PARSE_FAILED"
        hand_b = d_b.path(exp_b) / "hands" / res_b["hand_id"]
        assert not (hand_b / "decision.json").exists()
        assert not (hand_b / "tracking" / "link.json").exists()
        orig_b = tracking.audit_originals(hand_b)
        assert orig_b["response_status"] == "PARSE_FAILED"
        att_b = dossier.read(next((hand_b / "responses").glob("*/attempt.json")))
        assert "forbidden when put side is absent" in str(att_b.get("error") or "")

    # 3. Transient missing Put (`missing_delta` or `429`) MUST NOT emit MARKET_SINGLE_SIDE_ONLY; once recovered, deals both sides!
    for transient_key in ("missing_delta", "429"):
        clk_t = [slot_0 + 2 * M]
        put_state = {transient_key: True}
        wb_t, arch_t, dos_t, plan_t, _, exp_t, _, sync_t = _setup_nov26_workbench_and_archive(
            tmp_path / f"transient_{transient_key}", monkeypatch, clock=clk_t, put_mode=put_state,
        )
        with serving(wb_t) as base_t:
            svc_t = post(base_t, wb_t)
            d_t = dossier.Dossiers(dos_t, clock=lambda: clk_t[0])
            res_t1, = tracking.auto_deal(
                d_t, plan_t, arch_t, svc_t,
                llm_config={"api_key": "k-t", "transport": lambda *_a, **_kw: pytest.fail("must not deal on transient missing")},
            )
            assert res_t1["status"] == "NO_EVIDENCE" and res_t1["block_state"] == "DATA_BLOCKED"
            # Wait past 429 cooldown if active, clear transient fault -> deals dual-side cleanly
            put_state[transient_key] = False
            clk_t[0] += 15_000
            sync_t()
            wb_t.refresh(now_ms=clk_t[0], record_daily=False)
            prep_t2 = svc_t("prepare_deal", {"target_expiry_ms": exp_t, "slot_ms": slot_0, "allow_refresh": True, "input_profile": "p0_strict@1"})
            assert prep_t2["readiness_status"] == "SOURCE_READY" and set(prep_t2["cases"]) == {"put", "call"}

    # 4. Structural 0-qualified-spread absence (`total_qualified == 0`, discovery emits `capture_state="PARTIAL"`) -> MARKET_SINGLE_SIDE_ONLY cleanly!
    clk_struct = [slot_0 + 2 * M]
    wb_st, _, _, _, _, exp_st, _, _ = _setup_nov26_workbench_and_archive(
        tmp_path / "structural_single", monkeypatch, clock=clk_struct,
    )
    orig_pub_get = discovery._public_get
    monkeypatch.setattr(
        discovery,
        "_public_get",
        lambda b, p, pr, t: (
            {"result": [r for r in orig_pub_get(b, p, pr, t)["result"] if not r["instrument_name"].endswith("-P")]}
            if p == "/public/get_instruments"
            else orig_pub_get(b, p, pr, t)
        ),
    )
    st_view = wb_st.refresh(now_ms=clk_struct[0], record_daily=False)
    prep_st = wb_st.shadow_tracking(
        "prepare_deal",
        {"target_expiry_ms": exp_st, "slot_ms": slot_0, "allow_refresh": False, "input_profile": "p0_strict@1"},
    )
    assert prep_st["readiness_status"] == "MARKET_SINGLE_SIDE_ONLY"
    assert prep_st["ready_for_deal"] is True
    assert set(prep_st["cases"]) == {"call"}
    assert "put" in prep_st["absent_sides"]
    sr_st = tracking.evaluate_source_readiness(
        workbench_view=st_view, now_ms=clk_struct[0], target_expiry_ms=exp_st, slot_ms=slot_0, input_profile="p0_strict@1",
    )
    assert sr_st["status"] == "MARKET_SINGLE_SIDE_ONLY"
    assert sr_st["checks"]["accept_lock_ready"] is True
