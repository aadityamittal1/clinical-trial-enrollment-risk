-- One row per study x canonical drug: DRUG / BIOLOGICAL interventions whose raw name
-- normalises to a non-NULL canonical name. Feeds dim_drug and bridge_study_drug.
select distinct
    i.nct_id,
    d.canonical_name,
    {{ surrogate_key(['d.canonical_name']) }} as drug_key
from {{ ref('stg_interventions') }} as i
inner join {{ ref('stg_drug_names') }} as d
    on i.intervention_name = d.raw_name
where i.intervention_type in ('DRUG', 'BIOLOGICAL')
  and d.canonical_name is not null
