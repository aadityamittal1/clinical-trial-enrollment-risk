"""train -> register -> load -> score on the sample warehouse, in temp dirs, via the CLI."""

import json
from pathlib import Path

import duckdb
import pytest

from common.config import get_settings
from ml.__main__ import main
from ml.predict import ModelNotFoundError, load_model
from ml.score import ML_SCORES_COLUMNS, score
from tests.ml._helpers import REPO, Trained, copy_warehouse

N_OPEN = 800


def _ml_scores(db: str) -> list[tuple[str, str]]:
    con = duckdb.connect(db, read_only=True)
    try:
        return [
            (str(name), str(kind).lower())
            for name, kind in con.execute(
                "select column_name, data_type from information_schema.columns "
                "where table_schema = 'marts' and table_name = 'ml_scores' "
                "order by ordinal_position"
            ).fetchall()
        ]
    finally:
        con.close()


def test_load_model_without_registered_version_raises(ml_env: Path) -> None:
    with pytest.raises(ModelNotFoundError, match="trial-risk-lgbm"):
        load_model(get_settings())
    assert (ml_env / "mlflow").is_dir()  # parent directory of the sqlite store created


def test_cli_without_model_exits_nonzero(ml_env: Path) -> None:
    assert main(["score"]) == 1


def test_train_register_load_score_round_trip(ml_env: Path) -> None:
    mlruns_before = (REPO / "mlruns").exists()
    settings = get_settings()

    assert main(["train"]) == 0
    loaded = load_model(get_settings())
    assert loaded.name == settings.model_name
    assert loaded.version == "1"
    assert loaded.run_id

    assert main(["score"]) == 0
    con = duckdb.connect(settings.duckdb_path, read_only=True)
    try:
        df = con.execute("select * from marts.ml_scores").df()
        (n_latest,) = con.execute("select count(*) from marts.ml_scores_latest").fetchone() or (0,)
    finally:
        con.close()
    assert len(df) == N_OPEN
    assert n_latest == N_OPEN
    assert df["nct_id"].is_unique
    assert df["risk_score"].between(0, 1).all()
    assert set(df["model_version"]) == {"1"}
    assert df["scored_at"].nunique() == 1
    assert all(len(json.loads(s)) > 3 for s in df["shap_values"])
    assert _ml_scores(settings.duckdb_path) == [
        (n, "timestamp" if k == "timestamp" else k) for n, k in _ml_scores_ddl()
    ]

    # Artifacts live next to the sqlite store, never in a stray ./mlruns.
    artifacts = ml_env / "mlflow" / "artifacts"
    assert artifacts.is_dir()
    assert any(p.name == "MLmodel" for p in artifacts.rglob("MLmodel"))
    assert (REPO / "mlruns").exists() == mlruns_before

    # A second training run registers version 2; load_model picks the latest.
    assert main(["train"]) == 0
    assert load_model(get_settings()).version == "2"


def _ml_scores_ddl() -> list[tuple[str, str]]:
    duck = {"varchar": "varchar", "double": "double", "timestamp": "timestamp"}
    return [(name, duck[kind]) for name, kind in ML_SCORES_COLUMNS]


def test_score_creates_ml_scores_when_missing(trained: Trained, tmp_path: Path) -> None:
    db = copy_warehouse(Path(trained.settings.duckdb_path), tmp_path)
    con = duckdb.connect(str(db))
    con.execute("drop view if exists marts.ml_scores_latest")
    con.execute("drop table marts.ml_scores")
    con.close()
    settings = trained.settings.model_copy(update={"duckdb_path": str(db)})

    assert score(settings) == N_OPEN
    assert [n for n, _ in _ml_scores(str(db))] == [n for n, _ in ML_SCORES_COLUMNS]
    assert dict(_ml_scores(str(db))) == dict(_ml_scores_ddl())


def test_dbt_hook_and_scorer_agree_on_ml_scores_columns() -> None:
    macro = (REPO / "dbt" / "macros" / "ml_scores.sql").read_text(encoding="utf-8")
    for name, kind in ML_SCORES_COLUMNS:
        assert f"('{name}', '{kind}')" in macro
