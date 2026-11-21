#!/usr/bin/env bash
set -euo pipefail

cache_directory=$(uv cache dir)
printf 'Resolved CI tool cache: %s\n' "$cache_directory"
declare -A seen_environments=()
for environment in "$cache_directory"/environments-v2/* "$cache_directory"/archive-v0/* backend/.venv .venv "${PRE_COMMIT_HOME:-$HOME/.cache/pre-commit}"/repo*/py_env*; do
  if [[ ! -f "$environment/pyvenv.cfg" ]]; then
    continue
  fi
  environment=$(readlink -f "$environment")
  if [[ -n "${seen_environments[$environment]:-}" ]]; then
    continue
  fi
  seen_environments[$environment]=1
  printf '\nResolved CI environment: %s\n' "$environment"
  "$environment/bin/python" --version
  uv pip freeze --python "$environment/bin/python"
  find -L "$environment/lib" -maxdepth 4 -type f \( -name METADATA -o -name RECORD -o -name WHEEL -o -name direct_url.json \) -print0 | sort -z | xargs -0 --no-run-if-empty sha256sum
done
