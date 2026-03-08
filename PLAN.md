# Sarj Docs — Build Plan

> Last updated: 2026-03-05

## Progress Overview

| Phase | Name | Status |
|-------|------|--------|
| 1 | GCP Setup | **Pending** (manual, user-side) |
| 2 | Backend Foundation | ✅ Done |
| 3 | Authentication | ✅ Done |
| 4 | Workspace Sync | **Blocked** on Phase 1 |
| 5 | Sidebar Tree API | ✅ Done |
| 6 | Drive Import & Webhooks | **Blocked** on Phase 1 |
| 7 | Frontend Foundation | ✅ Done |
| 8 | Sidebar UI | ✅ Done |
| 9 | Document View | ✅ Done |
| 10 | Permissions UI | ✅ Done |
| 11a | Frontend Auth Flow | ✅ Done |
| 11b | Data Layer (React Query + Hooks) | ✅ Done (mock fallback) |
| 11c | Backend: GET page + Permissions CRUD | ✅ Done |
| 11d | Schema Fixes (trigger + constraint) | ✅ Done |
| 11e | Drag-and-Drop | ✅ Done (sibling reorder) |
| 11f | Loading States & Toasts | ✅ Done |
| 11g | Search | **Stretch** — not started |
| 12 | Wire Frontend to Real API | **Ready** after Phase 1 |

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
│   ├── main.py                             # FastAPI app, CORS, auth + pages + permissions routers
│   ├── config.py                           # pydantic-settings
│   ├── database.py                         # Async SQLAlchemy engine/session/Base
│   ├── models/
│   │   ├── __init__.py                     # Re-exports all models
│   │   ├── user.py                         # User
│   │   ├── workspace.py                    # Workspace, WorkspaceMembership
│   │   ├── page.py                         # Page, PagePermission
│   │   └── drive_webhook.py               # DriveWebhookChannel
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── router.py                       # /auth/login, callback, logout, me, refresh
│   │   ├── jwt.py                          # JWT create/decode (access + refresh, type-safe)
│   │   ├── encryption.py                   # Fernet encrypt/decrypt Google tokens
│   │   └── dependencies.py                # get_current_user (cookie-first, header fallback)
│   ├── pages/
│   │   ├── __init__.py
│   │   ├── router.py                       # /pages/tree, GET/{id}, CRUD, publish
│   │   ├── schemas.py                      # PageCreate, PageUpdate (sentinel pattern), PageTreeNode, etc.
│   │   └── service.py                      # Tree query, CRUD, access control (_visible_pages_filter)
│   └── permissions/
│       ├── __init__.py
│       ├── router.py                       # GET /permissions/page/{id}, POST, DELETE
│       ├── schemas.py                      # PermissionCreate (with validator), PermissionResponse
│       └── service.py                      # list, grant, revoke (owner-only enforcement)
```

**Key details:**
- Auth: Google OAuth → JWT in HTTP-only cookies, Fernet-encrypted Google tokens in DB
- Pages API: Tree endpoint uses single query + Python tree build, shared `_visible_pages_filter` for access control
- Permissions: Owner-only management, at_least_one_grantee validation
- Page CRUD uses **placeholder Drive IDs** — real Drive integration pending Phase 6
- Migration 002 adds `updated_at` trigger on pages + CHECK constraint on page_permissions

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
- Auth: Login page, AuthProvider context, auto-redirect (commented out for dev)
- Data layer: React Query hooks for page tree, workspaces, current user — all fall back to mock data when backend is unavailable
- API client: Full typed wrapper with 401 → token refresh → retry logic
- Sidebar: Drag-and-drop reorder (siblings only, per-level DnD contexts), toast feedback
- Document view: Breadcrumbs, header, iframe, route-level loading skeleton
- Permissions: Share dialog with search, invite, permission editing, visibility toggle
- Toasts: Sonner for create, delete, rename, reorder, copy link actions

---

## What's Remaining

### Phase 1 — GCP Setup (manual, user-side)

- [ ] Create GCP project (or use existing)
- [ ] Enable APIs: Google Drive API, Google Admin SDK
- [ ] Create OAuth 2.0 credentials (Web Application)
  - Redirect URI: `http://localhost:8000/auth/callback`
  - Copy Client ID + Client Secret
- [ ] Create Service Account
  - Download JSON key file
  - Enable domain-wide delegation
- [ ] In Google Admin → Security → API Controls → Domain-wide Delegation:
  - Add service account Client ID with scopes:
    - `https://www.googleapis.com/auth/admin.directory.group.readonly`
    - `https://www.googleapis.com/auth/admin.directory.group.member.readonly`
- [ ] Create `Sarj Docs` root folder in Google Drive, note the folder ID
- [ ] Decide admin email for service account impersonation
- [ ] Fill in `backend/.env` with all credentials

### Phase 4 — Workspace Sync (blocked on Phase 1)

- [ ] Add `GOOGLE_ADMIN_IMPERSONATE_EMAIL` to config.py
- [ ] Create `app/workspaces/` package:
  - `service.py` — Admin SDK client (service account + domain-wide delegation), fetch groups → upsert workspaces, fetch user memberships → upsert workspace_memberships
  - `router.py` — `GET /workspaces` (list user's workspaces), `POST /workspaces/sync` (trigger manual sync)
  - `schemas.py` — WorkspaceResponse
- [ ] Hook workspace membership sync into the auth callback (on login, refresh user's group memberships)
- [ ] Register router in `main.py`

### Phase 6 — Drive Import & Webhooks (blocked on Phase 1)

- [ ] Create `app/drive/` package:
  - `service.py` — Drive API client helpers (create doc, create folder chain, move file, set permissions)
  - `import_service.py` — Scan Drive root folder recursively, match folders to workspaces, upsert pages
  - `webhook_service.py` — Register/renew webhook channels, process change notifications
  - `router.py` — `POST /admin/import`, `POST /webhooks/drive`
- [ ] Add `WEBHOOK_BASE_URL` to config.py
- [ ] Wire Drive API calls into page CRUD (replace placeholder IDs):
  - `create_page` → create Google Doc in correct folder
  - `update_page` (re-parent) → move file in Drive
  - `delete_page` → trash file in Drive
  - `publish_page` → move file + grant Google Group permissions
- [ ] APScheduler background task for webhook channel renewal (every 6 days)
- [ ] Register router in `main.py`

### Phase 12 — Wire Frontend to Real API (after Phase 1 + backend running)

- [ ] Enable auth redirect in `auth-provider.tsx` (uncomment router.push to /login)
- [ ] Wire sidebar components to use `usePageTree`, `useWorkspaces`, `useCurrentUser` hooks instead of direct mock data imports
- [ ] Wire document view to fetch page by ID from API (`api.getPage`)
- [ ] Wire share dialog to real permissions API (`api.getPagePermissions`, `grantPermission`, `revokePermission`)
- [ ] Wire page create/rename/delete actions to API mutations with React Query `useMutation` + cache invalidation
- [ ] Wire drag-and-drop reorder to call `api.updatePage` with new sort_order
- [ ] Add error boundaries for API failures
- [ ] Remove mock data fallbacks once backend is stable

### Phase 11g — Search (stretch goal)

- [ ] `GET /pages/search?q=...` backend endpoint (title search with `ILIKE`)
- [ ] Command palette / search bar in frontend (Cmd+K)

---

## Architecture Reference

### Request Flow

```
Browser (Next.js)
    │  JWT in HTTP-only cookie
    ▼
FastAPI
    ├── Validates JWT (get_current_user)
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
