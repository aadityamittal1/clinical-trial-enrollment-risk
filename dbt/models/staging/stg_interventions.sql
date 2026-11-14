-- One row per study x intervention (protocolSection.armsInterventionsModule.interventions[]).
with flattened as (
    {{ flatten_array(ref('stg_ctgov_latest'), 'study', 'protocolSection.armsInterventionsModule.interventions') }}
)

select
    nct_id,
    elem_index as intervention_index,
    {{ json_str('elem', 'type') }} as intervention_type,
    {{ json_str('elem', 'name') }} as intervention_name
from flattened
