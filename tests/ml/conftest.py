"""Fixtures for the ML tests: one sample warehouse per session, one trained model per session.

The sample warehouse is built exactly like the documented local path:
``python -m ingestion load --source sample`` then ``dbt build --target local``, into a temp
DuckDB file. Tests that write get their own copy.
"""

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from common.config import Settings
from ml.train import train
from tests.ml._helpers import REPO, Trained, copy_warehouse, make_settings, sqlite_uri

DBT_DIR = REPO / "dbt"

# Narrow, known-noisy third-party warnings (pytest runs with filterwarnings=error).
ML_WARNING_FILTERS: list[str] = [
    # MLflow's sqlite tracking/registry store uses a SQLAlchemy loader option deprecated in 2.1.
    "ignore:The ``noload`` loader strategy is deprecated:sqlalchemy.exc.SADeprecationWarning",
    # Emitted while mlflow.pyfunc imports its own chat/response types (not our code).
    "ignore:.*Any type hint is inferred as AnyType:UserWarning",
]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    here = Path(__file__).parent
    for item in items:
        if here in item.path.parents:
            for spec in ML_WARNING_FILTERS:
                item.add_marker(pytest.mark.filterwarnings(spec))


def _env(duckdb_path: Path) -> dict[str, str]:
    project = {name.upper() for name in Settings.model_fields}
    env = {k: v for k, v in os.environ.items() if k.upper() not in project}
    env.update(
        MODE="local",
        STORAGE_BACKEND="file",
        DUCKDB_PATH=str(duckdb_path),
        PYTHONUTF8="1",
        DBT_SEND_ANONYMOUS_USAGE_STATS="false",
    )
    return env


def _run(args: list[str], env: dict[str, str]) -> None:
    proc = subprocess.run(
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
    assert proc.returncode == 0, (proc.stdout + proc.stderr)[-6000:]


@pytest.fixture(scope="session")
def sample_warehouse(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Path to a DuckDB file built from data/sample (treat as read-only; copy to write)."""
    work = tmp_path_factory.mktemp("ml_warehouse")
    db = work / "trialrisk.duckdb"
    env = _env(db)
    _run([sys.executable, "-m", "ingestion", "load", "--source", "sample"], env)
    _run(
        [
            sys.executable,
            "-m",
            "dbt.cli.main",
            "build",
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
    return db


@pytest.fixture(scope="session")
def trained(sample_warehouse: Path, tmp_path_factory: pytest.TempPathFactory) -> Trained:
    """The default model trained once on the sample warehouse (own copy, own MLflow store)."""
    work = tmp_path_factory.mktemp("ml_trained")
    settings = make_settings(copy_warehouse(sample_warehouse, work / "wh"), work / "mlflow")
    return Trained(settings=settings, result=train(settings), mlflow_dir=work / "mlflow")


@pytest.fixture
def ml_env(
    sample_warehouse: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Point DUCKDB_PATH / MLFLOW_TRACKING_URI at a private warehouse copy and MLflow store."""
    db = copy_warehouse(sample_warehouse, tmp_path / "wh")
    monkeypatch.setenv("DUCKDB_PATH", str(db))
    monkeypatch.setenv("MLFLOW_TRACKING_URI", sqlite_uri(tmp_path / "mlflow" / "mlflow.db"))
    yield tmp_path
