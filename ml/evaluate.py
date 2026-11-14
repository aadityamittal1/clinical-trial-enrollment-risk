"""Re-evaluate the registered model on the test split and write the model-card inputs.

Outputs (committed, consumed by docs/model_card.md):
* ``docs/model_metrics.json`` - test metrics of the registered LightGBM version recomputed
  here, the training run's logged metrics for both models (LightGBM and the logistic
  baseline) on validation and test, the no-skill PR-AUC, calibration bins and global SHAP
  importance on the test split;
* ``docs/img/calibration.png`` - reliability diagram of the registered model on the test
  split.

The split boundaries are read from the training run's parameters, so evaluation always
uses the split the model was trained with.
"""

import json
import logging
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from common.config import Settings, get_settings
from common.warehouse import get_warehouse
from ml.features import CATEGORICAL_FEATURES, LABEL_COLUMN, NUMERIC_FEATURES, load_labelled
from ml.metrics import (
    N_CALIBRATION_BINS,
    PREVALENCE_NOTE,
    binary_metrics,
    calibration_bins,
    expected_calibration_error,
    plot_calibration,
)
from ml.predict import load_model, score_frame
from ml.split import TEST_END, TRAIN_END, VAL_END, time_split
from ml.tracking import configure_mlflow

log = logging.getLogger(__name__)

DOCS_DIR = Path(__file__).resolve().parents[1] / "docs"
METRICS_FILE = "model_metrics.json"
CALIBRATION_FILE = Path("img") / "calibration.png"
SOFT_LEAK_NOTE = (
    "n_sites, n_countries and us_only come from the current site list, which can change "
    "after registration (known soft leak, kept per the spec)."
)


def _run_info(settings: Settings, run_id: str | None) -> tuple[dict[str, str], dict[str, float]]:
    if run_id is None:
        return {}, {}
    from mlflow.tracking import MlflowClient

    uri = configure_mlflow(settings)
    run = MlflowClient(tracking_uri=uri).get_run(run_id)
    return dict(run.data.params), dict(run.data.metrics)


def _global_shap(scores: pd.DataFrame) -> list[dict[str, Any]]:
    values = pd.DataFrame([json.loads(s) for s in scores["shap_values"]])
    mean_abs = values.abs().mean().sort_values(ascending=False)
    mean = values.mean()
    return [
        {"feature": str(f), "mean_abs_shap": float(v), "mean_shap": float(mean[str(f)])}
        for f, v in mean_abs.items()
    ]


def evaluate(settings: Settings | None = None, out_dir: Path = DOCS_DIR) -> dict[str, Any]:
    settings = settings or get_settings()
    model = load_model(settings)
    params, run_metrics = _run_info(settings, model.run_id)
    train_end = int(params.get("train_end", TRAIN_END))
    val_end = int(params.get("val_end", VAL_END))
    test_end = int(params.get("test_end", TEST_END))

    warehouse = get_warehouse(settings)
    try:
        data = load_labelled(warehouse)
    finally:
        warehouse.close()
    split = time_split(data, train_end=train_end, val_end=val_end, test_end=test_end)

    scores = score_frame(model, split.test)
    y_test = split.test[LABEL_COLUMN]
    p_test = scores["risk_score"].to_numpy()
    test = binary_metrics(y_test, p_test)
    bins = calibration_bins(y_test, p_test)
    test["ece"] = expected_calibration_error(bins)

    logged = run_metrics.get("lightgbm_test_pr_auc")
    matches = logged is not None and math.isclose(logged, test["pr_auc"], abs_tol=1e-9)
    if logged is not None and not matches:
        log.warning("recomputed test PR-AUC %.6f != training run %.6f", test["pr_auc"], logged)

    def pick(prefix: str) -> dict[str, float]:
        return {k: v for k, v in sorted(run_metrics.items()) if k.startswith(prefix)}

    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model_name": model.name,
        "model_version": model.version,
        "run_id": model.run_id,
        "prevalence_note": PREVALENCE_NOTE,
        "soft_leak_note": SOFT_LEAK_NOTE,
        "split": {
            "by": "start_year",
            "train": f"<= {train_end}",
            "val": f"{train_end + 1}-{val_end}",
            "test": f"{val_end + 1}-{test_end}",
            "dropped": f"> {test_end} or missing start_year",
            "sizes": split.sizes(),
            "positive_rate": {
                name: float(getattr(split, name)[LABEL_COLUMN].mean())
                for name in ("train", "val", "test")
            },
        },
        "test_registered_model": test,
        "test_matches_training_run": matches,
        "pr_auc": {
            split_name: {
                "lightgbm": run_metrics.get(f"lightgbm_{split_name}_pr_auc"),
                "logistic": run_metrics.get(f"logistic_{split_name}_pr_auc"),
                "no_skill": run_metrics.get(f"no_skill_{split_name}_pr_auc"),
            }
            for split_name in ("train", "val", "test")
        },
        "training_run_metrics": {
            "lightgbm": pick("lightgbm_"),
            "logistic": pick("logistic_"),
            "no_skill": pick("no_skill_"),
        },
        "calibration_test": {"n_bins": N_CALIBRATION_BINS, "strategy": "quantile", "bins": bins},
        "global_shap_test": _global_shap(scores),
        "features": {
            "categorical": list(CATEGORICAL_FEATURES),
            "numeric": list(NUMERIC_FEATURES),
        },
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = out_dir / METRICS_FILE
    metrics_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    plot_calibration(
        {f"LightGBM v{model.version}": (y_test, p_test)},
        out_dir / CALIBRATION_FILE,
        title=(
            f"{model.name} v{model.version}: test split (start years "
            f"{val_end + 1}-{test_end}, n={len(split.test)}, "
            f"{int(y_test.sum())} positive)"
        ),
    )
    log.info("wrote %s and %s", metrics_path, out_dir / CALIBRATION_FILE)
    return report
