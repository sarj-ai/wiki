#!/usr/bin/env bash
set -euo pipefail

gcloud run jobs update "$MIGRATION_JOB" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --image "$RELEASE_IMAGE" \
  --command ".venv/bin/python" \
  --args=-m,app.cache.cleanup_legacy \
  --quiet
set +e
cleanup_output=$(gcloud run jobs execute "$MIGRATION_JOB" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --wait \
  --quiet 2>&1)
cleanup_status=$?
set -e
printf '%s\n' "$cleanup_output"
if (( cleanup_status != 0 )); then
  execution_name=$(grep -Eo "${MIGRATION_JOB}-[a-z0-9]{5}" <<< "$cleanup_output" | tail -n 1 || true)
  if [[ -n "$execution_name" ]]; then
    gcloud run jobs executions describe "$execution_name" \
      --project "$PROJECT_ID" \
      --region "$REGION" \
      --format='yaml(metadata.name,status.conditions,status.failedCount,status.logUri)' || true
    gcloud logging read \
      "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"${MIGRATION_JOB}\" AND labels.\"run.googleapis.com/execution_name\"=\"${execution_name}\" AND severity>=ERROR" \
      --project "$PROJECT_ID" \
      --limit 40 \
      --order asc \
      --format='value(timestamp,severity,textPayload,jsonPayload.message)' || true
  fi
fi
gcloud run jobs update "$MIGRATION_JOB" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --image "$RELEASE_IMAGE" \
  --command ".venv/bin/alembic" \
  --args=upgrade,head \
  --quiet
exit "$cleanup_status"
