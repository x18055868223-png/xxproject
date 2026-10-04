from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "signal_audit"
LOCATION_SNIPPET = DEPLOY / "nginx.signal-audit-location.conf.example"
HTTP_SERVER = DEPLOY / "nginx.signal-audit.conf.example"
REVIEW_STATE_PATH = "/api/workbench/review-state"
REVIEW_POST_PATH = "/api/workbench/review"
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


def assert_http_post_review_blocked(config):
    body = exact_location_body(config, REVIEW_POST_PATH)
    assert_true("return 403;" in body, "HTTP server example must keep review POST blocked")
    assert_true("proxy_pass" not in body, "HTTP server example must not proxy review POST")


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


def main():
    location_snippet = read(LOCATION_SNIPPET)
    http_server = read(HTTP_SERVER)

    assert_review_state_proxy(location_snippet, LOCATION_SNIPPET.name)
    assert_review_state_proxy(http_server, HTTP_SERVER.name)
    assert_http_post_review_blocked(http_server)
    assert_https_location_post_guard(location_snippet)

    print("signal_audit_nginx_workbench_proxy: PASS")


if __name__ == "__main__":
    main()
