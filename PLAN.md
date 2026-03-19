# Sarj Docs — Build Plan

> Last updated: 2026-03-19

## Progress Overview

| Phase | Name | Status |
|-------|------|--------|
| 1 | GCP Setup | ✅ Done |
| 2 | Backend Foundation | ✅ Done |
| 3 | Authentication | ✅ Done |
| 4 | Workspace Sync | ✅ Done |
| 5 | Sidebar Tree API | ✅ Done |
| 6 | Drive Import & Webhooks | ✅ Done |
| 7 | Frontend Foundation | ✅ Done |
| 8 | Sidebar UI | ✅ Done |
| 9 | Document View | ✅ Done |
| 10 | Permissions UI | ✅ Done |
| 11a | Frontend Auth Flow | ✅ Done |
| 11b | Data Layer (React Query + Hooks) | ✅ Done |
| 11c | Backend: GET page + Permissions CRUD | ✅ Done |
| 11d | Schema Fixes (trigger + constraint) | ✅ Done |
| 11e | Drag-and-Drop | ✅ Done (sibling reorder) |
| 11f | Loading States & Toasts | ✅ Done |
| 11g | Search | ✅ Done (PostgreSQL tsvector full-text search) |
| 12 | Module Split & Code Quality | ✅ Done |
| 13 | Security Hardening | ✅ Done |
| 14 | Sync Rewrite | ✅ Done |

---

## What's Built

### Backend (`backend/`)

```
backend/
├── pyproject.toml
├── .env.example
├── .gitignore
├── alembic.ini
├── alembic/
│   ├── env.py                              # Async Alembic setup
│   ├── script.py.mako
│   └── versions/
│       ├── 001_initial_schema.py           # All 6 tables
│       └── 002_schema_fixes.py            # updated_at trigger + at_least_one_grantee constraint
├── app/
│   ├── __init__.py
│   ├── main.py                             # FastAPI app, CORS, all routers
│   ├── config.py                           # pydantic-settings
│   ├── database.py                         # Async SQLAlchemy engine/session/Base
│   ├── models/
│   │   ├── __init__.py                     # Re-exports all models
│   │   ├── user.py                         # User (with token_version)
│   │   ├── workspace.py                    # Workspace, WorkspaceMembership
│   │   ├── page.py                         # Page, PagePermission
│   │   └── drive_webhook.py               # DriveWebhookChannel
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── router.py                       # /auth/login, callback, logout, me, refresh
│   │   ├── jwt.py                          # JWT create/decode (access + refresh, token_version)
│   │   ├── encryption.py                   # Fernet encrypt/decrypt Google tokens
│   │   └── dependencies.py                # get_current_user (cookie-first, header fallback)
│   ├── docs_transformer/                   # Google Docs API → content blocks (13 modules)
│   ├── drive/
│   │   ├── credentials.py                 # Service account + user credential helpers
│   │   ├── files.py                       # Drive file operations
│   │   ├── folders.py                     # Folder chain creation, traversal
│   │   ├── content.py                     # Doc content fetching
│   │   └── changes.py                     # Drive Changes API for incremental sync
│   ├── pages/
│   │   ├── router.py                       # /pages/tree, GET/{id}, CRUD, publish, content
│   │   ├── schemas.py                      # PageCreate, PageUpdate, PageTreeNode, etc.
│   │   ├── service.py                      # Tree query, CRUD, access control
│   │   ├── sync.py                        # Two-pass Drive sync (full + incremental)
│   │   ├── background.py                  # Background task helpers
│   │   ├── tree.py                        # Tree building utilities
│   │   └── helpers.py                     # Shared page helpers
│   ├── permissions/
│   │   ├── router.py                       # GET /permissions/page/{id}, POST, DELETE
│   │   ├── schemas.py                      # PermissionCreate (with validator), PermissionResponse
│   │   └── service.py                      # list, grant, revoke (owner-only enforcement)
│   ├── search/
│   │   ├── router.py                       # GET /search?q=...
│   │   └── service.py                      # PostgreSQL tsvector full-text search
│   ├── webhooks/
│   │   ├── router.py                       # POST /webhooks/drive
│   │   └── service.py                      # Webhook verification + change processing
│   ├── workspaces/
│   │   ├── router.py                       # GET /workspaces, POST /workspaces/sync
│   │   ├── schemas.py                      # WorkspaceResponse
│   │   └── service.py                      # Admin SDK: groups sync, membership sync
│   └── internal/
│       ├── router.py                       # Cloud Scheduler endpoints (reindex, webhook renewal)
│       └── dependencies.py                # OIDC audience verification
```

**Key details:**
- Auth: Google OAuth → JWT in HTTP-only cookies, Fernet-encrypted Google tokens in DB, token versioning for logout
- Pages API: Tree endpoint uses single query + Python tree build, shared `_visible_pages_filter` for access control
- Permissions: Owner-only management, at_least_one_grantee validation (DB CHECK constraint)
- Drive sync: Two-pass architecture (collect from Drive, then apply to DB) with incremental Changes API support
- Search: PostgreSQL tsvector full-text search
- Internal endpoints: OIDC-verified Cloud Scheduler endpoints for reindex and webhook renewal

### Frontend (`frontend/`)

