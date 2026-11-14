"""Train the baseline (logistic regression) and the main model (LightGBM).

Both are scikit-learn pipelines whose first step is ``ml.features.prepare_features``, so
the registered artifact accepts raw ``marts.ml_features`` rows and applies exactly the
training-time handling at scoring time:

* logistic regression: one-hot categoricals (unseen -> all zeros), median-imputed and
  standardised numerics with missing-value indicators;
* LightGBM: categoricals ordinal-encoded into one column each and used as native
  LightGBM categoricals (unseen / missing -> NaN, LightGBM's missing branch); numerics
  passed through (NaN handled natively).

Data: labelled studies, split by start year (``ml.split``). Logged to MLflow: parameters,
PR-AUC / ROC-AUC / Brier / log loss for train, validation and test, the no-skill PR-AUC
(positive rate) per split, and calibration plots. The LightGBM pipeline is registered under
``settings.model_name``. If any test PR-AUC exceeds ``SUSPICIOUS_PR_AUC`` the run is
flagged as suspected leakage and fails unless ``allow_suspicious`` is set.
"""

import json
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, OrdinalEncoder, StandardScaler

from common.config import Settings, get_settings
from common.warehouse import get_warehouse
from ml.features import (
    CATEGORICAL_FEATURES,
    FEATURES,
    LABEL_COLUMN,
    NUMERIC_FEATURES,
    load_labelled,
    prepare_features,
)
from ml.metrics import (
    PREVALENCE_NOTE,
    binary_metrics,
    calibration_bins,
    expected_calibration_error,
    plot_calibration,
)
from ml.split import TEST_END, TRAIN_END, VAL_END, Split, time_split
from ml.tracking import ensure_experiment

log = logging.getLogger(__name__)

SEED = 42
SUSPICIOUS_PR_AUC = 0.95
MODELS = ("logistic", "lightgbm")
SPLITS = ("train", "val", "test")

# Fixed a priori (small data: shallow trees, shrinkage, row/column subsampling). Not tuned
# on the test split.
LIGHTGBM_PARAMS: dict[str, Any] = {
    "n_estimators": 200,
    "learning_rate": 0.03,
    "num_leaves": 15,
    "min_child_samples": 20,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.8,
    "reg_lambda": 1.0,
    "cat_smooth": 10.0,
    "min_data_per_group": 20,
    "n_jobs": 1,
    "deterministic": True,
    "force_col_wise": True,
    "verbose": -1,
}
LOGISTIC_PARAMS: dict[str, Any] = {"C": 1.0, "max_iter": 5000}

# MLflow stores the sklearn pipeline with skops (no arbitrary pickle on load); these are the
# non-sklearn types in the LightGBM pipeline that loading must trust.
SKOPS_TRUSTED_TYPES: list[str] = [
    "collections.OrderedDict",
    "lightgbm.basic.Booster",
    "lightgbm.sklearn.LGBMClassifier",
    "ml.features.prepare_features",
]


class SuspiciousMetricsError(RuntimeError):
    """Test PR-AUC above SUSPICIOUS_PR_AUC: likely leakage; refused without allow_suspicious."""


def _prepare_step() -> FunctionTransformer:
    return FunctionTransformer(prepare_features)


def build_logistic(seed: int = SEED, **overrides: Any) -> Pipeline:
    encode = ColumnTransformer(
        [
            (
                "cat",
                OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                list(CATEGORICAL_FEATURES),
            ),
            (
                "num",
                make_pipeline(
                    SimpleImputer(strategy="median", add_indicator=True), StandardScaler()
                ),
                list(NUMERIC_FEATURES),
            ),
        ]
    )
    params = {**LOGISTIC_PARAMS, **overrides}
    return Pipeline(
        [
            ("prepare", _prepare_step()),
            ("encode", encode),
            ("clf", LogisticRegression(random_state=seed, **params)),
        ]
    )


def build_lightgbm(seed: int = SEED, **overrides: Any) -> Pipeline:
    encode = ColumnTransformer(
        [
            (
                "cat",
                OrdinalEncoder(
                    handle_unknown="use_encoded_value",
                    unknown_value=np.nan,
                    encoded_missing_value=np.nan,
                ),
                list(CATEGORICAL_FEATURES),
            ),
            ("num", "passthrough", list(NUMERIC_FEATURES)),
        ],
        verbose_feature_names_out=False,
    ).set_output(transform="pandas")
    params = {**LIGHTGBM_PARAMS, **overrides}
    return Pipeline(
        [
            ("prepare", _prepare_step()),
            ("encode", encode),
            ("clf", LGBMClassifier(random_state=seed, **params)),
        ]
    )


def fit_pipeline(pipeline: Pipeline, X: pd.DataFrame, y: Any) -> Pipeline:
    """Fit a pipeline from build_logistic / build_lightgbm (LightGBM gets its categoricals)."""
    y_arr = np.asarray(y, dtype=int)
    if isinstance(pipeline[-1], LGBMClassifier):
        return pipeline.fit(X, y_arr, clf__categorical_feature=list(CATEGORICAL_FEATURES))
    return pipeline.fit(X, y_arr)


@dataclass
class TrainResult:
    run_id: str
    model_name: str
    model_version: str | None
    metrics: dict[str, float]
    split_sizes: dict[str, int]
    calibration: dict[str, list[dict[str, float]]] = field(default_factory=dict)


def _check_split(split: Split) -> None:
    for name in SPLITS:
        part: pd.DataFrame = getattr(split, name)
        if part.empty or part[LABEL_COLUMN].nunique() < 2:
            raise ValueError(
                f"{name} split has {len(part)} rows and needs both classes; widen the "
                f"start-year boundaries (train_end={split.train_end}, val_end={split.val_end}, "
                f"test_end={split.test_end})"
            )


