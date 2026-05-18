# Security Command Center (SCC) + branch protection docs.
#
# SCC is GCP's unified security posture dashboard. Enabling the
# ``securitycenter.googleapis.com`` API (see main.tf) lets an
# operator run SCC scans against the Cloud Run service, Cloud SQL
# instance, and Secret Manager secrets from the GCP Console.
#
# There's no Terraform resource to enable a Standard-tier SCC scan
# — the API-enable is the whole terraform-side of it, and the scan
# itself is triggered from the Console under
# Security → Security Command Center → Settings. The findings
# triage and any remediation PRs are out-of-band operator work;
# PIVOT_PLAN §8.1 earmarks them as "a dedicated sub-PR after the
# first SCC scan runs".
#
# Organization-level SCC settings (enable_asset_discovery,
# inclusion_mode, etc.) require the organization admin role and
# live one level above this project. If Sarj later wants to
# manage SCC at the org level via Terraform, add a
# google_scc_v2_organization_source resource here.
#
# ---------------------------------------------------------------------------
# Branch protection for ``main``
# ---------------------------------------------------------------------------
#
# Managed via the GitHub API rather than Terraform because the
# hashicorp/github provider requires a PAT with repo-admin scope
# that we don't want to persist in Terraform state. See
# scripts/github/README.md for the ``gh api`` command and the
# branch-protection.json payload. Apply on cutover night after the
# feature branch has merged.
#
# Required settings at a glance:
#   * Pull request with >= 1 approving review
#   * Required status checks: CI Backend + CI Frontend jobs (see ``scripts/github/branch-protection.json``)
#   * Conversation resolution required
#   * Signed commits
#   * No force push, no deletion
