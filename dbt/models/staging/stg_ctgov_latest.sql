-- Latest raw version of each study: highest last_update_posted, ties broken by the most
-- recent load. Every other ctgov staging model reads the JSON from here.
with ranked as (
    select
        nct_id,
        last_update_posted,
        loaded_at,
        study,
        row_number() over (
            partition by nct_id
            order by last_update_posted desc nulls last, loaded_at desc
        ) as version_rank
    from {{ source('raw', 'ctgov_studies') }}
    where nct_id is not null
)

select
    nct_id,
    last_update_posted,
    loaded_at,
    study
from ranked
where version_rank = 1
