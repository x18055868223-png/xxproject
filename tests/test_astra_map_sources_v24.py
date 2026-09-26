import json
from datetime import date, timedelta

from tools.astra_data_engine_v23 import DataEngine
from tools import astra_map_sources_v24 as map24


T = 1_790_323_200_000


def test_real_loader_path_records_completion_instead_of_request_clock(tmp_path, monkeypatch):
    registry=write_registry(tmp_path/'registry.json',[map24.ETF_FLOW])
    engine=DataEngine(tmp_path/'state',registry_path=registry)
    def loader_for(*args,**kwargs):
        return lambda: {'values':{'response':'received'},'observation_end_ms':T-1000,
                        'retrieved_at_ms':T,'data_state':'OK','source_revision':'raw-response'}
    monkeypatch.setattr(map24,'_loader_for',loader_for)
    records=map24.collect_sources(engine,T,config={'products':[map24.ETF_FLOW],'clock_ms':lambda:T+5000})
    assert records[0]['time']['first_seen_at_ms']==T+5000
    assert records[0]['time']['observation_end_ms']==T-1000


def write_registry(path, product_ids):
    registry = {
        "schema": "astra_data_product_registry@2.3.0",
        "products": [
            {
                "product_id": product_id,
                "provider_id": product_id.split(".")[0],
                "collector_id": "test_collector",
                "primary_collector": "server",
                "connection_status": "REGISTERED_ADAPTER",
                "definition_version": product_id + "@test",
                "market": "BTC",
                "asset": "BTC",
                "currency": "USD",
                "aggregation": "test",
                "unit": "mixed",
                "usage_policies": {
                    "default": {"ttl_ms": 300_000, "allowed_data_states": ["OK", "PARTIAL"]},
                    "fetch": {"ttl_ms": 300_000, "allowed_data_states": ["OK", "PARTIAL"]},
                },
                "request_policy": {"budget_per_minute": 50, "retry_backoff_ms": [1000]},
            }
            for product_id in product_ids
        ],
    }
    path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
    return path


def csv_from_totals(days_and_totals):
    rows = ["Date,IBIT,FBTC,MSBT,Total", "Total,1,2,3,6", "Average,1,2,3,6"]
    for day, total in days_and_totals:
        rows.append(f"{day.isoformat()},{total},0,-,{total}")
    return "\n".join(rows)


def test_nominal_chain_cost_does_not_use_contract_index_as_precise_distance(tmp_path):
    registry = write_registry(tmp_path / "registry.json", [map24.BRK_COST])
    engine = DataEngine(tmp_path / "state", registry)
    engine.ingest(map24.BRK_COST, {
        "cp_sth_usd": 100, "source_price_usd": 110,
        "observation_dates": {"cp_sth": "2026-09-25", "source_price": "2026-09-25"},
        "series": {"cp_sth": {"points": [{"date": "2026-09-25", "value": 100}]}},
    }, observation_end_ms=T, retrieved_at_ms=T)
    record = engine.read(map24.BRK_COST, now_ms=T + 1, usage="background")
    row = map24._inventory_row([record], [], T + 1, 999)
    assert "gap_sth_now_pct" not in row["metrics"]
    assert abs(row["metrics"]["gap_sth_source_price_pct"] - 10) < 1e-9
    assert row["metrics"]["cost_comparison_basis"] == "BITVIEW_SAME_SOURCE_SAME_DAY_ONLY"


def test_macro_index_points_are_distinct_from_relative_percent_and_yield_bp():
    dates = [date(2026, 8, 1) + timedelta(days=i) for i in range(21)]
    result = map24.macro_common_metrics({
        "dollar": [{"date": day.isoformat(), "value": 200 + i} for i, day in enumerate(dates)],
        "nominal_10y": [{"date": day.isoformat(), "value": 2 + i / 100} for i, day in enumerate(dates)],
    })
    assert result["dollar"]["change_5obs"] == 5
    assert abs(result["dollar"]["change_5obs_pct"] - (220 / 215 - 1) * 100) < 1e-9
    assert abs(result["dollar"]["change_20obs_pct"] - 10) < 1e-9
    assert abs(result["nominal_10y"]["change_5obs_bp"] - 5) < 1e-9
    assert "change_5obs_pct" not in result["nominal_10y"]


def test_funding_24h_uses_actual_settlement_clock_without_fixed_frequency_or_zero_fill():
    rows = [
        {"fundingTime": T - map24.MS_DAY - 1000, "fundingRate": "0.004"},
        {"fundingTime": T - 23 * 3600000, "fundingRate": "0.001"},
        {"fundingTime": T - 2 * 3600000, "fundingRate": "-0.0002"},
        {"fundingTime": T - 2 * 3600000, "fundingRate": "-0.0002"},
        {"fundingTime": T + 1000, "fundingRate": "0.1"},
        {"fundingTime": T - 1000, "renamedRate": "0.1"},
        {"fundingTime": T - 1000, "fundingRate": "0.1", "record_type": "predicted"},
    ]
    result = map24.funding_settlement_metrics(rows, cutoff_at_ms=T)
    assert result["window_24h"]["settled_count"] == 2
    assert abs(result["window_24h"]["settled_rate_sum"] - .0008) < 1e-12
    assert result["previous_24h"]["settled_rate_sum"] == .004
    assert result["rejected_record_count"] == 2
    assert result["predicted_count_excluded"] == 1
    assert map24.funding_settlement_metrics([], cutoff_at_ms=T)["window_24h"]["settled_rate_sum"] is None


def etf_rows_for_example():
    latest = date(2026, 9, 18)
    sessions = map24.nyse_trading_days(latest - timedelta(days=130), latest)
    needed = sessions[-67:]
    totals = [(day, 100) for day in needed[:60]]
    totals.extend((day, value) for day, value in zip(needed[60:], [300, 300, 300, 300, 100, 100, 100]))
    return totals


