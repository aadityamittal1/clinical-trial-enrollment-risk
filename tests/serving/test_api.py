"""API tests (FastAPI TestClient) against a sample warehouse and a really trained model."""

import warnings
from pathlib import Path

import duckdb
import pytest

from common.config import Settings
from ml.features import FEATURES
from ml.predict import FeatureMismatchError
from ml.train import train
from serving.app import MODEL_RECHECK_SECONDS, create_app
from tests.serving.conftest import Env

with warnings.catch_warnings():
    # Starlette nags about httpx -> httpx2 at import time; third-party, not our code.
    warnings.filterwarnings("ignore", message="Using `httpx` with `starlette.testclient`")
    from fastapi.testclient import TestClient


def _open_ids(db: Path, n: int = 3) -> list[str]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            "select nct_id from marts.dim_study where is_open order by nct_id limit ?", [n]
        ).fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


def test_health_with_model(api_env: Env) -> None:
    with TestClient(create_app()) as client:
        r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is True
    assert body["model_name"] == "trial-risk-lgbm"
    assert body["model_version"] == "1"


def test_score_by_nct_ids_with_not_found(api_env: Env) -> None:
    ids = _open_ids(api_env.duckdb)
    with TestClient(create_app()) as client:
        r = client.post("/score", json={"nct_ids": [*ids, "NCT99999999"]})
    assert r.status_code == 200
    body = r.json()
    assert [x["nct_id"] for x in body["results"]] == ids
    assert body["not_found"] == ["NCT99999999"]
    first = body["results"][0]
    assert 0.0 <= first["risk_score"] <= 1.0
    assert len(first["top_drivers"]) == 3
    assert {"name", "shap"} <= set(first["top_drivers"][0])
    assert first["top_drivers"][0]["name"] in FEATURES
    assert body["model_version"] == "1"


def test_score_by_ids_matches_batch_scorer(api_env: Env) -> None:
    """Same code path: an API score equals the row `python -m ml score` writes."""
    ids = _open_ids(api_env.duckdb, 2)
    with TestClient(create_app()) as client:
        api = client.post("/score", json={"nct_ids": ids}).json()["results"]
        n = client.post("/score/batch").json()["rows_written"]
    assert n > 0
    con = duckdb.connect(str(api_env.duckdb), read_only=True)
    try:
        rows = con.execute(
            "select nct_id, risk_score from marts.ml_scores where nct_id in (?, ?) order by nct_id",
            ids,
        ).fetchall()
    finally:
        con.close()
    assert [(r["nct_id"], pytest.approx(r["risk_score"])) for r in api] == rows


def test_score_raw_features(api_env: Env) -> None:
    row = {
        "nct_id": "NCT00000001",
        "phase": "PHASE2",
        "sponsor_class": "INDUSTRY",
        "n_sites": 12,
        "n_arms": 2,
        "has_drug": True,
        "condition_category": "oncology",
    }  # remaining features omitted -> null
    with TestClient(create_app()) as client:
        r = client.post("/score", json={"features": [row, {"phase": "NOT_A_PHASE"}]})
    assert r.status_code == 200
    body = r.json()
    assert body["not_found"] == []
    assert [x["nct_id"] for x in body["results"]] == ["NCT00000001", None]
    assert all(0.0 <= x["risk_score"] <= 1.0 for x in body["results"])


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"nct_ids": [], "features": []},
        {"nct_ids": ["NCT00000001"], "features": [{"phase": "PHASE1"}]},
        {"features": [{"enrollment_count": 5}]},  # unknown / leaky column
        {"nct_ids": "NCT00000001"},
        {"nct_ids": ["NCT00000001", "bogus'; drop table x"]},
        {"nct_ids": ["nct00000001"]},
    ],
)
def test_score_rejects_bad_requests(api_env: Env, payload: dict[str, object]) -> None:
    with TestClient(create_app()) as client:
        assert client.post("/score", json=payload).status_code == 422


def test_no_model_health_ok_and_score_503(no_model_env: Env) -> None:
    with TestClient(create_app()) as client:
        h = client.get("/health")
        assert h.status_code == 200
        assert h.json()["model_loaded"] is False
        assert h.json()["model_version"] is None
        assert client.post("/score", json={"nct_ids": ["NCT00000001"]}).status_code == 503
        assert client.post("/score/batch").status_code == 503


def test_model_loaded_lazily_after_training(no_model_env: Env) -> None:
    with TestClient(create_app()) as client:
        assert client.get("/health").json()["model_loaded"] is False
        train(
            Settings(
                duckdb_path=str(no_model_env.duckdb),
                mlflow_tracking_uri=no_model_env.mlflow_uri,
            )
        )
        assert client.get("/health").json()["model_loaded"] is True
        ids = _open_ids(no_model_env.duckdb, 1)
        assert client.post("/score", json={"nct_ids": ids}).status_code == 200


