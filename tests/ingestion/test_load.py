import re
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from common.storage import FileStorage, write_jsonl_gz
from common.warehouse import DuckDBWarehouse
from ingestion import load

REPO = Path(__file__).resolve().parents[2]


def ctgov_study(nct: str, updated: str) -> dict[str, Any]:
    return {
        "protocolSection": {
            "identificationModule": {"nctId": nct},
            "statusModule": {"lastUpdatePostDateStruct": {"date": updated}},
        },
        "hasResults": False,
    }


def fda_rec(name: str) -> dict[str, Any]:
    return {
        "drug_name": name,
        "fetched_at": "2026-11-14T00:00:00+00:00",
        "total_reports": None,
        "results": [{"term": "NAUSEA", "count": 3}],
    }


@pytest.fixture
def landing(tmp_path: Path) -> FileStorage:
    s = FileStorage(tmp_path / "landing")
    write_jsonl_gz(
        s,
        "ctgov/2026-11-14/part-00000.jsonl.gz",
        [ctgov_study("NCT1", "2026-01-02"), ctgov_study("NCT2", "2026-02-03")],
    )
    write_jsonl_gz(s, "fda/2026-11-14/part-00000.jsonl.gz", [fda_rec("aspirin")])
    return s


@pytest.fixture
def wh(tmp_path: Path) -> DuckDBWarehouse:
    return DuckDBWarehouse(tmp_path / "w.duckdb")


def count(wh: DuckDBWarehouse, table: str) -> int:
    return int(wh.query_df(f"select count(*) as n from {table}")["n"][0])


def test_load_creates_tables_and_loads_rows(landing: FileStorage, wh: DuckDBWarehouse) -> None:
    res = load.load_files(landing, wh)
    assert res.files_loaded == 2
    assert count(wh, "raw.ctgov_studies") == 2
    assert count(wh, "raw.fda_event_counts") == 1
    df = wh.query_df(
        "select nct_id, last_update_posted, loaded_at, "
        "json_extract_string(study, '$.protocolSection.identificationModule.nctId') as j "
        "from raw.ctgov_studies order by nct_id"
    )
    assert list(df["nct_id"]) == ["NCT1", "NCT2"] and list(df["j"]) == ["NCT1", "NCT2"]
    assert str(df["last_update_posted"][0])[:10] == "2026-01-02"
    assert df["loaded_at"].notna().all()
    p = wh.query_df(
        "select drug_name, json_extract_string(payload, '$.results[0].term') as t "
        "from raw.fda_event_counts"
    )
    assert p["drug_name"][0] == "aspirin" and p["t"][0] == "NAUSEA"


def test_second_run_loads_nothing(landing: FileStorage, wh: DuckDBWarehouse) -> None:
    load.load_files(landing, wh)
    res = load.load_files(landing, wh)
    assert res.files_loaded == 0
    assert count(wh, "raw.ctgov_studies") == 2
    assert count(wh, "raw._loaded_files") == 2


def test_new_file_loaded_incrementally(landing: FileStorage, wh: DuckDBWarehouse) -> None:
    load.load_files(landing, wh)
    write_jsonl_gz(
        landing, "ctgov/2026-11-15/part-00000.jsonl.gz", [ctgov_study("NCT1", "2026-06-01")]
    )
    res = load.load_files(landing, wh)
    assert res.files_loaded == 1
    assert count(wh, "raw.ctgov_studies") == 3


def test_empty_landing_still_creates_tables(tmp_path: Path, wh: DuckDBWarehouse) -> None:
    res = load.load_files(FileStorage(tmp_path / "none"), wh)
    assert res.files_loaded == 0
    assert count(wh, "raw.ctgov_studies") == 0


def test_snowflake_copy_sql_shape() -> None:
    sql = load.snowflake_copy_sql("ctgov", "ctgov/2026-11-14/part-00000.jsonl.gz")
    assert "COPY INTO raw.ctgov_studies" in sql
    assert "@raw.landing_stage/ctgov/2026-11-14/" in sql
    assert "part-00000.jsonl.gz" in sql
    assert "raw.fda_event_counts" in load.snowflake_copy_sql("fda", "fda/2026-11-14/part-0.gz")
    for source in ("ctgov", "fda"):
        copy = load.snowflake_copy_sql(source, f"{source}/2026-11-14/part-0.jsonl.gz")
        assert "SYSDATE()" in copy  # loaded_at in UTC
        assert "CURRENT_TIMESTAMP" not in copy


