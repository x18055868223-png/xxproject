"""Read-only view of the inquirer's question sheets for the workbench (docs/opus/14).

The research archive tick writes one sheet per side at the scheduled hours (UTC 02, 14) and
once for a new contract (`inquiry/<day>/<HH>_<side>_<case_id>.json`), listed in the archive
state under `contracts[<short>|<long>].inquiry` (and, for three days after expiry, under
`closed_recent`); the case file it read sits next to it as `cases/<same name>.json.gz`.

- `sheets(key)`: the sheets of one contract, oldest first, with slot, trigger and status;
- `view(short, long, file, summary)`: one sheet in full plus the case entries its refs cite and
  the experience reading of its questions (`lens`, docs/opus/19; the summary has none)
  (the audit), or only its first questions and counts (the Decision Path audit step);
- `latest_ref(key)`: the sheet shown by default, recorded with Decision Path marks.

The state file is written by the archive service while the workbench reads as root, so every
listed path must have the sheet file pattern and resolve inside the archive. Sheets never
change once written and are cached by path; of a case file only the cited entries are kept.
No model is called and nothing is written.
"""
from __future__ import annotations

import gzip
import json
import re
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

try:   # the reading is optional: a deploy without it must not take the workbench down
    if __package__:
        from . import astra_inquiry_lens_v1 as lens_core
    else:
        import astra_inquiry_lens_v1 as lens_core
except ImportError:
    lens_core = None

SCHEMA = "astra_inquiry_view@0.1"
INSTRUMENT = re.compile(r"BTC-\d{1,2}[A-Z]{3}\d{2}-\d{3,7}-[PC]")
SHEET_FILE = re.compile(r"inquiry/(\d{4}-\d{2}-\d{2})/(\d{2})_(put|call)_(cf_[0-9a-f]{24})\.json")
ENTRY_MAX_BYTES = 4096
CACHE_MAX = 16
# Files are written by the archive service and read here as root: never read more than this.
STATE_MAX_BYTES = 8 * 1024 * 1024
SHEET_MAX_BYTES = 1024 * 1024
CASE_MAX_BYTES = 8 * 1024 * 1024        # after decompression
SUMMARY_QUESTIONS = 3
SCHEDULED_HOURS_UTC = (2, 14)
MEANING_CN = "提问者只提问题、不下结论；未经前向验证，只作参考，不进入任何门槛或决策卡"


class InquiryViewError(Exception):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


def contract_key(short: Any, long: Any) -> str:
    """The archive key of a contract, from validated instrument names."""
    if not (isinstance(short, str) and INSTRUMENT.fullmatch(short) and isinstance(long, str) and INSTRUMENT.fullmatch(long)):
        raise InquiryViewError("bad_contract")
    return f"{short}|{long}"


def _refs_of(sheet: Mapping[str, Any]) -> list[str]:
    """Every case ref the sheet cites, in first-seen order."""
    out: list[str] = []

    def add(refs: Any) -> None:
        for ref in refs if isinstance(refs, list) else []:
            if isinstance(ref, str) and ref not in out:
                out.append(ref)

    def objects(value: Any) -> list[Mapping[str, Any]]:
        return [v for v in value if isinstance(v, Mapping)] if isinstance(value, list) else []

    for q in objects(sheet.get("questions")):
        for p in objects(q.get("premises")):
            add(p.get("refs"))
    for a in objects(sheet.get("anomalies")):
        add(a.get("refs"))
    brief = sheet.get("fact_brief") if isinstance(sheet.get("fact_brief"), Mapping) else {}
    for layer in objects(brief.get("layers")):
        for f in objects(layer.get("facts")):
            add(f.get("refs"))
    for c in objects(brief.get("cross_layer")):
        add(c.get("refs"))
    return out


