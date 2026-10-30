locals {
  dammam_image = coalesce(
    var.dammam_image,
    "${var.dammam_region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.dammam[0].repository_id}/backend:latest",
  )
  active_backend_service_name = google_cloud_run_v2_service.backend_dammam[0].name
}

resource "google_cloud_run_v2_service" "backend_dammam" {
  count               = var.enable_dammam_stack ? 1 : 0
  name                = "sarj-docs-backend-dammam"
  location            = var.dammam_region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  deletion_protection = true

  lifecycle {
    ignore_changes = [client, client_version, template[0].containers[0].image]
  }

  template {
    service_account = google_service_account.backend.email
    timeout         = "300s"

    scaling {
      min_instance_count = 1
      max_instance_count = 3
    }

    vpc_access {
      connector = google_vpc_access_connector.dammam[0].id
      egress    = "PRIVATE_RANGES_ONLY"
    }

    containers {
      image   = local.dammam_image
      command = [".venv/bin/uvicorn"]
      args    = ["app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

      ports {
        container_port = 8000
      }

      resources {
        limits = { cpu = "2", memory = "1Gi" }
      }

      startup_probe {
        http_get {
          path = "/health"
          port = 8000
        }
        initial_delay_seconds = 10
        period_seconds        = 10
        failure_threshold     = 6
        timeout_seconds       = 5
      }

      liveness_probe {
        http_get {
          path = "/health"
          port = 8000
        }
        period_seconds = 30
      }

      dynamic "env" {
        for_each = merge({
          ALLOWED_DOMAIN                      = "sarj.ai"
          COOKIE_DOMAIN                       = "sarj.ai"
          FRONTEND_URL                        = var.frontend_url
          GOOGLE_REDIRECT_URI                 = "https://${var.backend_domain}/auth/callback"
          DB_INSTANCE_CONNECTION_NAME         = google_sql_database_instance.dammam[0].connection_name
          DB_NAME                             = "sarj_wiki"
          DB_IAM_USER                         = trimsuffix(google_service_account.backend.email, ".gserviceaccount.com")
          GOOGLE_CLOUD_PROJECT                = var.project_id
          GEMINI_LOCATION                     = "global"
          GEMINI_MODEL                        = "gemini-3.5-flash-lite"
          GEMINI_REORGANIZATION_MODEL         = "gemini-3.5-flash"
          CLOUD_TASKS_QUEUE                   = google_cloud_tasks_queue.smart_create_dammam[0].name
          CLOUD_TASKS_LOCATION                = var.dammam_region
          CLOUD_TASKS_TARGET_URL              = "https://${var.backend_domain}/internal/tasks/smart-create"
          CLOUD_TASKS_INVOKER_SERVICE_ACCOUNT = google_service_account.cloud_tasks_invoker.email
          DOCUMENT_TRANSFORM_QUEUE            = google_cloud_tasks_queue.document_transform_dammam[0].name
          DOCUMENT_TRANSFORM_UPLOAD_BUCKET    = google_storage_bucket.markdown_uploads_dammam[0].name
          DOCUMENT_TRANSFORM_UPLOAD_SIGNER    = google_service_account.backend.email
          DOCUMENT_TRANSFORM_TARGET_URL       = "https://${var.backend_domain}/internal/tasks/document-transform"
          DOCUMENT_TRANSFORM_FALLBACK_MODEL   = "gemini-2.5-flash-lite"
          DOCUMENT_TRANSFORM_IN_PLACE_ENABLED = "true"
          LOG_JSON                            = "true"
          MAINTENANCE_MODE                    = tostring(var.maintenance_mode)
          MCP_NATIVE_ENABLED                  = tostring(var.enable_mcp_native)
          MCP_NATIVE_HTTPS_CALLBACKS          = jsonencode([])
          MCP_NATIVE_ISSUER                   = "https://${var.backend_domain}"
          MCP_NATIVE_LOOPBACK_PATHS           = jsonencode(["/callback"])
          }, local.document_transform_qa_enabled ? {
          DOCUMENT_TRANSFORM_QA_AUDIENCE                = "https://${var.backend_domain}/internal/document-transform-qa/exchange"
          DOCUMENT_TRANSFORM_QA_BOOTSTRAP_URL           = "https://${var.backend_domain}/internal/document-transform-qa/bootstrap"
          DOCUMENT_TRANSFORM_QA_JOB                     = google_cloud_run_v2_job.document_transform_qa[0].name
          DOCUMENT_TRANSFORM_QA_IMAGE                   = var.document_transform_qa_image
          DOCUMENT_TRANSFORM_QA_SERVICE_ACCOUNT         = google_service_account.document_transform_qa[0].email
          DOCUMENT_TRANSFORM_QA_IMAGE_DIGEST            = split("@", var.document_transform_qa_image)[1]
          DOCUMENT_TRANSFORM_QA_RELEASE_SHA             = var.document_transform_qa_release_sha
          DOCUMENT_TRANSFORM_QA_BACKEND_SERVICE_ACCOUNT = google_service_account.backend.email
          DOCUMENT_TRANSFORM_QA_OPERATOR_EMAILS         = jsonencode(var.document_transform_qa_operator_emails)
          } : {}, var.document_transform_qa_corpus_source_folder_id != null ? {
          DOCUMENT_TRANSFORM_QA_CORPUS_SOURCE_FOLDER_ID = var.document_transform_qa_corpus_source_folder_id
        } : {})
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = {
          SECRET_KEY           = google_secret_manager_secret.secrets["secret-key"].secret_id
          FERNET_KEY           = google_secret_manager_secret.secrets["fernet-key"].secret_id
          GOOGLE_CLIENT_ID     = google_secret_manager_secret.secrets["google-client-id"].secret_id
          GOOGLE_CLIENT_SECRET = google_secret_manager_secret.secrets["google-client-secret"].secret_id
        }
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }

      env {
        name = "CACHE_FERNET_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.cache_fernet_key.secret_id
            version = google_secret_manager_secret_version.cache_fernet_key.version
          }
        }
      }

      dynamic "env" {
        for_each = var.provision_mcp_native_secrets ? {
          MCP_NATIVE_FERNET_KEYS = {
            secret  = google_secret_manager_secret.mcp_native_fernet_keys[0].secret_id
            version = google_secret_manager_secret_version.mcp_native_fernet_keys[0].version
          }
          MCP_NATIVE_LOCK_KEY = {
            secret  = google_secret_manager_secret.mcp_native_lock_key[0].secret_id
            version = google_secret_manager_secret_version.mcp_native_lock_key[0].version
          }
        } : {}
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value.secret
              version = env.value.version
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_project_iam_member.backend_cloudsql,
    google_secret_manager_secret_iam_member.backend_access,
    google_secret_manager_secret_iam_member.backend_cache_key_access,
    google_secret_manager_secret_iam_member.backend_mcp_native_fernet_keys_access,
    google_secret_manager_secret_iam_member.backend_mcp_native_lock_key_access,
    google_cloud_tasks_queue_iam_member.backend_enqueuer_dammam,
    google_cloud_tasks_queue_iam_member.backend_document_transform_enqueuer_dammam,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "backend_dammam_public" {
  count    = var.enable_dammam_stack ? 1 : 0
  project  = var.project_id
  name     = google_cloud_run_v2_service.backend_dammam[0].name
  location = var.dammam_region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "tasks_dammam_invoker" {
  count    = var.enable_dammam_stack ? 1 : 0
  project  = var.project_id
  name     = google_cloud_run_v2_service.backend_dammam[0].name
  location = var.dammam_region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.cloud_tasks_invoker.email}"
}
