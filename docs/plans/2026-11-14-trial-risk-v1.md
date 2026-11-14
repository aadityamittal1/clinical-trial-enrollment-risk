# Trial Risk Platform v1 — Implementation Plan

**Spec:** `docs/design-doc.pdf` (Clinical Trial Enrollment Risk Platform — Design Doc, Nov 13 2026). The spec is the authority; this plan argues from it.

**Goal:** A monorepo that ingests ClinicalTrials.gov + openFDA, models the data in raw/staging/marts with dbt, trains and serves a low-enrollment-termination classifier, and ships dashboards — runnable end to end in local (open-source) mode, with the cloud (Azure/AKS/Snowflake/Power BI) mode fully defined in code.

## Build-machine reality (binding on every task)

- The build machine is Windows 11 on ARM64 with Python 3.12, `uv`, Node, git and `gh`. It has **no** Docker, kind, helm, kubectl, tofu, make, az, and no Azure/Snowflake/Power BI accounts.
- Therefore two tiers of "done":
  - **Executable tier** (must actually run and be tested here): ingestion, loader, dbt on DuckDB, ML train/evaluate/score, FastAPI serving, the in-process pipeline runner, all on the committed sample snapshot.
  - **Static tier** (written carefully, validated statically, clearly marked unverified): Dockerfiles, Helm charts, kind config, OpenTofu, Airflow DAG, Snowflake SQL and the Snowflake branch of every dbt macro, GitHub Actions, Superset YAML, Power BI PBIP. Static-tier files must parse (YAML/JSON/HCL syntax checks where a pure-Python or Node checker exists) and must never be described as tested.
- Reports must say exactly what was run and what was not.

## Global Constraints

- **Layout** (repo root = this directory): `common/`, `ingestion/`, `dags/`, `dbt/`, `ml/`, `serving/`, `pipeline/`, `deploy/{docker,helm,tofu,kind,snowflake}/`, `bi/{powerbi,superset}/`, `data/sample/`, `docs/`, `tests/`, `.github/workflows/`, `Makefile`, `README.md`, `LICENSE` (MIT), `pyproject.toml`.
- **Python:** 3.12, one root `pyproject.toml` managed by `uv`; packages `common`, `ingestion`, `ml`, `serving`, `pipeline` are importable from the repo root. Lint `ruff`, types `mypy`, tests `pytest` under `tests/<package>/`. Task 1 declares every dependency; later tasks do not edit `pyproject.toml` unless unavoidable, and then say so in the report.
- **Entry points (the contract every other component calls):**
  - `python -m ingestion ctgov [--full] [--max-pages N]` — pull studies to landing.
  - `python -m ingestion fda [--max-drugs N]` — pull openFDA reaction counts to landing.
  - `python -m ingestion load [--source landing|sample]` — load landing (or `data/sample/`) files into raw tables.
  - `python -m ingestion sample` — (re)build the committed snapshot in `data/sample/`.
  - `dbt build --project-dir dbt --profiles-dir dbt --target local|cloud`
  - `python -m ml train`, `python -m ml evaluate`, `python -m ml score`
  - `uvicorn serving.app:app` — `POST /score`, `GET /health`.
  - `python -m pipeline run [--source sample|live]` — chains everything in-process (the no-Kubernetes path).
- **Environment variables (read only through `common.config.get_settings()`):**
  `MODE` (`local`|`cloud`, default `local`); `STORAGE_BACKEND` (`file`|`azure`, default `file`); `LANDING_DIR` (default `data/landing`); `AZURE_STORAGE_CONNECTION_STRING`; `LANDING_CONTAINER` (default `landing`); `DUCKDB_PATH` (default `data/warehouse/trialrisk.duckdb`); `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_PASSWORD`, `SNOWFLAKE_ROLE`, `SNOWFLAKE_WAREHOUSE`, `SNOWFLAKE_DATABASE`; `MLFLOW_TRACKING_URI` (default `sqlite:///data/mlflow/mlflow.db`); `MODEL_NAME` (default `trial-risk-lgbm`); `OPENFDA_API_KEY` (optional).
  Warehouse = DuckDB when `MODE=local`, Snowflake when `MODE=cloud`. `STORAGE_BACKEND=azure` + Azurite connection string is the Kubernetes local mode; `file` is the no-Docker path.
