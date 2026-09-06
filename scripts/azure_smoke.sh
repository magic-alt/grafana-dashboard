#!/usr/bin/env bash
set -euo pipefail

resource_group="${1:?resource group required}"
deployment_name="${2:?deployment name required}"

outputs=$(az deployment group show --resource-group "$resource_group" --name "$deployment_name" --query properties.outputs -o json)
echo "$outputs" | python -m json.tool >/dev/null

grafana=$(echo "$outputs" | python -c 'import json,sys; print(json.load(sys.stdin)["managedGrafanaEndpoint"]["value"])')
monitor=$(echo "$outputs" | python -c 'import json,sys; print(json.load(sys.stdin)["azureMonitorWorkspaceId"]["value"])')
identity=$(echo "$outputs" | python -c 'import json,sys; print(json.load(sys.stdin)["workloadIdentityClientId"]["value"])')

[[ "$grafana" == https://* ]]
[[ "$monitor" == /subscriptions/* ]]
[[ -n "$identity" ]]

az resource show --ids "$monitor" --query properties -o json >/dev/null
az grafana show --ids "$(echo "$outputs" | python -c 'import json,sys; print(json.load(sys.stdin)["managedGrafanaId"]["value"])')" -o json >/dev/null

# Capture a reproducible cost-query timestamp/window for later budget analysis. The Cost
# Management API can legitimately return no rows for newly-created resources.
az costmanagement query --scope "/subscriptions/$(az account show --query id -o tsv)/resourceGroups/$resource_group" --type ActualCost --timeframe MonthToDate -o json > azure-cost-snapshot.json || true

echo "azure resource smoke passed; endpoint=$grafana"