def test_d04_etf_parser_distinguishes_zero_missing_holiday_and_summary_rows():
    source = "\n".join(
        [
            "Date,IBIT,FBTC,MSBT,Total",
            "Fee,0,0,0,0",
            "2026-09-04,0,-,,0",
            "2026-09-08,(25),10,15,0",
        ]
    )
    parsed = map24.parse_etf_flow_table(source)
    assert [row["date"] for row in parsed["rows"]] == ["2026-09-04", "2026-09-08"]
    assert parsed["rows"][0]["funds"]["ibit"]["state"] == "zero"
    assert parsed["rows"][0]["funds"]["fbtc"]["state"] == "not_disclosed"
    assert parsed["rows"][0]["funds"]["msbt"]["state"] == "blank"
    assert parsed["skipped_rows"][0]["reason"] == "summary_or_non_day_row"
    assert map24.nyse_trading_days(date(2026, 9, 4), date(2026, 9, 8)) == [date(2026, 9, 4), date(2026, 9, 8)]


def test_d04_etf_missing_trading_day_does_not_skip_forward_to_make_7_days():
    totals = etf_rows_for_example()
    missing_day = totals[-4][0]
    source = csv_from_totals([(day, value) for day, value in totals if day != missing_day])
    parsed = map24.parse_etf_flow_table(source)
    assert parsed["metrics"]["f7_usd_m"] is None
    assert "etf_last7_has_missing_session" in parsed["metrics"]["missing"]


def test_d05_d06_etf_u_requires_prior60_and_positive_inflow_slowdown_is_not_outflow():
    parsed = map24.parse_etf_flow_table(csv_from_totals(etf_rows_for_example()))
    metrics = parsed["metrics"]
    assert metrics["f3_usd_m"] == 300
    assert metrics["f7_usd_m"] == 1500
    assert metrics["q_abs_usd_m_prior60"] == 100
    assert metrics["u3"] == 1
    assert round(metrics["u7"], 6) == round((1500 / 7) / 100, 6)
    assert "仍为净流入" in metrics["summary_cn"]
    assert "低于7日窗口" in metrics["summary_cn"]


def test_t01_etf_after_cutoff_row_is_not_allowed_into_snapshot():
    day = date(2026, 9, 25)
    cutoff_before_close = map24.nyse_session_close_ms(day) - 1
    parsed = map24.parse_etf_flow_table(csv_from_totals([(day, 500)]), cutoff_at_ms=cutoff_before_close)
    assert parsed["rows"] == []
    assert any(item["reason"] == "after_cutoff" for item in parsed["skipped_rows"])


def test_d07_rp_and_cp_are_distinct_and_cp_is_not_a_density_peak():
    prices = map24.capitalized_price([(1, 10), (1, 30)])
    assert prices["realized_price"] == 20
    assert prices["capitalized_price"] == 25


def test_bitview_cost_flat_fields_match_projection_contract():
    fields = map24._bitview_cost_flat_fields(
        {
            "cp_sth": {"points": [{"date": "2026-09-24", "value": 100}]},
            "cp_lth": {"points": [{"date": "2026-09-24", "value": 80}]},
            "cp_all": {"points": [{"date": "2026-09-24", "value": 90}]},
            "phase": {"points": [{"date": "2026-09-24", "value": "neutral"}]},
            "source_price": {"points": [{"date": "2026-09-24", "value": 110}]},
        }
    )
    assert fields["cp_sth_usd"] == 100
    assert fields["cp_lth_usd"] == 80
    assert fields["cp_all_usd"] == 90
    assert fields["source_phase"] == "neutral"
    assert fields["source_price_usd"] == 110
    assert fields["latest_observation_date"] == "2026-09-24"


def test_d08_d09_realized_pnl_sums_amounts_not_daily_percentages_and_handles_modes():
    profit = [{"date": "2026-09-01", "value": 9}, {"date": "2026-09-02", "value": 10}]
    loss = [{"date": "2026-09-01", "value": 1}, {"date": "2026-09-02", "value": 90}]
    metrics = map24.realized_pnl_metrics(profit, loss, value_mode="daily")
    assert metrics["loss_share_14d"] == 91 / 110

    cumulative_profit = [
        {"date": "2026-08-31", "value": 100},
        {"date": "2026-09-01", "value": 109},
        {"date": "2026-09-02", "value": 119},
    ]
    cumulative_loss = [
        {"date": "2026-08-31", "value": 50},
        {"date": "2026-09-01", "value": 51},
        {"date": "2026-09-02", "value": 141},
    ]
    cum = map24.realized_pnl_metrics(cumulative_profit, cumulative_loss, value_mode="cumulative")
    assert cum["profit_14d_usd"] == 19
    assert cum["loss_14d_usd"] == 91

    zero = map24.realized_pnl_metrics([{"date": "2026-09-01", "value": 0}], [{"date": "2026-09-01", "value": 0}], value_mode="daily")
    assert zero["loss_share_14d"] is None


def test_d10_oi_native_quantity_is_not_replaced_by_notional_price_move():
    metrics = map24.oi_change_metrics(100, 100, 120_000, 100_000)
    assert metrics["native_change_btc"] == 0
    assert metrics["native_oi_unchanged"] is True
    assert round(metrics["notional_change_pct"], 8) == 20
    assert metrics["notional_is_price_dependent"] is True


def test_d11_funding_dedupes_settled_records_and_excludes_predictions():
    records = [
        {"fundingTime": 1000, "fundingRate": "0.0001"},
        {"fundingTime": 1000, "fundingRate": "0.0002"},
        {"fundingTime": 2000, "fundingRate": "-0.0001"},
        {"funding_time_ms": 3000, "funding_rate": "0.0003", "record_type": "predicted"},
    ]
    metrics = map24.funding_settlement_metrics(records)
    assert metrics["settled_count"] == 2
    assert round(metrics["settled_funding_rate_sum"], 7) == 0.0001
    assert metrics["predicted_count_excluded"] == 1
    assert metrics["prediction_mixed_with_settlement"] is False


def test_d12_borrow_hourly_rate_apr_and_day_peak_identity():
    apr = map24.borrow_apr_from_hourly(0.000004)
    assert round(apr["simple_apr_pct"], 3) == 3.504
    assert apr["formula"] == "hourly_rate_decimal * 24 * 365"


