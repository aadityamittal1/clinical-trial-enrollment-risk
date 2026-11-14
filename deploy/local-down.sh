#!/usr/bin/env bash
# Remove the local kind cluster (and with it every release, PVC and Secret).
# NOT TESTED on the build machine.
set -euo pipefail

CLUSTER="trial-risk"

command -v kind >/dev/null 2>&1 || { echo "missing required tool: kind" >&2; exit 1; }

if kind get clusters | grep -qx "$CLUSTER"; then
  kind delete cluster --name "$CLUSTER"
else
  echo "kind cluster '$CLUSTER' not found; nothing to do"
fi
