# Sarj Wiki — Project Reference for Claude

Before editing or committing, run `make setup-hooks`. It idempotently installs
Lefthook 2.1.10; root `make setup`, backend `make setup`, frontend `npm install`,
`npm ci`, and `npm run dev` also install it automatically. Fix hook failures and
retry the commit instead of bypassing them.

Run `make standards-upgrade` to upgrade every Standards package, refresh both locks,
and regenerate all six canonical configs as one change. `make standards-check`, hooks,
and CI reject config or lock drift. `scripts/check_standards.py` discovers every
installed Python, SQL, and IaC registry rule, so never maintain a handwritten hook list.

`make check` is the exact local mirror of CI (pre-push runs it; each step is tagged
with the workflow step it mirrors — keep the twins in sync). `make fix` applies every
available autofix. `sarj-lint-configs doctor` cross-checks every Standards version pin
against `.sarj-standards.toml`, the one authoritative pin site.

## Overview

Notion-like sidebar layered on top of Google Drive for `@sarj.ai`. The user's real Drive hierarchy **is** the sidebar — we don't maintain a parallel tree. Postgres is an overlay for icons, per-user ordering, and UI prefs; nothing in our DB is authoritative and all of it can be dropped and rebuilt from Drive.

## Stack

| Layer    | Tech                                              |
| -------- | ------------------------------------------------- |
| Frontend | Next.js 16 App Router, React 19, shadcn, Tailwind |
| Backend  | FastAPI, Python 3.14                              |
| DB       | Postgres, SQLAlchemy async, Alembic               |
| Auth     | Google OAuth 2.0 (restricted to `@sarj.ai`)       |
| Content  | Google Drive / Docs (iframe embedded)             |

## Repo layout

```text
backend/    FastAPI app (see "Backend layout" below)
frontend/   Next.js 16 App Router
terraform/  infra
scripts/    one-off ops scripts
```

## Backend layout

Most modules that hold business logic follow the same shape: `service.py` defines an ABC + concrete implementation, `dependencies.py` wires it through FastAPI `Depends`, `router.py` is a thin HTTP adapter. Module-local helpers (`errors.py`, `schemas.py`, retry/encryption/jwt utilities) sit alongside as needed. Two modules deviate: `sidebar/` names its service file `cache.py` (the cache _is_ the service), and `drive/` has no `router.py` of its own — its listing endpoints are mounted from `sidebar/router.py`, and only its full-text search endpoint lives in `drive/search_router.py`.

```text
backend/app/
  auth/       AuthService (OAuth exchange, JWT lifecycle, token_version revocation)
              + encryption.py (Fernet), jwt.py, errors.py (AuthError hierarchy)
  drive/      DriveService (drives.list, folder walk, listing, search, snippets)
              + search_router.py (only HTTP surface this module exposes itself)
              + drives_cache.py, retry.py (tenacity), schemas.py, snippets.py (pure helpers)
              listing endpoints live in sidebar/router.py — drive/ has no router.py
  extras/     ExtrasService (icons, sort order, prefs — Postgres-only)
              + router.py, errors.py, schemas.py
  files/      FilesService (Drive file CRUD + strict-scope check via DriveService)
              + router.py, breadcrumb.py, errors.py (FilesError hierarchy), schemas.py
  sidebar/    SidebarCacheService in cache.py (drive-tree cache, stale-while-revalidate)
              + router.py (sidebar + drive listing endpoints), singleflight.py
  models/     SQLAlchemy models — user.py + wiki.py (five Sarj Wiki overlay tables)
  config.py   pydantic-settings, loaded once at startup
  database.py async engine + session factory
  logging_config.py   loguru sink + stdlib intercept handler
  main.py     FastAPI app, lifespan, slowapi rate limiter, routers
```

Routers never call Google APIs or the DB directly — they resolve a service via `Depends` and pass through. `__init__.py` files are empty; every call site imports from the module that actually owns the symbol.

## Tooling

