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

variable "alert_email" {
  description = "Email address for monitoring alerts"
  type        = string
}

variable "github_repo" {
  description = "GitHub repository (owner/repo) for Workload Identity Federation"
  type        = string
  default     = "a-alhashemi/sarj-docs"
}

# ---------------------------------------------------------------------------
# Sub-PR B — perimeter security (load balancer + Cloudflare WAF)
# ---------------------------------------------------------------------------

variable "cloudflare_api_token" {
  description = "Cloudflare API token with Zone:Edit + Firewall:Edit scopes"
  type        = string
  sensitive   = true
}

variable "cloudflare_zone_id" {
  description = "Cloudflare zone ID for sarj.ai"
  type        = string
}

variable "cloudflare_zone_name" {
  description = "Cloudflare zone name — typically sarj.ai"
  type        = string
  default     = "sarj.ai"
}
