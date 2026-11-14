{#-
  JSON / semi-structured access, isolated behind adapter dispatch.

  Paths are dotted key paths relative to the column, without a leading "$." and without
  array subscripts, e.g. 'protocolSection.statusModule.overallStatus'. An empty path ('')
  means the value itself (used on flattened array elements).

  DuckDB: column type JSON; json_extract / json_extract_string with '$.a.b' paths.
  Snowflake: column type VARIANT; GET_PATH(col, 'a.b'). STRIP_NULL_VALUE turns a JSON null
  into SQL NULL before casting. (Snowflake branches: static tier, never executed here.)
-#}

{% macro _duckdb_json_path(path) -%}
    '{{ "$" if path == "" else "$." ~ path }}'
{%- endmacro %}

{% macro _snowflake_variant(col, path) -%}
    {%- if path == "" -%}
        strip_null_value({{ col }})
    {%- else -%}
        strip_null_value(get_path({{ col }}, '{{ path }}'))
    {%- endif -%}
{%- endmacro %}


{#- ---- json_str: scalar as varchar --------------------------------------------------
  Use it on scalar values only (strings, numbers, booleans). On an OBJECT or ARRAY value
  the branches differ: DuckDB returns the JSON text, while the Snowflake `::varchar` cast
  raises an error ("Failed to cast variant value ... to VARCHAR"). Use json_array_join /
  json_array_length / flatten_array for arrays.
-#}
{% macro json_str(col, path) -%}
    {{ return(adapter.dispatch('json_str', 'trial_risk')(col, path)) }}
{%- endmacro %}

{% macro duckdb__json_str(col, path) -%}
    json_extract_string({{ col }}, {{ _duckdb_json_path(path) }})
{%- endmacro %}

{% macro snowflake__json_str(col, path) -%}
    {{ _snowflake_variant(col, path) }}::varchar
{%- endmacro %}


{# ---- json_int / json_float / json_bool: NULL when missing or not castable ------------ #}
{% macro json_int(col, path) -%}
    {{ return(adapter.dispatch('json_int', 'trial_risk')(col, path)) }}
{%- endmacro %}

{% macro duckdb__json_int(col, path) -%}
    try_cast(json_extract_string({{ col }}, {{ _duckdb_json_path(path) }}) as bigint)
{%- endmacro %}

{% macro snowflake__json_int(col, path) -%}
    try_cast({{ _snowflake_variant(col, path) }}::varchar as bigint)
{%- endmacro %}


{% macro json_float(col, path) -%}
    {{ return(adapter.dispatch('json_float', 'trial_risk')(col, path)) }}
{%- endmacro %}

{% macro duckdb__json_float(col, path) -%}
    try_cast(json_extract_string({{ col }}, {{ _duckdb_json_path(path) }}) as double)
{%- endmacro %}

{% macro snowflake__json_float(col, path) -%}
    try_cast({{ _snowflake_variant(col, path) }}::varchar as double)
{%- endmacro %}


{% macro json_bool(col, path) -%}
    {{ return(adapter.dispatch('json_bool', 'trial_risk')(col, path)) }}
{%- endmacro %}

{% macro duckdb__json_bool(col, path) -%}
    try_cast(json_extract_string({{ col }}, {{ _duckdb_json_path(path) }}) as boolean)
{%- endmacro %}

{% macro snowflake__json_bool(col, path) -%}
    try_cast({{ _snowflake_variant(col, path) }}::varchar as boolean)
{%- endmacro %}


{# ---- json_date: ClinicalTrials.gov dates are 'YYYY-MM' or 'YYYY-MM-DD' --------------- #}
{% macro json_date(col, path) -%}
    {{ str_to_date(json_str(col, path)) }}
{%- endmacro %}


{# ---- json_array_length: number of elements; NULL when the key is missing ------------ #}
{% macro json_array_length(col, path) -%}
    {{ return(adapter.dispatch('json_array_length', 'trial_risk')(col, path)) }}
{%- endmacro %}

{% macro duckdb__json_array_length(col, path) -%}
    json_array_length({{ col }}, {{ _duckdb_json_path(path) }})
{%- endmacro %}

{% macro snowflake__json_array_length(col, path) -%}
    array_size({{ _snowflake_variant(col, path) }})
{%- endmacro %}


{# ---- json_array_join: string array -> 'A<sep>B'; NULL or '' when missing or empty ---- #}
{% macro json_array_join(col, path, sep) -%}
    {{ return(adapter.dispatch('json_array_join', 'trial_risk')(col, path, sep)) }}
{%- endmacro %}

{% macro duckdb__json_array_join(col, path, sep) -%}
    array_to_string(json_extract_string({{ col }}, '$.{{ path }}[*]'), '{{ sep }}')
{%- endmacro %}

{% macro snowflake__json_array_join(col, path, sep) -%}
    array_to_string({{ _snowflake_variant(col, path) }}::array, '{{ sep }}')
{%- endmacro %}


{#-
  ---- flatten_array: one row per array element ---------------------------------------
  Renders a complete SELECT: every column of `relation`, plus
    elem        the element (DuckDB JSON / Snowflake VARIANT; read it with json_str(elem, ...))
    elem_index  0-based position in the array
  Rows whose array is missing, null or empty produce no output rows.
-#}
{% macro flatten_array(relation, json_col, path) -%}
    {{ return(adapter.dispatch('flatten_array', 'trial_risk')(relation, json_col, path)) }}
{%- endmacro %}

{% macro duckdb__flatten_array(relation, json_col, path) -%}
    select
        src.*,
        f.elem as elem,
        cast(f.elem_ordinal - 1 as integer) as elem_index
    from {{ relation }} as src
    cross join unnest(
        cast(json_extract(src.{{ json_col }}, '$.{{ path }}') as json[])
    ) with ordinality as f(elem, elem_ordinal)
{%- endmacro %}

{% macro snowflake__flatten_array(relation, json_col, path) -%}
    select
        src.*,
        f.value as elem,
        cast(f.index as integer) as elem_index
    from {{ relation }} as src,
        lateral flatten(input => get_path(src.{{ json_col }}, '{{ path }}')) as f
{%- endmacro %}