- **Landing format:** gzipped JSON Lines at `<source>/<YYYY-MM-DD>/part-<NNNNN>.jsonl.gz`, sources `ctgov` and `fda`.
  - `ctgov` line = one study object exactly as the API returned it (never edited).
  - `fda` line = `{"drug_name": <canonical lowercase name>, "fetched_at": <ISO-8601 UTC>, "total_reports": <int|null>, "results": [{"term": <str>, "count": <int>}, ...]}`.
  - Incremental watermark lives in storage at `_state/ctgov_watermark.json`.
- **Raw tables** (schema `raw`, created by the loader, never edited afterwards):
  - `raw.ctgov_studies(nct_id varchar, last_update_posted date, loaded_at timestamp, study JSON|VARIANT)` — 1 row per study per load.
  - `raw.fda_event_counts(drug_name varchar, loaded_at timestamp, payload JSON|VARIANT)` — 1 row per drug per load.
- **Warehouse schemas:** `raw`, `staging`, `marts`. Mart column contract is in Task 3; ML, serving and BI rely on it verbatim.
- **Leakage rules (spec, "Leakage traps"):** `enrollmentInfo.count`, any completion date or duration derived from it, `lastUpdatePostDate`, results-posted flags and `whyStopped` are **never** model features. Site count is kept and documented as a known soft leak.
- **Secrets:** none in the repo, in Helm values, or in workflow files. `.env` is git-ignored; `.env.example` is committed.
- **Infrastructure as code is OpenTofu**, not Terraform.
- **Git:** work on branch `build/v1`. Implementers do **not** run `git add`/`git commit` (several run in parallel in one working tree); the controller commits each task's files. Implementers touch only the paths their task owns.
- **Honesty:** no fabricated metrics, screenshots or "verified" claims. Label precision from the 200-example hand check is reported as pending until a human does it.

## Out of scope for v1

Synthea, RxNorm normalization, the multi-class model, any real cloud deployment, the screen recording and screenshots, pushing to GitHub.

---

### Task 1: Foundation — scaffold and `common` package

**Owns:** `pyproject.toml`, `uv.lock`, `.gitignore`, `.env.example`, `LICENSE`, `Makefile`, `common/`, `tests/common/`, empty `__init__.py` for `ingestion`, `ml`, `serving`, `pipeline`, `tests/conftest.py`.

**Build:**
1. `pyproject.toml` with every runtime and dev dependency the project needs: `httpx`, `tenacity`, `azure-storage-blob`, `duckdb`, `pandas`, `pyarrow`, `dbt-core`, `dbt-duckdb`, `scikit-learn`, `lightgbm`, `shap`, `mlflow`, `matplotlib`, `fastapi`, `uvicorn`, `pydantic`, `pydantic-settings`, `pyyaml`; optional extra `cloud` = `snowflake-connector-python`, `dbt-snowflake`; dev group = `pytest`, `ruff`, `mypy`, `respx`, `pandas-stubs`, `types-PyYAML`. Configure ruff, mypy and pytest in the same file.
2. Create the environment with `uv sync` and prove every dependency imports. The machine is Windows ARM64: if a wheel (likely `lightgbm`, `duckdb`, `shap`, `pyarrow`) is missing for ARM64, use an x86-64 CPython through uv for the local venv (for example `uv python install cpython-3.12-windows-x86_64` and `UV_PYTHON`), without committing anything Windows-specific that would break Linux CI. Record the exact working commands in the report; they go into the README later.
3. `common/config.py`: `Settings` (pydantic-settings) with the environment variables in Global Constraints and their defaults, plus `get_settings()` (cached, with a way to reset in tests). Derived property `warehouse` = `duckdb` or `snowflake`.
4. `common/storage.py`: `Storage` protocol with `write_bytes(key, data)`, `read_bytes(key)`, `list_keys(prefix) -> list[str]` (sorted), `exists(key)`; `FileStorage(root)` and `AzureBlobStorage(connection_string, container)` (creates the container if missing); `get_storage(settings)`. Helpers `write_jsonl_gz(storage, key, records)` and `read_jsonl_gz(storage, key) -> Iterator[dict]`.
5. `common/warehouse.py`: `Warehouse` with `execute(sql, params=None)`, `query_df(sql) -> pandas.DataFrame`, `write_df(df, schema, table, mode="append"|"replace")`, `close()`; `DuckDBWarehouse(path)` (creates parent dirs and the schema on write) and `SnowflakeWarehouse(settings)` (connector imported lazily so the `cloud` extra stays optional); `get_warehouse(settings)`.
6. `Makefile` with targets `setup`, `lint`, `test`, `run` (= `python -m pipeline run --source sample`), `local-up`, `local-down`, `cloud-up`, `cloud-down`; the last four call scripts under `deploy/` that Task 6 writes (`deploy/local-up.sh`, `deploy/local-down.sh`, `deploy/cloud-up.sh`, `deploy/cloud-down.sh`).
7. `.gitignore` covering `.venv`, `data/landing/`, `data/warehouse/`, `data/mlflow/`, `mlruns/`, `dbt/target/`, `dbt/logs/`, `dbt/dbt_packages/`, `.env`, `.superpowers/`, caches. `data/sample/` must stay tracked.

