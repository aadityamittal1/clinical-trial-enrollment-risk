-- One row per canonical drug name (DRUG / BIOLOGICAL interventions after normalisation).
-- has_fda_data: the latest openFDA response for that name has at least one reaction.
-- Non-drug names (saline, blood sample, ...) are kept: name matching is a documented
-- risk, and the share of rows with has_fda_data is the BI match rate.
with study_drugs as (
    select distinct
        i.nct_id,
        d.canonical_name
    from {{ ref('stg_interventions') }} as i
    inner join {{ ref('stg_drug_names') }} as d
        on i.intervention_name = d.raw_name
    where i.intervention_type in ('DRUG', 'BIOLOGICAL')
      and d.canonical_name is not null
),

fda_drugs as (
    select distinct drug_name
    from {{ ref('stg_fda_event_counts') }}
)

select
    {{ surrogate_key(['s.canonical_name']) }} as drug_key,
    s.canonical_name,
    count(distinct s.nct_id) as n_studies,
    max(case when f.drug_name is not null then 1 else 0 end) = 1 as has_fda_data
from study_drugs as s
left join fda_drugs as f
    on s.canonical_name = f.drug_name
group by s.canonical_name
