#!/usr/bin/env bash
set -euo pipefail

previous_version="$(uv run --no-project --with typer==0.27.2 --python 3.14 \
  python -m scripts.frontend_release.rollback active)"
echo "version_id=$previous_version" >>"$GITHUB_OUTPUT"
