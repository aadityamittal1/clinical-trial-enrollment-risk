# Agent guide: Clinical Trial Enrollment Risk Platform

This file tells coding agents how to work in this repo. Humans should start with `README.md`.

The project predicts which clinical trials are likely to be terminated for low enrollment, using only free public
U.S. government APIs (ClinicalTrials.gov v2, openFDA). It is an analytics portfolio project, not decision support
for real trials. Never add patient-level data or wording that makes clinical or regulatory claims.

## Read first

| Need | File |
|---|---|
| What exists and what has actually been run | `README.md` (see "Verify on first real run") |
| Why it is built this way | `docs/design-doc.pdf`, `docs/adr/0001`–`0006` |
| Original build plan and task boundaries | `docs/plans/2026-11-14-trial-risk-v1.md` |
| Model behaviour and limits | `docs/model_card.md`, `docs/model_metrics.json` |
| Mart column contract | `dbt/models/marts/_marts.yml` |

## Commands

Run everything from the repo root through `uv`. `make` is not installed on the owner's Windows machine, so use the
underlying commands.

| Purpose | Command |
|---|---|
| Install | `uv sync --all-extras` |
| Lint | `uv run ruff check .` and `uv run ruff format --check .` |
| Types | `uv run mypy common ingestion ml serving pipeline` |
| All tests | `uv run pytest -q` |
| One area | `uv run pytest tests/<area> -q` |
| End-to-end on the committed sample (under a minute) | `uv run python -m pipeline run --source sample` |
| Same, with a retrain | `uv run python -m pipeline run --source sample --retrain` |
| Evaluate the registered model | `uv run python -m ml evaluate` |
| Serve the API | `uv run uvicorn serving.app:app --port 8000` |

pytest treats warnings as errors (`filterwarnings = ["error"]`). Test output must stay clean.

## Map

| Folder | Role | Depends on |
|---|---|---|
| `common/` | Settings, landing storage, warehouse (DuckDB or Snowflake) | nothing |
| `ingestion/` | API clients, landing files, raw-table loader, sample builder | `common` |
| `dbt/` | Staging, intermediate and mart models; dispatch macros per warehouse | raw tables |
| `ml/` | Features, time split, train, evaluate, score | marts, `common` |
| `serving/` | FastAPI scoring service | `ml`, `common` |
| `pipeline/` | One-command runner that chains the steps | all of the above |
| `dags/` | Airflow DAGs that run the same steps as Kubernetes pods | images, chart |
| `deploy/` | Dockerfiles, Helm chart, kind, OpenTofu, Snowflake setup | none at import time |
| `bi/` | Superset export bundle, Power BI project | marts |
| `tests/` | Mirrors the folders above | — |

`MODE=local|cloud` picks the warehouse and dbt target. `STORAGE_BACKEND=file|azure` picks the landing zone. Both are
read only through `common/config.py`; do not read environment variables anywhere else.

## Two tiers of trust

- **Executed tier:** `common`, `ingestion`, `dbt` on DuckDB, `ml`, `serving`, `pipeline`. Changes here must be proven
  by running tests and, for anything touching data flow, the sample pipeline.
- **Static tier:** Docker, kind, Helm, Airflow, OpenTofu, Azure, the Snowflake dbt target and Snowflake/Azure Python
  paths, GitHub Actions, Superset, Power BI. None of it has ever run. The tests for it are syntax and contract
  checks. Never report a static-tier change as "working"; say "written, not run".

Docker, kind, helm, kubectl, tofu and az are not installed on the owner's machine. Do not try to install them or
create cloud accounts; ask the owner.

## Invariants that must not break

1. **No label leakage.** Features describe a trial as registered, not how it ended. `ml/features.py` has a leakage
   guard and a dbt lineage test fails if any model upstream of `ml_features`, other than `stg_studies`, reads a
   forbidden field. Do not weaken either to get a test green.
2. **Time split is fixed:** train start year ≤2016, validation 2017–18, test 2019–20 (ADR 0004). Never tune on test.
3. **Suspicious-score guard:** training fails above PR-AUC 0.95 unless `--allow-suspicious`. A score that high means
   leakage until proven otherwise. Do not pass the flag to make a run succeed.
4. **Metrics are generated, not typed.** `docs/model_metrics.json` and `docs/img/calibration.png` come from
   `ml evaluate`. If you change labels, features or the split, regenerate them and update the README and model card
   numbers in the same change.
5. **Mart contract.** Column names and types in `_marts.yml` are consumed by `ml`, `serving` and `bi`. A rename needs
   all four updated together.
