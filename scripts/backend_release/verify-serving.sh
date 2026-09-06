#!/usr/bin/env bash
set -euo pipefail

uv run --no-project --python 3.14 python -m scripts.backend_release.recovery \
  --sha "$GITHUB_SHA" serving \
  --project "$PROJECT_ID" --region "$REGION" --repository "$REPOSITORY" \
  --service "$SERVICE" --image "$IMAGE"
