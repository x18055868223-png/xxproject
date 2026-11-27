"""Single-writer SQLite WAL execution store, epoch fencing, continuous capital auth audit, and rebuildable ledger."""

from __future__ import annotations

from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Mapping

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:
    msvcrt = None  # type: ignore[assignment]

if __package__:
    from .astra_exec_contracts_v1 import (
        D,
        ONE,
        SAT,
        ZERO,
        BookStatus,
        CapitalAuthorization,
        CommandStatus,
        EpisodePatch,
        EpisodeState,
        ExecCommand,
        ExchangeFacts,
        LifecyclePhase,
        VenueTrade,
        ceil_1e8,
        classify_trade_attribution,
        classify_tx_log_attribution,
        compute_effective_budget_btc,
        compute_episode_expiry_settlement,
        dataclass_to_jsonable,
        dec_str,
        delivery_fee_btc,
        inverse_option_expiry_payoff_btc,
        matches_episode_future,
        opt_dec,
        option_trade_fee_btc,
        trade_purpose_for_episode,
    )
else:
    from astra_exec_contracts_v1 import (
        D,
        ONE,
        SAT,
        ZERO,
        BookStatus,
        CapitalAuthorization,
        CommandStatus,
        EpisodePatch,
        EpisodeState,
        ExecCommand,
        ExchangeFacts,
        LifecyclePhase,
        VenueTrade,
        ceil_1e8,
        classify_trade_attribution,
        classify_tx_log_attribution,
        compute_effective_budget_btc,
        compute_episode_expiry_settlement,
        dataclass_to_jsonable,
        dec_str,
        delivery_fee_btc,
        inverse_option_expiry_payoff_btc,
        matches_episode_future,
        opt_dec,
        option_trade_fee_btc,
        trade_purpose_for_episode,
    )

STORE_SCHEMA = "astra_exec_store@1.2.0"


class WriterLockError(RuntimeError):
    """Raised when the single-writer OS lock cannot be acquired."""


class StaleWriterEpochError(RuntimeError):
    """Raised when a stale writer attempts to mutate after epoch advanced."""


class EpisodeCreationError(ValueError):
    """Raised when episode creation violates active-slot, replay, or bound invariants."""


def _acquire_os_lock(lock_path: Path) -> int:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    fd = os.open(str(lock_path), flags, 0o600)
    try:
        if os.name != "nt":
            os.chmod(lock_path, 0o600)
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif msvcrt is not None:
            os.lseek(fd, 0, os.SEEK_SET)
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        os.close(fd)
        raise WriterLockError(f"EXEC_WRITER_LOCK_HELD: {lock_path}") from exc
    payload = f"pid={os.getpid()}\n".encode("utf-8")
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, payload)
    os.ftruncate(fd, max(1, len(payload)))
    os.fsync(fd)
    return fd


def _release_os_lock(fd: int | None) -> None:
    if fd is None:
        return
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        elif msvcrt is not None:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _legacy_phase_map(raw_phase: str) -> LifecyclePhase:
    if raw_phase == "AWAITING_APPROVAL":
        return LifecyclePhase.ENTRY
    if raw_phase == "ENTRY_CLOSED":
        return LifecyclePhase.TP_MANAGED
    return LifecyclePhase(raw_phase)


def _row_col(row: sqlite3.Row, name: str, default: Any = None) -> Any:
    keys = row.keys()
    return row[name] if name in keys and row[name] is not None else default


def _row_to_episode(row: sqlite3.Row) -> EpisodeState:
    return EpisodeState(
        episode_id=row["episode_id"],
        env=row["env"],
        subaccount_id=row["subaccount_id"],
        dossier_id=row["dossier_id"],
        hand_id=row["hand_id"],
        p5_episode_id=row["p5_episode_id"],
        side=row["side"],
        short_instrument=row["short_instrument"],
        long_instrument=row["long_instrument"],
        short_strike_usd=D(row["short_strike_usd"]),
        long_strike_usd=D(row["long_strike_usd"]),
        expiry_ms=int(row["expiry_ms"]),
        contract_hash=row["contract_hash"],
        phase=_legacy_phase_map(str(row["phase"])),
        created_at_ms=int(row["created_at_ms"]),
        auth_epoch=int(_row_col(row, "auth_epoch", 0)),
        target_qty_btc=D(_row_col(row, "target_qty_btc", "0.1")),
        min_net_credit_per_btc=D(_row_col(row, "min_net_credit_per_btc", "0.0008")),
        max_zero_rec_ep_outlay_btc=D(_row_col(row, "max_zero_rec_ep_outlay_btc", "0.0006")),
        effective_budget_at_entry_btc=D(_row_col(row, "effective_budget_at_entry_btc", "0.05")),
        margin_model_at_entry=str(_row_col(row, "margin_model_at_entry", "SM")),
        entry_spot_usd=opt_dec(_row_col(row, "entry_spot_usd", None)),
        decided_at_ms=int(_row_col(row, "decided_at_ms", 0)),
        link_recorded_at_ms=int(_row_col(row, "link_recorded_at_ms", 0)),
        c_decision_ref_btc=opt_dec(_row_col(row, "c_decision_ref_btc", None)),
        estimated_abort_loss_at_ep_btc=opt_dec(_row_col(row, "estimated_abort_loss_at_ep_btc", None)),
        entry_started_at_ms=row["entry_started_at_ms"],
        last_ep_fill_at_ms=_row_col(row, "last_ep_fill_at_ms", None),
        entry_halted=bool(row["entry_halted"]),
        entry_halt_reason=row["entry_halt_reason"],
        entry_closed=bool(row["entry_closed"]),
        entry_closed_at_ms=row["entry_closed_at_ms"],
        entry_closure_reason=row["entry_closure_reason"],
        matched_qty_btc=D(row["matched_qty_btc"]),
        matched_short_proceeds_btc=D(row["matched_short_proceeds_btc"]),
        matched_prot_cost_ex_fee_btc=D(row["matched_prot_cost_ex_fee_btc"]),
        matched_entry_fees_btc=D(row["matched_entry_fees_btc"]),
        matched_net_credit_btc=D(row["matched_net_credit_btc"]),
        unmatched_prot_qty_btc=D(row["unmatched_prot_qty_btc"]),
        unmatched_prot_cost_btc=D(row["unmatched_prot_cost_btc"]),
        tp_budget_total_btc=D(row["tp_budget_total_btc"]),
        tp_cap_init_btc=D(row["tp_cap_init_btc"]),
        tp_target_capture_ratio=opt_dec(_row_col(row, "tp_target_capture_ratio", None)),
        tp_substate=row["tp_substate"],
        exit_latched=bool(row["exit_latched"]),
        exit_latch_reason=row["exit_latch_reason"],
        exit_started_at_ms=row["exit_started_at_ms"],
        operator_exit_all=bool(_row_col(row, "operator_exit_all", 0)),
        rx_quote_samples=int(_row_col(row, "rx_quote_samples", 0)),
        rx_cash_wall_10009=bool(_row_col(row, "rx_cash_wall_10009", 0)),
        short_liability_cleared=bool(row["short_liability_cleared"]),
        inventory_cleared=bool(row["inventory_cleared"]),
        cashflow_settled=bool(row["cashflow_settled"]),
        terminal_outcome=row["terminal_outcome"],
        next_cmd_seq=int(row["next_cmd_seq"]),
        last_entry_attempt_ms=int(row["last_entry_attempt_ms"]),
        entry_attempts=int(row["entry_attempts"]),
        last_rx_attempt_ms=int(row["last_rx_attempt_ms"]),
        rx_attempts=int(row["rx_attempts"]),
        last_lx_attempt_ms=int(row["last_lx_attempt_ms"]),
        alerts=tuple(json.loads(row["alerts_json"] or "[]")),
    )


def _row_to_command(row: sqlite3.Row) -> ExecCommand:
    return ExecCommand(
        cmd_id=row["cmd_id"],
        episode_id=row["episode_id"],
        purpose=row["purpose"],
        label=row["label"],
        instrument_name=row["instrument_name"],
        direction=row["direction"],
        amount=D(row["amount"]),
        limit_price=D(row["limit_price"]),
        time_in_force=row["time_in_force"],
        reduce_only=bool(row["reduce_only"]),
        post_only=bool(row["post_only"]),
        reject_post_only=bool(row["reject_post_only"]),
        valid_until_ms=int(row["valid_until_ms"]),
        created_at_ms=int(row["created_at_ms"]),
        status=CommandStatus(row["status"]),
        venue_order_id=row["venue_order_id"],
        filled_amount=D(row["filled_amount"]),
        last_error=row["last_error"],
        updated_at_ms=int(row["updated_at_ms"]),
        cancel_requested_at_ms=row["cancel_requested_at_ms"],
    )


