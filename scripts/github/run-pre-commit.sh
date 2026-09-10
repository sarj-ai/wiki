#!/usr/bin/env bash
set -euo pipefail

if [[ -z "$BASE_SHA" ]]; then
  uvx --python 3.14.7 'pre-commit==4.6.2' run --all-files --show-diff-on-failure
  exit 0
elif [[ "$BASE_SHA" =~ ^0+$ ]]; then
  BASE_SHA=$(git rev-list --max-parents=0 HEAD)
fi
uvx --python 3.14.7 'pre-commit==4.6.2' run --from-ref "$BASE_SHA" --to-ref "$HEAD_SHA" --show-diff-on-failure
