"""Small synthetic ml_features-shaped frames for fast unit tests."""

import numpy as np
import pandas as pd

from ml.features import BOOLEAN_FEATURES, CATEGORICAL_FEATURES, COUNT_FEATURES

CATEGORY_VALUES = {
    "phase": ["NA", "PHASE1", "PHASE2", "PHASE3"],
    "allocation": ["RANDOMIZED", "NA", "NON_RANDOMIZED"],
    "masking": ["NONE", "SINGLE", "DOUBLE"],
    "primary_purpose": ["TREATMENT", "PREVENTION", "OTHER"],
    "sponsor_class": ["OTHER", "INDUSTRY", "NIH"],
    "sex": ["ALL", "FEMALE", "MALE"],
    "condition_category": ["Neoplasms", "Unknown", "Infections"],
}


def synthetic_features(n: int, seed: int = 0) -> pd.DataFrame:
    """Raw rows like marts.ml_features plus a ``label`` driven by n_sites and sponsor_class."""
    rng = np.random.default_rng(seed)
    data: dict[str, object] = {"nct_id": [f"NCT{i:08d}" for i in range(n)]}
    for c in CATEGORICAL_FEATURES:
        values = rng.choice(CATEGORY_VALUES[c], size=n).astype(object)
        values[rng.random(n) < 0.05] = None
        data[c] = values
    for c in BOOLEAN_FEATURES:
        values = (rng.random(n) < 0.5).astype(object)
        values[rng.random(n) < 0.1] = None
        data[c] = values
    for c in COUNT_FEATURES:
        data[c] = rng.integers(0, 30, size=n).astype(float)
    data["start_year"] = rng.integers(2008, 2021, size=n).astype(float)
    df = pd.DataFrame(data)
    logit = -1.0 - 0.25 * (df["n_sites"] - 15) + np.where(df["sponsor_class"] == "INDUSTRY", -1, 1)
    df["label"] = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    return df
