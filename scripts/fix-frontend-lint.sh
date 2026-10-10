#!/usr/bin/env bash
set -euo pipefail

cd frontend
node_modules/.bin/eslint --fix --suppressions-location eslint-suppressions.json . || true