def test_d13_macro_changes_use_common_observation_endpoints():
    series = {
        "dollar": [{"date": "2026-09-01", "value": 100}, {"date": "2026-09-02", "value": 101}],
        "nominal_10y": [{"date": "2026-08-31", "value": 4.0}, {"date": "2026-09-02", "value": 4.1}],
        "real_10y": [{"date": "2026-09-02", "value": 1.8}, {"date": "2026-09-03", "value": 1.9}],
    }
    metrics = map24.macro_common_metrics(series)
    assert metrics["common_dates"] == ["2026-09-02"]
    assert metrics["latest_date"] == "2026-09-02"
    assert "less_than_21_common_observations" in metrics["missing"]


def test_collect_sources_uses_engine_fetch_and_completion_clock_for_first_seen(tmp_path):
    registry = write_registry(tmp_path / "registry.json", [map24.ETF_FLOW])
    engine = DataEngine(tmp_path / "state", registry_path=registry)
    calls = []

    def transport(url, params=None, response_type="text"):
        calls.append((url, params, response_type))
        return csv_from_totals(etf_rows_for_example())

    clock_values = iter([T + 1234])
    records = map24.collect_sources(
        engine,
        T,
        transport=transport,
        config={"products": [map24.ETF_FLOW], "clock_ms": lambda: next(clock_values)},
    )
    assert len(records) == 1
    record = records[0]
    assert record["time"]["retrieved_at_ms"] == T + 1234
    assert record["time"]["first_seen_at_ms"] == T + 1234
    assert record["content"]["values"]["request_audit"]["actual_http_requests"] == 1
    assert calls[0][2] == "text"


def test_read_sources_reads_cache_without_http_and_requalifies_usage(tmp_path):
    registry = write_registry(tmp_path / "registry.json", [map24.ETF_FLOW])
    engine = DataEngine(tmp_path / "state", registry_path=registry)
    engine.ingest(
        map24.ETF_FLOW,
        {"metrics": {"summary_cn": "ok"}},
        observation_end_ms=T - 1000,
        retrieved_at_ms=T,
        scope={"asset": "BTC", "source": "farside", "table": "historical"},
    )
    records = map24.read_sources(engine, T + 1000, {"products": [map24.ETF_FLOW]})
    assert len(records) == 1
    assert records[0]["usage_decision"]["can_use"] is True
    assert records[0].get("fetch_decision") is None


def test_macro_broad_usd_uses_dtwexbgs_with_limited_cosd_coed_and_dxy_is_not_loader(tmp_path):
    registry = write_registry(tmp_path / "registry.json", [map24.MACRO_USD_BROAD])
    engine = DataEngine(tmp_path / "state", registry_path=registry)
    calls = []

    def transport(url, params=None, response_type="text"):
        calls.append((url, params, response_type))
        return "observation_date,DTWEXBGS\n2026-09-01,100\n2026-09-02,101\n"

    records = map24.collect_sources(
        engine,
        T,
        transport=transport,
        config={"products": [map24.MACRO_USD_BROAD], "clock_ms": lambda: T + 1, "fred_coed": "2026-09-26"},
    )
    assert len(records) == 1
    assert records[0]["identity"]["product_id"] == map24.MACRO_USD_BROAD
    assert calls[0][1]["id"] == "DTWEXBGS"
    assert calls[0][1]["coed"] == "2026-09-26"
    assert calls[0][1]["cosd"] == "2026-06-28"
    try:
        map24._loader_for(map24.MACRO_DXY, transport=transport, config={})
    except map24.MapSourceError as exc:
        assert exc.code == "UNSUPPORTED_PRODUCT"
    else:
        raise AssertionError("DXY must stay unconnected in this adapter")


def test_oi_missing_source_time_is_partial_and_not_backfilled_to_retrieved(tmp_path):
    registry = write_registry(tmp_path / "registry.json", [map24.OI_NATIVE])
    engine = DataEngine(tmp_path / "state", registry_path=registry)

    def transport(url, params=None, response_type="json"):
        return {"symbol": "BTCUSDT", "openInterest": "100"}

    records = map24.collect_sources(
        engine,
        T,
        transport=transport,
        config={"products": [map24.OI_NATIVE], "clock_ms": lambda: T + 222},
    )
    record = records[0]
    assert record["time"]["observation_end_ms"] == 0
    assert record["time"]["retrieved_at_ms"] == T + 222
    assert record["quality"]["data_state"] == "PARTIAL"
    assert "BINANCE_OI_SOURCE_TIME_MISSING" in record["quality"]["reason_codes"]
    assert record["content"]["values"]["source_time_basis"] == "missing_source_time"


def test_oi_24h_endpoint_delta_uses_native_open_interest_window(tmp_path):
    registry = write_registry(tmp_path / "registry.json", [map24.OI_NATIVE])
    engine = DataEngine(tmp_path / "state", registry_path=registry)
    source_time = T - 1000
    calls = []

    def transport(url, params=None, response_type="json"):
        calls.append((url, params))
        if "openInterestHist" in url:
            return [{"timestamp": source_time - map24.MS_DAY, "sumOpenInterest": "90"}]
        return {"symbol": "BTCUSDT", "openInterest": "100", "time": source_time}

    records = map24.collect_sources(
        engine,
        T,
        transport=transport,
        config={"products": [map24.OI_NATIVE], "clock_ms": lambda: T + len(calls)},
    )
    values = records[0]["content"]["values"]
    assert values["oi_24h_window"]["status"] == "OK"
    assert values["oi_24h_window"]["change_native_btc"] == 10
    assert values["request_audit"]["actual_http_requests"] == 2
    assert calls[1][1]["startTime"] == source_time - map24.MS_DAY


def test_t02_same_payload_preserves_first_seen_and_revision_creates_new_record(tmp_path):
    registry = write_registry(tmp_path / "registry.json", [map24.ETF_FLOW])
    engine = DataEngine(tmp_path / "state", registry_path=registry)
    values = {"schema": "btc_etf_flow_normalized@2.4.0", "metrics": {"f3_usd_m": 1}}
    first = engine.ingest(map24.ETF_FLOW, values, observation_end_ms=T - 1000, retrieved_at_ms=T, source_revision="rev-1")
    same = engine.ingest(map24.ETF_FLOW, values, observation_end_ms=T - 1000, retrieved_at_ms=T + 1000, source_revision="rev-1")
    revised = engine.ingest(map24.ETF_FLOW, {**values, "metrics": {"f3_usd_m": 2}}, observation_end_ms=T - 1000, retrieved_at_ms=T + 2000, source_revision="rev-2", parent_record_ids=[first["record_id"]])
    assert same["record_id"] == first["record_id"]
    assert same["time"]["first_seen_at_ms"] == T
    assert revised["record_id"] != first["record_id"]
    assert revised["version"]["parent_record_ids"] == [first["record_id"]]


