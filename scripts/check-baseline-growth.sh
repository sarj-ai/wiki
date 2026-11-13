#!/usr/bin/env bash
set -euo pipefail

base_sha=${1:-}
if [[ -z "$base_sha" ]]; then
  base_sha=$(git merge-base origin/main HEAD)
fi

temp_dir=$(mktemp -d)
trap 'rm -rf "$temp_dir"' EXIT

git diff --name-only "$base_sha" >"$temp_dir/changed-files"
git show "$base_sha:.sarj-standards.toml" >"$temp_dir/base-standards.toml"
{
  for managed in oxlint.strict.mts eslint.strict.mjs; do
    if git cat-file -e "$base_sha:frontend/$managed" 2>/dev/null; then
      git show "$base_sha:frontend/$managed"
    fi
  done
  for config in oxlint.config.mts eslint.config.ts eslint.config.mts eslint.config.cts eslint.config.js eslint.config.mjs eslint.config.cjs; do
    if git cat-file -e "$base_sha:frontend/$config" 2>/dev/null; then
      git show "$base_sha:frontend/$config"
    fi
  done
} >"$temp_dir/base-eslint-configs.txt"

for baseline in python-standards-baseline.json frontend/oxlint-suppressions.json; do
  base_file="$temp_dir/$(basename "$baseline")"
  prefix=""
  if [[ "$baseline" == frontend/* ]]; then
    prefix="frontend/"
  fi
  if git show "$base_sha:$baseline" >"$base_file" 2>/dev/null; then
    comparison=(--standards-base "$temp_dir/base-standards.toml" --standards-current .sarj-standards.toml)
    if [[ "$baseline" == frontend/* ]]; then
      comparison+=(--base-config "$temp_dir/base-eslint-configs.txt")
    fi
    uv run --project backend --locked python scripts/standards_checks/check_baseline_growth.py \
      "${comparison[@]}" "$base_file" "$baseline"
  else
    uv run --project backend --locked python scripts/standards_checks/check_baseline_growth.py \
      --initial "$baseline" "$temp_dir/changed-files" "$prefix"
  fi
done
