"""Loader: landing files (or the committed sample) -> ``raw`` tables.

Raw tables are append-only, one row per study (or drug) per load. Loaded files are tracked in
``raw._loaded_files`` so a second run loads nothing. A crash between the data insert and the
tracking insert re-loads that one file on the next run; the dbt staging layer keeps the latest
version per study / drug, so duplicates are harmless.

DuckDB path: read through ``Storage`` and insert. Snowflake path: ``COPY INTO`` from the
external stage ``raw.landing_stage`` (static tier, not executed on the build machine); the
committed sample is not in the stage, so it always goes through the insert path.
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


def snowflake_copy_sql(source: str, key: str) -> str:
    folder, _, filename = key.rpartition("/")
    stage = f"@raw.landing_stage/{folder}/"
    if source == "ctgov":
        return (
            "COPY INTO raw.ctgov_studies (nct_id, last_update_posted, loaded_at, study) FROM ("
            "SELECT $1:protocolSection.identificationModule.nctId::varchar, "
            "TRY_TO_DATE($1:protocolSection.statusModule.lastUpdatePostDateStruct.date::varchar), "
            f"CURRENT_TIMESTAMP()::timestamp_ntz, $1 FROM {stage}) "
            f"FILES = ({_lit(filename)}) FILE_FORMAT = (TYPE = JSON)"
        )
    return (
        "COPY INTO raw.fda_event_counts (drug_name, loaded_at, payload) FROM ("
        "SELECT $1:drug_name::varchar, CURRENT_TIMESTAMP()::timestamp_ntz, $1 "
        f"FROM {stage}) FILES = ({_lit(filename)}) FILE_FORMAT = (TYPE = JSON)"
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
                wh.execute(snowflake_copy_sql(source, key))
                n = 0
            else:
                records = list(read_jsonl_gz(storage, key))
                now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
                table = "ctgov_studies" if source == "ctgov" else "fda_event_counts"
                wh.write_df(_frame(source, records, now), "raw", table)
                n = len(records)
            wh.execute(
                "insert into raw._loaded_files "
                f"values ({_lit(tracked)}, cast(current_timestamp as timestamp), {n})"
            )
            files += 1
            rows += n
    return LoadResult(files_loaded=files, rows_loaded=rows)
