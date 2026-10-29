resource "google_artifact_registry_repository" "dammam" {
  count         = var.enable_dammam_stack ? 1 : 0
  repository_id = "sarj-docs-dammam"
  location      = var.dammam_region
  format        = "DOCKER"
  description   = "Saudi container images for Sarj Wiki"

  docker_config {
    immutable_tags = true
  }

  cleanup_policy_dry_run = false

  cleanup_policies {
    id     = "delete-untagged"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "2592000s"
    }
  }

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}
