"""Central settings. Every environment variable is read through get_settings()."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mode: Literal["local", "cloud"] = "local"
    storage_backend: Literal["file", "azure"] = "file"
    landing_dir: str = "data/landing"
    azure_storage_connection_string: str | None = None
    landing_container: str = "landing"
    duckdb_path: str = "data/warehouse/trialrisk.duckdb"
    snowflake_account: str | None = None
    snowflake_user: str | None = None
    snowflake_password: str | None = None
    snowflake_role: str | None = None
    snowflake_warehouse: str | None = None
    snowflake_database: str | None = None
    mlflow_tracking_uri: str = "sqlite:///data/mlflow/mlflow.db"
    model_name: str = "trial-risk-lgbm"
    openfda_api_key: str | None = None

    @property
    def warehouse(self) -> Literal["duckdb", "snowflake"]:
        return "duckdb" if self.mode == "local" else "snowflake"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings() -> None:
    """Drop the cached settings (used by tests)."""
    get_settings.cache_clear()
