
resource "google_service_account" "backend" {
  account_id   = "sarj-docs-backend"
  display_name = "Sarj Wiki Backend (Cloud Run)"

  depends_on = [google_project_service.apis["iam.googleapis.com"]]
}

resource "google_service_account" "cloud_tasks_invoker" {
  account_id   = "sarj-wiki-task-invoker"
  display_name = "Sarj Wiki Cloud Tasks Invoker"

  depends_on = [google_project_service.apis["iam.googleapis.com"]]
}

resource "google_project_iam_member" "backend_cloudsql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_project_iam_member" "backend_cloudsql_instance_user" {
  project = var.project_id
  role    = "roles/cloudsql.instanceUser"
  member  = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_project_iam_member" "backend_vertex_ai" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_service_account_iam_member" "backend_acts_as_task_invoker" {
  service_account_id = google_service_account.cloud_tasks_invoker.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.backend.email}"
}
