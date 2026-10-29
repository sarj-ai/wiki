resource "google_cloud_run_v2_job" "backend_migrations_dammam" {
  count               = var.enable_dammam_stack ? 1 : 0
  name                = "sarj-docs-backend-migrations-dammam"
  location            = var.dammam_region
  deletion_protection = true

  lifecycle {
    ignore_changes = [client, client_version, template[0].template[0].containers[0].image]
  }

  template {
    parallelism = 1
    task_count  = 1
    template {
      service_account = google_service_account.backend.email
      max_retries     = 0
      timeout         = "600s"
      vpc_access {
        connector = google_vpc_access_connector.dammam[0].id
        egress    = "PRIVATE_RANGES_ONLY"
      }
      containers {
        image   = local.dammam_image
        command = [".venv/bin/alembic"]
        args    = ["upgrade", "head"]
        resources {
          limits = { cpu = "1", memory = "512Mi" }
        }
        env {
          name  = "DB_INSTANCE_CONNECTION_NAME"
          value = google_sql_database_instance.dammam[0].connection_name
        }
        env {
          name  = "DB_NAME"
          value = "sarj_wiki"
        }
        env {
          name  = "DB_IAM_USER"
          value = trimsuffix(google_service_account.backend.email, ".gserviceaccount.com")
        }
      }
    }
  }
}

resource "google_cloud_run_v2_job_iam_member" "github_actions_migration_invoker_dammam" {
  count    = var.enable_dammam_stack ? 1 : 0
  project  = var.project_id
  location = var.dammam_region
  name     = google_cloud_run_v2_job.backend_migrations_dammam[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.github_actions.email}"
}
