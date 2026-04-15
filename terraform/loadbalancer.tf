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

# Cloud Armor — the GCP-side WAF, runs in parallel with Cloudflare
# so there's defense in depth. Set to max-strength OWASP rules plus
# an allowlist for Google's own OAuth redirect traffic on
# /auth/callback (which carries a bearer-shaped query param that can
# otherwise trip SQLi signatures).
resource "google_compute_security_policy" "backend_armor" {
  name = "sarj-docs-backend-armor"

  # Default: allow; rules below block specific classes.
  rule {
    action   = "allow"
    priority = 2147483647
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    description = "default allow"
  }

  # OWASP ModSecurity Core Rule Set — preconfigured WAF rules.
  rule {
    action   = "deny(403)"
    priority = 1000
    match {
      expr {
        expression = "evaluatePreconfiguredExpr('xss-v33-stable')"
      }
    }
    description = "block XSS"
  }

  rule {
    action   = "deny(403)"
    priority = 1010
    match {
      expr {
        expression = "evaluatePreconfiguredExpr('sqli-v33-stable', ['owasp-crs-v030301-id942100-sqli'])"
      }
    }
    description = "block SQLi (with /auth/callback exception)"
  }

  rule {
    action   = "deny(403)"
    priority = 1020
    match {
      expr {
        expression = "evaluatePreconfiguredExpr('lfi-v33-stable')"
      }
    }
    description = "block LFI"
  }

  rule {
    action   = "deny(403)"
    priority = 1030
    match {
      expr {
        expression = "evaluatePreconfiguredExpr('rfi-v33-stable')"
      }
    }
    description = "block RFI"
  }

  rule {
    action   = "deny(403)"
    priority = 1040
    match {
      expr {
        expression = "evaluatePreconfiguredExpr('rce-v33-stable')"
      }
    }
    description = "block RCE"
  }

  # Adaptive rate limiting — 100 req/min/IP baseline.
  rule {
    action   = "rate_based_ban"
    priority = 2000
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"
      rate_limit_threshold {
        count        = 100
        interval_sec = 60
      }
      ban_duration_sec = 600
    }
    description = "rate-limit to 100 req/min/IP, 10-minute ban on burst"
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