def test_no_connection_held_between_requests(api_env: Env) -> None:
    """A writer (the pipeline pods) can open the file read-write after a /score call."""
    ids = _open_ids(api_env.duckdb, 1)
    with TestClient(create_app()) as client:
        assert client.post("/score", json={"nct_ids": ids}).status_code == 200
        con = duckdb.connect(str(api_env.duckdb))  # read-write: fails if the API holds a handle
        con.close()


def test_score_503_when_warehouse_missing(api_env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUCKDB_PATH", str(api_env.duckdb) + ".missing")
    from common.config import reset_settings

    reset_settings()
    with TestClient(create_app()) as client:
        r = client.post("/score", json={"nct_ids": ["NCT00000001"]})
    assert r.status_code == 503


def test_batch_writes_rows(api_env: Env) -> None:
    with TestClient(create_app()) as client:
        r = client.post("/score/batch")
    assert r.status_code == 200
    body = r.json()
    assert body["rows_written"] > 0
    assert body["model_version"] == "1"
    con = duckdb.connect(str(api_env.duckdb), read_only=True)
    try:
        (n,) = con.execute("select count(*) from marts.ml_scores").fetchone() or (0,)
    finally:
        con.close()
    assert n == body["rows_written"]


@pytest.mark.parametrize(
    "row",
    [
        {"n_sites": "abc"},
        {"n_sites": True},
        {"phase": 3},
        {"has_drug": "yes"},
        {"has_drug": 2},
        {"nct_id": 123},
        {"n_arms": [1]},
        {"n_arms": {"a": 1}},
    ],
)
def test_score_rejects_mistyped_features(api_env: Env, row: dict[str, object]) -> None:
    with TestClient(create_app()) as client:
        assert client.post("/score", json={"features": [row]}).status_code == 422


def test_health_status_no_model(no_model_env: Env) -> None:
    with TestClient(create_app()) as client:
        body = client.get("/health").json()
        r = client.post("/score", json={"nct_ids": ["NCT00000001"]})
    assert body["status"] == "no_model"
    assert body["model_loaded"] is False
    assert r.status_code == 503
    assert r.json()["detail"].startswith("no_model")


def test_health_status_degraded_when_registry_unreachable(
    no_model_env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocker = tmp_path / "a_file"
    blocker.write_text("x")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "sqlite:///" + (blocker / "sub" / "m.db").as_posix())
    with TestClient(create_app()) as client:  # startup must not fail
        h = client.get("/health")
        assert h.status_code == 200
        body = h.json()
        assert body["status"] == "degraded"
        assert body["model_loaded"] is False
        assert "registry unavailable" in body["detail"]
        for r in (
            client.post("/score", json={"nct_ids": ["NCT00000001"]}),
            client.post("/score/batch"),
        ):
            assert r.status_code == 503
            assert r.json()["detail"].startswith("degraded")


def test_health_status_degraded_on_feature_mismatch(
    api_env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(settings: object) -> object:
        raise FeatureMismatchError("secret details that must not leak")

    monkeypatch.setattr("serving.app.load_model", boom)
    with TestClient(create_app()) as client:
        body = client.get("/health").json()
        r = client.post("/score", json={"nct_ids": ["NCT00000001"]})
    assert body["status"] == "degraded"
    assert "feature list" in body["detail"]
    assert "secret" not in body["detail"]
    assert r.status_code == 503
    assert "feature list" in r.json()["detail"]


def test_new_model_version_picked_up_after_recheck_interval(no_model_env: Env) -> None:
    now = [1000.0]
    settings = Settings(
        duckdb_path=str(no_model_env.duckdb), mlflow_tracking_uri=no_model_env.mlflow_uri
    )
    train(settings)
    ids = _open_ids(no_model_env.duckdb, 1)
    with TestClient(create_app(clock=lambda: now[0])) as client:
        assert client.get("/health").json()["model_version"] == "1"
        train(settings)  # registers v2
        now[0] += MODEL_RECHECK_SECONDS - 1
        assert client.get("/health").json()["model_version"] == "1"  # not re-checked yet
        now[0] += 2
        assert client.get("/health").json()["model_version"] == "2"
        r = client.post("/score", json={"nct_ids": ids})
        assert r.json()["model_version"] == "2"
        assert client.post("/score/batch").json()["model_version"] == "2"


def test_batch_503_when_warehouse_busy(api_env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    def busy(*args: object, **kwargs: object) -> int:
        raise duckdb.TransactionException("conflict with a concurrent writer")

    monkeypatch.setattr("serving.app.batch_score", busy)
    with TestClient(create_app()) as client:
        r = client.post("/score/batch")
    assert r.status_code == 503
    assert "warehouse unavailable" in r.json()["detail"]


def test_batch_endpoint_documents_no_auth() -> None:
    doc = create_app().openapi()["paths"]["/score/batch"]["post"]["description"]
    assert "no authentication" in doc.lower()
    assert "ml_scores" in doc
