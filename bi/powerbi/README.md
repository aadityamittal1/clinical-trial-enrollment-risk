# Power BI project (cloud mode, Snowflake)

`TrialRisk.pbip` is a Power BI Project (PBIP): a text-only report plus semantic model that diffs in Git. It reads
the Snowflake `MARTS` schema in **Import mode** (not DirectQuery, so a click never wakes the warehouse) and is meant
to be refreshed once after the daily pipeline run.

**Status: the layout has not been exercised.** Power BI Desktop was not available on the build machine, so
nothing here has been opened in Desktop or refreshed against Snowflake. The files were written from knowledge of
the PBIP, TMDL and PBIR formats and checked only statically (`tests/bi/test_bi_static.py`: JSON parses, every
table and column matches the dbt mart contract, every visual field resolves to a model column or measure). No
screenshots exist.

## Format choices

- **Report: PBIR** (`TrialRisk.Report/definition/` with `report.json`, `pages/<id>/page.json`,
  `pages/<id>/visuals/<id>/visual.json`), not the older single-file `report.json` layout. PBIR is the folder form
  built for Git diffs.
- **Semantic model: TMDL** (`TrialRisk.SemanticModel/definition/`): `model.tmdl`, `expressions.tmdl`,
  `relationships.tmdl`, `tables/<mart>.tmdl` (one per mart, 11 in all).
- Schema versions in the JSON (`report/3.0.0`, `page/2.0.0`, `visualContainer/2.0.0`, `definition.pbir` 4.0,
  `definition.pbism` 4.2) are from memory; adjust them to whatever your Desktop version writes.

## Open it

1. Windows only. Install Power BI Desktop and turn on Options > Preview features > **Power BI Project (.pbip)
   save option** and **Store reports using enhanced metadata format (PBIR)** and **Store semantic model using TMDL
   format**.
2. File > Open > `bi/powerbi/TrialRisk.pbip`.
3. Home > Transform data > Edit parameters, then set the three Snowflake parameters (no credentials are stored in
   the files; Desktop asks for them on the first refresh and keeps them in your Windows profile):

   | Parameter | Value |
   |---|---|
   | `SnowflakeServer` | account host, e.g. `xy12345.west-europe.azure.snowflakecomputing.com` |
   | `SnowflakeWarehouse` | virtual warehouse used for refresh |
   | `SnowflakeDatabase` | database containing the `MARTS` schema (the dbt cloud target's database) |

4. Refresh. The shared function `fnMart` selects `MARTS.<TABLE>` and lower-cases column names, because Snowflake
   returns upper case and the model uses the dbt column names.

## Pages

| Page | Visuals |
|---|---|
| Portfolio overview | trial-count card; slicers for condition category (`ml_features.condition_category`) and start year; trials by phase and status (stacked column); sponsor-class split (donut); filled map of site countries |
| Enrollment risk | high-risk and mean-score cards; sponsor-class slicer; ranked table of open trials with risk score and top 3 SHAP drivers, sorted by risk score descending; risk distribution by phase and by sponsor class (trial counts per 0.1 score band, calculated column `risk_band`) |
| Drug safety context | "Reports across top reactions" and OpenFDA match-rate cards; drug slicer; top reported reactions (bar); open trials per drug with a `ctgov_url` link column |
| Trial detail (hidden) | drill-through page: right-click a row in the ranked table > Drill through; shows the trial, its score and SHAP drivers, its drugs and their top reactions |

Measures: `Trial Count`, `Open Trial Count`, `Scored Trials`, `Mean Risk Score`, `High Risk Count`,
`High Risk Threshold` (0.5; change it only in `ml_scores_latest.tmdl`), `Site Count`,
`Reports Across Top Reactions` (sum of `report_count` over each drug's listed reactions, not total reports:
openFDA gives no report total), `OpenFDA Match Rate` (share of `dim_drug` rows with `has_fda_data`),
`Study Drug Links`.

## Changing the defaults

- **Page 2 default view** is all open trials sorted by risk score descending, with sponsor class as a slicer. The
  sort is the single `sortDefinition` in the `ranked` visual's `visual.json` (page `8293a937b37057daa4ae` is
  "Enrollment risk"; look for the folder whose `page.json` has that display name); to default to industry only,
  add an `INDUSTRY` filter on `dim_study.lead_sponsor_class` to that visual's `filterConfig`.
- `ml_scores_latest` is empty until `python -m ml score` has run, so the risk visuals are empty until then.

## What the first open will probably need fixed

- PBIR schema versions or property names rejected by Desktop (see above). If a visual fails to load, delete its
  folder and re-create it in Desktop; the model is the part that matters.
- The drill-through `pageBinding` and page `filterConfig` are the least certain parts of the PBIR JSON; set it up
  in the UI (drag `dim_study[nct_id]` to Drill through) and compare.
- TMDL: indentation of the multi-line `fnMart` expression, `ref expression` lines and the one-to-one
  `fromCardinality`/`toCardinality` syntax.
- Relationships: `ml_features` is one-to-one with `dim_study` (both directions) and the two bridge-to-dimension
  relationships on `bridge_study_drug` are both-directions so that the drug slicer filters studies; Desktop may
  flag ambiguity. The trial-per-drug table relies on the `Study Drug Links` measure to hide combinations with
  no bridge row.
- The filled map needs the tenant/Desktop setting that allows map visuals.
- Bounding the "top reactions" bar to 25 uses the data (25 per drug), not a Top N filter.
