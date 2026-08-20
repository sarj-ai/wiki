resource "google_cloud_run_v2_job" "backend_migrations" {
  name                = "sarj-docs-backend-migrations"
  location            = var.region
  deletion_protection = true

  lifecycle {
    ignore_changes = [template[0].template[0].containers[0].image]
  }

  template {
    parallelism = 1
    task_count  = 1

    template {
      service_account = google_service_account.backend.email
      max_retries     = 0
      timeout         = "600s"

      vpc_access {
        connector = google_vpc_access_connector.connector.id
        egress    = "ALL_TRAFFIC"
      }

      containers {
        image   = "${var.region}-docker.pkg.dev/${var.project_id}/sarj-docs/backend:latest"
        command = [".venv/bin/alembic"]
        args    = ["upgrade", "head"]

        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }

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
      }
    }
  }

  depends_on = [
    google_project_service.apis["run.googleapis.com"],
    google_project_iam_member.backend_cloudsql,
    google_project_iam_member.backend_cloudsql_instance_user,
  ]
}
