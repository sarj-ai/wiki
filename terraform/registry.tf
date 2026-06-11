# Artifact Registry for container images

resource "google_artifact_registry_repository" "sarj_docs" {
  repository_id = "sarj-docs"
  location      = var.region
  format        = "DOCKER"
  description   = "Container images for Sarj Wiki"

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

  depends_on = [google_project_service.apis["artifactregistry.googleapis.com"]]
}
