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
  # No DATABASE_URL secret: the backend authenticates to Cloud SQL as
  # its service account (IAM database auth via the Cloud SQL
  # connector), so no DSN or password exists to protect.
  secrets = {
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
