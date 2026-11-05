resource "google_cloud_scheduler_job" "document_transform_reconcile_dammam" {
  count            = var.enable_dammam_stack ? 1 : 0
  name             = "document-transform-reconcile-dammam"
  description      = "Recover up to 50 validated pending document-transform dispatches per run"
  project          = var.project_id
  region           = var.dammam_region
  schedule         = "*/5 * * * *"
  time_zone        = "Etc/UTC"
  attempt_deadline = "120s"
  paused           = var.maintenance_mode

  retry_config {
    retry_count          = 0
    max_retry_duration   = "0s"
    min_backoff_duration = "5s"
    max_backoff_duration = "60s"
    max_doublings        = 5
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.backend_domain}/internal/tasks/document-transform/reconcile"
    body        = base64encode("{}")
    headers = {
      "Content-Type" = "application/json"
    }

    oidc_token {
      service_account_email = google_service_account.cloud_tasks_invoker.email
      audience              = "https://${var.backend_domain}/internal/tasks/document-transform"
    }
  }

  depends_on = [
    google_project_service.apis["cloudscheduler.googleapis.com"],
    google_cloud_run_v2_service.backend_dammam,
    google_cloud_tasks_queue_iam_member.backend_document_transform_enqueuer_dammam,
    google_service_account_iam_member.backend_acts_as_task_invoker,
  ]
}
