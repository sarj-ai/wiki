#!/usr/bin/env bash
set -euo pipefail

test "$TRIVY_RESULT" = success
test "$TERRAFORM_RESULT" = success
