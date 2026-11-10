#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

port="$( { grep -E '^PUBLIC_PORT=' .env || true; } | tail -n 1 | cut -d= -f2- | tr -d "\"'")"
api="http://127.0.0.1:${port:-8080}/api"
customer="demo3-$(date +%H%M%S)"

field() {
  python3 -c 'import json, sys
value = json.load(sys.stdin)
for key in sys.argv[1:]:
    value = value[int(key)] if isinstance(value, list) else value[key]
print(value)' "$@"
}

pretty() {
  python3 -m json.tool --no-ensure-ascii
}

cache_header() {
  curl -fsS -o /dev/null -D - "$api/products/$1" | tr -d '\r' | grep -i '^x-cache'
}

rcli() {
  docker compose exec -T redis sh -c 'REDISCLI_AUTH="$(cat /run/secrets/redis_password)" exec redis-cli "$@"' rcli "$@"
}

psql_shop() {
  docker compose exec -T postgres psql -U shop -d shop -c "$1"
}

wait_order() {
  local body status
  for _ in $(seq 1 20); do
    body="$(curl -fsS "$api/orders/$1")"
    status="$(field status <<< "$body")"
    if [[ "$status" != pending ]]; then
      pretty <<< "$body"
      return 0
    fi
    sleep 0.5
  done
  echo "order $1 is still pending: check make logs S=worker"
  return 1
}

place_order() {
  local response
  response="$(curl -sS -H 'Content-Type: application/json' -w '\n%{http_code}' \
    -d "{\"customer\":\"$customer\",\"items\":[{\"product_id\":$1,\"qty\":$2}]}" "$api/orders")"
  echo "POST /api/orders qty=$2 -> HTTP $(tail -n 1 <<< "$response")" >&2
  head -n 1 <<< "$response" | field id
}

echo "== admin login"
token="$(printf '{"password":"%s"}' "$(cat secrets/admin_password)" \
  | curl -fsS -H 'Content-Type: application/json' --data-binary @- "$api/admin/login" | field token)"
auth="Authorization: Bearer $token"
echo "token received"

echo "== scenario 1: catalog CRUD with cache-aside"
drop_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
body="{\"title\":\"Демо-релиз $customer\",\"artist\":\"demo3.sh\",\"format\":\"cassette\",\"description\":\"Создан скриптом demo3\",\"price\":\"500.00\",\"stock\":5,\"drop_at\":\"$drop_at\",\"per_customer_limit\":2}"
created="$(curl -fsS -H "$auth" -H 'Content-Type: application/json' -d "$body" "$api/products")"
id="$(field id <<< "$created")"
echo "INSERT: POST /api/products -> id $id"
pretty <<< "$created"

echo "== SELECT twice: first from postgres, second from redis"
cache_header "$id"
cache_header "$id"
echo "redis TTL product:$id = $(rcli TTL "product:$id") s"

echo "== UPDATE price 500 -> 450 drops the cached copy"
updated="${body/\"500.00\"/\"450.00\"}"
curl -fsS -o /dev/null -w "PUT /api/products/$id -> HTTP %{http_code}\n" \
  -X PUT -H "$auth" -H 'Content-Type: application/json' -d "$updated" "$api/products/$id"
echo "redis EXISTS product:$id = $(rcli EXISTS "product:$id")"
cache_header "$id"
echo "price now: $(curl -fsS "$api/products/$id" | field price)"

echo "== DELETE"
curl -fsS -o /dev/null -w 'DELETE -> HTTP %{http_code}\n' -X DELETE -H "$auth" "$api/products/$id"
curl -sS -o /dev/null -w 'GET after delete -> HTTP %{http_code}\n' "$api/products/$id"

echo "== scenario 2: order through rabbitmq and worker"
pick="$(curl -fsS "$api/products" | python3 -c 'import json, sys
from datetime import datetime, timezone
now = datetime.now(timezone.utc)
for product in json.load(sys.stdin):
    if product["stock"] > 0 and datetime.fromisoformat(product["drop_at"]) <= now:
        print(product["id"], product["per_customer_limit"], product["stock"], product["title"])
        break')"
if [[ -z "$pick" ]]; then
  echo "no release on sale with stock > 0: add one in the admin page"
  exit 1
fi
read -r pid limit stock title <<< "$pick"
echo "release $pid «$title»: stock $stock, limit $limit per customer, customer $customer"

first="$(place_order "$pid" 1)"
wait_order "$first"

echo "== scenario 3: same customer exceeds the limit, worker rejects"
second="$(place_order "$pid" "$limit")"
wait_order "$second"

echo "== worker log"
docker compose logs --no-color worker | grep -E "order ($first|$second) " | tail -n 2 || true

echo "== feed:recent in redis"
curl -fsS "$api/feed?limit=3" | pretty

echo "== delete a release with orders is refused"
curl -sS -w '\nHTTP %{http_code}\n' -X DELETE -H "$auth" "$api/products/$pid"

echo "== postgres: orders and audit log"
psql_shop "SELECT id, customer, status, reason, total, processed_by FROM orders ORDER BY id DESC LIMIT 2;"
psql_shop "SELECT id, event_type, consumer, consumed_at FROM event_log ORDER BY id DESC LIMIT 8;"

echo "== redis: keys"
rcli --scan --pattern 'product*'
rcli LLEN feed:recent

echo "== rabbitmq: queues"
docker compose exec -T rabbitmq rabbitmqctl -q list_queues name type messages consumers
