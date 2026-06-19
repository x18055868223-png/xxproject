import importlib.util
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]
SIGNAL_FILE = ROOT / "demo" / "最新交付物" / "neutral_regulation_demo_fmz.py"


def load_signal_module():
    spec = importlib.util.spec_from_file_location("nrd_signal", SIGNAL_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def sample_info_payload():
    return {
        "asset": "BTC",
        "fetched_at": "2026-06-18T15:15:23.408169+00:00",
        "stale": False,
        "availability": "ready",
        "gex_board": {
            "total_net_gex": -112000000,
            "dvol": 40.3,
            "market_state": "negative_gamma",
        },
        "gamma_exposure": {
            "n2": 62000,
            "n1": 60000,
            "flip_point": 64642.901,
            "volatility_trigger": 60000,
            "spot_price": 63541.5,
            "magnet_price": 64000,
            "p1": 67000,
            "p2": 80000,
        },
        "volatility": {
            "iv_rv_ratio": 0.84,
            "pcr": 1.32,
            "term_structure": [
                {"expiry": "19 JUN 26", "atm_iv": 40.6, "skew_25d": -9.8},
            ],
        },
        "flow": {
            "call_premium": 7000000,
            "put_premium": 12100000,
            "call_put_bias": "37% Call",
            "put_call_ratio": 1.72,
            "abnormal_signal": "Put-led selling is setting the tone.",
        },
        "missing_fields": [],
        "rank": {
            "window": {
                "mode": "rolling_30d_or_available",
                "lookback_days": 30,
                "sample_count": 42,
                "history_retained_count": 42,
                "window_days": 14.5,
            },
            "metrics": {
                "gex_board.total_net_gex": {
                    "value": -112000000,
                    "rank_pct": 64,
                    "percentile": 0.64,
                    "sample_count": 42,
                    "quality": "warming_up",
                    "abs_rank_pct": 88,
                    "abs_percentile": 0.88,
                },
                "gex_board.dvol": {
                    "value": 40.3,
                    "rank_pct": 52,
                    "percentile": 0.52,
                    "sample_count": 42,
                    "quality": "warming_up",
                },
                "volatility.iv_rv_ratio": {
                    "value": 0.84,
                    "rank_pct": 41,
                    "percentile": 0.41,
                    "sample_count": 42,
                    "quality": "warming_up",
                },
                "volatility.pcr": {
                    "value": 1.32,
                    "rank_pct": 73,
                    "percentile": 0.73,
                    "sample_count": 42,
                    "quality": "warming_up",
                },
                "flow.call_share_pct": {
                    "value": 37,
                    "rank_pct": 31,
                    "percentile": 0.31,
                    "sample_count": 42,
                    "quality": "warming_up",
                },
                "flow.put_call_ratio": {
                    "value": 1.72,
                    "rank_pct": 79,
                    "percentile": 0.79,
                    "sample_count": 42,
                    "quality": "warming_up",
                },
            },
        },
    }


class FakeChart:
    def __init__(self, config):
        self.config = config
        self.add_calls = []
        self.reset_called = False

    def reset(self):
        self.reset_called = True

    def add(self, index, point):
        self.add_calls.append((index, point))


def test_rank_is_preserved_and_rendered(mod):
    snapshot = mod.parse_info_payload(sample_info_payload(), dict(mod.CONFIG))
    rank = snapshot.get("rank")
    assert_true(isinstance(rank, dict), "rank object should be preserved")
    metrics = rank.get("metrics") or {}
    assert_true(metrics["gex_board.total_net_gex"]["rank_pct"] == 64,
                "netGEX rank_pct should be preserved")
    assert_true(metrics["gex_board.total_net_gex"]["abs_rank_pct"] == 88,
                "absolute netGEX rank_pct should be preserved")

    table = mod._gex_info_table(snapshot)
    rank_rows = [row for row in table["rows"] if row and row[0] == "rank"]
    assert_true(len(rank_rows) == 1, "GEX status table should include one rank row")
    rank_text = " ".join(str(cell) for cell in rank_rows[0])
    for expected in ("netGEX", "64%", "|netGEX|", "88%", "IV/RV", "41%", "P/C", "73%"):
        assert_true(expected in rank_text, "rank row missing " + expected)
    assert_true("n=42" in rank_text, "rank row should expose sample count")

    audited = mod._audit_gex_info(snapshot)
    audited_rank = audited.get("rank") or {}
    audited_metrics = audited_rank.get("metrics") or {}
    assert_true(audited_metrics["gex_board.total_net_gex"]["rank_pct"] == 64,
                "audit gex_info should preserve rank metrics for frontend cards")


def test_chart_adds_processed_net_gamma_line(mod):
    holder = {}

    def chart_factory(config):
        holder["chart"] = FakeChart(config)
        return holder["chart"]

    mod.Chart = chart_factory
    config = dict(mod.CONFIG)
    config["chart_reset_on_start"] = False
    chart = mod.DemoChart(config)
    ok = chart.update({
        "ts_ms": 1781788800000,
        "runtime_facts": {"current_price": 63541.5},
        "factor_snapshot": {
            "anchor": {},
            "flow": {},
            "m_die": {},
            "gex_info": {"total_net_gex": -112000000},
        },
    })
    assert_true(ok, "chart update should succeed")
    chart_config = holder["chart"].config
    title = chart_config["title"]["text"]
    assert_true("0.4.1" not in title and "前置信号" not in title,
                "chart title should not expose old NRD 0.4.1 pre-signal wording")
    series_ids = [item.get("id") for item in chart_config["series"]]
    assert_true("processed_net_gamma_musd" in series_ids,
                "chart should define processed_net_gamma_musd series")
    y_axis_titles = [item.get("title", {}).get("text") for item in chart_config["yAxis"]]
    assert_true("净Gamma(M USD)" in y_axis_titles,
                "chart should define a net gamma million-USD axis")
    net_gamma_index = series_ids.index("processed_net_gamma_musd")
    assert_true((net_gamma_index, [1781788800000, -112.0]) in holder["chart"].add_calls,
                "chart should add net gamma scaled to million USD")


def test_chart_does_not_mix_internal_gamma_regime_proxy(mod):
    holder = {}

    def chart_factory(config):
        holder["chart"] = FakeChart(config)
        return holder["chart"]

    mod.Chart = chart_factory
    config = dict(mod.CONFIG)
    config["chart_reset_on_start"] = False
    chart = mod.DemoChart(config)
    ok = chart.update({
        "ts_ms": 1781788800000,
        "runtime_facts": {"current_price": 63541.5},
        "factor_snapshot": {
            "anchor": {},
            "flow": {},
            "m_die": {},
            "gex_info": {},
            "gamma_regime": {"net_gamma_notional_usd": 999000000},
        },
    })
    assert_true(ok, "chart update should still succeed without GEX net gamma")
    assert_true(all(index != 5 for index, point in holder["chart"].add_calls),
                "chart must not draw internal gamma_regime proxy as GEX net gamma")


def main():
    mod = load_signal_module()
    test_rank_is_preserved_and_rendered(mod)
    test_chart_adds_processed_net_gamma_line(mod)
    test_chart_does_not_mix_internal_gamma_regime_proxy(mod)
    print("signal_gex_rank_chart_contract: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_gex_rank_chart_contract: FAIL - " + str(exc))
        sys.exit(1)
