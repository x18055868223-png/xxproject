from __future__ import annotations

from dataclasses import dataclass
from http.cookies import SimpleCookie
import hashlib
import hmac
import ipaddress
import math
import secrets
import threading
import time
from typing import Callable, Mapping
from urllib.parse import urlparse


COOKIE_NAME = "astra_workbench_session"
COOKIE_PATH = "/api/workbench"
DEFAULT_TTL_SECONDS = 12 * 60 * 60
SCHEMA = "astra_workbench_session@1.0.0"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass(frozen=True)
class AuthResult:
    ok: bool
    status_code: int
    reason: str
    credential_kind: str | None = None
    expires_at_ms: int | None = None
    authorization_for_workbench: str | None = None


@dataclass(frozen=True)
class SessionResponse:
    status_code: int
    body: dict[str, object]
    set_cookie: str | None = None

    @property
    def headers(self) -> dict[str, str]:
        return {"Set-Cookie": self.set_cookie} if self.set_cookie else {}


@dataclass
class _SessionRecord:
    expires_at_ms: int
    sequence: int


def _now_ms() -> int:
    return int(time.time() * 1000)


def _header(headers: Mapping[str, str] | object | None, name: str) -> str | None:
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if callable(getter):
        value = getter(name)
        if value is not None:
            return str(value)
    lower = name.lower()
    if isinstance(headers, Mapping):
        for key, value in headers.items():
            if str(key).lower() == lower:
                return str(value)
    return None


def _is_loopback_host(value: str | None) -> bool:
    if not value:
        return False
    host = _split_host(value)
    if host is None:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _split_host(value: str) -> str | None:
    raw = value.strip()
    if not raw:
        return None
    if raw.startswith("["):
        end = raw.find("]")
        return raw[1:end] if end > 0 else None
    if raw.count(":") > 1:
        return raw
    return raw.rsplit(":", 1)[0].strip() or None


