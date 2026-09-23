from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import astra_joint_comparisons as comparisons
import astra_joint_experiment as experiment
import astra_joint_inference as inference
import astra_joint_model as model
from astra_joint_contract import FEATURE_GROUPS, PROTOCOL
from astra_joint_sources import digest, save


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def candidate(
    row_id: str,
    *,
    year: int,
    split: str,
    side: str,
    episode_id: str,
    observation_kind: str = "cooldown",
    offset: int = 0,
    actual_width: float = 2000.0,
) -> dict[str, object]:
    side_sign = -1.0 if side == "put" else 1.0
    row: dict[str, object] = {
        "row_id": row_id,
        "observation_id": f"O-{row_id}",
        "event_family": "price_rebalance",
        "episode_id": episode_id,
        "active_episode_id": episode_id,
        "in_episode": "1",
        "observation_kind": observation_kind,
        "as_of_ms": 1_600_000_000_000 + offset * 60_000,
        "entry_ms": 1_600_000_060_000 + offset * 60_000,
        "expiry_ms": 1_600_000_000_000 + 12 * 3_600_000,
        "delivery_date": f"{year}-01-{(offset % 28) + 1:02d}",
        "split": split,
        "side": side,
        "target_width": 2000.0,
        "actual_width": actual_width,
        "short_strike": 80_000.0 if side == "put" else 82_000.0,
        "long_strike": 78_000.0 if side == "put" else 84_000.0,
        "short_creation_ms": 1_500_000_000_000,
        "long_creation_ms": 1_500_000_000_000,
        "entry_price": 81_000.0 + offset,
        "price_source": "unit",
        "option_source_sha256": "unit",
        "source_observation_hash": "unit",
        "side_sign": side_sign,
    }
    for index, name in enumerate(FEATURE_GROUPS["joint"]):
        row.setdefault(name, ((offset + index) % 19) / 100.0)
    row["side_sign"] = side_sign
    row["dte_hours"] = 12.0 - offset * 0.01
    row["short_distance_fraction"] = 0.02 + offset * 0.0001
    row["width_fraction"] = actual_width / float(row["entry_price"])
    return row


def hashed_artifact() -> dict[str, object]:
    artifact: dict[str, object] = {
        "schema": inference.MODEL_SCHEMA,
        "status": "available",
        "model_kind": "two_part_catboost",
        "model_version": "unit",
        "feature_group": "joint",
        "training_cutoff": "2022-12-31",
        "feature_names": ["side_sign"],
        "scope": {"target": "unit"},
        "catboost_classifier": {
            "role": "classifier",
            "scale": 1.0,
            "bias": 0.0,
            "oblivious_trees": [
                {"splits": [{"feature_index": 0, "border": 0.0, "missing_goes_right": False}], "leaf_values": [-2.0, 2.0]}
            ],
        },
        "catboost_regressor": {
            "role": "regressor",
            "scale": 1.0,
            "bias": 0.0,
            "oblivious_trees": [
                {"splits": [{"feature_index": 0, "border": 0.0, "missing_goes_right": False}], "leaf_values": [0.10, 0.20]}
            ],
        },
    }
    artifact["artifact_hash"] = model.stable_hash({key: value for key, value in artifact.items() if key != "artifact_hash"})
    return artifact


def selection_result() -> dict[str, object]:
    artifact = hashed_artifact()
    selected = {
        "status": "available",
        "selected": {"candidate_id": "unit", "model_family": "catboost", "feature_group": "joint"},
        "selected_artifact": artifact,
        "selected_artifact_hash": artifact["artifact_hash"],
    }
    return {
        "schema": model.MODEL_SELECTION_SCHEMA,
        "status": "available",
        "selection_note": "unit",
        "selected": selected["selected"],
        "selected_artifact": artifact,
        "selected_artifact_hash": artifact["artifact_hash"],
        "best_artifacts": {"statistical": selected, "mechanism": selected, "joint": selected},
        "sealed_primary_comparisons": [],
        "candidate_artifacts": [{"candidate_id": f"C-{index}", "artifact": artifact, "artifact_hash": artifact["artifact_hash"]} for index in range(33)],
        "experiment_context": {"candidate_count_expected": 33, "candidate_count_observed": 33},
    }


