from copy import deepcopy
import json
from pathlib import Path

import pytest

from tools.astra_map_presentation_v241 import build_presentation
from tools.astra_map_v24 import _hash, build_map


T = 1_790_323_200_000


def data_record(
    record_id,
    product_id,
    values=None,
    *,
    observation_start_ms=None,
    observation_end_ms=T - 2_000,
    first_seen_at_ms=T - 1_000,
    published_at_ms=None,
    qualified_at_ms=T,
    can_use=True,
    status="USABLE",
    reasons=None,
):
    return {
        "schema": "astra_data_record@2.3.0",
        "record_id": record_id,
        "identity": {"product_id": product_id, "currency": "USD", "unit": "USD", "asset": "BTC"},
        "time": {
            "observation_start_ms": observation_start_ms,
            "observation_end_ms": observation_end_ms,
            "published_at_ms": published_at_ms,
            "first_seen_at_ms": first_seen_at_ms,
        },
        "content": {"values": dict(values or {})},
        "usage_qualification": {
            "can_use": can_use,
            "status": status,
            "qualified_at_ms": qualified_at_ms,
            "reason_codes": list(reasons or []),
        },
    }


def source_map(records, source_ids, *, map_cutoff=T, manifest_cutoff=T, row_cutoff=T - 100):
    mapping = {
        "schema": "btc_map@1",
        "map_id": "source-test-map",
        "cutoff_at_ms": map_cutoff,
        "source_manifest": {
            "source_records": records,
        },
        "background": {
            "financing": {
                "summary_cn": "融资来源混合。",
                "metrics": {"oi_24h_pct": 1.2},
                "cutoff_at_ms": row_cutoff,
                "source_record_ids": source_ids,
                "fact_state": "partial",
                "missing": [],
            }
        },
    }
    if manifest_cutoff != "__missing__":
        mapping["source_manifest"]["cutoff_at_ms"] = manifest_cutoff
    return mapping


def financing_row(mapping):
    return next(row for row in build_presentation(mapping)["background_rows"] if row["key"] == "financing")


def row_by_key(presentation, key):
    return next(row for row in presentation["background_rows"] if row["key"] == key)


def real_fixture():
    path = Path(__file__).parent / "fixtures/astra_map_presentation_real_v241.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["map"], data["snapshot"]


def test_background_source_clock_rows_do_not_mix_slow_and_fast_sources():
    oi = data_record(
        "oi-1",
        "binance.um.btcusdt.oi.native.v1",
        {"oi_24h_pct": 1.2},
        observation_start_ms=T - 3_700_000,
        observation_end_ms=T - 600_000,
        first_seen_at_ms=T - 590_000,
    )
    borrow = data_record(
        "borrow-1",
        "cex.usdt.borrow_rate.v1",
        {"borrow_apr_pct": 5.2},
        observation_start_ms=T - 86_400_000,
        observation_end_ms=T - 3_600_000,
        first_seen_at_ms=T - 3_590_000,
    )

    row = financing_row(source_map([oi, borrow], ["oi-1", "borrow-1"]))

    assert row["cutoff_at_ms"] == T
    assert row["product_cutoff_at_ms"] == T - 100
    assert row["first_seen_at_ms"] is None
    assert row["source_observation_end_ms"] is None
    clocks = {item["product_id"]: item for item in row["source_clock_rows"]}
    assert clocks["binance.um.btcusdt.oi.native.v1"]["observation_end_ms"] == T - 600_000
    assert clocks["cex.usdt.borrow_rate.v1"]["first_seen_at_ms"] == T - 3_590_000
    assert all(item["eligible"] is True for item in clocks.values())


def test_real_etf_background_uses_map_freeze_cutoff_and_keeps_product_observation_cutoff():
    mapping, snapshot = real_fixture()

    presentation = build_presentation(mapping, snapshot)
    allocation = row_by_key(presentation, "allocation")
    clock = allocation["source_clock_rows"][0]

    assert allocation["cutoff_at_ms"] == mapping["cutoff_at_ms"]
    assert allocation["cutoff_at_ms"] == 1_790_408_762_017
    assert allocation["product_cutoff_at_ms"] == mapping["background"]["allocation"]["cutoff_at_ms"]
    assert allocation["product_cutoff_at_ms"] == 1_790_366_400_000
    assert clock["product_id"] == "btc.etf.flow.daily.v1"
    assert clock["observation_end_ms"] == 1_790_366_400_000
    assert clock["eligible"] is False
    assert clock["reasons"] == ["source_manifest_cutoff_missing"]


