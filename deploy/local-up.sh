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

# Azurite account key is generated once per cluster; nothing is committed.
# Re-runs reuse the key already in the Secret, otherwise running pods would
# keep the old key and get 403s.
AZURITE_ACCOUNT="devstore"
AZURITE_KEY=""
if kubectl -n "$NAMESPACE" get secret trial-risk-secrets >/dev/null 2>&1; then
  EXISTING="$(kubectl -n "$NAMESPACE" get secret trial-risk-secrets -o jsonpath='{.data.AZURITE_ACCOUNTS}' | base64 -d)"
  AZURITE_KEY="${EXISTING#*:}"
fi
if [ -z "$AZURITE_KEY" ]; then
  AZURITE_KEY="$(openssl rand -base64 32)"
fi
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

# A re-tagged ":local" image is only picked up by new pods, and a changed
# Secret only by restarted ones. Azurite is NOT restarted (it uses neither
# rebuilt image, and a restart is not needed to keep its key).
echo "==> restart workloads to pick up rebuilt images / Secret"
kubectl -n "$NAMESPACE" rollout restart deployment/trial-risk-serving deployment/trial-risk-mlflow

# The Airflow chart runs the scheduler as a Deployment or a StatefulSet
# depending on version/executor; try both, each on its own, and only tolerate
# "not found" (any other error still stops the script).
restart_airflow() {
  local name="$1" kind out
  for kind in deployment statefulset; do
    if out="$(kubectl -n "$NAMESPACE" rollout restart "${kind}/${name}" 2>&1)"; then
      echo "$out"
      return 0
    fi
    case "$out" in
      *NotFound*|*"not found"*) ;;
      *) echo "$out" >&2; return 1 ;;
    esac
  done
  echo "note: ${name} not found as a deployment or statefulset; not restarted" >&2
}
restart_airflow airflow-scheduler
restart_airflow airflow-webserver

cat <<'MSG'

Stack is starting. Check with: kubectl -n trial-risk get pods
  Airflow UI:  http://localhost:8080  (default chart user; change it)
  Scoring API: http://localhost:8000/health  (NodePort 30081 mapped by kind)
  MLflow UI:   kubectl -n trial-risk port-forward svc/trial-risk-mlflow 5000:5000
First run: trigger the trial_risk_sample DAG (bootstrap: data, model, scores)
before relying on trial_risk_daily, which never trains a model.
Tear down (and reset all state) with: deploy/local-down.sh
MSG
