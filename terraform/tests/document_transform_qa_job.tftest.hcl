mock_provider "google" { # sarj-noqa: SARJ206 -- Offline wiring contract; activation still requires a reviewed real-provider plan.
  override_during = plan
}

mock_provider "random" {
  override_during = plan
}

variables {
  frontend_url         = "https://wiki.example.test"
  backend_domain       = "api-wiki.example.test"
  google_client_id     = "synthetic-client"
  google_client_secret = "synthetic-secret"
  fernet_key           = "synthetic-key"
  cache_fernet_key     = "synthetic-cache-key"
  alert_email          = "alerts@example.test"
}

run "qa_job_is_absent_by_default" {
  command = plan

  assert {
    condition = alltrue([
      length(google_service_account.document_transform_qa) == 0,
      length(google_cloud_run_v2_job.document_transform_qa) == 0,
      length(google_storage_bucket.document_transform_qa) == 0,
      length(google_sql_user.document_transform_qa) == 0,
      length(google_cloud_run_v2_job_iam_member.document_transform_qa_invoker) == 0,
      length(google_secret_manager_secret_iam_member.document_transform_qa_existing_secrets) == 0,
    ])
    error_message = "Document-transform QA infrastructure must remain absent by default."
  }
}

run "enabled_job_is_private_single_task_and_digest_pinned" {
  command = plan

  variables {
    enable_document_transform_qa_job     = true
    document_transform_qa_image          = "me-central2-docker.pkg.dev/sarj-docs/sarj-docs-dammam/document-transform-qa@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    document_transform_qa_invoker_member = "group:security-qa@sarj.ai"
  }

  assert {
    condition = (
      google_cloud_run_v2_job.document_transform_qa[0].template[0].parallelism == 1 &&
      google_cloud_run_v2_job.document_transform_qa[0].template[0].task_count == 1 &&
      google_cloud_run_v2_job.document_transform_qa[0].template[0].template[0].max_retries == 0 &&
      google_cloud_run_v2_job.document_transform_qa[0].template[0].template[0].timeout == "7200s"
    )
    error_message = "QA must be a bounded, non-retrying, single-task manual job."
  }

  assert {
    condition     = google_cloud_run_v2_job.document_transform_qa[0].template[0].template[0].containers[0].image == var.document_transform_qa_image
    error_message = "QA must use the exact digest-pinned image."
  }

  assert {
    condition = toset(google_cloud_run_v2_job.document_transform_qa[0].template[0].template[0].volumes[0].gcs[0].mount_options) == toset([
      "uid=1001",
      "gid=1001",
      "file-mode=600",
      "dir-mode=700",
    ])
    error_message = "The durable evidence mount must preserve the runner's private file-mode contract."
  }

  assert {
    condition = (
      google_storage_bucket.document_transform_qa[0].uniform_bucket_level_access &&
      google_storage_bucket.document_transform_qa[0].public_access_prevention == "enforced" &&
      alltrue([
        for condition in google_storage_bucket.document_transform_qa[0].lifecycle_rule[0].condition : condition.age == 30
      ])
    )
    error_message = "QA evidence must remain private and lifecycle bounded."
  }


  assert {
    condition = (
      google_project_iam_member.document_transform_qa_cloudsql_client[0].role == "roles/cloudsql.client" &&
      google_project_iam_member.document_transform_qa_cloudsql_user[0].role == "roles/cloudsql.instanceUser" &&
      google_project_iam_member.document_transform_qa_vertex[0].role == "roles/aiplatform.user" &&
      google_storage_bucket_iam_member.document_transform_qa_objects[0].role == "roles/storage.objectUser"
    )
    error_message = "QA must have only the explicit database, judge, and dedicated-bucket roles."
  }

  assert {
    condition = (
      google_cloud_run_v2_job_iam_member.document_transform_qa_invoker[0].member == "group:security-qa@sarj.ai" &&
      google_cloud_run_v2_job_iam_member.document_transform_qa_invoker[0].role == "roles/run.invoker"
    )
    error_message = "Only the explicitly configured human principal may invoke QA."
  }

  assert {
    condition = toset(keys(google_secret_manager_secret_iam_member.document_transform_qa_existing_secrets)) == toset([
      "secret-key",
      "fernet-key",
      "google-client-id",
      "google-client-secret",
    ])
    error_message = "QA may read only the four existing secrets needed for session and Google OAuth access."
  }
}
