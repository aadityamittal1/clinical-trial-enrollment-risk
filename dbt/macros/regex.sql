{#-
  Regex helpers. Patterns are written in the common subset of RE2 (DuckDB) and POSIX ERE
  (Snowflake): no lookarounds, no non-capturing groups, no \b / \d / \s shortcuts (POSIX
  bracket classes such as [[:space:]] instead), at most 9 capture groups. Matching is
  case-sensitive; callers lower() their input.

  Dialect differences handled here:
  - string literals: Snowflake treats backslash as an escape character inside '...', so
    backslashes are doubled there (DuckDB string literals are standard SQL).
  - search: DuckDB regexp_matches is a partial (search) match, while Snowflake REGEXP_LIKE
    is anchored to the whole string, so REGEXP_INSTR(...) > 0 is used on Snowflake.
  - replace: DuckDB needs the 'g' flag; Snowflake REGEXP_REPLACE replaces every occurrence
    by default. Back-references are \1..\9 in both.
  - count: DuckDB has no REGEXP_COUNT; len(regexp_extract_all(...)) counts the matches.
  - Semantics: RE2 alternation is leftmost-first, POSIX is leftmost-longest. Patterns used
    in this project are written so that both give the same match (see drug_names.sql).
-#}

{% macro regex_literal(pattern) -%}
    {{ return(adapter.dispatch('regex_literal', 'trial_risk')(pattern)) }}
{%- endmacro %}

{% macro duckdb__regex_literal(pattern) -%}
    '{{ pattern | replace("'", "''") }}'
{%- endmacro %}

{% macro snowflake__regex_literal(pattern) -%}
    '{{ pattern | replace("\\", "\\\\") | replace("'", "''") }}'
{%- endmacro %}


{# regex_search: TRUE when `pattern` matches anywhere in `expr`; NULL when expr is NULL. #}
{% macro regex_search(expr, pattern) -%}
    {{ return(adapter.dispatch('regex_search', 'trial_risk')(expr, pattern)) }}
{%- endmacro %}

{% macro duckdb__regex_search(expr, pattern) -%}
    regexp_matches({{ expr }}, {{ regex_literal(pattern) }})
{%- endmacro %}

{% macro snowflake__regex_search(expr, pattern) -%}
    (regexp_instr({{ expr }}, {{ regex_literal(pattern) }}) > 0)
{%- endmacro %}


{# regex_replace: replace every non-overlapping match. #}
{% macro regex_replace(expr, pattern, replacement) -%}
    {{ return(adapter.dispatch('regex_replace', 'trial_risk')(expr, pattern, replacement)) }}
{%- endmacro %}

{% macro duckdb__regex_replace(expr, pattern, replacement) -%}
    regexp_replace({{ expr }}, {{ regex_literal(pattern) }}, {{ regex_literal(replacement) }}, 'g')
{%- endmacro %}

{% macro snowflake__regex_replace(expr, pattern, replacement) -%}
    regexp_replace({{ expr }}, {{ regex_literal(pattern) }}, {{ regex_literal(replacement) }})
{%- endmacro %}


{# regex_count: number of non-overlapping matches; NULL when expr is NULL. #}
{% macro regex_count(expr, pattern) -%}
    {{ return(adapter.dispatch('regex_count', 'trial_risk')(expr, pattern)) }}
{%- endmacro %}

{% macro duckdb__regex_count(expr, pattern) -%}
    len(regexp_extract_all({{ expr }}, {{ regex_literal(pattern) }}))
{%- endmacro %}

{% macro snowflake__regex_count(expr, pattern) -%}
    regexp_count({{ expr }}, {{ regex_literal(pattern) }})
{%- endmacro %}
