"""Daily trial-risk pipeline and the manually triggered training DAG.

STATIC TIER: written against Airflow with the cncf.kubernetes provider.
Not executed by the authors; validated only by tests/dags/test_dag_static.py (ast).

Settings come from Airflow Variables, then environment, then defaults:
  TRIAL_RISK_NAMESPACE (trial-risk), TRIAL_RISK_PIPELINE_IMAGE
  (ghcr.io/OWNER/trial-risk-pipeline:latest), TRIAL_RISK_MODE (local|cloud).
When the mode is "local" the PVC `trial-risk-warehouse` is mounted at /data so
all pods share the DuckDB file.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.models import Variable
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from kubernetes.client import models as k8s


def _setting(name: str, default: str) -> str:
    return str(Variable.get(name, default_var=os.environ.get(name, default)))


NAMESPACE = _setting("TRIAL_RISK_NAMESPACE", "trial-risk")
IMAGE = _setting("TRIAL_RISK_PIPELINE_IMAGE", "ghcr.io/OWNER/trial-risk-pipeline:latest")
MODE = _setting("TRIAL_RISK_MODE", "local")
DBT_TARGET = "local" if MODE == "local" else "cloud"

ENV_FROM = [
    k8s.V1EnvFromSource(config_map_ref=k8s.V1ConfigMapEnvSource(name="trial-risk-env")),
    k8s.V1EnvFromSource(secret_ref=k8s.V1SecretEnvSource(name="trial-risk-secrets")),
]

VOLUMES: list[k8s.V1Volume] = []
VOLUME_MOUNTS: list[k8s.V1VolumeMount] = []
if MODE == "local":
    VOLUMES = [
        k8s.V1Volume(
            name="warehouse",
            persistent_volume_claim=k8s.V1PersistentVolumeClaimVolumeSource(
                claim_name="trial-risk-warehouse"
            ),
        )
    ]
    VOLUME_MOUNTS = [k8s.V1VolumeMount(name="warehouse", mount_path="/data")]

DEFAULT_ARGS = {
    "owner": "trial-risk",
    "retries": 3,
    "retry_delay": timedelta(minutes=2),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=30),
}


def _pod(task_id: str, cmds: list[str]) -> KubernetesPodOperator:
    return KubernetesPodOperator(
        task_id=task_id,
        name=task_id.replace("_", "-"),
        namespace=NAMESPACE,
        image=IMAGE,
        cmds=cmds,
        env_from=ENV_FROM,
        volumes=VOLUMES,
        volume_mounts=VOLUME_MOUNTS,
        get_logs=True,
        is_delete_operator_pod=True,
    )


with DAG(
    dag_id="trial_risk_daily",
    description="Ingest, load, model, score and refresh marts",
    schedule="@daily",
    start_date=datetime(2026, 11, 1),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["trial-risk"],
) as daily_dag:
    ingest_ctgov = _pod("ingest_ctgov", ["python", "-m", "ingestion", "ctgov"])
    ingest_fda = _pod("ingest_fda", ["python", "-m", "ingestion", "fda"])
    load_raw = _pod("load_raw", ["python", "-m", "ingestion", "load", "--source", "landing"])
    dbt_build = _pod(
        "dbt_build",
        ["dbt", "build", "--project-dir", "dbt", "--profiles-dir", "dbt", "--target", DBT_TARGET],
    )
    score = _pod("score", ["python", "-m", "ml", "score"])
    refresh_marts = _pod(
        "refresh_marts",
        ["dbt", "build", "--project-dir", "dbt", "--profiles-dir", "dbt", "--target", DBT_TARGET,
         "--select", "marts"],
    )

    ingest_ctgov >> ingest_fda >> load_raw >> dbt_build >> score >> refresh_marts

with DAG(
    dag_id="trial_risk_train",
    description="Manually triggered model training",
    schedule=None,
    start_date=datetime(2026, 11, 1),
    catchup=False,
    default_args=DEFAULT_ARGS,
    tags=["trial-risk"],
) as train_dag:
    train = _pod("train", ["python", "-m", "ml", "train"])
