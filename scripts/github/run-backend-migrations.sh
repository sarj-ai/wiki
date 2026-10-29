#!/usr/bin/env bash
set -euo pipefail

: "${MIGRATION_JOB:?MIGRATION_JOB is required}"
: "${PROJECT_ID:?PROJECT_ID is required}"
: "${REGION:?REGION is required}"
: "${RELEASE_IMAGE:?RELEASE_IMAGE is required}"

verify_job() {
  jq --slurp --exit-status --arg image "$1" '
    length == 1 and (.[0] |
      .apiVersion == "run.googleapis.com/v1" and .kind == "Job" and
      (.spec.template.spec.template.spec.containers |
        type == "array" and length == 1 and
        .[0].command == [".venv/bin/alembic"] and
        .[0].args == ["upgrade", "head"] and
        ($image == "" or .[0].image == $image)))
  ' >/dev/null
}

if ! gcloud run jobs describe "$MIGRATION_JOB" \
  --project "$PROJECT_ID" --region "$REGION" --format=json | verify_job ""; then
  echo "Migration job configuration could not be verified; refusing to update or execute." >&2
  exit 1
fi

if ! gcloud run jobs update "$MIGRATION_JOB" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --image "$RELEASE_IMAGE" \
  --format=json \
  --quiet | verify_job "$RELEASE_IMAGE"; then
  echo "Migration job update could not be verified; refusing to execute." >&2
  exit 1
fi

gcloud run jobs execute "$MIGRATION_JOB" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --wait \
  --quiet
