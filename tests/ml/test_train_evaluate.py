"""Training guards and evaluation outputs."""

import json
from pathlib import Path

import pytest

from ml import train as train_module
from ml.evaluate import CALIBRATION_FILE, METRICS_FILE, evaluate
from ml.train import SuspiciousMetricsError, train
from tests.ml._helpers import Trained, copy_warehouse, make_settings
from tests.ml._synthetic import synthetic_features


def test_training_logs_both_models_and_no_skill(trained: Trained) -> None:
    m = trained.result.metrics
    for model in ("lightgbm", "logistic"):
        for split in ("val", "test"):
            assert 0.0 <= m[f"{model}_{split}_pr_auc"] <= 1.0
    for split in ("train", "val", "test"):
        assert m[f"n_{split}"] == trained.result.split_sizes[split]
        assert m[f"no_skill_{split}_pr_auc"] == pytest.approx(
            m[f"n_positive_{split}"] / m[f"n_{split}"]
        )
    assert trained.result.model_version == "1"


def test_suspicious_pr_auc_refuses_to_register(
    sample_warehouse: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(copy_warehouse(sample_warehouse, tmp_path / "wh"), tmp_path / "ml")
    monkeypatch.setattr(train_module, "SUSPICIOUS_PR_AUC", 0.0)  # every model is "too good"
    with pytest.raises(SuspiciousMetricsError, match="PR-AUC"):
        train(settings)
    from ml.predict import ModelNotFoundError, load_model

    with pytest.raises(ModelNotFoundError):
        load_model(settings)
    result = train(settings, allow_suspicious=True)
    assert result.model_version == "1"


def test_split_without_both_classes_is_rejected(
    trained: Trained, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every labelled row starts in 2010, so val (2017-2018) and test (2019-2020) are empty
    # by construction, whatever the warehouse holds.
    frame = synthetic_features(60, seed=3).assign(start_year=2010.0)
    monkeypatch.setattr(train_module, "load_labelled", lambda warehouse: frame)
    with pytest.raises(ValueError, match="widen"):
        train(trained.settings)


def test_evaluate_writes_metrics_json_and_calibration_png(trained: Trained, tmp_path: Path) -> None:
    report = evaluate(trained.settings, out_dir=tmp_path)
    metrics = json.loads((tmp_path / METRICS_FILE).read_text(encoding="utf-8"))
    png = tmp_path / CALIBRATION_FILE
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert metrics["model_version"] == "1"
    assert metrics["test_matches_training_run"] is True
    assert metrics["test_registered_model"]["pr_auc"] == pytest.approx(
        trained.result.metrics["lightgbm_test_pr_auc"]
    )
    assert set(metrics["pr_auc"]["test"]) == {"lightgbm", "logistic", "no_skill"}
    assert "oversamples" in metrics["prevalence_note"]
    for word in ("latest version", "eligibility criteria", "site list", "ESTIMATED"):
        assert word in metrics["soft_leak_note"]
    note = metrics["calibration_note"]
    assert "No recalibration" in note["recalibration"]
    for cohort in ("val", "test"):
        assert 0.0 < note["cohorts"][cohort]["mean_predicted"] < 1.0
        assert 0.0 < note["cohorts"][cohort]["observed_rate"] < 1.0
    assert note["cohorts"]["test"]["observed_rate"] == metrics["split"]["positive_rate"]["test"]
    assert (
        metrics["global_shap_test"][0]["mean_abs_shap"]
        >= metrics["global_shap_test"][-1]["mean_abs_shap"]
    )
    assert report["split"]["sizes"] == trained.result.split_sizes
