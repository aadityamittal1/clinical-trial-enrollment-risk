-- One row per study x condition (protocolSection.conditionsModule.conditions[], free text).
with flattened as (
    {{ flatten_array(ref('stg_ctgov_latest'), 'study', 'protocolSection.conditionsModule.conditions') }}
)

select
    nct_id,
    elem_index as condition_index,
    trim({{ json_str('elem', '') }}) as condition_name
from flattened
where nullif(trim({{ json_str('elem', '') }}), '') is not null
