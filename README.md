# Sarj Docs

Internal knowledge base for [@sarj.ai](https://sarj.ai), built on top of Google Docs. Notion-like UX with nested sidebar navigation, workspace grouping via Google Admin, and embedded Google Docs editing — all within the Google Workspace ecosystem.

## Tech Stack

- **Frontend:** Next.js (App Router), Shadcn UI, Tailwind CSS
- **Backend:** FastAPI (Python), SQLAlchemy async, Alembic
- **Database:** PostgreSQL
- **Auth:** Google OAuth 2.0 (restricted to @sarj.ai)
- **Content:** Google Drive / Google Docs
- **Infrastructure:** Terraform, Google Cloud Run, Cloud SQL, Secret Manager

## Setup

### Git hooks

Use Node 24+, npm 11, Python 3.14, and uv. Root `make setup`, backend `make setup`,
and `npm install` in `frontend/` install Lefthook automatically. Hooks check staged
files with Ruff, ESLint, TypeScript, Prettier, Markdownlint, Taplo, and every rule
discovered from the pinned Sarj registries, then run the full quality suite before pushes.
To reinstall the hooks manually:

```bash
make setup-hooks
```

Upgrade every Sarj Standards package, refresh both locks, and regenerate all six
canonical configs with one command:

```bash
make standards-upgrade
```

Commit the package manifests, locks, and generated configs together. Hooks and CI
run `make standards-check` and reject config or lock drift.

### Secrets

Local-dev secrets live in Google Secret Manager (project `sarj-docs`).
Pull them once after cloning (cached for 24h; `make pull-local-secrets-force`
bypasses; `make push-local-secrets` publishes local edits back):

```bash
make pull-local-secrets   # writes backend/.env
```

### Backend

```bash
cd backend
make setup
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
cp .env.example .env.local  # fill in your values
npm run dev
```

## Architecture

- Next.js client calls FastAPI endpoints only — never Google APIs directly
- FastAPI handles all Google API calls, token management, DB operations, and access control
- Google Drive folder structure mirrors the app's page tree
- Google Admin SDK syncs workspace groups and memberships

## Project Structure

### Backend (`backend/app/`)

| Package             | Purpose                                                                |
| ------------------- | ---------------------------------------------------------------------- |
| `auth/`             | OAuth flow, JWT (with token versioning), encryption, auth dependencies |
| `docs_transformer/` | Google Docs API response → structured content blocks (13 modules)      |
| `drive/`            | Drive API helpers: credentials, files, folders, content, changes       |
| `pages/`            | Page CRUD, content fetching, background tasks, sync, tree building     |
| `permissions/`      | Page sharing and access control                                        |
| `search/`           | Full-text search with PostgreSQL tsvector                              |
| `webhooks/`         | Drive push notification handling and verification                      |
| `workspaces/`       | Google Groups sync, workspace management                               |
| `internal/`         | Cloud Scheduler endpoints (reindex, webhook renewal)                   |

### Frontend (`frontend/src/`)

| Directory                 | Purpose                                                    |
| ------------------------- | ---------------------------------------------------------- |
| `app/`                    | Next.js App Router pages (login, app shell, document view) |
| `components/sidebar/`     | Collapsible tree, workspace switcher, drag-and-drop        |
| `components/document/`    | Document header, iframe embed, loading skeletons           |
| `components/permissions/` | Share dialog and share button                              |
| `hooks/`                  | React Query hooks for page tree, workspaces, current user  |
| `lib/`                    | API client, auth helpers, utilities                        |

## Deployment

Infrastructure is managed via **Terraform** in the `terraform/` directory:

- **Cloud Run** — Hosts both backend (FastAPI) and frontend (Next.js via OpenNext + Cloudflare Workers)
- **Cloud SQL** — PostgreSQL database
- **Secret Manager** — Stores all sensitive configuration (OAuth credentials, service account keys, encryption keys)
- **CI/CD** — GitHub Actions deploys on push to `main`
- **Migrations** — Run automatically as part of the Docker `CMD` on backend startup

## Environment Variables

### Backend (required)

| Variable                         | Description                                   |
| -------------------------------- | --------------------------------------------- |
| `DATABASE_URL`                   | PostgreSQL connection string                  |
| `GOOGLE_CLIENT_ID`               | OAuth 2.0 client ID                           |
| `GOOGLE_CLIENT_SECRET`           | OAuth 2.0 client secret                       |
| `GOOGLE_REDIRECT_URI`            | OAuth callback URL                            |
| `JWT_SECRET_KEY`                 | Secret key for signing JWTs                   |
| `FERNET_KEY`                     | Encryption key for Google tokens at rest      |
| `DRIVE_ROOT_FOLDER_ID`           | Root `Sarj Docs` folder ID in Google Drive    |
| `SERVICE_ACCOUNT_JSON`           | Service account key JSON (base64 or raw)      |
| `GOOGLE_ADMIN_IMPERSONATE_EMAIL` | Admin email for service account impersonation |
| `DRIVE_WEBHOOK_TOKEN`            | Token for verifying Drive webhook headers     |
| `FRONTEND_URL`                   | Frontend origin for CORS and redirects        |

### Backend (optional)

| Variable                    | Description                                                              |
| --------------------------- | ------------------------------------------------------------------------ |
| `SHARED_DRIVE_ID`           | Shared Drive ID for Changes API (leave empty for folder-level SA access) |
| `INTERNAL_AUDIENCE`         | Cloud Run service URL for OIDC audience verification (production)        |
| `INTERNAL_SCHEDULER_SECRET` | Shared secret for internal endpoints (local dev, min 32 chars)           |
| `WEBHOOK_BASE_URL`          | Base URL for Drive webhook registration                                  |

### Frontend

| Variable              | Description          |
| --------------------- | -------------------- |
| `NEXT_PUBLIC_API_URL` | Backend API base URL |
