# ADR 0006: No probability recalibration in v1

Status: accepted.

## Context

The dashboards show probabilities, so calibration matters. On the sample, predicted probabilities run low on recent
cohorts: mean predicted 0.117 against an observed rate of 0.154 on validation, and 0.101 against 0.127 on test
(LightGBM). The sample deliberately oversamples terminated trials, so its prevalence is not the real prevalence, and the
validation and test sets have 40 and 32 positives.

## Decision

Do not apply Platt scaling, isotonic regression or any other recalibration in v1. Report the calibration table and plot as
they are (`docs/model_metrics.json`, `docs/img/calibration.png`) and state the downward bias. Revisit calibration on the
full dataset, not on the oversampled sample.

## Consequences

- Scores are best read as a ranking. Absolute values are not real-world probabilities.
- A recalibrator fitted on 40 validation positives from a sample with a distorted base rate would fit the sample, not the
  registry, and would look better than it is.
- Dashboard thresholds such as the 0.5 "high risk" cut in the BI bundles are placeholders.
