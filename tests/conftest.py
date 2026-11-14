"""Shared pytest configuration: isolate every test from env vars, .env and cached settings."""

from collections.abc import Iterator

import pytest

from common.config import Settings, reset_settings

ENV_VARS = [
    "MODE",
    "STORAGE_BACKEND",
    "LANDING_DIR",
    "AZURE_STORAGE_CONNECTION_STRING",
    "LANDING_CONTAINER",
    "DUCKDB_PATH",
    "SNOWFLAKE_ACCOUNT",
    "SNOWFLAKE_USER",
    "SNOWFLAKE_PASSWORD",
    "SNOWFLAKE_ROLE",
    "SNOWFLAKE_WAREHOUSE",
    "SNOWFLAKE_DATABASE",
    "MLFLOW_TRACKING_URI",
    "MODEL_NAME",
    "OPENFDA_API_KEY",
]


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    reset_settings()
    yield
    reset_settings()
