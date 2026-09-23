from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import astra_joint_inference as inference
import astra_joint_model as model
from astra_joint_contract import FEATURE_GROUPS, PROTOCOL, SEED


def base_row(day: int, *, split: str | None = None, side: str = "put_credit", target: float = 0.0) -> dict[str, object]:
    row: dict[str, object] = {
        "row_id": f"R-{day}-{side}",
        "event_family": "unit",
        "episode_id": f"E-{day}",
        "observation_kind": "cooldown",
        "as_of_ms": day * 86_400_000,
        "entry_ms": day * 86_400_000 + 60_000,
        "expiry_ms": day * 86_400_000 + 12 * 3_600_000,
        "delivery_date": f"2021-07-{(day % 28) + 1:02d}",
        "side": side,
        "target_width": 2000.0,
        "actual_width": 2000.0,
        "entry_price": 100_000.0 + day,
        "loss_normalized": target,
        "dte_hours": 12.0,
        "short_distance_fraction": 0.01 + day * 0.00001,
        "width_fraction": 0.02,
        "side_sign": -1 if side == "put_credit" else 1,
    }
    if split is not None:
        row["split"] = split
    for index, name in enumerate(set(FEATURE_GROUPS["joint"]) - set(row)):
        row[name] = ((day + index) % 31) / 100.0
    return row


def synthetic_rows(train_days: int = 120, selection_days: int = 45) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for day in range(train_days):
        delivery_date = f"2021-{(day // 28) + 1:02d}-{(day % 28) + 1:02d}"
        for side in ("put_credit", "call_credit"):
            target = 0.035 + (day % 7) * 0.004 if day % 3 == 0 else 0.0
            row = base_row(day, split="train", side=side, target=target)
            row["delivery_date"] = delivery_date
            rows.append(row)
    for offset in range(selection_days):
        day = train_days + offset
        delivery_date = f"2022-{(offset // 28) + 1:02d}-{(offset % 28) + 1:02d}"
        for side in ("put_credit", "call_credit"):
            target = 0.025 + (offset % 5) * 0.003 if offset % 4 == 0 else 0.0
            row = base_row(day, split="selection", side=side, target=target)
            row["delivery_date"] = delivery_date
            rows.append(row)
    return rows


def test_split_derives_protocol_years_when_explicit_split_missing() -> None:
    assert model.split_for_row({"delivery_date": "2020-01-10"}) == "train"
    assert model.split_for_row({"delivery_date": "2022-01-10"}) == "selection"
    assert model.split_for_row({"delivery_date": "2023-01-10"}) == "locked_test"
    assert model.split_for_row({"delivery_date": "2025-01-10"}) == "development"


def test_filter_rows_keeps_primary_width_and_valid_targets_only() -> None:
    valid = base_row(1, split="train", target=0.0)
    wrong_width = dict(valid, row_id="wrong-width", target_width=1500.0)
    no_target = dict(valid, row_id="no-target", loss_normalized="")
    no_side = dict(valid, row_id="no-side", side="straddle")

    rows = model.filter_rows([valid, wrong_width, no_target, no_side], split_names={"train"})

    assert [row["row_id"] for row in rows] == [valid["row_id"]]


def test_put_call_side_names_remain_compatible_with_credit_names() -> None:
    put = base_row(1, split="train", side="put", target=0.0)
    call = base_row(2, split="train", side="call", target=0.0)
    put.pop("side_sign")
    call.pop("side_sign")

    assert model.canonical_side(put["side"]) == "put_credit"
    assert model.canonical_side(call["side"]) == "call_credit"
    assert model.side_sign(put) == -1
    assert model.side_sign(call) == 1


def test_boundary_purge_removes_episode_and_expiry_overlap() -> None:
    train_keep = base_row(1, split="train", target=0.0)
    train_episode_overlap = dict(base_row(2, split="train", target=0.0), episode_id="shared-episode")
    train_expiry_overlap = dict(base_row(3, split="train", target=0.0), expiry_ms=999_000)
    selection_a = dict(base_row(4, split="selection", target=0.0), episode_id="shared-episode", expiry_ms=777_000)
    selection_b = dict(base_row(5, split="selection", target=0.0), episode_id="other", expiry_ms=999_000)

    kept, report = model.training_rows_for_selection(
        [train_keep, train_episode_overlap, train_expiry_overlap, selection_a, selection_b],
        holdout_splits={"selection"},
    )

    assert [row["row_id"] for row in kept] == [train_keep["row_id"]]
    assert report["removed_rows"] == 2
    assert report["removed_by_key_type"]["episode"] == 1
    assert report["removed_by_key_type"]["expiry_ms"] == 1
    assert report["holdout_splits"] == ["selection"]


