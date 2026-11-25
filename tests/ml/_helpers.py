"""Helpers shared by the ML tests (imported by conftest and test modules)."""

import shutil
from dataclasses import dataclass
from pathlib import Path

from common.config import Settings
from ml.train import TrainResult

REPO = Path(__file__).resolve().parents[2]


def copy_warehouse(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    shutil.copyfile(src, dest)
    return dest


def sqlite_uri(path: Path) -> str:
    return "sqlite:///" + path.as_posix()


def make_settings(duckdb_path: Path, mlflow_dir: Path) -> Settings:
    return Settings(
        mode="local",
        storage_backend="file",
        duckdb_path=str(duckdb_path),
        mlflow_tracking_uri=sqlite_uri(mlflow_dir / "mlflow.db"),
        model_name="trial-risk-lgbm",
    )


@dataclass
class Trained:
    settings: Settings
    result: TrainResult
    mlflow_dir: Path
