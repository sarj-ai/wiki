# Google Cloud Load Balancer fronting the Cloud Run backend.
#
# Why not point Cloudflare straight at the Cloud Run URL: Cloud Run
# serves on an auto-generated ``*.run.app`` hostname, which means
# Cloudflare can't do origin SSL cert validation or native
# origin-rule rewriting cleanly. Putting an external HTTPS LB in
# front gives us a stable global anycast IP we can map in Cloudflare,
# a managed SSL cert issued via Google-managed Certificate Manager,
# and a single place to apply Google Cloud Armor rules in parallel
# with Cloudflare's edge WAF.
#
# Traffic path: user → Cloudflare edge (WAF + DDoS + bot mitigation)
#                → GCLB anycast IP (Cloud Armor baseline + TLS term)
#                → serverless NEG → Cloud Run backend.

locals {
  cloudflare_origin_cidrs = [
    "173.245.48.0/20",
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "141.101.64.0/18",
    "108.162.192.0/18",
    "190.93.240.0/20",
    "188.114.96.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "162.158.0.0/15",
    "104.16.0.0/13",
    "104.24.0.0/14",
    "172.64.0.0/13",
    "131.0.72.0/22",
    "2400:cb00::/32",
    "2606:4700::/32",
    "2803:f800::/32",
    "2405:b500::/32",
    "2405:8100::/32",
    "2a06:98c0::/29",
    "2c0f:f248::/32",
  ]
}

# Reserve a global IPv4 address that Cloudflare will DNS-proxy to.
resource "google_compute_global_address" "backend_lb" {
  name = "sarj-docs-backend-lb-ip"

  depends_on = [google_project_service.apis["compute.googleapis.com"]]
}

# Serverless Network Endpoint Group wraps the Cloud Run service so
# the LB can route to it without exposing the run.app URL.
# The edge policy establishes Cloudflare as the only intended public path before
# requests reach the backend WAF. The explicit catch-all rejects every other
# source; Cloud Armor reserves the mandatory default rule, which is unreachable.
resource "google_compute_security_policy" "edge_armor" {
  name = "sarj-docs-edge-armor"
  type = "CLOUD_ARMOR_EDGE"

  rule {
    action   = "allow"
    priority = 600
    preview  = false
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = slice(local.cloudflare_origin_cidrs, 0, 10)
      }
    }
    description = "allow Cloudflare origins (1/3)"
  }

  rule {
    action   = "allow"
    priority = 610
    preview  = false
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = slice(local.cloudflare_origin_cidrs, 10, 20)
      }
    }
    description = "allow Cloudflare origins (2/3)"
  }

  rule {
    action   = "allow"
    priority = 620
    preview  = false
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = slice(
          local.cloudflare_origin_cidrs,
          20,
          length(local.cloudflare_origin_cidrs),
        )
      }
    }
    description = "allow Cloudflare origins (3/3)"
  }

  rule {
    action   = "deny(403)"
    priority = 2147483646
    preview  = false
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    description = "deny non-Cloudflare origins"
  }

  rule {
    action   = "allow"
    priority = 2147483647
    preview  = false
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    description = "mandatory default; unreachable after source gate"
  }
}

