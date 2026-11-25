{#-
  Fails (returns rows) when ml_features has a column whose name looks like a leakage
  field from the spec's "Leakage traps": enrollment count, completion date / duration,
  last update date, results flags, why_stopped, or the outcome itself (status, label).
  Column names are read from the built relation, so this runs after ml_features.
-#}
{%- set leak_pattern = 'enrol|complet|duration|last_update|update_post|results|why_stop|stopped|overall_status|outcome|label|terminat' -%}
{%- set leaked = [] -%}
{%- if execute -%}
    {%- for col in adapter.get_columns_in_relation(ref('ml_features')) -%}
        {%- if modules.re.search(leak_pattern, col.name | lower) -%}
            {%- do leaked.append(col.name | lower) -%}
        {%- endif -%}
    {%- endfor -%}
{%- endif -%}

{%- if leaked | length > 0 %}
{%- for name in leaked %}
select '{{ name }}' as leaked_column
{% if not loop.last %}union all{% endif %}
{%- endfor %}
{%- else %}
select cast(null as varchar) as leaked_column
from {{ ref('ml_features') }}
where 1 = 0
{%- endif %}
