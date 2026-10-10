#!/usr/bin/env bash
set -euo pipefail

stamp=$1
ttl=$2
target=$3
message=$4
recent_stamp=$(find "$stamp" -mmin "-$ttl" 2>/dev/null) || recent_stamp=""
if [[ -f "$stamp" ]] && [[ -n "$recent_stamp" ]]; then
  echo "● $message fresh (pulled today) — skipping. Run 'make $target' to refresh."
else
  "$5" "$target"
fi