def test_build_background_contains_four_rows_and_keeps_source_values(tmp_path):
    registry = write_registry(tmp_path / "registry.json", [map24.ETF_FLOW])
    engine = DataEngine(tmp_path / "state", registry_path=registry)
    record = engine.ingest(
        map24.ETF_FLOW,
        map24.parse_etf_flow_table(csv_from_totals(etf_rows_for_example())),
        observation_end_ms=T - 1000,
        retrieved_at_ms=T,
    )
    background = map24.build_background([record], T)
    assert set(background["rows"]) == {"allocation", "inventory", "financing", "external_conditions"}
    assert background["rows"]["allocation"]["source_record_ids"] == [record["record_id"]]
    assert "source_values" in background["rows"]["allocation"]["metrics"]


def test_background_rejects_stale_or_partial_records_for_current_summary(tmp_path):
    registry = write_registry(tmp_path / "registry.json", [map24.BRK_COST, map24.OI_NATIVE, map24.MACRO_USD_BROAD])
    engine = DataEngine(tmp_path / "state", registry_path=registry)
    cost = engine.ingest(
        map24.BRK_COST,
        {"schema": "bitview_cost_basis@2.4.0", "cp_sth_usd": 100, "series": {"cp_sth": {"points": [{"date": "2026-09-01", "value": 100}]}}},
        observation_end_ms=T - 1_000_000,
        retrieved_at_ms=T - 1_000_000,
    )
    oi = engine.ingest(
        map24.OI_NATIVE,
        {"schema": "binance_oi_native@2.4.0", "open_interest_native_btc": 100},
        observation_end_ms=T - 1_000_000,
        retrieved_at_ms=T - 1_000_000,
    )
    macro = engine.ingest(
        map24.MACRO_USD_BROAD,
        {"schema": "fred_public_daily@2.4.0", "observations": [{"date": "2026-09-01", "value": 100}]},
        observation_end_ms=T - 1_000_000,
        retrieved_at_ms=T - 1_000_000,
    )
    stale_records = [
        engine.read(map24.BRK_COST, now_ms=T + 1_000_000, usage="fetch", scope={"asset": "BTC", "source": "bitview", "sample": "limited"}),
        engine.read(map24.OI_NATIVE, now_ms=T + 1_000_000, usage="fetch", scope={"symbol": "BTCUSDT", "venue": "binance_um"}),
        engine.read(map24.MACRO_USD_BROAD, now_ms=T + 1_000_000, usage="fetch", scope={"series": "DTWEXBGS"}),
    ]
    background = map24.build_background(stale_records, T + 1_000_000)
    inventory = background["rows"]["inventory"]
    financing = background["rows"]["financing"]
    external = background["rows"]["external_conditions"]
    assert inventory["fact_state"] == "partial"
    assert "cost_basis_source_values" in inventory["metrics"]
    assert "cost_basis" not in inventory["metrics"]
    assert "当前合格背景" in inventory["summary_cn"]
    assert financing["fact_state"] == "partial"
    assert "oi_source_values" in financing["metrics"]
    assert "oi" not in financing["metrics"]
    assert external["fact_state"] == "partial"
    assert "dollar_source_values" in external["metrics"]
    assert external["metrics"]["missing"]



def test_bitview_search_string_ids_are_kept_as_candidates():
    candidates = map24._extract_series_candidates(["capitalized_price", "lth_capitalized_price"])
    assert candidates == [
        {"id": "capitalized_price", "series_id": "capitalized_price"},
        {"id": "lth_capitalized_price", "series_id": "lth_capitalized_price"},
    ]


def test_bitview_day1_epoch_matches_official_genesis_day():
    assert map24.BITVIEW_DAY1_EPOCH == date(2009, 1, 1)
    assert (date(2009, 1, 2) - map24.BITVIEW_DAY1_EPOCH).days == 1


def test_bitview_cost_uses_string_id_search_and_integer_day1_sample_bounds():
    calls = []
    expected_start = (date(2026, 9, 1) - map24.BITVIEW_DAY1_EPOCH).days
    expected_end = (date(2026, 9, 3) - map24.BITVIEW_DAY1_EPOCH).days

    metadata = {
        "capitalized_price": {"id": "capitalized_price", "name": "Capitalized Price", "description": "all capitalized price", "indexes": ["day1"], "unit": "Dollars"},
        "lth_capitalized_price": {"id": "lth_capitalized_price", "name": "LTH Capitalized Price", "description": "long-term holder capitalized price", "indexes": ["day1"], "unit": "Dollars"},
        "sth_capitalized_price": {"id": "sth_capitalized_price", "name": "STH Capitalized Price", "description": "short-term holder capitalized price", "indexes": ["day1"], "unit": "Dollars"},
        "capital_sentiment_phase": {"id": "capital_sentiment_phase", "name": "Capital Sentiment Phase", "description": "capital sentiment phase", "indexes": ["day1"], "type": "String"},
        "source_price": {"id": "source_price", "name": "Source Price", "description": "bitcoin source price", "indexes": ["day1"], "unit": "Dollars"},
    }
    values = {
        "capitalized_price": [90, 91],
        "lth_capitalized_price": [80, 81],
        "sth_capitalized_price": [100, 101],
        "capital_sentiment_phase": ["neutral", "heated"],
        "source_price": [110, 111],
    }

    def transport(url, params=None, response_type="json"):
        calls.append((url, dict(params or {})))
        if url.endswith("/api/series/search"):
            q = params.get("q", "")
            if "short" in q or "sth" in q:
                return ["capitalized_price", "lth_capitalized_price", "sth_capitalized_price"]
            if "long" in q or "lth" in q:
                return ["capitalized_price", "lth_capitalized_price"]
            if "phase" in q or "sentiment" in q:
                return ["capital_sentiment_phase"]
            if "price" in q and "capitalized" not in q:
                return ["source_price"]
            return ["capitalized_price", "lth_capitalized_price"]
        series_id = url.rstrip("/").split("/")[-1]
        maybe_index = url.rstrip("/").split("/")[-2]
        if series_id == "day1":
            real_id = maybe_index
            assert params == {"start": str(expected_start), "end": str(expected_end)}
            return {"index": "day1", "start": expected_start, "end": expected_end, "data": values[real_id]}
        return metadata[series_id]

    loaded = map24._load_bitview_cost(
        transport,
        {"bitview_start": "2026-09-01", "bitview_end": "2026-09-03", "clock_ms": lambda: T + 1},
    )
    values_out = loaded["values"]
    assert values_out["cp_all_usd"] == 91
    assert values_out["cp_lth_usd"] == 81
    assert values_out["cp_sth_usd"] == 101
    assert values_out["source_phase"] == "heated"
    assert values_out["source_price_usd"] == 111
    assert values_out["latest_observation_date"] == "2026-09-02"
    assert loaded["observation_end_ms"] == map24.utc_day_end_ms(date(2026, 9, 2))
    sample_calls = [item for item in calls if item[0].endswith('/day1')]
    assert sample_calls
    assert all(item[1] == {"start": str(expected_start), "end": str(expected_end)} for item in sample_calls)


