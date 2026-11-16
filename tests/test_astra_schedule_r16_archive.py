"""Tests for 16:00 UTC r16 level cards and persistent FMZ band_snapshots archival."""
import datetime as dt
import gzip
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import astra_anchor_relative_v1 as ard
import astra_level_behaviour_v1 as lb
import astra_player_dossier_v1 as dossier
import astra_research_archive_v1 as ar
from test_astra_level_behaviour_v1 import CONTRACT, E, H, M, WALLS, _levels, _path
from test_astra_player_dossier_v1 import Clock, EXPIRY, SLOT, plan, source
from test_astra_research_archive_v1 import FakeDeribit

R2 = E - 18 * H     # 2026-11-11 14:00 UTC
R16 = E - 16 * H    # 2026-11-11 16:00 UTC (16h before 2026-11-12 08:00 UTC)

CALL_CONTRACT = {
    **CONTRACT,
    "side": "call",
    "short_instrument": "BTC-12NOV26-84000-C",
    "long_instrument": "BTC-12NOV26-86000-C",
    "short_strike_usd": 84000.0,
    "long_strike_usd": 86000.0,
}


def _write_case(root: Path, slot_ms: int, side: str, case_id: str, *, built_at_ms: int, walls_expiry: int = E) -> None:
    hour = dt.datetime.fromtimestamp(slot_ms / 1000, dt.timezone.utc)
    contract = CONTRACT if side == "put" else CALL_CONTRACT
    case = {
        "case_id": case_id,
        "asof_ms": built_at_ms - 5_000,
        "build": {"built_at_ms": built_at_ms},
        "layers": {
            "L1": [{"ref": "L1.contract", "values": contract}],
            "L3": [{"ref": "L3.expiry_walls", "values": {**WALLS, "expiry_ms": walls_expiry}}],
        },
        "refs": {
            "L1.contract": {"layer": "L1", "index": 0},
            "L3.expiry_walls": {"layer": "L3", "index": 0},
        },
    }
    d = root / "cases" / hour.strftime("%Y-%m-%d")
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{hour.strftime('%H')}_{side}_{case_id}.json.gz").write_bytes(
        gzip.compress(json.dumps(case).encode("utf-8"))
    )


def _seed_structure(root: Path) -> None:
    snaps = [
        {
            "schema": "structure_snapshot@0.1",
            "at_ms": t,
            "expiry_ms": E,
            "expected_move_usd": 1950.0,
            "profile": {"walls": {"pin": 80500.0}, "top": WALLS["top"]},
            "strikes": [{"k": k, "c_oi": 1.0, "p_oi": 10.0} for k in (78500.0, 79000.0, 79500.0, 83500.0, 84000.0, 84500.0)],
        }
        for t in range(E - 40 * H, E, 30 * M)
    ]
    (root / "structure").mkdir(parents=True, exist_ok=True)
    (root / "structure" / "2026-11-12.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in snaps), encoding="utf-8"
    )


def _fmz_row(exported_ms: int, *, half: float = 420.0, flow: dict | None = None) -> dict:
    utc8 = dt.datetime.fromtimestamp(exported_ms / 1000, dt.timezone(dt.timedelta(hours=8))).isoformat()
    fs = {
        "anchor": {
            "band_half": half,
            "band_clamped": False,
            "ready": True,
            "freshness": "FRESH",
            "gex_source_ts_ms": exported_ms - 5_000,
        }
    }
    if flow is not None:
        fs["flow"] = flow
    return {
        "snapshot_exported_ms": exported_ms,
        "source_available_at_ms": exported_ms + 1_000,
        "ingested_at_ms": exported_ms + 2_000,
        "snapshot_exported_utc8": utc8,
        "factor_snapshot": fs,
        "heavy_extra_payload": {"debug": [1, 2, 3]},
    }


