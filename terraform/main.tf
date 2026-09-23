provider "google" {
  project = var.project_id
  region  = var.dammam_region
}

# Enable required GCP APIs
resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "sqladmin.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "monitoring.googleapis.com",
    "vpcaccess.googleapis.com",
    "compute.googleapis.com",
    "iam.googleapis.com",
    "servicenetworking.googleapis.com",
    "certificatemanager.googleapis.com",
    "securitycenter.googleapis.com",
    "aiplatform.googleapis.com",
    "cloudtasks.googleapis.com",
    "cloudscheduler.googleapis.com",
    "docs.googleapis.com",
    "cloudasset.googleapis.com",
    "storage.googleapis.com",
    "iamcredentials.googleapis.com",
  ])

  service            = each.value
  disable_on_destroy = false
}
