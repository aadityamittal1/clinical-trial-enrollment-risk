# ADR 0002: DuckDB as the local warehouse, with adapter-dispatch macros

Status: accepted.

## Context

Cloud mode uses Snowflake, which has a 30-day trial and costs money while a warehouse runs. The repo has to stay
reproducible after the trial ends and run on a laptop. JSON and array handling differ between engines (Snowflake
`VARIANT`, `GET_PATH`, `LATERAL FLATTEN`, `REGEXP_INSTR`; DuckDB `json_extract`, `unnest`, `regexp_matches`).

## Decision

Use DuckDB through `dbt-duckdb` as the local warehouse (`MODE=local`). Keep model SQL free of dialect-specific syntax and
put every difference in dbt macros that use `adapter.dispatch` (`dbt/macros/`: JSON extraction, casts, hashing, regex,
study parsing, the score table). Each macro has a `duckdb__` and a `snowflake__` implementation. dbt targets `local` and
`cloud` are in `dbt/profiles.yml`.

## Consequences

- One set of models runs on both engines. A change to a model does not need two versions.
- Only the DuckDB branch has been executed (157 models and tests pass on the sample). The Snowflake branch has been
  parsed and compiled, and its SQL was syntax-checked once with `sqlglot` during development; no `sqlglot` check is
  kept in the repo. It has never been run against Snowflake. Regex semantics can differ between RE2 (DuckDB) and POSIX
  ERE (Snowflake); patterns are written in the common subset to avoid that, but this is untested on Snowflake.
- DuckDB allows one writer at a time, so the pipeline and a BI tool reading the file cannot both write.
