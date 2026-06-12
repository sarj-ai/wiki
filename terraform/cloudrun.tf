# Cloud Run backend service — post-pivot Sarj Wiki layout.
#
# What the Sarj Wiki backend needs at runtime:
#   * Non-sensitive plaintext env: ALLOWED_DOMAIN, FRONTEND_URL,
#     GOOGLE_REDIRECT_URI, DB_INSTANCE_CONNECTION_NAME, DB_NAME,
#     DB_IAM_USER (DB auth is IAM — the SA is the credential)
#   * Sensitive secrets (via Secret Manager): SECRET_KEY, FERNET_KEY,
#     GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
#
# What the legacy Sarj Wiki stack needed but Sarj Wiki does NOT:
#   * DRIVE_WEBHOOK_TOKEN — no more Drive push notifications
#   * DRIVE_ROOT_FOLDER_ID — Sarj Wiki discovers drives via
#     drives.list() at runtime, no static root folder
#   * ADMIN_IMPERSONATE_EMAIL — no more Admin SDK group sync
#   * SERVICE_ACCOUNT_KEY_PATH — no service account in the read path
#   * CLOUD_SCHEDULER_SA_EMAIL — no /internal endpoints
#   * WEBHOOK_BASE_URL — no webhook registrations
#   * /secrets/service-account-key volume mount — removed

resource "google_cloud_run_v2_service" "backend" {
  name     = "sarj-docs-backend"
  location = var.region

  # Restrict ingress to the internal load balancer. Without this, the
  # auto-generated ``*.run.app`` hostname is publicly reachable and
  # bypasses the entire Cloudflare + GCLB + Cloud Armor perimeter.
  # Traffic must now arrive via ``google_compute_global_forwarding_rule.backend_https``
  # in loadbalancer.tf — direct hits to the run.app URL are rejected.
  ingress = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"

  deletion_protection = true

  # CI/CD manages the container image — don't let Terraform overwrite it
  lifecycle {
    ignore_changes = [template[0].containers[0].image]

    postcondition {
      condition     = self.ingress == "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
      error_message = "Backend must stay internal-LB only; the allUsers invoker grant is only safe behind the GCLB + Cloud Armor perimeter."
    }
  }

  template {
    service_account = google_service_account.backend.email

    scaling {
      min_instance_count = 1
      # The 2026-06 latency audit showed a single instance serializing
      # everything: a cold sidebar fans out ~22 concurrent calls and
      # multi-drive search adds a Google round-trip per drive, so
      # requests queued behind one throttled CPU (folder listings
      # measured at 2-3.5s that are ~300ms uncontended). min stays 1 so
      # the in-process caches keep their hit rate on the common path.
      max_instance_count = 3
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
          # 2 vCPU so the thread pool that wraps the blocking Google
          # API client (asyncio.to_thread) actually runs in parallel
          # with the event loop instead of timeslicing one core.
          cpu    = "2"
          memory = "1Gi"
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
        # Scopes the auth JWT cookies to the parent domain so the
        # Next.js server at wiki.sarj.ai can gate routes and prefetch
        # server-side (#44). Deliberately separate from ALLOWED_DOMAIN.
        name  = "COOKIE_DOMAIN"
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

      # Database via the Cloud SQL Python Connector with IAM database
      # authentication — none of these are secrets; the credential is
      # the service account itself.
      env {
        name  = "DB_INSTANCE_CONNECTION_NAME"
        value = google_sql_database_instance.main.connection_name
      }
      env {
        name  = "DB_NAME"
        value = google_sql_database.sarj_wiki.name
      }
      env {
        name  = "DB_IAM_USER"
        value = google_sql_user.wiki_app_iam.name
      }

      # Sensitive env vars from Secret Manager
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
    }
  }

  depends_on = [
    google_project_service.apis["run.googleapis.com"],
    google_secret_manager_secret_version.secrets,
    google_secret_manager_secret_iam_member.backend_access,
  ]
}
