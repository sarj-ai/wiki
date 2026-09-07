# Offline schema/plan contract only. This proves default-off production wiring,
# not cloud provisioning, client acceptance or safe activation.
mock_provider "google" { # sarj-noqa: SARJ206 -- Offline default-off wiring only; reviewed real-provider plan and apply remain separate activation gates.
  override_during = plan
}

mock_provider "random" {
  override_during = plan
}

override_resource {
  target          = google_secret_manager_secret_version.mcp_native_fernet_keys
  override_during = plan
  values          = { version = "7" }
}

override_resource {
  target          = google_secret_manager_secret_version.mcp_native_lock_key
  override_during = plan
  values          = { version = "3" }
}

override_resource {
  target          = google_service_account.backend
  override_during = plan
  values = {
    email = "backend@wiki-test.iam.gserviceaccount.com"
    name  = "projects/wiki-test/serviceAccounts/backend@wiki-test.iam.gserviceaccount.com"
  }
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

run "native_mcp_default_plan_provisions_no_secrets" {
  command = plan

  assert {
    condition = (
      length(google_secret_manager_secret.mcp_native_fernet_keys) == 0 &&
      length(google_secret_manager_secret.mcp_native_lock_key) == 0 &&
      length(google_secret_manager_secret_version.mcp_native_fernet_keys) == 0 &&
      length(google_secret_manager_secret_version.mcp_native_lock_key) == 0 &&
      length(google_secret_manager_secret_iam_member.backend_mcp_native_fernet_keys_access) == 0 &&
      length(google_secret_manager_secret_iam_member.backend_mcp_native_lock_key_access) == 0
    )
    error_message = "The merge/default plan must not provision native MCP secrets or IAM."
  }

  assert {
    condition = length([
      for setting in google_cloud_run_v2_service.backend_dammam[0].template[0].containers[0].env : setting
      if contains(["MCP_NATIVE_FERNET_KEYS", "MCP_NATIVE_LOCK_KEY"], setting.name)
    ]) == 0
    error_message = "The merge/default backend must not reference unprovisioned native MCP secrets."
  }

  assert {
    condition = one([
      for setting in google_cloud_run_v2_service.backend_dammam[0].template[0].containers[0].env : setting.value
      if setting.name == "MCP_NATIVE_ENABLED"
    ]) == "false"
    error_message = "Native MCP must remain explicitly disabled in the default plan."
  }
}

run "native_mcp_rejects_partial_explicit_provisioning" {
  command = plan

  variables {
    provision_mcp_native_secrets = true
    mcp_native_fernet_keys       = "synthetic-mcp-ring"
  }

  expect_failures = [var.provision_mcp_native_secrets]
}

run "native_mcp_rejects_enablement_without_secrets" {
  command = plan

  variables {
    enable_mcp_native = true
  }

  expect_failures = [var.enable_mcp_native]
}

run "native_mcp_rejects_implicit_secret_provisioning" {
  command = plan

  variables {
    mcp_native_fernet_keys = "synthetic-mcp-ring"
    mcp_native_lock_key    = "synthetic-mcp-lock"
  }

  expect_failures = [var.provision_mcp_native_secrets]
}

run "native_mcp_rejects_lone_implicit_fernet_key" {
  command = plan

  variables {
    mcp_native_fernet_keys = "synthetic-mcp-ring"
  }

  expect_failures = [var.provision_mcp_native_secrets]
}

run "native_mcp_rejects_lone_implicit_lock_key" {
  command = plan

  variables {
    mcp_native_lock_key = "synthetic-mcp-lock"
  }

  expect_failures = [var.provision_mcp_native_secrets]
}

run "native_mcp_is_provisioned_but_disabled" {
  command = plan

  variables {
    provision_mcp_native_secrets = true
    mcp_native_fernet_keys       = "synthetic-mcp-ring"
    mcp_native_lock_key          = "synthetic-mcp-lock"
  }

  assert {
    condition = one([
      for setting in google_cloud_run_v2_service.backend_dammam[0].template[0].containers[0].env : setting.value
      if setting.name == "MCP_NATIVE_ENABLED"
    ]) == "false"
    error_message = "Native MCP must remain explicitly disabled in production plumbing."
  }

  assert {
    condition = {
      for setting in google_cloud_run_v2_service.backend_dammam[0].template[0].containers[0].env : setting.name => setting.value
      if contains([
        "MCP_NATIVE_ISSUER",
        "MCP_NATIVE_LOOPBACK_PATHS",
        "MCP_NATIVE_HTTPS_CALLBACKS",
      ], setting.name)
      } == {
      MCP_NATIVE_ISSUER          = "https://api-wiki.example.test"
      MCP_NATIVE_LOOPBACK_PATHS  = jsonencode(["/callback"])
      MCP_NATIVE_HTTPS_CALLBACKS = jsonencode([])
    }
    error_message = "Native MCP must use the fixed API issuer and only Codex's portless variable-loopback callback path."
  }

  assert {
    condition = alltrue([
      for contract in [
        {
          name    = "MCP_NATIVE_FERNET_KEYS"
          secret  = google_secret_manager_secret.mcp_native_fernet_keys[0].secret_id
          version = google_secret_manager_secret_version.mcp_native_fernet_keys[0].version
        },
        {
          name    = "MCP_NATIVE_LOCK_KEY"
          secret  = google_secret_manager_secret.mcp_native_lock_key[0].secret_id
          version = google_secret_manager_secret_version.mcp_native_lock_key[0].version
        },
        ] : alltrue([
          for setting in google_cloud_run_v2_service.backend_dammam[0].template[0].containers[0].env : (
            setting.value == null &&
            setting.value_source[0].secret_key_ref[0].secret == contract.secret &&
            setting.value_source[0].secret_key_ref[0].version == contract.version &&
            setting.value_source[0].secret_key_ref[0].version != "latest"
          ) if setting.name == contract.name
      ])
    ])
    error_message = "Native MCP keys must use independent Secret Manager entries pinned to exact versions."
  }

  assert {
    condition = (
      google_secret_manager_secret.mcp_native_fernet_keys[0].secret_id == "sarj-docs-mcp-native-fernet-keys" &&
      google_secret_manager_secret.mcp_native_lock_key[0].secret_id == "sarj-docs-mcp-native-lock-key" &&
      google_secret_manager_secret_version.mcp_native_fernet_keys[0].deletion_policy == "DISABLE" &&
      google_secret_manager_secret_version.mcp_native_lock_key[0].deletion_policy == "DISABLE"
    )
    error_message = "Native MCP secrets must be independent and retain disabled historical versions."
  }

  assert {
    condition = alltrue([
      for binding in [
        google_secret_manager_secret_iam_member.backend_mcp_native_fernet_keys_access[0],
        google_secret_manager_secret_iam_member.backend_mcp_native_lock_key_access[0],
        ] : (
        binding.role == "roles/secretmanager.secretAccessor" &&
        binding.member == "serviceAccount:${google_service_account.backend.email}"
      )
    ])
    error_message = "Only the backend runtime identity may read native MCP key material."
  }
}

run "native_mcp_can_be_explicitly_enabled_with_both_keys" {
  command = plan

  variables {
    provision_mcp_native_secrets = true
    enable_mcp_native            = true
    mcp_native_fernet_keys       = "synthetic-mcp-ring"
    mcp_native_lock_key          = "synthetic-mcp-lock"
  }

  assert {
    condition = one([
      for setting in google_cloud_run_v2_service.backend_dammam[0].template[0].containers[0].env : setting.value
      if setting.name == "MCP_NATIVE_ENABLED"
    ]) == "true"
    error_message = "Native MCP must be enabled only through the explicit activation variable."
  }

  assert {
    condition = length([
      for setting in google_cloud_run_v2_service.backend_dammam[0].template[0].containers[0].env : setting
      if contains(["MCP_NATIVE_FERNET_KEYS", "MCP_NATIVE_LOCK_KEY"], setting.name)
    ]) == 2
    error_message = "An enabled native MCP runtime must attach both independent key references."
  }
}
