import pytest

from common.config import Settings, get_settings, reset_settings

ENV_KEYS = [
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
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    reset_settings()


def test_defaults() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.mode == "local"
    assert s.storage_backend == "file"
    assert s.landing_dir == "data/landing"
    assert s.landing_container == "landing"
    assert s.duckdb_path == "data/warehouse/trialrisk.duckdb"
    assert s.mlflow_tracking_uri == "sqlite:///data/mlflow/mlflow.db"
    assert s.model_name == "trial-risk-lgbm"
    assert s.openfda_api_key is None
    assert s.azure_storage_connection_string is None
    assert s.warehouse == "duckdb"


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODE", "cloud")
    monkeypatch.setenv("STORAGE_BACKEND", "azure")
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "acct")
    monkeypatch.setenv("MODEL_NAME", "other")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.mode == "cloud"
    assert s.warehouse == "snowflake"
    assert s.storage_backend == "azure"
    assert s.snowflake_account == "acct"
    assert s.model_name == "other"


def test_invalid_mode_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODE", "banana")
    with pytest.raises(ValueError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_get_settings_cached_and_resettable(monkeypatch: pytest.MonkeyPatch) -> None:
    first = get_settings()
    assert get_settings() is first
    monkeypatch.setenv("MODEL_NAME", "changed")
    assert get_settings().model_name != "changed"
    reset_settings()
    assert get_settings().model_name == "changed"
