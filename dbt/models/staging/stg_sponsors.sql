-- One row per study x sponsor: the lead sponsor (sponsor_role = 'lead', sponsor_index 0)
-- and every collaborator (sponsor_role = 'collaborator', sponsor_index 1..n).
{%- set m = 'protocolSection.sponsorCollaboratorsModule' %}

with latest as (
    select * from {{ ref('stg_ctgov_latest') }}
),

lead_sponsor as (
    select
        nct_id,
        'lead' as sponsor_role,
        0 as sponsor_index,
        trim({{ json_str('study', m ~ '.leadSponsor.name') }}) as sponsor_name,
        {{ json_str('study', m ~ '.leadSponsor.class') }} as sponsor_class
    from latest
),

collaborators as (
    select
        nct_id,
        'collaborator' as sponsor_role,
        elem_index + 1 as sponsor_index,
        trim({{ json_str('elem', 'name') }}) as sponsor_name,
        {{ json_str('elem', 'class') }} as sponsor_class
    from ({{ flatten_array(ref('stg_ctgov_latest'), 'study', m ~ '.collaborators') }}) as flattened
)

select * from lead_sponsor
where nullif(sponsor_name, '') is not null
union all
select * from collaborators
where nullif(sponsor_name, '') is not null