# Cloud Armor — GCP-side WAF paired with the Cloudflare edge.
#
# Rule-priority notes (Cloud Armor evaluates lowest priority first
# and the first matching terminal action short-circuits the rest):
#
#   500-540   OWASP WAF rules. Deny on match, continue on no-match —
#             run before the allowlist so a malicious request from a
#             Cloudflare IP still gets blocked.
#   550-580   Missing-client-IP enforcement and per-client-IP throttles.
#   600-620   Cloudflare IP allowlist. Cloudflare publishes 15 IPv4
#             ranges and 7 IPv6 ranges; ``SRC_IPS_V1`` caps at 10 per
#             rule, so we split across three rules. Refresh quarterly
#             from https://www.cloudflare.com/ips/.
#   2147483647 (default)  Deny everything else. Direct hits to the GCLB
#             IP from non-Cloudflare sources and direct hits to the
#             Cloud Run run.app URL (rejected earlier by
#             ``INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER``) are closed.
#
# Backend rate rules rely on the enforced edge origin policy; otherwise their
# conforming action could bypass this policy's source gate.
resource "google_compute_security_policy" "backend_armor" {
  name = "sarj-docs-backend-armor"

  advanced_options_config {
    user_ip_request_headers = ["CF-Connecting-IP"]
  }

  # OWASP ModSecurity Core Rule Set — preconfigured WAF rules.
  # XSS rule with opt-out for 941340. Rule 941340 ("IE XSS Filters")
  # detects base64-encoded content and fires on every request that
  # carries our JWT cookies. Same whack-a-mole outcome as SQLi at
  # higher paranoia — opting out the specific rule preserves all other
  # XSS coverage while letting authenticated traffic through.
  rule {
    action   = "deny(403)"
    priority = 500
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('xss-v33-stable', {'opt_out_rule_ids': ['owasp-crs-v030301-id941340-xss']})"
      }
    }
    description = "block XSS (opt-out 941340: fires on JWT base64url cookies)"
  }

  # Higher SQLi sensitivity levels match the base64url JWT cookie on
  # authenticated requests. Sensitivity 1 avoids that false positive;
  # SQLAlchemy parameterization and Cloudflare's upstream WAF remain
  # additional SQLi defenses.
  rule {
    action   = "deny(403)"
    priority = 510
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('sqli-v33-stable', {'sensitivity': 1})"
      }
    }
    description = "block SQLi (paranoia 1, JWT-cookie compatible)"
  }

  # LFI at sensitivity 1 with opt-out for 930120. Rule 930120 ("OS
  # File Access") fires on URL-encoded slashes in the OAuth callback's
  # scope parameter — ``https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fdrive``
  # decodes to a path-like string that triggers even at paranoia level 1.
  rule {
    action   = "deny(403)"
    priority = 520
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('lfi-v33-stable', {'sensitivity': 1, 'opt_out_rule_ids': ['owasp-crs-v030301-id930120-lfi']})"
      }
    }
    description = "block LFI (sensitivity 1, opt-out 930120: fires on OAuth scope param)"
  }

  # RFI at sensitivity 1. Paranoid RFI rule 931130 ("HTTP/HTTPS URL in
  # parameter") matches the URL-encoded ``scope=https://www.googleapis.com/...``
  # values Google sends back in the OAuth callback after consent — the
  # ``prompt=consent`` flow makes the callback URL noticeably more
  # verbose than the default and trips the paranoid rule. Same pattern
  # as the SQLi / LFI false positives.
  rule {
    action   = "deny(403)"
    priority = 530
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('rfi-v33-stable', {'sensitivity': 1})"
      }
    }
    description = "block RFI (sensitivity 1, OAuth-callback compatible)"
  }

  # RCE rule with opt-out for 932200. Rule 932200 ("RCE Bypass
  # Techniques") matches base64url patterns that are structurally
  # identical to JWT signatures. Opting it out keeps all other RCE
  # coverage intact.
  rule {
    action   = "deny(403)"
    priority = 540
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('rce-v33-stable', {'opt_out_rule_ids': ['owasp-crs-v030301-id932200-rce']})"
      }
    }
    description = "block RCE (opt-out 932200: fires on JWT base64url cookies)"
  }

  # A missing or empty visitor identity would make USER_IP fall back to the
  # immediate Cloudflare source IP. Detect that condition separately so
  # enforcement fails closed without sharing one proxy rate-limit bucket.
  rule {
    action   = "deny(403)"
    priority = 550
    preview  = false
    match {
      expr {
        expression = "!has(request.headers['cf-connecting-ip']) || request.headers['cf-connecting-ip'] == ''"
      }
    }
    description = "deny missing or empty client IP"
  }

  # MCP admission is preview-only until proxy identity and CEL matching are
  # verified. These approximate per-IP limits complement the durable PostgreSQL
  # DCR budget; they do not implement a second global quota. Existing enforced
  # rules, including the missing-header rejection and general 200/minute limit,
  # still apply during preview. Invalid user-IP headers fall back to the proxy IP
  # and share its rate-limit bucket, failing toward throttling rather than bypass.
  rule {
    action   = "throttle"
    priority = 553
    preview  = true
    match {
      expr {
        expression = "request.method == 'POST' && request.path.urlDecode() == '/register'"
      }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "USER_IP"
      rate_limit_threshold {
        count        = 6
        interval_sec = 600
      }
    }
    description = "preview MCP registration throttle per client IP"
  }

  rule {
    action   = "throttle"
    priority = 554
    preview  = true
    match {
      expr {
        expression = "request.method == 'GET' && request.path.urlDecode() == '/authorize'"
      }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "USER_IP"
      rate_limit_threshold {
        count        = 30
        interval_sec = 60
      }
    }
    description = "preview MCP authorization throttle per client IP"
  }

  # Enforcing this later would replace (not stack with) the general 200/minute
  # rule for POST /token. Review the explicit 300/minute allowance at that gate.
  rule {
    action   = "throttle"
    priority = 555
    preview  = true
    match {
      expr {
        expression = "request.method == 'POST' && request.path.urlDecode() == '/token'"
      }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "USER_IP"
      rate_limit_threshold {
        count        = 300
        interval_sec = 60
      }
    }
    description = "preview MCP token throttle per client IP"
  }

  rule {
    action   = "throttle"
    priority = 556
    preview  = true
    match {
      expr {
        expression = "request.method == 'POST' && request.path.urlDecode() == '/revoke'"
      }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "USER_IP"
      rate_limit_threshold {
        count        = 120
        interval_sec = 60
      }
    }
    description = "preview MCP revocation throttle per client IP"
  }

  # Keep Drive search and snippet requests in independent buckets, matching the
  # product's per-route rate contract. OPTIONS remains exempt.
  rule {
    action   = "throttle"
    priority = 560
    preview  = false
    match {
      expr {
        expression = "has(request.headers['cf-connecting-ip']) && request.headers['cf-connecting-ip'] != '' && request.method != 'OPTIONS' && request.path.urlDecode() == '/drive-search'"
      }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "USER_IP"

      rate_limit_threshold {
        count        = 30
        interval_sec = 60
      }
    }
    description = "drive search throttle per client IP"
  }

  rule {
    action   = "throttle"
    priority = 570
    preview  = false
    match {
      expr {
        expression = "has(request.headers['cf-connecting-ip']) && request.headers['cf-connecting-ip'] != '' && request.method != 'OPTIONS' && request.path.urlDecode() == '/drive-search/snippet'"
      }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "USER_IP"

      rate_limit_threshold {
        count        = 30
        interval_sec = 60
      }
    }
    description = "search snippet throttle per client IP"
  }

  # Health checks and preflights must never consume a client bucket. All other
  # search-path methods are handled by the two independent rules above.
  rule {
    action   = "throttle"
    priority = 580
    preview  = false
    match {
      expr {
        expression = "has(request.headers['cf-connecting-ip']) && request.headers['cf-connecting-ip'] != '' && request.method != 'OPTIONS' && request.path.urlDecode() != '/health' && !request.path.urlDecode().matches('^/drive-search$|^/drive-search/snippet$')"
      }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "USER_IP"

      rate_limit_threshold {
        count        = 200
        interval_sec = 60
      }
    }
    description = "general throttle per client IP"
  }

  # Cloudflare IPv4 set 1 (first 10 of 15 published ranges).
  rule {
    action   = "allow"
    priority = 600
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = slice(local.cloudflare_origin_cidrs, 0, 10)
      }
    }
    description = "allow Cloudflare IPv4 (1/3)"
  }

  # Cloudflare IPv4 set 2 (remaining 5) + Cloudflare IPv6 set 1 (first 5).
  rule {
    action   = "allow"
    priority = 610
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = slice(local.cloudflare_origin_cidrs, 10, 20)
      }
    }
    description = "allow Cloudflare IPv4 (2/3) + IPv6 (1/2)"
  }

  # Cloudflare IPv6 set 2 (remaining 2).
  rule {
    action   = "allow"
    priority = 620
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = slice(
          local.cloudflare_origin_cidrs,
          20,
          length(local.cloudflare_origin_cidrs),
        )
      }
    }
    description = "allow Cloudflare IPv6 (2/2)"
  }

  # Default: deny. Traffic that doesn't come through Cloudflare is rejected.
  rule {
    action   = "deny(403)"
    priority = 2147483647
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    description = "default deny — non-Cloudflare traffic"
  }
}

