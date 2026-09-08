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
      length(google_cloud_run_v2_job_iam_member.document_transform_qa_launcher) == 0,
      length(google_cloud_run_v2_job_iam_member.document_transform_qa_attestor) == 0,
      length(google_project_iam_member.document_transform_qa_vertex) == 0,
      length(google_storage_bucket_iam_member.document_transform_qa_objects) == 0,
    ])
    error_message = "Document-transform QA infrastructure must remain absent by default."
  }
}

run "enabled_job_is_private_single_task_and_digest_pinned" {
  command = plan

  variables {
    enable_document_transform_qa_job      = true
    document_transform_qa_image           = "me-central2-docker.pkg.dev/sarj-docs/sarj-docs-dammam/document-transform-qa@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    document_transform_qa_release_sha     = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    document_transform_qa_operator_emails = ["security-qa@sarj.ai"]
  }

  assert {
    condition = alltrue([
      length(google_service_account.document_transform_qa) == 1,
      length(google_cloud_run_v2_job.document_transform_qa) == 1,
      length(google_storage_bucket.document_transform_qa) == 1,
      length(google_cloud_run_v2_job_iam_member.document_transform_qa_launcher) == 1,
      length(google_cloud_run_v2_job_iam_member.document_transform_qa_attestor) == 1,
      length(google_project_iam_member.document_transform_qa_vertex) == 1,
      length(google_storage_bucket_iam_member.document_transform_qa_objects) == 1,
    ])
    error_message = "Enabling QA must create exactly the reviewed resources and backend-only IAM bindings."
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
      google_project_iam_member.document_transform_qa_vertex[0].role == "roles/aiplatform.user" &&
      google_storage_bucket_iam_member.document_transform_qa_objects[0].role == "roles/storage.objectUser"
    )
    error_message = "QA must have only the explicit judge and dedicated-bucket roles."
  }

  assert {
    condition = (
      google_cloud_run_v2_job_iam_member.document_transform_qa_launcher[0].role == "roles/run.jobsExecutor" &&
      google_cloud_run_v2_job_iam_member.document_transform_qa_attestor[0].role == "roles/run.viewer"
    )
    error_message = "Only the backend may launch without overrides and read back the immutable execution."
  }

  assert {
    condition = alltrue([
      for env in google_cloud_run_v2_job.document_transform_qa[0].template[0].template[0].containers[0].env :
      !contains(["SECRET_KEY", "FERNET_KEY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "DB_IAM_USER", "DB_NAME", "DB_INSTANCE_CONNECTION_NAME", "CAPABILITY_TOKEN", "GRANT_TOKEN", "RUN_ID"], env.name)
    ])
    error_message = "QA must receive neither production secrets nor database connectivity settings."
  }

  assert {
    condition     = google_cloud_run_v2_job.document_transform_qa[0].template[0].template[0].containers[0].args == null
    error_message = "QA launch must transport no run IDs, capabilities, or secrets in container arguments."
  }
}
