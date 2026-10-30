# VPC for private connectivity between Cloud Run and Cloud SQL

resource "google_compute_network" "vpc" {
  name                    = "sarj-docs-vpc"
  auto_create_subnetworks = false

  depends_on = [google_project_service.apis["compute.googleapis.com"]]
}

# Private services access for Cloud SQL
resource "google_compute_global_address" "private_ip_range" {
  name          = "sarj-docs-private-ip"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "private_vpc" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip_range.name]

  depends_on = [google_project_service.apis["servicenetworking.googleapis.com"]]
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
