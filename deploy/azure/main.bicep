targetScope = 'resourceGroup'

@description('Azure region for the observability platform resources.')
param location string = resourceGroup().location

@description('Short prefix used for resource names.')
@minLength(3)
@maxLength(16)
param namePrefix string = 'magicobs'

@allowed([
  'dev'
  'staging'
  'prod'
])
param environment string = 'dev'

@minValue(30)
@maxValue(730)
param logRetentionDays int = 30

@description('Optional Entra principal that receives Grafana Admin. Leave empty for IaC-only service deployment.')
param grafanaAdminPrincipalId string = ''

@description('Deploy the stock/LEAN reference PostgreSQL database in Azure. Generic observability does not require it.')
param deployReferenceDatabase bool = false

@description('Administrator login for the optional PostgreSQL reference database.')
param postgresAdministratorLogin string = 'obsadmin'

@secure()
@description('Password for the optional PostgreSQL reference database. Required only when deployReferenceDatabase=true.')
param postgresAdministratorPassword string = ''

var suffix = uniqueString(subscription().id, resourceGroup().id, namePrefix, environment)
var commonTags = {
  environment: environment
  managedBy: 'bicep'
  workload: 'observability-platform'
  repository: 'magic-alt/grafana-dashboard'
}
var monitoringReaderRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '43d0d8ad-25c7-4714-9337-8ba259a9fe05')
var monitoringMetricsPublisherRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '3913510d-42f4-4e42-8a64-420c390055eb')
var grafanaAdminRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '22926164-76b3-42b3-bc55-97df8dab3e41')
var keyVaultSecretsUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: '${namePrefix}-${environment}-vnet'
  location: location
  tags: commonTags
  properties: {
    addressSpace: {
      addressPrefixes: [
        '10.20.0.0/16'
      ]
    }
    subnets: [
      {
        name: 'container-apps'
        properties: {
          addressPrefix: '10.20.0.0/23'
          delegations: [
            {
              name: 'Microsoft.App-environments'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: 'postgres'
        properties: {
          addressPrefix: '10.20.4.0/24'
          delegations: [
            {
              name: 'Microsoft.DBforPostgreSQL-flexibleServers'
              properties: {
                serviceName: 'Microsoft.DBforPostgreSQL/flexibleServers'
              }
            }
          ]
        }
      }
      {
        name: 'private-endpoints'
        properties: {
          addressPrefix: '10.20.5.0/24'
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

resource appsSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  parent: vnet
  name: 'container-apps'
}

resource postgresSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  parent: vnet
  name: 'postgres'
}

resource privateEndpointSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  parent: vnet
  name: 'private-endpoints'
}

resource workloadIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${namePrefix}-${environment}-workload-mi'
  location: location
  tags: commonTags
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

resource dataCollectionEndpoint 'Microsoft.Insights/dataCollectionEndpoints@2024-03-11' = {
  name: '${namePrefix}-${environment}-otlp-dce'
  location: location
  tags: commonTags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${workloadIdentity.id}': {}
    }
  }
  properties: {
    description: 'Native OTLP ingress for the Magic Alt observability platform.'
    networkAcls: {
      publicNetworkAccess: 'Enabled'
    }
  }
}

resource dataCollectionRule 'Microsoft.Insights/dataCollectionRules@2024-03-11' = {
  name: '${namePrefix}-${environment}-otlp-dcr'
  location: location
  tags: commonTags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${workloadIdentity.id}': {}
    }
  }
  properties: {
    description: 'Routes native OTLP metrics to Azure Monitor Workspace and OTLP logs/traces to Log Analytics.'
    dataCollectionEndpointId: dataCollectionEndpoint.id
    directDataSources: {
      otelMetrics: [
        {
          name: 'otlp-metrics'
          streams: [
            'Custom-Metrics-Otel'
          ]
          enrichWithResourceAttributes: [
            '*'
          ]
        }
      ]
      otelLogs: [
        {
          name: 'otlp-logs'
          streams: [
            'Microsoft-OTel-Logs'
          ]
          enrichWithResourceAttributes: [
            '*'
          ]
        }
      ]
      otelTraces: [
        {
          name: 'otlp-traces'
          streams: [
            'Microsoft-OTel-Traces-Spans'
            'Microsoft-OTel-Traces-Events'
            'Microsoft-OTel-Traces-Resources'
          ]
          enrichWithResourceAttributes: [
            '*'
          ]
        }
      ]
    }
    destinations: {
      monitoringAccounts: [
        {
          accountResourceId: monitorWorkspace.id
          name: 'azure-monitor-workspace'
        }
      ]
      logAnalytics: [
        {
          workspaceResourceId: logAnalytics.id
          name: 'log-analytics'
        }
      ]
    }
    dataFlows: [
      {
        streams: [
          'Custom-Metrics-Otel'
        ]
        destinations: [
          'azure-monitor-workspace'
        ]
      }
      {
        streams: [
          'Microsoft-OTel-Logs'
          'Microsoft-OTel-Traces-Spans'
          'Microsoft-OTel-Traces-Events'
          'Microsoft-OTel-Traces-Resources'
        ]
        destinations: [
          'log-analytics'
        ]
      }
    ]
  }
}