def test_r16_level_cards_write_put_and_call_at_1600_utc(tmp_path):
    assert "r16" in lb.ROUNDS
    assert ar.BACKFILL_ROUND_HOURS == {"r1": 30, "r2": 18}
    assert ar.PLAYER_ROUND_HOURS == {"r1": 30, "r2": 18}

    _seed_structure(tmp_path)
    _write_case(tmp_path, R16, "put", "cf_r16_put", built_at_ms=R16 + 15_000)
    _write_case(tmp_path, R16, "call", "cf_r16_call", built_at_ms=R16 + 20_000)

    archive = ar.Archive(tmp_path)
    pts = lambda: _path(E - 47 * H, R16 + 45 * M)
    assert ar.write_level_cards(archive, now_ms=R16 + 10 * M, points_fn=pts) == 2

    cards = [c for c in _levels(tmp_path) if c["schema"] == "level_card@0.1"]
    assert {(c["round"], c["side"]) for c in cards} == {("r16", "put"), ("r16", "call")}
    put_card = next(c for c in cards if c["side"] == "put")
    call_card = next(c for c in cards if c["side"] == "call")
    assert put_card["case_id"] == "cf_r16_put" and put_card["read_at_ms"] == R16 + 15_000
    assert call_card["case_id"] == "cf_r16_call" and call_card["read_at_ms"] == R16 + 20_000
    assert ar.write_level_cards(archive, now_ms=R16 + 50 * M, points_fn=pts) == 0
    assert archive.verify()["ok"] is True


def test_r16_ignores_1400_utc_and_out_of_window_cases(tmp_path):
    _seed_structure(tmp_path)
    # Valid 14:00 UTC cases exist, plus an out-of-window case planted under 16_call_*.
    _write_case(tmp_path, R2, "put", "cf_r2_put", built_at_ms=R2 + 10_000)
    _write_case(tmp_path, R2, "call", "cf_r2_call", built_at_ms=R2 + 10_000)
    _write_case(tmp_path, R16, "call", "cf_stale_14", built_at_ms=R2 + 10_000)

    archive = ar.Archive(tmp_path)
    pts = lambda: _path(E - 47 * H, R16 + 50 * M)

    # Before the 40-minute lookback expires, r16 waits rather than consuming 14:00 UTC cases.
    assert ar.write_level_cards(archive, now_ms=R16 + 10 * M, points_fn=pts) == 0
    assert _levels(tmp_path) == []

    # After the 40-minute lookback expires, r16 falls back to no_case cards (never relabeling 14:00 UTC).
    assert ar.write_level_cards(archive, now_ms=R16 + 41 * M, points_fn=pts) == 2
    cards = [c for c in _levels(tmp_path) if c["schema"] == "level_card@0.1"]
    assert {(c["round"], c["side"]) for c in cards} == {("r16", "put"), ("r16", "call")}
    for card in cards:
        assert card["case_id"] is None
        assert card["contract_key"] is None
        assert "no_case" in card["data_gaps"]
        assert card["read_at_ms"] == R16 + 41 * M
    assert archive.verify()["ok"] is True


