#!/usr/bin/env bash
set -euo pipefail

for required in PROJECT_ID REGION SERVICE MCP_VERIFIED_REVISION GITHUB_OUTPUT; do
  if [[ -z "${!required:-}" ]]; then
    echo "$required is required to resolve the native MCP release mode" >&2
    exit 1
  fi
done

case "$MCP_VERIFIED_REVISION" in
  *[!a-z0-9-]* | "")
    echo "MCP_VERIFIED_REVISION must be canonical" >&2
    exit 1
    ;;
esac

revision_file=$(mktemp /tmp/wiki-mcp-revision.XXXXXX)
trap 'rm -f -- "$revision_file"' EXIT
gcloud run revisions describe "$MCP_VERIFIED_REVISION" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --format=json >"$revision_file"

expected_service=$SERVICE
revision_service=$(jq -r '.metadata.labels["serving.knative.dev/service"] // ""' "$revision_file")
if [[ "$revision_service" != "$expected_service" ]] || ! jq -e \
  'any(.status.conditions[]?; .type == "Ready" and .status == "True")' \
  "$revision_file" >/dev/null; then
  echo "The native MCP mode source is not the exact ready service revision" >&2
  exit 1
fi

flag_count=$(jq '[.spec.containers[0].env[]? | select(.name == "MCP_NATIVE_ENABLED")] | length' "$revision_file")
enabled_flag=$(jq -r '[.spec.containers[0].env[]? | select(.name == "MCP_NATIVE_ENABLED")][0].value // ""' "$revision_file")
if [[ "$flag_count" != 1 || ("$enabled_flag" != true && "$enabled_flag" != false) ]]; then
  echo "The exact revision must contain one canonical MCP_NATIVE_ENABLED flag" >&2
  exit 1
fi

if [[ "$enabled_flag" == true ]]; then
  expected_issuer=${MCP_EXPECTED_ISSUER:-https://api-wiki.sarj.ai}
  jq -e --arg issuer "$expected_issuer" '
    def env($name): [.spec.containers[0].env[]? | select(.name == $name)];
    def pinned_secret($name):
      env($name) as $entry
      | ($entry | length) == 1
      and ($entry[0].valueFrom.secretKeyRef.name | type) == "string"
      and ($entry[0].valueFrom.secretKeyRef.name | length) > 0
      and ($entry[0].valueFrom.secretKeyRef.key | test("^[1-9][0-9]*$"));
    env("MCP_NATIVE_ISSUER") == [{"name":"MCP_NATIVE_ISSUER","value":$issuer}]
    and env("MCP_NATIVE_LOOPBACK_PATHS") == [{"name":"MCP_NATIVE_LOOPBACK_PATHS","value":"[\"/callback\"]"}]
    and env("MCP_NATIVE_HTTPS_CALLBACKS") == [{"name":"MCP_NATIVE_HTTPS_CALLBACKS","value":"[]"}]
    and pinned_secret("MCP_NATIVE_FERNET_KEYS")
    and pinned_secret("MCP_NATIVE_LOCK_KEY")
  ' "$revision_file" >/dev/null || {
    echo "Enabled native MCP revision lacks its exact issuer, callback, or pinned key contract" >&2
    exit 1
  }
fi

if [[ "$enabled_flag" == true ]]; then
  mode=enabled
else
  mode=disabled
fi

printf 'mode=%s\n' "$mode" >>"$GITHUB_OUTPUT"
