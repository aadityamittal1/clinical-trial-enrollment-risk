"""The single definition of the model's inputs, shared by training and scoring.

Features come from ``marts.ml_features`` (registration-time only, see the dbt model). The
label comes from ``marts.fct_trial_outcomes``. ``prepare_features`` is the one place raw
rows become model input; it runs as the first step of every model pipeline, so training,
batch scoring and the API apply exactly the same handling:

* categoricals -> string, NULL -> ``MISSING_CATEGORY`` (unseen values are handled by the
  encoders in the pipelines);
* booleans and counts -> float64, NULL -> NaN.

Leakage guard (spec, "Leakage traps"): enrollment count, completion dates and anything
derived from them, last-update date, results flags, why_stopped and the outcome itself
are never model inputs. ``check_no_leakage`` raises ``LeakageError`` if such a column is
requested.
"""

import re
from collections.abc import Iterable, Sequence

import numpy as np
import pandas as pd

from common.warehouse import Warehouse

CATEGORICAL_FEATURES: tuple[str, ...] = (
    "phase",
    "allocation",
    "masking",
    "primary_purpose",
    "sponsor_class",
    "sex",
    "condition_category",
)

# Booleans are numeric 0/1 (NaN when not reported).
BOOLEAN_FEATURES: tuple[str, ...] = (
    "has_drug",
    "has_biological",
    "has_device",
    "has_procedure",
    "has_behavioral",
    "has_other_intervention",
    "has_dmc",
    "healthy_volunteers",
    "us_only",
)

COUNT_FEATURES: tuple[str, ...] = (
    "n_arms",
    "n_collaborators",
    "min_age_years",
    "max_age_years",
    "n_inclusion_criteria",
    "n_exclusion_criteria",
    "criteria_text_length",
    # Known soft leak (spec): the site list can change after registration.
    "n_sites",
    "n_countries",
    "n_conditions",
    "start_year",
)

NUMERIC_FEATURES: tuple[str, ...] = BOOLEAN_FEATURES + COUNT_FEATURES
FEATURES: tuple[str, ...] = CATEGORICAL_FEATURES + NUMERIC_FEATURES

ID_COLUMN = "nct_id"
LABEL_COLUMN = "label"
MISSING_CATEGORY = "__missing__"

# Column-name patterns that must never be model inputs.
LEAKAGE_PATTERNS: tuple[str, ...] = (
    r"enrol",  # enrollment count / type: overwritten with the actual count at close
    r"completion",  # completion dates flip ESTIMATED -> ACTUAL
    r"duration",  # durations built on completion dates
    r"last_?update",  # lastUpdatePostDate
    r"result",  # results-posted flags
    r"why_?stopped",
    r"status",  # overall status is the outcome
    r"outcome",
    r"^label$",
)
_LEAKAGE_RE = re.compile("|".join(f"(?:{p})" for p in LEAKAGE_PATTERNS), re.IGNORECASE)


class LeakageError(ValueError):
    """A column that encodes the outcome was requested as a model input."""


def check_no_leakage(columns: Iterable[str]) -> None:
    leaky = [c for c in columns if _LEAKAGE_RE.search(c)]
    if leaky:
        raise LeakageError(
            f"leakage-named column(s) requested as model input: {', '.join(leaky)} "
            "(spec 'Leakage traps': enrollment, completion/duration, last update, results, "
            "why_stopped and status/outcome are never features)"
        )


check_no_leakage(FEATURES)


def select_sql(columns: Sequence[str] = FEATURES, where: str | None = None) -> str:
    """SQL selecting ``nct_id`` plus ``columns`` from marts.ml_features (alias ``f``)."""
    check_no_leakage(columns)
    cols = ", ".join(f"f.{c}" for c in (ID_COLUMN, *columns))
    sql = f"select {cols} from marts.ml_features as f"
    if where:
        sql += f" {where}"
    return sql


def load_labelled(warehouse: Warehouse) -> pd.DataFrame:
    """Features + label for every labelled study (label 1 = low-enrollment termination)."""
    check_no_leakage(FEATURES)
    cols = ", ".join(f"f.{c}" for c in (ID_COLUMN, *FEATURES))
    sql = (
        f"select {cols}, o.label from marts.ml_features as f "
        "join marts.fct_trial_outcomes as o on f.nct_id = o.nct_id "
        "where o.label is not null order by f.nct_id"
    )
    df = warehouse.query_df(sql)
    df[LABEL_COLUMN] = df[LABEL_COLUMN].astype(int)
    return df


def load_open(warehouse: Warehouse) -> pd.DataFrame:
    """Features for every open study (dim_study.is_open: RECRUITING / NOT_YET_RECRUITING)."""
    return warehouse.query_df(
        select_sql(
            FEATURES,
            where=(
                "join marts.dim_study as d on f.nct_id = d.nct_id where d.is_open order by f.nct_id"
            ),
        )
    )


def _to_float(s: pd.Series) -> "pd.Series[float]":
    if s.dtype == object or str(s.dtype) == "boolean":
        s = s.map(lambda v: np.nan if v is None or v is pd.NA else v)
    return pd.to_numeric(s, errors="coerce").astype(np.float64)


def _to_category_str(s: pd.Series) -> pd.Series:
    out = s.astype(object).where(s.notna(), MISSING_CATEGORY)
    return out.map(str).astype(object)


def prepare_features(df: pd.DataFrame) -> pd.DataFrame:
    """Raw ml_features rows -> model input (FEATURES columns, fixed dtypes, same order)."""
    missing = [c for c in FEATURES if c not in df.columns]
    if missing:
        raise KeyError(f"feature column(s) missing from input: {', '.join(missing)}")
    out = pd.DataFrame(index=df.index)
    for c in CATEGORICAL_FEATURES:
        out[c] = _to_category_str(df[c])
    for c in NUMERIC_FEATURES:
        out[c] = _to_float(df[c])
    return out
