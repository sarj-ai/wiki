locals {
  dammam_image = coalesce(
    var.dammam_image,
    "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.sarj_docs.repository_id}/backend:latest",
  )
  active_backend_service_name = var.serve_dammam ? google_cloud_run_v2_service.backend_dammam[0].name : google_cloud_run_v2_service.backend.name
  active_database_name        = var.serve_dammam ? google_sql_database_instance.dammam[0].name : google_sql_database_instance.main.name
}

resource "google_storage_bucket" "terraform_state_dammam" {
  count                       = var.enable_dammam_stack ? 1 : 0
  name                        = "sarj-docs-terraform-state-dammam"
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

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_storage_bucket_iam_member" "terraform_state_dammam_ci" {
  count  = var.enable_dammam_stack ? 1 : 0
  bucket = google_storage_bucket.terraform_state_dammam[0].name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.github_actions.email}"
}

resource "google_compute_subnetwork" "dammam" {
  count         = var.enable_dammam_stack ? 1 : 0
  name          = "sarj-docs-subnet-dammam"
  ip_cidr_range = "10.0.2.0/24"
  region        = var.dammam_region
  network       = google_compute_network.vpc.id
}

resource "google_vpc_access_connector" "dammam" {
  count         = var.enable_dammam_stack ? 1 : 0
  name          = "sarj-docs-connector-dmm"
  region        = var.dammam_region
  ip_cidr_range = "10.8.2.0/28"
  network       = google_compute_network.vpc.id
  min_instances = 2
  max_instances = 3

  depends_on = [google_project_service.apis["vpcaccess.googleapis.com"]]
}

resource "google_artifact_registry_repository" "dammam" {
  count         = var.enable_dammam_stack ? 1 : 0
  repository_id = "sarj-docs-dammam"
  location      = var.dammam_region
  format        = "DOCKER"
  description   = "Saudi container images for Sarj Wiki"

  docker_config {
    immutable_tags = true
  }

  cleanup_policy_dry_run = false

  cleanup_policies {
    id     = "delete-untagged"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "2592000s"
    }
  }

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_sql_database_instance" "dammam" {
  count                = var.enable_dammam_stack ? 1 : 0
  name                 = "sarj-docs-db-dammam"
  database_version     = "POSTGRES_18"
  region               = var.dammam_region
  master_instance_name = var.dammam_database_promoted ? null : google_sql_database_instance.main.name

  settings {
    tier              = var.db_tier
    edition           = "ENTERPRISE"
    availability_type = "ZONAL"
    disk_size         = 10
    disk_autoresize   = true

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.vpc.id
      ssl_mode        = "ENCRYPTED_ONLY"
    }

    dynamic "backup_configuration" {
      for_each = var.dammam_database_promoted ? [true] : []
      content {
        enabled                        = true
        point_in_time_recovery_enabled = true
        location                       = var.dammam_region
        start_time                     = "02:00"
        transaction_log_retention_days = 7

        backup_retention_settings {
          retained_backups = 14
        }
      }
    }

    deletion_protection_enabled = true

    dynamic "maintenance_window" {
      for_each = var.dammam_database_promoted ? [true] : []
      content {
        day          = 7
        hour         = 3
        update_track = "stable"
      }
    }

    database_flags {
      name  = "max_connections"
      value = "25"
    }
    database_flags {
      name  = "shared_buffers"
      value = "32768"
    }
    database_flags {
      name  = "work_mem"
      value = "2048"
    }
    database_flags {
      name  = "maintenance_work_mem"
      value = "32768"
    }
    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }
    database_flags {
      name  = "cloudsql.enable_pgaudit"
      value = "on"
    }
    database_flags {
      name  = "log_connections"
      value = "on"
    }
    database_flags {
      name  = "log_disconnections"
      value = "on"
    }
    database_flags {
      name  = "pgaudit.log"
      value = "ddl,role"
    }
  }

  deletion_protection = true

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [
    google_service_networking_connection.private_vpc,
    google_project_service.apis["sqladmin.googleapis.com"],
  ]
}

