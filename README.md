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
make pull-local-secrets
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
make doctor
```

`make check` mirrors CI. `make audit` performs network-backed dependency audits, and
`make doctor` verifies generated standards configuration and version pins.

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
SHA, and one explicit human `user:` or `group:` invoker. The job has its own service account, zero
retries, and a dedicated public-access-prevented evidence bucket mounted with UID 1001 and
`0600`/`0700` modes. It has no Cloud SQL identity, database grants, Secret Manager access, production
signing/encryption material, or Google OAuth client secret. Its only project roles are the dedicated
bucket's `storage.objectUser` role and `aiplatform.user` for optional visual judging. The human gets
only `run.jobsExecutorWithOverrides`; no Scheduler or CI trigger is attached.

The authenticated same-site `POST /document-transforms/qa/grants` surface issues self-delegated
grants only. Existing cookie-origin middleware rejects cross-site cookie requests. The audit row
stores both issuer and subject user IDs, exact session and token version, release SHA, image digest,
source and destination folders, mode, and a maximum 15-minute expiry. The workload-authenticated
`POST /internal/document-transform-qa/exchange` endpoint atomically consumes a grant and returns an
opaque five-minute capability. Renewal rotates that capability under the same row lock for at most
the two-hour job lifetime and rechecks session revocation and token version every time. The job
never reads or updates grant rows directly. Consume and renewal requests include UUID request
nonces: deterministic backend-only derivation replays the exact response after a committed response
is lost, while a one-generation previous-token overlap permits the same renewal retry and rejects a
different nonce or older generation.

This repository deliberately does not activate the job or provide an unauthenticated grant-issuer
CLI. Activation is a separate reviewed operation:

1. Implement dedicated backend capability operations for the runner's bounded Drive reads, PDF
   exports, transform submission, and task polling. Until then Cloud execution deliberately fails
   before grant consumption; local corpus execution is unchanged.
2. Build and smoke-test `backend/Dockerfile.qa`, attest it, and supply the exact repository digest
   and matching 40-character release SHA.
3. Apply migration `0018_transform_qa_grants`, review a real Terraform plan, enable the job for a
   named security QA principal, and read back every IAM binding, image/env binding, mount option,
   absence of secrets/Cloud SQL access, and zero-retry policy.
4. Use the logged-in issuer endpoint, run a one-document canary, inspect its private evidence and
   owner-only output, and only then issue a separate grant for the full corpus.

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

**Disabled by default; production connection is not yet verified.** Native OAuth,
consent, connection management and Streamable HTTP remain disabled in the current
production release and in a default Terraform plan. Activation requires an explicit, reviewed
`enable_mcp_native=true` apply with both independent MCP keys attached. The release
pipeline resolves the flag and key references from the exact candidate revision; its verifier
then requires discovery, OAuth challenge, hostile transport rejection, the
exact serving revision and correlated secret-log hygiene to pass.

### Connect after release

Obtain the exact HTTPS endpoint from the released Wiki integration screen or verified
release record. Replace the deliberately nonfunctional placeholder:

```sh
codex mcp add sarj-wiki --url VERIFIED_WIKI_MCP_URL && codex mcp login sarj-wiki
```

Verify the Wiki-linked Google account and client in browser consent; start read-only. No local
daemon, additional npm package, secret paste, or manual TOML rewrite should be needed.

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

After release, inspect Codex's `/mcp` status and call `wiki_connection_status` to verify
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

| Gate | Required evidence |
| --- | --- |
| Transport | Real authenticated Streamable HTTP calls; exact HTTPS resource, issuer, discovery, challenge and path; bounded requests; hostile hosts/origins rejected, CLI requests without Origin accepted. |
| Runtime | Composed lifespans and shutdown; REST cookie/CSRF unchanged; Wiki session tokens rejected by MCP; maintenance and disabled states fail closed. |
| Identity and consent | Existing user matched by Google subject; strict audience, verified-email and expiry checks; separate read/write consent; no credential passthrough or silent elevation. |
| Shared OAuth state | Encrypted PostgreSQL and stable keys; two-instance one-use code exchange, callback/refresh/revoke races, crash recovery and generation fences; reconnect never revives revoked tokens. |
| Access and privacy | Two-user document/task/alias isolation, fresh access-loss and failed-copy checks; bounded output; logs contain no credentials, codes, document bodies or provider URLs. |
| Transformations | Shared HTTP/MCP admission, stale-source and mode/strategy guards, same-key deduplication, ambiguous-submit recovery, queue retry and task readback on private QA copies. |
| Codex setup | Actual add/login/read test, denied write before consent, approved write afterward, correct account, collision-safe configuration and verified revoke/reconnect. |
| Deployment | Migrations and full CI pass; deployed revision and queue health verified; private output independently rendered/reviewed; operator rollback rehearsed. |

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
request logging as well as application logs. Retain the disabled state until those controls,
secret references, migration, browser cookies and real Codex acceptance are verified.
Rollback by disabling MCP and verifying REST/health; preserve additive tables and secrets.
Disabling does not revoke durable credentials, and it does not pause admitted document jobs.

## License and security

Released under the [MIT License](LICENSE). Report security concerns privately to the
repository maintainers; do not open a public issue containing sensitive details.
