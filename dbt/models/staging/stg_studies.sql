-- One row per study (latest version), flattened from the ClinicalTrials.gov v2 JSON.
{%- set p = 'protocolSection' %}

with latest as (
    select * from {{ ref('stg_ctgov_latest') }}
),

extracted as (
    select
        nct_id,
        {{ json_str('study', p ~ '.identificationModule.briefTitle') }} as brief_title,
        {{ json_str('study', p ~ '.statusModule.overallStatus') }} as overall_status,
        {{ json_str('study', p ~ '.statusModule.whyStopped') }} as why_stopped,
        {{ json_str('study', p ~ '.designModule.studyType') }} as study_type,
        {{ json_array_join('study', p ~ '.designModule.phases', '/') }} as phases_joined,
        {{ json_str('study', p ~ '.designModule.designInfo.allocation') }} as allocation,
        {{ json_str('study', p ~ '.designModule.designInfo.maskingInfo.masking') }} as masking,
        {{ json_str('study', p ~ '.designModule.designInfo.primaryPurpose') }} as primary_purpose,
        {{ json_array_length('study', p ~ '.armsInterventionsModule.armGroups') }} as n_arms,
        {{ json_int('study', p ~ '.designModule.enrollmentInfo.count') }} as enrollment_count,
        {{ json_str('study', p ~ '.designModule.enrollmentInfo.type') }} as enrollment_type,
        {{ json_date('study', p ~ '.statusModule.startDateStruct.date') }} as start_date,
        {{ json_str('study', p ~ '.statusModule.startDateStruct.type') }} as start_date_type,
        {{ json_date('study', p ~ '.statusModule.completionDateStruct.date') }} as completion_date,
        {{ json_str('study', p ~ '.statusModule.completionDateStruct.type') }} as completion_date_type,
        nullif(trim({{ json_str('study', p ~ '.sponsorCollaboratorsModule.leadSponsor.name') }}), '') as lead_sponsor_name,
        {{ json_str('study', p ~ '.sponsorCollaboratorsModule.leadSponsor.class') }} as lead_sponsor_class,
        {{ json_array_length('study', p ~ '.sponsorCollaboratorsModule.collaborators') }} as n_collaborators_raw,
        {{ json_bool('study', p ~ '.oversightModule.oversightHasDmc') }} as has_dmc,
        {{ json_str('study', p ~ '.eligibilityModule.minimumAge') }} as minimum_age,
        {{ json_str('study', p ~ '.eligibilityModule.maximumAge') }} as maximum_age,
        {{ json_str('study', p ~ '.eligibilityModule.sex') }} as sex,
        {{ json_bool('study', p ~ '.eligibilityModule.healthyVolunteers') }} as healthy_volunteers,
        {{ json_str('study', p ~ '.eligibilityModule.eligibilityCriteria') }} as eligibility_criteria,
        {{ json_bool('study', 'hasResults') }} as has_results,
        {{ json_date('study', p ~ '.statusModule.studyFirstPostDateStruct.date') }} as first_posted_date,
        last_update_posted
    from latest
)

select
    nct_id,
    brief_title,
    overall_status,
    why_stopped,
    study_type,
    coalesce(nullif(phases_joined, ''), 'NA') as phase,
    allocation,
    masking,
    primary_purpose,
    n_arms,
    enrollment_count,
    enrollment_type,
    start_date,
    start_date_type,
    completion_date,
    completion_date_type,
    lead_sponsor_name,
    lead_sponsor_class,
    coalesce(n_collaborators_raw, 0) as n_collaborators,
    has_dmc,
    {{ age_to_years('minimum_age') }} as min_age_years,
    {{ age_to_years('maximum_age') }} as max_age_years,
    sex,
    healthy_volunteers,
    eligibility_criteria,
    has_results,
    first_posted_date,
    last_update_posted
from extracted
