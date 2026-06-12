# Service accounts and IAM bindings
#
# Sarj Wiki only needs one runtime service account — the Cloud
# Run backend — because the pivot removed:
#   * Cloud Scheduler jobs (no more webhook renewal or group sync)
#   * Admin SDK domain-wide-delegation
#   * Drive push webhook handlers
# The old ``sarj-docs-scheduler`` SA was dropped alongside
# terraform/scheduler.tf.

# Cloud Run backend service account
resource "google_service_account" "backend" {
  account_id   = "sarj-docs-backend"
  display_name = "Sarj Wiki Backend (Cloud Run)"

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

# Allow public access to the backend — JWT + @sarj.ai domain
# enforcement at the app layer is the real auth boundary. The GCLB
# + Cloudflare layer on top (see loadbalancer.tf / cloudflare.tf)
# terminates TLS, applies WAF rules, and scrubs bad traffic before
# it reaches Cloud Run.
resource "google_cloud_run_v2_service_iam_member" "backend_public" {
  project  = var.project_id
  name     = google_cloud_run_v2_service.backend.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}
