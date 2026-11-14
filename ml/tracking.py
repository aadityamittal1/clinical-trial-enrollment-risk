"""MLflow wiring: tracking/registry URI from settings, artifacts next to the sqlite store.

Local default: ``sqlite:///data/mlflow/mlflow.db`` with artifacts in ``data/mlflow/artifacts``
(both git-ignored). The parent directory is created on first use, and the experiment is
created with an explicit artifact location so nothing lands in a stray ``./mlruns``.
For a remote tracking server (http/https) the server decides where artifacts go.
"""

from pathlib import Path

import mlflow
from mlflow.tracking import MlflowClient

from common.config import Settings

EXPERIMENT_NAME = "trial-risk"
_SQLITE_PREFIX = "sqlite:///"


def sqlite_path(uri: str) -> Path | None:
    """The database file of a ``sqlite:///`` URI (relative paths resolve against the cwd)."""
    if not uri.startswith(_SQLITE_PREFIX):
        return None
    return Path(uri[len(_SQLITE_PREFIX) :])


def artifact_location(uri: str) -> str | None:
    db = sqlite_path(uri)
    if db is None:
        return None
    return (db.resolve().parent / "artifacts").as_uri()


def configure_mlflow(settings: Settings) -> str:
    """Point MLflow tracking and registry at settings.mlflow_tracking_uri; return the URI."""
    uri = settings.mlflow_tracking_uri
    db = sqlite_path(uri)
    if db is not None:
        db.parent.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(uri)
    mlflow.set_registry_uri(uri)
    return uri


def ensure_experiment(settings: Settings, name: str = EXPERIMENT_NAME) -> str:
    """Experiment id, creating the experiment (with local artifact location) if needed."""
    uri = configure_mlflow(settings)
    client = MlflowClient(tracking_uri=uri, registry_uri=uri)
    experiment = client.get_experiment_by_name(name)
    if experiment is not None:
        return str(experiment.experiment_id)
    return str(client.create_experiment(name, artifact_location=artifact_location(uri)))
