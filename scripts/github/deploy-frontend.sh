#!/usr/bin/env bash
set -euo pipefail

test ! -e "$WRANGLER_OUTPUT_FILE_PATH"
npm run deploy
