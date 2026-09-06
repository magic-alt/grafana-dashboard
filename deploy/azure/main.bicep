targetScope = 'resourceGroup'

@description('Azure region for the observability platform resources.')
param location string = resourceGroup().location

@description('Short prefix used for globally/regionally unique resource names.')
@minLength(3)
@maxLength(20)
param namePrefix string = 'magicobs'

@description('Deployment environment tag, for example dev, staging, or prod.')
@allowed([
  'dev'
  'staging'
  'prod'
])
param environment string = 'dev'

@description('Log Analytics retention in days. Keep development retention deliberately small for cost control.')
@minValue(30)
@maxValue(730)
param logRetentionDays int = 30

var commonTags = {
  environment: environment
  managedBy: 'bicep'
  workload: 'observability-platform'
  repository: 'magic-alt/grafana-dashboard'
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: '${namePrefix}-${environment}-logs'
  location: location
  tags: commonTags
  properties: {
    retentionInDays: logRetentionDays
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
  sku: {
    name: 'PerGB2018'
  }
}

resource monitorWorkspace 'Microsoft.Monitor/accounts@2023-04-03' = {
  name: '${namePrefix}-${environment}-monitor'
  location: location
  tags: commonTags
  properties: {
    publicNetworkAccess: 'Enabled'
  }
}

resource managedGrafana 'Microsoft.Dashboard/grafana@2023-09-01' = {
  name: '${namePrefix}-${environment}-grafana'
  location: location
  tags: commonTags
  identity: {
    type: 'SystemAssigned'
  }
  sku: {
    name: 'Standard'
  }
  properties: {
    apiKey: 'Disabled'
    deterministicOutboundIP: 'Disabled'
    publicNetworkAccess: 'Enabled'
    zoneRedundancy: 'Disabled'
  }
}

resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2026-01-01' = {
  name: '${namePrefix}-${environment}-cae'
  location: location
  tags: commonTags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
    publicNetworkAccess: 'Enabled'
    zoneRedundant: false
  }
}

output logAnalyticsWorkspaceId string = logAnalytics.id
output azureMonitorWorkspaceId string = monitorWorkspace.id
output managedGrafanaId string = managedGrafana.id
output managedGrafanaEndpoint string = managedGrafana.properties.endpoint
output managedGrafanaPrincipalId string = managedGrafana.identity.principalId
output containerAppsEnvironmentId string = containerAppsEnvironment.id