def test_bitview_default_day_sample_window_excludes_current_utc_day():
    current_day = date(2026, 9, 26)
    params, missing = map24._bitview_sample_params(
        "day1",
        {"clock_ms": lambda: map24.utc_day_end_ms(current_day)},
        key="source_price",
    )
    assert missing is None
    assert params == {
        "start": str(((current_day - timedelta(days=30)) - map24.BITVIEW_DAY1_EPOCH).days),
        "end": str((current_day - map24.BITVIEW_DAY1_EPOCH).days),
    }


def test_bitview_cost_filters_current_or_future_daily_points_before_observation_clock():
    calls = []
    current_day = date(2026, 9, 26)
    start_day = date(2026, 9, 24)
    end_day = date(2026, 9, 27)
    expected_params = {
        "start": str((start_day - map24.BITVIEW_DAY1_EPOCH).days),
        "end": str((end_day - map24.BITVIEW_DAY1_EPOCH).days),
    }
    concepts = {
        "cp_sth": "STH Capitalized Price",
        "cp_lth": "LTH Capitalized Price",
        "cp_all": "Capitalized Price",
        "phase": "Capital Sentiment Phase",
        "source_price": "Source Price",
    }

    def transport(url, params=None, response_type="json"):
        calls.append((url, dict(params or {})))
        if url.endswith("/api/series/search"):
            q = params.get("q", "").lower()
            if "short" in q or "sth" in q:
                return ["cp_sth"]
            if "long" in q or "lth" in q:
                return ["cp_lth"]
            if "phase" in q or "sentiment" in q:
                return ["phase"]
            if "price" in q and "capitalized" not in q:
                return ["source_price"]
            return ["cp_all"]
        if url.endswith("/day1"):
            assert params == expected_params
            series_id = url.rstrip("/").split("/")[-2]
            values = ["neutral", "heated", "late"] if series_id == "phase" else [100, 101, 102]
            return {"index": "day1", "start": expected_params["start"], "end": expected_params["end"], "data": values}
        series_id = url.rstrip("/").split("/")[-1]
        return {"id": series_id, "name": concepts[series_id], "description": concepts[series_id], "indexes": ["day1"], "type": "Dollars"}

    loaded = map24._load_bitview_cost(
        transport,
        {
            "bitview_start": start_day.isoformat(),
            "bitview_end": end_day.isoformat(),
            "clock_ms": lambda: map24.utc_day_end_ms(current_day),
        },
    )
    assert loaded["observation_end_ms"] == map24.utc_day_end_ms(date(2026, 9, 25))
    assert loaded["observation_end_ms"] < map24.utc_day_end_ms(current_day)
    assert "bitview_incomplete_utc_day_filtered:source_price" in loaded["reason_codes"]
    assert loaded["values"]["series"]["source_price"]["points"][-1]["date"] == "2026-09-25"


def test_bitview_height_only_realized_pnl_stays_partial_without_guessing_sample_bounds():
    calls = []

    def transport(url, params=None, response_type="json"):
        calls.append((url, dict(params or {})))
        if url.endswith("/api/series/search"):
            q = params.get("q", "")
            if "profit" in q:
                return ["realized_profit"]
            if "loss" in q:
                return ["realized_loss"]
            if "cap" in q or "capitalization" in q:
                return ["realized_cap"]
            return []
        series_id = url.rstrip("/").split("/")[-1]
        return {"id": series_id, "name": series_id.replace("_", " "), "description": series_id.replace("_", " "), "indexes": ["height"], "type": "Dollars"}

    loaded = map24._load_bitview_pnl(transport, {"clock_ms": lambda: T + 2})
    assert loaded["quality" if False else "data_state"] == "PARTIAL"
    assert "BITVIEW_SOURCE_TIMESTAMP_MISSING" in loaded["reason_codes"]
    assert "bitview_first_height_search_empty:profit" in loaded["reason_codes"]
    assert "less_than_14_complete_utc_days" in loaded["reason_codes"]
    assert not any(call[0].endswith('/height') for call in calls)


