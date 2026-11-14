{#-
  surrogate_key(['col_a', 'col_b']): md5 hex digest of the '|'-joined, NULL-safe values.
  MD5 returns the same 32-char lowercase hex string on DuckDB and Snowflake, so a key
  computed in one warehouse equals the key computed in the other.
-#}
{% macro surrogate_key(columns) -%}
    {{ return(adapter.dispatch('surrogate_key', 'trial_risk')(columns)) }}
{%- endmacro %}

{#- One implementation for both warehouses: md5, cast(... as varchar), coalesce and || behave
    the same on DuckDB and Snowflake (MD5 returns a 32-character lowercase hex VARCHAR). -#}
{% macro default__surrogate_key(columns) -%}
    md5(
        {%- for c in columns %}
        coalesce(cast({{ c }} as varchar), '_null_'){% if not loop.last %} || '|' ||{% endif %}
        {%- endfor %}
    )
{%- endmacro %}
