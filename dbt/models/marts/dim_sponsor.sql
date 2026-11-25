-- One row per sponsor name (lead sponsors and collaborators).
-- A name can carry different classes across studies (collaborators are often UNKNOWN);
-- the class shown is the most frequent known class, preferring classes given as lead
-- sponsor, ties alphabetical. UNKNOWN only when no other class was ever given.
with class_counts as (
    select
        sponsor_name,
        sponsor_class,
        sum(case when sponsor_role = 'lead' then 1 else 0 end) as n_lead,
        count(*) as n_rows
    from {{ ref('stg_sponsors') }}
    where sponsor_class is not null
    group by sponsor_name, sponsor_class
),

ranked as (
    select
        sponsor_name,
        sponsor_class,
        row_number() over (
            partition by sponsor_name
            order by
                case when sponsor_class = 'UNKNOWN' then 1 else 0 end,
                n_lead desc,
                n_rows desc,
                sponsor_class
        ) as class_rank
    from class_counts
),

names as (
    select distinct sponsor_name from {{ ref('stg_sponsors') }}
)

select
    {{ surrogate_key(['n.sponsor_name']) }} as sponsor_key,
    n.sponsor_name,
    coalesce(r.sponsor_class, 'UNKNOWN') as sponsor_class
from names as n
left join ranked as r
    on n.sponsor_name = r.sponsor_name
   and r.class_rank = 1
