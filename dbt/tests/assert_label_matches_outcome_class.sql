-- Fails for rows whose label or outcome_class disagrees with the label rule:
-- 1 <=> terminated_low_enrollment, 0 <=> completed, NULL otherwise; classes match status.
select nct_id, overall_status, outcome_class, label
from {{ ref('fct_trial_outcomes') }}
where not (
       (outcome_class = 'terminated_low_enrollment' and label = 1 and overall_status = 'TERMINATED')
    or (outcome_class = 'completed' and label = 0 and overall_status = 'COMPLETED')
    or (outcome_class = 'terminated_other' and label is null and overall_status = 'TERMINATED')
    or (outcome_class = 'withdrawn' and label is null and overall_status = 'WITHDRAWN')
)
