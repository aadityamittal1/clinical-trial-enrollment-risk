# ADR 0005: File storage backend for no-Docker runs

Status: accepted.

## Context

The design doc's local mode uses Azurite (an Azure Blob emulator) on a kind cluster. That needs Docker, kind, helm and
kubectl. The reviewer-friendly path is one that works on any machine with Python and `uv`, and the build machine had no
Docker.

## Decision

Add a second storage implementation behind the same interface in `common/storage.py`, selected by
`STORAGE_BACKEND=file` (the default): the landing zone is a directory (`LANDING_DIR`, default `data/landing`) with the same
layout as the blob container (`<source>/<YYYY-MM-DD>/part-<NNNNN>.jsonl.gz`, plus `_state/` for the watermark).
`STORAGE_BACKEND=azure` uses Azure Blob Storage or Azurite. `python -m pipeline run` chains the steps in-process.

## Consequences

- The full pipeline runs, and was verified, with no Docker and no Kubernetes.
- The Azure Blob path is covered only by tests with a mocked SDK. Azurite and real Blob Storage have not been run.
- The no-Docker path does not exercise Airflow, Helm or the container images, so it cannot catch problems in those.
