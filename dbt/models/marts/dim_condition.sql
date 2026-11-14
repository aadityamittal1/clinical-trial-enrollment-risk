-- One row per condition (free-text conditionsModule.conditions, matched case-insensitively).
-- MeSH browse data exists per study, not per condition, so a condition's category is the
-- most common known category among the studies that list it (ties: category priority,
-- then name); 'Unknown' when none of those studies has a known category.
with conditions as (
    select
        nct_id,
        condition_name,
        {{ surrogate_key(['lower(condition_name)']) }} as condition_key
    from {{ ref('stg_conditions') }}
),

display_name as (
    select
        condition_key,
        condition_name,
        row_number() over (
            partition by condition_key
            order by count(*) desc, condition_name
        ) as name_rank
    from conditions
    group by condition_key, condition_name
),

category_priority as (
    select condition_category, min(priority) as priority
    from {{ ref('mesh_condition_categories') }}
    group by condition_category
),

category_votes as (
    select
        c.condition_key,
        sc.condition_category,
        count(distinct c.nct_id) as n_studies
    from conditions as c
    inner join {{ ref('int_study_condition_category') }} as sc
        on c.nct_id = sc.nct_id
    where sc.condition_category <> 'Unknown'
    group by c.condition_key, sc.condition_category
),

category_ranked as (
    select
        v.condition_key,
        v.condition_category,
        row_number() over (
            partition by v.condition_key
            order by v.n_studies desc, p.priority, v.condition_category
        ) as category_rank
    from category_votes as v
    inner join category_priority as p
        on v.condition_category = p.condition_category
)

select
    d.condition_key,
    d.condition_name,
    coalesce(c.condition_category, 'Unknown') as condition_category
from display_name as d
left join category_ranked as c
    on d.condition_key = c.condition_key
   and c.category_rank = 1
where d.name_rank = 1
