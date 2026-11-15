# ADR 0004: Time-based train, validation and test split

Status: accepted.

## Context

Recent trials that are already closed skew toward early stops: a trial that started in 2023 and is already closed is more
likely to have been stopped than finished. A random split would also mix years, so the test score would reflect an
interpolation, not a forecast.

## Decision

Split by `start_year` (`ml/split.py`): train up to 2016, validation 2017-2018, test 2019-2020. Studies that started after
2020, or have no start year, are dropped from modelling. Metrics are reported per split.

## Consequences

- The test score is for trials that started after the training period, which is closer to how the model would be used.
- Test and validation sets are small on the sample (252 and 260 rows, with 32 and 40 positives), so metrics are noisy and
  model comparisons are within noise.
- Dropping years after 2020 removes some recent data from training.
- `start_year` mixes actual and estimated start dates, so the boundaries are approximate.
