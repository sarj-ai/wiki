output "backend_url" {
  description = "Cloud Run backend service URL (not the public URL — that's lb_ip / backend_domain)"
  value       = google_cloud_run_v2_service.backend_dammam[0].uri
}

output "smart_create_queue" {
  description = "Cloud Tasks queue resource name used for asynchronous Smart Create"
  value       = google_cloud_tasks_queue.smart_create_dammam[0].id
}

output "cloud_tasks_invoker_service_account" {
  description = "OIDC identity allowed to invoke the internal Smart Create task endpoint"
  value       = google_service_account.cloud_tasks_invoker.email
}

output "artifact_registry" {
  description = "Docker image registry path"
  value       = "${var.dammam_region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.dammam[0].repository_id}"
}

output "cloud_sql_private_ip" {
  description = "Cloud SQL private IP address"
  value       = google_sql_database_instance.dammam[0].private_ip_address
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

output "dammam_backend_url" {
  description = "Protected Dammam Cloud Run canary URL"
  value       = var.enable_dammam_stack ? google_cloud_run_v2_service.backend_dammam[0].uri : null
}

output "dammam_database_connection_name" {
  description = "Dammam production SQL connection name"
  value       = var.enable_dammam_stack ? google_sql_database_instance.dammam[0].connection_name : null
}

output "dammam_state_bucket" {
  description = "Versioned Saudi Terraform state bucket"
  value       = var.enable_dammam_stack ? google_storage_bucket.terraform_state_dammam[0].name : null
}

output "document_transform_qa_job" {
  description = "Default-off private corpus QA job name"
  value       = local.document_transform_qa_enabled ? google_cloud_run_v2_job.document_transform_qa[0].name : null
}

output "document_transform_qa_artifact_bucket" {
  description = "Private, lifecycle-bounded QA evidence bucket"
  value       = local.document_transform_qa_enabled ? google_storage_bucket.document_transform_qa[0].name : null
}

# CNAME challenge record for DNS-01 cert validation. Add this once to
# Cloudflare DNS (DNS-only, grey cloud) — Google checks it on every renewal,
# so it must stay in place permanently.
output "cert_dns_authorization_record" {
  description = "CNAME record to add to Cloudflare for DNS-01 cert validation"
  value       = google_certificate_manager_dns_authorization.backend.dns_resource_record
}
