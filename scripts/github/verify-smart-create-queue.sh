#!/usr/bin/env bash
set -euo pipefail

for queue_name in "$SMART_CREATE_QUEUE_NAME" "$DOCUMENT_TRANSFORM_QUEUE_NAME"; do
  queue_state=$(gcloud tasks queues describe "$queue_name" \
    --location "$REGION" \
    --project "$PROJECT_ID" \
    --format='value(state)')
  if [[ "$queue_state" != "RUNNING" ]]; then
    echo "Queue $queue_name is not RUNNING (state: $queue_state). Investigate before deploying; deliberate pauses must not be resumed automatically." >&2
    exit 1
  fi
  echo "Queue $queue_name is RUNNING"
done
