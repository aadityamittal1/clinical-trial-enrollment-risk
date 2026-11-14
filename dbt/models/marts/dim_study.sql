-- One row per study: descriptive attributes for BI and the scoring API.
select
    s.nct_id,
    s.brief_title,
    s.phase,
    s.overall_status,
    s.overall_status in ('RECRUITING', 'NOT_YET_RECRUITING') as is_open,
    case
        when s.lead_sponsor_name is not null then {{ surrogate_key(['s.lead_sponsor_name']) }}
    end as sponsor_key,
    s.lead_sponsor_class,
    s.start_date,
    cast(extract(year from s.start_date) as integer) as start_year,
    s.completion_date,
    s.enrollment_count,
    s.enrollment_type,
    'https://clinicaltrials.gov/study/' || s.nct_id as ctgov_url
from {{ ref('stg_studies') }} as s