def test_archive_band_snapshots_retains_rolling_overwrites_and_deduplicates(tmp_path):
    archive = ar.Archive(tmp_path)
    snap_file = tmp_path / "snapshots.jsonl"

    t1 = R16 - 2 * H
    t2 = R16 - H
    t3 = R16 - 30 * M
    t4 = R16
    bad_rows = [
        {"snapshot_exported_ms": True, "source_available_at_ms": t1, "factor_snapshot": {"anchor": {"band_half": 1}}},
        {"snapshot_exported_ms": t1, "source_available_at_ms": None, "factor_snapshot": {"anchor": {"band_half": 1}}},
        {"snapshot_exported_ms": t1, "source_available_at_ms": t1, "factor_snapshot": {"anchor": "not-a-map"}},
    ]
    rows_round1 = [
        _fmz_row(t2, half=410.0, flow={"net_15m": 12.5}),
        _fmz_row(t1, half=400.0),
        _fmz_row(t1, half=400.0),  # duplicate within same file
        *bad_rows,
    ]
    snap_file.write_text(
        "\n".join(json.dumps(r) for r in rows_round1) + "\nnot-json\n",
        encoding="utf-8",
    )

    assert ar.archive_band_snapshots(archive, snap_file, now_ms=R16 + 1_000) == 2
    assert ar.archive_band_snapshots(archive, snap_file, now_ms=R16 + 2_000) == 0

    # FMZ overwrites snapshots.jsonl with a rolling tail window that drops t1 and adds t3, t4.
    rows_round2 = [
        _fmz_row(t2, half=410.0, flow={"net_15m": 12.5}),
        _fmz_row(t3, half=430.0),
        _fmz_row(t4, half=440.0, flow={"net_15m": -5.0}),
    ]
    snap_file.write_text("".join(json.dumps(r) + "\n" for r in rows_round2), encoding="utf-8")

    assert ar.archive_band_snapshots(archive, snap_file, now_ms=R16 + 3_000) == 2
    assert ar.archive_band_snapshots(archive, snap_file, now_ms=R16 + 4_000) == 0

    archived_path = tmp_path / "band_snapshots" / "2026-11-11.jsonl"
    archived = [json.loads(line) for line in archived_path.read_text(encoding="utf-8").splitlines()]
    assert [r["snapshot_exported_ms"] for r in archived] == [t1, t2, t3, t4]
    assert "heavy_extra_payload" not in archived[1]
    assert archived[1]["factor_snapshot"] == {
        "anchor": {
            "band_half": 410.0,
            "band_clamped": False,
            "ready": True,
            "freshness": "FRESH",
            "gex_source_ts_ms": t2 - 5_000,
        },
        "flow": {"net_15m": 12.5},
    }
    assert archived[1]["ingested_at_ms"] == t2 + 2_000
    assert archived[1]["snapshot_exported_utc8"].endswith("+08:00")
    assert ard.frozen_band(archived[3], t4 + 5_000)["half_width_usd"] == 440.0
    assert archive.verify()["ok"] is True


def test_tick_archives_band_snapshots_and_isolates_errors(tmp_path, monkeypatch):
    archive = ar.Archive(tmp_path)
    snap_file = tmp_path / "snapshots.jsonl"
    snap_file.write_text(json.dumps(_fmz_row(R16 - 5 * M, half=500.0)) + "\n", encoding="utf-8")

    monkeypatch.setattr(ar, "collect_structure", lambda *a, **k: (0, []))
    monkeypatch.setattr(ar, "collect_flow", lambda *a, **k: 0)
    deribit = ar.Deribit(FakeDeribit(path=_path(E - 47 * H, R16)))

    r1 = ar.tick(archive, now_ms=R16, view=None, fetch_case=lambda s: {}, deribit=deribit, snapshots_path=snap_file)
    assert r1["band_snapshots"] == 1
    assert not [e for e in r1["errors"] if e["step"] == "band_snapshots"]
    assert archive.verify()["ok"] is True

    monkeypatch.setattr(ar, "archive_band_snapshots", lambda *a, **k: (_ for _ in ()).throw(OSError("unreadable")))
    r2 = ar.tick(archive, now_ms=R16 + 10 * M, view=None, fetch_case=lambda s: {}, deribit=deribit, snapshots_path=snap_file)
    assert any(e["step"] == "band_snapshots" and e["error"] == "OSError" for e in r2["errors"])


def test_tick_main_resolves_snapshots_env_var(tmp_path, monkeypatch):
    archive = ar.Archive(tmp_path)
    snap_file = tmp_path / "fmz_snapshots.jsonl"
    captured = {}

    monkeypatch.setenv("ASTRA_WORKBENCH_SNAPSHOTS_JSONL", str(snap_file))
    monkeypatch.setattr(ar, "_http_json", lambda *a, **k: None)
    monkeypatch.setattr(ar, "tick", lambda a, **kw: captured.update(kw) or {"ok": True})

    ns = ar.argparse.Namespace(
        workbench="http://127.0.0.1:8895",
        inbox=str(tmp_path / "inbox"),
        receipts=str(tmp_path / "receipts"),
        workbench_dir=str(tmp_path / "wb"),
    )
    ar._tick_main(ns, archive)
    assert captured["snapshots_path"] == snap_file

    monkeypatch.setenv("ASTRA_WORKBENCH_SNAPSHOTS_JSONL", "")
    captured.clear()
    ar._tick_main(ns, archive)
    assert captured["snapshots_path"] is None


