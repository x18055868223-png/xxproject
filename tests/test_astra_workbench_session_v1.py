from http.cookies import SimpleCookie

from tools.astra_workbench_session_v1 import COOKIE_NAME, WorkbenchSessions


def _headers(host="127.0.0.1:8919", origin="http://127.0.0.1:8919", **extra):
    headers = {"Host": host}
    if origin is not None:
        headers["Origin"] = origin
    headers.update(extra)
    return headers


def _cookie_value(set_cookie):
    parsed = SimpleCookie()
    parsed.load(set_cookie)
    return parsed[COOKIE_NAME].value


def test_login_exchanges_bearer_for_http_only_loopback_cookie():
    now = [1_000]
    sessions = WorkbenchSessions("super-secret-token", now_ms=lambda: now[0])

    response = sessions.login(
        "POST",
        _headers(),
        "127.0.0.1",
        authorization="Bearer super-secret-token",
    )

    assert response.status_code == 200
    assert response.body == {
        "schema": "astra_workbench_session@1.0.0",
        "authenticated": True,
        "expires_at_ms": 43_201_000,
        "code": "ok",
    }
    assert "super-secret-token" not in str(response.body)
    assert "super-secret-token" not in response.set_cookie
    assert f"{COOKIE_NAME}=" in response.set_cookie
    assert "HttpOnly" in response.set_cookie
    assert "SameSite=Strict" in response.set_cookie
    assert "Path=/api/workbench" in response.set_cookie
    assert "Max-Age=43200" in response.set_cookie
    assert "Secure" not in response.set_cookie

    cookie = _cookie_value(response.set_cookie)
    auth = sessions.authorize(
        "POST",
        {"Host": "127.0.0.1:8919", "Origin": "http://127.0.0.1:8919", "Cookie": f"{COOKIE_NAME}={cookie}"},
        "127.0.0.1",
    )
    assert auth.ok is True
    assert auth.credential_kind == "session"
    assert auth.authorization_for_workbench == "Bearer super-secret-token"
    assert sessions.status({"Host": "127.0.0.1:8919", "Cookie": f"{COOKIE_NAME}={cookie}"}, "127.0.0.1")[
        "authenticated"
    ] is True


def test_public_origin_cookie_is_secure_and_requires_explicit_https_transport():
    sessions = WorkbenchSessions("secret", public_origin="https://audit.example.com", now_ms=lambda: 1_000)
    headers = _headers(host="audit.example.com", origin="https://audit.example.com")

    direct_http = sessions.login("POST", {**headers, "X-Forwarded-Proto": "https"}, "198.51.100.10", "Bearer secret")
    assert direct_http.status_code == 403
    assert direct_http.body["code"] == "insecure_transport"
    assert direct_http.set_cookie is None

    https = sessions.login("POST", headers, "198.51.100.10", "Bearer secret", request_scheme="https")
    assert https.status_code == 200
    assert "Secure" in https.set_cookie
    assert "HttpOnly" in https.set_cookie
    assert "SameSite=Strict" in https.set_cookie


def test_refresh_cookie_preserves_absolute_expiry():
    now = [10_000]
    sessions = WorkbenchSessions("secret", ttl_seconds=10, now_ms=lambda: now[0])
    login = sessions.login("POST", _headers(), "127.0.0.1", "Bearer secret")
    cookie = _cookie_value(login.set_cookie)

    now[0] = 14_100
    refresh = sessions.refresh(
        "POST",
        _headers(Cookie=f"{COOKIE_NAME}={cookie}"),
        "127.0.0.1",
    )

    assert refresh.status_code == 200
    assert refresh.body["expires_at_ms"] == login.body["expires_at_ms"]
    assert _cookie_value(refresh.set_cookie) == cookie
    assert "Max-Age=6" in refresh.set_cookie


def test_expiry_and_logout_remove_session_without_leaking_token():
    now = [1_000]
    sessions = WorkbenchSessions("secret", ttl_seconds=1, now_ms=lambda: now[0])
    login = sessions.login("POST", _headers(), "127.0.0.1", "Bearer secret")
    cookie = _cookie_value(login.set_cookie)
    cookie_headers = _headers(Cookie=f"{COOKIE_NAME}={cookie}")

    now[0] = 2_001
    assert sessions.authorize("GET", cookie_headers, "127.0.0.1").ok is False
    assert sessions.status(cookie_headers, "127.0.0.1")["authenticated"] is False

    now[0] = 3_000
    second = sessions.login("POST", _headers(), "127.0.0.1", "Bearer secret")
    second_cookie = _cookie_value(second.set_cookie)
    logout = sessions.logout("DELETE", _headers(Cookie=f"{COOKIE_NAME}={second_cookie}"), "127.0.0.1")

    assert logout.status_code == 200
    assert logout.body["authenticated"] is False
    assert "Max-Age=0" in logout.set_cookie
    assert sessions.authorize("GET", _headers(Cookie=f"{COOKIE_NAME}={second_cookie}"), "127.0.0.1").ok is False


def test_cross_site_origin_is_rejected_for_cookie_login():
    sessions = WorkbenchSessions("secret", now_ms=lambda: 1_000)

    response = sessions.login(
        "POST",
        _headers(origin="http://evil.example"),
        "127.0.0.1",
        "Bearer secret",
    )

    assert response.status_code == 403
    assert response.body["code"] == "origin_not_allowed"
    assert response.set_cookie is None
    null_origin = sessions.login("POST", _headers(origin="null"), "127.0.0.1", "Bearer secret")
    assert null_origin.status_code == 403
    assert null_origin.body["code"] == "origin_not_allowed"