def _canonical_host_port(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.startswith("["):
        end = raw.find("]")
        if end < 0:
            return None
        host = raw[1:end].lower()
        port = raw[end + 1:]
        return f"[{host}]{port}"
    if raw.count(":") > 1:
        return f"[{raw.lower()}]"
    if ":" in raw:
        host, port = raw.rsplit(":", 1)
        return f"{host.lower()}:{port}"
    return raw.lower()


def _origin_tuple(origin: str | None) -> tuple[str, str] | None:
    if not origin:
        return None
    parsed = urlparse(origin.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        return None
    return parsed.scheme, _canonical_host_port(parsed.netloc) or ""


def _same_origin_url(url: str | None, origin: tuple[str, str]) -> bool:
    if not url:
        return False
    parsed = urlparse(url.strip())
    return parsed.scheme == origin[0] and (_canonical_host_port(parsed.netloc) or "") == origin[1]


class WorkbenchSessions:
    def __init__(
        self,
        secret: str | None,
        public_origin: str | None = None,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_sessions: int = 32,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds_must_be_positive")
        if max_sessions <= 0:
            raise ValueError("max_sessions_must_be_positive")
        normalized_origin = _origin_tuple(public_origin) if public_origin else None
        if public_origin and normalized_origin is None:
            raise ValueError("public_origin_invalid")
        if normalized_origin and normalized_origin[0] != "https" and not _is_loopback_host(normalized_origin[1]):
            raise ValueError("public_origin_must_be_https")
        self.secret = secret
        self.public_origin = public_origin.strip().rstrip("/") if public_origin else None
        self._public_origin_tuple = normalized_origin
        self.ttl_seconds = int(ttl_seconds)
        self.max_sessions = int(max_sessions)
        self.now_ms = now_ms or _now_ms
        self._hash_key = secrets.token_bytes(32)
        self._sessions: dict[str, _SessionRecord] = {}
        self._lock = threading.RLock()
        self._sequence = 0

    def status(
        self,
        headers: Mapping[str, str] | object | None,
        peer_host: str | None,
        *,
        request_scheme: str | None = None,
    ) -> dict[str, object]:
        result = self.authorize("GET", headers, peer_host, request_scheme=request_scheme)
        return {
            "schema": SCHEMA,
            "authenticated": result.ok,
            "expires_at_ms": result.expires_at_ms if result.ok else None,
        }

    def login(
        self,
        method: str,
        headers: Mapping[str, str] | object | None,
        peer_host: str | None,
        authorization: str | None = None,
        *,
        request_scheme: str | None = None,
    ) -> SessionResponse:
        if method.upper() != "POST":
            return self._response(405, "method_not_allowed")
        context = self._cookie_write_context(headers, peer_host, request_scheme=request_scheme)
        if context is not None:
            return self._response(403, context)
        auth = self._bearer_authorize(authorization or _header(headers, "Authorization"))
        if not auth.ok:
            return self._response(auth.status_code, auth.reason)

        token = secrets.token_urlsafe(32)
        expires_at_ms = self.now_ms() + self.ttl_seconds * 1000
        with self._lock:
            self._prune_locked()
            self._sequence += 1
            self._sessions[self._digest(token)] = _SessionRecord(expires_at_ms, self._sequence)
            self._enforce_cap_locked()
        return self._response(
            200,
            "ok",
            authenticated=True,
            expires_at_ms=expires_at_ms,
            set_cookie=self._session_cookie(token, expires_at_ms, headers, request_scheme=request_scheme),
        )

    def refresh(
        self,
        method: str,
        headers: Mapping[str, str] | object | None,
        peer_host: str | None,
        *,
        request_scheme: str | None = None,
    ) -> SessionResponse:
        if method.upper() != "POST":
            return self._response(405, "method_not_allowed")
        context = self._cookie_write_context(headers, peer_host, request_scheme=request_scheme)
        if context is not None:
            return self._response(403, context)
        token = self._cookie_token(headers)
        record = self._session_record(token)
        if record is None:
            return self._response(401, "unauthorized", set_cookie=self._clear_cookie(headers, request_scheme=request_scheme))
        return self._response(
            200,
            "ok",
            authenticated=True,
            expires_at_ms=record.expires_at_ms,
            set_cookie=self._session_cookie(token or "", record.expires_at_ms, headers, request_scheme=request_scheme),
        )

    def logout(
        self,
        method: str,
        headers: Mapping[str, str] | object | None,
        peer_host: str | None,
        *,
        request_scheme: str | None = None,
    ) -> SessionResponse:
        if method.upper() != "DELETE":
            return self._response(405, "method_not_allowed")
        context = self._cookie_write_context(headers, peer_host, request_scheme=request_scheme)
        if context is not None:
            return self._response(403, context)
        token = self._cookie_token(headers)
        if token:
            with self._lock:
                self._sessions.pop(self._digest(token), None)
        return self._response(
            200,
            "ok",
            authenticated=False,
            expires_at_ms=None,
            set_cookie=self._clear_cookie(headers, request_scheme=request_scheme),
        )

    def evaluate(
        self,
        method: str,
        headers: Mapping[str, str] | object | None,
        peer_host: str | None,
        authorization: str | None = None,
        *,
        request_scheme: str | None = None,
    ) -> AuthResult:
        return self.authorize(method, headers, peer_host, authorization, request_scheme=request_scheme)

    def authorize(
        self,
        method: str,
        headers: Mapping[str, str] | object | None,
        peer_host: str | None,
        authorization: str | None = None,
        *,
        request_scheme: str | None = None,
    ) -> AuthResult:
        method_upper = method.upper()
        transport_reason = self._transport_reason(headers, peer_host, request_scheme=request_scheme)
        if transport_reason is not None:
            return AuthResult(False, 403, transport_reason)

        explicit_authorization = authorization or _header(headers, "Authorization")
        if not explicit_authorization and not self._cookie_token(headers):
            return AuthResult(False, 401, "unauthorized")
        if method_upper in UNSAFE_METHODS:
            origin_reason = self._origin_reason(headers, require_cookie_context=not bool(explicit_authorization))
            if origin_reason is not None:
                return AuthResult(False, 403, origin_reason)

        if explicit_authorization:
            bearer = self._bearer_authorize(explicit_authorization)
            if not bearer.ok:
                return bearer
            return bearer

        token = self._cookie_token(headers)
        record = self._session_record(token)
        if record is None:
            return AuthResult(False, 401, "unauthorized")
        return AuthResult(
            True,
            200,
            "ok",
            credential_kind="session",
            expires_at_ms=record.expires_at_ms,
            authorization_for_workbench=self._workbench_authorization(),
        )

    def _bearer_authorize(self, authorization: str | None) -> AuthResult:
        prefix = "Bearer "
        if not self.secret:
            return AuthResult(False, 503, "server_token_unconfigured")
        if not isinstance(authorization, str) or not authorization.startswith(prefix):
            return AuthResult(False, 401, "unauthorized")
        supplied = authorization[len(prefix):]
        if not hmac.compare_digest(supplied, self.secret):
            return AuthResult(False, 401, "unauthorized")
        return AuthResult(
            True,
            200,
            "ok",
            credential_kind="bearer",
            authorization_for_workbench=self._workbench_authorization(),
        )

    def _workbench_authorization(self) -> str | None:
        return f"Bearer {self.secret}" if self.secret else None

    def _cookie_write_context(
        self,
        headers: Mapping[str, str] | object | None,
        peer_host: str | None,
        *,
        request_scheme: str | None,
    ) -> str | None:
        transport_reason = self._transport_reason(headers, peer_host, request_scheme=request_scheme)
        if transport_reason is not None:
            return transport_reason
        return self._origin_reason(headers, require_cookie_context=True)

    def _transport_reason(
        self,
        headers: Mapping[str, str] | object | None,
        peer_host: str | None,
        *,
        request_scheme: str | None,
    ) -> str | None:
        host = _header(headers, "Host")
        host_port = _canonical_host_port(host)
        scheme = (request_scheme or "").lower()
        if _is_loopback_host(peer_host):
            if _is_loopback_host(host) or self._host_matches_public_origin(host_port):
                return None
            return "host_not_allowed"
        if scheme == "https" and self._host_matches_public_origin(host_port):
            return None
        return "insecure_transport"

    def _origin_reason(
        self,
        headers: Mapping[str, str] | object | None,
        *,
        require_cookie_context: bool,
    ) -> str | None:
        raw_origin = _header(headers, "Origin")
        origin = _origin_tuple(raw_origin)
        if raw_origin is not None and origin is None:
            return "origin_not_allowed"
        if origin is not None:
            return None if self._origin_allowed(origin, headers) else "origin_not_allowed"
        if not require_cookie_context:
            return None
        fetch_site = (_header(headers, "Sec-Fetch-Site") or "").lower()
        referer_origin = self._expected_origin_from_referer(headers)
        if fetch_site == "same-origin" and referer_origin is not None:
            return None
        return "origin_required"

    def _origin_allowed(self, origin: tuple[str, str], headers: Mapping[str, str] | object | None) -> bool:
        host_port = _canonical_host_port(_header(headers, "Host"))
        if self._public_origin_tuple and origin == self._public_origin_tuple and self._host_matches_public_origin(host_port):
            return True
        return _is_loopback_host(origin[1]) and host_port == origin[1]

    def _expected_origin_from_referer(self, headers: Mapping[str, str] | object | None) -> tuple[str, str] | None:
        referer = _header(headers, "Referer")
        parsed = urlparse(referer or "")
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None
        origin = (parsed.scheme, _canonical_host_port(parsed.netloc) or "")
        return origin if self._origin_allowed(origin, headers) and _same_origin_url(referer, origin) else None

    def _host_matches_public_origin(self, host_port: str | None) -> bool:
        return bool(self._public_origin_tuple and host_port == self._public_origin_tuple[1])

    def _cookie_token(self, headers: Mapping[str, str] | object | None) -> str | None:
        raw = _header(headers, "Cookie")
        if not raw:
            return None
        cookie = SimpleCookie()
        try:
            cookie.load(raw)
        except Exception:
            return None
        morsel = cookie.get(COOKIE_NAME)
        return morsel.value if morsel is not None else None

    def _session_record(self, token: str | None) -> _SessionRecord | None:
        if not token:
            return None
        digest = self._digest(token)
        with self._lock:
            record = self._sessions.get(digest)
            if record is None:
                return None
            if record.expires_at_ms <= self.now_ms():
                self._sessions.pop(digest, None)
                return None
            return _SessionRecord(record.expires_at_ms, record.sequence)

    def _digest(self, token: str) -> str:
        return hmac.new(self._hash_key, token.encode("utf-8"), hashlib.sha256).hexdigest()

    def _prune_locked(self) -> None:
        now = self.now_ms()
        expired = [digest for digest, record in self._sessions.items() if record.expires_at_ms <= now]
        for digest in expired:
            self._sessions.pop(digest, None)

    def _enforce_cap_locked(self) -> None:
        while len(self._sessions) > self.max_sessions:
            oldest = min(self._sessions.items(), key=lambda item: (item[1].expires_at_ms, item[1].sequence))[0]
            self._sessions.pop(oldest, None)

    def _session_cookie(
        self,
        token: str,
        expires_at_ms: int,
        headers: Mapping[str, str] | object | None,
        *,
        request_scheme: str | None,
    ) -> str:
        max_age = max(0, math.ceil((expires_at_ms - self.now_ms()) / 1000))
        parts = [
            f"{COOKIE_NAME}={token}",
            f"Max-Age={max_age}",
            f"Path={COOKIE_PATH}",
            "HttpOnly",
            "SameSite=Strict",
        ]
        if self._secure_cookie(headers, request_scheme=request_scheme):
            parts.append("Secure")
        return "; ".join(parts)

    def _clear_cookie(
        self,
        headers: Mapping[str, str] | object | None,
        *,
        request_scheme: str | None,
    ) -> str:
        parts = [
            f"{COOKIE_NAME}=",
            "Max-Age=0",
            f"Path={COOKIE_PATH}",
            "HttpOnly",
            "SameSite=Strict",
        ]
        if self._secure_cookie(headers, request_scheme=request_scheme):
            parts.append("Secure")
        return "; ".join(parts)

    def _secure_cookie(self, headers: Mapping[str, str] | object | None, *, request_scheme: str | None) -> bool:
        if (request_scheme or "").lower() == "https":
            return True
        origin = _origin_tuple(_header(headers, "Origin"))
        if self._public_origin_tuple and origin == self._public_origin_tuple:
            return True
        return False

    def _response(
        self,
        status_code: int,
        code: str,
        *,
        authenticated: bool = False,
        expires_at_ms: int | None = None,
        set_cookie: str | None = None,
    ) -> SessionResponse:
        return SessionResponse(
            status_code,
            {
                "schema": SCHEMA,
                "authenticated": authenticated,
                "expires_at_ms": expires_at_ms,
                "code": code,
            },
            set_cookie,
        )
