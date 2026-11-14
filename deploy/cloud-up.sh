#!/usr/bin/env bash
# Provision Azure with OpenTofu, fetch AKS credentials, install the Helm releases.
# NOT TESTED on the build machine (no tofu/az/helm/kubectl there). Costs real
# money while up: run deploy/cloud-down.sh when finished.
# Needs: tofu, az (logged in: `az login`), kubectl, helm, and
#   deploy/tofu/terraform.tfvars (copy terraform.tfvars.example).
# Optional env: GHCR_OWNER (image owner), SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

NAMESPACE="trial-risk"
AIRFLOW_CHART_VERSION="${AIRFLOW_CHART_VERSION:-1.15.0}"
OWNER="${GHCR_OWNER:?set GHCR_OWNER to the GitHub owner of the ghcr.io images}"

for tool in tofu az kubectl helm; do
  command -v "$tool" >/dev/null 2>&1 || { echo "missing required tool: $tool" >&2; exit 1; }
done

if [ ! -f deploy/tofu/terraform.tfvars ]; then
  echo "deploy/tofu/terraform.tfvars not found; copy terraform.tfvars.example and fill it in" >&2
  exit 1
fi

echo "==> tofu apply"
tofu -chdir=deploy/tofu init -input=false
tofu -chdir=deploy/tofu apply -input=false -auto-approve

out() { tofu -chdir=deploy/tofu output -raw "$1"; }

RG="$(out resource_group_name)"
AKS="$(out aks_cluster_name)"
KV="$(out key_vault_name)"
TENANT="$(out tenant_id)"
CSI_CLIENT_ID="$(out key_vault_csi_client_id)"
STORAGE_ACCOUNT="$(out storage_account_name)"

echo "==> AKS credentials"
az aks get-credentials --resource-group "$RG" --name "$AKS" --overwrite-existing
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

OPENFDA_CREATED="$(out openfda_secret_created)"

echo "==> helm: trial-risk"
helm upgrade --install trial-risk deploy/helm/trial-risk   --namespace "$NAMESPACE"   -f deploy/helm/trial-risk/values-aks.yaml   --set "images.pipeline.repository=ghcr.io/${OWNER}/trial-risk-pipeline"   --set "images.serving.repository=ghcr.io/${OWNER}/trial-risk-serving"   --set "keyVault.name=${KV}"   --set "keyVault.tenantId=${TENANT}"   --set "keyVault.clientId=${CSI_CLIENT_ID}"   --set "keyVault.openfdaApiKey=${OPENFDA_CREATED}"   --set "mlflow.artifactRoot=wasbs://mlflow@${STORAGE_ACCOUNT}.blob.core.windows.net/"   --set "config.SNOWFLAKE_ACCOUNT=${SNOWFLAKE_ACCOUNT:-}"   --set "config.SNOWFLAKE_USER=${SNOWFLAKE_USER:-}"

echo "==> helm: airflow (official chart)"
# Helm replaces lists wholesale, so the override file carries the complete env
# contract (same names as airflow-values-aks.yaml) with OWNER filled in.
AF_OVERRIDE="$(mktemp)"
trap 'rm -f "$AF_OVERRIDE"' EXIT
cat >"$AF_OVERRIDE" <<YAML
images:
  airflow:
    repository: ghcr.io/${OWNER}/trial-risk-airflow
env:
  - name: TRIAL_RISK_MODE
    value: cloud
  - name: TRIAL_RISK_NAMESPACE
    value: ${NAMESPACE}
  - name: TRIAL_RISK_IMAGE
    value: ghcr.io/${OWNER}/trial-risk-pipeline:latest
  - name: TRIAL_RISK_IMAGE_PULL_POLICY
    value: Always
  - name: TRIAL_RISK_CONFIGMAP
    value: trial-risk-env
  - name: TRIAL_RISK_SECRET
    value: trial-risk-secrets
  - name: TRIAL_RISK_DUCKDB_PVC
    value: trial-risk-warehouse
  - name: TRIAL_RISK_DUCKDB_MOUNT
    value: /data
YAML
helm repo add apache-airflow https://airflow.apache.org >/dev/null 2>&1 || true
helm repo update apache-airflow >/dev/null
helm upgrade --install airflow apache-airflow/airflow   --version "$AIRFLOW_CHART_VERSION"   --namespace "$NAMESPACE"   -f deploy/helm/airflow-values-aks.yaml   -f "$AF_OVERRIDE"   --timeout 10m

cat <<MSG

Done. Remember the one-time manual step: run deploy/snowflake/setup.sql in
Snowflake (placeholders: tenant ${TENANT}, storage account ${STORAGE_ACCOUNT}).
MSG