def test_wrong_bearer_is_rejected_even_on_valid_loopback_transport():
    sessions = WorkbenchSessions("secret", now_ms=lambda: 1_000)

    response = sessions.login("POST", _headers(), "127.0.0.1", "Bearer wrong")
    auth = sessions.authorize("POST", _headers(Authorization="Bearer wrong"), "127.0.0.1")

    assert response.status_code == 401
    assert response.body["code"] == "unauthorized"
    assert auth.ok is False
    assert auth.status_code == 401


def test_bearer_without_origin_is_allowed_only_on_legal_transport():
    sessions = WorkbenchSessions("secret", public_origin="https://audit.example.com", now_ms=lambda: 1_000)

    local = sessions.authorize(
        "POST",
        _headers(origin=None, Authorization="Bearer secret"),
        "127.0.0.1",
    )
    remote_http = sessions.authorize(
        "POST",
        _headers(host="audit.example.com", origin=None, Authorization="Bearer secret"),
        "198.51.100.10",
    )
    remote_https = sessions.authorize(
        "POST",
        _headers(host="audit.example.com", origin=None, Authorization="Bearer secret"),
        "198.51.100.10",
        request_scheme="https",
    )

    assert local.ok is True
    assert remote_http.ok is False
    assert remote_http.reason == "insecure_transport"
    assert remote_https.ok is True


def test_session_cap_evicts_oldest_session_without_disk_state():
    now = [1_000]
    sessions = WorkbenchSessions("secret", max_sessions=2, now_ms=lambda: now[0])

    first = sessions.login("POST", _headers(), "127.0.0.1", "Bearer secret")
    now[0] += 1
    second = sessions.login("POST", _headers(), "127.0.0.1", "Bearer secret")
    now[0] += 1
    third = sessions.login("POST", _headers(), "127.0.0.1", "Bearer secret")

    first_cookie = _cookie_value(first.set_cookie)
    second_cookie = _cookie_value(second.set_cookie)
    third_cookie = _cookie_value(third.set_cookie)

    assert sessions.authorize("GET", _headers(Cookie=f"{COOKIE_NAME}={first_cookie}"), "127.0.0.1").ok is False
    assert sessions.authorize("GET", _headers(Cookie=f"{COOKIE_NAME}={second_cookie}"), "127.0.0.1").ok is True
    assert sessions.authorize("GET", _headers(Cookie=f"{COOKIE_NAME}={third_cookie}"), "127.0.0.1").ok is True


def test_same_origin_fetch_metadata_can_refresh_cookie_without_origin_header():
    sessions = WorkbenchSessions("secret", now_ms=lambda: 1_000)
    login = sessions.login("POST", _headers(), "127.0.0.1", "Bearer secret")
    cookie = _cookie_value(login.set_cookie)
    headers = {
        "Host": "127.0.0.1:8919",
        "Cookie": f"{COOKIE_NAME}={cookie}",
        "Sec-Fetch-Site": "same-origin",
        "Referer": "http://127.0.0.1:8919/#locked",
    }

    response = sessions.refresh("POST", headers, "127.0.0.1")

    assert response.status_code == 200
    assert response.body["authenticated"] is True


def _login_cookie(sessions, token):
    response = sessions.login("POST", _headers(), "127.0.0.1", authorization=f"Bearer {token}")
    assert response.status_code == 200
    return f"{COOKIE_NAME}={_cookie_value(response.set_cookie)}"


def test_sessions_survive_a_restart_and_store_no_secret(tmp_path):
    store = tmp_path / "sessions.json"
    first = WorkbenchSessions("server-token", now_ms=lambda: 1_000, store_path=store)
    cookie = _login_cookie(first, "server-token")
    text = store.read_text(encoding="utf-8")
    assert "server-token" not in text and cookie.split("=", 1)[1] not in text
    restarted = WorkbenchSessions("server-token", now_ms=lambda: 2_000, store_path=store)
    assert restarted.status({"Host": "127.0.0.1:8919", "Cookie": cookie}, "127.0.0.1")["authenticated"]
    # logout is persisted too
    restarted.logout("DELETE", _headers(Cookie=cookie), "127.0.0.1")
    again = WorkbenchSessions("server-token", now_ms=lambda: 3_000, store_path=store)
    assert not again.status({"Host": "127.0.0.1:8919", "Cookie": cookie}, "127.0.0.1")["authenticated"]


def test_a_changed_server_token_or_expiry_drops_stored_sessions(tmp_path):
    store = tmp_path / "sessions.json"
    first = WorkbenchSessions("old-token", ttl_seconds=10, now_ms=lambda: 1_000, store_path=store)
    cookie = _login_cookie(first, "old-token")
    rotated = WorkbenchSessions("new-token", ttl_seconds=10, now_ms=lambda: 2_000, store_path=store)
    assert not rotated.status({"Host": "127.0.0.1:8919", "Cookie": cookie}, "127.0.0.1")["authenticated"]
    second = WorkbenchSessions("new-token", ttl_seconds=10, now_ms=lambda: 2_000, store_path=store)
    cookie = _login_cookie(second, "new-token")
    expired = WorkbenchSessions("new-token", ttl_seconds=10, now_ms=lambda: 60_000, store_path=store)
    assert not expired.status({"Host": "127.0.0.1:8919", "Cookie": cookie}, "127.0.0.1")["authenticated"]


def test_an_unreadable_store_starts_empty(tmp_path):
    store = tmp_path / "sessions.json"
    store.write_text("not json", encoding="utf-8")
    sessions = WorkbenchSessions("server-token", now_ms=lambda: 1_000, store_path=store)
    assert _login_cookie(sessions, "server-token")
