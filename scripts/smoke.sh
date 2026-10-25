#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

port="$( { grep -E '^PUBLIC_PORT=' .env || true; } | tail -n 1 | cut -d= -f2- | tr -d "\"'")"
base="http://127.0.0.1:${port:-8080}"

fail() {
  echo "FAIL: $1"
  ./scripts/diag.sh || true
  exit 1
}

expect() {
  local url="$1" pattern="$2" body=""
  for _ in $(seq 1 15); do
    body="$(curl -fsS --max-time 5 "$url" || true)"
    if grep -q "$pattern" <<< "$body"; then
      echo "ok $url contains $pattern"
      return 0
    fi
    sleep 2
  done
  fail "$url does not contain $pattern, last body: ${body:0:300}"
}

echo "== up"
docker compose up -d --build || fail "docker compose up"

echo "== wait for healthy services"
for attempt in $(seq 1 60); do
  bad="$(docker compose ps -a --format '{{.Service}}|{{.State}}|{{.Health}}|{{.ExitCode}}' \
    | awk -F'|' '!(($2 == "running" && $3 == "healthy") || ($2 == "exited" && $4 == "0")) {print $1}')"
  if [[ -z "$bad" ]]; then
    echo "all services healthy after $attempt checks"
    break
  fi
  if [[ "$attempt" == 60 ]]; then
    fail "not healthy: $(tr '\n' ' ' <<< "$bad")"
  fi
  sleep 3
done

echo "== requests through gateway"
expect "$base/api/hello" '"instance"'
expect "$base/api/health" '"status":"ok"'
expect "$base/api/hello" '"seconds_ago"'
expect "$base/" '<title>'

echo "== smoke test passed"
