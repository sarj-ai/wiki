#!/usr/bin/env bash
set -euo pipefail

source_sha="$(uv run --no-project --with typer==0.27.2 --python 3.14 \
  python -m scripts.backend_release.recovery \
  --verifier-sha "$GITHUB_SHA" source --run-id "$SOURCE_RUN_ID" \
  --run-attempt "$SOURCE_RUN_ATTEMPT")"
echo "source_sha=$source_sha" >>"$GITHUB_OUTPUT"
