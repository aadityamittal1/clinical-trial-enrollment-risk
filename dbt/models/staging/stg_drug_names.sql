-- One row per distinct raw intervention name of type DRUG or BIOLOGICAL, with the
-- canonical name used to join openFDA (rules: ingestion/drug_names.py, SQL twin:
-- macros/drug_names.sql). canonical_name is NULL for placebos and names that are empty
-- once doses and route / form words are stripped.
with drug_interventions as (
    select distinct intervention_name as raw_name
    from {{ ref('stg_interventions') }}
    where intervention_type in ('DRUG', 'BIOLOGICAL')
      and intervention_name is not null
)

select
    raw_name,
    {{ canonical_drug_name('raw_name') }} as canonical_name
from drug_interventions
