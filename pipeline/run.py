"""The no-Kubernetes path: ingest -> load -> dbt build -> train -> score -> dbt refresh.

Every step runs in this process except dbt, which is a subprocess (``python -m dbt.cli.main``)
reading the same settings through the environment. The first failing step stops the run and
is named in the error; the CLI turns that into a non-zero exit code.
"""

import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pandas as pd

from common.config import Settings, get_settings
from common.warehouse import get_warehouse
from ml.predict import ModelNotFoundError, latest_version, load_model

log = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[1]
DBT_DIR = REPO / "dbt"
LAYERS = ("raw", "staging", "marts")

Source = Literal["sample", "live"]


class StepError(RuntimeError):
    """A pipeline step failed; ``step`` names it."""

    def __init__(self, step: str, message: str) -> None:
        super().__init__(f"step '{step}' failed: {message}")
        self.step = step


@dataclass
class Summary:
    source: str
    table_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    label_counts: dict[str, int] = field(default_factory=dict)
    model_name: str = ""
    model_version: str = ""
    trained: bool = False
    n_scored: int = 0
    seconds: float = 0.0

    def render(self) -> str:
        lines = [f"== pipeline summary (source: {self.source}) =="]
        for layer in LAYERS:
            tables = self.table_counts.get(layer, {})
            lines.append(f"{layer} ({len(tables)} tables):")
            lines.extend(f"  {layer}.{name:<28} {n:>9,}" for name, n in tables.items())
        lines.append("labels (marts.fct_trial_outcomes.label): " + _fmt(self.label_counts))
        action = "trained" if self.trained else "reused"
        lines.append(f"model: {self.model_name} version {self.model_version} ({action})")
        lines.append(f"studies scored this run: {self.n_scored}")
        lines.append(f"elapsed: {self.seconds:.1f}s")
        return "\n".join(lines)


def _fmt(counts: dict[str, int]) -> str:
    return ", ".join(f"{k}: {v}" for k, v in counts.items()) or "none"


def _run_ingestion(step: str, argv: list[str]) -> None:
    from ingestion.__main__ import main as ingestion_main

    try:
        code = ingestion_main(argv)
    except SystemExit as exc:  # argparse
        code = int(exc.code) if isinstance(exc.code, int) else 2
    except Exception as exc:
        raise StepError(step, f"{type(exc).__name__}: {exc}") from exc
    if code != 0:
        raise StepError(step, f"`python -m ingestion {' '.join(argv)}` exited with {code}")


def _dbt(step: str, settings: Settings, select: str | None = None) -> None:
    target = "local" if settings.mode == "local" else "cloud"
    cmd = [
        sys.executable,
        "-m",
        "dbt.cli.main",
        "build",
        "--project-dir",
        str(DBT_DIR),
        "--profiles-dir",
        str(DBT_DIR),
        "--target",
        target,
    ]
    if select:
        cmd += ["--select", select]
    env = {**os.environ, "DBT_SEND_ANONYMOUS_USAGE_STATS": "false", "PYTHONUTF8": "1"}
    # dbt reads DUCKDB_PATH itself; pass the resolved value so .env / defaults apply too.
    env["DUCKDB_PATH"] = str(Path(settings.duckdb_path).resolve())
    print(f"[{step}] {' '.join(cmd[2:])}", flush=True)
    proc = subprocess.run(
        cmd, cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr).strip()[-3000:]
        raise StepError(step, f"dbt exited with {proc.returncode}\n{tail}")
    last = [ln for ln in proc.stdout.strip().splitlines() if "Done." in ln]
    print(f"[{step}] {last[-1].split(' ', 1)[-1].strip() if last else 'ok'}", flush=True)


def has_model(settings: Settings) -> bool:
    try:
        latest_version(settings)
    except ModelNotFoundError:
        return False
    return True


def _registered(settings: Settings) -> bool:
    try:
        return has_model(settings)
    except Exception as exc:
        raise StepError("train", f"cannot reach the model registry: {exc}") from exc


def _collect_summary(settings: Settings, summary: Summary) -> None:
    wh = get_warehouse(settings)
    try:
        for layer in LAYERS:
            names = wh.query_df(
                "select table_name as t from information_schema.tables "
                f"where lower(table_schema) = '{layer}' order by 1"
            )["t"]
            counts: dict[str, int] = {}
            for name in names:
                n = wh.query_df(f"select count(*) as n from {layer}.{name}")["n"].iloc[0]
                counts[str(name).lower()] = int(n)
            summary.table_counts[layer] = counts
        labels = wh.query_df(
            "select cast(label as varchar) as label, count(*) as n "
            "from marts.fct_trial_outcomes group by 1 order by 1"
        )
        summary.label_counts = {
            ("unlabelled" if lab is None or pd.isna(lab) else str(lab)): int(n)
            for lab, n in zip(labels["label"].tolist(), labels["n"].tolist(), strict=True)
        }
    finally:
        wh.close()


def run_pipeline(
    source: Source = "sample",
    *,
    retrain: bool = False,
    skip_train: bool = False,
    max_pages: int | None = None,
    max_drugs: int | None = None,
    settings: Settings | None = None,
    echo: Callable[[str], None] = print,
) -> Summary:
    """Run every step; raise ``StepError`` naming the first one that fails."""
    settings = settings or get_settings()
    started = time.monotonic()
    summary = Summary(source=source)

    if skip_train and not _registered(settings):  # fail fast, before any work
        raise StepError("train", "--skip-train given but no model is registered")

    if source == "live":
        args = ["ctgov"] + (["--max-pages", str(max_pages)] if max_pages is not None else [])
        _run_ingestion("ingest-ctgov", args)
        args = ["fda"] + (["--max-drugs", str(max_drugs)] if max_drugs is not None else [])
        _run_ingestion("ingest-fda", args)
    _run_ingestion("load", ["load", "--source", "sample" if source == "sample" else "landing"])
    _dbt("dbt-build", settings)

    registered = _registered(settings)
    if retrain or not registered:
        from ml.train import train

        echo("[train] training")
        try:
            train(settings)
        except Exception as exc:
            raise StepError("train", f"{type(exc).__name__}: {exc}") from exc
        summary.trained = True
    else:
        echo("[train] model already registered; reusing (pass --retrain to retrain)")

    try:
        model = load_model(settings)
        from ml.score import score

        echo(f"[score] scoring open studies with {model.name} v{model.version}")
        summary.n_scored = score(settings, model=model)
    except Exception as exc:
        raise StepError("score", f"{type(exc).__name__}: {exc}") from exc
    summary.model_name, summary.model_version = model.name, model.version

    _dbt("dbt-refresh", settings, select="marts")

    try:
        _collect_summary(settings, summary)
    except Exception as exc:
        raise StepError("summary", f"{type(exc).__name__}: {exc}") from exc
    summary.seconds = time.monotonic() - started
    return summary
