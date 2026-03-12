# Artifact Registry for container images

resource "google_artifact_registry_repository" "sarj_docs" {
  repository_id = "sarj-docs"
  location      = var.region
  format        = "DOCKER"
  description   = "Container images for Sarj Docs"

  depends_on = [google_project_service.apis["artifactregistry.googleapis.com"]]
}
