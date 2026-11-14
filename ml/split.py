"""Time-based train / validation / test split by start_year.

Spec, "Leakage traps" -> censoring: recent trials that are already closed skew toward early
stops, so the split is by start year and years too recent for most trials to have finished
are dropped. Defaults: train <= 2016, validate 2017-2018, test 2019-2020, later years (and
rows without a start year) dropped.
"""

from dataclasses import dataclass

import pandas as pd

TRAIN_END = 2016
VAL_END = 2018
TEST_END = 2020


@dataclass(frozen=True)
class Split:
    train: pd.DataFrame
    val: pd.DataFrame
    test: pd.DataFrame
    train_end: int
    val_end: int
    test_end: int

    def sizes(self) -> dict[str, int]:
        return {"train": len(self.train), "val": len(self.val), "test": len(self.test)}


def time_split(
    df: pd.DataFrame,
    train_end: int = TRAIN_END,
    val_end: int = VAL_END,
    test_end: int = TEST_END,
    year_col: str = "start_year",
) -> Split:
    """Split rows by ``year_col``: (-inf, train_end], (train_end, val_end], (val_end, test_end]."""
    if not train_end < val_end < test_end:
        raise ValueError(
            f"boundaries must satisfy train_end < val_end < test_end, "
            f"got {train_end}, {val_end}, {test_end}"
        )
    year = pd.to_numeric(df[year_col], errors="coerce")

    def part(mask: "pd.Series[bool]") -> pd.DataFrame:
        return df[mask.fillna(False).astype(bool)].reset_index(drop=True).copy()

    return Split(
        train=part(year <= train_end),
        val=part((year > train_end) & (year <= val_end)),
        test=part((year > val_end) & (year <= test_end)),
        train_end=train_end,
        val_end=val_end,
        test_end=test_end,
    )