def test_bitview_realized_pnl_height_bridge_builds_strict_utc_14_day_window():
    calls = []
    start_day = date(2026, 9, 1)
    end_day = date(2026, 9, 15)
    day1_start = (start_day - map24.BITVIEW_DAY1_EPOCH).days
    day1_end = ((end_day + timedelta(days=1)) - map24.BITVIEW_DAY1_EPOCH).days
    heights = [1000 + i * 10 for i in range(15)]

    metadata = {
        "realized_profit_sum_24h": {"id": "realized_profit_sum_24h", "name": "Realized Profit Sum 24h", "description": "trailing 24h realized profit", "indexes": ["height", "day1"], "type": "Dollars"},
        "realized_loss_sum_24h": {"id": "realized_loss_sum_24h", "name": "Realized Loss Sum 24h", "description": "trailing 24h realized loss", "indexes": ["height", "day1"], "type": "Dollars"},
        "realized_profit": {"id": "realized_profit", "name": "Realized Profit", "description": "per block realized profit", "indexes": ["height"], "type": "Dollars"},
        "realized_loss": {"id": "realized_loss", "name": "Realized Loss", "description": "per block realized loss", "indexes": ["height"], "type": "Dollars"},
        "realized_cap": {"id": "realized_cap", "name": "Realized Cap", "description": "realized cap stock", "indexes": ["day1"], "type": "Dollars"},
        "first_height": {"id": "first_height", "name": "First Height", "description": "first block height for day1", "indexes": ["day1"], "type": "Integer"},
    }

    def transport(url, params=None, response_type="json"):
        calls.append((url, dict(params or {})))
        if url.endswith("/api/series/search"):
            q = params.get("q", "")
            if "first" in q:
                return ["first_height"]
            if "profit" in q:
                return ["realized_profit_sum_24h", "realized_profit"]
            if "loss" in q:
                return ["realized_loss_sum_24h", "realized_loss"]
            if "cap" in q or "capitalization" in q:
                return ["realized_cap"]
            return []
        if url.endswith("/api/series/first_height/day1"):
            assert params == {"start": str(day1_start), "end": str(day1_end)}
            return {"index": "day1", "start": day1_start, "end": day1_end, "data": heights}
        if url.endswith("/api/series/realized_profit/height"):
            assert params == {"start": "1000", "end": "1140"}
            return {"index": "height", "start": 1000, "end": 1140, "data": [1] * 140}
        if url.endswith("/api/series/realized_loss/height"):
            assert params == {"start": "1000", "end": "1140"}
            return {"index": "height", "start": 1000, "end": 1140, "data": [0.25] * 140}
        if "/realized_profit_sum_24h/" in url or "/realized_loss_sum_24h/" in url:
            raise AssertionError("trailing 24h series must not be sampled as per-block PnL")
        if url.endswith("/api/series/realized_cap/day1"):
            cap_start = (date(2026, 9, 14) - map24.BITVIEW_DAY1_EPOCH).days
            cap_end = (date(2026, 9, 15) - map24.BITVIEW_DAY1_EPOCH).days
            assert params == {"start": str(cap_start), "end": str(cap_end)}
            return {"index": "day1", "start": cap_start, "end": cap_end, "data": [113]}
        series_id = url.rstrip("/").split("/")[-1]
        return metadata[series_id]

    loaded = map24._load_bitview_pnl(
        transport,
        {
            "bitview_start": start_day.isoformat(),
            "bitview_end": end_day.isoformat(),
            "clock_ms": lambda: map24.utc_day_end_ms(date(2026, 9, 26)),
        },
    )
    metrics = loaded["values"]["metrics"]
    assert loaded["data_state"] == "OK"
    assert loaded["reason_codes"] == []
    assert metrics["window_days"] == [(start_day + timedelta(days=i)).isoformat() for i in range(14)]
    assert metrics["profit_14d_usd"] == 140
    assert metrics["loss_14d_usd"] == 35
    assert metrics["loss_share_14d"] == 35 / 175
    assert metrics["realized_cap_start_usd"] is None
    assert metrics["realized_cap_end_usd"] == 113
    assert loaded["values"]["series"]["profit"]["index"] == "height"
    assert loaded["values"]["series"]["profit"]["height_bridge"]["day_count"] == 14
    assert loaded["values"]["request_audit"]["actual_http_requests"] <= loaded["values"]["request_audit"]["max_http_requests"]
    assert not any("/realized_profit_sum_24h/" in call[0] for call in calls)


def test_bitview_realized_pnl_bridge_future_window_fails_closed_without_height_sample():
    calls = []

    def transport(url, params=None, response_type="json"):
        calls.append((url, dict(params or {})))
        if url.endswith("/api/series/search"):
            return ["realized_profit"] if "profit" in params.get("q", "") else []
        return {"id": "realized_profit", "name": "Realized Profit", "description": "per block realized profit", "indexes": ["height"], "type": "Dollars"}

    loaded = map24._load_bitview_pnl(
        transport,
        {
            "bitview_start": "2026-09-20",
            "bitview_end": "2026-09-30",
            "clock_ms": lambda: map24.utc_day_end_ms(date(2026, 9, 26)),
        },
    )
    assert loaded["data_state"] == "PARTIAL"
    assert "bitview_daily_bridge_future_day:profit" in loaded["reason_codes"]
    assert not any(call[0].endswith("/height") for call in calls)


def test_bitview_realized_pnl_default_window_uses_latest_official_complete_boundaries():
    calls = []
    current_day = date(2026, 9, 26)
    default_start = current_day - timedelta(days=20)
    first_query_start = (default_start - map24.BITVIEW_DAY1_EPOCH).days
    first_query_end = ((current_day + timedelta(days=1)) - map24.BITVIEW_DAY1_EPOCH).days
    exact_start = (date(2026, 9, 11) - map24.BITVIEW_DAY1_EPOCH).days
    exact_end = (date(2026, 9, 26) - map24.BITVIEW_DAY1_EPOCH).days

    def transport(url, params=None, response_type="json"):
        calls.append((url, dict(params or {})))
        if url.endswith("/api/series/search"):
            q = params.get("q", "")
            if "first" in q:
                return ["first_height"]
            if "profit" in q:
                return ["realized_profit"]
            if "loss" in q:
                return ["realized_loss"]
            return []
        if url.endswith("/api/series/first_height/day1"):
            if params == {"start": str(first_query_start), "end": str(first_query_end)}:
                return {"index": "day1", "start": first_query_start, "end": first_query_end, "data": [6000 + i for i in range(20)]}
            assert params == {"start": str(exact_start), "end": str(exact_end)}
            return {"index": "day1", "start": exact_start, "end": exact_end, "data": [6005 + i for i in range(15)]}
        if url.endswith("/api/series/realized_profit/height"):
            assert params == {"start": "6005", "end": "6019"}
            return {"index": "height", "start": 6005, "end": 6019, "data": [2] * 14}
        if url.endswith("/api/series/realized_loss/height"):
            assert params == {"start": "6005", "end": "6019"}
            return {"index": "height", "start": 6005, "end": 6019, "data": [1] * 14}
        series_id = url.rstrip("/").split("/")[-1]
        return {"id": series_id, "name": series_id.replace("_", " "), "description": series_id.replace("_", " "), "indexes": ["day1"] if series_id == "first_height" else ["height"], "type": "Dollars"}

    loaded = map24._load_bitview_pnl(transport, {"clock_ms": lambda: map24.utc_day_end_ms(current_day)})
    metrics = loaded["values"]["metrics"]
    assert metrics["window_days"] == [(date(2026, 9, 11) + timedelta(days=i)).isoformat() for i in range(14)]
    assert metrics["profit_14d_usd"] == 28
    assert metrics["loss_14d_usd"] == 14
    bridge = loaded["values"]["series"]["profit"]["height_bridge"]
    assert bridge["window_source"] == "latest_official_day1_boundary"
    assert bridge["end_day_exclusive"] == "2026-09-25"
    assert bridge["current_utc_day"] == "2026-09-26"
    assert all("bitview_daily_bridge_bounds_missing" not in code for code in loaded["reason_codes"])


