locals {
  model_batch_event_url = "https://${var.backend_domain}/internal/model-batches/events"
}

data "google_project" "current" {
  project_id = var.project_id
}

data "google_storage_project_service_account" "current" {
  project = var.project_id
}

resource "google_storage_bucket" "model_batches_dammam" {
  count                       = var.enable_dammam_stack ? 1 : 0
  name                        = "sarj-docs-model-batches-dammam"
  location                    = upper(var.dammam_region)
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  lifecycle {
    prevent_destroy = true
  }

  lifecycle_rule {
    action { type = "Delete" }
    condition { age = 8 }
  }
}

resource "google_storage_bucket_iam_member" "model_batches_backend" {
  count  = var.enable_dammam_stack ? 1 : 0
  bucket = google_storage_bucket.model_batches_dammam[0].name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_storage_bucket_iam_member" "model_batches_vertex_ai" {
  for_each = var.enable_dammam_stack ? toset(["roles/storage.objectViewer", "roles/storage.objectCreator"]) : toset([])
  bucket   = google_storage_bucket.model_batches_dammam[0].name
  role     = each.value
  member   = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-aiplatform.iam.gserviceaccount.com"

  depends_on = [google_project_service.apis["aiplatform.googleapis.com"]]
}

resource "google_pubsub_topic" "model_batch_outputs_dammam" {
  count = var.enable_dammam_stack ? 1 : 0
  name  = "model-batch-outputs-dammam"

  depends_on = [google_project_service.apis["pubsub.googleapis.com"]]
}

resource "google_pubsub_topic_iam_member" "model_batch_outputs_gcs_publisher" {
  count  = var.enable_dammam_stack ? 1 : 0
  topic  = google_pubsub_topic.model_batch_outputs_dammam[0].id
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${data.google_storage_project_service_account.current.email_address}"
}

resource "google_storage_notification" "model_batch_outputs_dammam" {
  count              = var.enable_dammam_stack ? 1 : 0
  bucket             = google_storage_bucket.model_batches_dammam[0].name
  topic              = google_pubsub_topic.model_batch_outputs_dammam[0].id
  payload_format     = "JSON_API_V1"
  event_types        = ["OBJECT_FINALIZE"]
  object_name_prefix = "model-batches/"

  depends_on = [google_pubsub_topic_iam_member.model_batch_outputs_gcs_publisher]
}

resource "google_service_account_iam_member" "pubsub_mints_task_invoker_tokens" {
  service_account_id = google_service_account.cloud_tasks_invoker.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-pubsub.iam.gserviceaccount.com"

  depends_on = [google_project_service.apis["pubsub.googleapis.com"]]
}

resource "google_pubsub_subscription" "model_batch_events_dammam" {
  count                      = var.enable_dammam_stack ? 1 : 0
  name                       = "model-batch-events-dammam"
  topic                      = google_pubsub_topic.model_batch_outputs_dammam[0].id
  ack_deadline_seconds       = 60
  message_retention_duration = "86400s"

  expiration_policy {
    ttl = ""
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  push_config {
    push_endpoint = local.model_batch_event_url

    oidc_token {
      service_account_email = google_service_account.cloud_tasks_invoker.email
      audience              = local.model_batch_event_url
    }
  }

  depends_on = [
    google_cloud_run_v2_service.backend_dammam,
    google_service_account_iam_member.pubsub_mints_task_invoker_tokens,
  ]
}

resource "google_cloud_scheduler_job" "model_batch_reconcile_dammam" {
  count            = var.enable_dammam_stack ? 1 : 0
  name             = "model-batch-reconcile-dammam"
  description      = "Reconcile submitted model batches whose output notifications were missed"
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
    uri         = "https://${var.backend_domain}/internal/model-batches/reconcile"
    body        = base64encode("{}")
    headers = {
      "Content-Type" = "application/json"
    }

    oidc_token {
      service_account_email = google_service_account.cloud_tasks_invoker.email
      audience              = local.model_batch_event_url
    }
  }

  depends_on = [
    google_project_service.apis["cloudscheduler.googleapis.com"],
    google_cloud_run_v2_service.backend_dammam,
    google_storage_bucket_iam_member.model_batches_backend,
  ]
}