**Tests (TDD):** settings defaults and env overrides; `FileStorage` round trip, sorted listing, jsonl.gz helpers; `DuckDBWarehouse` write/append/replace/query on a temp file. Azure and Snowflake classes: construction-level tests with the SDK mocked only.

**Done when:** `uv run pytest`, `uv run ruff check .`, `uv run mypy common` all pass with clean output.

---

### Task 2: Ingestion, loader and the sample snapshot

**Owns:** `ingestion/`, `tests/ingestion/`, `data/sample/`.

**Build:**
1. `ingestion/ctgov.py`: client for `https://clinicaltrials.gov/api/v2/studies`, interventional studies only, cursor pagination (`pageToken`, `pageSize` up to 1000), retries with exponential backoff on 429/5xx/timeouts (tenacity). Incremental: read the watermark, request only studies whose `LastUpdatePostDate` is after it, write the new watermark only after the whole pull succeeds. `--full` ignores the watermark. Each page becomes one `ctgov/<date>/part-NNNNN.jsonl.gz`. Check the exact filter syntax against the live API before coding it.
2. `ingestion/drug_names.py`: `canonical_drug_name(raw: str) -> str | None` — lowercase, strip dose (`10 mg`, `5 mg/kg`, `0.5%`), route and form words (oral, tablet, capsule, injection, iv, intravenous, subcutaneous, topical, solution, placebo → `None`), collapse whitespace. This is the Python twin of the dbt `stg_drug_names` logic in Task 3; keep the rule list in one data structure and document it in the module docstring so Task 3 can mirror it.
3. `ingestion/fda.py`: for the top N (default 800) canonical names of DRUG/BIOLOGICAL interventions by study count — read from `raw.ctgov_studies` when it exists, otherwise from the landing files — one request each to `https://api.fda.gov/drug/event.json` with `count=patient.reaction.reactionmeddrapt.exact` and a search on the drug name; 404 means "no reports" (write an empty `results`); respect 240 req/min and stop cleanly at the daily cap; send `OPENFDA_API_KEY` when set.
4. `ingestion/load.py`: create schema `raw` and the two raw tables if missing; load every landing file not already loaded (track loaded keys in `raw._loaded_files`) — DuckDB path reads through `Storage`; Snowflake path issues `COPY INTO` from the external stage `raw.landing_stage` (static tier). `--source sample` loads from `data/sample/` instead of landing.
5. `ingestion/sample.py` + `ingestion/__main__.py` (argparse subcommands `ctgov`, `fda`, `load`, `sample`).
6. **Build and keep the snapshot** by running `python -m ingestion sample` against the live APIs: about 3,000 interventional studies — roughly 2,000 COMPLETED or TERMINATED with start dates spread across 2008–2020 (make sure at least ~250 TERMINATED), about 800 RECRUITING or NOT_YET_RECRUITING, about 200 of other statuses (WITHDRAWN, SUSPENDED, ACTIVE_NOT_RECRUITING, UNKNOWN) — plus openFDA counts for the 60 most common drug names in that sample. Same landing format, under `data/sample/ctgov/<date>/` and `data/sample/fda/<date>/`. Keep the total under 20 MB; write `data/sample/README.md` with the build date, counts by status, and the public-domain note from the spec.

