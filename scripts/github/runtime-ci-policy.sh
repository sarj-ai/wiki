#!/usr/bin/env bash
# Classify a complete NUL-delimited diff; static/security gates stay unconditional.
set -euo pipefail

backend=false
frontend=false
while IFS= read -r -d '' path; do
  case "$path" in
    backend/app/*|backend/pyproject.toml|backend/uv.lock|backend/uv.toml|backend/.python-version|\
    frontend/openapi.json|frontend/openapi-ts.config.ts|frontend/scripts/patch-generated.mjs|frontend/src/lib/generated/*)
      backend=true
      frontend=true
      ;;
    backend/*) backend=true ;;
    frontend/*) frontend=true ;;
    terraform/*|docs/*.md|README.md|LICENSE) ;;
    *)
      # Shared tooling and unknown paths cannot prove either domain irrelevant.
      backend=true
      frontend=true
      break
      ;;
  esac
done
printf 'backend=%s\nfrontend=%s\n' "$backend" "$frontend"
