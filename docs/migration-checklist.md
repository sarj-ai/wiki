# Hidden Drive Content Migration Checklist

**Status:** Operational runbook. Executed manually by a human, out-of-band from the code pipeline.
**Related plan section:** [`PIVOT_PLAN.md`](../PIVOT_PLAN.md) §12 Sub-PR B-prime.
**Hard gate:** **This migration must be complete and verified before Sub-PR F (cutover) merges to `main`.** Earlier sub-PRs can land on `feat/wiki-pivot` without waiting for this work — they don't reach prod until cutover.

---

## 1. Why this exists

The current Sarj Wiki backend stores its app-managed content in a hidden Google shared drive (`0AI_dKpHiz__7Uk9PVA`) that only the service account is a direct member of. Users access individual files there via group-shared permissions but never see the drive itself in their Drive UI.

The Sarj Wiki architecture can't work with that pattern. `drives.list()` called with a user's own OAuth token returns only the drives the user is a direct member of — which at Sarj means **Engineering, Marketing, Sales, Sarj Teamwide**. The hidden drive is invisible to every user, so Sarj Wiki cannot cache its tree, walk its folders, or serve its files.

**Before cutover, every file currently in `0AI_dKpHiz__7Uk9PVA` must be moved into one of the four real shared drives** (or archived/deleted). After the move, the hidden drive can be left empty and eventually deleted by a Workspace admin.

This checklist is how we know the migration is complete and safe to cut over.

---

## 2. The four destination drives

Confirmed by `drives.list()` with a real user OAuth token during the rev 4 spike:

| Drive ID | Name | Role |
|---|---|---|
| `0AEHVYATmK2c3Uk9PVA` | Engineering | Engineering workspace |
| `0ALbcFI0-h_mcUk9PVA` | Marketing | Marketing workspace |
| `0ANC3fHHg2Th3Uk9PVA` | Sales | Sales workspace |
| `0AKz0g8Uh4umNUk9PVA` | Sarj Teamwide | Cross-team / teamwide content |

The source drive being drained:

| Drive ID | Name (if any) | Access |
|---|---|---|
| `0AI_dKpHiz__7Uk9PVA` | (hidden — app drive) | Service account only |

---

## 3. Classification rules (team-agreed)

For each top-level folder currently under `0AI_dKpHiz__7Uk9PVA`, decide its destination **before** moving anything. Record decisions in the table below.

**Guidelines:**
- **Engineering content** → Engineering drive (default for anything team-technical).
- **Marketing, brand, landing page, sales collateral** → Marketing drive.
- **Customer deals, sales pipeline, contracts** → Sales drive.
- **Cross-team, handbook, onboarding, company-wide policies, All Hands, legal** → Sarj Teamwide drive.
- **Defunct / empty / duplicate / obsolete** → Trash in Drive (not moved). Document the reason.
- **Unclear** → Sarj Teamwide by default, flag for owner review post-migration.

### Classification table

Fill in and get the listed reviewer to sign off before any move happens.

| Top-level folder in hidden drive | Destination drive | Target parent folder (inside destination) | Reviewer | Sign-off |
|---|---|---|---|---|
| _example: "Engineering/Core Engineers"_ | Engineering | `/` (root) | @aalhashemi | ☐ |
| | | | | ☐ |
| | | | | ☐ |
| | | | | ☐ |
| | | | | ☐ |

> **Note:** At time of spike (2026-04-15) the hidden drive contained ~4,000 files across ~1,600 folders with ~24 top-level entries (reported by orphan-reconstruction from a flat query — the real count may be lower since some were nested). Add rows for every actual top-level folder you see when you run the audit in §4.

---

## 4. Pre-migration audit

Before moving anything, produce a snapshot of what's currently in the hidden drive so we can compare against it after.

**Who can do this:** a Workspace admin or anyone with access to the service account credentials. The SA is the only identity that can list the hidden drive's contents.

**Manual approach (preferred — no code committed):**

