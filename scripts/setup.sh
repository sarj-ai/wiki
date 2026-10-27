#!/usr/bin/env bash
set -euo pipefail

mise --no-config --no-env --no-hooks exec aqua:hashicorp/terraform@1.17.0 -- terraform -chdir=terraform init -backend=false -input=false -lockfile=readonly
uv sync --project backend --locked
npm ci --prefix frontend
