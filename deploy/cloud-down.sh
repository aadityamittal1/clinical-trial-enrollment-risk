#!/usr/bin/env bash
# Destroy all Azure resources with OpenTofu. NOT TESTED on the build machine.
# Destroying the AKS cluster removes the workloads, so there is deliberately no
# `helm uninstall` here: it would act on whatever kube-context is current,
# possibly the local kind cluster.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

command -v tofu >/dev/null 2>&1 || { echo "missing required tool: tofu" >&2; exit 1; }

if [ ! -f deploy/tofu/terraform.tfvars ]; then
  echo "deploy/tofu/terraform.tfvars not found; destroy needs the same variables as cloud-up.sh" >&2
  exit 1
fi

# Destroy never uses the value, but the variable has no default and tofu
# insists on one in a fresh shell.
export TF_VAR_snowflake_password="${TF_VAR_snowflake_password:-destroy-only}"

tofu -chdir=deploy/tofu init -input=false
tofu -chdir=deploy/tofu destroy -input=false -auto-approve
