from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "signal_audit"
LOCATION_SNIPPET = DEPLOY / "nginx.signal-audit-location.conf.example"
HTTP_SERVER = DEPLOY / "nginx.signal-audit.conf.example"
REVIEW_STATE_PATH = "/api/workbench/review-state"
REVIEW_POST_PATH = "/api/workbench/review"
SESSION_PATH = "/api/workbench/session"
LOCK_PATH = "/api/workbench/locked-candidates"
WORKBENCH_BACKEND = "http://127.0.0.1:8895"


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def read(path):
    assert_true(path.exists(), "missing nginx example: " + path.name)
    return path.read_text(encoding="utf-8")


def exact_location_body(config, route):
    pattern = r"location\s*=\s*" + re.escape(route) + r"\s*\{(?P<body>.*?)\n\s*\}"
    match = re.search(pattern, config, flags=re.S)
    assert_true(match is not None, "missing exact nginx location for " + route)
    return match.group("body")


def prefix_location_body(config, route):
    pattern = r"location\s+\^~\s+" + re.escape(route) + r"\s*\{(?P<body>.*?)\n\s*\}"
    match = re.search(pattern, config, flags=re.S)
    assert_true(match is not None, "missing prefix nginx location for " + route)
    return match.group("body")


def assert_forwarded_headers(body, source_name):
    assert_true("proxy_set_header Host $host;" in body,
                source_name + " should forward Host to the backend")
    assert_true("X-Forwarded-Proto" not in body and "X-Forwarded-Host" not in body,
                source_name + " should not imply forwarded headers are trusted for auth")


def assert_review_state_proxy(config, source_name):
    body = exact_location_body(config, REVIEW_STATE_PATH)
    assert_true(
        f"proxy_pass {WORKBENCH_BACKEND}{REVIEW_STATE_PATH};" in body,
        source_name + " should proxy the read-only review-state endpoint to the workbench service",
    )
    assert_true(
        "limit_except GET { deny all; }" in body,
        source_name + " should only forward GET/HEAD review-state requests",
    )
    assert_true(
        'add_header Cache-Control "no-store";' in body,
        source_name + " should mark review-state responses no-store",
    )
    assert_forwarded_headers(body, source_name)


def assert_read_proxy(config, source_name, route, *, prefix=False):
    body = prefix_location_body(config, route) if prefix else exact_location_body(config, route)
    assert_true(
        "limit_except GET { deny all; }" in body,
        source_name + " should keep " + route + " read-only at the proxy",
    )
    assert_true(
        "proxy_pass " + WORKBENCH_BACKEND in body,
        source_name + " should proxy " + route + " to the workbench service",
    )
    assert_true(
        'add_header Cache-Control "no-store";' in body,
        source_name + " should mark " + route + " no-store",
    )
    assert_forwarded_headers(body, source_name)


def assert_public_http_blocks_private_workbench_api(config):
    exact = exact_location_body(config, "/api/workbench")
    prefix = prefix_location_body(config, "/api/workbench/")
    for body, label in ((exact, "exact"), (prefix, "prefix")):
        assert_true("return 403;" in body, "HTTP server must block " + label + " workbench API")
        assert_true("proxy_pass" not in body, "HTTP server must not proxy " + label + " workbench API")


def assert_https_location_post_guard(config):
    body = exact_location_body(config, REVIEW_POST_PATH)
    assert_true(
        "if ($scheme != https) { return 403; }" in body,
        "location snippet should retain the HTTPS guard before review POST proxying",
    )
    assert_true(
        f"proxy_pass {WORKBENCH_BACKEND}{REVIEW_POST_PATH};" in body,
        "location snippet should keep the existing HTTPS-only review POST proxy",
    )
    assert_forwarded_headers(body, "location snippet")


def assert_https_write_guard(config, route):
    body = exact_location_body(config, route)
    assert_true(
        "if ($scheme != https) { return 403; }" in body,
        "location snippet should retain HTTPS guard for " + route,
    )
    assert_true(
        "client_max_body_size 8k;" in body,
        "location snippet should keep small request body limit for " + route,
    )
    assert_true(
        f"proxy_pass {WORKBENCH_BACKEND}{route};" in body,
        "location snippet should proxy " + route + " to the backend",
    )
    assert_forwarded_headers(body, "location snippet")


def main():
    location_snippet = read(LOCATION_SNIPPET)
    http_server = read(HTTP_SERVER)

    assert_public_http_blocks_private_workbench_api(http_server)

    assert_read_proxy(location_snippet, LOCATION_SNIPPET.name, "/api/workbench")
    assert_read_proxy(location_snippet, LOCATION_SNIPPET.name, "/api/workbench/candidate-evidence/", prefix=True)
    assert_read_proxy(location_snippet, LOCATION_SNIPPET.name, "/api/workbench/wpem-evidence/", prefix=True)

    assert_review_state_proxy(location_snippet, LOCATION_SNIPPET.name)
    assert_https_write_guard(location_snippet, SESSION_PATH)
    assert_https_write_guard(location_snippet, LOCK_PATH)
    assert_true(
        "if ($scheme != https) { return 403; }" in prefix_location_body(
            location_snippet, LOCK_PATH + "/"
        ),
        "location snippet should guard locked candidate detail/stop prefix by HTTPS",
    )
    assert_https_location_post_guard(location_snippet)
    assert_true(
        "if ($scheme != https) { return 403; }" in prefix_location_body(
            location_snippet, "/api/workbench/wpem-proof/"
        ),
        "location snippet should guard full proof prefix by HTTPS",
    )

    print("signal_audit_nginx_workbench_proxy: PASS")


if __name__ == "__main__":
    main()
