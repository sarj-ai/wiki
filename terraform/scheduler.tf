# Cloud Scheduler jobs (replace APScheduler)

resource "google_cloud_scheduler_job" "renew_webhooks" {
  name             = "sarj-docs-renew-webhooks"
  description      = "Renew expiring Drive webhook channels"
  schedule         = "0 */6 * * *" # Every 6 hours
  time_zone        = "Asia/Riyadh"
  attempt_deadline = "300s"
  region           = "me-central2"

  retry_config {
    retry_count          = 3
    min_backoff_duration = "10s"
    max_backoff_duration = "120s"
    max_doublings        = 3
  }

  http_target {
    http_method = "POST"
    uri         = "${google_cloud_run_v2_service.backend.uri}/internal/renew-webhooks"

    oidc_token {
      service_account_email = google_service_account.scheduler.email
      audience              = google_cloud_run_v2_service.backend.uri
    }
  }

  depends_on = [google_project_service.apis["cloudscheduler.googleapis.com"]]
}

resource "google_cloud_scheduler_job" "sync_workspaces" {
  name             = "sarj-docs-sync-workspaces"
  description      = "Sync Google Groups, folder permissions, and user memberships"
  schedule         = "*/15 * * * *" # Every 15 minutes
  time_zone        = "Asia/Riyadh"
  attempt_deadline = "600s"
  region           = "me-central2"

  retry_config {
    retry_count          = 2
    min_backoff_duration = "30s"
    max_backoff_duration = "300s"
    max_doublings        = 2
  }

  http_target {
    http_method = "POST"
    uri         = "${google_cloud_run_v2_service.backend.uri}/internal/sync-workspaces"

    oidc_token {
      service_account_email = google_service_account.scheduler.email
      audience              = google_cloud_run_v2_service.backend.uri
    }
  }

  depends_on = [google_project_service.apis["cloudscheduler.googleapis.com"]]
}
