locals {
  active_database_name = google_sql_database_instance.dammam[0].name
}


resource "google_sql_database" "sarj_wiki" {
  name     = "sarj_wiki"
  instance = google_sql_database_instance.dammam[0].name

  lifecycle {
    prevent_destroy = true
  }
}

resource "random_password" "wiki_db" {
  length  = 64
  special = false
}

resource "google_sql_user" "wiki_app" {
  name     = "sarj_wiki_app"
  instance = google_sql_database_instance.dammam[0].name
  password = random_password.wiki_db.result
}

resource "google_sql_user" "wiki_app_iam" {
  name     = trimsuffix(google_service_account.backend.email, ".gserviceaccount.com")
  instance = google_sql_database_instance.dammam[0].name
  type     = "CLOUD_IAM_SERVICE_ACCOUNT"
}

resource "google_sql_database_instance" "dammam" {
  count                = var.enable_dammam_stack ? 1 : 0
  name                 = "sarj-docs-db-dammam"
  database_version     = "POSTGRES_18"
  region               = var.dammam_region
  master_instance_name = null

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

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      location                       = var.dammam_region
      start_time                     = "02:00"
      transaction_log_retention_days = 7

      backup_retention_settings {
        retained_backups = 14
      }
    }

    deletion_protection_enabled = true

    maintenance_window {
      day          = 7
      hour         = 3
      update_track = "stable"
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
