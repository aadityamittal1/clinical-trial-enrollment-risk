"""Label hand-check sample export and precision."""

from pathlib import Path

import pandas as pd
import pytest

from common.warehouse import DuckDBWarehouse
from ml.labels import SAMPLE_COLUMNS, export_label_sample, label_precision
from tests.ml._helpers import copy_warehouse


def test_export_200_matches_and_non_matches_among_terminated(
    sample_warehouse: Path, tmp_path: Path
) -> None:
    wh = DuckDBWarehouse(copy_warehouse(sample_warehouse, tmp_path / "wh"))
    try:
        out = tmp_path / "label_check" / "sample.csv"
        sample = export_label_sample(wh, out, n=200, seed=42)
        terminated = set(
            wh.query_df(
                "select nct_id from marts.fct_trial_outcomes where overall_status = 'TERMINATED'"
            )["nct_id"]
        )
    finally:
        wh.close()
    written = pd.read_csv(out, keep_default_na=False)
    assert list(written.columns) == SAMPLE_COLUMNS
    assert len(written) == 200
    assert written["nct_id"].is_unique
    assert set(written["nct_id"]) <= terminated
    assert (written["regex_match"] == 1).sum() == 100
    assert (written["regex_match"] == 0).sum() == 100
    assert (written["human_label"] == "").all()
    assert sample["nct_id"].tolist() == written["nct_id"].tolist()


def test_label_precision_from_filled_file(tmp_path: Path) -> None:
    path = tmp_path / "filled.csv"
    pd.DataFrame(
        {
            "nct_id": ["A", "B", "C", "D", "E", "F"],
            "why_stopped": ["low accrual"] * 6,
            "regex_match": [1, 1, 1, 1, 0, 0],
            "human_label": ["1", "yes", "0", "", "1", "0"],
        }
    ).to_csv(path, index=False)
    check = label_precision(path)
    assert check.precision == pytest.approx(2 / 3)
    assert check.n_matches_labelled == 3
    assert check.miss_rate == pytest.approx(0.5)
    assert check.n_non_matches_labelled == 2


def test_label_precision_needs_labelled_matches(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    pd.DataFrame(
        {"nct_id": ["A"], "why_stopped": ["x"], "regex_match": [1], "human_label": [""]}
    ).to_csv(path, index=False)
    with pytest.raises(ValueError, match="no regex matches"):
        label_precision(path)


def test_label_precision_rejects_bad_values(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    pd.DataFrame(
        {"nct_id": ["A"], "why_stopped": ["x"], "regex_match": [1], "human_label": ["maybe"]}
    ).to_csv(path, index=False)
    with pytest.raises(ValueError, match="human_label"):
        label_precision(path)