- **Python deps/scripts**: `uv` only. Never `pip`, `pip3`, `venv`, `python -m venv`.
- **Shell**: use relative paths; cwd is already repo root.
- **Backend checkers (blocking, run on every change)**: `ruff`, `mypy` (strict), `pyright` (strict), `ty`, `vulture`, `lint-imports` (import-linter layering contracts in `pyproject.toml`). All must pass. Tests run with `uv run pytest`; CI enforces `--cov-fail-under=70` (a ratchet — raise, never lower).
- **Frontend checkers (blocking)**: `prettier`, `eslint`, `tsc` (TS 6, extends `@sarj/tsconfig/strict.json`), `knip`, `type-coverage --at-least 99`.
- **API contract**: `frontend/openapi.json` is the committed contract artifact; `frontend/src/lib/generated/` (hey-api client + zod schemas) is git-ignored and regenerated from it on `npm install` (postinstall) or via `npm run generate:api`. After changing any backend Pydantic model: `cd backend && uv run python scripts/export_openapi.py ../frontend/openapi.json && cd ../frontend && npm run generate:api`. On PRs, CI (`ci-quality.yml` api-contract) regenerates and auto-commits `openapi.json` drift to the branch; direct pushes fail on drift.
- **Repo-wide pre-commit (blocking in CI via ci-quality.yml)**: hygiene hooks, gitleaks, actionlint, and the `sarj-*` AST rules from `sarj-ai/standards`. Suppress a sarj finding inline with `# sarj-noqa: SARJ00X — reason`.
- **Canonical strict layers (blocking)**: org-strict Ruff (`backend/.ruff-strict.toml`), org-strict pyright (`backend/.pyright-strict.json`), `pip-audit`, and Trivy scans. Strict files are byte-for-byte canonical; repository-specific overrides belong in `backend/pyproject.toml` or `backend/pyrightconfig.json`, never in generated files.

## Architecture rules

1. Next.js never calls Google APIs, touches the DB, or runs business logic — only FastAPI endpoints.
2. FastAPI owns all Google API calls, tokens, DB reads/writes, and auth enforcement.
3. Service logic lives in `XxxService` classes behind ABCs, wired via `Depends`. Routers are thin.
4. If a technical choice or API behavior is uncertain, ask — don't invent.
5. Deliver iteratively; don't rewrite the app in one response.

## Code standards

Audit commands at `/sarj-audit:*` (from the `sarj-ai/standards` plugin marketplace, enabled via `.claude/settings.json`) check these. Apply them when writing or reviewing code:

