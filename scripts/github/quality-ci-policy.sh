#!/usr/bin/env bash
# Skip only a standalone copy demonstrably covered by Release Main's full call.
set -euo pipefail

retain_quality() {
  printf 'run_quality=true\n'
  exit 0
}

if (($# != 3)) || [[ "$1" != --diff ]]; then
  retain_quality
fi
base="$2"
head="$3"
if [[ ! "$base" =~ ^[0-9a-fA-F]{40}$ || ! "$head" =~ ^[0-9a-fA-F]{40}$ || "$base" == 0000000000000000000000000000000000000000 ]]; then
  retain_quality
fi
# New/rewritten or unavailable history cannot prove native push path selection.
if ! git merge-base --is-ancestor "$base" "$head"; then
  retain_quality
fi
if ! commits=$(git rev-list --count "$base..$head") || ((commits > 1000)); then
  retain_quality
fi

paths_file=$(mktemp "$RUNNER_TEMP/quality-paths.XXXXXX")
trap 'rm -f "$paths_file"' EXIT
# Keep both sides of moves and filenames containing whitespace/newlines intact.
if ! git diff --no-ext-diff --no-textconv --no-renames --name-status -z "$base" "$head" -- >"$paths_file"; then
  retain_quality
fi

count=0
release=false
while IFS= read -r -d '' status && IFS= read -r -d '' path; do
  count=$((count + 1))
  # Native Actions filters inspect at most 300 files. Deletions may be the old
  # side of a rename; retain quality rather than assume native rename matching.
  if ((count > 300)) || [[ "$status" != [AMT] ]]; then
    retain_quality
  fi
  # Contract: exactly the literal and trailing /** paths in release-main.yml.
  case "$path" in
    backend/.dockerignore | backend/Dockerfile | backend/alembic.ini | backend/alembic/* | backend/app/* | \
      backend/pyproject.toml | backend/uv.lock | backend/uv.toml | \
      frontend/* | .github/workflows/ci-frontend.yml | .github/workflows/deploy-backend.yml | \
      .github/workflows/deploy-frontend.yml | scripts/github/release_gate.py | scripts/github/verify-release-gate.sh | \
      scripts/github/deploy-backend-candidate.sh | scripts/github/report-blocked-backend-release.sh | \
      scripts/github/run-backend-migrations.sh | scripts/github/switch-backend-production-traffic.sh | \
      scripts/github/verify-backend-production-health.sh | scripts/github/verify-backend-correlated-logs.sh | \
      scripts/github/verify-mcp-release.sh | scripts/github/resolve-mcp-release-mode.sh | \
      scripts/github/verify-smart-create-queue.sh | scripts/wiki.py | scripts/backend_release/* | \
      scripts/frontend_release/* | .github/workflows/release-main.yml)
      release=true
      ;;
  esac
done <"$paths_file"

if [[ "$release" == true ]]; then
  printf 'run_quality=false\n'
else
  retain_quality
fi
