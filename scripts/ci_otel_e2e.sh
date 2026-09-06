#!/usr/bin/env bash
set -euo pipefail

export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-ci-postgres-password}"
export GRAFANA_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD:-ci-grafana-password}"
export GRAFANA_ANONYMOUS_ENABLED=true
export DEPLOYMENT_ENVIRONMENT=ci
export OBS_SERVICE_NAMESPACE=magic-alt

compose=(docker compose -f docker-compose.yml -f docker-compose.observability.yml)
trap '${compose[*]} down -v --remove-orphans >/dev/null 2>&1 || true' EXIT

"${compose[@]}" up -d postgres migrate tempo loki prometheus pyroscope otel-collector grafana

for url in http://localhost:13133 http://localhost:9090/-/ready http://localhost:3100/ready http://localhost:3200/ready; do
  for _ in $(seq 1 60); do
    if curl -fsS "$url" >/dev/null; then break; fi
    sleep 2
  done
  curl -fsS "$url" >/dev/null
done

python scripts/otel_e2e.py

docker run --rm -v "$PWD/observability/prometheus/rules:/rules:ro" prom/prometheus:v${PROMETHEUS_VERSION:-3.14.0} promtool check rules /rules/*.yml