@pytest.mark.parametrize(
    ("observation_end_ms", "first_seen_at_ms", "raw_field", "raw_value"),
    [
        (None, T - 1_000, "observation_end_ms", None),
        (0, T - 1_000, "observation_end_ms", 0),
        (-1, T - 1_000, "observation_end_ms", -1),
        (T - 2_000, None, "first_seen_at_ms", None),
        (T - 2_000, 0, "first_seen_at_ms", 0),
        (T - 2_000, -1, "first_seen_at_ms", -1),
    ],
)
def test_source_clock_rows_treat_null_zero_and_negative_required_clocks_as_missing(
    observation_end_ms,
    first_seen_at_ms,
    raw_field,
    raw_value,
):
    record = data_record(
        "bad-clock",
        "binance.um.btcusdt.funding.settled.v1",
        {"funding": 0.0001},
        observation_end_ms=observation_end_ms,
        first_seen_at_ms=first_seen_at_ms,
    )

    row = financing_row(source_map([record], ["bad-clock"]))
    clock = row["source_clock_rows"][0]

    assert clock["eligible"] is False
    assert clock[raw_field] == raw_value
    assert "source_clock_missing" in clock["reasons"]
    assert row["first_seen_at_ms"] is None
    assert row["source_observation_end_ms"] is None


def test_source_clock_rows_surface_bad_order_as_ineligible():
    missing_clock = data_record(
        "funding-missing-clock",
        "binance.um.btcusdt.funding.settled.v1",
        {"funding": 0.0001},
        observation_end_ms=None,
        first_seen_at_ms=None,
    )
    bad_clock = data_record(
        "borrow-bad-clock",
        "cex.usdt.borrow_rate.v1",
        {"borrow_apr_pct": 5.2},
        observation_end_ms=T - 1_000,
        first_seen_at_ms=T - 10_000,
    )

    row = financing_row(source_map([missing_clock, bad_clock], ["funding-missing-clock", "borrow-bad-clock"]))

    clocks = {item["record_id"]: item for item in row["source_clock_rows"]}
    assert clocks["funding-missing-clock"]["eligible"] is False
    assert "source_clock_missing" in clocks["funding-missing-clock"]["reasons"]
    assert clocks["borrow-bad-clock"]["eligible"] is False
    assert "observation_after_first_seen" in clocks["borrow-bad-clock"]["reasons"]


@pytest.mark.parametrize(
    ("manifest_cutoff", "expected_reason"),
    [
        ("__missing__", "source_manifest_cutoff_missing"),
        (T - 1, "source_manifest_cutoff_mismatch"),
    ],
)
def test_source_clock_rows_require_manifest_cutoff_to_match_map_cutoff(manifest_cutoff, expected_reason):
    record = data_record(
        "oi-1",
        "binance.um.btcusdt.oi.native.v1",
        {"oi_24h_pct": 1.2},
        observation_end_ms=T - 2_000,
        first_seen_at_ms=T - 1_000,
    )

    row = financing_row(source_map([record], ["oi-1"], manifest_cutoff=manifest_cutoff, row_cutoff=T - 86_400_000))
    clock = row["source_clock_rows"][0]

    assert row["cutoff_at_ms"] == T
    assert row["product_cutoff_at_ms"] == T - 86_400_000
    assert clock["eligible"] is False
    assert clock["observation_end_ms"] == T - 2_000
    assert clock["reasons"] == [expected_reason]


