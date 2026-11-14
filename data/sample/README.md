# Sample snapshot

Build date: 2026-11-14 (UTC). Rebuild with `python -m ingestion sample` (needs network).

Contents are landing-format files (gzipped JSON Lines) that `python -m ingestion load --source sample` loads into `raw.ctgov_studies` and `raw.fda_event_counts`.

## Counts (the test suite checks these against the files)

- Total studies: 3015
- openFDA drugs: 60 (3 returned no reports / HTTP 404)
- Size on disk: 12.5 MB

### Studies by overall status

| status | studies |
|---|---|
| ACTIVE_NOT_RECRUITING | 60 |
| COMPLETED | 1430 |
| NOT_YET_RECRUITING | 400 |
| RECRUITING | 400 |
| SUSPENDED | 40 |
| TERMINATED | 585 |
| UNKNOWN | 40 |
| WITHDRAWN | 60 |

### Closed studies (COMPLETED / TERMINATED) by start year

| start year | COMPLETED | TERMINATED |
|---|---|---|
| 2008 | 110 | 45 |
| 2009 | 110 | 45 |
| 2010 | 110 | 45 |
| 2011 | 110 | 45 |
| 2012 | 110 | 45 |
| 2013 | 110 | 45 |
| 2014 | 110 | 45 |
| 2015 | 110 | 45 |
| 2016 | 110 | 45 |
| 2017 | 110 | 45 |
| 2018 | 110 | 45 |
| 2019 | 110 | 45 |
| 2020 | 110 | 45 |

## How it was built

Interventional studies only (`AREA[StudyType]INTERVENTIONAL`). Every matching NCT id was listed per stratum, then a fixed pseudo-random subset was taken by ordering `sha1(nct_id)`, so the same registry state gives the same sample. Strata:

- COMPLETED: 110 per start year, 2008-2020
- TERMINATED: 45 per start year, 2008-2020
- RECRUITING: 400 (any start date)
- NOT_YET_RECRUITING: 400 (any start date)
- WITHDRAWN: 60 (any start date)
- SUSPENDED: 40 (any start date)
- ACTIVE_NOT_RECRUITING: 60 (any start date)
- UNKNOWN: 40 (any start date)

openFDA: the 60 most common canonical DRUG/BIOLOGICAL names in the sample (`ingestion/drug_names.py`), one `count=patient.reaction.reactionmeddrapt.exact` request each. Study records are stored exactly as the API returned them.

## Licensing

ClinicalTrials.gov and openFDA data are U.S. government works (public domain), so this snapshot can be committed for reproducibility. Do not use it for clinical decisions; openFDA data is unvalidated.