def test_bitview_realized_pnl_bridge_rejects_missing_height_in_span():
    start_day = date(2026, 9, 1)
    end_day = date(2026, 9, 15)
    day1_start = (start_day - map24.BITVIEW_DAY1_EPOCH).days
    day1_end = ((end_day + timedelta(days=1)) - map24.BITVIEW_DAY1_EPOCH).days

    def transport(url, params=None, response_type="json"):
        if url.endswith("/api/series/search"):
            q = params.get("q", "")
            if "first" in q:
                return ["first_height"]
            if "profit" in q:
                return ["realized_profit"]
            return []
        if url.endswith("/api/series/first_height/day1"):
            return {"index": "day1", "start": day1_start, "end": day1_end, "data": [1000 + i * 10 for i in range(15)]}
        if url.endswith("/api/series/realized_profit/height"):
            return {"index": "height", "start": 1000, "end": 1140, "data": [1] * 139}
        series_id = url.rstrip("/").split("/")[-1]
        return {"id": series_id, "name": "Realized Profit", "description": "per block realized profit", "indexes": ["height"] if series_id != "first_height" else ["day1"], "type": "Dollars"}

    loaded = map24._load_bitview_pnl(
        transport,
        {"bitview_start": start_day.isoformat(), "bitview_end": end_day.isoformat(), "clock_ms": lambda: map24.utc_day_end_ms(date(2026, 9, 26))},
    )
    assert loaded["data_state"] == "PARTIAL"
    assert "bitview_height_span_incomplete:profit" in loaded["reason_codes"]


def test_bitview_realized_pnl_bridge_rejects_duplicate_height():
    start_day = date(2026, 9, 1)
    end_day = date(2026, 9, 15)
    day1_start = (start_day - map24.BITVIEW_DAY1_EPOCH).days
    day1_end = ((end_day + timedelta(days=1)) - map24.BITVIEW_DAY1_EPOCH).days
    points = [[1000 + i, 1] for i in range(140)]
    points[7] = [1006, 1]

    def transport(url, params=None, response_type="json"):
        if url.endswith("/api/series/search"):
            q = params.get("q", "")
            if "first" in q:
                return ["first_height"]
            if "profit" in q:
                return ["realized_profit"]
            return []
        if url.endswith("/api/series/first_height/day1"):
            return {"index": "day1", "start": day1_start, "end": day1_end, "data": [1000 + i * 10 for i in range(15)]}
        if url.endswith("/api/series/realized_profit/height"):
            return {"index": "height", "data": points}
        series_id = url.rstrip("/").split("/")[-1]
        return {"id": series_id, "name": "Realized Profit", "description": "per block realized profit", "indexes": ["height"] if series_id != "first_height" else ["day1"], "type": "Dollars"}

    loaded = map24._load_bitview_pnl(
        transport,
        {"bitview_start": start_day.isoformat(), "bitview_end": end_day.isoformat(), "clock_ms": lambda: map24.utc_day_end_ms(date(2026, 9, 26))},
    )
    assert loaded["data_state"] == "PARTIAL"
    assert "bitview_height_duplicate:profit:1006" in loaded["reason_codes"]


def test_bitview_realized_pnl_bridge_rejects_height_span_over_guardrail():
    start_day = date(2026, 9, 1)
    end_day = date(2026, 9, 15)
    day1_start = (start_day - map24.BITVIEW_DAY1_EPOCH).days
    day1_end = ((end_day + timedelta(days=1)) - map24.BITVIEW_DAY1_EPOCH).days

    def transport(url, params=None, response_type="json"):
        if url.endswith("/api/series/search"):
            q = params.get("q", "")
            if "first" in q:
                return ["first_height"]
            if "profit" in q:
                return ["realized_profit"]
            return []
        if url.endswith("/api/series/first_height/day1"):
            return {"index": "day1", "start": day1_start, "end": day1_end, "data": [1000 + i * 1000 for i in range(15)]}
        series_id = url.rstrip("/").split("/")[-1]
        return {"id": series_id, "name": "Realized Profit", "description": "per block realized profit", "indexes": ["height"] if series_id != "first_height" else ["day1"], "type": "Dollars"}

    loaded = map24._load_bitview_pnl(
        transport,
        {
            "bitview_start": start_day.isoformat(),
            "bitview_end": end_day.isoformat(),
            "bitview_height_max_span": 5000,
            "clock_ms": lambda: map24.utc_day_end_ms(date(2026, 9, 26)),
        },
    )
    assert loaded["data_state"] == "PARTIAL"
    assert "bitview_height_span_too_large:profit" in loaded["reason_codes"]


def test_bitview_first_height_date_index_is_not_used_as_day1_bridge():
    def transport(url, params=None, response_type="json"):
        if url.endswith("/api/series/search"):
            q = params.get("q", "")
            if "first" in q:
                return ["first_height"]
            if "profit" in q:
                return ["realized_profit"]
            return []
        series_id = url.rstrip("/").split("/")[-1]
        if series_id == "first_height":
            return {"id": "first_height", "name": "First Height", "description": "first block height", "indexes": ["date"], "type": "Integer"}
        return {"id": series_id, "name": "Realized Profit", "description": "per block realized profit", "indexes": ["height"], "type": "Dollars"}

    loaded = map24._load_bitview_pnl(
        transport,
        {"bitview_start": "2026-09-01", "bitview_end": "2026-09-15", "clock_ms": lambda: map24.utc_day_end_ms(date(2026, 9, 26))},
    )
    assert loaded["data_state"] == "PARTIAL"
    assert "bitview_first_height_day1_index_missing:profit" in loaded["reason_codes"]


