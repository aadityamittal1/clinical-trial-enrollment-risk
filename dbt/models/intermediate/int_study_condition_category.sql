-- One row per study: the top-level condition category.
-- Every MeSH term of the study (matched terms and ancestors) is looked up in the
-- tree-root list seeds/mesh_condition_categories.csv. When several categories match, the
-- one with the lowest priority wins (ties: alphabetical category). Studies without a
-- matching term (including the ones without conditionBrowseModule) get 'Unknown'.
with matches as (
    select
        t.nct_id,
        c.condition_category,
        c.priority
    from {{ ref('stg_condition_mesh_terms') }} as t
    inner join {{ ref('mesh_condition_categories') }} as c
        on t.mesh_term = c.mesh_term
),

ranked as (
    select
        nct_id,
        condition_category,
        row_number() over (
            partition by nct_id
            order by priority, condition_category
        ) as category_rank
    from matches
),

n_categories as (
    select nct_id, count(distinct condition_category) as n_matched_categories
    from matches
    group by nct_id
)

select
    s.nct_id,
    coalesce(r.condition_category, 'Unknown') as condition_category,
    coalesce(n.n_matched_categories, 0) as n_matched_categories
from {{ ref('stg_studies') }} as s
left join ranked as r
    on s.nct_id = r.nct_id
   and r.category_rank = 1
left join n_categories as n
    on s.nct_id = n.nct_id
