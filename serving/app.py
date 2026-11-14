"""FastAPI scoring service: ``uvicorn serving.app:app``.

* ``GET /health``: always 200; ``model_loaded`` says whether a model is registered.
* ``POST /score``: by ``nct_ids`` (looked up in ``marts.ml_features``) or by raw ``features``.
* ``POST /score/batch``: same code path as ``python -m ml score`` (the only writing endpoint).

Operational rules: the model is loaded at startup if one is registered and lazily on the
first request otherwise (the API recovers after the first training run without a restart);
no warehouse connection is kept between requests (read-only for lookups where the backend
allows it) so pipeline pods can write the same DuckDB file. This package imports only
``common`` and ``ml`` (the serving image contains nothing else).
"""

import logging
import re
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import duckdb
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from common.config import Settings, get_settings
from common.warehouse import get_warehouse
from ml.features import FEATURES, ID_COLUMN, select_sql
from ml.predict import LoadedModel, ModelNotFoundError, load_model, score_frame
from ml.score import score as batch_score

log = logging.getLogger(__name__)

MAX_ITEMS = 1000
_NCT_RE = re.compile(r"NCT\d{8}")
_FEATURE_SET = frozenset(FEATURES)


class ScoreRequest(BaseModel):
    """Exactly one of ``nct_ids`` or ``features``."""

    model_config = ConfigDict(extra="forbid")

    nct_ids: list[str] | None = Field(default=None, min_length=1, max_length=MAX_ITEMS)
    features: list[dict[str, Any]] | None = Field(default=None, min_length=1, max_length=MAX_ITEMS)

    @model_validator(mode="after")
    def _exactly_one(self) -> "ScoreRequest":
        if (self.nct_ids is None) == (self.features is None):
            raise ValueError("provide exactly one of 'nct_ids' or 'features'")
        bad = [i for i in self.nct_ids or [] if not _NCT_RE.fullmatch(i)]
        if bad:
            raise ValueError(f"invalid nct_id(s) (expected NCT + 8 digits): {bad[:5]}")
        for i, row in enumerate(self.features or []):
            unknown = sorted(set(row) - _FEATURE_SET - {ID_COLUMN})
            if unknown:
                raise ValueError(
                    f"features[{i}]: unknown column(s) {unknown}; allowed: nct_id + model features"
                )
        return self


class Driver(BaseModel):
    name: str
    shap: float


class StudyScore(BaseModel):
    nct_id: str | None
    risk_score: float
    top_drivers: list[Driver]


class ScoreResponse(BaseModel):
    model_name: str
    model_version: str
    results: list[StudyScore]
    not_found: list[str]


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    model_name: str | None
    model_version: str | None


class BatchResponse(BaseModel):
    model_name: str
    model_version: str
    rows_written: int


class ModelHolder:
    """Holds the loaded model; (re)loads lazily when nothing is loaded."""

    def __init__(self) -> None:
        self._model: LoadedModel | None = None
        self._lock = threading.Lock()

    def get(self, settings: Settings) -> LoadedModel | None:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                try:
                    self._model = load_model(settings)
                    log.info("loaded %s version %s", self._model.name, self._model.version)
                except ModelNotFoundError as exc:
                    log.info("no model yet: %s", exc)
        return self._model


def _lookup_features(settings: Settings, ids: list[str]) -> pd.DataFrame:
    """Feature rows for already-validated NCT ids. No connection outlives this call."""
    in_list = ", ".join(f"'{i}'" for i in ids)  # ids match ^NCT\d{8}$: safe to inline
    sql = select_sql(FEATURES, where=f"where f.nct_id in ({in_list})")
    if settings.warehouse == "duckdb":
        # Read-only: never blocks (or is blocked by) nothing but an active writer.
        con = duckdb.connect(settings.duckdb_path, read_only=True)
        try:
            return con.execute(sql).df()
        finally:
            con.close()
    warehouse = get_warehouse(settings)
    try:
        return warehouse.query_df(sql)
    finally:
        warehouse.close()


def _to_response(
    model: LoadedModel, scores: pd.DataFrame, ids: list[str | None], not_found: list[str]
) -> ScoreResponse:
    results: list[StudyScore] = []
    for pos, row in enumerate(scores.itertuples(index=False)):
        drivers = [
            Driver(
                name=str(getattr(row, f"top_driver_{k}")),
                shap=float(getattr(row, f"top_driver_{k}_shap")),
            )
            for k in (1, 2, 3)
        ]
        results.append(
            StudyScore(nct_id=ids[pos], risk_score=float(str(row.risk_score)), top_drivers=drivers)
        )
    return ScoreResponse(
        model_name=model.name, model_version=model.version, results=results, not_found=not_found
    )


def create_app() -> FastAPI:
    holder = ModelHolder()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        holder.get(get_settings())  # eager load when a model exists; never fails startup
        yield

    app = FastAPI(title="Trial risk scoring", version="1", lifespan=lifespan)

    def require_model(settings: Settings) -> LoadedModel:
        model = holder.get(settings)
        if model is None:
            raise HTTPException(503, "no model registered yet; run `python -m ml train`")
        return model

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        model = holder.get(get_settings())
        return HealthResponse(
            status="ok",
            model_loaded=model is not None,
            model_name=model.name if model else None,
            model_version=model.version if model else None,
        )

    @app.post("/score", response_model=ScoreResponse)
    def score(req: ScoreRequest) -> ScoreResponse:
        settings = get_settings()
        model = require_model(settings)

        if req.nct_ids is not None:
            requested = list(dict.fromkeys(req.nct_ids))
            valid = requested  # validated by ScoreRequest
            found = pd.DataFrame()
            if valid:
                try:
                    found = _lookup_features(settings, valid)
                except (duckdb.IOException, duckdb.CatalogException) as exc:
                    raise HTTPException(503, f"warehouse unavailable: {exc}") from exc
            present = set(found[ID_COLUMN]) if not found.empty else set()
            not_found = [i for i in requested if i not in present]
            if found.empty:
                return _to_response(model, score_frame(model, found), [], not_found)
            found = found.set_index(ID_COLUMN).loc[[i for i in requested if i in present]]
            found = found.reset_index()
            scores = score_frame(model, found)
            return _to_response(model, scores, [str(i) for i in found[ID_COLUMN]], not_found)

        rows = req.features or []
        ids: list[str | None] = [r.get(ID_COLUMN) for r in rows]
        frame = pd.DataFrame(
            [{c: r.get(c) for c in FEATURES} for r in rows], columns=list(FEATURES)
        )
        frame.insert(
            0, ID_COLUMN, [str(i) if i is not None else f"row-{n}" for n, i in enumerate(ids)]
        )
        return _to_response(model, score_frame(model, frame), ids, [])

    @app.post("/score/batch", response_model=BatchResponse)
    def score_batch() -> BatchResponse:
        settings = get_settings()
        model = require_model(settings)
        try:
            n = batch_score(settings, model=model)
        except duckdb.IOException as exc:
            raise HTTPException(503, f"warehouse unavailable: {exc}") from exc
        return BatchResponse(model_name=model.name, model_version=model.version, rows_written=n)

    return app


app = create_app()