```
frontend/
├── src/
│   ├── app/
│   │   ├── layout.tsx                      # Root: Geist fonts, ThemeProvider, QueryProvider, AuthProvider, Toaster
│   │   ├── login/page.tsx                  # Google sign-in page
│   │   └── (app)/
│   │       ├── layout.tsx                  # App shell: SidebarProvider + AppSidebar
│   │       ├── page.tsx                    # "Select a document" placeholder
│   │       └── docs/[pageId]/
│   │           ├── page.tsx                # Document view (iframe + header + share button)
│   │           └── loading.tsx             # Route-level loading skeleton
│   ├── components/
│   │   ├── theme-provider.tsx
│   │   ├── query-provider.tsx              # React Query client provider
│   │   ├── auth-provider.tsx               # Auth context (useAuth hook)
│   │   ├── ui/                             # Shadcn components (auto-generated)
│   │   ├── sidebar/
│   │   │   ├── app-sidebar.tsx             # Full sidebar: header, tree, footer, toast actions
│   │   │   ├── workspace-switcher.tsx      # Workspace dropdown
│   │   │   ├── page-tree.tsx               # DndContext + SortableContext, loading prop
│   │   │   ├── page-tree-item.tsx          # useSortable, nested DnD contexts, hover actions
│   │   │   └── sidebar-skeleton.tsx        # Loading skeleton for sidebar
│   │   ├── document/
│   │   │   ├── document-header.tsx         # Breadcrumbs, icon, title, badges, share button
│   │   │   ├── document-iframe.tsx         # Google Docs iframe with loading state
│   │   │   └── document-skeleton.tsx       # Loading skeleton for document view
│   │   └── permissions/
│   │       ├── share-dialog.tsx            # Full share modal (search, permissions, visibility toggle)
│   │       └── share-button.tsx            # Opens share dialog
│   ├── lib/
│   │   ├── utils.ts                        # Shadcn cn() utility
│   │   ├── api.ts                          # Full API client with 401 auto-retry (all endpoints)
│   │   ├── auth.ts                         # Login URL, fetchCurrentUser, refreshToken, logout
│   │   ├── mock-data.ts                    # Mock pages, workspaces, users, permissions
│   │   └── page-utils.ts                   # Breadcrumb builder
│   ├── types/
│   │   └── index.ts                        # Page, Workspace, User, PagePermission, Permission
│   └── hooks/
│       ├── use-mobile.ts                   # Shadcn mobile detection
│       ├── use-page-tree.ts                # React Query hook (API with mock fallback)
│       ├── use-workspaces.ts               # React Query hook (API with mock fallback)
│       └── use-current-user.ts             # React Query hook (API with mock fallback)
```

**Key details:**
- Auth: Login page, AuthProvider context, auto-redirect on 401
- Data layer: React Query hooks for page tree, workspaces, current user — all fall back to mock data when backend is unavailable
- API client: Full typed wrapper with 401 → token refresh → retry logic
- Sidebar: Drag-and-drop reorder (siblings only, per-level DnD contexts), toast feedback
- Document view: Breadcrumbs, header, iframe, route-level loading skeleton
- Permissions: Share dialog with search, invite, permission editing, visibility toggle
- Toasts: Sonner for create, delete, rename, reorder, copy link actions

---

## Completed Phases (Detail)

### Phase 12 — Module Split & Code Quality

- Split monolithic `pages/service.py` into focused modules: `sync.py`, `background.py`, `tree.py`, `helpers.py`
- Split `drive/service.py` into `credentials.py`, `files.py`, `folders.py`, `content.py`, `changes.py`
- Created `docs_transformer/` package (13 modules) for Google Docs API → structured content block conversion
- Created `search/` package with PostgreSQL tsvector full-text search
- Created `webhooks/` package for Drive push notification handling
- Created `workspaces/` package for Google Groups sync and workspace management
- Created `internal/` package for Cloud Scheduler endpoints

### Phase 13 — Security Hardening

- JWT token versioning: `token_version` on User model, embedded in JWT, incremented on logout
- Refresh token rotation on use
- Cookie security: `max_age` matching JWT expiry, `SameSite=lax` (dev) / `SameSite=none` (prod)
- Drive webhook verification via `DRIVE_WEBHOOK_TOKEN` and `X-Goog-Channel-Token` header
- Internal endpoint OIDC audience verification via `INTERNAL_AUDIENCE`
- Replaced python-jose with PyJWT (actively maintained)

### Phase 14 — Sync Rewrite

- Two-pass sync: Pass 1 collects all folders/docs from Drive concurrently (no DB access), Pass 2 applies to DB
- Smart section creation: only folders with docs in their subtree get section pages
- Incremental sync via Drive Changes API with saved `startPageToken`
- Force sync option (`?force=true`) for full rescan
- Section pages use synthetic `section-{folder_id}` Drive IDs
- Synced pages have `created_by=NULL` (workspace members can modify)

---

## Architecture Reference

### Request Flow

```
Browser (Next.js)
    │  JWT in HTTP-only cookie
    ▼
FastAPI
    ├── Validates JWT (get_current_user + token_version check)
    ├── Enforces access control (DB queries)
    ├── Calls Google Drive API (user's encrypted tokens)
    ├── Calls Google Admin SDK (service account)
    └── Returns JSON

Google Drive  ─── content storage (Docs, folders)
Google Admin  ─── source of truth for group membership
```

### Access Control

```
Can user U see page P?
  1. P.created_by == U.id                                    → YES (owner)
  2. P.is_private == FALSE AND U ∈ P.workspace members       → YES
  3. page_permissions has (P, U) or (P, U's workspace)       → YES
  4. Otherwise                                                → DENY (403)
```

### Drive Folder Structure

```
Sarj Docs/                        ← root folder (DRIVE_ROOT_FOLDER_ID)
├── Engineering/                  ← workspace folders
│   └── Culture/
│       └── How We Work (Doc)
├── Marketing/
│   └── Brand Guidelines (Doc)
└── Private/
    └── ali@sarj.ai/              ← per-user private folder
        └── My Draft (Doc)
```
