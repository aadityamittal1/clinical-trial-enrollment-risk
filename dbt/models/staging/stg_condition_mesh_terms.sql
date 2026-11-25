-- One row per study x MeSH term from derivedSection.conditionBrowseModule: the matched
-- terms (`meshes`, term_source = 'mesh') and their ancestors (`ancestors`,
-- term_source = 'ancestor'). The API gives no tree level or browse branch, so the
-- top-level category is derived later from a fixed list of tree-root names
-- (seeds/mesh_condition_categories.csv).
{%- set m = 'derivedSection.conditionBrowseModule' %}

with meshes as (
    {{ flatten_array(ref('stg_ctgov_latest'), 'study', m ~ '.meshes') }}
),

ancestors as (
    {{ flatten_array(ref('stg_ctgov_latest'), 'study', m ~ '.ancestors') }}
),

terms as (
    select
        nct_id,
        'mesh' as term_source,
        {{ json_str('elem', 'id') }} as mesh_id,
        {{ json_str('elem', 'term') }} as mesh_term
    from meshes
    union all
    select
        nct_id,
        'ancestor' as term_source,
        {{ json_str('elem', 'id') }} as mesh_id,
        {{ json_str('elem', 'term') }} as mesh_term
    from ancestors
)

select distinct
    nct_id,
    term_source,
    mesh_id,
    mesh_term
from terms
where mesh_term is not null
