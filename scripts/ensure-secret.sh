#!/usr/bin/env bash
set -euo pipefail

gcloud secrets describe "$1" --project="$2" >/dev/null 2>&1 ||
  gcloud secrets create "$1" --project="$2" --replication-policy=automatic