class RecordingWarehouse:
    """Mocked Snowflake warehouse: records SQL and write_df calls, runs nothing."""

    def __init__(self) -> None:
        self.sql: list[str] = []
        self.writes: list[tuple[str, str, str, list[str], list[Any]]] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.sql.append(sql)

    def query_df(self, sql: str) -> pd.DataFrame:
        self.sql.append(sql)
        return pd.DataFrame({"file_key": pd.Series([], dtype=str)})

    def write_df(self, df: pd.DataFrame, schema: str, table: str, mode: str = "append") -> None:
        col = "study" if "study" in df.columns else "payload"
        self.writes.append((schema, table, mode, list(df.columns), list(df[col])))

    def close(self) -> None:
        pass


def test_snowflake_insert_path_parses_json(landing: FileStorage) -> None:
    wh = RecordingWarehouse()
    res = load.load_files(landing, wh, dialect="snowflake", source_label="sample")
    assert res.files_loaded == 2 and res.rows_loaded == 3
    # The frames go to staging tables (replace), never straight into the VARIANT tables.
    staged = {(schema, table, mode) for schema, table, mode, _, _ in wh.writes}
    assert staged == {
        ("raw", "_load_ctgov_studies", "replace"),
        ("raw", "_load_fda_event_counts", "replace"),
    }
    for _, _, _, _, json_values in wh.writes:
        assert all(isinstance(v, str) for v in json_values)  # JSON text in a string column
    inserts = [s for s in wh.sql if s.startswith("insert into raw.") and "_loaded_files" not in s]
    assert len(inserts) == 2
    ctgov = next(s for s in inserts if "raw.ctgov_studies" in s)
    fda = next(s for s in inserts if "raw.fda_event_counts" in s)
    assert "parse_json(study)" in ctgov and "from raw._load_ctgov_studies" in ctgov
    assert "parse_json(payload)" in fda and "from raw._load_fda_event_counts" in fda
    assert "drop table if exists raw._load_ctgov_studies" in wh.sql
    assert "drop table if exists raw._load_fda_event_counts" in wh.sql
    # Insert and tracking row share one transaction; the staging table is dropped after commit.
    i = wh.sql.index(ctgov)
    assert wh.sql[i - 1] == "begin"
    assert wh.sql[i + 1].startswith("insert into raw._loaded_files")
    assert wh.sql[i + 2] == "commit"
    assert wh.sql[i + 3] == "drop table if exists raw._load_ctgov_studies"


def test_duckdb_path_does_not_parse_json(landing: FileStorage, tmp_path: Path) -> None:
    seen: list[str] = []

    class Spy(DuckDBWarehouse):
        def execute(self, sql: str, params: Any = None) -> None:
            seen.append(sql)
            super().execute(sql, params)

    spy = Spy(tmp_path / "spy.duckdb")
    load.load_files(landing, spy)
    assert seen
    assert not [s for s in seen if "parse_json" in s.lower() or "_load_" in s]
    assert count(spy, "raw.ctgov_studies") == 2
    spy.close()


def test_committed_sample_matches_readme(tmp_path: Path) -> None:
    readme = (REPO / "data" / "sample" / "README.md").read_text(encoding="utf-8")
    m_ctgov = re.search(r"Total studies:\s*(\d+)", readme)
    m_fda = re.search(r"openFDA drugs:\s*(\d+)", readme)
    assert m_ctgov and m_fda
    wh = DuckDBWarehouse(tmp_path / "s.duckdb")
    load.load_files(FileStorage(REPO / "data" / "sample"), wh, source_label="sample")
    assert count(wh, "raw.ctgov_studies") == int(m_ctgov.group(1))
    assert count(wh, "raw.fda_event_counts") == int(m_fda.group(1))
    wh.close()


def test_failed_tracking_insert_rolls_back_rows(landing: FileStorage, tmp_path: Path) -> None:
    class Flaky(DuckDBWarehouse):
        def execute(self, sql: str, params: Any = None) -> None:
            if sql.startswith("insert into raw._loaded_files"):
                raise RuntimeError("crash")
            super().execute(sql, params)

    flaky = Flaky(tmp_path / "flaky.duckdb")
    with pytest.raises(RuntimeError):
        load.load_files(landing, flaky)
    assert count(flaky, "raw.ctgov_studies") == 0
    assert count(flaky, "raw.fda_event_counts") == 0
    flaky.close()


def test_cli_load_sample_into_fresh_duckdb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from ingestion.__main__ import main

    monkeypatch.chdir(REPO)
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "cli.duckdb"))
    assert main(["load", "--source", "sample"]) == 0
    assert "load:" in capsys.readouterr().out
    wh = DuckDBWarehouse(tmp_path / "cli.duckdb")
    assert count(wh, "raw.ctgov_studies") > 0 and count(wh, "raw.fda_event_counts") > 0
    wh.close()