def make_research_root(tmp_path: Path) -> Path:
    root = tmp_path / "research"
    (root / "decisions").mkdir(parents=True)
    (root / "outcomes").mkdir()
    (root / "models").mkdir()
    (root / "protocol.json").write_text(json.dumps(PROTOCOL), encoding="utf-8")
    files: dict[str, str] = {}
    for year, split in [(2020, "train"), (2021, "train"), (2022, "selection"), (2023, "locked_test")]:
        rows = [
            candidate(f"{year}-cool-put", year=year, split=split, side="put", episode_id=f"E-{year}", observation_kind="cooldown", offset=1),
            candidate(f"{year}-cool-call", year=year, split=split, side="call", episode_id=f"E-{year}", observation_kind="cooldown", offset=1, actual_width=2500.0),
            candidate(f"{year}-shock-put", year=year, split=split, side="put", episode_id=f"E-{year}", observation_kind="shock", offset=0),
            candidate(f"{year}-shock-call", year=year, split=split, side="call", episode_id=f"E-{year}", observation_kind="shock", offset=0),
            candidate(f"{year}-plus15-put", year=year, split=split, side="put", episode_id=f"E-{year}", observation_kind="plus15", offset=15),
            candidate(f"{year}-plus15-call", year=year, split=split, side="call", episode_id=f"E-{year}", observation_kind="plus15", offset=15),
        ]
        path = root / "decisions" / f"candidates-{year}.csv"
        write_csv(path, rows)
        files[path.name] = digest(path)
        losses = []
        for row in rows:
            side = row["side"]
            kind = row["observation_kind"]
            loss = 0.0 if side == "put" else 0.25
            if kind == "shock":
                loss = 0.10 if side == "put" else 0.30
            if kind == "plus15":
                loss = 0.02 if side == "put" else 0.20
            width_btc = float(row["actual_width"]) / float(row["entry_price"])
            losses.append(
                {
                    "row_id": row["row_id"],
                    "settlement_price": 81_000.0,
                    "payout_btc": loss * width_btc,
                    "loss_normalized": loss,
                    "status": "settled",
                }
            )
        write_csv(root / "outcomes" / f"outcomes-{year}.csv", losses)
    (root / "decisions" / "candidate_seal.json").write_text(json.dumps({"files": files}, ensure_ascii=False), encoding="utf-8")
    selection = selection_result()
    result_path = root / "models" / "selection_result.json"
    result_path.write_text(json.dumps(selection, ensure_ascii=False), encoding="utf-8")
    identity_rows = experiment.read_candidate_identity_rows(root, experiment.LOCKED_TEST_YEARS)
    seal = experiment.build_selection_seal(root, selection, result_path, identity_rows)
    save(root / "models" / "selection_seal.json", seal, immutable=True)
    save(root / "models" / "locked_test_report.json", {"schema": "unit"}, immutable=True)
    return root


def test_supplementary_comparisons_write_model_baselines_and_corrected_primary(tmp_path: Path) -> None:
    root = make_research_root(tmp_path)

    payload = comparisons.run_supplementary_comparisons(root)

    assert payload["schema"] == comparisons.SUPPLEMENTARY_SCHEMA
    assert payload["coverage"]["put_call_pairs"] == 3
    assert payload["coverage"]["same_actual_width_pairs"] == 2
    assert payload["coverage"]["different_actual_width_pairs"] == 1
    assert "joint_minus_statistical" in payload["corrected_primary_comparisons"]["comparisons"]
    joint = payload["feature_group_results"]["joint"]
    assert joint["selected_side_counts"]["put_credit"] == 3
    assert joint["standalone"]["model_choice"]["by_credit_scenario"]["0.10"]["win_rate"] is not None
    assert joint["comparisons"]["model_vs_fixed_call"]["actual_payout_btc_saving"]["seven_day_block"]["status"] == "available"
    assert joint["same_actual_width_subset"]["records"] == 2
    assert joint["sample_records"]
    assert (root / "models" / "supplementary_comparisons.json").exists()
    assert (root / "models" / "supplementary_comparisons.md").read_text(encoding="utf-8").startswith("# Astra 联合研究 v1 补充对照")


def test_supplementary_comparisons_refuse_to_overwrite(tmp_path: Path) -> None:
    root = make_research_root(tmp_path)
    comparisons.run_supplementary_comparisons(root)

    with pytest.raises(comparisons.SupplementaryComparisonError, match="already exist"):
        comparisons.run_supplementary_comparisons(root)


def test_supplementary_comparisons_do_not_generate_missing_locked_outcomes(tmp_path: Path) -> None:
    root = make_research_root(tmp_path)
    (root / "outcomes" / "outcomes-2023.csv").unlink()

    with pytest.raises(comparisons.SupplementaryComparisonError, match="will not be generated"):
        comparisons.run_supplementary_comparisons(root)


def test_episode_transition_keeps_missing_pair_coverage() -> None:
    rows = [
        {**candidate("shock-put", year=2023, split="locked_test", side="put", episode_id="E", observation_kind="shock", offset=0), "loss_normalized": 0.2, "payout_btc": 0.004},
        {**candidate("plus15-put", year=2023, split="locked_test", side="put", episode_id="E", observation_kind="plus15", offset=15), "loss_normalized": 0.1, "payout_btc": 0.002},
        {**candidate("cool-call", year=2023, split="locked_test", side="call", episode_id="E2", observation_kind="cooldown", offset=1), "loss_normalized": 0.0, "payout_btc": 0.0},
    ]

    result = comparisons.episode_transition_comparisons(rows)

    assert result["transitions"]["shock_to_plus15"]["paired_records"] == 1
    assert result["transitions"]["shock_to_plus15"]["all"]["payout_btc_saving_vs_shock"]["seven_day_block"]["status"] == "available"
    assert result["transitions"]["shock_to_cooldown"]["missing_counts"]["missing_cooldown"] == 1
    assert result["transitions"]["shock_to_cooldown"]["missing_counts"]["missing_shock"] == 1


