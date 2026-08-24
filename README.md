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

## Dammam production cutover

The regional migration is staged; never combine database promotion and URL-map switching in one
Terraform apply. Every saved plan must contain no destroys or replacements. Both Cloud Tasks
queues must be paused and `maintenance_mode=true` must be live before Cloud SQL promotion.

1. Deploy the backward-compatible token-lifecycle migration to Doha and record the immutable
   backend image digest.
2. Build the parallel stack with `enable_dammam_stack=true`, `serve_dammam=false`, and that
   `dammam_image`. Keep `smart-create-dammam` paused.
3. Wait for `sarj-docs-db-dammam` to be `RUNNABLE`; confirm it replicates `sarj-docs-db` with zero
   lag.
4. Pause `smart-create`, verify both queues have zero running tasks, enable maintenance, and check
   that application routes return 503 while `/health` remains 200.
5. Promote `sarj-docs-db-dammam`, then reconcile with `dammam_database_promoted=true`,
   `maintenance_mode=true`, and `serve_dammam=false`.
6. Confirm Doha `/health` can reach the promoted Dammam database, then apply `serve_dammam=true`
   while maintenance remains enabled and confirm public health reports the Dammam release.
7. Disable maintenance, resume only `smart-create-dammam`, and run authenticated journeys. Keep
   Doha as a protected rollback runtime connected to the Dammam primary.
8. Point backend deployment repository variables at `me-central2`, `sarj-docs-dammam`,
   `sarj-docs-backend-dammam`, `sarj-docs-backend-migrations-dammam`, and `smart-create-dammam`.

After promotion, runtime rollback means setting `serve_dammam=false`; never restore the former
Doha database as primary. Migrate Terraform state to the versioned Dammam bucket only after the
observation window, and decommission protected Doha resources in a separate reviewed change.

## License and security

Released under the [MIT License](LICENSE). Report security concerns privately to the
repository maintainers; do not open a public issue containing sensitive details.
