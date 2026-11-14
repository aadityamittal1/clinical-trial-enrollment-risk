-- Study x drug bridge (distinct pairs; DRUG / BIOLOGICAL interventions with a canonical name).
select distinct
    i.nct_id,
    {{ surrogate_key(['d.canonical_name']) }} as drug_key
from {{ ref('stg_interventions') }} as i
inner join {{ ref('stg_drug_names') }} as d
    on i.intervention_name = d.raw_name
where i.intervention_type in ('DRUG', 'BIOLOGICAL')
  and d.canonical_name is not null
