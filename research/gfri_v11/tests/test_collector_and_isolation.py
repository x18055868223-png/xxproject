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

from deploy.gfri_v11.preflight_check import parse_env_file, verify_env_safety
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
                os.environ["GFRI_SOURCE_AGE_MAX_MS"] = "1800000"
                os.environ["GFRI_STATE_DIR"] = str(state_dir)
                os.environ["GFRI_RAW_CAPTURE_DIR"] = str(raw_dir)
                resolved_cfg, inc_okx, res_state, res_raw = collector.resolve_runtime_from_env()
                self.assertTrue(resolved_cfg.flags.collector_enabled)
                self.assertFalse(inc_okx)
                self.assertEqual(resolved_cfg.confirm_sample_count, 4)
                self.assertEqual(resolved_cfg.confirm_interval_sec, 5.0)
                self.assertEqual(resolved_cfg.max_ob_skew_ms, 300_000)
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


if __name__ == "__main__":
    unittest.main()
