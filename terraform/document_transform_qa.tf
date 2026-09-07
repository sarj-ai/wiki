locals {
  document_transform_qa_enabled = var.enable_dammam_stack && var.enable_document_transform_qa_job
}

resource "google_service_account" "document_transform_qa" {
  count        = local.document_transform_qa_enabled ? 1 : 0
  account_id   = "sarj-wiki-transform-qa"
  display_name = "Sarj Wiki document-transform QA job"

  depends_on = [google_project_service.apis["iam.googleapis.com"]]
}

resource "google_project_iam_member" "document_transform_qa_cloudsql_client" {
  count   = local.document_transform_qa_enabled ? 1 : 0
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.document_transform_qa[0].email}"
}

resource "google_project_iam_member" "document_transform_qa_cloudsql_user" {
  count   = local.document_transform_qa_enabled ? 1 : 0
  project = var.project_id
  role    = "roles/cloudsql.instanceUser"
  member  = "serviceAccount:${google_service_account.document_transform_qa[0].email}"
}

resource "google_project_iam_member" "document_transform_qa_vertex" {
  count   = local.document_transform_qa_enabled ? 1 : 0
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.document_transform_qa[0].email}"
}

resource "google_sql_user" "document_transform_qa" {
  count    = local.document_transform_qa_enabled ? 1 : 0
  name     = trimsuffix(google_service_account.document_transform_qa[0].email, ".gserviceaccount.com")
  instance = google_sql_database_instance.dammam[0].name
  type     = "CLOUD_IAM_SERVICE_ACCOUNT"
}

resource "google_storage_bucket" "document_transform_qa" {
  count                       = local.document_transform_qa_enabled ? 1 : 0
  name                        = "sarj-wiki-document-transform-qa-${var.project_id}"
  location                    = upper(var.dammam_region)
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning {
    enabled = true
  }

  soft_delete_policy {
    retention_duration_seconds = 604800
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age = 30
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_storage_bucket_iam_member" "document_transform_qa_objects" {
  count  = local.document_transform_qa_enabled ? 1 : 0
  bucket = google_storage_bucket.document_transform_qa[0].name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.document_transform_qa[0].email}"
}

resource "google_secret_manager_secret_iam_member" "document_transform_qa_existing_secrets" {
  for_each = local.document_transform_qa_enabled ? toset([
    "secret-key",
    "fernet-key",
    "google-client-id",
    "google-client-secret",
  ]) : toset([])

  secret_id = google_secret_manager_secret.secrets[each.value].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.document_transform_qa[0].email}"
}

resource "google_cloud_run_v2_job" "document_transform_qa" {
  count               = local.document_transform_qa_enabled ? 1 : 0
  name                = "sarj-wiki-document-transform-qa"
  location            = var.dammam_region
  deletion_protection = true

  template {
    parallelism = 1
    task_count  = 1
    template {
      service_account = google_service_account.document_transform_qa[0].email
      max_retries     = 0
      timeout         = "7200s"

      vpc_access {
        connector = google_vpc_access_connector.dammam[0].id
        egress    = "PRIVATE_RANGES_ONLY"
      }

      volumes {
        name = "qa-evidence"
        gcs {
          bucket        = google_storage_bucket.document_transform_qa[0].name
          read_only     = false
          mount_options = ["uid=1001", "gid=1001", "file-mode=600", "dir-mode=700"]
        }
      }

      containers {
        image = var.document_transform_qa_image

        resources {
          limits = { cpu = "2", memory = "2Gi" }
        }

        volume_mounts {
          name       = "qa-evidence"
          mount_path = "/var/lib/wiki-qa"
        }

        env {
          name  = "DB_INSTANCE_CONNECTION_NAME"
          value = google_sql_database_instance.dammam[0].connection_name
        }
        env {
          name  = "DB_NAME"
          value = google_sql_database.sarj_wiki.name
        }
        env {
          name  = "DB_IAM_USER"
          value = trimsuffix(google_service_account.document_transform_qa[0].email, ".gserviceaccount.com")
        }
        env {
          name  = "GOOGLE_CLOUD_PROJECT"
          value = var.project_id
        }
        env {
          name  = "WIKI_QA_DURABLE_ARTIFACT_ROOT"
          value = "/var/lib/wiki-qa"
        }

        dynamic "env" {
          for_each = {
            SECRET_KEY           = "secret-key"
            FERNET_KEY           = "fernet-key"
            GOOGLE_CLIENT_ID     = "google-client-id"
            GOOGLE_CLIENT_SECRET = "google-client-secret"
          }
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = google_secret_manager_secret.secrets[env.value].secret_id
                version = "latest"
              }
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_project_iam_member.document_transform_qa_cloudsql_client,
    google_project_iam_member.document_transform_qa_cloudsql_user,
    google_project_iam_member.document_transform_qa_vertex,
    google_secret_manager_secret_iam_member.document_transform_qa_existing_secrets,
    google_storage_bucket_iam_member.document_transform_qa_objects,
  ]
}

resource "google_cloud_run_v2_job_iam_member" "document_transform_qa_invoker" {
  count    = local.document_transform_qa_enabled ? 1 : 0
  project  = var.project_id
  location = var.dammam_region
  name     = google_cloud_run_v2_job.document_transform_qa[0].name
  role     = "roles/run.invoker"
  member   = var.document_transform_qa_invoker_member
}
