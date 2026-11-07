
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

resource "google_compute_global_address" "backend_lb" {
  name = "sarj-docs-backend-lb-ip"

  depends_on = [google_project_service.apis["compute.googleapis.com"]]
}

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

resource "google_compute_security_policy" "backend_armor" {
  name = "sarj-docs-backend-armor"

  advanced_options_config {
    user_ip_request_headers = ["CF-Connecting-IP"]
  }

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

resource "google_compute_url_map" "backend" {
  name            = "sarj-docs-backend-url-map"
  default_service = google_compute_backend_service.backend_dammam[0].id
}

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

resource "google_compute_ssl_policy" "backend" {
  name            = "${var.project_id}-backend-modern"
  profile         = "MODERN"
  min_tls_version = "TLS_1_2"
}

resource "google_compute_target_https_proxy" "backend" {
  name            = "sarj-docs-backend-https-proxy"
  url_map         = google_compute_url_map.backend.id
  certificate_map = "//certificatemanager.googleapis.com/${google_certificate_manager_certificate_map.backend.id}"
  ssl_policy      = google_compute_ssl_policy.backend.id
}

resource "google_compute_global_forwarding_rule" "backend_https" {
  name                  = "sarj-docs-backend-forwarding"
  target                = google_compute_target_https_proxy.backend.id
  port_range            = "443"
  ip_address            = google_compute_global_address.backend_lb.address
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

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
