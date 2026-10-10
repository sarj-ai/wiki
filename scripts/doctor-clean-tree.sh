#!/usr/bin/env bash
set -euo pipefail

temp_dir=$(mktemp -d)
temp_dir=$(cd "$temp_dir" && pwd -P)
trap 'rm -rf "$temp_dir"' EXIT
git ls-files --cached --others --exclude-standard -z |
  tar --null -T - -cf - | tar -xf - -C "$temp_dir"
"$@" --root "$temp_dir" doctor
