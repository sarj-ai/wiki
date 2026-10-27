# Sarj Wiki

Sarj Wiki is an internal knowledge base that presents Google Drive and Google Docs
through a focused workspace interface for Sarj.

> This repository and the running service are currently internal-only.

## Requirements

- [mise](https://mise.jdx.dev/)
- Node.js (pinned in `.mise.toml`)
- npm (pinned in `frontend/package.json`)
- Python 3.14+
- [uv](https://docs.astral.sh/uv/)
- Google Cloud CLI access to the `sarj-docs` project for local secrets

## Setup

```sh
mise install
make setup
gcloud secrets versions access latest --secret=env-backend-local --project=sarj-docs > backend/.env
```

Initialize the database:

```sh
cd backend
uv run alembic upgrade head
```

## Development

Run the backend and frontend in separate terminals:

```sh
cd backend && uv run uvicorn app.main:app --reload
cd frontend && npm run dev
```

## Verification

```sh
make check
make audit
make standards-check
```

`make check` mirrors CI. `make audit` performs network-backed dependency audits, and
`make standards-check` verifies generated standards configuration, version pins, and code.

Python baseline verification is serial by default. CI sets
`WIKI_PYTHON_BASELINE_JOBS=2` to use the native checker's bounded worker budget.

See the [439-pass performance record and reproduction commands](docs/performance.json) for
frontend loading and backend latency work.

Original-tranche production-build measurements against the same locked main toolchain:

| Measurement                         | Before    | After     |
| ----------------------------------- | --------- | --------- |
| Initial app JavaScript, raw         | 676,364 B | 545,058 B |
| Initial app JavaScript, gzip        | 216,381 B | 177,713 B |
| Gzip CPU for a 10,000-node tree     | 25.034 ms | 7.796 ms  |
| Sidebar extras database round trips | 3         | 1         |

Client chunks exclude shared framework assets and deferred dialogs in both builds.
The compression benchmark compares levels 9 and 4 on the same fixture; compressed
size grows 3.9%. These measurements isolate application costs, not overall live
page-load time. Backend responses expose request timing and byte metrics for
production follow-up. PostgreSQL CI covers connection release, task projections,
bounded result reads, and large encrypted cache batches.

The follow-up work adds 95 new passes, including 88 across five agents. Against
the deployed `80b6e095` baseline, controlled measurements include:

| Measurement                                     | Before    | After     |
| ----------------------------------------------- | --------- | --------- |
| Document app JavaScript, gzip                   | 191,766 B | 190,761 B |
| SQL construction/write, 1,000 cache entries     | 31.586 ms | 10.812 ms |
| Cache envelope decrypt, 10,000-item fixture     | 9.694 ms  | 4.276 ms  |
| Document-icon observers, 20 drives              | 20        | 1         |
| Geometry reads, 100 resize events               | 100       | 1         |
| Provider reads, 200 parents sharing an ancestor | 400       | 201       |

Small cache batches retain the faster `VALUES`/`IN` paths. New indexes cover legacy
cache cleanup, task expiry, and pending dispatch. Fresh permissions, encryption,
key rotation, idempotency, and task admission locks remain enforced. Final homepage
gzip inventory grows 0.9% (177,697 to 179,354 B), including guards against retired
sessions dispatching queued mutations, refreshing credentials, publishing speculative
iframes, or repopulating cleared state. Credential refresh uses an independent
transaction so it cannot release the caller's admission lock or commit pending
changes. Resource and CPU reductions are measured separately from transfer size and
production page-load time. Rejected regressions and remaining tradeoffs are recorded;
these passes do not establish a physical performance maximum.

The previous tranche added 30 distinct candidates across five agents: 26 retained
improvements and four measured rejections, bringing the record to 229 passes.
Against the deployed `1f7fa858` baseline, controlled measurements include:

| Measurement                                      | Before    | After     |
| ------------------------------------------------ | --------- | --------- |
| Provider-refresh SQL statements                  | 4         | 3         |
| Live-lease poll updates, 31 polls                | 31        | 0         |
| Metadata batch fixture, 701 IDs / 5 ms per batch | 53.418 ms | 15.686 ms |
| Search-snippet reads while typing                | 14        | 2         |
| Collapsed tree-index entry reads, 23,000 entries | 23,000    | 0         |
| Expanded-folder subscriptions, 500 rows          | 1,000     | 500       |

That integrated gate passed 4,251 backend tests and 803 frontend tests. Browser
recovery restores verified user preferences after reload, obsolete polling stops
at session retirement, and error-body reads have size and time bounds. Final
compressed client inventories increase about 0.6%: home 179,354 to 180,375 B and
document 190,761 to 191,806 B. Read, resource and controlled timing reductions
are separate from this bundle tradeoff and do not establish production page-load
latency. The record includes candidate-specific methods and rejected regressions.

The following tranche adds another 30 distinct candidates across five agents: 25
retained improvements and five measured rejections, bringing the record to 259.
Against the deployed `edfc6588` baseline, controlled measurements include:

| Measurement                                    | Before    | After     |
| ---------------------------------------------- | --------- | --------- |
| Complete search requests, eight shared drives  | 10        | 2         |
| Collapsed-folder query observers, 500 rows     | 500       | 0         |
| Warm tree read, 10,000 nodes                   | 4.187 ms  | 1.123 ms  |
| Encrypted cache hit, 4 MiB plaintext           | 14.320 ms | 10.476 ms |
| Sidebar overlay read, 20,001 rows              | 68.641 ms | 31.847 ms |
| Longest event-loop gap, six 4 MiB cache writes | 58.183 ms | 20.808 ms |
| Sibling ID reads, 100 drag events / 10,000 IDs | 1,000,000 | 0         |

Complete all-drive search avoids unnecessary fallback requests. Partial pages,
incomplete searches and excessive per-drive results retain the fresh per-drive
queries; My Drive still uses its separate owner-filtered query. Warm trees retain
a fresh authenticated PostgreSQL tuple-version read before omitting ciphertext.
Duplicate move clicks admit one write, retired MCP reads stop, and success-body
decoding has byte and deadline bounds. Small cache and tree fixtures have explicit
cost increases of about 0.03–0.07 ms; cold tree timings are essentially unchanged.
The record preserves sampling methods, rejected regressions and workload limits.
The integrated gate passes 4,299 backend tests and 870 frontend tests. Final
compressed app inventories grow about 0.4%: home 180,375 to 181,032 B and document
191,806 to 192,517 B. Resource and CPU gains are separate from this transfer-size
tradeoff and do not establish production page-load latency.

The October 11 follow-up evaluates [50 more candidates across five agents](docs/performance/round5-20261011.json),
bringing the record to 309: 31 retained improvements and 19 measured rejections.
Controlled measurements against `040541f1` include:

| Measurement                                       | Before    | After     |
| ------------------------------------------------- | --------- | --------- |
| Cold tree read, 10,000 nodes                      | 37.796 ms | 32.722 ms |
| Sidebar overlay conversion, 20,001 rows           | 32.986 ms | 22.048 ms |
| Bounded text preparation, wordy two-MiB fixture   | 4.602 ms  | 0.145 ms  |
| Folder refetches after one drive refresh          | 11        | 3         |
| Move-selection folder-name reads, 1,000 rows      | 2,000     | 4         |
| Shared session-refresh abort listeners, 100 reads | 100       | 1         |

The homepage now shows up to five recent accessible pages, with links and workspace
names. It reuses the existing per-user browser history and checks current file
metadata and permissions in one request for at most six IDs; an empty history makes
no request. The Sarj logo returns to this landing view. Account changes and retired
reads cannot republish old results. History is local to the browser.

These gains measure isolated work and resources. Small inputs and large icon
workers have documented cost tradeoffs; the move picker adds local row subscriptions
and its variable cold-mount timings do not establish faster browser mounting. The
record preserves rejected candidates and reports the final client-bundle inventory
separately from production page-load latency.

The final local CI mirror passes 4,362 backend tests (88.99% coverage), 906 frontend
tests and 11 infrastructure tests. The new landing UI increases the compressed app
inventory by 6,492 B on home (181,032 to 187,524 B, 3.59%) and 6,497 B on the
document route (192,517 to 199,014 B, 3.37%). These inventories exclude framework
and deferred chunks; they do not establish live load time.

Production qualification also found configured HTML headers overriding private
API response headers. An API-specific header preserves private/no-store caching,
and redacted read failures retain request IDs. This repair adds zero passes; its
complete frontend gate passes 911 tests.

The next October 11 tranche evaluates [50 further candidates across five agents](docs/performance/round6-20261011.json),
bringing the record to 359: 26 retained improvements and 24 evidenced rejections.
Each agent measures ten distinct bulk-query, caching, optimistic UI, preloading or
advanced candidates. Controlled results against `fdc479be` include:

| Measurement                                          | Before    | After    |
| ---------------------------------------------------- | --------- | -------- |
| Fresh recent-alias read, 50 inputs, local PostgreSQL | 14.269 ms | 1.116 ms |
| Reconciliation reads after eight overlapping edits   | 8         | 1        |
| Same-drive cached move, 5,000 nodes                  | 3.667 ms  | 1.887 ms |
| Retire 1,000 pending move display leases             | 4.102 ms  | 0.115 ms |
| Viewer selection, 100,000 bounded derivations        | 19.740 ms | 2.025 ms |
| Smart Create prompt, 80 folders and 80 templates     | 0.242 ms  | 0.088 ms |
| Depth-limited candidate assembly, 2,048 folders      | 4.627 ms  | 1.737 ms |
| Encrypted in-place checkpoint, 1,500 requests        | 4.019 ms  | 3.667 ms |

Recent aliases use fresh, smaller provider metadata and one array-bound SQL read.
The large SQL gain depends on PostgreSQL planning and statistics; custom-plan and
small-input regressions remain documented. Large metadata jobs retain four worker
owners, and immutable SQL construction, bounded HMAC seeds and small-tree decoding
reduce cache preparation without extending authoritative result lifetimes.

Pending preferences appear immediately with reversible ownership-aware patches.
Overlapping edits reconcile after their last pending write, and confirmed renames
update existing caches before the next read. Failure, fresh reads and session
retirement cannot roll back a newer publication. Viewer preloading retains
foreground retries, reduced-motion paints, network/device limits and iframe caps.
Candidate assembly preserves cycles, missing parents and depth ordering; task
checkpoints retain encryption, UTF-8 limits, lease/version checks and canonical
attestation hashes.

The unchanged local CI mirror passes 4,403 backend tests (89.20% coverage), 946
frontend tests, 323 repository tests and 11 infrastructure tests. Type coverage is
99.71%. Compressed app inventory grows 2,755 B on home (187,524 to 190,279 B,
1.47%) and 2,754 B on the document route (199,014 to 201,768 B, 1.38%). These
inventories exclude framework and deferred chunks. The measurements isolate local
work, resources and bytes; they do not establish live load time or an absolute
optimization maximum. All five scope records include rejected prototypes, controls,
variance, reproduction commands and final source hashes.

The following October 11 tranche evaluates [50 additional candidates across five agents](docs/performance/round7-20261011.json),
bringing the record to 409: 27 retained improvements and 23 evidenced rejections.
Each agent measures ten distinct backend-read, provider, browser-transport,
rendering or interaction candidates. Controlled results against `3925f073` include:

| Measurement | Before | After |
| ----------- | ------ | ----- |
| Fresh active-alias read, local PostgreSQL | 0.339 ms | 0.270 ms |
| Tree cache with 32 large descriptions, retained bytes | 67,442,084 B | 23,338,568 B |
| Large destination top80 construction, peak traced bytes | 6,084,320 B | 315,918 B |
| Large Smart Create failure body, bytes pulled | 1,048,589 B | 40,960 B |
| Mounted document-viewer data subscriptions | 5 | 1 |
| Idle mutation observers after using 40 item menus | 120 | 0 |

Shortcut metadata aligns unique targets before expanding duplicate rows, without
changing authorization probes. Large validated destination catalogs stream into
the existing top80 heap; small catalogs retain their prior construction path.
Unshared read leaders avoid allocating a follower Future, and bootstrap failures
cancel and drain request-owned companions while shared Drive discovery survives.

Drag feedback delivers notifications to affected row, folder and container IDs.
The 1,000-file, 128-folder, eight-drive fixture reduces native source subscriptions
from 1,264 to one; keyed JavaScript listeners remain. Smaller controls include
extra dispatch overhead. Used menus retire observers
after their dialogs and admitted writes settle; dismissal focus and retry remain
qualified. Recent-page retries retain the shell while issuing fresh reads, and the
landing page continues to show up to five permission-checked recent pages.

Failed metadata reads cannot authorize an iframe from retained cache data.
Successful fresh metadata remains reusable, and activating a prewarmed target
consumes its stale owner atomically. Singleton continuation work is faster under
the same final authorization policy; the integrated authorization guard costs
6–11% in tiny local probes against the older canonical path. Valid alias typing is
faster, while invalid-only input is about 24% slower. Error readers keep their
existing bounds, unused upload bodies are disposed, and successful mutation
responses retain their larger body contract.

The serialized-payload tree budget reduces retained memory but slows an evicted
tree reread from 4.287 to 6.230 ms. It is not an exact mutable decoded-heap limit.
The natural-input sort prototype is rejected for regressions. The expansion
subscription prototype is rejected after compiler and reset controls exposed
lifecycle costs: its final lease variant leaves total retained owners flat and
adds reset risk for only a tiny realistic-workload CPU gain. All five scope
records retain rejected prototypes, controls, variance, reproduction commands and
final source hashes. Integration typing, hook and fixture repairs count zero;
the first failed full gate remains recorded, with no baseline or suppression edits.

The unchanged local CI mirror passes 4,443 backend tests
(89.29% coverage), 992 frontend tests,
323 repository tests and 11 infrastructure tests.
Frontend type coverage is 99.72%.
Compressed app inventory changes by +1,599 B on home
(190,279 to 191,878 B, +0.84%) and
+1,599 B on the document route
(201,768 to 203,367 B, +0.79%).
These inventories exclude framework and deferred chunks. Controlled measurements
do not establish production load time or an absolute optimization maximum.

The October 12 (UTC) final follow-up evaluates [ten more candidates across two agents](docs/performance/round8-20261012.json),
bringing the preserved record from 409 to 419: 8 retained improvements
and 2 evidenced rejections. Each agent measures five new mechanisms
against deployed `5343c4e8`; repeated probes, fixture repairs, review and release
administration count zero. Representative controlled results are:

| Measurement | Before | After |
| ----------- | ------ | ----- |
| Native OAuth authorize, 64 headers (median µs) | 99.3277 | 90.4752 |
| Normal native metadata response (median µs) | 10.5254 | 7.27906 |
| Native cookie origin middleware, two auth cookies (median µs) | 6.1787 | 5.73698 |
| Viewer-action subscriptions retained by1,000 intent links (native subscriptions) | 1,000 | 0 |
| Cold destination folder index,10,000 nodes (median µs) | 153.884 | 116.638 |
| Storage writes for100 same-page recordings (native storage writes) | 100 | 1 |

Both scope records include native entrypoint controls, paired measurements,
small-input costs, rejected prototypes, reproduction commands and exact source
and artifact hashes. Request handling retains authentication, request bounds,
cookie parsing, privacy headers and native OAuth behavior. Frontend controls
cover compiler output, session retirement, denied authorization, mutable inputs,
storage failures and independent response bodies. Cached native metadata adds
673 retained bytes per normal two-scope route composition and 5,393 bytes for
256 scopes; one-time composition adds 3.20 and 7.99 microseconds respectively.
The native model remains alongside its serialized bytes. The recent-page landing
continues to show up to five permission-checked pages.

The unchanged local CI mirror passes 4,461 backend tests
(89.32% coverage), 997 frontend tests,
323 repository tests and 11 infrastructure tests,
plus the production build. Frontend type coverage is 99.72%.
Compressed app inventory changes by -148 B on home
(191,740 to 191,592 B, -0.08%) and
-127 B on the document route
(203,231 to 203,104 B, -0.06%).
These inventories exclude shared framework and deferred chunks. The regenerable
task-owned webpack cache was cleared after baseline capture for disk headroom;
source snapshots, original build chunks and raw evidence were retained. Local
CPU and resource reductions do not establish live load time or an absolute
optimization maximum. Small-input regressions remain explicit in the records.

The next October 12 (UTC) follow-up evaluates [ten new candidates across two agents](docs/performance/round9-20261012.json),
preserving the previous 419 entries and bringing the record to 429:
4 retained improvements and 6 evidenced rejections.
Each agent evaluates five mechanisms against deployed `a2aedf97` and audits
earlier candidate text. Repeated probes, repairs, reviews, tests and release
administration count zero. Representative controlled results are:

| Measurement | Before | After |
| ----------- | ------ | ----- |
| Native-state update with 10,000 live states (median ms) | 7.27844 | 4.11203 |
| Exact-class authorization policy with 16 callbacks (median µs) | 102.029 | 59.3246 |
| Record a recent alias with 50 stored visits (median µs) | 24.8574 | 16.2574 |
| Native Promise initialization events for 4 MiB / 512 chunks (events per completed response) | 3,088 | 2,062 |

Both scope records retain complete native entrypoint controls, paired samples,
small and negative workloads, explicit costs, reproduction commands and exact
source and artifact hashes. Authentication, quota boundaries, request ownership,
storage validation and recovery behavior remain qualified. Recent-page navigation
continues to show up to five permission-checked pages from native account history.

The unchanged local CI mirror passes 4,467 backend tests
(89.32% coverage), 1,002 frontend tests,
323 repository tests and 11 infrastructure tests,
plus the production build. Frontend type coverage is 99.72%.
Home and document app inventories change by +4 and
+93 raw bytes; gzip changes are +7 and
+44 bytes (+0.00% and +0.02%).
The previous qualified build and task-owned webpack cache were retained.
These inventories exclude shared framework and deferred chunks; local CPU,
resource and query reductions do not establish live page-load latency or an
absolute optimization ceiling. Every earlier ledger entry remains unchanged.

The drive-loading regression was reproduced in the compiled Cloudflare Worker:
server `fetch` rejects `redirect: "error"` before reaching the API. Server clients
now inspect manual redirects and reject them without following or exposing their
destination. Deployment verification checks both the exact health revision and
the real authentication/drive proxies with an invalid synthetic session, requiring
their expected redacted `401` responses.

Round 10 evaluates ten new candidates across the [backend](docs/performance/round10-20261012-backend.json) and [frontend](docs/performance/round10-20261012-frontend.json): four accepted improvements and six evidenced rejections. These ten IDs extend the preserved ledger from 429 to 439. The [30 recorded UX scenarios](docs/performance/round10-20261012-ux.json) received ten Agy and twenty Codex reviews and led to twenty distinct UX repairs; those repairs, repeated checks and release administration add zero performance candidates.

The accepted recent-page cache removed all repeat reads and loading frames in 30 same-session warm returns for both one and six history IDs. In native React/QueryClient controls with a 20 ms loopback response, median ready time changed from 21.957042 to 0.457083 ms for one ID and from 22.187583 to 0.925667 ms for six (25 measured pairs plus five warmups). Healthy home-link intent moved one authority read before navigation, leaving zero navigation-time reads and no initial loading frame when that read had completed; the existing 150 ms speculation delay and hidden-page, Save-Data, touch-hover, early-leave and retired-owner limits remain; direct foreground touch still performs the needed read. Freshness is 30 seconds, GC is five minutes, and 50 distinct unobserved queries retained at most eight recent batches (737 bytes of metadata), with zero batches after owner unmount.

Backend conditional reads of a fresh valid cached sidebar tree now use one PostgreSQL SELECT instead of two when the ETag changes. Matching 304 and ordinary cached reads retain one SELECT, and the native 304 controls return no ciphertext. Compatible serial Drive/Files requests also build one SDK root instead of two, retaining the same provider reads: five for the serial HTML fixture and eight for six recent files. Final local operation medians were 2.582000→2.065563 ms for the small changed-ETag route, 5.812677→4.654912 ms for serial HTML with a cold SDK, and 7.795370→6.835500 ms for six recent files on one worker. The warm recent control changed 2.651358→2.657660 ms and the two-worker control still required two SDK roots and changed 7.808474→7.785677 ms; the records preserve these limits and earlier noisy or negative measurements.

The frontend benefit requires fresh authority in the same verified live session. A new session or cold reload still validates access; stale or changed history and known denials, invalidations or mutations can require another read. Cold ready medians for zero, one and six IDs changed 1.575375→3.057208, 21.941625→23.548417 and 21.947375→23.930333 ms, with unchanged request and loading-frame counts. Native diagnostic initialization changed 102.480208→103.767000 ms; retained heap rose 43,072 bytes (19,627,704→19,670,776), and diagnostic-entry gzip grew 4,471 bytes (66,112→70,583). These controlled timings and diagnostic bytes do not establish production page-load latency or final Next network bundle size. The unchanged complete local CI mirror passes 4,477 backend tests (89.30% coverage), 1,140 frontend tests in 130 files (99.73% type coverage), 323 repository tests, 13 Node security tests, 11 infrastructure tests, and the production build. Home/document app inventories grow by 19,113/19,627 raw bytes and 7,449/8,101 gzip bytes (3.8878%/3.9877%). Shared framework assets and deferred chunks are excluded. Baseline build assets, source snapshots and raw evidence were preserved; this task clone’s regenerable webpack cache was cleared for disk headroom and regenerated by the final build.

## Saudi production topology

Production is pinned to Google Cloud `me-central2` (Dammam): Cloud Run, Cloud SQL, Cloud Tasks,
Artifact Registry, the VPC connector, and deployment jobs all live in Saudi Arabia. The global
load balancer routes only to `sarj-docs-backend-dammam`; GitHub repository variables must keep the
backend release workflow pointed at the matching Dammam resources.

Cloud SQL has point-in-time recovery and 14 retained regional backups. Independent logical exports
belong in the versioned, soft-delete-protected `sarj-docs-db-exports-dammam` bucket. Before a risky
database operation, create both an on-demand backup and a timestamped export, verify each completed,
and verify `/health` reports both `status=ok` and `database=ok`.

Do not recreate the retired Doha runtime or route traffic outside Dammam. A runtime rollback uses a
previous immutable Dammam image revision; a data rollback uses Dammam PITR or the protected Saudi
export. Database migrations must remain backward compatible because the deployment workflow runs
them before switching Cloud Run traffic.

Backend verification-only recovery requires both the completed deploy workflow run ID and its
exact run-attempt number. Copy both values from the same GitHub Actions attempt; never reuse an
older attempt after that run has been rerun. Leaving both inputs empty performs a normal deployment,
while supplying only one fails closed without running deployment mutations. Recovery validates the
source attempt's exact Standards, backend, frontend, IaC, and quality job receipts before obtaining
cloud credentials, so it does not rerun suites already proven for the same immutable main commit.

Main pushes use `Release Main` to run backend, frontend, quality, IaC, and the exact Standards
receipt once before the selected deployments. Component routing compares against the last
successful whole main release, so superseded pushes remain included. Missing or unusable history
selects both deployments. The first release after this workflow is introduced therefore deploys
both components. Each deployment retains its fresh-main fences and release-specific verification;
the existing manual deploy and backend verification-only inputs remain available.

Coding agents should batch related commits locally and push complete review or verification
checkpoints, rather than each edit. Keep incomplete work in a draft PR, use the normal hooks,
and mark it ready when the change is reviewable. Merge only after all required checks succeed
for the exact revision being merged.

## Document Transform and Markdown Import

Document Transform creates a branded Google Doc or Google Slides sibling from an existing Drive
file. Import Markdown accepts pasted Markdown or a local UTF-8 `.md` file and creates a native
Google Doc in the selected folder. Copy mode is the default and never edits the source. Native Google Docs may
also be amended in place when the independently gated server capability is enabled; Drive version
history remains available. Native Slides and PowerPoint are copy-only. Every run explicitly selects
one of three modes:

- `format` applies the deterministic Sarj document system without rewriting prose.
- `unslop` removes only confirmed AI-writing defects and otherwise returns an exact no-op.
- `both` runs the conservative unslop pass before applying the Sarj document system.

Submit `POST /document-transforms` with an `Idempotency-Key` UUID header, then poll
`GET /tasks/{task_id}` until the asynchronous task reaches a terminal state. Inputs may be a Drive
file with an expected modification time or pasted Markdown with a destination folder ID. The
authenticated API infers the source type from current server-side Drive metadata rather than
trusting a client-supplied type.

For a local `.md` file, read it on the client and submit its contents; the remote API cannot
open a path on your computer. With `jq`, a Wiki access token, and a writable Drive folder ID:

```sh
key="$(uuidgen)"
jq -n --rawfile content report.md --arg folder "$DESTINATION_FOLDER_ID" \
  '{source: {kind: "markdown", content: $content, name: "Report", destination_folder_id: $folder}, mode: "both"}' \
  | curl --fail-with-body --silent --show-error --include \
      --header "Authorization: Bearer $WIKI_ACCESS_TOKEN" \
      --header "Idempotency-Key: $key" \
      --header 'Content-Type: application/json' \
      --data-binary @- https://api-wiki.sarj.ai/document-transforms
```

The API returns `202 Accepted` quickly with a `Location: /tasks/{task_id}` header. Poll that
task for the Google Doc output. Keep `key` and the exact request for safe retries; after a
timeout, check `GET /document-transforms/admissions/{key}` first.

The copy-on-write pipeline applies only fixed, deterministic deletion recipes. Remaining contextual
candidates are preserved and surfaced as review findings; models are reserved for offline QA judging
until a deterministic grammar and factual-equivalence gate can prove their edits safe. The pipeline
verifies source block hashes, protects facts and syntax, and applies Google Docs mutations against a
required revision. The high-signal detector pack is
derived from `theclaymethod/unslop` version 2.3.0 at commit
`d81f5196167ded24f46fced04958c0c12d681798`; scanner hits are only candidates, and accepted edits
cannot alter names, numbers, dates, citations, links, code, units, scope, negation, or force-bearing
language.

Inputs are bounded to exactly 512 KiB of UTF-8 Markdown bytes and 500,000 UTF-16 text units. Google Docs and
Markdown allow at most 750 blocks and 50 Google Docs tabs; native Slides allow at most 2,000
editable text shapes, 10,000 total elements, and 200 slides. Mutation plans are also bounded by
request count and serialized bytes. Drive
copies preserve unsupported rich structures. Markdown images, raw HTML, footnotes,
strikethrough, nested quotations, and tilde code fences are rejected because the native converter
cannot prove preservation; pasted Markdown is rejected before task creation, and Drive-hosted
Markdown is rejected before output creation. Every Markdown mode first renders native headings,
lists, tables, links, emphasis, quotations, code, and dividers into DOCX. Unslop-only uses a neutral
unbranded theme, while `format` and `both` use the pinned Sarj theme. The converted Google Doc is
re-read and checked against a normalized semantic import manifest; content, structure, list
restarts, and branding mismatches become explicit review findings. Native Google Doc outputs are
re-read after mutation and checked
against the validated text plus a deterministic structural inventory before success is recorded.
Completed outputs receive an idempotent Wiki short link only after both content and structure
preservation pass. Non-destructive review findings remain visible and do not prevent a link;
the link does not approve those findings or change Drive access. Failed preservation checks
never create an automatic link. A link-service outage does not replay document mutations.
Native Slides use the same bounded text planner, but only plain single-run text shapes are editable.
Bullets, links, mixed text runs, speaker notes, tables, charts, images, video, groups, masters,
layouts, geometry, and slide order are protected by a separate topology digest.

Native Slides are copied within the same folder. Drive-hosted `.pptx` files are size-bounded,
validated as safe Office ZIP packages, checked against Drive's current import formats, and imported
as a new native Google Slides sibling. The original deck and all permissions remain untouched.
Outputs report `access_scope=inherited` because a sibling inherits its parent folder's access; the
service never changes permissions or calls an inherited copy private. Slides in-place and
Slides-to-PowerPoint export remain disabled until their revision and round-trip guarantees pass
dedicated production canaries.

The production QA policy uses Vertex AI in `global` with pinned Gemini models as visual and semantic
judges. Their verdicts can fail a canary but cannot mutate user prose. Visual restyling and accepted
text edits are deterministic, so clean prose is not rewritten without a factual-equivalence proof.

The brand layer uses the immutable, hashed `sarj-2026-v4` manifest distilled from the canonical Sarj
design skill. It applies Nunito typography, Sarj heading sizes, the current `#392868` purple,
left-aligned body text, margins, and consistent paragraph rhythm. Existing Docs headers, logos,
footers, tabs, page numbers, and rich objects are preserved. Markdown imports receive a native DOCX
header logo and footer page field before Google Docs conversion. That DOCX is an intermediate Google
import artifact: it declares the manifest's Nunito Latin font and Arial complex-script fallback but
does not embed font binaries, so exact local PDF typography is not a supported portability claim. The
canonical logo is served from
`/brand/sarj-logo-light.png`; if unavailable, the task returns a `logo_unavailable` review finding
instead of fabricating a placeholder or failing the formatting batch.

For presentations, the same manifest applies Nunito, role-specific sizes, and Sarj colors only to
plain standard placeholders whose effective style, safe font-size direction, disabled autofit, and
light-background contrast can all be proven. Native level-zero placeholder inheritance and theme
colors are resolved deterministically; custom, ambiguous, dark, or fit-sensitive text is preserved
with a review finding. A non-Nunito effective font family is preserved rather than substituted.
Master and layout identity, mixed text runs, charts and their data colors, imagery, object geometry,
and slide order remain unchanged. Resolved, single-style BODY or OBJECT narrative text with at least
80 normalized characters is flagged for visual review below the conservative 12 pt floor; that
advisory never changes its font size. The pinned black logo is placed only in a collision-free corner
on a proven light background; inherited placeholder templates do not count as rendered content.
Formatting never asks a model to generate layout or Slides API operations.

Recovered Codex and Claude requests for headings, lists, checklists, tables, links, quotations, code
blocks, dividers, centered logo, hierarchy, spacing, bold table headers, and visual QA are covered.
Left alignment intentionally supersedes the earlier justified-body request after a 75-page PDF
showed severe spacing rivers. Template-specific generated tabs, linked tab indexes, per-tab headers,
covers, date chips, Mermaid images, captions, and native footnotes are not silently approximated;
they require a future explicit template.

Document transformations run on a dedicated Cloud Tasks queue and OIDC-authenticated internal
worker. Task results expose the pipeline version, actual model-use flag, deterministic/model patch
counts, brand profile ID and digest, UNSLOP catalog digest/source version, validation summary,
findings, and output metadata.

For a local `.md` file, `POST /document-transforms/uploads` returns a ten-minute GCS signed PUT URL,
an `upload_id`, and the exact headers to send. PUT the UTF-8 file bytes directly to that URL, then
`POST /document-transforms` with `source: {"kind":"uploaded_markdown","upload_id":"…",
"name":"…","destination_folder_id":"…"}` and an `Idempotency-Key` UUID. The browser import
dialog performs these steps automatically. The API accepts the upload reference only from its owner,
checks the 512 KiB limit and Markdown safety before admitting a task, and fetches that private object
for conversion. Pasted Markdown remains available with `kind: "markdown"` and inline `content`.

Release QA uses `backend/scripts/qa_document_transform_corpus.py`. Planning is read-only and writes
a seeded manifest plus a resumable JSONL ledger. The source may be a Shared Drive tree, but every
native Google Doc must remain readable and copyable in the same bound Drive. Outputs may be created
only in a separately supplied, owner-only, unshared My Drive folder. The runner verifies that
destination before every submission and fails if a source, permission inventory, destination, or
output parent changes. Prior transform outputs are excluded. Use the full production release SHA
shown by `/health` and keep the canary and corpus in separate private artifact directories.

First plan the one-document canary, without creating a Drive file:

```bash
cd backend
uv run python scripts/qa_document_transform_corpus.py \
  --seed canary-2026-09-04 \
  --expected-release-sha 0123456789abcdef0123456789abcdef01234567 \
  --manifest /private/tmp/wiki-doc-transform-qa/canary/manifest.json \
  --ledger /private/tmp/wiki-doc-transform-qa/canary/events.jsonl \
  --source-folder-id CANARY_SOURCE_FOLDER_ID \
  --destination-folder-id OWNER_ONLY_QA_DESTINATION_FOLDER_ID \
  --judge-model gemini-3.8-flash \
  --count 1 \
  --canary
```

Review the canary manifest, then execute that exact plan. Pin the installed `pdftoppm` version and a
versioned Vertex model ID; mutable `latest` aliases are rejected:

```bash
uv run python scripts/qa_document_transform_corpus.py \
  --seed canary-2026-09-04 \
  --expected-release-sha 0123456789abcdef0123456789abcdef01234567 \
  --manifest /private/tmp/wiki-doc-transform-qa/canary/manifest.json \
  --ledger /private/tmp/wiki-doc-transform-qa/canary/events.jsonl \
  --source-folder-id CANARY_SOURCE_FOLDER_ID \
  --destination-folder-id OWNER_ONLY_QA_DESTINATION_FOLDER_ID \
  --artifacts-dir /private/tmp/wiki-doc-transform-qa/canary/artifacts \
  --pdftoppm-version 25.06.0 \
  --judge-model gemini-3.8-flash \
  --count 1 \
  --canary \
  --execute \
  --confirm-live-copy CREATE_1_PRIVATE_CANARY
```

Only after the canary is terminal, structurally verified, rendered on every page, and visually
accepted should you plan the full source-folder corpus. Pass the verified canary manifest, ledger,
and artifact directory to bind the corpus to that exact release and exclude the canary source:

```bash
uv run python scripts/qa_document_transform_corpus.py \
  --seed corpus-2026-09-04 \
  --expected-release-sha 0123456789abcdef0123456789abcdef01234567 \
  --manifest /private/tmp/wiki-doc-transform-qa/corpus/manifest.json \
  --ledger /private/tmp/wiki-doc-transform-qa/corpus/events.jsonl \
  --source-folder-id SECURITY_SHARED_DRIVE_FOLDER_ID \
  --destination-folder-id OWNER_ONLY_QA_DESTINATION_FOLDER_ID \
  --canary-manifest /private/tmp/wiki-doc-transform-qa/canary/manifest.json \
  --canary-ledger /private/tmp/wiki-doc-transform-qa/canary/events.jsonl \
  --canary-artifacts-dir /private/tmp/wiki-doc-transform-qa/canary/artifacts \
  --judge-model gemini-3.8-flash
```

Review every enumerated source, then repeat that command with a private corpus `--artifacts-dir`,
the same pinned renderer and judge, plus
`--execute --confirm-live-copy CREATE_ALL_OWNER_ONLY_DESTINATION_COPIES`. Execution reads the
application bearer token only from `WIKI_QA_ACCESS_TOKEN`. UUIDv5 keys include the destination and
provide idempotent replay; the JSONL ledger resumes only after revalidating its release, complete
source set, source/output binding, permissions, destination, and artifact hashes. The runner stops
at the first failed invariant, retains successful labeled outputs in the bound QA destination for
inspection, renders every eligible page, and never edits a source or calls a Drive permission
mutation.

### Default-off Cloud Run corpus runner

Terraform contains an opt-in `sarj-wiki-document-transform-qa` Cloud Run Job for authenticated
corpus QA. It is absent unless `enable_document_transform_qa_job=true`; enabling it also requires
an immutable image from the exact `sarj-docs-dammam/document-transform-qa` repository, its release
SHA, and an explicit lowercase `@sarj.ai` operator allowlist. The backend service account is the
only job launcher and viewer; operators never receive Cloud Run IAM or runtime-override rights.
The job has its own service account, zero retries, and a dedicated public-access-prevented evidence
bucket mounted with UID 1001 and
`0600`/`0700` modes. It has no Cloud SQL identity, database grants, Secret Manager access, production
signing/encryption material, or Google OAuth client secret. Its only project roles are the dedicated
bucket's `storage.objectUser` role and `aiplatform.user` for optional visual judging. No Scheduler or
CI trigger is attached.

An allowlisted, logged-in operator calls the same-site
`POST /document-transforms/qa/launch` endpoint with a UUIDv7 `Idempotency-Key`. The backend owns the
launch, accepts no job or execution ID from the operator, runs the statically configured job with an
empty request body, and polls Cloud Run for its immutable execution identity. It binds the exact
execution name, UID, job, image digest, and release SHA to a single-active-run ledger before the
worker can receive a capability. A claimed, failed, bound, or bootstrapped run blocks a different-key
launch only while its bound grant or job authority remains live; expiry cleanup, or a future
explicitly reviewed reconciliation mechanism, releases that boundary.

When the backend-owned launcher is configured, the legacy
`POST /document-transforms/qa/grants` and credential-consume exchange flow is disabled. The exchange
endpoint accepts only the exact `?operation=renew` selector for a workload-authenticated launcher
renewal, and only after the capability is bound to the bootstrapped execution and its persisted
submission task; absent, duplicate, or alternate selectors fail before request-body parsing or OIDC.
The complete legacy exchange remains available only for installations that have not configured the
launcher, so an older deployment can be retired deliberately rather than silently changing
behavior. Migration
`0020_qa_attestation` revokes grants associated with pre-attestation 0019 runs and preserves those
runs as audit-only `retired` rows; their missing Cloud Run UID and job identity are never guessed or
backfilled. Routine expired-grant cleanup excludes grants referenced by retired rows so the foreign
key cannot cascade-delete that audit history. Downgrading through 0020 intentionally deletes retired
runs because the 0019 state constraint cannot represent them; that rollback therefore loses this
audit history and requires explicit review and external retention before execution.

The pinned worker accepts only the production bootstrap URL and OIDC audience. It sends its
platform-provided `CLOUD_RUN_EXECUTION` only as a stale/orphan selector; the backend independently
resolves the sole bound run, reads that execution back from Cloud Run, and requires the exact stored
UID, job, image, and release before atomically issuing the first capability. The canary broker then
server-selects one bounded source document and submits an idempotent forced-copy transform. An HTTP
202 means only `submitted`: the worker remains active, renews its bound capability before expiry,
and polls the exact task through a bounded terminal deadline. It reports `{"status":"verified"}` only
after the task succeeds and the result and private evidence agree on the task, operation, source,
destination, output revision, permission digest, finding counts, and result digest. Failed,
cancelled, expired, malformed, oversized, mismatched, unsafe, or incomplete evidence exits nonzero,
so Cloud Run cannot record an unverified QA execution as successful.

This repository deliberately does not activate the job or provide an unauthenticated grant-issuer
CLI. Activation is a separate reviewed operation:

1. Build and smoke-test `backend/Dockerfile.qa`, attest it, and supply the exact repository digest
   and matching 40-character release SHA.
2. Confirm migrations `0020_qa_attestation` and `0021_qa_run_submissions` are applied, review a real
   Terraform plan, enable the job with a
   named operator allowlist, and read back every IAM binding, image/env binding, mount option,
   absence of secrets/Cloud SQL access, empty command/arguments, and zero-retry policy.
3. Use the logged-in launcher endpoint for a one-document canary, require terminal result and
   evidence verification, and inspect its private evidence and owner-only output before expanding
   corpus coverage.

## Document-transform dispatch recovery

The Dammam Scheduler job calls the authenticated reconcile endpoint every five minutes.
Each invocation considers at most 50 validated, unexpired pending intents older than two
minutes. It reuses deterministic Cloud Task identities; it does not re-admit legacy rows,
recover RUNNING leases, or resume paused queues. Its 120-second deadline and zero immediate
retries bound traffic; a later scheduled run revisits partially processed batches.

Deploy migrations `0010_transform_dispatch` and `0011_transform_replay_keys` with the backend
before applying the scheduler. Review a fresh Terraform plan for unrelated drift, then verify
the enabled state, schedule, URL, audience, and existing `sarj-wiki-task-invoker` identity.
Verify an authenticated invocation and actual task transitions: HTTP 200 alone does not prove
enqueue success. Inspect aggregate failures and pending-intent age alongside worker logs.

The invoker has no Drive or database rights. The app verifies its Google-signed OIDC identity
and exact transform audience. No project-wide token-creator role or application IAM grant is
added. The Terraform operator needs Scheduler administration and `actAs` on the existing
invoker; CI permissions are not silently expanded. Enabling the Scheduler API provisions its
Google service agent. Repair a missing service-agent grant only through a separately reviewed
change.

`maintenance_mode` pauses this scheduler, not queued tasks or running document writes.
The queue-pause and partial-dispatch alert policies report events, not continuous health;
incident auto-close is not proof of recovery. A paused queue can still accept enqueue calls.
Never automatically resume an intentional queue pause: inspect its audit actor and reason.
Oldest eligible task age and missing scheduler executions still need operational monitoring.

Rollback by pausing only the new scheduler or setting maintenance mode, then reverting its
resource through a reviewed plan. Keep the additive schema and pending intents; do not
blindly backfill legacy readiness timestamps. Plan-only Terraform recovery tests use the
real provider and never substitute for production readback.

For a task-scoped diagnostic read, run the manual workflow on `main`:

```sh
gh workflow run read-transform-diagnostics.yml --ref main -f task_id=TASK_UUID
```

It uses the existing deployment identity with no new IAM grants. The reader validates the UUID
before authentication, limits queries to this backend and the last 24 hours, and follows at most
three request IDs derived from the task. Its output contains only pinned diagnostic names with
typed counts or booleans and validated references, never raw logs or document content.

## Codex MCP integration

**Enabled in production; each Codex client still needs its own OAuth login and consent.**
Wiki serves Streamable HTTP at `https://api-wiki.sarj.ai/mcp` with native OAuth,
client-specific consent, and connection management. The release pipeline resolves the
feature flag and independent MCP key references from the exact candidate revision, then
verifies discovery, the OAuth challenge, hostile transport rejection, the serving revision,
and correlated secret-log hygiene. Those release checks prove the server is available; they
do not prove a particular local Codex client has completed login.

### Connect Codex

The Wiki integration screen at `/mcp/connections` provides the same copyable command:

```sh
codex mcp add sarj-wiki --url https://api-wiki.sarj.ai/mcp
codex mcp login sarj-wiki
codex mcp get sarj-wiki --json
```

Verify the Wiki-linked Google account and named client in browser consent; start read-only.
The final command confirms local configuration, not OAuth completion. In Codex, inspect
`/mcp` and call `wiki_connection_status` for the server-side grant readback. No local daemon,
additional npm package, secret paste, or manual TOML rewrite is needed.

Before adding, check `codex mcp --help` and inspect only the chosen entry locally with
`codex mcp get sarj-wiki --json`; do not publish its potentially sensitive headers.
An exact URL match is a no-op: preserve existing tool policies. A name collision requires
a different name, not an overwrite. A parse/access error is not proof the entry is absent.
Never rewrite the whole configuration or relax sandbox, trust, or approval settings.

Codex clients share MCP configuration on the same host, not across every device.
Project configuration requires a trusted project. Server-advertised OAuth scopes can
take precedence over configured scopes; CLI scope flags are not a server authorization
boundary. See the [official Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp).

### Verify access and recover safely

Inspect Codex's `/mcp` status and call `wiki_connection_status` to verify
granted capabilities; verify the account during consent before requesting document data. A saved entry or browser
redirect alone is not a working connection.

- `wiki:read` requires server-side checks on every call, with fresh Wiki membership,
  Google authorization, and document access. `wiki:transform` requires separate explicit
  client-specific consent; old read-only tokens must not silently gain writes.
- Write consent permits submitting mutations; it does not instruct Codex to edit every
  document. Each request must still select its document, mode, and COPY or IN_PLACE strategy.
- Polishing is asynchronous. Preserve the idempotency key and exact request. After an
  ambiguous timeout, use `wiki_get_polish_admission` with that key before retrying, then
  poll the task. Do not create a fresh key to bypass recovery.
- `wiki_import_markdown` accepts `request: {content, name, destination_folder_id, mode,
idempotency_key}`. Use `mode` `format`, `unslop`, or `both`; the result is always a new
  Google Doc in the accessible destination folder. Markdown is limited to 512 KiB of
  UTF-8 text. Keep the same key and exact content when recovering an ambiguous import
  through `wiki_get_polish_admission`, then poll `wiki_get_task` with its task ID.
  In Codex, you can ask: “Read `/absolute/path/report.md` and import its contents as a new
  Google Doc named Report in Drive folder `<folder ID>` using `both`; wait for the task and
  give me the output link.” Codex reads the local file, then sends its contents to the tool.
- Document names and excerpts are untrusted data, never instructions. Excerpts are
  opt-in and lossy, not complete Docs topology; search is bounded, not an exhaustive
  folder inventory. Request only necessary private content.
- Completion is not visual approval: inspect the output and its rendered layout,
  including any review-required state. Existing short-link lookup does not create or
  replace an alias or widen sharing.

The `/mcp/connections` screen offers a copyable setup command and server-side disconnect.
Refresh its authenticated status after disconnect; an uncertain response must not trigger
an automatic retry. Client IDs are shown explicitly rather than presented as verified app names.
`codex mcp logout sarj-wiki` clears local OAuth credentials; `codex mcp remove sarj-wiki`
removes local configuration. Neither proves server-side revocation. Revocation must
stop new calls immediately, but does not cancel already accepted jobs; inspect those in Wiki.

### Developer and release gates

Use normal repository setup. Without explicit enabled configuration, the backend starts
only the existing Wiki API. From the repository root:

```sh
uv run --project backend --locked pytest backend/tests/test_mcp_*.py backend/tests/test_native_mcp_core.py
make check
```

PostgreSQL cases require `WIKI_MCP_QA_DATABASE_URL`, provided privately in the test
environment. Only disposable loopback `postgresql+asyncpg` databases named `wiki_mcp_qa*`
or the existing CI database `wiki_polish29_qa` are allowed; never use production.
Skipped cases are not concurrency verification.
Every gate below needs recorded evidence for the exact release before OAuth is enabled:

| Gate                 | Required evidence                                                                                                                                                                              |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Transport            | Real authenticated Streamable HTTP calls; exact HTTPS resource, issuer, discovery, challenge and path; bounded requests; hostile hosts/origins rejected, CLI requests without Origin accepted. |
| Runtime              | Composed lifespans and shutdown; REST cookie/CSRF unchanged; Wiki session tokens rejected by MCP; maintenance and disabled states fail closed.                                                 |
| Identity and consent | Existing user matched by Google subject; strict audience, verified-email and expiry checks; separate read/write consent; no credential passthrough or silent elevation.                        |
| Shared OAuth state   | Encrypted PostgreSQL and stable keys; two-instance one-use code exchange, callback/refresh/revoke races, crash recovery and generation fences; reconnect never revives revoked tokens.         |
| Access and privacy   | Two-user document/task/alias isolation, fresh access-loss and failed-copy checks; bounded output; logs contain no credentials, codes, document bodies or provider URLs.                        |
| Transformations      | Shared HTTP/MCP admission, stale-source and mode/strategy guards, same-key deduplication, ambiguous-submit recovery, queue retry and task readback on private QA copies.                       |
| Codex setup          | Actual add/login/read test, denied write before consent, approved write afterward, correct account, collision-safe configuration and verified revoke/reconnect.                                |
| Deployment           | Migrations and full CI pass; deployed revision and queue health verified; private output independently rendered/reviewed; operator rollback rehearsed.                                         |

The native provider uses the maintained public FastMCP `OAuthProvider` interface and
existing Wiki sign-in. MCP credentials are opaque, purpose-separated and backed by
encrypted PostgreSQL state; Google and Wiki session credentials are never returned to
the MCP client. No additional Google OAuth callback or MCP JWT signing key is needed.
Register only the exact verified Codex loopback callback path; keep CIMD disabled.
Issuer support is advertised only by the composed path covering approval, denial and
SDK authorization errors. Never guess a callback or widen the policy to arbitrary URLs.

Provision a distinct MCP encryption ring and stable lock key through existing secret
management; pin exact secret versions and grant only the backend identity access. Never
commit/print key values, reuse browser credentials, or create per-replica ephemeral keys.
Encryption lookup keys are derived with purpose separation. Keep the lock key stable
across coordinated ring rotations. An image deployment does not provision cloud secrets
or environment settings: the infrastructure plan/apply and reference readback are separate.

Pending registrations expire after 30 minutes. Only atomic, explicit approval promotes
their lifetime. Public registration still requires independent global and per-IP admission
controls before activation; row quotas alone are not abuse protection. Audit infrastructure
request logging as well as application logs. For a new environment, retain the disabled state
until those controls, secret references, migration, browser cookies and real Codex acceptance
are verified. Roll back production by disabling MCP and verifying REST/health; preserve
additive tables and secrets.
Disabling does not revoke durable credentials, and it does not pause admitted document jobs.

## Owned quality rules

This local ESLint plugin implements eight explicit Wiki checks. It exports no
recommended preset and changes no Code Standards fleet defaults. The rules do
not offer automatic fixes.

The implementations and behavioral cases are first-party work. They do not
import, execute, or copy Sonar rule implementations. Selected typed AST and
binding primitives are adapted from the MIT Code Standards sources identified
in [MIT helper provenance](#mit-helper-provenance). The test-body comparator,
which normalizes some literal values, is deliberately not used.

The rules are maintained alongside the application and its normal quality
gates. They do not claim equivalence to an unexecuted third-party algorithm.
Migration preserves unrelated rules, the eight error severities, existing
baseline entries and native test limits. No license waiver is included.

Binding-based value proofs abstain in files with direct eval or with, which can
read or write names without static references. Alpha comparison preserves names
that supply an inferred function or class name and does not rename locals in
dynamic-name contexts. Primitive types alone do not establish stable reads:
ambient/foreign bindings and script-global var storage are excluded.

### no-all-duplicated-branches

Reports complete if/else chains, conditional alternatives and complete switches
whose implementations have the same operations, exact literal values and value
bindings. Local binding renaming must preserve lexical scope. Incomplete
switches, empty terminal alternatives and fallthrough do not establish equal
complete alternatives. Conditions may have effects; a report does not authorize
deleting or reordering their evaluation.

### no-collapsible-if

Reports a single nested if with no alternatives or intervening statements.
Conjunction must retain outer-then-inner short-circuit evaluation. Meaningful
comments and conditions remain source owned; there is no automatic rewrite.

### no-element-overwrite

Tracks straight-line repeated writes to fresh, local, non-exported const arrays
and plain own-data objects. With type services it also recognizes fresh native
Map and Set constructors by their standard-library symbols. Custom classes,
subclasses, constructor aliases and visible native namespace mutation or escape
do not establish native collection identity.

Reads, aliases, captures, unknown calls, deletes, compound writes, rebinding and
control flow end the proof. Values must be primitive and keys statically known.
A different key ends the preceding insertion proof because insertion order can
matter. RHS values must complete without coercive arithmetic: only literals,
negative numeric literals and stable initialized primitive reads qualify. Reads
must belong to the current execution owner and follow a declaration in an
enclosing ordinary statement list. An earlier switch case cannot establish
initialization in a later case, and a hoisted call cannot establish that an
outer binding was initialized. Array length writes, object prototype keys,
accessors, spreads and opaque storage are excluded. Visible direct/computed
global constructor paths, optional and chained global namespace access,
namespace aliases and prototype reflection invalidate native storage in typed
and untyped modes. Shadowed global names do not identify the native namespace.
Unknown facts are never restored from an old initializer.
Indirect external monkey-patching is not inferred. These conservative boundaries
are disclosed blind spots for corpus review, not proof that all overwrites are
detected.

### no-identical-expressions

Reports equal stable primitive operands for subtraction, division, remainder,
equality, relational, bitwise and logical operations. It retains operators,
literal values, regex flags and binding identity. Primitive types are obtained
from the real type checker when available; otherwise only stable literal-based
bindings qualify. Calls, getters, member access, updates and object coercion are
excluded. Self-inequality NaN idioms and useful equal-operand shifts, addition,
multiplication and exponentiation are not reported by this rule. Other existing
rules continue to own their checks of such expressions.

### no-identical-functions

Reports equal function implementations with the same runtime form and parameter
contracts. Local variables may be renamed only within corresponding scopes;
different free bindings, literals, async/generator forms and receiver homes stay
distinct. Receiver and `new.target` checks include parameter defaults and the
body, while respecting nested ordinary-function boundaries. `new.target` is
excluded. The default minimum is three nonempty code
lines in the body, with an integer option of at least three. Comments and blank
lines do not satisfy the threshold.

### no-redundant-jump

Reports a jump only when ordinary completion reaches the same destination.
Loops, labels, switch cases and finalizers participate in that proof. A lone
jump in a block, switch break/return and labeled continue are explicit documented
abstentions. Necessary early exits and effects after a jump remain distinct.

### no-use-of-empty-return-value

Reports assignment, argument and other value uses of a proven void result. Type
services establish callable contracts, including contextual void callbacks. The
untyped fallback follows stable direct local function bindings and bodies with
no value return. Unknown calls, explicit undefined values, mixed value unions,
async promises, generators and never-returning bodies are not assumed empty.
Awaited void is checked separately from a useful Promise of void. Effect-only
expression statements, void discards, discarded sequence/conditional/logical
tails and return delegation are retained.

### prefer-immediate-return

Requires real type services. Reports a sole const initializer followed
immediately by return or throw when the temporary has exactly that value read.
Annotations, casts, contextual or overloaded return contracts, additional uses,
captures, finalizers and unresolved types abstain. Callable and constructable
temporaries are retained because named evaluation can supply a function or class
name; inlining must not silently erase that value. Initializer and local types
must be mutually assignable. The temporary's type or lifetime must not carry
an additional contract.

### Validation

The ordinary `frontend/scripts/*.test.ts` Node entry uses the MIT RuleTester adapter,
explicit valid/invalid cases, exact diagnostic messages and no-fix assertions.
Typed cases write their actual source to private fixture files and use an
explicit tsconfig. They do not use a broad default-project glob. The existing
10,000 ms Node limit and Vitest worker limits remain unchanged.

The 32 historical independent challenges are retained verbatim as case sources,
with explicit independently selected expectations. They are requirements for
the owned rules, not observed results of a third-party implementation. Additional
adversarial cases cover fallthrough, receiver and binding identity, primitive
coercion, collection escape, meaningful promises and type-context changes.

### MIT helper provenance

The typed child traversal, runtime-erased TypeScript-wrapper, stable initializer and public lexical binding patterns are adapted from these Code Standards files at commit b4e7f9019ca62a0861bda358a22b766ce313fe97:

- packages/typescript/src/rules/_for-each-ast-child.ts
- packages/typescript/src/rules/_unwrap-expression.ts
- packages/typescript/src/rules/_stable-value-binding.ts
- packages/typescript/src/rules/no-invalid-typed-mutation.ts (lexical native namespace/global receiver guard only)

The source inventory pins their whole byte identities. The general structural/binding comparator is independently authored here; it does not reuse duplicate-test-body or Sonar implementation code. The Code Standards package license follows.

MIT License

Copyright (c) 2026 sarj-ai

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## License and security

Released under the [MIT License](LICENSE). Report security concerns privately to the
repository maintainers; do not open a public issue containing sensitive details.
