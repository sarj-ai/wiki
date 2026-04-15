# Navigator Staging Validation — Sub-PR E

**Status:** Runbook. Execute top-to-bottom before Sub-PR F (cutover) can be marked ready.
**Environment:** staging deployment of `feat/navigator-pivot`. Not prod.
**Accounts:** two real `@sarj.ai` accounts are required. Call them **User A** (day-one cold-cache tester) and **User B** (warm-cache tester + propagation counterparty).
**Source of truth for this runbook:** `PIVOT_PLAN.md` §12 (Sub-PR E), §13 (risks), §17 (spike findings). If a section here contradicts the plan, the plan wins.

Every section below has an **expected** and an **actual** slot. Fill in the actual result as you go. Anything marked **go/no-go gate** blocks the cutover outright — if it fails, stop and escalate before touching prod.

---

## 0. Pre-flight

- [ ] `feat/navigator-pivot` deployed to staging (backend + frontend)
- [ ] Staging Postgres has the Navigator Alembic migration applied (`drive_tree_cache`, `doc_icons`, `user_folder_icons`, `user_sort_order`, `user_preferences` exist; legacy `pages`/`workspaces`/`page_permissions`/`workspace_memberships`/`drive_webhook_channels` dropped)
- [ ] Staging `.env` has no reference to `DRIVE_WEBHOOK_TOKEN`, `INTERNAL_AUDIENCE`, or `INTERNAL_SCHEDULER_SECRET`
- [ ] Staging `frontend/.env.local` points `NEXT_PUBLIC_API_URL` at the staging backend
- [ ] `backend/service-account-key.json` is **not** referenced by any running code path in the feature branch (grep check, covered again in §9)
- [ ] Both test accounts can log in to staging via Google OAuth and land on `/`

---

## 1. Third-party cookie validation — **go/no-go gate**

The Navigator iframes `docs.google.com`, `drive.google.com`, and `*.googleusercontent.com`. If a browser blocks third-party cookies, the iframe redirects to a Google login page and the product is broken. This is the single biggest risk in the pivot (PIVOT_PLAN §13.1).

Test each browser fresh (incognito/private window with the default third-party-cookie policy, not a "third-party cookies allowed" profile).

### Chrome (stable, desktop)
- [ ] Log in to staging as User A
- [ ] Click a Google Doc in the sidebar
- [ ] **Expected:** iframe renders the doc body within ~2s; cursor lands inside the doc; typing a character persists when you reload
- [ ] **Actual:**

### Safari (desktop)
- [ ] Same flow, fresh private window
- [ ] **Expected:** same as Chrome
- [ ] **Actual:**

### Firefox (desktop)
- [ ] Same flow, fresh private window
- [ ] **Expected:** same as Chrome
- [ ] **Actual:**

### Failure mode
If any browser shows a Google login prompt inside the iframe or the iframe stays blank past ~5s, **stop**. Options to explore before the cutover:
- `CHIPS` (partitioned cookies) — allows iframe cookies under a per-top-level partition
- Reconsider the iframe-first approach entirely (this would invalidate the pivot premise)

Record the decision in the review thread on the draft cutover PR.

---

## 2. CSP validation

Every supported mimeType must load cleanly inside the iframe with our production CSP (`frontend/next.config.ts`). A misconfigured `frame-src` silently blocks iframes — no popup, just a blank rectangle.

Open DevTools Console while each test runs. **Any CSP violation is a fail**, even if the content still appears.

| File type | Sample | Expected |
|---|---|---|
| Google Doc | any doc in Engineering | renders, no console violations |
| Google Sheet | any sheet in Engineering | renders, no console violations |
| Google Slides | any deck in Engineering | renders, no console violations |
| Google Drawing | any drawing | renders, no console violations |
| Google Form | any form | renders, no console violations |
| PDF | upload a PDF to Engineering | Drive preview renders |
| Image | upload a PNG | Drive preview renders |

- [ ] All seven rows pass
- [ ] DevTools Console shows zero `Refused to frame` or `Content Security Policy` warnings across the whole run
- [ ] **Actual:**

---

## 3. Tree walker latency per drive — folders-only, cold

Spike numbers to beat (PIVOT_PLAN §17.3):

| Drive | Target cold walk |
|---|---|
| Engineering | ≤ 6s |
| Marketing | ≤ 3s |
| Sales | ≤ 3s |
| Sarj Teamwide | ≤ 3s |
| `drives.list()` | ≤ 1.4s |
| **First drive visible in sidebar** | **~ 1.3s** |
| **All four drives fully loaded** | **~ 6s** |

### Procedure
1. **Truncate `drive_tree_cache` on staging DB** so every drive starts cold:
   ```sql
   DELETE FROM drive_tree_cache;
   ```
2. Hard-reload the Navigator as User A. Open DevTools Network tab, filter for `/sidebar`.
3. Measure:
   - [ ] Time to `/sidebar/drives` response: **_____ ms** (expected ≤ 1.4s)
   - [ ] Time to `/sidebar/drives/{engineering}/folders` response: **_____ ms** (expected ≤ 6s)
   - [ ] Time to `/sidebar/drives/{marketing}/folders` response: **_____ ms** (expected ≤ 3s)
   - [ ] Time to `/sidebar/drives/{sales}/folders` response: **_____ ms** (expected ≤ 3s)
   - [ ] Time to `/sidebar/drives/{teamwide}/folders` response: **_____ ms** (expected ≤ 3s)
