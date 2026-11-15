"""Loader: landing files (or the committed sample) -> ``raw`` tables.

Raw tables are append-only, one row per study (or drug) per load. Loaded files are tracked in
``raw._loaded_files`` so a second run loads nothing.

Crash safety: on DuckDB the rows and the tracking row are written in one transaction, so a
crash never half-loads a file. Snowflake caveat of the raw contract: ``COPY INTO`` and the
tracking insert are separate statements (no multi-statement transaction through this
interface), so a crash between them re-loads that file; Snowflake's own load history usually
skips already-copied files, and in any case the dbt staging layer keeps the latest version
per ``nct_id`` / drug, so duplicates are harmless.

DuckDB path: read through ``Storage`` and insert. Snowflake path: ``COPY INTO`` from the
external stage ``raw.landing_stage`` (static tier, not executed on the build machine); the
committed sample is not in the stage, so it always goes through the insert path. On
Snowflake the insert path writes the frame (JSON as text) to a staging table
``raw._load_<table>`` and then inserts into the raw table with ``parse_json``, because
``write_pandas`` would land the JSON text as a VARIANT *string* that every ``get_path`` in
dbt reads as NULL (static tier as well: checked with a mocked warehouse only).
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import pandas as pd

from common.storage import Storage, read_jsonl_gz
from common.warehouse import Warehouse
from ingestion.ctgov import study_nct_id, study_update_date

Dialect = Literal["duckdb", "snowflake"]
SOURCES = ("ctgov", "fda")

_DDL: dict[str, list[str]] = {
    "duckdb": [
        "create schema if not exists raw",
        "create table if not exists raw.ctgov_studies "
        "(nct_id varchar, last_update_posted date, loaded_at timestamp, study json)",
        "create table if not exists raw.fda_event_counts "
        "(drug_name varchar, loaded_at timestamp, payload json)",
        "create table if not exists raw._loaded_files "
        "(file_key varchar, loaded_at timestamp, n_rows bigint)",
    ],
    "snowflake": [
        "create schema if not exists raw",
        "create table if not exists raw.ctgov_studies "
        "(nct_id varchar, last_update_posted date, loaded_at timestamp, study variant)",
        "create table if not exists raw.fda_event_counts "
        "(drug_name varchar, loaded_at timestamp, payload variant)",
        "create table if not exists raw._loaded_files "
        "(file_key varchar, loaded_at timestamp, n_rows bigint)",
    ],
}


@dataclass
class LoadResult:
    files_loaded: int
    rows_loaded: int


def ensure_raw_tables(wh: Warehouse, dialect: Dialect = "duckdb") -> None:
    for stmt in _DDL[dialect]:
        wh.execute(stmt)


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _track_sql(tracked: str, n: int) -> str:
    return (
        "insert into raw._loaded_files "
        f"values ({_lit(tracked)}, cast(current_timestamp as timestamp), {n})"
    )


def snowflake_copy_sql(source: str, key: str) -> str:
    folder, _, filename = key.rpartition("/")
    stage = f"@raw.landing_stage/{folder}/"
    if source == "ctgov":
        return (
            "COPY INTO raw.ctgov_studies (nct_id, last_update_posted, loaded_at, study) FROM ("
            "SELECT $1:protocolSection.identificationModule.nctId::varchar, "
            "TRY_TO_DATE($1:protocolSection.statusModule.lastUpdatePostDateStruct.date::varchar), "
            f"SYSDATE(), $1 FROM {stage}) "
            f"FILES = ({_lit(filename)}) FILE_FORMAT = (TYPE = JSON)"
        )
    # SYSDATE() is the current UTC time as TIMESTAMP_NTZ (loaded_at is UTC everywhere).
    return (
        "COPY INTO raw.fda_event_counts (drug_name, loaded_at, payload) FROM ("
        "SELECT $1:drug_name::varchar, SYSDATE(), $1 "
        f"FROM {stage}) FILES = ({_lit(filename)}) FILE_FORMAT = (TYPE = JSON)"
    )


# Raw table -> (plain columns, JSON column) for the Snowflake staging-table insert.
_SNOWFLAKE_INSERT_COLUMNS = {
    "ctgov_studies": ("nct_id, last_update_posted, loaded_at", "study"),
    "fda_event_counts": ("drug_name, loaded_at", "payload"),
}


def snowflake_staging_table(table: str) -> str:
    return f"_load_{table}"


def snowflake_insert_sql(table: str) -> str:
    """Move one staged frame into ``raw.<table>``, parsing the JSON text into a VARIANT."""
    cols, json_col = _SNOWFLAKE_INSERT_COLUMNS[table]
    return (
        f"insert into raw.{table} ({cols}, {json_col}) "
        f"select {cols}, parse_json({json_col}) from raw.{snowflake_staging_table(table)}"
    )


def _frame(source: str, records: list[dict[str, Any]], now: datetime) -> pd.DataFrame:
    if source == "ctgov":
        rows = []
        for s in records:
            d = study_update_date(s)
            rows.append(
                (
                    study_nct_id(s),
                    datetime.strptime(d, "%Y-%m-%d").date() if d else None,
                    now,
                    json.dumps(s, ensure_ascii=False),
                )
            )
        return pd.DataFrame(rows, columns=["nct_id", "last_update_posted", "loaded_at", "study"])
    rows_fda = [(r["drug_name"], now, json.dumps(r, ensure_ascii=False)) for r in records]
    return pd.DataFrame(rows_fda, columns=["drug_name", "loaded_at", "payload"])


def load_files(
    storage: Storage,
    wh: Warehouse,
    *,
    dialect: Dialect = "duckdb",
    source_label: str = "",
) -> LoadResult:
    """Load every landing file not yet in ``raw._loaded_files``.

    ``source_label`` prefixes the tracked key (``sample/...``) so the committed sample and a
    real landing zone with the same dates never collide.
    """
    ensure_raw_tables(wh, dialect)
    done = set(wh.query_df("select file_key from raw._loaded_files")["file_key"])
    prefix = f"{source_label}/" if source_label else ""
    files = rows = 0
    for source in SOURCES:
        for key in storage.list_keys(f"{source}/"):
            tracked = prefix + key
            if tracked in done or not key.endswith(".jsonl.gz"):
                continue
            if dialect == "snowflake" and not source_label:
                # No cross-statement transaction here: COPY INTO commits on its own.
                wh.execute(snowflake_copy_sql(source, key))
                wh.execute(_track_sql(tracked, 0))
                n = 0
            else:
                records = list(read_jsonl_gz(storage, key))
                now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
                table = "ctgov_studies" if source == "ctgov" else "fda_event_counts"
                frame = _frame(source, records, now)
                if dialect == "snowflake":
                    # Staging write first: write_pandas creates the table (DDL commits on
                    # Snowflake), so it stays outside the insert + tracking transaction.
                    wh.write_df(frame, "raw", snowflake_staging_table(table), mode="replace")
                wh.execute("begin")
                try:
                    if dialect == "snowflake":
                        wh.execute(snowflake_insert_sql(table))
                    else:
                        wh.write_df(frame, "raw", table)
                    wh.execute(_track_sql(tracked, len(records)))
                except BaseException:
                    wh.execute("rollback")
                    raise
                wh.execute("commit")
                if dialect == "snowflake":
                    wh.execute(f"drop table if exists raw.{snowflake_staging_table(table)}")
                n = len(records)
            files += 1
            rows += n
    return LoadResult(files_loaded=files, rows_loaded=rows)
