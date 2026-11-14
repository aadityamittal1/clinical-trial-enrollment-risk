"""Registry error mapping and the feature-list check in load_model."""

from pathlib import Path
from typing import Any

import pytest
from mlflow.exceptions import MlflowException
from mlflow.protos.databricks_pb2 import INTERNAL_ERROR, RESOURCE_DOES_NOT_EXIST
from mlflow.tracking import MlflowClient

from ml import features as feature_spec
from ml.predict import (
    FeatureMismatchError,
    ModelNotFoundError,
    check_feature_lists,
    latest_version,
    load_model,
)
from tests.ml._helpers import Trained, make_settings


def _raise(code: int) -> Any:
    def search(self: MlflowClient, *args: Any, **kwargs: Any) -> Any:
        raise MlflowException("registry says no", error_code=code)

    return search


def test_not_found_registry_error_maps_to_model_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(tmp_path / "wh.duckdb", tmp_path / "mlflow")
    monkeypatch.setattr(MlflowClient, "search_model_versions", _raise(RESOURCE_DOES_NOT_EXIST))
    with pytest.raises(ModelNotFoundError, match="trial-risk-lgbm"):
        latest_version(settings)


def test_other_registry_errors_are_not_reported_as_missing_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(tmp_path / "wh.duckdb", tmp_path / "mlflow")
    monkeypatch.setattr(MlflowClient, "search_model_versions", _raise(INTERNAL_ERROR))
    with pytest.raises(MlflowException, match="registry says no") as info:
        latest_version(settings)
    assert not isinstance(info.value, ModelNotFoundError)


def _params() -> dict[str, str]:
    return {
        "categorical_features": ",".join(feature_spec.CATEGORICAL_FEATURES),
        "numeric_features": ",".join(feature_spec.NUMERIC_FEATURES),
    }


def test_check_feature_lists_accepts_current_features() -> None:
    check_feature_lists(_params(), "m v1")


@pytest.mark.parametrize("key", ["categorical_features", "numeric_features"])
def test_check_feature_lists_rejects_changed_or_missing_lists(key: str) -> None:
    changed = {**_params(), key: _params()[key] + ",something_new"}
    with pytest.raises(FeatureMismatchError, match=key):
        check_feature_lists(changed, "m v1")
    missing = {k: v for k, v in _params().items() if k != key}
    with pytest.raises(FeatureMismatchError, match=key):
        check_feature_lists(missing, "m v1")


def test_load_model_refuses_a_version_trained_on_other_features(
    trained: Trained, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert load_model(trained.settings).version == "1"  # current features: loads
    monkeypatch.setattr(
        feature_spec, "NUMERIC_FEATURES", (*feature_spec.NUMERIC_FEATURES, "n_new_feature")
    )
    with pytest.raises(FeatureMismatchError, match="numeric_features") as info:
        load_model(trained.settings)
    assert isinstance(info.value, ModelNotFoundError)  # serving's 503 path still applies
