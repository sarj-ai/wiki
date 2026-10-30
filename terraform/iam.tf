# Service accounts and IAM bindings

# Cloud Run backend service account
resource "google_service_account" "backend" {
  account_id   = "sarj-docs-backend"
  display_name = "Sarj Wiki Backend (Cloud Run)"

  depends_on = [google_project_service.apis["iam.googleapis.com"]]
}

# Carries only the Google-signed OIDC identity used on queue deliveries. It has
# no database, Drive, or project permissions of its own.
resource "google_service_account" "cloud_tasks_invoker" {
  account_id   = "sarj-wiki-task-invoker"
  display_name = "Sarj Wiki Cloud Tasks Invoker"

  depends_on = [google_project_service.apis["iam.googleapis.com"]]
}

# Backend SA can connect to Cloud SQL
resource "google_project_iam_member" "backend_cloudsql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.backend.email}"
}

# Required on top of cloudsql.client for IAM database authentication —
# lets the SA log in to Postgres as itself (no password anywhere).
resource "google_project_iam_member" "backend_cloudsql_instance_user" {
  project = var.project_id
  role    = "roles/cloudsql.instanceUser"
  member  = "serviceAccount:${google_service_account.backend.email}"
}

# Smart Create uses Vertex AI with the backend's workload identity. No API key
# or long-lived Google credential is present in the application environment.
resource "google_project_iam_member" "backend_vertex_ai" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.backend.email}"
}

# The backend may enqueue into this queue, without receiving project-wide task
# administration permissions.
# Creating an OIDC-authenticated task requires the enqueueing backend to select
# (act as) the dedicated token identity.
resource "google_service_account_iam_member" "backend_acts_as_task_invoker" {
  service_account_id = google_service_account.cloud_tasks_invoker.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.backend.email}"
}
