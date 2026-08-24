resource "google_monitoring_notification_channel" "email" {
  display_name = "Sarj Wiki Alerts"
  type         = "email"

  labels = {
    email_address = var.alert_email
  }

  depends_on = [google_project_service.apis["monitoring.googleapis.com"]]
}

resource "google_monitoring_uptime_check_config" "backend_health" {
  display_name = "Sarj Wiki Backend Health"
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

resource "google_monitoring_alert_policy" "backend_errors" {
  display_name = "Backend 5xx Errors"
  combiner     = "OR"

  conditions {
    display_name = "5xx error rate elevated"

    condition_threshold {
      filter = <<-EOT
        resource.type = "cloud_run_revision"
        AND resource.labels.service_name = "${local.active_backend_service_name}"
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

resource "google_monitoring_alert_policy" "db_cpu" {
  display_name = "Database CPU > 80%"
  combiner     = "OR"

  conditions {
    display_name = "High CPU utilization"

    condition_threshold {
      filter = <<-EOT
        resource.type = "cloudsql_database"
        AND resource.labels.database_id = "${var.project_id}:${local.active_database_name}"
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

resource "google_monitoring_alert_policy" "db_memory" {
  display_name = "Database Memory > 85%"
  combiner     = "OR"

  conditions {
    display_name = "High memory utilization"

    condition_threshold {
      filter = <<-EOT
        resource.type = "cloudsql_database"
        AND resource.labels.database_id = "${var.project_id}:${local.active_database_name}"
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
