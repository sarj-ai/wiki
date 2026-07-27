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

variable "db_tier" {
  description = "Cloud SQL machine tier"
  type        = string
  default     = "db-f1-micro"
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

variable "alert_email" {
  description = "Email address for monitoring alerts"
  type        = string
}

variable "github_repo" {
  description = "GitHub repository (owner/repo) for Workload Identity Federation"
  type        = string
  default     = "sarj-ai/wiki"
}

# Sub-PR B — perimeter security (load balancer + Cloud Armor WAF)
# Cloudflare WAF rules and DNS are managed manually in the Cloudflare
# dashboard — no Terraform resources or API token required.
