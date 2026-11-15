# ADR 0003: Power BI Import mode over DirectQuery

Status: accepted.

## Context

The Power BI report reads the Snowflake marts. DirectQuery sends a query to Snowflake on each visual interaction, which
wakes the warehouse and spends credits. The data changes once a day.

## Decision

Every table in the semantic model (`bi/powerbi/TrialRisk.SemanticModel/`) uses Import mode. The report is refreshed once
after the daily pipeline run.

## Consequences

- Clicks in the report do not touch Snowflake. Warehouse cost is the refresh only.
- Data is as old as the last refresh. For a daily batch pipeline this is the same freshness the pipeline offers.
- The model has not been opened in Power BI Desktop or refreshed against Snowflake.
