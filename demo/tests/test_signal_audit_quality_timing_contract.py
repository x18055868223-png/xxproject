import importlib.util
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]
SIGNAL_FILE = (
    ROOT / "demo" / "\u6700\u65b0\u4ea4\u4ed8\u7269" /
    "neutral_regulation_demo_fmz.py"
)
DEPLOY_FRONTEND = ROOT / "deploy" / "signal_audit" / "frontend"
DIST_FRONTEND = ROOT / "dist" / "signal-audit-deploy" / "frontend"


def load_signal_module():
    spec = importlib.util.spec_from_file_location("nrd_signal", SIGNAL_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(
            "{}: expected {!r}, got {!r}".format(message, expected, actual)
        )


def read(path):
    return path.read_text(encoding="utf-8")


def main():
    mod = load_signal_module()
    config = dict(mod.CONFIG)
    card = mod.build_sample_review_card(config)
    cross = card["factor_cross_section"]

    cross["neutral_repair"]["age_ms"] = 15_000
    cross["tmvf"]["age_ms"] = 45_000
    cross["micro_flow"]["age_ms"] = 90_000
    cross["macro_pressure"]["data_age_ms"] = 180_000
    cross["gamma_regime"]["age_ms"] = 240_000
    cross["skew"]["age_ms"] = 300_000
    cross["gex_info"] = {
        "availability": "ready",
        "data_status": "OK",
        "market_state": "negative_gamma",
        "age_ms": 600_000,
        "fetched_at": "2026-06-19T14:47:21+00:00",
        "source_ref": "GEX_MONITOR_API",
    }

    record = mod.build_audit_record(card, config)
    sources = record["quality"]["sources"]

    assert_equal(sources["price"]["age_ms"], 0, "price age should be explicit")
    for name, expected_age in (
            ("neutral_repair", 15_000),
            ("tmvf", 45_000),
            ("micro_flow", 90_000),
            ("macro_pressure", 180_000),
            ("gamma_regime", 240_000),
            ("skew", 300_000),
            ("gex_info", 600_000)):
        assert_equal(sources[name]["age_ms"], expected_age,
                     name + " quality age")
        assert_true(sources[name]["observed_at"],
                    name + " quality observed_at should be displayable")
        assert_true(sources[name]["source_ref"],
                    name + " source_ref should stay available")

    assert_equal(sources["gex_info"]["observed_at"],
                 "2026-06-19T14:47:21+00:00",
                 "gex observed_at should prefer fetched_at")

    for root in (DEPLOY_FRONTEND, DIST_FRONTEND):
        app = read(root / "app.js")
        assert_true("function qualitySourceView(doc, key, source)" in app,
                    "app.js should derive quality timing from nearby factor data")
        assert_true("function qualityReasonText(source)" in app,
                    "app.js should not mark OK sources as missing reason")
        assert_true("factor_cross_section.macro_pressure" in app,
                    "app.js should know macro quality fallback path")
        assert_true("qualitySourceView(doc, key, source)" in app,
                    "renderQuality should use the derived source view")

    print("signal_audit_quality_timing_contract: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_audit_quality_timing_contract: FAIL - " + str(exc))
        sys.exit(1)
