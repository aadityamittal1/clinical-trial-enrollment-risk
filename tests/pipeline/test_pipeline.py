"""End-to-end: ``python -m pipeline run --source sample`` in a scratch area."""

import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from common.config import Settings

REPO = Path(__file__).resolve().parents[2]


def _env(work: Path) -> dict[str, str]:
    project = {name.upper() for name in Settings.model_fields}
    env = {k: v for k, v in os.environ.items() if k.upper() not in project}
    env.update(
        DUCKDB_PATH=str(work / "wh" / "trialrisk.duckdb"),
        MLFLOW_TRACKING_URI="sqlite:///" + (work / "mlflow" / "mlflow.db").as_posix(),
        DBT_TARGET_PATH=str(work / "dbt_target"),
        DBT_LOG_PATH=str(work / "dbt_logs"),
        DBT_SEND_ANONYMOUS_USAGE_STATS="false",
        PYTHONUTF8="1",
        MLFLOW_DISABLE_AGENT_HINT="1",
    )
    return env


def _pipeline(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pipeline", "run", *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1200,
        check=False,
    )


def _scalar(db: str, sql: str) -> int:
    con = duckdb.connect(db, read_only=True)
    try:
        row = con.execute(sql).fetchone()
    finally:
        con.close()
    assert row is not None
    return int(row[0])


def test_skip_train_fails_fast_without_model(tmp_path: Path) -> None:
    env = _env(tmp_path)
    proc = _pipeline(env, "--source", "sample", "--skip-train")
    assert proc.returncode != 0
    assert "step 'train'" in proc.stderr
    assert not Path(env["DUCKDB_PATH"]).exists()  # nothing ran before the check


def test_end_to_end_sample_then_rerun_reuses_model(tmp_path: Path) -> None:
    env = _env(tmp_path)
    proc = _pipeline(env, "--source", "sample")
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    out = proc.stdout
    for needle in (
        "raw (",
        "staging (",
        "marts (",
        "labels (marts.fct_trial_outcomes.label)",
        "model: trial-risk-lgbm version 1 (trained)",
        "studies scored this run:",
    ):
        assert needle in out, out

    db = env["DUCKDB_PATH"]
    latest = _scalar(db, "select count(*) from marts.ml_scores_latest")
    assert latest > 0
    assert latest == _scalar(db, "select count(*) from marts.dim_study where is_open")

    # Second run: model is reused (not retrained) and scores are appended again.
    proc2 = _pipeline(env, "--source", "sample")
    assert proc2.returncode == 0, proc2.stdout[-3000:] + proc2.stderr[-3000:]
    assert "version 1 (reused)" in proc2.stdout
    assert _scalar(db, "select count(*) from marts.ml_scores") == 2 * latest
    assert _scalar(db, "select count(*) from marts.ml_scores_latest") == latest

    # --skip-train now passes because a model exists.
    proc3 = _pipeline(env, "--source", "sample", "--skip-train")
    assert proc3.returncode == 0, proc3.stdout[-3000:] + proc3.stderr[-3000:]


def test_failing_step_is_named(tmp_path: Path) -> None:
    env = _env(tmp_path)
    # Break the offline "load" step: the warehouse path's parent is a regular file, so the
    # warehouse cannot be created.
    env["DUCKDB_PATH"] = str(tmp_path / "wh_is_a_file" / "x" / "y.duckdb")
    blocker = tmp_path / "wh_is_a_file"
    blocker.write_text("not a directory")
    proc = _pipeline(env, "--source", "sample")
    assert proc.returncode != 0
    assert "step 'load' failed" in proc.stderr


def test_failing_dbt_step_is_named(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pipeline.run as run

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["dbt"], 1, stdout="1 of 3 ERROR model x", stderr="")

    monkeypatch.setattr("pipeline.run.subprocess.run", fake_run)
    settings = Settings(
        duckdb_path=str(tmp_path / "wh" / "t.duckdb"),
        mlflow_tracking_uri="sqlite:///" + (tmp_path / "m" / "m.db").as_posix(),
    )
    with pytest.raises(run.StepError) as info:
        run.run_pipeline("sample", settings=settings, echo=lambda _: None)
    assert info.value.step == "dbt-build"
    assert "step 'dbt-build' failed" in str(info.value)
    assert "ERROR model x" in str(info.value)


def test_sample_load_is_independent_of_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pipeline.run as run

    monkeypatch.chdir(tmp_path)
    settings = Settings(duckdb_path=str(tmp_path / "wh.duckdb"))
    run._load_sample(settings)
    assert _scalar(settings.duckdb_path, "select count(*) from raw.ctgov_studies") > 0


def test_dbt_env_carries_snowflake_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipeline.run as run

    seen: dict[str, str] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs["env"])  # type: ignore[call-overload]
        seen["target"] = cmd[cmd.index("--target") + 1]
        return subprocess.CompletedProcess(cmd, 0, stdout="Done. PASS=1", stderr="")

    monkeypatch.setattr("pipeline.run.subprocess.run", fake_run)
    settings = Settings(mode="cloud", snowflake_account="acc", snowflake_password="pw")
    run._dbt("dbt-build", settings)
    assert seen["target"] == "cloud"
    assert seen["SNOWFLAKE_ACCOUNT"] == "acc"
    assert seen["SNOWFLAKE_PASSWORD"] == "pw"


@pytest.mark.parametrize("flags", [["--retrain", "--skip-train"]])
def test_retrain_and_skip_train_conflict(flags: list[str]) -> None:
    proc = _pipeline({**os.environ}, "--source", "sample", *flags)
    assert proc.returncode == 2
