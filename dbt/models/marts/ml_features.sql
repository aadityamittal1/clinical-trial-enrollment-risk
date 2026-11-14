-- One row per study: registration-time features for the model.
--
-- LEAKAGE RULES (spec, "Leakage traps"): nothing here may come from enrollment count,
-- completion dates (or durations built on them), last-update date, results flags,
-- why_stopped or the overall status. Only the stg_studies columns listed in `design`
-- below are read; tests/dbt/test_dbt_build.py and tests/assert_ml_features_no_leakage.sql
-- guard this.
-- Known soft leak (documented): n_sites, n_countries and us_only come from the current
-- site list, which can change after registration.
-- start_year comes from start_date whether it is tagged ACTUAL or ESTIMATED.
with design as (
    select
        nct_id,
        phase,
        allocation,
        masking,
        primary_purpose,
        n_arms,
        lead_sponsor_class,
        n_collaborators,
        has_dmc,
        min_age_years,
        max_age_years,
        sex,
        healthy_volunteers,
        eligibility_criteria,
        start_date
    from {{ ref('stg_studies') }}
),

criteria as (
    select
        nct_id,
        eligibility_criteria,
        position('exclusion criteria' in lower(eligibility_criteria)) as exclusion_pos
    from design
),

criteria_sections as (
    -- Text before the first 'exclusion criteria' is the inclusion section, the rest the
    -- exclusion section; without that phrase the whole text counts as inclusion.
    select
        nct_id,
        eligibility_criteria,
        case
            when exclusion_pos = 0 then eligibility_criteria
            else substr(eligibility_criteria, 1, exclusion_pos - 1)
        end as inclusion_text,
        case
            when exclusion_pos > 0 then substr(eligibility_criteria, exclusion_pos)
        end as exclusion_text
    from criteria
),

criteria_counts as (
    select
        nct_id,
        {{ count_criteria_items('inclusion_text', 'inclusion criteria') }} as n_inclusion_criteria,
        case
            when eligibility_criteria is null then null
            when exclusion_text is null then 0
            else {{ count_criteria_items('exclusion_text', 'exclusion criteria') }}
        end as n_exclusion_criteria,
        length(eligibility_criteria) as criteria_text_length
    from criteria_sections
),

intervention_flags as (
    select
        nct_id,
        max(case when intervention_type = 'DRUG' then 1 else 0 end) as has_drug,
        max(case when intervention_type = 'BIOLOGICAL' then 1 else 0 end) as has_biological,
        max(case when intervention_type = 'DEVICE' then 1 else 0 end) as has_device,
        max(case when intervention_type = 'PROCEDURE' then 1 else 0 end) as has_procedure,
        max(case when intervention_type = 'BEHAVIORAL' then 1 else 0 end) as has_behavioral,
        max(
            case
                when intervention_type in ('DRUG', 'BIOLOGICAL', 'DEVICE', 'PROCEDURE', 'BEHAVIORAL')
                    then 0
                else 1
            end
        ) as has_other_intervention
    from {{ ref('stg_interventions') }}
    group by nct_id
),

site_footprint as (
    select
        nct_id,
        count(*) as n_sites,
        count(distinct country) as n_countries,
        max(case when country = 'United States' then 0 else 1 end) = 0 as us_only
    from {{ ref('stg_locations') }}
    group by nct_id
),

condition_counts as (
    select nct_id, count(distinct lower(condition_name)) as n_conditions
    from {{ ref('stg_conditions') }}
    group by nct_id
)

select
    d.nct_id,
    d.phase,
    d.allocation,
    d.masking,
    d.primary_purpose,
    d.n_arms,
    coalesce(i.has_drug, 0) = 1 as has_drug,
    coalesce(i.has_biological, 0) = 1 as has_biological,
    coalesce(i.has_device, 0) = 1 as has_device,
    coalesce(i.has_procedure, 0) = 1 as has_procedure,
    coalesce(i.has_behavioral, 0) = 1 as has_behavioral,
    coalesce(i.has_other_intervention, 0) = 1 as has_other_intervention,
    d.lead_sponsor_class as sponsor_class,
    d.n_collaborators,
    d.has_dmc,
    d.min_age_years,
    d.max_age_years,
    d.sex,
    d.healthy_volunteers,
    c.n_inclusion_criteria,
    c.n_exclusion_criteria,
    c.criteria_text_length,
    coalesce(f.n_sites, 0) as n_sites,
    coalesce(f.n_countries, 0) as n_countries,
    f.us_only,
    cc.condition_category,
    coalesce(n.n_conditions, 0) as n_conditions,
    cast(extract(year from d.start_date) as integer) as start_year
from design as d
left join criteria_counts as c
    on d.nct_id = c.nct_id
left join intervention_flags as i
    on d.nct_id = i.nct_id
left join site_footprint as f
    on d.nct_id = f.nct_id
left join {{ ref('int_study_condition_category') }} as cc
    on d.nct_id = cc.nct_id
left join condition_counts as n
    on d.nct_id = n.nct_id
