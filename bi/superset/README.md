# Superset dashboard (local mode, DuckDB)

One dashboard, **Trial Risk**, with three tabs: Portfolio overview, Enrollment risk, Drug safety context. It reads
the dbt marts (schema `marts`) in the local DuckDB warehouse. The Power BI report in `../powerbi/` is the cloud
twin.

**Status: the import has not been exercised.** Apache Superset was not available on the build machine, so this
bundle was written to the `superset export-dashboards` layout from knowledge of the format and checked only
statically (`tests/bi/test_bi_static.py`: YAML parses, every column exists in the mart contract, UUIDs between
files resolve). Dataset SQL was run against a DuckDB warehouse built from the sample snapshot (row counts and
column lists match the declared columns). No screenshots exist.

## Layout

`trial_risk_dashboard/` is the bundle root (what `export-dashboards` puts inside its zip):

```
metadata.yaml
databases/TrialRisk_DuckDB.yaml
datasets/TrialRisk_DuckDB/{ds_studies,ds_sites,ds_drug_safety,ds_trial_drugs,dim_drug}.yaml
charts/<key>_<n>.yaml                  15 charts
dashboards/Trial_Risk.yaml             three tabs, five native filters
```

Datasets are virtual (join SQL over `marts.*`) except `dim_drug`, which is a plain table. Saved metrics:
`trial_count`, `mean_risk_score`, `high_risk_count` (score >= 0.5), `site_count`, `report_volume`
("Reports across top reactions": the sum of `report_count` over a drug's listed reactions, not total reports,
because openFDA gives no report total), `match_rate` (share of `dim_drug` rows with `has_fda_data`).

## Import

1. Install Superset with the DuckDB driver: `pip install apache-superset duckdb-engine` (Superset 4.x assumed).
2. Make the warehouse file visible to Superset and edit the URI in `databases/TrialRisk_DuckDB.yaml`
   (`sqlalchemy_uri`, default `duckdb:////app/data/warehouse/trialrisk.duckdb?access_mode=read_only`) to the
   absolute path of `DUCKDB_PATH` (repo default `data/warehouse/trialrisk.duckdb`). `read_only` lets Superset
   and the pipeline coexist only while the pipeline is not writing; DuckDB allows one writer.
3. Zip the bundle folder so the zip contains one top-level directory, then import:

   ```
   cd bi/superset && zip -r trial_risk_dashboard.zip trial_risk_dashboard
   superset import-dashboards -p trial_risk_dashboard.zip -u admin
   ```

   (or Dashboards > Import in the UI). Open the dashboard at `/superset/dashboard/trial-risk/`.

## Changing the defaults

- **Page 2 default view** (all open trials, sorted by risk score descending, sponsor class as a filter): one line,
  `params.order_by_cols` (and `adhoc_filters`, `is_open AND risk_score IS NOT NULL`) in
  `charts/p2_ranked_*.yaml`. To restrict to industry sponsors by default, set the `Sponsor class` native filter's
  `defaultDataMask` in `dashboards/Trial_Risk.yaml`.
- **High-risk threshold**: `high_risk_count` in `datasets/.../ds_studies.yaml` and the subheader of
  `charts/p2_high_*.yaml`.
- `ml_scores_latest` is empty until `python -m ml score` has run, so the risk charts are empty until then.

## What the first import will probably need fixed

- `query_context` is `null` in every chart; Superset rebuilds it when a chart is opened and saved. If an import
  rejects null, remove the key.
- Native filter targets use `datasetUuid` (the exported form in recent 4.x); older versions want `datasetId`.
- `box_plot` field names (`columns` = distribute across, `groupby` = dimensions) and the `world_map` params may
  differ by version; the chart can be re-pointed in the UI and re-exported.
- The world map matches `country` by name. Country names that ClinicalTrials.gov spells differently from
  Superset's list will not colour.
- Link columns are HTML (`ctgov_link`); if the table shows raw `<a ...>` text, enable "Render columns in HTML
  format" on that column.
- Tab/row widths and heights are guesses; adjust in edit mode.
