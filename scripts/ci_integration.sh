#!/usr/bin/env bash
set -euo pipefail

export POSTGRES_DB="${POSTGRES_DB:-stockdash}"
export POSTGRES_USER="${POSTGRES_USER:-stock}"
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-ci-postgres-password}"
export GRAFANA_ADMIN_USER="${GRAFANA_ADMIN_USER:-admin}"
export GRAFANA_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD:-ci-grafana-password}"
export GRAFANA_ANONYMOUS_ENABLED="true"

cleanup() { docker compose down -v --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT

# Grafana is gated on migrate:service_completed_successfully, so a successful
# `up` here proves the Alembic migration completed before Grafana started.
docker compose up -d postgres migrate grafana

for attempt in $(seq 1 60); do
  if curl --fail --silent http://localhost:3000/api/health >/dev/null; then break; fi
  if [[ "$attempt" == "60" ]]; then
    docker compose ps -a
    docker compose logs grafana migrate postgres
    echo "Grafana did not become healthy" >&2
    exit 1
  fi
  sleep 2
done

docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" < tests/fixtures/seed.sql
python3 scripts/smoke_check.py

docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "SELECT version_num FROM alembic_version WHERE version_num='0001_initial';"

echo "integration smoke passed"
