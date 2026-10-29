# Offline wiring contract; this does not execute CEL or mint GitHub OIDC tokens.
mock_provider "google" { # sarj-noqa: SARJ206 -- Mocked plan checks identity wiring; the production release separately verifies real federation.
  override_during = plan
}

mock_provider "random" {
  override_during = plan
}

override_resource {
  target          = google_iam_workload_identity_pool.github
  override_during = plan
  values          = { name = "projects/451099906256/locations/global/workloadIdentityPools/github-actions" }
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

run "federation_retains_the_original_immutable_identity_and_main_only_events" {
  command = plan

  assert {
    condition = google_iam_workload_identity_pool_provider.github.attribute_condition == join(" && ", [
      "assertion.repository_id == '1183627122'",
      "assertion.repository_owner_id == '148175495'",
      "assertion.ref == 'refs/heads/main'",
      "assertion.event_name in ['push', 'workflow_dispatch']",
    ])
    error_message = "Repository renames must preserve the original owner/repository IDs and main-only production events."
  }

  assert {
    condition = google_iam_workload_identity_pool_provider.github.attribute_mapping == tomap({
      "google.subject"                = "assertion.sub"
      "attribute.actor"               = "assertion.actor"
      "attribute.repository"          = "assertion.repository"
      "attribute.repository_id"       = "assertion.repository_id"
      "attribute.repository_owner_id" = "assertion.repository_owner_id"
    })
    error_message = "Preserve subject/actor mappings and explicitly expose immutable repository and owner IDs."
  }

  assert {
    condition = (
      google_iam_workload_identity_pool_provider.github.oidc[0].issuer_uri == "https://token.actions.githubusercontent.com" &&
      google_service_account_iam_binding.github_actions_wif.role == "roles/iam.workloadIdentityUser" &&
      google_service_account_iam_binding.github_actions_wif.members == toset([
        "principalSet://iam.googleapis.com/projects/451099906256/locations/global/workloadIdentityPools/github-actions/attribute.repository_id/1183627122",
      ])
    )
    error_message = "Only the original repository ID may impersonate the existing deployment account through GitHub OIDC."
  }
}

run "repository_names_cannot_replace_numeric_identity" {
  command = plan
  variables {
    github_repository_id = "sarj-ai/wiki"
  }
  expect_failures = [var.github_repository_id]
}

run "owner_names_cannot_replace_numeric_identity" {
  command = plan
  variables {
    github_repository_owner_id = "sarj-ai"
  }
  expect_failures = [var.github_repository_owner_id]
}
