#!/usr/bin/env bash
set -euo pipefail

uv sync --locked --project backend
npm ci --prefix frontend --no-audit --no-fund --ignore-scripts
