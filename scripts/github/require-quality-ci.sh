#!/usr/bin/env bash
set -euo pipefail

test "$PYTHON_BASELINE_RESULT" = success
test "$REPOSITORY_QUALITY_RESULT" = success
