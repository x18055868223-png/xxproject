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

import astra_joint_experiment as experiment
import astra_joint_inference as inference
import astra_joint_model as model
from astra_joint_contract import FEATURE_GROUPS, PROTOCOL
from astra_joint_sources import digest


def base_candidate(
    row_id: str,
    *,
    year: int,
    split: str,
    side: str,
    episode_id: str,
    target_width: float = 2000.0,
) -> dict[str, object]:
    day = int(row_id.split("-")[-1]) if row_id.split("-")[-1].isdigit() else 1
    row: dict[str, object] = {
        "row_id": row_id,
        "observation_id": f"O-{row_id}",
        "event_family": "price_rebalance",
        "episode_id": episode_id,
        "active_episode_id": episode_id,
        "in_episode": "1",
        "observation_kind": "cooldown",
        "as_of_ms": 1_600_000_000_000 + day * 60_000,
        "entry_ms": 1_600_000_060_000 + day * 60_000,
        "expiry_ms": 1_600_000_000_000 + day * 86_400_000,
        "delivery_date": f"{year}-01-{(day % 28) + 1:02d}",
        "split": split,
        "side": side,
        "target_width": target_width,
        "actual_width": 2000.0,
        "short_strike": 80_000.0,
        "long_strike": 78_000.0 if side == "put" else 82_000.0,
        "short_name": f"BTC-{year}-SHORT-{row_id}",
        "long_name": f"BTC-{year}-LONG-{row_id}",
        "short_creation_ms": 1_500_000_000_000,
        "long_creation_ms": 1_500_000_000_000,
        "entry_price": 81_000.0 + day,
        "price_source": "unit",
        "option_source_sha256": "unit",
        "source_observation_hash": "unit",
    }
    for index, name in enumerate(FEATURE_GROUPS["joint"]):
        if name == "side_sign":
            row[name] = -1.0 if side == "put" else 1.0
        elif name == "dte_hours":
            row[name] = 12.0
        elif name == "short_distance_fraction":
            row[name] = 0.01 + day * 0.0001
        elif name == "width_fraction":
            row[name] = 0.02
        else:
            row[name] = ((day + index) % 17) / 100.0
    return row


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


def make_research_root(tmp_path: Path) -> Path:
    root = tmp_path / "research"
    (root / "decisions").mkdir(parents=True)
    (root / "outcomes").mkdir()
    (root / "protocol.json").write_text(json.dumps(PROTOCOL), encoding="utf-8")
    files: dict[str, str] = {}
    for year, split in [(2020, "train"), (2021, "train"), (2022, "selection"), (2023, "locked_test")]:
        rows = []
        for index in range(1, 5):
            for side in ("put", "call"):
                rows.append(base_candidate(f"{year}-{side}-{index}", year=year, split=split, side=side, episode_id=f"E-{year}-{index}"))
        path = root / "decisions" / f"candidates-{year}.csv"
        write_csv(path, rows)
        files[path.name] = digest(path)
        if year != 2023:
            write_csv(
                root / "outcomes" / f"outcomes-{year}.csv",
                [
                    {
                        "row_id": row["row_id"],
                        "settlement_price": 81_000.0,
                        "payout_btc": 0.0,
                        "loss_normalized": 0.03 if index % 3 == 0 else 0.0,
                        "status": "settled",
                    }
                    for index, row in enumerate(rows)
                ],
            )
    (root / "decisions" / "candidate_seal.json").write_text(
        json.dumps({"schema": "unit", "files": files}, ensure_ascii=False),
        encoding="utf-8",
    )
    return root


