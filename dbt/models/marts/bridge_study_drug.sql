-- Study x drug bridge (distinct pairs; DRUG / BIOLOGICAL interventions with a canonical name).
select
    nct_id,
    drug_key
from {{ ref('int_study_drugs') }}
