"""Warehouse abstraction: DuckDB locally, Snowflake in cloud mode."""

from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from typing import Any, Literal, Protocol

import duckdb
import pandas as pd

from common.config import Settings

WriteMode = Literal["append", "replace"]


class Warehouse(Protocol):
    def execute(self, sql: str, params: Sequence[Any] | None = None) -> None: ...

    def query_df(self, sql: str) -> pd.DataFrame: ...

    def write_df(
        self, df: pd.DataFrame, schema: str, table: str, mode: WriteMode = "append"
    ) -> None: ...

    def close(self) -> None: ...


def _check_mode(mode: str) -> None:
    if mode not in ("append", "replace"):
        raise ValueError(f"mode must be 'append' or 'replace', got {mode!r}")


class DuckDBWarehouse:
    def __init__(self, path: str | Path) -> None:
        db_path = Path(path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(db_path))

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> None:
        self._con.execute(sql, params)

    def query_df(self, sql: str) -> pd.DataFrame:
        return self._con.execute(sql).df()

    def write_df(
        self, df: pd.DataFrame, schema: str, table: str, mode: WriteMode = "append"
    ) -> None:
        _check_mode(mode)
        self._con.execute(f'create schema if not exists "{schema}"')
        self._con.register("_df_in", df)
        try:
            target = f'"{schema}"."{table}"'
            if mode == "replace":
                self._con.execute(f"create or replace table {target} as select * from _df_in")
            else:
                self._con.execute(
                    f"create table if not exists {target} as select * from _df_in where 1=0"
                )
                self._con.execute(f"insert into {target} by name select * from _df_in")
        finally:
            self._con.unregister("_df_in")

    def close(self) -> None:
        self._con.close()


class SnowflakeWarehouse:
    def __init__(self, settings: Settings) -> None:
        import snowflake.connector  # lazy: the `cloud` extra is optional

        self._con = snowflake.connector.connect(
            account=settings.snowflake_account,
            user=settings.snowflake_user,
            password=settings.snowflake_password,
            role=settings.snowflake_role,
            warehouse=settings.snowflake_warehouse,
            database=settings.snowflake_database,
        )

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> None:
        with closing(self._con.cursor()) as cur:
            cur.execute(sql, params)

    def query_df(self, sql: str) -> pd.DataFrame:
        with closing(self._con.cursor()) as cur:
            cur.execute(sql)
            df: pd.DataFrame = cur.fetch_pandas_all()
        return df.rename(columns=str.lower)

    def write_df(
        self, df: pd.DataFrame, schema: str, table: str, mode: WriteMode = "append"
    ) -> None:
        _check_mode(mode)
        from snowflake.connector.pandas_tools import write_pandas

        self.execute(f"create schema if not exists {schema}")
        write_pandas(
            self._con,
            df,
            table.upper(),
            schema=schema.upper(),
            auto_create_table=True,
            overwrite=(mode == "replace"),
        )

    def close(self) -> None:
        self._con.close()


def get_warehouse(settings: Settings) -> Warehouse:
    if settings.warehouse == "snowflake":
        return SnowflakeWarehouse(settings)
    return DuckDBWarehouse(settings.duckdb_path)
