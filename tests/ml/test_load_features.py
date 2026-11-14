"""load_features: validated NCT ids, safe query, one row per known study."""

from pathlib import Path
from typing import Any

import pytest

from common.warehouse import DuckDBWarehouse
from ml.features import FEATURES, ID_COLUMN, load_features, validate_nct_ids
from tests.ml._helpers import copy_warehouse


class NoQueryWarehouse:
    """Fails the test if any SQL is issued."""

    def query_df(self, sql: str) -> Any:
        raise AssertionError(f"query issued: {sql}")


@pytest.mark.parametrize(
    "bad",
    [
        "NCT0000000",  # 7 digits
        "NCT000000001",  # 9 digits
        "nct00000001",
        "NCT00000001 ",
        "NCT00000001\n",
        "NCT0000000١",  # non-ASCII digit
        "NCT00000001' or '1'='1",
        "'; drop table marts.ml_features; --",
        "",
        None,
        12345678,
    ],
)
def test_invalid_ids_raise_before_any_query(bad: Any) -> None:
    with pytest.raises(ValueError, match="invalid NCT id"):
        load_features(NoQueryWarehouse(), ["NCT00000001", bad])  # type: ignore[arg-type]


def test_validate_dedupes_and_keeps_order() -> None:
    assert validate_nct_ids(["NCT00000002", "NCT00000001", "NCT00000002"]) == [
        "NCT00000002",
        "NCT00000001",
    ]


def test_load_features_from_sample_warehouse(sample_warehouse: Path, tmp_path: Path) -> None:
    wh = DuckDBWarehouse(copy_warehouse(sample_warehouse, tmp_path))
    try:
        known = wh.query_df("select nct_id from marts.ml_features order by nct_id limit 3")
        ids = known["nct_id"].tolist()
        df = load_features(wh, [ids[2], ids[0], "NCT99999999", ids[0]])
        empty = load_features(wh, [])
    finally:
        wh.close()
    assert list(df.columns) == [ID_COLUMN, *FEATURES]
    assert df[ID_COLUMN].tolist() == [ids[0], ids[2]]  # unknown id: no row
    assert list(empty.columns) == [ID_COLUMN, *FEATURES]
    assert empty.empty
