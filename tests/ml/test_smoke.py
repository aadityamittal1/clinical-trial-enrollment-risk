"""Smoke check from the spec (CI: ``pytest tests/ml -k smoke``).

Scores are in [0, 1] and LightGBM's test PR-AUC beats the no-skill rate (the positive
rate of the test split) on the sample, with the default split (train <= 2016, validate
2017-2018, test 2019-2020). The sample oversamples terminated trials, so these numbers are
not real-world performance.
"""

from common.warehouse import get_warehouse
from ml.features import load_open
from ml.predict import load_model, score_frame
from tests.ml._helpers import Trained


def test_smoke_scores_in_unit_interval_and_lightgbm_beats_no_skill(trained: Trained) -> None:
    m = trained.result.metrics
    assert trained.result.split_sizes["test"] > 0
    assert m["lightgbm_test_pr_auc"] > m["no_skill_test_pr_auc"], m
    assert 0.0 < m["no_skill_test_pr_auc"] < 1.0

    model = load_model(trained.settings)
    warehouse = get_warehouse(trained.settings)
    try:
        open_studies = load_open(warehouse)
    finally:
        warehouse.close()
    scores = score_frame(model, open_studies)
    assert len(scores) == len(open_studies) > 0
    assert scores["risk_score"].notna().all()
    assert scores["risk_score"].between(0.0, 1.0).all()


def test_smoke_training_is_deterministic(trained: Trained) -> None:
    # Seeds are fixed: refitting the same pipelines gives the same test PR-AUC.
    from common.warehouse import get_warehouse as gw
    from ml.features import LABEL_COLUMN, load_labelled
    from ml.metrics import binary_metrics
    from ml.split import time_split
    from ml.train import build_lightgbm, fit_pipeline

    warehouse = gw(trained.settings)
    try:
        split = time_split(load_labelled(warehouse))
    finally:
        warehouse.close()
    pipe = fit_pipeline(build_lightgbm(), split.train, split.train[LABEL_COLUMN])
    pr_auc = binary_metrics(split.test[LABEL_COLUMN], pipe.predict_proba(split.test)[:, 1])
    assert pr_auc["pr_auc"] == trained.result.metrics["lightgbm_test_pr_auc"]
