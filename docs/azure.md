# Azure deployment

## Deployment philosophy

Do not lift the local Docker Compose observability stack unchanged into Azure.

The stable interface is **OTLP**, not a particular telemetry backend. Local development uses the Grafana OSS stack because it is cheap, inspectable, and portable. Azure should preferentially use managed services for identity, operations, and telemetry storage while preserving the same application instrumentation contract.

Recommended target:

```text
Application workloads
    |
    | OTLP
    v
OpenTelemetry gateway / Azure Monitor ingestion
    |
    +--> Azure Monitor / Log Analytics
    +--> Azure Monitor Workspace
                |
                v
        Azure Managed Grafana
```

Reference/domain PostgreSQL data should move to an Azure database only if the stock/LEAN lab itself needs to run in Azure. It is not required for generic observability ingestion.

## What the Bicep landing zone creates

`deploy/azure/main.bicep` currently creates a development landing zone containing:

- Log Analytics workspace;
- Azure Monitor workspace;
- Azure Managed Grafana with a system-assigned managed identity;
- Azure Container Apps managed environment for optional application/collector workloads.

This is deliberately a **landing zone**, not the final production topology. It validates resource creation, naming, and the cloud deployment boundary without pretending that public networking and development retention are production security settings.

## Prerequisites

- Azure CLI with Bicep support;
- a selected subscription;
- a resource group in the intended Azure region;
- permission to deploy the resources above and create the required role assignments during the production-hardening phase.

## Validate Bicep locally

```bash
make azure-check
```

Equivalent command:

```bash
az bicep build --file deploy/azure/main.bicep --stdout >/dev/null
```

## Development deployment

Example:

```bash
az group create \
  --name rg-magic-observability-dev \
  --location southeastasia

az deployment group create \
  --resource-group rg-magic-observability-dev \
  --template-file deploy/azure/main.bicep \
  --parameters \
      namePrefix=magicobs \
      environment=dev \
      logRetentionDays=30
```

Inspect outputs:

```bash
az deployment group show \
  --resource-group rg-magic-observability-dev \
  --name main \
  --query properties.outputs
```

Azure may choose a generated deployment name if `--name` is omitted. Use the actual deployment name shown by the CLI.

## Workload configuration

Keep the same resource contract used locally:

```text
OTEL_SERVICE_NAME=<service>
OBS_SERVICE_NAMESPACE=magic-alt
SERVICE_VERSION=<release-or-git-sha>
DEPLOYMENT_ENVIRONMENT=dev
OBS_PROJECT=<project>
```

The actual Azure Monitor/OTLP endpoint and authentication mechanism must be injected by the Azure deployment layer. Do not bake Azure credentials into an application image or Grafana provisioning file.

## Production hardening backlog

Before a production rollout, add and validate all of the following.

### Identity and access

- Entra ID groups for Grafana Admin/Editor/Viewer roles;
- managed identities for workloads;
- least-privilege role assignments between Managed Grafana and Azure Monitor resources;
- no long-lived Grafana API keys unless there is a specific automation requirement;
- separate human administration and workload identities.

### Secrets

- Azure Key Vault for application/database secrets;
- Key Vault references or managed identity instead of plaintext environment secrets;
- secret rotation tests.

### Networking

- private networking/private endpoints where supported;
- VNet integration for application workloads;
- explicit ingress policy for any OpenTelemetry gateway;
- no public database exposure;
- egress policy for data sources/model providers/broker APIs as required by each workload.

### Reliability

- production retention policy per signal type;
- capacity and ingestion-volume tests;
- availability objectives and alert routing;
- backup/restore for domain PostgreSQL if deployed;
- infrastructure deployment rollback strategy;
- regional/zone design based on the required SLA.

### Cost governance

Observability is easy to make more expensive than the workload it observes. Define budgets for:

- Log Analytics ingestion and retention;
- metric cardinality;
- trace sample rate;
- profile sample/retention policy;
- dashboard/query frequency;
- Container Apps minimum replicas and scaling.

Development should default to short retention and aggressive telemetry hygiene. Production increases retention only for signals with a concrete operational or compliance requirement.

## Azure validation gate

A deployment should not be called validated merely because Bicep returned success. The Azure verification run should prove:

1. the Managed Grafana endpoint is reachable through the intended identity path;
2. a test service can emit OTLP logs, metrics, and traces;
3. all three signals arrive in the intended Azure Monitor stores;
4. a trace can be correlated to related logs/metrics in Grafana;
5. dashboard provisioning/import is repeatable from Git;
6. an alert can fire and reach a test notification route;
7. telemetry stops or degrades predictably when a dependency is denied or unavailable;
8. cost/ingestion volume for the validation window is recorded.

Keep the Azure smoke test as automation in this repository once credentials/federated GitHub identity are configured. Until then, CI validates the Bicep syntax and local telemetry contract without requiring cloud credentials.
