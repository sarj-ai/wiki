#!/usr/bin/env bash
set -euo pipefail

base_sha=${1:-}
if [[ -z "$base_sha" ]]; then
  base_sha=$(git merge-base origin/main HEAD)
fi

temp_dir=$(mktemp -d)
trap 'rm -rf "$temp_dir"' EXIT

git diff --name-only "$base_sha" > "$temp_dir/changed-files"
git show "$base_sha:.sarj-standards.toml" > "$temp_dir/base-standards.toml"
{
  git show "$base_sha:frontend/eslint.strict.mjs"
  git show "$base_sha:frontend/eslint.config.mjs"
} > "$temp_dir/base-eslint-configs.mjs"

for baseline in python-standards-baseline.json frontend/eslint-suppressions.json; do
  base_file="$temp_dir/$(basename "$baseline")"
  if git show "$base_sha:$baseline" > "$base_file" 2>/dev/null; then
    if [[ "$baseline" == frontend/* ]]; then
      uv run --project backend --locked python scripts/check_baseline_growth.py \
        --standards-base "$temp_dir/base-standards.toml" \
        --standards-current .sarj-standards.toml \
        --base-config "$temp_dir/base-eslint-configs.mjs" \
        "$base_file" "$baseline"
    else
      uv run --project backend --locked python scripts/check_baseline_growth.py \
        --standards-base "$temp_dir/base-standards.toml" \
        --standards-current .sarj-standards.toml \
        "$base_file" "$baseline"
    fi
  else
    prefix=""
    if [[ "$baseline" == frontend/* ]]; then
      prefix="frontend/"
    fi
    uv run --project backend --locked python scripts/check_baseline_growth.py \
      --initial "$baseline" "$temp_dir/changed-files" "$prefix"
  fi
done
