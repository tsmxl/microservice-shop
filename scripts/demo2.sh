#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

port="$( { grep -E '^PUBLIC_PORT=' .env || true; } | tail -n 1 | cut -d= -f2- | tr -d "\"'")"
base="http://127.0.0.1:${port:-8080}"

echo "== services"
docker compose ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'

echo "== GET $base/api/hello"
curl -fsS "$base/api/hello" | python3 -m json.tool --no-ensure-ascii

echo "== GET $base/api/health"
curl -fsS "$base/api/health" | python3 -m json.tool --no-ensure-ascii

echo "== GET $base/"
curl -fsS -o /dev/null -w 'HTTP %{http_code}, %{size_download} bytes\n' "$base/"

echo "== postgres"
docker compose exec -T postgres psql -U shop -d shop -tAc 'select version()'

echo "== redis"
docker compose exec -T redis sh -c 'REDISCLI_AUTH="$(cat /run/secrets/redis_password)" redis-cli info server' | grep -E '^redis_version'

echo "== rabbitmq"
docker compose exec -T rabbitmq rabbitmq-diagnostics -q server_version

echo "== worker heartbeat"
docker compose logs --no-color --tail 3 worker