**Tests (TDD):** pagination and retry behaviour with `respx`; watermark only advances on success; `canonical_drug_name` table of cases; fda 404 handling; loader idempotency (second run loads nothing) on a temp DuckDB; a test that loads the committed sample and checks row counts match `data/sample/README.md`.

**Done when:** tests, ruff and mypy pass for `ingestion`; the snapshot is on disk; `python -m ingestion load --source sample` fills both raw tables in a fresh DuckDB.

---

### Task 3: dbt project — staging, marts, macros, tests

**Owns:** `dbt/`, `tests/dbt/`.

**Build:**
1. `dbt/dbt_project.yml`, `dbt/profiles.yml` with target `local` (dbt-duckdb, path from `DUCKDB_PATH`) and `cloud` (dbt-snowflake, all values from `SNOWFLAKE_*` env vars), sources for the raw tables with a freshness check on `loaded_at`, and `marts.ml_scores` declared as a source (the scoring job writes it, see Task 4).
2. `dbt/macros/`: every JSON/array difference isolated behind adapter dispatch — at minimum `json_str(col, path)`, `json_int`, `json_bool`, `json_date` (ClinicalTrials.gov dates can be `YYYY-MM` or `YYYY-MM-DD`), `flatten_array(relation, json_col, path)`, plus regex and hash helpers if the dialects differ. Each macro has a `duckdb__` and a `snowflake__` implementation (`VARIANT`/`LATERAL FLATTEN` vs DuckDB `json`/`unnest`). Models contain no dialect-specific SQL.
3. Staging (schema `staging`):
   - `stg_studies` — latest version per `nct_id` by `last_update_posted`. Columns: `nct_id, brief_title, overall_status, why_stopped, study_type, phase` (phases array joined with `/`, `NA` when absent)`, allocation, masking, primary_purpose, n_arms, enrollment_count, enrollment_type, start_date, start_date_type, completion_date, completion_date_type, lead_sponsor_name, lead_sponsor_class, n_collaborators, has_dmc, min_age_years, max_age_years, sex, healthy_volunteers, eligibility_criteria, has_results, first_posted_date, last_update_posted`.
   - `stg_conditions`, `stg_interventions` (with `intervention_type`), `stg_locations` (country, state, city, latitude, longitude), `stg_sponsors` (lead + collaborators with a role column) — 1 row per study × item.
   - `stg_drug_names` — 1 row per raw intervention name of type DRUG or BIOLOGICAL, with `canonical_name`, following the rules documented in `ingestion/drug_names.py` (if that file is not there yet, implement the rules listed in Task 2 item 2).
   - `stg_fda_event_counts` — latest load per drug, exploded to drug × reaction.
4. Marts (schema `marts`) — **this column contract is binding for ML, serving and BI:**
   - `dim_study`: `nct_id` (PK), `brief_title, phase, overall_status, is_open` (status RECRUITING or NOT_YET_RECRUITING)`, sponsor_key, lead_sponsor_class, start_date, start_year, completion_date, enrollment_count, enrollment_type, ctgov_url`.
   - `dim_sponsor`: `sponsor_key` (PK, hash of name), `sponsor_name, sponsor_class`.
   - `dim_condition`: `condition_key` (PK), `condition_name, condition_category`.
   - `dim_drug`: `drug_key` (PK), `canonical_name, n_studies, has_fda_data`.
   - `bridge_study_condition` (`nct_id, condition_key`), `bridge_study_drug` (`nct_id, drug_key`).
   - `fct_study_sites`: `site_key` (PK), `nct_id, country, state, city, latitude, longitude`.
   - `fct_trial_outcomes`: 1 row per study with status COMPLETED, TERMINATED or WITHDRAWN — `nct_id` (PK), `overall_status, why_stopped, outcome_class` (`completed` | `terminated_low_enrollment` | `terminated_other` | `withdrawn`), `label` (1 for `terminated_low_enrollment`, 0 for `completed`, null otherwise), `enrollment_count, enrollment_type`. The low-enrollment regex on `why_stopped` (accrual, recruit, enrol, insufficient/lack of participants/patients/subjects, and similar) lives in one macro so it can be iterated on.
   - `ml_features`: 1 row per study, registration-time only — `nct_id` (PK), `phase, allocation, masking, primary_purpose, n_arms, has_drug, has_biological, has_device, has_procedure, has_behavioral, has_other_intervention, sponsor_class, n_collaborators, has_dmc, min_age_years, max_age_years, sex, healthy_volunteers, n_inclusion_criteria, n_exclusion_criteria, criteria_text_length, n_sites, n_countries, us_only, condition_category, n_conditions, start_year`. Nothing listed under the Leakage rules may appear or be used to derive a column here.
   - `fct_drug_safety`: `drug_key, reaction, report_count, reaction_rank` (PK = drug_key + reaction), top 25 reactions per drug.
   - `ml_scores_latest`: view over the `ml_scores` source keeping the most recent `scored_at` per `nct_id`; it must build (empty) when `ml_scores` has no rows — the model creates the empty source table in an `on-run-start` hook if it does not exist, with the Task 4 columns.
   - `condition_category`: the top-level category from the record's MeSH browse data in `derivedSection.conditionBrowseModule`; inspect real sample records to pick the right field, fall back to `Unknown`.