def test_bitview_realized_pnl_bridge_rejects_negative_per_block_values():
    start_day = date(2026, 9, 1)
    end_day = date(2026, 9, 15)
    day1_start = (start_day - map24.BITVIEW_DAY1_EPOCH).days
    day1_end = ((end_day + timedelta(days=1)) - map24.BITVIEW_DAY1_EPOCH).days

    def transport(url, params=None, response_type="json"):
        if url.endswith("/api/series/search"):
            q = params.get("q", "")
            if "first" in q:
                return ["first_height"]
            if "profit" in q:
                return ["realized_profit"]
            if "loss" in q:
                return ["realized_loss"]
            return []
        if url.endswith("/api/series/first_height/day1"):
            return {"index": "day1", "start": day1_start, "end": day1_end, "data": [1000 + i for i in range(15)]}
        if url.endswith("/api/series/realized_profit/height"):
            return {"index": "height", "start": 1000, "end": 1014, "data": [1] * 14}
        if url.endswith("/api/series/realized_loss/height"):
            return {"index": "height", "start": 1000, "end": 1014, "data": [1, -1] + [1] * 12}
        series_id = url.rstrip("/").split("/")[-1]
        return {"id": series_id, "name": series_id.replace("_", " "), "description": series_id.replace("_", " "), "indexes": ["day1"] if series_id == "first_height" else ["height"], "type": "Dollars"}

    loaded = map24._load_bitview_pnl(
        transport,
        {
            "bitview_start": start_day.isoformat(),
            "bitview_end": end_day.isoformat(),
            "clock_ms": lambda: map24.utc_day_end_ms(date(2026, 9, 26)),
        },
    )
    assert loaded["data_state"] == "PARTIAL"
    assert "bitview_negative_value:loss" in loaded["reason_codes"]


def test_bitview_realized_pnl_bridge_respects_request_budget():
    def transport(url, params=None, response_type="json"):
        if url.endswith("/api/series/search"):
            return ["realized_profit"]
        return {"id": "realized_profit", "name": "Realized Profit", "description": "per block realized profit", "indexes": ["height"], "type": "Dollars"}

    loaded = map24._load_bitview_pnl(
        transport,
        {
            "bitview_start": "2026-09-01",
            "bitview_end": "2026-09-15",
            "bitview_max_requests": 2,
            "clock_ms": lambda: map24.utc_day_end_ms(date(2026, 9, 26)),
        },
    )
    assert loaded["data_state"] == "PARTIAL"
    assert "bitview_request_limit:profit" in loaded["reason_codes"]
    assert loaded["values"]["request_audit"]["actual_http_requests"] == 2
    assert loaded["values"]["request_audit"]["max_http_requests"] == 2


def test_bitview_date_index_uses_iso_date_bounds_not_day1_integer_bounds():
    calls = []
    expected_day1_start = str((date(2026, 9, 1) - map24.BITVIEW_DAY1_EPOCH).days)

    def transport(url, params=None, response_type="json"):
        calls.append((url, dict(params or {})))
        if url.endswith("/api/series/search"):
            return ["daily_price"]
        if url.endswith("/api/series/daily_price/date"):
            assert params == {"start": "2026-09-01", "end": "2026-09-03"}
            assert params["start"] != expected_day1_start
            return {"index": "date", "start": "2026-09-01", "end": "2026-09-03", "data": [10, 11]}
        return {"id": "daily_price", "name": "Bitcoin Price", "description": "bitcoin source price", "indexes": ["date"], "unit": "Dollars"}

    found = map24._discover_bitview_series(
        transport,
        {"bitview_start": "2026-09-01", "bitview_end": "2026-09-03"},
        {"source_price": "bitcoin source price"},
    )
    series = found["series"]["source_price"]
    assert series["index"] == "date"
    assert series["points"] == [{"date": "2026-09-01", "value": 10.0}, {"date": "2026-09-02", "value": 11.0}]


def test_realized_pnl_cumulative_mode_builds_full_14_day_window():
    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(15)]
    profit = [{"date": day.isoformat(), "value": i} for i, day in enumerate(days)]
    loss = [{"date": day.isoformat(), "value": i * 2} for i, day in enumerate(days)]
    cap = [{"date": day.isoformat(), "value": 100 + i} for i, day in enumerate(days)]
    metrics = map24.realized_pnl_metrics(profit, loss, cap, value_mode="cumulative")
    assert metrics["window_days"] == [day.isoformat() for day in days[1:]]
    assert metrics["profit_14d_usd"] == 14
    assert metrics["loss_14d_usd"] == 28
    assert metrics["loss_share_14d"] == 28 / 42
    assert metrics["realized_cap_start_usd"] == 101
    assert metrics["realized_cap_end_usd"] == 114
    assert metrics["missing"] == []


def test_background_rejects_records_observed_after_current_cutoff(tmp_path):
    registry = write_registry(tmp_path / "registry.json", [map24.ETF_FLOW])
    engine = DataEngine(tmp_path / "state", registry_path=registry)
    record = engine.ingest(
        map24.ETF_FLOW,
        {"schema": "btc_etf_flow_normalized@2.4.0", "metrics": {"summary_cn": "future", "missing": []}},
        observation_end_ms=T + 60_000,
        retrieved_at_ms=T + 60_000,
        scope={"asset": "BTC", "source": "farside", "table": "historical"},
    )
    read = engine.read(map24.ETF_FLOW, now_ms=T, usage="background", scope={"asset": "BTC", "source": "farside", "table": "historical"})
    background = map24.build_background([read], T)
    row = background["rows"]["allocation"]
    assert row["fact_state"] == "partial"
    assert any("OBSERVATION_AFTER_CUTOFF" in item for item in row["missing"])
    assert any("FIRST_SEEN_AFTER_CUTOFF" in item for item in row["missing"])
    assert row["summary_cn"] == "ETF配置记录存在，但当前用途资格未通过。"
    assert row["source_record_ids"] == [record["record_id"]]
