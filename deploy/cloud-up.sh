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

echo "==> helm: trial-risk"
helm upgrade --install trial-risk deploy/helm/trial-risk \
  --namespace "$NAMESPACE" \
  -f deploy/helm/trial-risk/values-aks.yaml \
  --set "images.pipeline.repository=ghcr.io/${OWNER}/trial-risk-pipeline" \
  --set "images.serving.repository=ghcr.io/${OWNER}/trial-risk-serving" \
  --set "keyVault.name=${KV}" \
  --set "keyVault.tenantId=${TENANT}" \
  --set "keyVault.clientId=${CSI_CLIENT_ID}" \
  --set "mlflow.artifactRoot=wasbs://mlflow@${STORAGE_ACCOUNT}.blob.core.windows.net/" \
  --set "config.SNOWFLAKE_ACCOUNT=${SNOWFLAKE_ACCOUNT:-}" \
  --set "config.SNOWFLAKE_USER=${SNOWFLAKE_USER:-}"

echo "==> helm: airflow (official chart)"
helm repo add apache-airflow https://airflow.apache.org >/dev/null 2>&1 || true
helm repo update apache-airflow >/dev/null
helm upgrade --install airflow apache-airflow/airflow \
  --version "$AIRFLOW_CHART_VERSION" \
  --namespace "$NAMESPACE" \
  -f deploy/helm/airflow-values-aks.yaml \
  --set "images.airflow.repository=ghcr.io/${OWNER}/trial-risk-airflow" \
  --set "env[1].value=ghcr.io/${OWNER}/trial-risk-pipeline:latest" \
  --timeout 10m

cat <<MSG

Done. Remember the one-time manual step: run deploy/snowflake/setup.sql in
Snowflake (placeholders: tenant ${TENANT}, storage account ${STORAGE_ACCOUNT}).
MSG