def fake_selection_result() -> dict[str, object]:
    artifact = {
        "schema": inference.MODEL_SCHEMA,
        "status": "available",
        "model_kind": "two_part_gam",
        "model_version": "unit",
        "feature_group": "unit",
        "training_cutoff": "2022-12-31",
        "scope": {"target": "unit"},
        "design_size": 2,
        "preprocess": {
            "version": "unit",
            "numeric_features": [
                {
                    "name": "side_sign",
                    "median": -1.0,
                    "mean": 0.0,
                    "scale": 1.0,
                    "spline": {"type": "constant"},
                }
            ],
        },
        "logistic_model": {"link": "logit", "intercept": 0.0, "coef": [0.0, 0.0]},
        "gamma_model": {"link": "log", "intercept": -2.302585092994046, "coef": [0.0, 0.0]},
    }
    artifact["artifact_hash"] = model.stable_hash({key: value for key, value in artifact.items() if key != "artifact_hash"})
    selected = {
        "status": "available",
        "selected": {"candidate_id": "unit", "model_family": "gam", "feature_group": "joint"},
        "selected_artifact": artifact,
        "selected_artifact_hash": artifact["artifact_hash"],
        "selection_note": "unit",
    }
    return {
        "schema": model.MODEL_SELECTION_SCHEMA,
        "status": "available",
        "selection_note": "unit",
        "selected": selected["selected"],
        "selected_artifact": artifact,
        "selected_artifact_hash": artifact["artifact_hash"],
        "best_artifacts": {
            "statistical": selected,
            "mechanism": selected,
            "joint": selected,
        },
        "sealed_primary_comparisons": [],
        "candidate_artifacts": [{"candidate_id": f"C-{index}", "artifact": artifact, "artifact_hash": artifact["artifact_hash"]} for index in range(33)],
    }


def write_selection_files(root: Path, selection: dict[str, object]) -> None:
    models = root / "models"
    models.mkdir(exist_ok=True)
    selection["experiment_context"] = {
        "candidate_count_expected": 33,
        "candidate_count_observed": len(selection.get("candidate_artifacts") or []),
    }
    result_path = models / "selection_result.json"
    result_path.write_text(json.dumps(selection, ensure_ascii=False), encoding="utf-8")
    identity_rows = experiment.read_candidate_identity_rows(root, experiment.LOCKED_TEST_YEARS)
    seal = experiment.build_selection_seal(root, selection, result_path, identity_rows)
    (models / "selection_seal.json").write_text(json.dumps(seal, ensure_ascii=False), encoding="utf-8")


