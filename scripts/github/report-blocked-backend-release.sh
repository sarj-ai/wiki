#!/usr/bin/env bash
set -euo pipefail

{
  echo '## Backend production gate failed'
  echo "- Previous revision: \`${PREVIOUS_REVISION}\`"
  echo "- Candidate revision: \`${CANDIDATE_REVISION}\`"
  echo "- Migration outcome: \`${MIGRATION_OUTCOME}\`"
  echo "- Traffic-switch outcome: \`${TRAFFIC_OUTCOME}\`"
  echo '- Automatic rollback was not attempted.'
  echo '- Migration compatibility with the previous revision is not proven, so rollback safety is not guaranteed.'
  echo '- The workflow orders migrations before the traffic switch and stops immediately if they fail.'
  echo '- Stop subsequent merges, inspect the bounded release evidence, and repair forward.'
} >>"$GITHUB_STEP_SUMMARY"
