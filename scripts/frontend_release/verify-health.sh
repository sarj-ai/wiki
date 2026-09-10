#!/usr/bin/env bash
set -euo pipefail

body_file="$RUNNER_TEMP/frontend-health-body.json"
headers_file="$RUNNER_TEMP/frontend-health-headers.txt"
canary_id="$(node --eval 'process.stdout.write(crypto.randomUUID())')"

for attempt in 1 2 3 4 5 6; do
  if ! status="$(curl --silent --show-error --max-time 15 \
      --output "$body_file" \
      --dump-header "$headers_file" \
      --write-out '%{http_code}' \
      --header "X-Request-ID: $canary_id" \
      https://wiki.sarj.ai/health)"; then
    status=000
  fi
  response_id="$(awk -F ':' '
    tolower($1) == "x-request-id" {
      sub(/^[^:]*:[[:space:]]*/, "")
      gsub(/\r/, "")
      value = $0
    }
    END { print value }
  ' "$headers_file")"
  cache_control="$(awk -F ':' '
    tolower($1) == "cache-control" {
      sub(/^[^:]*:[[:space:]]*/, "")
      gsub(/\r/, "")
      value = tolower($0)
    }
    END { print value }
  ' "$headers_file")"
  cache_ok=false
  case ",${cache_control// /}," in
    *,no-store,*) cache_ok=true ;;
  esac
  body_ok=false
  if jq --exit-status --arg sha "$GITHUB_SHA" \
    '.status == "ok" and .revision == $sha' "$body_file" >/dev/null; then
    body_ok=true
  fi
  request_id_ok=false
  if test "$response_id" = "$canary_id"; then
    request_id_ok=true
  fi
  if test "$status" = 200 \
    && test "$body_ok" = true \
    && test "$request_id_ok" = true \
    && test "$cache_ok" = true; then
    rm -f "$body_file" "$headers_file"
    echo "Frontend production revision verified."
    exit 0
  fi
  echo "Frontend revision not ready (attempt $attempt/6, HTTP ${status:-000}, body=$body_ok, request_id=$request_id_ok, no_store=$cache_ok)."
  sleep 10
done

rm -f "$body_file" "$headers_file"
echo "Frontend production revision could not be verified." >&2
exit 1
