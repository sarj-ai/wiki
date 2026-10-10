#!/usr/bin/env bash
set -euo pipefail

{
  echo '## Trivy IaC scan (terraform/)'
  echo '```'
  cat trivy-report.txt
  echo '```'
} >>"$GITHUB_STEP_SUMMARY"
