# Magic Alt Observability Platform

A reusable observability platform for **metrics, logs, traces, profiles, dashboards, and operational diagnostics** across local development, CI, and Azure.

The repository started as a Grafana stock-dashboard lab. It now treats the stock pipeline and QuantConnect LEAN experiment as **reference workloads** while the reusable product boundary is OpenTelemetry + Grafana-as-code.

## What this repository provides

| Capability | Local implementation | Cloud boundary |
| --- | --- | --- |
| Telemetry ingestion | OpenTelemetry Collector | OTLP-compatible Azure Monitor path |
| Metrics | Prometheus | Azure Monitor Workspace |
| Logs | Loki | Azure Monitor / Log Analytics |
| Traces | Tempo | Azure Monitor tracing |
| Profiles | Pyroscope | Environment-specific profiling backend |
| Visualization | Grafana OSS | Azure Managed Grafana |
| Domain/reference data | PostgreSQL | Optional managed PostgreSQL for reference workloads |
| Infrastructure | Docker Compose | Bicep |
| Verification | pytest + repository contracts + Compose smoke | Bicep validation; Azure smoke gate documented |

The intended users are not only the included stock and LEAN labs. The same telemetry contract is designed for quant/QLib/LEAN, RAG and AI-agent services, MuJoCo training, robotics/servo systems, and ordinary web/backend services.

## Architecture

```text
 qlib / LEAN       RAG / Agents       MuJoCo / Robotics       APIs
      \                 |                    |                 /
       +----------------+--------------------+----------------+
                                OTLP
                                 |
                     +-----------v-----------+
                     | OpenTelemetry Collector|
                     +-----+---------+--------+
                           |         |         \
                        traces    metrics      logs
                           |         |           |
                         Tempo   Prometheus     Loki
                           \         |           /
                            +--------+----------+
                                     |
                                  Grafana

                              profiles -> Pyroscope
                       domain/reference data -> PostgreSQL
```

See [Architecture](docs/architecture.md) for boundaries, dependency rules, migration strategy, and production gates.

## Quick start

### 1. Configure the environment

```bash
cp .env.example .env
```

Replace at least these two placeholder values before starting the stack:

```text
POSTGRES_PASSWORD=...
GRAFANA_ADMIN_PASSWORD=...
```

Anonymous Grafana access is **disabled by default**.

### 2. Start the core stock reference workload

```bash
docker compose up -d --build
```

Open Grafana at:

```text
http://localhost:3000
```

The stock dashboard retains the stable UID:

```text
http://localhost:3000/d/local-stock-market/local-stock-market-dashboard
```

### 3. Start the full local observability stack

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.observability.yml \
  up -d --build
```

Default local endpoints:

| Service | Endpoint |
| --- | --- |
| Grafana | `http://localhost:3000` |
| OTLP gRPC | `localhost:4317` |
| OTLP HTTP | `http://localhost:4318` |
| OTel health | `http://localhost:13133` |
| Tempo | `http://localhost:3200` |
| Loki | `http://localhost:3100` |
| Prometheus | `http://localhost:9090` |
| Pyroscope | `http://localhost:4040` |

## Safe-by-default lab profiles

The original experimental control surfaces are no longer part of the default serving path.

Run the stock control lab explicitly:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.observability.yml \
  --profile lab-control \
  up -d observability-control
```

Run the LEAN control lab explicitly:

```bash
mkdir -p .local/lean-platform .local/lean-data

docker compose \
  -f docker-compose.yml \
  -f docker-compose.observability.yml \
  --profile lean-lab \
  up -d lean-observability-control
```

The LEAN lab mounts a Docker socket and is therefore intentionally classified as a **local privileged experiment**, not a cloud deployment pattern.

Run a one-shot stock observability case:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.observability.yml \
  --profile observability-case \
  run --rm observability-case
```

## Engineering verification

Install development tooling:

```bash
python3 -m pip install -r collector/requirements.txt
python3 -m pip install -e '.[dev]'
```

Run the fast verification gates:

```bash
make test
make validate
make compose-check
```

Run the deterministic integration test:

```bash
make integration
```

The integration test deliberately seeds PostgreSQL instead of calling Yahoo Finance, so CI is not coupled to a public market-data service or its rate limits.

The existing browser probes remain available:

```bash
npm ci
npm run test:browser
npm run test:observability
npm run test:lean-observability
```

## Telemetry contract for other projects

Every service should identify itself with stable resource attributes:

```text
service.name=<process-or-worker>
service.namespace=magic-alt
service.version=<git-sha-or-release>
deployment.environment=<local|ci|dev|staging|prod>
project.name=<repository-or-product>
```

Local host applications can export OTLP to:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
```

Compose services use:

```text
http://otel-collector:4318
```

For concrete conventions and examples for QLib, LEAN, RAG/agents, MuJoCo, and robot/servo telemetry, see [Project integrations](docs/integrations.md).

## Azure

The Azure target is a managed landing zone rather than a lift-and-shift of the local containers.

Validate the infrastructure definition:

```bash
make azure-check
```

Deploy a development landing zone:

```bash
az group create \
  --name rg-magic-observability-dev \
  --location southeastasia

az deployment group create \
  --resource-group rg-magic-observability-dev \
  --template-file deploy/azure/main.bicep \
  --parameters namePrefix=magicobs environment=dev logRetentionDays=30
```

The Bicep template establishes Azure Monitor, Log Analytics, Azure Managed Grafana, and an Azure Container Apps environment. Production deployment still requires the identity/RBAC, Key Vault, private networking, alerting/SLO, cost, backup/DR, and load-validation gates described in [Azure deployment](docs/azure.md).

## Repository layout

```text
collector/
  obs_platform/            # reusable Python runtime/config/telemetry contract
  *.py                     # legacy/reference stock + LEAN workloads

grafana/
  dashboards/              # dashboards as code
  provisioning/            # datasource/dashboard provisioning

observability/
  otel-collector.yml       # OTLP gateway and signal routing
  prometheus.yml
  loki.yml
  tempo.yml
  alloy-ebpf.alloy

deploy/azure/
  main.bicep               # Azure landing zone

tests/
  fixtures/                # deterministic integration data
  test_*.py                # unit/contract tests

scripts/
  validate_repo.py         # architecture/repository contract gate
  ci_integration.sh        # deterministic Compose integration test
  *smoke* / *probe*        # existing runtime/browser diagnostics

docs/
  architecture.md
  integrations.md
  azure.md
```

## Compatibility policy

The productization work intentionally avoids a flag-day rewrite:

- existing Grafana dashboard UIDs are retained;
- stock and LEAN workflows remain reference workloads;
- `collector/observability_support.py` remains a compatibility façade;
- new shared runtime code lives under `collector/obs_platform`;
- workstation-specific paths are now runtime mounts/configuration;
- privileged experiment controls require explicit profiles.

This lets the repository become reusable first, then progressively split the large legacy lab scripts into domain/application/infrastructure adapters under regression coverage.

## Product maturity

This branch establishes a **production-oriented platform baseline**: portable telemetry contract, secure defaults, dashboards/provisioning as code, multi-signal ingestion, deterministic tests, CI, and Azure IaC.

It should not yet be described as a fully production-certified enterprise service. Remaining gates include authenticated control-plane APIs, versioned database migrations, SLO/alert rules, telemetry-cardinality budgets, Azure RBAC/Key Vault/private networking, backup/DR, capacity tests, signed release images, SBOM/provenance, and cloud end-to-end validation.

## License

This repository is intended to remain reusable across the `magic-alt` project family. Add/confirm the repository license before publishing packaged releases or accepting external contributions.