resource workloadDcrPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(dataCollectionRule.id, workloadIdentity.id, monitoringMetricsPublisherRoleId)
  scope: dataCollectionRule
  properties: {
    principalId: workloadIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: monitoringMetricsPublisherRoleId
  }
}

resource managedGrafana 'Microsoft.Dashboard/grafana@2025-08-01' = {
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
    publicNetworkAccess: environment == 'dev' ? 'Enabled' : 'Disabled'
    zoneRedundancy: environment == 'prod' ? 'Enabled' : 'Disabled'
  }
}

resource grafanaMonitorReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(monitorWorkspace.id, managedGrafana.id, monitoringReaderRoleId)
  scope: monitorWorkspace
  properties: {
    principalId: managedGrafana.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: monitoringReaderRoleId
  }
}

resource grafanaLogsReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(logAnalytics.id, managedGrafana.id, monitoringReaderRoleId)
  scope: logAnalytics
  properties: {
    principalId: managedGrafana.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: monitoringReaderRoleId
  }
}

resource grafanaAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(grafanaAdminPrincipalId)) {
  name: guid(managedGrafana.id, grafanaAdminPrincipalId, grafanaAdminRoleId)
  scope: managedGrafana
  properties: {
    principalId: grafanaAdminPrincipalId
    roleDefinitionId: grafanaAdminRoleId
  }
}

resource grafanaMonitorPrivateEndpoint 'Microsoft.Dashboard/grafana/managedPrivateEndpoints@2025-08-01' = {
  parent: managedGrafana
  name: 'azure-monitor-workspace'
  location: location
  tags: commonTags
  properties: {
    groupIds: [
      'prometheusMetrics'
    ]
    privateLinkResourceId: monitorWorkspace.id
    privateLinkResourceRegion: location
    privateLinkServiceUrl: ''
    requestMessage: 'Managed Grafana private access to Azure Monitor Workspace'
  }
}

resource grafanaPrivateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.grafana.azure.com'
  location: 'global'
  tags: commonTags
}

resource grafanaPrivateDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: grafanaPrivateDnsZone
  name: '${namePrefix}-${environment}-grafana-vnet-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

resource grafanaPrivateEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = if (environment != 'dev') {
  name: '${namePrefix}-${environment}-grafana-pe'
  location: location
  tags: commonTags
  properties: {
    subnet: {
      id: privateEndpointSubnet.id
    }
    privateLinkServiceConnections: [
      {
        name: 'grafana'
        properties: {
          groupIds: [
            'grafana'
          ]
          privateLinkServiceId: managedGrafana.id
        }
      }
    ]
  }
}

resource grafanaDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = if (environment != 'dev') {
  parent: grafanaPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'grafana'
        properties: {
          privateDnsZoneId: grafanaPrivateDnsZone.id
        }
      }
    ]
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: take('${namePrefix}${environment}${suffix}', 24)
  location: location
  tags: commonTags
  properties: {
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enablePurgeProtection: environment == 'prod'
    softDeleteRetentionInDays: 30
    publicNetworkAccess: environment == 'dev' ? 'Enabled' : 'Disabled'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: environment == 'dev' ? 'Allow' : 'Deny'
    }
    sku: {
      family: 'A'
      name: 'standard'
    }
  }
}

