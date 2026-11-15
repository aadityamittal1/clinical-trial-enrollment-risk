# ADR 0001: OpenTofu over Terraform

Status: accepted.

## Context

The cloud infrastructure (resource group, AKS, storage, Key Vault, budget) is defined as code so the whole stack can be
created and destroyed with one command. Terraform moved to the non-open-source BSL license in 2023. OpenTofu is the
open-source fork and runs the same `.tf` files. The project's premise is that everything except the paid cloud services
is open source.

## Decision

Write the infrastructure in HCL under `deploy/tofu/` and run it with OpenTofu (`tofu init`, `tofu apply`, `tofu destroy`).
Providers are `azurerm` (~> 4.0), plus `hashicorp/time` for a short wait before Key Vault secret writes.

## Consequences

- No dependency on a BSL-licensed tool. The files still work with Terraform if a reader prefers it, apart from the
  `tofu` command name in `deploy/cloud-up.sh` and `deploy/cloud-down.sh`.
- Some tutorials and modules are written for Terraform; they usually work unchanged but are not guaranteed to.
- Nothing in `deploy/tofu/` has been run. `tofu validate` has not been executed, so azurerm 4.x attribute names are
  unconfirmed.
