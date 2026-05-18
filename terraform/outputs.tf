output "backend_url" {
  description = "Cloud Run backend service URL (not the public URL — that's lb_ip / backend_domain)"
  value       = google_cloud_run_v2_service.backend.uri
}

output "artifact_registry" {
  description = "Docker image registry path"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.sarj_docs.repository_id}"
}

output "cloud_sql_private_ip" {
  description = "Cloud SQL private IP address"
  value       = google_sql_database_instance.main.private_ip_address
}

output "lb_ip" {
  description = "Global HTTPS LB anycast IP — point Cloudflare's proxied A record at this"
  value       = google_compute_global_address.backend_lb.address
}

output "wif_provider" {
  description = "Workload Identity Federation provider (set as WIF_PROVIDER secret in GitHub)"
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "wif_service_account" {
  description = "WIF service account email (set as WIF_SERVICE_ACCOUNT secret in GitHub)"
  value       = google_service_account.github_actions.email
}

# CNAME challenge record for DNS-01 cert validation. Add this once to
# Cloudflare DNS (DNS-only, grey cloud) — Google checks it on every renewal,
# so it must stay in place permanently.
output "cert_dns_authorization_record" {
  description = "CNAME record to add to Cloudflare for DNS-01 cert validation"
  value       = google_certificate_manager_dns_authorization.backend.dns_resource_record
}
