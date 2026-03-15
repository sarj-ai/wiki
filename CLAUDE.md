# Sarj Docs — Project Reference for Claude

## Project Overview

An internal knowledge base for `@sarj.ai`, built on top of Google Docs. The goal is a Notion-like UX with infinite sidebar nesting, workspace grouping via Google Admin, and embedded Google Docs editing — without leaving the Google Workspace ecosystem.

---

## Tech Stack

| Layer        | Technology                                           |
|--------------|------------------------------------------------------|
| Frontend     | Next.js (App Router), React                          |
| UI           | Shadcn UI, Tailwind CSS                              |
| Backend      | FastAPI (Python)                                     |
| Database     | PostgreSQL + SQLAlchemy (async) + Alembic migrations |
| Auth         | Google OAuth 2.0 (restricted to @sarj.ai)            |
| Content      | Google Drive / Google Docs (iframe embedded)         |
| Admin Sync   | Google Admin SDK Directory API (service account)     |
| Drive Events | Google Drive Push Notifications (webhooks)           |

---

## Tooling Rules

- **Always use `uv`** for Python dependency management, virtual environments, and running scripts. Never use `pip`, `pip3`, `venv`, or `python -m venv`.

---

## Architecture Rules (STRICTLY ENFORCED)

1. **Separation of concerns**: The Next.js client NEVER calls Google APIs directly, performs database operations, or executes complex business logic. It only calls FastAPI endpoints.
2. **FastAPI owns everything**: All Google API calls, token management, database reads/writes, access control enforcement, and business logic live in FastAPI.
3. **No assumptions**: If a technical choice or API behavior is uncertain, ask — do not invent requirements.
4. **Iterative delivery**: Do not write the entire application in one response. Build incrementally per the phases below.

---

## Authentication & Security

- **Login**: Google Social Login ONLY. No username/password.
- **Domain restriction**: Only `@sarj.ai` emails may authenticate. Enforced in FastAPI — never trust the client.
- **Token storage**: Google OAuth access + refresh tokens stored server-side, encrypted at rest (Fernet/AES). Never sent to the client.
- **Session**: FastAPI issues its own signed JWT after validating the Google OAuth callback. The client uses this JWT for all API calls.
- **Admin SDK**: A GCP service account with domain-wide delegation is used to call the Admin Directory API as a service (not as the user). The service account JSON key is stored as a backend environment variable only.

---

## Drive & Hierarchy Architecture

### The Key Principle: Drive Mirrors Our Tree

Our app tree and the Google Drive folder structure are **kept in sync**. When a user creates `Engineering → Culture → How We Work`, the backend:
1. Ensures `Sarj Docs/Engineering/Culture/` folder chain exists in the shared Workspace Drive.
2. Creates the Google Doc inside `Culture/`.
3. Records the page in our DB with the `google_drive_id` (file) and `drive_folder_id` (parent folder).

**Draft documents** go into a per-user folder: `Sarj Docs/My Drafts/{user_email}/`.

**Moving a page** in our tree → FastAPI moves the Drive file to the new Drive folder (via `files.update` with `addParents`/`removeParents`).

**Publishing a draft** to a workspace → FastAPI moves the Drive file from the "My Drafts" folder to the workspace folder and grants the Google Group read/edit access via Drive sharing.

### Drive Folder Root Structure
```
Sarj Docs/                        ← app root folder (shared drive or team folder)
├── Engineering/                  ← workspace/section folders
│   └── Culture/
│       └── How We Work (Doc)
├── Marketing/
│   └── Brand Guidelines (Doc)
└── My Drafts/
    └── ali@sarj.ai/              ← per-user draft folder
        └── My Draft (Doc)
```

### Existing Docs Import
- On first setup, FastAPI scans the existing Drive folder structure under `Sarj Docs/`.
- Each discovered file is matched to a workspace based on its folder path, then inserted into the `pages` table.
- Group tags are applied based on which workspace folder the file lives in.

### Listening for New Changes (Drive Webhooks)
- FastAPI registers a **Google Drive Push Notification** (`files.watch`) on the root `Sarj Docs/` folder.
- When Drive detects a new file or folder change, it POSTs to our `/webhooks/drive` endpoint.
- FastAPI fetches the changed file's metadata, determines its folder path, infers the workspace, and upserts the `pages` record.
- Webhook channel registrations expire every 7 days — a background task re-registers them automatically.

---

## Database Schema

