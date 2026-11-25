-- Study x condition bridge (distinct pairs).
select distinct
    nct_id,
    {{ surrogate_key(['lower(condition_name)']) }} as condition_key
from {{ ref('stg_conditions') }}
