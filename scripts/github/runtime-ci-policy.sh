#!/usr/bin/env bash
# Classify a complete NUL-delimited diff; static/security gates stay unconditional.
set -euo pipefail

if (($# != 0)); then
  if (($# != 3)) || [[ "$1" != --diff ]]; then
    echo 'usage: runtime-ci-policy.sh [--diff BASE_SHA HEAD_SHA]' >&2
    exit 2
  fi
  # Disable rename detection so both sides of a move select their domains.
  if git diff --no-ext-diff --no-textconv --no-renames --name-only -z "$2" "$3" -- >"$RUNNER_TEMP/changed-paths"; then
    exec <"$RUNNER_TEMP/changed-paths"
    exec >>"$GITHUB_OUTPUT"
  else
    echo '::warning::Change detection failed; running full runtime qualification'
    printf 'backend=true\nfrontend=true\n' >>"$GITHUB_OUTPUT"
    exit 0
  fi
fi

backend=false
frontend=false
while IFS= read -r -d '' path; do
  case "$path" in
    backend/app/* | backend/pyproject.toml | backend/uv.lock | backend/uv.toml | backend/.python-version | \
      frontend/openapi.json | frontend/openapi-ts.config.ts | frontend/scripts/patch-generated.ts | frontend/scripts/patch-generated.mjs | frontend/src/lib/generated/*)
      backend=true
      frontend=true
      ;;
    backend/*) backend=true ;;
    frontend/*) frontend=true ;;
    terraform/* | docs/*.md | README.md | LICENSE) ;;
    *)
      # Shared tooling and unknown paths cannot prove either domain irrelevant.
      backend=true
      frontend=true
      break
      ;;
  esac
done
printf 'backend=%s\nfrontend=%s\n' "$backend" "$frontend"
