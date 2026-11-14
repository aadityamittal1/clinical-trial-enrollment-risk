{#-
  Use the configured schema name verbatim (staging, marts) instead of dbt's default
  "<target schema>_<custom schema>", so both warehouses get exactly raw / staging / marts.
-#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema | trim }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
