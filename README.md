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
a seeded manifest plus a resumable JSONL ledger. It selects only owner-only, unshared native Google
Docs whose parent folders are also owner-only and private; prior transform outputs are excluded.
Use the full production release SHA shown by `/health` and keep the canary and corpus in separate
private artifact directories.

First plan the one-document canary, without creating a Drive file:

```bash
cd backend
uv run python scripts/qa_document_transform_corpus.py \
  --seed canary-2026-09-04 \
  --expected-release-sha 0123456789abcdef0123456789abcdef01234567 \
  --manifest /private/tmp/wiki-doc-transform-qa/canary/manifest.json \
  --ledger /private/tmp/wiki-doc-transform-qa/canary/events.jsonl \
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
  --artifacts-dir /private/tmp/wiki-doc-transform-qa/canary/artifacts \
  --pdftoppm-version 25.06.0 \
  --judge-model gemini-3.8-flash \
  --count 1 \
  --canary \
  --execute \
  --confirm-live-copy CREATE_1_PRIVATE_CANARY
```

Only after the canary is terminal, structurally verified, rendered on every page, and visually
accepted should you plan the 20-document corpus. Pass the verified canary manifest, ledger, and
artifact directory to bind the corpus to that exact release and exclude the canary source:

```bash
uv run python scripts/qa_document_transform_corpus.py \
  --seed corpus-2026-09-04 \
  --expected-release-sha 0123456789abcdef0123456789abcdef01234567 \
  --manifest /private/tmp/wiki-doc-transform-qa/corpus/manifest.json \
  --ledger /private/tmp/wiki-doc-transform-qa/corpus/events.jsonl \
  --canary-manifest /private/tmp/wiki-doc-transform-qa/canary/manifest.json \
  --canary-ledger /private/tmp/wiki-doc-transform-qa/canary/events.jsonl \
  --canary-artifacts-dir /private/tmp/wiki-doc-transform-qa/canary/artifacts \
  --judge-model gemini-3.8-flash
```

Review the 20 selected sources, then repeat that command with a private corpus `--artifacts-dir`,
the same pinned renderer and judge, plus
`--execute --confirm-live-copy CREATE_20_PRIVATE_SIBLINGS`. Execution reads the application bearer
token only from `WIKI_QA_ACCESS_TOKEN`. UUIDv5 keys provide idempotent replay; the JSONL ledger resumes
only after revalidating its release, source/output binding, permissions, and artifact hashes. The
runner stops at the first failed invariant, retains successful labeled siblings for inspection,
renders every eligible page, and never calls a Drive permission mutation.

## License and security

Released under the [MIT License](LICENSE). Report security concerns privately to the
repository maintainers; do not open a public issue containing sensitive details.
