# Sarj Wiki Terraform

Infrastructure-as-code for the Sarj Wiki production environment
on GCP + Cloudflare. Updated for Sub-PR B (rev 8 of
`PIVOT_PLAN.md`).

## Layout

| File | Purpose |
|---|---|
| `main.tf` | GCP + Cloudflare providers, enabled APIs |
| `versions.tf` | Terraform + provider version pins, GCS backend |
| `variables.tf` | All inputs (project/region, domains, OAuth, Cloudflare) |
| `network.tf` | VPC, private services access, VPC connector for Cloud Run → Cloud SQL |
| `database.tf` | Cloud SQL Postgres instance + user + database |
| `secrets.tf` | Secret Manager entries for DB URL, Fernet, OAuth secrets |
| `iam.tf` | Service accounts and IAM bindings |
| `cloudrun.tf` | Cloud Run backend service definition |
| `loadbalancer.tf` | Global HTTPS LB + Cloud Armor WAF + managed TLS cert *(Sub-PR B, new)* |
| `cloudflare.tf` | Cloudflare DNS + OWASP managed rules + rate limit + bot mitigation *(Sub-PR B, new)* |
| `security.tf` | Security Command Center enablement + branch protection docs *(Sub-PR B, new)* |
| `registry.tf` | Artifact Registry for backend Docker images |
| `cicd.tf` | Workload Identity Federation for GitHub Actions deploys |
| `monitoring.tf` | Cloud Monitoring alert policies |
| `outputs.tf` | Useful outputs (LB IP, backend URL, etc.) |

## What was removed from the legacy Sarj Wiki stack

- `scheduler.tf` — Cloud Scheduler jobs for webhook renewal and
  group sync are deleted along with the `/internal` endpoints.
- `sarj-docs-scheduler` service account — no longer needed.
- Secret Manager entries `webhook-token` and `service-account-key`
  — no Drive push notifications, no domain-wide-delegation SA.
- Cloud Run env vars `DRIVE_WEBHOOK_TOKEN`, `DRIVE_ROOT_FOLDER_ID`,
  `ADMIN_IMPERSONATE_EMAIL`, `SERVICE_ACCOUNT_KEY_PATH`,
  `WEBHOOK_BASE_URL`, `CLOUD_SCHEDULER_SA_EMAIL` — all deleted.
- Cloud Run `/secrets/service-account-key` volume mount — gone.
- `cloudscheduler.googleapis.com` API — no longer enabled.
- Terraform variables `admin_impersonate_email`,
  `drive_root_folder_id`, `service_account_key_path` — all deleted.

## Apply (fresh-ish deploy)

One-time prerequisites:

1. GCP project `sarj-docs` exists and `$USER` has
   `roles/owner` on it.
2. GCS bucket `sarj-docs-terraform-state` exists in the same project
   (for Terraform's remote state).
3. Cloudflare API token with `Zone:Edit` + `Firewall Services:Edit`
   scoped to the `sarj.ai` zone.
4. A `terraform.tfvars` file (based on `terraform.tfvars.example`)
   with all required variables filled in.

```sh
cd terraform/

# Bootstrap
terraform init

# Dry run — verify the plan matches expectations, especially the
# "resources to be destroyed" list (scheduler jobs, webhook-token
# secret, service-account-key volume, legacy env vars).
terraform plan

# Apply — requires Cloudflare API token in TF_VAR_cloudflare_api_token
# or cloudflare_api_token in terraform.tfvars.
terraform apply
```

## Sub-PR B additions explained

### Google Cloud Load Balancer (`loadbalancer.tf`)

Google Cloud's external HTTPS load balancer with:

- **Global anycast IP** (`google_compute_global_address`) — the
  only public entry point users hit. Cloudflare A-records point
  at this IP.
- **Serverless NEG** — wraps the Cloud Run service so the LB can
  route to it without exposing the `run.app` URL.
- **Cloud Armor security policy** — GCP's WAF, runs in parallel
  with Cloudflare's edge WAF. Enabled preconfigured rules for XSS,
  SQLi, LFI, RFI, and RCE from the OWASP ModSecurity CRS, plus a
  rate-based-ban rule capping per-IP traffic at 100 req/min.
- **Google-managed TLS cert** via Certificate Manager, scoped to
  `backend_domain`.
- **HTTP → HTTPS 301 redirect** so port-80 traffic gets upgraded
  before it reaches Cloud Run.

### Cloudflare (`cloudflare.tf`)

Edge perimeter with defense-in-depth over Cloud Armor:

- **DNS**: A-record for `backend_domain` pointing at the LB IP,
  proxied (orange cloud) so all traffic flows through Cloudflare.
- **Zone settings**: strict SSL, always-HTTPS, min TLS 1.2, TLS 1.3
  enabled, high security level, browser-integrity check on.
- **Managed OWASP ruleset** enabled at zone level.
- **Custom rate limit**: 200 req/min/IP on the backend domain
  (independent of Cloud Armor's 100 req/min).
- **Bot mitigation**: block known bots except on `/health`, and
  managed-challenge requests originating from unexpected regions.

### Security Command Center (`security.tf`)

Enables the `securitycenter.googleapis.com` API so SCC can scan
Cloud Run, Cloud SQL, and Secret Manager for misconfigurations.
Organization-level settings are left as a documented placeholder
(they require the org admin role, which Sarj doesn't necessarily
hold at the GCP-org level).

### Branch protection

Managed via GitHub API rather than Terraform — see
`scripts/github/README.md` for the `gh api` command + the
`branch-protection.json` payload.

## Secret rotation playbook

Secret Manager holds Fernet, SECRET_KEY, and Google OAuth creds.
To rotate:

```sh
# Generate a new version without breaking the running service.
echo -n "NEW_VALUE" | gcloud secrets versions add sarj-docs-secret-key --data-file=-

# Cloud Run will pick up the new "latest" on the next deploy or
# revision update. Force a new revision:
gcloud run services update sarj-docs-backend --region=me-central1

# Once verified, disable the old version:
gcloud secrets versions disable OLD_VERSION --secret=sarj-docs-secret-key
```

## Cost posture (rough)

| Component | Order-of-magnitude monthly |
|---|---|
| Cloud Run (1 always-on instance, 1 vCPU / 512 MB) | ~$25 |
| Cloud SQL (db-f1-micro) | ~$10 |
| Global LB + forwarding rule + Cloud Armor | ~$25 |
| Certificate Manager | free for the first cert |
| Secret Manager | <$1 for active secret count |
| Cloudflare Free tier | $0 |
| Security Command Center Standard | $0 |
| **Total** | **~$60/month** |

Does not include Drive / Workspace licenses, which are orthogonal.