def test_dossier_respond_error_provenance_and_settle_due_cli(tmp_path, monkeypatch, capsys):
    clock = Clock(SLOT + M)
    d = dossier.Dossiers(tmp_path, clock)
    hand = d.expect(EXPIRY, "SCHEDULED", plan=plan(), slot=SLOT)
    d.deal(hand, source(clock))

    long_err = "TimeoutError: " + ("x" * 300)
    rec = d.respond(hand, "attempt-timeout", "TIMEOUT", error=long_err)
    assert rec["status"] == "TIMEOUT"
    assert rec["error"] == long_err[:240] and len(rec["error"]) == 240
    # Idempotent replay without passing error remains backward-compatible
    assert d.respond(hand, "attempt-timeout", "TIMEOUT") == rec
    assert d.respond(hand, "attempt-failed", "REQUEST_FAILED", error="HTTP 502")["error"] == "HTTP 502"
    assert d.respond(hand, "attempt-blank", "NO_RESPONSE", error="   ")["error"] is None

    # CLI settle-due executes settle_due inside writer lock without invoking tick()
    clock.ms = EXPIRY + 5 * M
    monkeypatch.setattr(dossier, "now_ms", clock)
    monkeypatch.setattr(dossier, "public", lambda method, **params: {"data": [{"date": "2026-11-15", "delivery_price": 64250.0}]})
    monkeypatch.setattr(dossier.archive.Deribit, "index_path", lambda _: [])
    monkeypatch.setattr(dossier.Dossiers, "tick", lambda *a, **k: pytest.fail("settle-due must not call tick()"))
    monkeypatch.setattr(sys, "argv", ["astra_player_dossier_v1.py", "--root", str(tmp_path), "settle-due"])

    dossier.main()
    printed = json.loads(capsys.readouterr().out)
    expected_summary = d.path(EXPIRY) / "summary.md"
    assert printed == [str(expected_summary)]
    outcomes = list((d.path(EXPIRY) / "outcomes").glob("outcome_*/outcome.json"))
    assert len(outcomes) == 1 and dossier.read(outcomes[0])["classification"] == "OFFICIAL"

    # Real subprocess smoke test when nothing remains due
    repo_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, str(repo_root / "tools" / "astra_player_dossier_v1.py"), "--root", str(tmp_path), "settle-due"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(proc.stdout) == []


def test_player_dossier_deploy_settle_timeout_and_deal_timer_assets():
    deploy_dir = Path(__file__).resolve().parents[1] / "deploy" / "player_dossier"

    deal_timer = (deploy_dir / "astra-player-deal.timer").read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 16:02,07,12,17:00 UTC" in deal_timer
    assert "16:02,05,10,15:00 UTC" not in deal_timer

    timeout_conf = (deploy_dir / "50-timeout.conf").read_text(encoding="utf-8")
    assert "[Service]" in timeout_conf
    assert "TimeoutStartSec=900" in timeout_conf

    settle_service = (deploy_dir / "astra-player-settle.service").read_text(encoding="utf-8")
    for required in (
        "Type=oneshot",
        "User=bitnami",
        "Group=research",
        "UMask=0027",
        "EnvironmentFile=/etc/signal-audit/llm.env",
        "EnvironmentFile=/etc/signal-audit/workbench-v20.env",
        "ExecStart=/usr/bin/python3 /opt/signal-audit-tools/astra_player_dossier_v1.py --root /var/lib/xxproject/workbench/shadow_dossiers settle-due",
        "TimeoutStartSec=300",
    ):
        assert required in settle_service

    settle_timer = (deploy_dir / "astra-player-settle.timer").read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 08:06,16,31:00 UTC" in settle_timer
    assert "Persistent=true" in settle_timer
    assert "WantedBy=timers.target" in settle_timer

