"""Static checks only: Airflow is never imported, nothing is executed."""

import ast
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DAG_SRC = (ROOT / "dags" / "trial_risk_daily.py").read_text(encoding="utf-8")
TREE = ast.parse(DAG_SRC)


def _pod_calls() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for node in ast.walk(TREE):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_pod"
            and len(node.args) == 2
        ):
            tid = ast.literal_eval(node.args[0])
            elts = node.args[1].elts  # type: ignore[attr-defined]
            out[tid] = [
                ast.literal_eval(e) if isinstance(e, ast.Constant) else "<expr>" for e in elts
            ]
    return out


def test_task_ids_and_commands() -> None:
    pods = _pod_calls()
    assert set(pods) == {
        "ingest_ctgov", "ingest_fda", "load_raw", "dbt_build", "score", "refresh_marts", "train",
    }
    assert pods["ingest_ctgov"] == ["python", "-m", "ingestion", "ctgov"]
    assert pods["ingest_fda"] == ["python", "-m", "ingestion", "fda"]
    assert pods["load_raw"][:4] == ["python", "-m", "ingestion", "load"]
    assert pods["dbt_build"][:2] == ["dbt", "build"]
    assert "--project-dir" in pods["dbt_build"] and "--profiles-dir" in pods["dbt_build"]
    assert pods["score"] == ["python", "-m", "ml", "score"]
    assert pods["train"] == ["python", "-m", "ml", "train"]


def test_order() -> None:
    chain: list[str] | None = None
    for node in ast.walk(TREE):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.BinOp):
            names: list[str] = []
            cur: ast.expr = node.value
            while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.RShift):
                assert isinstance(cur.right, ast.Name)
                names.append(cur.right.id)
                cur = cur.left
            if isinstance(cur, ast.Name):
                names.append(cur.id)
                chain = names[::-1]
    assert chain == [
        "ingest_ctgov", "ingest_fda", "load_raw", "dbt_build", "score", "refresh_marts",
    ]


def test_dag_settings() -> None:
    assert 'dag_id="trial_risk_daily"' in DAG_SRC
    assert 'dag_id="trial_risk_train"' in DAG_SRC
    assert DAG_SRC.count("catchup=False") == 2
    assert "schedule=None" in DAG_SRC
    assert '"retries": 3' in DAG_SRC and "retry_exponential_backoff" in DAG_SRC
    for needle in ("trial-risk-env", "trial-risk-secrets", "trial-risk-warehouse"):
        assert needle in DAG_SRC
    assert "airflow.providers.cncf.kubernetes.operators.pod" in DAG_SRC


def _wf(name: str) -> dict:
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))


def _triggers(wf: dict) -> dict:
    return wf.get("on", wf.get(True))  # PyYAML parses bare `on` as True


def test_ci_jobs() -> None:
    wf = _wf("ci.yml")
    assert "pull_request" in _triggers(wf)
    assert {"lint-test", "pipeline-sample", "docker-build", "kind-e2e"} <= set(wf["jobs"])
    fast = str(wf["jobs"]["pipeline-sample"])
    assert "python -m pipeline run --source sample" in fast
    assert "pytest tests/ml -k smoke" in fast
    assert "kind" not in fast
    assert "deploy/local-up.sh" in str(wf["jobs"]["kind-e2e"])


def test_cd_jobs() -> None:
    wf = _wf("cd.yml")
    assert "main" in _triggers(wf)["push"]["branches"]
    assert {"push-images", "deploy-aks"} <= set(wf["jobs"])
    assert "CLOUD_MODE_UP" in wf["jobs"]["deploy-aks"]["if"]
    assert wf["jobs"]["deploy-aks"]["needs"] == "push-images"
    assert "helm upgrade" in str(wf["jobs"]["deploy-aks"])
    assert "ghcr.io/${{ github.repository_owner }}/trial-risk-pipeline" in str(wf)


def test_no_literal_secrets() -> None:
    for name in ("ci.yml", "cd.yml"):
        text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        for line in text.splitlines():
            if "password" in line.lower() or "creds" in line.lower():
                assert "${{ secrets." in line