1. Open `drive.google.com` as a Workspace admin who can see the hidden drive (or use admin → "Shared drive management" to navigate into it).
2. For each top-level folder, note:
   - Folder name
   - Approximate file count (Drive's folder view shows this)
   - Approximate subfolder count
   - Any obvious owner / last-modified signal
3. Paste the result into §3's classification table.

**Alternate — ad-hoc read-only audit via SA (does NOT live in the repo):**

If the manual approach is too tedious, a Workspace admin with local access to `service-account-key.json` can run a one-off Python snippet using the existing `get_sa_drive_service()` helper. Do this in a scratch directory, not in this repo, and do not commit the script. The snippet should:

1. Call `files.list(driveId="0AI_dKpHiz__7Uk9PVA", corpora="drive", q="trashed=false", fields="files(id,name,mimeType,parents)")` paginated, and dump to a local JSON file.
2. Group by `parents` to reconstruct the top-level folder list.
3. Print counts per top-level subtree.

The dump is input to §3's classification. It is **not** committed to the repo.

---

## 5. Execution — moving files

### 5.1 Preferred method: Google Drive UI, folder-by-folder

For each classified top-level folder:

1. Open the hidden drive as a Workspace admin.
2. Right-click the folder → **"Move to"** → pick the destination shared drive → pick the target parent folder.
3. Drive prompts: "Moving this folder will change who has access. Continue?" Review the permission change summary carefully.
4. Confirm the move.
5. **Check access.** Immediately log in as a non-admin `@sarj.ai` user who was previously relying on that content and verify the files are still visible at the new location.
6. Update the classification table: check the sign-off box for that folder, record the date of the move.

**Do not bulk-move everything at once.** Move one top-level folder at a time, wait for each to complete, verify, then move the next. If a move surfaces unexpected permission errors, stop and investigate before proceeding.

### 5.2 Handling permission drift

Some files in the hidden drive may have been shared with specific users or groups via file-level ACLs, not through drive-level membership. When you move such files into a destination drive, they inherit the destination drive's ACL — which means:

- Users/groups who were explicitly granted access to the file may gain access via the destination drive's broader membership.
- Users/groups who had file-level access but are NOT members of the destination drive may **lose** visibility.

**Before each move, flag files that look permission-sensitive:**
- Contracts, HR docs, compensation, incident reports, interview notes.
- Anything previously shared with non-`@sarj.ai` collaborators (external partners).

For flagged files, either:
- Re-share them explicitly to the needed user/group after the move.
- Or route them through Sarj Teamwide with a restricted subfolder ACL.

Document every flagged file in the classification table with a note.

### 5.3 Files with no good destination

If a folder's content is genuinely defunct — old demo recordings, draft PRDs from 2023, one-off experiments — **trash them** instead of moving. Drive's trash preserves files for 30 days; if someone complains, they can be restored with one click.

Document "trashed" rows in the classification table so the verification step doesn't flag them as missing.

---

## 6. Post-migration verification

After every planned move is complete, verify the destination state.

### 6.1 Count verification

For each destination drive, expect the file count to have grown by roughly the sum of moved-in file counts from §3. A variance of ±2% is acceptable (Drive sometimes lags on count updates).

| Drive | Pre-migration file count | Post-migration file count | Expected delta | Actual delta | OK? |
|---|---|---|---|---|---|
| Engineering | | | | | ☐ |
| Marketing | | | | | ☐ |
| Sales | | | | | ☐ |
| Sarj Teamwide | | | | | ☐ |
| Hidden drive (`0AI_dK...`) | | ~0 | | | ☐ |

### 6.2 Spot-check sample

Pick **10 files at random** from the migrated set — mixing Docs, Sheets, Slides, and one PDF. For each, verify:

- The file appears in the destination drive at the expected path.
- A non-admin `@sarj.ai` user who previously had access can still open it.
- The file's title and content are intact (not corrupted by the move).
- Drive's version history still shows pre-move revisions.

Record the spot-check results in a ticket linked from this checklist.

### 6.3 Sarj Wiki smoke test (staging)

Once Sub-PRs A–D are on the feature branch and deployed to staging, log into staging as a real `@sarj.ai` user and confirm:

- `drives.list()` returns 4 drives (not 5 — the hidden drive should no longer appear even to the SA, or if it does, it should be empty).
- The sidebar renders all 4 drives as workspaces.
- Navigating into each drive shows the expected folder tree.
- A handful of migrated files open correctly in the iframe viewer.

This smoke test is part of Sub-PR E staging validation; it's listed here as a reminder that the content migration is a prerequisite for meaningful Sub-PR E testing.

### 6.4 Hidden drive status

After verification:

- **Option A (recommended):** leave the hidden drive empty but un-deleted for 30 days. If something surfaces as missing, files can still be recovered from Drive's trash within the grace period.
- **Option B:** ask a Workspace admin to delete the hidden drive immediately after the 30-day grace period elapses.

Either way, remove the service account's membership from it as soon as Sarj Wiki is live in prod — the SA should have no business touching Drive after cutover.

---

## 7. Rollback guidance

The migration is **non-destructive per-file** — every move just changes parent folders, no content is deleted unless explicitly trashed in §5.3. Rolling back a bad move is:

1. **Find the misplaced file in the destination drive.**
2. **Right-click → "Move to"** → navigate back to the hidden drive and re-parent it.
3. Drive version history preserves everything; nothing is lost.

For bulk errors (whole-folder misclassification), move the folder back the same way you moved it in. Do this **before** cutover; after cutover, Sarj Wiki is authoritative and rollback pathways get more complicated.

There is no automated rollback script because there are no automated moves in the first place.

---

## 8. Sign-off

The migration is considered **complete** when all of the following are checked:

- [ ] §3 classification table fully populated and reviewed
- [ ] §4 pre-migration audit snapshot captured and stored somewhere retrievable (Drive folder, ticket, email — anywhere but this repo)
- [ ] §5 every planned move executed, one folder at a time, with per-folder sign-off
- [ ] §6.1 file counts verified and within ±2%
- [ ] §6.2 spot-check of 10 random files passed
- [ ] §6.3 staging smoke test passed (coordinated with Sub-PR E)
- [ ] §6.4 hidden drive left empty; plan for its eventual deletion recorded

Once all boxes are checked, the gate on Sub-PR F (cutover) is cleared and the cutover PR can be marked ready for merge.
