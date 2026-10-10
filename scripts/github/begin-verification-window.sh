#!/usr/bin/env bash
set -euo pipefail

started_at=$(date -u +'%Y-%m-%dT%H:%M:%S.%NZ')
echo "started_at=$started_at" >>"$GITHUB_OUTPUT"
