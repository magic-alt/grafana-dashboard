# Azure production deployment

## Target architecture

The Azure deployment keeps OTLP as the application contract and replaces local LGTM storage with Azure managed services:

```text
GitHub Actions --OIDC--> Azure deployment identity
                           |
Applications/collectors --OTLP + Entra--> DCE / DCR
                           |                 |       |
                           |              metrics  logs/traces
                           |                 |       |
                           |              Azure    Log Analytics
                           |              Monitor      |
                           |                 +----+----+
                           |                      |
                           +----------------> Managed Grafana
                                                  |
                                            private access
```

The Bicep landing zone creates a user-assigned workload identity, VNet/subnets, Log Analytics, Azure Monitor Workspace, native OTLP DCE/DCR, Azure Managed Grafana, Key Vault, Container Apps Environment, RBAC assignments, and production/staging private endpoints for Grafana and Key Vault. The stock/LEAN PostgreSQL database is optional because generic observability does not require domain storage.

## GitHub OIDC

The deployment workflow uses `id-token: write` and `azure/login`. Configure an Entra application or user-assigned identity with a federated credential that trusts this repository/environment. Store only identifiers (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`) in the GitHub environment; do not create an Azure client secret.

Use protected GitHub Environments named `staging` and `prod`. Restrict production to the default branch or signed release tags and require an approving reviewer.

## Native OTLP / DCR

The Bicep definition creates `directDataSources` for:

- metrics stream `Custom-Metrics-Otel` → Azure Monitor Workspace;
- logs stream `Microsoft-OTel-Logs` → Log Analytics;
- trace span/event/resource streams → Log Analytics.

The workload managed identity receives `Monitoring Metrics Publisher` at DCR scope. The DCE remains publicly addressable in this phase but requires Entra authentication; private Azure Monitor ingestion through AMPLS is a separate production networking gate because it changes DNS and all Monitor ingestion/query paths together.

Native OTLP/DCR ingestion is still treated as a staging-qualified capability: `azure_smoke.sh` must be extended with an authenticated three-signal probe in the target subscription before production approval.

## Managed Grafana

Azure Managed Grafana uses a system-assigned identity. IaC grants it Monitoring Reader on both Azure Monitor Workspace and Log Analytics. Grafana also creates a managed private endpoint to the Azure Monitor Workspace using the `prometheusMetrics` private-link group.

For `staging`/`prod`, the Grafana workspace disables public access and receives an inbound private endpoint in the platform VNet with the `privatelink.grafana.azure.com` private DNS zone. Operators therefore need network reachability (VPN, Bastion/VM, peered VNet, or an approved private access path) before using the UI.

## Key Vault

Key Vault uses Azure RBAC and denies public traffic in staging/production. The workload managed identity receives Key Vault Secrets User. A private endpoint and `privatelink.vaultcore.azure.net` DNS zone are created for non-development environments.

## Optional PostgreSQL

Set `deployReferenceDatabase=true` only if the stock/LEAN reference workloads themselves will run in Azure. The database is deployed into a dedicated delegated subnet with private DNS and public network access disabled. The generic fleet/OTLP platform does not depend on PostgreSQL.

Provide the password as a secure deployment parameter; IaC stores it in Key Vault. Production should later replace password administration with Microsoft Entra authentication for application access where practical.

## Deployment

```bash
az group create --name rg-magic-observability-staging --location southeastasia

az deployment group what-if \
  --resource-group rg-magic-observability-staging \
  --template-file deploy/azure/main.bicep \
  --parameters namePrefix=magicobs environment=staging

az deployment group create \
  --resource-group rg-magic-observability-staging \
  --template-file deploy/azure/main.bicep \
  --parameters namePrefix=magicobs environment=staging
```

CI performs Bicep compilation without cloud credentials. `.github/workflows/azure-deploy.yml` is the credentialed, environment-protected deployment path.

## Production gates

A production promotion requires all of these:

1. CI, CodeQL, OTLP E2E, rule validation and IaC compilation green.
2. Release image referenced by immutable digest and carrying SBOM, signature and provenance.
3. GitHub `staging` environment deployment successful.
4. Azure what-if reviewed.
5. Managed Grafana access works through the intended private route.
6. DCR identity/RBAC confirmed and native OTLP logs/metrics/traces tested in Azure.
7. Alert routing and one synthetic alert verified.
8. Cost Management snapshot captured and compared with the environment budget.
9. Backup/restore test completed if optional PostgreSQL is enabled.
10. `prod` environment approval granted.

## Remaining private-networking gate

The template makes Grafana and Key Vault private and VNet-integrates Container Apps/PostgreSQL. Fully private Azure Monitor ingestion/query requires Azure Monitor Private Link Scope (AMPLS) and DNS changes. That should be introduced after the staging native-OTLP path is proven because an incorrectly scoped AMPLS can break ingestion/query for all attached Monitor resources.