5. Tests: `unique` + `not_null` on every primary key, `relationships` between facts/bridges and dimensions, `accepted_values` on `overall_status`, `phase`, `outcome_class` and `sponsor_class`, source freshness. A singular test that fails if `ml_features` has any column named like a leakage field.
6. `tests/dbt/test_dbt_build.py`: pytest that loads the sample into a temp DuckDB (via `python -m ingestion load --source sample`) and runs `dbt build --target local`, asserting success and non-empty marts.

**Done when:** `dbt build --target local` passes every model and test on the sample; `dbt parse --target cloud` (or `dbt compile` with dummy env vars, if it can run without a connection) is attempted and its result reported honestly; the Snowflake macro branches are reviewed by eye against Snowflake syntax and marked static tier.

---

### Task 4: ML — training, evaluation, batch scoring

**Owns:** `ml/`, `tests/ml/`.

**Build:**
1. `ml/features.py`: load `marts.ml_features` joined to `marts.fct_trial_outcomes` through `common.warehouse`; the single list of feature columns with their types (categorical vs numeric) used by both training and scoring; a guard that raises if a leakage-named column is requested.
2. `ml/split.py`: time split by `start_year` — train ≤ 2016, validate 2017–2018, test 2019–2020, later years dropped; boundaries are parameters.
3. `ml/train.py`: baseline logistic regression (one-hot + scaled numerics) and LightGBM, both as scikit-learn pipelines; log parameters, PR-AUC (validation and test), the no-skill PR-AUC (positive rate), and a calibration plot PNG to MLflow; register the LightGBM pipeline under `MODEL_NAME`. Seeds fixed. If test PR-AUC > 0.95, log a loud leakage warning and fail unless `--allow-suspicious` is passed.
4. `ml/evaluate.py`: reload the registered model, recompute metrics on the test split, write `docs/model_card.md` inputs (metrics JSON + calibration PNG under `docs/img/`).
5. `ml/predict.py`: `load_model(settings)` and `score_frame(model, features_df) -> DataFrame` returning `nct_id, risk_score` (in [0,1]), `top_driver_1..3` (feature names), `top_driver_1..3_shap`, `shap_values` (JSON string of per-feature SHAP for the positive class). This function is the one place scoring happens; serving imports it.
6. `ml/score.py`: score every open study (`dim_study.is_open`) and append to `marts.ml_scores` with columns `nct_id, risk_score, model_version, scored_at, top_driver_1, top_driver_2, top_driver_3, top_driver_1_shap, top_driver_2_shap, top_driver_3_shap, shap_values`.
7. `ml/labels.py` + CLI `python -m ml label-sample`: export 200 random regex matches and non-matches among TERMINATED trials to `docs/label_check/sample.csv` with an empty `human_label` column, and a function that computes precision from a filled-in file. Do not fill it in.
8. `ml/__main__.py` with subcommands `train`, `evaluate`, `score`, `label-sample`.

