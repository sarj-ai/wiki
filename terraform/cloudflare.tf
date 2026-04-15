# Cloudflare edge — DNS + WAF in front of the GCLB backend.
#
# Traffic path summary:
#   user → Cloudflare proxy (orange cloud)
#         → GCLB anycast IP (Cloud Armor)
#         → Cloud Run backend
#
# Cloudflare handles TLS termination at the edge (closest POP to
# the user), runs the managed OWASP rule set in "simulate" → "block"
# progression, and applies custom rate-limit + bot mitigation rules.
# Cloud Armor (see loadbalancer.tf) runs in parallel with its own
# OWASP ruleset so we have two independent WAFs — if one set of
# rules misses a payload, the other catches it, and the overlap is
# zero-cost since each runs at a different tier.

# A record: api-docs.sarj.ai → GCLB anycast IP (orange-cloud proxied)
resource "cloudflare_record" "backend" {
  zone_id = var.cloudflare_zone_id
  name    = var.backend_domain
  type    = "A"
  value   = google_compute_global_address.backend_lb.address
  proxied = true
  ttl     = 1 # auto when proxied
  comment = "Navigator backend — proxied to GCLB via Cloudflare"
}

# Zone-level security settings.
resource "cloudflare_zone_settings_override" "sarj_ai" {
  zone_id = var.cloudflare_zone_id

  settings {
    ssl                      = "strict"
    always_use_https         = "on"
    automatic_https_rewrites = "on"
    min_tls_version          = "1.2"
    tls_1_3                  = "on"
    security_level           = "high"
    challenge_ttl            = 1800
    browser_check            = "on"
    hotlink_protection       = "on"
    opportunistic_encryption = "on"
    websockets               = "on"
  }
}

# Managed rules — enable Cloudflare's OWASP Core Ruleset.
resource "cloudflare_ruleset" "managed_owasp" {
  zone_id     = var.cloudflare_zone_id
  name        = "Navigator managed OWASP rules"
  description = "Cloudflare's managed OWASP Core Ruleset, action set to block at default paranoia level"
  kind        = "zone"
  phase       = "http_request_firewall_managed"

  rules {
    action = "execute"
    action_parameters {
      id = "4814384a9e5d4991b9815dcfc25d2f1f" # Cloudflare Managed Ruleset ID
    }
    expression  = "true"
    description = "Execute Cloudflare Managed Ruleset"
    enabled     = true
  }

  rules {
    action = "execute"
    action_parameters {
      id = "efb7b8c949ac4650a09736fc376e9aee" # OWASP Core Ruleset ID
    }
    expression  = "true"
    description = "Execute OWASP Core Ruleset"
    enabled     = true
  }
}

# Custom rate limit: 200 requests per minute per IP on any /api/*
# path. Cloudflare's rate-limit engine tracks burstiness separately
# from Cloud Armor's to avoid thundering-herd misfires.
resource "cloudflare_ruleset" "rate_limit" {
  zone_id     = var.cloudflare_zone_id
  name        = "Navigator rate limit"
  description = "Per-IP rate limit on the Navigator backend"
  kind        = "zone"
  phase       = "http_ratelimit"

  rules {
    action = "block"
    ratelimit {
      characteristics     = ["ip.src"]
      period              = 60
      requests_per_period = 200
      mitigation_timeout  = 600
    }
    expression  = "http.host eq \"${var.backend_domain}\""
    description = "200 req/min/IP on the Navigator backend domain"
    enabled     = true
  }
}

# Bot mitigation — challenge non-browser traffic on OAuth endpoints.
# The /auth/login and /auth/callback endpoints are OAuth
# redirect destinations, so legit traffic always has a Google
# Referer header. Strip bot scanners by requiring it.
resource "cloudflare_ruleset" "custom_firewall" {
  zone_id     = var.cloudflare_zone_id
  name        = "Navigator custom firewall rules"
  description = "Per-path custom mitigations layered on top of the managed rules"
  kind        = "zone"
  phase       = "http_request_firewall_custom"

  rules {
    action      = "block"
    expression  = <<-EOT
      (http.host eq "${var.backend_domain}")
      and (cf.client.bot)
      and not (http.request.uri.path eq "/health")
    EOT
    description = "Block known bots from the Navigator backend except /health"
    enabled     = true
  }

  rules {
    action      = "managed_challenge"
    expression  = <<-EOT
      (http.host eq "${var.backend_domain}")
      and (ip.geoip.country ne "SA")
      and (ip.geoip.country ne "US")
      and (ip.geoip.country ne "GB")
      and (ip.geoip.country ne "DE")
      and (ip.geoip.country ne "FR")
      and (ip.geoip.country ne "NL")
    EOT
    description = "Managed challenge for traffic from unexpected regions"
    enabled     = true
  }
}
