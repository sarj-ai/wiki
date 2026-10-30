#!/usr/bin/env bash
set -euo pipefail

completion_filter=$(
  cat <<EOF
resource.type="cloud_run_revision"
AND resource.labels.service_name="${SERVICE}"
AND resource.labels.revision_name="${VERIFIED_REVISION}"
AND timestamp>="${DEPLOY_STARTED_AT}"
AND timestamp<="${VERIFY_FINISHED_AT}"
AND jsonPayload.record.extra.request_id="${CANARY_ID}"
AND jsonPayload.record.message="request.complete"
EOF
)
completion_count=0
for _attempt in {1..12}; do
  completion_count=$(gcloud logging read "$completion_filter" \
    --project "$PROJECT_ID" \
    --limit 10 \
    --format='value(insertId)' | wc -l | tr -d ' ')
  if [[ "$completion_count" -ge 1 ]]; then
    break
  fi
  sleep 10
done
if [[ "$completion_count" -lt 1 ]]; then
  echo "No correlated request.complete log arrived for the production canary" >&2
  exit 1
fi

error_filter=$(
  cat <<EOF
resource.type="cloud_run_revision"
AND resource.labels.service_name="${SERVICE}"
AND resource.labels.revision_name="${VERIFIED_REVISION}"
AND timestamp>="${DEPLOY_STARTED_AT}"
AND timestamp<="${VERIFY_FINISHED_AT}"
AND (
  httpRequest.status>=500
  OR severity>=ERROR
  OR jsonPayload.record.level.name=~"^(ERROR|CRITICAL)$"
  OR textPayload=~"[|] (ERROR|CRITICAL) +[|]"
)
EOF
)
error_count=$(gcloud logging read "$error_filter" \
  --project "$PROJECT_ID" \
  --limit 100 \
  --format='value(insertId)' | wc -l | tr -d ' ')
if [[ "$error_count" -ne 0 ]]; then
  echo "The new revision emitted $error_count error log entries in the bounded release window" >&2
  exit 1
fi

{
  release_sha=${EXPECTED_RELEASE_SHA:-$GITHUB_SHA}
  echo '## Backend production gate'
  echo "- Revision: \`${VERIFIED_REVISION}\`"
  echo "- Release: \`${release_sha}\`"
  echo "- Migration job: \`${MIGRATION_JOB}\`"
  echo "- Canary: \`${CANARY_ID}\`"
  echo "- Correlated completion logs: $completion_count"
  echo '- Error logs: 0'
} >>"$GITHUB_STEP_SUMMARY"
