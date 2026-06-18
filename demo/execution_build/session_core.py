# -*- coding: utf-8 -*-
"""ExecutionSession and ApprovalIntent contract harness for demo v0.2."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class PrecommitChecks:
    signal_fresh: bool
    vrp_rechecked: bool
    spm_rechecked: bool
    quotes_rechecked: bool
    ledger_rechecked: bool
    spread_ok: bool
    maker_only: bool

    def all_passed(self) -> bool:
        return all(asdict(self).values())


def _stable_hash(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass
class ExecutionSession:
    session_id: str
    signal_package_id: str
    created_ts: int
    state: str
    locked_plan: Optional[Dict[str, Any]] = None
    approval_intent: Optional[Dict[str, Any]] = None

    @classmethod
    def open(cls, session_id: str, signal_package_id: str, now_ts: int) -> "ExecutionSession":
        return cls(
            session_id=session_id,
            signal_package_id=signal_package_id,
            created_ts=now_ts,
            state="SIGNAL_OBSERVED",
        )

    def lock_plan(self, plan: Dict[str, Any], now_ts: int, ttl_sec: int) -> Dict[str, Any]:
        plan_copy = dict(plan)
        plan_hash = _stable_hash(plan_copy)
        self.locked_plan = {
            "schema_name": "ExecutionPlanPackage",
            "session_id": self.session_id,
            "signal_package_id": self.signal_package_id,
            "plan": plan_copy,
            "plan_hash": plan_hash,
            "plan_created_ts": now_ts,
            "ttl_sec": ttl_sec,
            "expires_ts": now_ts + ttl_sec,
        }
        self.state = "PLAN_LOCKED"
        return self.locked_plan

    def approve_locked_plan(
        self,
        now_ts: int,
        checks: PrecommitChecks,
        allow_real_order: bool,
        operator_note: str = "",
    ) -> Dict[str, Any]:
        if not self.locked_plan:
            raise ValueError("Cannot approve without locked plan")
        approval_id = _stable_hash({
            "session_id": self.session_id,
            "signal_package_id": self.signal_package_id,
            "plan_hash": self.locked_plan["plan_hash"],
            "approval_created_ts": now_ts,
        })[:16]
        self.approval_intent = {
            "schema_name": "ApprovalIntentPackage",
            "schema_version": "nrd.integration.approval_intent.v0.1",
            "approval_id": approval_id,
            "session_id": self.session_id,
            "signal_package_id": self.signal_package_id,
            "plan_hash": self.locked_plan["plan_hash"],
            "plan_created_ts": self.locked_plan["plan_created_ts"],
            "approval_created_ts": now_ts,
            "ttl_sec": self.locked_plan["ttl_sec"],
            "approval_state": "ARMED",
            "allow_real_order": bool(allow_real_order),
            "operator_note": operator_note,
            "precommit_checks": asdict(checks),
        }
        self.state = "ARMED_PREVIEW"
        return self.approval_intent

    def can_commit_order(self, now_ts: int) -> bool:
        if self.state != "ARMED_PREVIEW" or not self.locked_plan or not self.approval_intent:
            return False
        if now_ts >= self.locked_plan["expires_ts"]:
            self.approval_intent["approval_state"] = "EXPIRED"
            return False
        checks = PrecommitChecks(**self.approval_intent["precommit_checks"])
        return bool(self.approval_intent["allow_real_order"] and checks.all_passed())

    def package(self) -> Dict[str, Any]:
        return {
            "schema_name": "ExecutionSessionPackage",
            "schema_version": "nrd.integration.execution_session.v0.1",
            "session_id": self.session_id,
            "signal_package_id": self.signal_package_id,
            "state": self.state,
            "locked_plan": self.locked_plan or {},
            "approval_intent": self.approval_intent or {},
        }
