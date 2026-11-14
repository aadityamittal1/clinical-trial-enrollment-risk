"""Training guards and evaluation outputs."""

import json
from pathlib import Path

import pytest

from ml import train as train_module
from ml.evaluate import CALIBRATION_FILE, METRICS_FILE, evaluate
from ml.train import SuspiciousMetricsError, train
from tests.ml._helpers import Trained, copy_warehouse, make_settings


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


def test_split_without_both_classes_is_rejected(trained: Trained) -> None:
    # No labelled study starts after 2020, so val and test would be empty.
    with pytest.raises(ValueError, match="widen"):
        train(trained.settings, train_end=2025, val_end=2026, test_end=2027)


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
    assert (
        metrics["global_shap_test"][0]["mean_abs_shap"]
        >= metrics["global_shap_test"][-1]["mean_abs_shap"]
    )
    assert report["split"]["sizes"] == trained.result.split_sizes
