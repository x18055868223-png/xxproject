"""Decision Path verification sessions and experiment records (2026-10-14).

A session verifies one frozen policy: the contract identity and the start reading are
fixed when it starts; "refresh to now" adds a second reading of the same contract.
Per-step marks (toward underwrite / unchanged / toward decline), first-open times and
the conclusion are kept, and one record row per conclusion, expiry and settlement is
appended to records.jsonl for the offline report (tools/astra_decision_path_report.py).

Conclusions: lock (the lock is created by the workbench and its id kept here), decline,
watch (the session stays open). A session still open at expiry is closed as
"unconcluded". File-based, one JSON file per session; no model, no trading.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Any, Mapping

if __package__:
    from . import astra_decision_path_v1 as dp
else:  # direct CLI execution from tools/
    import astra_decision_path_v1 as dp

SCHEMA = "astra_decision_path_session@1.0.0"
RECORD_SCHEMA = "astra_decision_path_record@1.0.0"
ID_RE = re.compile(r"^dpv_[0-9a-f]{16}$")
MAX_OPEN = 24
CLAIM_MS = 120_000   # a conclusion in progress (a lock capture can take a while)
OPEN, CLOSED = "open", "closed"


class DecisionPathError(Exception):  # not ValueError: the HTTP layer maps ValueError to bad_json
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code = code
        self.status = status


class DecisionPathStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.sessions_dir = self.root / "sessions"
        self.records_path = self.root / "records.jsonl"
        self._lock = threading.RLock()
        self._index: dict[str, dict[str, Any]] | None = None
        self._claims: dict[str, int] = {}

    # ------------------------------------------------------------ files
    def _path(self, vid: str) -> Path:
        if not isinstance(vid, str) or not ID_RE.match(vid):
            raise DecisionPathError("invalid_verification_id", 400)
        return self.sessions_dir / f"{vid}.json"

    def _write(self, session: Mapping[str, Any]) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        path = self._path(session["verification_id"])
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(session, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
        self._load_index()[session["verification_id"]] = self._summary(session)

    def _append_record(self, row: Mapping[str, Any]) -> bool:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with self.records_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"schema": RECORD_SCHEMA, **row}, ensure_ascii=False, separators=(",", ":")) + "\n")
            return True
        except OSError:
            return False

    def _record_or_keep(self, session: dict[str, Any], row: Mapping[str, Any] | None = None) -> None:
        """Append record rows; a row that cannot be written stays in the session and is retried
        on later passes, so session state and experiment records cannot drift apart for good."""
        pending = list(session.get("records_pending") or [])
        rows = pending + ([dict(row)] if row is not None else [])
        left = [r for r in rows if not self._append_record(r)]
        if left or pending:
            session["records_pending"] = left
            self._write(session)

    def _load_index(self) -> dict[str, dict[str, Any]]:
        if self._index is None:
            index: dict[str, dict[str, Any]] = {}
            if self.sessions_dir.exists():
                for path in self.sessions_dir.glob("dpv_*.json"):
                    try:
                        session = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        continue
                    if (isinstance(session, dict) and session.get("schema") == SCHEMA
                            and isinstance(session.get("verification_id"), str) and session.get("status") in {OPEN, CLOSED}):
                        index[session["verification_id"]] = self._summary(session)
            self._index = index
        return self._index

    @staticmethod
    def _summary(session: Mapping[str, Any]) -> dict[str, Any]:
        conclusion = session.get("conclusion") or {}
        return {"verification_id": session["verification_id"], "status": session["status"],
                "side": session.get("side"), "candidate_id": session.get("candidate_id"),
                "expiry_ms": (session.get("contract") or {}).get("expiry_ms"),
                "started_at_ms": session.get("started_at_ms"), "path_version": session.get("path_version"),
                "conclusion": conclusion.get("choice"), "episode_id": conclusion.get("episode_id"),
                "concluded_at_ms": conclusion.get("at_ms"), "records_pending": bool(session.get("records_pending")),
                "last_watch_at_ms": session.get("last_watch_at_ms"), "settled": bool(session.get("outcome"))}

    # ------------------------------------------------------------ reads
    def get(self, vid: str) -> dict[str, Any]:
        with self._lock:
            path = self._path(vid)
            try:
                session = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                raise DecisionPathError("verification_not_found", 404) from None
            except (OSError, ValueError):
                raise DecisionPathError("verification_unreadable", 500) from None
            return session

    def summaries(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted((dict(item) for item in self._load_index().values()),
                          key=lambda item: -(item.get("started_at_ms") or 0))

    def open_for(self, candidate_id: str) -> dict[str, Any] | None:
        with self._lock:
            found = [item for item in self._load_index().values()
                     if item["status"] == OPEN and item.get("candidate_id") == candidate_id]
            return max(found, key=lambda item: item.get("started_at_ms") or 0) if found else None

    def verification_for_episode(self) -> dict[str, str]:
        """Episode -> the first verification that concluded into it (a later lock conclusion on an
        already-tracked contract links to the same episode)."""
        with self._lock:
            out: dict[str, tuple[int, str]] = {}
            for item in self._load_index().values():
                eid, at = item.get("episode_id"), item.get("concluded_at_ms") or 0
                if eid and (eid not in out or at < out[eid][0]):
                    out[eid] = (at, item["verification_id"])
            return {eid: vid for eid, (_, vid) in out.items()}

    def claim(self, vid: str, *, now_ms: int) -> None:
        """Mark a conclusion in progress; a second conclusion of the same session is refused."""
        with self._lock:
            self._open_session(vid)
            started = self._claims.get(vid)
            if started is not None and now_ms - started < CLAIM_MS:
                raise DecisionPathError("verification_concluding")
            self._claims[vid] = now_ms

    def release(self, vid: str) -> None:
        with self._lock:
            self._claims.pop(vid, None)

    # ------------------------------------------------------------ writes
    def start(self, *, candidate: Mapping[str, Any], reading: Mapping[str, Any], now_ms: int) -> tuple[dict[str, Any], bool]:
        """A new session, or the open one for the same contract ("continue")."""
        snapshot = candidate.get("snapshot") or {}
        candidate_id = snapshot.get("candidate_id")
        if not candidate_id:
            raise DecisionPathError("snapshot_missing")
        with self._lock:
            existing = self.open_for(candidate_id)
            if existing is not None:
                return self.get(existing["verification_id"]), False
            if sum(1 for item in self._load_index().values() if item["status"] == OPEN) >= MAX_OPEN:
                raise DecisionPathError("too_many_open_verifications")
            contract = dict(snapshot.get("contract") or {})
            economics = snapshot.get("economics") or {}
            session = {
                "schema": SCHEMA, "verification_id": "dpv_" + secrets.token_hex(8), "status": OPEN,
                "path_version": dp.PATH_VERSION, "steps": dp.steps_public(),
                "side": candidate.get("side") or contract.get("side"), "candidate_id": candidate_id,
                "contract": contract, "start_snapshot_id": snapshot.get("snapshot_id"),
                "entry_after_fee_btc": economics.get("net_credit_after_entry_fee_btc"),
                "started_at_ms": now_ms, "readings": {"start": dict(reading), "now": None},
                "changes": [], "opened": {}, "marks": {}, "decisions": [], "conclusion": None,
                "llm_call_count": 0, "execution_allowed": False,
            }
            self._write(session)
            return session, True

    def _open_session(self, vid: str) -> dict[str, Any]:
        session = self.get(vid)
        if session["status"] != OPEN:
            raise DecisionPathError("verification_closed")
        return session

    def set_now(self, vid: str, reading: Mapping[str, Any], *, now_ms: int) -> dict[str, Any]:
        with self._lock:
            session = self._open_session(vid)
            session["readings"]["now"] = dict(reading)
            session["changes"] = dp.compare(session["readings"]["start"], reading)
            session["refreshed_at_ms"] = now_ms
            self._write(session)
            return session

    def step(self, vid: str, step_id: str, *, lean: str | None, now_ms: int) -> dict[str, Any]:
        if step_id not in dp.STEP_IDS or (lean is not None and lean not in dp.MARKS):
            raise DecisionPathError("bad_step_request", 400)
        with self._lock:
            session = self._open_session(vid)
            session["opened"].setdefault(step_id, now_ms)
            if lean is not None:
                session["marks"][step_id] = {"lean": lean, "at_ms": now_ms}
            self._write(session)
            return session

    def conclude(self, vid: str, choice: str, *, now_ms: int, episode_id: str | None = None,
                 lock_created: bool | None = None) -> dict[str, Any]:
        if choice not in dp.CONCLUSIONS:
            raise DecisionPathError("bad_conclusion", 400)
        with self._lock:
            session = self._open_session(vid)
            session["decisions"].append({"choice": choice, "at_ms": now_ms, "episode_id": episode_id})
            if choice == "watch":
                session["last_watch_at_ms"] = now_ms
                self._write(session)
                return session
            session["status"] = CLOSED
            session["conclusion"] = {"choice": choice, "at_ms": now_ms, "episode_id": episode_id,
                                     "lock_created": lock_created}
            self._write(session)
            self._record_or_keep(session, self._record(session, "concluded", now_ms))
            return session

    def expire_due(self, now_ms: int) -> list[str]:
        closed = []
        with self._lock:
            for item in list(self._load_index().values()):
                expiry = item.get("expiry_ms")
                if item["status"] != OPEN or not isinstance(expiry, int) or now_ms < expiry:
                    continue
                try:
                    session = self.get(item["verification_id"])
                    session["status"] = CLOSED
                    session["conclusion"] = {"choice": "unconcluded", "at_ms": now_ms, "episode_id": None,
                                             "after_watch": bool(session.get("last_watch_at_ms"))}
                    self._write(session)
                    self._record_or_keep(session, self._record(session, "expired_open", now_ms))
                    closed.append(session["verification_id"])
                except (DecisionPathError, OSError):
                    continue  # one damaged session must not stop the others from closing
            for item in list(self._load_index().values()):
                if item.get("records_pending"):
                    try:
                        self._record_or_keep(self.get(item["verification_id"]))
                    except (DecisionPathError, OSError):
                        continue
        return closed

    def settle(self, vid: str, outcome: Mapping[str, Any], *, now_ms: int) -> dict[str, Any]:
        with self._lock:
            session = self.get(vid)
            session["outcome"] = {**dict(outcome), "settled_at_ms": now_ms}
            self._write(session)
            self._record_or_keep(session, {"event": "settled", "verification_id": vid, "at_ms": now_ms,
                                           "candidate_id": session.get("candidate_id"), "outcome": session["outcome"]})
            return session

    def unsettled_after(self, now_ms: int, *, delay_ms: int) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._load_index().values()
                    if not item.get("settled") and isinstance(item.get("expiry_ms"), int)
                    and now_ms >= item["expiry_ms"] + delay_ms]

    @staticmethod
    def _record(session: Mapping[str, Any], event: str, now_ms: int) -> dict[str, Any]:
        start = (session.get("readings") or {}).get("start") or {}
        now = (session.get("readings") or {}).get("now") or {}
        verdicts = lambda reading: {k: ((v or {}).get("verdict") or {}).get("code")  # noqa: E731
                                    for k, v in (reading.get("steps") or {}).items()}
        opened = session.get("opened") or {}
        order = sorted(opened, key=opened.get)
        dwell = {step: (opened[order[i + 1]] - opened[step]) for i, step in enumerate(order[:-1])}
        conclusion = session.get("conclusion") or {}
        if order and isinstance(conclusion.get("at_ms"), int):
            dwell[order[-1]] = conclusion["at_ms"] - opened[order[-1]]
        return {
            "event": event, "verification_id": session["verification_id"], "at_ms": now_ms,
            "path_version": session.get("path_version"), "side": session.get("side"),
            "candidate_id": session.get("candidate_id"),
            "contract": {k: (session.get("contract") or {}).get(k) for k in
                         ("side", "short_strike_usd", "long_strike_usd", "expiry_ms", "short_instrument", "long_instrument")},
            "started_at_ms": session.get("started_at_ms"), "entry_after_fee_btc": session.get("entry_after_fee_btc"),
            "verdicts_start": verdicts(start), "verdicts_now": verdicts(now) if now else None,
            "q1_items": [i.get("step") for i in ((start.get("steps") or {}).get("Q1") or {}).get("items") or []],
            "marks": dp.summarize_marks(session.get("marks") or {}),
            "settled_after": dp.first_settled_step(session.get("marks") or {}),
            "opened_order": order, "dwell_ms": dwell,
            "conclusion": conclusion.get("choice"), "episode_id": conclusion.get("episode_id"),
            "decisions": session.get("decisions") or [],
        }

    def records(self) -> list[dict[str, Any]]:
        rows = []
        try:
            with self.records_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and row.get("schema") == RECORD_SCHEMA:
                        rows.append(row)
        except FileNotFoundError:
            pass
        return rows