def test_boundary_purge_can_use_locked_test_identity_without_test_target() -> None:
    train_keep = base_row(1, split="train", target=0.0)
    train_overlap = dict(base_row(2, split="train", target=0.0), episode_id="shared-locked")
    locked_boundary = dict(base_row(3, split="locked_test", target=0.25), episode_id="shared-locked")
    locked_boundary.pop("loss_normalized")

    kept, report = model.training_rows_for_selection(
        [train_keep, train_overlap, locked_boundary],
        holdout_splits={"locked_test"},
    )

    assert [row["row_id"] for row in kept] == [train_keep["row_id"]]
    assert report["removed_rows"] == 1
    assert report["holdout_splits"] == ["locked_test"]


def test_catboost_internal_validation_keeps_minimum_training_days() -> None:
    rows = []
    for day in range(125):
        row = base_row(day, split="train", target=0.01 if day % 3 == 0 else 0.0)
        row["delivery_date"] = f"2021-{(day // 28) + 1:02d}-{(day % 28) + 1:02d}"
        rows.append(row)

    train_indexes, eval_indexes = model._internal_catboost_validation_indexes(rows)
    train_days = {rows[index]["delivery_date"] for index in train_indexes}
    eval_days = {rows[index]["delivery_date"] for index in eval_indexes}

    assert len(train_days) >= model.PROTOCOL["min_training_delivery_days"]
    assert eval_days
    assert not (train_days & eval_days)


def test_training_qualification_requires_delivery_days_and_positive_days() -> None:
    insufficient = [base_row(day, split="train", target=0.0) for day in range(20)]
    assert model.training_qualification(insufficient)["status"] == "insufficient"

    enough = []
    for day in range(120):
        row = base_row(day, split="train", target=0.01 if day < 35 else 0.0)
        row["delivery_date"] = f"2021-{(day // 28) + 1:02d}-{(day % 28) + 1:02d}"
        enough.append(row)

    qualification = model.training_qualification(enough)

    assert qualification["status"] == "available"
    assert qualification["delivery_dates"] >= PROTOCOL["min_training_delivery_days"]
    assert qualification["positive_delivery_dates"] >= PROTOCOL["min_positive_delivery_days"]


def test_equal_delivery_day_weights_give_each_day_same_total_weight() -> None:
    rows = [
        dict(base_row(1), delivery_date="2021-01-01"),
        dict(base_row(2), delivery_date="2021-01-01"),
        dict(base_row(3), delivery_date="2021-01-02"),
    ]
    weights = model.equal_delivery_day_weights(rows)
    totals: dict[str, float] = {}
    for row, weight in zip(rows, weights):
        totals[str(row["delivery_date"])] = totals.get(str(row["delivery_date"]), 0.0) + weight

    assert totals["2021-01-01"] == pytest.approx(totals["2021-01-02"])
    assert sum(weights) == pytest.approx(len(rows))


def test_configure_training_threads_caps_existing_high_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMP_NUM_THREADS", "64")
    monkeypatch.setenv("MKL_NUM_THREADS", "2")

    model.configure_training_threads()

    assert int(model.os.environ["OMP_NUM_THREADS"]) == PROTOCOL["max_training_threads"]
    assert model.os.environ["MKL_NUM_THREADS"] == "2"


def test_insufficient_fit_returns_explicit_artifact_without_training_dependency() -> None:
    result = model.fit_two_part_gam(
        [base_row(1, split="train", target=0.0)],
        feature_group="statistical",
        logistic_c=1.0,
        gamma_alpha=1.0,
    )

    if result["status"] == "insufficient":
        assert result["artifact"]["model_kind"] == "insufficient"
        assert "insufficient_training_delivery_days" in result["qualification"]["reasons"]
    else:
        pytest.fail("single-row fit should not be trainable")


