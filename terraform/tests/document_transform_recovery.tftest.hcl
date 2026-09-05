# Plan-only contract checks use the real provider. Run with an authenticated
# operator identity and review the deployment plan separately before applying.

variables {
  frontend_url         = "https://wiki.example.test"
  backend_domain       = "api-wiki.example.test"
  google_client_id     = "test-client"
  google_client_secret = "test-secret"
  fernet_key           = "test-key"
  cache_fernet_key     = "test-cache-key"
  alert_email          = "alerts@example.test"
}

run "bounded_authenticated_recovery" {
  command = plan

  assert {
    condition     = google_cloud_scheduler_job.document_transform_reconcile_dammam[0].http_target[0].uri == "https://api-wiki.example.test/internal/tasks/document-transform/reconcile"
    error_message = "Recovery must target only the authenticated reconciliation route."
  }

  assert {
    condition     = google_cloud_scheduler_job.document_transform_reconcile_dammam[0].http_target[0].oidc_token[0].audience == "https://api-wiki.example.test/internal/tasks/document-transform"
    error_message = "OIDC audience must match DOCUMENT_TRANSFORM_TARGET_URL, not the reconciliation URL."
  }

  assert {
    condition     = google_cloud_scheduler_job.document_transform_reconcile_dammam[0].http_target[0].http_method == "POST"
    error_message = "Reconciliation must use POST."
  }

  assert {
    condition     = google_cloud_scheduler_job.document_transform_reconcile_dammam[0].http_target[0].oidc_token[0].service_account_email == "sarj-wiki-task-invoker@sarj-docs.iam.gserviceaccount.com"
    error_message = "Recovery must reuse the existing task invoker identity, not the backend or Scheduler service agent."
  }

  assert {
    condition = (
      google_cloud_scheduler_job.document_transform_reconcile_dammam[0].schedule == "*/5 * * * *" &&
      google_cloud_scheduler_job.document_transform_reconcile_dammam[0].time_zone == "Etc/UTC" &&
      google_cloud_scheduler_job.document_transform_reconcile_dammam[0].attempt_deadline == "120s" &&
      google_cloud_scheduler_job.document_transform_reconcile_dammam[0].retry_config[0].retry_count == 0 &&
      google_cloud_scheduler_job.document_transform_reconcile_dammam[0].retry_config[0].max_retry_duration == "0s"
    )
    error_message = "Recovery must retain bounded frequency, deadline and no immediate retry amplification."
  }

  assert {
    condition     = google_cloud_scheduler_job.document_transform_reconcile_dammam[0].retry_config[0].max_doublings == 5
    error_message = "Recovery must use Scheduler's canonical doubling default to avoid perpetual drift; retry count and duration remain zero."
  }

  assert {
    condition     = !google_cloud_scheduler_job.document_transform_reconcile_dammam[0].paused
    error_message = "Recovery should be enabled outside maintenance mode."
  }

  assert {
    condition = (
      strcontains(google_monitoring_alert_policy.document_transform_queue_paused[0].conditions[0].condition_matched_log[0].filter, "google.cloud.tasks.v2.CloudTasks.PauseQueue") &&
      google_monitoring_alert_policy.document_transform_queue_paused[0].alert_strategy[0].notification_rate_limit[0].period == "300s"
    )
    error_message = "Queue pause events must have a bounded notification alert."
  }

  assert {
    condition     = strcontains(google_monitoring_alert_policy.document_transform_dispatch_failed[0].conditions[0].condition_matched_log[0].filter, "jsonPayload.record.extra.failed>0")
    error_message = "Partial dispatch failure must alert even when reconciliation returns HTTP 200."
  }
}

run "maintenance_pauses_recovery" {
  command = plan

  variables {
    maintenance_mode = true
  }

  assert {
    condition     = google_cloud_scheduler_job.document_transform_reconcile_dammam[0].paused
    error_message = "Maintenance mode must pause autonomous reconciliation."
  }
}
