# PIVOT PLAN — Sarj Docs → The Navigator

**Status:** Draft for review (revision 2). No code changes to production until the full pivot PR is merged.
**Date:** 2026-04-15
**Revision history:**
- rev 1 (2026-04-14) — initial draft
- rev 2 (2026-04-15) — user review #1: workspace removal, per-user sort order and folder icons, fixed-type icons, hide-folder semantics, Edit-in-Google toolbar button, Drive Search API from day one, sub-PR workflow, SA key rotation moves to cutover, overnight cutover, single prod deploy
- rev 3 (2026-04-15) — user review #2: tsvector deleted immediately (no fallback), Drive Search scoped to shared drive via `corpora=drive`, drag-drop iframe concern removed, code deletion happens in same sub-PRs as replacements (not deferred to cutover)
- **rev 4 (2026-04-15) — Drive Search + tree walker spike findings** — major architecture change: workspaces map to real shared drives (not top-level folders inside one drive), the hidden `0AI_dKpHiz__7Uk9PVA` drive must be content-migrated out to the 4 real shared drives, per-user `sidebar_cache` replaced with shared per-drive `drive_tree_cache`, cold load is 15s for first user of the day and ~1.4s warm, folders-only with lazy file load added as a fallback optimization, content migration added as its own sub-PR. See §17 Spike Findings for raw data.
- **rev 5 (2026-04-15) — content migration is manual and out-of-band.** A human will move files out of the hidden `0AI_dKpHiz__7Uk9PVA` drive by hand — no scripts, no SA-driven migration, no automated rollback. Sub-PR B-prime shrinks to a verification checklist; the code pivot becomes fully disjoint from the content move.
- **rev 6 (2026-04-15) — review #3:** progressive sidebar rendering made the default (no 14s spinner anywhere); content migration is a prerequisite to cutover (Sub-PR F), not to Sub-PR C; spike scripts stay local, nothing committed to the repo.
- **rev 7 (2026-04-15) — concurrency:** added §7.3 (in-memory singleflight cache coalescing as a day-one requirement) and §7.4 (per-worker limitation + Postgres advisory-lock upgrade path for future scale).
- **rev 8 (2026-04-16) — iframe capability discoveries + implementation amendments.** Four plan items verified experimentally against the running backend / a test user's real Drive, some of which contradict the original plan:
  1. **Google Forms editor cannot be iframed cross-origin.** A form created via `forms.new` under the signed-in user's own account still shows a "You need access" gate inside our iframe. We verified this with a form owned by `aalhashemi@sarj.ai` and visible in that account's Drive. Creation path (Drive API vs Forms API vs Google's own UI) is irrelevant — the restriction is on the Forms editor's behavior under a cross-origin top frame. **New decision 11.8: Forms open in a new tab permanently.** `FileRow` routes form clicks through `window.open(_blank)`, the sidebar shows an `ExternalLink` indicator next to form rows so the different behavior is visible before the click, and the create-file flow uses `window.open` after a form is created. Forms API migration was considered and explicitly rejected — it would require an OAuth scope addition (user re-consent) and buy us zero functional improvement.
  2. **Google's native Share button inside a cross-origin Docs iframe is unreachable.** The iframe loads cleanly and shows the full editor toolbar including the Share button, but clicking Share silently fails. Root cause (confirmed via the browser console): Google Docs' Share dialog loads a nested people-picker iframe from `contacts.google.com` / `accounts.google.com` / `apis.google.com`, and those nested documents ship with `Content-Security-Policy: frame-ancestors https://docs.google.com`. The browser's per-resource `frame-ancestors` check walks the entire ancestor chain (`localhost:3000 → docs.google.com → contacts.google.com`) and fails because `localhost:3000` is above `docs.google.com`. No amount of CSP config on our side overrides Google's own `frame-ancestors`. **New decision 11.7: in-app sharing is not possible and no custom share dialog will be built.** Users share via "Open in Google Docs" in the Navigator toolbar, which opens the file in a top-level tab where the native dialog works. The original §4.7 stance of "no custom share dialog, users click the native button inside the iframe" is amended to "no custom share dialog, users click Open-in-Google-Docs and share from the new tab".
  3. **Strict-mode shared-drive scoping is enforced in the backend.** The original plan implicitly scoped the Navigator to shared drives via `drives.list()` at the sidebar layer, but nothing stopped a user from deep-linking `/d/{any-file-id}` into the Navigator chrome — direct-URL access would load any file the user's OAuth token could see, including personal My Drive files. `app/files/service.py` now runs every file operation through `_ensure_in_member_drive`, which 404s any file whose `driveId` is absent (My Drive) or not in the user's `drives.list()`. Applied to `get`, `create`, `rename`, `move`, and `trash`. 404 rather than 403 so out-of-scope probes can't distinguish "file doesn't exist" from "exists but not for you". Covered by two new unit tests in `test_files_service.py`.
  4. **Command palette (Cmd+K, Spotlight-style) replaces §4.9's simple search input.** The original plan described a top-bar search box calling `/drive-search` and showing a flat result list. What shipped is richer: a large centered Dialog (max-w-3xl × 560px), two-column layout with a result list on the left and a lazy-loaded preview panel on the right, global `Cmd+K` / `Ctrl+K` binding, arrow-key navigation with `scrollIntoView`, and per-result snippet preview. Snippets come from a new `GET /drive-search/snippet` endpoint that exports the file as `text/plain` (Docs/Sheets/Slides only, 2 MB cap) and extracts ~120 chars of context around the first query match. Uses **prefix-truncation fallback** (down to 4 chars) to match Drive's stemming — a query of "phases" hits docs containing only "phase" and vice versa. Frontend highlights the matched term with `<mark>`. Click-to-select, Enter or the Open button to navigate — browsing doesn't leave the current view. The old inline popover `DriveSearchBox` is now a button trigger that opens the palette.
  5. **Icon editing fully wired per §3 icon rules.** Implementation uses `emoji-mart` (re-added as a dep; lazy-loaded via `next/dynamic` so the ~2 MB of emoji data only lands on first picker open). New `EmojiPickerPopover` component wraps radix Popover with the Picker. Clicking a Doc icon opens the picker → writes to `doc_icons` (global, all users see the change). Clicking a folder icon → writes to `user_folder_icons` (per-user). Other mime types render a non-clickable icon. New `GET /extras/doc-icons` endpoint lists every global row in one round-trip so the sidebar can populate the override map at load.
  6. **Sidebar polish delivered on top of the plan.** Features added that weren't specified in any earlier rev: resizable sidebar with pixel width persisted in localStorage (drag handle at the right edge, cross-iframe pointer capture handled via a full-screen overlay during drag), persistent expanded-folder and collapsed-drive state keyed by Drive id, themed 404 pages (`(app)/not-found.tsx` + root `not-found.tsx` + reusable `NavigatorNotFound` component), Navigator footer dropdown containing Preferences / theme toggle / Logout behind the user avatar, shadcn Checkbox primitive for the preferences dialog.
**Source docs:**
- `Docs Platform Thoughts` (Doc 1) — `/docs/22e7a695-de36-401a-8276-b2f457c710be`
- `From Sarj Docs to The Navigator` (Doc 2) — `/docs/fa918961-2c17-4e69-8088-b79629896eec`

---

## 1. Context

The current Sarj Docs implementation drifted from the original plan in `CLAUDE.md`, which called for iframe-embedded Google Docs with a Notion-like chrome around them. Instead, we built a 13-module `docs_transformer/` that parses the Google Docs API into custom blocks and renders them ourselves. This created:

- A large custom rendering surface that duplicates Google Docs functionality
- A `cached_content` JSONB column that must be kept in sync with Drive
- A webhook + scheduler pipeline to drive that sync
- A `page_permissions` / `workspace_memberships` access-control layer that can diverge from Drive's own ACLs
- A service account dependency (domain-wide delegation) with `backend/service-account-key.json` checked into the repo

Doc 1 critiques this architecture on security, scope, and simplicity grounds. Doc 2 proposes a pivot to a lightweight "Navigator" that iframes Google content, treats Drive as the source of truth, and removes most of the custom machinery.

This plan merges both docs into a concrete execution plan.

---

## 2. Guiding Principles

1. **Drive is the source of truth.** We never store authoritative document state in our DB — only presentational extras (icons, covers, sort order) and per-user performance caches.
2. **Access control lives in Drive, not our DB.** Every read uses the user's own OAuth token. A user can never see a file their Drive account can't see.
3. **No long-lived service account in the read path.** Sync is a side-effect of user activity, not a background job.
4. **Delete code aggressively.** With Claude it's cheap to bring back. The burden of proof is on keeping code, not removing it.
5. **Iframe first, render nothing ourselves.** All document content, editing, commenting, and suggesting is delegated to Google.
6. **No prod changes until the final pivot PR is merged.** All work happens on a long-running feature branch (`feat/navigator-pivot`) composed of sub-PRs for easy review. Production is untouched until one coordinated overnight cutover at the end.
7. **Workspaces map to real Google shared drives.** Spike findings (rev 4) showed that `DRIVE_ROOT_FOLDER_ID` in the current `.env` is a hidden shared drive that only the service account can access, not a folder inside a single parent drive. The user actually has access to 4 real shared drives: Engineering, Marketing, Sales, Sarj Teamwide. Each becomes a top-level "workspace" in the Navigator. Top-level folders **inside** each shared drive become "sections," and nesting continues from there. There is no `workspaces` table, no Admin SDK group sync, no workspace CRUD — `drives.list()` is the runtime source of truth.
8. **Tree cache is shared per drive, not per user.** Every member of a shared drive sees the same folder tree (Drive's ACL is drive-scoped), so caching per drive amortizes the cold-load cost across all members. Per-user differences live in overlay tables (`user_sort_order`, `user_folder_icons`, `user_preferences`).

---

## 3. Supported Content Types

The Navigator renders any Google Workspace file type by constructing the correct embed URL from the file's mimeType. Non-Google files (PDFs, images, videos) use Drive's native preview endpoint. Every viewer also has an "Open in Google [X]" toolbar button that deep-links to the non-embedded Google URL in a new tab.

| mimeType | Sidebar icon (global, fixed unless noted) | Embed URL pattern | "Open in Google" URL |
|---|---|---|---|
| `application/vnd.google-apps.document` | Docs icon (user-editable via `doc_icons`) | `https://docs.google.com/document/d/{id}/edit?embedded=true` | `https://docs.google.com/document/d/{id}/edit` |
| `application/vnd.google-apps.spreadsheet` | Sheets icon (fixed) | `https://docs.google.com/spreadsheets/d/{id}/edit?embedded=true` | `https://docs.google.com/spreadsheets/d/{id}/edit` |
| `application/vnd.google-apps.presentation` | Slides icon (fixed) | `https://docs.google.com/presentation/d/{id}/edit?embedded=true` | `https://docs.google.com/presentation/d/{id}/edit` |
| `application/vnd.google-apps.drawing` | Drawings icon (fixed) | `https://docs.google.com/drawings/d/{id}/edit?embedded=true` | `https://docs.google.com/drawings/d/{id}/edit` |
| `application/vnd.google-apps.form` | Forms icon (fixed) | `https://docs.google.com/forms/d/{id}/edit?embedded=true` | `https://docs.google.com/forms/d/{id}/edit` |
| `application/vnd.google-apps.folder` | Folder icon (per-user editable) | — (sidebar node only) | — |
| `application/pdf` | PDF icon (fixed) | `https://drive.google.com/file/d/{id}/preview` | `https://drive.google.com/file/d/{id}/view` |
| Image / video / other | Generic file icon (fixed) | `https://drive.google.com/file/d/{id}/preview` | `https://drive.google.com/file/d/{id}/view` |

**Icon rules (confirmed review #1):**
- **Google Doc icons are user-editable** and stored globally — everyone sees the same custom emoji for a given doc.
- **Folder icons are per-user editable.** User A sees folder X with 🚀, User B sees folder X with 🛠. Both are free to choose.
- **All other types have fixed, global icons** that reflect the file type (Sheets, Slides, Drawings, Forms, PDF, image, generic). They cannot be changed.

The frontend page component picks the URL template based on mimeType. No custom rendering, ever.

---

## 4. The New User Experience

### 4.1 Login
Google OAuth → `@sarj.ai` domain check → JWT cookie. **If a user is logged out or their session expires, the entire Navigator redirects to the login page** — not just the iframe area. Any 401 from the backend triggers a full-page redirect to `/auth/login`.

### 4.2 First session of the day — progressive load, never a loading spinner

The sidebar is **never blocked waiting for the slowest drive**. It fills in progressively as each piece of data arrives. A user always sees *something* within ~1.3 seconds and can start interacting with it immediately, even while deeper parts are still loading in the background.

**Load sequence (cold — first user of the day):**

1. **t ≈ 0s** — page loads, sidebar shows a minimal skeleton
2. **t ≈ 1.3s** — `drives.list()` returns; the 4 drive rows (Engineering, Marketing, Sales, Sarj Teamwide) appear in the sidebar, each marked as "loading folders"
3. **t ≈ 1.5–3s** — Marketing, Sales, Sarj Teamwide folders-only walks complete; their folder trees render. User can click into any of them.
4. **t ≈ 6s** — Engineering's folders-only walk completes; its folder tree renders. Sidebar is now fully navigable.
5. **Files inside each folder** load lazily on expand (~1.3s) or in background as the user idles — whichever happens first. Background prefetching can warm commonly-used folders while the user reads the sidebar.

**Load sequence (warm — everyone else):**

1. **t ≈ 0s** — page loads
2. **t ≈ 1.3s** — `drives.list()` returns; per-drive `drive_tree_cache` reads complete in parallel (~50ms each); sidebar fully populated, including files.
3. Instant navigation from then on (React Query client-side cache).

**Key design points:**

- **Per-drive parallel queries.** The frontend fires one request per drive via React Query; each drive's sidebar section populates independently. No drive waits for another.
- **Folders-only is the default first pass**, not a fallback. It's what gives us the 6s Engineering load instead of 14s. Files inside each folder load lazily on expand or via background prefetch — both cheap because individual folder listings are ~1.3s each.
- **Staleness is friendly.** On warm loads, if `drive_tree_cache` is older than TTL, the client *still* renders the stale cache immediately while the backend kicks off an async refresh. User never waits for fresh data; fresh data replaces the stale version when it arrives.
- **Background refresh.** Every sidebar load past TTL kicks off an async walk using the current user's token. The user doesn't see it happen.

### 4.3 Sidebar — the new shape

The sidebar is a projection of the user's **real Google shared drives**:

- **Top-level sidebar items = shared drives the user is a Member of.** The Navigator calls `drives.list()` with the user's own OAuth token at sidebar load. Whatever comes back becomes the set of "workspaces" they see. At Sarj today that's Engineering, Marketing, Sales, Sarj Teamwide — confirmed by spike in §17.
- **Top-level folders inside each shared drive = "sections".** (e.g., inside Engineering: Archive, Data Team, Platform Team, Teamwide, Vision Bank Team.)
- **Deeper nesting is unlimited** and mirrors Drive exactly.
- Each non-folder node displays its type-appropriate icon (see §3 icon rules).
- Per-user sort order (see §4.6) controls ordering within each folder.

**Access control is free.** If a user isn't a Member of a shared drive, `drives.list()` doesn't return it, and they can't see its cached tree even though the tree exists in our DB — the access check happens at `drives.list()` time, not at the cache level.

**Sidebar-level controls:**
- **"+" button on every folder (workspace or section)** — opens a menu to create a Doc, Sheet, Slides, Drawing, Form, or sub-Folder inside that specific folder.
- **Hide non-Doc files toggle** — per-user preference. Hides Sheets/Slides/Forms/Drawings/PDFs/images from the sidebar so the user sees only Google Docs. Files still exist in Drive; they're just visually filtered.
- **Hide specific workspaces (top-level folders)** — per-user preference. Users can individually hide one or more top-level folders from their sidebar to focus on only the workspaces they care about. This is a selective hide, not a blanket "hide all folders" — a user might hide `Marketing` and `Sales` but keep `Engineering` visible. Stored as an array of drive folder IDs in `user_preferences`.

### 4.4 Opening a file
Clicking a sidebar item routes to `/d/{drive_id}` (new URL scheme — uses the Drive file ID directly, no separate UUID). The page renders:

- **Breadcrumb** built from the user's `sidebar_cache` tree
- **Icon + title** (from Drive metadata + `doc_icons`)
- **A thin Navigator toolbar** above the iframe with at minimum:
  - An **"Open in Google [Docs | Sheets | Slides | Drawings | Forms | Drive]"** button that opens the non-embedded Google URL for this file in a new tab. The label and target URL are picked based on mimeType (see §3 table). If the file type doesn't have a meaningful "edit in Google" equivalent (e.g. a PDF), the button falls back to "Open in Drive."
- **A full-height iframe** for the file itself

No re-rendered content, no duplicate header, no custom toolbar beyond the thin Navigator strip. All document features — comments, suggesting, version history, Google's native share — work natively inside the iframe.

### 4.5 Creating a file
The "+" button on each folder in the sidebar opens a menu: **Doc, Sheet, Slides, Drawing, Form, Folder**. Selecting one calls a backend endpoint that creates the file in Drive (using the user's token), placed directly in that specific folder, then navigates to it.

There is no "draft" step. No "publish" flow. Private files live in the user's own `My Drive` outside the Navigator entirely.

### 4.6 Dragging and dropping
**Sort order is per-user** (decision 11.1). Each user organizes their own sidebar the way they want; dragging doesn't affect anyone else's view.

- Within a folder → updates `user_sort_order(user_id, drive_id)` via a fractional index.
- Across folders → calls Drive's `files.update` with `addParents`/`removeParents` using the user's token (the file genuinely moves in Drive, which affects everyone), *and* updates the user's own `user_sort_order` for its new position.

### 4.7 Sharing
No custom share dialog. Users click the Google Docs/Sheets/Slides native share button inside the iframe, which uses their own Drive permissions. No service account writes to Drive ACLs, ever.

### 4.8 Dark mode
Chrome (sidebar, header, breadcrumb, Navigator toolbar) respects dark mode. **Document body does not** — Google Docs has no dark mode, and the iframe renders Google's light theme. Decision 11.5 accepts this regression.

### 4.9 Search
**Search is replaced with the Google Drive Search API from day one** (decision 11.6). The top bar has a search input that queries Drive via the user's own OAuth token and returns results with title, type, breadcrumb, and a click-through to `/d/{drive_id}`.

**Access control is free.** Drive API calls authenticated as user X automatically return only files X has permission to see — Google enforces ACLs at the API level. We don't add any filtering on top.

**Scope is limited to the Sarj Docs shared drive**, not the user's entire Google Drive. The search request passes `corpora=drive&driveId={sharedDriveId}&includeItemsFromAllDrives=true&supportsAllDrives=true` so results come from the Navigator's shared drive only, not random personal `My Drive` files. Users can still search their personal files in `drive.google.com` directly if needed.

The existing `tsvector` search is deleted in the same sub-PR that adds Drive Search (Sub-PR C). No fallback; no parallel-running search backends.

---

## 5. Architecture

### 5.1 What gets killed

| Component | Why |
|---|---|
| `app/docs_transformer/` (13 modules) | No more custom rendering; iframe handles everything |
| `pages.cached_content` JSONB column | No content to cache; content lives in Drive |
| `pages` table itself | Replaced by `drive_tree_cache` (shared per drive) + `doc_icons` / `user_folder_icons` / `user_sort_order` overlays |
| `page_permissions` table | Drive is the ACL |
| `workspaces` table | **Confirmed removed (decision 11.2).** Top-level shared drive folders replace it visually |
| `workspace_memberships` table | Confirmed removed |
| `app/workspaces/` module and Admin SDK group sync | Confirmed removed |
| `admin.directory.group.readonly` + `admin.directory.group.member.readonly` scopes | No more Admin SDK calls |
| `app/webhooks/` + `drive_webhook_channels` table | No sync to drive; no cache to invalidate from Drive events |
| `DRIVE_WEBHOOK_TOKEN`, `INTERNAL_AUDIENCE`, `INTERNAL_SCHEDULER_SECRET` | No webhooks, no internal scheduler |
| `app/pages/background_tasks.py` two-pass sync | No sync |
| Search tsvector + GIN index | Replaced by Drive Search API in the same sub-PR that adds it (Sub-PR C). No fallback. |
| `app/search/` module | Deleted in Sub-PR C |
| `app/permissions/` module | Drive is the ACL |
| Custom "publish draft" flow and `My Drafts/{email}/` folder convention | Drafts live in user's own Drive |
| Recursive CTE tree queries | Tree is a per-user JSONB blob |
| `backend/service-account-key.json` on disk | **Rotated and removed at cutover time** (decision 8). The Navigator never uses it; it lingers only until the cutover merge. |
| The hidden `0AI_dKpHiz__7Uk9PVA` shared drive | **Content migrated out manually by a human** (rev 5) into the 4 real shared drives before cutover. The pivot code treats this as out-of-band prerequisite work, not something the pipeline orchestrates. The drive can be left empty or deleted afterward; not our concern. |

### 5.2 What stays

| Component | Purpose |
|---|---|
| Google OAuth login | Unchanged |
| `@sarj.ai` domain enforcement | Unchanged |
| JWT issuance, `token_version`, refresh rotation | Unchanged |
| Encrypted token storage on `users` | Unchanged |
| Next.js App Router + Shadcn chrome | Unchanged |
| Sidebar UI with infinite nesting + drag-drop | Unchanged shape, new data source |
| Dark/light mode for chrome | Unchanged |
| Alembic migration system | Used to drop old tables and add new ones |
| Drive OAuth scope `https://www.googleapis.com/auth/drive` | **Kept** (decision 11.3 — downgrading would break discovery) |

### 5.3 What's new

- `app/drive/drives.py` — wraps `drives.list()` for workspace enumeration
- `app/drive/tree_walker.py` — walks a single shared drive using `corpora=drive` + the user's token, returns a flat list; builds the tree in memory
- `app/sidebar/` — orchestrates the per-drive cache reads, background refresh, overlay joins, **and the singleflight coalescing helper** (§7.3)
- `app/extras/` — CRUD for `doc_icons`, `user_folder_icons`, `user_sort_order`
- `app/files/` — unified file CRUD (create, rename, move, delete) for any mimeType, using user token
- `app/drive/search.py` — Drive Search API integration (decision 11.6)
- Frontend `<FileViewer>` component — single component that picks the right iframe URL + Open-in-Google target based on mimeType
- Frontend `<NavigatorToolbar>` — thin toolbar above the iframe with the Open-in-Google button
- Frontend global 401 interceptor — full-page redirect to `/auth/login` on any unauthenticated response
- Frontend progressive sidebar — renders each drive's tree as its walk completes, rather than waiting for the slowest drive

---

## 6. Data Model Changes

### 6.1 New tables

```sql
drive_tree_cache:                          -- shared per-drive tree cache (rev 4)
  drive_id     TEXT PRIMARY KEY            -- Google shared drive ID
  tree         JSONB NOT NULL              -- full serialized tree (all members see this)
  refreshed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
  refreshed_by UUID REFERENCES users(id)   -- whose token was last used to walk this drive

doc_icons:                                 -- Google Doc icons, global and editable
  drive_id   TEXT PRIMARY KEY              -- Google Drive file ID of a Doc only
  icon       TEXT NULL                     -- emoji or image URL, visible to everyone
  cover_url  TEXT NULL
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
  updated_by UUID REFERENCES users(id)

user_folder_icons:                         -- per-user folder icons
  user_id    UUID REFERENCES users(id) ON DELETE CASCADE
  folder_drive_id TEXT NOT NULL
  icon       TEXT NULL
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
  PRIMARY KEY (user_id, folder_drive_id)

user_sort_order:                           -- per-user sidebar ordering (decision 11.1)
  user_id    UUID REFERENCES users(id) ON DELETE CASCADE
  drive_id   TEXT NOT NULL                 -- folder or file
  sort_order DOUBLE PRECISION NOT NULL     -- fractional index
  PRIMARY KEY (user_id, drive_id)

user_preferences:                          -- per-user UI prefs
  user_id              UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE
  show_non_doc_files   BOOLEAN NOT NULL DEFAULT TRUE
  hidden_workspace_ids TEXT[] NOT NULL DEFAULT '{}'   -- drive IDs this user has hidden
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
```

**Why shared per-drive instead of per-user (rev 4 change):** Drive's ACL is at the drive-membership level — every Member of a shared drive sees the same set of files and folders. So the tree is identical for all Members and caching it once amortizes the cold-load cost. Access control happens at sidebar load via `drives.list()` — users only get shown drives they're actually members of. A drive's cache may exist in the DB but a non-member never sees it.

**Icon model summary:**
- **Google Doc icons** → `doc_icons` (global, editable by any user with Drive write access)
- **Folder icons** → `user_folder_icons` (per-user, editable only by the user themselves)
- **Sheet / Slide / Drawing / Form / PDF / image / other** icons → fixed in frontend code, no DB row, cannot be changed

### 6.2 Tables dropped at cutover

- `pages`
- `page_permissions`
- `workspaces` (decision 11.2)
- `workspace_memberships` (decision 11.2)
- `drive_webhook_channels`
- Any search-related indexes (`tsvector`, GIN)

All drops happen in a single Alembic migration at the cutover, not incrementally. Staging validates the migration end-to-end before prod runs it.

### 6.3 Tables unchanged

- `users` (including `token_version` and encrypted token columns)

---

## 7. Sync Model

Sync is a side-effect of user activity. No background scheduler. No webhooks. No service account calls.

### 7.1 Reads — progressive, never blocking

Sync is a side-effect of user activity. No scheduler, no webhooks, no service account in the sync path. The client and backend cooperate to always render *something* fast and fill in the rest asynchronously.

**Sidebar load (per-drive streaming):**

1. Client hits `GET /sidebar/drives` → backend calls `drives.list()` with user token → returns the list of accessible drives (~1.3s). **This is also our access control check.**
2. Client renders the drive rows immediately, each in a "loading folders" state.
3. For each drive, client fires `GET /sidebar/drives/{drive_id}/folders` in **parallel** (one request per drive, via React Query).
4. Backend responds to each with `drive_tree_cache[drive_id]`:
   - **Warm cache**: return cached folder tree immediately (~50ms). If stale, also kick off a background refresh using the current user's token.
   - **Cold cache**: walk that drive's **folders only** synchronously with user's token (5.8s for Engineering, ~1.4s for the small drives), store in `drive_tree_cache`, return it.
5. As each drive's response arrives, the client renders that drive's folder tree in place. No drive waits for any other.
6. Files inside folders are **not fetched at sidebar load time**. They arrive via `GET /sidebar/folders/{folder_id}/files` when:
   - The user expands a folder (foreground lazy load, ~1.3s)
   - The client prefetches in the background for commonly-used or currently-visible folders

**Folder expand (lazy file load):**

- Client fires `GET /sidebar/folders/{folder_id}/files` → backend calls `files.list(q="'{folder_id}' in parents and not folder")` with user token (~1.3s) → returns the file list.
- Result is cached in both `drive_tree_cache` (persistent, shared) and the client's React Query store.
- Subsequent expands of the same folder are instant from client cache, then DB cache.

**Why folders-only for the initial pass:**

Spike (§17) measured Engineering at 13.8s for folders+files but only 5.8s for folders alone — because Drive's per-page latency is ~1.5s and folders-only fits in 4 paginated calls instead of 9. Loading files lazily removes that cost from the critical path. Total cold sidebar load: ~6s for the biggest drive, ~3s for everything else, all in parallel.

**File open** (`/d/{drive_id}`) → fetches Drive metadata with user's token (mimeType for iframe URL, title). Icon from `doc_icons` for Docs; from `user_folder_icons` for folders; hardcoded in frontend for other types.

**Search** → Drive Search API call with user's token, `corpora=drive&driveId=...` per accessible drive (decision 11.6). Access control automatic.

### 7.2 Writes (all use the user's own OAuth token)

- **Create file** → Drive API create, then update `drive_tree_cache[drive_id].tree` inline so the creator (and every other user on that drive) sees it on next load.
- **Rename / move / delete** → Drive API call, then update `drive_tree_cache` inline. Same drive's other users see the change on their next sidebar load.
- **Move across drives** → Drive API call (may fail if user doesn't have write on destination), then update **both** affected drive caches inline.
- **Set Doc icon** → updates `doc_icons` (global).
- **Set folder icon** → updates `user_folder_icons` (per-user).
- **Reorder (drag-drop)** → updates `user_sort_order` (per-user).
- **Hide workspace / toggle non-Doc files** → updates `user_preferences`.

**No scheduler, no webhooks, no service account in the sync path.**

### 7.3 Concurrency and cache coalescing

Multiple users can trigger the same underlying Drive read at the same time (thundering herd). Without coordination, we'd do redundant Drive walks, waste API quota, and slow everyone down. One small mitigation handles every case that matters.

**Singleflight cache coalescing (day-one requirement).**

`app/sidebar/` keeps an in-memory map `{key → asyncio.Future}` with an `asyncio.Lock` guarding the map. The keys are:

- `drive:{drive_id}` for full-drive folder walks
- `folder:{folder_id}` for folder lazy-loads (file listings inside a folder)

When a request needs to read drive X or folder Y:

1. Acquire the lock.
2. If the key has an in-flight future, grab a reference and release the lock.
3. Otherwise, create a new future, store it under the key, release the lock, and start the walk.
4. The requester awaits the future. **Every concurrent request for the same key awaits the same future** — only one real Drive walk happens.
5. When the walk finishes, resolve the future and remove it from the map.

This is ~30 lines of Python. Classic singleflight pattern (Go's `golang.org/x/sync/singleflight`, Django's `memoize_in_progress`). It applies to:

- **Cold-cache reads** — first user to log in starts the walk; users 2–N await the same result.
- **Background refreshes** — only one refresh per drive runs at a time.
- **Folder lazy loads** — concurrent clicks on the same folder coalesce to one Drive call.

**Write-write races (accepted as non-issue).**

If two concurrent walks for the same drive *do* somehow both finish (rare — only possible if singleflight times out or is bypassed), both UPSERT into `drive_tree_cache`. The upsert uses `ON CONFLICT (drive_id) DO UPDATE SET tree = excluded.tree, refreshed_at = excluded.refreshed_at WHERE excluded.refreshed_at > drive_tree_cache.refreshed_at` so only the freshest write survives. Both datasets are fresh anyway; last-writer-wins is fine.

**Write-read consistency during live user activity (accepted as non-issue).**

User A creates a file while User B's sidebar is rendering. Depending on timing, B sees either the pre-create or post-create state. The cache is eventually consistent by design — B sees the new file on the next TTL refresh. Acceptable for the product.

**Drive API rate limits (non-issue at Sarj scale).**

Google's per-project quota is comfortably above anything 50 users could generate. Even without singleflight, 50 concurrent cold walks × 9 pages = 450 calls in a minute is well inside the limit. Singleflight makes this a theoretical concern, not an operational one.

### 7.4 Scaling limitation — per-worker singleflight

**Singleflight coalesces within a single backend worker process.** If prod runs multiple Cloud Run instances (or multiple Uvicorn workers within one instance), each has its own in-memory map, and thundering herds can still happen *across* workers.

**At Sarj's current scale this is not a problem.** Cloud Run is typically running one instance at low traffic; all concurrent requests land on the same async event loop and share the same singleflight map. Day-one deployment is fine.

**Upgrade path when scale grows:** replace the in-memory lock with a **PostgreSQL advisory lock** keyed by `hash(drive_id)` or `hash(folder_id)`:

```python
async with db.begin():
    got_lock = await db.scalar(
        text("SELECT pg_try_advisory_xact_lock(:key)"),
        {"key": hash(f"drive:{drive_id}") & 0x7FFFFFFF},
    )
    if not got_lock:
        # Another worker is walking this drive right now. Wait briefly,
        # then re-check the cache — it should be populated.
        ...
```

Same semantics as in-memory singleflight, works across all workers and all instances. Trade: one extra DB round-trip per cache miss. Worth the cost only when per-worker coalescing is no longer enough.

**Trigger for the upgrade:** when metrics show simultaneous cold walks for the same drive happening from different worker PIDs, or when Cloud Run starts routinely scaling to 2+ instances. Not building this day one.

---

## 8. Security Improvements

Drawn from Doc 1's security asks and the architectural simplifications above.

**Important:** per decisions 1, 8, and 12, *no security change ships to prod independently*. Everything lands on the `feat/navigator-pivot` branch and ships to prod only as part of the one coordinated cutover PR. This is a conscious tradeoff — it keeps the blast radius of each individual sub-PR small while keeping prod stable.

### 8.1 Ships inside the pivot branch

- **Rotate and remove `backend/service-account-key.json`.** Performed *at cutover time* (decision 8), after the Admin SDK and workspace code have been deleted and the SA has nothing left to do. The new world has no long-lived service-account credential stored anywhere. Revoke the old key immediately after cutover.
- **Add security headers** in `frontend/next.config.ts`:
  - `Content-Security-Policy` — `default-src 'self'`, `frame-src https://docs.google.com https://drive.google.com https://*.googleusercontent.com`, plus scoped `script-src`, `style-src`, `img-src`, `connect-src`
  - `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`
  - `X-Frame-Options: SAMEORIGIN` (restricts who can embed *us*; does not affect what we embed in iframes)
  - `X-Content-Type-Options: nosniff`
  - `Referrer-Policy: strict-origin-when-cross-origin`
  - `Permissions-Policy` scoped tightly (disable camera, mic, geolocation, etc.)
- **Enable Google Security Command Center**, run a scan, address findings as a dedicated sub-PR.
- **Move any remaining secrets to Google Secret Manager.** The backend reads from Secret Manager at startup, not from env files or disk.
- **Put the backend behind Google Cloud Load Balancer behind Cloudflare**, max WAF settings on the LB. This is infrastructure (Terraform) and can land as its own sub-PR; flipping DNS happens at cutover.

### 8.2 Delivered by the pivot itself

- **Full custom ACL layer removed** — no more `page_permissions` divergence risk. Drive is authoritative.
- **Service account removed from read path entirely** — every Drive call is attributed to a real user, which is great for audit and revocation.
- **`cached_content` removed** — a DB leak no longer exposes document contents.
- **Webhook surface removed** — fewer public endpoints, fewer things to verify.
- **`admin.directory.group.readonly` scope dropped** — no more Admin SDK calls, no more cross-directory exposure.

### 8.3 Drive OAuth scope

**Decision 11.3:** Keep the current `https://www.googleapis.com/auth/drive` (full access) scope.

Downgrading was considered and rejected because:
- `drive.file` — only files the app *created* or opened via Google Picker. The Navigator cannot walk the shared drive to build a sidebar from pre-existing files. Users would have to Picker-add every file manually. Fatal.
- `drive.readonly` — can list everything, but no create/rename/move/delete. Breaks all write flows.
- `drive.metadata.readonly` — metadata only, can't fetch enough for iframe URL construction.

`drive` is the minimum scope the Navigator architecture requires.

---

## 9. DevOps & Tooling

Drawn from Doc 1's modernization asks. Every item below lands on `feat/navigator-pivot` as one or more sub-PRs. Nothing ships to prod until the final cutover merge.

### 9.1 Python (backend)

- Add `ruff` with strict settings (all rules enabled, explicit ignores)
- Add `mypy --strict`
- Add `pytest` with initial unit tests for auth, tree_walker, extras CRUD, Drive Search
- Upgrade all dependencies to latest via `uv sync --upgrade`
- Target Python 3.14 (from current 3.12)

### 9.2 TypeScript (frontend)

- Add `prettier` with config
- Jack `eslint` up to maximum strictness (consider `eslint-config-strict` or manual rules)
- Jack `tsconfig.json` to max strictness (`strict: true`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`)
- Add `vitest` unit tests for utility functions, hooks, and the FileViewer URL builder
- Upgrade all dependencies to latest

### 9.3 CI (`.github/workflows/`)

Currently only `deploy-backend.yml` and `deploy-frontend.yml`. Add to the feature branch:

- `ci-backend.yml` — runs `ruff check`, `mypy`, `pytest` on every push and PR
- `ci-frontend.yml` — runs `prettier --check`, `eslint`, `tsc --noEmit`, `vitest run` on every push and PR
- `ci-e2e.yml` — runs Playwright e2e on every PR (against a staging deployment)
- `codeql.yml` — GitHub CodeQL security scanning
- Branch protection rules on `main` applied at cutover: require all CI checks passing, require PR review, no force push

### 9.4 Dependency hygiene

- Enable Dependabot or Renovate for both `pyproject.toml` and `package.json`
- Automated PRs for minor/patch upgrades, gated on CI

---

## 10. Feature Impact Matrix

Every feature currently in Sarj Docs, how it changes:

| Feature | Status | Notes |
|---|---|---|
| Google OAuth login | Kept | Unchanged |
| Session expiry → full-page redirect to login | **New** | Any 401 anywhere triggers a hard redirect, not just an iframe-level one |
| Sidebar tree | Changed | Per-user JSONB cache built from Drive via user's token |
| Sidebar "workspace" concept | Changed | A "workspace" is now simply a top-level folder in the shared drive — no DB entity |
| Doc icons | Kept | Global and editable via `doc_icons` |
| Folder icons | Changed | **Per-user editable** via `user_folder_icons` (decision review #1) |
| Sheet / Slide / Drawing / Form / PDF / image icons | Changed | Fixed global type icons, hardcoded in frontend, not editable |
| Covers | Kept | Global via `doc_icons.cover_url` |
| Drag-and-drop reorder within folder | Kept | Updates `user_sort_order` **per-user** (decision 11.1) |
| Drag-and-drop across folders | Kept | Calls Drive `files.update` with user token + updates user's own sort order |
| Infinite nesting | Kept | Natural from Drive's folder structure |
| Create new Doc | Changed | User token, created directly inside the clicked folder via per-folder "+" button |
| Create new Sheet / Slide / Drawing / Form | **New** | Same "+" menu, different mimeType |
| Create new Folder | Changed | User token, via per-folder "+" button |
| Rename | Changed | User token, updates user's cache inline |
| Move | Changed | User token, updates user's cache inline |
| Delete (trash) | Changed | User token |
| View Doc | Changed | Full Google Docs iframe + thin Navigator toolbar with "Open in Google Docs" button |
| View Sheet | **New** | Google Sheets iframe + "Open in Google Sheets" |
| View Slide deck | **New** | Google Slides iframe + "Open in Google Slides" |
| View Drawing | **New** | Google Drawings iframe + "Open in Google Drawings" |
| View Form | **New** | Google Forms iframe + "Open in Google Forms" |
| View PDF | **New** | Drive preview iframe + "Open in Drive" |
| View image / video / other | **New** | Drive preview iframe + "Open in Drive" |
| "Open in Google [type]" toolbar button | **New** | Deep-links to non-embedded Google URL based on mimeType |
| Comments / suggesting / version history | Changed | Handled natively inside Google iframe — actually *more* features than before |
| Share dialog | **Removed** | Users use Google Docs' native share button inside the iframe |
| Private drafts (`My Drafts/{email}/`) | **Removed** | Drafts live in user's own Drive |
| Publish draft to workspace | **Removed** | No drafts to publish |
| Workspace creation | **Removed** | Workspaces are Drive folders |
| Workspace switcher | **Removed** | Top-level folders are workspaces; no switcher needed |
| Search (tsvector) | **Removed** | Deleted in Sub-PR C alongside Drive Search addition. No parallel search backends. |
| Drive Search API | **New (day one)** | Scoped to the Sarj Docs shared drive via `corpora=drive&driveId=...`. Access control enforced automatically by Drive API (user's own token = user's own ACL). |
| Starred pages | Changed | Uses Drive's native `starred` flag instead of a custom table |
| Last-edited tracking | Changed | Read from Drive's `modifiedTime` field, no custom table |
| Breadcrumbs | Kept | Built from user's `sidebar_cache` tree |
| Dark mode (chrome) | Kept | Unchanged |
| Dark mode (doc body) | **Removed (regression)** | Google Docs has no dark mode; iframe renders light. Decision 11.5 accepts this. |
| Hide non-Doc files toggle | **New** | Per-user preference in `user_preferences.show_non_doc_files` |
| Hide specific workspaces (top-level folders) | **New** | Per-user preference in `user_preferences.hidden_workspace_ids` — selective, not all-or-nothing |
| Drive webhook sync | **Removed** | No sync to do |
| Background cache refresh job | **Removed** | User activity is the refresh trigger |
| Admin SDK group sync | **Removed** | No workspaces, no group sync |

---

## 11. Decisions (resolved in review #1)

Previously open questions; answers from review #1 locked in.

1. **Sort order — per-user.** Each user organizes their own sidebar. Stored in `user_sort_order`. Does not affect anyone else's view. Notion-style.
2. **Workspaces and Admin SDK — dropped entirely.** No `workspaces` table, no `workspace_memberships`, no Admin SDK calls, no `admin.directory.group.readonly` scope. Top-level shared drive folders visually replace the workspace concept.
3. **Drive OAuth scope — keep `drive` (full access).** Downgrading was considered and rejected (see §8.3 for analysis). `drive.file` + Picker would break sidebar discovery; `drive.readonly` would break all writes; `drive.metadata.readonly` is insufficient.
4. **URL migration — break old URLs.** `/docs/{uuid}` links break at cutover. The shared drive is being cleaned up during migration anyway, so old bookmarks would be stale regardless. No redirect table.
5. **Dark mode regression — accepted.** Document body will always render Google's light theme. Chrome (sidebar, header, toolbar) continues to respect the user's dark mode preference.
6. **Search — Drive Search API only, replaces tsvector immediately.** The old `tsvector` search is deleted in the same sub-PR that adds Drive Search (Sub-PR C). No dual-backend state on the feature branch. Access control is free because Drive API calls authenticated as the user automatically return only files they can see. Scoped to the Sarj Docs shared drive via `corpora=drive&driveId=...` so results don't include the user's personal `My Drive`. **(Amended rev 8: shipped as a Spotlight-style `Cmd+K` command palette with lazy per-result snippet preview backed by `drive.files.export(text/plain)` + prefix-truncation stemming, not a simple top-bar input.)**
7. **No custom share dialog — decision locked (rev 8).** Originally stated as "users click the Google native share button inside the iframe, which uses their own Drive permissions". Verified experimentally that this doesn't work: Google Docs' Share dialog loads nested people-picker iframes from `contacts.google.com` / `accounts.google.com` / `apis.google.com`, and those nested documents ship with `frame-ancestors https://docs.google.com`, which fails when the top-level origin is anything other than `docs.google.com`. The browser's per-resource `frame-ancestors` walks the entire ancestor chain — no amount of CSP config on our side overrides it. A custom Drive-API-based share dialog was considered and rejected; users share by clicking "Open in Google Docs" in the Navigator toolbar, which opens the file in a top-level tab where the native Share dialog works. The trade-off is one extra tab for any share action.
8. **Forms open in a new tab permanently (rev 8).** Originally assumed Forms would iframe just like Docs/Sheets/Slides. Empirical test (a form created by `forms.new` under the signed-in user's own account) still shows a "You need access" gate inside our iframe regardless of creation path. The Forms editor has a stricter cross-origin iframe check than the other editors and fully bails out instead of degrading gracefully. Creating via the Forms API instead of Drive API was tested as a workaround and rejected — wouldn't fix the iframe gate. `FileRow` routes form clicks through `window.open(_blank)` and shows an `ExternalLink` indicator next to the filename; the create-file flow opens newly created Forms in a new tab too. The command palette's `openResult` follows the same rule.

---

## 12. Rollout Plan

**Single prod deploy.** All sub-PRs land on `feat/navigator-pivot` (long-running branch). Production is untouched until the final merge to `main`, which triggers one coordinated overnight cutover.

Since nothing reaches prod until the final merge, each sub-PR can **aggressively delete the old code it replaces** — the feature branch becomes the Navigator from Sub-PR C onward. No "old + new coexist" limbo, no feature flag, no parallel code paths. The only downside is that sub-PRs after C can't be partially reverted to restore old Sarj Docs behavior, but that's fine — the whole feature branch is atomic and reversible via `git`.

Each sub-PR below is opened against `feat/navigator-pivot` for focused review. Sub-PRs A and B are independent of the pivot and can be parallelized with each other.

### Sub-PR A — Hardening & tooling (no pivot logic)
Purely preparatory; does not touch old code paths.

- Add `ruff`, `mypy`, `pytest` to backend with initial tests on existing modules
- Add `prettier`, stricter `eslint`/`tsconfig`, `vitest` to frontend
- Add `ci-backend.yml`, `ci-frontend.yml`, `ci-e2e.yml`, `codeql.yml` workflows
- Enable Dependabot / Renovate
- Add all security headers to `next.config.ts`
- Upgrade dependencies to latest, target Python 3.14

### Sub-PR B — Infrastructure hardening (Terraform)
- GCLB + Cloudflare WAF configuration
- Secret Manager integration for remaining secrets
- Google Security Command Center enablement + findings remediation
- Branch protection rules on `main` (applied at cutover)

### Sub-PR B-prime — Content migration checklist (documentation only)

**Content migration is manual and happens entirely outside this repo.** A human will move files out of the hidden `0AI_dKpHiz__7Uk9PVA` drive into the 4 real shared drives (Engineering, Marketing, Sales, Sarj Teamwide) by hand — through the Google Drive UI or other operational tooling. No migration script, no SA automation, no code changes. The pivot's code path is fully decoupled from the move.

**What this sub-PR contains:**
- A checklist document in the repo describing:
  - A list of top-level folders currently in `0AI_dKpHiz__7Uk9PVA` that need to be classified
  - The mapping to destinations (Engineering / Marketing / Sales / Sarj Teamwide / archive-and-delete)
  - A verification procedure: for each destination drive, confirm expected file counts and spot-check a sample for permissions after the human migration
- **No code.** Nothing that modifies Drive. Nothing committed to the codebase other than the checklist itself.

**Gating rule:** this sub-PR lands whenever the checklist is written. The actual **human migration** is only required to be complete before **Sub-PR F (cutover)** — because that's the moment the Navigator becomes live in prod and the hidden drive stops working. Sub-PR C (code deletions) can merge to the feature branch long before the human migration is done; those deletions only affect prod at cutover.

**Risk profile:** zero from a code perspective. The real risk lives in the human migration itself — wrong-folder placement, missed files, permission issues — but those happen outside this repo and are handled by the human's judgment. The checklist is the backstop.

### Sub-PR C — New backend + delete replaced backend code
This PR simultaneously adds the Navigator backend and deletes everything it replaces. It can land on `feat/navigator-pivot` at any time — it doesn't depend on the human migration being complete, because the feature branch isn't in prod yet.

**Added:**
- Alembic migration: create `drive_tree_cache`, `doc_icons`, `user_folder_icons`, `user_sort_order`, `user_preferences`
- `app/drive/drives.py` (wraps `drives.list()` with the user token)
- `app/drive/tree_walker.py` (walks a single shared drive with `corpora=drive` + user token)
- `app/drive/search.py` (Drive Search API, `corpora=drive&driveId=...` per accessible drive)
- `app/sidebar/` (cache orchestration, background refresh, progressive walks)
- `app/extras/`, `app/files/` modules
- New endpoints mounted at clean paths (`/sidebar`, `/files`, `/search`, `/extras`)
- Unit tests for each new module
- Data migration script that copies `pages.icon` → `doc_icons`, `pages.sort_order` → `user_sort_order` (seeded per existing user)

**Deleted in the same PR:**
- `app/docs_transformer/` (all 13 modules)
- `app/pages/` (old router, services, background tasks)
- `app/workspaces/` (Admin SDK sync, workspace CRUD)
- `app/permissions/`
- `app/search/` (tsvector implementation)
- `app/webhooks/`
- Alembic migration that drops `pages`, `page_permissions`, `workspaces`, `workspace_memberships`, `drive_webhook_channels`, search indexes
- Removal of `DRIVE_WEBHOOK_TOKEN`, `INTERNAL_AUDIENCE`, `INTERNAL_SCHEDULER_SECRET` from config
- Removal of `admin.directory.group.readonly` + `admin.directory.group.member.readonly` scopes from OAuth consent screen
- Removal of related Python dependencies

After this PR merges to `feat/navigator-pivot`, the backend on the feature branch is 100% Navigator. No old code remains.

### Sub-PR D — New frontend + delete replaced frontend code
Same pattern: add new UI, delete old UI in the same PR.

**Added:**
- `<FileViewer>` component with iframe URL builder by mimeType
- `<NavigatorToolbar>` with Open-in-Google button
- New sidebar component wired to new backend
- New `/d/{drive_id}` route
- Global 401 interceptor → hard redirect to login
- Drive Search input in top bar
- Per-folder "+" button with create menu
- Drag-drop using `user_sort_order`
- Hide-workspace and hide-non-Doc-files preferences UI
- Fixed type icons for Sheets, Slides, Drawings, Forms, PDF, images, other

**Deleted in the same PR:**
- Old `/docs/[pageId]` route and its components
- Custom content renderer components (block renderers, table renderer, checklist renderer, etc.)
- Old sidebar components
- Old share dialog
- Old workspace switcher
- Old search UI
- Old tsvector-based client code
- Removal of related npm dependencies

After this PR merges, the feature branch frontend is 100% Navigator.

### Sub-PR E — Staging validation (doc + test artifacts, not code)
Runs against a staging deployment with real `@sarj.ai` accounts. Must pass before the draft cutover PR is marked ready.

- **Third-party cookie validation** in Chrome, Safari, Firefox. **Go/no-go gate.**
- **CSP validation** — iframe loads cleanly for every supported mimeType
- **Tree walker latency per drive (folders-only)** — measure cold populate on Engineering, Marketing, Sales, Sarj Teamwide with the folders-only default. Spike-measured targets (§17): ≤6s for Engineering's folder tree, ≤3s for the small drives, ≤1.4s warm load for subsequent users. Verify the progressive-rendering UX — each drive should appear independently, none blocking on another.
- **Cache sharing across users** — two users log in sequentially; second user's load should be ~1.4s (warm cache), not ~15s.
- **Drive Search validation** — functional test against a known set of documents in each of the 4 drives
- **Rename/move/delete propagation** between two users on the same drive — verify ≤cache-TTL freshness lag
- **Content migration verification** — all files that were in `0AI_dKpHiz__7Uk9PVA` are now findable in their destination drives with correct permissions
- **Full happy path** — login, sidebar loads progressively, open Doc/Sheet/Slides/PDF from each drive, edit inside iframe, create via "+" button, drag-drop, rename, hide a workspace
- **SA key smoke test** — confirm no code path in the feature branch reads `service-account-key.json`

### Sub-PR F — Cutover (merge to main)
This is the only change that touches prod. Scheduled for an overnight window.

**Pre-merge (hard gates):**
- **Human content migration out of `0AI_dKpHiz__7Uk9PVA` is complete** and verified against Sub-PR B-prime's checklist. This is the moment the hidden drive's contents actually need to live in the real shared drives — not any earlier, because the feature branch isn't in prod yet.
- Sub-PRs A–D merged to `feat/navigator-pivot`
- Sub-PR E staging validation complete and green

**The cutover PR itself is small** — it's just a merge commit from `feat/navigator-pivot` to `main`. All the deletion and schema changes already happened on the feature branch.

**Execution (overnight):**
1. Merge `feat/navigator-pivot` → `main`
2. Deployment runs automatically, Alembic migration drops old tables on prod DB
3. **Rotate and revoke `backend/service-account-key.json`** in GCP Console, delete the file from the repo if still present (should not be — Sub-PR C removed any code reading it)
4. Enable branch protection on `main`
5. Smoke test core flows on prod with a test `@sarj.ai` account
6. Announce Navigator is live in `#engineering` for next-day team rollout

No 48h dual-run period. No feature flag. One overnight switch.

---

## 13. Risks & What Could Go Wrong

### 13.1 High risk

- **Third-party cookies blocked in iframes.** Chrome, Safari, and Firefox have aggressive third-party cookie policies. If `docs.google.com` cookies are blocked inside our iframe, every document load redirects to a Google login page and the product breaks. **Go/no-go gate in Sub-PR E.** If broken: explore `CHIPS` or reconsider the iframe approach.
- **Manual content migration errors (out-of-band).** Moving ~4000 files by hand is error-prone: wrong-folder placement, missed files, broken permissions. This risk is outside the code pipeline — it lives with whoever performs the manual move. Mitigations: (a) verification checklist in Sub-PR B-prime, (b) optional audit script comparing hidden-drive contents to destination-drive contents, (c) Google Drive's own version history makes single-file restores easy if something lands wrong.
- **Cache warm-up UX for first-user-of-the-day.** Spike (§17) measured ~6s to fetch folders-only for Engineering. The progressive rendering design (§4.2) means the user always sees something by ~1.3s and a fully-navigable sidebar by ~6s, never a blocking spinner. Still, the first-user experience is slower than the ~1.4s warm load — worth watching in the first week post-launch.
- **CSP `frame-src` misconfiguration.** Getting the allowed Google domains wrong silently blocks iframes. Validated in Sub-PR E.
- **Drive Search quality unverified.** Spike (§17) confirmed Drive Search **works** — full-text, name, type, date filters all functional at 800–1500ms per query. But we deleted the old search in Sub-PR C with no fallback. If users find the UX worse (ranking, typo tolerance), there's no easy revert. Mitigation: collect user feedback in the first week and budget a follow-up PR if needed.
- **User membership mismatch.** The Navigator assumes every user who should see content X is a Member of the shared drive containing X. If some users were relying on group-level file shares (not drive membership), they won't see those files after migration. Needs verification during the content-migration audit in Sub-PR B-prime. If it looks bad, revisit decision 11.6 before deleting old code.

### 13.2 Medium risk

- **Dark mode regression in doc body.** Decision 11.5 accepts this, but teammates may still be surprised. Communicate clearly in the rollout announcement.
- **Browser not logged into Google.** If a user's browser has no Google session, the iframe redirects them to Google login. Navigator global 401 interceptor only handles *our* 401s, not Google's. Need a fallback UI: detect iframe-load-failed and show a "Log into your @sarj.ai Google account in this browser" prompt.
- **Per-user cache staleness across users.** User A renames a doc; User B sees the old title for up to 60s. Acceptable.
- **Users losing drafts feature.** Communicate early, document the "create in your own Drive" workaround in the announcement.
- **URL breakage for old bookmarks.** Decision 11.4 accepts this — shared drive is being cleaned up anyway. Announce in advance so people know not to trust existing `/docs/{uuid}` links.
- **Cutover rollback plan.** If something catastrophic breaks after the overnight merge, we need a revert strategy. Keep the pre-cutover DB snapshot and a revert commit ready.

### 13.3 Lower risk

- **Drive API quota.** At Sarj scale (<50 users) we're nowhere near per-project quotas. Worth monitoring after launch.
- **Icon/cover data loss at migration.** Current icons live on `pages.icon`; the cutover migration needs to copy them into `doc_icons` keyed by `google_drive_id` before dropping `pages`. Data migration script to be written as part of Sub-PR C. Note: `google_drive_id` values for files that got re-parented during Sub-PR B-prime are **still valid** (Drive IDs are stable across moves), so icons survive the content migration.
- **`sort_order` migration.** Current data is global; we're moving to per-user. Cutover migration copies current `pages.sort_order` into `user_sort_order` for every existing user as a starting baseline.
- **Stale `drive_tree_cache` rows for deleted drives.** If a shared drive is deleted, its cached tree stays in the DB until manually cleaned. Harmless (no user will `drives.list()` it), but a cleanup cron could run monthly.
- **Per-user folder icons lost on migration.** Existing global folder icons become each user's starting point, then users are free to customize.

### 13.4 Non-risks (explicitly stated)

- **DB leak exposure.** With `cached_content` gone, a DB leak exposes only metadata (titles, icons, sort order), not document content.
- **SA key leak.** After cutover, no SA key exists at all in the read path.
- **Sync divergence bugs.** No sync, no divergence.
- **Webhook replay attacks.** No webhooks.
- **ACL drift.** Drive is the only ACL.

---

## 14. What's NOT in this plan (explicitly out of scope)

- Offline support
- Mobile native apps
- Real-time presence indicators outside of what Google Docs natively provides
- AI/LLM features over documents
- Commenting or annotation overlays on top of the iframe
- Cross-document linking UI (users can still paste Drive links)
- Custom document templates (Google Docs templates still work natively)

---

## 15. Approval Checklist

All six review #1 decisions are locked in (§11). Remaining items before code starts:

- [x] §11.1 — sort order: **per-user**
- [x] §11.2 — workspaces + Admin SDK: **dropped entirely** (in rev 4: workspaces = real shared drives via `drives.list()`)
- [x] §11.3 — Drive OAuth scope: **keep `drive` (full access)**
- [x] §11.4 — old URLs: **break at cutover**
- [x] §11.5 — dark mode on doc body: **regression accepted**
- [x] §11.6 — search: **Drive Search API from day one** (shipped as a Cmd+K command palette with lazy snippet preview — rev 8)
- [x] §11.7 — no custom share dialog; users click Open-in-Google-Docs (rev 8, replaces the original "click the native button inside the iframe" plan after the `frame-ancestors` discovery)
- [x] §11.8 — Forms open in a new tab permanently (rev 8, after verifying the Forms editor hard-gates in cross-origin iframes regardless of creation path)
- [x] Drive Search spike — **complete (§17)**
- [x] Tree walker latency spike — **complete (§17)**
- [x] Plan rev 8 reviewed — iframe-capability discoveries + implementation amendments landed 2026-04-16
- [x] Sub-PR B-prime checklist document merged to `feat/navigator-pivot` (`e1e98e1`)
- [ ] Human content migration out of `0AI_dKpHiz__7Uk9PVA` complete and verified **before the cutover PR merges to main** (not gated to any earlier sub-PR)
- [x] `feat/navigator-pivot` branch cut from `main`
- [ ] Draft cutover PR opened against `main` for visibility
- [x] Sub-PR A kicked off and merged (`df2b7db` backend tooling, `0b4dd67` frontend tooling, `10ee859` CI, `7ff8467` python bump)
- [x] Sub-PR B infra hardening landed on the feature branch (`4760f07` — GCLB + Cloudflare WAF + Secret Manager cleanup + branch-protection script). Still needs an operator with GCP + Cloudflare creds to run `terraform apply`.
- [x] Sub-PR C backend pivot merged to the feature branch (`d677040` through `55c47b9` + rev 8 strict-scoping hardening in `f58552b`)
- [x] Sub-PR D frontend pivot merged to the feature branch (D1–D6 + rev 8 sidebar polish / command palette / icon editing in `8c1d1a2`, `53a7c46`, `f843f7a`)
- [x] Sub-PR E staging validation — dev-based pass complete (`53e6343` runbook + `6b54bef` rev 8 reframe as cutover-night runbook)

---

## 16. Branch & PR Plan

Once this plan is approved:

1. Cut branch `feat/navigator-pivot` from `main`
2. Open a draft PR titled `feat: pivot to The Navigator` from `feat/navigator-pivot` → `main`, linking this document. This PR stays draft until cutover night.
3. All work lands as sub-PRs against `feat/navigator-pivot` (not `main`). Sub-PRs follow the breakdown in §12: A (tooling), B (infra), B-prime (migration checklist — documentation only), C (backend), D (frontend), E (staging validation doc). Sub-PRs may be opened in parallel; none block each other at the code level. The **human content migration itself** is the one hard dependency, but it only gates Sub-PR F (cutover) — not Sub-PR C.
4. Each sub-PR gets its own focused review. CI runs on every sub-PR against the feature branch.
5. When Sub-PR E validation passes, the draft cutover PR is marked ready, reviewed, and merged overnight. That merge is the only touch to prod.
6. After cutover, the `feat/navigator-pivot` branch is deleted.

---

## 17. Spike Findings (rev 4)

Raw data from the pre-pivot spikes that informed rev 4's architecture changes. Run on 2026-04-15 against live Drive using `aalhashemi@sarj.ai`'s OAuth token.

### 17.1 Drive Search API — works well

Tested against `corpora=allDrives` with full-text, name, type, and date filter queries. Sample results:

| Query | Results | Latency |
|---|---|---|
| `name contains 'plan'` | 20 | 934ms |
| `fullText contains 'dark mode'` | 6 | 824ms |
| `fullText contains 'navigator'` | 1 (correctly found "From Sarj Docs to The Navigator") | 811ms |
| `fullText contains 'security'` | 20 | 986ms |
| `fullText + mimeType='document'` | 20 | 1123ms |
| Multi-word phrase `'sarj docs platform'` | 20 | 1057ms |

**Findings:**
- All query types functional: name, full-text, mimeType, date, combinations.
- Latency range 800–1800ms, acceptable for interactive search.
- Access control is automatic — user's own OAuth token only returns files they can see. No app-side filtering needed.
- `corpora=drive&driveId=...` returned 403 `teamDriveMembershipRequired` for the hidden drive. Fine — `corpora=allDrives` works regardless and `drives.list()` gives us the set of drive IDs to scope searches to.

**Decision:** ship Drive Search in Sub-PR C. Delete tsvector in the same PR. No fallback needed.

### 17.2 The `DRIVE_ROOT_FOLDER_ID` finding — critical

```
ID in .env: 0AI_dKpHiz__7Uk9PVA

files().get() → 404 "File not found"
drives().get() → 404 "Shared drive not found"

drives().list() with user token returns:
  0AEHVYATmK2c3Uk9PVA — Engineering
  0ALbcFI0-h_mcUk9PVA — Marketing
  0ANC3fHHg2Th3Uk9PVA — Sales
  0AKz0g8Uh4umNUk9PVA — Sarj Teamwide
```

`0AI_dKpHiz__7Uk9PVA` is a **fifth, hidden shared drive** that only the service account is a direct member of. The current backend uses that SA to list and manage files there; users access individual files via group shares. This is incompatible with a user-token-based Navigator: if the user can't see the drive in `drives.list()`, the Navigator can't cache its tree with their token either.

**Consequence:** Sub-PR B-prime was added to the plan. Content must be migrated out of `0AI_dKpHiz__7Uk9PVA` into the 4 real shared drives before Sub-PR C's code deletions remove the SA path.

### 17.3 Tree walker latency — real numbers

Walking all 4 accessible shared drives in parallel with `corpora=drive` and the user's own token:

| Drive | Files | Folders | API calls | Cold latency |
|---|---|---|---|---|
| Engineering | 4058 | 1624 | 9 | 13,863ms |
| Marketing | 23 | 5 | 1 | 1,638ms |
| Sales | 149 | 30 | 1 | 1,651ms |
| Sarj Teamwide | 20 | 3 | 1 | 1,667ms |
| **TOTAL (parallel)** | **4250** | **1662** | **12** | **13,875ms** |
| + `drives.list()` | | | | +1,259ms |
| **Cold load total** | | | | **~15,134ms** |

Tree reconstruction from the flat file list: **2ms** (trivial).

**Per-page Drive API latency is the bottleneck — ~1.5s floor per paginated call.** Engineering needs 9 serial pages because of pagination, which can't be parallelized within a single drive.

**Folders-only — the actual production strategy** (§4.2, §7.1):

| Drive | Folders | API calls | Latency |
|---|---|---|---|
| Engineering | 1624 | 4 | 5,862ms |
| Marketing | 5 | 1 | 1,404ms |
| Sales | 30 | 1 | 1,444ms |
| Sarj Teamwide | 3 | 1 | 1,554ms |
| **TOTAL (parallel)** | **1662** | **7** | **5,867ms** |
| + `drives.list()` | | | +1,380ms |
| **Cold load** | | | **~7,247ms** |

Lazy file load per folder (on user-expand): ~1.3s per folder.

### 17.4 Why shared per-drive cache (not per-user)

- Drive's ACL is at the drive-membership level. Every Member of `Engineering` sees the identical folder tree.
- Caching per drive means only the **first user of the day** per drive pays the 14s cold-walk cost. Every subsequent user reads from DB in ~50ms.
- At 4 drives and ~50 users, shared caching amortizes the cost across ~50x more reads.
- Per-user overlays (`user_sort_order`, `user_folder_icons`, `user_preferences`) handle the per-user differences without duplicating the tree.

**This replaced the earlier `sidebar_cache (user_id PRIMARY KEY)` design.**

### 17.5 Cold-load UX — how it actually feels

With the progressive, folders-only-first architecture from §4.2 and §7.1, there is **no 14-second loading state anywhere**. The user sees:

1. **t ≈ 1.3s** — the 4 drive rows appear.
2. **t ≈ 3s** — Marketing / Sales / Sarj Teamwide folder trees render.
3. **t ≈ 6s** — Engineering's folder tree renders; sidebar fully navigable.
4. **Files inside folders** load lazily on expand (~1.3s) or via background prefetch.

Warm loads are ~1.4s end-to-end for everything.

The folders-only-first design plus per-drive parallel streaming means the user is never blocked on the slowest drive. Background refresh on every load past TTL keeps caches warm.

### 17.6 Spike scripts — not committed

The spike scripts (`drive_search_spike.py`, `real_drives_spike.py`, `folders_only_spike.py`, `verify_id.py`, etc.) live only on the dev machine in `/tmp/` and are **not committed to the repo**. They were throwaway validation tools; their only lasting output is the numbers recorded in this section.