def test_fit_reports_dependency_unavailable_after_data_qualification(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = synthetic_rows(train_days=120, selection_days=0)

    def fail_imports() -> object:
        raise model.MissingModelDependency("unit missing sklearn")

    monkeypatch.setattr(model, "_require_sklearn", fail_imports)

    result = model.fit_two_part_gam(
        rows,
        feature_group="statistical",
        logistic_c=1.0,
        gamma_alpha=1.0,
    )

    assert result["status"] == "dependency_unavailable"
    assert result["qualification"]["status"] == "available"
    assert result["artifact"] is None


def test_catboost_json_export_portable_tree_is_stdlib_predictable() -> None:
    classifier_json = {
        "features_info": {
            "float_features": [
                {"flat_feature_index": 0, "nan_value_treatment": "AsFalse"},
                {"flat_feature_index": 1, "nan_value_treatment": "AsFalse"},
            ]
        },
        "scale_and_bias": [1.0, [0.0]],
        "oblivious_trees": [
            {
                "splits": [{"split_type": "FloatFeature", "float_feature_index": 0, "border": 0.0}],
                "leaf_values": [-1.0, 1.0],
            }
        ],
    }
    regressor_json = {
        "features_info": {"float_features": [{"flat_feature_index": 0}, {"flat_feature_index": 1}]},
        "scale_and_bias": [1.0, [0.0]],
        "oblivious_trees": [{"splits": [], "leaf_values": [0.25]}],
    }
    artifact = {
        "schema": model.MODEL_SCHEMA,
        "status": "available",
        "model_kind": "two_part_catboost",
        "model_version": "unit",
        "feature_group": "unit",
        "feature_names": ["ret_15", "side_sign"],
        "catboost_classifier": model._portable_catboost_from_json(classifier_json, ["ret_15", "side_sign"], role="classifier"),
        "catboost_regressor": model._portable_catboost_from_json(regressor_json, ["ret_15", "side_sign"], role="regressor"),
    }

    prediction = inference.predict_row(artifact, {"side": "put", "ret_15": 0.1})

    assert prediction["probability_positive"] == pytest.approx(1.0 / (1.0 + math.exp(-1.0)))
    assert prediction["conditional_positive_loss"] == pytest.approx(0.25)


def test_real_catboost_export_matches_native_estimators_when_available() -> None:
    np = pytest.importorskip("numpy")
    catboost = pytest.importorskip("catboost")
    rows = []
    feature_names = ("ret_15", "side_sign")
    for index in range(80):
        side = "put_credit" if index % 2 == 0 else "call_credit"
        row = base_row(index, split="train", side=side, target=0.04 + index * 0.0005 if index % 3 == 0 else 0.0)
        row["ret_15"] = (index % 9 - 4) / 10.0
        rows.append(row)
    x = np.asarray([[row["ret_15"], row["side_sign"]] for row in rows], dtype=float)
    y = np.asarray([row["loss_normalized"] for row in rows], dtype=float)
    classifier = catboost.CatBoostClassifier(
        depth=3,
        learning_rate=0.03,
        iterations=30,
        loss_function="Logloss",
        random_seed=SEED,
        verbose=False,
        allow_writing_files=False,
        thread_count=1,
    )
    regressor = catboost.CatBoostRegressor(
        depth=3,
        learning_rate=0.03,
        iterations=30,
        loss_function="RMSE",
        random_seed=SEED,
        verbose=False,
        allow_writing_files=False,
        thread_count=1,
    )
    classifier.fit(x, (y > 0).astype(int))
    regressor.fit(x[y > 0], y[y > 0])
    artifact = model.export_catboost_pair(
        classifier=classifier,
        regressor=regressor,
        feature_names_=feature_names,
        feature_group="unit",
        depth=3,
        training_rows=rows,
        qualification={"status": "available"},
        purge_report={},
    )
    test_rows = [
        {"row_id": "T1", "side": "put", "ret_15": 0.2},
        {"row_id": "T2", "side": "call", "ret_15": -0.2},
        {"row_id": "T3", "side": "put", "ret_15": None},
    ]

    for row in test_rows:
        native_x = np.asarray(
            [[np.nan if row.get("ret_15") is None else row["ret_15"], inference.side_sign_from_row(row)]],
            dtype=float,
        )
        native_probability = float(classifier.predict_proba(native_x)[0, 1])
        native_loss = max(model.EPSILON, float(regressor.predict(native_x)[0]))
        exported = inference.predict_row(artifact, row)
        assert exported["probability_positive"] == pytest.approx(native_probability, abs=1e-8, rel=1e-8)
        assert exported["conditional_positive_loss"] == pytest.approx(native_loss, abs=1e-8, rel=1e-8)


def test_catboost_challenger_path_returns_exportable_candidate_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("numpy")
    pytest.importorskip("catboost")
    rows = synthetic_rows(train_days=120, selection_days=20)
    monkeypatch.setitem(model.PROTOCOL, "catboost_max_iterations", 30)
    monkeypatch.setitem(model.PROTOCOL, "catboost_depths", [3])

    candidates = model.train_catboost_challengers(rows, feature_groups=("statistical",), holdout_splits={"selection"})

    assert len(candidates) == 1
    assert candidates[0]["status"] == "available"
    assert candidates[0]["artifact"]["schema"] == model.MODEL_SCHEMA
    assert candidates[0]["artifact"]["model_kind"] == "two_part_catboost"
    assert candidates[0]["selection_metrics"]["status"] == "available"
    internal = candidates[0]["catboost_internal_validation"]
    assert internal["full_refit_rows"] == candidates[0]["qualification"]["rows"]
    assert internal["full_refit_delivery_dates"] == candidates[0]["qualification"]["delivery_dates"]
    assert internal["full_refit_rows"] >= internal["fit_rows"]
    assert 1 <= internal["classifier_selected_iterations"] <= model.PROTOCOL["catboost_max_iterations"]
    assert 1 <= internal["regressor_selected_iterations"] <= model.PROTOCOL["catboost_max_iterations"]


def test_gam_export_matches_kept_estimators_when_sklearn_is_available() -> None:
    pytest.importorskip("numpy")
    pytest.importorskip("sklearn")
    rows = synthetic_rows()

    fit = model.fit_two_part_gam(
        rows,
        feature_group="statistical",
        logistic_c=1.0,
        gamma_alpha=1.0,
        keep_estimators=True,
    )

    assert fit["status"] == "available"
    selection = model.filter_rows(rows, split_names={"selection"})[:8]
    runtime_predictions = model.predict_with_runtime(fit, selection)
    exported_predictions = inference.predict_rows(fit["artifact"], selection)
    for runtime, exported in zip(runtime_predictions, exported_predictions):
        assert exported["probability_positive"] == pytest.approx(runtime["probability_positive"], abs=1e-8, rel=1e-8)
        assert exported["conditional_positive_loss"] == pytest.approx(runtime["conditional_positive_loss"], abs=1e-8, rel=1e-8)
        assert exported["expected_loss_normalized"] == pytest.approx(runtime["expected_loss_normalized"], abs=1e-8, rel=1e-8)


def test_fit_gam_grid_preserves_all_hyperparameter_candidates() -> None:
    pytest.importorskip("numpy")
    pytest.importorskip("sklearn")
    rows = synthetic_rows()

    candidates = model.fit_gam_grid_candidates(rows, feature_group="statistical", purge_against_splits={"selection"})

    assert len(candidates) == len(PROTOCOL["gam_logistic_C"]) * len(PROTOCOL["gam_gamma_alpha"])
    assert all(candidate["selection_metrics"]["primary_metric"] == "expected_loss_mae" for candidate in candidates)
    assert {candidate["logistic_c"] for candidate in candidates} == set(PROTOCOL["gam_logistic_C"])
    assert {candidate["gamma_alpha"] for candidate in candidates} == set(PROTOCOL["gam_gamma_alpha"])


def test_train_select_returns_exportable_gam_when_sklearn_is_available() -> None:
    pytest.importorskip("numpy")
    pytest.importorskip("sklearn")
    rows = synthetic_rows()

    result = model.train_select(rows, feature_groups=("statistical",), include_catboost=False)

    assert result["status"] == "available"
    assert result["schema"] == model.MODEL_SELECTION_SCHEMA
    assert result["selected"]["model_family"] == "gam"
    assert result["selected_artifact"]["schema"] == model.MODEL_SCHEMA
    assert result["selected_artifact"]["status"] == "available"
    assert result["selected_artifact"]["scope"]["training_feature_support"]["ret_15"]["missing_rate"] == pytest.approx(0.0)
    assert result["selected_artifact"]["scope"]["training_feature_support"]["side_sign"]["min"] == -1.0
    assert result["selected_artifact"]["scope"]["training_feature_support"]["side_sign"]["max"] == 1.0
    metrics = result["selected"]["selection_metrics"]
    assert metrics["status"] == "available"
    assert metrics["rows"] > 0
    assert result["holdout_purge_splits"] == ["locked_test", "selection"]
    assert result["best_artifacts"]["statistical"]["status"] == "available"
    assert len(result["candidate_artifacts"]) == len(PROTOCOL["gam_logistic_C"]) * len(PROTOCOL["gam_gamma_alpha"])
    assert all(candidate["candidate_id"] for candidate in result["candidates"])
    assert result["sealed_primary_comparisons"][0]["status"] == "unavailable"


def test_train_select_default_purges_locked_test_boundaries_without_using_test_label() -> None:
    pytest.importorskip("numpy")
    pytest.importorskip("sklearn")
    rows = synthetic_rows()
    locked = dict(base_row(999, split="locked_test", target=0.99), episode_id=rows[0]["episode_id"], expiry_ms=999_999_999)
    locked.pop("loss_normalized")
    rows.append(locked)

    result = model.train_select(rows, feature_groups=("statistical",), include_catboost=False)

    assert result["status"] == "available"
    assert result["candidates"][0]["purge_report"]["holdout_splits"] == ["locked_test", "selection"]
    assert result["candidates"][0]["purge_report"]["removed_rows"] >= 1


def test_select_best_candidate_uses_delivery_day_one_se_rule() -> None:
    artifact = {"schema": model.MODEL_SCHEMA, "status": "available"}
    catboost_best = {
        "candidate_id": "cat",
        "model_family": "catboost",
        "feature_group": "joint",
        "exportable_stdlib": True,
        "artifact": artifact,
        "artifact_hash": "cat-hash",
        "selection_metrics": {
            "status": "available",
            "expected_loss_mae": 0.100,
            "expected_loss_mae_se_by_delivery": 0.020,
            "payout_probability_brier": 0.20,
            "tail_underestimate_p95": 0.30,
        },
    }
    gam_within_one_se = {
        "candidate_id": "gam",
        "model_family": "gam",
        "feature_group": "joint",
        "exportable_stdlib": True,
        "artifact": artifact,
        "artifact_hash": "gam-hash",
        "selection_metrics": {
            "status": "available",
            "expected_loss_mae": 0.115,
            "expected_loss_mae_se_by_delivery": 0.002,
            "payout_probability_brier": 0.21,
            "tail_underestimate_p95": 0.31,
        },
    }

    selected = model.select_best_candidate([catboost_best, gam_within_one_se])

    assert selected["selected"]["candidate_id"] == "gam"
    assert selected["selection_note"] == "gam_within_one_delivery_day_standard_error_of_challenger"


def test_reliability_bins_are_weighted_and_ordered() -> None:
    predictions = [
        {"probability_positive": 0.1},
        {"probability_positive": 0.4},
        {"probability_positive": 0.8},
        {"probability_positive": 0.9},
    ]
    targets = [0.0, 1.0, 1.0, 0.0]
    weights = [1.0, 3.0, 1.0, 1.0]

    bins = model.reliability_bins(predictions, targets, weights, bins=2)

    assert len(bins) == 2
    assert bins[0]["probability_max"] <= bins[1]["probability_min"]
    assert bins[0]["observed_positive_rate"] == pytest.approx(0.75)


def test_holm_uses_probability_order_not_comparison_name():
    adjusted = model.holm_adjust({'a_large': 0.2, 'z_small': 0.01, 'm_medium': 0.03})
    assert adjusted == pytest.approx({'z_small':0.03,'m_medium':0.06,'a_large':0.2})


def test_calendar_blocks_do_not_join_missing_days_and_p_is_not_zero():
    left={'2023-01-01':1.0,'2023-01-02':1.0,'2023-02-01':1.0}
    right={k:0.0 for k in left}
    result=model.bootstrap_metric_difference(left,right,repetitions=99,block_days=7)
    assert result['calendar_blocks'] == 2
    assert result['p_value_two_sided_centered'] == pytest.approx(0.01)