@pytest.mark.parametrize(
    ("observation_start_ms", "expected_eligible", "expected_reasons"),
    [
        (None, True, []),
        (0, False, ["observation_start_invalid"]),
        (-1, False, ["observation_start_invalid"]),
        (T - 500, False, ["observation_start_after_end"]),
        (T + 1, False, ["observation_start_after_cutoff", "observation_start_after_end"]),
    ],
)
def test_observation_start_is_optional_but_invalid_or_future_values_are_rejected(
    observation_start_ms,
    expected_eligible,
    expected_reasons,
):
    record = data_record(
        "oi-1",
        "binance.um.btcusdt.oi.native.v1",
        {"oi_24h_pct": 1.2},
        observation_start_ms=observation_start_ms,
        observation_end_ms=T - 1_000,
        first_seen_at_ms=T - 500,
    )

    row = financing_row(source_map([record], ["oi-1"]))
    clock = row["source_clock_rows"][0]

    assert clock["observation_start_ms"] == observation_start_ms
    assert clock["eligible"] is expected_eligible
    assert clock["reasons"] == expected_reasons


@pytest.mark.parametrize(
    ("published_at_ms", "qualified_at_ms", "expected_reason"),
    [
        (T + 1, T, "published_after_cutoff"),
        (None, T + 1, "qualified_after_cutoff"),
    ],
)
def test_source_clock_rows_reuse_published_and_qualified_future_guards(
    published_at_ms,
    qualified_at_ms,
    expected_reason,
):
    record = data_record(
        "oi-1",
        "binance.um.btcusdt.oi.native.v1",
        {"oi_24h_pct": 1.2},
        observation_end_ms=T - 1_000,
        first_seen_at_ms=T - 500,
        published_at_ms=published_at_ms,
        qualified_at_ms=qualified_at_ms,
    )

    row = financing_row(source_map([record], ["oi-1"]))
    clock = row["source_clock_rows"][0]

    assert clock["eligible"] is False
    assert clock["reasons"] == [expected_reason]


def test_source_manifest_dedupes_identical_record_ids_without_mutating_inputs():
    record = data_record("record-1", "demo.product", {"x": 1})
    background = {
        "source_records": [record, deepcopy(record)],
        "allocation": {"source_record_ids": ["record-1"], "metrics": {"x": 1}},
    }
    before = deepcopy(background)

    mapping = build_map(None, now_ms=T, background=background)

    assert background == before
    assert mapping["source_manifest"]["source_records"] == [{"record_id": "record-1"}]


def test_source_manifest_rejects_conflicting_records_with_the_same_record_id():
    first = data_record("record-1", "demo.product", {"x": 1})
    second = data_record("record-1", "demo.product", {"x": 2})
    background = {"source_records": [first, second]}

    with pytest.raises(ValueError, match="conflicting source records"):
        build_map(None, now_ms=T, background=background)


def supplied_manifest(records):
    body = {
        "schema": "btc_map_source_manifest@1",
        "cutoff_at_ms": T,
        "candidate_id": None,
        "source_records": records,
        "immutable": True,
    }
    body["manifest_hash"] = _hash(body)
    return body


@pytest.mark.parametrize("same_content_duplicate", [False, True])
def test_supplied_manifest_keeps_legacy_duplicates_unchanged_and_does_not_mutate(same_content_duplicate):
    record = data_record("record-1", "demo.product", {"x": 1})
    records = [record, deepcopy(record)] if same_content_duplicate else [record]
    manifest = supplied_manifest(records)
    background = {"source_manifest": manifest}
    before = deepcopy(background)

    mapping = build_map(None, now_ms=T, background=background)

    assert background == before
    assert mapping["source_manifest"] == manifest
    assert mapping["source_manifest"] is not manifest
    assert len(mapping["source_manifest"]["source_records"]) == len(records)
    assert mapping["source_manifest"]["manifest_hash"] == manifest["manifest_hash"]


def test_supplied_manifest_rejects_same_record_id_conflict_even_with_valid_hash():
    first = data_record("record-1", "demo.product", {"x": 1})
    second = data_record("record-1", "demo.product", {"x": 2})
    background = {"source_manifest": supplied_manifest([first, second])}
    before = deepcopy(background)

    with pytest.raises(ValueError, match="conflicting source records"):
        build_map(None, now_ms=T, background=background)

    assert background == before
