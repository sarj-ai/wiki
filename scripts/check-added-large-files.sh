#!/usr/bin/env bash
set -euo pipefail

readonly max_bytes=$((512 * 1024))
status=0

for file in "$@"; do
  [[ -f "$file" ]] || continue
  if git diff --cached --quiet --diff-filter=A -- "$file"; then
    continue
  fi
  if [[ "$(git check-attr filter -- "$file")" == *": filter: lfs" ]]; then
    continue
  fi
  bytes=$(wc -c <"$file")
  if ((bytes > max_bytes)); then
    printf '%s (%d KB) exceeds 512 KB.\n' "$file" "$(((bytes + 1023) / 1024))"
    status=1
  fi
done

exit "$status"
