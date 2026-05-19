# Backlog — Known Issues & Future Work

> Items discovered during testing. Prioritized by impact.

## UI/UX Polish

- [ ] **Hard refresh flashes light theme** — page briefly shows light mode then switches to dark. Likely a `next-themes` hydration issue where the class isn't set before first paint.
- [ ] **Emoji picker search not auto-focused** — when opening the emoji picker (change icon), the search input should be focused immediately so user can type to filter.
- [ ] **Spacing fine-tuning** — view mode spacing may need further adjustment to match Google Docs more closely. Current values are a middle ground.
- [ ] **Section expand arrow vs workspace header hierarchy** — section chevrons could be more visually distinct from workspace-level headers.
- [ ] **Long page title only visible on hover** — tooltip shows full title, but consider allowing text wrap or a wider sidebar option.

## Content Rendering

- [ ] **Smart chips in paragraphs** — chips outside tables show as "Smart chip" placeholder. Chip enrichment currently only works for table cells via HTML export. Needs full-doc HTML export matching.
- [ ] **Pageless/headless mode header image** — header images in pageless Google Docs templates don't render. Need to investigate where the Docs API stores these.
- [ ] **Checklist detection edge cases** — Google Docs uses `GLYPH_TYPE_UNSPECIFIED` with no glyph symbol for native checklists. Current detection covers this, but third-party checklist formats may differ.

## Auth & Security

- [ ] **Mobile login (cross-domain cookies)** — mobile browsers block `SameSite=none` cookies from different domains. Fix: put backend on same domain as frontend (e.g., `api-wiki.sarj.ai`) and set cookie `domain=.sarj.ai`.
- [ ] **Mobile iframe shows "you need access" for Slides / Docs / Sheets** — Google's embedded `/edit` view relies on the user's `docs.google.com` session cookies inside the iframe, but iOS Safari / Chrome iOS (both WebKit) block third-party cookies in iframes hard. OAuth-only sign-in on our side doesn't create a Google web session for the iframe to read. Options: (a) detect mobile and fall back to `/preview` (read-only but iframe-friendly), (b) detect mobile and show a "Open in Google" CTA instead of the iframe, (c) instruct users to open `docs.google.com` once in their mobile browser to seed a first-party Google session — current de-facto workaround.
- [ ] **Login page flash on expired token** — mostly fixed (silent refresh before redirect), but edge cases may remain if refresh token is also expired.
- [ ] **Styled 403 page for Cloud Armor denials** — when Cloud Armor blocks a request (WAF false positive, non-Cloudflare source) the browser shows a blank Google 403 page. Add a styled error page, either via a Cloudflare Workers rule that intercepts origin 403s for `wiki.sarj.ai` / `api-wiki.sarj.ai` and renders a branded page, or via GCLB `default_url_redirect` / custom error responses on the URL map.

## Sync

- [ ] **Force sync slow for large trees** — scanning 1600+ folders takes minutes. Optimization: with SA as Shared Drive member, use flat `files.list` query instead of per-folder listing.
- [ ] **Incremental sync may miss new folders** — Changes API tracks file changes but may not detect new folders created by users (not the SA). Force sync is the workaround.

## Deferred from Audit

- [ ] **Prop drilling in sidebar** — `WorkspaceSection` accepts 22+ props. Consider React context to reduce drilling.
- [ ] **Synthetic keyboard event for search** — search button fakes `Cmd+K` event. Should use a shared callback/context instead.

## Post-Cutover Infrastructure

> Sequence after the wiki cutover lands and stabilises. None of these block launch. Suggested order: secrets → docs → Cloud Build → IAM DB.

- [ ] **Secrets out of `terraform.tfvars` into Secret Manager** — Refactor `terraform/secrets.tf` to read secret values via `data "google_secret_manager_secret_version"` instead of `var.*`. Tfvars then carries only non-sensitive config (project, region, hostnames, tier) and becomes committable — eliminates the chronic gitignore footgun. Needs a one-time bootstrap script to seed Secret Manager before first apply. ~half-day.
- [ ] **From-scratch setup docs** — Extend `terraform/README.md` with a "From scratch" section: GCP project creation, foundation APIs to enable before terraform, GCS state bucket, WIF (or Cloud Build trigger if that migration lands first), OAuth client + initial redirect URIs, Cloudflare zone prerequisites, Secret Manager bootstrap, first-apply ordering. Capture what was actually done during cutover while it's fresh. ~1–2 hrs.
- [ ] **Cloud Build for deploys** — Move `deploy-backend.yml` + `deploy-frontend.yml` off GitHub Actions onto Cloud Build triggered on push to `main`. Drops Workload Identity Federation, native AR push (same-network), audit trail in Cloud Logging. Keep GitHub Actions for CI (lint/typecheck/test/PR build smoke). ~half-day; touches `cicd.tf`, adds `cloudbuild.yaml`, retires WIF resources.
- [ ] **Passwordless DB (Cloud SQL IAM authentication)** — Switch Cloud SQL from password to IAM auth. Backend SA presents OAuth tokens via the Cloud SQL Python Connector or an Auth Proxy sidecar with `--auto-iam-authn`. Removes the `db_password` secret entirely and ties DB access to GCP IAM (revoke SA = revoke DB). Touches terraform (IAM SA user + `cloudsql_iam_authentication=on` flag), backend driver, alembic flow, local dev (Cloud SQL Proxy + `gcloud auth login`). ~1–2 days — non-trivial token-refresh + dev-loop work; tackle alone, not bundled with anything else.