resource "google_cloud_tasks_queue" "smart_create_dammam" {
  count    = var.enable_dammam_stack ? 1 : 0
  name     = "smart-create-dammam"
  location = var.dammam_region

  rate_limits {
    max_dispatches_per_second = 1
    max_concurrent_dispatches = 3
  }

  retry_config {
    max_attempts  = 10
    min_backoff   = "5s"
    max_backoff   = "300s"
    max_doublings = 5
  }

  stackdriver_logging_config {
    sampling_ratio = 1
  }
}

resource "google_cloud_tasks_queue_iam_member" "backend_enqueuer_dammam" {
  count    = var.enable_dammam_stack ? 1 : 0
  project  = var.project_id
  location = google_cloud_tasks_queue.smart_create_dammam[0].location
  name     = google_cloud_tasks_queue.smart_create_dammam[0].name
  role     = "roles/cloudtasks.enqueuer"
  member   = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_cloud_tasks_queue_iam_member" "github_actions_queue_viewer_dammam" {
  count    = var.enable_dammam_stack ? 1 : 0
  project  = var.project_id
  location = google_cloud_tasks_queue.smart_create_dammam[0].location
  name     = google_cloud_tasks_queue.smart_create_dammam[0].name
  role     = "roles/cloudtasks.viewer"
  member   = "serviceAccount:${google_service_account.github_actions.email}"
}

resource "google_cloud_run_v2_service" "backend_dammam" {
  count               = var.enable_dammam_stack ? 1 : 0
  name                = "sarj-docs-backend-dammam"
  location            = var.dammam_region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  deletion_protection = true

  lifecycle {
    ignore_changes = [client, client_version]
  }

  template {
    service_account = google_service_account.backend.email

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
        for_each = {
          ALLOWED_DOMAIN                      = "sarj.ai"
          COOKIE_DOMAIN                       = "sarj.ai"
          FRONTEND_URL                        = var.frontend_url
          GOOGLE_REDIRECT_URI                 = "https://${var.backend_domain}/auth/callback"
          DB_INSTANCE_CONNECTION_NAME         = var.dammam_database_promoted ? google_sql_database_instance.dammam[0].connection_name : google_sql_database_instance.main.connection_name
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
          LOG_JSON                            = "true"
          MAINTENANCE_MODE                    = tostring(var.maintenance_mode)
        }
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
    }
  }

  depends_on = [
    google_project_iam_member.backend_cloudsql,
    google_secret_manager_secret_iam_member.backend_access,
    google_secret_manager_secret_iam_member.backend_cache_key_access,
    google_cloud_tasks_queue_iam_member.backend_enqueuer_dammam,
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

resource "google_cloud_run_v2_job" "backend_migrations_dammam" {
  count               = var.enable_dammam_stack ? 1 : 0
  name                = "sarj-docs-backend-migrations-dammam"
  location            = var.dammam_region
  deletion_protection = true

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
          value = var.dammam_database_promoted ? google_sql_database_instance.dammam[0].connection_name : google_sql_database_instance.main.connection_name
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

resource "google_compute_region_network_endpoint_group" "backend_dammam" {
  count                 = var.enable_dammam_stack ? 1 : 0
  name                  = "sarj-docs-backend-neg-dammam"
  region                = var.dammam_region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = google_cloud_run_v2_service.backend_dammam[0].name
  }
}

resource "google_compute_backend_service" "backend_dammam" {
  count                 = var.enable_dammam_stack ? 1 : 0
  name                  = "sarj-docs-backend-service-dammam"
  protocol              = "HTTPS"
  timeout_sec           = 30
  load_balancing_scheme = "EXTERNAL_MANAGED"
  enable_cdn            = false
  edge_security_policy  = google_compute_security_policy.edge_armor.id
  security_policy       = google_compute_security_policy.backend_armor.id

  backend {
    group = google_compute_region_network_endpoint_group.backend_dammam[0].id
  }

  log_config {
    enable      = true
    sample_rate = 1.0
  }
}
