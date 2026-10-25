#!/usr/bin/env bash
set -euo pipefail

uv run --no-project --with typer==0.27.3 --python 3.14 python -m scripts.backend_release.recovery \
  --verifier-sha "$GITHUB_SHA" serving --source-sha "$SOURCE_RELEASE_SHA" \
  --project "$PROJECT_ID" --region "$REGION" --repository "$REPOSITORY" \
  --service "$SERVICE" --image "$IMAGE"
