output "resource_group_name" {
  value = azurerm_resource_group.main.name
}

output "aks_cluster_name" {
  value = azurerm_kubernetes_cluster.main.name
}

output "storage_account_name" {
  value = azurerm_storage_account.main.name
}

output "landing_container_url" {
  value = "https://${azurerm_storage_account.main.name}.blob.core.windows.net/${azurerm_storage_container.landing.name}"
}

output "key_vault_name" {
  value = azurerm_key_vault.main.name
}

output "tenant_id" {
  value = data.azurerm_client_config.current.tenant_id
}

output "key_vault_csi_client_id" {
  description = "Client ID of the Key Vault CSI add-on identity (SecretProviderClass userAssignedIdentityID)."
  value       = azurerm_kubernetes_cluster.main.key_vault_secrets_provider[0].secret_identity[0].client_id
}
