variable "subscription_id" {
  description = "Azure subscription ID."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
  default     = "eastus2"
}

variable "name_prefix" {
  description = "Prefix for resource names (lowercase letters and digits, max 10)."
  type        = string
  default     = "trialrisk"
}

variable "aks_node_vm_size" {
  description = "VM size for the single AKS node."
  type        = string
  default     = "Standard_B2s"
}

variable "aks_node_count" {
  description = "Node count for the single small node pool."
  type        = number
  default     = 1
}

variable "budget_amount" {
  description = "Monthly budget in USD."
  type        = number
  default     = 25
}

variable "budget_start_date" {
  description = "First day of the CURRENT month, RFC3339. Azure rejects start dates in the past months; update it when you re-apply in a later month."
  type        = string
  default     = "2026-11-01T00:00:00Z"
}

variable "budget_alert_emails" {
  description = "Email addresses notified at 50/80/100 percent of the budget."
  type        = list(string)
}

variable "snowflake_password" {
  description = "Snowflake password. Supply via TF_VAR_snowflake_password. cloud-down.sh supplies a throwaway value for destroy."
  type        = string
  sensitive   = true
}

variable "openfda_api_key" {
  description = "Optional openFDA API key. Empty means no Key Vault secret is created."
  type        = string
  default     = ""
  sensitive   = true
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default = {
    project = "trial-risk"
  }
}
