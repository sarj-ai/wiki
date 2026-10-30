variable "project_id" {
  description = "GCP project ID"
  type        = string
  default     = "sarj-docs"
}

variable "dammam_region" {
  description = "Saudi GCP region used by the canonical production stack"
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

variable "provision_mcp_native_secrets" {
  description = "Provision the native MCP secrets and attach them to the backend while MCP remains disabled"
  type        = bool
  default     = false

  validation {
    condition = (
      var.provision_mcp_native_secrets == (var.mcp_native_fernet_keys != null) &&
      var.provision_mcp_native_secrets == (var.mcp_native_lock_key != null)
    )
    error_message = "Set both native MCP secret values exactly when provision_mcp_native_secrets is true."
  }
}

variable "enable_mcp_native" {
  description = "Expose the native MCP and OAuth routes after secrets are explicitly provisioned"
  type        = bool
  default     = false

  validation {
    condition     = !var.enable_mcp_native || var.provision_mcp_native_secrets
    error_message = "enable_mcp_native requires provision_mcp_native_secrets so both independent key references are present."
  }
}

variable "mcp_native_fernet_keys" {
  description = "Newest-first independent Fernet key ring for native MCP credential state"
  type        = string
  sensitive   = true
  ephemeral   = true
  nullable    = true
  default     = null

  validation {
    condition     = var.mcp_native_fernet_keys == null || try(trimspace(var.mcp_native_fernet_keys) != "", false)
    error_message = "mcp_native_fernet_keys must be null or non-empty."
  }
}

variable "mcp_native_fernet_keys_generation" {
  description = "Monotonic generation that publishes a new native MCP credential key-ring version"
  type        = number
  default     = 1

  validation {
    condition     = var.mcp_native_fernet_keys_generation >= 1 && floor(var.mcp_native_fernet_keys_generation) == var.mcp_native_fernet_keys_generation
    error_message = "mcp_native_fernet_keys_generation must be a positive integer."
  }
}

variable "mcp_native_lock_key" {
  description = "Independent stable Fernet key for native MCP database lock identities"
  type        = string
  sensitive   = true
  ephemeral   = true
  nullable    = true
  default     = null

  validation {
    condition     = var.mcp_native_lock_key == null || try(trimspace(var.mcp_native_lock_key) != "", false)
    error_message = "mcp_native_lock_key must be null or non-empty."
  }
}

variable "mcp_native_lock_key_generation" {
  description = "Monotonic generation for coordinated native MCP lock-key replacement"
  type        = number
  default     = 1

  validation {
    condition     = var.mcp_native_lock_key_generation >= 1 && floor(var.mcp_native_lock_key_generation) == var.mcp_native_lock_key_generation
    error_message = "mcp_native_lock_key_generation must be a positive integer."
  }
}

variable "alert_email" {
  description = "Email address for monitoring alerts"
  type        = string
}

variable "github_repository_id" {
  description = "Immutable GitHub repository ID authorized for production deployment"
  type        = string
  default     = "1183627122"

  validation {
    condition     = can(regex("^[1-9][0-9]*$", var.github_repository_id))
    error_message = "github_repository_id must be a positive numeric GitHub repository ID."
  }
}

variable "github_repository_owner_id" {
  description = "Immutable GitHub organization ID that owns the deployment repository"
  type        = string
  default     = "148175495"

  validation {
    condition     = can(regex("^[1-9][0-9]*$", var.github_repository_owner_id))
    error_message = "github_repository_owner_id must be a positive numeric GitHub owner ID."
  }
}

variable "enable_document_transform_qa_job" {
  description = "Provision the private, manually invoked document-transform corpus QA job"
  type        = bool
  default     = false
}

variable "document_transform_qa_image" {
  description = "Digest-pinned QA runner image; required only when the default-off QA job is enabled"
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      !var.enable_document_transform_qa_job ||
      (var.document_transform_qa_image != null && can(regex(
        "^${var.dammam_region}-docker[.]pkg[.]dev/${var.project_id}/sarj-docs-dammam/document-transform-qa@sha256:[0-9a-f]{64}$",
        var.document_transform_qa_image,
      )))
    )
    error_message = "Enabled document-transform QA requires the exact sarj-docs-dammam/document-transform-qa repository digest."
  }
}

variable "document_transform_qa_release_sha" {
  description = "Exact backend/source release represented by the QA image"
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      !var.enable_document_transform_qa_job ||
      (var.document_transform_qa_release_sha != null && can(regex("^[0-9a-f]{40}$", var.document_transform_qa_release_sha)))
    )
    error_message = "Enabled document-transform QA requires an exact 40-character release SHA."
  }
}

variable "document_transform_qa_operator_emails" {
  description = "Exact lowercase sarj.ai operators allowed to request a backend-owned QA launch"
  type        = list(string)
  default     = []

  validation {
    condition = (
      !var.enable_document_transform_qa_job ||
      (length(var.document_transform_qa_operator_emails) > 0 && alltrue([
        for email in var.document_transform_qa_operator_emails :
        can(regex("^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@sarj[.]ai$", email)) && email == lower(email)
      ]))
    )
    error_message = "Enabled document-transform QA requires at least one exact lowercase sarj.ai operator."
  }
}

variable "document_transform_qa_corpus_source_folder_id" {
  description = "Exact server-controlled Security folder ID eligible for read-only QA corpus planning; null keeps corpus planning disabled"
  type        = string
  default     = null

  validation {
    condition     = var.document_transform_qa_corpus_source_folder_id == null || trimspace(var.document_transform_qa_corpus_source_folder_id) != ""
    error_message = "document_transform_qa_corpus_source_folder_id must be null or non-empty."
  }
}

# Cloudflare WAF rules and DNS are managed manually in the Cloudflare
# dashboard — no Terraform resources or API token required.
