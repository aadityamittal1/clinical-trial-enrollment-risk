"""End-to-end dbt test on the committed sample.

Loads data/sample into a temporary DuckDB file (python -m ingestion load --source sample),
runs ``dbt build --target local`` once for the module, then checks the marts: non-empty,
columns equal to the documented contract in dbt/models/marts/_marts.yml, drug-name parity
with ingestion/drug_names.py, the label and the leakage guard.
"""

import ast
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pytest
import yaml

from common.config import Settings
from ingestion import drug_names
from ingestion.drug_names import canonical_drug_name

REPO = Path(__file__).resolve().parents[2]
DBT_DIR = REPO / "dbt"
MARTS_YML = DBT_DIR / "models" / "marts" / "_marts.yml"
EMPTY_MARTS = {"ml_scores_latest"}  # empty until the scoring job has run


@dataclass
class Built:
    duckdb_path: Path
    env: dict[str, str]
    work: Path
    build: subprocess.CompletedProcess[str]


def _env(duckdb_path: Path) -> dict[str, str]:
    """Process env without any project setting (a developer's .env / shell must not leak in)."""
    env = {k: v for k, v in os.environ.items() if k.upper() not in _project_vars()}
    env.update(
        MODE="local",
        STORAGE_BACKEND="file",
        DUCKDB_PATH=str(duckdb_path),
        PYTHONUTF8="1",
        DBT_SEND_ANONYMOUS_USAGE_STATS="false",
    )
    return env


def _project_vars() -> set[str]:
    return {name.upper() for name in Settings.model_fields}


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
        check=False,
    )


def _dbt(cmd: list[str], work: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            sys.executable,
            "-m",
            "dbt.cli.main",
            *cmd,
            "--project-dir",
            str(DBT_DIR),
            "--profiles-dir",
            str(DBT_DIR),
            "--target",
            "local",
            "--target-path",
            str(work / "target"),
            "--log-path",
            str(work / "logs"),
        ],
        env,
    )


def _tail(proc: subprocess.CompletedProcess[str]) -> str:
    return (proc.stdout + proc.stderr)[-6000:]


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Built]:
    work = tmp_path_factory.mktemp("dbt")
    db = work / "warehouse" / "trialrisk.duckdb"
    env = _env(db)
    load = _run([sys.executable, "-m", "ingestion", "load", "--source", "sample"], env)
    assert load.returncode == 0, _tail(load)
    build = _dbt(["build"], work, env)
    yield Built(duckdb_path=db, env=env, work=work, build=build)


@pytest.fixture
def con(built: Built) -> Iterator[duckdb.DuckDBPyConnection]:
    assert built.build.returncode == 0, _tail(built.build)
    c = duckdb.connect(str(built.duckdb_path), read_only=True)
    yield c
    c.close()


def _contract() -> dict[str, list[str]]:
    spec = yaml.safe_load(MARTS_YML.read_text(encoding="utf-8"))
    return {m["name"]: [c["name"] for c in m["columns"]] for m in spec["models"]}


def _columns(con: duckdb.DuckDBPyConnection, schema: str, table: str) -> list[str]:
    rows = con.execute(
        "select column_name from information_schema.columns "
        "where table_schema = ? and table_name = ? order by ordinal_position",
        [schema, table],
    ).fetchall()
    return [r[0] for r in rows]


def test_dbt_build_succeeds(built: Built) -> None:
    assert built.build.returncode == 0, _tail(built.build)
    assert "Completed successfully" in built.build.stdout
    summary = re.search(r"Done\. PASS=(\d+) WARN=(\d+) ERROR=(\d+) SKIP=(\d+)", built.build.stdout)
    assert summary, _tail(built.build)
    assert int(summary.group(1)) > 100
    assert summary.group(3) == "0"
    assert summary.group(4) == "0"


def test_marts_match_the_documented_contract(con: duckdb.DuckDBPyConnection) -> None:
    contract = _contract()
    expected = {
        "dim_study",
        "dim_sponsor",
        "dim_condition",
        "dim_drug",
        "bridge_study_condition",
        "bridge_study_drug",
        "fct_study_sites",
        "fct_trial_outcomes",
        "ml_features",
        "fct_drug_safety",
        "ml_scores_latest",
    }
    assert set(contract) == expected
    for model, columns in contract.items():
        assert _columns(con, "marts", model) == columns, model


def test_marts_are_populated(con: duckdb.DuckDBPyConnection) -> None:
    for model in _contract():
        (n,) = con.execute(f"select count(*) from marts.{model}").fetchone() or (0,)
        if model in EMPTY_MARTS:
            assert n == 0, model
        else:
            assert n > 0, model
    (studies,) = con.execute("select count(*) from marts.dim_study").fetchone() or (0,)
    (features,) = con.execute("select count(*) from marts.ml_features").fetchone() or (0,)
    assert studies == features == 3015


def test_ml_scores_table_created_with_scoring_columns(con: duckdb.DuckDBPyConnection) -> None:
    assert _columns(con, "marts", "ml_scores") == [
        "nct_id",
        "risk_score",
        "model_version",
        "scored_at",
        "top_driver_1",
        "top_driver_2",
        "top_driver_3",
        "top_driver_1_shap",
        "top_driver_2_shap",
        "top_driver_3_shap",
        "shap_values",
    ]


