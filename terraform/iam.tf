# Service accounts and IAM bindings

# Cloud Run backend service account
resource "google_service_account" "backend" {
  account_id   = "sarj-docs-backend"
  display_name = "Sarj Docs Backend (Cloud Run)"

  depends_on = [google_project_service.apis["iam.googleapis.com"]]
}

# Cloud Scheduler service account
resource "google_service_account" "scheduler" {
  account_id   = "sarj-docs-scheduler"
  display_name = "Sarj Docs Cloud Scheduler"

  depends_on = [google_project_service.apis["iam.googleapis.com"]]
}

# Backend SA can connect to Cloud SQL
resource "google_project_iam_member" "backend_cloudsql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.backend.email}"
}

# Scheduler SA can invoke the backend Cloud Run service
resource "google_cloud_run_v2_service_iam_member" "scheduler_invoker" {
  project  = var.project_id
  name     = google_cloud_run_v2_service.backend.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

# Allow public access to the backend (app handles its own auth via JWT)
resource "google_cloud_run_v2_service_iam_member" "backend_public" {
  project  = var.project_id
  name     = google_cloud_run_v2_service.backend.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}
