#!/usr/bin/env bash
set -euo pipefail

if [ "$FORCE_FULL" = true ]; then
  test "$ROUTING_RESULT" = skipped
  expected=success
else
  test "$ROUTING_RESULT" = success
  case "$RUNTIME_SELECTED" in
  true) expected=success ;;
  false) expected=skipped ;;
  *) exit 1 ;;
  esac
fi
test "$LINT_RESULT" = success
test "$TEST_RESULT" = "$expected"
test "$POSTGRES_RESULT" = "$expected"
test "$IMAGE_RESULT" = success
