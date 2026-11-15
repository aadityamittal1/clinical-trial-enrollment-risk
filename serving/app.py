"""FastAPI scoring service: ``uvicorn serving.app:app``.

* ``GET /health``: always 200; ``status`` is ok | no_model | degraded.
* ``POST /score``: by ``nct_ids`` (looked up in ``marts.ml_features``) or by raw ``features``.
* ``POST /score/batch``: same code path as ``python -m ml score`` (the only writing endpoint).

Operational rules: the model is loaded at startup if one is registered and lazily on the
first request otherwise (the API recovers after the first training run without a restart);
no warehouse connection is kept between requests (read-only for lookups where the backend
allows it) so pipeline pods can write the same DuckDB file. This package imports only
``common`` and ``ml`` (the serving image contains nothing else).
"""

import logging
import math
import threading
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from common.config import Settings, get_settings
from common.warehouse import Warehouse, WriteMode, get_warehouse
from ml.features import (
    BOOLEAN_FEATURES,
    CATEGORICAL_FEATURES,
    COUNT_FEATURES,
    FEATURES,
    ID_COLUMN,
    NCT_ID_RE,
    load_features,
)
from ml.predict import (
    FeatureMismatchError,
    LoadedModel,
    ModelNotFoundError,
    latest_version,
    load_model,
    score_frame,
)
from ml.score import score as batch_score

log = logging.getLogger(__name__)

MAX_ITEMS = 1000
MAX_ID_LENGTH = 64
# How often a loaded model is compared with the latest registered version.
MODEL_RECHECK_SECONDS = 60.0
_FEATURE_SET = frozenset(FEATURES)

Clock = Callable[[], float]


def _coerce_feature(name: str, value: Any) -> Any:
    """Validate/coerce one raw feature value; ValueError when the type does not fit."""
    if value is None:
        return None
    if name in CATEGORICAL_FEATURES:
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string, got {type(value).__name__}")
        return value
    if name in BOOLEAN_FEATURES:
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        raise ValueError(f"{name} must be a boolean (true/false or 0/1)")
    assert name in COUNT_FEATURES
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return value


class ScoreRequest(BaseModel):
    """Exactly one of ``nct_ids`` or ``features``."""

    model_config = ConfigDict(extra="forbid")

    nct_ids: list[str] | None = Field(default=None, min_length=1, max_length=MAX_ITEMS)
    features: list[dict[str, Any]] | None = Field(default=None, min_length=1, max_length=MAX_ITEMS)

    @model_validator(mode="after")
    def _validate(self) -> "ScoreRequest":
        if (self.nct_ids is None) == (self.features is None):
            raise ValueError("provide exactly one of 'nct_ids' or 'features'")
        bad = [i for i in self.nct_ids or [] if not NCT_ID_RE.fullmatch(i)]
        if bad:
            raise ValueError(f"invalid nct_id(s) (expected NCT + 8 digits): {bad[:5]}")
        for i, row in enumerate(self.features or []):
            unknown = sorted(set(row) - _FEATURE_SET - {ID_COLUMN})
            if unknown:
                raise ValueError(
                    f"features[{i}]: unknown column(s) {unknown}; allowed: nct_id + model features"
                )
            nid = row.get(ID_COLUMN)
            if nid is not None and (not isinstance(nid, str) or len(nid) > MAX_ID_LENGTH):
                raise ValueError(
                    f"features[{i}]: nct_id must be a string of <= {MAX_ID_LENGTH} chars"
                )
            for name in _FEATURE_SET & set(row):
                try:
                    row[name] = _coerce_feature(name, row[name])
                except ValueError as exc:
                    raise ValueError(f"features[{i}]: {exc}") from exc
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
    status: str  # ok | no_model | degraded
    model_loaded: bool
    model_name: str | None
    model_version: str | None
    detail: str | None = None


class BatchResponse(BaseModel):
    model_name: str
    model_version: str
    rows_written: int


class ModelHolder:
    """Holds the loaded model and its state; never raises on registry problems.

    * nothing loaded: every call re-checks the registry (cheap), so the API recovers after the
      first training run;
    * model loaded: the latest registered version is compared at most every
      ``recheck_seconds`` and the model is reloaded under the lock when it differs.
    """

    def __init__(
        self, clock: Clock = time.monotonic, recheck_seconds: float = MODEL_RECHECK_SECONDS
    ) -> None:
        self._clock = clock
        self._recheck = recheck_seconds
        self._lock = threading.Lock()
        self._model: LoadedModel | None = None
        self._status = "no_model"
        self._detail: str | None = "no model registered yet; run `python -m ml train`"
        self._checked_at = -math.inf

    def _set_failure(self, exc: Exception) -> None:
        self._model = None
        if isinstance(exc, FeatureMismatchError):
            self._status = "degraded"
            self._detail = "registered model was trained on a different feature list; retrain"
        elif isinstance(exc, ModelNotFoundError):
            self._status = "no_model"
            self._detail = "no model registered yet; run `python -m ml train`"
        else:
            self._status = "degraded"
            self._detail = f"model registry unavailable ({type(exc).__name__})"

    def refresh(self, settings: Settings) -> None:
        now = self._clock()
        if self._model is not None and now - self._checked_at < self._recheck:
            return
        with self._lock:
            now = self._clock()
            if self._model is not None and now - self._checked_at < self._recheck:
                return
            self._checked_at = now
            try:
                if self._model is None:
                    self._model = load_model(settings)
                    log.info("loaded %s version %s", self._model.name, self._model.version)
                elif str(latest_version(settings).version) != self._model.version:
                    self._model = load_model(settings)
                    log.info("reloaded %s version %s", self._model.name, self._model.version)
                self._status, self._detail = "ok", None
            except Exception as exc:
                log.warning("model refresh failed: %s: %s", type(exc).__name__, exc)
                if self._model is None:
                    self._set_failure(exc)
                # else: keep serving the model we have; the next recheck tries again

    @property
    def model(self) -> LoadedModel | None:
        return self._model

    @property
    def status(self) -> tuple[str, str | None]:
        return self._status, self._detail


