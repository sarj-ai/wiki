# Monitoring and alerting

# Notification channel
resource "google_monitoring_notification_channel" "email" {
  display_name = "Sarj Docs Alerts"
  type         = "email"

  labels = {
    email_address = var.alert_email
  }

  depends_on = [google_project_service.apis["monitoring.googleapis.com"]]
}

# Uptime check — hits /health every 5 minutes
resource "google_monitoring_uptime_check_config" "backend_health" {
  display_name = "Sarj Docs Backend Health"
  timeout      = "10s"
  period       = "300s"

  http_check {
    path         = "/health"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = var.backend_domain
    }
  }

  depends_on = [google_project_service.apis["monitoring.googleapis.com"]]
}

# Alert: backend is down
resource "google_monitoring_alert_policy" "uptime_failure" {
  display_name = "Backend Down"
  combiner     = "OR"

  conditions {
    display_name = "Health check failing"

    condition_threshold {
      filter          = "resource.type = \"uptime_url\" AND metric.type = \"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.label.check_id = \"${google_monitoring_uptime_check_config.backend_health.uptime_check_id}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "300s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]

  alert_strategy {
    auto_close = "1800s"
  }
}

# Alert: high 5xx error rate
resource "google_monitoring_alert_policy" "backend_errors" {
  display_name = "Backend 5xx Errors"
  combiner     = "OR"

  conditions {
    display_name = "5xx error rate elevated"

    condition_threshold {
      filter = <<-EOT
        resource.type = "cloud_run_revision"
        AND resource.labels.service_name = "${google_cloud_run_v2_service.backend.name}"
        AND metric.type = "run.googleapis.com/request_count"
        AND metric.labels.response_code_class = "5xx"
      EOT

      comparison      = "COMPARISON_GT"
      threshold_value = 5
      duration        = "300s"

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_RATE"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]
}

# Alert: Cloud SQL CPU > 80%
resource "google_monitoring_alert_policy" "db_cpu" {
  display_name = "Database CPU > 80%"
  combiner     = "OR"

  conditions {
    display_name = "High CPU utilization"

    condition_threshold {
      filter = <<-EOT
        resource.type = "cloudsql_database"
        AND resource.labels.database_id = "${var.project_id}:${google_sql_database_instance.main.name}"
        AND metric.type = "cloudsql.googleapis.com/database/cpu/utilization"
      EOT

      comparison      = "COMPARISON_GT"
      threshold_value = 0.8
      duration        = "600s"

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_MEAN"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]
}

# Alert: Cloud SQL memory > 85%
resource "google_monitoring_alert_policy" "db_memory" {
  display_name = "Database Memory > 85%"
  combiner     = "OR"

  conditions {
    display_name = "High memory utilization"

    condition_threshold {
      filter = <<-EOT
        resource.type = "cloudsql_database"
        AND resource.labels.database_id = "${var.project_id}:${google_sql_database_instance.main.name}"
        AND metric.type = "cloudsql.googleapis.com/database/memory/utilization"
      EOT

      comparison      = "COMPARISON_GT"
      threshold_value = 0.85
      duration        = "600s"

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_MEAN"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]
}

# Alert: Cloud Scheduler job failures
resource "google_monitoring_alert_policy" "scheduler_failures" {
  display_name = "Scheduler Job Failed"
  combiner     = "OR"

  conditions {
    display_name = "Cron job failed"

    condition_threshold {
      filter = <<-EOT
        resource.type = "cloud_scheduler_job"
        AND metric.type = "cloudscheduler.googleapis.com/job/attempt_count"
        AND metric.labels.status != "SUCCESSFUL"
      EOT

      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"

      aggregations {
        alignment_period   = "900s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]
}