**Tests (TDD):** split boundaries; leakage guard; `score_frame` output schema and range on a small fitted model; train → register → load → score round trip on the sample warehouse in a temp dir; smoke assertion from the spec: scores in [0,1] and LightGBM test PR-AUC above the no-skill rate on the sample (if the sample is too small for a stable result, widen the test years via parameters and report it — do not weaken the assertion silently).

**Done when:** tests, ruff, mypy pass for `ml`; `python -m ml train && python -m ml score` works against a warehouse built from the sample, and the report states the real metrics.

---

### Task 5: Serving API and the in-process pipeline runner

**Owns:** `serving/`, `pipeline/`, `tests/serving/`, `tests/pipeline/`.

**Build:**
1. `serving/app.py`: FastAPI; model loaded once at startup via `ml.predict.load_model`. `GET /health` → status, model name and version. `POST /score` accepts either `{"nct_ids": [...]}` (features looked up in `marts.ml_features`; unknown ids reported in a `not_found` list) or `{"features": [{...}]}` (raw feature rows); returns per study `nct_id, risk_score, top_drivers` (name + shap). `POST /score/batch` triggers the same code path as `python -m ml score` and returns the row count written. Pydantic request/response models; 503 when no model is registered.
2. `pipeline/run.py` + `pipeline/__main__.py`: `python -m pipeline run --source sample|live [--skip-train]` runs, in order, ingest (live only) → load → `dbt build` (subprocess) → train (unless skipped or a model already exists and `--skip-train`) → score → a second `dbt build --select ml_scores_latest+` style refresh; stops on first failure with a clear message and a non-zero exit; prints a short summary (row counts per layer, model version, number scored).

**Tests:** FastAPI `TestClient` for health, both `/score` shapes, not-found handling, 503 without a model; one end-to-end test that runs `python -m pipeline run --source sample` in a temp working area and asserts `ml_scores_latest` is non-empty.

**Done when:** tests, ruff, mypy pass; the end-to-end run on the sample succeeds from a clean state and its summary is in the report.

---

### Task 6: Deploy — Docker, Helm, kind, OpenTofu, Snowflake setup (static tier)

**Owns:** `deploy/`, `tests/deploy/`.

**Build:**
1. `deploy/docker/pipeline.Dockerfile` (ingestion + dbt + ml + pipeline, entrypoint runs any Global Constraints command) and `deploy/docker/serving.Dockerfile` (uvicorn), both built with uv from the root `pyproject.toml`, non-root user, `.dockerignore` at the repo root.
2. `deploy/helm/trial-risk/`: an umbrella chart with templates for the scoring API (Deployment + Service + probes on `/health`), MLflow tracking server (Deployment + Service, artifacts in blob storage), Azurite (local only, behind a values flag), a ConfigMap with the Global Constraints env vars, a Secret template fed from values that are never committed, and RBAC that lets Airflow launch pods. `values-local.yaml` (kind: Azurite on, DuckDB on a PersistentVolumeClaim shared by the pipeline pods, `MODE=local`, `STORAGE_BACKEND=azure`) and `values-aks.yaml` (`MODE=cloud`, Key Vault through the Secrets Store CSI driver, images from GHCR). Airflow itself is installed from the official `apache-airflow` chart with `deploy/helm/airflow-values-local.yaml` and `airflow-values-aks.yaml` (DAGs baked into or git-synced from this repo — pick one and document it).
3. `deploy/kind/cluster.yaml`; `deploy/local-up.sh`, `deploy/local-down.sh` (kind create, build + load images, create the Secret from `.env`, helm install), `deploy/cloud-up.sh`, `deploy/cloud-down.sh` (tofu apply / destroy, get AKS credentials, helm upgrade).
4. `deploy/tofu/`: resource group, storage account + `landing` and `mlflow` containers, Key Vault with the Snowflake and storage secrets as variables (no values committed), AKS on the free control-plane tier with one small node pool and the Key Vault CSI add-on, a $25 budget with alerts at 50/80/100 %, `terraform.tfvars.example`, outputs the scripts need. Region East US 2.
5. `deploy/snowflake/setup.sql`: database, schemas `raw`/`staging`/`marts`, one X-Small warehouse with `AUTO_SUSPEND = 60`, a resource monitor that suspends at 75 % of quota, role and grants, storage integration + external stage `raw.landing_stage` on the landing container, JSON file format.
6. `tests/deploy/test_static.py`: every YAML under `deploy/` outside Helm `templates/` parses; `Chart.yaml` and values files have the keys the templates reference (a simple scan for `.Values.x.y` paths is enough); no file under `deploy/` contains anything that looks like a secret value.