4. **Progressive-rendering UX check** — record by stopwatch or DevTools Performance trace:
   - [ ] First drive row visible in sidebar at ~1.3s? (not "all four at 6s")
   - [ ] Marketing/Sales/Teamwide render independently of Engineering? (they do not wait on it)

**Fail mode:** if any single drive blocks the whole sidebar, the React Query fan-out is broken. Re-verify that `DriveSection` fires its own query per drive and that the sidebar doesn't `await` all of them together. See `frontend/src/components/navigator-sidebar/navigator-sidebar.tsx` — drives render inside a `.map`, each calling `useDriveTree` in its own component.

---

## 4. Cache sharing across users — warm load

The whole point of the shared-per-drive cache is that the first user of the day eats the 6s cold walk and every subsequent user reads the DB cache in ~50ms (PIVOT_PLAN §17.4).

### Procedure
1. User A has just completed §3 above — `drive_tree_cache` is now warm for all four drives.
2. User B hard-reloads the Navigator in a **different browser** (so React Query client cache is fresh).
3. Measure User B's `/sidebar/drives/{engineering}/folders` latency in Network panel.

- [ ] User B's Engineering folders response: **_____ ms** (expected ≤ 200ms, target ~50ms)
- [ ] User B's sidebar fully populated in under 1.5s end to end

**Fail mode:** if User B's load is anywhere near cold-walk latency (~6s), the cache is not being read. Check `app/sidebar/cache.py:get_drive_tree` — the warm path should `return tree` before calling into `_sf.do`. Verify the row exists in `drive_tree_cache` between the two runs.

---

## 5. Drive Search validation

Drive Search replaces the deleted tsvector path (PIVOT_PLAN §11.6). The backend fans out one `files.list(q=fullText contains '…')` per drive the user has access to, scoped via `corpora=drive&driveId=…`.

### Procedure
Pre-seed some known content:
- [ ] User A has a Doc titled `Staging Validation Canary Doc` in Engineering with the body text `nectarine pinwheel` (intentionally weird to avoid false hits)
- [ ] User A has a Sheet titled `Q4 Canary Rollup` in Marketing

Run each query in the sidebar search input as User A:
| Query | Expected hits | Actual |
|---|---|---|
| `Canary` | both docs | |
| `nectarine pinwheel` | only the Engineering doc (full-text match) | |
| `Q4 Canary` | only the Marketing sheet | |
| `ffflslsl` (garbage) | zero results, not an error | |

- [ ] Latency per query: **_____ ms** (target 800–1500ms per PIVOT_PLAN §17.1)
- [ ] Clicking a result navigates to `/d/{drive_id}` and the file loads

### Access-control spot-check
- [ ] Create a doc in User A's personal **My Drive** (NOT the shared drives) titled `Private Canary`
- [ ] Search `Private Canary` from User A's sidebar → **zero results** (`corpora=drive` scope excludes My Drive — PIVOT_PLAN §4.9)
- [ ] **Actual:**

### User-isolation spot-check
- [ ] Pick a drive User A is a member of but User B is NOT
- [ ] Add a doc titled `Isolation Canary` to that drive as User A
- [ ] Search `Isolation Canary` from User B's sidebar → zero results
- [ ] **Actual:**

---

## 6. Rename / move / delete propagation between two users

Write paths invalidate `drive_tree_cache` for the affected drive (backend `files.router._invalidate_drive_of`) and the client mutation hook invalidates the React Query cache (`useCreateFile` / `useRenameFile` / `useMoveFile` / `useDeleteFile` in `hooks/use-navigator-mutations.ts`). Propagation should be immediate on the next sidebar load and at most cache-TTL-delayed across separate sessions.

### Procedure
Both users in Engineering at once. Pick a folder both can see.

- [ ] **Create (User A, seen by User B):** User A clicks `+ Doc` inside `Engineering/Platform Team`, names it `Propagation Test 1`. User B reloads the sidebar. **Expected:** `Propagation Test 1` appears in User B's tree within one reload.
- [ ] **Rename (User A, seen by User B):** User A renames it to `Propagation Test 1a`. User B reloads. **Expected:** new name appears.
- [ ] **Move (User A, seen by User B):** User A opens the `…` menu → Move to… → picks `Engineering/Data Team`. User B reloads. **Expected:** doc vanishes from Platform Team, appears in Data Team.
- [ ] **Delete (User A, seen by User B):** User A opens `…` → Move to trash → confirm. User B reloads. **Expected:** doc gone from sidebar. Verify it's in User A's `drive.google.com/drive/trash`.

**Fail mode:** if User B has to wait the full 10-minute cache TTL for changes, backend invalidation is broken. Inspect the write path in `app/files/router.py` — every mutation should call `_invalidate_drive_of` which deletes the `drive_tree_cache` row.

---