def test_run_train_uses_2023_only_as_identity_purge(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = make_research_root(tmp_path)
    seen: dict[str, object] = {}

    def fake_train_select(rows: list[dict[str, object]], **kwargs: object) -> dict[str, object]:
        locked_rows = [row for row in rows if row.get("split") == "locked_test"]
        assert locked_rows
        assert all("loss_normalized" not in row and "payout_btc" not in row for row in locked_rows)
        seen["kwargs"] = kwargs
        result = fake_selection_result()
        result["experiment_context"] = {}
        return result

    monkeypatch.setattr(experiment.model, "train_select", fake_train_select)

    seal = experiment.run_train(root, include_catboost=True)

    assert seal["schema"] == experiment.SELECTION_SEAL_SCHEMA
    assert seal["data"]["locked_test_targets_read"] is False
    assert seal["data"]["label_years"] == [2020, 2021, 2022]
    assert seal["data"]["locked_test_identity_rows"] == 8
    assert seen["kwargs"]["holdout_purge_splits"] == {"selection", "locked_test"}
    assert seal["candidate_count_expected"] == 33
    assert seal["candidate_count_observed"] == 33
    assert (root / "models" / "selection_result.json").exists()
    assert (root / "models" / "selection_seal.json").exists()


def test_run_test_is_the_stage_that_opens_2023_outcomes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = make_research_root(tmp_path)
    selection = fake_selection_result()
    write_selection_files(root, selection)
    called: dict[str, object] = {}

    def fake_build_outcomes(root_arg: Path, years: list[int]) -> dict[str, object]:
        called["years"] = list(years)
        write_csv(
            Path(root_arg) / "outcomes" / "outcomes-2023.csv",
            [
                {
                    "row_id": f"2023-{side}-{index}",
                    "settlement_price": 81_000.0,
                    "payout_btc": 0.0,
                    "loss_normalized": 0.02 if side == "call" else 0.0,
                    "status": "settled",
                }
                for index in range(1, 5)
                for side in ("put", "call")
            ],
        )
        return {"2023": {"rows": 8, "missing": 0}}

    monkeypatch.setattr(experiment.dataset, "build_outcomes", fake_build_outcomes)

    report = experiment.run_test(root, credit_scenarios=[0.10])

    assert called["years"] == [2023]
    assert report["schema"] == experiment.LOCKED_TEST_REPORT_SCHEMA
    assert report["coverage"]["locked_rows"] == 8
    assert "joint_minus_statistical" in report["primary_comparisons"]
    assert report["primary_comparisons"]["joint_minus_statistical"]["holm_adjusted_p"] is not None
    assert report["baselines"]["by_credit_scenario"]["0.10"]["fixed_put"]["win_rate"] == 1.0
    assert (root / "models" / "locked_test_report.json").exists()


def test_run_test_validates_selection_before_opening_2023_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = make_research_root(tmp_path)
    selection = fake_selection_result()
    selection["candidate_artifacts"][0]["artifact"]["scope"]["tampered_after_training"] = True
    write_selection_files(root, selection)

    def fail_if_called(root_arg: Path, years: list[int]) -> dict[str, object]:
        raise AssertionError("locked-test outcomes must not be opened before selection validation succeeds")

    monkeypatch.setattr(experiment.dataset, "build_outcomes", fail_if_called)

    with pytest.raises(experiment.ExperimentStateError, match="candidate_artifact_embedded_hash_mismatch"):
        experiment.run_test(root, credit_scenarios=[0.10])


def test_training_lock_blocks_concurrent_selection(tmp_path: Path) -> None:
    root = make_research_root(tmp_path)
    models = root / "models"
    models.mkdir(exist_ok=True)
    (models / "training.lock").write_text("busy", encoding="utf-8")

    with pytest.raises(experiment.ExperimentStateError, match="training lock already exists"):
        experiment.run_train(root, include_catboost=False)


def test_payoff_summary_uses_delivery_date_equal_weighting_as_primary() -> None:
    rows = []
    for index in range(4):
        rows.append({"row_id": f"d1-{index}", "delivery_date": "2023-01-01", "loss_normalized": 0.0})
    rows.append({"row_id": "d2-loss", "delivery_date": "2023-01-02", "loss_normalized": 1.0})

    summary = experiment.payoff_summary(rows, 0.10)

    assert summary["primary_weighting"] == "date_equal"
    assert summary["mean_net_normalized"] == pytest.approx(-0.4)
    assert summary["raw"]["mean_net"] == pytest.approx(-0.1)


def test_clock_and_observation_buckets_handle_string_booleans() -> None:
    inside = {"observation_kind": "clock", "in_episode": "True"}
    outside = {"observation_kind": "clock", "in_episode": "False"}
    shock = {"observation_kind": "shock", "in_episode": "1"}

    assert experiment.event_clock_bucket(inside) == "clock_inside_episode"
    assert experiment.event_clock_bucket(outside) == "clock_outside_episode"
    assert experiment.observation_kind_bucket(shock) == "shock"


def test_side_choice_reports_btc_ranking_and_same_width_sensitivity() -> None:
    put = base_candidate("pair-put-1", year=2023, split="locked_test", side="put", episode_id="PAIR")
    call = base_candidate("pair-call-1", year=2023, split="locked_test", side="call", episode_id="PAIR")
    call["actual_width"] = 2500.0
    put["loss_normalized"] = 0.0
    put["payout_btc"] = 0.0
    call["loss_normalized"] = 0.2
    call["payout_btc"] = 0.2 * (float(call["actual_width"]) / float(call["entry_price"]))
    artifact = fake_selection_result()["selected_artifact"]

    summary = experiment.model_side_choice_summary(artifact, [put, call], credit_scenarios=[0.10])

    assert summary["predicted_btc_ranking"]["records"] == 1
    assert summary["predicted_normalized_ranking"]["records"] == 1
    assert summary["same_actual_width_pairs"]["records"] == 0
    assert summary["coverage"]["different_actual_width_pairs"] == 1


def test_run_report_summarizes_without_locked_test(tmp_path: Path) -> None:
    root = make_research_root(tmp_path)
    models = root / "models"
    models.mkdir()
    (models / "selection_result.json").write_text(json.dumps(fake_selection_result(), ensure_ascii=False), encoding="utf-8")
    (models / "selection_seal.json").write_text(json.dumps({"schema": experiment.SELECTION_SEAL_SCHEMA}, ensure_ascii=False), encoding="utf-8")

    payload = experiment.run_report(root)

    assert payload["schema"] == experiment.SUMMARY_REPORT_SCHEMA
    assert payload["locked_test_status"] == "not_run"
    assert (models / "experiment_summary.md").read_text(encoding="utf-8").startswith("# Astra 联合研究 v1")
