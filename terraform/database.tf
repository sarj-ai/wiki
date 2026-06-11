# Cloud SQL PostgreSQL instance

resource "google_sql_database_instance" "main" {
  name             = "sarj-docs-db"
  database_version = "POSTGRES_18"
  region           = var.region

  settings {
    tier              = var.db_tier
    availability_type = "ZONAL"
    disk_size         = 10
    disk_autoresize   = true

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.vpc.id
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = "02:00"
      transaction_log_retention_days = 7

      backup_retention_settings {
        retained_backups = 14
      }
    }

    deletion_protection_enabled = true

    maintenance_window {
      day          = 7 # Sunday
      hour         = 3 # 3 AM UTC
      update_track = "stable"
    }

    database_flags {
      name  = "max_connections"
      value = "25"
    }

    database_flags {
      name  = "shared_buffers"
      value = "32768" # 32 MB (in 8KB pages) — default ~128 MB is too much for f1-micro
    }

    database_flags {
      name  = "work_mem"
      value = "2048" # 2 MB — limit per-query sort/hash memory
    }

    database_flags {
      name  = "maintenance_work_mem"
      value = "32768" # 32 MB — for VACUUM, index builds
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

resource "google_sql_database" "sarj_docs" {
  name     = "sarj_docs"
  instance = google_sql_database_instance.main.name
}

resource "google_sql_user" "app" {
  name     = "sarj_docs_app"
  instance = google_sql_database_instance.main.name
  password = var.db_password
}

# Sarj Wiki — post-pivot DB + user. Lives in the same instance as
# ``sarj_docs`` so we share Cloud SQL overhead, but isolated to its
# own database and credential. ``sarj_docs`` / ``sarj_docs_app``
# stay in place untouched so a same-day rollback is just flipping the
# ``sarj-docs-db-url`` secret back to the old DSN — no data motion.
resource "google_sql_database" "sarj_wiki" {
  name     = "sarj_wiki"
  instance = google_sql_database_instance.main.name
}

resource "google_sql_user" "wiki_app" {
  name     = "sarj_wiki_app"
  instance = google_sql_database_instance.main.name
  password = var.wiki_db_password
}