resource workloadKeyVaultSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, workloadIdentity.id, keyVaultSecretsUserRoleId)
  scope: keyVault
  properties: {
    principalId: workloadIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRoleId
  }
}

resource keyVaultPrivateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.vaultcore.azure.net'
  location: 'global'
  tags: commonTags
}

resource keyVaultPrivateDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: keyVaultPrivateDnsZone
  name: '${namePrefix}-${environment}-kv-vnet-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

resource keyVaultPrivateEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = if (environment != 'dev') {
  name: '${namePrefix}-${environment}-kv-pe'
  location: location
  tags: commonTags
  properties: {
    subnet: {
      id: privateEndpointSubnet.id
    }
    privateLinkServiceConnections: [
      {
        name: 'vault'
        properties: {
          groupIds: [
            'vault'
          ]
          privateLinkServiceId: keyVault.id
        }
      }
    ]
  }
}

resource keyVaultDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = if (environment != 'dev') {
  parent: keyVaultPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'vault'
        properties: {
          privateDnsZoneId: keyVaultPrivateDnsZone.id
        }
      }
    ]
  }
}

resource postgresPrivateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = if (deployReferenceDatabase) {
  name: '${namePrefix}.${environment}.postgres.database.azure.com'
  location: 'global'
  tags: commonTags
}

resource postgresPrivateDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = if (deployReferenceDatabase) {
  parent: postgresPrivateDnsZone
  name: '${namePrefix}-${environment}-postgres-vnet-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = if (deployReferenceDatabase) {
  name: take('${namePrefix}-${environment}-${suffix}', 63)
  location: location
  tags: commonTags
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    administratorLogin: postgresAdministratorLogin
    administratorLoginPassword: postgresAdministratorPassword
    version: '16'
    storage: {
      storageSizeGB: 32
    }
    backup: {
      backupRetentionDays: environment == 'prod' ? 14 : 7
      geoRedundantBackup: 'Disabled'
    }
    network: {
      delegatedSubnetResourceId: postgresSubnet.id
      privateDnsZoneArmResourceId: postgresPrivateDnsZone.id
      publicNetworkAccess: 'Disabled'
    }
    highAvailability: {
      mode: environment == 'prod' ? 'SameZone' : 'Disabled'
    }
  }
  dependsOn: [
    postgresPrivateDnsLink
  ]
}

resource postgresPasswordSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (deployReferenceDatabase) {
  parent: keyVault
  name: 'reference-postgres-password'
  properties: {
    value: postgresAdministratorPassword
  }
}

resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2026-01-01' = {
  name: '${namePrefix}-${environment}-cae'
  location: location
  tags: commonTags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${workloadIdentity.id}': {}
    }
  }
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
    publicNetworkAccess: environment == 'dev' ? 'Enabled' : 'Disabled'
    vnetConfiguration: {
      infrastructureSubnetId: appsSubnet.id
      internal: environment != 'dev'
    }
    zoneRedundant: environment == 'prod'
  }
}

output logAnalyticsWorkspaceId string = logAnalytics.id
output azureMonitorWorkspaceId string = monitorWorkspace.id
output managedGrafanaId string = managedGrafana.id
output managedGrafanaEndpoint string = managedGrafana.properties.endpoint
output managedGrafanaPrincipalId string = managedGrafana.identity.principalId
output workloadIdentityId string = workloadIdentity.id
output workloadIdentityClientId string = workloadIdentity.properties.clientId
output workloadIdentityPrincipalId string = workloadIdentity.properties.principalId
output dataCollectionRuleId string = dataCollectionRule.id
output dataCollectionRuleImmutableId string = dataCollectionRule.properties.immutableId
output dataCollectionEndpointId string = dataCollectionEndpoint.id
output dataCollectionLogsEndpoint string = dataCollectionEndpoint.properties.logsIngestion.endpoint
output dataCollectionMetricsEndpoint string = dataCollectionEndpoint.properties.metricsIngestion.endpoint
output keyVaultId string = keyVault.id
output containerAppsEnvironmentId string = containerAppsEnvironment.id
output referencePostgresId string = deployReferenceDatabase ? postgres.id : ''