# URL map — all paths go to the backend service.
resource "google_compute_url_map" "backend" {
  name            = "sarj-docs-backend-url-map"
  default_service = google_compute_backend_service.backend_dammam[0].id
}

# Managed TLS cert via Certificate Manager.
#
# Validation method: DNS-01 (via ``google_certificate_manager_dns_authorization``).
#
# We deliberately do *not* use the default HTTP-01 validation here because
# the backend domain (``api-wiki.sarj.ai``) is proxied through Cloudflare —
# DNS resolves to Cloudflare's anycast IPs, not the GCLB IP, so Google's
# HTTP-01 challenger can't reach the LB to fetch the validation token. This
# would be a chicken-and-egg even without Cloudflare: the LB has no cert
# until the cert validates, and HTTP-01 would need to hit the LB to validate.
#
# With DNS-01, Google publishes a CNAME challenge record (surfaced via the
# ``cert_dns_authorization_record`` output) that we add once to Cloudflare
# DNS. Google validates by DNS lookup, which works through the Cloudflare
# proxy because it's checking DNS, not HTTP. The CNAME is permanent — it
# stays in place for renewals so we never repeat this dance.
resource "google_certificate_manager_dns_authorization" "backend" {
  name        = "sarj-docs-backend-dns-auth"
  location    = "global"
  domain      = var.backend_domain
  description = "DNS-01 authorization for ${var.backend_domain} (Cloudflare-proxied)"

  depends_on = [google_project_service.apis["certificatemanager.googleapis.com"]]
}