**Done when:** the static tests pass, and the report lists which tools (helm lint, tofu validate, docker build) could not be run here.

---

### Task 7: Airflow DAG and GitHub Actions (static tier)

**Owns:** `dags/`, `.github/workflows/`, `tests/dags/`.

**Build:**
1. `dags/trial_risk_daily.py`: daily DAG `ingest_ctgov → ingest_fda → load_raw → dbt_build → score → refresh_marts`, each a `KubernetesPodOperator` running the pipeline image with the Global Constraints commands (scoring calls `python -m ml score`), retries with backoff, `catchup=False`, env from the chart's ConfigMap and Secret, image and namespace from Airflow Variables or env with defaults. A separate manually triggered DAG `trial_risk_train` for `python -m ml train`.
2. `.github/workflows/ci.yml` (every pull request): ruff, mypy, pytest; build both Docker images; create a kind cluster, `deploy/local-up.sh`, run the pipeline on the sample snapshot, dbt tests, and the model smoke test (scores in [0,1], PR-AUC above baseline). `.github/workflows/cd.yml` (push to `main`): push images to GHCR, then `helm upgrade` on AKS only when a repository variable `CLOUD_MODE_UP` is `true`; credentials only from GitHub secrets.
3. `tests/dags/test_dag_static.py`: parse the DAG files with `ast` and assert task ids, order and commands without importing Airflow (Airflow is not installed here and does not support Windows); YAML-parse both workflows and assert the required jobs exist.

**Done when:** static tests pass and the report says plainly that neither the DAG nor the workflows have been executed.

---

### Task 8: BI — Superset definitions and Power BI project (static tier)

**Owns:** `bi/`, `tests/bi/`.

**Build:**
1. `bi/superset/`: Superset export-format YAML (database, datasets on the marts, charts, one dashboard with three tabs) for the three pages in the spec — Portfolio overview (trial counts by phase and status, sponsor-class split, site map, filters for condition category and start year), Enrollment risk (ranked open trials with risk score and top 3 SHAP drivers, risk distribution by phase and sponsor class, per-trial detail), Drug safety context (top reactions per drug, report volume, link to each trial's ClinicalTrials.gov page, and the openFDA match rate). Plus `bi/superset/README.md` on importing it and pointing it at the DuckDB file.
2. `bi/powerbi/TrialRisk.pbip` with `TrialRisk.SemanticModel/` in TMDL (tables for every mart in the Task 3 contract, Snowflake connection through parameters, Import mode, relationships, measures such as trial count, mean risk score, high-risk count, match rate) and `TrialRisk.Report/` with the three pages laid out to match the spec's visuals. `bi/powerbi/README.md` explains opening it in Power BI Desktop on Windows, setting the Snowflake parameters, and that the layout has not been opened in Desktop yet.
3. `tests/bi/test_bi_static.py`: all YAML/JSON parses; every column referenced by a Superset dataset or TMDL table exists in the Task 3 mart contract (read the real columns from `dbt/models/marts/*.yml`).

**Done when:** static tests pass; the report states that neither Superset import nor Power BI Desktop was exercised.

---

### Task 9: README, ADRs, model card

**Owns:** `README.md`, `docs/` (except `docs/plans/`, `docs/design-doc.pdf`, and files Task 4 generated).

**Build:** README (what and why, architecture diagram in Mermaid, the two modes, quick start for the no-Docker path with the exact commands that were verified on this machine, the Kubernetes local path and cloud path marked as not yet run, results from the real sample run, leakage section, label-check status = pending, cost guardrails, repo map); `docs/adr/` with short ADRs for: OpenTofu over Terraform, DuckDB as local warehouse with adapter-dispatch macros, Import mode over DirectQuery, time-based split, file storage backend for no-Docker runs; `docs/model_card.md` from Task 4's real metrics. Every number must come from an artifact in the repo; every unverified component is labelled as such.

**Done when:** every command in the quick start has been run successfully as written, and links and paths resolve.
