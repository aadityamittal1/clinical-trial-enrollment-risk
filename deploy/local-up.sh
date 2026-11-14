#!/usr/bin/env bash
# Bring up the local Kubernetes stack: kind cluster, images, Secret, Helm releases.
# NOT TESTED on the build machine (no docker/kind/helm/kubectl there).
# Needs: docker, kind, kubectl, helm, openssl. Reads ./.env (git-ignored).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

NAMESPACE="trial-risk"
CLUSTER="trial-risk"
AIRFLOW_CHART_VERSION="${AIRFLOW_CHART_VERSION:-1.15.0}"

for tool in docker kind kubectl helm openssl; do
  command -v "$tool" >/dev/null 2>&1 || { echo "missing required tool: $tool" >&2; exit 1; }
done

# Load .env without echoing values.
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
else
  echo "warning: .env not found, continuing with an empty environment (copy .env.example)" >&2
fi

echo "==> kind cluster"
if ! kind get clusters | grep -qx "$CLUSTER"; then
  kind create cluster --config deploy/kind/cluster.yaml
fi
kubectl config use-context "kind-${CLUSTER}" >/dev/null

echo "==> build images"
docker build -f deploy/docker/pipeline.Dockerfile -t trial-risk-pipeline:local .
docker build -f deploy/docker/serving.Dockerfile -t trial-risk-serving:local .
docker build -f deploy/docker/airflow.Dockerfile -t trial-risk-airflow:local .

echo "==> load images into kind"
kind load docker-image --name "$CLUSTER" \
  trial-risk-pipeline:local trial-risk-serving:local trial-risk-airflow:local

echo "==> namespace and Secret (values come from .env / generated, never from the repo)"
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

# Azurite account key is generated per cluster; nothing is committed.
AZURITE_ACCOUNT="devstore"
AZURITE_KEY="$(openssl rand -base64 32)"
AZURE_CONN="DefaultEndpointsProtocol=http;AccountName=${AZURITE_ACCOUNT};AccountKey=${AZURITE_KEY};BlobEndpoint=http://trial-risk-azurite:10000/${AZURITE_ACCOUNT};"

kubectl -n "$NAMESPACE" create secret generic trial-risk-secrets \
  --from-literal=AZURITE_ACCOUNTS="${AZURITE_ACCOUNT}:${AZURITE_KEY}" \
  --from-literal=AZURE_STORAGE_CONNECTION_STRING="${AZURE_CONN}" \
  --from-literal=SNOWFLAKE_PASSWORD="${SNOWFLAKE_PASSWORD:-}" \
  --from-literal=OPENFDA_API_KEY="${OPENFDA_API_KEY:-}" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "==> helm: trial-risk"
helm upgrade --install trial-risk deploy/helm/trial-risk \
  --namespace "$NAMESPACE" \
  -f deploy/helm/trial-risk/values-local.yaml

echo "==> helm: airflow (official chart)"
helm repo add apache-airflow https://airflow.apache.org >/dev/null 2>&1 || true
helm repo update apache-airflow >/dev/null
helm upgrade --install airflow apache-airflow/airflow \
  --version "$AIRFLOW_CHART_VERSION" \
  --namespace "$NAMESPACE" \
  -f deploy/helm/airflow-values-local.yaml \
  --timeout 10m

cat <<'MSG'

Stack is starting. Check with: kubectl -n trial-risk get pods
  Airflow UI:  http://localhost:8080  (default chart user; change it)
  Scoring API: kubectl -n trial-risk port-forward svc/trial-risk-serving 8000:8000
  MLflow UI:   kubectl -n trial-risk port-forward svc/trial-risk-mlflow 5000:5000
Tear down with: deploy/local-down.sh
MSG
