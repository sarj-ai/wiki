# Log-based alert policies report events, not continuous queue state.
# https://docs.cloud.google.com/logging/docs/alerting/log-based-alerts
resource "google_monitoring_alert_policy" "document_transform_queue_paused" {
  count        = var.enable_dammam_stack ? 1 : 0
  display_name = "Wiki document transformation queue paused"
  combiner     = "OR"

  conditions {
    display_name = "Document transform PauseQueue audit event"
    condition_matched_log {
      filter = <<-EOT
        protoPayload.serviceName="cloudtasks.googleapis.com"
        protoPayload.methodName="google.cloud.tasks.v2.CloudTasks.PauseQueue"
        protoPayload.resourceName="projects/${var.project_id}/locations/${var.dammam_region}/queues/${google_cloud_tasks_queue.document_transform_dammam[0].name}"
        NOT protoPayload.status.code:*
      EOT
    }
  }

  documentation {
    mime_type = "text/markdown"
    content   = "The document-transform queue was paused. Inspect the audit actor and maintenance intent, then verify current queue state and pending tasks. Do not automatically resume an intentional maintenance pause. Incident auto-close is not proof of recovery."
  }

  alert_strategy {
    notification_rate_limit {
      period = "300s"
    }
    auto_close = "1800s"
  }

  notification_channels = [google_monitoring_notification_channel.email.id]
}

resource "google_monitoring_alert_policy" "document_transform_dispatch_failed" {
  count        = var.enable_dammam_stack ? 1 : 0
  display_name = "Wiki document transformation recovery incomplete"
  combiner     = "OR"

  conditions {
    display_name = "Validated pending tasks could not be enqueued"
    condition_matched_log {
      filter = <<-EOT
        resource.type="cloud_run_revision"
        resource.labels.service_name="${google_cloud_run_v2_service.backend_dammam[0].name}"
        jsonPayload.record.message:"document transform dispatch reconciliation incomplete"
        jsonPayload.record.extra.failed>0
      EOT
    }
  }

  documentation {
    mime_type = "text/markdown"
    content   = "A bounded reconciliation pass could not dispatch some validated pending tasks. HTTP 200 from the scheduler endpoint is not proof that all dispatches succeeded. Inspect queue state, invoker configuration and aggregate failure logs; pending intents remain durable for the next scheduled pass."
  }

  alert_strategy {
    notification_rate_limit {
      period = "300s"
    }
    auto_close = "1800s"
  }

  notification_channels = [google_monitoring_notification_channel.email.id]
}
