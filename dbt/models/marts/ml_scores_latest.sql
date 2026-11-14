{{ config(materialized='view') }}
-- Latest score per study from marts.ml_scores (appended by `python -m ml score`).
-- The on-run-start hook creates ml_scores empty when missing, so this view always builds;
-- it is empty until the first scoring run. Ties on scored_at: highest model_version wins.
with ranked as (
    select
        nct_id,
        risk_score,
        model_version,
        scored_at,
        top_driver_1,
        top_driver_2,
        top_driver_3,
        top_driver_1_shap,
        top_driver_2_shap,
        top_driver_3_shap,
        shap_values,
        row_number() over (
            partition by nct_id
            order by scored_at desc, model_version desc
        ) as score_rank
    from {{ source('marts', 'ml_scores') }}
    where nct_id is not null
)

select
    nct_id,
    risk_score,
    model_version,
    scored_at,
    top_driver_1,
    top_driver_2,
    top_driver_3,
    top_driver_1_shap,
    top_driver_2_shap,
    top_driver_3_shap,
    shap_values
from ranked
where score_rank = 1
