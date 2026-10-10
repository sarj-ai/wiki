#!/usr/bin/env bash
set -euo pipefail

# Disable rename detection so both sides of a move select their domains.
if git diff --no-ext-diff --no-textconv --no-renames --name-only -z "$BASE_SHA" "$HEAD_SHA" -- >"$RUNNER_TEMP/changed-paths"; then
  bash scripts/github/runtime-ci-policy.sh <"$RUNNER_TEMP/changed-paths" >>"$GITHUB_OUTPUT"
else
  echo '::warning::Change detection failed; running full runtime qualification'
  printf 'backend=true\nfrontend=true\n' >>"$GITHUB_OUTPUT"
fi
