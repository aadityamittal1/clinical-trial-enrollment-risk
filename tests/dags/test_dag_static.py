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
        "ingest_ctgov",
        "ingest_fda",
        "load_raw",
        "source_freshness",
        "dbt_build",
        "score",
        "refresh_marts",
        "train",
        "pipeline_sample",
    }
    assert pods["ingest_ctgov"] == ["python", "-m", "ingestion", "ctgov"]
    assert pods["ingest_fda"] == ["python", "-m", "ingestion", "fda"]
    assert pods["load_raw"] == ["python", "-m", "ingestion", "load", "--source", "landing"]
    base = ["dbt", "build", "--project-dir", "dbt", "--profiles-dir", "dbt", "--target", "<expr>"]
    assert pods["dbt_build"] == base
    assert pods["refresh_marts"] == [*base, "--select", "marts"]
    assert pods["score"] == ["python", "-m", "ml", "score"]
    assert pods["train"] == ["python", "-m", "ml", "train"]
    assert pods["source_freshness"] == [
        "dbt",
        "source",
        "freshness",
        "--project-dir",
        "dbt",
        "--profiles-dir",
        "dbt",
        "--target",
        "<expr>",
    ]
    assert pods["pipeline_sample"] == ["python", "-m", "pipeline", "run", "--source", "sample"]


def test_dbt_target_expression() -> None:
    assert 'DBT_TARGET = "local" if MODE == "local" else "cloud"' in DAG_SRC


def test_image_and_pod_settings() -> None:
    assert '"trial-risk-pipeline:local" if MODE == "local"' in DAG_SRC
    assert "image_pull_policy=PULL_POLICY" in DAG_SRC
    assert 'on_finish_action="delete_pod"' in DAG_SRC
    assert "is_delete_operator_pod" not in DAG_SRC


def test_env_contract_and_security_context() -> None:
    names = {
        n.value
        for n in ast.walk(TREE)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and n.value.startswith("TRIAL_RISK_")
    }
    assert names == {
        "TRIAL_RISK_MODE",
        "TRIAL_RISK_NAMESPACE",
        "TRIAL_RISK_IMAGE",
        "TRIAL_RISK_IMAGE_PULL_POLICY",
        "TRIAL_RISK_CONFIGMAP",
        "TRIAL_RISK_SECRET",
        "TRIAL_RISK_DUCKDB_PVC",
        "TRIAL_RISK_DUCKDB_MOUNT",
    }
    assert "Variable" not in DAG_SRC
    assert "run_as_user=10001" in DAG_SRC and "fs_group=10001" in DAG_SRC


def test_airflow_image_in_ci_and_cd() -> None:
    ci = str(_wf("ci.yml")["jobs"]["docker-build"])
    assert "deploy/docker/airflow.Dockerfile" in ci and "trial-risk-airflow:local" in ci
    cd = str(_wf("cd.yml")["jobs"]["push-images"])
    assert "deploy/docker/airflow.Dockerfile" in cd
    assert "ghcr.io/${{ env.OWNER }}/trial-risk-airflow" in cd


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
        "ingest_ctgov",
        "ingest_fda",
        "load_raw",
        "source_freshness",
        "dbt_build",
        "score",
        "refresh_marts",
    ]


def test_dag_settings() -> None:
    assert 'dag_id="trial_risk_daily"' in DAG_SRC
    assert 'dag_id="trial_risk_train"' in DAG_SRC
    assert 'dag_id="trial_risk_sample"' in DAG_SRC
    assert DAG_SRC.count("catchup=False") == 3
    assert DAG_SRC.count("schedule=None") == 2
    assert '"retries": 3' in DAG_SRC and "retry_exponential_backoff" in DAG_SRC
    assert "name=CONFIGMAP" in DAG_SRC and "name=SECRET" in DAG_SRC
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
    kind = str(wf["jobs"]["kind-e2e"])
    assert "deploy/local-up.sh" in kind
    assert "kubectl wait" in kind and "kubectl get pods" in kind and "-n trial-risk" in kind


def _kind_runs() -> list[str]:
    steps = _wf("ci.yml")["jobs"]["kind-e2e"]["steps"]
    return [s["run"] for s in steps if "run" in s]