class _ReadOnlyDuckDB:
    """Warehouse view for lookups: read-only DuckDB connection, closed after each use."""

    def __init__(self, path: str) -> None:
        if not Path(path).exists():
            raise duckdb.IOException(f"warehouse file not found: {path}")
        self._con = duckdb.connect(path, read_only=True)

    def query_df(self, sql: str) -> pd.DataFrame:
        return self._con.execute(sql).df()

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> None:
        raise NotImplementedError("read-only warehouse")

    def write_df(
        self, df: pd.DataFrame, schema: str, table: str, mode: WriteMode = "append"
    ) -> None:
        raise NotImplementedError("read-only warehouse")

    def close(self) -> None:
        self._con.close()


def _lookup_features(settings: Settings, ids: list[str]) -> pd.DataFrame:
    """Feature rows for validated NCT ids. No connection outlives this call.

    Uses ``ml.features.load_features`` (validated lookup) through a read-only DuckDB handle
    (``get_warehouse`` only opens read-write, which would block pipeline writers).
    """
    wh: Warehouse = (
        _ReadOnlyDuckDB(settings.duckdb_path)
        if settings.warehouse == "duckdb"
        else get_warehouse(settings)
    )
    try:
        return load_features(wh, ids)
    finally:
        wh.close()


def _to_response(
    model: LoadedModel, scores: pd.DataFrame, ids: list[str | None], not_found: list[str]
) -> ScoreResponse:
    results: list[StudyScore] = []
    for pos, row in enumerate(scores.to_dict("records")):
        drivers = [
            Driver(name=str(row[f"top_driver_{k}"]), shap=float(row[f"top_driver_{k}_shap"]))
            for k in (1, 2, 3)
        ]
        results.append(
            StudyScore(nct_id=ids[pos], risk_score=float(row["risk_score"]), top_drivers=drivers)
        )
    return ScoreResponse(
        model_name=model.name, model_version=model.version, results=results, not_found=not_found
    )


def create_app(clock: Clock = time.monotonic) -> FastAPI:
    holder = ModelHolder(clock=clock)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        holder.refresh(get_settings())  # eager load; registry errors never fail startup
        yield

    app = FastAPI(title="Trial risk scoring", version="1", lifespan=lifespan)

    def require_model(settings: Settings) -> LoadedModel:
        holder.refresh(settings)
        model = holder.model
        if model is None:
            status, detail = holder.status
            raise HTTPException(503, f"{status}: {detail}")
        return model

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        holder.refresh(get_settings())
        model = holder.model
        status, detail = holder.status
        return HealthResponse(
            status=status,
            model_loaded=model is not None,
            model_name=model.name if model else None,
            model_version=model.version if model else None,
            detail=detail,
        )

    @app.post("/score", response_model=ScoreResponse)
    def score(req: ScoreRequest) -> ScoreResponse:
        settings = get_settings()
        model = require_model(settings)

        if req.nct_ids is not None:
            requested = list(dict.fromkeys(req.nct_ids))  # already validated by ScoreRequest
            try:
                found = _lookup_features(settings, requested)
            except duckdb.Error as exc:
                log.warning("warehouse lookup failed: %s", exc)
                raise HTTPException(503, f"warehouse unavailable ({type(exc).__name__})") from exc
            present = set(found[ID_COLUMN])
            not_found = [i for i in requested if i not in present]
            ordered = found.set_index(ID_COLUMN).loc[[i for i in requested if i in present]]
            ordered = ordered.reset_index()
            scores = score_frame(model, ordered)
            return _to_response(model, scores, [str(i) for i in ordered[ID_COLUMN]], not_found)

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
        """Score every open study with the registered model and append the run to ml_scores.

        WARNING: no authentication in v1 (the service is meant for a private cluster / demo).
        Every call is a full scoring run that appends one row per open study to
        ``marts.ml_scores``, so do not expose this endpoint publicly.
        """
        settings = get_settings()
        model = require_model(settings)
        try:
            n = batch_score(settings, model=model)
        except duckdb.Error as exc:  # e.g. a concurrent writer holds the DuckDB file
            log.warning("batch scoring failed: %s", exc)
            raise HTTPException(503, f"warehouse unavailable ({type(exc).__name__})") from exc
        return BatchResponse(model_name=model.name, model_version=model.version, rows_written=n)

    return app


app = create_app()
