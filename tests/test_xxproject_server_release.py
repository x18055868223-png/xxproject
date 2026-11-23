"""Step 6c tests: reproducible server release build, preflight/install/upgrade/rollback, and server health checks."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tarfile
import time
from typing import Any

import pytest

from tools import astra_signal_producer_v1 as sp
from tools import xxproject_server_health_v1 as health_check


ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "deploy" / "xxproject_server" / "build_release.py"
DEPLOY_SCRIPT = ROOT / "deploy" / "xxproject_server" / "deploy_release.py"


def _load_script(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_reproducible_release_build_and_security_hygiene(tmp_path: Path):
    builder = _load_script("xxproject_build_release", BUILD_SCRIPT)
    pkg_a = tmp_path / "out_a" / "release.tar.gz"
    pkg_b = tmp_path / "out_b" / "release.tar.gz"

    fixed_commit = "607d66fe111fcae67f05a82cced9347e67058847"
    res_a = builder.build_release(
        ROOT,
        pkg_a,
        source_commit=fixed_commit,
        release_tag="v-test-repro",
        allow_dirty=True,
    )
    res_b = builder.build_release(
        ROOT,
        pkg_b,
        source_commit=fixed_commit,
        release_tag="v-test-repro",
        allow_dirty=True,
    )
    assert res_a["package_sha256"] == res_b["package_sha256"]
    assert res_a["files_sha256"] == res_b["files_sha256"]
    assert pkg_a.read_bytes() == pkg_b.read_bytes()

    with tarfile.open(pkg_a, "r:gz") as tf:
        names = tf.getnames()
        assert "RELEASE_MANIFEST.json" in names
        assert "signal_core/neutral_regulation_demo_fmz.py" in names
        assert "tools/astra_signal_producer_v1.py" in names
        assert "tools/xxproject_server_health_v1.py" in names
        for name in names:
            lower = name.lower()
            assert not lower.endswith(".env"), f"forbidden .env in tarball: {name}"
            assert not lower.endswith(".db3"), f"forbidden .db3 in tarball: {name}"
            assert not lower.endswith(".jsonl"), f"forbidden .jsonl in tarball: {name}"
            assert ".artifacts" not in lower
            assert "spm_manual_gate_execution" not in lower
            assert "fmz-password" not in lower

        manifest = json.loads(tf.extractfile("RELEASE_MANIFEST.json").read().decode("utf-8"))
        inv = manifest["security_invariants"]
        assert inv["fmz_robot_bundled"] is False
        assert inv["fmz_execution_bundled"] is False
        assert inv["signal_core_read_only_demo"] is True
        assert inv["ai_player_mode"] == "SHADOW_ONLY"
        assert inv["ai_player_execution_allowed"] is False
        assert inv["secrets_or_runtime_data_bundled"] is False


def test_install_upgrade_rollback_preserves_state_and_secrets(tmp_path: Path):
    builder = _load_script("xxproject_build_release_deploy", BUILD_SCRIPT)
    deployer = _load_script("xxproject_deploy_release", DEPLOY_SCRIPT)

    pkg_v1 = tmp_path / "pkg_v1.tar.gz"
    pkg_v2 = tmp_path / "pkg_v2.tar.gz"
    r1 = builder.build_release(
        ROOT,
        pkg_v1,
        source_commit="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        release_tag="v1",
        allow_dirty=True,
    )
    r2 = builder.build_release(
        ROOT,
        pkg_v2,
        source_commit="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        release_tag="v2",
        allow_dirty=True,
    )
    assert r1["release_id"] != r2["release_id"]

    prefix = tmp_path / "prefix_root"

    # 1. Preflight + install v1 + verify
    pre1 = deployer.cmd_preflight(pkg_v1, prefix)
    assert pre1["ok"] is True
    inst1 = deployer.cmd_install(pkg_v1, prefix, apply_units=True, no_systemd=True)
    assert inst1["ok"] is True
    assert "astra-signal-producer.service" in inst1["installed_units"]
    assert "astra-player-deal.service" in inst1["installed_units"]
    assert "signal-audit-materialize.service" in inst1["installed_units"]
    assert "signal-audit-materialize.timer" in inst1["installed_units"]
    assert len(inst1["installed_units"]) == 21
    ver1 = deployer.cmd_verify(prefix)
    assert ver1["ok"] is True
    assert ver1["release_id"] == r1["release_id"]
    assert ver1["fmz_robot_bundled"] is False
    assert ver1["ai_player_mode"] == "SHADOW_ONLY"

    # 2. Seed host secrets & mutable runtime state that must survive upgrade + rollback
    secret_path = prefix / "etc" / "signal-audit" / "llm.env"
    secret_bytes = b"OPENAI_API_KEY=do-not-clobber-secret\n"
    secret_path.write_bytes(secret_bytes)

    state_path = prefix / "var" / "lib" / "xxproject" / "workbench" / "shadow_dossiers" / "keep.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_bytes = b'{"hand":"preserved","mode":"SHADOW_ONLY"}\n'
    state_path.write_bytes(state_bytes)

    # 3. Install v2 -> verify -> check status pointers
    inst2 = deployer.cmd_install(pkg_v2, prefix, apply_units=True, no_systemd=True)
    assert inst2["ok"] is True
    assert inst2["previous_release_id"] == r1["release_id"]
    ver2 = deployer.cmd_verify(prefix)
    assert ver2["release_id"] == r2["release_id"]
    st2 = deployer.cmd_status(prefix)
    assert st2["current"] == r2["release_id"]
    assert st2["previous"] == r1["release_id"]
    assert secret_path.read_bytes() == secret_bytes
    assert state_path.read_bytes() == state_bytes

    # 4. Rollback to v1 -> verify -> check state, secrets, receipts, zero FMZ dependency
    rb = deployer.cmd_rollback(prefix, apply_units=True, no_systemd=True)
    assert rb["ok"] is True
    assert rb["release_id"] == r1["release_id"]
    assert rb["runtime_state_preserved"] is True
    assert rb["etc_configs_preserved"] is True
    ver_rb = deployer.cmd_verify(prefix)
    assert ver_rb["release_id"] == r1["release_id"]
    assert ver_rb["fmz_robot_bundled"] is False
    assert secret_path.read_bytes() == secret_bytes
    assert state_path.read_bytes() == state_bytes

    receipts = [
        json.loads(line)
        for line in (prefix / "opt" / "xxproject" / "deployments.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [r["action"] for r in receipts] == ["install", "install", "rollback"]


def test_server_health_checker_green_and_failure_paths(tmp_path: Path):
    import datetime as dt

    builder = _load_script("xxproject_build_release_health", BUILD_SCRIPT)
    deployer = _load_script("xxproject_deploy_release_health", DEPLOY_SCRIPT)

    pkg = tmp_path / "pkg.tar.gz"
    builder.build_release(ROOT, pkg, source_commit="607d66fe111fcae67f05a82cced9347e67058847", allow_dirty=True)
    prefix = tmp_path / "host"
    deployer.cmd_install(pkg, prefix, apply_units=False, no_systemd=True)
    release_dir = prefix / "opt" / "xxproject" / "current"

    # Generate valid live_public_read_only native producer artifacts + fixed-round signal_review card
    tick_ms = int(dt.datetime(2026, 10, 15, 15, 1, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
    core = sp.load_signal_core(unique_module=True)[0]
    core.now_ms = lambda: tick_ms
    sig_logs = tmp_path / "signal_native" / "logs"
    sig_state = tmp_path / "signal_native" / "state"
    runner = sp.NativeSignalRuntime(
        logs_dir=sig_logs,
        state_dir=sig_state,
        core_mod=core,
        core_sha256=sp.compute_lf_sha256(ROOT / "demo" / "\u6700\u65b0\u4ea4\u4ed8\u7269" / "neutral_regulation_demo_fmz.py"),
        overrides={"gex_info_base_url": "http://127.0.0.1:8000"},
    )

    def _mock_live_http(url: str, params=None, **_kw):
        params = params or {}

        def ok(data: Any) -> dict[str, Any]:
            return {"quality": core.QUALITY_OK, "data": data, "error": None, "fetched_at_ms": tick_ms}

        if "/api/v3/klines" in url or "/fapi/v1/klines" in url:
            step = 60_000 if "/api/v3/" in url else 900_000
            limit = int(params.get("limit", 48))
            return ok([
                [tick_ms - (limit - i + 1) * step, "100000", "100010", "99990", "100002", "10", tick_ms - (limit - i) * step - 1, "1000000", 50, "5.5", "550000", "0"]
                for i in range(limit)
            ])
        if "/api/v3/aggTrades" in url or "/fapi/v1/aggTrades" in url:
            return ok([{"a": i + 1, "p": "100000.0", "q": "1.0", "T": tick_ms - 1000, "m": False} for i in range(6)])
        if "/api/v3/depth" in url:
            return ok({"lastUpdateId": 1, "bids": [["99995.0", "5.0"]], "asks": [["100005.0", "5.0"]]})
        if "/fapi/v1/premiumIndex" in url:
            return ok({"symbol": "BTCUSDT", "markPrice": "100005", "indexPrice": "100000", "lastFundingRate": "0.0001", "time": tick_ms - 1000})
        if "/fapi/v1/fundingRate" in url:
            return ok([{"symbol": "BTCUSDT", "fundingRate": "0.0001", "fundingTime": tick_ms - 28_800_000}])
        if "/v1/info" in url:
            return ok({
                "asset": "BTC",
                "quality": "OK",
                "stale": False,
                "data_state": "live",
                "availability": "ready",
                "market_state": "positive_gamma",
                "gex_board": {"total_net_gex": 250_000_000.0, "dvol": 45.0, "spot": 100000.0},
                "gex_time_semantics": {"schema_version": "gex_time_semantics@1.0.0", "fields": {"gex_board.total_net_gex": {"observed_at_ms": tick_ms - 10_000, "time_errors": []}}},
            })
        if "gex" in url:
            return ok({"flip_point": 100010.0, "spot": 100000.0, "spring": 200.0, "timestamp": tick_ms - 10_000})
        if "public/get_instruments" in url:
            return ok({"jsonrpc": "2.0", "result": []})
        if "public/get_index_price" in url:
            return ok({"jsonrpc": "2.0", "result": {"index_price": 100000.0}})
        if "finance.yahoo.com" in url:
            ts_list = [int((tick_ms - (10 - i) * 86_400_000) // 1000) for i in range(10)]
            return ok({"chart": {"result": [{"timestamp": ts_list, "indicators": {"quote": [{"close": [100.0 + i * 0.1 for i in range(10)]}]}}]}})
        return {"quality": core.QUALITY_ERROR, "data": None, "error": f"unexpected:{url}"}

    runner.runtime.http.get_json = _mock_live_http
    snap, _ = runner.tick(live_fetch=True)
    now_ms = int(snap["ts_ms"]) + 5_000
    health_data = json.loads((sig_state / "producer_health.json").read_text(encoding="utf-8"))
    health_data["updated_at_ms"] = now_ms - 1_000
    (sig_state / "producer_health.json").write_text(json.dumps(health_data), encoding="utf-8")

    map_current = tmp_path / "map" / "current_map.json"
    map_current.parent.mkdir(parents=True)
    map_current.write_text(
        json.dumps({
            "schema": "astra_map_data_server@2.4.2",
            "generated_at_ms": now_ms - 5_000,
            "completeness": {"complete": True},
            "fmz": {"status": "current"},
        }),
        encoding="utf-8",
    )

    kpf_current = tmp_path / "kpf" / "current.json"
    kpf_current.parent.mkdir(parents=True)
    kpf_current.write_text(
        json.dumps({"generated_at_ms": now_ms - 5_000, "status": "READY", "version_id": "kpf-test"}),
        encoding="utf-8",
    )

    archive_root = tmp_path / "archive"
    for sub in ("cases", "structure", "levels", "band_snapshots"):
        (archive_root / sub).mkdir(parents=True)

    plan_copy = tmp_path / "plan.json"
    plan_copy.write_text((ROOT / "deploy" / "player_dossier" / "plan.daily_r16.v2.json").read_text(encoding="utf-8"), encoding="utf-8")

    def run_check(*, now_override: int | None = None):
        return health_check.run_health_check(
            signal_logs_dir=sig_logs,
            signal_state_dir=sig_state,
            gex_url="http://127.0.0.1:8000",
            workbench_url="http://127.0.0.1:8895",
            workbench_env=None,
            map_current=map_current,
            kpf_current=kpf_current,
            archive_root=archive_root,
            player_plan=plan_copy,
            release_dir=release_dir,
            expected_core_version="2.0.6",
            skip_network=True,
            require_release_manifest=True,
            now_ms=now_ms if now_override is None else now_override,
        )

    green = run_check()
    assert green["ok"] is True, f"unexpected health failure: {green}"

    # Daily closed-day KPF bundles stay valid up to 48h (172,800,000 ms), then fail kpf_stale
    kpf_12h = {"generated_at_ms": now_ms - (12 * 3_600_000), "status": "READY", "version_id": "kpf-test"}
    kpf_current.write_text(json.dumps(kpf_12h), encoding="utf-8")
    assert run_check()["ok"] is True

    kpf_72h = {"generated_at_ms": now_ms - (72 * 3_600_000), "status": "READY", "version_id": "kpf-test"}
    kpf_current.write_text(json.dumps(kpf_72h), encoding="utf-8")
    r_kpf_stale = run_check()
    assert r_kpf_stale["ok"] is False
    assert r_kpf_stale["checks"]["kpf_light"]["reason"] == "kpf_stale"

    kpf_current.write_text(
        json.dumps({"generated_at_ms": now_ms - 5_000, "status": "READY", "version_id": "kpf-test"}),
        encoding="utf-8",
    )

    # Failure 1: stale producer health
    bad_health = dict(health_data, updated_at_ms=now_ms - 600_000)
    (sig_state / "producer_health.json").write_text(json.dumps(bad_health), encoding="utf-8")
    r_stale = run_check()
    assert r_stale["ok"] is False
    assert r_stale["checks"]["signal_producer"]["reason"] == "producer_health_stale"

    # Failure 2: fmz_platform_used=True flag violation
    bad_flag = dict(health_data, fmz_platform_used=True)
    (sig_state / "producer_health.json").write_text(json.dumps(bad_flag), encoding="utf-8")
    r_flag = run_check()
    assert r_flag["ok"] is False
    assert r_flag["checks"]["signal_producer"]["reason"] == "fmz_platform_flag_must_be_false"

    # Restore valid health & check Failure 3: player plan execution_allowed violation
    (sig_state / "producer_health.json").write_text(json.dumps(health_data), encoding="utf-8")
    bad_plan = json.loads(plan_copy.read_text(encoding="utf-8"))
    bad_plan["execution_allowed"] = True
    plan_copy.write_text(json.dumps(bad_plan), encoding="utf-8")
    r_plan = run_check()
    assert r_plan["ok"] is False
    assert r_plan["checks"]["player_plan"]["reason"] == "player_plan_safety_boundary_violated"

    # Restore valid plan & check Failure 4: release manifest tampering
    plan_copy.write_text((ROOT / "deploy" / "player_dossier" / "plan.daily_r16.v2.json").read_text(encoding="utf-8"), encoding="utf-8")
    (release_dir / "tools" / "astra_signal_producer_v1.py").write_text("# tampered\n", encoding="utf-8")
    r_tamper = run_check()
    assert r_tamper["ok"] is False
    assert r_tamper["checks"]["release_manifest"]["reason"] == "release_integrity_failed"


def test_workbench_unit_env_precedence_and_network_health_checks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    unit_text = (
        ROOT / "deploy" / "xxproject_server" / "systemd_units_unified" / "astra-workbench-v20.service"
    ).read_text(encoding="utf-8")
    assert "EnvironmentFile=-/etc/signal-audit/llm.env" in unit_text
    assert "EnvironmentFile=/etc/signal-audit/workbench-v20.env" in unit_text
    assert unit_text.index("EnvironmentFile=-/etc/signal-audit/llm.env") < unit_text.index(
        "EnvironmentFile=/etc/signal-audit/workbench-v20.env"
    )
    assert (
        "ExecStart=/usr/bin/env ASTRA_WORKBENCH_FRONTEND_DIR=/opt/xxproject/current/deploy/astra_workbench_v3 "
        "/usr/bin/python3 /opt/xxproject/current/tools/astra_workbench_v20.py"
    ) in unit_text

    # Seed offline health/data files and valid live_public_read_only snapshot
    sig_logs = tmp_path / "logs"
    sig_state = tmp_path / "state"
    import datetime as dt
    tick_ms = int(dt.datetime(2026, 10, 15, 15, 1, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
    core = sp.load_signal_core(unique_module=True)[0]
    core.now_ms = lambda: tick_ms
    runner = sp.NativeSignalRuntime(
        logs_dir=sig_logs,
        state_dir=sig_state,
        core_mod=core,
        core_sha256="sha-test",
        overrides={"live_fetch_enabled": False, "offline_fixture_enabled": True},
    )
    runner.tick(live_fetch=False)
    # Rewrite snapshots.jsonl line as live_public_read_only for health check
    snap_row = json.loads((sig_logs / "snapshots.jsonl").read_text(encoding="utf-8").splitlines()[0])
    snap_row["decision"]["runtime_facts"]["runtime_mode"] = "live_public_read_only"
    (sig_logs / "snapshots.jsonl").write_text(json.dumps(snap_row) + "\n", encoding="utf-8")
    now_ms = int(snap_row["decision"]["ts_ms"]) + 5_000

    map_current = tmp_path / "current_map.json"
    map_current.write_text(
        json.dumps({"schema": "astra_map_data_server@2.4.2", "generated_at_ms": now_ms - 1000}),
        encoding="utf-8",
    )
    kpf_current = tmp_path / "kpf_current.json"
    kpf_current.write_text(json.dumps({"generated_at_ms": now_ms - 1000, "status": "READY"}), encoding="utf-8")
    archive_root = tmp_path / "archive"
    for sub in ("cases", "structure", "levels", "band_snapshots"):
        (archive_root / sub).mkdir(parents=True)
    plan_copy = tmp_path / "plan.json"
    plan_copy.write_text(
        (ROOT / "deploy" / "player_dossier" / "plan.daily_r16.v2.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    wb_env = tmp_path / "workbench-v20.env"
    wb_env.write_text("ASTRA_WORKBENCH_TOKEN=secret-tok-123\n", encoding="utf-8")
    monkeypatch.delenv("ASTRA_WORKBENCH_TOKEN", raising=False)

    gex_stale = [False]
    wb_exec_allowed = [False]

    def fake_http(url: str, *, headers=None, timeout_s=5.0):
        del timeout_s
        if url.endswith("/health"):
            return 200, {"status": "ok", "version": "0.2.1"}
        if url.endswith("/v1/info"):
            return 200, {"availability": "ready", "stale": gex_stale[0], "data_age_ms": 1000}
        if url.endswith("/api/workbench/session"):
            return 200, {"ok": True}
        if url.endswith("/api/workbench"):
            assert (headers or {}).get("Authorization") == "Bearer secret-tok-123"
            return 200, {
                "market_context": {"status": "current", "strategy_version": "2.0.6"},
                "discovery": {"candidates": {"c1": {"execution_allowed": wb_exec_allowed[0]}}},
            }
        raise AssertionError(f"unexpected URL: {url}")

    # 1. Mocked network + auth green path
    ok_res = health_check.run_health_check(
        signal_logs_dir=sig_logs,
        signal_state_dir=sig_state,
        gex_url="http://127.0.0.1:8000",
        workbench_url="http://127.0.0.1:8895",
        workbench_env=wb_env,
        map_current=map_current,
        kpf_current=kpf_current,
        archive_root=archive_root,
        player_plan=plan_copy,
        release_dir=None,
        skip_network=False,
        require_workbench_auth=True,
        now_ms=now_ms,
        http_get=fake_http,
    )
    assert ok_res["ok"] is True
    assert ok_res["checks"]["workbench"]["authenticated_check"] is True
    assert ok_res["checks"]["gexmonitorapi"]["ok"] is True

    # 2. Fail closed when require_workbench_auth=True and env file missing/unreadable
    no_auth = health_check.run_health_check(
        signal_logs_dir=sig_logs,
        signal_state_dir=sig_state,
        gex_url="http://127.0.0.1:8000",
        workbench_url="http://127.0.0.1:8895",
        workbench_env=tmp_path / "missing.env",
        map_current=map_current,
        kpf_current=kpf_current,
        archive_root=archive_root,
        player_plan=plan_copy,
        release_dir=None,
        skip_network=False,
        require_workbench_auth=True,
        now_ms=now_ms,
        http_get=fake_http,
    )
    assert no_auth["ok"] is False
    assert no_auth["checks"]["workbench"]["reason"] == "workbench_token_unreadable_or_missing"

    # 3. Fail on execution_allowed=True or stale GEX /v1/info
    wb_exec_allowed[0] = True
    bad_wb = health_check.run_health_check(
        signal_logs_dir=sig_logs,
        signal_state_dir=sig_state,
        gex_url="http://127.0.0.1:8000",
        workbench_url="http://127.0.0.1:8895",
        workbench_env=wb_env,
        map_current=map_current,
        kpf_current=kpf_current,
        archive_root=archive_root,
        player_plan=plan_copy,
        release_dir=None,
        skip_network=False,
        require_workbench_auth=True,
        now_ms=now_ms,
        http_get=fake_http,
    )
    assert bad_wb["ok"] is False
    assert bad_wb["checks"]["workbench"]["reason"] == "workbench_execution_allowed_must_be_false"

    wb_exec_allowed[0] = False
    gex_stale[0] = True
    bad_gex = health_check.run_health_check(
        signal_logs_dir=sig_logs,
        signal_state_dir=sig_state,
        gex_url="http://127.0.0.1:8000",
        workbench_url="http://127.0.0.1:8895",
        workbench_env=wb_env,
        map_current=map_current,
        kpf_current=kpf_current,
        archive_root=archive_root,
        player_plan=plan_copy,
        release_dir=None,
        skip_network=False,
        require_workbench_auth=True,
        now_ms=now_ms,
        http_get=fake_http,
    )
    assert bad_gex["ok"] is False
    assert bad_gex["checks"]["gexmonitorapi"]["reason"] == "gexmonitorapi_not_ready_or_stale"


def test_deploy_release_fail_before_mutate_dropin_backup_and_window_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import datetime as dt

    builder = _load_script("xxproject_build_release_guards", BUILD_SCRIPT)
    deployer = _load_script("xxproject_deploy_release_guards", DEPLOY_SCRIPT)

    pkg_v1 = tmp_path / "pkg_v1.tar.gz"
    pkg_v2 = tmp_path / "pkg_v2.tar.gz"
    r1 = builder.build_release(
        ROOT, pkg_v1, source_commit="cccccccccccccccccccccccccccccccccccccccc", release_tag="v1", allow_dirty=True
    )
    r2 = builder.build_release(
        ROOT, pkg_v2, source_commit="dddddddddddddddddddddddddddddddddddddddd", release_tag="v2", allow_dirty=True
    )

    prefix = tmp_path / "host"
    dropin = prefix / "etc" / "systemd" / "system" / "astra-workbench-v20.service.d" / "50-legacy.conf"
    dropin.parent.mkdir(parents=True, exist_ok=True)
    dropin.write_text("[Service]\nMemoryMax=2G\n", encoding="utf-8")

    # 1. Legacy drop-ins are archived into release unit_backups and removed from systemd dir
    inst1 = deployer.cmd_install(pkg_v1, prefix, apply_units=True, no_systemd=True)
    assert "astra-workbench-v20.service.d/50-legacy.conf" in inst1["backed_up_dropins"]
    assert not dropin.exists()
    archived = prefix / "opt" / "xxproject" / "unit_backups" / r1["release_id"] / "astra-workbench-v20.service.d" / "50-legacy.conf"
    assert archived.is_file()

    # 2. Missing host GEX venv fails BEFORE mutating releases/, current pointer, or units
    monkeypatch.setattr(deployer.os, "name", "posix")
    with pytest.raises(RuntimeError, match="MISSING_HOST_GEX_RUNTIME_VENV"):
        deployer.cmd_install(pkg_v2, prefix, apply_units=True, no_systemd=False)
    assert deployer.cmd_status(prefix)["current"] == r1["release_id"]
    assert r2["release_id"] not in deployer.cmd_status(prefix)["available_releases"]

    with pytest.raises(RuntimeError, match="MISSING_HOST_GEX_RUNTIME_VENV"):
        deployer.cmd_rollback(prefix, to_release=r1["release_id"], apply_units=True, no_systemd=False)
    assert deployer.cmd_status(prefix)["current"] == r1["release_id"]

    # 3. Root settlement (08:00 UTC) and auto-deal (16:00 UTC) windows refuse un-overridden deploy
    monkeypatch.setattr(deployer, "_is_system_root", lambda _p: True)
    settle_ms = int(dt.datetime(2026, 11, 23, 8, 5, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
    deal_ms = int(dt.datetime(2026, 11, 23, 16, 10, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
    safe_ms = int(dt.datetime(2026, 11, 23, 10, 0, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)

    with pytest.raises(RuntimeError, match="UNSAFE_DEPLOY_WINDOW_UTC"):
        deployer.cmd_install(pkg_v2, prefix, apply_units=False, no_systemd=True, now_ms=settle_ms)
    with pytest.raises(RuntimeError, match="UNSAFE_DEPLOY_WINDOW_UTC"):
        deployer.cmd_rollback(prefix, to_release=r1["release_id"], apply_units=False, no_systemd=True, now_ms=deal_ms)
    deployer._validate_deploy_window(prefix, allow_window_override=False, now_ms=safe_ms)
    deployer._validate_deploy_window(prefix, allow_window_override=True, now_ms=deal_ms)