```sql
-- Self-referential tree for Notion-like sidebar hierarchy
pages:
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid()
  parent_id        UUID REFERENCES pages(id) ON DELETE CASCADE NULL  -- NULL = root
  google_drive_id  TEXT NOT NULL UNIQUE   -- Google Doc file ID
  drive_folder_id  TEXT NOT NULL          -- Google Drive folder ID that contains this doc
  title            TEXT NOT NULL          -- cached; refreshed from Drive lazily
  type             TEXT NOT NULL CHECK(type IN ('page', 'section'))
  sort_order       FLOAT NOT NULL DEFAULT 0  -- fractional indexing for drag-drop reorder
  is_private       BOOLEAN NOT NULL DEFAULT TRUE
  created_by       UUID REFERENCES users(id)
  workspace_id     UUID REFERENCES workspaces(id) NULL
  icon             TEXT NULL              -- emoji or image URL
  cover_url        TEXT NULL
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()

workspaces:
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid()
  name                TEXT NOT NULL
  google_group_email  TEXT NOT NULL UNIQUE  -- from Google Admin SDK (e.g. engineering@sarj.ai)
  drive_folder_id     TEXT NOT NULL          -- root Drive folder ID for this workspace
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()

users:
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid()
  email                    TEXT NOT NULL UNIQUE
  google_id                TEXT NOT NULL UNIQUE
  display_name             TEXT
  avatar_url               TEXT
  encrypted_access_token   TEXT   -- Fernet-encrypted
  encrypted_refresh_token  TEXT   -- Fernet-encrypted
  token_expiry             TIMESTAMPTZ
  created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()

workspace_memberships:
  user_id       UUID REFERENCES users(id) ON DELETE CASCADE
  workspace_id  UUID REFERENCES workspaces(id) ON DELETE CASCADE
  PRIMARY KEY (user_id, workspace_id)

page_permissions:
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid()
  page_id       UUID REFERENCES pages(id) ON DELETE CASCADE
  user_id       UUID REFERENCES users(id) ON DELETE CASCADE NULL
  workspace_id  UUID REFERENCES workspaces(id) ON DELETE CASCADE NULL
  permission    TEXT NOT NULL CHECK(permission IN ('view', 'edit'))
  -- at least one of user_id or workspace_id must be non-null (enforced in app layer)

drive_webhook_channels:
  channel_id      TEXT PRIMARY KEY       -- Google-assigned channel ID
  resource_id     TEXT NOT NULL          -- Drive resource being watched
  folder_id       TEXT NOT NULL          -- Drive folder ID being watched
  expiry          TIMESTAMPTZ NOT NULL   -- re-register before this time
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
```

**Tree traversal**: Adjacency list (`parent_id`). PostgreSQL recursive CTEs (`WITH RECURSIVE`) handle full-tree fetches efficiently.

---

## Access Control Logic

```
Can user U see page P?
  1. P.created_by == U                                          → YES (owner always sees own)
  2. P.is_private == FALSE AND U is in P.workspace_id          → YES
  3. page_permissions has a record for (P, U) or (P, U's workspace) → YES
  4. Otherwise                                                  → NO (403, never leak existence)
```

---

## Google Docs Iframe Embedding

```
https://docs.google.com/document/d/{DRIVE_ID}/edit?embedded=true
```

- FastAPI validates user authorization, then returns the `google_drive_id`.
- The Next.js client constructs the iframe URL client-side only after receiving the Drive ID.
- Browser's existing Google session (user already logged in via OAuth) handles iframe auth automatically.
- This is viable because all users are `@sarj.ai` Workspace members.

---

## Google API Scopes

### User OAuth scopes (requested during login)
- `openid`, `email`, `profile`
- `https://www.googleapis.com/auth/drive` — read/write Drive files

### Service account (domain-wide delegation) scopes
- `https://www.googleapis.com/auth/admin.directory.group.readonly` — read Google Admin groups
- `https://www.googleapis.com/auth/admin.directory.group.member.readonly` — read group members

---

## Build Phases (in order)

### Phase 1 — GCP Setup (manual, user-side)
- Create OAuth 2.0 credentials (Web Application). Callback: `http://localhost:8000/auth/callback` (dev) and production URL.
- Create a Service Account. Enable domain-wide delegation. Download JSON key.
- Enable APIs: Google Drive API, Google Admin SDK Directory API.
- Create the `Sarj Docs` root folder in Google Drive. Note the folder ID.

### Phase 2 — Backend Foundation
- FastAPI project scaffold with `uv` or `pip` + `pyproject.toml`
- PostgreSQL connection via `asyncpg` + SQLAlchemy async
- Alembic migration setup
- All schema tables created via migrations

