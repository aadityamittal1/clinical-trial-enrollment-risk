-- One row per study x site (protocolSection.contactsLocationsModule.locations[]).
-- The site list is the current one, not the one at registration (documented soft leak).
with flattened as (
    {{ flatten_array(ref('stg_ctgov_latest'), 'study', 'protocolSection.contactsLocationsModule.locations') }}
)

select
    nct_id,
    elem_index as location_index,
    {{ json_str('elem', 'facility') }} as facility,
    {{ json_str('elem', 'city') }} as city,
    {{ json_str('elem', 'state') }} as state,
    {{ json_str('elem', 'country') }} as country,
    {{ json_float('elem', 'geoPoint.lat') }} as latitude,
    {{ json_float('elem', 'geoPoint.lon') }} as longitude
from flattened
