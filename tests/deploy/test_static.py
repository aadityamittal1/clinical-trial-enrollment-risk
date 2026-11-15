"""Static checks for the deploy/ tree. Nothing here runs docker, helm, kind or tofu."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
CHART = DEPLOY / "helm" / "trial-risk"
TEXT_SUFFIXES = {".yaml", ".yml", ".sh", ".tf", ".sql", ".tpl", ".example", ".json", ""}


def _files(suffixes: set[str] | None = None) -> list[Path]:
    out = []
    for p in DEPLOY.rglob("*"):
        if p.is_file() and (suffixes is None or p.suffix in suffixes):
            out.append(p)
    return sorted(out)


def _plain_yaml_files() -> list[Path]:
    return [p for p in _files({".yaml", ".yml"}) if "templates" not in p.relative_to(DEPLOY).parts]


def test_yaml_outside_templates_parses() -> None:
    files = _plain_yaml_files()
    assert files, "expected YAML files under deploy/"
    for p in files:
        docs = list(yaml.safe_load_all(p.read_text(encoding="utf-8")))
        assert docs, f"{p} is empty"


def test_chart_yaml_keys() -> None:
    chart = yaml.safe_load((CHART / "Chart.yaml").read_text(encoding="utf-8"))
    for key in ("apiVersion", "name", "version", "appVersion", "description"):
        assert key in chart, f"Chart.yaml missing {key}"
    assert chart["name"] == "trial-risk"
    assert chart["apiVersion"] == "v2"


def _has_path(data: object, path: list[str]) -> bool:
    cur = data
    for part in path:
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True


def _leaf_paths(data: object, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    if isinstance(data, dict) and data:
        paths = []
        for k, v in data.items():
            paths += _leaf_paths(v, (*prefix, str(k)))
        return paths
    return [prefix] if prefix else []


def test_templates_reference_only_defined_values() -> None:
    values = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    pattern = re.compile(r"\.Values((?:\.[A-Za-z_][A-Za-z0-9_]*)+)")
    seen = 0
    for tpl in (CHART / "templates").iterdir():
        for match in pattern.finditer(tpl.read_text(encoding="utf-8")):
            path = match.group(1).lstrip(".").split(".")
            seen += 1
            assert _has_path(values, path), (
                f"{tpl.name}: .Values.{'.'.join(path)} not in values.yaml"
            )
    assert seen > 20


def test_overlay_values_are_known_keys() -> None:
    base = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    for name in ("values-local.yaml", "values-aks.yaml"):
        overlay = yaml.safe_load((CHART / name).read_text(encoding="utf-8"))
        for path in _leaf_paths(overlay):
            assert _has_path(base, list(path)), f"{name}: {'.'.join(path)} not in values.yaml"


def test_required_names_and_modes() -> None:
    base = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    local = yaml.safe_load((CHART / "values-local.yaml").read_text(encoding="utf-8"))
    aks = yaml.safe_load((CHART / "values-aks.yaml").read_text(encoding="utf-8"))
    assert base["configMapName"] == "trial-risk-env"
    assert base["secrets"]["name"] == "trial-risk-secrets"
    assert local["config"]["MODE"] == "local"
    assert local["config"]["STORAGE_BACKEND"] == "azure"
    assert local["azurite"]["enabled"] is True
    assert local["azurite"]["persistence"]["enabled"] is True
    assert base["azurite"]["persistence"]["enabled"] is False
    assert local["duckdb"]["persistence"]["enabled"] is True
    assert aks["config"]["MODE"] == "cloud"
    assert aks["keyVault"]["enabled"] is True
    assert aks["azurite"]["enabled"] is False
    expected_env = {
        "MODE",
        "STORAGE_BACKEND",
        "LANDING_DIR",
        "LANDING_CONTAINER",
        "DUCKDB_PATH",
        "MLFLOW_TRACKING_URI",
        "MODEL_NAME",
        "SNOWFLAKE_ACCOUNT",
        "SNOWFLAKE_USER",
        "SNOWFLAKE_ROLE",
        "SNOWFLAKE_WAREHOUSE",
        "SNOWFLAKE_DATABASE",
    }
    assert expected_env <= set(base["config"])


def test_airflow_values_choose_baked_dags() -> None:
    for name in ("airflow-values-local.yaml", "airflow-values-aks.yaml"):
        v = yaml.safe_load((DEPLOY / "helm" / name).read_text(encoding="utf-8"))
        assert v["dags"]["gitSync"]["enabled"] is False
        assert "trial-risk-airflow" in v["images"]["airflow"]["repository"]
    assert (DEPLOY / "docker" / "airflow.Dockerfile").exists()


def test_shell_scripts_strict_and_lf() -> None:
    scripts = _files({".sh"})
    names = {p.name for p in scripts}
    assert {
        "local-up.sh",
        "local-down.sh",
        "cloud-up.sh",
        "cloud-down.sh",
        "entrypoint.sh",
    } <= names
    for p in scripts:
        raw = p.read_bytes()
        assert b"\r\n" not in raw, f"{p} has CRLF line endings"
        text = raw.decode("utf-8")
        assert text.startswith("#!/usr/bin/env bash\n"), p
        assert "set -euo pipefail" in text, p


def test_scripts_use_tofu_not_terraform() -> None:
    for p in _files({".sh"}):
        assert not re.search(r"\bterraform\s+(init|apply|plan|destroy|output)", p.read_text())
    tf = _files({".tf"})
    assert {p.name for p in tf} >= {"main.tf", "variables.tf", "outputs.tf", "versions.tf"}
    assert not [p for p in _files() if p.suffix == ".tfstate"]


def test_tofu_has_required_resources() -> None:
    main = (DEPLOY / "tofu" / "main.tf").read_text(encoding="utf-8")
    for needle in (
        'resource "azurerm_resource_group"',
        'resource "azurerm_storage_account"',
        'name                  = "landing"',
        'name                  = "mlflow"',
        'resource "azurerm_key_vault"',
        'sku_tier            = "Free"',
        "key_vault_secrets_provider",
        'resource "azurerm_consumption_budget_subscription"',
    ):
        assert needle in main, needle
    assert re.findall(r"threshold\s*=\s*(\d+)", main) == ["50", "80", "100"]
    variables = (DEPLOY / "tofu" / "variables.tf").read_text(encoding="utf-8")
    assert '"eastus2"' in variables
    assert "default     = 25" in variables
    assert (DEPLOY / "tofu" / "terraform.tfvars.example").exists()


@pytest.mark.parametrize("name", ["main.tf", "variables.tf", "outputs.tf", "versions.tf"])
def test_tf_braces_balanced(name: str) -> None:
    text = (DEPLOY / "tofu" / name).read_text(encoding="utf-8")
    text = re.sub(r"#.*", "", text)
    text = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)  # drop string contents (incl. ${...})
    assert text.count("{") == text.count("}")
    assert text.count("[") == text.count("]")


def test_snowflake_setup_has_required_objects() -> None:
    sql = (DEPLOY / "snowflake" / "setup.sql").read_text(encoding="utf-8").upper()
    for needle in (
        "CREATE DATABASE",
        "TRIAL_RISK.RAW",
        "TRIAL_RISK.STAGING",
        "TRIAL_RISK.MARTS",
        "WAREHOUSE_SIZE = 'XSMALL'",
        "AUTO_SUSPEND = 60",
        "CREATE RESOURCE MONITOR",
        "75 PERCENT DO SUSPEND",
        "CREATE ROLE",
        "CREATE STORAGE INTEGRATION",
        "RAW.LANDING_STAGE",
        "TYPE = JSON",
    ):
        assert needle in sql, needle


def test_dockerfiles() -> None:
    for name in ("pipeline", "serving", "airflow"):
        text = (DEPLOY / "docker" / f"{name}.Dockerfile").read_text(encoding="utf-8")
        assert text.count("\nFROM ") + text.startswith("FROM ") >= 1
    for name, pkgs in (
        ("pipeline", ["common", "ingestion", "ml", "pipeline", "dbt", "data/sample"]),
        ("serving", ["common", "ml", "serving"]),
    ):
        text = (DEPLOY / "docker" / f"{name}.Dockerfile").read_text(encoding="utf-8")
        assert "USER app" in text
        assert "--extra cloud" in text
        assert "uv sync" in text
        for pkg in pkgs:
            assert f"COPY --chown=app:app {pkg}/" in text, f"{name}: {pkg}"
    assert "uvicorn" in (DEPLOY / "docker" / "serving.Dockerfile").read_text()
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    for entry in (".git", ".venv", ".env"):
        assert entry in ignore
    assert "!deploy/docker" in ignore


def test_azurite_data_on_pvc_when_enabled() -> None:
    tpl = (CHART / "templates" / "azurite.yaml").read_text(encoding="utf-8")
    assert "{{- if .Values.azurite.persistence.enabled }}" in tpl
    assert "kind: PersistentVolumeClaim" in tpl
    assert "claimName: {{ .Values.azurite.persistence.claimName }}" in tpl
    assert "emptyDir: {}" in tpl  # still the default when persistence is off
    assert "{{- else }}" in tpl


def test_uv_pin_identical_in_dockerfiles() -> None:
    pins = {}
    for name in ("pipeline", "serving"):
        text = (DEPLOY / "docker" / f"{name}.Dockerfile").read_text(encoding="utf-8")
        found = re.findall(r"COPY --from=ghcr\.io/astral-sh/uv:([0-9][0-9.]*) /uv ", text)
        assert len(found) == 1, f"{name}: expected one pinned uv image, got {found}"
        pins[name] = found[0]
    assert pins["pipeline"] == pins["serving"], pins


def test_probes_on_health() -> None:
    serving = (CHART / "templates" / "serving.yaml").read_text(encoding="utf-8")
    # Readiness on /health; liveness only on the TCP port (a slow MLflow must not kill the pod).
    assert serving.count("path: /health") == 1
    ready = serving.index("readinessProbe:")
    live = serving.index("livenessProbe:")
    assert "path: /health" in serving[ready:live]
    assert "tcpSocket:" in serving[live : live + 80]
    assert "MLFLOW_HTTP_REQUEST_MAX_RETRIES" in serving
    assert "MLFLOW_HTTP_REQUEST_TIMEOUT" in serving
    base = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    assert int(base["serving"]["mlflowHttp"]["maxRetries"]) <= 2
    assert int(base["serving"]["mlflowHttp"]["timeoutSeconds"]) <= 15


# --------------------------------------------------------------- secret scan

# A value counts as a placeholder (allowed) when it is empty, "" / '' or starts
# with $, {{ or < (optionally after a quote).
_KEYWORD = r"(?i)(?:password|passwd|secret|token|api_?key)\w*\s*(?:[:=])(?!-)\s*"
SECRET_PATTERNS = {
    "account key": re.compile(r"AccountKey=(?!\$\{)[A-Za-z0-9+/=]{20,}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "github token": re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{30,}"),
    "aws key": re.compile(r"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}(?![A-Za-z0-9])"),
    "sas token": re.compile(r"[?&]sig=[A-Za-z0-9%+/=]{20,}"),
    "long base64": re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9+/])"),
    "credential assignment": re.compile(_KEYWORD + r"['\"]?(?!\$|\{\{|<|['\"]|\s|$)\S{8,}"),
}


def scan_line(line: str) -> list[str]:
    return [label for label, rx in SECRET_PATTERNS.items() if rx.search(line)]


def _utf8_text(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def test_no_secret_values_under_deploy() -> None:
    offenders = []
    scanned = 0
    for p in _files():
        text = _utf8_text(p)
        if text is None:
            continue
        scanned += 1
        for lineno, line in enumerate(text.splitlines(), 1):
            for label in scan_line(line):
                offenders.append(f"{p.relative_to(ROOT)}:{lineno}: {label}")
    assert scanned >= 25
    assert not offenders, "\n".join(offenders)


def test_secret_scanner_catches_known_shapes() -> None:
    leaked = [
        "SNOWFLAKE_PASSWORD: hunter2hunter2",
        "OPENFDA_API_KEY=abcd1234efgh",
        '  password = "S3cretValue99"',
        "# token: ghp_" + "a" * 36,
        "--from-literal=SNOWFLAKE_PASSWORD=plaintext123",
        "DefaultEndpointsProtocol=http;AccountName=x;AccountKey=" + "A" * 40 + "==;",
        "AKIA" + "A" * 16,
        "-----BEGIN RSA PRIVATE KEY-----",
        "blob = " + "Ab1/" * 12,
    ]
    for line in leaked:
        assert scan_line(line), f"scanner missed: {line}"


def test_secret_scanner_allows_placeholders() -> None:
    ok = [
        'SNOWFLAKE_PASSWORD: ""',
        '--from-literal=SNOWFLAKE_PASSWORD="${SNOWFLAKE_PASSWORD:-}"',
        'export TF_VAR_snowflake_password="${TF_VAR_snowflake_password:-destroy-only}"',
        "secretName: {{ .Values.secrets.name }}",
        "AccountKey=${AZURITE_KEY};",
        "secrets: {}",
        "objectType: secret",
        "AZURE_TENANT_ID = '<AZURE_TENANT_ID>'",
    ]
    for line in ok:
        assert not scan_line(line), f"false positive: {line} -> {scan_line(line)}"


def test_airflow_env_contract_exact() -> None:
    expected = {
        "TRIAL_RISK_MODE",
        "TRIAL_RISK_NAMESPACE",
        "TRIAL_RISK_IMAGE",
        "TRIAL_RISK_IMAGE_PULL_POLICY",
        "TRIAL_RISK_CONFIGMAP",
        "TRIAL_RISK_SECRET",
        "TRIAL_RISK_DUCKDB_PVC",
        "TRIAL_RISK_DUCKDB_MOUNT",
    }
    modes = {
        "airflow-values-local.yaml": ("local", "IfNotPresent"),
        "airflow-values-aks.yaml": ("cloud", "Always"),
    }
    for name, (mode, pull) in modes.items():
        v = yaml.safe_load((DEPLOY / "helm" / name).read_text(encoding="utf-8"))
        env = {e["name"]: e["value"] for e in v["env"]}
        assert set(env) == expected, name
        assert len(v["env"]) == len(expected), f"{name}: duplicate env names"
        assert env["TRIAL_RISK_MODE"] == mode
        assert env["TRIAL_RISK_IMAGE_PULL_POLICY"] == pull
        assert env["TRIAL_RISK_NAMESPACE"] == "trial-risk"
        assert env["TRIAL_RISK_DUCKDB_PVC"] == "trial-risk-warehouse"
    up = (DEPLOY / "cloud-up.sh").read_text(encoding="utf-8")
    assert "env[" not in up, "cloud-up.sh must not use positional env --set"
    assert "TRIAL_RISK_MODE" in up


def test_cloud_up_requires_snowflake_identity() -> None:
    up = (DEPLOY / "cloud-up.sh").read_text(encoding="utf-8")
    assert "${SNOWFLAKE_ACCOUNT:?" in up and "${SNOWFLAKE_USER:?" in up
    assert "SNOWFLAKE_ACCOUNT:-" not in up and "SNOWFLAKE_USER:-" not in up


def test_serving_nodeport_local() -> None:
    local = yaml.safe_load((CHART / "values-local.yaml").read_text(encoding="utf-8"))
    assert local["serving"]["service"] == {"type": "NodePort", "nodePort": 30081}
    kind = yaml.safe_load((DEPLOY / "kind" / "cluster.yaml").read_text(encoding="utf-8"))
    ports = kind["nodes"][0]["extraPortMappings"]
    assert {"containerPort": 30081, "hostPort": 8000, "protocol": "TCP"} in ports


def test_budget_is_subscription_scoped_and_destroy_safe() -> None:
    main = (DEPLOY / "tofu" / "main.tf").read_text(encoding="utf-8")
    assert "azurerm_consumption_budget_subscription" in main
    assert "azurerm_consumption_budget_resource_group" not in main
    assert "time_sleep" in main
    down = (DEPLOY / "cloud-down.sh").read_text(encoding="utf-8")
    assert "TF_VAR_snowflake_password" in down
    code = [ln for ln in down.splitlines() if not ln.lstrip().startswith("#")]
    assert not any("helm uninstall" in ln for ln in code)
    assert "terraform.tfvars" in down


def test_local_up_is_rerunnable() -> None:
    up = (DEPLOY / "local-up.sh").read_text(encoding="utf-8")
    assert "get secret trial-risk-secrets" in up
    assert "rollout restart" in up
    assert "kind load docker-image" in up
    # Airflow restarts: each component separately, Deployment or StatefulSet, no blanket || true.
    assert "for kind in deployment statefulset" in up
    assert "restart_airflow airflow-scheduler" in up
    assert "restart_airflow airflow-webserver" in up
    for line in up.splitlines():
        if "rollout restart" in line:
            assert "|| true" not in line, line


def test_snowflake_role_reachable() -> None:
    sql = (DEPLOY / "snowflake" / "setup.sql").read_text(encoding="utf-8")
    assert "GRANT ROLE TRIAL_RISK_ROLE TO ROLE SYSADMIN" in sql
    assert "GRANT CREATE SCHEMA ON DATABASE TRIAL_RISK TO ROLE TRIAL_RISK_ROLE;" in sql


def test_no_terraform_state_or_tfvars_committed() -> None:
    for p in _files():
        assert p.name != "terraform.tfvars"
        assert not p.name.endswith((".tfstate", ".tfstate.backup"))
        assert p.name != ".env"
