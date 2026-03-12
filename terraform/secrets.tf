# Secret Manager — store all sensitive configuration

resource "random_password" "secret_key" {
  length  = 64
  special = true
}

locals {
  # DATABASE_URL constructed from Cloud SQL private IP
  database_url = "postgresql+asyncpg://${google_sql_user.app.name}:${var.db_password}@${google_sql_database_instance.main.private_ip_address}:5432/${google_sql_database.sarj_docs.name}"

  secrets = {
    "db-url"               = local.database_url
    "secret-key"           = random_password.secret_key.result
    "fernet-key"           = var.fernet_key
    "google-client-id"     = var.google_client_id
    "google-client-secret" = var.google_client_secret
    "service-account-key"  = file(var.service_account_key_path)
  }
}

resource "google_secret_manager_secret" "secrets" {
  for_each  = local.secrets
  secret_id = "sarj-docs-${each.key}"

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis["secretmanager.googleapis.com"]]
}

resource "google_secret_manager_secret_version" "secrets" {
  for_each    = local.secrets
  secret      = google_secret_manager_secret.secrets[each.key].id
  secret_data = each.value
}

# Grant Cloud Run SA access to read secrets
resource "google_secret_manager_secret_iam_member" "backend_access" {
  for_each  = local.secrets
  secret_id = google_secret_manager_secret.secrets[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend.email}"
}
