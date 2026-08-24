variable "project_id" {
  description = "GCP project ID"
  type        = string
  default     = "sarj-docs"
}

variable "dammam_region" {
  description = "Saudi GCP region used by the parallel migration stack"
  type        = string
  default     = "me-central2"
}

variable "enable_dammam_stack" {
  description = "Create the canonical Dammam production stack"
  type        = bool
  default     = true
}

variable "dammam_image" {
  description = "Pinned image digest for the Dammam Cloud Run service and migration job"
  type        = string
  default     = null
  nullable    = true
}

variable "maintenance_mode" {
  description = "Return 503 for application routes while keeping /health available during cutover"
  type        = bool
  default     = false
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

variable "cache_fernet_key" {
  description = "Newest-first Fernet key ring for disposable caches"
  type        = string
  sensitive   = true
  ephemeral   = true
}

variable "cache_fernet_key_generation" {
  description = "Monotonic generation that publishes a new cache key-ring version"
  type        = number
  default     = 1

  validation {
    condition     = var.cache_fernet_key_generation >= 1 && floor(var.cache_fernet_key_generation) == var.cache_fernet_key_generation
    error_message = "cache_fernet_key_generation must be a positive integer."
  }
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

# Cloudflare WAF rules and DNS are managed manually in the Cloudflare
# dashboard — no Terraform resources or API token required.
