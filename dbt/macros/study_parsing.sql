{#- Portable parsing helpers for study fields (built only from dispatched macros). -#}

{#-
  age_to_years(expr): ClinicalTrials.gov age strings ('18 Years', '6 Months', '1 Year',
  '28 Days', '2 Weeks', '12 Hours', '30 Minutes') -> age in years (double).
  NULL when missing or in an unknown unit.
-#}
{% macro age_to_years(expr) -%}
    {%- set amount = safe_cast("split_part(trim(" ~ expr ~ "), ' ', 1)", 'double') -%}
    {%- set unit = "lower(split_part(trim(" ~ expr ~ "), ' ', 2))" -%}
    case
        when {{ unit }} in ('year', 'years') then {{ amount }}
        when {{ unit }} in ('month', 'months') then {{ amount }} / 12.0
        when {{ unit }} in ('week', 'weeks') then {{ amount }} * 7.0 / 365.25
        when {{ unit }} in ('day', 'days') then {{ amount }} / 365.25
        when {{ unit }} in ('hour', 'hours') then {{ amount }} / (365.25 * 24.0)
        when {{ unit }} in ('minute', 'minutes') then {{ amount }} / (365.25 * 24.0 * 60.0)
    end
{%- endmacro %}


{#-
  count_criteria_items(expr, header): number of items in one eligibility-criteria section.
  - List items: lines that start (after optional blanks and one optional punctuation
    character, for escaped markdown such as '\-' or '1\.') with '*', '-', '•', or
    '<n>.' / '<n>)' followed by a blank. Nested sub-items are counted too.
  - Sections written as plain paragraphs (no list items): the number of non-blank lines,
    minus one when the section contains its header (`header`, e.g. 'exclusion criteria').
  NULL for NULL.
-#}
{% macro criteria_item_pattern() -%}
    {{ return('(^|\n)[[:blank:]]*[[:punct:]]?([*•-]|[0-9]+[[:punct:]]?[.)])[[:blank:]]') }}
{%- endmacro %}

{% macro nonblank_line_pattern() -%}
    {{ return('(^|\n)[[:blank:]]*[^[:space:]]') }}
{%- endmacro %}

{% macro count_criteria_items(expr, header) -%}
    case
        {#- explicit: DuckDB GREATEST skips NULLs, Snowflake GREATEST returns NULL #}
        when {{ expr }} is null then null
        when {{ regex_count(expr, criteria_item_pattern()) }} > 0
            then {{ regex_count(expr, criteria_item_pattern()) }}
        else greatest(
            {{ regex_count(expr, nonblank_line_pattern()) }}
            - case when position('{{ header }}' in lower({{ expr }})) > 0 then 1 else 0 end,
            0
        )
    end
{%- endmacro %}
