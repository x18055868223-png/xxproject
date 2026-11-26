"""Cross-process Deribit HTTP 429 cooldown coordination via atomic JSON file.

Enabled only when ``ASTRA_DERIBIT_COOLDOWN_FILE`` is set (or an explicit path is
passed). When disabled or on read/write error, all helpers fail open so single-
process behavior and isolated unit tests remain completely unaffected.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

ENV_KEY = "ASTRA_DERIBIT_COOLDOWN_FILE"
SCHEMA = "astra_deribit_cooldown@1.0.0"
MAX_COOLDOWN_MS = 300_000


def _now_ms(now_ms: int | None) -> int:
    return int(time.time() * 1000) if now_ms is None else int(now_ms)


def resolve_path(path: str | Path | None = None) -> Path | None:
    """Resolve explicit cooldown path or ``ASTRA_DERIBIT_COOLDOWN_FILE`` env var."""
    if path is not None:
        text = str(path).strip()
        return Path(text) if text else None
    text = (os.environ.get(ENV_KEY) or "").strip()
    return Path(text) if text else None


def read_cooldown(
    path: str | Path | None = None, *, now_ms: int | None = None
) -> dict[str, Any] | None:
    """Return active cooldown metadata if unexpired, else ``None``."""
    target = resolve_path(path)
    if target is None or not target.is_file():
        return None
    now = _now_ms(now_ms)
    try:
        raw = target.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    until = data.get("cooldown_until_ms")
    if isinstance(until, bool) or not isinstance(until, int):
        return None
    if until <= now or until > now + MAX_COOLDOWN_MS + 60_000:
        return None
    return {
        "schema": SCHEMA,
        "cooldown_until_ms": until,
        "remaining_ms": until - now,
        "source": str(data.get("source") or ""),
        "reason": str(data.get("reason") or "DERIBIT_HTTP_429"),
        "updated_at_ms": data.get("updated_at_ms"),
    }


def remaining_ms(path: str | Path | None = None, *, now_ms: int | None = None) -> int:
    """Return remaining cooldown milliseconds, or ``0`` when inactive/unset."""
    active = read_cooldown(path, now_ms=now_ms)
    return int(active["remaining_ms"]) if active is not None else 0


def record_cooldown(
    path: str | Path | None = None,
    *,
    cooldown_until_ms: int,
    source: str,
    reason: str = "DERIBIT_HTTP_429",
    now_ms: int | None = None,
) -> dict[str, Any] | None:
    """Atomically record or extend a shared Deribit cooldown deadline."""
    target = resolve_path(path)
    if target is None:
        return None
    now = _now_ms(now_ms)
    capped_until = min(int(cooldown_until_ms), now + MAX_COOLDOWN_MS)
    if capped_until <= now:
        return None
    existing = read_cooldown(target, now_ms=now)
    effective_until = capped_until
    if existing is not None:
        effective_until = min(
            max(int(existing["cooldown_until_ms"]), capped_until),
            now + MAX_COOLDOWN_MS,
        )
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "cooldown_until_ms": effective_until,
        "source": str(source),
        "reason": str(reason),
        "updated_at_ms": now,
    }
    tmp_path: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target.with_name(f".{target.name}.tmp-{os.getpid()}-{time.time_ns()}")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            try:
                os.chmod(tmp_path, 0o664)
            except OSError:
                pass
        os.replace(tmp_path, target)
    except OSError:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
        return None
    return {**payload, "remaining_ms": effective_until - now}