resource "google_certificate_manager_certificate" "backend" {
  name     = "sarj-docs-backend-cert"
  location = "global"

  managed {
    domains            = [var.backend_domain]
    dns_authorizations = [google_certificate_manager_dns_authorization.backend.id]
  }

  depends_on = [google_project_service.apis["certificatemanager.googleapis.com"]]
}

resource "google_certificate_manager_certificate_map" "backend" {
  name = "sarj-docs-backend-cert-map"
}

resource "google_certificate_manager_certificate_map_entry" "backend" {
  name         = "sarj-docs-backend-cert-map-entry"
  map          = google_certificate_manager_certificate_map.backend.name
  hostname     = var.backend_domain
  certificates = [google_certificate_manager_certificate.backend.id]
}

# HTTPS proxy binding the URL map + cert map.
resource "google_compute_target_https_proxy" "backend" {
  name            = "sarj-docs-backend-https-proxy"
  url_map         = google_compute_url_map.backend.id
  certificate_map = "//certificatemanager.googleapis.com/${google_certificate_manager_certificate_map.backend.id}"
}

# Global forwarding rule — the actual entry point at the reserved IP.
resource "google_compute_global_forwarding_rule" "backend_https" {
  name                  = "sarj-docs-backend-forwarding"
  target                = google_compute_target_https_proxy.backend.id
  port_range            = "443"
  ip_address            = google_compute_global_address.backend_lb.address
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

# HTTP → HTTPS redirect so :80 traffic gets upgraded before it
# reaches Cloud Run. Cloudflare already does this at the edge but
# defense in depth.
resource "google_compute_url_map" "http_redirect" {
  name = "sarj-docs-backend-http-redirect"

  default_url_redirect {
    https_redirect         = true
    redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"
    strip_query            = false
  }
}

resource "google_compute_target_http_proxy" "http_redirect" {
  name    = "sarj-docs-backend-http-proxy"
  url_map = google_compute_url_map.http_redirect.id
}

resource "google_compute_global_forwarding_rule" "backend_http_redirect" {
  name                  = "sarj-docs-backend-http-forwarding"
  target                = google_compute_target_http_proxy.http_redirect.id
  port_range            = "80"
  ip_address            = google_compute_global_address.backend_lb.address
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

resource "google_compute_region_network_endpoint_group" "backend_dammam" {
  count                 = var.enable_dammam_stack ? 1 : 0
  name                  = "sarj-docs-backend-neg-dammam"
  region                = var.dammam_region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = google_cloud_run_v2_service.backend_dammam[0].name
  }
}

resource "google_compute_backend_service" "backend_dammam" {
  count                 = var.enable_dammam_stack ? 1 : 0
  name                  = "sarj-docs-backend-service-dammam"
  protocol              = "HTTPS"
  timeout_sec           = 30
  load_balancing_scheme = "EXTERNAL_MANAGED"
  enable_cdn            = false
  edge_security_policy  = google_compute_security_policy.edge_armor.id
  security_policy       = google_compute_security_policy.backend_armor.id

  backend {
    group = google_compute_region_network_endpoint_group.backend_dammam[0].id
  }

  log_config {
    enable      = true
    sample_rate = 1.0
  }
}
