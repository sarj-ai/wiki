#!/usr/bin/env bash
set -euo pipefail

partial=()
for file in "$@"; do
  [[ -f "$file" ]] || continue
  if ! git diff --quiet -- "$file"; then
    partial+=("$file")
  fi
done

if (("${#partial[@]}")); then
  printf 'Refusing to autofix partially staged files; this would stage unrelated hunks:\n' >&2
  printf '  %s\n' "${partial[@]}" >&2
  printf 'Stage the whole file or stash its unstaged hunks, then retry.\n' >&2
  exit 1
fi