def _entry(case: Mapping[str, Any], ref: str) -> dict[str, Any] | None:
    at = (case.get("refs") or {}).get(ref)
    if not isinstance(at, Mapping):
        return None
    try:
        e = case["layers"][at["layer"]][at["index"]]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(e, Mapping):
        return None
    out = {"ref": ref, "layer": at.get("layer"), "label_cn": e.get("label_cn"), "quality": e.get("quality"),
           "quality_reason": e.get("quality_reason"), "asof_ms": e.get("asof_ms"), "source": e.get("source"),
           "relations": e.get("relations") or {}}
    text = json.dumps(e.get("values"), ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(text) > ENTRY_MAX_BYTES:
        out.update(values=None, values_preview=text[:ENTRY_MAX_BYTES].decode("utf-8", "ignore"), truncated=True)
    else:
        out.update(values=e.get("values"), truncated=False)
    return out


def _lens(sheet: Mapping[str, Any], case: Mapping[str, Any] | None, version: Any) -> dict[str, Any]:
    """The experience reading (docs/opus/19); a failure says so rather than breaking the sheet."""
    if lens_core is None:
        return {"version": None, "error": "lens_unavailable"}
    try:
        return lens_core.sheet_lens(sheet, case, prompt_version=version)
    except Exception as exc:
        return {"version": getattr(lens_core, "VERSION", None), "error": type(exc).__name__}


def summary_of(sheet: Mapping[str, Any]) -> dict[str, Any]:
    """The first questions and the counts, for the Decision Path audit step."""
    questions = [q for q in sheet.get("questions") or [] if isinstance(q, Mapping)]
    counts = {p: sum(1 for q in questions if q.get("priority") == p) for p in ("high", "medium", "low")}
    return {"questions": [{k: q.get(k) for k in ("id", "priority", "type", "bears_on", "answer_source", "question_cn")}
                          for q in questions[:SUMMARY_QUESTIONS]],
            "question_count": len(questions), "by_priority": counts,
            "anomaly_count": len([a for a in sheet.get("anomalies") or [] if isinstance(a, Mapping)])}


class InquiryReader:
    def __init__(self, root: str | Path, *, scheduled_hours: tuple[int, ...] = SCHEDULED_HOURS_UTC):
        self.root = Path(root).resolve()
        self.scheduled_hours = tuple(scheduled_hours)
        self._lock = threading.Lock()
        self._state: tuple[float, dict[str, Any]] | None = None
        self._sheets: OrderedDict[str, Any] = OrderedDict()
        self._entries: OrderedDict[str, Any] = OrderedDict()

    # ---------------------------------------------------------------- files

    def _path(self, rel: str) -> Path:
        path = (self.root / rel).resolve()
        if self.root not in path.parents:
            raise InquiryViewError("inquiry_path_outside_archive", 404)
        return path

    @staticmethod
    def _bounded(path: Path, limit: int) -> bytes:
        if path.stat().st_size > limit:
            raise InquiryViewError("inquiry_file_too_large", 503)
        return path.read_bytes()

    @staticmethod
    def _object(raw: bytes) -> dict[str, Any]:
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError("not a JSON object")
        return body

    def _lists(self) -> dict[str, list[str]]:
        """Each contract's sheet files (open, then recently closed), re-read only when the
        state file changed (the tick replaces it atomically)."""
        path = self.root / "_state" / "state.json"
        st = path.stat()
        stamp = (st.st_mtime_ns, st.st_size, st.st_ino)   # a rewrite within one mtime tick still changes size or inode
        with self._lock:
            if self._state and self._state[0] == stamp:
                return self._state[1]
        state = self._object(self._bounded(path, STATE_MAX_BYTES))
        lists: dict[str, list[str]] = {}
        for section in ("closed_recent", "contracts"):      # an open record wins
            records = state.get(section)
            for key, record in (records.items() if isinstance(records, dict) else []):
                if isinstance(record, Mapping) and isinstance(record.get("inquiry"), list):
                    lists[key] = [f for f in record["inquiry"] if isinstance(f, str) and SHEET_FILE.fullmatch(f)]
        with self._lock:
            self._state = (stamp, lists)
        return lists

    def _cached(self, cache: OrderedDict, rel: str, load) -> Any:
        with self._lock:
            if rel in cache:
                cache.move_to_end(rel)
                return cache[rel]
        value = load()
        with self._lock:
            cache[rel] = value
            while len(cache) > CACHE_MAX:
                cache.popitem(last=False)
        return value

    def _bounded_path(self, rel: str) -> Path:
        path = self._path(rel)
        if path.stat().st_size > CASE_MAX_BYTES:
            raise InquiryViewError("inquiry_file_too_large", 503)
        return path

    def _sheet(self, rel: str) -> Mapping[str, Any]:
        return self._cached(self._sheets, rel, lambda: self._object(self._bounded(self._path(rel), SHEET_MAX_BYTES)))

    def _case_entries(self, rel: str, sheet: Mapping[str, Any], version: Any = None) -> dict[str, Any]:
        """The case file's identity and the entries the sheet cites (not the whole case)."""
        case_rel = "cases/" + rel[len("inquiry/"):] + ".gz"

        def load() -> dict[str, Any]:
            with gzip.open(self._bounded_path(case_rel), "rb") as handle:   # bounded: a gzip bomb stops here
                raw = handle.read(CASE_MAX_BYTES + 1)
            if len(raw) > CASE_MAX_BYTES:
                raise InquiryViewError("inquiry_file_too_large", 503)
            case = self._object(raw)
            entries = {}
            for ref in _refs_of(sheet):
                entry = _entry(case, ref)
                if entry is not None:
                    entries[ref] = entry
            return {"case": {"file": case_rel, "case_id": case.get("case_id"), "asof_ms": case.get("asof_ms"),
                             "contract_cn": case.get("contract_cn"), "builder": (case.get("build") or {}).get("builder")},
                    "entries": entries, "lens": _lens(sheet, case, version)}

        return self._cached(self._entries, rel, load)

    # ---------------------------------------------------------------- reads

    def _meta(self, rel: str) -> dict[str, Any]:
        m = SHEET_FILE.fullmatch(rel)
        day, hour = m.group(1), int(m.group(2))
        slot = int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000) + hour * 3_600_000
        out: dict[str, Any] = {"file": rel, "slot_ms": slot, "side": m.group(3), "case_id": m.group(4)}
        try:
            body = self._sheet(rel)
        except (OSError, ValueError, InquiryViewError):
            return {**out, "status": "unreadable", "has_sheet": False,
                    "trigger": "scheduled" if hour in self.scheduled_hours else "new_contract", "trigger_inferred": True}
        trigger = body.get("trigger")
        sheet = body.get("sheet")
        return {**out, "asof_ms": body.get("asof_ms"), "status": body.get("status"),
                "has_sheet": isinstance(sheet, Mapping),
                "questions": len(sheet.get("questions") or []) if isinstance(sheet, Mapping) else 0,
                "trigger": trigger if trigger in {"scheduled", "new_contract"}
                else ("scheduled" if hour in self.scheduled_hours else "new_contract"),
                "trigger_inferred": trigger not in {"scheduled", "new_contract"}}

    def sheets(self, key: str) -> list[dict[str, Any]]:
        return sorted((self._meta(f) for f in self._lists().get(key) or []), key=lambda s: (s["slot_ms"], s["file"]))

    def latest_ref(self, key: str) -> dict[str, Any] | None:
        """The sheet shown by default for a contract: the newest one that has a sheet."""
        ok = [s for s in self.sheets(key) if s["has_sheet"]]
        if not ok:
            return None
        return {k: ok[-1].get(k) for k in ("file", "case_id", "asof_ms", "slot_ms", "trigger", "status")}

    def view(self, short: Any, long: Any, file: Any = None, *, summary: bool = False) -> dict[str, Any]:
        key = contract_key(short, long)
        listed = self.sheets(key)
        body: dict[str, Any] = {"schema": SCHEMA, "contract_key": key, "sheets": listed, "selected": None,
                                "meaning_cn": MEANING_CN, "llm_call_count": 0}
        if file is not None and not any(s["file"] == file for s in listed):
            raise InquiryViewError("inquiry_file_not_listed", 404)
        pick = file if file is not None else (self.latest_ref(key) or {}).get("file")
        if not pick:
            return body
        meta = next(s for s in listed if s["file"] == pick)
        try:
            result = self._sheet(pick)
        except (OSError, ValueError, InquiryViewError) as exc:
            body["selected"] = {**meta, "read_error": type(exc).__name__}
            return body
        sheet = result.get("sheet") if isinstance(result.get("sheet"), Mapping) else None
        selected = {**meta, "prompt_version": result.get("prompt_version"), "requested_model": result.get("requested_model"),
                    "error": result.get("error")}
        if summary:
            body["selected"] = {**selected, "summary": summary_of(sheet) if sheet else None}
            return body
        selected.update(issues=result.get("issues") or [], sheet=sheet, entries={}, case=None,
                        usage=[a.get("usage") for a in result.get("attempts") or [] if isinstance(a, Mapping)])
        if sheet:
            try:
                selected.update(self._case_entries(pick, sheet, result.get("prompt_version")))
            except (OSError, ValueError, InquiryViewError) as exc:
                selected["case_error"] = type(exc).__name__
            if "lens" not in selected:      # without the case: categories only, no numbers
                selected["lens"] = _lens(sheet, None, result.get("prompt_version"))
        body["selected"] = selected
        return body
