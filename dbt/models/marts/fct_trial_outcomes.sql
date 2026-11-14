-- One row per closed study (COMPLETED, TERMINATED, WITHDRAWN): the model label.
--   completed                  COMPLETED                                         label 0
--   terminated_low_enrollment  TERMINATED and why_stopped matches the pattern    label 1
--   terminated_other           TERMINATED otherwise (incl. no why_stopped)        label null
--   withdrawn                  WITHDRAWN                                          label null
-- The pattern lives in macros/low_enrollment.sql.
with closed as (
    select
        nct_id,
        overall_status,
        why_stopped,
        enrollment_count,
        enrollment_type
    from {{ ref('stg_studies') }}
    -- Label population (spec): interventional studies only. Ingestion already pulls only
    -- interventional studies; the filter keeps the label definition self-contained.
    where overall_status in ('COMPLETED', 'TERMINATED', 'WITHDRAWN')
      and study_type = 'INTERVENTIONAL'
),

classified as (
    select
        nct_id,
        overall_status,
        why_stopped,
        case
            when overall_status = 'COMPLETED' then 'completed'
            when overall_status = 'WITHDRAWN' then 'withdrawn'
            when coalesce({{ is_low_enrollment('why_stopped') }}, false)
                then 'terminated_low_enrollment'
            else 'terminated_other'
        end as outcome_class,
        enrollment_count,
        enrollment_type
    from closed
)

select
    nct_id,
    overall_status,
    why_stopped,
    outcome_class,
    case outcome_class
        when 'terminated_low_enrollment' then 1
        when 'completed' then 0
    end as label,
    enrollment_count,
    enrollment_type
from classified
