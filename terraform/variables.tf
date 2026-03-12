variable "project_id" {
  description = "GCP project ID"
  type        = string
  default     = "sarj-docs"
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "me-central1"
}

variable "frontend_url" {
  description = "Frontend URL (e.g. https://docs.sarj.ai)"
  type        = string
}

variable "backend_domain" {
  description = "Backend domain (e.g. api-docs.sarj.ai)"
  type        = string
}

variable "drive_root_folder_id" {
  description = "Google Drive root folder ID for Sarj Docs"
  type        = string
}

variable "admin_impersonate_email" {
  description = "Workspace admin email for Admin SDK impersonation"
  type        = string
  default     = "nasr@sarj.ai"
}

variable "db_tier" {
  description = "Cloud SQL machine tier"
  type        = string
  default     = "db-f1-micro"
}

variable "db_password" {
  description = "Cloud SQL database password"
  type        = string
  sensitive   = true
}

variable "google_client_id" {
  description = "Google OAuth client ID"
  type        = string
  sensitive   = true
}

variable "google_client_secret" {
  description = "Google OAuth client secret"
  type        = string
  sensitive   = true
}

variable "fernet_key" {
  description = "Fernet encryption key for token storage"
  type        = string
  sensitive   = true
}

variable "service_account_key_path" {
  description = "Local path to the GCP service account JSON key file"
  type        = string
}

variable "alert_email" {
  description = "Email address for monitoring alerts"
  type        = string
}

variable "github_repo" {
  description = "GitHub repository (owner/repo) for Workload Identity Federation"
  type        = string
  default     = "a-alhashemi/sarj-docs"
}
