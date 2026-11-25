-- One row per study x site, for the maps. Current site list (sites can change after
-- registration).
select
    {{ surrogate_key(['nct_id', 'location_index']) }} as site_key,
    nct_id,
    country,
    state,
    city,
    latitude,
    longitude
from {{ ref('stg_locations') }}
