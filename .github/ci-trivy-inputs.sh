#!/usr/bin/env bash
set -euo pipefail

printf 'Updated Trivy cache data identities\n'
if [[ -d .cache/trivy ]]; then
  find .cache/trivy -type f \( -name metadata.json -o -name trivy.db -o -name trivy-java.db -o -name policy.tar.gz \) -print0 | sort -z | xargs -0 --no-run-if-empty sha256sum
  find .cache/trivy -type f -name metadata.json -print -exec cat {} \;
fi