def value_item(side: str, net: float) -> dict[str, object]:
    return {
        "side": side,
        "row_id": side,
        "actual_width": 2000.0,
        "entry_price": 80_000.0,
        "dte_hours": 12.0,
        "width_btc": 0.025,
        "predicted_loss_normalized": 0.1,
        "predicted_btc_payout": 0.0025,
        "actual_loss_normalized": 0.0,
        "actual_payout_btc": 0.0,
        "net_btc_by_credit": {"0.10": net},
        "net_win_by_credit": {"0.10": 1.0 if net > comparisons.EPSILON else 0.0},
        "net_breakeven_by_credit": {"0.10": 1.0 if abs(net) <= comparisons.EPSILON else 0.0},
    }


def comparison_record(put_net: float, call_net: float, *, model_choice_side: str = "equal") -> dict[str, object]:
    put = value_item("put_credit", put_net)
    call = value_item("call_credit", call_net)
    equal = comparisons.averaged_values(put, call, [0.10])
    if model_choice_side == "put":
        choice = put
        selected_side = "put_credit"
    elif model_choice_side == "call":
        choice = call
        selected_side = "call_credit"
    else:
        choice = equal
        selected_side = "tie_equal_put_call"
    return {
        "pair_key": ["unit"],
        "delivery_date": "2023-01-01",
        "same_actual_width": True,
        "selected_side": selected_side,
        "selection_tie": selected_side == "tie_equal_put_call",
        "model_choice": choice,
        "baselines": {
            "fixed_put": put,
            "fixed_call": call,
            "equal_put_call_risk": equal,
        },
    }


def test_equal_put_call_win_rate_uses_side_indicators_not_average_net_sign() -> None:
    records = [comparison_record(0.001, -0.001)]

    summary = comparisons.summarize_model_records(records, [0.10])

    equal_summary = summary["standalone"]["equal_put_call_risk"]["by_credit_scenario"]["0.10"]
    model_summary = summary["standalone"]["model_choice"]["by_credit_scenario"]["0.10"]
    assert equal_summary["mean"] == pytest.approx(0.0)
    assert equal_summary["win_rate"] == pytest.approx(0.5)
    assert equal_summary["breakeven_rate"] == pytest.approx(0.0)
    assert model_summary["win_rate"] == pytest.approx(0.5)


def test_equal_put_call_tail_uses_single_side_distribution_not_average_net() -> None:
    records = [comparison_record(0.001, -0.009)]

    summary = comparisons.summarize_model_records(records, [0.10])

    equal_summary = summary["standalone"]["equal_put_call_risk"]["by_credit_scenario"]["0.10"]
    assert equal_summary["mean"] == pytest.approx(-0.004)
    assert equal_summary["win_rate"] == pytest.approx(0.5)
    assert equal_summary["tail_loss_p95_abs"] == pytest.approx(0.009)


def test_net_breakeven_is_not_counted_as_win() -> None:
    records = [comparison_record(0.0, 0.002, model_choice_side="put")]

    summary = comparisons.summarize_model_records(records, [0.10])

    put_summary = summary["standalone"]["fixed_put"]["by_credit_scenario"]["0.10"]
    assert put_summary["mean"] == pytest.approx(0.0)
    assert put_summary["win_rate"] == pytest.approx(0.0)
    assert put_summary["breakeven_rate"] == pytest.approx(1.0)


def test_holm_credit_key_keeps_decimal_scenario() -> None:
    feature_group_results = {
        "joint": {
            "comparisons": {
                "model_vs_fixed_put": {
                    "by_credit_scenario": {
                        "0.10": {"seven_day_block": {"p_value_two_sided_centered": 0.03}}
                    }
                }
            },
            "same_actual_width_subset": {"comparisons": {}},
        }
    }
    key = comparisons.holm_path("joint", "all_pairs", "model_vs_fixed_put", "net_credit", "0.10")

    comparisons.apply_holm(feature_group_results, {key: 0.03})

    scenarios = feature_group_results["joint"]["comparisons"]["model_vs_fixed_put"]["by_credit_scenario"]
    assert scenarios["0.10"]["seven_day_block"]["holm_adjusted_p"] == pytest.approx(0.03)
    assert "0" not in scenarios


def test_supplementary_predictions_are_cached_by_artifact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = make_research_root(tmp_path)
    calls: list[list[str]] = []

    def fake_predict_rows(artifact: dict[str, object], rows: list[dict[str, object]]) -> list[dict[str, float]]:
        calls.append([str(row.get("row_id")) for row in rows])
        return [{"expected_loss_normalized": 0.05 if row.get("side") == "put" else 0.20} for row in rows]

    monkeypatch.setattr(comparisons.inference, "predict_rows", fake_predict_rows)

    comparisons.run_supplementary_comparisons(root)

    assert len(calls) == 1
    assert set(calls[0]) == {
        "2023-cool-put",
        "2023-cool-call",
        "2023-shock-put",
        "2023-shock-call",
        "2023-plus15-put",
        "2023-plus15-call",
    }
