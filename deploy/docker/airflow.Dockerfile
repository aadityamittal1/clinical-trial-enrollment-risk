# syntax=docker/dockerfile:1
# Airflow image with this repo's DAGs baked in (see deploy/helm/airflow-values-*.yaml).
#   docker build -f deploy/docker/airflow.Dockerfile -t trial-risk-airflow:local .
# The .dockerignore does not exclude dags/.
FROM apache/airflow:2.10.5-python3.12

COPY --chown=airflow:root dags/ /opt/airflow/dags/
