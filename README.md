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

## Document Polish

Document Polish creates a new, branded Google Doc from an existing Drive file or pasted Markdown.
It never edits the source file. Every run explicitly selects one of three modes:

- `format` applies the deterministic Sarj document system without rewriting prose.
- `unslop` removes only confirmed AI-writing defects and otherwise returns an exact no-op.
- `both` runs the conservative unslop pass before applying the Sarj document system.

Submit `POST /document-transforms` with an `Idempotency-Key` UUID header, then poll
`GET /tasks/{task_id}` until the asynchronous task reaches a terminal state. Inputs may be a Drive
file with an expected modification time or pasted Markdown with a destination folder ID.

The copy-on-write pipeline pins model IDs, proposes structured block patches, verifies source block
hashes, protects facts and syntax, and applies Google Docs mutations against a required revision.
Unsafe suggestions are skipped and surfaced as review findings. The high-signal detector pack is
derived from `theclaymethod/unslop` version 2.3.0 at commit
`d81f5196167ded24f46fced04958c0c12d681798`; scanner hits are only candidates, and accepted edits
cannot alter names, numbers, dates, citations, links, code, units, scope, negation, or force-bearing
language.

Inputs are bounded to 512 KiB of pasted Markdown, 500,000 UTF-16 text units, and 50 Google Docs
tabs. Drive copies preserve unsupported rich structures. Markdown images, raw HTML, footnotes,
strikethrough, nested quotations, and tilde code fences are rejected before output creation because
the native converter cannot yet prove preservation. Markdown imports remain marked
`structure_verification_required` until an automated Google Docs round-trip comparator exists.

The production model policy uses Vertex AI in `global`: `gemini-3.1-flash-lite` for conservative
patch proposals, `gemini-2.5-flash-lite` as the stable fallback, and `gemini-3.8-flash` as the final
higher-reasoning escalation. Visual restyling is deterministic so clean prose is not rewritten
without a factual-equivalence proof.

The brand layer applies Nunito typography, Sarj heading sizes, restrained purple accents,
left-aligned body text, margins, and consistent paragraph rhythm. Existing Docs headers, logos,
footers, tabs, page numbers, and rich objects are preserved. Markdown imports receive a native DOCX
header logo and footer page field before Google Docs conversion. The canonical logo is served from
`/brand/sarj-logo-light.png`; if unavailable, the task returns a `logo_unavailable` review finding
instead of fabricating a placeholder or failing the formatting batch.

Recovered Codex and Claude requests for headings, lists, checklists, tables, links, quotations, code
blocks, dividers, centered logo, hierarchy, spacing, bold table headers, and visual QA are covered.
Left alignment intentionally supersedes the earlier justified-body request after a 75-page PDF
showed severe spacing rivers. Template-specific generated tabs, linked tab indexes, per-tab headers,
covers, date chips, Mermaid images, captions, and native footnotes are not silently approximated;
they require a future explicit template.

Document transformations run on a dedicated Cloud Tasks queue and OIDC-authenticated internal
worker. Task results expose the pipeline version, model IDs, UNSLOP source version, validation
summary, findings, and output metadata.

## License and security

Released under the [MIT License](LICENSE). Report security concerns privately to the
repository maintainers; do not open a public issue containing sensitive details.
