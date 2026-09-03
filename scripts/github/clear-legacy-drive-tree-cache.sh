#!/usr/bin/env bash
set -euo pipefail

gcloud run jobs update "$MIGRATION_JOB" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --image "$RELEASE_IMAGE" \
  --command ".venv/bin/python" \
  --args=-m,app.cache.cleanup_legacy \
  --quiet
cleanup_status=0
gcloud run jobs execute "$MIGRATION_JOB" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --wait \
  --quiet || cleanup_status=$?
gcloud run jobs update "$MIGRATION_JOB" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --image "$RELEASE_IMAGE" \
  --command ".venv/bin/alembic" \
  --args=upgrade,head \
  --quiet
exit "$cleanup_status"
