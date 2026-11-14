{#- Casts that return NULL instead of failing, and the partial-date parser. -#}

{# safe_cast: NULL when `expr` (a string expression) cannot be cast to `type`. #}
{% macro safe_cast(expr, type) -%}
    {{ return(adapter.dispatch('safe_cast', 'trial_risk')(expr, type)) }}
{%- endmacro %}

{% macro duckdb__safe_cast(expr, type) -%}
    try_cast({{ expr }} as {{ type }})
{%- endmacro %}

{# Snowflake TRY_CAST only accepts string input, hence the explicit varchar cast. #}
{% macro snowflake__safe_cast(expr, type) -%}
    try_cast(cast({{ expr }} as varchar) as {{ type }})
{%- endmacro %}


{#-
  str_to_date: 'YYYY-MM-DD' -> that date, 'YYYY-MM' -> first day of the month,
  anything else (NULL, '', malformed) -> NULL.
-#}
{% macro str_to_date(expr) -%}
    {{ return(adapter.dispatch('str_to_date', 'trial_risk')(expr)) }}
{%- endmacro %}

{% macro duckdb__str_to_date(expr) -%}
    try_cast(
        case when length({{ expr }}) = 7 then {{ expr }} || '-01' else {{ expr }} end
        as date
    )
{%- endmacro %}

{% macro snowflake__str_to_date(expr) -%}
    try_to_date(
        case when length({{ expr }}) = 7 then {{ expr }} || '-01' else {{ expr }} end,
        'YYYY-MM-DD'
    )
{%- endmacro %}