def _flat_params(seed: int, split: Split) -> dict[str, Any]:
    params: dict[str, Any] = {
        "seed": seed,
        "train_end": split.train_end,
        "val_end": split.val_end,
        "test_end": split.test_end,
        "n_features": len(FEATURES),
        "categorical_features": ",".join(CATEGORICAL_FEATURES),
        "numeric_features": ",".join(NUMERIC_FEATURES),
    }
    params.update({f"lightgbm_{k}": v for k, v in LIGHTGBM_PARAMS.items()})
    params.update({f"logistic_{k}": v for k, v in LOGISTIC_PARAMS.items()})
    return params


def train(
    settings: Settings | None = None,
    *,
    train_end: int = TRAIN_END,
    val_end: int = VAL_END,
    test_end: int = TEST_END,
    allow_suspicious: bool = False,
    seed: int = SEED,
) -> TrainResult:
    """Fit both models, log everything to MLflow and register the LightGBM pipeline."""
    import mlflow
    import mlflow.sklearn

    settings = settings or get_settings()
    warehouse = get_warehouse(settings)
    try:
        data = load_labelled(warehouse)
    finally:
        warehouse.close()
    split = time_split(data, train_end=train_end, val_end=val_end, test_end=test_end)
    _check_split(split)

    models = {"logistic": build_logistic(seed), "lightgbm": build_lightgbm(seed)}
    for pipe in models.values():
        fit_pipeline(pipe, split.train, split.train[LABEL_COLUMN])

    metrics: dict[str, float] = {}
    predictions: dict[tuple[str, str], np.ndarray] = {}
    for split_name in SPLITS:
        part: pd.DataFrame = getattr(split, split_name)
        for model_name, pipe in models.items():
            p = pipe.predict_proba(part)[:, 1]
            predictions[(model_name, split_name)] = p
            m = binary_metrics(part[LABEL_COLUMN], p)
            for key in ("pr_auc", "roc_auc", "brier", "log_loss", "mean_predicted"):
                metrics[f"{model_name}_{split_name}_{key}"] = m[key]
        metrics[f"no_skill_{split_name}_pr_auc"] = float(part[LABEL_COLUMN].mean())
        metrics[f"n_{split_name}"] = float(len(part))
        metrics[f"n_positive_{split_name}"] = float(part[LABEL_COLUMN].sum())

    calibration: dict[str, list[dict[str, float]]] = {}
    for model_name in MODELS:
        bins = calibration_bins(split.test[LABEL_COLUMN], predictions[(model_name, "test")])
        calibration[model_name] = bins
        metrics[f"{model_name}_test_ece"] = expected_calibration_error(bins)

    experiment_id = ensure_experiment(settings)
    with mlflow.start_run(experiment_id=experiment_id, run_name="train") as run:
        mlflow.log_params(_flat_params(seed, split))
        mlflow.log_metrics(metrics)
        mlflow.set_tag("prevalence_note", PREVALENCE_NOTE)
        with tempfile.TemporaryDirectory() as tmp:
            for split_name in ("val", "test"):
                part = getattr(split, split_name)
                png = plot_calibration(
                    {
                        name: (part[LABEL_COLUMN], predictions[(name, split_name)])
                        for name in MODELS
                    },
                    Path(tmp) / f"calibration_{split_name}.png",
                    title=(
                        f"Calibration, {split_name} split (start years "
                        f"{_years(split, split_name)}, n={len(part)})"
                    ),
                )
                mlflow.log_artifact(str(png), artifact_path="plots")
            cal = Path(tmp) / "calibration_test_bins.json"
            cal.write_text(json.dumps(calibration, indent=2), encoding="utf-8")
            mlflow.log_artifact(str(cal), artifact_path="plots")

        suspicious = {
            k: metrics[k]
            for k in ("lightgbm_test_pr_auc", "logistic_test_pr_auc")
            if metrics[k] > SUSPICIOUS_PR_AUC
        }
        if suspicious:
            mlflow.set_tag("leakage_suspected", "true")
            log.warning(
                "!!! LEAKAGE SUSPECTED !!! test PR-AUC above %.2f: %s. A registration-time "
                "model should not be this good; check ml_features for outcome-derived columns.",
                SUSPICIOUS_PR_AUC,
                suspicious,
            )
            if not allow_suspicious:
                raise SuspiciousMetricsError(
                    f"test PR-AUC above {SUSPICIOUS_PR_AUC}: {suspicious}; refusing to register "
                    "(pass --allow-suspicious to override after investigating)"
                )

        info = mlflow.sklearn.log_model(
            sk_model=models["lightgbm"],
            name="model",
            registered_model_name=settings.model_name,
            serialization_format="skops",
            skops_trusted_types=SKOPS_TRUSTED_TYPES,
        )
        version = getattr(info, "registered_model_version", None)
        run_id = run.info.run_id

    log.info(
        "trained run %s; registered %s version %s; test PR-AUC lightgbm %.3f, logistic %.3f, "
        "no-skill %.3f",
        run_id,
        settings.model_name,
        version,
        metrics["lightgbm_test_pr_auc"],
        metrics["logistic_test_pr_auc"],
        metrics["no_skill_test_pr_auc"],
    )
    return TrainResult(
        run_id=run_id,
        model_name=settings.model_name,
        model_version=None if version is None else str(version),
        metrics=metrics,
        split_sizes=split.sizes(),
        calibration=calibration,
    )


def _years(split: Split, name: str) -> str:
    if name == "train":
        return f"<= {split.train_end}"
    if name == "val":
        return f"{split.train_end + 1}-{split.val_end}"
    return f"{split.val_end + 1}-{split.test_end}"
