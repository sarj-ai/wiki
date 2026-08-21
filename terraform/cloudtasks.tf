# Smart Create runs asynchronously through one HTTP queue targeting the existing
# backend service. SQL tasks remain the durable source of truth; Cloud Tasks is
# only the at-least-once delivery mechanism.

resource "google_cloud_tasks_queue" "smart_create" {
  name     = "smart-create"
  location = var.region

  rate_limits {
    # Bound legitimate OIDC-authenticated deliveries independently; the public
    # perimeter still protects this route from arbitrary callers.
    max_dispatches_per_second = 1
    max_concurrent_dispatches = 3
  }

  retry_config {
    # Outlive the six-minute SQL execution lease so a task is recovered even
    # when a container dies before it can explicitly release that lease.
    max_attempts  = 10
    min_backoff   = "5s"
    max_backoff   = "300s"
    max_doublings = 5
  }

  stackdriver_logging_config {
    sampling_ratio = 1
  }

  depends_on = [google_project_service.apis["cloudtasks.googleapis.com"]]
}
