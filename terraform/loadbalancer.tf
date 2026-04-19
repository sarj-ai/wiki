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

# Reserve a global IPv4 address that Cloudflare will DNS-proxy to.
resource "google_compute_global_address" "backend_lb" {
  name = "sarj-docs-backend-lb-ip"

  depends_on = [google_project_service.apis["compute.googleapis.com"]]
}

# Serverless Network Endpoint Group wraps the Cloud Run service so
# the LB can route to it without exposing the run.app URL.
resource "google_compute_region_network_endpoint_group" "backend_neg" {
  name                  = "sarj-docs-backend-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = google_cloud_run_v2_service.backend.name
  }
}

# Backend service — wires the NEG into the LB with Cloud Armor.
resource "google_compute_backend_service" "backend" {
  name                  = "sarj-docs-backend-service"
  protocol              = "HTTPS"
  port_name             = "https"
  timeout_sec           = 30
  load_balancing_scheme = "EXTERNAL_MANAGED"
  enable_cdn            = false

  backend {
    group = google_compute_region_network_endpoint_group.backend_neg.id
  }

  security_policy = google_compute_security_policy.backend_armor.id

  log_config {
    enable      = true
    sample_rate = 1.0
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
#   600-620   Cloudflare IP allowlist. Cloudflare publishes 15 IPv4
#             ranges and 7 IPv6 ranges; ``SRC_IPS_V1`` caps at 10 per
#             rule, so we split across three rules. Refresh from
#             https://www.cloudflare.com/ips/ on cutover night and at
#             least quarterly.
#   2147483647 (default)  Deny everything else. Direct hits to the GCLB
#             IP from non-Cloudflare sources and direct hits to the
#             Cloud Run run.app URL (rejected earlier by
#             ``INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER``) are closed.
#
# Rate limiting is intentionally NOT done here. ``rate_based_ban`` with
# ``conform_action = allow`` short-circuits evaluation, which either
# (a) runs before the allowlist and lets non-Cloudflare traffic through
# under the burst threshold, or (b) runs after the allowlist and never
# fires. The FastAPI in-process limiter (``main.py``) handles
# per-IP throttling at the application layer, and Cloudflare's edge
# rate limiting covers global burst protection.
resource "google_compute_security_policy" "backend_armor" {
  name = "sarj-docs-backend-armor"

  # OWASP ModSecurity Core Rule Set — preconfigured WAF rules.
  rule {
    action   = "deny(403)"
    priority = 500
    match {
      expr {
        expression = "evaluatePreconfiguredExpr('xss-v33-stable')"
      }
    }
    description = "block XSS"
  }

  rule {
    action   = "deny(403)"
    priority = 510
    match {
      expr {
        expression = "evaluatePreconfiguredExpr('sqli-v33-stable', ['owasp-crs-v030301-id942100-sqli'])"
      }
    }
    description = "block SQLi (with /auth/callback exception)"
  }

  rule {
    action   = "deny(403)"
    priority = 520
    match {
      expr {
        expression = "evaluatePreconfiguredExpr('lfi-v33-stable')"
      }
    }
    description = "block LFI"
  }

  rule {
    action   = "deny(403)"
    priority = 530
    match {
      expr {
        expression = "evaluatePreconfiguredExpr('rfi-v33-stable')"
      }
    }
    description = "block RFI"
  }

  rule {
    action   = "deny(403)"
    priority = 540
    match {
      expr {
        expression = "evaluatePreconfiguredExpr('rce-v33-stable')"
      }
    }
    description = "block RCE"
  }

  # Cloudflare IPv4 set 1 (first 10 of 15 published ranges).
  rule {
    action   = "allow"
    priority = 600
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = [
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
        ]
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
        src_ip_ranges = [
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
        ]
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
        src_ip_ranges = [
          "2a06:98c0::/29",
          "2c0f:f248::/32",
        ]
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
  default_service = google_compute_backend_service.backend.id
}

# Managed TLS cert via Certificate Manager
resource "google_certificate_manager_certificate" "backend" {
  name     = "sarj-docs-backend-cert"
  location = "global"

  managed {
    domains = [var.backend_domain]
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
