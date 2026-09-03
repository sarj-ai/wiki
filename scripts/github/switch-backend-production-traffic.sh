#!/usr/bin/env bash
set -euo pipefail

gcloud run services update-traffic "$SERVICE" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --to-revisions "$CANDIDATE_REVISION=100" \
  --quiet
