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

# Independent logical database exports complement Cloud SQL PITR and on-demand
# backups. The bucket stays in Saudi Arabia and is protected from accidental
# deletion and overwrites.
resource "google_storage_bucket" "db_exports_dammam" {
  count                       = var.enable_dammam_stack ? 1 : 0
  name                        = "sarj-docs-db-exports-dammam"
  location                    = upper(var.dammam_region)
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning {
    enabled = true
  }

  soft_delete_policy {
    retention_duration_seconds = 2592000
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_storage_bucket_iam_member" "db_exports_dammam_sql" {
  count  = var.enable_dammam_stack ? 1 : 0
  bucket = google_storage_bucket.db_exports_dammam[0].name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_sql_database_instance.dammam[0].service_account_email_address}"
}

resource "google_storage_bucket" "markdown_uploads_dammam" {
  count                       = var.enable_dammam_stack ? 1 : 0
  name                        = "sarj-docs-markdown-uploads-dammam"
  location                    = upper(var.dammam_region)
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  lifecycle {
    prevent_destroy = true
  }

  cors {
    origin          = [var.frontend_url]
    method          = ["PUT"]
    response_header = ["Content-Type", "x-goog-content-length-range", "x-goog-if-generation-match"]
    max_age_seconds = 3600
  }

  lifecycle_rule {
    action { type = "Delete" }
    condition { age = 8 }
  }
}

resource "google_storage_bucket_iam_member" "markdown_uploads_backend" {
  count  = var.enable_dammam_stack ? 1 : 0
  bucket = google_storage_bucket.markdown_uploads_dammam[0].name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_service_account_iam_member" "backend_markdown_upload_signer" {
  service_account_id = google_service_account.backend.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.backend.email}"
}