6. **Both warehouses.** New SQL goes through the dispatch macros in `dbt/macros/` with a DuckDB and a Snowflake
   branch. Only the DuckDB branch can be run here; mark the Snowflake branch as unverified.
7. **DAG and chart share eight settings** (`TRIAL_RISK_MODE`, `_NAMESPACE`, `_IMAGE`, `_IMAGE_PULL_POLICY`,
   `_CONFIGMAP`, `_SECRET`, `_DUCKDB_PVC`, `_DUCKDB_MOUNT`). Change `dags/` and `deploy/helm/` together.
8. **`ml_scores` is written by the scoring job, not dbt.** dbt reads it as a source and exposes `ml_scores_latest`.
9. **Shell scripts are LF and executable** (`.gitattributes`, mode 100755). Keep that when editing on Windows.
10. **uv is pinned to 0.11.19** in the Dockerfiles and checked by a test.

## Secrets and data

- `.env` holds the owner's openFDA key and is git-ignored. Never print, log, commit or paste its values. Check a key
  is loaded by length only.
- A test scans for committed secrets. Do not add exceptions to it.
- The sample in `data/sample/` is public registry data and includes investigator contact details as published. Do not
  extract, list or repurpose those fields; no model or mart needs them.
- Be polite to the APIs: keep the existing rate limiting, the openFDA 429 stop, and the ClinicalTrials.gov watermark.
  A full live pull (about 500 pages plus 800 drugs) needs the owner's go-ahead.
- Do not put the owner's name, email or project details into requests to outside services.

## Git rules

- Work on a branch, never directly on `main`. Current integration branch: `build/v1`.
- Do not push, merge, open a pull request or publish anything without the owner's explicit yes each time.
- No attribution lines in commit messages or pull request descriptions.
- Do not commit `data/landing/`, `data/warehouse/`, `mlruns/`, dbt `target/` or `logs/`, or `.superpowers/`.

## How to split work across models

The orchestrating session plans, splits and reviews; it does not write the code itself. No subagent runs above Opus.

| Work | Model |
|---|---|
| Breaking down a request, rulings on ambiguity, deciding task order | Orchestrator (Fable) |
| dbt models and macros, anything in `ml/`, label rule changes, deploy chart logic | Opus |
| Well-specified changes in `common`, `ingestion`, `serving`, `pipeline`, tests, docs, BI files | Sonnet |
| Single-file mechanical fixes, lookups, fact checks against the repo | Haiku or Sonnet |
| Task review | Sonnet by default; Opus for dbt, ML and deploy diffs |
| Whole-branch review before merge | Opus |

Rules for the orchestrator:

1. One task owns one folder. Parallel agents are safe only on disjoint folders; anything touching `common/` or
   `_marts.yml` runs alone because everything depends on it.
2. Order follows the data: `common` → `ingestion` → `dbt` → `ml` → `serving`/`pipeline` → `dags`/`deploy`/`bi` → docs.
3. Give each agent a brief with exact file paths, the invariants above that apply, and the commands to prove the work.
   Do not paste session history.
4. Every task gets an independent review of its diff before it counts as done. Implementers do not review themselves
   and do not spawn their own subagents.
5. Implementers do not commit when several run in parallel in one working tree; the orchestrator commits per task.
6. Docs are written last and fact-checked against the repo, because they quote numbers.

## Definition of done

A change is done when all of these are true, and the report says which were actually run:

- `uv run ruff check .`, `uv run ruff format --check .` and `uv run mypy common ingestion ml serving pipeline` are clean.
- `uv run pytest -q` passes with no warnings.
- For changes to ingestion, dbt, ML, serving or the runner: `uv run python -m pipeline run --source sample` exits 0.
- For changes that move model numbers: metrics regenerated and docs updated.
- Static-tier changes are labelled "not run" and, if they add a new first-run risk, added to the README checklist.

Report failures with the output. Do not describe a skipped step as passed.

## Known open items

Check these before starting related work so you do not rediscover them:

- Label precision is unmeasured; `docs/label_check/sample.csv` awaits the owner's hand labels. Known false-positive
  shapes include "Enrollment stopped due to safety issue" and "Recruitment halted under COVID restrictions".
- Scores read low on recent cohorts; no recalibration in v1 (ADR 0006).
- LightGBM overfits the training years and does not beat logistic regression on the sample test set.
- Snowflake key-pair sign-in is documented but not wired.
- `MODE=cloud` with `STORAGE_BACKEND=file` on a non-sample load is unsupported and fails silently.
- `cd.yml` uses `helm upgrade --reuse-values`; switch to `--reset-then-reuse-values` before the first chart change.
- No cross-run openFDA request counter.
- `POST /score/batch` has no authentication; do not expose the service publicly as it is.
