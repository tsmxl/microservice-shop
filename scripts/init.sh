#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

SECRETS=(
  postgres_password
  pg_replication_password
  redis_password
  rabbitmq_password
  rabbitmq_cookie
  admin_password
  admin_token
  storage_secret
)

env_get() {
  { grep -E "^$1=" .env || true; } | tail -n 1 | cut -d= -f2- | tr -d "\"'"
}

command -v openssl >/dev/null || { echo "openssl is required"; exit 1; }

echo "== env"
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "created .env from .env.example"
fi
if [[ -s .env && -n "$(tail -c 1 .env)" ]]; then
  echo >> .env
fi
while IFS= read -r line || [[ -n "$line" ]]; do
  key="${line%%=*}"
  [[ -z "$key" || "$key" == \#* ]] && continue
  if ! grep -qE "^${key}=" .env; then
    printf '%s\n' "$line" >> .env
    echo "added $key to .env"
  fi
done < .env.example

echo "== secrets"
mkdir -p secrets
chmod 700 secrets
for name in "${SECRETS[@]}"; do
  if [[ ! -s "secrets/$name" ]]; then
    openssl rand -hex 24 | tr -d '\n' > "secrets/$name"
    echo "generated secrets/$name"
  fi
done
touch secrets/ts_authkey
chmod 644 secrets/*
[[ -s secrets/ts_authkey ]] || echo "secrets/ts_authkey is empty: put a Tailscale auth key there before make public"

echo "== dirs"
for key in DATA_DIR BACKUP_DIR BACKUP_MIRROR_DIR; do
  dir="$(env_get "$key")"
  [[ -n "$dir" ]] || { echo "$key is empty in .env"; exit 1; }
  if [[ ! -d "$dir" ]]; then
    mkdir -p "$dir"
    echo "created $dir"
    if [[ "$key" == DATA_DIR ]]; then
      if chattr +C "$dir" 2>/dev/null; then
        echo "disabled copy-on-write for $dir"
      fi
    fi
  fi
done

echo "== host"
docker --version || echo "docker: not found"
docker compose version || echo "docker compose: not found"
if command -v getenforce >/dev/null; then
  echo "selinux: $(getenforce)"
else
  echo "selinux: getenforce not found"
fi
docker info --format 'driver={{.Driver}} cgroup={{.CgroupVersion}} security={{json .SecurityOptions}}' \
  || echo "docker info failed: check that the docker service runs and the user is in the docker group"
df -h "$(env_get DATA_DIR)" | tail -n 1
ip route | grep -E '(^|[^0-9])10\.10\.' || echo "routes 10.10.x.x: none"
networks="$(docker network ls -q 2>/dev/null \
  | xargs -r docker network inspect --format '{{.Name}} {{range .IPAM.Config}}{{.Subnet}} {{end}}' 2>/dev/null || true)"
overlap="$(grep -E ' 10\.10\.' <<< "$networks" | grep -v '^shop_' || true)"
if [[ -n "$overlap" ]]; then
  echo "docker networks in 10.10.x.x outside this project, they conflict with the IP plan:"
  echo "$overlap"
else
  echo "docker networks in 10.10.x.x: only shop_*"
fi
if [[ -r /proc/sys/net/bridge/bridge-nf-call-iptables ]]; then
  echo "bridge-nf-call-iptables: $(cat /proc/sys/net/bridge/bridge-nf-call-iptables)"
else
  echo "bridge-nf-call-iptables: br_netfilter is not loaded yet, the firewall service loads it"
fi

echo "== done"
echo "admin password: secrets/admin_password"
