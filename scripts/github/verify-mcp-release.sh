#!/usr/bin/env bash
set -euo pipefail

mode=${MCP_VERIFY_MODE:-disabled}
base_url=${MCP_BASE_URL:-https://api-wiki.sarj.ai}
base_url=${base_url%/}
if [[ ! "$base_url" =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?$ ]]; then
  echo "MCP_BASE_URL must be an exact HTTPS origin" >&2
  exit 1
fi
if [[ "$mode" != disabled && "$mode" != enabled ]]; then
  echo "MCP_VERIFY_MODE must be disabled or enabled" >&2
  exit 1
fi

probe_dir=$(mktemp -d /tmp/wiki-mcp-release.XXXXXX)
trap 'rm -rf -- "$probe_dir"' EXIT
request_counter=0

probe() {
  local method=$1
  local path=$2
  shift 2
  request_counter=$((request_counter + 1))
  PROBE_STATUS=$(curl --silent --show-error \
    --connect-timeout 10 \
    --max-time 20 \
    --request "$method" \
    --dump-header "$probe_dir/headers" \
    --output "$probe_dir/body" \
    --write-out '%{http_code}' \
    --header "X-Request-ID: wiki-mcp-${GITHUB_SHA:-local}-${request_counter}" \
    "$@" \
    "$base_url$path")
}

if [[ "$mode" == disabled ]]; then
  while read -r method path; do
    probe "$method" "$path"
    if [[ "$PROBE_STATUS" != 404 ]]; then
      echo "Disabled native MCP route $method $path returned $PROBE_STATUS instead of 404" >&2
      exit 1
    fi
  done <<'EOF'
GET /.well-known/oauth-authorization-server
GET /.well-known/oauth-protected-resource/mcp
GET /authorize
POST /token
POST /register
POST /revoke
POST /mcp
POST /mcp/
GET /integrations/mcp/connections
GET /integrations/mcp/consent/synthetic-intent
GET /integrations/mcp/consent/synthetic-intent/status
POST /integrations/mcp/consent/synthetic-intent
DELETE /integrations/mcp/connections/00000000-0000-0000-0000-000000000000
EOF
  echo "Native MCP remains disabled with the exact route inventory returning 404"
  exit 0
fi

issuer=${MCP_EXPECTED_ISSUER:-$base_url/}
issuer=${issuer%/}/
resource="$base_url/mcp"
resource_metadata="$base_url/.well-known/oauth-protected-resource/mcp"

probe GET /.well-known/oauth-authorization-server
if [[ "$PROBE_STATUS" != 200 ]] || ! jq -e \
  --arg issuer "$issuer" \
  --arg base "$base_url" \
  '.issuer == $issuer
   and .authorization_endpoint == ($base + "/authorize")
   and .token_endpoint == ($base + "/token")
   and .registration_endpoint == ($base + "/register")
   and .revocation_endpoint == ($base + "/revoke")
   and .authorization_response_iss_parameter_supported == true
   and .client_id_metadata_document_supported == false
   and (.code_challenge_methods_supported | index("S256") != null)' \
  "$probe_dir/body" >/dev/null; then
  echo "Native MCP authorization metadata is unavailable or non-canonical" >&2
  exit 1
fi

probe GET /.well-known/oauth-protected-resource/mcp
if [[ "$PROBE_STATUS" != 200 ]] || ! jq -e \
  --arg issuer "$issuer" \
  --arg resource "$resource" \
  '.resource == $resource and .authorization_servers == [$issuer]' \
  "$probe_dir/body" >/dev/null; then
  echo "Native MCP protected-resource metadata is unavailable or non-canonical" >&2
  exit 1
fi

probe POST /mcp \
  --header 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
challenge=$(awk 'tolower($1) == "www-authenticate:" { sub(/\r$/, "", $0); print }' \
  "$probe_dir/headers" | tail -n 1)
if [[ "$PROBE_STATUS" != 401 || "$challenge" != *"$resource_metadata"* ]]; then
  echo "Unauthenticated native MCP did not return the expected OAuth challenge" >&2
  exit 1
fi

for hostile in host origin; do
  if [[ "$hostile" == host ]]; then
    header='Host: attacker.invalid'
  else
    header='Origin: https://attacker.invalid'
  fi
  probe POST /mcp \
    --header "$header" \
    --header 'Content-Type: application/json' \
    --data '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
  if [[ ! "$PROBE_STATUS" =~ ^(400|403|421)$ ]]; then
    echo "Native MCP accepted a hostile $hostile transport boundary ($PROBE_STATUS)" >&2
    exit 1
  fi
done

for required in PROJECT_ID REGION SERVICE MCP_VERIFIED_REVISION MCP_LOG_WINDOW_START; do
  if [[ -z "${!required:-}" ]]; then
    echo "$required is required for enabled MCP log-hygiene verification" >&2
    exit 1
  fi
done
if [[ ! "$PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]] ||
  [[ ! "$REGION" =~ ^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$ ]] ||
  [[ ! "$SERVICE" =~ ^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$ ]] ||
  [[ ! "$MCP_VERIFIED_REVISION" =~ ^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$ ]] ||
  [[ ! "$MCP_LOG_WINDOW_START" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z$ ]]; then
  echo "Enabled MCP log-hygiene identifiers must be canonical and filter-safe" >&2
  exit 1
fi

gcloud run revisions describe "$MCP_VERIFIED_REVISION" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --format=json >"$probe_dir/revision.json"
if ! jq -e \
  --arg revision "$MCP_VERIFIED_REVISION" \
  --arg service "$SERVICE" \
  '.metadata.name == $revision
   and .metadata.labels["serving.knative.dev/service"] == $service
   and any(.status.conditions[]?; .type == "Ready" and .status == "True")' \
  "$probe_dir/revision.json" >/dev/null; then
  echo "MCP_VERIFIED_REVISION is not the exact ready revision for SERVICE" >&2
  exit 1
fi

sentinel="wm_access_wikiMcpLogHygiene${GITHUB_RUN_ID:-local}${GITHUB_RUN_ATTEMPT:-0}"
log_request_id="wiki-mcp-secret-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-0}"
PROBE_STATUS=$(curl --silent --show-error \
  --connect-timeout 10 \
  --max-time 20 \
  --request POST \
  --output "$probe_dir/body" \
  --write-out '%{http_code}' \
  --header "X-Request-ID: $log_request_id" \
  --header "Authorization: Bearer $sentinel" \
  --header 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
  "$resource")
if [[ "$PROBE_STATUS" != 401 ]]; then
  echo "Synthetic invalid bearer probe returned $PROBE_STATUS instead of 401" >&2
  exit 1
fi

log_count=0
for _attempt in {1..12}; do
  # The upper bound is captured after the synthetic probe, and refreshed after
  # every ingestion wait. A caller-supplied pre-probe timestamp cannot exclude
  # the very event this gate is intended to inspect.
  log_window_end=$(date -u +'%Y-%m-%dT%H:%M:%S.%NZ')
  log_filter=$(printf '%s\n' \
    'resource.type="cloud_run_revision"' \
    "AND resource.labels.service_name=\"${SERVICE}\"" \
    "AND resource.labels.revision_name=\"${MCP_VERIFIED_REVISION}\"" \
    "AND timestamp>=\"${MCP_LOG_WINDOW_START}\"" \
    "AND timestamp<=\"${log_window_end}\"" \
    "AND jsonPayload.record.extra.request_id=\"${log_request_id}\"")
  gcloud logging read "$log_filter" \
    --project "$PROJECT_ID" \
    --limit 20 \
    --format=json >"$probe_dir/logs.json"
  log_count=$(jq 'length' "$probe_dir/logs.json")
  if [[ "$log_count" -ge 1 ]]; then
    break
  fi
  sleep 10
done
if [[ "$log_count" -lt 1 ]]; then
  echo "No correlated native MCP log arrived for secret-hygiene verification" >&2
  exit 1
fi
if grep -F -- "$sentinel" "$probe_dir/logs.json" >/dev/null; then
  echo "Native MCP logs retained synthetic bearer material" >&2
  exit 1
fi

echo "Native MCP discovery, challenge, transport rejection and secret-log hygiene passed"
