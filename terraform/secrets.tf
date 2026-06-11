# Secret Manager — all sensitive configuration consumed by the
# Sarj Wiki backend at runtime. Non-sensitive config (allowed domain,
# frontend URL, OAuth redirect URI) stays as plaintext env vars in
# cloudrun.tf. Secrets the legacy Sarj Wiki backend needed — the
# drive webhook token and the domain-wide-delegation service account
# key — were removed during the pivot to Sarj Wiki and no longer
# exist here.

resource "random_password" "secret_key" {
  length  = 64
  special = true
}

locals {
  # DATABASE_URL constructed from Cloud SQL private IP. Points at the
  # new ``sarj_wiki`` database via the ``sarj_wiki_app`` credential.
  # The legacy ``sarj_docs`` DB / ``sarj_docs_app`` user are retained
  # in the same instance for rollback only — they are not referenced
  # here.
  database_url = "postgresql+asyncpg://${google_sql_user.wiki_app.name}:${var.wiki_db_password}@${google_sql_database_instance.main.private_ip_address}:5432/${google_sql_database.sarj_wiki.name}"

  secrets = {
    "db-url"               = local.database_url
    "secret-key"           = random_password.secret_key.result
    "fernet-key"           = var.fernet_key
    "google-client-id"     = var.google_client_id
    "google-client-secret" = var.google_client_secret
  }
}

resource "google_secret_manager_secret" "secrets" {
  for_each  = local.secrets
  secret_id = "sarj-docs-${each.key}"

  replication {
    auto {}
  }

  lifecycle {
    prevent_destroy = true
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
