-- Latest openFDA load per drug, exploded to one row per drug x reaction term.
-- Drugs whose latest response has no results (HTTP 404 / no reports) produce no rows.
-- total_reports is always null in the openFDA count endpoint and is not modelled.
with ranked as (
    select
        drug_name,
        loaded_at,
        payload,
        row_number() over (partition by drug_name order by loaded_at desc) as load_rank
    from {{ source('raw', 'fda_event_counts') }}
    where drug_name is not null
),

latest as (
    select drug_name, loaded_at, payload
    from ranked
    where load_rank = 1
),

flattened as (
    {{ flatten_array('latest', 'payload', 'results') }}
)

select
    drug_name,
    {{ json_str('elem', 'term') }} as reaction,
    {{ json_int('elem', 'count') }} as report_count,
    {{ str_to_date("left(" ~ json_str('payload', 'fetched_at') ~ ", 10)") }} as fetched_date,
    loaded_at
from flattened
where {{ json_str('elem', 'term') }} is not null
