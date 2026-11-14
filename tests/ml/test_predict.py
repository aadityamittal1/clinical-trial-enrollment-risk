"""score_frame: output schema, range and SHAP explanations on a small fitted model."""

import json

import numpy as np
import pandas as pd
import pytest
from sklearn.pipeline import Pipeline

from ml.features import FEATURES
from ml.predict import SCORE_COLUMNS, LoadedModel, score_frame
from ml.train import build_lightgbm, fit_pipeline
from tests.ml._synthetic import synthetic_features

pytestmark = pytest.mark.filterwarnings(
    # LightGBM on a tiny synthetic frame: some trees cannot split further.
    "ignore:.*No further splits with positive gain.*:UserWarning",
)


@pytest.fixture(scope="module")
def model() -> Pipeline:
    train = synthetic_features(400, seed=1)
    pipe = build_lightgbm(n_estimators=40, min_child_samples=5)
    return fit_pipeline(pipe, train, train["label"])


@pytest.fixture
def rows() -> pd.DataFrame:
    return synthetic_features(25, seed=2).drop(columns="label")


def test_output_schema_and_order(model: Pipeline, rows: pd.DataFrame) -> None:
    out = score_frame(model, rows)
    assert list(out.columns) == SCORE_COLUMNS
    assert SCORE_COLUMNS == [
        "nct_id",
        "risk_score",
        "top_driver_1",
        "top_driver_2",
        "top_driver_3",
        "top_driver_1_shap",
        "top_driver_2_shap",
        "top_driver_3_shap",
        "shap_values",
    ]
    assert out["nct_id"].tolist() == rows["nct_id"].tolist()
    assert list(out.index) == list(range(len(rows)))


def test_risk_score_is_the_positive_class_probability_in_unit_interval(
    model: Pipeline, rows: pd.DataFrame
) -> None:
    out = score_frame(model, rows)
    assert out["risk_score"].between(0.0, 1.0).all()
    np.testing.assert_allclose(out["risk_score"], model.predict_proba(rows)[:, 1])


def test_shap_values_cover_every_feature_by_readable_name(
    model: Pipeline, rows: pd.DataFrame
) -> None:
    out = score_frame(model, rows)
    for payload in out["shap_values"]:
        shap = json.loads(payload)
        assert list(shap) == list(FEATURES)
        assert all(isinstance(v, float) for v in shap.values())


def test_shap_values_add_up_to_the_log_odds(model: Pipeline, rows: pd.DataFrame) -> None:
    out = score_frame(model, rows)
    total = np.array([sum(json.loads(s).values()) for s in out["shap_values"]])
    risk = out["risk_score"].to_numpy()
    logit = np.log(risk / (1 - risk))
    base = logit - total  # the explainer's expected value: one constant for every row
    np.testing.assert_allclose(base, base[0], atol=1e-4)


def test_top_drivers_are_the_largest_absolute_shap_values(
    model: Pipeline, rows: pd.DataFrame
) -> None:
    out = score_frame(model, rows)
    for _, r in out.iterrows():
        shap = json.loads(r["shap_values"])
        ranked = sorted(shap, key=lambda k: -abs(shap[k]))
        drivers = [r["top_driver_1"], r["top_driver_2"], r["top_driver_3"]]
        assert len(set(drivers)) == 3
        assert all(d in FEATURES for d in drivers)
        values = [r["top_driver_1_shap"], r["top_driver_2_shap"], r["top_driver_3_shap"]]
        assert [abs(v) for v in values] == sorted((abs(v) for v in values), reverse=True)
        assert abs(values[0]) == pytest.approx(abs(shap[ranked[0]]), abs=1e-6)
        for d, v in zip(drivers, values, strict=True):
            assert shap[d] == pytest.approx(v, abs=1e-6)


def test_unseen_categories_and_nulls_are_scored(model: Pipeline, rows: pd.DataFrame) -> None:
    odd = rows.copy()
    odd.loc[0, "phase"] = "PHASE9"  # never seen in training
    odd.loc[1, "sponsor_class"] = None
    odd.loc[2, "has_dmc"] = None
    odd.loc[3, "n_arms"] = None
    odd.loc[4, "start_year"] = 2031  # beyond the training years
    out = score_frame(model, odd)
    assert out["risk_score"].between(0.0, 1.0).all()
    assert out["risk_score"].notna().all()


def test_extra_columns_are_ignored(model: Pipeline, rows: pd.DataFrame) -> None:
    extra = rows.assign(enrollment_count=999, why_stopped="low accrual")
    pd.testing.assert_frame_equal(score_frame(model, extra), score_frame(model, rows))


def test_empty_frame(model: Pipeline, rows: pd.DataFrame) -> None:
    out = score_frame(model, rows.iloc[0:0])
    assert list(out.columns) == SCORE_COLUMNS
    assert out.empty


def test_accepts_a_loaded_model(model: Pipeline, rows: pd.DataFrame) -> None:
    loaded = LoadedModel(name="m", version="7", pipeline=model, run_id=None)
    pd.testing.assert_frame_equal(score_frame(loaded, rows), score_frame(model, rows))