### Phase 3 — Authentication
- Google OAuth 2.0 flow (`/auth/login`, `/auth/callback`, `/auth/logout`)
- Domain enforcement (`@sarj.ai` only, in backend)
- JWT issuance + refresh
- Token encryption/storage

### Phase 4 — Workspace Sync
- Service account client setup
- Admin SDK: fetch all `@sarj.ai` groups → upsert `workspaces` table
- On user login: fetch user's group memberships → upsert `workspace_memberships`

### Phase 5 — Sidebar Tree API
- `GET /pages/tree` — returns user's visible page tree (recursive CTE, respects access control)
- `POST /pages` — create page (creates Drive doc + Drive folder chain + DB record)
- `PATCH /pages/{id}` — rename, reorder, re-parent (with Drive file move)
- `DELETE /pages/{id}` — soft-delete (trash in Drive, mark deleted in DB)
- `POST /pages/{id}/publish` — move from private to workspace (Drive share + DB update)

### Phase 6 — Drive Import & Webhooks
- `POST /admin/import` — scan existing Drive structure under root folder, ingest into DB
- `POST /webhooks/drive` — receive Drive push notifications, upsert pages
- Background task: re-register expiring webhook channels

### Phase 7 — Frontend Foundation
- Next.js App Router scaffold
- Shadcn UI + Tailwind with Sarj theme wired in
- `next-themes` for dark/light mode
- Auth flow: redirect to `/auth/login`, handle JWT cookie from backend

### Phase 8 — Sidebar UI
- Collapsible, infinitely nested sidebar tree component
- Drag-and-drop reordering (via `dnd-kit`)
- Workspace switcher
- New page creation inline

### Phase 9 — Document View
- Page route: `/docs/[pageId]`
- Fetch page metadata from FastAPI, render Google Docs iframe
- Page header: title, icon, workspace badge, share button

### Phase 10 — Permissions UI
- Share modal: share with workspace or specific user
- Toggle private/public
- View current collaborators

---

## Sarj Theme

### Light Mode (`:root`)
```css
:root {
  --background: #f6efe7;
  --foreground: #251040;
  --card: #ffffff;
  --card-foreground: #251040;
  --popover: #ffffff;
  --popover-foreground: #251040;
  --primary: #000000;
  --primary-foreground: #ffffff;
  --secondary: #251040;
  --secondary-foreground: #ffffff;
  --muted: #f5f5f5;
  --muted-foreground: #737373;
  --accent: #f5f5f5;
  --accent-foreground: #171717;
  --destructive: #e06000;
  --border: #e5e5e5;
  --input: #e5e5e5;
  --ring: #a1a1a1;
  --chart-1: #e06000;
  --chart-2: #009689;
  --chart-3: #104e64;
  --chart-4: #ffb900;
  --chart-5: #fe9a00;
  --sidebar: #fafafa;
  --sidebar-foreground: #251040;
  --sidebar-primary: #251040;
  --sidebar-primary-foreground: #ffffff;
  --sidebar-accent: #D0C3DF;
  --sidebar-accent-foreground: #251040;
  --sidebar-border: #e5e5e5;
  --sidebar-ring: oklch(0.708 0 0);

  --font-sans: 'Geist', 'Geist Fallback', ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', sans-serif;
  --font-serif: 'Geist', 'Geist Fallback', ui-serif, Georgia, Cambria, 'Times New Roman', Times, serif;
  --font-mono: 'Geist Mono', 'Geist Mono Fallback', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;

  --radius: 0.625rem;

  --shadow-2xs: 0 1px 3px 0px rgb(0 0 0 / 0.05);
  --shadow-xs:  0 1px 3px 0px rgb(0 0 0 / 0.05);
  --shadow-sm:  0 1px 3px 0px rgb(0 0 0 / 0.10), 0 1px 2px -1px rgb(0 0 0 / 0.10);
  --shadow:     0 1px 3px 0px rgb(0 0 0 / 0.10), 0 1px 2px -1px rgb(0 0 0 / 0.10);
  --shadow-md:  0 1px 3px 0px rgb(0 0 0 / 0.10), 0 2px 4px -1px rgb(0 0 0 / 0.10);
  --shadow-lg:  0 1px 3px 0px rgb(0 0 0 / 0.10), 0 4px 6px -1px rgb(0 0 0 / 0.10);
  --shadow-xl:  0 1px 3px 0px rgb(0 0 0 / 0.10), 0 8px 10px -1px rgb(0 0 0 / 0.10);
  --shadow-2xl: 0 1px 3px 0px rgb(0 0 0 / 0.25);
}
```

