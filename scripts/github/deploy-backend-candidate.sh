#!/usr/bin/env bash
set -euo pipefail

candidate_image="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/${IMAGE}:${GITHUB_SHA}"
candidate_digest=$(gcloud artifacts docker images describe "$candidate_image" \
  --project "$PROJECT_ID" \
  --format='value(image_summary.digest)')
expected_revision_image="${candidate_image%:*}@${candidate_digest}"
service_json=$(gcloud run services describe "$SERVICE" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --format=json)
previous_revision=$(jq -r \
  '[.status.traffic[]? | select(.percent == 100) | .revisionName] | if length == 1 then .[0] else "" end' \
  <<<"$service_json")
if [[ -z "$previous_revision" ]]; then
  echo "Cloud Run does not have exactly one revision at 100% traffic" >&2
  exit 1
fi
previous_image=$(gcloud run revisions describe "$previous_revision" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --format='value(spec.containers[0].image)')
if [[ "$previous_image" == "$expected_revision_image" ]]; then
  echo "This immutable release is already serving production traffic" >&2
  exit 1
fi
echo "previous_revision=$previous_revision" >>"$GITHUB_OUTPUT"
echo "started_at=$(date -u +'%Y-%m-%dT%H:%M:%S.%NZ')" >>"$GITHUB_OUTPUT"

gcloud run services update "$SERVICE" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --image "$candidate_image" \
  --no-traffic \
  --quiet

candidate_revision=''
candidate_ready=''
for _attempt in {1..6}; do
  service_json=$(gcloud run services describe "$SERVICE" \
    --project "$PROJECT_ID" \
    --region "$REGION" \
    --format=json)
  candidate_revision=$(jq -r '.status.latestCreatedRevisionName // empty' \
    <<<"$service_json")
  if [[ -n "$candidate_revision" ]]; then
    revision_json=$(gcloud run revisions describe "$candidate_revision" \
      --project "$PROJECT_ID" \
      --region "$REGION" \
      --format=json)
    candidate_ready=$(jq -r \
      '[.status.conditions[]? | select(.type == "Ready") | .status][0] // empty' \
      <<<"$revision_json")
    if [[ "$candidate_ready" == 'True' ]]; then
      break
    fi
  fi
  sleep 10
done
if [[ -z "$candidate_revision" || "$candidate_ready" != 'True' ]]; then
  echo "The no-traffic Cloud Run candidate did not become ready" >&2
  exit 1
fi
candidate_revision_image=$(gcloud run revisions describe "$candidate_revision" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --format='value(spec.containers[0].image)')
if [[ "$candidate_revision_image" != "$expected_revision_image" ]]; then
  echo "The no-traffic candidate does not resolve to the release image digest" >&2
  exit 1
fi
echo "new_revision=$candidate_revision" >>"$GITHUB_OUTPUT"
echo "release_image=$expected_revision_image" >>"$GITHUB_OUTPUT"
