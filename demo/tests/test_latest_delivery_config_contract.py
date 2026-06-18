import importlib.util
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]
SIGNAL_FILE = ROOT / "demo" / "最新交付物" / "neutral_regulation_demo_fmz.py"

EXPECTED_GEX_INFO_BASE_URL = "http://13.231.16.198:8000"
EXPECTED_GEX_INFO_TOKEN = "<REDACTED_GEX_INFO_TOKEN>"


def load_signal_module():
    spec = importlib.util.spec_from_file_location("nrd_signal", SIGNAL_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


class RecordingHttp:
    def __init__(self):
        self.url = None
        self.headers = None
        self.timeout_sec = None

    def get_json(self, url, headers=None, timeout_sec=None):
        self.url = url
        self.headers = headers or {}
        self.timeout_sec = timeout_sec
        return {"quality": "ERROR", "error": "intentional contract stop"}


def main():
    mod = load_signal_module()
    config = dict(mod.CONFIG)
    user_keys = tuple(getattr(mod, "USER_CONFIG_KEYS", ()))
    user_docs = getattr(mod, "USER_CONFIG_DOC_CN", {})

    assert_true(user_keys, "USER_CONFIG_KEYS should declare the user-facing config surface")
    for key in user_keys:
        assert_true(key in config, "USER_CONFIG_KEYS contains unknown key " + key)
        doc = user_docs.get(key)
        assert_true(isinstance(doc, str) and doc.strip(),
                    "missing Chinese doc for user-facing key " + key)
        assert_true(any(ord(ch) > 127 for ch in doc),
                    "doc should contain Chinese semantics for " + key)

    for key in (
        "asset",
        "spot_symbol",
        "futures_symbol",
        "deribit_currency",
        "max_main_loops",
        "loop_sleep_ms",
        "gex_info_enabled",
        "gex_info_base_url",
        "gex_info_token",
        "signal_review_enabled",
        "signal_review_push_enabled",
        "signal_review_push_test",
        "audit_static_base_url",
        "logs_dir",
    ):
        assert_true(key in user_keys, "missing user-facing config key " + key)

    for internal_key in (
        "edb_base_weights",
        "ggr_negative_veto_strength",
        "nr_mdie_event_threshold",
        "tmvf_24h_ema_fast",
    ):
        assert_true(internal_key not in user_keys,
                    "internal model default leaked into user-facing keys " + internal_key)

    assert_true(config["gex_info_enabled"] is True, "GEX info should be enabled by default")
    assert_true(config["gex_info_base_url"] == EXPECTED_GEX_INFO_BASE_URL,
                "GEX info base URL should point at the strategy server")
    assert_true(config["gex_info_token"] == EXPECTED_GEX_INFO_TOKEN,
                "GEX info token should match the deployment token")
    assert_true(
        mod._gex_info_endpoint(config["gex_info_base_url"])
        == EXPECTED_GEX_INFO_BASE_URL + "/v1/info",
        "host-only GEX info base should normalize to /v1/info",
    )
    assert_true(
        mod._gex_info_endpoint(EXPECTED_GEX_INFO_BASE_URL + "/v1/info")
        == EXPECTED_GEX_INFO_BASE_URL + "/v1/info",
        "full GEX info endpoint should remain stable",
    )
    config["gex_info_cache_file"] = ""
    http = RecordingHttp()
    adapter = mod.GexInfoAdapter(http, config)
    adapter.refresh()
    assert_true(http.url == EXPECTED_GEX_INFO_BASE_URL + "/v1/info",
                "GEX adapter should call normalized /v1/info endpoint")
    assert_true(
        http.headers.get("Authorization") == "Bearer " + EXPECTED_GEX_INFO_TOKEN,
        "GEX adapter should send deployment bearer token",
    )
    assert_true(http.timeout_sec == config["http_timeout_sec"],
                "GEX adapter should use configured HTTP timeout")

    print("latest_delivery_config_contract: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("latest_delivery_config_contract: FAIL - " + str(exc))
        sys.exit(1)
