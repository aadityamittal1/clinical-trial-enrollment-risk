"""Feature list, leakage guard and the shared frame preparation."""

import numpy as np
import pandas as pd
import pytest

from ml import features
from ml.features import (
    CATEGORICAL_FEATURES,
    FEATURES,
    NUMERIC_FEATURES,
    LeakageError,
    check_no_leakage,
    prepare_features,
    select_sql,
)


def test_feature_list_is_categorical_plus_numeric_without_duplicates() -> None:
    assert list(FEATURES) == [*CATEGORICAL_FEATURES, *NUMERIC_FEATURES]
    assert len(set(FEATURES)) == len(FEATURES)
    assert "nct_id" not in FEATURES


def test_spec_features_are_present() -> None:
    # Spec "Features (registration-time only)": design, sponsor, population, footprint,
    # topic, time.
    for name in (
        "phase",
        "allocation",
        "masking",
        "primary_purpose",
        "n_arms",
        "has_drug",
        "sponsor_class",
        "n_collaborators",
        "has_dmc",
        "min_age_years",
        "max_age_years",
        "sex",
        "healthy_volunteers",
        "n_inclusion_criteria",
        "n_exclusion_criteria",
        "criteria_text_length",
        "n_sites",
        "n_countries",
        "us_only",
        "condition_category",
        "n_conditions",
        "start_year",
    ):
        assert name in FEATURES, name


@pytest.mark.parametrize(
    "column",
    [
        "enrollment_count",
        "enrollment_type",
        "completion_date",
        "primary_completion_date",
        "planned_duration_days",
        "last_update_posted",
        "last_update_post_date",
        "has_results",
        "results_first_posted",
        "why_stopped",
        "overall_status",
        "outcome_class",
        "label",
    ],
)
def test_leakage_guard_rejects_outcome_columns(column: str) -> None:
    with pytest.raises(LeakageError, match=column):
        check_no_leakage(["phase", column])


def test_leakage_guard_accepts_the_feature_list() -> None:
    check_no_leakage(FEATURES)


def test_select_sql_refuses_leaky_columns() -> None:
    with pytest.raises(LeakageError):
        select_sql([*FEATURES, "enrollment_count"])


def test_select_sql_reads_ml_features_only() -> None:
    sql = select_sql(FEATURES)
    assert "marts.ml_features" in sql
    for name in FEATURES:
        assert name in sql


def test_leakage_error_is_a_value_error() -> None:
    assert issubclass(LeakageError, ValueError)
    assert features.LEAKAGE_PATTERNS  # documented, non-empty


def test_prepare_features_types_and_missing_values() -> None:
    raw = pd.DataFrame(
        {
            "nct_id": ["NCT1", "NCT2"],
            **{c: ["X", None] for c in CATEGORICAL_FEATURES},
            **{c: [1, None] for c in NUMERIC_FEATURES},
            "has_dmc": pd.Series([True, None], dtype=object),
            "enrollment_count": [10, 20],  # ignored: never selected
        }
    )
    out = prepare_features(raw)
    assert list(out.columns) == list(FEATURES)
    for c in CATEGORICAL_FEATURES:
        assert out[c].tolist() == ["X", features.MISSING_CATEGORY]
    for c in NUMERIC_FEATURES:
        assert out[c].dtype == np.float64
    assert out["has_dmc"].iloc[0] == 1.0
    assert np.isnan(out["has_dmc"].iloc[1])


def test_prepare_features_fails_on_missing_columns() -> None:
    with pytest.raises(KeyError, match="phase"):
        prepare_features(pd.DataFrame({"nct_id": ["NCT1"]}))
