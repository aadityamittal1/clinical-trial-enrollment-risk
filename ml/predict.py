"""The one place scoring happens. Batch scoring (ml.score) and the API (serving) import it.

* ``load_model(settings)`` -> ``LoadedModel``: the latest registered version of
  ``settings.model_name``; raises ``ModelNotFoundError`` when nothing is registered and
  ``FeatureMismatchError`` (a ``ModelNotFoundError`` subclass, so it is also "no usable
  model") when the version was trained on a different feature list than the current
  ``ml.features``. Other registry failures (corrupt / locked store) propagate unchanged.
* ``score_frame(model, features_df)`` -> one row per input row with ``nct_id``,
  ``risk_score`` (probability of termination for low enrollment, in [0, 1]), the three
  largest absolute SHAP drivers and all SHAP values as a JSON string.

``features_df`` holds raw ``marts.ml_features`` rows (``nct_id`` plus the columns in
``ml.features.FEATURES``; extra columns are ignored). The model pipeline itself applies
``ml.features.prepare_features`` and the encoders, so callers never preprocess.

SHAP values are TreeSHAP contributions of the LightGBM step for the positive class, in
log-odds units, one per original feature (categoricals are ordinal-encoded into a single
column each, so names map one-to-one to ``FEATURES``). For every row
``sum(shap_values) + expected_value == logit(risk_score)``.
"""

import json
import warnings
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline

from common.config import Settings, get_settings
from ml import features as feature_spec
from ml.features import ID_COLUMN
from ml.tracking import configure_mlflow

SCORE_COLUMNS: list[str] = [
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
N_DRIVERS = 3
SHAP_DECIMALS = 6


class ModelNotFoundError(LookupError):
    """No version of the model is registered (serving maps this to HTTP 503)."""


class FeatureMismatchError(ModelNotFoundError):
    """The registered version was trained on a different feature list than ml.features now has.

    The pipeline's preprocessing step references ``ml.features.prepare_features`` by name,
    which reads the module's current FEATURES; scoring an old version after FEATURES changed
    would silently feed it different inputs, so loading refuses instead.
    """


# Registry errors that mean "this model / version does not exist" (anything else re-raises).
_NOT_FOUND_ERROR_CODES = frozenset({"RESOURCE_DOES_NOT_EXIST", "NOT_FOUND"})


@dataclass(frozen=True)
class LoadedModel:
    name: str
    version: str  # MLflow registered model version, e.g. "3"
    pipeline: Pipeline
    run_id: str | None


def latest_version(settings: Settings) -> Any:
    """Latest ModelVersion of settings.model_name; ModelNotFoundError when there is none."""
    from mlflow.exceptions import MlflowException
    from mlflow.tracking import MlflowClient

    uri = configure_mlflow(settings)
    client = MlflowClient(tracking_uri=uri, registry_uri=uri)
    name = settings.model_name
    try:
        versions = client.search_model_versions(f"name = '{name}'")
    except MlflowException as exc:
        if exc.error_code in _NOT_FOUND_ERROR_CODES:
            raise ModelNotFoundError(f"no registered model {name!r} at {uri}: {exc}") from exc
        raise  # corrupt / locked / unreachable registry is not "no model"
    if not versions:
        raise ModelNotFoundError(
            f"no registered version of model {name!r} at {uri}; run `python -m ml train`"
        )
    return max(versions, key=lambda v: int(v.version))


def check_feature_lists(params: dict[str, str], where: str) -> None:
    """Raise FeatureMismatchError unless the run's logged feature lists equal ml.features."""
    expected = {
        "categorical_features": ",".join(feature_spec.CATEGORICAL_FEATURES),
        "numeric_features": ",".join(feature_spec.NUMERIC_FEATURES),
    }
    for key, current in expected.items():
        logged = params.get(key)
        if logged is None:
            raise FeatureMismatchError(f"{where}: run has no {key!r} param; cannot verify features")
        if logged != current:
            raise FeatureMismatchError(
                f"{where} was trained with {key}={logged!r} but ml.features now has "
                f"{current!r}; retrain (`python -m ml train`) before scoring"
            )


def load_model(settings: Settings | None = None) -> LoadedModel:
    """Load the latest registered version of settings.model_name from the MLflow registry."""
    import mlflow.sklearn
    from mlflow.tracking import MlflowClient

    settings = settings or get_settings()
    version = latest_version(settings)
    where = f"model {settings.model_name!r} version {version.version}"
    if not version.run_id:
        raise FeatureMismatchError(f"{where} has no training run; cannot verify features")
    uri = configure_mlflow(settings)
    run = MlflowClient(tracking_uri=uri, registry_uri=uri).get_run(version.run_id)
    check_feature_lists(dict(run.data.params), where)
    pipeline = mlflow.sklearn.load_model(f"models:/{settings.model_name}/{version.version}")
    return LoadedModel(
        name=settings.model_name,
        version=str(version.version),
        pipeline=pipeline,
        run_id=version.run_id,
    )


def _positive_class_shap(estimator: Any, X: pd.DataFrame) -> np.ndarray:
    import shap

    with warnings.catch_warnings():
        # shap notes that its LightGBM binary output format changed; we handle both shapes.
        warnings.filterwarnings(
            "ignore",
            message="LightGBM binary classifier with TreeExplainer shap values output",
            category=UserWarning,
        )
        values = shap.TreeExplainer(estimator).shap_values(X)
    arr = np.asarray(values, dtype=float)
    if isinstance(values, list):  # [negative class, positive class]
        arr = np.asarray(values[-1], dtype=float)
    elif arr.ndim == 3:  # (rows, features, classes)
        arr = arr[..., -1]
    return arr


def _empty_scores() -> pd.DataFrame:
    dtypes = {
        "risk_score": "float64",
        "top_driver_1_shap": "float64",
        "top_driver_2_shap": "float64",
        "top_driver_3_shap": "float64",
    }
    return pd.DataFrame({c: pd.Series(dtype=dtypes.get(c, "object")) for c in SCORE_COLUMNS})


def score_frame(model: LoadedModel | Pipeline, features_df: pd.DataFrame) -> pd.DataFrame:
    """Risk score and SHAP explanation for every row of ``features_df`` (input order kept)."""
    pipeline = model.pipeline if isinstance(model, LoadedModel) else model
    if features_df.empty:
        return _empty_scores()
    X = pipeline[:-1].transform(features_df)  # prepare + encode, once
    risk = np.clip(pipeline[-1].predict_proba(X)[:, 1].astype(float), 0.0, 1.0)
    names = [str(c) for c in X.columns]
    shap_values = np.round(_positive_class_shap(pipeline[-1], X), SHAP_DECIMALS)
    order = np.argsort(-np.abs(shap_values), axis=1, kind="stable")[:, :N_DRIVERS]

    out: dict[str, Any] = {
        "nct_id": features_df[ID_COLUMN].astype(str).to_numpy(),
        "risk_score": risk,
    }
    rows = np.arange(len(features_df))
    for k in range(N_DRIVERS):
        out[f"top_driver_{k + 1}"] = [names[j] for j in order[:, k]]
    for k in range(N_DRIVERS):
        out[f"top_driver_{k + 1}_shap"] = shap_values[rows, order[:, k]]
    out["shap_values"] = [
        json.dumps(dict(zip(names, (float(v) for v in row), strict=True))) for row in shap_values
    ]
    return pd.DataFrame(out, columns=SCORE_COLUMNS)
