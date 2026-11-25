{#-
  marts.ml_scores is owned by the scoring job (python -m ml score appends to it). dbt only
  reads it, through the `marts.ml_scores` source and the ml_scores_latest view. This
  on-run-start hook creates the table empty, with the scoring job's columns, when it does
  not exist yet, so ml_scores_latest builds on a fresh warehouse. It never alters or
  truncates an existing table.

  The DDL is plain SQL that DuckDB and Snowflake both accept (Snowflake maps double to
  FLOAT and timestamp to TIMESTAMP_NTZ).
-#}

{% macro ml_scores_columns() -%}
    {{ return([
        ('nct_id', 'varchar'),
        ('risk_score', 'double'),
        ('model_version', 'varchar'),
        ('scored_at', 'timestamp'),
        ('top_driver_1', 'varchar'),
        ('top_driver_2', 'varchar'),
        ('top_driver_3', 'varchar'),
        ('top_driver_1_shap', 'double'),
        ('top_driver_2_shap', 'double'),
        ('top_driver_3_shap', 'double'),
        ('shap_values', 'varchar')
    ]) }}
{%- endmacro %}

{#-
  Two hooks (dbt_project.yml on-run-start), one statement each: Snowflake runs one
  statement per call. They return SQL instead of calling run_query so that `dbt compile`
  and `dbt parse` never touch the warehouse.
-#}
{% macro ml_scores_relation() -%}
    {{ return(api.Relation.create(database=target.database, schema='marts', identifier='ml_scores')) }}
{%- endmacro %}

{% macro create_marts_schema_if_missing() -%}
    create schema if not exists {{ ml_scores_relation().without_identifier() }}
{%- endmacro %}

{% macro create_ml_scores_if_missing() -%}
    create table if not exists {{ ml_scores_relation() }} (
        {%- for name, type in ml_scores_columns() %}
        {{ name }} {{ type }}{% if not loop.last %},{% endif %}
        {%- endfor %}
    )
{%- endmacro %}
