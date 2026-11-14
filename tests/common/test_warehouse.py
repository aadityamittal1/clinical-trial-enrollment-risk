from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from common.config import Settings
from common.warehouse import DuckDBWarehouse, SnowflakeWarehouse, get_warehouse


@pytest.fixture
def wh(tmp_path: Path) -> Iterator[DuckDBWarehouse]:
    w = DuckDBWarehouse(tmp_path / "nested" / "dir" / "t.duckdb")
    yield w
    w.close()


def test_write_creates_parent_dirs_schema_and_table(wh: DuckDBWarehouse, tmp_path: Path) -> None:
    wh.write_df(pd.DataFrame({"a": [1, 2], "b": ["x", "y"]}), "raw", "t")
    assert (tmp_path / "nested" / "dir" / "t.duckdb").exists()
    df = wh.query_df("select * from raw.t order by a")
    assert df["a"].tolist() == [1, 2]
    assert df["b"].tolist() == ["x", "y"]


def test_append_and_replace(wh: DuckDBWarehouse) -> None:
    wh.write_df(pd.DataFrame({"a": [1]}), "raw", "t")
    wh.write_df(pd.DataFrame({"a": [2]}), "raw", "t", mode="append")
    assert wh.query_df("select a from raw.t order by a")["a"].tolist() == [1, 2]
    wh.write_df(pd.DataFrame({"a": [9]}), "raw", "t", mode="replace")
    assert wh.query_df("select a from raw.t")["a"].tolist() == [9]


def test_append_matches_columns_by_name(wh: DuckDBWarehouse) -> None:
    wh.write_df(pd.DataFrame({"a": [1], "b": ["x"]}), "raw", "t")
    wh.write_df(pd.DataFrame({"b": ["y"], "a": [2]}), "raw", "t", mode="append")
    df = wh.query_df("select a, b from raw.t order by a")
    assert df["a"].tolist() == [1, 2]
    assert df["b"].tolist() == ["x", "y"]


def test_execute_with_params(wh: DuckDBWarehouse) -> None:
    wh.execute("create schema s")
    wh.execute("create table s.t (a integer)")
    wh.execute("insert into s.t values (?)", [5])
    assert wh.query_df("select a from s.t")["a"].tolist() == [5]


def test_invalid_mode(wh: DuckDBWarehouse) -> None:
    with pytest.raises(ValueError):
        wh.write_df(pd.DataFrame({"a": [1]}), "raw", "t", mode="nope")  # type: ignore[arg-type]


def test_get_warehouse_duckdb(tmp_path: Path) -> None:
    s = Settings(_env_file=None, duckdb_path=str(tmp_path / "w.duckdb"))  # type: ignore[call-arg]
    w = get_warehouse(s)
    assert isinstance(w, DuckDBWarehouse)
    w.close()


def test_snowflake_construction_and_execute() -> None:
    s = Settings(  # type: ignore[call-arg]
        _env_file=None,
        mode="cloud",
        snowflake_account="a",
        snowflake_user="u",
        snowflake_password="p",
        snowflake_role="r",
        snowflake_warehouse="w",
        snowflake_database="d",
    )
    fake = MagicMock()
    parent = MagicMock(connector=fake)
    with patch.dict("sys.modules", {"snowflake": parent, "snowflake.connector": fake}):
        wh = get_warehouse(s)
        assert isinstance(wh, SnowflakeWarehouse)
        fake.connect.assert_called_once_with(
            account="a", user="u", password="p", role="r", warehouse="w", database="d"
        )
        wh.execute("select 1")
        fake.connect.return_value.cursor.return_value.execute.assert_called_once_with(
            "select 1", None
        )
        wh.close()
        fake.connect.return_value.close.assert_called_once()


def test_snowflake_write_df_uses_unquoted_uppercase_identifiers() -> None:
    s = Settings(_env_file=None, mode="cloud")  # type: ignore[call-arg]
    fake = MagicMock()
    tools = MagicMock()
    parent = MagicMock(connector=fake)
    mods = {
        "snowflake": parent,
        "snowflake.connector": fake,
        "snowflake.connector.pandas_tools": tools,
    }
    with patch.dict("sys.modules", mods):
        wh = get_warehouse(s)
        df = pd.DataFrame({"nct_id": ["N1"], "risk_score": [0.5]})
        wh.write_df(df, "marts", "ml_scores", mode="append")
        args, kwargs = tools.write_pandas.call_args
        assert args[0] is fake.connect.return_value
        assert list(args[1].columns) == ["NCT_ID", "RISK_SCORE"]
        assert args[2] == "ML_SCORES"
        assert kwargs["schema"] == "MARTS"
        assert kwargs["quote_identifiers"] is False
        assert kwargs["overwrite"] is False
        wh.write_df(df, "marts", "ml_scores", mode="replace")
        assert tools.write_pandas.call_args.kwargs["overwrite"] is True
        assert list(df.columns) == ["nct_id", "risk_score"]  # caller's frame untouched
