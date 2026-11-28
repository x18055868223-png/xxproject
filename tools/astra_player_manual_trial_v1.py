"""Isolated repeatable manual AD_HOC shadow-trial CLI (strictly SHADOW_ONLY).

Replaces only the trigger time (current active expiry via AD_HOC) and isolated
writable directories (shadow_dossiers, workbench state, exec state). Reuses
official P5 dossier freeze, prompts, LLM settings/ledger, quote confirmation,
native shadow lock, and ExecutionCoordinator(SHADOW_ONLY) handoff verification.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

try:
    import grp
    import pwd
except ImportError:  # pragma: no cover - Windows
    grp = None  # type: ignore[assignment]
    pwd = None  # type: ignore[assignment]

DEFAULT_TOOLS_DIR = Path(__file__).resolve().parent
DEFAULT_PLAN = Path("/opt/xxproject/current/deploy/player_dossier/plan.daily_r16.v3_p5.json")
DEFAULT_ARCHIVE = Path("/var/lib/xxproject/research_archive")
DEFAULT_LLM_ENV = Path("/etc/signal-audit/llm.env")
DEFAULT_WORKBENCH_ENV = Path("/etc/signal-audit/workbench-v20.env")

_WORKBENCH_READONLY_MARKET_KEYS = (
    "ASTRA_WORKBENCH_SNAPSHOTS_JSONL",
    "ASTRA_WORKBENCH_FRONTEND_DIR",
    "ASTRA_WORKBENCH_REFRESH_SECONDS",
    "ASTRA_MAP_SOURCES_ENABLED",
    "ASTRA_MAP_SOURCE_MODE",
    "ASTRA_MAP_PRODUCER_CURRENT_PATH",
    "ASTRA_MAP_PRODUCER_MAX_AGE_MS",
    "ASTRA_MAP_KPF_MANIFEST_PATH",
    "ASTRA_MAP_KPF_SNAPSHOT_PATH",
    "ASTRA_MAP_KPF_AUDIT_PATH",
    "ASTRA_MAP_KPF_DEBUG_PATH",
    "ASTRA_LOCKED_SAMPLE_INTERVAL_MS",
    "ASTRA_LOCKED_MAX_ACTIVE",
    "ASTRA_LOCKED_MAX_EPISODES",
    "ASTRA_LOCKED_MAX_BYTES",
    "ASTRA_LOCKED_MAX_SAMPLES",
    "ASTRA_WORKBENCH_MEMORY_SOFT_MB",
    "ASTRA_WORKBENCH_MEMORY_HARD_MB",
    "ASTRA_EXPIRY_GAMMA_ENABLED",
    "ASTRA_CROSS_ASSET_ENABLED",
)
_ARCHIVE_READONLY_SUBDIRS = ("structure", "levels", "band_snapshots")


def parse_env_file(path: Path | None) -> dict[str, str]:
    """Parse KEY=VALUE lines from an env file without logging or mutating os.environ."""
    out: dict[str, str] = {}
    if path is None or not path.is_file():
        return out
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        key = k.strip()
        val = v.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in {"'", '"'}:
            val = val[1:-1]
        if key:
            out[key] = val
    return out


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _chown_posix(path: Path, user: str, group: str) -> None:
    if os.name == "nt" or pwd is None or grp is None:
        return
    uid = pwd.getpwnam(user).pw_uid
    gid = grp.getgrnam(group).gr_gid
    os.chown(path, uid, gid)


def _posix_preexec(user: str, group: str, extra_groups: tuple[str, ...], umask_val: int):
    if os.name == "nt" or pwd is None or grp is None:
        return None
    if os.geteuid() != 0:
        raise RuntimeError(f"service_identities_requires_root_for_{user}")
    uid = pwd.getpwnam(user).pw_uid
    gid = grp.getgrnam(group).gr_gid
    gids = [gid]
    for gname in extra_groups:
        g_id = grp.getgrnam(gname).gr_gid
        if g_id not in gids:
            gids.append(g_id)

    def _drop() -> None:
        os.umask(umask_val)
        os.setgroups(gids)
        os.setgid(gid)
        os.setuid(uid)

    return _drop


def _http_json(
    url: str,
    *,
    token: str,
    method: str = "GET",
    body: Mapping[str, Any] | None = None,
    timeout_s: float = 10.0,
) -> tuple[int, dict[str, Any]]:
    opener = build_opener(ProxyHandler({}))
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    data: bytes | None = None
    if body is not None:
        data = json.dumps(dict(body), ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with opener.open(req, timeout=timeout_s) as resp:
            return int(resp.status), json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return int(exc.code), json.loads(raw)
        except json.JSONDecodeError:
            return int(exc.code), {"code": f"http_{exc.code}", "detail": raw[:200]}


def _sanitize_contracts(source: Mapping[str, Any] | None, dossier_mod: Any) -> dict[str, Any]:
    if not isinstance(source, Mapping):
        return {}
    out: dict[str, Any] = {}
    for case, card in zip(source.get("cases") or (), source.get("cards") or ()):
        if not isinstance(case, Mapping) or not isinstance(card, Mapping):
            continue
        side = str(card.get("side") or "")
        if side not in {"put", "call"}:
            continue
        contract = dossier_mod.player.bind(case, card, side)
        quote = dossier_mod.player.values(case, "L2.quote") or {}
        out[side] = {
            "short_instrument": contract.get("short_instrument"),
            "long_instrument": contract.get("long_instrument"),
            "short_strike_usd": contract.get("short_strike_usd"),
            "long_strike_usd": contract.get("long_strike_usd"),
            "credit_btc": quote.get("credit_btc"),
            "valuation_ts_ms": quote.get("valuation_ts_ms"),
        }
    return out


def _sanitize_link_summary(
    hand: Path,
    deal_res: Mapping[str, Any] | None,
    tracking_mod: Any,
) -> dict[str, Any] | None:
    link_doc = tracking_mod.optional(hand / "tracking" / "link.json")
    raw_link = deal_res.get("link") if isinstance(deal_res, Mapping) and isinstance(deal_res.get("link"), Mapping) else None
    if isinstance(link_doc, dict):
        origin = link_doc.get("origin") if isinstance(link_doc.get("origin"), dict) else {}
        t0 = link_doc.get("t0") if isinstance(link_doc.get("t0"), dict) else {}
        t0_econ = t0.get("economy") if isinstance(t0.get("economy"), dict) else {}
        t0_quote = t0.get("quote") if isinstance(t0.get("quote"), dict) else {}
        return {
            "state": link_doc.get("state"),
            "lock_mode": link_doc.get("lock_mode"),
            "delayed_lock": link_doc.get("delayed_lock"),
            "recorded_at_ms": link_doc.get("recorded_at_ms"),
            "dossier_episode_id": origin.get("dossier_episode_id"),
            "tracker_episode_id": t0.get("tracker_episode_id"),
            "t0_locked_at_ms": t0.get("locked_at_ms"),
            "t0_credit_btc": t0_quote.get("credit_btc"),
            "t0_net_credit_btc": t0_econ.get("net_credit_after_entry_fee_btc"),
            "execution_allowed": link_doc.get("execution_allowed"),
        }
    if raw_link is not None:
        return {
            "state": raw_link.get("state"),
            "reason": raw_link.get("reason"),
            "error": raw_link.get("error"),
            "execution_allowed": raw_link.get("execution_allowed"),
        }
    return None


def _import_tools_modules(tools_dir: Path) -> tuple[Any, Any, Any]:
    if __package__:
        from . import astra_evidence_qual_v1 as evidence_qual
        from . import astra_player_dossier_v1 as dossier
        from . import astra_player_tracking_v13 as tracking
        return evidence_qual, dossier, tracking
    resolved = str(tools_dir.resolve())
    if resolved not in sys.path:
        sys.path.insert(0, resolved)
    import astra_evidence_qual_v1 as evidence_qual
    import astra_player_dossier_v1 as dossier
    import astra_player_tracking_v13 as tracking
    return evidence_qual, dossier, tracking


def _import_exec_modules(tools_dir: Path) -> tuple[Any, Any]:
    if __package__:
        from . import astra_exec_store_v1 as exec_store
        from . import astra_execution_coordinator_v1 as coord_mod
        return exec_store, coord_mod
    resolved = str(tools_dir.resolve())
    if resolved not in sys.path:
        sys.path.insert(0, resolved)
    import astra_exec_store_v1 as exec_store
    import astra_execution_coordinator_v1 as coord_mod
    return exec_store, coord_mod


def run_deal_worker(
    *,
    tools_dir: Path,
    trial_root: Path,
    plan_path: Path,
    archive_view: Path,
    workbench_url: str,
    reference: str,
    preflight_only: bool = False,
    service: Any = None,
    llm_config: Mapping[str, Any] | None = None,
    clock: Any = None,
) -> dict[str, Any]:
    """Execute one isolated AD_HOC deal + optional real LLM call + native lock inside trial_root."""
    evidence_qual, dossier, tracking = _import_tools_modules(tools_dir)

    shadow_root = trial_root / "shadow_dossiers"
    plan = dossier.read(plan_path)
    prompt_version = plan.get("prompt_version") or tracking.review.INITIAL_SYSTEM_PROMPT_VERSION
    if prompt_version not in tracking.review.INITIAL_SYSTEM_PROMPTS:
        raise ValueError(f"unsupported plan prompt_version: {prompt_version!r}")
    p0_profile = getattr(dossier.player, "INPUT_PROFILE_P0_STRICT", "p0_strict@1")
    legacy_profile = getattr(dossier.player, "INPUT_PROFILE_LEGACY", "legacy_v1")
    input_profile = plan.get("input_profile") or (
        p0_profile if prompt_version == tracking.review.P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION else legacy_profile
    )
    cand_round = str(plan.get("candidate_round") or "r16")
    eff_clock = clock or dossier.now_ms
    d = dossier.Dossiers(shadow_root, clock=eff_clock)
    eff_service = service or tracking.local_service(workbench_url, os.environ.get("ASTRA_AI_SHADOW_LOCK_TOKEN", ""))

    prep = eff_service(
        "prepare_deal",
        {
            "candidate_round": cand_round,
            "allow_refresh": False,
            "input_profile": input_profile,
        },
    )
    if (
        isinstance(prep, dict)
        and prep.get("readiness_status") in {"L3_OR_L4_INCOMPLETE", "DISCOVERY_INCOMPLETE", "STALE_OR_PRE_SLOT_VIEW"}
        and int(prep.get("deribit_cooldown_remaining_ms") or 0) == 0
    ):
        prep = eff_service(
            "prepare_deal",
            {
                "candidate_round": cand_round,
                "allow_refresh": True,
                "input_profile": input_profile,
            },
        )
    if not (
        isinstance(prep, dict)
        and prep.get("ready_for_deal") is True
        and prep.get("readiness_status") in {"SOURCE_READY", "MARKET_SINGLE_SIDE_ONLY"}
        and isinstance(prep.get("cases"), dict)
    ):
        return {
            "ok": False,
            "stage": "prepare_deal_blocked",
            "llm_called": False,
            "readiness_status": prep.get("readiness_status") if isinstance(prep, dict) else "UNAVAILABLE",
            "blockers": list(prep.get("blockers") or ()) if isinstance(prep, dict) else ["prepare_deal_invalid"],
            "expiry_ms": prep.get("target_expiry_ms") if isinstance(prep, dict) else None,
        }

    expiry = int(prep["target_expiry_ms"])
    dealt_cases = prep["cases"]
    is_single_side = prep.get("readiness_status") == "MARKET_SINGLE_SIDE_ONLY"
    absent_sides = prep.get("absent_sides") if isinstance(prep.get("absent_sides"), dict) else None
    now_qual = int(d.clock())
    qual = evidence_qual.qualify_deal_case_pair(
        dealt_cases.get("put"),
        dealt_cases.get("call"),
        target_expiry_ms=expiry,
        now_ms=now_qual,
        slot_ms=None,
        require_in_slot=False,
        require_live_fresh=True,
        require_content_sha256=True,
        require_l3=True,
        input_profile=input_profile,
        allow_single_side=is_single_side,
    )
    if not qual.get("ok"):
        return {
            "ok": False,
            "stage": "qualify_cases_blocked",
            "llm_called": False,
            "expiry_ms": expiry,
            "reason_codes": list(qual.get("reason_codes") or ()),
            "error": qual.get("error"),
        }

    with tracking._short_writer(d):
        hand = d.expect(expiry, "AD_HOC", plan=plan, slot=None, reference=reference)
    try:
        source = tracking._call_with_supported_kwargs(
            dossier.collect,
            archive_view,
            expiry,
            int(d.clock()),
            startup=True,
            clock=d.clock,
            candidate_round=cand_round,
            fallback_cases=dealt_cases,
            provenance_override="manual ad-hoc shadow trial verified workbench native case + Deribit public read",
            input_profile=input_profile,
            absent_sides=absent_sides,
        )
        with tracking._short_writer(d):
            d.deal(hand, source, input_profile=input_profile)
    except (OSError, LookupError, ValueError) as exc:
        state = "POLICY_BLOCKED" if isinstance(exc, dossier.player.HandNotDealt) else "DATA_BLOCKED"
        with tracking._short_writer(d):
            d.note(hand, state, str(exc))
        return {
            "ok": False,
            "stage": "freeze_blocked",
            "llm_called": False,
            "dossier_id": hand.parent.parent.name,
            "hand_id": hand.name,
            "expiry_ms": expiry,
            "sample": "AD_HOC",
            "scheduled_at_ms": None,
            "reference": reference,
            "block_state": state,
            "reason": str(exc)[:240],
        }

    meta = d.verify_evidence(hand)
    exp = tracking.load(hand / "expected.json")
    contracts = _sanitize_contracts(source, dossier)
    base_receipt: dict[str, Any] = {
        "dossier_id": hand.parent.parent.name,
        "hand_id": hand.name,
        "sample": exp.get("sample"),
        "scheduled_at_ms": exp.get("scheduled_at_ms"),
        "expiry_ms": expiry,
        "reference": reference,
        "plan_id": plan.get("plan_id"),
        "prompt_version": prompt_version,
        "input_profile": input_profile,
        "hand_frozen_at_ms": meta.get("hand_frozen_at_ms"),
        "input_sha256": meta.get("input_sha256"),
        "contracts": contracts,
    }
    if preflight_only:
        return {
            **base_receipt,
            "ok": True,
            "stage": "preflight_frozen",
            "llm_called": False,
        }

    cfg_override = dict(llm_config or {})
    cfg_override.setdefault("max_initial_attempts", 1)
    eff_cfg = tracking._llm_settings(cfg_override)
    t0 = time.monotonic()
    deal_res = tracking._deal_hand(
        d,
        hand,
        eff_service,
        eff_cfg,
        prompt_version=prompt_version,
        input_profile=input_profile,
    )
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    auto_attempts = tracking._auto_attempts(hand)
    last_att = auto_attempts[-1] if auto_attempts else {}
    att_cfg = last_att.get("configuration") if isinstance(last_att.get("configuration"), dict) else {}
    decision_doc = tracking.optional(hand / "decision.json")
    judgment = decision_doc.get("judgment") if isinstance(decision_doc, dict) and isinstance(decision_doc.get("judgment"), dict) else {}
    link_summary = _sanitize_link_summary(hand, deal_res if isinstance(deal_res, dict) else None, tracking)
    deal_status = deal_res.get("status") if isinstance(deal_res, dict) else None

    return {
        **base_receipt,
        "ok": deal_status == "VALID" and (
            judgment.get("action") != "Accept"
            or (isinstance(link_summary, dict) and link_summary.get("state") in {"SHADOW_LOCK_CREATED", "ALREADY_LINKED"})
        ),
        "stage": "deal_completed",
        "llm_called": bool(auto_attempts),
        "elapsed_ms": elapsed_ms,
        "model": last_att.get("model") or eff_cfg.get("model"),
        "reasoning_effort": att_cfg.get("reasoning_effort") or eff_cfg.get("reasoning_effort"),
        "http_status": att_cfg.get("http_status"),
        "usage": att_cfg.get("usage"),
        "attempt_status": last_att.get("status") or deal_status,
        "deal_status": deal_status,
        "deal_error": deal_res.get("error") if isinstance(deal_res, dict) else None,
        "decision_action": judgment.get("action") or (deal_res.get("decision") if isinstance(deal_res, dict) else None),
        "decision_side": judgment.get("side"),
        "decided_at_ms": decision_doc.get("decided_at_ms") if isinstance(decision_doc, dict) else None,
        "link": link_summary,
    }


def run_exec_verify_worker(
    *,
    tools_dir: Path,
    trial_root: Path,
    dossier_id: str | None = None,
    hand_id: str | None = None,
    p5_episode_id: str | None = None,
    expect_write_denied: bool = False,
) -> dict[str, Any]:
    """Verify isolated SHADOW_ONLY coordinator handoff and cross-UID read/write boundaries."""
    exec_store, coord_mod = _import_exec_modules(tools_dir)

    shadow_root = trial_root / "shadow_dossiers"
    exec_state = trial_root / "exec_state"
    exec_state.mkdir(parents=True, exist_ok=True)

    direct_reads: list[str] = []
    if dossier_id and hand_id:
        hand_dir = shadow_root / "dossiers" / dossier_id / "hands" / hand_id
        for rel in ("expected.json", "evidence/identity.json", "decision.json", "tracking/request.json", "tracking/link.json"):
            target = hand_dir / rel
            if target.exists():
                json.loads(target.read_text(encoding="utf-8"))
                direct_reads.append(rel)
        if p5_episode_id:
            cond_file = shadow_root / "dossiers" / dossier_id / "episodes" / p5_episode_id / "condition.json"
            if cond_file.exists():
                json.loads(cond_file.read_text(encoding="utf-8"))
                direct_reads.append(f"episodes/{p5_episode_id}/condition.json")

    write_denied = False
    probe_path = shadow_root / ".exec_write_probe"
    try:
        probe_path.write_text("x", encoding="utf-8")
        probe_path.unlink(missing_ok=True)
        if expect_write_denied:
            raise RuntimeError("UNEXPECTED_EXEC_WRITE_PERMISSION_IN_DOSSIER_ROOT")
    except PermissionError:
        write_denied = True

    contracts, _aux = coord_mod.scan_p5_dossier_root(shadow_root)
    parsed_hand = None
    if hand_id and hand_id in contracts:
        c = contracts[hand_id]
        parsed_hand = {
            "hand_id": c.hand_id,
            "episode_id": c.episode_id,
            "decision": c.decision,
            "side": c.side,
            "short_instrument": c.short_instrument,
            "long_instrument": c.long_instrument,
            "link_state": c.link_state,
            "facts_readable": c.facts_readable,
            "read_error": c.read_error,
            "c_decision_ref_btc": str(c.c_decision_ref_btc) if c.c_decision_ref_btc is not None else None,
        }
    elif dossier_id and hand_id:
        c_state = coord_mod.read_hand_p5_state(
            shadow_root,
            dossier_id=dossier_id,
            hand_id=hand_id,
            p5_episode_id=p5_episode_id or "",
        )
        if c_state is not None:
            parsed_hand = {
                "hand_id": c_state.hand_id,
                "decision": c_state.decision,
                "link_state": c_state.link_state,
                "facts_readable": c_state.facts_readable,
                "read_error": c_state.read_error,
            }

    store = exec_store.ExecutionStore(exec_state / "exec_store.sqlite")
    adapter = coord_mod.DeribitRestAdapter(mode="SHADOW_ONLY", base_url="https://www.deribit.com")
    try:
        coord = coord_mod.ExecutionCoordinator(
            mode="SHADOW_ONLY",
            env_tag="trial",
            store=store,
            adapter=adapter,
            status_path=exec_state / "public_status.json",
            dossier_root=shadow_root,
        )
        coord.startup_init()
        pub_status = coord.run_tick()
    finally:
        adapter.close()
        store.close()

    return {
        "ok": pub_status.get("mode") == "SHADOW_ONLY" and pub_status.get("writes_enabled") is False,
        "direct_reads": direct_reads,
        "write_denied_as_expected": write_denied,
        "scanned_linked_hands": sorted(contracts.keys()),
        "handoff_contract": parsed_hand,
        "coordinator_status": {
            "mode": pub_status.get("mode"),
            "writes_enabled": pub_status.get("writes_enabled"),
            "cockpit_mode": pub_status.get("cockpit_mode"),
            "health": pub_status.get("health"),
            "stale": pub_status.get("stale"),
            "active_episode": pub_status.get("active_episode"),
            "manual_takeover_required": pub_status.get("manual_takeover_required"),
        },
    }


def _prepare_archive_readonly_view(archive: Path, archive_view: Path, *, service_identities: bool) -> None:
    archive_view.mkdir(parents=True, exist_ok=True)
    if service_identities:
        _chown_posix(archive_view, "bitnami", "research")
    if os.name != "nt":
        os.chmod(archive_view, 0o750)
    for sub in _ARCHIVE_READONLY_SUBDIRS:
        src = archive / sub
        dst = archive_view / sub
        if src.exists() and not dst.exists():
            try:
                dst.symlink_to(src, target_is_directory=True)
            except OSError:
                if os.name != "nt":
                    raise
                shutil.copytree(src, dst, dirs_exist_ok=True)


def _resolve_readonly_prod_cooldown_files(wb_env_file: Mapping[str, str]) -> tuple[Path, ...]:
    if os.name == "nt" and "ASTRA_DERIBIT_COOLDOWN_FILE" not in wb_env_file:
        return ()
    raw = (wb_env_file.get("ASTRA_DERIBIT_COOLDOWN_FILE") or "/var/lib/xxproject/workbench/deribit_cooldown.json").strip()
    if not raw:
        return ()
    return (Path(raw),)


def _active_cooldown_remaining_ms(paths: Sequence[Path], *, now_ms: int | None = None) -> int:
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    best_rem = 0
    for p in paths:
        try:
            if not p.is_file():
                continue
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        until = data.get("cooldown_until_ms")
        if isinstance(until, bool) or not isinstance(until, int):
            continue
        if now < until <= now + 360_000:
            best_rem = max(best_rem, until - now)
    return best_rem


def _build_workbench_env(
    wb_env_file: Mapping[str, str],
    *,
    port: int,
    wb_state: Path,
    shadow_root: Path,
    archive: Path,
    wb_token: str,
    shadow_token: str,
    cooldown_file: Path | None = None,
    readonly_cooldown_files: Sequence[Path] = (),
) -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    if os.name != "nt":
        env["HOME"] = str(wb_state)
    for key in _WORKBENCH_READONLY_MARKET_KEYS:
        if key in wb_env_file:
            env[key] = wb_env_file[key]
    eff_cd_file = cooldown_file if cooldown_file is not None else (wb_state / "deribit_cooldown.json")
    env.update(
        {
            "ASTRA_WORKBENCH_HOST": "127.0.0.1",
            "ASTRA_WORKBENCH_PORT": str(port),
            "ASTRA_WORKBENCH_STATE_DIR": str(wb_state),
            "ASTRA_WORKBENCH_TOKEN": wb_token,
            "ASTRA_AI_SHADOW_DOSSIER_ROOT": str(shadow_root),
            "ASTRA_AI_SHADOW_EXPERIMENT": "1",
            "ASTRA_AI_SHADOW_LOCK_TOKEN": shadow_token,
            "ASTRA_DERIBIT_COOLDOWN_FILE": str(eff_cd_file),
            "ASTRA_WORKBENCH_USAGE_LEDGER": str(wb_state / "unused_wb_ledger.json"),
            "ASTRA_WORKBENCH_REFRESH_SECONDS": "120",
            "ASTRA_SCHEDULED_REVIEW_HOURS_UTC": "",
            "ASTRA_WORKBENCH_WPEM_ENABLED": "1",
            "ASTRA_EXPIRY_GAMMA_ENABLED": "1",
            "ASTRA_CROSS_ASSET_ENABLED": "0",
            "ASTRA_CHAIN_AUDIT_ENABLED": "0",
            "ASTRA_RESEARCH_ARCHIVE_ENABLED": "0",
            "ASTRA_RESEARCH_ARCHIVE": str(archive),
            "ASTRA_EXEC_STATUS_PATH": "",
        }
    )
    if readonly_cooldown_files:
        env["ASTRA_DERIBIT_COOLDOWN_READONLY_FILES"] = os.pathsep.join(str(p) for p in readonly_cooldown_files)
    return env


def _build_deal_env(
    llm_env_file: Mapping[str, str],
    wb_env_file: Mapping[str, str],
    *,
    deal_run: Path,
    shadow_root: Path,
    shadow_token: str,
    cooldown_file: Path | None = None,
    readonly_cooldown_files: Sequence[Path] = (),
) -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    if os.name != "nt":
        env["HOME"] = str(deal_run)
    env.update(llm_env_file)
    env.update(wb_env_file)
    eff_cd_file = cooldown_file if cooldown_file is not None else (deal_run / "deribit_cooldown.json")
    env.update(
        {
            "ASTRA_AI_SHADOW_LOCK_TOKEN": shadow_token,
            "ASTRA_AI_SHADOW_DOSSIER_ROOT": str(shadow_root),
            "ASTRA_DERIBIT_COOLDOWN_FILE": str(eff_cd_file),
            "ASTRA_WORKBENCH_STATE_DIR": str(deal_run / "unused_wb_state"),
        }
    )
    if readonly_cooldown_files:
        env["ASTRA_DERIBIT_COOLDOWN_READONLY_FILES"] = os.pathsep.join(str(p) for p in readonly_cooldown_files)
    else:
        env.pop("ASTRA_DERIBIT_COOLDOWN_READONLY_FILES", None)
    return env


def _read_log_tail(log_path: Path | None, limit: int = 500) -> str:
    if log_path is None or not log_path.is_file():
        return ""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    return text[-limit:] if len(text) > limit else text


def _wait_workbench_ready(
    proc: subprocess.Popen[bytes],
    workbench_url: str,
    shadow_token: str,
    *,
    timeout_s: float,
    log_path: Path | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + float(timeout_s)
    last_err = ""
    while time.monotonic() < deadline:
        rc = proc.poll()
        if rc is not None:
            tail = _read_log_tail(log_path)
            suffix = f" log_tail={tail!r}" if tail else ""
            raise RuntimeError(f"isolated_workbench_exited_early:{rc}{suffix}")
        try:
            st, body = _http_json(
                f"{workbench_url}/api/workbench/shadow-tracking/readiness",
                token=shadow_token,
                method="POST",
                body={},
                timeout_s=10.0,
            )
            if st == 200 and isinstance(body, dict) and body.get("view_updated_at_ms") is not None:
                cd_ms = int(body.get("deribit_cooldown_remaining_ms") or 0)
                if cd_ms <= 0:
                    return body
                last_err = f"deribit_cooldown_remaining_ms={cd_ms}"
            else:
                last_err = f"status={st}"
        except (OSError, URLError, HTTPError, ValueError) as exc:
            last_err = f"{type(exc).__name__}:{exc}"
        time.sleep(0.5)
    tail = _read_log_tail(log_path)
    suffix = f" log_tail={tail!r}" if tail else ""
    raise TimeoutError(f"isolated_workbench_ready_timeout:{last_err}{suffix}")


@contextmanager
def _managed_trial_root(*, service_identities: bool, keep_temp: bool):
    trial_root = Path(tempfile.mkdtemp(prefix="astra-manual-shadow-trial-"))
    if os.name != "nt":
        os.chmod(trial_root, 0o755)
    try:
        shadow_root = trial_root / "shadow_dossiers"
        wb_state = trial_root / "wb_state"
        deal_run = trial_root / "deal_run"
        exec_state = trial_root / "exec_state"
        archive_view = trial_root / "archive_view"
        shared_cd_dir = trial_root / "shared_cooldown"
        for d_path, user, grp_name, mode in (
            (shadow_root, "bitnami", "research", 0o2750),
            (wb_state, "astra-workbench", "astra-workbench", 0o750),
            (deal_run, "bitnami", "research", 0o750),
            (exec_state, "astra-exec", "astra-exec", 0o700),
            (archive_view, "bitnami", "research", 0o750),
            (shared_cd_dir, "bitnami", "research", 0o2770),
        ):
            d_path.mkdir(parents=True, exist_ok=True)
            if service_identities:
                _chown_posix(d_path, user, grp_name)
            if os.name != "nt":
                os.chmod(d_path, mode)
        yield trial_root
    finally:
        if not keep_temp and trial_root.exists():
            shutil.rmtree(trial_root, ignore_errors=False)


def run_manual_trial(
    *,
    tools_dir: Path,
    plan_path: Path,
    archive: Path,
    llm_env_path: Path,
    workbench_env_path: Path,
    service_identities: bool = False,
    preflight_only: bool = False,
    reference: str | None = None,
    workbench_ready_timeout_s: float = 120.0,
    keep_temp: bool = False,
) -> dict[str, Any]:
    """Run one complete isolated AD_HOC shadow trial across real service identities and clean up."""
    utc_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    trial_ref = reference or f"manual_shadow_trial_{utc_stamp}_{secrets.token_hex(3)}"
    llm_env_map = parse_env_file(llm_env_path)
    wb_env_map = parse_env_file(workbench_env_path)
    script_path = Path(__file__).resolve()

    wb_proc: subprocess.Popen[bytes] | None = None
    wb_terminated = False
    wb_pid: int | None = None
    temp_root_str = ""
    temp_removed = False

    with _managed_trial_root(service_identities=service_identities, keep_temp=keep_temp) as trial_root:
        temp_root_str = str(trial_root)
        shadow_root = trial_root / "shadow_dossiers"
        wb_state = trial_root / "wb_state"
        deal_run = trial_root / "deal_run"
        exec_state = trial_root / "exec_state"
        archive_view = trial_root / "archive_view"
        shared_cd_file = trial_root / "shared_cooldown" / "deribit_cooldown.json"
        ro_cd_files = _resolve_readonly_prod_cooldown_files(wb_env_map)
        _prepare_archive_readonly_view(archive, archive_view, service_identities=service_identities)

        pre_cd_deadline = time.monotonic() + float(workbench_ready_timeout_s)
        while ro_cd_files and time.monotonic() < pre_cd_deadline:
            pre_rem_ms = _active_cooldown_remaining_ms(ro_cd_files)
            if pre_rem_ms <= 0:
                break
            time.sleep(min(pre_rem_ms / 1000.0 + 0.5, max(0.5, pre_cd_deadline - time.monotonic())))

        port = _pick_free_port()
        wb_url = f"http://127.0.0.1:{port}"
        wb_token = secrets.token_hex(16)
        shadow_token = secrets.token_hex(32)
        wb_log = wb_state / "workbench.log"
        deal_result_file = deal_run / "deal_receipt.json"

        wb_env = _build_workbench_env(
            wb_env_map,
            port=port,
            wb_state=wb_state,
            shadow_root=shadow_root,
            archive=archive,
            wb_token=wb_token,
            shadow_token=shadow_token,
            cooldown_file=shared_cd_file,
            readonly_cooldown_files=ro_cd_files,
        )
        deal_env = _build_deal_env(
            llm_env_map,
            wb_env_map,
            deal_run=deal_run,
            shadow_root=shadow_root,
            shadow_token=shadow_token,
            cooldown_file=shared_cd_file,
            readonly_cooldown_files=ro_cd_files,
        )
        exec_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        if os.name != "nt":
            exec_env["HOME"] = str(exec_state)

        wb_preexec = _posix_preexec("astra-workbench", "astra-workbench", ("bitnami", "research"), 0o027) if service_identities else None
        deal_preexec = _posix_preexec("bitnami", "research", ("research",), 0o027) if service_identities else None
        exec_preexec = _posix_preexec("astra-exec", "astra-exec", ("research",), 0o077) if service_identities else None

        try:
            with wb_log.open("wb") as log_fh:
                wb_proc = subprocess.Popen(
                    [sys.executable, str(tools_dir / "astra_workbench_v20.py"), "--host", "127.0.0.1", "--port", str(port)],
                    cwd=str(tools_dir),
                    env=wb_env,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    preexec_fn=wb_preexec,
                )
            wb_pid = wb_proc.pid
            readiness = _wait_workbench_ready(
                wb_proc,
                wb_url,
                shadow_token,
                timeout_s=workbench_ready_timeout_s,
                log_path=wb_log,
            )

            deal_cmd = [
                sys.executable,
                str(script_path),
                "--worker",
                "deal",
                "--tools-dir",
                str(tools_dir),
                "--trial-root",
                str(trial_root),
                "--plan",
                str(plan_path),
                "--archive",
                str(archive_view),
                "--workbench-url",
                wb_url,
                "--reference",
                trial_ref,
                "--result-file",
                str(deal_result_file),
            ]
            if preflight_only:
                deal_cmd.append("--preflight-only")

            for attempt in range(3):
                deal_run_res = subprocess.run(
                    deal_cmd,
                    cwd=str(tools_dir),
                    env=deal_env,
                    capture_output=True,
                    text=True,
                    preexec_fn=deal_preexec,
                )
                if deal_run_res.returncode != 0 or not deal_result_file.is_file():
                    raise RuntimeError(
                        f"deal_worker_failed rc={deal_run_res.returncode} stderr={deal_run_res.stderr[-300:]!r}"
                    )
                deal_receipt = json.loads(deal_result_file.read_text(encoding="utf-8"))
                if not (
                    attempt < 2
                    and deal_receipt.get("stage") == "prepare_deal_blocked"
                    and deal_receipt.get("readiness_status") == "DERIBIT_429_COOLDOWN"
                    and deal_receipt.get("llm_called") is False
                ):
                    break
                eff_cd_ms = _active_cooldown_remaining_ms([shared_cd_file, *ro_cd_files])
                time.sleep(max(2.0, min(60.0, (eff_cd_ms / 1000.0) + 1.5)))
                readiness = _wait_workbench_ready(
                    wb_proc,
                    wb_url,
                    shadow_token,
                    timeout_s=workbench_ready_timeout_s,
                    log_path=wb_log,
                )

            wb_check: dict[str, Any] = {"ok": False}
            dossier_id = deal_receipt.get("dossier_id")
            hand_id = deal_receipt.get("hand_id")
            st_lck, locked_body = _http_json(f"{wb_url}/api/workbench/locked-candidates", token=wb_token, timeout_s=15.0)
            wb_check["locked_candidates_status"] = st_lck
            episodes = (locked_body.get("episodes") if isinstance(locked_body, dict) else None) or (
                locked_body.get("items") if isinstance(locked_body, dict) else None
            ) or ()
            wb_check["locked_count"] = len(episodes) if isinstance(locked_body, dict) else None
            wb_check["locked_episode_ids"] = [
                str(ep.get("episode_id"))
                for ep in episodes
                if isinstance(ep, dict) and ep.get("episode_id")
            ]
            if dossier_id and hand_id:
                st_aud, aud_body = _http_json(
                    f"{wb_url}/api/workbench/player-audit/{dossier_id}/{hand_id}",
                    token=wb_token,
                    timeout_s=15.0,
                )
                wb_check["audit_status"] = st_aud
                wb_check["audit_state"] = aud_body.get("state") if isinstance(aud_body, dict) else None
                wb_check["audit_tracking_state"] = aud_body.get("tracking_state") if isinstance(aud_body, dict) else None
                wb_check["tracking_read_error"] = aud_body.get("tracking_read_error") if isinstance(aud_body, dict) else None
                wb_check["ok"] = st_aud == 200 and st_lck == 200 and (aud_body.get("tracking_read_error") is None)
            else:
                wb_check["ok"] = st_lck == 200

            p5_ep_id = (deal_receipt.get("link") or {}).get("dossier_episode_id") if isinstance(deal_receipt.get("link"), dict) else None
            exec_cmd = [
                sys.executable,
                str(script_path),
                "--worker",
                "exec_verify",
                "--tools-dir",
                str(tools_dir),
                "--trial-root",
                str(trial_root),
            ]
            if dossier_id:
                exec_cmd.extend(["--dossier-id", str(dossier_id)])
            if hand_id:
                exec_cmd.extend(["--hand-id", str(hand_id)])
            if p5_ep_id:
                exec_cmd.extend(["--p5-episode-id", str(p5_ep_id)])
            if service_identities:
                exec_cmd.append("--expect-write-denied")

            exec_run_res = subprocess.run(
                exec_cmd,
                cwd=str(tools_dir),
                env=exec_env,
                capture_output=True,
                text=True,
                preexec_fn=exec_preexec,
            )
            if exec_run_res.returncode != 0:
                raise RuntimeError(
                    f"exec_verify_worker_failed rc={exec_run_res.returncode} stderr={exec_run_res.stderr[-300:]!r}"
                )
            exec_check = json.loads(exec_run_res.stdout)
        finally:
            if wb_proc is not None:
                if wb_proc.poll() is None:
                    wb_proc.terminate()
                    try:
                        wb_proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        wb_proc.kill()
                        wb_proc.wait(timeout=5)
                wb_terminated = wb_proc.poll() is not None

    temp_removed = not Path(temp_root_str).exists() if temp_root_str else True
    return {
        "schema": "astra_player_manual_trial@1.0.0",
        "trial_ref": trial_ref,
        "tools_dir": str(tools_dir.resolve()),
        "service_identities": bool(service_identities),
        "preflight_only": bool(preflight_only),
        "readiness_check": {
            "enabled": readiness.get("enabled"),
            "view_updated_at_ms": readiness.get("view_updated_at_ms"),
            "discovery_capture_state": readiness.get("discovery_capture_state"),
        },
        "deal": deal_receipt,
        "isolated_workbench_check": wb_check,
        "exec_coordinator_check": exec_check,
        "cleanup": {
            "workbench_pid": wb_pid,
            "workbench_terminated": wb_terminated,
            "temp_root_removed": temp_removed,
            "keep_temp": bool(keep_temp),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", choices=("deal", "exec_verify"), default=None)
    parser.add_argument("--tools-dir", type=Path, default=DEFAULT_TOOLS_DIR)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--llm-env", type=Path, default=DEFAULT_LLM_ENV)
    parser.add_argument("--workbench-env", type=Path, default=DEFAULT_WORKBENCH_ENV)
    parser.add_argument("--service-identities", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--reference", default=None)
    parser.add_argument("--workbench-ready-timeout", type=float, default=120.0)
    parser.add_argument("--keep-temp", action="store_true")
    # Internal worker arguments
    parser.add_argument("--trial-root", type=Path, default=None)
    parser.add_argument("--workbench-url", default="")
    parser.add_argument("--result-file", type=Path, default=None)
    parser.add_argument("--dossier-id", default=None)
    parser.add_argument("--hand-id", default=None)
    parser.add_argument("--p5-episode-id", default=None)
    parser.add_argument("--expect-write-denied", action="store_true")
    args = parser.parse_args(argv)

    if args.worker == "deal":
        assert args.trial_root is not None and args.result_file is not None
        out = run_deal_worker(
            tools_dir=args.tools_dir,
            trial_root=args.trial_root,
            plan_path=args.plan,
            archive_view=args.archive,
            workbench_url=args.workbench_url,
            reference=args.reference or "manual_shadow_trial",
            preflight_only=args.preflight_only,
        )
        raw = (json.dumps(out, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        args.result_file.write_bytes(raw)
        if os.name != "nt":
            os.chmod(args.result_file, 0o640)
        return 0

    if args.worker == "exec_verify":
        assert args.trial_root is not None
        out = run_exec_verify_worker(
            tools_dir=args.tools_dir,
            trial_root=args.trial_root,
            dossier_id=args.dossier_id,
            hand_id=args.hand_id,
            p5_episode_id=args.p5_episode_id,
            expect_write_denied=args.expect_write_denied,
        )
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    out = run_manual_trial(
        tools_dir=args.tools_dir,
        plan_path=args.plan,
        archive=args.archive,
        llm_env_path=args.llm_env,
        workbench_env_path=args.workbench_env,
        service_identities=args.service_identities,
        preflight_only=args.preflight_only,
        reference=args.reference,
        workbench_ready_timeout_s=args.workbench_ready_timeout,
        keep_temp=args.keep_temp,
    )
    print(json.dumps(out, ensure_ascii=False, indent=2))
    deal = out.get("deal") or {}
    wb_check = out.get("isolated_workbench_check") or {}
    exec_check = out.get("exec_coordinator_check") or {}
    cleanup = out.get("cleanup") or {}
    ok = (
        deal.get("ok") is True
        and wb_check.get("ok") is True
        and exec_check.get("ok") is True
        and cleanup.get("workbench_terminated") is True
        and (bool(cleanup.get("keep_temp")) or cleanup.get("temp_root_removed") is True)
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