## 7. Content migration verification

The human content migration out of `0AI_dKpHiz__7Uk9PVA` is performed by hand per Sub-PR B-prime. This step confirms it actually happened before cutover.

### Procedure
- [ ] Sub-PR B-prime checklist is complete and signed off by whoever did the migration
- [ ] For each of the four real shared drives, confirm file counts match the checklist's expected values (±5% tolerance — some dupes/trashings during human migration are normal)
- [ ] Spot-check 10 files that moved: each is now in the correct destination drive, opens cleanly, and has the correct Drive-level permissions (owner is still a real user, not the SA)
- [ ] Direct `files().get(0AI_dKpHiz__7Uk9PVA)` via a Drive API client returns `404 Shared drive not found` OR the drive is empty of anything besides `.tombstone` / placeholder files

Anything still living under `0AI_dKpHiz__7Uk9PVA` at cutover time will be inaccessible post-cutover because the Navigator's user-token code path cannot see it. **Escalate any stranded files before proceeding.**

---

## 8. Full happy path — end-to-end

One user, one session, from cold login to fully navigating the product. This is the dress rehearsal.

User A, fresh incognito window, third-party cookies at browser default.

- [ ] Land on `/`, redirected to `/login`, click "Sign in with Google"
- [ ] Complete Google OAuth with an `@sarj.ai` account, redirected back to `/`
- [ ] Sidebar renders progressively — first drive row in ~1.3s, Engineering's folder tree in ~6s
- [ ] Click a Google Doc in Engineering → `/d/{id}` loads, iframe renders, cursor in doc body
- [ ] Click a Sheet in Marketing → Sheets iframe loads
- [ ] Click a Slides deck → Slides iframe loads
- [ ] Click a PDF uploaded earlier → Drive preview renders
- [ ] Click the toolbar's "Open in Google Docs" button → new tab opens the non-embedded Google URL
- [ ] From the sidebar, click `+` on a drive header → create a new Doc named `Happy Path Canary` → new doc opens in viewer
- [ ] Rename it via the `…` menu → toast confirms → new name in sidebar
- [ ] Drag-drop a sibling above `Happy Path Canary` — reorder persists after page reload
- [ ] Open `…` → Move to… → pick a different folder → dialog closes, file appears in new location
- [ ] Open `…` → Move to trash → confirm → file disappears from sidebar
- [ ] Search `Canary` → every canary doc from earlier sections shows up
- [ ] Open the settings dropdown → toggle "Show non-Doc files" off → Sheets/Slides/PDFs disappear from sidebar
- [ ] Toggle it back on → they reappear
- [ ] Toggle "hide Sarj Teamwide" → that drive row disappears
- [ ] Toggle it back on → it reappears
- [ ] Click the user avatar / log out → redirected to `/login`
- [ ] Attempt to visit `/d/{some_known_file_id}` directly while logged out → redirected to `/login` with `?redirect=…`
- [ ] Log back in → landed on the original file

**Any step that fails is a bug; file it, fix it, and rerun the happy path start-to-finish.**

---

## 9. SA key smoke test

The pivot removes the service account from the read path entirely (PIVOT_PLAN §5.1, §8.1). Before cutover we need to prove nothing in the running feature branch reads `service-account-key.json`.

- [ ] `git grep -n service-account-key.json backend/app` returns **no matches** (only PIVOT_PLAN/CLAUDE.md references in the repo, not in runtime code)
- [ ] `git grep -n 'DRIVE_WEBHOOK_TOKEN\|INTERNAL_AUDIENCE\|INTERNAL_SCHEDULER_SECRET' backend/app` returns **no matches**
- [ ] On staging, temporarily rename `backend/service-account-key.json` to `service-account-key.json.disabled` and restart the backend
- [ ] Run §8 happy path again
- [ ] **Expected:** everything still works. If anything breaks, the SA path is still live somewhere and must be found and removed **before cutover**
- [ ] Restore the file name so the rest of this runbook can proceed (or leave it disabled if you're confident)

### grep sweep
- [ ] `git grep -n workspaces\\.sync\\|admin_sdk\\|groups_service\\|DRIVE_WEBHOOK_TOKEN backend/app` → no matches
- [ ] `git grep -n getPageTree\\|page_permissions\\|workspace_memberships frontend/src` → no matches

---

## 10. Sign-off

When every section above is green:

- [ ] All go/no-go gates (§1 third-party cookies) passed in all three browsers
- [ ] No CSP violations (§2)
- [ ] Cold walk latencies within spike targets (§3)
- [ ] Warm cache works across users (§4)
- [ ] Drive Search returns correct hits + respects scope (§5)
- [ ] Write propagation works in one reload (§6)
- [ ] Content migration confirmed complete (§7)
- [ ] Happy path passes end-to-end (§8)
- [ ] SA key smoke test — Navigator runs with the key file renamed (§9)

**Sign-off:**
- Operator: ________________
- Date: ________________
- Staging commit SHA: ________________
- Notes / deferred items:

Once signed off, mark the draft cutover PR as **Ready for review** and schedule the overnight cutover per PIVOT_PLAN §12 Sub-PR F.
