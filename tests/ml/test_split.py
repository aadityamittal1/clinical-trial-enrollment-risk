"""Time-based split by start_year (spec, "Leakage traps" -> censoring)."""

from collections.abc import Sequence

import pandas as pd
import pytest

from ml.split import time_split


def frame(years: Sequence[int | None]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "nct_id": [f"NCT{i:08d}" for i in range(len(years))],
            "start_year": pd.array(years, dtype="Int64"),
            "label": [i % 2 for i in range(len(years))],
        }
    )


def test_default_boundaries_train_le_2016_val_2017_2018_test_2019_2020() -> None:
    df = frame(list(range(2005, 2025)))
    split = time_split(df)
    assert split.train["start_year"].max() == 2016
    assert split.train["start_year"].min() == 2005
    assert sorted(split.val["start_year"].unique()) == [2017, 2018]
    assert sorted(split.test["start_year"].unique()) == [2019, 2020]


def test_later_years_and_missing_start_year_are_dropped() -> None:
    df = frame([2015, 2021, 2024, None, 2019])
    split = time_split(df)
    kept = pd.concat([split.train, split.val, split.test])
    assert sorted(kept["start_year"].tolist()) == [2015, 2019]


def test_splits_are_disjoint_and_cover_kept_rows() -> None:
    df = frame(list(range(2008, 2023)) * 3)
    split = time_split(df)
    ids = [set(part["nct_id"]) for part in (split.train, split.val, split.test)]
    assert ids[0].isdisjoint(ids[1])
    assert ids[0].isdisjoint(ids[2])
    assert ids[1].isdisjoint(ids[2])
    assert sum(len(i) for i in ids) == len(df[df["start_year"] <= 2020])


def test_boundaries_are_parameters() -> None:
    df = frame(list(range(2008, 2021)))
    split = time_split(df, train_end=2012, val_end=2015, test_end=2018)
    assert split.train["start_year"].max() == 2012
    assert sorted(split.val["start_year"].unique()) == [2013, 2014, 2015]
    assert sorted(split.test["start_year"].unique()) == [2016, 2017, 2018]


def test_boundaries_must_be_increasing() -> None:
    df = frame([2010])
    with pytest.raises(ValueError, match="train_end < val_end < test_end"):
        time_split(df, train_end=2018, val_end=2017, test_end=2020)
    with pytest.raises(ValueError, match="train_end < val_end < test_end"):
        time_split(df, train_end=2016, val_end=2018, test_end=2018)


def test_split_returns_copies_with_fresh_index() -> None:
    df = frame([2010, 2019, 2017])
    split = time_split(df)
    assert list(split.test.index) == [0]
    split.test.loc[0, "label"] = 99
    assert 99 not in df["label"].tolist()
