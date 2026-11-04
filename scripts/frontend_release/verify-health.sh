#!/usr/bin/env bash
set -euo pipefail

if ! [[ ${CLOUDFLARE_ACCOUNT_ID:-} =~ ^[a-f0-9]{32}$ ]] ||
  ! [[ ${CLOUDFLARE_API_TOKEN:-} =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "Frontend security verification requires Cloudflare authentication." >&2
  exit 1
fi
for endpoint in script-settings subdomain; do
  if ! settings="$(printf 'header = "Authorization: Bearer %s"\n' "$CLOUDFLARE_API_TOKEN" |
    curl --fail --silent --show-error --connect-timeout 5 --max-time 15 --config - \
      "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/scripts/sarj-wiki/$endpoint")"; then
    echo "Frontend Worker security metadata could not be read." >&2
    exit 1
  fi
  case "$endpoint" in
    script-settings) condition='.success == true and .result.observability.redact_query_string == true' ;;
    subdomain) condition='.success == true and .result.previews_enabled == false' ;;
  esac
  if ! jq --exit-status "$condition" <<<"$settings" >/dev/null; then
    echo "Frontend Worker security settings failed verification." >&2
    exit 1
  fi
done

body_file="$RUNNER_TEMP/frontend-health-body.json"
headers_file="$RUNNER_TEMP/frontend-health-headers.txt"
proxy_body_file="$RUNNER_TEMP/frontend-proxy-body.json"
canary_id="$(node --eval 'process.stdout.write(crypto.randomUUID())')"
trap 'rm -f "$body_file" "$headers_file" "$proxy_body_file"' EXIT

verify_read_proxy() {
  local path status
  for path in auth/me sidebar/drives; do
    if ! status="$(curl --silent --show-error --max-time 30 \
      --output "$proxy_body_file" \
      --write-out '%{http_code}' \
      --header 'Cookie: access_token=release-probe-invalid' \
      --header 'X-Wiki-Read: 1' \
      --header 'Sec-Fetch-Site: same-origin' \
      --header "X-Request-ID: $canary_id" \
      "https://wiki.sarj.ai/api/wiki/$path")"; then
      status=000
    fi
    if test "$status" != 401 || ! jq --exit-status \
      '.detail == "Your session has expired"' "$proxy_body_file" >/dev/null; then
      echo "Frontend read proxy failed ($path, HTTP $status)." >&2
      return 1
    fi
  done
}

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
  if test "$status" = 200 &&
    test "$body_ok" = true &&
    test "$request_id_ok" = true &&
    test "$cache_ok" = true &&
    verify_read_proxy; then
    echo "Frontend production revision verified."
    exit 0
  fi
  echo "Frontend revision not ready (attempt $attempt/6, HTTP ${status:-000}, body=$body_ok, request_id=$request_id_ok, no_store=$cache_ok)."
  sleep 10
done

echo "Frontend production revision could not be verified." >&2
exit 1
