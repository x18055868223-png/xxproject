"""Integration, collector default-off guard, deployment preflight, and isolation tests (Categories F & G)."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

WORKTREE_ROOT = Path(__file__).resolve().parents[3]
if str(WORKTREE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKTREE_ROOT))

from deploy.gfri_v11.preflight_check import parse_env_file, run_host_checks, verify_env_safety
from research.gfri_v11 import collector
from research.gfri_v11.config import DEFAULT_CONFIG, ShadowFlags
from research.gfri_v11.history import ShadowLedger


def _make_raw_gex(ts_iso: str = "2026-11-18T14:00:00Z", spot: float = 64000.0) -> dict:
    return {
        "asset": "BTC",
        "asset_price": spot,
        "timestamp": ts_iso,
        "data_timestamp": ts_iso,
        "computed_at": ts_iso,
        "stale": False,
        "snapshot_id": "snap-int-1",
        "universe_hash": "u-hash-1",
        "methodology_version": "deribit_coin_only_v1",
        "universe_policy": "coin_margined_only",
        "total_gex": -120_000_000.0,
        "quality": {"status": "DEGRADED", "reasons": ["secondary_venues_unavailable:bybit,okx,binance"], "total_open_interest": 394000.0},
        "hedging_flows": {
            "total_gamma": -120_000_000.0,
            "units": {
                "total_gamma": "usd_delta_per_1pct_spot_move",
                "hedging_usd": "usd_notional",
            },
            "scenario_sets": {"price_only": {"iv_mode": "fixed", "time_forward_hours": 0}},
            "price_only_curve": [
                {"move_pct": -1.0, "hedging_usd": -120_000_000.0},
                {"move_pct": 0.0, "hedging_usd": 0.0},
                {"move_pct": 1.0, "hedging_usd": 120_000_000.0},
            ],
            "model": {"dealer_position_model": "oi_proxy_call_long_put_short", "dealer_positions_observed": False},
        },
    }


def _make_deribit_book(ts_ms: int, spot: float = 64000.0, depth_total_usd: float = 40_000_000.0) -> dict:
    bids = [[spot - (i + 1) * 50.0, depth_total_usd / 15.0] for i in range(15)]
    asks = [[spot + (i + 1) * 50.0, depth_total_usd / 15.0] for i in range(15)]
    return {
        "result": {
            "timestamp": ts_ms,
            "best_bid_price": bids[0][0],
            "best_ask_price": asks[0][0],
            "bids": bids,
            "asks": asks,
        }
    }


def _make_deribit_inst() -> dict:
    return {"result": {"instrument_name": "BTC-PERPETUAL", "quote_currency": "USD", "instrument_type": "reversed", "contract_size": 10.0}}


class TestCollectorAndIsolation(unittest.TestCase):
    def test_scheduled_mode_default_off_exits_zero_without_network(self) -> None:
        old_env = {k: os.environ.get(k) for k in ("GFRI_COLLECTOR_ENABLED", "GFRI_DECISION_AUTHORITY", "GFRI_PLAYER_INTEGRATION_ENABLED", "GFRI_LIVE_VETO_ENABLED")}
        try:
            for k in old_env:
                os.environ.pop(k, None)
            rc = collector.main(["--scheduled"])
            self.assertEqual(rc, 0)
        finally:
            for k, v in old_env.items():
                if v is not None:
                    os.environ[k] = v

    def test_preflight_example_env_is_locked_shadow_only_and_disabled(self) -> None:
        env_path = WORKTREE_ROOT / "deploy" / "gfri_v11" / "gfri_v11.env.example"
        vals = parse_env_file(env_path)
        self.assertEqual(verify_env_safety(vals, require_disabled=True), [])

    def test_locked_flags_cannot_enable_live_veto_or_player_integration(self) -> None:
        with self.assertRaises(ValueError):
            ShadowFlags(live_veto_enabled=True).validate_locked()
        with self.assertRaises(ValueError):
            ShadowFlags(player_integration_enabled=True).validate_locked()
        with self.assertRaises(ValueError):
            ShadowFlags(decision_authority="LIVE_VETO").validate_locked()

    def test_end_to_end_core_history_policy_cycle_and_ledger_append(self) -> None:
        base_ts_ms = 1_795_010_400_000  # 2026-11-18T14:00:00Z
        gex_raw = _make_raw_gex()
        inst_raw = _make_deribit_inst()
        call_idx = {"book": 0}

        def fake_fetcher(url: str) -> dict:
            if "gex-latest" in url:
                raw = json.dumps(gex_raw).encode()
                return {"status": 200, "received_at_ms": base_ts_ms + 500, "sha256": hashlib.sha256(raw).hexdigest(), "byte_len": len(raw), "payload": gex_raw}
            if "get_instrument" in url:
                raw = json.dumps(inst_raw).encode()
                return {"status": 200, "received_at_ms": base_ts_ms + 600, "sha256": hashlib.sha256(raw).hexdigest(), "byte_len": len(raw), "payload": inst_raw}
            if "get_order_book" in url:
                idx = call_idx["book"]
                call_idx["book"] += 1
                ob_ts = base_ts_ms + 1_000 + idx * 20_000
                book = _make_deribit_book(ob_ts)
                raw = json.dumps(book).encode()
                return {"status": 200, "received_at_ms": ob_ts + 80, "sha256": hashlib.sha256(raw).hexdigest(), "byte_len": len(raw), "payload": book}
            raise RuntimeError("Unexpected URL")

        cfg = replace(DEFAULT_CONFIG, confirm_interval_sec=0.0)
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            out1 = collector.run_shadow_cycle(
                cfg=cfg,
                state_dir=state_dir,
                append_ledger=True,
                include_okx=False,
                interval_sec=0.0,
                fetcher=fake_fetcher,
                sleeper=lambda _: None,
                git_commit="testcommit",
            )
            audit = out1["audit_record"]
            self.assertEqual(audit["decision_authority"], "SHADOW_ONLY")
            self.assertFalse(audit["live_veto_enabled"])
            self.assertFalse(audit["player_integration_enabled"])
            self.assertEqual(audit["data_quality"], "DEGRADED")
            self.assertIn("secondary_venues_unavailable:bybit,okx,binance", audit["failure_reasons"])
            self.assertEqual(audit["shadow_decision"], "WARMING_UP")
            self.assertTrue(out1["confirmed_cycle"]["shares_same_gex_snapshot"])
            self.assertEqual(out1["confirmed_cycle"]["independent_gamma_estimates"], 1)
            self.assertIn("GFRI v1.1", out1["console_text"])

            records = ShadowLedger.load_records(state_dir / "gfri_shadow_ledger.jsonl")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["git_commit"], "testcommit")

    def test_upstream_fetch_failure_is_non_blocking_unknown_and_exits_zero(self) -> None:
        base_ts_ms = 1_795_010_400_000
        gex_raw = _make_raw_gex()
        inst_raw = _make_deribit_inst()

        # Case 1: GEXMonitor fails completely -> emits UNKNOWN audit record, appends to ledger, returns cleanly
        def fail_gex_fetcher(url: str) -> dict:
            if "gex-latest" in url:
                raise ConnectionError("gex_down")
            if "get_instrument" in url:
                raw = json.dumps(inst_raw).encode()
                return {"status": 200, "received_at_ms": base_ts_ms, "sha256": hashlib.sha256(raw).hexdigest(), "byte_len": len(raw), "payload": inst_raw}
            book = _make_deribit_book(base_ts_ms)
            raw = json.dumps(book).encode()
            return {"status": 200, "received_at_ms": base_ts_ms, "sha256": hashlib.sha256(raw).hexdigest(), "byte_len": len(raw), "payload": book}

        cfg = replace(DEFAULT_CONFIG, confirm_interval_sec=0.0)
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            out_fail = collector.run_shadow_cycle(
                cfg=cfg,
                state_dir=state_dir,
                append_ledger=True,
                include_okx=False,
                interval_sec=0.0,
                fetcher=fail_gex_fetcher,
                sleeper=lambda _: None,
            )
            audit = out_fail["audit_record"]
            self.assertEqual(audit["data_quality"], "UNKNOWN")
            self.assertEqual(audit["shadow_decision"], "UNKNOWN")
            self.assertTrue(any(r.startswith("gex_latest_fetch_failed") for r in audit["failure_reasons"]))
            self.assertEqual(len(ShadowLedger.load_records(state_dir / "gfri_shadow_ledger.jsonl")), 1)

        # Case 2: 1 of 3 Deribit book calls fails while 2 succeed -> still confirms (2/3)
        calls = {"n": 0}

        def partial_fail_book(url: str) -> dict:
            if "gex-latest" in url:
                raw = json.dumps(gex_raw).encode()
                return {"status": 200, "received_at_ms": base_ts_ms + 200, "sha256": hashlib.sha256(raw).hexdigest(), "byte_len": len(raw), "payload": gex_raw}
            if "get_instrument" in url:
                raw = json.dumps(inst_raw).encode()
                return {"status": 200, "received_at_ms": base_ts_ms + 300, "sha256": hashlib.sha256(raw).hexdigest(), "byte_len": len(raw), "payload": inst_raw}
            calls["n"] += 1
            if calls["n"] == 2:
                raise TimeoutError("transient_ob_timeout")
            ob_ts = base_ts_ms + calls["n"] * 10_000
            book = _make_deribit_book(ob_ts)
            raw = json.dumps(book).encode()
            return {"status": 200, "received_at_ms": ob_ts + 50, "sha256": hashlib.sha256(raw).hexdigest(), "byte_len": len(raw), "payload": book}

        out_partial = collector.run_shadow_cycle(
            cfg=cfg,
            include_okx=False,
            interval_sec=0.0,
            fetcher=partial_fail_book,
            sleeper=lambda _: None,
        )
        self.assertEqual(out_partial["audit_record"]["confirmation_status"], "CONFIRMED")
        self.assertEqual(out_partial["audit_record"]["ratios"]["put"]["valid_count"], 2)
        self.assertTrue(any("deribit_book_s2_fetch_failed" in r for r in out_partial["audit_record"]["failure_reasons"]))

    def test_env_runtime_overrides_raw_capture_dir_and_deploy_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            raw_dir = Path(tmp) / "raw"
            env_backup = dict(os.environ)
            try:
                os.environ["GFRI_COLLECTOR_ENABLED"] = "1"
                os.environ["GFRI_INCLUDE_OKX_REF"] = "0"
                os.environ["GFRI_CONFIRM_SAMPLES"] = "4"
                os.environ["GFRI_CONFIRM_INTERVAL_SEC"] = "5"
                os.environ["GFRI_OB_SKEW_MAX_MS"] = "300000"
                os.environ["GFRI_OB_AGE_MAX_MS"] = "45000"
                os.environ["GFRI_OB_RECV_LAG_MAX_MS"] = "25000"
                os.environ["GFRI_SOURCE_AGE_MAX_MS"] = "1800000"
                os.environ["GFRI_STATE_DIR"] = str(state_dir)
                os.environ["GFRI_RAW_CAPTURE_DIR"] = str(raw_dir)
                resolved_cfg, inc_okx, res_state, res_raw = collector.resolve_runtime_from_env()
                self.assertTrue(resolved_cfg.flags.collector_enabled)
                self.assertFalse(inc_okx)
                self.assertEqual(resolved_cfg.confirm_sample_count, 4)
                self.assertEqual(resolved_cfg.confirm_interval_sec, 5.0)
                self.assertEqual(resolved_cfg.max_ob_skew_ms, 300_000)
                self.assertEqual(resolved_cfg.max_ob_age_ms, 45_000)
                self.assertEqual(resolved_cfg.max_ob_receive_lag_ms, 25_000)
                self.assertEqual(resolved_cfg.max_source_age_ms, 1_800_000)
                self.assertEqual(res_state, state_dir)
                self.assertEqual(res_raw, raw_dir)

                # Save raw bundle check during cycle
                base_ts_ms = 1_795_010_400_000
                gex_raw = _make_raw_gex()
                inst_raw = _make_deribit_inst()

                def ok_fetcher(url: str) -> dict:
                    payload = gex_raw if "gex-latest" in url else (inst_raw if "get_instrument" in url else _make_deribit_book(base_ts_ms))
                    raw = json.dumps(payload).encode()
                    return {"status": 200, "received_at_ms": base_ts_ms, "sha256": hashlib.sha256(raw).hexdigest(), "byte_len": len(raw), "payload": payload}

                collector.run_shadow_cycle(
                    cfg=replace(resolved_cfg, confirm_sample_count=2, confirm_interval_sec=0.0),
                    state_dir=res_state,
                    raw_capture_dir=res_raw,
                    append_ledger=True,
                    include_okx=False,
                    interval_sec=0.0,
                    fetcher=ok_fetcher,
                    sleeper=lambda _: None,
                )
                saved_cycles = list(raw_dir.glob("cycle_*"))
                self.assertEqual(len(saved_cycles), 1)
                self.assertTrue((saved_cycles[0] / "gex_latest.json").exists())
            finally:
                os.environ.clear()
                os.environ.update(env_backup)

        # Deployment scripts sanity checks (High #4 / Medium #7 / PYTHONPATH)
        deploy_dir = WORKTREE_ROOT / "deploy" / "gfri_v11"
        enable_sh = (deploy_dir / "enable_collector.sh").read_text(encoding="utf-8")
        service_txt = (deploy_dir / "gfri-shadow-collector.service").read_text(encoding="utf-8")
        self.assertLess(enable_sh.index('if [ ! -f "$ENV_FILE" ]'), enable_sh.index("preflight_check.py"))
        self.assertIn("chown -R bitnami:bitnami /opt/gfri-shadow/state /opt/gfri-shadow/raw_captures", enable_sh)
        self.assertIn("Environment=PYTHONPATH=/opt/gfri-shadow/current", service_txt)

    def test_p01_spec_cache_fallback_and_expired_cache_fails_closed(self) -> None:
        base_ts_ms = 1_795_010_400_000
        gex_raw = _make_raw_gex()
        inst_raw = _make_deribit_inst()
        inst_should_fail = {"flag": False}

        def fetcher(url: str) -> dict:
            if "get_instrument" in url:
                if inst_should_fail["flag"]:
                    raise ConnectionError("instrument_endpoint_down")
                raw = json.dumps(inst_raw).encode()
                return {"status": 200, "received_at_ms": base_ts_ms, "raw_bytes": raw, "sha256": hashlib.sha256(raw).hexdigest(), "byte_len": len(raw), "payload": inst_raw}
            payload = gex_raw if "gex-latest" in url else _make_deribit_book(base_ts_ms)
            raw = json.dumps(payload).encode()
            return {"status": 200, "received_at_ms": base_ts_ms, "raw_bytes": raw, "sha256": hashlib.sha256(raw).hexdigest(), "byte_len": len(raw), "payload": payload}

        cfg = replace(DEFAULT_CONFIG, confirm_interval_sec=0.0, instrument_cache_ttl_ms=3600_000)
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            # 1. Missing instrument with empty cache -> UNKNOWN
            inst_should_fail["flag"] = True
            out_no_cache = collector.run_shadow_cycle(cfg=cfg, state_dir=state_dir, include_okx=False, interval_sec=0.0, fetcher=fetcher, sleeper=lambda _: None)
            self.assertEqual(out_no_cache["audit_record"]["data_quality"], "UNKNOWN")
            self.assertIn("missing_or_unverified_deribit_instrument", out_no_cache["audit_record"]["failure_reasons"])

            # 2. Succeed live -> writes deribit_spec_cache.json
            inst_should_fail["flag"] = False
            out_ok = collector.run_shadow_cycle(cfg=cfg, state_dir=state_dir, include_okx=False, interval_sec=0.0, fetcher=fetcher, sleeper=lambda _: None)
            self.assertEqual(out_ok["audit_record"]["data_quality"], "DEGRADED")
            self.assertTrue((state_dir / "deribit_spec_cache.json").exists())

            # 3. ConnectionError within TTL -> verified_cache works
            inst_should_fail["flag"] = True
            out_cached = collector.run_shadow_cycle(cfg=cfg, state_dir=state_dir, include_okx=False, interval_sec=0.0, fetcher=fetcher, sleeper=lambda _: None)
            self.assertEqual(out_cached["observations"][0]["provenance"]["deribit_spec_source"], "verified_cache")
            self.assertEqual(out_cached["audit_record"]["data_quality"], "DEGRADED")

            # 4. Expired cache -> fails closed UNKNOWN
            cache_obj = json.loads((state_dir / "deribit_spec_cache.json").read_text(encoding="utf-8"))
            cache_obj["expires_at_ms"] = base_ts_ms - 1
            (state_dir / "deribit_spec_cache.json").write_text(json.dumps(cache_obj), encoding="utf-8")
            out_expired = collector.run_shadow_cycle(cfg=cfg, state_dir=state_dir, include_okx=False, interval_sec=0.0, fetcher=fetcher, sleeper=lambda _: None)
            self.assertEqual(out_expired["audit_record"]["data_quality"], "UNKNOWN")
            self.assertIn("deribit_instrument_cache_expired", out_expired["audit_record"]["failure_reasons"])

    def test_p07_raw_archive_hashes_manifest_and_failure_statuses(self) -> None:
        base_ts_ms = 1_795_010_400_000
        gex_raw = _make_raw_gex()
        inst_raw = _make_deribit_inst()

        # Deliberately non-canonical whitespace so raw_http_sha256 != reserialized_json_sha256
        def wire_fetcher(url: str) -> dict:
            payload = gex_raw if "gex-latest" in url else (inst_raw if "get_instrument" in url else _make_deribit_book(base_ts_ms))
            raw_bytes = (json.dumps(payload, indent=4) + "\n").encode("utf-8")
            return {"status": 200, "received_at_ms": base_ts_ms, "raw_bytes": raw_bytes, "sha256": hashlib.sha256(raw_bytes).hexdigest(), "byte_len": len(raw_bytes), "payload": payload}

        cfg = replace(DEFAULT_CONFIG, confirm_interval_sec=0.0)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Case A: Clean write preserves exact wire bytes & manifest
            out_ok = collector.run_shadow_cycle(cfg=cfg, state_dir=root / "s1", raw_capture_dir=root / "raw_ok", include_okx=False, interval_sec=0.0, fetcher=wire_fetcher, sleeper=lambda _: None)
            arc = out_ok["audit_record"]["raw_archive_status"]
            self.assertEqual(arc["status"], "SAVED")
            self.assertTrue(arc["complete_replayable"])
            gex_meta = arc["files"]["gex_latest"]
            self.assertEqual(gex_meta["verified_disk_sha256"], gex_meta["raw_http_sha256"])
            self.assertNotEqual(gex_meta["raw_http_sha256"], gex_meta["reserialized_json_sha256"])
            manifest = json.loads((Path(arc["cycle_dir"]) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["computed_summary"]["put_gfri_confirmed"], out_ok["audit_record"]["put"]["gfri_confirmed"])

            # Case B: Unwritable target (regular file blocking cycle dir) -> FAILED, complete_replayable=False
            bad_raw = root / "blocked_file"
            bad_raw.write_text("not_a_dir", encoding="utf-8")
            out_fail = collector.run_shadow_cycle(cfg=cfg, state_dir=root / "s2", raw_capture_dir=bad_raw, include_okx=False, interval_sec=0.0, fetcher=wire_fetcher, sleeper=lambda _: None)
            self.assertEqual(out_fail["audit_record"]["raw_archive_status"]["status"], "FAILED")
            self.assertFalse(out_fail["audit_record"]["raw_archive_status"]["complete_replayable"])
            self.assertTrue(any(r.startswith("raw_archive_write_failed:") for r in out_fail["audit_record"]["failure_reasons"]))

            # Case C: Partial failure (one expected book file blocked by existing directory) -> PARTIAL
            freeze_expected = base_ts_ms + 50
            partial_dir = root / "raw_partial"
            (partial_dir / f"cycle_{freeze_expected}" / "deribit_book_2.json").mkdir(parents=True, exist_ok=True)
            out_part = collector.run_shadow_cycle(cfg=cfg, state_dir=root / "s3", raw_capture_dir=partial_dir, include_okx=False, interval_sec=0.0, fetcher=wire_fetcher, sleeper=lambda _: None)
            self.assertEqual(out_part["audit_record"]["raw_archive_status"]["status"], "PARTIAL")
            self.assertFalse(out_part["audit_record"]["raw_archive_status"]["complete_replayable"])
            self.assertTrue(any("deribit_book_2" in r for r in out_part["audit_record"]["failure_reasons"]))

            # Case D: manifest.json write blocked by directory -> audit_record["raw_archive_status"] synced to PARTIAL/complete_replayable=False
            manifest_block_dir = root / "raw_manifest_fail"
            (manifest_block_dir / f"cycle_{freeze_expected}" / "manifest.json").mkdir(parents=True, exist_ok=True)
            out_mf = collector.run_shadow_cycle(cfg=cfg, state_dir=root / "s4", raw_capture_dir=manifest_block_dir, include_okx=False, interval_sec=0.0, fetcher=wire_fetcher, sleeper=lambda _: None)
            self.assertEqual(out_mf["audit_record"]["raw_archive_status"]["status"], "PARTIAL")
            self.assertFalse(out_mf["audit_record"]["raw_archive_status"]["complete_replayable"])
            self.assertTrue(any(r.startswith("raw_archive_manifest_failed:") for r in out_mf["audit_record"]["failure_reasons"]))

    def test_p02_end_to_end_single_side_truncation_preserves_valid_side_policy(self) -> None:
        base_ts_ms = 1_795_010_400_000
        inst_raw = _make_deribit_inst()
        mode = {"truncate_put": False, "call_depth": 80_000_000.0}  # 1.5x -> 3.0x when halved

        def e2e_fetcher(ts_ref: list[int]):
            def _f(url: str) -> dict:
                t = ts_ref[0]
                if "gex-latest" in url:
                    p = _make_raw_gex()
                    p["data_timestamp"] = t - 2_000
                    p["timestamp"] = t - 2_000
                    p["computed_at"] = t - 1_000
                elif "get_instrument" in url:
                    p = inst_raw
                else:
                    p = _make_deribit_book(t, depth_total_usd=mode["call_depth"])
                    if mode["truncate_put"]:
                        # Truncate bids to within -0.2% only while asks cover >1%
                        p["result"]["bids"] = [[64000.0 - (i + 1) * 5.0, 1_000_000.0] for i in range(15)]
                raw = json.dumps(p).encode()
                return {"status": 200, "received_at_ms": t, "raw_bytes": raw, "sha256": hashlib.sha256(raw).hexdigest(), "byte_len": len(raw), "payload": p}
            return _f

        cfg = replace(DEFAULT_CONFIG, confirm_interval_sec=0.0, max_source_age_ms=86_400_000)
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            ts_holder = [base_ts_ms]
            fetcher = e2e_fetcher(ts_holder)

            # 4 warmup baseline cycles (CALL ~1.5x)
            for step in range(4):
                ts_holder[0] = base_ts_ms + step * 600_000
                collector.run_shadow_cycle(cfg=cfg, state_dir=state_dir, append_ledger=True, include_okx=False, interval_sec=0.0, fetcher=fetcher, sleeper=lambda _: None)

            # Cycle 5: PUT truncated -> UNKNOWN while CALL thin -> 3.0x (SHADOW_PAUSE on CALL via rapid jump)
            mode["truncate_put"] = True
            mode["call_depth"] = 40_000_000.0
            ts_holder[0] = base_ts_ms + 4 * 600_000
            out_p02 = collector.run_shadow_cycle(cfg=cfg, state_dir=state_dir, append_ledger=True, include_okx=False, interval_sec=0.0, fetcher=fetcher, sleeper=lambda _: None)
            audit = out_p02["audit_record"]
            self.assertEqual(audit["global_data_quality"], "DEGRADED")
            self.assertEqual(audit["data_quality"], "UNKNOWN")
            self.assertEqual(audit["partial_valid_side"], "CALL")
            self.assertEqual(audit["put"]["status"], "UNKNOWN")
            self.assertEqual(audit["call"]["status"], "VALID")
            self.assertAlmostEqual(audit["call"]["gfri_confirmed"], 3.75, places=4)
            self.assertEqual(audit["policy_result"]["put_decision"], "UNKNOWN")
            self.assertEqual(audit["policy_result"]["call_decision"], "SHADOW_PAUSE")
            self.assertEqual(audit["policy_result"]["partial_pause_side"], "CALL")
            self.assertEqual(audit["shadow_decision"], "UNKNOWN")
            self.assertIn("+3.75x", out_p02["console_text"])
            self.assertIn("候选暂缓", out_p02["console_text"])

    def test_p11_crash_recovery_and_p13_host_preflight(self) -> None:
        base_ts_ms = 1_795_010_400_000
        gex_raw = _make_raw_gex()
        inst_raw = _make_deribit_inst()

        # High pressure (~3.0x) across cycles
        def make_fetcher(ts_ref: list[int]):
            def _f(url: str) -> dict:
                t = ts_ref[0]
                if "gex-latest" in url:
                    p = _make_raw_gex(ts_iso="2026-11-18T14:00:00Z")
                    p["data_timestamp"] = t - 2_000
                    p["timestamp"] = t - 2_000
                    p["computed_at"] = t - 1_000
                elif "get_instrument" in url:
                    p = inst_raw
                else:
                    p = _make_deribit_book(t, depth_total_usd=40_000_000.0)
                raw = json.dumps(p).encode()
                return {"status": 200, "received_at_ms": t, "raw_bytes": raw, "sha256": hashlib.sha256(raw).hexdigest(), "byte_len": len(raw), "payload": p}
            return _f

        cfg = replace(DEFAULT_CONFIG, confirm_interval_sec=0.0, max_source_age_ms=86_400_000)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "state"
            ts_holder = [base_ts_ms]
            fetcher = make_fetcher(ts_holder)

            # 4 warmup cycles + 2 high cycles -> triggers SHADOW_PAUSE_BOTH
            for step in range(6):
                ts_holder[0] = base_ts_ms + step * 600_000
                collector.run_shadow_cycle(cfg=cfg, state_dir=state_dir, append_ledger=True, include_okx=False, interval_sec=0.0, fetcher=fetcher, sleeper=lambda _: None)

            # Simulate crash AFTER ledger append by corrupting gfri_policy_state.json to stale pre-pause state
            (state_dir / "gfri_policy_state.json").write_text('{"last_freeze_at_ms": 1, "put": {"active_pause": false, "pause_consecutive": 0}, "call": {"active_pause": false, "pause_consecutive": 0}}', encoding="utf-8")

            ts_holder[0] = base_ts_ms + 6 * 600_000
            out_rec = collector.run_shadow_cycle(cfg=cfg, state_dir=state_dir, append_ledger=True, include_okx=False, interval_sec=0.0, fetcher=fetcher, sleeper=lambda _: None)
            self.assertTrue(out_rec["recovered_policy_state"])
            self.assertEqual(out_rec["audit_record"]["shadow_decision"], "SHADOW_PAUSE_BOTH")

            # Host preflight check (P1-3)
            env_vals = parse_env_file(WORKTREE_ROOT / "deploy" / "gfri_v11" / "gfri_v11.env.example")
            host_res = run_host_checks(env_vals, state_dir=state_dir, raw_dir=root / "raw", probe_network=False)
            self.assertTrue(host_res["ok"])
            self.assertTrue(host_res["checks"]["python_version"]["ok"])
            self.assertTrue(host_res["checks"]["module_imports"]["ok"])


if __name__ == "__main__":
    unittest.main()