def test_label_has_both_classes(con: duckdb.DuckDBPyConnection) -> None:
    counts = dict(
        con.execute(
            "select outcome_class, count(*) from marts.fct_trial_outcomes group by 1"
        ).fetchall()
    )
    assert counts["completed"] == 1430
    assert counts["withdrawn"] == 60
    assert counts["terminated_low_enrollment"] + counts["terminated_other"] == 585
    assert counts["terminated_low_enrollment"] > 100
    rows = con.execute(
        "select why_stopped, outcome_class from marts.fct_trial_outcomes "
        "where overall_status = 'TERMINATED' and why_stopped in "
        "('Poor accrual', 'Slow enrollment', 'Lack of funding', 'Sponsor decision')"
    ).fetchall()
    classes = {w: c for w, c in rows}
    assert classes.get("Poor accrual") == "terminated_low_enrollment"
    assert classes.get("Lack of funding") == "terminated_other"


def test_stg_drug_names_match_python_normaliser(con: duckdb.DuckDBPyConnection) -> None:
    rows = con.execute("select raw_name, canonical_name from staging.stg_drug_names").fetchall()
    assert len(rows) > 1000
    mismatches = [(raw, sql) for raw, sql in rows if canonical_drug_name(raw) != sql]
    assert mismatches == []


def test_every_fda_drug_is_in_dim_drug(con: duckdb.DuckDBPyConnection) -> None:
    fda = {r[0] for r in con.execute("select drug_name from raw.fda_event_counts").fetchall()}
    dims = {r[0] for r in con.execute("select canonical_name from marts.dim_drug").fetchall()}
    assert fda and fda <= dims
    (with_data,) = con.execute(
        "select count(*) from marts.dim_drug where has_fda_data"
    ).fetchone() or (0,)
    assert 0 < with_data <= len(fda)


def _macro_list(text: str, macro: str) -> list[str]:
    m = re.search(r"macro " + macro + r"\(\).*?return\((\[.*?\])\)", text, re.S)
    assert m, macro
    value: Any = ast.literal_eval(m.group(1))
    return list(value)


def test_drug_name_word_lists_match_python() -> None:
    text = (DBT_DIR / "macros" / "drug_names.sql").read_text(encoding="utf-8")
    assert _macro_list(text, "drug_null_words") == list(drug_names.NULL_WORDS)
    assert _macro_list(text, "drug_strip_words") == list(drug_names.STRIP_WORDS)
    assert _macro_list(text, "drug_dose_units") == list(drug_names.DOSE_UNITS)
    assert _macro_list(text, "drug_dose_per_units") == list(drug_names.DOSE_PER_UNITS)


TRICKY_NAMES = [
    "10 mg 20 mg aspirin",
    "5mg10mg x",
    "10 mg20mg",
    "Placebo-matched",
    "placebos",
    "Metformin (500 mg) oral tablets",
    "Insulin 10 IU/kg/day",
    "0.5% Lidocaine gel",
    "Drug 1,000 mg",
    "abc 10 - 20mg/m2",
    "abc 10mg/m²",
    "µg test 5 µg",
    "5 mgx drug",
    "drug.5mg",
    "Drug (10mg)(20mg)",
    "10mg/20mg/30mg combo",
    "tab-oral",
    "+aspirin&",
    "drug 5mg/kgx",
    "drug 2 g/lx",
    "ICI 182,780",
    "   spaced    out   ",
    "Oral, IV, injection",
    "50%",
    "%",
    "",
]


def test_canonical_drug_name_macro_on_edge_cases(built: Built) -> None:
    values = ", ".join(
        "('" + v.replace("'", "''") + "', " + str(i) + ")" for i, v in enumerate(TRICKY_NAMES)
    )
    sql = "select i, {{ canonical_drug_name('v') }} as c from (values " + values + ") as t(v, i)"
    proc = _dbt(
        ["show", "--inline", sql, "--output", "json", "--log-format", "json", "--limit", "-1"],
        built.work,
        built.env,
    )
    assert proc.returncode == 0, _tail(proc)
    preview = None
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("info", {}).get("name") == "ShowNode":
            preview = json.loads(event["data"]["preview"])
    assert preview is not None, _tail(proc)
    got = {row["i"]: row["c"] for row in preview}
    expected = {i: canonical_drug_name(v) for i, v in enumerate(TRICKY_NAMES)}
    assert got == expected


LEAKY_COLUMNS = (
    "enrollment_count",
    "enrollment_type",
    "completion_date",
    "completion_date_type",
    "last_update_posted",
    "has_results",
    "why_stopped",
    "overall_status",
)


def test_ml_features_sql_reads_no_leaky_column() -> None:
    sql = (DBT_DIR / "models" / "marts" / "ml_features.sql").read_text(encoding="utf-8")
    code = re.sub(r"--[^\n]*", "", sql)
    for column in LEAKY_COLUMNS:
        assert column not in code, column
    assert "fct_trial_outcomes" not in code


def test_ml_features_columns_have_no_leaky_names(con: duckdb.DuckDBPyConnection) -> None:
    pattern = re.compile(r"enrol|complet|last_update|results|why_stop|status|outcome|label")
    assert [c for c in _columns(con, "marts", "ml_features") if pattern.search(c)] == []


def test_select_marts_rebuilds_marts_only(built: Built) -> None:
    assert built.build.returncode == 0, _tail(built.build)
    proc = _dbt(["build", "--select", "marts"], built.work, built.env)
    assert proc.returncode == 0, _tail(proc)
    assert "staging.stg_studies" not in proc.stdout
    assert "marts.ml_features" in proc.stdout
    assert "marts.ml_scores_latest" in proc.stdout
