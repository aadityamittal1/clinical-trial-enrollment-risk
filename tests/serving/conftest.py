"""Fixtures for the serving tests: one sample warehouse and one trained model per session."""

import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from common.config import Settings
from ml.train import train

REPO = Path(__file__).resolve().parents[2]
DBT_DIR = REPO / "dbt"

# Narrow, known-noisy third-party warnings (pytest runs with filterwarnings=error).
SERVING_WARNING_FILTERS: list[str] = [
    "ignore:The ``noload`` loader strategy is deprecated:sqlalchemy.exc.SADeprecationWarning",
    "ignore:.*Any type hint is inferred as AnyType:UserWarning",
]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    here = Path(__file__).parent
    for item in items:
        if here in item.path.parents:
            for spec in SERVING_WARNING_FILTERS:
                item.add_marker(pytest.mark.filterwarnings(spec))


def sqlite_uri(path: Path) -> str:
    return "sqlite:///" + path.as_posix()


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
    work = tmp_path_factory.mktemp("serving_warehouse")
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


@dataclass
class Env:
    duckdb: Path
    mlflow_uri: str


@pytest.fixture(scope="session")
def trained_env(sample_warehouse: Path, tmp_path_factory: pytest.TempPathFactory) -> Env:
    """Warehouse copy plus an MLflow store holding one registered model."""
    work = tmp_path_factory.mktemp("serving_trained")
    db = work / "trialrisk.duckdb"
    shutil.copyfile(sample_warehouse, db)
    uri = sqlite_uri(work / "mlflow" / "mlflow.db")
    train(Settings(duckdb_path=str(db), mlflow_tracking_uri=uri))
    return Env(db, uri)


@pytest.fixture
def api_env(trained_env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    """Private writable warehouse copy; shared MLflow store holding a model (read only)."""
    db = tmp_path / "trialrisk.duckdb"
    shutil.copyfile(trained_env.duckdb, db)
    monkeypatch.setenv("DUCKDB_PATH", str(db))
    monkeypatch.setenv("MLFLOW_TRACKING_URI", trained_env.mlflow_uri)
    yield Env(db, trained_env.mlflow_uri)


@pytest.fixture
def no_model_env(
    trained_env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Env]:
    """Warehouse present, empty MLflow store."""
    db = tmp_path / "trialrisk.duckdb"
    shutil.copyfile(trained_env.duckdb, db)
    uri = sqlite_uri(tmp_path / "mlflow" / "mlflow.db")
    monkeypatch.setenv("DUCKDB_PATH", str(db))
    monkeypatch.setenv("MLFLOW_TRACKING_URI", uri)
    yield Env(db, uri)
