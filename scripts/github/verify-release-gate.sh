#!/usr/bin/env bash
set -euo pipefail

# Pass only public release identity and job outcomes, never the full environment.
jq -n '{
  GITHUB_SHA: env.GITHUB_SHA,
  GITHUB_REPOSITORY: env.GITHUB_REPOSITORY,
  GITHUB_REPOSITORY_ID: env.GITHUB_REPOSITORY_ID,
  GITHUB_EVENT_NAME: env.GITHUB_EVENT_NAME,
  GITHUB_REF: env.GITHUB_REF,
  GITHUB_WORKFLOW_SHA: env.GITHUB_WORKFLOW_SHA,
  GITHUB_WORKFLOW_REF: env.GITHUB_WORKFLOW_REF,
  GITHUB_OUTPUT: env.GITHUB_OUTPUT,
  RELEASE_NEEDS: (env.RELEASE_NEEDS // "{}")
}' | uv run --no-project --with typer==0.27.3 --python 3.14 python scripts/github/release_gate.py "$@"
