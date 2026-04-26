# Sarj Navigator — Project Reference for Claude

## Overview

Notion-like sidebar layered on top of Google Drive for `@sarj.ai`. The user's real Drive hierarchy **is** the sidebar — we don't maintain a parallel tree. Postgres is an overlay for icons, per-user ordering, and UI prefs; nothing in our DB is authoritative and all of it can be dropped and rebuilt from Drive.

## Stack

| Layer    | Tech                                               |
|----------|----------------------------------------------------|
| Frontend | Next.js 16 App Router, React 19, shadcn, Tailwind  |
| Backend  | FastAPI, Python 3.14                               |
| DB       | Postgres, SQLAlchemy async, Alembic                |
| Auth     | Google OAuth 2.0 (restricted to `@sarj.ai`)        |
| Content  | Google Drive / Docs (iframe embedded)              |

## Backend layout

Every module that holds business logic follows the same shape: `service.py` defines an ABC + concrete implementation, `dependencies.py` wires it through FastAPI `Depends`, `router.py` is a thin HTTP adapter.

```
app/
  auth/       AuthService (OAuth exchange, JWT lifecycle, token_version revocation)
  drive/      DriveService (drives.list, tree walk, search, snippets); snippets.py pure helpers
  extras/     ExtrasService (icons, sort order, prefs — Postgres-only)
  files/      FilesService (Drive file CRUD + strict-scope check via DriveService)
  sidebar/    SidebarCacheService (drive-tree cache, stale-while-revalidate, singleflight)
  models/     SQLAlchemy models (user + five Navigator overlay tables)
  config.py   pydantic-settings, loaded once at startup
  logging_config.py   loguru sink + stdlib intercept handler
  main.py     FastAPI app, lifespan, rate limiter, routers
```

Routers never call Google APIs or the DB directly — they resolve a service via `Depends` and pass through. `__init__.py` files are empty; every call site imports from the module that actually owns the symbol.

## Tooling

- **Python deps/scripts**: `uv` only. Never `pip`, `pip3`, `venv`, `python -m venv`.
- **Shell**: use relative paths; cwd is already repo root.
- **Checkers run on every change**: `ruff`, `mypy` (strict), `pyright` (strict), `ty`, `vulture`. All must pass. Tests run with `uv run pytest`.

## Architecture rules

1. Next.js never calls Google APIs, touches the DB, or runs business logic — only FastAPI endpoints.
2. FastAPI owns all Google API calls, tokens, DB reads/writes, and auth enforcement.
3. Service logic lives in `XxxService` classes behind ABCs, wired via `Depends`. Routers are thin.
4. If a technical choice or API behavior is uncertain, ask — don't invent.
5. Deliver iteratively; don't rewrite the app in one response.

## Code standards

Audit commands at `/sarj-audit:*` (from the `sarj-agentic` plugin, enabled via `.claude/settings.json`) check these. Apply them when writing or reviewing code:

- **Stepdown rule** — Each file reads top-to-bottom. Exported/public functions sit at the top (below imports / types / constants); each function only calls helpers defined below it. Reader sees intent before implementation.
- **Library-first** — Don't hand-roll what a well-maintained package already covers (date math, slugs, retries, debounce, CSV, UUIDs, etc.). Reach for the package; reserve hand-rolling for cases where the library would be heavier than what it replaces.
- **Move logic down the stack** — Push work to the lowest layer that can handle it: client → server (server components / loaders, not `useEffect` fetches); app code → SQL (filter / group / sort / join in the query, not in JS); raw HTML → shadcn primitives (`Button`, `Select`, `Dialog`, etc., not hand-rolled `<button>` / `<div>` patterns).
- **Service layer + DI** — already enforced (Architecture rules #3): business logic in `XxxService` behind ABCs, wired via FastAPI `Depends`; routers stay thin.
- **Strict linting** — `ruff`, `mypy --strict`, `pyright --strict`, `ty`, `vulture` on every change (see Tooling). Don't downgrade rules to `warn` to silence them — fix the violation.

## Auth & security

- Google OAuth 2.0 only; domain-restricted to `@sarj.ai` in `AuthService._enforce_domain`.
- Google access + refresh tokens stored server-side, Fernet-encrypted. Never sent to the client.
- FastAPI issues its own JWT after OAuth callback. JWT is the client credential (cookie or `Authorization: Bearer`).
- `users.token_version` embedded in JWT; logout bumps it, invalidating every prior token.
- Refresh tokens rotate on use.
- JWT cookie: `max_age` = JWT expiry, `SameSite=lax` dev, `SameSite=none` prod.
- `AuthError` is a typed exception hierarchy (`AuthFailedError`, `DomainRestrictedError`, `TokenRevokedError`) — each carries a `redirect_key` the callback router maps to the frontend's `?error=...` param.

## Access control

**Drive membership is the only source of truth.** We store no `page_permissions`, no `workspace_memberships`. A user sees a drive/folder/file iff Google Drive says they can. `DriveService.list_drives` is the boundary — results from there decide what appears in the sidebar.

## Database schema

```sql
users:
  id, email, google_id, display_name, avatar_url,
  encrypted_access_token, encrypted_refresh_token, token_expiry,
  token_version  -- bumped on logout; embedded in JWTs for revocation

drive_tree_cache:
  drive_id PK, tree (JSONB), refreshed_at, refreshed_by
  -- one row per shared drive; amortized across members

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

```
https://docs.google.com/document/d/{DRIVE_ID}/edit?embedded=true
```

FastAPI returns `google_drive_id`; the client builds the iframe URL. The browser's existing Google session handles iframe auth (all users are `@sarj.ai` Workspace members).

## Google OAuth scopes

- `openid`, `email`, `profile`
- `https://www.googleapis.com/auth/drive` — read/write Drive

No service account, no Admin SDK, no domain-wide delegation.

## Theme & dark mode

- Design tokens live in `frontend/src/app/globals.css` (`:root` + `.dark`), exposed via Tailwind's `@theme inline` block. Use the CSS variables — never hard-code colors.
- Font: Geist / Geist Mono.
- Dark mode via `next-themes` (`attribute="class"`); defaults to `prefers-color-scheme`; manual toggle persists in `localStorage`.

## Logging

`loguru` is the single logging path. `app/logging_config.py` installs a JSON sink (prod, driven by `LOG_JSON`) or pretty sink (dev) and routes stdlib `logging` (uvicorn, fastapi, sqlalchemy) through an InterceptHandler so every line flows through one format. Inside app code: `from loguru import logger` and `logger.info("msg with {arg}", arg=value)`.

## Environment variables

Loaded via `pydantic-settings` in `app/config.py`; `extra="ignore"` tolerates stale legacy keys.

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres DSN |
| `SECRET_KEY` | JWT signing key (≥32 chars) |
| `FERNET_KEY` | Token encryption key (valid Fernet key) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | OAuth credentials |
| `GOOGLE_REDIRECT_URI` | OAuth callback URL |
| `ALLOWED_DOMAIN` | Default `sarj.ai` |
| `FRONTEND_URL` | CORS origin + cookie secure-mode inference |
| `LOG_JSON` | Emit JSON log lines (default `false`) |
| `LOG_LEVEL` | Default `INFO` |
