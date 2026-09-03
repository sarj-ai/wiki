#!/usr/bin/env bash
set -euo pipefail

gcloud tasks queues describe "$SMART_CREATE_QUEUE_NAME" \
  --location "$REGION" \
  --project "$PROJECT_ID" \
  --format='value(name)'