def test_kind_job_runs_pipeline_in_cluster() -> None:
    runs = _kind_runs()
    # Nothing in the kind job runs the pipeline or dbt on the runner host.
    for run in runs:
        assert "uv run python -m pipeline" not in run, run
        assert "uv run dbt" not in run, run
    job_step = next(r for r in runs if "kind: Job" in r)
    body = job_step.split("<<'YAML' | kubectl -n trial-risk apply -f -\n", 1)[1]
    body = body.split("\nYAML\n", 1)[0]
    job = yaml.safe_load(body)
    assert job["kind"] == "Job"
    pod = job["spec"]["template"]["spec"]
    assert pod["restartPolicy"] == "Never"
    assert pod["securityContext"] == {"runAsUser": 10001, "fsGroup": 10001}
    (c,) = pod["containers"]
    assert c["image"] == "trial-risk-pipeline:local"
    assert c["imagePullPolicy"] in ("Never", "IfNotPresent")
    assert c["command"] == ["python", "-m", "pipeline", "run", "--source", "sample"]
    assert {"configMapRef": {"name": "trial-risk-env"}} in c["envFrom"]
    assert {"secretRef": {"name": "trial-risk-secrets"}} in c["envFrom"]
    assert c["volumeMounts"] == [{"name": "warehouse", "mountPath": "/data"}]
    claim = pod["volumes"][0]["persistentVolumeClaim"]["claimName"]
    assert claim == "trial-risk-warehouse"
    # Waits with a bound, and prints the logs whatever the outcome.
    assert "seq 1 90" in job_step and "logs job/pipeline-sample" in job_step
    health = next(r for r in runs if "localhost:8000/health" in r)
    assert "curl -fsS http://localhost:8000/health" in health
    assert '"ok"' in health and "seq 1 18" in health


def test_cd_jobs() -> None:
    wf = _wf("cd.yml")
    assert "main" in _triggers(wf)["push"]["branches"]
    assert {"push-images", "deploy-aks"} <= set(wf["jobs"])
    assert "CLOUD_MODE_UP" in wf["jobs"]["deploy-aks"]["if"]
    assert wf["jobs"]["deploy-aks"]["needs"] == "push-images"
    assert "helm upgrade" in str(wf["jobs"]["deploy-aks"])
    assert "ghcr.io/${{ env.OWNER }}/trial-risk-pipeline" in str(wf)
    assert "${GITHUB_REPOSITORY_OWNER,,}" in str(wf)
    deploy = str(wf["jobs"]["deploy-aks"])
    assert "images.pipeline.tag=" in deploy and "images.serving.repository=" in deploy
    # Without --reuse-values the upgrade would drop what cloud-up.sh set with --set.
    assert "--reuse-values" in deploy


def test_no_literal_secrets() -> None:
    for name in ("ci.yml", "cd.yml"):
        text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        for line in text.splitlines():
            if "password" in line.lower() or "creds" in line.lower():
                assert "${{ secrets." in line


def _dag_tasks() -> dict[str, set[str]]:
    """dag_id -> names of _pod(...) tasks created inside its `with DAG(...)` block."""
    out: dict[str, set[str]] = {}
    for node in ast.walk(TREE):
        if not isinstance(node, ast.With):
            continue
        call = node.items[0].context_expr
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)):
            continue
        if call.func.id != "DAG":
            continue
        dag_id = next(ast.literal_eval(k.value) for k in call.keywords if k.arg == "dag_id")
        tasks = set()
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Name)
                and sub.func.id == "_pod"
            ):
                tasks.add(ast.literal_eval(sub.args[0]))
        out[dag_id] = tasks
    return out


def test_sample_dag_is_manual_bootstrap() -> None:
    dags = _dag_tasks()
    assert dags["trial_risk_sample"] == {"pipeline_sample"}
    assert dags["trial_risk_train"] == {"train"}
    # The daily DAG never trains (bootstrap via trial_risk_sample / trial_risk_train).
    assert "train" not in dags["trial_risk_daily"]
    assert "pipeline_sample" not in dags["trial_risk_daily"]
    with_sample = next(
        n
        for n in ast.walk(TREE)
        if isinstance(n, ast.With)
        and any(
            k.arg == "dag_id" and ast.literal_eval(k.value) == "trial_risk_sample"
            for k in n.items[0].context_expr.keywords
        )  # type: ignore[attr-defined]
    )
    call = with_sample.items[0].context_expr
    kw = {k.arg: k.value for k in call.keywords}  # type: ignore[attr-defined]
    assert isinstance(kw["schedule"], ast.Constant) and kw["schedule"].value is None
    assert isinstance(kw["catchup"], ast.Constant) and kw["catchup"].value is False
