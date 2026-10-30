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
  git show "$base_sha:frontend/eslint.strict.mjs"
  for config in eslint.config.ts eslint.config.mts eslint.config.cts eslint.config.js eslint.config.mjs eslint.config.cjs; do
    if git cat-file -e "$base_sha:frontend/$config" 2>/dev/null; then
      git show "$base_sha:frontend/$config"
    fi
  done
} >"$temp_dir/base-eslint-configs.txt"

initializing_diagnostic_baselines=false
if [[ -f diagnostic-baseline.json ]] &&
  ! git cat-file -e "$base_sha:diagnostic-baseline.json" 2>/dev/null; then
  initializing_diagnostic_baselines=true
fi

for baseline in python-standards-baseline.json frontend/eslint-suppressions.json; do
  base_file="$temp_dir/$(basename "$baseline")"
  prefix=""
  if [[ "$baseline" == frontend/* ]]; then
    prefix="frontend/"
  fi
  if [[ "$initializing_diagnostic_baselines" == true ]]; then
    uv run --project backend --locked python scripts/standards_checks/check_baseline_growth.py \
      --initial "$baseline" "$temp_dir/changed-files" "$prefix"
  elif git show "$base_sha:$baseline" >"$base_file" 2>/dev/null; then
    if [[ "$baseline" == frontend/* ]]; then
      uv run --project backend --locked python scripts/standards_checks/check_baseline_growth.py \
        --standards-base "$temp_dir/base-standards.toml" \
        --standards-current .sarj-standards.toml \
        --base-config "$temp_dir/base-eslint-configs.txt" \
        --bootstrap standards-baseline-bootstrap.json \
        --merge-base "$base_sha" \
        --allowance-path "$baseline" \
        --changed-files "$temp_dir/changed-files" \
        --changed-prefix "$prefix" \
        "$base_file" "$baseline"
    else
      uv run --project backend --locked python scripts/standards_checks/check_baseline_growth.py \
        --standards-base "$temp_dir/base-standards.toml" \
        --standards-current .sarj-standards.toml \
        "$base_file" "$baseline"
    fi
  else
    uv run --project backend --locked python scripts/standards_checks/check_baseline_growth.py \
      --initial "$baseline" "$temp_dir/changed-files" "$prefix"
  fi
done
