# Architecture

## Product boundary

This repository is an **observability platform plus reference workloads**, not a stock-dashboard application.

The platform owns:

- a stable OpenTelemetry ingestion contract;
- local backends for metrics, logs, traces, and profiles;
- Grafana datasource and dashboard provisioning as code;
- repeatable local/CI validation;
- the Azure landing zone and deployment contract;
- compatibility helpers that let existing labs migrate incrementally.

The platform does **not** own application business logic. Stock collection and LEAN backtests are retained as reference workloads because they exercise network I/O, CPU analysis, database I/O, browser rendering, and long-running jobs.

## Context

```text
+------------------------ Application projects -------------------------+
| qlib / quant | LEAN | RAG / agents | MuJoCo / robotics | web APIs    |
+-------------------------------+---------------------------------------+
                                |
                       OTLP gRPC / HTTP
                                |
                  +-------------v-------------+
                  | OpenTelemetry Collector   |
                  | resource policy / batching|
                  +---+-------------+----------+
                      |             |             \
                   traces        metrics           logs
                      |             |                |
                    Tempo       Prometheus          Loki
                      \             |                /
                       +------------+---------------+
                                    |
                                 Grafana
                                    |
                              dashboards / SLOs

Profiles are sent to Pyroscope. Domain/reference data such as stock prices and
backtest results remains in PostgreSQL and is not used as a generic telemetry store.
```

## Architectural layers

### 1. Workload layer

Projects instrument their code using OpenTelemetry SDKs or an OpenTelemetry-compatible agent. Application code depends only on the telemetry contract, not on Tempo, Loki, Prometheus, Grafana, or Azure-specific APIs.

Required resource attributes:

| Attribute | Purpose | Example |
| --- | --- | --- |
| `service.name` | Deployable process or worker | `ragbot-worker` |
| `service.namespace` | Organization/platform boundary | `magic-alt` |
| `service.version` | Build/release identity | Git SHA or semver |
| `deployment.environment` | Environment | `local`, `ci`, `dev`, `prod` |
| `project.name` | Product/repository grouping | `qlib-platform` |

The Python compatibility runtime lives in `collector/obs_platform`. Existing lab code imports through `collector/observability_support.py`; new Python services should import the shared runtime directly or use the standard OpenTelemetry SDK.

### 2. Telemetry gateway

`observability/otel-collector.yml` is the ingestion boundary. It currently:

- accepts OTLP/gRPC and OTLP/HTTP;
- applies memory limiting, standard resource attributes, and batching;
- exports traces to Tempo;
- exports metrics through a Prometheus endpoint;
- exports OTLP logs to Loki;
- exposes a health endpoint for orchestration.

Keeping this gateway stable is the main portability mechanism. Azure can replace local exporters without changing application instrumentation.

### 3. Storage and visualization

The local developer stack uses:

- Prometheus — metrics;
- Loki — logs;
- Tempo — traces;
- Pyroscope — continuous profiles;
- Grafana — cross-signal visualization;
- PostgreSQL — reference-workload/domain data only.

Dashboards and datasources are provisioned from Git and UI mutation is disabled by default. Changes should therefore be reviewable, testable, and reproducible.

### 4. Delivery layer

Delivery is deliberately split by environment:

- **Local:** Docker Compose for fast development and integration tests.
- **CI:** static contracts + unit tests + deterministic Compose smoke tests.
- **Azure:** Bicep-managed Azure Monitor / Managed Grafana / Container Apps environment, with OTLP as the application-facing contract.

## Dependency rules

1. Application/domain packages must not import Grafana, Tempo, Loki, or Azure SDKs merely to emit telemetry.
2. Telemetry resource attributes are low-cardinality. IDs such as request ID, trace ID, run ID, symbol, and model name must be reviewed before being promoted to metric labels.
3. Secrets are runtime configuration, never committed provisioning values.
4. Dashboards are source artifacts. Production edits flow through Git rather than being made only in the Grafana UI.
5. Local privileged experiments are opt-in profiles. A Docker socket or host PID namespace is never part of the default/cloud serving path.
6. Reference workloads may depend on the platform; the platform must not depend on reference-workload business logic.

## Reliability and operability model

The product target is built around four operational questions:

1. **Is the service available?** health, traffic, error rate, saturation.
2. **Is it fast enough?** latency distributions and critical-path traces.
3. **Why is it unhealthy?** correlated logs, traces, profiles, and dependency telemetry.
4. **Can we prove a release is safe?** CI contracts, deterministic integration tests, dashboard validation, IaC validation, and release identity in telemetry.

The repository currently establishes the product baseline for these guarantees. Enterprise production maturity still requires environment-specific alert routing, SLO/error-budget policies, retention/cost budgets, identity/RBAC, private networking, backup/DR, and load/capacity qualification before a production go-live.

## Migration strategy from the original lab

The migration is intentionally incremental:

- Existing dashboard UIDs remain stable so links do not break.
- Existing stock and LEAN scripts remain executable.
- `observability_support.py` is a compatibility façade over the new platform runtime.
- Developer-specific paths are converted to mounted configuration.
- Dangerous local controls move behind explicit Compose profiles.

A later cleanup can split large legacy scripts into domain/application/infrastructure adapters after the product boundary and regression suite are stable. This avoids a high-risk flag-day rewrite.

## Next architecture gates

Before calling the repository production-ready, complete these gates:

- replace application-owned `CREATE TABLE` calls with versioned database migrations;
- split stock/LEAN control HTTP handlers from domain orchestration and persistence adapters;
- add authenticated control-plane APIs and authorization tests;
- add Prometheus recording rules, alert rules, and SLO definitions as code;
- add cardinality and telemetry-volume budgets;
- provision Azure RBAC, Key Vault, private endpoints/VNet integration, and workload identities;
- add backup/restore and disaster-recovery tests;
- add load tests and explicit availability/latency objectives;
- sign/version container images and add SBOM/provenance in release CI.
