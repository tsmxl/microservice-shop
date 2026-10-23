#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== compose ps"
docker compose ps -a --format 'table {{.Service}}\t{{.State}}\t{{.Health}}\t{{.Status}}\t{{.Ports}}'

echo "== not healthy"
bad="$(docker compose ps -a --format '{{.Service}}|{{.State}}|{{.Health}}|{{.ExitCode}}' \
  | awk -F'|' '!(($2 == "running" && ($3 == "healthy" || $3 == "")) || ($2 == "exited" && $4 == "0")) {print $1}')"
if [[ -z "$bad" ]]; then
  echo "all services are healthy"
  exit 0
fi
while read -r svc; do
  echo "== logs $svc"
  docker compose logs --no-color --tail 40 "$svc"
  echo "== health log $svc"
  docker inspect --format '{{if .State.Health}}{{range .State.Health.Log}}{{.ExitCode}} {{.Output}}{{end}}{{end}}' \
    "$(docker compose ps -a -q "$svc" | head -n 1)" | tail -n 5 || true
done <<< "$bad"
