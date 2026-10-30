
resource "google_project_iam_audit_config" "all_services" {
  project = var.project_id
  service = "allServices"

  audit_log_config {
    log_type = "ADMIN_READ"
  }
  audit_log_config {
    log_type = "DATA_READ"
  }
  audit_log_config {
    log_type = "DATA_WRITE"
  }
}

resource "google_monitoring_alert_policy" "secret_access_anomaly" {
  display_name = "Secret read by non-backend principal"
  combiner     = "OR"

  conditions {
    display_name = "AccessSecretVersion by unexpected principal"
    condition_matched_log {
      filter = <<-EOT
        protoPayload.methodName="google.cloud.secretmanager.v1.SecretManagerService.AccessSecretVersion"
        AND NOT protoPayload.authenticationInfo.principalEmail="${google_service_account.backend.email}"
      EOT
    }
  }

  alert_strategy {
    notification_rate_limit {
      period = "300s"
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]
  depends_on            = [google_project_iam_audit_config.all_services]
}

resource "google_monitoring_alert_policy" "iam_policy_change" {
  display_name = "IAM policy changed"
  combiner     = "OR"

  conditions {
    display_name = "SetIamPolicy called"
    condition_matched_log {
      filter = "protoPayload.methodName:\"SetIamPolicy\""
    }
  }

  alert_strategy {
    notification_rate_limit {
      period = "300s"
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]
}

resource "google_monitoring_alert_policy" "sa_key_created" {
  display_name = "Service account key created"
  combiner     = "OR"

  conditions {
    display_name = "CreateServiceAccountKey called"
    condition_matched_log {
      filter = "protoPayload.methodName=\"google.iam.admin.v1.CreateServiceAccountKey\""
    }
  }

  alert_strategy {
    notification_rate_limit {
      period = "3600s"
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]
}

resource "google_logging_metric" "auth_failures" {
  name   = "backend_auth_failures"
  filter = <<-EOT
    resource.type="cloud_run_revision"
    AND resource.labels.service_name="${local.active_backend_service_name}"
    AND (httpRequest.status=401 OR httpRequest.status=403)
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

resource "google_monitoring_alert_policy" "auth_failure_spike" {
  display_name = "Backend auth-failure spike (401/403)"
  combiner     = "OR"

  conditions {
    display_name = "401/403 rate elevated"

    condition_threshold {
      filter = <<-EOT
        resource.type="cloud_run_revision"
        AND metric.type="logging.googleapis.com/user/${google_logging_metric.auth_failures.name}"
      EOT

      comparison      = "COMPARISON_GT"
      threshold_value = 20
      duration        = "300s"

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]
}
