resource "google_cloud_tasks_queue" "smart_create_dammam" {
  count    = var.enable_dammam_stack ? 1 : 0
  name     = "smart-create-dammam"
  location = var.dammam_region

  rate_limits {
    max_dispatches_per_second = 1
    max_concurrent_dispatches = 3
  }

  retry_config {
    max_attempts  = 10
    min_backoff   = "5s"
    max_backoff   = "300s"
    max_doublings = 5
  }

  stackdriver_logging_config {
    sampling_ratio = 1
  }
}

resource "google_cloud_tasks_queue" "document_transform_dammam" {
  count    = var.enable_dammam_stack ? 1 : 0
  name     = "document-transform-dammam"
  location = var.dammam_region

  rate_limits {
    max_dispatches_per_second = 1
    max_concurrent_dispatches = 2
  }

  retry_config {
    # Dispatches rejected while a prior worker lease is still active do not count
    # toward the application's separate ten claimed-execution safety cap.
    max_attempts  = 100
    min_backoff   = "5s"
    max_backoff   = "300s"
    max_doublings = 5
  }

  stackdriver_logging_config {
    sampling_ratio = 1
  }

  depends_on = [google_project_service.apis["cloudtasks.googleapis.com"]]
}

resource "google_cloud_tasks_queue_iam_member" "backend_enqueuer_dammam" {
  count    = var.enable_dammam_stack ? 1 : 0
  project  = var.project_id
  location = google_cloud_tasks_queue.smart_create_dammam[0].location
  name     = google_cloud_tasks_queue.smart_create_dammam[0].name
  role     = "roles/cloudtasks.enqueuer"
  member   = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_cloud_tasks_queue_iam_member" "backend_document_transform_enqueuer_dammam" {
  count    = var.enable_dammam_stack ? 1 : 0
  project  = var.project_id
  location = google_cloud_tasks_queue.document_transform_dammam[0].location
  name     = google_cloud_tasks_queue.document_transform_dammam[0].name
  role     = "roles/cloudtasks.enqueuer"
  member   = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_cloud_tasks_queue_iam_member" "github_actions_queue_viewer_dammam" {
  count    = var.enable_dammam_stack ? 1 : 0
  project  = var.project_id
  location = google_cloud_tasks_queue.smart_create_dammam[0].location
  name     = google_cloud_tasks_queue.smart_create_dammam[0].name
  role     = "roles/cloudtasks.viewer"
  member   = "serviceAccount:${google_service_account.github_actions.email}"
}

resource "google_cloud_tasks_queue_iam_member" "github_actions_document_transform_queue_viewer_dammam" {
  count    = var.enable_dammam_stack ? 1 : 0
  project  = var.project_id
  location = google_cloud_tasks_queue.document_transform_dammam[0].location
  name     = google_cloud_tasks_queue.document_transform_dammam[0].name
  role     = "roles/cloudtasks.viewer"
  member   = "serviceAccount:${google_service_account.github_actions.email}"
}
