#!/usr/bin/env bash
set -euo pipefail

UV_PYTHON_DOWNLOADS_JSON_URL=https://raw.githubusercontent.com/astral-sh/uv/f69fb50c8b28997af9c6b0e5c700a34470d86005/crates/uv-python-managed/download-metadata.json \
  uv run --no-config --no-project --python 3.15.0 --with code-standards==8.147.0 python -m sarj_standards.libs.adoption.native_bootstrap
# GitHub applies this path handoff after the step; setup runs in this step.
while IFS= read -r native_path; do
  export PATH="$native_path:$PATH"
done <"$GITHUB_PATH"
uvx --no-config --isolated --python 3.14 --from sarj-standards-bootstrap code-standards setup --tools-only

if [[ "${FORCE_FULL:-false}" == true || -z "$BASE_SHA" ]]; then
  uvx --python 3.14.8 'pre-commit==4.7.0' run --all-files --show-diff-on-failure
  exit 0
elif [[ "$BASE_SHA" =~ ^0+$ ]]; then
  BASE_SHA=$(git rev-list --max-parents=0 HEAD)
fi
uvx --python 3.14.8 'pre-commit==4.7.0' run --from-ref "$BASE_SHA" --to-ref "$HEAD_SHA" --show-diff-on-failure
