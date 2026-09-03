#!/usr/bin/env bash
set -euo pipefail

gcloud run jobs update "$MIGRATION_JOB" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --image "$RELEASE_IMAGE" \
  --command ".venv/bin/alembic" \
  --args "upgrade,head" \
  --quiet
gcloud run jobs execute "$MIGRATION_JOB" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --wait \
  --quiet
