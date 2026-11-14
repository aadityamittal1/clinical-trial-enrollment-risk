"""Batch scoring: every open study (dim_study.is_open) -> append to marts.ml_scores.

marts.ml_scores is append-only (one row per study per scoring run); dbt's
ml_scores_latest view picks the latest score per study. The table is normally created
empty by a dbt on-run-start hook (dbt/macros/ml_scores.sql); ``ensure_ml_scores_table``
creates it with the same DDL when it is missing, so scoring also works on its own.
"""

import logging
from datetime import UTC, datetime

import pandas as pd

from common.config import Settings, get_settings
from common.warehouse import Warehouse, get_warehouse
from ml.features import load_open
from ml.predict import LoadedModel, load_model, score_frame

log = logging.getLogger(__name__)

# Same columns, order and types as the dbt hook (dbt/macros/ml_scores.sql).
ML_SCORES_COLUMNS: list[tuple[str, str]] = [
    ("nct_id", "varchar"),
    ("risk_score", "double"),
    ("model_version", "varchar"),
    ("scored_at", "timestamp"),
    ("top_driver_1", "varchar"),
    ("top_driver_2", "varchar"),
    ("top_driver_3", "varchar"),
    ("top_driver_1_shap", "double"),
    ("top_driver_2_shap", "double"),
    ("top_driver_3_shap", "double"),
    ("shap_values", "varchar"),
]


def ensure_ml_scores_table(warehouse: Warehouse) -> None:
    columns = ", ".join(f"{name} {kind}" for name, kind in ML_SCORES_COLUMNS)
    warehouse.execute("create schema if not exists marts")
    warehouse.execute(f"create table if not exists marts.ml_scores ({columns})")


def build_score_rows(
    model: LoadedModel, features_df: pd.DataFrame, scored_at: datetime
) -> pd.DataFrame:
    """score_frame output plus model_version and scored_at, in ml_scores column order."""
    scores = score_frame(model, features_df)
    scores["model_version"] = model.version
    scores["scored_at"] = pd.Series([scored_at] * len(scores), dtype="datetime64[us]")
    return scores[[name for name, _ in ML_SCORES_COLUMNS]]


def score(settings: Settings | None = None, model: LoadedModel | None = None) -> int:
    """Score all open studies with the latest registered model; return rows appended."""
    settings = settings or get_settings()
    model = model or load_model(settings)
    warehouse = get_warehouse(settings)
    try:
        features_df = load_open(warehouse)
        # UTC, stored as a naive timestamp (dbt contract: "Scoring timestamp (UTC)").
        scored_at = datetime.now(UTC).replace(tzinfo=None)
        rows = build_score_rows(model, features_df, scored_at)
        ensure_ml_scores_table(warehouse)
        if not rows.empty:
            warehouse.write_df(rows, "marts", "ml_scores", mode="append")
    finally:
        warehouse.close()
    log.info("scored %d open studies with %s version %s", len(rows), model.name, model.version)
    return len(rows)
