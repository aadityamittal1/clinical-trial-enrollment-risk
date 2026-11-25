"""Metrics and the calibration plot shared by training and evaluation.

PR-AUC (average precision) is the headline metric because the positive class is a
minority; its no-skill value is the positive rate of the split. NOTE: the committed sample
deliberately oversamples terminated trials, so positive rates (and therefore PR-AUC and
the absolute probabilities) are not real-world prevalence.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.metrics import (  # noqa: E402
    average_precision_score,
    brier_score_loss,
    log_loss,
    roc_auc_score,
)

N_CALIBRATION_BINS = 8
PREVALENCE_NOTE = (
    "The sample snapshot oversamples terminated trials, so positive rates, PR-AUC values "
    "and absolute probabilities do not reflect real-world prevalence."
)


def binary_metrics(y: Sequence[int] | np.ndarray | pd.Series, p: np.ndarray) -> dict[str, float]:
    """PR-AUC, its no-skill baseline (positive rate), ROC-AUC, Brier score and log loss."""
    y_arr = np.asarray(y, dtype=int)
    p_arr = np.asarray(p, dtype=float)
    if y_arr.min() == y_arr.max():
        raise ValueError("metrics need both classes in the split")
    return {
        "pr_auc": float(average_precision_score(y_arr, p_arr)),
        "no_skill_pr_auc": float(y_arr.mean()),
        "roc_auc": float(roc_auc_score(y_arr, p_arr)),
        "brier": float(brier_score_loss(y_arr, p_arr)),
        "log_loss": float(log_loss(y_arr, p_arr, labels=[0, 1])),
        "mean_predicted": float(p_arr.mean()),
        "n": float(len(y_arr)),
        "n_positive": float(y_arr.sum()),
    }


def calibration_bins(
    y: Sequence[int] | np.ndarray | pd.Series, p: np.ndarray, n_bins: int = N_CALIBRATION_BINS
) -> list[dict[str, float]]:
    """Quantile bins of predicted probability: mean prediction vs observed positive rate."""
    frame = pd.DataFrame({"y": np.asarray(y, dtype=int), "p": np.asarray(p, dtype=float)})
    frame["bin"] = pd.qcut(frame["p"].rank(method="first"), q=n_bins, labels=False)
    grouped = frame.groupby("bin").agg(
        mean_predicted=("p", "mean"),
        observed_rate=("y", "mean"),
        count=("y", "size"),
        p_min=("p", "min"),
        p_max=("p", "max"),
    )
    return [{str(k): float(v) for k, v in row.items()} for row in grouped.to_dict("records")]


def expected_calibration_error(bins: Sequence[Mapping[str, float]]) -> float:
    total = sum(b["count"] for b in bins)
    return float(
        sum(b["count"] / total * abs(b["mean_predicted"] - b["observed_rate"]) for b in bins)
    )


def plot_calibration(
    curves: Mapping[str, tuple[Any, np.ndarray]],
    path: Path,
    title: str,
    n_bins: int = N_CALIBRATION_BINS,
) -> Path:
    """Reliability diagram (quantile bins) for each named (y, p) pair; writes a PNG."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(6.4, 5.6), dpi=120)
    upper = 0.0
    for name, (y, p) in curves.items():
        bins = calibration_bins(y, p, n_bins)
        xs = [b["mean_predicted"] for b in bins]
        ys = [b["observed_rate"] for b in bins]
        brier = brier_score_loss(np.asarray(y, dtype=int), np.asarray(p, dtype=float))
        ax.plot(xs, ys, marker="o", label=f"{name} (Brier {brier:.3f})")
        upper = max(upper, *xs, *ys)
    upper = min(1.0, upper * 1.1 + 0.02)
    ax.plot([0, upper], [0, upper], linestyle="--", color="grey", label="perfectly calibrated")
    first_y = np.asarray(next(iter(curves.values()))[0], dtype=int)
    ax.axhline(first_y.mean(), linestyle=":", color="black", label="positive rate (no skill)")
    ax.set_xlim(0, upper)
    ax.set_ylim(0, upper)
    ax.set_xlabel(f"Mean predicted probability ({n_bins} quantile bins)")
    ax.set_ylabel("Observed share terminated for low enrollment")
    ax.set_title(title, fontsize=10)
    ax.legend(loc="upper left", fontsize=8)
    fig.text(
        0.01,
        0.01,
        "Sample oversamples terminated trials: rates are not real-world.",
        fontsize=7,
        color="dimgrey",
    )
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(path)
    plt.close(fig)
    return path
