-- Top 25 openFDA reactions per drug (latest load), for drugs in dim_drug.
-- PK: drug_key + reaction. reaction_rank 1 = most reported; ties broken alphabetically.
with reactions as (
    select
        d.drug_key,
        f.reaction,
        sum(f.report_count) as report_count
    from {{ ref('stg_fda_event_counts') }} as f
    inner join {{ ref('dim_drug') }} as d
        on f.drug_name = d.canonical_name
    group by d.drug_key, f.reaction
),

ranked as (
    select
        drug_key,
        reaction,
        report_count,
        row_number() over (
            partition by drug_key
            order by report_count desc nulls last, reaction
        ) as reaction_rank
    from reactions
)

select
    drug_key,
    reaction,
    report_count,
    cast(reaction_rank as integer) as reaction_rank
from ranked
where reaction_rank <= 25