- **Stepdown rule** — Each file reads top-to-bottom. Exported/public functions sit at the top (below imports / types / constants); each function only calls helpers defined below it. Reader sees intent before implementation.
- **Library-first** — Don't hand-roll what a well-maintained package already covers (date math, slugs, retries, debounce, CSV, UUIDs, etc.). Reach for the package; reserve hand-rolling for cases where the library would be heavier than what it replaces.
- **Move logic down the stack** — Push work to the lowest layer that can handle it: client → server (server components / loaders, not `useEffect` fetches); app code → SQL (filter / group / sort / join in the query, not in JS); raw HTML → shadcn primitives (`Button`, `Select`, `Dialog`, etc., not hand-rolled `<button>` / `<div>` patterns).
- **Service layer + DI** — already enforced (Architecture rules #3): business logic in `XxxService` behind ABCs, wired via FastAPI `Depends`; routers stay thin.
- **Strict linting** — all blocking checkers in Tooling on every change. Don't downgrade rules to `warn` to silence them — fix the violation (or add a narrowly-scoped inline `# noqa: CODE — reason` / `# sarj-noqa: SARJ00X — reason` with justification).

## Auth & security

- Google OAuth 2.0 only; domain-restricted to `@sarj.ai` in `AuthService._enforce_domain`.
- Google access + refresh tokens stored server-side, Fernet-encrypted. Never sent to the client.
- FastAPI issues its own JWT after OAuth callback. JWT is the client credential (cookie or `Authorization: Bearer`).
- `users.token_version` embedded in JWT; logout bumps it (invalidating every prior token) and revokes the Google refresh token at `oauth2.googleapis.com/revoke`.
- Refresh re-issues a new token pair but does NOT yet rotate/expire the old refresh token (no reuse-detection); a stolen refresh token stays valid until logout or its 7-day expiry. Per-token-family rotation is a tracked follow-up (see `.github/BACKLOG.md`).
- JWT cookie: `max_age` = JWT expiry, `SameSite=lax` (both dev and prod).
- `AuthError` is a typed exception hierarchy (`AuthFailedError`, `DomainRestrictedError`, `TokenRevokedError`) — each carries a `redirect_key` the callback router maps to the frontend's `?error=...` param.

## Access control

**Drive membership is the only source of truth.** We store no `page_permissions`, no `workspace_memberships`. A user sees a drive/folder/file iff Google Drive says they can. `DriveService.list_drives` is the boundary — results from there decide what appears in the sidebar.

**Shortcuts (`application/vnd.google-apps.shortcut`) are surfaced as native nodes** with a `Link2` overlay glyph. `_walk_folders_sync` and `_list_children_sync` include shortcuts in their queries, pull `shortcutDetails(targetId, targetMimeType)`, and feed the result through `_resolve_shortcut_nodes` — an async resolver that probes each shortcut's target via `files.get` in parallel. Probe outcomes:

- `200 OK` → shortcut renders normally; `shortcut_target_drive_id`/`shortcut_target_drive_name` populated for the tooltip.
- `403` → `shortcut_inaccessible=True`; the row renders greyed-out with a "contact admin" tooltip and click is suppressed (sidebar) / Open is disabled (search).
- `404` or `trashed=true` → the shortcut is dropped from results entirely (auto-prune).

Drive's ACL on the target is the only source of truth. The shortcut entry's own ACL (which controls _visibility_ of the row) is independent — shortcuts can be visible to users who lack target access, hence the inaccessible-state UX. Folder shortcuts expand by listing the _target's_ children via the existing `/sidebar/folders/{id}/files` endpoint; their subtree is lazy-fetched, never denormalized into the host drive's `drive_tree_cache`. Search results run through the same resolver so the palette greys out hits the user can't open.

## Database schema

```sql
users:
  id, email, google_id, display_name, avatar_url,
  encrypted_access_token, encrypted_refresh_token,
  token_version  -- bumped on logout; embedded in JWTs for revocation

drive_tree_cache:
  drive_id PK, tree (text: Fernet-encrypted DriveTree JSON), refreshed_at, refreshed_by
  -- one row per shared drive; amortized across members. Folder names are
  -- user content, so the blob is opaque to anyone reading the DB; rows
  -- older than CACHE_MAX_AGE (30d) are purged opportunistically on save

doc_icons:
  drive_id PK, icon, cover_url, updated_at, updated_by
  -- global, editable by anyone with Drive write access

user_folder_icons:
  (user_id, folder_drive_id) PK, icon, updated_at

user_sort_order:
  (user_id, drive_id) PK, sort_order (float)  -- fractional indexing

user_preferences:
  user_id PK, show_non_doc_files, hidden_workspace_ids[], updated_at
```

## Sidebar loader contract

Three endpoints, each independent so one slow drive doesn't gate the whole sidebar:

1. `GET /sidebar/drives` — `DriveService.list_drives()`. Access-control boundary.
2. `GET /sidebar/drives/{drive_id}/folders` — `SidebarCacheService.get_drive_tree()`. Served from `drive_tree_cache`; on miss, `DriveService.walk_drive_folders` runs a BFS through singleflight and writes back.
3. `GET /sidebar/folders/{folder_id}/files` — lazy file list on folder expand, also singleflight-coalesced.

No Drive webhooks, no `files.watch`, no push subscriptions. Cache staleness handled by TTL (`CACHE_TTL`) + stale-while-revalidate refreshes.

## Google Docs iframe

```text
https://docs.google.com/document/d/{DRIVE_ID}/edit?embedded=true
```

FastAPI returns `google_drive_id`; the client builds the iframe URL. The browser's existing Google session handles iframe auth (all users are `@sarj.ai` Workspace members).

## Google OAuth scopes

- `openid`, `email`, `profile`
- `https://www.googleapis.com/auth/drive` — read/write Drive

No service account, no Admin SDK, no domain-wide delegation.

## Theme & dark mode

- Design tokens live in `frontend/src/app/globals.css` (`:root` + `.dark`), exposed via Tailwind's `@theme inline` block. Use the CSS variables — never hard-code colors.
- Font: Nunito (sans, self-hosted from `frontend/public/fonts`) + Geist Mono.
- Dark mode via `next-themes` (`attribute="class"`); defaults to `prefers-color-scheme`; manual toggle persists in `localStorage`.

## Logging

`loguru` is the single logging path. `app/logging_config.py` installs a JSON sink (prod, driven by `LOG_JSON`) or pretty sink (dev) and routes stdlib `logging` (uvicorn, fastapi, sqlalchemy) through an InterceptHandler so every line flows through one format. Inside app code: `from loguru import logger` and `logger.info("msg with {arg}", arg=value)`.

## Environment variables

Loaded via `pydantic-settings` in `app/config.py`; `extra="ignore"` tolerates stale legacy keys.

Exactly one database path must be configured: `DATABASE_URL` (local dev / CI), or the `DB_*` connector trio (production — Cloud SQL IAM database auth; the service account is the credential, no password exists).

| Variable                                    | Purpose                                                          |
| ------------------------------------------- | ---------------------------------------------------------------- |
| `DATABASE_URL`                              | Postgres DSN (dev/CI path)                                       |
| `DB_INSTANCE_CONNECTION_NAME`               | Cloud SQL instance (`project:region:instance`, prod path)        |
| `DB_IAM_USER`                               | IAM database user (backend SA email sans `.gserviceaccount.com`) |
| `DB_NAME`                                   | Database name (default `sarj_wiki`)                              |
| `SECRET_KEY`                                | JWT signing key (≥32 chars)                                      |
| `FERNET_KEY`                                | Token encryption key (valid Fernet key)                          |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | OAuth credentials                                                |
| `GOOGLE_REDIRECT_URI`                       | OAuth callback URL                                               |
| `ALLOWED_DOMAIN`                            | Default `sarj.ai`                                                |
| `FRONTEND_URL`                              | CORS origin + cookie secure-mode inference                       |
| `LOG_JSON`                                  | Emit JSON log lines (default `false`)                            |
| `LOG_LEVEL`                                 | Default `INFO`                                                   |
