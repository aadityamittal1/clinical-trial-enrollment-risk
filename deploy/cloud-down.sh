#!/usr/bin/env bash
# Uninstall the Helm releases and destroy all Azure resources with OpenTofu.
# NOT TESTED on the build machine.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

NAMESPACE="trial-risk"

command -v tofu >/dev/null 2>&1 || { echo "missing required tool: tofu" >&2; exit 1; }

# Best effort: the cluster may already be unreachable.
if command -v helm >/dev/null 2>&1; then
  helm uninstall airflow --namespace "$NAMESPACE" 2>/dev/null || true
  helm uninstall trial-risk --namespace "$NAMESPACE" 2>/dev/null || true
fi

tofu -chdir=deploy/tofu init -input=false
tofu -chdir=deploy/tofu destroy -input=false -auto-approve
