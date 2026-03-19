# Cloud Run backend service

resource "google_cloud_run_v2_service" "backend" {
  name     = "sarj-docs-backend"
  location = var.region

  # CI/CD manages the container image — don't let Terraform overwrite it
  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }

  template {
    service_account = google_service_account.backend.email

    scaling {
      min_instance_count = 1
      max_instance_count = 1
    }

    vpc_access {
      connector = google_vpc_access_connector.connector.id
      egress    = "ALL_TRAFFIC"
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/sarj-docs/backend:latest"

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      # Health check
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

      # Non-sensitive env vars
      env {
        name  = "ALLOWED_DOMAIN"
        value = "sarj.ai"
      }
      env {
        name  = "FRONTEND_URL"
        value = var.frontend_url
      }
      env {
        name  = "GOOGLE_REDIRECT_URI"
        value = "https://${var.backend_domain}/auth/callback"
      }
      env {
        name  = "WEBHOOK_BASE_URL"
        value = "https://${var.backend_domain}"
      }
      env {
        name  = "DRIVE_ROOT_FOLDER_ID"
        value = var.drive_root_folder_id
      }
      env {
        name  = "ADMIN_IMPERSONATE_EMAIL"
        value = var.admin_impersonate_email
      }
      env {
        name  = "SERVICE_ACCOUNT_KEY_PATH"
        value = "/secrets/service-account-key/latest"
      }
      env {
        name  = "CLOUD_SCHEDULER_SA_EMAIL"
        value = google_service_account.scheduler.email
      }

      # Sensitive env vars from Secret Manager
      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.secrets["db-url"].secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "SECRET_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.secrets["secret-key"].secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "FERNET_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.secrets["fernet-key"].secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "GOOGLE_CLIENT_ID"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.secrets["google-client-id"].secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "GOOGLE_CLIENT_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.secrets["google-client-secret"].secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "DRIVE_WEBHOOK_TOKEN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.secrets["webhook-token"].secret_id
            version = "latest"
          }
        }
      }

      # Mount service account key as a file
      volume_mounts {
        name       = "sa-key"
        mount_path = "/secrets/service-account-key"
      }
    }

    volumes {
      name = "sa-key"
      secret {
        secret = google_secret_manager_secret.secrets["service-account-key"].secret_id
        items {
          version = "latest"
          path    = "latest"
        }
      }
    }
  }

  depends_on = [
    google_project_service.apis["run.googleapis.com"],
    google_secret_manager_secret_version.secrets,
    google_secret_manager_secret_iam_member.backend_access,
  ]
}