def _default_capital_auth_json() -> str:
    return json.dumps(
        {
            "entry_armed": False,
            "budget_cap_btc": "0",
            "max_qty_btc": "0.1",
            "grant_baseline_pnl_btc": "0",
            "auth_epoch": 0,
            "updated_at_ms": 0,
            "operator": "init",
            "note": "unarmed_default",
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def _parse_capital_auth(raw: str | None) -> CapitalAuthorization:
    if not raw:
        return CapitalAuthorization()
    obj = json.loads(raw)
    return CapitalAuthorization(
        entry_armed=bool(obj.get("entry_armed", False)),
        budget_cap_btc=D(obj.get("budget_cap_btc", "0")),
        max_qty_btc=D(obj.get("max_qty_btc", "0.1")),
        grant_baseline_pnl_btc=D(obj.get("grant_baseline_pnl_btc", "0")),
        auth_epoch=int(obj.get("auth_epoch", 0)),
        updated_at_ms=int(obj.get("updated_at_ms", 0)),
        operator=str(obj.get("operator") or "init"),
        note=str(obj.get("note") or ""),
    )


def _serialize_capital_auth(auth: CapitalAuthorization) -> str:
    return json.dumps(dataclass_to_jsonable(auth), sort_keys=True, ensure_ascii=False)


_UNSET = object()


class ExecutionStore:
    """Single-writer SQLite WAL store with 4 core tables, capital auth audit chain, and rebuildable ledger."""

    def __init__(self, db_path: str | Path, *, lock_path: str | Path | None = None, read_only: bool = False) -> None:
        self.db_path = Path(db_path)
        self.lock_path = Path(lock_path) if lock_path else self.db_path.with_suffix(".writer.lock")
        self.read_only = read_only
        self._lock_fd: int | None = None
        self.writer_epoch: int | None = None
        self.conn: sqlite3.Connection

        if read_only:
            uri = f"file:{self.db_path.as_posix()}?mode=ro"
            self.conn = sqlite3.connect(uri, uri=True, timeout=5.0, isolation_level=None, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            return

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_fd = _acquire_os_lock(self.lock_path)
        try:
            self.conn = sqlite3.connect(str(self.db_path), timeout=5.0, isolation_level=None, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            if os.name != "nt" and self.db_path.exists():
                os.chmod(self.db_path, 0o600)
            self._init_schema()
            self._advance_epoch()
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        try:
            if hasattr(self, "conn"):
                self.conn.close()
        finally:
            _release_os_lock(self._lock_fd)
            self._lock_fd = None

    def _init_schema(self) -> None:
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=FULL;")
        self.conn.execute("PRAGMA foreign_keys=ON;")
        self.conn.execute("PRAGMA busy_timeout=5000;")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS episodes (
                episode_id TEXT PRIMARY KEY,
                env TEXT NOT NULL,
                subaccount_id TEXT NOT NULL,
                dossier_id TEXT NOT NULL,
                hand_id TEXT NOT NULL UNIQUE,
                p5_episode_id TEXT NOT NULL,
                side TEXT NOT NULL,
                short_instrument TEXT NOT NULL,
                long_instrument TEXT NOT NULL,
                short_strike_usd TEXT NOT NULL,
                long_strike_usd TEXT NOT NULL,
                expiry_ms INTEGER NOT NULL,
                contract_hash TEXT NOT NULL,
                phase TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                auth_epoch INTEGER NOT NULL DEFAULT 0,
                target_qty_btc TEXT NOT NULL DEFAULT '0.1',
                min_net_credit_per_btc TEXT NOT NULL DEFAULT '0.0008',
                max_zero_rec_ep_outlay_btc TEXT NOT NULL DEFAULT '0.0006',
                effective_budget_at_entry_btc TEXT NOT NULL DEFAULT '0.05',
                margin_model_at_entry TEXT NOT NULL DEFAULT 'SM',
                entry_spot_usd TEXT,
                decided_at_ms INTEGER NOT NULL DEFAULT 0,
                link_recorded_at_ms INTEGER NOT NULL DEFAULT 0,
                c_decision_ref_btc TEXT,
                estimated_abort_loss_at_ep_btc TEXT,
                entry_started_at_ms INTEGER,
                last_ep_fill_at_ms INTEGER,
                entry_halted INTEGER NOT NULL DEFAULT 0,
                entry_halt_reason TEXT,
                entry_closed INTEGER NOT NULL DEFAULT 0,
                entry_closed_at_ms INTEGER,
                entry_closure_reason TEXT,
                matched_qty_btc TEXT NOT NULL DEFAULT '0',
                matched_short_proceeds_btc TEXT NOT NULL DEFAULT '0',
                matched_prot_cost_ex_fee_btc TEXT NOT NULL DEFAULT '0',
                matched_entry_fees_btc TEXT NOT NULL DEFAULT '0',
                matched_net_credit_btc TEXT NOT NULL DEFAULT '0',
                unmatched_prot_qty_btc TEXT NOT NULL DEFAULT '0',
                unmatched_prot_cost_btc TEXT NOT NULL DEFAULT '0',
                tp_budget_total_btc TEXT NOT NULL DEFAULT '0',
                tp_cap_init_btc TEXT NOT NULL DEFAULT '0',
                tp_target_capture_ratio TEXT,
                tp_substate TEXT,
                exit_latched INTEGER NOT NULL DEFAULT 0,
                exit_latch_reason TEXT,
                exit_started_at_ms INTEGER,
                operator_exit_all INTEGER NOT NULL DEFAULT 0,
                rx_quote_samples INTEGER NOT NULL DEFAULT 0,
                rx_cash_wall_10009 INTEGER NOT NULL DEFAULT 0,
                short_liability_cleared INTEGER NOT NULL DEFAULT 0,
                inventory_cleared INTEGER NOT NULL DEFAULT 0,
                cashflow_settled INTEGER NOT NULL DEFAULT 0,
                terminal_outcome TEXT,
                next_cmd_seq INTEGER NOT NULL DEFAULT 1,
                last_entry_attempt_ms INTEGER NOT NULL DEFAULT 0,
                entry_attempts INTEGER NOT NULL DEFAULT 0,
                last_rx_attempt_ms INTEGER NOT NULL DEFAULT 0,
                rx_attempts INTEGER NOT NULL DEFAULT 0,
                last_lx_attempt_ms INTEGER NOT NULL DEFAULT 0,
                alerts_json TEXT NOT NULL DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS commands (
                cmd_id TEXT PRIMARY KEY,
                episode_id TEXT NOT NULL,
                purpose TEXT NOT NULL,
                label TEXT NOT NULL UNIQUE,
                instrument_name TEXT NOT NULL,
                direction TEXT NOT NULL,
                amount TEXT NOT NULL,
                limit_price TEXT NOT NULL,
                time_in_force TEXT NOT NULL,
                reduce_only INTEGER NOT NULL,
                post_only INTEGER NOT NULL,
                reject_post_only INTEGER NOT NULL,
                valid_until_ms INTEGER NOT NULL,
                created_at_ms INTEGER NOT NULL,
                status TEXT NOT NULL,
                venue_order_id TEXT,
                filled_amount TEXT NOT NULL DEFAULT '0',
                last_error TEXT,
                updated_at_ms INTEGER NOT NULL,
                cancel_requested_at_ms INTEGER,
                FOREIGN KEY(episode_id) REFERENCES episodes(episode_id)
            );

            CREATE TABLE IF NOT EXISTS venue_events (
                event_uid TEXT PRIMARY KEY,
                episode_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                instrument_name TEXT NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                trade_id TEXT,
                order_id TEXT,
                tx_log_id INTEGER,
                label TEXT,
                raw_json TEXT NOT NULL,
                ingested_at_ms INTEGER NOT NULL,
                FOREIGN KEY(episode_id) REFERENCES episodes(episode_id)
            );

            CREATE INDEX IF NOT EXISTS idx_commands_ep_status ON commands(episode_id, status);
            CREATE INDEX IF NOT EXISTS idx_events_ep_ts ON venue_events(episode_id, timestamp_ms);
            """
        )
        self._migrate_legacy_columns_idempotent()

    def _migrate_legacy_columns_idempotent(self) -> None:
        """Idempotently add v1.2 columns to pre-existing DBs while preserving all historical rows & events."""
        existing = {str(r["name"]) for r in self.conn.execute("PRAGMA table_info(episodes)").fetchall()}
        migrations = (
            ("auth_epoch", "INTEGER NOT NULL DEFAULT 0"),
            ("target_qty_btc", "TEXT NOT NULL DEFAULT '0.1'"),
            ("min_net_credit_per_btc", "TEXT NOT NULL DEFAULT '0.0008'"),
            ("max_zero_rec_ep_outlay_btc", "TEXT NOT NULL DEFAULT '0.0006'"),
            ("effective_budget_at_entry_btc", "TEXT NOT NULL DEFAULT '0.05'"),
            ("margin_model_at_entry", "TEXT NOT NULL DEFAULT 'SM'"),
            ("entry_spot_usd", "TEXT"),
            ("decided_at_ms", "INTEGER NOT NULL DEFAULT 0"),
            ("link_recorded_at_ms", "INTEGER NOT NULL DEFAULT 0"),
            ("c_decision_ref_btc", "TEXT"),
            ("estimated_abort_loss_at_ep_btc", "TEXT"),
            ("last_ep_fill_at_ms", "INTEGER"),
            ("tp_target_capture_ratio", "TEXT"),
            ("operator_exit_all", "INTEGER NOT NULL DEFAULT 0"),
            ("rx_quote_samples", "INTEGER NOT NULL DEFAULT 0"),
            ("rx_cash_wall_10009", "INTEGER NOT NULL DEFAULT 0"),
        )
        for col, ddl in migrations:
            if col not in existing:
                self.conn.execute(f"ALTER TABLE episodes ADD COLUMN {col} {ddl};")

    def _begin_write(self) -> None:
        if self.read_only:
            raise PermissionError("read-only store cannot mutate")
        self.conn.execute("BEGIN IMMEDIATE;")
        row = self.conn.execute("SELECT value FROM meta WHERE key='writer_epoch'").fetchone()
        db_epoch = int(row["value"]) if row else 0
        if self.writer_epoch is not None and db_epoch != self.writer_epoch:
            self.conn.execute("ROLLBACK;")
            raise StaleWriterEpochError(
                f"STALE_WRITER_EPOCH: instance={self.writer_epoch}, db={db_epoch}"
            )

    def _advance_epoch(self) -> None:
        self.conn.execute("BEGIN IMMEDIATE;")
        try:
            row = self.conn.execute("SELECT value FROM meta WHERE key='writer_epoch'").fetchone()
            next_epoch = (int(row["value"]) if row else 0) + 1
            self.conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('writer_epoch', ?)",
                (str(next_epoch),),
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema', ?)",
                (STORE_SCHEMA,),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('writes_disabled', '0')",
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('capital_auth', ?)",
                (_default_capital_auth_json(),),
            )
            self.conn.execute("COMMIT;")
            self.writer_epoch = next_epoch
        except Exception:
            self.conn.execute("ROLLBACK;")
            raise

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def get_capital_auth(self) -> CapitalAuthorization:
        return _parse_capital_auth(self.get_meta("capital_auth"))

    def get_capital_auth_audits(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT key, value FROM meta WHERE key LIKE 'capital_auth_audit:%' ORDER BY key ASC"
        ).fetchall()
        return [json.loads(str(r["value"])) for r in rows]

    def get_settled_pnl_btc(self) -> Decimal:
        rows = self.conn.execute(
            "SELECT episode_id FROM episodes WHERE cashflow_settled = 1 ORDER BY created_at_ms ASC"
        ).fetchall()
        total = ZERO
        for r in rows:
            ledger = self.rebuild_episode_ledger(str(r["episode_id"]))
            total += D(ledger["accounting"]["episode_total_realized_pnl_btc"])
        return total.quantize(SAT)

    def get_effective_budget_state(self) -> tuple[CapitalAuthorization, Decimal, Decimal]:
        auth = self.get_capital_auth()
        pnl_settled = self.get_settled_pnl_btc()
        b_eff = compute_effective_budget_btc(
            budget_cap_btc=auth.budget_cap_btc,
            pnl_settled_btc=pnl_settled,
            grant_baseline_pnl_btc=auth.grant_baseline_pnl_btc,
        )
        return auth, pnl_settled, b_eff

    def _append_auth_audit_in_tx(
        self,
        *,
        action: str,
        operator: str,
        reason: str,
        before: CapitalAuthorization,
        after: CapitalAuthorization,
        pnl_settled_btc: Decimal,
        now_ms: int,
        episode_id: str | None = None,
    ) -> None:
        audit_key = f"capital_auth_audit:{after.auth_epoch:06d}"
        audit_payload = {
            "audit_key": audit_key,
            "auth_epoch": after.auth_epoch,
            "timestamp_ms": int(now_ms),
            "operator": operator,
            "action": action,
            "reason": reason,
            "episode_id": episode_id,
            "pnl_settled_btc": dec_str(pnl_settled_btc),
            "before": dataclass_to_jsonable(before),
            "after": dataclass_to_jsonable(after),
        }
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?)",
            (audit_key, json.dumps(audit_payload, sort_keys=True, ensure_ascii=False)),
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('capital_auth', ?)",
            (_serialize_capital_auth(after),),
        )

    def set_capital_policy(
        self,
        *,
        entry_armed: bool,
        budget_cap_btc: Decimal | None = None,
        max_qty_btc: Decimal | None = None,
        reset_grant_baseline: bool = False,
        operator: str = "backend_config",
        note: str = "",
        now_ms: int,
    ) -> CapitalAuthorization:
        """Atomically update continuous capital authorization and append immutable audit entry."""
        if budget_cap_btc is not None and budget_cap_btc < ZERO:
            raise ValueError("budget_cap_btc_must_be_non_negative")
        if max_qty_btc is not None and max_qty_btc <= ZERO:
            raise ValueError("max_qty_btc_must_be_positive")
        pnl_settled = self.get_settled_pnl_btc()

        self._begin_write()
        try:
            before = _parse_capital_auth(self.get_meta("capital_auth"))
            new_cap = budget_cap_btc if budget_cap_btc is not None else before.budget_cap_btc
            new_qty = max_qty_btc if max_qty_btc is not None else before.max_qty_btc
            if entry_armed and new_cap <= ZERO:
                raise ValueError("cannot_arm_with_zero_budget_cap")
            new_baseline = pnl_settled if reset_grant_baseline else before.grant_baseline_pnl_btc
            after = CapitalAuthorization(
                entry_armed=bool(entry_armed),
                budget_cap_btc=new_cap,
                max_qty_btc=new_qty,
                grant_baseline_pnl_btc=new_baseline,
                auth_epoch=before.auth_epoch + 1,
                updated_at_ms=int(now_ms),
                operator=operator,
                note=note or ("armed" if entry_armed else "paused"),
            )
            active_row = self.conn.execute(
                "SELECT episode_id, entry_closed FROM episodes WHERE cashflow_settled = 0 ORDER BY created_at_ms DESC LIMIT 1"
            ).fetchone()
            active_ep_id = str(active_row["episode_id"]) if active_row else None
            if not entry_armed and active_row is not None and not bool(active_row["entry_closed"]):
                self.conn.execute(
                    """
                    UPDATE episodes
                    SET entry_halted = 1,
                        entry_halt_reason = COALESCE(entry_halt_reason, 'OPERATOR_PAUSE_ENTRY'),
                        updated_at_ms = ?
                    WHERE episode_id = ?
                    """,
                    (int(now_ms), active_ep_id),
                )
            self._append_auth_audit_in_tx(
                action="SET_CAPITAL_POLICY",
                operator=operator,
                reason=note or ("RESET_GRANT_BASELINE" if reset_grant_baseline else "UPDATE_CAPITAL_POLICY"),
                before=before,
                after=after,
                pnl_settled_btc=pnl_settled,
                now_ms=now_ms,
                episode_id=active_ep_id,
            )
            self.conn.execute("COMMIT;")
            return after
        except Exception:
            self.conn.execute("ROLLBACK;")
            raise

    def writes_disabled(self) -> tuple[bool, str | None]:
        flag = self.get_meta("writes_disabled", "0") == "1"
        reason = self.get_meta("writes_disabled_reason")
        return flag, reason

    def set_writes_disabled(
        self,
        reason: str,
        *,
        now_ms: int,
        operator: str = "admin_control",
    ) -> None:
        """One-way emergency stop-writes latch + immutable audit entry (never auto-resumes)."""
        pnl_settled = self.get_settled_pnl_btc()
        self._begin_write()
        try:
            before = _parse_capital_auth(self.get_meta("capital_auth"))
            after = CapitalAuthorization(
                entry_armed=False,
                budget_cap_btc=before.budget_cap_btc,
                max_qty_btc=before.max_qty_btc,
                grant_baseline_pnl_btc=before.grant_baseline_pnl_btc,
                auth_epoch=before.auth_epoch + 1,
                updated_at_ms=int(now_ms),
                operator=operator,
                note=f"writes_disabled:{reason}",
            )
            self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('writes_disabled', '1')")
            self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('writes_disabled_reason', ?)", (reason,))
            self.conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('writes_disabled_at_ms', ?)", (str(int(now_ms)),)
            )
            active_row = self.conn.execute(
                "SELECT episode_id, entry_closed FROM episodes WHERE cashflow_settled = 0 ORDER BY created_at_ms DESC LIMIT 1"
            ).fetchone()
            active_ep_id = str(active_row["episode_id"]) if active_row else None
            if active_row is not None and not bool(active_row["entry_closed"]):
                self.conn.execute(
                    """
                    UPDATE episodes
                    SET entry_halted = 1,
                        entry_halt_reason = COALESCE(entry_halt_reason, 'WRITES_DISABLED'),
                        updated_at_ms = ?
                    WHERE episode_id = ?
                    """,
                    (int(now_ms), active_ep_id),
                )
            self._append_auth_audit_in_tx(
                action="DISABLE_WRITES",
                operator=operator,
                reason=reason,
                before=before,
                after=after,
                pnl_settled_btc=pnl_settled,
                now_ms=now_ms,
                episode_id=active_ep_id,
            )
            self.conn.execute("COMMIT;")
        except Exception:
            self.conn.execute("ROLLBACK;")
            raise

    def reset_writes_disabled_admin_only(self, *, now_ms: int = 0, operator: str = "admin_cli") -> None:
        pnl_settled = self.get_settled_pnl_btc()
        self._begin_write()
        try:
            before = _parse_capital_auth(self.get_meta("capital_auth"))
            after = CapitalAuthorization(
                entry_armed=False,
                budget_cap_btc=before.budget_cap_btc,
                max_qty_btc=before.max_qty_btc,
                grant_baseline_pnl_btc=before.grant_baseline_pnl_btc,
                auth_epoch=before.auth_epoch + 1,
                updated_at_ms=int(now_ms),
                operator=operator,
                note="admin_reset_writes_disabled_unarmed",
            )
            self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('writes_disabled', '0')")
            self.conn.execute("DELETE FROM meta WHERE key IN ('writes_disabled_reason', 'writes_disabled_at_ms')")
            self._append_auth_audit_in_tx(
                action="ADMIN_RESET_WRITES_DISABLED",
                operator=operator,
                reason="admin_reset",
                before=before,
                after=after,
                pnl_settled_btc=pnl_settled,
                now_ms=now_ms,
                episode_id=None,
            )
            self.conn.execute("COMMIT;")
        except Exception:
            self.conn.execute("ROLLBACK;")
            raise

    def latch_exit_all(
        self,
        *,
        now_ms: int,
        operator: str = "admin_control",
        reason: str = "OPERATOR_EXIT_ALL",
    ) -> str | None:
        """Irreversibly latch EXIT_ALL on active episode, disarm new entry, and append audit entry."""
        pnl_settled = self.get_settled_pnl_btc()
        self._begin_write()
        try:
            active_row = self.conn.execute(
                "SELECT episode_id FROM episodes WHERE cashflow_settled = 0 ORDER BY created_at_ms DESC LIMIT 1"
            ).fetchone()
            active_ep_id = str(active_row["episode_id"]) if active_row else None
            before = _parse_capital_auth(self.get_meta("capital_auth"))
            after = CapitalAuthorization(
                entry_armed=False,
                budget_cap_btc=before.budget_cap_btc,
                max_qty_btc=before.max_qty_btc,
                grant_baseline_pnl_btc=before.grant_baseline_pnl_btc,
                auth_epoch=before.auth_epoch + 1,
                updated_at_ms=int(now_ms),
                operator=operator,
                note=f"exit_all:{reason}",
            )
            if active_ep_id is not None:
                self.conn.execute(
                    """
                    UPDATE episodes
                    SET entry_halted = 1,
                        entry_halt_reason = COALESCE(entry_halt_reason, 'OPERATOR_EXIT_ALL'),
                        exit_latched = 1,
                        operator_exit_all = 1,
                        exit_latch_reason = 'OPERATOR_EXIT_ALL',
                        exit_started_at_ms = COALESCE(exit_started_at_ms, ?),
                        updated_at_ms = ?
                    WHERE episode_id = ?
                    """,
                    (int(now_ms), int(now_ms), active_ep_id),
                )
            self._append_auth_audit_in_tx(
                action="EXIT_ALL",
                operator=operator,
                reason=reason,
                before=before,
                after=after,
                pnl_settled_btc=pnl_settled,
                now_ms=now_ms,
                episode_id=active_ep_id,
            )
            self.conn.execute("COMMIT;")
            return active_ep_id
        except Exception:
            self.conn.execute("ROLLBACK;")
            raise

    def latch_entry_halt(
        self,
        episode_id: str,
        *,
        reason: str = "ENTRY_HALTED",
        now_ms: int,
    ) -> None:
        self._begin_write()
        try:
            self.conn.execute(
                """
                UPDATE episodes
                SET entry_halted = 1,
                    entry_halt_reason = COALESCE(entry_halt_reason, ?),
                    updated_at_ms = ?
                WHERE episode_id = ?
                """,
                (reason, int(now_ms), episode_id),
            )
            self.conn.execute("COMMIT;")
        except Exception:
            self.conn.execute("ROLLBACK;")
            raise

    def create_episode(self, ep: EpisodeState) -> None:
        """Atomically persist episode + frozen entry risk/observation snapshots enforcing MAX_EPISODES=1 & anti-replay."""
        if ep.target_qty_btc <= ZERO or ep.min_net_credit_per_btc <= ZERO or ep.max_zero_rec_ep_outlay_btc <= ZERO:
            raise EpisodeCreationError("EPISODE_INVALID_BOUNDS")

        self._begin_write()
        try:
            active_cnt = self.conn.execute(
                "SELECT COUNT(*) AS cnt FROM episodes WHERE cashflow_settled = 0"
            ).fetchone()["cnt"]
            if active_cnt > 0:
                raise EpisodeCreationError("MAX_EPISODES_ACTIVE_OR_UNSETTLED")
            if self.conn.execute("SELECT 1 FROM episodes WHERE hand_id=?", (ep.hand_id,)).fetchone():
                raise EpisodeCreationError("DUPLICATE_HAND_EPISODE")

            self.conn.execute(
                """
                INSERT INTO episodes (
                    episode_id, env, subaccount_id, dossier_id, hand_id, p5_episode_id, side,
                    short_instrument, long_instrument, short_strike_usd, long_strike_usd, expiry_ms,
                    contract_hash, phase, created_at_ms, updated_at_ms,
                    auth_epoch, target_qty_btc, min_net_credit_per_btc, max_zero_rec_ep_outlay_btc,
                    effective_budget_at_entry_btc, margin_model_at_entry, entry_spot_usd,
                    decided_at_ms, link_recorded_at_ms, c_decision_ref_btc, estimated_abort_loss_at_ep_btc,
                    entry_started_at_ms, last_ep_fill_at_ms, entry_halted, entry_halt_reason, entry_closed,
                    entry_closed_at_ms, entry_closure_reason, matched_qty_btc,
                    matched_short_proceeds_btc, matched_prot_cost_ex_fee_btc,
                    matched_entry_fees_btc, matched_net_credit_btc, unmatched_prot_qty_btc,
                    unmatched_prot_cost_btc, tp_budget_total_btc, tp_cap_init_btc, tp_target_capture_ratio,
                    tp_substate, exit_latched, exit_latch_reason, exit_started_at_ms,
                    operator_exit_all, rx_quote_samples, rx_cash_wall_10009,
                    short_liability_cleared, inventory_cleared, cashflow_settled, terminal_outcome,
                    next_cmd_seq, last_entry_attempt_ms, entry_attempts, last_rx_attempt_ms,
                    rx_attempts, last_lx_attempt_ms, alerts_json
                ) VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                    ?,?,?,?,?,?,?,
                    ?,?,?,?,
                    ?,?,?,?,?,
                    ?,?,?,
                    ?,?,
                    ?,?,?,
                    ?,?,?,?,
                    ?,?,?,?,
                    ?,?,?,
                    ?,?,?,?,
                    ?,?,?,?,?,?,?
                )
                """,
                (
                    ep.episode_id,
                    ep.env,
                    ep.subaccount_id,
                    ep.dossier_id,
                    ep.hand_id,
                    ep.p5_episode_id,
                    ep.side,
                    ep.short_instrument,
                    ep.long_instrument,
                    dec_str(ep.short_strike_usd),
                    dec_str(ep.long_strike_usd),
                    ep.expiry_ms,
                    ep.contract_hash,
                    ep.phase.value,
                    ep.created_at_ms,
                    ep.created_at_ms,
                    ep.auth_epoch,
                    dec_str(ep.target_qty_btc),
                    dec_str(ep.min_net_credit_per_btc),
                    dec_str(ep.max_zero_rec_ep_outlay_btc),
                    dec_str(ep.effective_budget_at_entry_btc),
                    ep.margin_model_at_entry,
                    dec_str(ep.entry_spot_usd),
                    ep.decided_at_ms,
                    ep.link_recorded_at_ms,
                    dec_str(ep.c_decision_ref_btc),
                    dec_str(ep.estimated_abort_loss_at_ep_btc),
                    ep.entry_started_at_ms,
                    ep.last_ep_fill_at_ms,
                    int(ep.entry_halted),
                    ep.entry_halt_reason,
                    int(ep.entry_closed),
                    ep.entry_closed_at_ms,
                    ep.entry_closure_reason,
                    dec_str(ep.matched_qty_btc),
                    dec_str(ep.matched_short_proceeds_btc),
                    dec_str(ep.matched_prot_cost_ex_fee_btc),
                    dec_str(ep.matched_entry_fees_btc),
                    dec_str(ep.matched_net_credit_btc),
                    dec_str(ep.unmatched_prot_qty_btc),
                    dec_str(ep.unmatched_prot_cost_btc),
                    dec_str(ep.tp_budget_total_btc),
                    dec_str(ep.tp_cap_init_btc),
                    dec_str(ep.tp_target_capture_ratio),
                    ep.tp_substate,
                    int(ep.exit_latched),
                    ep.exit_latch_reason,
                    ep.exit_started_at_ms,
                    int(ep.operator_exit_all),
                    ep.rx_quote_samples,
                    int(ep.rx_cash_wall_10009),
                    int(ep.short_liability_cleared),
                    int(ep.inventory_cleared),
                    int(ep.cashflow_settled),
                    ep.terminal_outcome,
                    ep.next_cmd_seq,
                    ep.last_entry_attempt_ms,
                    ep.entry_attempts,
                    ep.last_rx_attempt_ms,
                    ep.rx_attempts,
                    ep.last_lx_attempt_ms,
                    json.dumps(list(ep.alerts)),
                ),
            )
            self.conn.execute("COMMIT;")
        except Exception:
            self.conn.execute("ROLLBACK;")
            raise

    def has_consumed_hand(self, hand_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM episodes WHERE hand_id=?", (hand_id,)).fetchone() is not None

    def get_active_episode(self) -> EpisodeState | None:
        row = self.conn.execute(
            "SELECT * FROM episodes WHERE cashflow_settled = 0 ORDER BY created_at_ms DESC LIMIT 1"
        ).fetchone()
        return _row_to_episode(row) if row else None

    def get_latest_episode(self) -> EpisodeState | None:
        row = self.conn.execute("SELECT * FROM episodes ORDER BY created_at_ms DESC LIMIT 1").fetchone()
        return _row_to_episode(row) if row else None

    def get_episode(self, episode_id: str) -> EpisodeState | None:
        row = self.conn.execute("SELECT * FROM episodes WHERE episode_id=?", (episode_id,)).fetchone()
        return _row_to_episode(row) if row else None

    def get_commands(self, episode_id: str) -> list[ExecCommand]:
        rows = self.conn.execute(
            "SELECT * FROM commands WHERE episode_id=? ORDER BY created_at_ms ASC, cmd_id ASC",
            (episode_id,),
        ).fetchall()
        return [_row_to_command(r) for r in rows]

    def get_foreign_attribution_ids(self, episode_id: str) -> tuple[frozenset[str], frozenset[str]]:
        ev_rows = self.conn.execute(
            "SELECT event_uid FROM venue_events WHERE episode_id != ?",
            (episode_id,),
        ).fetchall()
        cmd_rows = self.conn.execute(
            "SELECT venue_order_id FROM commands WHERE episode_id != ? AND venue_order_id IS NOT NULL",
            (episode_id,),
        ).fetchall()
        return (
            frozenset(str(r["event_uid"]) for r in ev_rows if r["event_uid"]),
            frozenset(str(r["venue_order_id"]) for r in cmd_rows if r["venue_order_id"]),
        )

    def get_venue_events(self, episode_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM venue_events WHERE episode_id=? ORDER BY timestamp_ms ASC, event_uid ASC",
            (episode_id,),
        ).fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "event_uid": r["event_uid"],
                    "episode_id": r["episode_id"],
                    "event_type": r["event_type"],
                    "instrument_name": r["instrument_name"],
                    "timestamp_ms": int(r["timestamp_ms"]),
                    "trade_id": r["trade_id"],
                    "order_id": r["order_id"],
                    "tx_log_id": r["tx_log_id"],
                    "label": r["label"],
                    "raw": json.loads(r["raw_json"]),
                    "ingested_at_ms": int(r["ingested_at_ms"]),
                }
            )
        return out

    def apply_episode_patch(self, episode_id: str, patch: EpisodePatch, *, now_ms: int) -> None:
        self._begin_write()
        try:
            self.conn.execute(
                """
                UPDATE episodes SET
                    phase=?, entry_started_at_ms=?, last_ep_fill_at_ms=?,
                    entry_spot_usd=?, estimated_abort_loss_at_ep_btc=?,
                    entry_halted=?, entry_halt_reason=?,
                    entry_closed=?, entry_closed_at_ms=?, entry_closure_reason=?,
                    matched_qty_btc=?, matched_short_proceeds_btc=?, matched_prot_cost_ex_fee_btc=?,
                    matched_entry_fees_btc=?, matched_net_credit_btc=?,
                    unmatched_prot_qty_btc=?, unmatched_prot_cost_btc=?,
                    tp_budget_total_btc=?, tp_cap_init_btc=?, tp_target_capture_ratio=?, tp_substate=?,
                    exit_latched=?, exit_latch_reason=?, exit_started_at_ms=?,
                    operator_exit_all=?, rx_quote_samples=?, rx_cash_wall_10009=?,
                    short_liability_cleared=?, inventory_cleared=?, cashflow_settled=?,
                    terminal_outcome=?, alerts_json=?, updated_at_ms=?
                WHERE episode_id=?
                """,
                (
                    patch.phase.value,
                    patch.entry_started_at_ms,
                    patch.last_ep_fill_at_ms,
                    dec_str(patch.entry_spot_usd),
                    dec_str(patch.estimated_abort_loss_at_ep_btc),
                    int(patch.entry_halted),
                    patch.entry_halt_reason,
                    int(patch.entry_closed),
                    patch.entry_closed_at_ms,
                    patch.entry_closure_reason,
                    dec_str(patch.matched_qty_btc),
                    dec_str(patch.matched_short_proceeds_btc),
                    dec_str(patch.matched_prot_cost_ex_fee_btc),
                    dec_str(patch.matched_entry_fees_btc),
                    dec_str(patch.matched_net_credit_btc),
                    dec_str(patch.unmatched_prot_qty_btc),
                    dec_str(patch.unmatched_prot_cost_btc),
                    dec_str(patch.tp_budget_total_btc),
                    dec_str(patch.tp_cap_init_btc),
                    dec_str(patch.tp_target_capture_ratio),
                    patch.tp_substate,
                    int(patch.exit_latched),
                    patch.exit_latch_reason,
                    patch.exit_started_at_ms,
                    int(patch.operator_exit_all),
                    patch.rx_quote_samples,
                    int(patch.rx_cash_wall_10009),
                    int(patch.short_liability_cleared),
                    int(patch.inventory_cleared),
                    int(patch.cashflow_settled),
                    patch.terminal_outcome,
                    json.dumps(list(patch.alerts)),
                    int(now_ms),
                    episode_id,
                ),
            )
            self.conn.execute("COMMIT;")
        except Exception:
            self.conn.execute("ROLLBACK;")
            raise

    def create_command_intent(
        self,
        cmd: ExecCommand,
        *,
        now_ms: int,
        estimated_abort_loss_btc: Decimal | None = None,
    ) -> ExecCommand:
        """Persist immutable command intent (PREPARED_UNKNOWN) BEFORE touching network."""
        self._begin_write()
        try:
            self.conn.execute(
                """
                INSERT INTO commands (
                    cmd_id, episode_id, purpose, label, instrument_name, direction,
                    amount, limit_price, time_in_force, reduce_only, post_only, reject_post_only,
                    valid_until_ms, created_at_ms, status, venue_order_id, filled_amount,
                    last_error, updated_at_ms, cancel_requested_at_ms
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    cmd.cmd_id,
                    cmd.episode_id,
                    cmd.purpose,
                    cmd.label,
                    cmd.instrument_name,
                    cmd.direction,
                    dec_str(cmd.amount),
                    dec_str(cmd.limit_price),
                    cmd.time_in_force,
                    int(cmd.reduce_only),
                    int(cmd.post_only),
                    int(cmd.reject_post_only),
                    cmd.valid_until_ms,
                    cmd.created_at_ms,
                    cmd.status.value,
                    cmd.venue_order_id,
                    dec_str(cmd.filled_amount),
                    cmd.last_error,
                    int(now_ms),
                    cmd.cancel_requested_at_ms,
                ),
            )
            purpose = cmd.purpose
            entry_inc = 1 if purpose in {"EP", "ES"} else 0
            rx_inc = 1 if purpose == "RX" else 0
            est_abort_str = dec_str(estimated_abort_loss_btc) if estimated_abort_loss_btc is not None else None
            self.conn.execute(
                """
                UPDATE episodes SET
                    next_cmd_seq = next_cmd_seq + 1,
                    entry_attempts = entry_attempts + ?,
                    last_entry_attempt_ms = CASE WHEN ? THEN ? ELSE last_entry_attempt_ms END,
                    rx_attempts = rx_attempts + ?,
                    last_rx_attempt_ms = CASE WHEN ? THEN ? ELSE last_rx_attempt_ms END,
                    last_lx_attempt_ms = CASE WHEN ? THEN ? ELSE last_lx_attempt_ms END,
                    estimated_abort_loss_at_ep_btc = COALESCE(?, estimated_abort_loss_at_ep_btc),
                    updated_at_ms = ?
                WHERE episode_id = ?
                """,
                (
                    entry_inc,
                    entry_inc,
                    int(now_ms),
                    rx_inc,
                    rx_inc,
                    int(now_ms),
                    1 if purpose == "LX" else 0,
                    int(now_ms),
                    est_abort_str,
                    int(now_ms),
                    cmd.episode_id,
                ),
            )
            self.conn.execute("COMMIT;")
            return cmd
        except Exception:
            self.conn.execute("ROLLBACK;")
            raise

    def update_command_state(
        self,
        cmd_id: str,
        *,
        status: CommandStatus,
        now_ms: int,
        venue_order_id: str | None = None,
        filled_amount: Decimal | None = None,
        last_error: Any = _UNSET,
        cancel_requested_at_ms: int | None = None,
    ) -> None:
        self._begin_write()
        try:
            row = self.conn.execute("SELECT * FROM commands WHERE cmd_id=?", (cmd_id,)).fetchone()
            if row is None:
                raise KeyError(f"missing command: {cmd_id}")
            new_filled = (
                dec_str(max(D(row["filled_amount"]), filled_amount))
                if filled_amount is not None
                else row["filled_amount"]
            )
            new_oid = venue_order_id if venue_order_id is not None else row["venue_order_id"]
            new_last_err = row["last_error"] if last_error is _UNSET else last_error
            new_cancel_req = (
                cancel_requested_at_ms if cancel_requested_at_ms is not None else row["cancel_requested_at_ms"]
            )
            self.conn.execute(
                """
                UPDATE commands
                SET status=?, venue_order_id=?, filled_amount=?, last_error=?, updated_at_ms=?, cancel_requested_at_ms=?
                WHERE cmd_id=?
                """,
                (status.value, new_oid, new_filled, new_last_err, int(now_ms), new_cancel_req, cmd_id),
            )
            if row["purpose"] == "RX" and status == CommandStatus.REJECTED and isinstance(new_last_err, str) and (
                "10009" in new_last_err or "not_enough_funds" in new_last_err.lower()
            ):
                self.conn.execute(
                    "UPDATE episodes SET rx_cash_wall_10009 = 1, updated_at_ms = ? WHERE episode_id = ?",
                    (int(now_ms), str(row["episode_id"])),
                )
            elif row["purpose"] == "RX" and status in {CommandStatus.FILLED, CommandStatus.OPEN, CommandStatus.CANCELLED}:
                self.conn.execute(
                    "UPDATE episodes SET rx_cash_wall_10009 = 0, updated_at_ms = ? WHERE episode_id = ?",
                    (int(now_ms), str(row["episode_id"])),
                )
            self.conn.execute("COMMIT;")
        except Exception:
            self.conn.execute("ROLLBACK;")
            raise

    def resolve_stale_unknown_command_admin_only(
        self,
        cmd_id: str,
        *,
        target_status: CommandStatus,
        operator: str,
        evidence_note: str,
        now_ms: int,
        min_age_ms: int = 1_500_000,
    ) -> ExecCommand:
        """Manually resolve an orphan UNKNOWN command past hot-cache/valid_until with immutable audit record."""
        if target_status not in {CommandStatus.EXPIRED_UNFILLED, CommandStatus.CANCELLED, CommandStatus.REJECTED}:
            raise ValueError(f"invalid_admin_resolution_status:{target_status.value}")
        op = str(operator or "").strip()
        note = str(evidence_note or "").strip()
        if not op or not note:
            raise ValueError("operator_and_evidence_note_required")

        self._begin_write()
        try:
            row = self.conn.execute("SELECT * FROM commands WHERE cmd_id=?", (cmd_id,)).fetchone()
            if row is None:
                raise KeyError(f"missing_command:{cmd_id}")
            cur_status = CommandStatus(row["status"])
            if cur_status not in {
                CommandStatus.PREPARED_UNKNOWN,
                CommandStatus.SUBMIT_UNKNOWN,
                CommandStatus.CANCEL_UNKNOWN,
            }:
                raise ValueError(f"command_not_unknown:{cur_status.value}")
            created_ms = int(row["created_at_ms"])
            valid_until_ms = int(row["valid_until_ms"])
            age_ms = max(0, int(now_ms) - created_ms)
            if age_ms < min_age_ms and int(now_ms) <= (valid_until_ms + 5_000):
                raise ValueError(f"unknown_command_not_stale_enough:age_ms={age_ms}")
            prev_err = str(row["last_error"] or "").strip()
            audit_err = f"{prev_err} | [admin_resolved:{op}:{note}]" if prev_err else f"[admin_resolved:{op}:{note}]"
            self.conn.execute(
                "UPDATE commands SET status=?, last_error=?, updated_at_ms=? WHERE cmd_id=?",
                (target_status.value, audit_err[:400], int(now_ms), cmd_id),
            )
            audit_key = f"admin_cmd_resolution:{cmd_id}:{int(now_ms)}"
            audit_val = json.dumps(
                {
                    "cmd_id": cmd_id,
                    "episode_id": row["episode_id"],
                    "label": row["label"],
                    "previous_status": cur_status.value,
                    "target_status": target_status.value,
                    "operator": op,
                    "evidence_note": note,
                    "resolved_at_ms": int(now_ms),
                },
                sort_keys=True,
                ensure_ascii=False,
            )
            self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (audit_key, audit_val))
            self.conn.execute("COMMIT;")
            updated_row = self.conn.execute("SELECT * FROM commands WHERE cmd_id=?", (cmd_id,)).fetchone()
            assert updated_row is not None
            return _row_to_command(updated_row)
        except Exception:
            self.conn.execute("ROLLBACK;")
            raise

    def ingest_order_trades(
        self,
        episode: EpisodeState,
        trades: tuple[VenueTrade, ...] | list[VenueTrade],
        *,
        subaccount_id: str,
        now_ms: int,
        extra_order_id_to_cmd: Mapping[str, ExecCommand] | None = None,
    ) -> tuple[int, Decimal]:
        """Synchronously persist trades returned directly by submit_order response, returning (inserted, accepted_qty)."""
        if not trades:
            return 0, ZERO
        cmds = self.get_commands(episode.episode_id)
        cmd_by_lbl = {c.label: c for c in cmds}
        cmd_by_oid = {c.venue_order_id: c for c in cmds if c.venue_order_id}
        if extra_order_id_to_cmd:
            for oid, cobj in extra_order_id_to_cmd.items():
                if oid:
                    cmd_by_oid[str(oid)] = cobj
        foreign_uids, foreign_oids = self.get_foreign_attribution_ids(episode.episode_id)
        inserted = 0
        accepted_qty = ZERO
        seen_tids: set[str] = set()
        self._begin_write()
        try:
            for tr in trades:
                if tr.trade_id in seen_tids:
                    continue
                seen_tids.add(tr.trade_id)
                accepted, _purpose = classify_trade_attribution(
                    tr,
                    episode=episode,
                    cmd_by_label=cmd_by_lbl,
                    cmd_by_order_id=cmd_by_oid,
                    facts_subaccount_id=subaccount_id,
                    foreign_event_uids=foreign_uids,
                    foreign_order_ids=foreign_oids,
                )
                if not accepted:
                    continue
                accepted_qty += tr.amount
                uid = f"trade:{tr.trade_id}"
                cur = self.conn.execute(
                    """
                    INSERT OR IGNORE INTO venue_events(
                        event_uid, episode_id, event_type, instrument_name, timestamp_ms,
                        trade_id, order_id, tx_log_id, label, raw_json, ingested_at_ms
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        uid,
                        episode.episode_id,
                        "USER_TRADE",
                        tr.instrument_name,
                        tr.timestamp_ms,
                        tr.trade_id,
                        tr.order_id,
                        None,
                        tr.label,
                        json.dumps(dataclass_to_jsonable(tr), sort_keys=True, ensure_ascii=False),
                        int(now_ms),
                    ),
                )
                inserted += cur.rowcount
            self.conn.execute("COMMIT;")
            return inserted, accepted_qty
        except Exception:
            self.conn.execute("ROLLBACK;")
            raise

    def ingest_facts_events(self, episode: EpisodeState, facts: ExchangeFacts, *, now_ms: int) -> int:
        """Idempotently ingest episode-attributable USER_TRADE and TX_LOG facts via shared attribution fences."""
        cmds = self.get_commands(episode.episode_id)
        cmd_by_lbl = {c.label: c for c in cmds}
        cmd_by_oid = {c.venue_order_id: c for c in cmds if c.venue_order_id}
        foreign_uids, foreign_oids = self.get_foreign_attribution_ids(episode.episode_id)

        existing_events = self.get_venue_events(episode.episode_id)
        known_trades: dict[str, tuple[str, Decimal]] = {}
        ep_order_ids = set(cmd_by_oid.keys())
        has_stage1 = False
        for ev in existing_events:
            raw = ev["raw"]
            if ev["event_type"] == "USER_TRADE":
                tid = str(raw["trade_id"])
                if raw.get("order_id"):
                    ep_order_ids.add(str(raw["order_id"]))
                purpose = trade_purpose_for_episode(raw, episode=episode, cmd_by_label=cmd_by_lbl, cmd_by_order_id=cmd_by_oid)
                known_trades[tid] = (purpose, D(raw["amount"]))
            elif ev["event_type"] == "TX_LOG":
                tx_t = str(raw.get("type") or "").lower()
                if tx_t in {"expiry", "exercise", "assignment"}:
                    has_stage1 = True

        inserted = 0
        self._begin_write()
        try:
            for tr in facts.recent_trades:
                accepted, purpose = classify_trade_attribution(
                    tr,
                    episode=episode,
                    cmd_by_label=cmd_by_lbl,
                    cmd_by_order_id=cmd_by_oid,
                    facts_subaccount_id=facts.subaccount_id,
                    foreign_event_uids=foreign_uids,
                    foreign_order_ids=foreign_oids,
                )
                if not accepted or purpose is None:
                    continue
                if tr.order_id:
                    ep_order_ids.add(tr.order_id)
                known_trades.setdefault(tr.trade_id, (purpose, tr.amount))
                uid = f"trade:{tr.trade_id}"
                cur = self.conn.execute(
                    """
                    INSERT OR IGNORE INTO venue_events(
                        event_uid, episode_id, event_type, instrument_name, timestamp_ms,
                        trade_id, order_id, tx_log_id, label, raw_json, ingested_at_ms
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        uid,
                        episode.episode_id,
                        "USER_TRADE",
                        tr.instrument_name,
                        tr.timestamp_ms,
                        tr.trade_id,
                        tr.order_id,
                        None,
                        tr.label,
                        json.dumps(dataclass_to_jsonable(tr), sort_keys=True, ensure_ascii=False),
                        int(now_ms),
                    ),
                )
                inserted += cur.rowcount

            ep_qty = sum((q for p, q in known_trades.values() if p == "EP"), ZERO)
            es_qty = sum((q for p, q in known_trades.values() if p == "ES"), ZERO)
            close_short_qty = sum((q for p, q in known_trades.values() if p in {"TP", "RX"}), ZERO)
            lx_qty = sum((q for p, q in known_trades.values() if p == "LX"), ZERO)
            has_open_at_expiry = (ep_qty > lx_qty) or (es_qty > close_short_qty) or has_stage1
            ep_trade_ids = set(known_trades.keys())

            for entry in facts.tx_logs:
                accepted, tx_role = classify_tx_log_attribution(
                    entry,
                    episode=episode,
                    ep_trade_ids=ep_trade_ids,
                    ep_order_ids=ep_order_ids,
                    facts_subaccount_id=facts.subaccount_id,
                    has_open_option_at_expiry=has_open_at_expiry,
                    foreign_event_uids=foreign_uids,
                    foreign_order_ids=foreign_oids,
                )
                if not accepted:
                    continue
                if tx_role == "stage1":
                    has_stage1 = True
                    has_open_at_expiry = True
                uid = f"txlog:{entry.id}"
                cur = self.conn.execute(
                    """
                    INSERT OR IGNORE INTO venue_events(
                        event_uid, episode_id, event_type, instrument_name, timestamp_ms,
                        trade_id, order_id, tx_log_id, label, raw_json, ingested_at_ms
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        uid,
                        episode.episode_id,
                        "TX_LOG",
                        entry.instrument_name,
                        entry.timestamp_ms,
                        entry.trade_id,
                        entry.order_id,
                        entry.id,
                        None,
                        json.dumps(dataclass_to_jsonable(entry), sort_keys=True, ensure_ascii=False),
                        int(now_ms),
                    ),
                )
                inserted += cur.rowcount

            self.conn.execute("COMMIT;")
            return inserted
        except Exception:
            self.conn.execute("ROLLBACK;")
            raise

    def rebuild_episode_ledger(
        self,
        episode_id: str,
        *,
        facts: ExchangeFacts | None = None,
        shadow_counterfactual: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Rebuild the sole economic ledger + non-gating observations from immutable venue_events + episode metadata."""
        ep = self.get_episode(episode_id)
        if ep is None:
            raise KeyError(f"episode not found: {episode_id}")
        commands = {c.label: c for c in self.get_commands(episode_id)}
        cmds_by_oid = {c.venue_order_id: c for c in commands.values() if c.venue_order_id}
        events = self.get_venue_events(episode_id)

        user_trades: dict[str, dict[str, Any]] = {}
        tx_trade_by_tid: dict[str, dict[str, Any]] = {}
        tx_all: list[dict[str, Any]] = []

        for ev in events:
            raw = ev["raw"]
            if ev["event_type"] == "USER_TRADE":
                tid = str(raw["trade_id"])
                user_trades[tid] = raw
            elif ev["event_type"] == "TX_LOG":
                tx_all.append(raw)
                if str(raw.get("type") or "").lower() == "trade" and raw.get("trade_id"):
                    tx_trade_by_tid[str(raw["trade_id"])] = raw

        def _purpose_for_trade(tr: dict[str, Any], tx: dict[str, Any] | None) -> str:
            return trade_purpose_for_episode(
                tr,
                episode=ep,
                cmd_by_label=commands,
                cmd_by_order_id=cmds_by_oid,
                tx_or_raw=tx,
            )

        all_trade_ids = sorted(set(user_trades) | set(tx_trade_by_tid))
        canonical_trades: list[dict[str, Any]] = []
        ep_qty = ZERO
        ep_gross_cost = ZERO
        ep_fee = ZERO
        es_qty = ZERO
        es_gross_credit = ZERO
        es_fee = ZERO
        close_short_qty = ZERO
        close_short_gross = ZERO
        close_short_fee = ZERO
        tp_gross = ZERO
        tp_fee = ZERO
        rx_gross = ZERO
        rx_fee = ZERO
        lx_qty = ZERO
        lx_gross = ZERO
        lx_fee = ZERO
        trades_missing_tx_log: list[str] = []

        actual_tx_cashflow = ZERO
        actual_tx_fees = ZERO
        estimated_trade_fees = ZERO

        for tid in all_trade_ids:
            tr = user_trades.get(tid) or {}
            tx = tx_trade_by_tid.get(tid)
            inst = str(tr.get("instrument_name") or (tx or {}).get("instrument_name") or "")
            qty = D(tr["amount"]) if "amount" in tr else D(tx["amount"])
            price = D(tr["price"]) if tr.get("price") is not None else D(tx["price"])
            direction = str(tr.get("direction") or "")
            if not direction:
                if inst == ep.long_instrument:
                    direction = "buy" if (tx and D(tx["cashflow"]) < ZERO) else "sell"
                else:
                    direction = "sell" if (tx and D(tx["cashflow"]) > ZERO) else "buy"
            purpose = _purpose_for_trade(tr, tx)

            gross_notional = qty * price
            tr_fee = D(tr["fee"]) if "fee" in tr else (D(tx["commission"]) if tx else ZERO)
            estimated_trade_fees += tr_fee

            if tx is not None:
                tx_change = D(tx["change"])
                tx_fee = D(tx["commission"])
                actual_tx_cashflow += tx_change
                actual_tx_fees += tx_fee
                effective_fee = tx_fee
                effective_cash = tx_change
            else:
                trades_missing_tx_log.append(tid)
                effective_fee = tr_fee
                signed = gross_notional if direction == "sell" else -gross_notional
                effective_cash = signed - tr_fee

            canonical_trades.append(
                {
                    "trade_id": tid,
                    "purpose": purpose,
                    "instrument_name": inst,
                    "direction": direction,
                    "qty_btc": dec_str(qty),
                    "price_btc": dec_str(price),
                    "fee_btc": dec_str(effective_fee),
                    "cash_delta_btc": dec_str(effective_cash),
                    "tx_confirmed": tx is not None,
                    "timestamp_ms": int(tr.get("timestamp_ms") or (tx or {}).get("timestamp_ms") or 0),
                }
            )

            if purpose == "EP":
                ep_qty += qty
                ep_gross_cost += gross_notional
                ep_fee += effective_fee
            elif purpose == "ES":
                es_qty += qty
                es_gross_credit += gross_notional
                es_fee += effective_fee
            elif purpose == "TP":
                close_short_qty += qty
                close_short_gross += gross_notional
                close_short_fee += effective_fee
                tp_gross += gross_notional
                tp_fee += effective_fee
            elif purpose == "RX":
                close_short_qty += qty
                close_short_gross += gross_notional
                close_short_fee += effective_fee
                rx_gross += gross_notional
                rx_fee += effective_fee
            elif purpose == "LX":
                lx_qty += qty
                lx_gross += gross_notional
                lx_fee += effective_fee

        stage1_rows: list[dict[str, Any]] = []
        stage2_rows: list[dict[str, Any]] = []
        other_adj_rows: list[dict[str, Any]] = []
        settlement_cashflow = ZERO
        settlement_fees = ZERO
        adjustment_cashflow = ZERO
        settlement_price_usd: Decimal | None = None

        for tx in tx_all:
            tx_type = str(tx.get("type") or "").lower()
            if tx_type == "trade":
                continue
            chg = D(tx["change"])
            comm = D(tx["commission"])
            actual_tx_cashflow += chg
            actual_tx_fees += comm
            if tx.get("settlement_price") is not None:
                settlement_price_usd = D(tx["settlement_price"])
            inst = str(tx.get("instrument_name") or "")
            if tx_type in {"expiry", "exercise", "assignment"}:
                stage1_rows.append(tx)
                settlement_cashflow += chg
                settlement_fees += comm
            elif tx_type == "delivery" and (
                matches_episode_future(inst, ep.short_instrument)
                or inst in {ep.short_instrument, ep.long_instrument}
            ):
                stage2_rows.append(tx)
                settlement_cashflow += chg
                settlement_fees += comm
            else:
                other_adj_rows.append(tx)
                adjustment_cashflow += chg
                settlement_fees += comm

        matched_qty = min(ep_qty, es_qty)
        unmatched_prot_qty = max(ZERO, ep_qty - es_qty)
        if ep_qty > ZERO and matched_qty > ZERO:
            matched_prot_ex_fee = ceil_1e8(ep_gross_cost * matched_qty / ep_qty)
            matched_ep_fee = ceil_1e8(ep_fee * matched_qty / ep_qty)
        else:
            matched_prot_ex_fee = ZERO
            matched_ep_fee = ZERO
        if es_qty > ZERO and matched_qty > ZERO:
            matched_short_gross = (es_gross_credit * matched_qty / es_qty).quantize(SAT)
            matched_es_fee = ceil_1e8(es_fee * matched_qty / es_qty)
        else:
            matched_short_gross = ZERO
            matched_es_fee = ZERO
        matched_entry_fees = matched_ep_fee + matched_es_fee
        matched_net_credit = matched_short_gross - matched_prot_ex_fee - matched_entry_fees

        unmatched_prot_cost = (ep_gross_cost + ep_fee) - (matched_prot_ex_fee + matched_ep_fee)
        lx_net_total = lx_gross - lx_fee
        if unmatched_prot_qty > ZERO and lx_qty > ZERO:
            unmatched_sold_qty = min(lx_qty, unmatched_prot_qty)
            unmatched_prot_net_recovery = (lx_net_total * unmatched_sold_qty / lx_qty).quantize(SAT)
        else:
            unmatched_prot_net_recovery = ZERO

        open_short_qty = max(ZERO, es_qty - close_short_qty)
        open_prot_qty = max(ZERO, ep_qty - lx_qty)
        _, _, expiry_settlement_complete = compute_episode_expiry_settlement(
            short_instrument=ep.short_instrument,
            long_instrument=ep.long_instrument,
            es_qty=es_qty,
            close_short_qty=close_short_qty,
            ep_qty=ep_qty,
            lx_qty=lx_qty,
            stage1_rows=stage1_rows,
            stage2_rows=stage2_rows,
            positions=facts.positions if facts is not None else None,
            expiry_ms=ep.expiry_ms,
            server_time_ms=facts.server_time_ms if facts is not None else None,
        )
        has_settled_expiry = expiry_settlement_complete

        realized_trade_closed_pnl = ZERO
        realized_closed_pnl = ZERO
        open_entry_credit_btc = ZERO
        matched_lx_net = lx_net_total - unmatched_prot_net_recovery
        if has_settled_expiry:
            open_short_qty = ZERO
            open_prot_qty = ZERO
            realized_closed_pnl = (
                (es_gross_credit - es_fee)
                - (matched_prot_ex_fee + matched_ep_fee)
                - (close_short_gross + close_short_fee)
                + matched_lx_net
                + settlement_cashflow
                + adjustment_cashflow
            )
            episode_total_realized_pnl = (
                (es_gross_credit - es_fee)
                - (ep_gross_cost + ep_fee)
                - (close_short_gross + close_short_fee)
                + lx_net_total
                + settlement_cashflow
                + adjustment_cashflow
            )
        else:
            if open_short_qty == ZERO and es_qty > ZERO:
                realized_trade_closed_pnl = (
                    (es_gross_credit - es_fee)
                    - (matched_prot_ex_fee + matched_ep_fee)
                    - (close_short_gross + close_short_fee)
                    + matched_lx_net
                    + adjustment_cashflow
                )
                episode_total_realized_pnl = realized_trade_closed_pnl - unmatched_prot_cost + unmatched_prot_net_recovery
            elif es_qty == ZERO and ep_qty > ZERO:
                realized_closed_pnl = ZERO
                realized_trade_closed_pnl = ZERO
                disposed_ratio = min(ONE, lx_qty / ep_qty)
                episode_total_realized_pnl = (
                    lx_net_total - ((ep_gross_cost + ep_fee) * disposed_ratio) + adjustment_cashflow
                ).quantize(SAT)
            else:
                if es_qty > ZERO and close_short_qty > ZERO:
                    closed_ratio = close_short_qty / es_qty
                    open_ratio = ONE - closed_ratio
                    realized_trade_closed_pnl = (
                        (es_gross_credit - es_fee) * closed_ratio
                        - (matched_prot_ex_fee + matched_ep_fee) * closed_ratio
                        - (close_short_gross + close_short_fee)
                    ).quantize(SAT)
                    open_entry_credit_btc = (
                        (es_gross_credit - es_fee) * open_ratio
                        - (matched_prot_ex_fee + matched_ep_fee) * open_ratio
                    ).quantize(SAT)
                else:
                    open_entry_credit_btc = matched_net_credit
                if unmatched_prot_qty > ZERO and lx_qty > ZERO:
                    disposed_unmatched = min(ONE, lx_qty / unmatched_prot_qty)
                    unmatched_realized = (lx_net_total - (unmatched_prot_cost * disposed_unmatched)).quantize(SAT)
                else:
                    unmatched_realized = ZERO
                episode_total_realized_pnl = (realized_trade_closed_pnl + unmatched_realized + adjustment_cashflow).quantize(SAT)

        unrealized_mark_pnl: Decimal | None = None
        unrealized_exec_pnl: Decimal | None = None
        if not has_settled_expiry and (open_short_qty > ZERO or open_prot_qty > ZERO):
            if facts is not None:
                sb = facts.short_book
                lb = facts.long_book
                if sb and lb and sb.mark_price is not None and lb.mark_price is not None:
                    unrealized_mark_pnl = (open_prot_qty * lb.mark_price - open_short_qty * sb.mark_price).quantize(SAT)
                if open_short_qty == ZERO:
                    if lb and lb.status in {BookStatus.OK, BookStatus.EMPTY_SIDE} and facts.long_spec is not None:
                        bid_val = (lb.bid_price or ZERO) * open_prot_qty
                        est_lx_fee = option_trade_fee_btc(lb.bid_price, open_prot_qty, facts.long_spec) if lb.bid_price else ZERO
                        unrealized_exec_pnl = (bid_val - est_lx_fee).quantize(SAT)
                elif (
                    sb
                    and sb.ask_price is not None
                    and lb
                    and lb.status in {BookStatus.OK, BookStatus.EMPTY_SIDE}
                    and facts.short_spec is not None
                    and facts.long_spec is not None
                ):
                    buy_cost = sb.ask_price * open_short_qty + option_trade_fee_btc(sb.ask_price, open_short_qty, facts.short_spec)
                    sell_val = (lb.bid_price or ZERO) * open_prot_qty
                    sell_fee = option_trade_fee_btc(lb.bid_price, open_prot_qty, facts.long_spec) if lb.bid_price else ZERO
                    unrealized_exec_pnl = (sell_val - sell_fee - buy_cost).quantize(SAT)

        settlement_crosscheck = None
        if has_settled_expiry and settlement_price_usd is not None and settlement_price_usd > ZERO:
            rem_short_at_exp = max(ZERO, es_qty - close_short_qty)
            rem_long_at_exp = max(ZERO, ep_qty - lx_qty)
            theo_short = inverse_option_expiry_payoff_btc(ep.side, ep.short_strike_usd, settlement_price_usd, -rem_short_at_exp)
            theo_long = inverse_option_expiry_payoff_btc(ep.side, ep.long_strike_usd, settlement_price_usd, rem_long_at_exp)
            theo_gross = theo_short + theo_long
            theo_deliv_fee = ZERO
            if facts is not None and facts.short_spec is not None and facts.long_spec is not None:
                if rem_short_at_exp > ZERO and theo_short < ZERO:
                    theo_deliv_fee += delivery_fee_btc(abs(theo_short) / rem_short_at_exp, rem_short_at_exp, facts.short_spec)
                if rem_long_at_exp > ZERO and theo_long > ZERO:
                    theo_deliv_fee += delivery_fee_btc(theo_long / rem_long_at_exp, rem_long_at_exp, facts.long_spec)
            theo_net = theo_gross - theo_deliv_fee
            settlement_crosscheck = {
                "settlement_price_usd": dec_str(settlement_price_usd),
                "theoretical_net_settlement_btc": dec_str(theo_net),
                "actual_settlement_change_btc": dec_str(settlement_cashflow),
                "diff_btc": dec_str(settlement_cashflow - theo_net),
                "stage1_count": len(stage1_rows),
                "stage2_delivery_count": len(stage2_rows),
            }

        commands_list = list(commands.values())
        open_short_cmds = [c for c in commands_list if c.purpose in {"ES", "TP", "RX"} and c.is_open_or_unknown]
        open_prot_cmds = [c for c in commands_list if c.purpose in {"EP", "LX"} and c.is_open_or_unknown]
        venue_short_flat = (
            all(p.size == ZERO for p in facts.positions if p.instrument_name == ep.short_instrument)
            if facts is not None
            else (open_short_qty == ZERO)
        )
        venue_prot_flat = (
            all(p.size == ZERO for p in facts.positions if p.instrument_name == ep.long_instrument)
            and all(p.size == ZERO for p in facts.positions if matches_episode_future(p.instrument_name, ep.short_instrument))
            if facts is not None
            else (open_prot_qty == ZERO)
        )
        short_cleared = venue_short_flat and not open_short_cmds
        inv_cleared = short_cleared and venue_prot_flat and not open_prot_cmds
        all_closed_by_trades_pre_expiry = (ep_qty > ZERO or es_qty > ZERO) and (
            max(ZERO, es_qty - close_short_qty) == ZERO and max(ZERO, ep_qty - lx_qty) == ZERO
        )
        zero_fills_aborted = (
            ep.entry_closed
            and ep_qty == ZERO
            and es_qty == ZERO
            and all(not c.is_open_or_unknown for c in commands_list)
        )
        cash_settled = (
            inv_cleared
            and not trades_missing_tx_log
            and (facts is None or facts.tx_logs_complete)
            and (zero_fills_aborted or all_closed_by_trades_pre_expiry or expiry_settlement_complete)
        )

        fee_variance = (actual_tx_fees - estimated_trade_fees).quantize(SAT) if not trades_missing_tx_log else None

        # Non-gating commercial observation metrics (Task Doc 3.3: missing ref -> None/"unavailable", never zero-filled!)
        actual_unit_net_credit: Decimal | None = (
            (matched_net_credit / matched_qty).quantize(SAT) if matched_qty > ZERO else None
        )
        credit_retention_ratio: Decimal | None = None
        entry_slippage_per_btc: Decimal | None = None
        entry_slippage_vs_decision_btc: Decimal | None = None
        if ep.c_decision_ref_btc is not None and actual_unit_net_credit is not None:
            if ep.c_decision_ref_btc > ZERO:
                credit_retention_ratio = (actual_unit_net_credit / ep.c_decision_ref_btc).quantize(Decimal("0.0001"))
            entry_slippage_per_btc = (ep.c_decision_ref_btc - actual_unit_net_credit).quantize(SAT)
            entry_slippage_vs_decision_btc = (ep.c_decision_ref_btc * matched_qty - matched_net_credit).quantize(SAT)

        observations = {
            "c_decision_ref_btc": dec_str(ep.c_decision_ref_btc),
            "c_decision_ref_status": "available" if ep.c_decision_ref_btc is not None else "unavailable",
            "actual_net_credit_per_btc": dec_str(actual_unit_net_credit),
            "credit_retention_ratio": dec_str(credit_retention_ratio),
            "entry_slippage_per_btc": dec_str(entry_slippage_per_btc),
            "entry_slippage_vs_decision_btc": dec_str(entry_slippage_vs_decision_btc),
            "estimated_abort_loss_at_ep_btc": dec_str(ep.estimated_abort_loss_at_ep_btc),
        }

        return {
            "schema": "astra_exec_ledger@1.2.0",
            "episode_id": episode_id,
            "hand_id": ep.hand_id,
            "phase": ep.phase.value,
            "terminal_outcome": ep.terminal_outcome,
            "short_liability_cleared": short_cleared,
            "inventory_cleared": inv_cleared,
            "cashflow_settled": cash_settled,
            "entry": {
                "ep_filled_btc": dec_str(ep_qty),
                "es_filled_btc": dec_str(es_qty),
                "matched_qty_btc": dec_str(matched_qty),
                "unmatched_prot_qty_btc": dec_str(unmatched_prot_qty),
                "matched_short_proceeds_btc": dec_str(matched_short_gross),
                "matched_prot_cost_ex_fee_btc": dec_str(matched_prot_ex_fee),
                "matched_entry_fees_btc": dec_str(matched_entry_fees),
                "matched_net_credit_btc": dec_str(matched_net_credit),
                "unmatched_prot_cost_btc": dec_str(unmatched_prot_cost),
            },
            "accounting": {
                "cashflow_btc": dec_str(actual_tx_cashflow),
                "open_entry_credit_btc": dec_str(open_entry_credit_btc),
                "realized_closed_pnl_btc": dec_str(realized_closed_pnl if has_settled_expiry else realized_trade_closed_pnl),
                "unmatched_prot_cost_btc": dec_str(unmatched_prot_cost),
                "unmatched_prot_recovery_btc": dec_str(unmatched_prot_net_recovery),
                "episode_total_realized_pnl_btc": dec_str(episode_total_realized_pnl),
                "unrealized_mark_pnl_btc": dec_str(unrealized_mark_pnl),
                "unrealized_executable_pnl_btc": dec_str(unrealized_exec_pnl),
                "settlement_pnl_btc": dec_str(settlement_cashflow),
                "tp_spend_ex_fee_btc": dec_str(tp_gross),
                "tp_fees_btc": dec_str(tp_fee),
                "rx_spend_ex_fee_btc": dec_str(rx_gross),
                "rx_fees_btc": dec_str(rx_fee),
                "lx_proceeds_ex_fee_btc": dec_str(lx_gross),
                "lx_fees_btc": dec_str(lx_fee),
                "actual_fees_btc": dec_str(actual_tx_fees),
                "estimated_fees_btc": dec_str(estimated_trade_fees),
                "fee_variance_btc": dec_str(fee_variance),
                "trades_missing_tx_log": trades_missing_tx_log,
            },
            "observations": observations,
            "credit_retention_ratio": observations["credit_retention_ratio"],
            "entry_slippage_vs_decision_btc": observations["entry_slippage_vs_decision_btc"],
            "two_stage_settlement": {
                "stage1_rows": stage1_rows,
                "stage2_delivery_rows": stage2_rows,
                "other_adjustment_rows": other_adj_rows,
                "crosscheck": settlement_crosscheck,
            },
            "trades": canonical_trades,
            "shadow_counterfactual": dict(shadow_counterfactual) if shadow_counterfactual is not None else None,
        }
