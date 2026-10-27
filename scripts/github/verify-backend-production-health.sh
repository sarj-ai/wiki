#!/usr/bin/env bash
set -euo pipefail

service_json=''
new_revision=''
active_revision=''
for _attempt in {1..6}; do
  service_json=$(gcloud run services describe "$SERVICE" \
    --project "$PROJECT_ID" \
    --region "$REGION" \
    --format=json)
  new_revision=$(jq -r '.status.latestReadyRevisionName // empty' <<<"$service_json")
  active_revision=$(jq -r \
    '[.status.traffic[]? | select(.percent == 100) | .revisionName] | if length == 1 then .[0] else "" end' \
    <<<"$service_json")
  if [[ -n "$new_revision" && "$active_revision" == "$new_revision" ]]; then
    break
  fi
  sleep 10
done
if [[ -z "$new_revision" || "$active_revision" != "$new_revision" ]]; then
  echo "Cloud Run did not converge to one latest revision at 100% traffic" >&2
  exit 1
fi
echo "new_revision=$new_revision" >>"$GITHUB_OUTPUT"

canary_id="wiki-${GITHUB_SHA:0:12}-${GITHUB_RUN_ATTEMPT}"
echo "canary_id=$canary_id" >>"$GITHUB_OUTPUT"
health_body="$RUNNER_TEMP/backend-health.json"
health_headers="$RUNNER_TEMP/backend-health.headers"
healthy=false
for _attempt in {1..6}; do
  : >"$health_body"
  : >"$health_headers"
  status=$(curl --silent --show-error \
    --connect-timeout 10 \
    --max-time 20 \
    --dump-header "$health_headers" \
    --output "$health_body" \
    --write-out '%{http_code}' \
    --header "X-Request-ID: $canary_id" \
    https://api-wiki.sarj.ai/health || true)
  echoed_id=$(awk 'tolower($1) == "x-request-id:" { sub(/\r$/, "", $2); print $2 }' \
    "$health_headers" | tail -n 1)
  if [[ "$status" == 200 && "$echoed_id" == "$canary_id" ]] &&
    jq -e --arg revision "${EXPECTED_RELEASE_SHA:-$GITHUB_SHA}" \
      '.status == "ok" and .database == "ok" and .revision == $revision' \
      "$health_body" >/dev/null; then
    healthy=true
    break
  fi
  sleep 10
done
if [[ "$healthy" != true ]]; then
  echo "Production health canary did not return the expected release identity" >&2
  exit 1
fi
echo "finished_at=$(date -u +'%Y-%m-%dT%H:%M:%S.%NZ')" >>"$GITHUB_OUTPUT"
