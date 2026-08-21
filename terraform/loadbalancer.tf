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
  name     = "sarj-docs-backend-service"
  protocol = "HTTPS"
  # ``port_name`` deliberately omitted — Serverless NEGs (Cloud Run)
  # don't accept it; GCP rejects ``port_name`` for serverless backends
  # with "Port name is not supported for a backend service with
  # Serverless network endpoint groups." Only instance-group NEGs need
  # this field.
  timeout_sec           = 30
  load_balancing_scheme = "EXTERNAL_MANAGED"
  enable_cdn            = false

  backend {
    group = google_compute_region_network_endpoint_group.backend_neg.id
  }

  edge_security_policy = google_compute_security_policy.edge_armor.id
  security_policy      = google_compute_security_policy.backend_armor.id

  log_config {
    enable      = true
    sample_rate = 1.0
  }
}

# The edge policy establishes Cloudflare as the only intended public path before
# requests reach the backend WAF. Its default deny remains in preview until the
# observed policy logs prove the allowlist covers production traffic.
resource "google_compute_security_policy" "edge_armor" {
  name = "sarj-docs-edge-armor"
  type = "CLOUD_ARMOR_EDGE"

  rule {
    action   = "allow"
    priority = 600
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
    priority = 2147483647
    preview  = true
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    description = "preview deny for non-Cloudflare origins"
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
# Backend rate rules are added only after the edge origin policy is enforced;
# otherwise their conforming action could bypass this policy's source gate.
# SlowAPI remains the temporary limiter during that no-gap rollout.
resource "google_compute_security_policy" "backend_armor" {
  name = "sarj-docs-backend-armor"

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

  # SQLi at sensitivity level 1 (paranoia 1 only). The cutover-night
  # post-mortem: paranoia levels 2-4 rules (942200, 942260, 942421,
  # 942432, ...) all match on base64-url-encoded strings, which is
  # exactly the shape of the JWT cookie this app sends on every
  # authenticated request. We exhausted four explicit opt-outs and a
  # fifth rule started firing on the next click. Rather than continue
  # whack-a-mole, the SQLi rule is now scoped to paranoia level 1 —
  # still catches the obvious patterns (UNION SELECT, OR 1=1,
  # sleep(), etc.) without matching JWTs. Real SQLi defense lives in
  # the application: every query goes through parameterised
  # SQLAlchemy, and Cloudflare's edge WAF still runs upstream of
  # GCLB on the orange-cloud path.
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
  default_service = google_compute_backend_service.backend.id
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
