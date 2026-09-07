# Secret Manager — all sensitive configuration consumed by the
# Sarj Wiki backend at runtime. Non-sensitive config (allowed domain,
# frontend URL, OAuth redirect URI) stays as plaintext env vars in
# cloudrun.tf.

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

resource "google_secret_manager_secret" "cache_fernet_key" {
  secret_id = "sarj-docs-cache-fernet-key"

  replication {
    auto {}
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.apis["secretmanager.googleapis.com"]]
}

resource "google_secret_manager_secret_version" "cache_fernet_key" {
  secret                 = google_secret_manager_secret.cache_fernet_key.id
  secret_data_wo         = var.cache_fernet_key
  secret_data_wo_version = var.cache_fernet_key_generation
  deletion_policy        = "DISABLE"
}

resource "google_secret_manager_secret" "mcp_native_fernet_keys" {
  count     = var.provision_mcp_native_secrets ? 1 : 0
  secret_id = "sarj-docs-mcp-native-fernet-keys"

  replication {
    auto {}
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.apis["secretmanager.googleapis.com"]]
}

resource "google_secret_manager_secret_version" "mcp_native_fernet_keys" {
  count                  = var.provision_mcp_native_secrets ? 1 : 0
  secret                 = google_secret_manager_secret.mcp_native_fernet_keys[0].id
  secret_data_wo         = var.mcp_native_fernet_keys
  secret_data_wo_version = var.mcp_native_fernet_keys_generation
  deletion_policy        = "DISABLE"
}

resource "google_secret_manager_secret" "mcp_native_lock_key" {
  count     = var.provision_mcp_native_secrets ? 1 : 0
  secret_id = "sarj-docs-mcp-native-lock-key"

  replication {
    auto {}
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.apis["secretmanager.googleapis.com"]]
}

resource "google_secret_manager_secret_version" "mcp_native_lock_key" {
  count                  = var.provision_mcp_native_secrets ? 1 : 0
  secret                 = google_secret_manager_secret.mcp_native_lock_key[0].id
  secret_data_wo         = var.mcp_native_lock_key
  secret_data_wo_version = var.mcp_native_lock_key_generation
  deletion_policy        = "DISABLE"
}

# Grant Cloud Run SA access to read secrets
resource "google_secret_manager_secret_iam_member" "backend_access" {
  for_each  = local.secrets
  secret_id = google_secret_manager_secret.secrets[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_secret_manager_secret_iam_member" "backend_cache_key_access" {
  secret_id = google_secret_manager_secret.cache_fernet_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_secret_manager_secret_iam_member" "backend_mcp_native_fernet_keys_access" {
  count     = var.provision_mcp_native_secrets ? 1 : 0
  secret_id = google_secret_manager_secret.mcp_native_fernet_keys[0].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_secret_manager_secret_iam_member" "backend_mcp_native_lock_key_access" {
  count     = var.provision_mcp_native_secrets ? 1 : 0
  secret_id = google_secret_manager_secret.mcp_native_lock_key[0].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend.email}"
}
