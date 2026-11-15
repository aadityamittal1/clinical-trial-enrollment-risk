#!/usr/bin/env bash
# Runs any Global Constraints command passed as arguments, e.g.
#   docker run IMAGE python -m ingestion ctgov --max-pages 2
#   docker run IMAGE dbt build --project-dir dbt --profiles-dir dbt --target local
# With no arguments it runs the in-process pipeline on the sample snapshot.
set -euo pipefail

if [ "$#" -eq 0 ]; then
  set -- python -m pipeline run --source sample
fi
exec "$@"
