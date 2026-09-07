# Offline schema/plan contract only: mocks do not execute Google's CEL engine or
# prove trusted-proxy header sanitization. Never run the real-provider test here.
mock_provider "google" { # sarj-noqa: SARJ206 -- Offline wiring only; required CI validates real providers, and CEL/proxy runtime remains an activation gate.
  override_during = plan
}

mock_provider "random" {
  override_during = plan
}

override_resource {
  target          = google_compute_security_policy.edge_armor
  override_during = plan
  values          = { id = "projects/test/global/securityPolicies/edge" }
}

override_resource {
  target          = google_compute_security_policy.backend_armor
  override_during = plan
  values          = { id = "projects/test/global/securityPolicies/backend" }
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

run "native_oauth_preview_preserves_enforced_security" {
  command = plan

  assert {
    condition = alltrue([
      for rule in google_compute_security_policy.backend_armor.rule : rule.preview
      if contains([553, 554, 555, 556], rule.priority)
    ])
    error_message = "All four MCP rules must remain non-enforcing previews."
  }

  assert {
    condition = alltrue([
      for priority, contract in {
        "553" = { method = "POST", path = "/register", count = 6, interval = 600 }
        "554" = { method = "GET", path = "/authorize", count = 30, interval = 60 }
        "555" = { method = "POST", path = "/token", count = 300, interval = 60 }
        "556" = { method = "POST", path = "/revoke", count = 120, interval = 60 }
        } : alltrue([
          for rule in google_compute_security_policy.backend_armor.rule : (
            rule.action == "throttle" && rule.preview &&
            rule.match[0].expr[0].expression == "request.method == '${contract.method}' && request.path.urlDecode() == '${contract.path}'" &&
            rule.rate_limit_options[0].enforce_on_key == "USER_IP" &&
            rule.rate_limit_options[0].conform_action == "allow" &&
            rule.rate_limit_options[0].exceed_action == "deny(429)" &&
            rule.rate_limit_options[0].rate_limit_threshold[0].count == contract.count &&
            rule.rate_limit_options[0].rate_limit_threshold[0].interval_sec == contract.interval
          ) if rule.priority == tonumber(priority)
      ])
    ])
    error_message = "MCP previews require exact decoded route/method, independent USER_IP throttles and fixed thresholds."
  }

  assert {
    condition = (
      google_compute_security_policy.backend_armor.name == "sarj-docs-backend-armor" &&
      google_compute_security_policy.edge_armor.name == "sarj-docs-edge-armor" &&
      google_compute_security_policy.edge_armor.type == "CLOUD_ARMOR_EDGE" &&
      toset(google_compute_security_policy.backend_armor.advanced_options_config[0].user_ip_request_headers) == toset(["CF-Connecting-IP"]) &&
      google_compute_backend_service.backend_dammam[0].edge_security_policy == google_compute_security_policy.edge_armor.id &&
      google_compute_backend_service.backend_dammam[0].security_policy == google_compute_security_policy.backend_armor.id
    )
    error_message = "Preserve both policy identities, trusted visitor header and both backend attachments."
  }

  assert {
    condition = toset([
      for rule in google_compute_security_policy.backend_armor.rule : rule.priority
    ]) == toset([500, 510, 520, 530, 540, 550, 553, 554, 555, 556, 560, 570, 580, 600, 610, 620, 2147483647])
    error_message = "Preserve every existing priority and add only the four supported MCP previews."
  }

  assert {
    condition = alltrue([
      for rule in google_compute_security_policy.backend_armor.rule : (
        rule.action == "deny(403)" && strcontains(rule.match[0].expr[0].expression, "evaluatePreconfiguredWaf(")
      ) if contains([500, 510, 520, 530, 540], rule.priority)
    ])
    error_message = "Existing WAF rules cannot become allows or throttles."
  }

  assert {
    condition = alltrue([
      for rule in google_compute_security_policy.backend_armor.rule : (
        rule.action == "throttle" && !rule.preview &&
        rule.rate_limit_options[0].enforce_on_key == "USER_IP" &&
        rule.rate_limit_options[0].rate_limit_threshold[0].count == (rule.priority == 580 ? 200 : 30) &&
        rule.rate_limit_options[0].rate_limit_threshold[0].interval_sec == 60
      ) if contains([560, 570, 580], rule.priority)
    ])
    error_message = "Existing search and general thresholds remain unchanged; preview token300 does not replace enforced general200."
  }

  assert {
    condition = alltrue([
      for rule in google_compute_security_policy.edge_armor.rule : (
        !rule.preview && rule.action == (rule.priority == 2147483646 ? "deny(403)" : "allow")
      )
      ]) && toset([
      for rule in google_compute_security_policy.edge_armor.rule : rule.priority
    ]) == toset([600, 610, 620, 2147483646, 2147483647])
    error_message = "The independently enforced Cloudflare-only edge source gate must not change."
  }
}