### Dark Mode (`.dark`)
```css
.dark {
  --background: #000000;
  --foreground: #f6efe7;
  --card: #171717;
  --card-foreground: #f6efe7;
  --popover: #171717;
  --popover-foreground: #f6efe7;
  --primary: #6762ab;
  --primary-foreground: #000000;
  --secondary: #262626;
  --secondary-foreground: #f6efe7;
  --muted: #262626;
  --muted-foreground: #a1a1a1;
  --accent: #262626;
  --accent-foreground: #fafafa;
  --destructive: #e06000;
  --border: #ffffff1a;
  --input: #ffffff26;
  --ring: #737373;
  --chart-1: #e06000;
  --chart-2: #00bc7d;
  --chart-3: #fe9a00;
  --chart-4: #ad46ff;
  --chart-5: #ff2056;
  --sidebar: #171717;
  --sidebar-foreground: #fafafa;
  --sidebar-primary: #1447e6;
  --sidebar-primary-foreground: #fafafa;
  --sidebar-accent: #262626;
  --sidebar-accent-foreground: #fafafa;
  --sidebar-border: #ffffff1a;
  --sidebar-ring: #737373;
}
```

### Tailwind `@theme inline` block
```css
@theme inline {
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --color-card: var(--card);
  --color-card-foreground: var(--card-foreground);
  --color-popover: var(--popover);
  --color-popover-foreground: var(--popover-foreground);
  --color-primary: var(--primary);
  --color-primary-foreground: var(--primary-foreground);
  --color-secondary: var(--secondary);
  --color-secondary-foreground: var(--secondary-foreground);
  --color-muted: var(--muted);
  --color-muted-foreground: var(--muted-foreground);
  --color-accent: var(--accent);
  --color-accent-foreground: var(--accent-foreground);
  --color-destructive: var(--destructive);
  --color-border: var(--border);
  --color-input: var(--input);
  --color-ring: var(--ring);
  --color-chart-1: var(--chart-1);
  --color-chart-2: var(--chart-2);
  --color-chart-3: var(--chart-3);
  --color-chart-4: var(--chart-4);
  --color-chart-5: var(--chart-5);
  --color-sidebar: var(--sidebar);
  --color-sidebar-foreground: var(--sidebar-foreground);
  --color-sidebar-primary: var(--sidebar-primary);
  --color-sidebar-primary-foreground: var(--sidebar-primary-foreground);
  --color-sidebar-accent: var(--sidebar-accent);
  --color-sidebar-accent-foreground: var(--sidebar-accent-foreground);
  --color-sidebar-border: var(--sidebar-border);
  --color-sidebar-ring: var(--sidebar-ring);

  --font-sans: var(--font-sans);
  --font-mono: var(--font-mono);
  --font-serif: var(--font-serif);

  --radius-sm: calc(var(--radius) - 4px);
  --radius-md: calc(var(--radius) - 2px);
  --radius-lg: var(--radius);
  --radius-xl: calc(var(--radius) + 4px);

  --shadow-2xs: var(--shadow-2xs);
  --shadow-xs:  var(--shadow-xs);
  --shadow-sm:  var(--shadow-sm);
  --shadow:     var(--shadow);
  --shadow-md:  var(--shadow-md);
  --shadow-lg:  var(--shadow-lg);
  --shadow-xl:  var(--shadow-xl);
  --shadow-2xl: var(--shadow-2xl);
}
```

---

## Dark/Light Mode

- Default: detect from `prefers-color-scheme` via `next-themes`.
- Manual toggle: overrides system preference, stored in `localStorage`.
- Implementation: `next-themes` with `attribute="class"` (adds `.dark` class to `<html>`).

---

## Decisions Log

| Decision | Choice | Rationale |
|---|---|---|
| Database | PostgreSQL | Performance, recursive CTEs, production-grade |
| Tree structure | Adjacency list + recursive CTE | Simple, sufficient for team scale |
| Admin SDK access | Service account + domain-wide delegation | No Super Admin required day-to-day |
| Drive structure | Mirrors app tree | Moving pages in app = moving files in Drive |
| Private docs location | `Sarj Docs/My Drafts/{email}/` folder | Clear separation, easy to audit |
| Doc import | Scan Drive root folder on demand | Ingest existing docs with group tags |
| New doc detection | Drive Push Notifications (webhooks) | Real-time, event-driven, no polling |
| Webhook renewal | Background APScheduler task | Channels expire every 7 days |
| Iframe auth | Browser session passthrough | Works because all users are @sarj.ai Workspace members |
