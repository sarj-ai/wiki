#!/usr/bin/env bash
set -euo pipefail

smoke_port=${WORKER_SMOKE_PORT:-8788}
[[ $smoke_port =~ ^[0-9]+$ ]]
smoke_dir=$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/wiki-worker-smoke.XXXXXX")
smoke_pid=''

trap '
  if [[ -n "$smoke_pid" ]]; then
    kill -TERM -- "-$smoke_pid" 2>/dev/null || true
    for _ in {1..20}; do
      if ! kill -0 -- "-$smoke_pid" 2>/dev/null; then
        break
      fi
      sleep 0.1
    done
    kill -KILL -- "-$smoke_pid" 2>/dev/null || true
    wait "$smoke_pid" 2>/dev/null || true
  fi
  rm -rf "$smoke_dir"
' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

node - "$smoke_port" <<'NODE'
const net = require("node:net");
const server = net.createServer();
server.once("error", (error) => {
  console.error("Worker smoke port is unavailable:", error.code);
  process.exitCode = 1;
});
server.listen(Number(process.argv[2]), "127.0.0.1", () => server.close());
NODE

test -f .open-next/worker.js
setsid node node_modules/wrangler/bin/wrangler.js dev --local --ip 127.0.0.1 \
  --port "$smoke_port" --inspector-port 0 --persist-to "$smoke_dir/state" \
  --log-level error >"$smoke_dir/worker.log" 2>&1 &
smoke_pid=$!

for _ in {1..20}; do
  if curl --fail --silent --max-time 3 --output "$smoke_dir/health.json" \
    "http://127.0.0.1:$smoke_port/health" &&
    node -e 'const fs = require("node:fs"); if (JSON.parse(fs.readFileSync(process.argv[1], "utf8")).status !== "ok") process.exit(1)' "$smoke_dir/health.json"; then
    echo 'Packaged Wiki Worker health passed in local workerd.'
    exit 0
  fi
  if ! kill -0 "$smoke_pid" 2>/dev/null; then
    break
  fi
  sleep 1
done

echo 'Packaged Wiki Worker failed its local runtime health check.' >&2
tail -n 30 "$smoke_dir/worker.log" >&2
exit 1
